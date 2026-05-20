import subprocess
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from django_experiment_tracker.models import GitCommit


class Command(BaseCommand):
    help = "Record git commit metadata into django_experiment_tracker.GitCommit"
    output_transaction = True
    requires_migrations_checks = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--backfill",
            type=int,
            default=0,
            help="Record last N commits reachable from HEAD",
        )
        parser.add_argument(
            "--stdin-rewrite-map",
            action="store_true",
            help="Read post-rewrite stdin map (old_sha new_sha) and record new SHAs",
        )

    def handle(self, *args, **options):
        backfill = options["backfill"]
        from_stdin_map = options["stdin_rewrite_map"]

        if from_stdin_map:
            shas = self._read_rewrite_new_shas()
            if backfill > 0:
                shas.extend(self._list_recent_shas(backfill))
        elif backfill > 0:
            shas = self._list_recent_shas(backfill)
        else:
            shas = [self._git(["rev-parse", "HEAD"]) ]

        unique_shas = self._dedupe_keep_order(shas)
        if not unique_shas:
            self.stdout.write(self.style.WARNING("No commits found to record."))
            return

        created_count = 0
        updated_count = 0
        for sha in unique_shas:
            normalized_sha = self._git(["rev-parse", sha])
            branch = self._branch_for_sha(normalized_sha)
            commit_time = self._commit_time(normalized_sha)
            _, created = GitCommit.objects.update_or_create(
                commit_sha=normalized_sha,
                defaults={"branch": branch, "commit_time": commit_time},
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Recorded {len(unique_shas)} commit(s): {created_count} created, {updated_count} updated."
            )
        )

    def _read_rewrite_new_shas(self):
        shas = []
        while True:
            try:
                line = input().strip()
            except EOFError:
                break
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            shas.append(parts[1])
        return shas

    def _list_recent_shas(self, count):
        if count <= 0:
            return []
        output = self._git(["rev-list", "--max-count", str(count), "HEAD"])
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _branch_for_sha(self, sha):
        output = self._git(["branch", "--contains", sha, "--format", "%(refname:short)"])
        branches = [line.strip() for line in output.splitlines() if line.strip()]
        if not branches:
            return "DETACHED"
        return branches[0]

    def _commit_time(self, sha):
        iso = self._git(["show", "-s", "--format=%cI", sha])
        commit_time = datetime.fromisoformat(iso)
        if timezone.is_naive(commit_time):
            return timezone.make_aware(commit_time, timezone=timezone.utc)
        return commit_time

    def _git(self, args):
        try:
            proc = subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CommandError("git is not available in PATH") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise CommandError(f"git {' '.join(args)} failed: {stderr}") from exc
        return proc.stdout.strip()

    def _dedupe_keep_order(self, items):
        seen = set()
        unique = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique
