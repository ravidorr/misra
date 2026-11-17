"""Export the tables of Supabase to JSON files, one per table (PLAN.md step 9.1, the restore path).

`python export_accounts.py <directory>` reads every row of the tables that hold personal data or choices with the
service-role key (PostgREST over HTTPS, all columns, including the token hash columns) and writes `<table>.json` into
the directory: one row per line, keys sorted, rows sorted by primary key, so a day's diff holds only what changed. The
job history goes with them: `jobs` (open and closed, every column but the text of the posting) and `crawl_log`. Supabase
is their only home since step 9.1 (the crawl rebuilds a scratch SQLite file from it each run), so the export is their
backup. The text (`description`, `details`, `snippet`, and `snippet_cut`, a flag about the snippet) is left out on
purpose: it lives in Supabase only, it is trimmed 30 days after a job closes and a backup would outlive that, and the
next crawl writes it again for every open job.
The short-lived abuse counters are not exported either.

Env: SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY. Unlike the sync, an empty value is an
error, not a skip: a backup that quietly does not run is worse than a red run.

THE FILES HOLD PERSONAL DATA and this repository is public. The directory must be outside the checkout (the module
refuses one inside it), the files are written readable by their owner only, nothing is ever printed except counts, and
the daily workflow copies them to a private repository only (`publish_backup.py`). Every table is read completely
before any file is written, and a table that cannot be read, or that changed while it was read three times in a row,
stops the export with a non-zero exit: there is never a partial table on disk."""

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import requests

PAGE = 1000  # rows asked per read; Supabase caps a response at 1,000 rows by default, a project may set less
TIMEOUT = 60
HTTP_OK, HTTP_REDIRECT = 200, 300
ATTEMPTS = 3  # reads of one table before giving up when it keeps changing underneath the read
REPO_ROOT = Path(__file__).resolve().parent
FILE_MODE, DIR_MODE = 0o600, 0o700
ALLOW_SHRINK = "--allow-shrink"  # the flag of the backup and of the pull that accepts a real loss
SHRINK_FROM = 5  # a count that was at least this many and is now under half of it is a loss, not a change


def fell_by_half(before: int, after: int) -> bool:
    """Did a row count fall by more than half? One rule for the backup guard and the pull guard (a wrong key or a
    change of policy sees nothing, or only part, of a table)."""
    return before >= SHRINK_FROM and after * 2 < before


@dataclass(frozen=True)
class Table:
    name: str
    key: tuple[str, ...]  # the primary key, in the order the rows are sorted
    columns: tuple[str, ...]  # every column of the table (of `jobs`: every one but the text), in the migrations' order
    account: bool = True  # personal data of a user (read whole, `select=*`); False is the public job history


# In foreign-key order (all of them point at auth.users only). Keep the columns equal to the migrations: a test reads
# the migrations and fails when they differ.
TABLES = (
    Table("profiles", ("user_id",), ("user_id", "email")),
    Table("user_prefs", ("user_id",), ("user_id", "filters", "email_settings", "updated_at")),
    Table(
        "user_jobs",
        ("user_id", "company", "job_id"),
        ("user_id", "company", "job_id", "state", "title", "location", "url", "created_at", "updated_at"),
    ),
    Table(
        "subscriptions",
        ("user_id",),
        (
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
        ),
    ),
    Table("emailed", ("user_id", "company", "job_id"), ("user_id", "company", "job_id", "emailed_at")),
)
# The job history, after the account tables: it points at no user. Columns are named in the request, so the text of a
# posting is never read.
JOBS = Table(
    "jobs",
    ("company", "job_id"),
    (
        "company",
        "job_id",
        "title",
        "team",
        "location",
        "url",
        "posted",
        "first_seen",
        "last_seen",
        "status",
        "level",
        "role_types",
        "regions",
        "locations",
    ),
    account=False,
)
CRAWL_LOG = Table("crawl_log", ("id",), ("id", "ts", "company", "ats", "ok", "n_jobs", "note"), account=False)
JOB_TABLES = (JOBS, CRAWL_LOG)
ALL_TABLES = (*TABLES, *JOB_TABLES)
DIRECTORY_REFUSED = "refusing to write personal data inside the repository checkout; use a directory outside it"


class ExportError(Exception):
    """The export cannot be trusted; the message is safe to print (a table name or a status, never a row or a key)."""


class Response(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class HttpSession(Protocol):
    def request(self, method: str, url: str, **kw: Any) -> Response: ...


class Changed(Exception):
    """The table changed between the first page and the last: read it again."""


def outside_repository(path: Path) -> Path:
    """The resolved path, or an error when it is the checkout or inside it."""
    resolved = path.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ExportError(DIRECTORY_REFUSED)
    return resolved


def pk(table: Table, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """The primary key as the database gives it (text, or a number for `crawl_log.id`, which sorts as a number)."""
    return tuple(row[column] for column in table.key)


class Source:
    """The PostgREST endpoint of one project, with the service-role key (sent in headers only, never printed)."""

    def __init__(self, session: HttpSession, url: str, key: str) -> None:
        if not url.startswith("https://"):
            raise ExportError("SUPABASE_URL must start with https://")
        self.session = session
        self.rest = url.rstrip("/") + "/rest/v1/"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}

    def page(self, table: Table, offset: int) -> tuple[list[dict[str, Any]], int]:
        """One page of rows and the table's total row count from the Content-Range header."""
        params: dict[str, str | int] = {
            "select": "*" if table.account else ",".join(table.columns),
            "order": ",".join(f"{column}.asc" for column in table.key),
            "limit": PAGE,
            "offset": offset,
        }
        try:
            response = self.session.request(
                "GET",
                self.rest + table.name,
                params=params,
                headers=self.headers,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ExportError(f"reading {table.name} failed: {type(exc).__name__}") from None
        if not HTTP_OK <= response.status_code < HTTP_REDIRECT:
            raise ExportError(f"reading {table.name} failed: HTTP {response.status_code}")
        try:
            rows = response.json()
        except ValueError:
            raise ExportError(f"reading {table.name} failed: the answer is not JSON") from None
        total = response.headers.get("Content-Range", "").rpartition("/")[2]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows) or not total.isdigit():
            raise ExportError(f"reading {table.name} failed: an answer in an unexpected shape")
        return rows, int(total)

    def read_once(self, table: Table) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        total = -1
        while True:
            page, count = self.page(table, len(rows))
            if total < 0:
                total = count
            if not page:  # a server may cap a response below PAGE, so only an empty page ends the read
                break
            rows.extend(page)
        for row in rows:
            missing = [column for column in table.key if row.get(column) is None]
            if missing:
                raise ExportError(f"{table.name} has a row without {', '.join(missing)}: the schema changed")
            if set(row) != set(table.columns):  # a new or dropped column: the restore would refuse this file
                drift = sorted(set(row) ^ set(table.columns))
                raise ExportError(f"{table.name} columns differ from TABLES ({', '.join(drift)}): update TABLES")
        if len(rows) != total or len({pk(table, row) for row in rows}) != len(rows):
            raise Changed
        return rows

    def read(self, table: Table) -> list[dict[str, Any]]:
        """Every row of the table, sorted by primary key. A table that changes under the read (a sign-in in the middle
        of it) is read again, up to ATTEMPTS times, and never returned half read."""
        for _ in range(ATTEMPTS):
            try:
                return sorted(self.read_once(table), key=lambda row: pk(table, row))
            except Changed:
                continue
        raise ExportError(f"{table.name} kept changing while it was read, {ATTEMPTS} times: try again")


def render(rows: Sequence[Mapping[str, Any]]) -> str:
    """One row per line, keys sorted, so a diff shows exactly the rows that changed."""
    if not rows:
        return "[]\n"
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(", ", ": ")) for row in rows]
    return "[\n  " + ",\n  ".join(lines) + "\n]\n"


def write_files(directory: Path, data: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    """Write every table's file; each file appears whole (written beside, then renamed over)."""
    directory.mkdir(parents=True, mode=DIR_MODE, exist_ok=True)
    directory.chmod(DIR_MODE)
    for name, rows in data.items():
        target = directory / f"{name}.json"
        scratch = directory / f".{name}.json.tmp"
        scratch.unlink(missing_ok=True)
        descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(render(rows))
        scratch.replace(target)


def orphans(data: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    """Rows of the other account tables whose user has no row in `profiles`: someone who signed in between the reads."""
    known = {row["user_id"] for row in data["profiles"]}
    return sum(1 for name, rows in data.items() if name != "profiles" for row in rows if row["user_id"] not in known)


def export(source: Source, directory: Path) -> dict[str, int]:
    """Read all the tables, then write all the files. Returns the row count of each table. A row that points at a
    user missing from `profiles` means the tables were read around a sign-in: everything is read again (ATTEMPTS
    times), and the export fails rather than write a set the restore could not rebuild the users from."""
    directory = outside_repository(directory)
    for _ in range(ATTEMPTS):
        accounts = {table.name: source.read(table) for table in TABLES}
        if not accounts["profiles"] and any(accounts.values()):  # a wrong key sees nothing through RLS
            raise ExportError("profiles is empty but other tables are not: the key may be wrong, nothing was written")
        if not orphans(accounts):
            data = {**accounts, **{table.name: source.read(table) for table in JOB_TABLES}}
            write_files(directory, data)
            return {name: len(rows) for name, rows in data.items()}
    raise ExportError(f"rows of users without a profile kept appearing, {ATTEMPTS} times: try again")


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.exit("usage: python export_accounts.py <directory outside the repository>")
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        sys.exit("export failed: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is empty, so nothing can be backed up")
    try:
        counts = export(Source(cast("HttpSession", requests.Session()), url, key), Path(args[0]))
    except ExportError as exc:
        sys.exit(f"export failed: {exc}")
    print(
        "export: " + ", ".join(f"{name} {count}" for name, count in counts.items()) + f" ({sum(counts.values())} rows)"
    )


if __name__ == "__main__":
    main()
