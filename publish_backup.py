"""Put the export (account tables and job history) into the private backup repository, history kept short (PLAN.md 9.1).

`python publish_backup.py <export directory> <remote> <work directory>` clones the remote into the work directory,
replaces the table files with the export, commits when something changed and pushes to `main`. The remote is the
PRIVATE repository `ravidorr/misra-backups` (this repository is public); the daily workflow gives it the write deploy
key through `GIT_SSH_COMMAND` (`scripts/backup-key.sh`). Both directories must be outside this checkout.

Retention. Deleting an account must delete it everywhere, and a git repository keeps history. So the history is
replaced when its first commit is older than MAX_AGE_DAYS: one new commit with no parent holds the current files and
is force-pushed over `main` (guarded by `--force-with-lease`, so it only replaces the history it saw). A person who
deleted their account therefore leaves the backups within MAX_AGE_DAYS plus one daily run, under the 30 days that
`PRIVACY.md` promises. GitHub may keep the replaced commits for a while before it erases them (it does not remove
them the moment a branch is force-pushed); `PRIVACY.md` says so. The force push only ever goes to the remote given on
the command line, which the workflow sets to the backup repository and nothing else.

Printed lines hold counts only; the commit messages hold counts only. The git child processes do not get the Supabase
or backup keys from this process's environment."""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from export_accounts import ALL_TABLES, ALLOW_SHRINK, ExportError, Table, fell_by_half, outside_repository

MAX_AGE_DAYS = 28  # the first commit is replaced at this age; PRIVACY.md promises 30 days
SECONDS_PER_DAY = 86400
BRANCH = "main"
IDENTITY = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
SECRET_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BACKUP_DEPLOY_KEY")
# Variables a git hook (or a caller) sets to point git at some other repository: the clone is the only one used.
LOCAL_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_COMMON_DIR")
STDERR_SHOWN = 300


class BackupError(Exception):
    """The backup cannot be published; the message is safe to print."""


def git_env() -> dict[str, str]:
    """This process's environment without the keys the git children never need and without any variable that would
    point git at another repository than the clone it is run in."""
    return {name: value for name, value in os.environ.items() if name not in SECRET_ENV + LOCAL_REPO_ENV}


def git(directory: Path, *args: str) -> str:
    """Run git in the directory; its output is returned, a failure raises with git's own error line (which names no
    data: a remote, a ref or an ssh error)."""
    try:
        done = subprocess.run(["git", *args], cwd=directory, env=git_env(), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise BackupError(f"git could not run: {type(exc).__name__}") from None
    if done.returncode:
        raise BackupError(f"git {args[0]} failed ({done.returncode}): {done.stderr.strip()[:STDERR_SHOWN]}")
    return done.stdout


def counts(source: Path, tables: Sequence[Table] = ALL_TABLES) -> dict[str, int]:
    """Rows per table of the export (the only thing the commit message and the log say about the data)."""
    out: dict[str, int] = {}
    for table in tables:
        try:
            rows = json.loads((source / f"{table.name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BackupError(f"{table.name}.json is missing or is not JSON in the export") from None
        if not isinstance(rows, list):
            raise BackupError(f"{table.name}.json is not a list")
        out[table.name] = len(rows)
    return out


def summary(numbers: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in numbers.items()) + f" ({sum(numbers.values())} rows)"


def open_clone(work: Path, remote: str) -> str | None:
    """Clone the remote's main into the work directory; returns its commit, or None when the remote is still empty."""
    work.mkdir(parents=True, mode=0o700, exist_ok=True)
    git(work, "init", "--quiet", "--initial-branch", BRANCH)
    git(work, "config", "user.name", IDENTITY[0])
    git(work, "config", "user.email", IDENTITY[1])
    git(work, "config", "commit.gpgsign", "false")
    git(work, "remote", "add", "origin", remote)
    if not git(work, "ls-remote", "--heads", "origin", BRANCH).strip():
        return None
    git(work, "fetch", "--quiet", "origin", BRANCH)
    tip = git(work, "rev-parse", "FETCH_HEAD").strip()
    git(work, "checkout", "--quiet", "-B", BRANCH, tip)
    return tip


def history_is_old(work: Path, now: datetime) -> bool:
    """Is the first commit of the history older than MAX_AGE_DAYS?"""
    firsts = git(work, "log", "--max-parents=0", "--format=%ct", BRANCH).split()
    return bool(firsts) and now.timestamp() - min(int(stamp) for stamp in firsts) > MAX_AGE_DAYS * SECONDS_PER_DAY


def place_files(work: Path, source: Path) -> None:
    """Make the clone's table files exactly the export's."""
    for table in ALL_TABLES:
        (work / f"{table.name}.json").write_bytes((source / f"{table.name}.json").read_bytes())


def previous_counts(work: Path) -> dict[str, int]:
    """Rows per table of the files at the tip of the clone. A table whose file is missing or odd has no entry, so only
    that table goes unjudged: the first backup after a table joins the export (the move of the history, step 9.1,
    added `jobs` and `crawl_log`) still guards the others, and the day after it guards that one too."""
    out: dict[str, int] = {}
    for table in ALL_TABLES:
        try:
            out.update(counts(work, (table,)))
        except BackupError:
            continue
    return out


def shrunk(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """The tables whose row count fell by more than half. A wrong key sees nothing through row-level security, and a
    change of policy can hide rows: neither may replace the good copy, least of all its history."""
    return [name for name, old in before.items() if fell_by_half(old, after.get(name, 0))]


def publish(source: Path, remote: str, work: Path, now: datetime | None = None, allow_shrink: bool = False) -> str:
    """Publish the export; returns one line of counts saying what happened (unchanged, committed, history replaced)."""
    now = now or datetime.now(UTC)
    source, work = outside_repository(source), outside_repository(work)
    numbers = counts(source)
    tip = open_clone(work, remote)
    fallen = [] if allow_shrink or tip is None else shrunk(previous_counts(work), numbers)
    if fallen:
        raise BackupError(
            f"{', '.join(fallen)} fell by more than half since the last backup: nothing was published "
            f"(a wrong key or a policy change?); if it is real, run again with {ALLOW_SHRINK}"
        )
    squash = tip is not None and history_is_old(work, now)
    if squash:
        git(work, "checkout", "--quiet", "--orphan", "replacement")
    place_files(work, source)
    git(work, "add", "--all")
    changed = bool(git(work, "status", "--porcelain").strip())
    if not changed and not squash:
        return f"backup unchanged: {summary(numbers)}"
    day = now.strftime("%Y-%m-%d")
    if squash:
        git(
            work,
            "commit",
            "--quiet",
            "-m",
            f"backup {day}: history replaced after {MAX_AGE_DAYS} days, {summary(numbers)}",
        )
        git(work, "push", "--quiet", f"--force-with-lease={BRANCH}:{tip}", "origin", f"HEAD:refs/heads/{BRANCH}")
        return f"backup history replaced: {summary(numbers)}"
    git(work, "commit", "--quiet", "-m", f"backup {day}: {summary(numbers)}")
    git(work, "push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}")
    return f"backup committed: {summary(numbers)}"


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    allow_shrink = ALLOW_SHRINK in args
    args = [arg for arg in args if arg != ALLOW_SHRINK]
    try:
        source, remote, work = args
    except ValueError:
        sys.exit("usage: python publish_backup.py <export directory> <remote> <work directory>")
    try:
        print(publish(Path(source), remote, Path(work), allow_shrink=allow_shrink))
    except (BackupError, ExportError) as exc:
        sys.exit(f"backup failed: {exc}")


if __name__ == "__main__":
    main()
