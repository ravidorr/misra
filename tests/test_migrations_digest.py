"""The lint of tests/test_migrations.py applied to the digest signup migration (step 6.2; PRD FR-8 AC 1 to 3): the
counter tables and the four functions belong to the service role alone, the functions run as the caller, and turning
off reuses the unsubscribe clean-up instead of copying it. The behaviour is tested against a database in
supabase/tests/digest_signup.sql."""

import re

import pytest

from tests.test_migrations import (
    CONFIRM_LIMIT_COLUMNS,
    CONFIRM_LIMIT_TABLES,
    CREATE_TABLE,
    ENABLE_RLS,
    GRANT_SUBSCRIBE_FUNCTION,
    MIGRATION_DIR,
    REAL,
    REVOKE,
    SUBSCRIBE_FUNCTION,
    SUBSCRIBE_FUNCTIONS,
    columns_of,
    problems,
    statements,
    statements_of,
)

DIGEST_FILE = "20261118100000_digest_signup_and_limits.sql"
START_SIGNATURE = "uuid, text, text, jsonb, text, timestamptz, text, timestamptz"
RESERVE_SIGNATURE = "date, text, text, integer, integer, integer"
CLOSED = "from public, anon, authenticated;"
RESERVE = f"revoke all on function public.confirm_email_reserve({RESERVE_SIGNATURE}) {CLOSED}"
START = f"revoke all on function public.subscribe_start({START_SIGNATURE}) {CLOSED}"
START_GRANT = f"grant execute on function public.subscribe_start({START_SIGNATURE}) to service_role;"
OFF_GRANT = "grant execute on function public.unsubscribe_user(uuid, timestamptz) to service_role;"
OFF_HEAD = "create function public.unsubscribe_user(p_user_id uuid, p_now timestamptz)"
OFF_ATTRS = "returns text\nlanguage plpgsql\nset search_path = ''"
RELEASE_HEAD = "create function public.confirm_email_release(p_day date, p_user_hash text, p_address_hash text)"
CALL = "  return public.unsubscribe_subscription(token_hash, p_now);"
COUNTER_TABLES = 2  # confirm_limits and confirm_daily_cap
GIVEN_BACK = 3  # the address refusal gives back one place and the cap refusal two


def functions() -> dict[str, re.Match[str]]:
    return {m["name"]: m for s in statements() if (m := SUBSCRIBE_FUNCTION.fullmatch(s))}


def test_the_counters_hold_only_keys_and_numbers_and_belong_to_the_service_role() -> None:
    stmts = statements()
    tables = {m["table"]: columns_of(m["body"]) for s in stmts if (m := CREATE_TABLE.fullmatch(s))}
    for name, columns in CONFIRM_LIMIT_COLUMNS.items():
        assert set(tables[name]) == columns
    secured = {m["table"] for s in stmts if (m := ENABLE_RLS.fullmatch(s))}
    assert secured >= CONFIRM_LIMIT_TABLES
    revoked = {(m["table"], role) for s in stmts if (m := REVOKE.fullmatch(s)) for role in m["roles"].split(", ")}
    assert revoked >= {(t, role) for t in CONFIRM_LIMIT_TABLES for role in ("anon", "authenticated")}
    mentions = [s for s in stmts if any(t in s for t in CONFIRM_LIMIT_TABLES)]
    assert not [s for s in mentions if s.startswith(("create policy", "grant"))]


def test_the_four_functions_are_invoker_functions_executable_by_the_service_role_alone() -> None:
    found = functions()
    assert set(found) == set(SUBSCRIBE_FUNCTIONS)
    for name, signature in SUBSCRIBE_FUNCTIONS.items():
        assert found[name]["args"].strip() == signature
    grants = [m for s in statements() if (m := GRANT_SUBSCRIBE_FUNCTION.fullmatch(s))]
    assert {g["name"] for g in grants} == set(SUBSCRIBE_FUNCTIONS)
    assert all("security definer" not in s for s in statements() if "subscribe_start" in s or "unsubscribe_user" in s)


def test_turning_off_calls_the_unsubscribe_function_and_copies_none_of_it() -> None:
    body = functions()["unsubscribe_user"]["body"]
    assert "return public.unsubscribe_subscription(token_hash, p_now)" in body
    assert "update public.subscriptions" not in body  # the clean-up lives in one place
    assert "delete from" not in body


def test_turning_on_never_confirms_and_never_touches_the_sent_list() -> None:
    body = functions()["subscribe_start"]["body"]
    assert "consent_confirmed_at = null" in body
    assert "unsubscribed_at = null" in body
    assert "p_now" in body
    assert "now()" not in body  # the clock is passed in
    assert "emailed" not in body
    assert "for update" in body
    insert = body[body.index("insert into") : body.index("on conflict")]
    assert "p_now, null, null" in insert  # confirmed and unsubscribed start empty


def test_the_guard_takes_the_user_then_the_address_then_the_day_and_a_refusal_uses_nothing() -> None:
    body = functions()["confirm_email_reserve"]["body"]
    assert body.index("p_user_hash") < body.index("p_address_hash") < body.index("confirm_daily_cap as c")
    assert body.count("greatest(sent - 1, 0)") == GIVEN_BACK
    release = functions()["confirm_email_release"]["body"]
    assert release.index("p_user_hash") < release.index("p_address_hash") < release.index("confirm_daily_cap")
    assert "where day < p_day - 1" in body  # old rows go on every call


def test_the_daily_cleanup_of_the_confirmation_counters_deletes_only_earlier_days_and_refuses_a_wrong_day() -> None:
    body = functions()["confirm_email_cleanup"]["body"]
    assert "delete from public.confirm_limits where day < p_day;" in body
    assert "delete from public.confirm_daily_cap where day < p_day;" in body
    assert "p_day is null or p_day > current_date" in body  # a wrong date can never wipe today's counters
    assert body.count("delete from") == COUNTER_TABLES  # one delete per counter table, nothing else


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (RESERVE, RESERVE.replace(CLOSED, "from public, anon;")),
        (START, START.replace(CLOSED, "from public;")),
        (START, ""),
        (START_GRANT, START_GRANT.replace("service_role", "authenticated")),
        (OFF_GRANT, OFF_GRANT.replace("service_role", "service_role, anon")),
        ("alter table public.confirm_limits enable row level security;", ""),
        ("alter table public.confirm_daily_cap enable row level security;", ""),
        (
            "revoke all on public.confirm_limits from anon, authenticated;",
            "revoke all on public.confirm_limits from anon;",
        ),
        ("revoke all on public.confirm_daily_cap from anon, authenticated;", ""),
        ("  key_hash text    not null,", "  key_hash text    not null,\n  address  text,"),
        ("  key_hash text    not null,", "  key_hash text    not null,\n  user_name text,"),
        (
            f"{RELEASE_HEAD}\nreturns text\nlanguage plpgsql\n",
            f"{RELEASE_HEAD}\nreturns text\nlanguage plpgsql\nsecurity definer\n",
        ),
        (f"{OFF_HEAD}\n{OFF_ATTRS}", f"{OFF_HEAD}\nreturns text\nlanguage plpgsql"),
        (CALL, f"  delete from auth.users where id = p_user_id;\n{CALL}"),
        (CALL, f"  delete from public.user_jobs where user_id = p_user_id;\n{CALL}"),
        (OFF_HEAD, OFF_HEAD.replace("p_now timestamptz", "p_now timestamptz, p_extra text")),
    ],
)
def test_the_lint_rejects_a_weakened_digest_migration(old: str, new: str) -> None:
    migration = (MIGRATION_DIR / DIGEST_FILE).read_text(encoding="utf-8")
    assert old in migration, old
    weakened = migration.replace(old, new, 1)
    assert problems(statements_of(f"{REAL.replace(migration, '')}\n{weakened}")), old


def test_the_lint_accepts_the_migration_as_it_is() -> None:
    assert problems(statements_of(REAL)) == []
