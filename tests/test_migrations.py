"""Sanity checks on the SQL of supabase/migrations/, without a database. This is an allow-list lint, not a proof: every
statement must be one of a few known kinds (so a view, `alter default privileges` or `disable row level security` fails
until this file is taught about it), every table has row-level security and a full revoke from the public roles, the
anonymous role can only read open jobs, the signed-in role gets only the privileges listed below, every policy lets a
user touch their own rows only, every function pins its search path and is closed to the public roles, and a function
that copies from `auth.users` never reads the provider's metadata (FR-1: the email is the only personal detail kept).
File names follow the Supabase CLI format. The policies themselves are tested against a real database with
supabase/tests/accounts_rls.sql (see supabase/README.md)."""

import re
from pathlib import Path

import pytest

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
PUBLIC_ROLES = {"anon", "authenticated", "public"}
ALL_WRITES = {"insert", "update", "delete", "truncate"}
CRUD = {"select", "insert", "update", "delete"}
# What the signed-in role may do on each table; the anonymous role may only read `jobs`. Each granted command needs a
# policy, and a policy for any other command is refused.
AUTHENTICATED_MAY = {"jobs": {"select"}, "profiles": {"select"}, "user_prefs": CRUD, "user_jobs": CRUD}
USER_TABLES = {"profiles", "user_prefs", "user_jobs"}
# The one test that makes a row "mine". `(select auth.uid())` is the same test evaluated once per statement.
OWN_ROW = {"(select auth.uid()) = user_id", "auth.uid() = user_id"}
# The only personal detail kept is the email: no column anywhere is for what the provider sends or for contact details.
PROFILE_COLUMNS = {"user_id", "email"}
PERSONAL_COLUMN = re.compile(
    r"name|avatar|photo|picture|phone|resume|cv|birth|address|identity|metadata", re.IGNORECASE
)
PROVIDER_DATA = re.compile(
    r"raw_user_meta_data|raw_app_meta_data|user_metadata|identity_data|identities|avatar|full_name|picture|\bname\b"
)
AUTH_TRIGGER_FUNCTIONS = {"sync_profile"}  # the only functions allowed to run on auth.users
BACKFILL = (
    "insert into public.profiles (user_id, email) select id, email from auth.users where email is not null "
    "on conflict do nothing"
)

CREATE_TABLE = re.compile(r"create table public\.(?P<table>\w+) \((?P<body>.*)\)")
CREATE_INDEX = re.compile(
    r"create index \w+ on public\.\w+ (?:using gin )?\(\w+(?:, \w+)*\)(?: where state = 'discard')?"
)
ENABLE_RLS = re.compile(r"alter table public\.(?P<table>\w+) enable row level security")
GRANT = re.compile(r"grant (?P<privs>[a-z, ]+) on public\.(?P<table>\w+) to (?P<roles>[a-z, ]+)")
REVOKE = re.compile(r"revoke (?P<privs>[a-z, ]+) on public\.(?P<table>\w+) from (?P<roles>[a-z, ]+)")
POLICY = re.compile(
    r'create policy "[^"]*" on public\.(?P<table>\w+) for (?P<command>\w+) to (?P<roles>[a-z, ]+) '
    r"(?:using \((?P<using>.+?)\))?(?: ?with check \((?P<check>.+?)\))?"
)
FUNCTION = re.compile(
    r"create function public\.(?P<name>\w+)\(\) returns trigger language plpgsql "
    r"(?P<definer>security definer )?set search_path = '' as \$\$(?P<body>.*)\$\$"
)
HOOK = re.compile(
    r"create function public\.custom_access_token_hook\(event jsonb\) returns jsonb language plpgsql stable "
    r"set search_path = '' as \$\$(?P<body>.*)\$\$"
)
HOOK_BODY = " begin return jsonb_set(event, '{claims,user_metadata}', '{}'::jsonb); end; "  # empties user_metadata
HOOK_GRANTS = {
    "grant usage on schema public to supabase_auth_admin",
    "grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin",
}
REVOKE_FUNCTION = re.compile(
    r"revoke (?:all|execute) on function public\.(?P<name>\w+)\((?:jsonb)?\) from (?P<roles>[a-z, ]+)"
)
TRIGGER = re.compile(
    r"create trigger \w+ (?:before|after) (?:insert|update|delete)(?: or (?:insert|update|delete))*"
    r"(?: of \w+)? on (?P<table>(?:public|auth)\.\w+) for each row "
    r"(?:when \([^)]*\) )?execute function public\.(?P<function>\w+)\(\)"
)
DOLLAR_QUOTE = re.compile(r"\$\w*\$")
CASCADE = "references auth.users (id) on delete cascade"


def clean(text: str) -> str:
    """Comments removed, lower-cased, whitespace collapsed."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return re.sub(r"\s+", " ", text).lower()


def statements_of(text: str) -> list[str]:
    """The statements of a migration. A `;` inside a dollar-quoted function body does not end the statement."""
    text = clean(text)
    found: list[str] = []
    start = pos = 0
    while True:
        semicolon = text.find(";", pos)
        quote = DOLLAR_QUOTE.search(text, pos)
        if quote and (semicolon == -1 or quote.start() < semicolon):
            end = text.find(quote[0], quote.end())
            pos = len(text) if end == -1 else end + len(quote[0])
            continue
        if semicolon == -1:
            break
        found.append(text[start:semicolon])
        start = pos = semicolon + 1
    found.append(text[start:])
    return [s.strip() for s in found if s.strip()]


def statements() -> list[str]:
    return statements_of("\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.sql"))))


def words(csv: str) -> set[str]:
    return {w.strip() for w in csv.split(",")}


def columns_of(body: str) -> dict[str, str]:
    """Column name to its definition, from the body of a create table (the constraints are left out)."""
    items, depth, current = [], 0, ""
    for char in body:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    items.append(current.strip())
    return {
        item.split()[0]: item
        for item in items
        if not item.startswith(("primary key", "unique", "check", "foreign key", "constraint"))
    }


def allowed_privileges(table: str, role: str) -> set[str]:
    if role == "authenticated":
        return AUTHENTICATED_MAY.get(table, set())
    return {"select"} if table == "jobs" else set()  # anon and public


def grant_problem(s: str) -> str | None:
    grant = GRANT.fullmatch(s)
    if not grant:
        return None
    for role in words(grant["roles"]) & PUBLIC_ROLES:
        if not words(grant["privs"]) <= allowed_privileges(grant["table"], role):
            return f"{role} may not get these privileges on {grant['table']}: {s}"
    return None


def policy_problem(s: str) -> str | None:
    policy = POLICY.fullmatch(s)
    if not policy:
        return None
    table, command, roles = policy["table"], policy["command"], words(policy["roles"])
    using, check = policy["using"], policy["check"]
    own = sorted(OWN_ROW)[0]
    if table == "jobs":
        wrong = command != "select" or using != "status = 'open'" or check or not roles <= PUBLIC_ROLES
        return f"the jobs policy must be select of open jobs for public roles: {s}" if wrong else None
    wants_using, wants_check = command in {"select", "update", "delete"}, command in {"insert", "update"}
    checks = [
        (table not in USER_TABLES, f"no policy is allowed on {table}: {s}"),
        (roles != {"authenticated"}, f"a policy on {table} must be for authenticated only: {s}"),
        (command not in AUTHENTICATED_MAY.get(table, set()), f"{table} has no {command} privilege: {s}"),
        ((using in OWN_ROW) != wants_using, f"a {command} policy on {table} must use {own}: {s}"),
        ((check in OWN_ROW) != wants_check, f"a {command} policy on {table} must check {own}: {s}"),
    ]
    return next((message for wrong, message in checks if wrong), None)


def table_problems(table: str, body: str) -> list[str]:
    """Columns that would keep more than the email, and user tables that would outlive their auth user."""
    columns = columns_of(body)
    found = [
        f"{table}.{name} looks like personal data other than the email"
        for name in columns
        if PERSONAL_COLUMN.search(name)
    ]
    if table == "profiles" and set(columns) != PROFILE_COLUMNS:
        found.append(f"profiles may hold only {sorted(PROFILE_COLUMNS)}, not {sorted(columns)}")
    if table in USER_TABLES and CASCADE not in columns.get("user_id", ""):
        found.append(f"{table}.user_id must be `{CASCADE}`")
    return found


def parse_function(s: str) -> tuple[str, bool, list[str]] | None:
    """(name, security definer, problems) of a create function statement this lint knows, or None."""
    if m := HOOK.fullmatch(s):
        bad = [] if m["body"] == HOOK_BODY else [f"the access token hook may only empty user_metadata: {s}"]
        return "custom_access_token_hook", False, bad
    if m := FUNCTION.fullmatch(s):
        bad = [f"function {m['name']} reads data the provider sends: {s}"] if PROVIDER_DATA.search(m["body"]) else []
        return m["name"], bool(m["definer"]), bad
    return None


def problems(stmts: list[str]) -> list[str]:
    """Everything wrong with a migration, as messages; empty when it is safe by this lint's rules."""
    found: list[str] = []
    tables: set[str] = set()
    secured: set[str] = set()
    revoked: set[tuple[str, str]] = set()
    policies: dict[str, set[str]] = {}
    functions: dict[str, bool] = {}  # name -> security definer
    closed: set[tuple[str, str]] = set()
    triggers: list[tuple[str, str]] = []
    for s in stmts:
        if m := CREATE_TABLE.fullmatch(s):
            tables.add(m["table"])
            found.extend(table_problems(m["table"], m["body"]))
        elif m := ENABLE_RLS.fullmatch(s):
            secured.add(m["table"])
        elif m := REVOKE.fullmatch(s):
            privs = words(m["privs"])
            if privs & {"all", "all privileges"} or privs >= ALL_WRITES:
                revoked |= {(m["table"], role) for role in words(m["roles"])}
        elif m := POLICY.fullmatch(s):
            policies.setdefault(m["table"], set()).add(m["command"])
        elif function := parse_function(s):
            functions[function[0]] = function[1]
            found.extend(function[2])
        elif m := REVOKE_FUNCTION.fullmatch(s):
            closed |= {(m["name"], role) for role in words(m["roles"])}
        elif m := TRIGGER.fullmatch(s):
            triggers.append((m["table"], m["function"]))
        elif not (CREATE_INDEX.fullmatch(s) or GRANT.fullmatch(s) or s in HOOK_GRANTS or s == BACKFILL):
            found.append(f"statement kind not allowed by the lint: {s}")
        found.extend(p for p in (grant_problem(s), policy_problem(s)) if p)
    found.extend(f"row-level security not enabled on {t}" for t in sorted(tables - secured))
    found.extend(
        f"all privileges on {t} not revoked from {role}"
        for t in sorted(tables)
        for role in ("anon", "authenticated")
        if (t, role) not in revoked
    )
    found.extend(
        f"policies on {t} are {sorted(policies.get(t, set()))}, not {sorted(AUTHENTICATED_MAY[t])}"
        for t in sorted(tables & set(AUTHENTICATED_MAY))
        if policies.get(t, set()) != AUTHENTICATED_MAY[t]
    )
    found.extend(
        f"function {name} is not closed to {role}"
        for name in sorted(functions)
        for role in ("public", "anon", "authenticated")
        if (name, role) not in closed
    )
    found.extend(trigger_problems(triggers, functions))
    return found


def trigger_problems(triggers: list[tuple[str, str]], functions: dict[str, bool]) -> list[str]:
    """Triggers that run an undefined function, or anything but the profile copy on auth.users."""
    found = []
    for table, function in triggers:
        if function not in functions:
            found.append(f"trigger on {table} runs {function}, which this migration does not define")
        elif table.startswith("auth.") and not (
            table == "auth.users" and function in AUTH_TRIGGER_FUNCTIONS and functions[function]
        ):
            found.append(
                f"only auth.users may have a trigger, running {sorted(AUTH_TRIGGER_FUNCTIONS)} (security definer)"
            )
    return found


def test_there_are_migrations_with_cli_names() -> None:
    files = sorted(MIGRATION_DIR.glob("*.sql"))
    assert files
    assert all(re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", p.name) for p in files)


def test_migrations_are_safe() -> None:
    stmts = statements()
    tables = {m["table"] for s in stmts if (m := CREATE_TABLE.fullmatch(s))}
    assert tables == {"jobs", "crawl_log", "profiles", "user_prefs", "user_jobs"}
    assert any(POLICY.fullmatch(s) for s in stmts)
    assert problems(stmts) == []


def test_crawl_log_has_no_policy_and_no_grant() -> None:
    assert not [s for s in statements() if s.startswith(("create policy", "grant")) and "crawl_log" in s]


def test_the_anonymous_role_gets_nothing_but_the_open_jobs() -> None:
    stmts = statements()
    grants = [g for s in stmts if (g := GRANT.fullmatch(s))]
    assert [(g["table"], g["privs"]) for g in grants if words(g["roles"]) & {"anon", "public"}] == [("jobs", "select")]
    policies = [p for s in stmts if (p := POLICY.fullmatch(s))]
    assert {p["table"] for p in policies if words(p["roles"]) & {"anon", "public"}} == {"jobs"}


def test_every_user_policy_is_for_the_signed_in_role_and_tests_the_own_row() -> None:
    policies = [p for s in statements() if (p := POLICY.fullmatch(s)) and p["table"] in USER_TABLES]
    assert {(p["table"], p["command"]) for p in policies} == {
        (table, command)
        for table, commands in AUTHENTICATED_MAY.items()
        if table in USER_TABLES
        for command in commands
    }
    for p in policies:
        assert words(p["roles"]) == {"authenticated"}
        assert {p["using"], p["check"]} - {None} <= OWN_ROW


def test_fr1_ac6_the_profile_keeps_only_the_email() -> None:
    stmts = statements()
    profiles = next(m for s in stmts if (m := CREATE_TABLE.fullmatch(s)) and m["table"] == "profiles")
    assert set(columns_of(profiles["body"])) == {"user_id", "email"}
    function = next(m for s in stmts if (m := FUNCTION.fullmatch(s)) and m["name"] == "sync_profile")
    assert function["definer"]
    assert not PROVIDER_DATA.search(function["body"])
    assert "new.email" in function["body"]
    assert "raw_user_meta_data" not in " ".join(stmts)  # not even named by a statement (comments are stripped)
    triggers = [m for s in stmts if (m := TRIGGER.fullmatch(s)) and m["table"].startswith("auth.")]
    assert [(t["table"], t["function"]) for t in triggers] == [("auth.users", "sync_profile")]
    # the access token carries the provider's name and photo as user_metadata unless this hook empties it
    hook = next(m for s in stmts if (m := HOOK.fullmatch(s)))
    assert hook["body"] == HOOK_BODY
    assert set(stmts) >= HOOK_GRANTS


def test_the_split_keeps_a_function_body_in_one_statement() -> None:
    body = "create function public.f() returns trigger language plpgsql set search_path = '' as $$ begin x; y; end; $$"
    assert statements_of(f"-- c\n{body};\nselect 1; /* ; */ select 2") == [body, "select 1", "select 2"]
    assert statements_of("select 1 as $a$ ; $a$; select 2") == ["select 1 as $a$ ; $a$", "select 2"]
    assert statements_of("select $$ unterminated; ") == ["select $$ unterminated;"]


BASE = "create table public.jobs (a text); alter table public.jobs enable row level security;"
REVOKES = "revoke all on public.jobs from anon, authenticated;"
REAL = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.sql")))


@pytest.mark.parametrize(
    "extra",
    [
        "alter default privileges in schema public grant all on tables to anon",
        "grant all on all tables in schema public to anon",
        "grant all on public.jobs to anon",
        'grant select on public.jobs to "anon"',
        "grant select on public.crawl_log to anon",
        "grant select on public.user_jobs to anon",
        "grant select on public.profiles to public",
        "alter table public.jobs disable row level security",
        "create view public.v as select * from public.jobs",
        "create function public.f() returns int security definer as $$ select 1 $$",
        "create unlogged table public.x (a text)",
        "create policy \"p\" on public.jobs for all to anon using (status = 'open')",
        "create policy \"read for select\" on public.jobs to anon using (status = 'open')",
        'create policy "p" on public.jobs for select to anon using (true)',
        "create policy \"p\" on public.jobs for select to anon using (status = 'open') with check (true)",
        "drop policy p on public.jobs",
        "insert into public.jobs (a) values ('x')",
        "create trigger t after insert on auth.users for each row execute function public.nothing()",
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
    policy = "create policy \"p\" on public.jobs for select to anon, authenticated using (status = 'open')"
    safe = f"{BASE} {REVOKES} grant select on public.jobs to anon, authenticated; {policy};"
    assert problems(statements_of(safe)) == []


OWN = "(select auth.uid()) = user_id"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # a policy that does not test the own row, or tests it on the wrong side
        (
            f'create policy "own job choices read" on public.user_jobs\n  for select to authenticated\n  using ({OWN})',
            'create policy "own job choices read" on public.user_jobs\n  for select to authenticated\n  using (true)',
        ),
        (
            f'for insert to authenticated\n  with check ({OWN});\ncreate policy "own job choices update"',
            'for insert to authenticated\n  with check (true);\ncreate policy "own job choices update"',
        ),
        (
            f'using ({OWN})\n  with check ({OWN});\ncreate policy "own prefs delete"',
            f'using ({OWN});\ncreate policy "own prefs delete"',
        ),  # update without the check: ids could be rewritten
        (
            f'create policy "own profile" on public.profiles\n  for select to authenticated\n  using ({OWN})',
            f'create policy "own profile" on public.profiles\n  for select to authenticated, anon\n  using ({OWN})',
        ),
        # privileges beyond the list
        (
            "grant select on public.profiles to authenticated;",
            "grant select, update on public.profiles to authenticated;",
        ),
        (
            "grant select, insert, update, delete on public.user_jobs to authenticated;",
            "grant all on public.user_jobs to authenticated;",
        ),
        (
            "grant select, insert, update, delete on public.user_jobs to authenticated;",
            "grant select, insert, update, delete on public.user_jobs to authenticated, anon;",
        ),
        # a granted command with no policy (the table would deny it) and a missing revoke
        (
            'create policy "own job choices delete" on public.user_jobs\n  for delete to authenticated\n'
            f"  using ({OWN});",
            "",
        ),
        ("revoke all on public.user_jobs from anon, authenticated;", "revoke all on public.user_jobs from anon;"),
        ("alter table public.user_jobs enable row level security;", ""),
        # personal data in the tables
        ("  email   text not null\n);", "  email   text not null,\n  full_name text\n);"),
        ("  email   text not null\n);", "  email   text not null,\n  avatar_url text\n);"),
        ("  state      text not null check", "  display_name text,\n  state      text not null check"),
        (
            "user_id    uuid not null references auth.users (id) on delete cascade,\n  company",
            "user_id    uuid not null,\n  company",
        ),  # the rows would outlive the auth user
        # functions
        (
            "insert into public.profiles (user_id, email) values (new.id, new.email)",
            "insert into public.profiles (user_id, email) values (new.id, new.raw_user_meta_data ->> 'email')",
        ),
        (
            "insert into public.profiles (user_id, email) values (new.id, new.email)",
            "insert into public.profiles (user_id, email) values (new.id, new.email); perform new.raw_user_meta_data",
        ),
        ("security definer\nset search_path = ''", "security definer"),
        (
            "revoke all on function public.sync_profile() from public, anon, authenticated;",
            "revoke all on function public.sync_profile() from public, anon;",
        ),
        ("revoke all on function public.touch_updated_at() from public, anon, authenticated;", ""),
        (
            "security definer\nset search_path = ''\nas $$\nbegin\n  insert",
            "set search_path = ''\nas $$\nbegin\n  insert",
        ),
        ("execute function public.sync_profile();", "execute function public.touch_updated_at();"),
        ("execute function public.sync_profile();", "execute function public.nothing();"),
        (
            "create trigger touch_user_jobs before insert or update on public.user_jobs",
            "create trigger touch_user_jobs before insert or update on auth.users",
        ),
        # the access token hook: it must empty user_metadata, and only Supabase Auth may run it
        ("return jsonb_set(event, '{claims,user_metadata}', '{}'::jsonb);", "return event;"),
        (
            "return jsonb_set(event, '{claims,user_metadata}', '{}'::jsonb);",
            "return jsonb_set(event, '{claims,user_metadata}', '{\"a\": 1}'::jsonb);",
        ),
        (
            "grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;",
            "grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin, anon;",
        ),
        (
            "revoke execute on function public.custom_access_token_hook(jsonb) from public, anon, authenticated;",
            "revoke execute on function public.custom_access_token_hook(jsonb) from public, anon;",
        ),
        ("set search_path = ''\nas $$\nbegin\n  return jsonb_set", "as $$\nbegin\n  return jsonb_set"),
        # anything else on the user tables
        (
            "insert into public.profiles (user_id, email) select id, email from auth.users where email is not null",
            "insert into public.profiles (user_id, email) select id, raw_user_meta_data ->> 'email' from auth.users",
        ),
        (
            "create trigger sync_profile after insert or update of email on auth.users",
            "create trigger sync_profile after insert or update on auth.identities",
        ),
    ],
)
def test_the_lint_rejects_a_weakened_accounts_migration(old: str, new: str) -> None:
    assert old in REAL, old
    assert problems(statements_of(REAL.replace(old, new)))


def test_the_accounts_migration_passes_when_unchanged() -> None:
    assert problems(statements_of(REAL)) == []


# ---- Stars and discards (step 4.2; FR-2 AC 10): the row limit and the discard that goes when its job closes ----


def function_named(name: str) -> re.Match[str]:
    return next(m for s in statements() if (m := FUNCTION.fullmatch(s)) and m["name"] == name)


def trigger_on(table: str, function: str) -> str:
    return next(
        s for s in statements() if (m := TRIGGER.fullmatch(s)) and (m["table"], m["function"]) == (table, function)
    )


def test_a_user_can_keep_a_limited_number_of_job_choices() -> None:
    limit = function_named("limit_user_jobs")
    assert not limit["definer"]  # it counts the caller's own rows, which row-level security already limits to theirs
    body = limit["body"]
    assert "pg_advisory_xact_lock(hashtext(new.user_id::text))" in body  # two requests cannot both pass the count
    assert ">= 2000" in body
    assert "errcode = '54000'" in body  # the code the page tells apart from a network failure
    assert (
        "where user_id = new.user_id and company = new.company and job_id = new.job_id" in body
    )  # a change is allowed
    assert trigger_on("public.user_jobs", "limit_user_jobs").startswith(
        "create trigger limit_user_jobs before insert on"
    )


def test_a_discard_of_a_job_that_is_not_open_is_skipped_and_a_star_is_kept() -> None:
    skip = function_named("skip_discard_of_closed_job")
    assert not skip["definer"]  # the signed-in user may read open jobs, which is all it looks at
    assert "new.state = 'discard'" in skip["body"]
    assert "status = 'open'" in skip["body"]
    assert "return null" in skip["body"]
    trigger = trigger_on("public.user_jobs", "skip_discard_of_closed_job")
    assert "before insert or update on public.user_jobs" in trigger


def test_fr2_ac10_a_discard_goes_when_its_job_closes_and_a_star_stays() -> None:
    drop = function_named("drop_discards_of_closed_job")
    assert drop["definer"]  # the rows belong to other users
    assert drop["body"] == (
        " begin delete from public.user_jobs"
        " where state = 'discard' and company = new.company and job_id = new.job_id; return new; end; "
    )
    trigger = trigger_on("public.jobs", "drop_discards_of_closed_job")
    assert "after insert or update of status on public.jobs" in trigger
    assert "when (new.status = 'closed')" in trigger


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "revoke all on function public.limit_user_jobs() from public, anon, authenticated;",
            "revoke all on function public.limit_user_jobs() from public, anon;",
        ),
        (
            "revoke all on function public.drop_discards_of_closed_job() from public, anon, authenticated;",
            "revoke all on function public.drop_discards_of_closed_job() from public, anon;",
        ),
        ("execute function public.limit_user_jobs();", "execute function public.nothing();"),
        (
            "language plpgsql\nset search_path = ''\nas $$\nbegin\n  -- One writer",
            "language plpgsql\nas $$\nbegin\n  -- One writer",
        ),
        (
            "language plpgsql\nsecurity definer\nset search_path = ''\nas $$\nbegin\n  delete",
            "language plpgsql\nsecurity definer\nas $$\nbegin\n  delete",
        ),
    ],
)
def test_the_lint_rejects_a_weakened_job_choices_migration(old: str, new: str) -> None:
    migration = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATION_DIR.glob("*.sql")))
    assert old in migration, old
    assert problems(statements_of(migration.replace(old, new)))
