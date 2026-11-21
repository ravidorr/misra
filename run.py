"""Crawl all companies with a working ATS, diff against the history (data/jobs.db, a scratch file pulled from
Supabase), mark new/closed. `--gaps` logs the coverage gaps; `--recheck` probes the researched sites that may reopen."""

import datetime
import queue
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import common
import descriptions
from adapters import ADAPTERS, add_descriptions
from browser_crawl import WHY, crawl_many
from common import (
    DB,
    DROP_BELIEVED,
    DROP_HOLD_DAYS,
    DROP_NOTE,
    ISRAEL,
    MAX_PURGE_COMPANIES,
    ROBOTS_NOTE,
    SCHEMA,
    ExclusionError,
    crawled_companies,
    is_excluded,
    load_companies,
    load_excluded,
    open_history,
)

DROP_MIN_OPEN = 10  # fewer open jobs swing too much to judge a drop; this many returning nothing is a broken page


def init() -> sqlite3.Connection:
    """An empty history for tests (or `touch data/jobs.db` for a local trial); a daily run starts from the pull."""
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c


def sharp_drop(c: sqlite3.Connection, name: str, n: int, today: str) -> tuple[str, bool]:
    """(crawl log note, held) for a browser crawl that returned n jobs. No note when it is not a sharp drop.

    A sharp drop is fewer than half the jobs open before, or no jobs at all when any were open. A site that starts
    paginating or capping its list, or a page the extractor can no longer read, looks like a company that closed
    most of its jobs, so the crawl is held: its jobs are recorded but nothing closes.

    The drop is believed on the next day once the company was held on each of the last DROP_HOLD_DAYS earlier days
    (a run that failed another way in between starts the count again). Runs on `today` do not count, so re-running
    the pipeline within a day cannot use up the hold. The exception is an empty crawl at a company with
    DROP_MIN_OPEN or more open jobs: that many roles vanishing at once is a page that stopped working (a cookie
    wall, a page that did not load), not closed roles, so it stays held and its jobs stay open."""
    prev = c.execute("SELECT COUNT(*) FROM jobs WHERE company=? AND status='open'", (name,)).fetchone()[0]
    empty = n == 0 and prev > 0
    if not empty and (prev < DROP_MIN_OPEN or n * 2 >= prev):
        return "", False
    rows = c.execute(
        "SELECT substr(ts,1,10), note FROM crawl_log WHERE company=? AND substr(ts,1,10) < ? ORDER BY ts DESC",
        (name, today),
    )
    held_by_day: dict[str, bool] = {}
    for day, note in rows:
        held_by_day.setdefault(day, (note or "").startswith(DROP_NOTE))  # the day's last run
    recent = list(held_by_day.values())[:DROP_HOLD_DAYS]
    detail = f"got {n}, {prev} open before"
    believable = not (empty and prev >= DROP_MIN_OPEN)
    if believable and len(recent) == DROP_HOLD_DAYS and all(recent):
        return f"{DROP_BELIEVED}: {detail}", False
    return f"{DROP_NOTE}: {detail}", True


def close_unseen(c: sqlite3.Connection, name: str, seen: set[str]) -> None:
    for (jid,) in c.execute("SELECT job_id FROM jobs WHERE company=? AND status='open'", (name,)).fetchall():
        if jid not in seen:
            c.execute("UPDATE jobs SET status='closed' WHERE company=? AND job_id=?", (name, jid))


def delete_robots_skips(c: sqlite3.Connection, names: set[str]) -> int:
    """Delete the jobs of the companies that robots.txt kept us away from in this run, from the scratch history, and
    return how many went. We do not hold the roles of a site whose policy does not allow us to read them: the sync
    deletes them from Supabase the same way, and the dashboard build no longer lists them.

    `names` is the companies skipped in this run that had no other result in it: two boards can share a company name,
    and a name with a crawled or failed board is never touched. Only a read robots.txt that disallows us makes a skip
    (common.robots_gate): one that cannot be fetched counts as allowed, so a glitch at a host deletes nothing."""
    return sum(c.execute("DELETE FROM jobs WHERE company=?", (name,)).rowcount for name in sorted(names))


def is_known(c: sqlite3.Connection, co: dict[str, Any], now: str) -> bool:
    """Has this company been seen before? A company seen for the first time is baselined silently, so adding one never
    floods the digest. Stored jobs say it was seen. An israel_only company stores only its Israel jobs, so one with
    none stored counts as seen once a crawl of the same kind has worked: otherwise the first Israel job it ever lists
    would be baselined silently and never reach the digest."""
    if c.execute("SELECT 1 FROM jobs WHERE company=?", (co["name"],)).fetchone():
        return True
    if not co.get("israel_only"):
        return False
    worked = c.execute(
        "SELECT 1 FROM crawl_log WHERE company=? AND ats=? AND ok=1 AND ts < ?", (co["name"], co["ats"], now)
    ).fetchone()
    return bool(worked)


def robots_skipped(co: dict[str, Any]) -> bool:
    """Was this company left uncrawled because its host's robots.txt does not allow it? That is a decision, not a
    failure: no retry, no failure count, no alert."""
    return WHY.get(co["slug"], "").startswith(ROBOTS_NOTE)


REASON_MAX = 150


def short_reason(why: str) -> str:
    """A failure reason fit for the public log: no URL query strings, one line, 150 characters at most."""
    return " ".join(re.sub(r"(https?://[^\s?#]*)[?#]\S*", r"\1", why, flags=re.IGNORECASE).split())[:REASON_MAX]


def fetch(co: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One adapter call. An adapter that raises fails its own company, not the whole run. A request that robots.txt
    refused makes the answer None with the reason ROBOTS_NOTE."""
    WHY.pop(co["slug"], None)
    common.reset_robots_block()
    try:
        jobs = ADAPTERS[co["ats"]](co["slug"])
    except Exception as exc:
        WHY[co["slug"]] = f"adapter error: {type(exc).__name__}: {exc}"[:200]
        return None
    if common.robots_blocked():  # even a part of the list is not the list: nothing may close on it
        WHY[co["slug"]] = f"{ROBOTS_NOTE} ({common.robots_blocked()})"
        return None
    return jobs


HELD_TEXTS: set[tuple[str, str]] = set()  # the jobs whose text Supabase holds, read from the pull's file by main()
DETAIL_STATS: dict[str, tuple[str, int, int, int]] = {}  # company -> (ats, requested, got a text, deferred)


def read_job_pages(co: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    """Gives a company's jobs the descriptions its list lacks (adapters.add_descriptions) and records the counts."""
    if co.get("israel_only"):  # the jobs the history keeps, so no page is read for one that is dropped
        jobs = [j for j in jobs if ISRAEL.search(j["location"] or "")]
    try:
        stats = add_descriptions(co["ats"], co["slug"], co["name"], jobs, HELD_TEXTS, datetime.date.today().toordinal())
    except Exception as exc:  # the list is what matters: a failure here costs texts, never the company
        print(f"::warning::job pages of {co['name']} failed: {type(exc).__name__}")
    else:
        if stats[0] or stats[2]:
            DETAIL_STATS[co["name"]] = (co["ats"], *stats)


def read_pages_of(
    co: dict[str, Any], jobs: list[dict[str, Any]] | None
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    if jobs:
        read_job_pages(co, jobs)
    return co, jobs


def crawl(co: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    jobs = fetch(co)
    if jobs is None and not robots_skipped(co):  # one retry: most failures are transient rate limits
        time.sleep(4)
        jobs = fetch(co)
    if jobs:  # after the list is final: nothing the job pages do can change it, and one that fails costs only its text
        read_job_pages(co, jobs)
    return co, jobs


def purge_excluded(c: sqlite3.Connection, excluded: list[str]) -> dict[str, int]:
    """Delete everything the history holds about the companies on the exclusion list: their jobs and crawl log. The
    match is the exact company name. Returns the number of jobs deleted per company that had any. A list that would
    clear more than MAX_PURGE_COMPANIES companies at once is refused before anything is deleted: add companies to
    the list in separate runs."""
    held = {
        name: n
        for name in excluded
        if (n := c.execute("SELECT COUNT(*) FROM jobs WHERE company=?", (name,)).fetchone()[0])
    }
    if len(held) > MAX_PURGE_COMPANIES:
        raise ExclusionError(
            f"refusing to delete the jobs of {len(held)} companies at once (limit {MAX_PURGE_COMPANIES}): "
            "add them to the exclusion list in separate runs"
        )
    for name in excluded:
        for table in ("jobs", "crawl_log"):
            c.execute(f"DELETE FROM {table} WHERE company=?", (name,))
    c.commit()
    return held


def close_uncrawled(c: sqlite3.Connection, names: list[str]) -> int:
    """Close the open jobs of the companies that are kept only for the coverage card (`crawl` is not true). Nothing
    crawls them, so nothing else would ever close their roles and the dashboard would list them as open for good. The
    rows stay, as closed history. Returns how many were closed."""
    n = sum(
        c.execute("UPDATE jobs SET status='closed' WHERE company=? AND status='open'", (name,)).rowcount
        for name in names
    )
    c.commit()
    return n


def check_spelling(c: sqlite3.Connection, known: list[str], excluded: list[str]) -> None:
    """Deleting needs the exact name, skipping does not care about case: a list that says `acme` while the data says
    `Acme` would hide the company but leave its rows. That must stop the run, so the list gets the stored spelling."""
    stored = {r[0] for t in ("jobs", "crawl_log") for r in c.execute(f"SELECT company FROM {t}")}
    if any(n and is_excluded(n, excluded) and n not in excluded for n in {*known, *stored}):
        raise ExclusionError(  # no name: an excluded company's name is private and the log is public
            "the exclusion list spells a company differently from the data: use the exact stored spelling"
        )


GAP_KINDS = ("failing", "held", "closed")  # the order gap_summary lists them in
FAILING_RUNS = 2  # a company is failing when its last two crawls failed, as in the dashboard (one-offs do not count)
GAP_STALE_DAYS = 7  # a company not crawled for this long before the newest crawl is no longer one of ours
DEAD_DAYS = 30  # a crawled company whose board has been empty (crawls ok, no job) this long is a dead board
GAP_NEW_DAYS = 2  # a gap is new when it began on the newest crawl day or this many days before it (a lost run fits)


def _outage_start(seq: list[tuple[int, str, str]]) -> str:
    """Timestamp of the first crawl of the run of failed crawls that ends the history."""
    start = seq[-1][2]
    for ok, _note, ts in reversed(seq):
        if ok:
            break
        start = ts
    return start


def coverage_gaps(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Companies whose crawls need a person's attention, one dict each: company, kind, note, since, new.

    kind is "failing" (the last two crawls failed, as in the dashboard's coverage gaps), "held" (the last crawl was
    held back as a sharp drop: nothing closed) or "closed" (a crawl believed a sharp drop and closed the jobs it no
    longer saw; this is an event, listed only while it is new). `since` identifies the gap: the start of the outage,
    or the believed crawl. A company whose last crawl was skipped because of robots.txt is none of these: it is on the
    dashboard's coverage card, and nobody has to act. `new` is True when `since` is on the newest crawl day or up to
    GAP_NEW_DAYS days before it (a company is failing from its second failed crawl, so an outage is new on the crawl
    that finds it and for two days after, and one lost daily run does not hide it). The history is rebuilt from
    Supabase on every run, so nothing remembers which gaps were logged. Companies not crawled in the last
    GAP_STALE_DAYS days are skipped."""
    history: dict[str, list[tuple[int, str, str]]] = {}
    query = "SELECT company, ts, ok, COALESCE(note, '') FROM crawl_log WHERE company IS NOT NULL ORDER BY ts, rowid"
    for company, ts, ok, note in c.execute(query):
        history.setdefault(company, []).append((ok, note, ts))
    if not history:
        return []
    newest = max(seq[-1][2][:10] for seq in history.values())
    cutoff = (datetime.date.fromisoformat(newest) - datetime.timedelta(days=GAP_STALE_DAYS)).isoformat()
    fresh = (datetime.date.fromisoformat(newest) - datetime.timedelta(days=GAP_NEW_DAYS)).isoformat()
    gaps: list[dict[str, Any]] = []

    def add(company: str, kind: str, note: str, since: str) -> None:
        gaps.append({"company": company, "kind": kind, "note": note, "since": since, "new": since[:10] >= fresh})

    for company, seq in sorted(history.items()):
        if seq[-1][2][:10] < cutoff:
            continue
        if seq[-1][1].startswith(ROBOTS_NOTE):
            continue  # skipped on purpose because robots.txt does not allow it: a decision, not a failure to report
        believed = next(((n, ts) for _ok, n, ts in reversed(seq) if n.startswith(DROP_BELIEVED)), None)
        if believed and believed[1][:10] >= fresh:
            add(company, "closed", believed[0], believed[1])
        ok, note, _ts = seq[-1]
        if not ok and note.startswith(DROP_NOTE):
            add(company, "held", note, _outage_start(seq))
        elif not any(
            o for o, n, _t in [x for x in seq if not x[1].startswith(ROBOTS_NOTE)][-FAILING_RUNS:]
        ):  # a skip is neither failure nor success
            add(company, "failing", note, _outage_start(seq))
    return gaps


def dead_boards(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Crawled companies whose board has been empty for DEAD_DAYS days: one dict each, company, since and new.

    Dead means the last crawl of the company in crawl_log was ok with no job (a NULL count is no job), and its
    first ok crawl with no job since the last ok crawl with a job (`since`) is DEAD_DAYS or more before the newest
    crawl day. Failed and skipped crawls in between do not restart the count, but one at the start does not begin it
    (the log keeps 90 days, so `since` never reaches back further, and a company observed for less is not judged
    yet). The company must have been crawled in the last GAP_STALE_DAYS days (an excluded or removed company stops
    showing after that). `new` is True when the run crossed DEAD_DAYS on the newest crawl day or up to GAP_NEW_DAYS
    days before it, as for the coverage gaps.

    n_jobs is counted after the Israel filter, so an israel_only company with no Israel roles can be flagged."""
    history: dict[str, list[tuple[int, int, str]]] = {}
    query = "SELECT company, ts, ok, COALESCE(n_jobs, 0) FROM crawl_log WHERE company IS NOT NULL ORDER BY ts, rowid"
    for company, ts, ok, n_jobs in c.execute(query):
        history.setdefault(company, []).append((ok, n_jobs, ts))
    if not history:
        return []
    newest = datetime.date.fromisoformat(max(seq[-1][2][:10] for seq in history.values()))
    stale = (newest - datetime.timedelta(days=GAP_STALE_DAYS)).isoformat()
    dead = (newest - datetime.timedelta(days=DEAD_DAYS)).isoformat()
    fresh = (newest - datetime.timedelta(days=DEAD_DAYS + GAP_NEW_DAYS)).isoformat()
    boards: list[dict[str, Any]] = []
    for company, seq in sorted(history.items()):
        if seq[-1][2][:10] < stale:
            continue
        since = seq[-1][2]
        for ok, n_jobs, ts in reversed(seq):
            if ok and n_jobs:
                break
            if ok:
                since = ts
        if seq[-1][0] and not seq[-1][1] and since[:10] <= dead:
            boards.append({"company": company, "since": since, "new": since[:10] >= fresh})
    return boards


def log_dead_boards(c: sqlite3.Connection) -> None:
    """One more log line after the coverage gaps: `dead boards: N (M new), ...: names` (counts and public company
    names only), a ::warning:: when one is new. An alert only: a failed check is a warning and never fails the step."""
    try:
        boards = dead_boards(c)
    except (sqlite3.Error, ValueError, TypeError) as exc:
        print(f"::warning::dead board check failed, {type(exc).__name__}")
        return
    if boards:
        new = sum(1 for b in boards if b["new"])
        names = ", ".join(b["company"] for b in boards)
        print(
            ("::warning::" if new else "")
            + f"dead boards: {len(boards)} ({new} new), no job for {DEAD_DAYS} days: {names}"
        )


def safe_coverage_gaps(c: sqlite3.Connection) -> tuple[list[dict[str, Any]], str]:
    """(gaps, error): a problem in the gap check comes back as text, so the gaps step can report it and go red."""
    try:
        return coverage_gaps(c), ""
    except (sqlite3.Error, ValueError, TypeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        return [], error


def gap_summary(gaps: list[dict[str, Any]]) -> str:
    """One log line: counts and company names only (the log is public, the names are public companies)."""
    parts = []
    for kind in GAP_KINDS:
        names = [g["company"] for g in gaps if g["kind"] == kind]
        if names:
            parts.append(f"{kind} {len(names)} ({', '.join(names)})")
    return f"coverage gaps: {len(gaps)} ({sum(1 for g in gaps if g['new'])} new): " + "; ".join(parts)


def log_coverage_gaps(c: sqlite3.Connection) -> bool:
    """Put the coverage gaps in the job log, so a crawl problem is never silent (nothing is emailed for them).

    One line of counts and company names (the log is public, the names are public companies). A gap that is new (see
    coverage_gaps) makes the line a ::warning:: annotation on the run. Then log_dead_boards adds its own line (skipped
    when the gap check itself failed). Returns False when the gap check failed, so the step can go red."""
    gaps, error = safe_coverage_gaps(c)
    if error:
        print(f"::warning::coverage check failed, {error}")
        return False
    if gaps:
        print(("::warning::" if any(g["new"] for g in gaps) else "") + gap_summary(gaps))
    log_dead_boards(c)
    return True


def gaps_step() -> int:
    """`python run.py --gaps`: its own workflow step after the crawl, red when the coverage check is broken."""
    c = open_history()
    try:
        return 0 if log_coverage_gaps(c) else 1
    finally:
        c.close()


RECHECK_REASONS = (
    "no_openings",
    "crawl_failing",
)  # the gap reasons checked again every day (TODO.md, Decisions on sources)


def recheck_targets(companies: list[dict[str, Any]], excluded: list[str]) -> list[dict[str, Any]]:
    """The researched records the daily recheck probes: not crawled, not excluded, a gap reason in RECHECK_REASONS and
    an address to read. `bot_protection` records are never here: a protected site is put aside, not retried."""
    return [
        co
        for co in companies
        if co.get("crawl") is not True
        and co.get("excluded") is None
        and (co.get("gap") or {}).get("reason") in RECHECK_REASONS
        and co.get("careers_url")
        and not is_excluded(co["name"], excluded)
    ]


def recheck_jobs(targets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]] | None]:
    """name -> the jobs the existing crawl path lists, None when the site could not be read (robots.txt included).
    A record with a known board goes through its adapter, any other through the browser crawl of its address; both
    take the same robots.txt rules and one request a second per host. Nothing is stored."""
    found: dict[str, list[dict[str, Any]] | None] = {}
    pages: dict[str, list[str]] = {}
    for co in targets:
        if co.get("ats") in ADAPTERS and co.get("slug"):
            found[co["name"]] = fetch(co)
        else:
            pages.setdefault(co["slug"] if co.get("ats") == "browser" else co["careers_url"], []).append(co["name"])
    for url, jobs in crawl_many(list(pages)) if pages else ():
        for name in pages[url]:
            found[name] = jobs
    return found


def recheck_summary(found: dict[str, list[dict[str, Any]] | None]) -> str:
    """One log line: counts, and the names of the public companies that show an Israel job (never a posting). A job with
    no readable location is only counted: a person decides whether the company is Israeli."""
    israel = {n: sum(1 for j in jobs if ISRAEL.search(j["location"] or "")) for n, jobs in found.items() if jobs}
    shown = {n: k for n, k in israel.items() if k}
    unplaced = [n for n, jobs in found.items() if jobs and not israel[n] and any(not j["location"] for j in jobs)]
    unreachable = sum(1 for jobs in found.values() if jobs is None)
    names = ", ".join(f"{n} ({k})" for n, k in sorted(shown.items()))
    return (
        ("::notice::" if shown else "")
        + f"recheck: {len(found)} probed, {len(shown)} with Israel jobs"
        + (f": {names}" if names else "")
        + f"; {len(unplaced)} with jobs of no readable location, {unreachable} unreachable"
    )


def recheck_step() -> int:
    """`python run.py --recheck`: its own workflow step after the crawl. It reads the pulled company list only, writes
    nothing (no history, no table) and holds no secret; a company that shows Israel jobs is pinned by hand with
    companyctl. A crash is a warning, never a red run: the recheck is a convenience."""
    try:
        found = recheck_jobs(recheck_targets(load_companies(), load_excluded()))
    except Exception as exc:
        print(f"::warning::recheck failed, {type(exc).__name__}")
        return 0
    print(recheck_summary(found))
    return 0


def save_descriptions(texts: list[dict[str, Any]], crawled: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Hands the descriptions to the sync step and logs their share (FR-4 AC 3: counts only, no text). The texts go to
    a work file outside the checkout (descriptions.work_path), never into data/jobs.db or any committed file. A file
    that cannot be written is a warning: the jobs are stored as always, only their descriptions are missing."""
    print(descriptions.share_report(crawled))
    if DETAIL_STATS:  # the job pages asked for: counts only, per ATS
        per_ats: dict[str, list[int]] = {}
        for ats, *counts in DETAIL_STATS.values():
            total = per_ats.setdefault(ats, [0, 0, 0])
            for i, n in enumerate(counts):
                total[i] += n
        parts = [
            f"{a} {n[0]} requests, {n[1]} with a text, {n[2]} left for later runs" for a, n in sorted(per_ats.items())
        ]
        print(f"job pages: {'; '.join(parts)}")
    try:
        descriptions.write_work_file(texts)
    except OSError as exc:
        print(f"::warning::the descriptions could not be handed to the sync step, {type(exc).__name__}")


def main() -> None:  # noqa: PLR0915
    excluded = load_excluded()  # a bad list stops the run: nobody can say who must not be crawled
    everyone = load_companies()
    every = crawled_companies(everyone)
    companies = [c for c in every if c["status"] == "ok" and not is_excluded(c["name"], excluded)]
    c = open_history()
    c.executescript(SCHEMA)  # a pulled file has the tables; `touch data/jobs.db` for a local trial does not
    check_spelling(c, [co["name"] for co in every], excluded)
    if purged := purge_excluded(c, excluded):  # counts only: an excluded company's name is private, the log is public
        print(f"exclusion list: deleted {sum(purged.values())} jobs of {len(purged)} excluded companies")
    if closed := close_uncrawled(c, [x["name"] for x in everyone if x not in every]):
        print(f"closed {closed} open jobs of companies that are no longer crawled")
    print(f"robots.txt: {'enforced' if common.ROBOTS_ENFORCED else 'NOT enforced (ROBOTS_ENFORCED is False)'}")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    api = [co for co in companies if co["ats"] != "browser"]
    br = [co for co in companies if co["ats"] == "browser"]
    failed: list[str] = []
    skipped: list[str] = []
    attempted: set[str] = set()  # companies with any result that is not a robots skip: crawled, held or failed
    crawled: list[tuple[str, list[dict[str, Any]]]] = []  # (ats, jobs) of every crawl that gave a list
    HELD_TEXTS.clear()
    HELD_TEXTS.update(descriptions.read_held())
    print(f"stored texts known to the crawl: {len(HELD_TEXTS)}")
    DETAIL_STATS.clear()
    texts: list[dict[str, Any]] = []  # the descriptions for the sync step: never stored in the database

    def record(batch: list[tuple[dict[str, Any], list[dict[str, Any]] | None]]) -> None:  # noqa: PLR0912
        """Write the results of these companies into the history and commit them, company by company. A crash while
        recording loses only the company being recorded, and a crash in the browser crawl keeps the API companies
        and the browser companies recorded before it (on a step timeout, the ones recorded so far): the workflow syncs
        after a failed crawl, so what was recorded reaches Supabase, and the run is red anyway."""
        for co, fetched in batch:
            c.commit()  # the previous company is complete: keep it whatever comes next
            name = co["name"]
            jobs = fetched
            if jobs is None and robots_skipped(co):  # not a failure: logged on its own, nothing closes
                skipped.append(name)
                c.execute(
                    "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
                    (now, name, co["ats"], False, 0, WHY[co["slug"]]),
                )
                continue
            attempted.add(name)
            # large multinational boards: keep Israel jobs only. A location that is empty or cannot be read does not
            # match, so such a job is dropped here; without the flag it is kept and regions.place calls it Unknown.
            if fetched is not None and co.get("israel_only"):
                jobs = [j for j in fetched if ISRAEL.search(j["location"] or "")]
            drop, held = (
                sharp_drop(c, name, len(jobs), now[:10]) if jobs is not None and co["ats"] == "browser" else ("", False)
            )
            if drop:  # keep the crawler's own reason for an empty page next to the hold
                reason = WHY.get(co["slug"], "")
                if reason.startswith((DROP_NOTE, DROP_BELIEVED)):
                    reason = ""  # our own note from an earlier run in this process, not the crawler's
                WHY[co["slug"]] = f"{drop} ({reason})" if reason else drop
            note = WHY[co["slug"]] if drop else ""  # the hold plus the crawler's own reason
            if jobs is None:
                note = WHY.get(co["slug"], "request failed")
            elif not jobs and co["ats"] == "browser":
                note = WHY.get(co["slug"], "")
            c.execute(
                "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
                (now, name, co["ats"], jobs is not None and not held, len(jobs or []), note),
            )
            if jobs is None:
                failed.append(name)  # never close jobs when the crawl failed
                continue
            if held:
                failed.append(name)  # a crawl that probably lost most of the list: record what it saw, close nothing
            crawled.append((co["ats"], jobs))
            texts.extend(
                {
                    "company": name,
                    "job_id": j["job_id"],
                    "description": j["description"],
                    "details": j.get("details") or {},
                }
                for j in jobs
                if j.get("description")
            )
            seen: set[str] = set()
            known = is_known(c, co, now)
            for j in jobs:
                seen.add(j["job_id"])
                cur = c.execute("SELECT 1 FROM jobs WHERE company=? AND job_id=?", (name, j["job_id"])).fetchone()
                if cur:
                    c.execute(
                        "UPDATE jobs SET last_seen=?, status='open', title=?, location=?, url=? "
                        "WHERE company=? AND job_id=?",
                        (now, j["title"], j["location"], j["url"], name, j["job_id"]),
                    )
                else:
                    # a company seen for the first time gets a silent baseline (reported=1). Nothing reads the column
                    # any more (send_digests.py goes by first_seen and the emailed table)
                    c.execute(
                        "INSERT INTO jobs(company,job_id,title,team,location,url,posted,first_seen,last_seen,reported) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            name,
                            j["job_id"],
                            j["title"],
                            j["team"],
                            j["location"],
                            j["url"],
                            j["posted"],
                            now,
                            now,
                            0 if known else 1,
                        ),
                    )
            if not held:
                close_unseen(c, name, seen)
        c.commit()

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(crawl, api))
    record(results)  # the API companies are in the history before the slow browser crawl starts

    def crawl_browser_companies() -> None:
        by_slug = {slug: [co for co in br if co["slug"] == slug] for slug in dict.fromkeys(co["slug"] for co in br)}

        def finish(fut: Future[tuple[dict[str, Any], list[dict[str, Any]] | None]]) -> None:
            later = fut.result()
            record([later])
            results.append(later)

        def drain(waiting: deque[Future[tuple[dict[str, Any], list[dict[str, Any]] | None]]]) -> None:
            failure: Exception | None = None
            while waiting:  # one company that cannot be recorded must not cost the others
                try:
                    finish(waiting.popleft())
                except Exception as exc:
                    c.rollback()
                    failure = failure or exc
            if failure:
                raise failure

        if by_slug:
            seen_slugs: set[str] = set()
            # One queue carries both the crawl's lists and the finished job-page reads, so a company is recorded as
            # soon as its pages are read, even while the next list is slow or stuck. Companies are recorded in the
            # order their lists arrived. A crash keeps every company whose list arrived.
            events: queue.Queue[tuple[str, Any]] = queue.Queue()

            def feed() -> None:
                try:
                    for slug, jobs in crawl_many(list(by_slug)):
                        events.put(("list", (slug, jobs)))
                except BaseException as exc:  # handed to the loop below
                    events.put(("error", exc))
                else:
                    events.put(("end", None))

            threading.Thread(target=feed, daemon=True).start()
            with ThreadPoolExecutor(8) as ex:
                waiting: deque[Future[tuple[dict[str, Any], list[dict[str, Any]] | None]]] = deque()
                try:
                    while True:
                        kind, value = events.get()
                        if kind == "error":
                            raise value
                        if kind == "end":
                            break
                        for slug, jobs in [value] if kind == "list" else []:  # "done" only wakes the loop
                            seen_slugs.add(slug)
                            # companies sharing a page each get their own copy of the jobs, made before any is submitted
                            sharing = [
                                (co, [dict(j) for j in jobs] if jobs and i else jobs)
                                for i, co in enumerate(by_slug[slug])
                            ]
                            for co, mine in sharing:
                                fut = ex.submit(read_pages_of, co, mine)
                                fut.add_done_callback(lambda f: events.put(("done", f)))
                                waiting.append(fut)
                        while waiting and waiting[0].done():
                            finish(waiting.popleft())
                except BaseException:
                    c.rollback()  # discard a half-recorded company: the drain below must not commit it
                    raise
                finally:
                    drain(waiting)
            for slug in by_slug.keys() - seen_slugs:  # a page the crawl never reported is a failed crawl
                for co in by_slug[slug]:
                    record([(co, None)])
                    results.append((co, None))

    crawl_browser_companies()
    dropped = delete_robots_skips(c, set(skipped) - attempted)
    c.commit()
    n_open = c.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0]
    print(f"crawled {len(companies)} companies, failed {len(failed)} {failed}, open jobs {n_open}")
    print(f"skipped by robots.txt {len(skipped)} {skipped}")
    save_descriptions(texts, crawled)
    print(f"robots.txt: {dropped} jobs of skipped companies deleted from the history")
    if not common.ROBOTS_ENFORCED:
        print(
            f"robots.txt would have refused {len(common.ROBOTS_WOULD_BLOCK)} hosts {sorted(common.ROBOTS_WOULD_BLOCK)}"
        )
    for co, jobs in results:
        failing = co["name"] in skipped or jobs is None or co["name"] in failed
        if co["ats"] == "browser" or failing:  # every failure says why, not only the browser ones
            state = "SKIPPED" if co["name"] in skipped else "FAILED" if failing else len(jobs or [])
            why = WHY.get(co["slug"]) or ("request failed" if failing else "")
            print(f"  {co['ats']} {co['name']:15s} {state}" + (f"  <- {short_reason(why)}" if why else ""))


if __name__ == "__main__":
    if "--gaps" in sys.argv:
        sys.exit(gaps_step())
    if "--recheck" in sys.argv:
        sys.exit(recheck_step())
    main()
