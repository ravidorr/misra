"""The backup step of the daily workflow (PLAN.md step 9.1): where it sits, which secrets it sees, how it handles the
key of the private backup repository and the personal data. The workflow is read as text, as in test_daily_workflow."""

import re
from pathlib import Path

from tests.test_daily_workflow import SECRET_REF, WORKFLOW, order, steps

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SECRETS = {"SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BACKUP_DEPLOY_KEY"}
KEY_SCRIPT = (ROOT / "scripts" / "backup-key.sh").read_text(encoding="utf-8")
HEADER = WORKFLOW.split("    steps:\n", 1)[0]
REMOTE = "git@github.com:ravidorr/misra-backups.git"


def script_of(step: str) -> str:
    return step.split("run: |\n", 1)[1]


def alert() -> str:
    return next(step for name, step in steps().items() if name.startswith("Email an alert"))


def test_the_backup_step_runs_after_the_digests_and_the_data_commit_and_before_the_alert() -> None:
    ids = order()
    alert_id = next(name for name in ids if name.startswith("Email an alert"))
    assert ids.index("digests") < ids.index("commit") < ids.index("backup") < ids.index(alert_id)


def test_the_backup_runs_on_every_run_whatever_the_earlier_steps_did_and_has_no_input_to_skip_it() -> None:
    step = steps()["backup"]
    assert "if: ${{ always() && steps.checkout.outcome == 'success' }}" in step
    assert "timeout-minutes: 10" in step
    assert "inputs." not in step
    assert "backup" not in HEADER  # nothing at the workflow's head (inputs, env) switches it off


def test_the_backup_step_is_the_only_one_with_the_backup_key_and_its_secrets_are_exactly_three() -> None:
    for name, step in steps().items():
        if name == "backup":
            assert set(SECRET_REF.findall(step)) == BACKUP_SECRETS
        else:
            assert "BACKUP_DEPLOY_KEY" not in step, name
    assert "BACKUP_DEPLOY_KEY" not in HEADER


def test_the_backup_step_exports_before_the_key_exists_to_a_directory_outside_the_checkout() -> None:
    run = script_of(steps()["backup"])
    assert (
        run.index("python export_accounts.py")
        < run.index(". scripts/backup-key.sh")
        < run.index("python publish_backup.py")
    )
    assert 'BACKUP_WORK="$(mktemp -d "$RUNNER_TEMP/backup.XXXXXX")"' in run
    assert 'python export_accounts.py "$BACKUP_WORK/export"' in run
    assert f'python publish_backup.py "$BACKUP_WORK/export" "{REMOTE}" "$BACKUP_WORK/clone"' in run
    assert "GITHUB_WORKSPACE" not in run and "$PWD" not in run
    assert "trap 'rm -rf \"$BACKUP_WORK\"' EXIT" in run  # the data goes even when the export itself fails
    assert "refs/heads/main" in run  # the same guard as the deploy push
    assert "BACKUP_DEPLOY_KEY secret is missing" in run  # an unset secret is an error, not a silent skip


def test_the_backup_step_prints_nothing_of_the_data_and_uploads_nothing() -> None:
    run = script_of(steps()["backup"])
    code = "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))
    for risky in ("cat ", "tee ", "set -x", "ls ", "cp ", "upload-artifact", "echo $", 'echo "$BACKUP'):
        assert risky not in code, risky
    uploads = [name for name, text in steps().items() if "upload-artifact" in text]
    assert uploads == []  # no artifact at all: the history is in Supabase, the account data must not be uploaded


def test_no_checkout_gets_a_key_and_the_two_keys_stay_apart() -> None:
    assert "ssh-key:" not in WORKFLOW
    step = steps()["backup"]
    assert "secrets.DEPLOY_KEY" not in step
    assert "deploy-key.sh" not in step
    assert "backup-key.sh" not in steps()["commit"]
    assert "backup-key.sh" not in steps()["verify"]


def test_the_key_script_uses_a_unique_key_file_only_that_key_and_removes_everything() -> None:
    assert 'key="$(mktemp "$RUNNER_TEMP/backup_key.XXXXXX")"' in KEY_SCRIPT
    assert 'hosts="$(mktemp "$RUNNER_TEMP/backup_hosts.XXXXXX")"' in KEY_SCRIPT
    assert "umask 077" in KEY_SCRIPT
    ssh = re.search(r'export GIT_SSH_COMMAND="(.+)"', KEY_SCRIPT)
    assert ssh
    for option in (
        "-i $key",
        "IdentitiesOnly=yes",
        "IdentityAgent=none",
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=$hosts",
    ):
        assert option in ssh[1]
    assert re.search(r"trap '[^']*rm -rf \"\$key\" \"\$hosts\"[^']*BACKUP_WORK[^']*' EXIT", KEY_SCRIPT)
    assert "set -x" not in KEY_SCRIPT
    assert "secrets." not in KEY_SCRIPT


def test_a_failed_backup_fails_the_run_and_is_named_in_the_alert_without_blocking_the_data() -> None:
    all_steps = steps()
    assert "backup=${{ steps.backup.outcome }}" in alert()
    assert "failure() || cancelled()" in alert()
    assert "continue-on-error" not in WORKFLOW
    for name in ("commit", "build"):
        assert "steps.backup" not in all_steps[name]  # nothing the deploy needs waits for it


def test_the_backup_shares_the_daily_concurrency_group_and_the_job_stays_read_only() -> None:
    assert "group: daily-job-digest\n  cancel-in-progress: false" in HEADER
    assert "permissions:\n  contents: read" in HEADER
