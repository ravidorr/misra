"""The daily workflow's digest step (PLAN.md step 6.4): where it sits, which secrets it sees, the dry-run input and
that the legacy maintainer digest is gone. The workflow is read as text (no YAML parser is a dependency): steps start at
`      - ` with two-space steps indentation under `    steps:`."""

import os
import re
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

FULL = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
# The guard job comes first in the file. WORKFLOW is the file without it, so every test of the run job (its steps, its
# header, its secrets) reads exactly what it read before the guard existed; the guard has its own tests below.
GUARD = FULL[FULL.index("  guard:\n") : FULL.index("  run:\n")]
WORKFLOW = FULL.replace(GUARD, "")
DIGEST_SECRETS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "RESEND_API_KEY", "UNSUBSCRIBE_SECRET")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")


def steps() -> dict[str, str]:
    """Step id -> the text of the step (every step in this workflow has an id or is found by name)."""
    body = WORKFLOW.split("    steps:\n", 1)[1]
    chunks = re.split(r"(?m)^      - ", body)[1:]
    out: dict[str, str] = {}
    for chunk in chunks:
        found = re.search(r"(?m)^        id: (\S+)$", chunk)
        named = re.match(r"name: (.+)", chunk)
        out[found[1] if found else named[1] if named else chunk.split("\n", 1)[0]] = chunk
    return out


def order() -> list[str]:
    return list(steps())


def test_the_digest_step_runs_after_the_sync_and_before_the_build_and_the_deploy_push() -> None:
    ids = order()
    assert ids.index("sync") < ids.index("digests") < ids.index("commit")
    assert ids.index("digests") < ids.index("Build dashboard")
    assert ids.index("crawl") < ids.index("sync")


def test_the_digest_step_runs_the_script_and_only_after_a_good_sync_whatever_the_earlier_steps_did() -> None:
    step = steps()["digests"]
    assert "run: python send_digests.py $DRY_RUN_FLAG\n" in step
    # fresh rows only, and not skipped because an earlier step was red
    assert "if: ${{ always() && steps.sync.outcome == 'success' }}" in step
    assert "continue-on-error" not in WORKFLOW  # a failure must fail the run, not hide behind a green job


def test_the_digest_step_is_the_only_step_with_the_resend_and_unsubscribe_secrets() -> None:
    for name, step in steps().items():
        used = set(SECRET_REF.findall(step))
        if name == "digests":
            assert set(DIGEST_SECRETS) <= used
            assert used == set(DIGEST_SECRETS)
        else:
            assert not used & {"RESEND_API_KEY", "UNSUBSCRIBE_SECRET"}, name
    # the service-role key is shared with the sync step and the backup step (test_backup_workflow.py), nothing else
    holders = {name for name, step in steps().items() if "SUPABASE_SERVICE_ROLE_KEY" in step}
    assert holders == {"sync", "digests", "backup"}


def test_no_secret_is_set_at_the_workflow_or_job_level_and_the_crawl_has_no_env() -> None:
    header = WORKFLOW.split("    steps:\n", 1)[0]
    assert "secrets." not in header
    assert "env:" not in steps()["crawl"]
    assert "env:" not in steps()["checkout"]


def test_the_dry_run_input_is_a_boolean_that_only_adds_the_flag() -> None:
    assert re.search(r"(?m)^    inputs:\n      digest_dry_run:\n(?:        .+\n)*?        type: boolean\n", WORKFLOW)
    assert re.search(r"(?m)^        default: false$", WORKFLOW)
    step = steps()["digests"]
    assert "DRY_RUN_FLAG: ${{ inputs.digest_dry_run && '--dry-run' || '' }}" in step
    assert "inputs." not in step.split("run:", 1)[1]  # nothing from the inputs is spliced into the shell command


def test_a_failed_digest_still_builds_and_commits_and_triggers_the_alert() -> None:
    all_steps = steps()
    assert "always() && steps.checkout.outcome == 'success'" in all_steps["Build dashboard"]
    assert "always() && steps.checkout.outcome == 'success'" in all_steps["commit"]
    alert = next(step for name, step in all_steps.items() if name.startswith("Email an alert"))
    assert "failure() || cancelled()" in alert
    assert "digests=${{ steps.digests.outcome }}" in alert
    assert "digest=${{ steps.digest.outcome }}" not in alert  # the legacy step is gone
    assert "python3 send_alert.py" in alert


def test_the_legacy_maintainer_digest_is_gone() -> None:
    assert "LEGACY_MAINTAINER_DIGEST" not in WORKFLOW
    assert "send_email" not in WORKFLOW
    assert "digest" not in steps()  # no step of that id; the subscriber digests are "digests"
    assert "\nenv:" not in WORKFLOW  # no workflow-level constants (and never a secret)


@pytest.mark.parametrize("secret", ["GMAIL_USER", "GMAIL_APP_PASSWORD", "DIGEST_TO"])
def test_only_the_failure_alert_holds_the_gmail_secrets(secret: str) -> None:
    holders = [name for name, step in steps().items() if f"secrets.{secret}" in step]
    assert [name for name in holders if name.startswith("Email an alert")] == holders
    assert len(holders) == 1


def test_the_gaps_step_follows_the_crawl_runs_after_it_and_fails_the_run_without_stopping_the_sync() -> None:
    ids = order()
    assert ids.index("crawl") + 1 == ids.index("gaps") < ids.index("sync")
    step = steps()["gaps"]
    assert "if: ${{ always() && steps.crawl.outcome == 'success' }}" in step
    assert "run: python run.py --gaps\n" in step
    assert re.search(r"timeout-minutes: \d+", step)
    assert "secrets." not in step  # it reads the committed database and needs nothing secret
    assert "gaps=${{ steps.gaps.outcome }}" in next(s for n, s in steps().items() if n.startswith("Email an alert"))
    assert "continue-on-error" not in step
    for name, other in steps().items():  # a broken check never blocks the sync, the digests, the build or the commit
        assert name in ("gaps",) or name.startswith("Email an alert") or "steps.gaps" not in other, name


# ---- The schedule and the guard (a scheduled run that never starts, a second try, and when it is skipped) ----


def crons() -> list[str]:
    return re.findall(r'(?m)^    - cron: "([^"]+)"', FULL)


def test_there_are_two_scheduled_runs_a_few_hours_apart_and_neither_is_on_a_crowded_minute() -> None:
    assert crons() == ["23 4 * * *", "53 9 * * *"]
    for cron in crons():
        minute, _hour, rest = cron.split(" ", 2)
        assert rest == "* * *"  # every day
        assert minute not in {"0", "00", "30"}  # GitHub delays or drops runs at :00 and :30
    first, second = (int(c.split()[1]) for c in crons())
    assert second - first in range(3, 9)  # a few hours later, not the same morning
    assert "UTC" in FULL.split("on:\n", 1)[1].split("workflow_dispatch", 1)[0]  # the Israel-time comment is kept


def test_the_manual_start_and_its_dry_run_input_are_unchanged() -> None:
    assert re.search(r"(?m)^  workflow_dispatch:\n    inputs:\n      digest_dry_run:\n", FULL)


def test_the_guard_job_only_looks_it_holds_no_secret_and_writes_nothing() -> None:
    assert not SECRET_REF.findall(GUARD)
    assert "secrets." not in GUARD
    assert "GH_TOKEN: ${{ github.token }}" in GUARD  # the default token, read-only below
    assert "permissions:\n      actions: read\n      contents: read\n" in GUARD
    for forbidden in ("checkout", "git push", "git commit", "ssh-key", "deploy-key"):
        assert forbidden not in GUARD, forbidden
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert f"-X {method}" not in GUARD, method  # the one API call is a GET
    assert "continue-on-error" not in FULL


def test_the_guard_runs_on_a_schedule_only_and_counts_only_green_scheduled_runs_of_this_workflow_today() -> None:
    assert "    if: ${{ github.event_name == 'schedule' }}\n" in GUARD
    script = GUARD.split("run: |\n", 1)[1]
    assert "actions/workflows/daily.yml/runs" in script
    for param in ("event=schedule", "status=success", "created=>=${today}", "--jq .total_count"):
        assert param in script, param
    assert 'today="$(date -u +%F)"' in script  # the UTC day, like the commit message
    assert "outputs:\n      skip: ${{ steps.check.outputs.skip }}\n" in GUARD


def test_the_guard_fails_open_the_skip_output_is_true_only_for_a_positive_count() -> None:
    script = GUARD.split("run: |\n", 1)[1]
    assert script.index("skip=false") < script.index("skip=true")  # the default is to run
    assert "set -e" not in script.replace("set -uo", "")  # a failed API call must not fail the job
    assert '[[ "$count" =~ ^[0-9]+$ ]]' in script  # an odd answer is not a skip
    assert "could not read the earlier runs; running the job anyway" in script
    assert script.count("skip=true") == 1
    assert script.rstrip().endswith('echo "skip=${skip}" >> "$GITHUB_OUTPUT"')  # one write, after the decision


@pytest.mark.parametrize(
    ("stub", "expected"),
    [
        ("echo 1", "skip=true"),
        ("echo 3", "skip=true"),
        ("echo 0", "skip=false"),
        ("echo garbage", "skip=false"),  # an odd answer is not a skip
        ("echo '<html>rate limited</html>'", "skip=false"),
        ("exit 1", "skip=false"),  # the API call failed: fail open, and the step still succeeds
    ],
)
def test_the_guard_script_decides_from_the_count_and_never_fails(tmp_path: Path, stub: str, expected: str) -> None:
    script = textwrap.dedent(GUARD.split("run: |\n", 1)[1])
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(f"#!/bin/bash\n{stub}\n", encoding="utf-8")
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
    output = tmp_path / "output"
    env = {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "owner/repo",
    }
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert output.read_text(encoding="utf-8") == f"{expected}\n"


def test_the_run_job_needs_the_guard_and_is_skipped_only_on_a_schedule_when_the_guard_said_skip() -> None:
    header = WORKFLOW.split("    steps:\n", 1)[0]
    assert "    needs: guard\n" in header
    expression = re.search(r"(?m)^    if: \$\{\{ (.+) \}\}$", header)
    assert expression
    assert expression[1] == "!cancelled() && (github.event_name != 'schedule' || needs.guard.outputs.skip != 'true')"
    # a manual run is never skipped, a failed or empty guard does not skip (fail open), only the string 'true' does
    assert "needs.guard.result" not in expression[1]


def test_the_failure_alert_belongs_to_the_run_job_so_a_skipped_run_sends_nothing() -> None:
    assert "Email an alert" in WORKFLOW
    assert "Email an alert" not in GUARD
    assert "send_alert" not in GUARD


def test_the_run_job_keeps_its_permissions_group_and_secret_scope_with_the_guard_added() -> None:
    assert FULL.count("group: daily-job-digest\n  cancel-in-progress: false") == 1  # the group is unchanged
    assert FULL.index("\npermissions:\n  contents: read\n") < FULL.index("jobs:")  # the default stays read-only
    assert "permissions:" not in WORKFLOW.split("jobs:", 1)[1]  # the run job asks for nothing more
    assert "ssh-key:" not in FULL
    used = {name for step in steps().values() for name in SECRET_REF.findall(step)}
    assert used == {
        "DEPLOY_KEY",
        "GMAIL_USER",
        "GMAIL_APP_PASSWORD",
        "DIGEST_TO",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "RESEND_API_KEY",
        "UNSUBSCRIBE_SECRET",
        "BACKUP_DEPLOY_KEY",
    }
