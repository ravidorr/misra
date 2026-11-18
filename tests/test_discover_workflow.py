"""The discover-companies workflow (PLAN_2.md step 3.2, decision D2): started by hand only, discover.py runs in a step
without the service-role key, and only the first step (the pull) and the last (the push) hold it. The workflow is read
as text, as in test_daily_workflow."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL = (ROOT / ".github" / "workflows" / "discover-companies.yml").read_text(encoding="utf-8")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")
KEY = "SUPABASE_SERVICE_ROLE_KEY"
BODY = FULL.split("    steps:\n", 1)[1]
STEPS = list(re.split(r"(?m)^      - ", BODY)[1:])


def step(start: str) -> str:
    return next(chunk for chunk in STEPS if chunk.startswith(start))


def test_it_is_started_by_hand_only_and_never_by_a_push_or_a_schedule() -> None:
    head = FULL.split("jobs:\n", 1)[0]
    assert re.search(r"(?m)^on:\n  workflow_dispatch: \{\}\n", head)
    for trigger in ("push:", "schedule:", "pull_request", "workflow_run", "workflow_call", "inputs:"):
        assert trigger not in head, trigger


def test_only_the_pull_and_the_push_hold_the_key_and_the_push_is_the_last_step() -> None:
    holders = [chunk for chunk in STEPS if KEY in chunk]
    assert [chunk.split("\n", 1)[0] for chunk in holders] == [
        "name: Pull the company list from Supabase",
        "name: Send the discovered fields to Supabase",
    ]
    assert STEPS[-1] is holders[-1]
    for chunk in holders:
        assert set(SECRET_REF.findall(chunk)) == {"SUPABASE_URL", KEY}
    assert KEY not in FULL.split("steps:\n", 1)[0]  # nothing at the workflow or job level


def test_discover_runs_in_its_own_step_between_them_with_no_secret_and_no_other_command() -> None:
    discover = step("name: Discover")
    assert "secrets." not in discover and "env:" not in discover
    assert re.search(r"(?m)^        run: python discover\.py$", discover)
    assert [chunk.split("\n", 1)[0] for chunk in STEPS].index("name: Discover the career pages and feeds") == len(
        STEPS
    ) - 2


def test_the_steps_run_the_pull_of_the_list_only_and_the_push_of_the_discovered_fields() -> None:
    assert re.search(r"(?m)^        run: python sync_supabase\.py --pull-companies$", step("name: Pull"))
    assert re.search(r"(?m)^        run: python sync_supabase\.py --push-companies$", step("name: Send"))


def test_nothing_checks_out_a_key_or_pushes_and_it_refuses_to_run_from_another_branch() -> None:
    assert "ssh-key:" not in BODY and "DEPLOY_KEY" not in BODY
    assert "persist-credentials: false" in FULL
    assert "git push" not in BODY and "git commit" not in BODY
    assert "permissions:\n  contents: read\n" in FULL
    guard = step("name: Check the branch")
    assert '"$GITHUB_REF" != "refs/heads/main"' in guard and "exit 1" in guard
    assert STEPS.index(guard) < [chunk.split("\n", 1)[0] for chunk in STEPS].index(
        "name: Pull the company list from Supabase"
    )


def test_it_has_its_own_group_that_never_cancels_a_run_that_started() -> None:
    assert "concurrency:\n  group: discover-companies\n  cancel-in-progress: false\n" in FULL
