"""The lint of tests/test_migrations.py applied to the subscribers migration (step 6.1; PRD FR-8 AC 7): the signed-in
role may change two preference columns and nothing that proves consent, the token hashes are unreadable to it,
and `emailed`
and the two functions belong to the service role alone. The behaviour is tested against a database in
supabase/tests/subscriptions.sql."""

import pytest

from tests.test_migrations import (
    CASCADE,
    COLUMN_GRANT,
    COLUMN_GRANTS,
    CREATE_TABLE,
    EMAILED_COLUMNS,
    GRANT,
    GRANT_SUBSCRIPTION_FUNCTION,
    MIGRATION_DIR,
    REAL,
    SUBSCRIPTION_COLUMNS,
    SUBSCRIPTION_FUNCTION,
    SUBSCRIPTION_FUNCTIONS,
    columns_of,
    function_named,
    problems,
    statements,
    statements_of,
    trigger_on,
    words,
)

SUBSCRIPTIONS_FILE = "20261117100000_subscriptions_and_emailed.sql"
SELECT_COLUMNS = (
    "grant select (user_id, address, frequency, filters, consent_given_at, consent_confirmed_at, unsubscribed_at, "
    "created_at, updated_at)\n  on public.subscriptions to authenticated;"
)
UPDATE_COLUMNS = "grant update (frequency, filters) on public.subscriptions to authenticated;"
OWN = "(select auth.uid()) = user_id"
CONFIRM_HEAD = (
    "returns text\nlanguage plpgsql\nset search_path = ''\nas $$\ndeclare\n  sub public.subscriptions%rowtype;\n"
    "begin\n  select * into sub from public.subscriptions where confirm_token_hash"
)
UNSUBSCRIBE_HEAD = CONFIRM_HEAD.replace("confirm_token_hash", "unsubscribe_token_hash")


def test_the_signed_in_role_gets_subscriptions_by_column_and_never_a_token_hash() -> None:
    stmts = statements()
    grants = [m for s in stmts if (m := COLUMN_GRANT.fullmatch(s))]
    assert {(g["priv"], g["table"], g["roles"]) for g in grants} == {
        ("select", "subscriptions", "authenticated"),
        ("update", "subscriptions", "authenticated"),
    }
    for grant in grants:
        assert words(grant["columns"]) == COLUMN_GRANTS[grant["priv"]]
        assert not any("token" in column for column in words(grant["columns"]))  # no hash, no expiry
    # the user can neither confirm themselves nor change the address consent was given for nor undo an unsubscribe
    assert COLUMN_GRANTS["update"] == {"frequency", "filters"}
    # nothing at table level, for anyone, on either table, and no insert or delete in any form
    table_grants = [GRANT.fullmatch(s) for s in stmts]
    assert not [m for m in table_grants if m and m["table"] in {"subscriptions", "emailed"}]
    writes = [s for s in stmts if s.startswith("grant") and ("insert" in s or "delete" in s)]
    assert not [s for s in writes if "subscriptions" in s or "emailed" in s]


def test_every_subscription_row_is_tied_to_its_user_and_the_tokens_are_stored_hashed() -> None:
    tables = {m["table"]: columns_of(m["body"]) for s in statements() if (m := CREATE_TABLE.fullmatch(s))}
    assert set(tables["subscriptions"]) == SUBSCRIPTION_COLUMNS
    assert set(tables["emailed"]) == EMAILED_COLUMNS
    assert CASCADE in tables["subscriptions"]["user_id"]  # account deletion removes the consent log too
    assert CASCADE in tables["emailed"]["user_id"]
    for name in ("confirm_token_hash", "unsubscribe_token_hash"):
        assert "unique" in tables["subscriptions"][name]
        assert "~ '^[0-9a-f]{64}$'" in tables["subscriptions"][name]  # a SHA-256, so a raw token cannot be stored


def test_the_subscription_functions_are_invoker_functions_closed_to_everyone_but_the_service_role() -> None:
    stmts = statements()
    functions = {m["name"]: m for s in stmts if (m := SUBSCRIPTION_FUNCTION.fullmatch(s))}
    assert set(functions) == set(SUBSCRIPTION_FUNCTIONS)
    for name, signature in SUBSCRIPTION_FUNCTIONS.items():
        assert functions[name]["args"] == signature
        assert "security definer" not in functions[name][0]
        assert "now()" not in functions[name]["body"]  # the clock is passed in
    executes = [GRANT_SUBSCRIPTION_FUNCTION.fullmatch(s) for s in stmts]
    assert {m["name"] for m in executes if m} == set(SUBSCRIPTION_FUNCTIONS)
    unsubscribe = functions["unsubscribe_subscription"]["body"]
    assert "delete from public.emailed where user_id = sub.user_id" in unsubscribe
    assert "confirm_token_hash = null" in unsubscribe  # an old confirmation link cannot turn sending back on
    confirm = functions["confirm_subscription"]["body"]
    assert confirm.index("unsubscribed_at is not null") < confirm.index("consent_confirmed_at = p_now")


def test_a_change_of_address_drops_the_confirmation() -> None:
    drop = function_named("clear_confirmation_on_address_change")
    assert not drop["definer"]
    assert "new.consent_confirmed_at := null" in drop["body"]
    assert "new.confirm_token_hash is not distinct from old.confirm_token_hash" in drop["body"]
    assert "before update of address on public.subscriptions" in trigger_on(
        "public.subscriptions", "clear_confirmation_on_address_change"
    )


def widened(columns: str) -> str:
    return f"grant update ({columns}) on public.subscriptions to authenticated;"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # what the signed-in role may select or update
        (UPDATE_COLUMNS, widened("frequency, filters, address")),
        (UPDATE_COLUMNS, widened("frequency, filters, consent_confirmed_at")),
        (UPDATE_COLUMNS, widened("frequency, filters, unsubscribed_at")),
        (UPDATE_COLUMNS, widened("frequency, filters, confirm_token_hash")),
        (UPDATE_COLUMNS, UPDATE_COLUMNS.replace("authenticated;", "authenticated, anon;")),
        (UPDATE_COLUMNS, "grant update on public.subscriptions to authenticated;"),
        (UPDATE_COLUMNS, f"{UPDATE_COLUMNS}\ngrant insert on public.subscriptions to authenticated;"),
        (UPDATE_COLUMNS, f"{UPDATE_COLUMNS}\ngrant delete on public.subscriptions to authenticated;"),
        (UPDATE_COLUMNS, f"{UPDATE_COLUMNS}\ngrant insert (address) on public.subscriptions to authenticated;"),
        (UPDATE_COLUMNS, f"{UPDATE_COLUMNS}\ngrant select (user_id) on public.emailed to authenticated;"),
        (UPDATE_COLUMNS, f"{UPDATE_COLUMNS}\ngrant select on public.emailed to authenticated;"),
        (SELECT_COLUMNS, SELECT_COLUMNS.replace("updated_at)", "updated_at, unsubscribe_token_hash)")),
        (SELECT_COLUMNS, SELECT_COLUMNS.replace("updated_at)", "updated_at, confirm_token_expires_at)")),
        (SELECT_COLUMNS, SELECT_COLUMNS.replace("authenticated;", "authenticated, anon;")),
        (SELECT_COLUMNS, "grant select on public.subscriptions to authenticated;"),
        # row-level security, revokes and policies
        ("alter table public.subscriptions enable row level security;", ""),
        ("alter table public.emailed enable row level security;", ""),
        (
            "revoke all on public.subscriptions from anon, authenticated;",
            "revoke all on public.subscriptions from anon;",
        ),
        ("revoke all on public.emailed from anon, authenticated;", "revoke all on public.emailed from anon;"),
        (
            f'create policy "own subscription read" on public.subscriptions\n  for select to authenticated\n'
            f"  using ({OWN});",
            'create policy "own subscription read" on public.subscriptions\n  for select to authenticated\n'
            "  using (true);",
        ),
        (
            f"  using ({OWN})\n  with check ({OWN});\n\ncreate trigger touch_subscriptions",
            f"  using ({OWN});\n\ncreate trigger touch_subscriptions",
        ),
        (
            UPDATE_COLUMNS,
            f'{UPDATE_COLUMNS}\ncreate policy "own emailed" on public.emailed for select to authenticated '
            f"using ({OWN});",
        ),
        (
            UPDATE_COLUMNS,
            f'{UPDATE_COLUMNS}\ncreate policy "gone" on public.subscriptions for delete to authenticated '
            f"using ({OWN});",
        ),
        # tables
        ("  frequency                text not null", "  full_name text,\n  frequency                text not null"),
        ("  address                  text not null", "  phone text,\n  address                  text not null"),
        (
            "  emailed_at timestamptz not null default now(),",
            "  emailed_at timestamptz not null default now(),\n  note text,",
        ),
        (
            "user_id    uuid not null references auth.users (id) on delete cascade,\n  company",
            "user_id    uuid not null,\n  company",
        ),
        (
            "user_id                  uuid primary key references auth.users (id) on delete cascade,",
            "user_id                  uuid primary key,",
        ),
        # functions, their grants and their reach
        (
            "revoke all on function public.confirm_subscription(text, timestamptz) from public, anon, authenticated;",
            "revoke all on function public.confirm_subscription(text, timestamptz) from public, anon;",
        ),
        (
            "revoke all on function public.unsubscribe_subscription(text, timestamptz) "
            "from public, anon, authenticated;",
            "",
        ),
        (
            "grant execute on function public.unsubscribe_subscription(text, timestamptz) to service_role;",
            "grant execute on function public.unsubscribe_subscription(text, timestamptz) to service_role, anon;",
        ),
        (
            "grant execute on function public.confirm_subscription(text, timestamptz) to service_role;",
            "grant execute on function public.confirm_subscription(text, timestamptz) to authenticated;",
        ),
        (CONFIRM_HEAD, CONFIRM_HEAD.replace("language plpgsql\n", "language plpgsql\nsecurity definer\n")),
        (UNSUBSCRIBE_HEAD, UNSUBSCRIBE_HEAD.replace("set search_path = ''\n", "")),
        (
            "delete from public.emailed where user_id = sub.user_id;\n  return 'unsubscribed';",
            "delete from public.user_jobs where user_id = sub.user_id;\n  return 'unsubscribed';",
        ),
        (
            "delete from public.emailed where user_id = sub.user_id;\n  return 'unsubscribed';",
            "delete from auth.users where id = sub.user_id;\n  return 'unsubscribed';",
        ),
        (
            "create function public.confirm_subscription(p_hash text, p_now timestamptz)",
            "create function public.confirm_subscription(p_hash text, p_now timestamptz, p_extra text)",
        ),
        (
            "revoke all on function public.clear_confirmation_on_address_change() from public, anon, authenticated;",
            "revoke all on function public.clear_confirmation_on_address_change() from public, anon;",
        ),
        ("execute function public.clear_confirmation_on_address_change();", "execute function public.nothing();"),
    ],
)
def test_the_lint_rejects_a_weakened_subscriptions_migration(old: str, new: str) -> None:
    migration = (MIGRATION_DIR / SUBSCRIPTIONS_FILE).read_text(encoding="utf-8")
    assert old in migration, old
    weakened = migration.replace(old, new)
    assert problems(statements_of(f"{REAL.replace(migration, '')}\n{weakened}")), old
