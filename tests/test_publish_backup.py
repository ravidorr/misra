"""publish_backup.py against real temporary git repositories (a bare one stands in for the private backup repository):
commit only when something changed, counts-only messages, and the retention rule that replaces old history."""

import json
import runpy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import export_accounts
import publish_backup
from export_accounts import TABLES, render
from publish_backup import MAX_AGE_DAYS, BackupError, counts, publish

NOW = datetime.now(UTC)  # commits are really made now (unless a test sets the git dates), so the ages are real
DAY = NOW.strftime("%Y-%m-%d")
TWO_COMMITS, PROMISED_DAYS = 2, 30
ANN, BOB = "ann@test.example", "bob@test.example"


def run(directory: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, check=True, env=publish_backup.git_env()
    )
    return done.stdout


def make_export(directory: Path, emails: tuple[str, ...] = (ANN, BOB)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    content: dict[str, list[dict[str, Any]]] = {table.name: [] for table in TABLES}
    content["profiles"] = [
        {"user_id": f"{n:08d}-0000-4000-8000-000000000000", "email": e} for n, e in enumerate(emails)
    ]
    for name, rows in content.items():
        (directory / f"{name}.json").write_text(render(rows), encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def no_inherited_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """A git hook (pre-push runs this suite) sets GIT_DIR and friends. They must never reach these tests' temporary
    repositories: with them set, `git init`, `git config` and `git commit` would act on the real checkout."""
    for name in publish_backup.LOCAL_REPO_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    bare = tmp_path / "backups.git"
    bare.mkdir()
    run(bare, "init", "--quiet", "--bare", "--initial-branch", "main")
    return bare


def history(remote: Path) -> list[str]:
    return run(remote, "log", "--format=%s", "main").splitlines()


def at(monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
    for name in ("GIT_COMMITTER_DATE", "GIT_AUTHOR_DATE"):
        monkeypatch.setenv(name, when.isoformat())


def test_the_first_publish_into_an_empty_remote_commits_the_files_with_a_counts_only_message(
    tmp_path: Path, remote: Path
) -> None:
    line = publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w", NOW)
    assert line == "backup committed: profiles 2, user_prefs 0, user_jobs 0, subscriptions 0, emailed 0 (2 rows)"
    assert history(remote) == [
        f"backup {DAY}: profiles 2, user_prefs 0, user_jobs 0, subscriptions 0, emailed 0 (2 rows)"
    ]
    files = run(remote, "ls-tree", "--name-only", "main").split()
    assert files == sorted(f"{table.name}.json" for table in TABLES)
    shown = run(remote, "show", "main:profiles.json")
    assert ANN in shown  # the data is in the backup repository, and only there
    assert ANN not in history(remote)[0]
    assert run(remote, "log", "-1", "--format=%an <%ae>", "main").strip() == (
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
    )


def test_an_identical_export_makes_no_commit(tmp_path: Path, remote: Path) -> None:
    export = make_export(tmp_path / "x")
    publish(export, str(remote), tmp_path / "w1", NOW)
    line = publish(export, str(remote), tmp_path / "w2", NOW + timedelta(days=1))
    assert line == "backup unchanged: profiles 2, user_prefs 0, user_jobs 0, subscriptions 0, emailed 0 (2 rows)"
    assert len(history(remote)) == 1


def test_a_change_makes_one_more_commit_and_history_is_kept_while_it_is_young(tmp_path: Path, remote: Path) -> None:
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w1", NOW)
    line = publish(
        make_export(tmp_path / "x", (ANN,)), str(remote), tmp_path / "w2", NOW + timedelta(days=MAX_AGE_DAYS)
    )
    assert line.startswith("backup committed: profiles 1,")
    assert len(history(remote)) == TWO_COMMITS
    assert BOB in run(remote, "log", "-p", "main")  # still in the older commit: allowed until the history is replaced


def test_history_older_than_the_limit_is_replaced_by_one_commit_with_no_parent(
    tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at(monkeypatch, NOW - timedelta(days=MAX_AGE_DAYS + 12))
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w1", NOW - timedelta(days=MAX_AGE_DAYS + 12))
    at(monkeypatch, NOW - timedelta(days=MAX_AGE_DAYS + 7))
    publish(
        make_export(tmp_path / "x", (ANN, BOB, "cy@test.example")),
        str(remote),
        tmp_path / "w2",
        NOW - timedelta(days=MAX_AGE_DAYS + 7),
    )
    assert len(history(remote)) == TWO_COMMITS
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    # Bob deleted his account: today's export no longer has him
    line = publish(make_export(tmp_path / "x", (ANN,)), str(remote), tmp_path / "w3", NOW)
    assert line.startswith("backup history replaced: profiles 1,")
    assert len(history(remote)) == 1
    assert history(remote)[0].startswith(f"backup {DAY}: history replaced after {MAX_AGE_DAYS} days, profiles 1,")
    assert run(remote, "rev-list", "--max-parents=0", "main").split() == run(remote, "rev-list", "main").split()
    everything = run(remote, "log", "-p", "--all")
    assert BOB not in everything and "cy@test.example" not in everything
    assert ANN in run(remote, "show", "main:profiles.json")
    assert run(remote, "for-each-ref", "--format=%(refname)").split() == ["refs/heads/main"]


def test_the_history_is_replaced_even_when_nothing_changed_that_day(
    tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = NOW - timedelta(days=MAX_AGE_DAYS + 1)
    at(monkeypatch, old)
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w1", old)
    publish(make_export(tmp_path / "x", (ANN,)), str(remote), tmp_path / "w1b", old)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    line = publish(make_export(tmp_path / "x", (ANN,)), str(remote), tmp_path / "w2", NOW)
    assert line.startswith("backup history replaced:")
    assert len(history(remote)) == 1
    # the next day starts the new history: young, unchanged, nothing to do
    assert publish(
        make_export(tmp_path / "x", (ANN,)), str(remote), tmp_path / "w3", NOW + timedelta(days=1)
    ).startswith("backup unchanged")


def test_history_just_under_the_limit_is_kept(tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at(monkeypatch, NOW - timedelta(days=MAX_AGE_DAYS, minutes=-5))
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w1", NOW)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    assert publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w2", NOW).startswith("backup unchanged")


def test_the_limit_is_under_the_thirty_days_the_privacy_notice_promises() -> None:
    assert MAX_AGE_DAYS + 1 < PROMISED_DAYS  # plus one daily run, and a day of slack for a failed run's retry


def test_the_clone_is_a_separate_directory_and_the_checkout_is_not_touched(tmp_path: Path, remote: Path) -> None:
    def listing() -> list[str]:
        return sorted(path.name for path in export_accounts.REPO_ROOT.iterdir())

    before = listing()
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w", NOW)
    assert listing() == before  # no table file, no clone, nothing new in the checkout
    assert export_accounts.REPO_ROOT not in (tmp_path / "w").resolve().parents
    assert (tmp_path / "w" / ".git").is_dir()


def test_a_directory_inside_the_checkout_is_refused(tmp_path: Path, remote: Path) -> None:
    inside = export_accounts.REPO_ROOT / "tests" / "no-such-work-dir"
    with pytest.raises(export_accounts.ExportError, match="outside it"):
        publish(make_export(tmp_path / "x"), str(remote), inside, NOW)
    with pytest.raises(export_accounts.ExportError, match="outside it"):
        publish(inside, str(remote), tmp_path / "w", NOW)
    assert not inside.exists()


def test_an_unreachable_remote_fails_with_gits_own_error_line(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match=r"^git ls-remote failed \(\d+\): "):
        publish(make_export(tmp_path / "x"), str(tmp_path / "nowhere.git"), tmp_path / "w", NOW)


def test_a_push_that_the_remote_refuses_is_an_error(tmp_path: Path, remote: Path) -> None:
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with pytest.raises(BackupError, match=r"^git push failed"):
        publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w", NOW)


def test_a_history_that_moved_after_it_was_read_is_not_overwritten(
    tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replacement is pushed with --force-with-lease: it only replaces the history it read."""
    old = NOW - timedelta(days=MAX_AGE_DAYS + 5)
    at(monkeypatch, old)
    publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w1", old)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    real = publish_backup.git

    def racing(directory: Path, *args: str) -> str:
        if args[0] == "push":  # someone else pushes between the fetch and the push
            other = tmp_path / "other"
            run(tmp_path, "clone", "--quiet", str(remote), str(other))
            (other / "extra.txt").write_text("x", encoding="utf-8")
            run(other, "add", "extra.txt")
            run(other, "-c", "user.name=o", "-c", "user.email=o@o", "commit", "--quiet", "-m", "other")
            run(other, "push", "--quiet", "origin", "HEAD:main")
        return real(directory, *args)

    monkeypatch.setattr(publish_backup, "git", racing)
    with pytest.raises(BackupError, match=r"^git push failed"):
        publish(make_export(tmp_path / "x"), str(remote), tmp_path / "w2", NOW)
    assert history(remote)[0] == "other"


def test_the_git_children_do_not_get_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in publish_backup.SECRET_ENV:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i key")
    env = publish_backup.git_env()
    assert not set(publish_backup.SECRET_ENV) & set(env)
    assert env["GIT_SSH_COMMAND"] == "ssh -i key"  # the one thing the push needs
    for name in publish_backup.LOCAL_REPO_ENV:  # a hook that runs the tests sets these; they must not reach the clone
        monkeypatch.setenv(name, "/elsewhere")
    assert not set(publish_backup.LOCAL_REPO_ENV) & set(publish_backup.git_env())


def test_git_missing_is_an_error_not_a_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(publish_backup.subprocess, "run", broken)
    with pytest.raises(BackupError, match=r"git could not run: FileNotFoundError"):
        publish_backup.git(tmp_path, "status")


@pytest.mark.parametrize("bad", ["missing", "not json", "dict"])
def test_a_bad_export_is_refused_before_anything_is_cloned(tmp_path: Path, remote: Path, bad: str) -> None:
    export = make_export(tmp_path / "x")
    target = export / "user_jobs.json"
    if bad == "missing":
        target.unlink()
    else:
        target.write_text("{oops" if bad == "not json" else json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(BackupError, match=r"user_jobs.json is (missing or is not JSON|not a list)"):
        publish(export, str(remote), tmp_path / "w", NOW)
    assert not (tmp_path / "w").exists()
    assert run(remote, "for-each-ref").strip() == ""


def test_counts_are_rows_per_table(tmp_path: Path) -> None:
    assert counts(make_export(tmp_path / "x")) == {
        "profiles": 2,
        "user_prefs": 0,
        "user_jobs": 0,
        "subscriptions": 0,
        "emailed": 0,
    }


def test_main_prints_one_line_of_counts(tmp_path: Path, remote: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish_backup.main([str(make_export(tmp_path / "x")), str(remote), str(tmp_path / "w")])
    shown = capsys.readouterr().out
    assert shown.startswith("backup committed: profiles 2,")
    assert ANN not in shown


def test_main_exits_with_the_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"^backup failed: profiles.json is missing"):
        publish_backup.main([str(tmp_path / "empty"), str(tmp_path / "r"), str(tmp_path / "w")])
    inside = export_accounts.REPO_ROOT / "tests" / "no-such-dir"
    with pytest.raises(SystemExit, match=r"^backup failed: refusing to write personal data"):
        publish_backup.main([str(inside), str(tmp_path / "r"), str(tmp_path / "w")])


@pytest.mark.parametrize("args", [[], ["a", "b"], ["a", "b", "c", "d"]])
def test_main_needs_three_arguments(args: list[str]) -> None:
    with pytest.raises(SystemExit, match="usage"):
        publish_backup.main(args)


def test_running_the_module_reads_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["publish_backup.py"])
    monkeypatch.delitem(sys.modules, "publish_backup", raising=False)
    with pytest.raises(SystemExit, match="usage"):
        runpy.run_module("publish_backup", run_name="__main__")


def big_export(directory: Path, rows: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        count = rows if table.name == "profiles" else 0
        content = [{"user_id": f"{n:08d}-0000-4000-8000-000000000000"} for n in range(count)]
        (directory / f"{table.name}.json").write_text(render(content), encoding="utf-8")
    return directory


def test_an_export_that_lost_most_rows_is_refused_and_never_squashes(
    tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = NOW - timedelta(days=MAX_AGE_DAYS + 3)
    at(monkeypatch, old)
    publish(big_export(tmp_path / "x", 10), str(remote), tmp_path / "w1", old)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    before = history(remote)
    with pytest.raises(BackupError, match=r"profiles fell by more than half.*--allow-shrink"):
        publish(big_export(tmp_path / "e", 0), str(remote), tmp_path / "w2", NOW)
    assert history(remote) == before  # the good copy and its history are still there
    assert "backup history replaced" in publish(big_export(tmp_path / "e", 0), str(remote), tmp_path / "w3", NOW, True)


def test_a_small_loss_is_not_a_shrink(tmp_path: Path, remote: Path) -> None:
    publish(big_export(tmp_path / "x", 10), str(remote), tmp_path / "w1", NOW)
    assert publish(big_export(tmp_path / "e", 6), str(remote), tmp_path / "w2", NOW).startswith("backup committed")
    assert publish_backup.shrunk({"a": 4}, {"a": 0}) == []  # too few rows to judge


def test_main_passes_the_allow_shrink_flag(tmp_path: Path, remote: Path, capsys: pytest.CaptureFixture[str]) -> None:
    publish_backup.main([str(big_export(tmp_path / "x", 10)), str(remote), str(tmp_path / "w1")])
    publish_backup.main([str(big_export(tmp_path / "e", 0)), str(remote), str(tmp_path / "w2"), "--allow-shrink"])
    assert "backup committed: profiles 0" in capsys.readouterr().out


def test_a_remote_whose_tip_has_no_table_files_is_not_judged_as_shrunk(tmp_path: Path, remote: Path) -> None:
    seed = tmp_path / "seed"
    run(tmp_path, "clone", "--quiet", str(remote), str(seed))
    (seed / "README.md").write_text("x", encoding="utf-8")
    run(seed, "add", "README.md")
    run(seed, "-c", "user.name=o", "-c", "user.email=o@o", "commit", "--quiet", "-m", "readme")
    run(seed, "push", "--quiet", "origin", "HEAD:main")
    assert publish(big_export(tmp_path / "x", 0), str(remote), tmp_path / "w", NOW).startswith("backup committed")
