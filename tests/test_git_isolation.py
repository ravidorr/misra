"""No test can run git against the real repository, even when a git hook exported GIT_DIR and friends."""

import os
import subprocess
import sys
from pathlib import Path

TEST = "tests/test_git_isolation.py::test_the_hook_variables_never_reach_a_test"


def test_the_hook_variables_never_reach_a_test() -> None:
    assert not [name for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE") if name in os.environ]


def test_the_guard_works_when_the_variables_are_exported(tmp_path: Path) -> None:
    """Run the check above in a child pytest whose environment has the variables, pointed at a decoy repository."""
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True)
    before = (decoy / ".git" / "config").read_text(encoding="utf-8")
    env = {**os.environ, "GIT_DIR": str(decoy / ".git"), "GIT_INDEX_FILE": str(decoy / ".git" / "index")}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TEST],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-300:]
    assert (decoy / ".git" / "config").read_text(encoding="utf-8") == before
