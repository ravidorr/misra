"""Copy the open jobs of data/jobs.db into the Supabase `jobs` table (PostgREST over HTTPS), then close the jobs that
are no longer open. Run after the crawl: `python sync_supabase.py`.

Env: SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY. If either is empty the sync is skipped
with a message and exit code 0, so the daily workflow keeps working until the secrets are set. Any other problem exits
non-zero, so the workflow's failure alert fires. The service-role key bypasses row-level security: it is sent only in
request headers, never printed, and exposed only to the sync step of the workflow.

After a good sync (so not on a day the crawl failed) the spam-guard counters are cleaned: `report_site_cleanup` (the
`report-site` function's) and `confirm_email_cleanup` (the confirmation email's), database functions only the service
role may run, delete the rows of every day before today, so a daily non-reversible hash is gone in the next daily
cleanup after its day ends, not only when a later request happens to be handled. A failed cleanup exits non-zero
like a failed sync, so the privacy notice's promise cannot silently stop being kept.

A company on the exclusion list (`data/excluded_companies.csv`) is deleted before anything is sent: its rows in `jobs`,
and the rows that keep its title and link or its job keys in `user_jobs` (a user's stars and discards) and `emailed`.
Only by exact company name, nothing when the list is empty, nothing at all when the list is bad or names more than
MAX_PURGE_COMPANIES companies that still have rows. Counts are logged, never the rows.

Every open job is sent, in Israel or not, with its `level` and `role_types` (classify.py) and its `regions` and
`locations` (regions.py). The upsert is keyed on (company, job_id) and merges columns, so a second run with the same
database writes the same values, and columns that request does not send are left alone.

The descriptions (PLAN.md step 7.1) are a second pass. The full text of a posting is never in data/jobs.db, which is
committed to a public repository: the crawl leaves it in a work file outside the checkout (descriptions.py) and this
step reads that file. Only the jobs that are open, not excluded and given a text by today's crawl get `description` and
`details`, in batches of their own (a batch needs the same columns in every row, and a job without a text must keep the
one it has). A hash of the text, kept in `details`, tells a posting that did not change: it is not sent again. The
snippet (descriptions.snippet: the first 255 characters at a word boundary, the only part of the text the public API
can read) is written with it, and refreshed when the stored one is not the one computed. A job that closes keeps its
text until `jobs_text_trim` sets description, details and snippet to null 30 days after the job closed (called after
this pass); the removal of an excluded company deletes the whole row, text included. A failure in this pass or in the
trim is a warning, never a failed step: the jobs are already stored and the day's update goes on."""

import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast

import requests

import classify
import descriptions
import regions
from common import DB, EXCLUDED_MIN_NAME, MAX_PURGE_COMPANIES, ExclusionError, is_excluded, load_excluded

BATCH = 500  # rows per upsert request
TEXT_BATCH = 25  # rows per upsert request with descriptions: up to 20,000 characters each
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
    texts: int = 0  # jobs whose description was written
    texts_same: int = 0  # jobs whose description was already stored as it is
    texts_failed: int = 0  # jobs whose description could not be written (a warning, not a failure)


class Client:
    """The PostgREST endpoint of one project, with the service-role key."""

    def __init__(self, session: HttpSession, url: str, key: str) -> None:
        if not url.startswith("https://"):
            raise SyncError("SUPABASE_URL must start with https://")
        self.session = session
        self.rest = url.rstrip("/") + "/rest/v1/"
        self.base = self.rest + "jobs"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(  # noqa: PLR0913, PLR0917
        self,
        method: str,
        params: Mapping[str, str | int],
        body: list[dict[str, Any]] | dict[str, Any] | None = None,
        table: str = "jobs",
        prefer: str | None = None,
        quiet: bool = False,
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
            reason = "" if quiet else f" {response.text[:300]}"  # an answer about a text can quote it: quiet omits
            raise SyncError(f"{method} {table} failed: HTTP {response.status_code}{reason}")
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

    def upsert(self, rows: list[dict[str, Any]], size: int | None = None, quiet: bool = False) -> None:
        size = size or BATCH
        for start in range(0, len(rows), size):
            self.call("POST", {"on_conflict": "company,job_id"}, rows[start : start + size], quiet=quiet)

    def open_keys(self) -> set[tuple[str, str]]:
        return set(self.open_text_hashes("company,job_id"))

    def stored_texts(self) -> dict[tuple[str, str], tuple[str | None, str | None]]:
        """The fingerprint of the stored description and the stored snippet of every open job (None when none)."""
        return self.open_text_hashes("company,job_id,snippet,hash:details->>" + descriptions.HASH_KEY)

    def open_text_hashes(self, select: str) -> dict[tuple[str, str], tuple[str | None, str | None]]:
        keys: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        offset = 0
        while True:
            params: dict[str, str | int] = {
                "select": select,
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
            keys.update(((row["company"], row["job_id"]), (row.get("hash"), row.get("snippet"))) for row in page)
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
    written, same, failed = push_texts(client, descriptions.read_work_file(), still_open)
    return Result(
        upserted=len(rows), closed=len(stale), purged=purged, texts=written, texts_same=same, texts_failed=failed
    )


def push_texts(
    client: Client, texts: Mapping[tuple[str, str], tuple[str, dict[str, Any]]], still_open: set[tuple[str, str]]
) -> tuple[int, int, int]:
    """Writes the descriptions of the open jobs that the crawl gave one for and that Supabase does not hold as they are.
    Returns (written, already stored, failed). Nothing here fails the sync: the jobs are stored by now. A request that
    fails is reported by its HTTP status only (an answer can quote the row, and the log is public), and the next batch
    still goes, so one bad row costs its batch and not every description."""
    wanted = sorted(still_open & texts.keys())
    if not wanted:
        if texts:  # the crawl left texts and none is for an open job: the keys do not match, which is worth a look
            print(f"::warning::{len(texts)} descriptions were left by the crawl and none is for an open job")
        return 0, 0, 0
    try:
        stored = client.stored_texts()
    except (
        SyncError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:  # an answer of another shape is no reason to fail the step
        reason = str(exc) if isinstance(exc, SyncError) else type(exc).__name__  # a SyncError is safe to print
        print(f"::warning::the stored descriptions could not be read, so none is written: {reason}")
        return 0, 0, len(wanted)
    fresh: list[dict[str, Any]] = []
    for company, job_id in wanted:
        text, details = texts[(company, job_id)]
        digest, short = descriptions.fingerprint(text, details), descriptions.snippet(text)
        if stored.get((company, job_id)) != (digest, short):  # a text, its details or its snippet is not stored as is
            fresh.append(
                {
                    "company": company,
                    "job_id": job_id,
                    "description": text,
                    "snippet": short,
                    "details": {**details, descriptions.HASH_KEY: digest},
                }
            )
    written = failed = 0
    for start in range(0, len(fresh), TEXT_BATCH):
        batch = fresh[start : start + TEXT_BATCH]
        try:
            client.upsert(batch, TEXT_BATCH, quiet=True)
            written += len(batch)
        except SyncError as exc:
            failed += len(batch)
            print(f"::warning::a batch of {len(batch)} descriptions was not written: {exc}")
    return written, len(wanted) - len(fresh), failed


CLEANUP_FUNCTIONS = ("report_site_cleanup", "confirm_email_cleanup")


def cleanup(client: Client, today: date) -> int:
    """Delete the spam-guard counters of every day before `today` (UTC, the day the functions count in) through the
    database functions `report_site_cleanup` and `confirm_email_cleanup`, and return how many rows went in all. Counts
    only, nothing personal is read."""
    total = 0
    for function in CLEANUP_FUNCTIONS:
        deleted = client.call(
            "POST", {}, {"p_day": today.isoformat()}, table=f"rpc/{function}", prefer="return=representation"
        ).json()
        if not isinstance(deleted, int) or isinstance(deleted, bool):
            raise SyncError(f"rpc/{function} returned something other than a count")
        total += deleted
    return total


def trim_texts(client: Client, today: date) -> int | None:
    """Deletes the text of the jobs that closed more than 30 days ago (description, details, snippet) through the
    database function `jobs_text_trim` and returns how many jobs lost text. Counts only, nothing is read. Never
    fails the step: a failure (the migration not applied yet, a bad answer) is a warning and None; the next run
    retries."""
    try:
        trimmed = client.call(
            "POST", {}, {"p_day": today.isoformat()}, table="rpc/jobs_text_trim", prefer="return=representation"
        ).json()
        if not isinstance(trimmed, int) or isinstance(trimmed, bool):
            raise SyncError("rpc/jobs_text_trim returned something other than a count")
    except (SyncError, ValueError) as exc:
        print(f"::warning::the text of closed jobs could not be trimmed: {exc}")
        return None
    return trimmed


def quality_report(conn: sqlite3.Connection) -> str:
    """The share of Israeli jobs in a region and of jobs not in Other, for the daily log."""
    jobs = conn.execute("SELECT title, location FROM jobs WHERE status='open'").fetchall()
    return "\n".join(
        [classify.report(title or "" for title, _ in jobs), regions.report(location for _, location in jobs)]
    )


def main() -> None:
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:  # an unset repository secret reaches the step as an empty string
        try:
            owed = load_excluded()
        except ExclusionError as exc:
            sys.exit(f"Supabase sync failed: {exc}")
        if owed:  # a removal that cannot reach Supabase must not look done
            sys.exit(f"Supabase sync failed: {len(owed)} companies are on the exclusion list but the secrets are empty")
        print(SKIP_MESSAGE)
        return
    conn = sqlite3.connect(DB)
    try:
        client = Client(cast("HttpSession", requests.Session()), url, key)
        result = sync(conn, client, load_excluded())
        print(f"Supabase: {result.upserted} open jobs upserted, {result.closed} closed, {result.purged} rows purged")
        done = f"{result.texts} written, {result.texts_same} unchanged, {result.texts_failed} failed"
        print(f"Supabase: descriptions {done}")
        trimmed = trim_texts(client, datetime.now(UTC).date())
        if trimmed is None:
            print("Supabase: the text of closed jobs was not trimmed today (see the warning above)")
        else:
            print(f"Supabase: {trimmed} closed jobs lost their text (30 days after closing)")
        print(f"Supabase: {cleanup(client, datetime.now(UTC).date())} spam-guard counter rows deleted")
        print(quality_report(conn))
    except (SyncError, ExclusionError) as exc:
        sys.exit(f"Supabase sync failed: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
