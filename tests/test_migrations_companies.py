"""The company list migration (PLAN_2.md step 3.1): the lint of tests/test_migrations.py applied to it, and the places
where the SQL keeps a copy of something the code owns (the gap reasons, the feeds, the limits, the columns, the records
of data/companies.json). A copy that drifts fails here, so the editor of step 3.3 can trust the database checks as the
one set of rules. The behaviour is tested against a database in supabase/tests/companies.sql."""

import json
import re
from pathlib import Path

import pytest

import adapters
import build_dashboard
import common
import export_accounts
from tests.test_migrations import (
    CREATE_TABLE,
    MIGRATION_DIR,
    REAL,
    clean,
    columns_of,
    problems,
    statements_of,
)

FILE = MIGRATION_DIR / "20261130100000_companies.sql"
SQL = FILE.read_text(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
COUNTS = (347, 267, 80)  # records, crawled, on the coverage card, at the commit of the migration
CHECKS = (
    "companies_name_unique",
    "companies_domain_unique",
    "companies_name_min_length",
    "companies_domain_required",
    "companies_exactly_one_status",
    "companies_gap_reason",
    "companies_careers_https",
    "companies_links_https",
    "companies_ats_known",
    "companies_excluded_keys",
    "companies_excluded_reason",
    "companies_excluded_date",
    "companies_excluded_shape",
    "companies_excluded_limit",
)


def create_table() -> str:
    return next(s for s in statements_of(SQL) if CREATE_TABLE.fullmatch(s))


def quoted_list_after(prefix: str) -> list[str]:
    """The quoted words of the `in (...)` list that follows `prefix` in the migration."""
    start = SQL.index(prefix) + len(prefix)
    return re.findall(r"'([a-z_]+)'", SQL[start : SQL.index(")", start)])


def test_the_lint_accepts_the_migration_as_it_is() -> None:
    assert problems(statements_of(REAL)) == []


def test_the_columns_are_the_record_keys_then_excluded_with_the_two_of_the_table() -> None:
    body = columns_of(CREATE_TABLE.fullmatch(create_table())["body"])  # type: ignore[index]
    assert list(body) == ["id", *common.RECORD_KEYS, "excluded", "updated_at"], (
        "a key of common.RECORD_KEYS is not a column (step 3.2 adds `excluded` to the keys)"
    )
    for key in ("links", "gap", "excluded"):
        assert body[key].startswith(f"{key} jsonb"), key
    for key in ("crawl", "israel_only", "verify"):
        assert body[key].startswith(f"{key} boolean"), key


def test_the_gap_reasons_are_the_keys_of_REASONS() -> None:
    assert quoted_list_after("gap ->> 'reason' in (") == list(build_dashboard.REASONS), (
        "the gap reasons of the migration differ from REASONS in build_dashboard.py: a new migration changes the check"
    )


def test_the_feeds_are_the_adapters_and_the_browser() -> None:
    assert quoted_list_after("ats = '' or ats in (") == [*adapters.ADAPTERS, "browser"], (
        "the ats list of the migration differs from adapters.ADAPTERS plus `browser` (run.py sends it to the browser)"
    )


def test_the_limits_are_the_ones_of_load_excluded() -> None:
    assert re.search(r"char_length\(name\) >= (\d+)\)", SQL)[1] == str(common.EXCLUDED_MIN_NAME)  # type: ignore[index]
    assert re.search(r"id is distinct from new\.id\) >= (\d+) then", SQL)[1] == str(common.EXCLUDED_MAX)  # type: ignore[index]
    assert f"at most {common.EXCLUDED_MAX} excluded companies" in SQL


def test_the_migration_loads_the_records_it_says() -> None:
    """The literal of the load is well formed: 347 records, 267 crawled and 80 on the card at the commit of the
    migration, keys of common.RECORD_KEYS only, no http address. It is NOT compared with data/companies.json: the file
    stays the live source until step 3.2, so an added company or a new row in data/excluded_companies.csv (a legal
    removal, which must never be blocked) must not fail a test. The table loads no exclusion; step 3.2 reloads it from
    the file of that day and the file then carries the exclusions."""
    raw = re.search(r"jsonb_to_recordset\(\$companies\$(.*?)\$companies\$::jsonb", SQL, flags=re.DOTALL)[1]  # type: ignore[index]
    loaded = json.loads(raw)
    assert (len(loaded), sum(r["crawl"] is True for r in loaded), sum("gap" in r for r in loaded)) == COUNTS
    assert all(set(r) <= set(common.RECORD_KEYS) for r in loaded)
    assert not any(r.get("careers_url", "").startswith("http://") for r in loaded)
    assert not any("excluded" in r for r in loaded)


def test_every_check_is_named_in_the_migration_and_the_readme() -> None:
    readme = (ROOT / "supabase" / "README.md").read_text(encoding="utf-8")
    code = clean(SQL)  # comments left out, so a name in the header does not count
    for name in CHECKS:
        if name.endswith("_unique"):
            assert f"create unique index {name} on" in code, name
        elif name.endswith("_limit"):
            assert f"constraint = '{name}'" in code, name
        else:
            assert f"constraint {name} check (" in code, name
        assert name in readme, f"{name} is not listed in supabase/README.md, 'The company list'"


def test_the_log_trigger_logs_the_name_and_never_a_reason() -> None:
    body = re.search(r"create function public\.companies_log_change\(\).*?\$\$(.*?)\$\$", SQL, flags=re.DOTALL)[1]  # type: ignore[index]
    assert "excluded" not in body
    assert "public.is_admin()" in body
    assert body.count("insert into") == 1


def test_the_company_list_is_not_in_the_backup_yet() -> None:
    """Deliberate: step 3.4 backs `companies` up with `Table(..., account=False)` in export_accounts.py (a version
    bump) and restores it. When that lands, this test is the one to change."""
    backed_up = {t.name for t in export_accounts.TABLES} | {t.name for t in export_accounts.JOB_TABLES}
    assert "companies" not in backed_up


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "grant select on public.companies to anon, authenticated;",
            "grant select, insert on public.companies to anon, authenticated;",
        ),
        (
            "grant insert, update, delete on public.companies to authenticated;",
            "grant insert, update, delete on public.companies to anon, authenticated;",
        ),
        (
            "grant insert, update, delete on public.companies to authenticated;",
            "grant insert, update, delete, truncate on public.companies to authenticated;",
        ),
        ("revoke all on public.companies from anon, authenticated, public;", ""),
        (
            "revoke all on public.companies from anon, authenticated, public;",
            "revoke all on public.companies from anon, public;",
        ),
        ("alter table public.companies enable row level security;", ""),
        (
            "for select to anon, authenticated using (excluded is null);",
            "for select to anon, authenticated using (true);",
        ),
        (
            "for select to anon, authenticated using (excluded is null);",
            "for select to anon, authenticated using (excluded is not null);",
        ),
        ("for select to authenticated using (public.is_admin());", "for select to authenticated using (true);"),
        (
            "for insert to authenticated with check (public.is_admin());",
            "for insert to authenticated with check (true);",
        ),
        (
            "for update to authenticated using (public.is_admin()) with check (public.is_admin());",
            "for update to authenticated using (public.is_admin());",
        ),
        (
            "for update to authenticated using (public.is_admin()) with check (public.is_admin());",
            "for update to authenticated using (true) with check (true);",
        ),
        (
            "for delete to authenticated using (public.is_admin());",
            "for delete to anon, authenticated using (public.is_admin());",
        ),
        ("for delete to authenticated using (public.is_admin());", "for delete to authenticated using (true);"),
        ("revoke all on function public.companies_log_change() from public, anon, authenticated;", ""),
        ("revoke all on function public.companies_before_write() from public, anon, authenticated;", ""),
        (
            "security definer\nset search_path = ''\nas $$\nbegin\n  if public.is_admin()",
            "set search_path = ''\nas $$\nbegin\n  if public.is_admin()",
        ),
        (
            "returns trigger\nlanguage plpgsql\nset search_path = ''\nas $$\nbegin\n  new.updated_at",
            "returns trigger\nlanguage plpgsql\nsecurity definer\nset search_path = ''\nas $$\nbegin\n  new.updated_at",
        ),
        ("  if public.is_admin() then\n", "  if true then\n"),
        (
            "case tg_op when 'DELETE' then old.name else new.name end",
            "case tg_op when 'DELETE' then old.name else new.excluded ->> 'reason' end",
        ),
        ("    );\n  end if;\n  return null;", "    );\n    delete from public.admin_log;\n  end if;\n  return null;"),
        ("  new.updated_at := now();\n", ""),
        (">= 200 then", ">= 2000 then"),
        (
            "create unique index companies_name_unique on public.companies (lower(name));",
            "create unique index companies_name_unique on public.companies (name);",
        ),
        ("from jsonb_to_recordset($companies$", "from public.jobs, jsonb_to_recordset($companies$"),
        (
            "$companies$::jsonb) as r (",
            "$companies$::jsonb) as r (name text) union all select email from auth.users, "
            "jsonb_to_recordset($companies$[]$companies$::jsonb) as r (",
        ),
        (
            "select name, domain, segment, crawl, careers_url, ats, slug, source, status, israel_only, verify, links, "
            "gap\nfrom",
            "select domain, name, segment, crawl, careers_url, ats, slug, source, status, israel_only, verify, links, "
            "gap\nfrom",
        ),
        (
            "create trigger companies_log_change after insert or update or delete on public.companies",
            "create trigger companies_log_change after insert or update or delete on public.admins",
        ),
    ],
)
def test_the_lint_rejects_a_weakened_companies_migration(old: str, new: str) -> None:
    assert old in SQL, old
    weakened = SQL.replace(old, new, 1)
    assert problems(statements_of(f"{REAL.replace(SQL, '')}\n{weakened}")), old
