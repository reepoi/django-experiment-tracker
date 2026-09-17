import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings
from django.core.management import get_commands
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from django_experiment_tracker.models import Experiment


EXPERIMENT_MODEL_SETTING = "EXPERIMENT_TRACKER_EXPERIMENT_MODEL"
ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def group_resource_values(values, resources_per_run):
    return [
        ",".join(values[index : index + resources_per_run])
        for index in range(0, len(values), resources_per_run)
    ]


class SchedulerInterrupted(Exception):
    pass


@dataclass
class RunningExperiment:
    alt_id: str
    resource: str
    process: subprocess.Popen


class Command(BaseCommand):
    help = "Run experiments in parallel with per-resource concurrency limits"
    requires_migrations_checks = True

    def add_arguments(self, parser):
        parser.add_argument(
            "alt_ids",
            nargs="+",
            help="Alternative IDs of the experiments to run",
        )
        parser.add_argument(
            "--runner",
            required=True,
            help="Management command that runs one experiment by alternative ID",
        )
        parser.add_argument(
            "--runner-argument",
            action="append",
            default=[],
            metavar="ARG",
            help=(
                "Argument passed to the runner after its alternative ID; repeat for "
                "multiple arguments (use --runner-argument=VALUE for values beginning "
                "with a dash)"
            ),
        )
        parser.add_argument(
            "--resource",
            required=True,
            metavar="NAME=VALUE[,VALUE...]",
            help="Environment variable and managed resource values",
        )
        parser.add_argument(
            "--slots-per-resource",
            type=int,
            default=1,
            metavar="N",
            help="Maximum simultaneous experiments per resource group (default: 1)",
        )
        parser.add_argument(
            "--resources-per-run",
            type=int,
            default=1,
            metavar="N",
            help="Number of resource values assigned to each experiment (default: 1)",
        )

    def handle(self, *args, **options):
        alt_ids = options["alt_ids"]
        runner = options["runner"]
        runner_arguments = options["runner_argument"]
        slots_per_resource = options["slots_per_resource"]
        resources_per_run = options["resources_per_run"]

        experiment_model = self._get_experiment_model()
        self._validate_alt_ids(experiment_model, alt_ids)
        self._validate_runner(runner)
        resource_name, resource_values = self._parse_resource(options["resource"])
        if slots_per_resource < 1:
            raise CommandError("--slots-per-resource must be at least 1")
        if resources_per_run < 1:
            raise CommandError("--resources-per-run must be at least 1")
        if len(resource_values) % resources_per_run:
            raise CommandError(
                "The number of resource values must be divisible by "
                "--resources-per-run"
            )

        slots = deque(
            resource_group
            for resource_group in group_resource_values(
                resource_values,
                resources_per_run,
            )
            for _ in range(slots_per_resource)
        )
        pending = deque(alt_ids)
        running = []
        failures = []
        interrupted_by = None
        previous_handlers = self._install_signal_handlers()

        try:
            while pending or running:
                while pending and slots:
                    alt_id = pending.popleft()
                    resource = slots.popleft()
                    running.append(
                        self._launch(
                            runner,
                            runner_arguments,
                            alt_id,
                            resource_name,
                            resource,
                        )
                    )

                completed = [item for item in running if item.process.poll() is not None]
                if not completed:
                    time.sleep(0.1)
                    continue

                for item in completed:
                    running.remove(item)
                    slots.append(item.resource)
                    if self._record_result(experiment_model, item):
                        failures.append(item.alt_id)
        except SchedulerInterrupted as exc:
            interrupted_by = str(exc)
            self._terminate_all(running)
        except (KeyboardInterrupt, SystemExit):
            interrupted_by = "interrupt"
            self._terminate_all(running)
        except Exception:
            self._terminate_all(running)
            raise
        finally:
            self._restore_signal_handlers(previous_handlers)

        if interrupted_by is not None:
            raise CommandError(f"Experiment scheduling interrupted by {interrupted_by}")
        if failures:
            raise CommandError(
                f"{len(failures)} experiment(s) failed: {', '.join(failures)}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"All {len(alt_ids)} experiment(s) completed successfully.")
        )

    def _get_experiment_model(self):
        model_label = getattr(settings, EXPERIMENT_MODEL_SETTING, None)
        if not model_label:
            raise CommandError(
                f"Set {EXPERIMENT_MODEL_SETTING} to the concrete experiment model, "
                "for example 'my_app.Experiment'"
            )

        try:
            experiment_model = apps.get_model(model_label)
        except (LookupError, ValueError) as exc:
            raise CommandError(
                f"{EXPERIMENT_MODEL_SETTING} refers to unknown model {model_label!r}"
            ) from exc

        if experiment_model._meta.abstract or not issubclass(
            experiment_model, Experiment
        ):
            raise CommandError(
                f"{EXPERIMENT_MODEL_SETTING} must identify a concrete subclass of "
                "django_experiment_tracker.models.Experiment"
            )
        return experiment_model

    def _validate_alt_ids(self, experiment_model, alt_ids_list):
        alt_ids = set(alt_ids_list)
        if len(alt_ids_list) != len(alt_ids):
            raise CommandError("Experiment alternative IDs must not contain duplicates")

        existing = set(
            experiment_model.objects.filter(alt_id__in=alt_ids).values_list(
                "alt_id", flat=True
            )
        )
        missing = sorted(alt_ids - existing)
        if missing:
            raise CommandError(f"Unknown experiment(s): {', '.join(missing)}")

    def _validate_runner(self, runner):
        if runner == "run_experiments":
            raise CommandError("run_experiments cannot use itself as its runner")
        if runner not in get_commands():
            raise CommandError(f"Unknown management command runner {runner!r}")

    def _parse_resource(self, resource):
        if "=" not in resource:
            raise CommandError(
                "--resource must have the form NAME=VALUE[,VALUE...]"
            )
        name, raw_values = resource.split("=", 1)
        values = [value.strip() for value in raw_values.split(",")]
        if not ENVIRONMENT_VARIABLE_PATTERN.fullmatch(name):
            raise CommandError(f"Invalid environment variable name {name!r}")
        if not values or any(not value for value in values):
            raise CommandError("--resource must contain one or more nonempty values")
        if len(values) != len(set(values)):
            raise CommandError("Managed resource values must not contain duplicates")
        return name, values

    def _launch(self, runner, runner_arguments, alt_id, resource_name, resource):
        command = [
            sys.executable,
            "-m",
            "django",
            runner,
            alt_id,
            *runner_arguments,
        ]
        environment = os.environ.copy()
        environment[resource_name] = resource
        connections.close_all()
        try:
            process = subprocess.Popen(
                command,
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            raise CommandError(f"Could not launch experiment {alt_id}: {exc}") from exc

        self.stdout.write(
            f"Started {alt_id} with {resource_name}={resource} (pid {process.pid})."
        )
        return RunningExperiment(alt_id, resource, process)

    def _record_result(self, experiment_model, item):
        experiment = experiment_model.objects.get(alt_id=item.alt_id)
        failed = item.process.returncode != 0 or experiment.exit_code != 0
        status = "failed" if failed else "completed"
        self.stdout.write(
            f"Experiment {item.alt_id} {status} on resource {item.resource}: "
            f"runner exit code {item.process.returncode}, experiment exit code "
            f"{experiment.exit_code!r}."
        )
        return failed

    def _install_signal_handlers(self):
        previous_handlers = {}
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, self._handle_signal)
        return previous_handlers

    def _restore_signal_handlers(self, previous_handlers):
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)

    def _handle_signal(self, signal_number, frame):
        raise SchedulerInterrupted(signal.Signals(signal_number).name)

    def _terminate_all(self, running):
        for item in running:
            if item.process.poll() is None:
                try:
                    os.killpg(item.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(item.process.poll() is not None for item in running):
                return
            time.sleep(0.1)

        for item in running:
            if item.process.poll() is None:
                try:
                    os.killpg(item.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
