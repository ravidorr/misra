"""Crawl all companies with a working ATS, diff against SQLite, mark new/closed. `--gaps` logs the coverage gaps."""

import contextlib
import datetime
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import common
from adapters import ADAPTERS
from browser_crawl import WHY, crawl_many
from common import (
    DB,
    DROP_BELIEVED,
    DROP_HOLD_DAYS,
    DROP_NOTE,
    ISRAEL,
    MAX_PURGE_COMPANIES,
    ROBOTS_CLOSE_DAYS,
    ROBOTS_NOTE,
    ExclusionError,
    is_excluded,
    load_excluded,
)

DROP_MIN_OPEN = 10  # fewer open jobs swing too much to judge a drop; this many returning nothing is a broken page


def init() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(
      company TEXT, job_id TEXT, title TEXT, team TEXT, location TEXT, url TEXT, posted TEXT,
      first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'open', reported INTEGER DEFAULT 0,
      PRIMARY KEY(company, job_id));
    CREATE TABLE IF NOT EXISTS crawl_log(ts TEXT, company TEXT, ats TEXT, ok INTEGER, n_jobs INTEGER);
    CREATE TABLE IF NOT EXISTS robots_skips(company TEXT PRIMARY KEY, since TEXT);
    """)
    with contextlib.suppress(sqlite3.OperationalError):  # column may already be there
        c.execute("ALTER TABLE crawl_log ADD COLUMN note TEXT")
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


def track_robots_skips(c: sqlite3.Connection, skipped: set[str], attempted: set[str], today: str) -> tuple[int, int]:
    """Close the open jobs of a company that robots.txt has kept us away from for ROBOTS_CLOSE_DAYS days in a row.
    Returns (companies that had open jobs closed, jobs closed).

    `skipped` is the companies skipped by robots.txt in this run and `attempted` the ones with any other result in this
    run (crawled, held or failed). Two boards can share a company name: such a name is never closed by this rule and
    never counts as skipped. The table robots_skips holds the first day of the current streak per company.
    A company that is not skipped in this run (it crawled, it failed another way, it left the list or the exclusion
    list took it) loses its row, so the count starts again: one run that is not a skip means the block is not
    steady.
    A closing uses the same close_unseen as every other close, so the Supabase sync marks the jobs closed and a later
    crawl reopens them."""
    skipped = skipped - attempted
    since = dict(c.execute("SELECT company, since FROM robots_skips").fetchall())
    for name in since.keys() - skipped:
        c.execute("DELETE FROM robots_skips WHERE company=?", (name,))
    for name in skipped - since.keys():
        c.execute("INSERT INTO robots_skips(company, since) VALUES(?,?)", (name, today))
    due = [
        name
        for name in sorted(skipped)
        if c.execute(
            "SELECT julianday(?) - julianday(since) >= ? FROM robots_skips WHERE company=?",
            (today, ROBOTS_CLOSE_DAYS, name),
        ).fetchone()[0]
    ]
    companies = jobs = 0
    for name in due:
        n = c.execute("SELECT COUNT(*) FROM jobs WHERE company=? AND status='open'", (name,)).fetchone()[0]
        companies += bool(n)  # a company closed on an earlier run has nothing left to close
        jobs += n
        close_unseen(c, name, set())
    return companies, jobs


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


def crawl(co: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    jobs = fetch(co)
    if jobs is None and not robots_skipped(co):  # one retry: most failures are transient rate limits
        time.sleep(4)
        jobs = fetch(co)
    return co, jobs


def purge_excluded(c: sqlite3.Connection, excluded: list[str]) -> dict[str, int]:
    """Delete everything stored about the companies on the exclusion list: their jobs, crawl log and gap alerts. The
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
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in excluded:
        for table in ("jobs", "crawl_log", "gap_alerts", "robots_skips"):
            if table in tables:
                c.execute(f"DELETE FROM {table} WHERE company=?", (name,))
    c.commit()
    if held:
        c.execute("VACUUM")  # the deleted titles and links must not stay in the free pages of the committed file
    return held


def check_spelling(c: sqlite3.Connection, known: list[str], excluded: list[str]) -> None:
    """Deleting needs the exact name, skipping does not care about case: a list that says `acme` while the data says
    `Acme` would hide the company but leave its rows. That must stop the run, so the list gets the stored spelling."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    stored = {
        r[0] for t in ("jobs", "crawl_log", "gap_alerts") if t in tables for r in c.execute(f"SELECT company FROM {t}")
    }
    for name in sorted(n for n in {*known, *stored} if n and is_excluded(n, excluded) and n not in excluded):
        raise ExclusionError(
            f"the exclusion list spells a company differently from the data ({name!r}): use that exact spelling"
        )


GAP_KINDS = ("failing", "held", "closed")  # the order gap_summary lists them in
FAILING_RUNS = 2  # a company is failing when its last two crawls failed, as in the dashboard (one-offs do not count)
GAP_STALE_DAYS = 7  # a company not crawled for this long before the newest crawl is no longer one of ours


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
    longer saw; this is an event, listed until the daily run has logged it). `since` identifies the gap: the start of
    the outage, or the believed crawl. A company whose last crawl was skipped because of robots.txt is none of these:
    it is on the dashboard's coverage card, and nobody has to act. `new` is True until the daily run logged the gap
    (see mark_alerted), so one outage is new once however its kind changes. Companies not
    crawled in the last GAP_STALE_DAYS days are skipped. A database without a crawl log, a note column or an alert
    table has nothing to report or nothing alerted yet."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "crawl_log" not in tables:
        return []
    note_col = "COALESCE(note, '')" if "note" in [r[1] for r in c.execute("PRAGMA table_info(crawl_log)")] else "''"
    alerted = set(c.execute("SELECT company, since FROM gap_alerts")) if "gap_alerts" in tables else set()
    history: dict[str, list[tuple[int, str, str]]] = {}
    query = f"SELECT company, ts, ok, {note_col} FROM crawl_log WHERE company IS NOT NULL ORDER BY ts, rowid"
    for company, ts, ok, note in c.execute(query):
        history.setdefault(company, []).append((ok, note, ts))
    if not history:
        return []
    newest = max(seq[-1][2][:10] for seq in history.values())
    cutoff = (datetime.date.fromisoformat(newest) - datetime.timedelta(days=GAP_STALE_DAYS)).isoformat()
    gaps: list[dict[str, Any]] = []

    def add(company: str, kind: str, note: str, since: str) -> None:
        gaps.append(
            {"company": company, "kind": kind, "note": note, "since": since, "new": (company, since) not in alerted}
        )

    for company, seq in sorted(history.items()):
        if seq[-1][2][:10] < cutoff:
            continue
        if seq[-1][1].startswith(ROBOTS_NOTE):
            continue  # skipped on purpose because robots.txt does not allow it: a decision, not a failure to report
        believed = next(((n, ts) for _ok, n, ts in reversed(seq) if n.startswith(DROP_BELIEVED)), None)
        if believed and (company, believed[1]) not in alerted:
            add(company, "closed", believed[0], believed[1])
        ok, note, _ts = seq[-1]
        if not ok and note.startswith(DROP_NOTE):
            add(company, "held", note, _outage_start(seq))
        elif not any(
            o for o, n, _t in [x for x in seq if not x[1].startswith(ROBOTS_NOTE)][-FAILING_RUNS:]
        ):  # a skip is neither failure nor success
            add(company, "failing", note, _outage_start(seq))
    return gaps


def safe_coverage_gaps(c: sqlite3.Connection) -> tuple[list[dict[str, Any]], str]:
    """(gaps, error): a problem in the gap check must not stop the crawl, so it comes back as text instead."""
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


def mark_alerted(c: sqlite3.Connection, gaps: list[dict[str, Any]]) -> None:
    """Remember that the daily run logged these gaps, so they stop being new. The caller commits."""
    c.execute("CREATE TABLE IF NOT EXISTS gap_alerts(company TEXT, since TEXT, kind TEXT, PRIMARY KEY(company, since))")
    c.executemany(
        "INSERT OR IGNORE INTO gap_alerts(company, since, kind) VALUES(?,?,?)",
        [(g["company"], g["since"], g["kind"]) for g in gaps],
    )


def log_coverage_gaps(c: sqlite3.Connection) -> bool:
    """Put the coverage gaps in the job log, so a crawl problem is never silent (nothing is emailed for them).

    One line of counts and company names (the log is public, the names are public companies). A gap that is new, one
    the log has not carried before, makes the line a ::warning:: annotation on the run. Logged gaps are recorded in
    gap_alerts, so they stop being new. Returns False when the check itself failed, so the step can go red."""
    gaps, error = safe_coverage_gaps(c)
    if error:
        print(f"::warning::coverage check failed, {error}")
        return False
    if gaps:
        print(("::warning::" if any(g["new"] for g in gaps) else "") + gap_summary(gaps))
        mark_alerted(c, gaps)
        c.commit()
    return True


def gaps_step() -> int:
    """`python run.py --gaps`: its own workflow step after the crawl, red when the coverage check is broken."""
    c = sqlite3.connect(DB)
    try:
        return 0 if log_coverage_gaps(c) else 1
    finally:
        c.close()


def main() -> None:  # noqa: PLR0912, PLR0915
    excluded = load_excluded()  # a bad list stops the run: nobody can say who must not be crawled
    with open("data/companies.json") as fh:
        every = json.load(fh)
    companies = [c for c in every if c["status"] == "ok" and not is_excluded(c["name"], excluded)]
    c = init()
    check_spelling(c, [co["name"] for co in every], excluded)
    for name, n in purge_excluded(c, excluded).items():
        print(f"exclusion list: deleted {n} jobs of {name}")
    print(f"robots.txt: {'enforced' if common.ROBOTS_ENFORCED else 'NOT enforced (ROBOTS_ENFORCED is False)'}")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    api = [co for co in companies if co["ats"] != "browser"]
    br = [co for co in companies if co["ats"] == "browser"]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(crawl, api))
    if br:
        got = crawl_many([co["slug"] for co in br])
        results.extend((co, got.get(co["slug"])) for co in br)
    failed: list[str] = []
    skipped: list[str] = []
    attempted: set[str] = set()  # companies with any result that is not a robots skip: crawled, held or failed
    for co, fetched in results:
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
        if fetched is not None and co.get("israel_only"):  # large multinational boards: keep Israel jobs only
            jobs = [j for j in fetched if ISRAEL.search(j["location"] or "")]
        drop, held = (
            sharp_drop(c, name, len(jobs), now[:10]) if jobs is not None and co["ats"] == "browser" else ("", False)
        )
        if drop:  # keep the crawler's own reason for an empty page next to the hold
            reason = WHY.get(co["slug"], "")
            if reason.startswith((DROP_NOTE, DROP_BELIEVED)):
                reason = ""  # our own note from an earlier run in this process, not the crawler's
            WHY[co["slug"]] = f"{drop} ({reason})" if reason else drop
        note = drop
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
                # a company seen for the first time gets a silent baseline (reported=1),
                # so adding companies never floods the digest
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
    closed_companies, closed_jobs = track_robots_skips(c, set(skipped), attempted, now[:10])
    c.commit()
    n_open = c.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0]
    print(f"crawled {len(companies)} companies, failed {len(failed)} {failed}, open jobs {n_open}")
    print(f"skipped by robots.txt {len(skipped)} {skipped}")
    print(f"robots.txt: closed {closed_jobs} jobs of {closed_companies} companies skipped for {ROBOTS_CLOSE_DAYS} days")
    if not common.ROBOTS_ENFORCED:
        print(
            f"robots.txt would have refused {len(common.ROBOTS_WOULD_BLOCK)} hosts {sorted(common.ROBOTS_WOULD_BLOCK)}"
        )
    for co, jobs in results:
        if co["ats"] == "browser":
            state = (
                "SKIPPED" if co["name"] in skipped else "FAILED" if jobs is None or co["name"] in failed else len(jobs)
            )
            print(f"  browser {co['name']:15s} {state}" + (f"  <- {WHY[co['slug']]}" if co["slug"] in WHY else ""))


if __name__ == "__main__":
    if "--gaps" in sys.argv:
        sys.exit(gaps_step())
    main()
