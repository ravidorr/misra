"""Build the digest of new, relevant jobs. `python3 digest.py` previews; `--commit` marks them reported."""

import datetime
import html
import re
import sqlite3
import sys
import time
from typing import Any

from common import DROP_BELIEVED, DROP_NOTE
from discover import ISRAEL

DB = "data/jobs.db"
# title must match one of these (product-focused + entry level + student)
ROLE = re.compile(
    r"product|\bPM\b|\bAPM\b|intern\b|internship|student|junior|\bjr\b|graduate|entry|trainee|סטודנט|מוצר", re.I
)
# drop clearly irrelevant senior or non-product matches
EXCLUDE = re.compile(
    r"sales|counsel|architect|partnerships|\b(senior|sr\.?|staff|principal|director|head|vp|lead)\b"
    r"|product (marketing|designer|design|security|support)|software (engineer|developer)",
    re.I,
)
PM_CORE = re.compile(r"product|\bPM\b|\bAPM\b|מוצר", re.I)
# non-product titles are Intern first, then Junior (shared with the dashboard)
INTERN = re.compile(r"intern\b|internship|student|סטודנט", re.I)
JUNIOR = re.compile(r"junior|\bjr\b|graduate|entry|trainee", re.I)


def select(c: sqlite3.Connection, only_unreported: bool = True) -> list[dict[str, Any]]:
    q = "SELECT company,title,team,location,url,first_seen FROM jobs WHERE status='open'"
    if only_unreported:
        q += " AND reported=0"
    out: list[dict[str, Any]] = []
    for r in c.execute(q + " ORDER BY company, title"):
        company, title, team, loc, url, _fs = r
        if not ISRAEL.search(loc or ""):
            continue
        if not ROLE.search(title) or (
            EXCLUDE.search(title) and not re.search(r"intern|student|junior|graduate", title, re.I)
        ):
            continue
        out.append(
            {
                "company": company,
                "title": title,
                "team": team,
                "location": loc,
                "url": url,
                "pm": bool(PM_CORE.search(title)),
            }
        )
    return out


GAP_LABEL = {
    "closed": "jobs closed after a sharp drop was believed",
    "held": "crawl held, nothing closed",
    "failing": "not crawled",
}
GAP_NOTE_MAX = 160
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
    """Companies whose crawls need a person's attention, one dict each: company, kind, note, last_good, since, new.

    kind is "failing" (the last two crawls failed, as in the dashboard's coverage gaps), "held" (the last crawl was
    held back as a sharp drop: nothing closed) or "closed" (a crawl believed a sharp drop and closed the jobs it no
    longer saw; this is an event, listed until an email has carried it). `since` identifies the gap: the start of the
    outage, or the believed crawl. `new` is True until an email that listed the gap was sent (see mark_alerted), so
    one outage alerts once however its kind changes, and an alert whose email failed is tried again. Companies not
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
    last_good = dict(c.execute("SELECT company, MAX(substr(ts,1,10)) FROM crawl_log WHERE ok=1 GROUP BY company"))
    gaps: list[dict[str, Any]] = []

    def add(company: str, kind: str, note: str, since: str) -> None:
        gap = {"company": company, "kind": kind, "note": note, "since": since}
        gaps.append({**gap, "last_good": last_good.get(company, "never"), "new": (company, since) not in alerted})

    for company, seq in sorted(history.items()):
        if seq[-1][2][:10] < cutoff:
            continue
        believed = next(((n, ts) for _ok, n, ts in reversed(seq) if n.startswith(DROP_BELIEVED)), None)
        if believed and (company, believed[1]) not in alerted:
            add(company, "closed", believed[0], believed[1])
        ok, note, _ts = seq[-1]
        if not ok and note.startswith(DROP_NOTE):
            add(company, "held", note, _outage_start(seq))
        elif not any(o for o, _n, _t in seq[-FAILING_RUNS:]):
            add(company, "failing", note, _outage_start(seq))
    return gaps


def safe_coverage_gaps(c: sqlite3.Connection) -> tuple[list[dict[str, Any]], str]:
    """(gaps, error): a problem in the gap check must not stop the job email, so it comes back as text instead."""
    try:
        return coverage_gaps(c), ""
    except (sqlite3.Error, ValueError, TypeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"::warning::coverage check failed, {error}", file=sys.stderr)
        return [], error


def mark_alerted(c: sqlite3.Connection, gaps: list[dict[str, Any]]) -> None:
    """Remember that an email carried these gaps, so they stop being new. The caller commits."""
    c.execute("CREATE TABLE IF NOT EXISTS gap_alerts(company TEXT, since TEXT, kind TEXT, PRIMARY KEY(company, since))")
    c.executemany(
        "INSERT OR IGNORE INTO gap_alerts(company, since, kind) VALUES(?,?,?)",
        [(g["company"], g["since"], g["kind"]) for g in gaps],
    )


def render(
    jobs: list[dict[str, Any]], gaps: list[dict[str, Any]] | None = None, gap_error: str = ""
) -> tuple[str, str]:
    gaps = gaps or []
    today = time.strftime("%Y-%m-%d")
    pm = [j for j in jobs if j["pm"]]
    interns = [j for j in jobs if not j["pm"] and INTERN.search(j["title"])]
    juniors = [j for j in jobs if not j["pm"] and not INTERN.search(j["title"]) and JUNIOR.search(j["title"])]

    def block(title: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        h = f"<h3>{title} ({len(items)})</h3><ul>"
        for j in items:
            h += (
                f'<li><b>{html.escape(j["company"])}</b>: <a href="{html.escape(j["url"] or "")}">'
                f"{html.escape(j['title'])}</a> <i>({html.escape(j['location'] or '')})</i></li>"
            )
        return h + "</ul>"

    body = f"<h2>New Israel jobs, {today}</h2>"
    body += block("Product roles", pm) + block("Intern roles (non-product titles)", interns)
    body += block("Junior roles (non-product titles)", juniors)
    if not jobs:
        body += "<p>No new matching jobs today.</p>"
    if gaps:
        body += f"<h3>Not crawled properly ({len(gaps)})</h3><ul>"
        for g in gaps:
            tag = " <b>new</b>" if g["new"] else ""
            why = f": {html.escape(g['note'][:GAP_NOTE_MAX])}" if g["note"] else ""
            body += (
                f"<li><b>{html.escape(g['company'])}</b>{tag}: {GAP_LABEL[g['kind']]}{why} "
                f"<i>(last good crawl {html.escape(g['last_good'])})</i></li>"
            )
        body += "</ul>"
    if gap_error:
        body += f"<p><b>Coverage check failed</b>: {html.escape(gap_error)}. See the workflow log.</p>"
    new_gaps = sum(1 for g in gaps if g["new"])
    if jobs or not new_gaps:
        return f"New Israel jobs: {len(jobs)} ({today})", body
    return f"Job crawl alert: {new_gaps} new ({today})", body


def main() -> None:
    c = sqlite3.connect(DB)
    jobs = select(c)
    gaps, gap_error = safe_coverage_gaps(c)
    subject, body = render(jobs, gaps, gap_error)
    with open("data/digest.html", "w") as fh:
        fh.write(body)
    print(subject)
    for j in jobs:
        print(("PM  " if j["pm"] else "    ") + f"{j['company']:18s} {j['title'][:60]:60s} {j['location'][:30]}")
    for g in gaps:
        print(f"GAP {'new' if g['new'] else '   '} {g['company']:18s} {g['kind']:8s} {g['note'][:60]}")
    if "--commit" in sys.argv:
        c.execute("UPDATE jobs SET reported=1 WHERE status='open'")
        mark_alerted(c, gaps)
        c.commit()
        print("marked reported")


if __name__ == "__main__":
    main()
