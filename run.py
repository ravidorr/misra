"""Crawl all companies with a working ATS, diff against SQLite, mark new/closed."""

import contextlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from adapters import ADAPTERS
from browser_crawl import WHY, crawl_many
from common import DROP_BELIEVED, DROP_HOLD_DAYS, DROP_NOTE
from discover import ISRAEL

DB = "data/jobs.db"
DROP_MIN_OPEN = 10  # fewer open jobs swing too much to judge a drop; this many returning nothing is a broken page


def init() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(
      company TEXT, job_id TEXT, title TEXT, team TEXT, location TEXT, url TEXT, posted TEXT,
      first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'open', reported INTEGER DEFAULT 0,
      PRIMARY KEY(company, job_id));
    CREATE TABLE IF NOT EXISTS crawl_log(ts TEXT, company TEXT, ats TEXT, ok INTEGER, n_jobs INTEGER);
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


def fetch(co: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One adapter call. An adapter that raises fails its own company, not the whole run."""
    try:
        return ADAPTERS[co["ats"]](co["slug"])
    except Exception as exc:
        WHY[co["slug"]] = f"adapter error: {type(exc).__name__}: {exc}"[:200]
        return None


def crawl(co: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    jobs = fetch(co)
    if jobs is None:  # one retry: most failures are transient rate limits
        time.sleep(4)
        jobs = fetch(co)
    return co, jobs


def main() -> None:  # noqa: PLR0912
    with open("data/companies.json") as fh:
        companies = [c for c in json.load(fh) if c["status"] == "ok"]
    c = init()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    api = [co for co in companies if co["ats"] != "browser"]
    br = [co for co in companies if co["ats"] == "browser"]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(crawl, api))
    if br:
        got = crawl_many([co["slug"] for co in br])
        results.extend((co, got.get(co["slug"])) for co in br)
    failed: list[str] = []
    for co, fetched in results:
        name = co["name"]
        jobs = fetched
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
    c.commit()
    n_open = c.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0]
    print(f"crawled {len(companies)} companies, failed {len(failed)} {failed}, open jobs {n_open}")
    for co, jobs in results:
        if co["ats"] == "browser":
            print(
                f"  browser {co['name']:15s} {'FAILED' if jobs is None or co['name'] in failed else len(jobs)}"
                + (f"  <- {WHY[co['slug']]}" if co["slug"] in WHY else "")
            )


if __name__ == "__main__":
    main()
