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
AUTHENTICATED_MAY = {
    "jobs": {"select"},
    "profiles": {"select"},
    "user_prefs": CRUD,
    "user_jobs": CRUD,
    "subscriptions": {"select", "update"},
}
# `emailed` belongs to the service role alone: no grant, no policy. `subscriptions` is granted column by column only
# (see COLUMN_GRANTS), never as a whole table, because a table-level select would include the two token hashes.
USER_TABLES = {"profiles", "user_prefs", "user_jobs", "subscriptions", "emailed"}
COLUMN_ONLY_TABLES = {"subscriptions"}
SUBSCRIPTION_COLUMNS = {
    "user_id",
    "address",
    "frequency",
    "filters",
    "confirm_token_hash",
    "confirm_token_expires_at",
    "unsubscribe_token_hash",
    "consent_given_at",
    "consent_confirmed_at",
    "unsubscribed_at",
    "created_at",
    "updated_at",
}
EMAILED_COLUMNS = {"user_id", "company", "job_id", "emailed_at"}
# What the signed-in role may select and update on `subscriptions`, by column: never a token hash or its expiry, and it
# may change only the two preference columns, so it can neither confirm itself, nor change the address that consent was
# given for, nor undo an unsubscribe.
COLUMN_GRANTS = {
    "select": SUBSCRIPTION_COLUMNS - {"confirm_token_hash", "confirm_token_expires_at", "unsubscribe_token_hash"},
    "update": {"frequency", "filters"},
}
# `address` is the one column that is an address on purpose: the place the digest goes, kept with the consent log.
PERSONAL_COLUMN_ALLOWED = {("subscriptions", "address")}
# The two functions behind confirm-email and unsubscribe: each may touch only these two tables, runs as the caller and
# is executable by the service role alone.
SUBSCRIPTION_FUNCTIONS = {
    "confirm_subscription": "p_hash text, p_now timestamptz",
    "unsubscribe_subscription": "p_hash text, p_now timestamptz",
}
SUBSCRIPTION_TABLES = {"subscriptions", "emailed"}
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
# The spam guard of the report-site Edge Function (step 5.1): two counter tables and the two functions that change
# them. Only the service role may touch any of it (no grant or policy for the public roles, execute for service_role).
REPORT_COLUMNS = {
    "report_ip_limits": {"day", "ip_hash", "reports"},
    "report_daily_cap": {"day", "sent"},
    # step 8.1: the call counter of the assistant endpoint, built like report_ip_limits (a keyed hash and the day)
    "assistant_ip_limits": {"day", "ip_hash", "calls"},
}
REPORT_TABLES = set(REPORT_COLUMNS)
REPORT_FUNCTIONS = {
    "report_site_reserve": "p_day date, p_ip_hash text, p_ip_limit integer, p_daily_cap integer",
    "report_site_release": "p_day date, p_ip_hash text",
    "report_site_cleanup": "p_day date",
    "assistant_reserve": "p_day date, p_ip_hash text, p_limit integer",
}
# Step 6.2: the spam guard of the confirmation email (counter tables) and the functions that turn the digest on and off
# for a signed-in user. Only the service role may touch any of it. The functions touch the subscription tables or these
# counters (and unsubscribe_user calls the step 6.1 function that does the clean-up) and nothing else.
CONFIRM_LIMIT_COLUMNS = {"confirm_limits": {"day", "key_hash", "sent"}, "confirm_daily_cap": {"day", "sent"}}
CONFIRM_LIMIT_TABLES = set(CONFIRM_LIMIT_COLUMNS)
SUBSCRIBE_FUNCTIONS = {
    "subscribe_start": (
        "p_user_id uuid, p_address text, p_frequency text, p_filters jsonb, p_confirm_hash text, "
        "p_confirm_expires timestamptz, p_unsubscribe_hash text, p_now timestamptz"
    ),
    "unsubscribe_user": "p_user_id uuid, p_now timestamptz",
    "confirm_email_reserve": (
        "p_day date, p_user_hash text, p_address_hash text, p_user_limit integer, p_address_limit integer, "
        "p_daily_cap integer"
    ),
    "confirm_email_release": "p_day date, p_user_hash text, p_address_hash text",
    "confirm_email_cleanup": "p_day date",
}
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
COLUMN_GRANT = re.compile(
    r"grant (?P<priv>[a-z]+) \((?P<columns>[a-z_, ]+)\) on public\.(?P<table>\w+) to (?P<roles>[a-z, ]+)"
)
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
REPORT_FUNCTION = re.compile(
    r"create (?:or replace )?function public\.(?P<name>report_site_reserve|report_site_release|report_site_cleanup"
    r"|assistant_reserve)"
    r"\((?P<args>[a-z_0-9 ,]*)\) "
    r"returns (?:text|void|integer) language plpgsql set search_path = '' as \$\$(?P<body>.*)\$\$"
)
SUBSCRIPTION_FUNCTION = re.compile(
    r"create function public\.(?P<name>confirm_subscription|unsubscribe_subscription)\((?P<args>[a-z_0-9 ,]*)\) "
    r"returns text language plpgsql set search_path = '' as \$\$(?P<body>.*)\$\$"
)
SUBSCRIBE_FUNCTION = re.compile(
    r"create function public\.(?P<name>subscribe_start|unsubscribe_user|confirm_email_reserve|confirm_email_release"
    r"|confirm_email_cleanup)"
    r"\((?P<args>[a-z_0-9 ,]*)\) "
    r"returns (?:text|integer) language plpgsql set search_path = '' as \$\$(?P<body>.*)\$\$"
)
REPORT_SIGNATURE = (
    r"(?:jsonb|date|date, text|date, text, integer|date, text, integer, integer|text, timestamptz|date, text, text"
    r"|date, text, text, integer, integer, integer|uuid, timestamptz"
    r"|uuid, text, text, jsonb, text, timestamptz, text, timestamptz"
    r"|text, text\[\], text\[\], text\[\], integer, integer|text)?"
)
REVOKE_FUNCTION = re.compile(
    rf"revoke (?:all|execute) on function public\.(?P<name>\w+)\({REPORT_SIGNATURE}\) from (?P<roles>[a-z, ]+)"
)
GRANT_REPORT_FUNCTION = re.compile(
    r"grant execute on function public\.(?P<name>report_site_reserve|report_site_release|report_site_cleanup"
    r"|assistant_reserve)"
    r"\((?:date, text, integer, integer|date, text, integer|date, text|date)\) to service_role"
)
GRANT_SUBSCRIBE_FUNCTION = re.compile(
    r"grant execute on function public\.(?P<name>confirm_email_reserve|confirm_email_release|confirm_email_cleanup"
    r"|subscribe_start|unsubscribe_user)"
    r"\((?:date, text, text, integer, integer, integer|date, text, text|date"
    r"|uuid, text, text, jsonb, text, timestamptz, text, timestamptz|uuid, timestamptz)\) to service_role"
)
GRANT_SUBSCRIPTION_FUNCTION = re.compile(
    r"grant execute on function public\.(?P<name>confirm_subscription|unsubscribe_subscription)"
    r"\(text, timestamptz\) to service_role"
)
TRIGGER = re.compile(
    r"create trigger \w+ (?:before|after) (?:insert|update|delete)(?: or (?:insert|update|delete))*"
    r"(?: of \w+)? on (?P<table>(?:public|auth)\.\w+) for each row "
    r"(?:when \([^)]*\) )?execute function public\.(?P<function>\w+)\(\)"
)
# Step 7.2: the one column added to `jobs` after the first migration, and the daily trim of the text of closed jobs.
ADD_JOBS_COLUMN = re.compile(r"alter table public\.jobs add column (?P<column>\w+) text")
JOBS_TRIM_FUNCTION = re.compile(
    r"create function public\.jobs_text_trim\((?P<args>[a-z_0-9 ,]*)\) returns integer language plpgsql "
    r"set search_path = '' as \$\$(?P<body>.*)\$\$"
)
GRANT_JOBS_TRIM_FUNCTION = re.compile(r"grant execute on function public\.jobs_text_trim\(date\) to service_role")
# Step 7.3: search over the text of a posting. The one security definer function the public roles may run, and the one
# index that is not on bare columns. Both are matched whole, so a change to either fails the lint until it is taught.
SEARCH_NAME = "search_job_keys"
SEARCH_FUNCTION = re.compile(
    r"create function public\.search_job_keys\((?P<args>[a-z_0-9 ,]*)\) returns jsonb language sql stable "
    r"security definer set search_path = '' as \$\$(?P<body>.*)\$\$"
)
SEARCH_INDEX = (
    "create index jobs_description_search on public.jobs using gin (to_tsvector('simple', coalesce(description, '')))"
)
GRANT_SEARCH_FUNCTION = "grant execute on function public.search_job_keys(text) to anon, authenticated, service_role"
# Step 8.1: the search the assistant endpoint runs. It is NOT definer (it runs as the service role, the only role that
# may execute it), reads `jobs` and `search_job_keys` and nothing else, and never names the text: the full text is
# reached through search_job_keys alone, so a change that reads `description` or `details` here fails the lint.
SEARCH_JOBS_NAME = "search_jobs"
SEARCH_JOBS_ARGS = (
    "p_query text, p_levels text[], p_role_types text[], p_regions text[], p_limit integer, p_offset integer"
)
SEARCH_JOBS_FUNCTION = re.compile(
    r"create function public\.search_jobs\((?P<args>[a-z_0-9 ,\[\]]*)\) returns jsonb language sql stable "
    r"set search_path = '' as \$\$(?P<body>.*)\$\$"
)
SEARCH_JOBS_GUARDS = (  # each is something the function must keep saying
    "j.status = 'open'",
    "limit greatest(1, least(coalesce(p_limit, 20), 50))",
    "public.search_job_keys(",
)
GRANT_SEARCH_JOBS = (
    "grant execute on function public.search_jobs(text, text[], text[], text[], integer, integer) to service_role"
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
    if column := COLUMN_GRANT.fullmatch(s):
        return column_grant_problem(column, s)
    grant = GRANT.fullmatch(s)
    if not grant:
        return None
    if grant["table"] in COLUMN_ONLY_TABLES and words(grant["roles"]) & PUBLIC_ROLES:
        return f"{grant['table']} may be granted column by column only: {s}"
    for role in words(grant["roles"]) & PUBLIC_ROLES:
        if not words(grant["privs"]) <= allowed_privileges(grant["table"], role):
            return f"{role} may not get these privileges on {grant['table']}: {s}"
    return None


# The columns of `jobs` that the public roles may read: all but the text of a posting and its details, which stay closed
# (migration 20261121100000_jobs_text_not_public.sql, the lawyer's answers of 2026-11-16). `snippet`, the first 255
# characters of the text, is the public part (migration 20261122100000_jobs_snippet.sql).
JOBS_PUBLIC_COLUMNS = {
    "company", "job_id", "title", "team", "location", "url", "posted", "first_seen", "last_seen", "status",
    "level", "role_types", "regions", "locations", "snippet",
}  # fmt: skip
JOBS_CLOSED_COLUMNS = {"description", "details"}


def column_grant_problem(grant: re.Match[str], s: str) -> str | None:
    """A column grant is for the signed-in role only, on `subscriptions` only, and only the listed columns. The one
    other: `jobs`, for anon and authenticated, the public columns and never the text."""
    if grant["table"] == "jobs":
        right = (
            grant["priv"] == "select"
            and words(grant["roles"]) == {"anon", "authenticated"}
            and words(grant["columns"]) <= JOBS_PUBLIC_COLUMNS  # the migrations grant the list in two parts
        )
        return (
            None
            if right
            else f"jobs may be granted to anon and authenticated only as {sorted(JOBS_PUBLIC_COLUMNS)}: {s}"
        )
    allowed = COLUMN_GRANTS.get(grant["priv"], set())
    wrong = (
        grant["table"] != "subscriptions"
        or words(grant["roles"]) != {"authenticated"}
        or not words(grant["columns"]) <= allowed
    )
    return f"authenticated may get only these columns of subscriptions: {sorted(allowed)}: {s}" if wrong else None


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
        if PERSONAL_COLUMN.search(name) and (table, name) not in PERSONAL_COLUMN_ALLOWED
    ]
    if table == "profiles" and set(columns) != PROFILE_COLUMNS:
        found.append(f"profiles may hold only {sorted(PROFILE_COLUMNS)}, not {sorted(columns)}")
    if table in REPORT_COLUMNS and set(columns) != REPORT_COLUMNS[table]:
        found.append(f"{table} may hold only {sorted(REPORT_COLUMNS[table])}, not {sorted(columns)}")
    if table in CONFIRM_LIMIT_COLUMNS and set(columns) != CONFIRM_LIMIT_COLUMNS[table]:
        found.append(f"{table} may hold only {sorted(CONFIRM_LIMIT_COLUMNS[table])}, not {sorted(columns)}")
    if table == "subscriptions" and set(columns) != SUBSCRIPTION_COLUMNS:
        found.append(f"subscriptions has other columns than {sorted(SUBSCRIPTION_COLUMNS)}: {sorted(columns)}")
    if table == "emailed" and set(columns) != EMAILED_COLUMNS:
        found.append(f"emailed has other columns than {sorted(EMAILED_COLUMNS)}: {sorted(columns)}")
    if table in USER_TABLES and CASCADE not in columns.get("user_id", ""):
        found.append(f"{table}.user_id must be `{CASCADE}`")
    return found


def parse_function(s: str) -> tuple[str, bool, list[str]] | None:
    """(name, security definer, problems) of a create function statement this lint knows, or None."""
    if m := HOOK.fullmatch(s):
        bad = [] if m["body"] == HOOK_BODY else [f"the access token hook may only empty user_metadata: {s}"]
        return "custom_access_token_hook", False, bad
    if m := REPORT_FUNCTION.fullmatch(s):
        touched = set(re.findall(r"public\.(\w+)", m["body"]))
        elsewhere = re.findall(r"\b(?:auth|storage|vault|extensions|net|pg_catalog|supabase_\w+)\.\w+", m["body"])
        bad = [] if m["args"] == REPORT_FUNCTIONS[m["name"]] else [f"function {m['name']} has another signature: {s}"]
        bad += [f"function {m['name']} may touch only the report tables, not {sorted(touched - REPORT_TABLES)}"] * bool(
            touched - REPORT_TABLES
        )
        bad += [f"function {m['name']} reaches outside public: {elsewhere}"] * bool(elsewhere)
        return m["name"], False, bad
    if m := SUBSCRIBE_FUNCTION.fullmatch(s):
        touched = set(re.findall(r"public\.(\w+)", m["body"])) - {"unsubscribe_subscription"}
        allowed = SUBSCRIPTION_TABLES | CONFIRM_LIMIT_TABLES
        elsewhere = re.findall(r"\b(?:auth|storage|vault|extensions|net|pg_catalog|supabase_\w+)\.\w+", m["body"])
        signature = m["args"].strip() == SUBSCRIBE_FUNCTIONS[m["name"]]
        bad = [] if signature else [f"function {m['name']} has another signature"]
        bad += [
            f"function {m['name']} may touch only the subscription tables and counters: {sorted(touched - allowed)}"
        ] * bool(touched - allowed)
        bad += [f"function {m['name']} reaches outside public: {elsewhere}"] * bool(elsewhere)
        return m["name"], False, bad
    if m := SUBSCRIPTION_FUNCTION.fullmatch(s):
        touched = set(re.findall(r"public\.(\w+)", m["body"]))
        elsewhere = re.findall(r"\b(?:auth|storage|vault|extensions|net|pg_catalog|supabase_\w+)\.\w+", m["body"])
        bad = [] if m["args"] == SUBSCRIPTION_FUNCTIONS[m["name"]] else [f"function {m['name']} has another signature"]
        bad += [
            f"function {m['name']} may touch only the subscription tables: {sorted(touched - SUBSCRIPTION_TABLES)}"
        ] * bool(touched - SUBSCRIPTION_TABLES)
        bad += [f"function {m['name']} reaches outside public: {elsewhere}"] * bool(elsewhere)
        return m["name"], False, bad
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
        elif m := JOBS_TRIM_FUNCTION.fullmatch(s):
            functions[JOBS_TRIM_NAME] = False
            found.extend(jobs_trim_problems(m))
        elif search := search_function(s):
            functions[search[0]] = search[1]
            found.extend(search[2])
        elif m := TRIGGER.fullmatch(s):
            triggers.append((m["table"], m["function"]))
        elif not (
            CREATE_INDEX.fullmatch(s)
            or GRANT.fullmatch(s)
            or COLUMN_GRANT.fullmatch(s)
            or GRANT_REPORT_FUNCTION.fullmatch(s)
            or GRANT_JOBS_TRIM_FUNCTION.fullmatch(s)
            or s in {SEARCH_INDEX, GRANT_SEARCH_FUNCTION, GRANT_SEARCH_JOBS}
            or ADD_JOBS_COLUMN.fullmatch(s)
            or GRANT_SUBSCRIPTION_FUNCTION.fullmatch(s)
            or GRANT_SUBSCRIBE_FUNCTION.fullmatch(s)
            or s in HOOK_GRANTS
            or s == BACKFILL
        ):
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


JOBS_TRIM_NAME = "jobs_text_trim"
JOBS_TRIM_WHERE = (
    "status = 'closed' and (description is not null or details is not null or snippet is not null) "
    "and coalesce(left(last_seen, 10), '') < to_char(p_day - 30, 'yyyy-mm-dd')"
)
JOBS_TRIM_NULLS = ("description", "details", "snippet")  # all the trim may write, to null


def jobs_trim_problems(m: re.Match[str]) -> list[str]:
    """The trim runs as the caller (the service role), has one signature, touches only `jobs` and nulls only the
    posting's text columns of closed jobs."""
    body = m["body"]
    touched = set(re.findall(r"public\.(\w+)", body)) - {"jobs"}
    found = [] if m["args"] == "p_day date" else [f"function {JOBS_TRIM_NAME} has another signature"]
    found += [f"function {JOBS_TRIM_NAME} may touch only jobs, not {sorted(touched)}"] * bool(touched)
    found += [f"function {JOBS_TRIM_NAME} reaches outside public"] * bool(
        re.search(r"\b(?:auth|storage|vault)\.", body)
    )
    found += [f"function {JOBS_TRIM_NAME} must delete nothing and update only closed jobs"] * bool(
        "delete" in body or "status = 'closed'" not in body
    )
    where = [" ".join(w.split()) for w in re.findall(r"\bwhere (.+?);", body, re.S)]
    found += [f"function {JOBS_TRIM_NAME} has another where clause: {where}"] * bool(where != [JOBS_TRIM_WHERE])
    assigned = re.findall(r"\bset (.+?) where", body, re.S)
    found += [f"function {JOBS_TRIM_NAME} may set only {sorted(JOBS_TRIM_NULLS)}"] * bool(
        [" ".join(a.split()) for a in assigned] != [", ".join(f"{c} = null" for c in JOBS_TRIM_NULLS)]
    )
    return found


SEARCH_SELECTS = "select j.company, j.job_id from public.jobs j"
SEARCH_RETURNS = "jsonb_agg(jsonb_build_array(found.company, found.job_id))"
SEARCH_GUARDS = (  # each is something the function must keep saying, see the comment of the migration
    "j.status = 'open'",
    "coalesce(p_query, '') ~ '[[:alnum:]]{3}'",
    "to_tsvector('simple', coalesce(j.description, '')) @@ plainto_tsquery('simple', left(p_query, 200))",
)
SEARCH_WHERE = (  # the whole clause, so nothing can be added to it or after it
    "where j.status = 'open' and coalesce(p_query, '') ~ '[[:alnum:]]{3}' "
    "and to_tsvector('simple', coalesce(j.description, '')) "
    "@@ plainto_tsquery('simple', left(p_query, 200)) order by j.company, j.job_id limit 3000 ) found"
)


def search_problems(m: re.Match[str]) -> list[str]:
    """The search is the one definer function the public roles may run, so it is pinned: one text argument, only `jobs`
    read, never written, only open jobs, a minimum and a maximum query, a limit, and only the two keys leave it (the
    text is read in the `where` clause and nowhere else)."""
    body = " ".join(m["body"].split())
    touched = set(re.findall(r"public\.(\w+)", body)) - {"jobs"}
    found = [] if m["args"] == "p_query text" else [f"function {SEARCH_NAME} has another signature"]
    found += [f"function {SEARCH_NAME} may touch only jobs, not {sorted(touched)}"] * bool(touched)
    found += [f"function {SEARCH_NAME} reaches outside public"] * bool(re.search(r"\b(?:auth|storage|vault)\.", body))
    found += [f"function {SEARCH_NAME} may only read"] * bool(re.search(r"\b(?:insert|update|delete|truncate)\b", body))
    found += [f"function {SEARCH_NAME} is missing: {g}" for g in SEARCH_GUARDS if g not in body]
    found += [f"function {SEARCH_NAME} may return only the two keys"] * (
        SEARCH_SELECTS not in body or SEARCH_RETURNS not in body or body.count("description") != 1
    )  # the one use is in the where clause
    found += [f"function {SEARCH_NAME} has another where clause"] * (SEARCH_WHERE not in body)
    found += [f"function {SEARCH_NAME} must use plainto_tsquery only, on description only"] * bool(
        re.search(r"(?<!plain)to_tsquery|websearch_|phraseto_|ts_headline|ts_rank|ts_stat|details|snippet", body)
    )
    return found


def search_function(s: str) -> tuple[str, bool, list[str]] | None:
    """(name, security definer, problems) of one of the two pinned search functions, or None."""
    if m := SEARCH_FUNCTION.fullmatch(s):
        return SEARCH_NAME, True, search_problems(m)
    if m := SEARCH_JOBS_FUNCTION.fullmatch(s):
        return SEARCH_JOBS_NAME, False, search_jobs_problems(m)
    return None


def search_jobs_problems(m: re.Match[str]) -> list[str]:
    """The assistant search reads open jobs through the one text function and returns at most 50 of them; it writes
    nothing and never names the columns that hold the text."""
    body = " ".join(m["body"].split())
    touched = set(re.findall(r"public\.(\w+)", body)) - {"jobs", "search_job_keys"}
    found = [] if m["args"].strip() == SEARCH_JOBS_ARGS else [f"function {SEARCH_JOBS_NAME} has another signature"]
    found += [f"function {SEARCH_JOBS_NAME} may touch only jobs and search_job_keys, not {sorted(touched)}"] * bool(
        touched
    )
    found += [f"function {SEARCH_JOBS_NAME} reaches outside public"] * bool(
        re.search(r"\b(?:auth|storage|vault)\.", body)
    )
    found += [f"function {SEARCH_JOBS_NAME} may only read"] * bool(
        re.search(r"\b(?:insert|update|delete|truncate)\b", body)
    )
    found += [f"function {SEARCH_JOBS_NAME} is missing: {g}" for g in SEARCH_JOBS_GUARDS if g not in body]
    found += [f"function {SEARCH_JOBS_NAME} must not name the text of a posting"] * bool(
        re.search(r"description|details", body)
    )
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
    assert (
        tables
        == {"jobs", "crawl_log", "profiles", "user_prefs", "user_jobs", "subscriptions", "emailed"}
        | REPORT_TABLES
        | CONFIRM_LIMIT_TABLES
    )
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


# ---- Report a site (step 5.1; FR-7): the counters of the spam guard belong to the service role alone ----


def test_the_report_counters_have_no_policy_and_no_grant_for_anyone_but_the_service_role() -> None:
    stmts = statements()
    mentions = [s for s in stmts if any(table in s for table in REPORT_TABLES)]
    assert not [s for s in mentions if s.startswith(("create policy", "grant"))]
    assert {m["table"] for s in stmts if (m := ENABLE_RLS.fullmatch(s))} >= REPORT_TABLES
    grants = [m for s in stmts if (m := GRANT_REPORT_FUNCTION.fullmatch(s))]
    assert {g["name"] for g in grants} == set(REPORT_FUNCTIONS)  # execute for service_role, and nothing else
    assert not [
        s
        for s in stmts
        if s.startswith("grant execute")
        and not GRANT_REPORT_FUNCTION.fullmatch(s)
        and not GRANT_SUBSCRIPTION_FUNCTION.fullmatch(s)
        and not GRANT_SUBSCRIBE_FUNCTION.fullmatch(s)
        and not GRANT_JOBS_TRIM_FUNCTION.fullmatch(s)
        and s not in {GRANT_SEARCH_FUNCTION, GRANT_SEARCH_JOBS}
        and s not in HOOK_GRANTS
    ]


def test_the_report_functions_are_invoker_functions_over_the_two_counters_only() -> None:
    functions = {m["name"]: m for s in statements() if (m := REPORT_FUNCTION.fullmatch(s))}
    assert set(functions) == set(REPORT_FUNCTIONS)
    for name, signature in REPORT_FUNCTIONS.items():
        assert functions[name]["args"] == signature
        assert "security definer" not in functions[name][0]  # they run with the caller's rights, the service role's
    reserve = functions["report_site_reserve"]["body"]
    assert "p_daily_cap < 1" in reserve  # a cap of zero sends nothing
    assert "where c.sent < p_daily_cap" in reserve  # the upsert refuses the one past the cap, atomically
    assert "delete from public.report_ip_limits where day < p_day - 1" in reserve  # old hashes do not pile up


def test_the_daily_cleanup_deletes_only_earlier_days_of_the_counters_and_refuses_a_wrong_day() -> None:
    # the function is defined twice: the report counters (5.1), then replaced to take the assistant's too (8.1)
    *_, cleanup = [
        m for s in statements() if (m := REPORT_FUNCTION.fullmatch(s)) and m["name"] == "report_site_cleanup"
    ]
    body = cleanup["body"]
    assert "delete from public.report_ip_limits where day < p_day;" in body
    assert "delete from public.report_daily_cap where day < p_day;" in body
    assert "delete from public.assistant_ip_limits where day < p_day;" in body
    assert "p_day is null or p_day > current_date" in body  # a wrong date can never wipe today's counters
    assert body.count("delete from") == len(REPORT_TABLES)  # one delete per counter table, nothing else


def test_the_report_counters_keep_nothing_personal() -> None:
    bodies = {
        m["table"]: columns_of(m["body"])
        for s in statements()
        if (m := CREATE_TABLE.fullmatch(s)) and m["table"] in REPORT_TABLES
    }
    assert {table: set(columns) for table, columns in bodies.items()} == REPORT_COLUMNS


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("alter table public.report_ip_limits enable row level security;", ""),
        ("alter table public.report_daily_cap enable row level security;", ""),
        (
            "revoke all on public.report_ip_limits from anon, authenticated;",
            "revoke all on public.report_ip_limits from anon;",
        ),
        ("revoke all on public.report_daily_cap from anon, authenticated;", ""),
        (
            "revoke all on function public.report_site_reserve(date, text, integer, integer)"
            " from public, anon, authenticated;",
            "revoke all on function public.report_site_reserve(date, text, integer, integer) from public, anon;",
        ),
        ("revoke all on function public.report_site_release(date, text) from public, anon, authenticated;", ""),
        (
            "grant execute on function public.report_site_release(date, text) to service_role;",
            "grant execute on function public.report_site_release(date, text) to service_role, anon;",
        ),
        (
            "grant execute on function public.report_site_reserve(date, text, integer, integer) to service_role;",
            "grant execute on function public.report_site_reserve(date, text, integer, integer) to anon;",
        ),
        (
            "revoke all on public.report_daily_cap from anon, authenticated;",
            "grant select on public.report_daily_cap to anon;",
        ),
        (
            "alter table public.report_daily_cap enable row level security;",
            "alter table public.report_daily_cap enable row level security;\n"
            'create policy "all" on public.report_daily_cap for select to anon using (true);',
        ),
        ("  ip_hash text    not null,", "  ip_hash text    not null,\n  email text,"),
        ("  ip_hash text    not null,", "  ip_hash text    not null,\n  address text,"),
        (
            "language plpgsql\nset search_path = ''\nas $$\ndeclare",
            "language plpgsql\nsecurity definer\nset search_path = ''\nas $$\ndeclare",
        ),
        ("language plpgsql\nset search_path = ''\nas $$\ndeclare", "language plpgsql\nas $$\ndeclare"),
        ("delete from public.report_ip_limits where day < p_day - 1;", "delete from public.user_jobs;"),
        ("delete from public.report_ip_limits where day < p_day - 1;", "delete from auth.users;"),
        (
            "revoke all on function public.report_site_release(date, text) from public, anon, authenticated;",
            "revoke all on function public.report_site_release(date, text) from anon, authenticated;",
        ),
        (
            "create function public.report_site_release(p_day date, p_ip_hash text)",
            "create function public.report_site_release(p_day date, p_ip_hash text, p_extra text)",
        ),
    ],
)
def test_the_lint_rejects_a_weakened_report_limits_migration(old: str, new: str) -> None:
    migration = (MIGRATION_DIR / "20261116110000_report_site_limits.sql").read_text(encoding="utf-8")
    assert old in migration, old
    weakened = migration.replace(old, new)
    assert problems(statements_of(f"{REAL.replace(migration, '')}\n{weakened}")), old


def test_the_text_of_a_posting_is_closed_to_the_public_roles_and_every_other_column_is_granted() -> None:
    stmts = statements()
    created = next(columns_of(m["body"]) for s in stmts if (m := CREATE_TABLE.fullmatch(s)) and m["table"] == "jobs")
    added = {m["column"] for s in stmts if (m := ADD_JOBS_COLUMN.fullmatch(s))}
    assert (
        set(created) | added == JOBS_PUBLIC_COLUMNS | JOBS_CLOSED_COLUMNS
    )  # a new column must be sorted into one of the two
    assert not JOBS_PUBLIC_COLUMNS & JOBS_CLOSED_COLUMNS
    # the table-level grant of the first migration is taken back, after it, and only the column grant follows
    table_level = stmts.index("grant select on public.jobs to anon, authenticated")
    revoke = stmts.index("revoke select on public.jobs from anon, authenticated")
    assert table_level < revoke
    column_grants = [m for s in stmts[revoke:] if (m := COLUMN_GRANT.fullmatch(s)) and m["table"] == "jobs"]
    assert [words(g["columns"]) for g in column_grants][-1] == {
        "snippet"
    }  # the snippet is granted by its own migration
    assert set().union(*(words(g["columns"]) for g in column_grants)) == JOBS_PUBLIC_COLUMNS
    assert not any(words(g["columns"]) & JOBS_CLOSED_COLUMNS for g in column_grants)
    assert not [s for s in stmts[revoke:] if GRANT.fullmatch(s) and "public.jobs" in s]  # no table-level grant again


def test_a_grant_of_the_posting_text_to_the_public_roles_is_refused() -> None:
    for columns in ("description", "company, details", "company, description, details"):
        s = f"grant select ({columns}) on public.jobs to anon, authenticated"
        assert grant_problem(s)
    assert grant_problem("grant select (company) on public.jobs to anon")  # both roles, the whole list


TRIM_FILE = MIGRATION_DIR / "20261123100000_jobs_text_trim.sql"
TRIM_GRANT = "grant execute on function public.jobs_text_trim(date) to service_role;"
TRIM_CLOSE = "revoke all on function public.jobs_text_trim(date) from public, anon, authenticated;"


def trim_body() -> str:
    (trim,) = [m for s in statements() if (m := JOBS_TRIM_FUNCTION.fullmatch(s))]
    return trim["body"]


def test_the_trim_nulls_the_text_columns_of_closed_jobs_after_30_days_and_refuses_a_wrong_day() -> None:
    body = trim_body()
    assert (
        "set description = null, details = null, snippet = null" in body
    )  # the text, its details (and hash), its snippet
    assert "where status = 'closed'" in body  # an open job is never touched
    assert "to_char(p_day - 30, 'yyyy-mm-dd')" in body  # more than 30 days after the last day the job was seen open
    assert "p_day is null or p_day > current_date" in body  # a wrong date can never trim early
    assert body.count("update public.jobs") == 1
    assert "delete" not in body  # the row stays: title, company, link and dates


def test_the_trim_is_an_invoker_function_executable_by_the_service_role_alone() -> None:
    text = TRIM_FILE.read_text(encoding="utf-8")
    assert TRIM_GRANT in text
    assert TRIM_CLOSE in text
    assert "security definer" not in text.split("create function", 1)[1].split("$$", 1)[0]
    assert problems(statements()) == []


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (TRIM_CLOSE, TRIM_CLOSE.replace("public, anon, authenticated", "public, anon")),
        (TRIM_CLOSE, ""),
        (TRIM_GRANT, TRIM_GRANT.replace("service_role", "service_role, anon")),
        (
            "create function public.jobs_text_trim(p_day date)",
            "create function public.jobs_text_trim(p_day date, p_x text)",
        ),
        ("where status = 'closed'", "where status = 'open'"),
        ("where status = 'closed'", "where true or status = 'closed'"),
        ("p_day - 30", "p_day - 3"),
        ("     and coalesce(", "     or coalesce("),
        ("   set description = null,", "   set description = null, title = null,"),
        (
            "  get diagnostics trimmed = row_count;",
            "  delete from public.user_jobs;\n  get diagnostics trimmed = row_count;",
        ),
        ("  get diagnostics trimmed = row_count;", "  delete from auth.users;\n  get diagnostics trimmed = row_count;"),
    ],
)
def test_a_weakened_trim_is_refused(old: str, new: str) -> None:
    text = TRIM_FILE.read_text(encoding="utf-8")
    assert old in text, old
    weakened = text.replace(old, new)
    assert problems(statements_of(f"{REAL.replace(text, '')}\n{weakened}")), old


@pytest.mark.parametrize("column", ["description", "details"])
def test_the_snippet_migration_cannot_grant_the_text(column: str) -> None:
    assert grant_problem(f"grant select (snippet, {column}) on public.jobs to anon, authenticated")


SEARCH_FILE = MIGRATION_DIR / "20261124100000_jobs_text_search.sql"
SEARCH_CLOSE = "revoke all on function public.search_job_keys(text) from public, anon, authenticated;"
SEARCH_GRANT = "grant execute on function public.search_job_keys(text) to anon, authenticated, service_role;"


def test_the_search_returns_keys_only_from_open_jobs_with_a_minimum_a_maximum_and_a_limit() -> None:
    text = SEARCH_FILE.read_text(encoding="utf-8")
    assert SEARCH_CLOSE in text
    assert SEARCH_GRANT in text
    (search,) = [m for s in statements() if (m := SEARCH_FUNCTION.fullmatch(s))]
    assert search_problems(search) == []
    assert "description" not in text.split("create function", 1)[1].split("$$", 2)[2]  # nothing after the body
    assert problems(statements()) == []
    # the public roles still cannot read the text itself: no column grant of it anywhere
    assert not any(words(g["columns"]) & JOBS_CLOSED_COLUMNS for s in statements() if (g := COLUMN_GRANT.fullmatch(s)))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (SEARCH_CLOSE, ""),
        (SEARCH_CLOSE, SEARCH_CLOSE.replace("public, anon, authenticated", "public, anon")),
        (SEARCH_GRANT, SEARCH_GRANT.replace("anon, ", "anon, public, ")),
        ("(p_query text)\nreturns", "(p_query text, p_all boolean)\nreturns"),
        ("security definer\n", ""),
        ("where j.status = 'open'", "where true"),
        ("coalesce(p_query, '') ~ '[[:alnum:]]{3}'", "coalesce(p_query, '') ~ '[[:alnum:]]{1}'"),
        ("where j.status = 'open'", "where j.status = 'open' or true"),
        ("limit 3000", "limit 30000"),
        ("plainto_tsquery('simple', left(p_query, 200))", "websearch_to_tsquery('simple', left(p_query, 200))"),
        (
            "@@ plainto_tsquery",
            "and to_tsvector('simple', j.details) @@ plainto_tsquery('x') @@ plainto_tsquery",
        ),
        ("limit 3000", ""),
        ("order by j.company, j.job_id", "order by j.company"),
        ("left(p_query, 200)", "p_query"),
        ("plainto_tsquery('simple'", "to_tsquery('simple'"),
        ("jsonb_build_array(found.company, found.job_id)", "jsonb_build_array(found.company, found.job_id, found.d)"),
        ("select j.company, j.job_id\n", "select j.company, j.job_id, j.description\n"),
        ("from public.jobs j\n", "from public.jobs j, public.user_jobs u\n"),
        ("    from (\n", "    from (\n      delete from public.jobs where true returning 1) x, (\n"),
        ("    from (\n", "    from auth.users, (\n"),
        ("coalesce(j.description, '')) @@", "coalesce(j.description, '')) @@ ts_headline('x', 'y') ||"),
        (
            "using gin (to_tsvector('simple', coalesce(description, '')))",
            "using gin (to_tsvector('simple', description))",
        ),
    ],
)
def test_a_weakened_search_is_refused(old: str, new: str) -> None:
    text = SEARCH_FILE.read_text(encoding="utf-8")
    assert old in text, old
    weakened = text.replace(old, new)
    assert problems(statements_of(f"{REAL.replace(text, '')}\n{weakened}")), old


@pytest.mark.parametrize("columns", ["description", "company, details", "snippet, description"])
def test_the_search_migration_cannot_open_the_text_by_a_grant(columns: str) -> None:
    assert grant_problem(f"grant select ({columns}) on public.jobs to anon, authenticated")


# ---- The assistant endpoint (step 8.1; FR-6): a counter like the report one, and a search that cannot open text ----

ASSISTANT_FILE = MIGRATION_DIR / "20261125100000_assistant_endpoint.sql"
SEARCH_JOBS_CLOSE = (
    "revoke all on function public.search_jobs(text, text[], text[], text[], integer, integer)"
    " from public, anon, authenticated;"
)
ASSISTANT_CLOSE = (
    "revoke all on function public.assistant_reserve(date, text, integer) from public, anon, authenticated;"
)


def test_the_assistant_search_is_for_the_service_role_alone_and_never_names_the_text() -> None:
    text = ASSISTANT_FILE.read_text(encoding="utf-8")
    assert SEARCH_JOBS_CLOSE in text
    assert ASSISTANT_CLOSE in text
    assert GRANT_SEARCH_JOBS in statements_of(text)
    (search,) = [m for s in statements() if (m := SEARCH_JOBS_FUNCTION.fullmatch(s))]
    assert search_jobs_problems(search) == []
    grants = [s for s in statements() if s.startswith("grant") and "search_jobs" in s]
    assert grants == [GRANT_SEARCH_JOBS]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (SEARCH_JOBS_CLOSE, ""),
        (SEARCH_JOBS_CLOSE, SEARCH_JOBS_CLOSE.replace("public, anon, authenticated", "public, anon")),
        (ASSISTANT_CLOSE, ""),
        (f"{GRANT_SEARCH_JOBS};", f"{GRANT_SEARCH_JOBS.replace('service_role', 'service_role, anon')};"),
        ("p_limit integer,\n  p_offset integer", "p_limit integer,\n  p_offset integer,\n  p_x text"),
        ("where j.status = 'open'", "where true"),
        ("least(coalesce(p_limit, 20), 50)", "coalesce(p_limit, 20)"),
        ("public.search_job_keys(array_to_string(q.terms, ' '))", "array[]::jsonb"),
        ("j.regions, j.locations, j.snippet", "j.regions, j.locations, j.snippet, j.description"),
        ("j.regions, j.locations, j.snippet", "j.regions, j.locations, j.snippet, j.details"),
        (
            "    select j.company, j.job_id, j.title",
            "    select (select count(*) from public.user_jobs), j.company, j.job_id, j.title",
        ),
        ("create function public.search_jobs(", "create function public.search_jobs_x("),
        ("language sql\nstable\nset search_path = ''\nas $$\n  with q", "language sql\nstable\nas $$\n  with q"),
        ("  delete from public.assistant_ip_limits where day < p_day - 1;\n", "  delete from auth.users;\n"),
        ("  ip_hash text    not null,", "  ip_hash text    not null,\n  email text,"),
        ("alter table public.assistant_ip_limits enable row level security;", ""),
        (
            "revoke all on public.assistant_ip_limits from anon, authenticated;",
            "revoke all on public.assistant_ip_limits from anon;",
        ),
    ],
)
def test_a_weakened_assistant_migration_is_refused(old: str, new: str) -> None:
    text = ASSISTANT_FILE.read_text(encoding="utf-8")
    assert old in text, old
    others = [p for p in sorted(MIGRATION_DIR.glob("*.sql")) if p != ASSISTANT_FILE]
    earlier = "\n".join(p.read_text(encoding="utf-8") for p in others)
    assert problems(statements_of(earlier + "\n" + text)) == []
    assert problems(statements_of(earlier + "\n" + text.replace(old, new)))
