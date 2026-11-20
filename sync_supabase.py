"""The two ends of the daily crawl (PLAN.md step 9.1). Supabase holds the history; data/jobs.db is a scratch file.

`python sync_supabase.py --pull` runs before the crawl: it rebuilds data/jobs.db from Supabase (every row of `jobs`,
open and closed, without the text of the postings, and the whole `crawl_log`, each row with its Supabase id). It
reads through the export's `Source`, which checks every table against its exact row count, and it refuses an empty
`jobs` table: a crawl that starts from nothing would take every job for a new one (and the digest would email them
all), and a `jobs` table that holds fewer than half of the open jobs the dashboard last published (docs/jobs.json; a
partly hidden table would look like jobs that closed; `--allow-shrink` for a real loss; a docs/jobs.json that cannot be
read fails the pull too). An empty `crawl_log` is a warning (a new project has none). The file is written beside and
renamed over, so there is never a half-built one, and the crawl and the dashboard build refuse a missing file.

The pull also writes the company list (PLAN_2.md step 3.2): every row of the `companies` table, the excluded ones
included (the service-role key reads them), into the git-ignored scratch file data/companies.json, sorted by name and
written the way discover.py writes it. A record carries the keys the table has a value for; every record that is not
excluded also has its five discovered fields, empty where the table holds null, so a company added with a name and a
domain waits for discovery and stops no run. Beside it the pull keeps the baseline, data/companies.pulled.json: the same
list as the table holds it (a null is a key left out). Both files are built beside (`.part`) and renamed over. The pull
refuses an empty table, as it refuses an empty `jobs` (a run without the list would crawl nobody and, worse, without
the exclusions), and a table with fewer than half of the yardstick (the larger of the baseline's records and `covered`
in docs/jobs.json, which a fresh runner has). `--pull-companies` does only this (the discover-companies workflow has no
use for the history).

`python sync_supabase.py --push-companies` runs after discover.py: it sends back the discovered fields
(discover.DISCOVERED) that discovery changed, by exact name. What discovery changed is the file against the baseline,
and each update names the values the row had at the pull, so a row an editor changed since is left alone. It never
sends a field a person owns (`crawl`, `gap`, `excluded`, `links`, `domain`, `segment`, `israel_only`, `verify`). A
company that had a working feed is held back when discovery now finds nothing for it (a dead feed stays `ok` in the
table until someone edits the row), and the counts are logged, never a name.

`python sync_supabase.py` runs after the crawl: it copies the open jobs of data/jobs.db into the Supabase `jobs` table
(PostgREST over HTTPS), closes the jobs that are no longer open, writes the crawl log rows of this run (the ones with
no Supabase id yet) and deletes the log older than CRAWL_LOG_DAYS days.

Env: SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_ROLE_KEY. Either one empty is an error, not a skip:
the history lives in Supabase, so a run without it must go red, not carry on. The sync also refuses a data/jobs.db that
the pull did not write (a `touch`ed or older file): its first_seen values would overwrite Supabase's. Any problem exits
non-zero, so the workflow's failure alert fires. The service-role key bypasses row-level security: it is sent only in
request headers, never printed, and exposed only to the steps of the workflow that need it, never to the crawl.

After a good sync (so not on a day the crawl failed) the spam-guard counters are cleaned: `report_site_cleanup` (the
`report-site` function's) and `confirm_email_cleanup` (the confirmation email's), database functions only the service
role may run, delete the rows of every day before today, so a daily non-reversible hash is gone in the next daily
cleanup after its day ends, not only when a later request happens to be handled. A failed cleanup exits non-zero
like a failed sync, so the privacy notice's promise cannot silently stop being kept.

A company on the exclusion list (the excluded records of data/companies.json) is deleted before anything is
sent: its rows in `jobs` and `crawl_log`, and the rows that keep its title and link or its job keys in
`user_jobs` (a user's stars and discards) and `emailed`.
Only by exact company name, nothing when the list is empty, nothing at all when the list is bad or names more than
MAX_PURGE_COMPANIES companies that still have rows. Counts are logged, never the rows.

Every open job is sent, in Israel or not, with its `level` and `role_types` (classify.py) and its `regions` and
`locations` (regions.py). The upsert is keyed on (company, job_id) and merges columns, so a second run with the same
database writes the same values, and columns that request does not send are left alone.

The descriptions (PLAN.md step 7.1) are a second pass. The full text of a posting is never in data/jobs.db, nor in the
export (the repository is public): the crawl leaves it in a work file outside the checkout (descriptions.py) and this
step reads that file. Only the jobs that are open, not excluded and given a text by today's crawl get `description` and
`details`, in batches of their own (a batch needs the same columns in every row, and a job without a text must keep the
one it has). A hash of the text, kept in `details`, tells a posting that did not change: it is not sent again. The
snippet (descriptions.snippet: the first 255 characters at a word boundary, the only part of the text the public API
can read) is written with it, and refreshed when the stored one is not the one computed. A job that closes keeps its
text until `jobs_text_trim` sets description, details and snippet to null 30 days after the job closed (called after
this pass); the removal of an excluded company deletes the whole row, text included. A failure in this pass or in the
trim is a warning, never a failed step: the jobs are already stored and the day's update goes on."""

import contextlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import requests

import classify
import descriptions
import regions
from common import (
    COMPANIES,
    CRAWL_LOG_DAYS,
    DB,
    EXCLUDED_MIN_NAME,
    MAX_PURGE_COMPANIES,
    PULLED,
    RECORD_KEYS,
    ROBOTS_MAX_PURGE_COMPANIES,
    ROBOTS_NOTE,
    SCHEMA,
    ExclusionError,
    https_only,
    is_excluded,
    load_companies,
    load_excluded,
    open_history,
)
from discover import DISCOVERED
from export_accounts import ALLOW_SHRINK, CRAWL_LOG, JOBS, ExportError, Source, Table, fell_by_half
from export_accounts import HttpSession as ExportSession

BATCH = 500  # rows per upsert request
TEXT_BATCH = 25  # rows per upsert request with descriptions: up to 20,000 characters each
PAGE = 1000  # rows asked per read; Supabase caps a response at 1,000 rows by default, a project may set less
TIMEOUT = 60
HTTP_OK, HTTP_REDIRECT = 200, 300
COLUMNS = ("company", "job_id", "title", "team", "location", "url", "posted", "first_seen", "last_seen")
OPEN_JOBS = f"SELECT {', '.join(COLUMNS)} FROM jobs WHERE status='open' ORDER BY company, job_id"
HISTORY_COLUMNS = (*COLUMNS, "status")  # the columns of `jobs` that the scratch history keeps
LOG_COLUMNS = ("ts", "company", "ats", "ok", "n_jobs", "note")
PURGE_TABLES = ("user_jobs", "emailed", "jobs", "crawl_log")  # where a company's rows live: stars, emailed keys, jobs
# The company list, every column of the record, read through the export's Source (exact count, the key's whole view)
COMPANY_TABLE = Table("companies", ("name",), RECORD_KEYS, account=False)
PUBLISHED = "docs/jobs.json"  # the dashboard's last published jobs: the pull's yardstick for a partial read
EMPTY_SECRETS = "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is empty: the history lives in Supabase, so nothing can run"


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
    robots_deleted: int = 0  # jobs deleted for the companies that robots.txt kept us away from
    texts: int = 0  # jobs whose description was written
    texts_same: int = 0  # jobs whose description was already stored as it is
    texts_failed: int = 0  # jobs whose description could not be written (a warning, not a failure)
    logged: int = 0  # crawl log rows written


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

    def stored_texts(self) -> dict[tuple[str, str], tuple[str | None, str | None, bool | None]]:
        """The fingerprint of the stored description, the stored snippet and its cut flag of every open job (None
        when none)."""
        return self.open_text_hashes("company,job_id,snippet,snippet_cut,hash:details->>" + descriptions.HASH_KEY)

    def open_text_hashes(self, select: str) -> dict[tuple[str, str], tuple[str | None, str | None, bool | None]]:
        keys: dict[tuple[str, str], tuple[str | None, str | None, bool | None]] = {}
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
            keys.update(
                ((row["company"], row["job_id"]), (row.get("hash"), row.get("snippet"), row.get("snippet_cut")))
                for row in page
            )
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
        print(f"Supabase: exclusion list, deleted {counts} for an excluded company")  # no name: the log is public
    return total


def robots_skipped(conn: sqlite3.Connection) -> list[str]:
    """The companies whose every crawl in this run (the log rows with no Supabase id yet) was skipped because of
    robots.txt. A name with any other result in the run, such as a second board that crawled or failed, is not one."""
    rows = conn.execute(
        "SELECT company FROM crawl_log WHERE id IS NULL AND company IS NOT NULL GROUP BY company "
        "HAVING MIN(substr(COALESCE(note, ''), 1, ?) = ?) = 1 ORDER BY company",
        (len(ROBOTS_NOTE), ROBOTS_NOTE),
    )
    return [name for (name,) in rows]


def delete_robots_skipped(client: Client, names: Sequence[str]) -> tuple[int, int]:
    """Delete the jobs, open and closed with their text, of the companies that robots.txt kept us away from, so we hold
    no role of a site whose policy does not allow us to read it. Only the `jobs` rows, by exact company name: the
    crawl log (it is the coverage card) and the stars and emailed keys of users stay, and a star keeps its own title and
    link and shows as no longer listed. A company without rows sends no delete, so a second run does nothing.
    More than ROBOTS_MAX_PURGE_COMPANIES companies with rows at once is refused (a host that answered `Disallow: /` by
    mistake must not empty the table): the sync then closes their jobs, as before. Never fails the sync: a failure is a
    warning and the next run tries again. Prints counts only. Returns (jobs deleted, companies)."""
    try:
        present = [name for name in names if client.has_rows("jobs", name)]
    except SyncError as exc:
        print(f"::warning::the jobs of robots.txt-skipped companies could not be looked up: {exc}")
        return 0, 0
    if len(present) > ROBOTS_MAX_PURGE_COMPANIES:
        print(
            f"::warning::robots.txt skipped {len(present)} companies with jobs "
            f"(limit {ROBOTS_MAX_PURGE_COMPANIES}): nothing deleted"
        )
        return 0, 0
    jobs = companies = 0
    for name in present:
        try:
            jobs += client.delete_company("jobs", name)
            companies += 1
        except SyncError as exc:
            print(f"::warning::the jobs of a robots.txt-skipped company could not be deleted: {exc}")
    return jobs, companies


def published_count() -> int:
    """How many open jobs the dashboard last published. docs/jobs.json is committed, so every checkout has it: one that
    cannot be read is an error (the pull fails closed), not a reason to skip the check."""
    try:
        return len(json.loads(Path(PUBLISHED).read_text(encoding="utf-8"))["jobs"])
    except (OSError, ValueError, KeyError, TypeError):
        raise SyncError(
            f"{PUBLISHED} could not be read, so the pulled jobs cannot be compared with the last published ones "
            f"(run again with {ALLOW_SHRINK} to skip the check)"
        ) from None


def pull(source: Source, path: str = DB, allow_shrink: bool = False) -> tuple[int, int]:
    """Rebuild the scratch history at `path` from Supabase and return (jobs, crawl log rows). Everything is read before
    anything is written; an empty `jobs` table is refused (see the module's note), and so is one that holds fewer than
    half of the open jobs the dashboard last published (a partly hidden or partly lost table would look like jobs that
    closed, and the digest would email the rest as new); `allow_shrink` skips that second check for a real loss. The
    file is built beside and renamed over the old one."""
    jobs = source.read(JOBS)
    log = source.read(CRAWL_LOG)
    if not jobs:
        raise SyncError(
            "Supabase holds no jobs: refusing to start a crawl from an empty history "
            "(restore the backup first, CONTRIBUTING.md, 'Backups')"
        )
    open_jobs = sum(row["status"] == "open" for row in jobs)
    if not allow_shrink and fell_by_half(last := published_count(), open_jobs):
        raise SyncError(
            f"Supabase holds {open_jobs} open jobs and the dashboard last published {last}: refusing to crawl from a "
            f"history that lost more than half of them (a wrong key or a policy change?); if it is real, run again "
            f"with {ALLOW_SHRINK}"
        )
    part = Path(path + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)  # data/ holds nothing else in a fresh checkout
    part.unlink(missing_ok=True)
    conn = sqlite3.connect(part)
    try:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {PULLED}")  # what the sync checks: this file came from Supabase
        conn.executemany(
            f"INSERT INTO jobs({', '.join(HISTORY_COLUMNS)}) VALUES({', '.join('?' * len(HISTORY_COLUMNS))})",
            [tuple(row[column] for column in HISTORY_COLUMNS) for row in jobs],
        )
        conn.executemany(
            f"INSERT INTO crawl_log({', '.join(LOG_COLUMNS)}, id) VALUES({', '.join('?' * (len(LOG_COLUMNS) + 1))})",
            [(*(row[column] for column in LOG_COLUMNS), row["id"]) for row in log],
        )
        conn.commit()
    finally:
        conn.close()
    part.replace(path)
    return len(jobs), len(log)


def pull_companies(source: Source, path: str = COMPANIES) -> int:
    """Write the company list of Supabase to `path` and return how many records it holds.

    Every row is read before any file is touched. An empty table is refused, and so is one with fewer than half of the
    yardstick (see `list_yardstick`): a partly hidden or partly lost table would hide the excluded records and stop
    the purge. Two files are written, each built beside (`.part`) and renamed over the old one, `path` first and its
    baseline `data/companies.pulled.json` after it. The baseline is the table as it is, a null being a key left out,
    sorted by name ignoring case in the format of discover.py, in the order of common.RECORD_KEYS; `path` is the same
    list for the readers, in which every record that is not excluded also has its five discovered fields, empty where
    the table holds null (a company added with a name and a domain waits for discovery and stops no run)."""
    rows = source.read(COMPANY_TABLE)
    if not rows:
        raise SyncError(
            "Supabase holds no companies: refusing to start a run without the list "
            "(restore the backup first: restore_accounts.py, CONTRIBUTING.md, 'Backups')"
        )
    before = list_yardstick(path)
    if fell_by_half(before, len(rows)):
        raise SyncError(
            f"Supabase holds {len(rows)} companies and the yardstick is {before}: refusing a list that lost more than "
            "half of them (a wrong key or a policy change?); if it is real, delete "
            f"{baseline_path(path)} and lower `covered` in {PUBLISHED}, then pull again"
        )
    records = [{k: row[k] for k in RECORD_KEYS if row[k] is not None} for row in rows]
    records.sort(key=lambda record: str(record["name"]).casefold())
    # a company added with a name and a domain has no discovered field yet: the readers get empty ones (not found
    # yet, as discover.py writes them) so such a record waits for discovery and never stops a run
    filled = [
        {k: r.get(k, "") for k in RECORD_KEYS if k in r or (k in DISCOVERED and "excluded" not in r)} for r in records
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)  # data/ holds nothing else in a fresh checkout
    for target, text in ((path, dump(filled)), (baseline_path(path), dump(records))):
        part = Path(target + ".part")
        part.write_text(text, encoding="utf-8")
        part.replace(target)
    return len(records)


def read_records(path: str) -> list[dict[str, Any]]:
    """The records of a list file; a file that is not a list of records is a ValueError, like one that is not JSON."""
    records = load_companies(path)
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError("not a list of records")
    return records


def list_yardstick(path: str) -> int:
    """What the table must not fall far below: the larger of the records of the last pull here (its baseline, which a
    fresh runner does not have) and `covered` in docs/jobs.json (the companies with a working feed when the dashboard
    was last built, committed, so every checkout has it). A baseline that exists and cannot be read is a warning; a
    missing file is no yardstick."""
    counts = [0]
    try:
        counts.append(len(read_records(baseline_path(path))))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        print("::warning::the baseline of the last pull could not be read, so the list is not compared with it")
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):  # no published number: a local trial
        counts.append(int(json.loads(Path(PUBLISHED).read_text(encoding="utf-8"))["covered"]))
    return max(counts)


def dump(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, indent=1, ensure_ascii=False) + "\n"


def baseline_path(path: str = COMPANIES) -> str:
    """Where the pull keeps the list as it pulled it (data/companies.pulled.json), for the push to compare with."""
    return path.removesuffix(".json") + ".pulled.json"


def condition(value: Any) -> str:
    """The PostgREST filter for a column that held exactly this pulled value (a key the pull left out is null)."""
    return "is.null" if value is None else f"eq.{value}"


def push_companies(client: Client, path: str = COMPANIES) -> tuple[int, int, int, int]:
    """Send the discovered fields that discover.py changed back to the table and return (companies changed, left
    alone, failed, held back).

    What discovery changed is the file compared with the list as the pull wrote it (the baseline), never with the
    table as it is now, so a field an editor saved since the pull is not mistaken for a discovery. Only the crawled
    records are considered and only the five DISCOVERED fields are ever sent, a careers address as https://. Each
    request names the company and the values the row had at the pull for all five fields, plus `crawl` true and no
    exclusion: a row an editor changed since (a pin, a correction, off the crawl, excluded) matches nothing and is left
    alone. A record the baseline does not have is left alone too (a company is added in the `companies` table; the
    admin page gets an editor in PLAN_2 step 3.3).

    A company that had a working feed (status ok) is held back when discovery now finds nothing for it: a status that
    is not ok, or a blank where there was a value, is never pushed over it, and one warning counts them (no names). A
    request that fails is counted and the next one still goes; the log holds the status, never the row."""
    baseline = {str(r.get("name")): r for r in read_records(baseline_path(path))}
    if not baseline:
        raise SyncError("the pulled list is empty: nothing to compare with, nothing written")
    changed = alone = failed = held = sent = 0
    for record in read_records(path):
        if record.get("crawl") is not True:
            continue
        pulled = baseline.get(str(record.get("name")))
        if pulled is None or pulled.get("crawl") is not True:
            alone += 1
            continue
        found = {k: record.get(k) or "" for k in DISCOVERED}
        found["careers_url"] = https_only(found["careers_url"])
        fields = {k: v for k, v in found.items() if v != (pulled.get(k) or "")}
        if not fields:
            continue
        if pulled.get("status") == "ok" and (
            found["status"] != "ok" or any(not v and pulled.get(k) for k, v in fields.items())
        ):
            held += 1
            continue
        where: dict[str, str | int] = {
            "name": f"eq.{pulled['name']}",
            "crawl": "eq.true",
            "excluded": "is.null",
            "select": "name",
            **{k: condition(pulled.get(k)) for k in DISCOVERED},
        }
        sent += 1
        try:
            done = client.call(
                "PATCH", where, fields, table="companies", prefer="return=representation", quiet=True
            ).json()
            if not isinstance(done, list):
                raise SyncError("PATCH companies returned something other than a list")
        except (SyncError, ValueError) as exc:  # a ValueError is an answer that is not JSON
            failed += 1
            print(f"::warning::a company could not be updated: {exc if isinstance(exc, SyncError) else 'not JSON'}")
            continue
        if done:
            changed += 1
        else:  # an editor changed the row since the pull
            alone += 1
    if sent and not changed and not failed:  # every update found its row changed, or none matched at all
        print(f"::warning::none of {sent} updates matched a row: the conditions may not match what the table holds")
    if held:
        print(
            f"::warning::{held} companies with a working feed were left as they are: discovery found nothing for them"
        )
    return changed, alone, failed, held


def push_crawl_log(conn: sqlite3.Connection, client: Client) -> int:
    """Write the crawl log rows of this run: the ones with no Supabase id yet (the pull gave every older row its id).
    Each batch is marked as written as soon as it went, so a second sync of the same file (after a failure half way)
    writes only the rest and never a row twice. Returns how many went."""
    rows = conn.execute(f"SELECT rowid, {', '.join(LOG_COLUMNS)} FROM crawl_log WHERE id IS NULL ORDER BY ts, rowid")
    pending = rows.fetchall()
    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        client.call(
            "POST",
            {},
            [dict(zip(LOG_COLUMNS, values[1:], strict=True)) for values in batch],
            table="crawl_log",
            prefer="return=minimal",
        )
        conn.executemany("UPDATE crawl_log SET id=0 WHERE rowid=?", [(values[0],) for values in batch])
        conn.commit()
    return len(pending)


def trim_crawl_log(client: Client, today: date) -> None:
    """Delete the crawl log older than CRAWL_LOG_DAYS days, so the table, the pull and the backup stay a fixed size.
    Never fails the step: a failure is a warning and the next run retries."""
    cutoff = (today - timedelta(days=CRAWL_LOG_DAYS)).isoformat()  # ts is ISO text, so text order is time order
    try:
        client.call("DELETE", {"ts": f"lt.{cutoff}"}, table="crawl_log")
    except SyncError as exc:
        print(f"::warning::the old crawl log could not be trimmed: {exc}")


def sync(conn: sqlite3.Connection, client: Client, excluded: Sequence[str] = ()) -> Result:
    purged = purge(client, excluded)
    skipped = robots_skipped(conn)
    robots_jobs, robots_companies = delete_robots_skipped(client, skipped)
    print(f"Supabase: robots.txt, deleted {robots_jobs} jobs of {robots_companies} companies")
    # a crawl that crashed after its robots skips can leave their jobs in the scratch history: drop them there too, so
    # neither this sync nor a retry on the same file (the log rows are marked written by then) sends them back
    conn.executemany("DELETE FROM jobs WHERE company=?", [(name,) for name in skipped])
    conn.commit()
    rows = [row for row in open_rows(conn) if not is_excluded(row["company"], list(excluded))]
    if not rows:
        raise SyncError("no open jobs in the local database: refusing to close every job in Supabase")
    client.upsert(rows)
    still_open = {(row["company"], row["job_id"]) for row in rows}
    stale = sorted(client.open_keys() - still_open)
    client.upsert([{"company": company, "job_id": job_id, "status": "closed"} for company, job_id in stale])
    written, same, failed = push_texts(client, descriptions.read_work_file(), still_open)
    return Result(
        upserted=len(rows),
        closed=len(stale),
        purged=purged,
        robots_deleted=robots_jobs,
        texts=written,
        texts_same=same,
        texts_failed=failed,
        logged=push_crawl_log(conn, client),  # last: the jobs and the texts are stored whatever this does
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
        cut = descriptions.snippet_cut(text)
        if stored.get((company, job_id)) != (
            digest,
            short,
            cut,
        ):  # the text, details, snippet or flag is not stored as is
            fresh.append(
                {
                    "company": company,
                    "job_id": job_id,
                    "description": text,
                    "snippet": short,
                    "snippet_cut": cut,
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


def credentials() -> tuple[str, str]:
    url, key = os.environ.get("SUPABASE_URL", "").strip(), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:  # an unset repository secret reaches the step as an empty string
        sys.exit(f"Supabase failed: {EMPTY_SECRETS}")
    return url, key


def leave_held(client: Client) -> int | None:
    """After a pull: leaves the keys of the open jobs whose text Supabase holds in a work file outside the checkout, so
    the crawl asks a job page only where there is none, or on that job's refresh turn (descriptions.write_held, no text
    in it). Any
    failure is a warning and no file: the crawl then asks for every job without a text, at most DETAIL_CAP per company.
    Returns the count, or None when it could not be left."""
    held = descriptions.work_path(descriptions.HELD_FILE)
    try:
        held.unlink(missing_ok=True)  # never an older run's keys
    except OSError as exc:
        print(f"::warning::an older list of stored descriptions could not be removed: {type(exc).__name__}")
        return None
    try:
        stored = client.stored_texts()
        return descriptions.write_held([key for key, (digest, *_rest) in stored.items() if digest])
    except (SyncError, ValueError, KeyError, TypeError, OSError) as exc:
        reason = str(exc) if isinstance(exc, SyncError) else type(exc).__name__  # a SyncError is safe to print
        print(f"::warning::the stored descriptions could not be listed for the crawl: {reason}")
        return None


def pull_main(url: str, key: str, allow_shrink: bool = False, history: bool = True) -> None:
    """The pull step: the company list, and the job history unless `history` is False. A failure stops the run."""
    session = requests.Session()
    source = Source(cast("ExportSession", session), url, key)
    try:
        if history:
            jobs, log = pull(source, allow_shrink=allow_shrink)
        companies = pull_companies(source)
    except (SyncError, ExportError) as exc:
        sys.exit(f"Supabase pull failed: {exc}")
    if history:
        print(f"Supabase: pulled {jobs} jobs and {log} crawl log rows into {DB}")
    print(f"Supabase: pulled {companies} companies into {COMPANIES}")
    if not history:
        return
    held = leave_held(Client(cast("HttpSession", session), url, key))
    if held is not None:
        print(f"Supabase: {held} open jobs already hold a description")
    if not log:  # not a failure (a new project has none), but every sharp-drop hold starts again
        print("::warning::the crawl log is empty: held drops start again")


def push_companies_main(url: str, key: str) -> None:
    """The last step of the discover-companies workflow: send the discovered fields back (see `push_companies`)."""
    session = requests.Session()
    try:
        changed, alone, failed, held = push_companies(Client(cast("HttpSession", session), url, key))
    except (SyncError, ExportError) as exc:
        sys.exit(f"Supabase push of the companies failed: {exc}")
    except (OSError, ValueError) as exc:  # data/companies.json or its baseline cannot be read
        sys.exit(f"Supabase push of the companies failed: a list file cannot be read ({type(exc).__name__})")
    print(
        f"Supabase: {changed} companies updated, {alone} left alone (changed or removed since the pull), "
        f"{held} held back"
    )
    if failed:
        sys.exit(f"Supabase push of the companies failed for {failed} companies (see the warnings above)")


def main(argv: Sequence[str] | None = None) -> None:
    url, key = credentials()
    args = sys.argv[1:] if argv is None else argv
    if "--pull" in args or "--pull-companies" in args:
        pull_main(url, key, ALLOW_SHRINK in args, history="--pull" in args)
        return
    if "--push-companies" in args:
        push_companies_main(url, key)
        return
    conn = open_history()
    if conn.execute("PRAGMA user_version").fetchone()[0] != PULLED:
        conn.close()
        sys.exit(
            f"Supabase sync failed: {DB} was not written by --pull (it did not come from Supabase, so its first_seen "
            "values would overwrite the real ones)"
        )
    try:
        client = Client(cast("HttpSession", requests.Session()), url, key)
        result = sync(conn, client, load_excluded())
        print(f"Supabase: {result.upserted} open jobs upserted, {result.closed} closed, {result.purged} rows purged")
        done = f"{result.texts} written, {result.texts_same} unchanged, {result.texts_failed} failed"
        print(f"Supabase: descriptions {done}")
        print(f"Supabase: {result.logged} crawl log rows written")
        trim_crawl_log(client, datetime.now(UTC).date())
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
