"""Copy the open jobs of data/jobs.db into the Supabase `jobs` table (PostgREST over HTTPS), then close the jobs that
are no longer open. Run after the crawl: `python sync_supabase.py`.

Env: SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY. If either is empty the sync is skipped
with a message and exit code 0, so the daily workflow keeps working until the secrets are set. Any other problem exits
non-zero, so the workflow's failure alert fires. The service-role key bypasses row-level security: it is sent only in
request headers, never printed, and exposed only to the sync step of the workflow.

Every open job is sent, in Israel or not, with its `level` and `role_types` (classify.py) and its `regions` and
`locations` (regions.py). The upsert is keyed on (company, job_id) and merges columns, so a second run with the same
database writes the same values, and columns this module does not send (`description`, `details`) are left alone."""

import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import requests

import classify
import regions
from digest import DB

BATCH = 500  # rows per upsert request
PAGE = 1000  # rows per read; Supabase caps a response at 1,000 rows by default
TIMEOUT = 60
HTTP_OK, HTTP_REDIRECT = 200, 300
COLUMNS = ("company", "job_id", "title", "team", "location", "url", "posted", "first_seen", "last_seen")
OPEN_JOBS = f"SELECT {', '.join(COLUMNS)} FROM jobs WHERE status='open' ORDER BY company, job_id"
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


class Client:
    """The PostgREST endpoint of one project, with the service-role key."""

    def __init__(self, session: HttpSession, url: str, key: str) -> None:
        if not url.startswith("https://"):
            raise SyncError("SUPABASE_URL must start with https://")
        self.session = session
        self.base = url.rstrip("/") + "/rest/v1/jobs"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(self, method: str, params: Mapping[str, str | int], body: list[dict[str, Any]] | None = None) -> Response:
        headers = dict(self.headers)
        if body is not None:
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        try:
            response = self.session.request(
                method, self.base, params=params, json=body, headers=headers, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise SyncError(f"{method} jobs failed: {type(exc).__name__}") from None
        if not HTTP_OK <= response.status_code < HTTP_REDIRECT:
            raise SyncError(f"{method} jobs failed: HTTP {response.status_code} {response.text[:300]}")
        return response

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
            keys.update((row["company"], row["job_id"]) for row in page)
            if len(page) < PAGE:
                return keys
            offset += PAGE


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


def sync(conn: sqlite3.Connection, client: Client) -> Result:
    rows = list(open_rows(conn))
    if not rows:
        raise SyncError("no open jobs in the local database: refusing to close every job in Supabase")
    client.upsert(rows)
    still_open = {(row["company"], row["job_id"]) for row in rows}
    stale = sorted(client.open_keys() - still_open)
    client.upsert([{"company": company, "job_id": job_id, "status": "closed"} for company, job_id in stale])
    return Result(upserted=len(rows), closed=len(stale))


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
        result = sync(conn, Client(cast("HttpSession", requests.Session()), url, key))
        print(f"Supabase: {result.upserted} open jobs upserted, {result.closed} closed")
        print(quality_report(conn))
    except SyncError as exc:
        sys.exit(f"Supabase sync failed: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
