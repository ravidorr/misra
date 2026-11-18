"""Turn the account export (`export_accounts.py`) back into SQL for Supabase (PLAN.md step 9.1, the restore path).

`python restore_accounts.py <export directory> <output .sql file> [--create-auth-users]` reads the eight `<table>.json`
files (the five account tables, then `jobs`, `crawl_log` and `companies`) and writes ONE SQL script. Nothing is
sent anywhere: the maintainer reads the script and runs it in the Supabase SQL editor (or with `psql`) of the project
to restore into. `CONTRIBUTING.md`, "Backups of the account tables", has the
steps and the rehearsal.

The script
- runs in one transaction, so a failure leaves nothing half restored;
- inserts the tables in foreign-key order (profiles, user_prefs, user_jobs, subscriptions, emailed), then `jobs`,
  `crawl_log` and `companies` (which point at no user, so every row goes in; a crawl log or company row keeps its `id`
  and the id sequence is moved past the largest one; a company keeps its `updated_at` and its `excluded` record).
  The job text is not in the export: the next crawl writes it again for the open jobs;
- uses `insert ... on conflict do nothing`: running it twice, or into a project that already holds some rows, changes
  nothing that is already there, and it never deletes anything;
- switches the tables' own triggers off for its length and back on at the end, so that the restored rows keep their
  timestamps and consent record (the triggers set `updated_at` to now, drop a discard of a job that is not open, and
  clear a confirmation when an address changes) instead of being rewritten while they go in;
- sends each table as one JSON text through `json_populate_recordset`, so Postgres converts every value itself.

Every row's `user_id` must exist in `auth.users` (the tables reference it). Without `--create-auth-users` the script
restores only the rows of people who still have an `auth.users` row, and silently skips the rest: it asks the database
itself (a `where exists (select 1 from auth.users ...)` on the exported user id, no email involved), so an account that
was deleted after the backup is never brought back, and the run does not fail on it. With `--create-auth-users` (a
new project that lost its sign-in rows) a first step creates a bare `auth.users` row (id and email, from `profiles`)
for each missing one, and then every row is restored; the person then signs in with Google as before and Supabase
links the sign-in to that row by its confirmed email. That option writes into Supabase's own table and has not been
tried against a real Supabase project, only against a local Postgres with the columns it names.

The script holds every person's data: it is written readable by the owner only, outside the repository, and never
printed. Only counts are printed."""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from export_accounts import ALL_TABLES, FILE_MODE, JOB_TABLES, TABLES, ExportError, Table, outside_repository

CREATE_AUTH_USERS = "--create-auth-users"
PAUSE = "--pause-subscriptions"
EXCLUDE = "--exclude-user-ids="
EMPTY_INSTANCE = "00000000-0000-0000-0000-000000000000"


class RestoreError(Exception):
    """The export cannot be restored; the message is safe to print (a file or a column name, never a value)."""


def load(directory: Path, table: Table) -> list[dict[str, Any]]:
    """The rows of one table's file, checked to be a list of rows whose columns are all columns of the table."""
    path = directory / f"{table.name}.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RestoreError(f"{path.name} cannot be read as JSON") from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RestoreError(f"{path.name} is not a list of rows")
    unknown = sorted({column for row in rows for column in row} - set(table.columns))
    if unknown:  # json_populate_recordset would drop them without a word
        raise RestoreError(f"{path.name} has columns {', '.join(unknown)} that the restore does not know")
    absent = [column for column in table.key if any(row.get(column) is None for row in rows)]
    if absent:
        raise RestoreError(f"{path.name} has a row without {', '.join(absent)}")
    return rows


def read_ids(path: Path) -> set[str]:
    """The user ids to leave out: one per line, blank lines and # comments ignored. An empty file is a valid answer
    ("nobody deleted their account since the backup")."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise RestoreError(f"{path.name} cannot be read") from None
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def leave_out(data: Mapping[str, Sequence[Mapping[str, Any]]], ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """The tables without the rows of the people who deleted their account after the backup was made (the job tables
    have no user and stay whole)."""
    return {name: [dict(row) for row in rows if str(row.get("user_id")) not in ids] for name, rows in data.items()}


def pause(data: dict[str, list[dict[str, Any]]], when: str) -> None:
    """Mark every subscription that is still active as unsubscribed (as the unsubscribe does: no filters), so that the
    people who unsubscribed after the backup are never emailed against their wish and must turn the email on again."""
    for row in data["subscriptions"]:
        if row.get("unsubscribed_at") is None:
            row["unsubscribed_at"], row["filters"] = when, {}


def literal(rows: Sequence[Mapping[str, Any]]) -> str:
    """The rows as one dollar-quoted JSON text; the tag never occurs in the JSON, so nothing inside can end it."""
    body = json.dumps(list(rows), ensure_ascii=True, sort_keys=True)
    tag = "restore"
    while f"${tag}$" in body:
        tag += "x"
    return f"${tag}${body}${tag}$"


def insert(table: Table, rows: Sequence[Mapping[str, Any]], only_existing_users: bool = True) -> str:
    """One table's insert. By default only the rows whose user still exists in `auth.users` go in (the existence check
    is the database's own, on the exported user id), so a deleted account is not brought back."""
    columns = ", ".join(table.columns)
    keep = "where exists (select 1 from auth.users u where u.id = r.user_id)\n" if only_existing_users else ""
    qualified = ", ".join(f"r.{column}" for column in table.columns) if only_existing_users else columns
    return (
        f"insert into public.{table.name} ({columns})\n"
        f"select {qualified} from json_populate_recordset(null::public.{table.name}, {literal(rows)}::json) r\n"
        f"{keep}"
        "on conflict do nothing;\n"
    )


def insert_all(table: Table, rows: Sequence[Mapping[str, Any]]) -> str:
    """One no-user table's insert: every row, none overwritten. A crawl log or company id is `generated always`, so it
    is given explicitly (that is what makes a second run a no-op), and the sequence moves past it afterwards."""
    columns = ", ".join(table.columns)
    override = " overriding system value" if table.key == ("id",) else ""
    text = (
        f"insert into public.{table.name} ({columns}){override}\n"
        f"select {columns} from json_populate_recordset(null::public.{table.name}, {literal(rows)}::json) r\n"
        "on conflict do nothing;\n"
    )
    if override:  # setval is strict: an empty table gives null and changes nothing
        text += (
            f"select setval(pg_get_serial_sequence('public.{table.name}', 'id'), "
            f"(select max(id) from public.{table.name}));\n"
        )
    return text


def auth_users(profiles: Sequence[Mapping[str, Any]]) -> str:
    """Create the bare sign-in rows the tables point at, for a project that lost them. Skips the ones that exist."""
    table = TABLES[0]
    return (
        "insert into auth.users (id, instance_id, aud, role, email, email_confirmed_at, created_at, updated_at,\n"
        "                        raw_app_meta_data, raw_user_meta_data)\n"
        f"select user_id, '{EMPTY_INSTANCE}', 'authenticated', 'authenticated', email, now(), now(), now(),\n"
        '       \'{"provider": "google", "providers": ["google"]}\'::jsonb, \'{}\'::jsonb\n'
        f"from json_populate_recordset(null::public.{table.name}, {literal(profiles)}::json)\n"
        "on conflict do nothing;\n"
    )


def script(data: Mapping[str, Sequence[Mapping[str, Any]]], with_auth_users: bool = False) -> str:
    """The whole restore script for the loaded tables."""
    names = [f"public.{table.name}" for table in ALL_TABLES]
    parts = [
        "-- Restore of the Misra account tables from the daily export. Idempotent and never deletes.\nbegin;\n",
        *(f"alter table {name} disable trigger user;\n" for name in names),
    ]
    if with_auth_users:
        parts.append(auth_users(data[TABLES[0].name]))
    parts.extend(insert(table, data[table.name], not with_auth_users) for table in TABLES)
    parts.extend(insert_all(table, data[table.name]) for table in JOB_TABLES)
    parts.extend(f"alter table {name} enable trigger user;\n" for name in names)
    parts.append("commit;\n")
    return "".join(parts)


def write_script(target: Path, text: str) -> None:
    """The script appears whole and readable by its owner only."""
    scratch = target.with_name(f".{target.name}.tmp")
    scratch.unlink(missing_ok=True)
    descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    scratch.replace(target)


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    with_auth_users, pausing = CREATE_AUTH_USERS in args, PAUSE in args
    excluded = [arg.removeprefix(EXCLUDE) for arg in args if arg.startswith(EXCLUDE)]
    paths = [arg for arg in args if arg not in (CREATE_AUTH_USERS, PAUSE) and not arg.startswith(EXCLUDE)]
    try:
        source, output = paths
    except ValueError:
        sys.exit(
            f"usage: python restore_accounts.py <export directory> <output .sql file> "
            f"[{CREATE_AUTH_USERS}] [{PAUSE}] [{EXCLUDE}<file of user ids>]"
        )
    if with_auth_users and not excluded:  # recreating sign-in rows would bring back people who asked for deletion
        sys.exit(f"restore failed: {CREATE_AUTH_USERS} needs {EXCLUDE}<file> (empty: nobody left since the backup)")
    try:
        target = outside_repository(Path(output))
        data = {table.name: load(Path(source), table) for table in ALL_TABLES}
        if excluded:
            data = leave_out(data, read_ids(Path(excluded[0])))
        if pausing:
            pause(data, datetime.now(UTC).isoformat())
        write_script(target, script(data, with_auth_users))
    except (ExportError, RestoreError) as exc:
        sys.exit(f"restore failed: {exc}")
    print("restore script: " + ", ".join(f"{name} {len(rows)}" for name, rows in data.items()))


if __name__ == "__main__":
    main()
