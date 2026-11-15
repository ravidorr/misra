"""The CI job that runs the SQL policy tests of supabase/tests against a Postgres service container: its place in
ci.yml, the stub of Supabase's roles and auth schema, and the runner script. The job runs the SQL, not this file."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
STUB = (REPO / "supabase" / "ci" / "supabase_stub.sql").read_text(encoding="utf-8")
RUNNER = (REPO / "scripts" / "run-sql-tests.sh").read_text(encoding="utf-8")
SQL_FILES = [*(REPO / "supabase" / "migrations").glob("*.sql"), *(REPO / "supabase" / "tests").glob("*.sql")]


def job(name: str) -> str:
    """The text of one job of ci.yml (from its two-space heading to the next heading)."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", CI, flags=re.MULTILINE | re.DOTALL)
    assert match, f"ci.yml has no job {name}"
    return match[1]


def test_the_sql_tests_are_a_separate_job_and_the_quality_job_is_untouched() -> None:
    jobs = re.findall(r"^  ([\w-]+):\n", CI.split("\njobs:\n", 1)[1], flags=re.MULTILINE)
    assert jobs == ["sql-tests", "quality"]  # the required check keeps its name
    assert "scripts/run-sql-tests.sh" not in job("quality")
    assert "run-sql-tests.sh postgresql://postgres:postgres@localhost:5432/postgres" in job("sql-tests")


def test_the_job_uses_a_postgres_17_service_container_and_waits_for_it() -> None:
    text = job("sql-tests")
    assert "image: postgres:17-alpine" in text
    assert "--health-cmd" in text and "pg_isready" in text
    assert "5432:5432" in text


def test_the_workflow_runs_on_every_pull_request_and_on_main() -> None:
    header = CI.split("\njobs:\n", 1)[0]
    assert "pull_request:" in header
    # no path filter, so every pull request that touches supabase/ runs the job
    assert not re.search(r"pull_request:\s*\n\s+paths", header)
    assert "branches: [main]" in header


def test_the_stub_creates_what_the_migrations_and_the_tests_name() -> None:
    for role in ("anon", "authenticated", "service_role", "supabase_auth_admin"):
        assert f"create role {role} " in STUB
    assert "create table auth.users" in STUB
    assert "create function auth.uid()" in STUB
    assert "alter default privileges in schema public grant all on tables" in STUB
    used = {r for sql in SQL_FILES for r in re.findall(r"\b(?:to|from|role)\s+(\w+)", sql.read_text(encoding="utf-8"))}
    assert {"anon", "authenticated", "service_role", "supabase_auth_admin"} <= used
    for column in ("id", "email", "raw_user_meta_data"):  # what the migrations and the tests write to or read from it
        assert f"  {column} " in STUB


def test_the_runner_stops_at_the_first_error_and_runs_the_stub_then_all_migrations_then_all_tests() -> None:
    assert "set -euo pipefail" in RUNNER
    assert "ON_ERROR_STOP=1" in RUNNER
    assert RUNNER.index("supabase/ci/supabase_stub.sql") < RUNNER.index("supabase/migrations/*.sql")
    assert RUNNER.index("supabase/migrations/*.sql") < RUNNER.index("supabase/tests/*.sql")
    assert "no migrations or no tests found" in RUNNER  # an empty glob must fail, not pass
    assert (REPO / "scripts" / "run-sql-tests.sh").stat().st_mode & 0o111  # executable, the job runs it directly
