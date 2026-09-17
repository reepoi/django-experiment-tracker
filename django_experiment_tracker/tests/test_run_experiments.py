from unittest.mock import Mock, patch

import pytest
from django.core.management.base import CommandError

from django_experiment_tracker.management.commands.run_experiments import (
    Command,
    group_resource_values,
)


def test_groups_contiguous_resource_values():
    assert group_resource_values(["0", "1", "2", "3"], 2) == ["0,1", "2,3"]


def test_launch_passes_grouped_resource_and_runner_arguments():
    process = Mock(pid=123)
    with (
        patch(
            "django_experiment_tracker.management.commands.run_experiments.subprocess.Popen",
            return_value=process,
        ) as popen,
        patch(
            "django_experiment_tracker.management.commands.run_experiments.connections.close_all"
        ),
    ):
        running = Command()._launch(
            "run_workflow",
            ["--workflow=train", "--training-tasks=2"],
            "exp_example",
            "CUDA_VISIBLE_DEVICES",
            "0,1",
        )

    command = popen.call_args.args[0]
    assert command[-4:] == [
        "run_workflow",
        "exp_example",
        "--workflow=train",
        "--training-tasks=2",
    ]
    assert popen.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "0,1"
    assert running.resource == "0,1"


@pytest.mark.parametrize("resources_per_run", [0, -1])
def test_rejects_invalid_resources_per_run(resources_per_run):
    command = Command()
    with (
        patch.object(command, "_get_experiment_model"),
        patch.object(command, "_validate_alt_ids"),
        patch.object(command, "_validate_runner"),
        pytest.raises(CommandError, match="resources-per-run must be at least 1"),
    ):
        command.handle(
            alt_ids=["exp_example"],
            runner="runner",
            runner_argument=[],
            resource="CUDA_VISIBLE_DEVICES=0,1",
            slots_per_resource=1,
            resources_per_run=resources_per_run,
        )


def test_rejects_incomplete_resource_group():
    command = Command()
    with (
        patch.object(command, "_get_experiment_model"),
        patch.object(command, "_validate_alt_ids"),
        patch.object(command, "_validate_runner"),
        pytest.raises(CommandError, match="must be divisible"),
    ):
        command.handle(
            alt_ids=["exp_example"],
            runner="runner",
            runner_argument=[],
            resource="CUDA_VISIBLE_DEVICES=0,1,2",
            slots_per_resource=1,
            resources_per_run=2,
        )
