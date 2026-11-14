"""Sanity checks on the SQL of supabase/migrations/, without a database. This is an allow-list lint, not a proof: every
statement must be one of a few known kinds (so a view, a function, `alter default privileges` or `disable row level
security` fails until this file is taught about it), every table has row-level security and a full revoke from the
public roles, policies only read open jobs, and the public roles get SELECT on `jobs` only. File names follow the
Supabase CLI format."""

import re
from pathlib import Path

import pytest

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
PUBLIC_ROLES = {"anon", "authenticated", "public"}
ALL_WRITES = {"insert", "update", "delete", "truncate"}

CREATE_TABLE = re.compile(r"create table public\.(?P<table>\w+) \(")
CREATE_INDEX = re.compile(r"create index \w+ on public\.\w+ (?:using gin )?\(\w+\)")
ENABLE_RLS = re.compile(r"alter table public\.(?P<table>\w+) enable row level security")
GRANT = re.compile(r"grant (?P<privs>[a-z, ]+) on public\.(?P<table>\w+) to (?P<roles>[a-z, ]+)")
REVOKE = re.compile(r"revoke (?P<privs>[a-z, ]+) on public\.(?P<table>\w+) from (?P<roles>[a-z, ]+)")
POLICY = re.compile(
    r'create policy "[^"]*" on public\.(?P<table>\w+) for (?P<command>\w+) to (?P<roles>[a-z, ]+) '
    r"using \(status = 'open'\)"
)


def clean(text: str) -> str:
    """Comments removed, lower-cased, whitespace collapsed."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return re.sub(r"\s+", " ", text).lower()


def statements_of(text: str) -> list[str]:
    return [s.strip() for s in clean(text).split(";") if s.strip()]


def statements() -> list[str]:
    return statements_of("\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.sql"))))


def words(csv: str) -> set[str]:
    return {w.strip() for w in csv.split(",")}


def access_problem(s: str) -> str | None:
    """The problem with a grant or policy statement, or None when it is fine or not a grant or policy."""
    grant, policy = GRANT.fullmatch(s), POLICY.fullmatch(s)
    if (
        grant
        and words(grant["roles"]) & PUBLIC_ROLES
        and (words(grant["privs"]) != {"select"} or grant["table"] != "jobs")
    ):
        return f"public roles may only get select on jobs: {s}"
    if policy and (
        policy["command"] != "select" or policy["table"] != "jobs" or not words(policy["roles"]) <= PUBLIC_ROLES
    ):
        return f"policy must be select on jobs for public roles: {s}"
    return None


def problems(stmts: list[str]) -> list[str]:
    """Everything wrong with a migration, as messages; empty when it is safe by this lint's rules."""
    found: list[str] = []
    tables: set[str] = set()
    secured: set[str] = set()
    revoked: set[tuple[str, str]] = set()
    for s in stmts:
        if m := CREATE_TABLE.match(s):
            tables.add(m["table"])
        elif m := ENABLE_RLS.fullmatch(s):
            secured.add(m["table"])
        elif m := REVOKE.fullmatch(s):
            privs = words(m["privs"])
            if privs & {"all", "all privileges"} or privs >= ALL_WRITES:
                revoked |= {(m["table"], role) for role in words(m["roles"])}
        elif not (CREATE_INDEX.fullmatch(s) or GRANT.fullmatch(s) or POLICY.fullmatch(s)):
            found.append(f"statement kind not allowed by the lint: {s}")
        if problem := access_problem(s):
            found.append(problem)
    found.extend(f"row-level security not enabled on {t}" for t in sorted(tables - secured))
    found.extend(
        f"all privileges on {t} not revoked from {role}"
        for t in sorted(tables)
        for role in ("anon", "authenticated")
        if (t, role) not in revoked
    )
    return found


def test_there_are_migrations_with_cli_names() -> None:
    files = sorted(MIGRATION_DIR.glob("*.sql"))
    assert files
    assert all(re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", p.name) for p in files)


def test_migrations_are_safe() -> None:
    stmts = statements()
    assert {m["table"] for s in stmts if (m := CREATE_TABLE.match(s))} == {"jobs", "crawl_log"}
    assert any(POLICY.fullmatch(s) for s in stmts)
    assert problems(stmts) == []


def test_crawl_log_has_no_policy_and_no_grant() -> None:
    assert not [s for s in statements() if s.startswith(("create policy", "grant")) and "crawl_log" in s]


BASE = "create table public.jobs (a text); alter table public.jobs enable row level security;"
REVOKES = "revoke all on public.jobs from anon, authenticated;"


@pytest.mark.parametrize(
    "extra",
    [
        "alter default privileges in schema public grant all on tables to anon",
        "grant all on all tables in schema public to anon",
        "grant all on public.jobs to anon",
        'grant select on public.jobs to "anon"',
        "grant select on public.crawl_log to anon",
        "alter table public.jobs disable row level security",
        "create view public.v as select * from public.jobs",
        "create function public.f() returns int security definer as $$ select 1 $$",
        "create unlogged table public.x (a text)",
        "create policy \"p\" on public.jobs for all to anon using (status = 'open')",
        "create policy \"read for select\" on public.jobs to anon using (status = 'open')",
        'create policy "p" on public.jobs for select to anon using (true)',
        "drop policy p on public.jobs",
    ],
)
def test_the_lint_rejects_unsafe_statements(extra: str) -> None:
    assert problems(statements_of(f"{BASE} {REVOKES} {extra};"))


@pytest.mark.parametrize(
    "migration",
    [
        BASE,  # no revoke
        f"{BASE} revoke insert on public.jobs from anon, authenticated;",  # partial revoke
        f"{BASE} revoke all on public.jobs from anon;",  # authenticated missing
        f"create table public.jobs (a text); {REVOKES}",  # no row-level security
        f"/* {BASE} */ create table public.jobs (a text); {REVOKES}",  # RLS only inside a block comment
    ],
)
def test_the_lint_rejects_unsafe_migrations(migration: str) -> None:
    assert problems(statements_of(migration))


def test_the_lint_accepts_a_safe_migration() -> None:
    safe = f"{BASE} {REVOKES} grant select on public.jobs to anon, authenticated;"
    assert problems(statements_of(safe)) == []
