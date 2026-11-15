"""The daily workflow's digest step (PLAN.md step 6.4): where it sits, which secrets it sees, the dry-run input and
the legacy maintainer digest switch. The workflow is read as text (no YAML parser is a dependency): steps start at
`      - ` with two-space steps indentation under `    steps:`."""

import re
from pathlib import Path

import pytest

WORKFLOW = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
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
    assert "digest=${{ steps.digest.outcome }}" in alert
    assert "python3 send_alert.py" in alert


def test_the_maintainer_digest_stays_behind_one_named_switch_that_is_on() -> None:
    switch = re.search(r'(?m)^env:\n(?:  #.*\n)*  LEGACY_MAINTAINER_DIGEST: "(true|false)"$', WORKFLOW)
    assert switch, "the switch is a workflow-level env constant"
    assert switch[1] == "true", "the maintainer flips it when subscribed, not before (PLAN.md step 6.4)"
    legacy = steps()["digest"]
    assert "if: ${{ success() && env.LEGACY_MAINTAINER_DIGEST == 'true' }}" in legacy
    assert "run: python send_email.py" in legacy
    assert "success()" in legacy  # still skipped when the crawl failed, as before


@pytest.mark.parametrize("secret", ["GMAIL_USER", "GMAIL_APP_PASSWORD", "DIGEST_TO"])
def test_the_legacy_digest_and_the_alert_keep_their_gmail_secrets(secret: str) -> None:
    assert f"{secret}: ${{{{ secrets.{secret} }}}}" in steps()["digest"]
    alert = next(step for name, step in steps().items() if name.startswith("Email an alert"))
    assert f"{secret}: ${{{{ secrets.{secret} }}}}" in alert
