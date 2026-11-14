"""Sanity checks on the SQL of supabase/migrations/, without a database: every table has row-level security, no policy
or grant lets the public write, and file names follow the Supabase CLI format."""

import re
from pathlib import Path

MIGRATIONS = sorted(Path(__file__).resolve().parent.parent.joinpath("supabase", "migrations").glob("*.sql"))
PUBLIC_ROLES = {"anon", "authenticated", "public"}
WRITE_PRIVILEGES = {"insert", "update", "delete", "truncate", "all"}


def sql() -> str:
    """All migrations in order, comments removed, lower-cased, whitespace collapsed."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)
    text = re.sub(r"--[^\n]*", "", text)
    return re.sub(r"\s+", " ", text).lower()


def statements() -> list[str]:
    return [s.strip() for s in sql().split(";") if s.strip()]


def test_there_are_migrations_with_cli_names() -> None:
    assert MIGRATIONS
    assert all(re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", path.name) for path in MIGRATIONS)


def test_every_table_has_row_level_security() -> None:
    tables = set(re.findall(r"create table (?:if not exists )?(?:public\.)?(\w+)", sql()))
    assert {"jobs", "crawl_log"} <= tables
    secured = set(re.findall(r"alter table (?:public\.)?(\w+) enable row level security", sql()))
    assert tables == secured


def test_no_policy_allows_public_writes() -> None:
    policies = [s for s in statements() if s.startswith("create policy")]
    assert policies, "jobs needs a select policy"
    for policy in policies:
        assert " for select " in policy, policy
        assert not re.search(r" for (insert|update|delete|all)\b", policy), policy


def test_policies_only_read_open_jobs() -> None:
    policies = [s for s in statements() if s.startswith("create policy")]
    assert all(" on public.jobs " in p and "using (status = 'open')" in p for p in policies)


def test_public_roles_get_select_on_jobs_only() -> None:
    granted = 0
    for statement in statements():
        match = re.match(r"grant (.+?) on (?:table )?(?:public\.)?(\w+) to (.+)", statement)
        if not match:
            continue
        privileges = {p.strip() for p in match.group(1).split(",")}
        roles = {r.strip() for r in re.sub(r" with grant option$", "", match.group(3)).split(",")}
        if roles & PUBLIC_ROLES:
            granted += 1
            assert privileges == {"select"}, statement
            assert match.group(2) == "jobs", statement
            assert not statement.endswith("with grant option"), statement
    assert granted


def test_write_privileges_are_revoked_from_public_roles() -> None:
    revoked: set[tuple[str, str]] = set()
    for statement in statements():
        match = re.match(r"revoke (.+?) on (?:table )?(?:public\.)?(\w+) from (.+)", statement)
        if match and {p.strip() for p in match.group(1).split(",")} & WRITE_PRIVILEGES:
            revoked |= {(match.group(2), role.strip()) for role in match.group(3).split(",")}
    for table in ("jobs", "crawl_log"):
        assert {(table, "anon"), (table, "authenticated")} <= revoked
