"""Copy the open jobs of data/jobs.db into the Supabase `jobs` table (PostgREST over HTTPS), then close the jobs that
are no longer open. Run after the crawl: `python sync_supabase.py`.

Env: SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY. If either is empty the sync is skipped
with a message and exit code 0, so the daily workflow keeps working until the secrets are set. Any other problem exits
non-zero, so the workflow's failure alert fires. The service-role key bypasses row-level security: it is sent only in
request headers, never printed, and exposed only to the sync step of the workflow.

A company on the exclusion list (`data/excluded_companies.csv`) is deleted before anything is sent: its rows in `jobs`,
and the rows that keep its title and link or its job keys in `user_jobs` (a user's stars and discards) and `emailed`.
Only by exact company name, nothing when the list is empty, nothing at all when the list is bad or names more than
MAX_PURGE_COMPANIES companies that still have rows. Counts are logged, never the rows.

Every open job is sent, in Israel or not, with its `level` and `role_types` (classify.py) and its `regions` and
`locations` (regions.py). The upsert is keyed on (company, job_id) and merges columns, so a second run with the same
database writes the same values, and columns this module does not send (`description`, `details`) are left alone."""

import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import requests

import classify
import regions
from common import EXCLUDED_MIN_NAME, MAX_PURGE_COMPANIES, ExclusionError, is_excluded, load_excluded
from digest import DB

BATCH = 500  # rows per upsert request
PAGE = 1000  # rows asked per read; Supabase caps a response at 1,000 rows by default, a project may set less
TIMEOUT = 60
HTTP_OK, HTTP_REDIRECT = 200, 300
COLUMNS = ("company", "job_id", "title", "team", "location", "url", "posted", "first_seen", "last_seen")
OPEN_JOBS = f"SELECT {', '.join(COLUMNS)} FROM jobs WHERE status='open' ORDER BY company, job_id"
PURGE_TABLES = ("user_jobs", "emailed", "jobs")  # where a company's rows live: stars, emailed keys, jobs
SKIP_MESSAGE = "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is empty: skipping the Supabase sync"


class SyncError(Exception):
    """The sync cannot go on or cannot be trusted; the message is safe to print (it never holds the key)."""


class Response(Protocol):
    status_code: int
    text: str

    def json(self) -> Any: ...


class HttpSession(Protocol):
    def request(self, method: str, url: str, **kw: Any) -> Response: ...


@dataclass(frozen=True)
class Result:
    upserted: int
    closed: int
    purged: int = 0  # rows deleted for the companies on the exclusion list


class Client:
    """The PostgREST endpoint of one project, with the service-role key."""

    def __init__(self, session: HttpSession, url: str, key: str) -> None:
        if not url.startswith("https://"):
            raise SyncError("SUPABASE_URL must start with https://")
        self.session = session
        self.rest = url.rstrip("/") + "/rest/v1/"
        self.base = self.rest + "jobs"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(
        self,
        method: str,
        params: Mapping[str, str | int],
        body: list[dict[str, Any]] | None = None,
        table: str = "jobs",
        prefer: str | None = None,
    ) -> Response:
        headers = dict(self.headers)
        if body is not None:
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = self.session.request(
                method,
                self.rest + table,
                params=params,
                json=body,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise SyncError(f"{method} {table} failed: {type(exc).__name__}") from None
        if not HTTP_OK <= response.status_code < HTTP_REDIRECT:
            raise SyncError(f"{method} {table} failed: HTTP {response.status_code} {response.text[:300]}")
        return response

    def has_rows(self, table: str, company: str) -> bool:
        """Does the table hold any row of exactly this company? (`eq` is an exact, case-sensitive match.)"""
        page = self.call("GET", {"select": "company", "company": f"eq.{company}", "limit": 1}, table=table).json()
        if not isinstance(page, list):
            raise SyncError(f"GET {table} returned something other than a list")
        return bool(page)

    def delete_company(self, table: str, company: str) -> int:
        """Delete every row of exactly this company and return how many went. The filter is always there: a delete
        without one would clear the table."""
        if len(company) < EXCLUDED_MIN_NAME or company != company.strip():
            raise SyncError("refusing to delete without a proper company name")
        response = self.call(
            "DELETE", {"company": f"eq.{company}", "select": "company"}, table=table, prefer="return=representation"
        )
        deleted = response.json()
        if not isinstance(deleted, list):
            raise SyncError(f"DELETE {table} returned something other than a list")
        return len(deleted)

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        for start in range(0, len(rows), BATCH):
            self.call("POST", {"on_conflict": "company,job_id"}, rows[start : start + BATCH])

    def open_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        offset = 0
        while True:
            params: dict[str, str | int] = {
                "select": "company,job_id",
                "status": "eq.open",
                "order": "company.asc,job_id.asc",
                "limit": PAGE,
                "offset": offset,
            }
            page = self.call("GET", params).json()
            if not isinstance(page, list):
                raise SyncError("GET jobs returned something other than a list")
            if not page:  # a server may cap a response below PAGE, so only an empty page ends the read
                return keys
            keys.update((row["company"], row["job_id"]) for row in page)
            offset += len(page)


def open_rows(conn: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    """The open jobs, one dict per job with the same keys every time (a batch needs that), in a fixed order."""
    for values in conn.execute(OPEN_JOBS):
        row = dict(zip(COLUMNS, values, strict=True))
        place = regions.place(row["location"])
        row["posted"] = row["posted"] or None
        row["status"] = "open"
        row["level"] = classify.level(row["title"] or "")
        row["role_types"] = classify.role_types(row["title"] or "")
        row["regions"] = list(place.regions)
        row["locations"] = list(place.locations)
        yield row


def purge(client: Client, excluded: Sequence[str]) -> int:
    """Delete everything Supabase holds about the companies on the exclusion list: their jobs, and the stars and
    discards (which keep a title and a link) and emailed keys of users. Only by exact company name. An empty list
    sends no request. More than MAX_PURGE_COMPANIES companies with rows at once is refused before any delete: that is a
    mistake in the list, and the maintainer adds companies in separate runs. Prints counts, never rows. Returns the
    number of rows deleted."""
    present = [name for name in excluded if any(client.has_rows(table, name) for table in PURGE_TABLES)]
    if len(present) > MAX_PURGE_COMPANIES:
        raise SyncError(
            f"refusing to delete the data of {len(present)} companies at once (limit {MAX_PURGE_COMPANIES}): "
            "add them to the exclusion list in separate runs"
        )
    total = 0
    for name in present:
        counts = {table: client.delete_company(table, name) for table in PURGE_TABLES}
        total += sum(counts.values())
        print(f"Supabase: exclusion list, deleted {counts} for {name}")
    return total


def sync(conn: sqlite3.Connection, client: Client, excluded: Sequence[str] = ()) -> Result:
    purged = purge(client, excluded)
    rows = [row for row in open_rows(conn) if not is_excluded(row["company"], list(excluded))]
    if not rows:
        raise SyncError("no open jobs in the local database: refusing to close every job in Supabase")
    client.upsert(rows)
    still_open = {(row["company"], row["job_id"]) for row in rows}
    stale = sorted(client.open_keys() - still_open)
    client.upsert([{"company": company, "job_id": job_id, "status": "closed"} for company, job_id in stale])
    return Result(upserted=len(rows), closed=len(stale), purged=purged)


def quality_report(conn: sqlite3.Connection) -> str:
    """The share of Israeli jobs in a region and of jobs not in Other, for the daily log."""
    jobs = conn.execute("SELECT title, location FROM jobs WHERE status='open'").fetchall()
    return "\n".join(
        [classify.report(title or "" for title, _ in jobs), regions.report(location for _, location in jobs)]
    )


def main() -> None:
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:  # an unset repository secret reaches the step as an empty string
        print(SKIP_MESSAGE)
        return
    conn = sqlite3.connect(DB)
    try:
        result = sync(conn, Client(cast("HttpSession", requests.Session()), url, key), load_excluded())
        print(f"Supabase: {result.upserted} open jobs upserted, {result.closed} closed, {result.purged} rows purged")
        print(quality_report(conn))
    except (SyncError, ExclusionError) as exc:
        sys.exit(f"Supabase sync failed: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
