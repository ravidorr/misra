"""Build the dashboard in docs/: a static page plus cacheable stylesheet, scripts and job data files."""

import base64
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import classify
import regions
from common import ROBOTS_NOTE, is_excluded, load_excluded
from digest import DB

# Stylesheets bundled into docs/dashboard.css, in order. Raw colors live only in tokens.css.
CSS_SOURCES = ("design-system/tokens.css", "design-system/components/*.css", "templates/dashboard.css")
# Files copied to docs/ as they are.
STATIC_FILES = (
    "templates/dashboard.html",
    "templates/dashboard.js",
    "templates/auth.js",
    "templates/report.js",
    "templates/theme.js",
    "templates/email.html",
    "templates/email.js",
)
# The built pages that carry __SRI_*__ placeholders.
PAGES = ("index.html", "email.html")
OUTPUT_NAMES = {"templates/dashboard.html": "index.html"}
# Third-party scripts copied byte for byte to docs/ (vendor/README.md says where each one comes from). The page loads
# them from its own origin, so the Content-Security-Policy needs no CDN.
VENDORED_FILES = ("vendor/supabase.js",)
# Placeholders in the page for the integrity hash of the file with that name in docs/.
INTEGRITY = re.compile(r"__SRI_([A-Z]+)_([A-Z]+)__")

# The backend the page reads the jobs from: the one place for the project URL and the PUBLIC anon key. Row-level
# security lets the anon key read open jobs only, so the key may live in docs/. The build puts both values into the
# page scripts (dashboard.js, auth.js and report.js) and the URL into the page's connect-src; later steps (stars) read
# them too.
# Never put the service-role key here.
SUPABASE_URL = "https://dncodqeqpoqwcxdctzco.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_9gvh4YdsfErlFsgJfmNexQ_yHF-XwnA"


# The researched companies with no usable job feed; each row says why in `reason` (a key of REASONS) and `note`.
UNRESOLVED = "data/unresolved_candidates.csv"
BLOCKED_WHY = "its site blocks automated access from GitHub's servers"
# Why a site is on the coverage gap card, in the order the card lists them. The keys are the only values the `reason`
# column of the CSV may hold; the labels are what the visitor reads.
REASONS = {
    "crawl_failing": "Failing in the latest crawl",
    "bot_protection": "Blocks automated access",
    "no_careers_url": "No careers page found",
    "site_unusable": "Site down, parked or unreachable",
    "no_openings": "No open roles in Israel, or the board is closed",
    "needs_reader": "Roles are listed, but we cannot read them yet",
    "robots_disallowed": "robots.txt does not allow it",
    "no_feed_found": "No job feed found yet",
}


def reason(note: str | None) -> str:
    n = note or ""
    if re.search(r"HTTP 40[13]|blocked page|Access Denied|Just a moment", n, re.I):
        return BLOCKED_WHY
    return n[:120] or "the last crawl failed"


def read_unresolved(excluded: list[str] | None = None) -> list[dict[str, str]]:
    """The rows of the unresolved-candidates CSV, without the companies on the exclusion list; a reason that is not in
    REASONS stops the build."""
    with open(UNRESOLVED, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"name", "reason", "note"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{UNRESOLVED}: no {', '.join(sorted(missing))} column")
        rows = [row for row in reader if not is_excluded(row["name"], excluded or [])]
    for row in rows:
        row["note"] = row["note"] or ""
        if row["reason"] not in REASONS:
            raise ValueError(f"{UNRESOLVED}: {row['name']} has the unknown reason {row['reason']!r}")
    return rows


def gap_groups(
    blocked: list[dict[str, str]], untracked: list[dict[str, str]], unresolved: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Every site that is not crawled, grouped by why, in REASONS order. A site named twice is counted once."""
    sites: dict[str, tuple[str, str]] = {}
    for gap in blocked:
        if gap["why"].startswith(ROBOTS_NOTE):  # a decision to respect the site, not a failure
            sites.setdefault(
                gap["c"], ("robots_disallowed", f"{gap['why']}; its roles may be old (last good crawl {gap['last']})")
            )
            continue
        code = "bot_protection" if gap["why"] == BLOCKED_WHY else "crawl_failing"
        sites.setdefault(
            gap["c"],
            (code, f"{gap['why'][:1].upper()}{gap['why'][1:]}; its roles may be old (last good crawl {gap['last']})"),
        )
    for row in unresolved:  # the researched reason and note win over the bare status of companies.json
        sites.setdefault(row["name"], (row["reason"], row["note"]))
    for company in untracked:
        code = "no_careers_url" if company["status"] == "not_found" else "no_feed_found"
        sites.setdefault(company["name"], (code, ""))
    groups = []
    for code, label in REASONS.items():
        members = [
            {"c": name, "why": why}
            for name, (c, why) in sorted(sites.items(), key=lambda site: site[0].lower())
            if c == code
        ]
        if members:
            groups.append({"reason": code, "label": label, "count": len(members), "sites": members})
    return groups


def read_css() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8").strip() for pattern in CSS_SOURCES for path in sorted(Path().glob(pattern))
    )


def job_posting(job: dict[str, Any]) -> dict[str, Any]:
    """Schema.org JobPosting for one role, so crawlers and agents can read the page without running scripts."""
    posting: dict[str, Any] = {
        "@type": "JobPosting",
        "title": job["t"],
        "hiringOrganization": {"@type": "Organization", "name": job["c"]},
        "url": job["u"],
        "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": job["l"]}},
    }
    if job["f"]:
        posting["datePosted"] = job["f"]
    if job["il"]:
        posting["jobLocation"]["address"]["addressCountry"] = "IL"
        # one Place per region and one per ungrouped place the job names, so "several cities" is several places
        places = [{"addressRegion": name} for name in job["rg"] if name != regions.REMOTE]
        places += [{"addressLocality": name} for name in job["lo"] if name != regions.CITY_NOT_GIVEN]
        if places:
            posting["jobLocation"] = [
                {"@type": "Place", "address": {"@type": "PostalAddress", **place, "addressCountry": "IL"}}
                for place in places
            ]
        if regions.REMOTE in job["rg"]:
            posting["jobLocationType"] = "TELECOMMUTE"
            posting["applicantLocationRequirements"] = {"@type": "Country", "name": "IL"}
    posting["experienceRequirements"] = job["lv"]
    if job["lv"] == classify.INTERN_LEVEL:
        posting["employmentType"] = "INTERN"
    posting["occupationalCategory"] = job["rt"]
    return posting


def integrity(path: Path) -> str:
    """Subresource Integrity value for a built file, so the browser rejects a stale or altered copy."""
    return "sha384-" + base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    excluded = load_excluded()  # an excluded company is on no list and no card, whatever the data still holds
    c = sqlite3.connect(DB)
    # latest crawl per company, and the last time it worked
    latest: dict[str, tuple[Any, Any, str]] = {}
    has_note = any(r[1] == "note" for r in c.execute("PRAGMA table_info(crawl_log)"))
    note_col = "COALESCE(note, '')" if has_note else "''"
    q = "SELECT company, ts, ok, " + note_col + " FROM crawl_log ORDER BY ts"
    recent: dict[str, list[Any]] = {}
    for company, ts, ok, note in c.execute(q):
        if is_excluded(company, excluded):
            continue
        latest[company] = (ts, ok, note)
        recent.setdefault(company, []).append(ok)
    last_good = dict(c.execute("SELECT company, MAX(substr(ts,1,10)) FROM crawl_log WHERE ok=1 GROUP BY company"))
    # failing = the last two crawls both failed (one-off errors do not count)
    # a company skipped for robots.txt is listed at once: it is a decision, so there is no second crawl to wait for
    failing = {co: v for co, v in latest.items() if not any(recent[co][-2:]) or v[2].startswith(ROBOTS_NOTE)}
    with open("data/companies.json") as fh:
        companies = [x for x in json.load(fh) if not is_excluded(x["name"], excluded)]
    unfed = [x for x in companies if x["status"] != "ok"]
    untracked = sorted(x["name"] for x in unfed)
    blocked = [{"c": co, "why": reason(v[2]), "last": last_good.get(co, "never")} for co, v in sorted(failing.items())]
    groups = gap_groups(blocked, unfed, read_unresolved(excluded))
    gaps = {
        "blocked": blocked,
        "untracked": untracked,
        "total": sum(g["count"] for g in groups),
        "groups": groups,
    }
    first = dict(c.execute("SELECT company, MIN(substr(first_seen,1,10)) FROM jobs GROUP BY company"))
    jobs: list[dict[str, Any]] = []
    for company, job_id, title, team, loc, url, fs in c.execute(
        "SELECT company, job_id, title, team, location, url, first_seen FROM jobs WHERE status='open'"
    ):
        if is_excluded(company, excluded):
            continue
        f = (fs or "")[:10]
        place = regions.place(loc)
        jobs.append(
            {
                "c": company,
                "i": job_id,
                "t": title.strip(),
                "m": (team or "").strip(),
                "l": (loc or "").strip()[:80],
                "u": url,
                "f": f,
                "lv": classify.level(title),
                "rt": classify.role_types(title),
                "rg": list(place.regions),
                "lo": list(place.locations),
                "il": place.israel,
                "n": bool(f and f > first.get(company, f)),
                "s": company in failing,
            }
        )
    generated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    payload = dump_json({"jobs": jobs, "gaps": gaps, "generated": generated})
    listing = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": n, "item": job_posting(j)}
            for n, j in enumerate((j for j in jobs if j["il"]), start=1)
        ],
    }
    out = Path("docs")
    (out / "jobs.json").write_text(payload, encoding="utf-8")
    (out / "jobs.jsonld").write_text(dump_json(listing), encoding="utf-8")
    (out / "dashboard.css").write_text(read_css(), encoding="utf-8")
    # What the page needs besides the jobs it reads from Supabase: the coverage gaps, the build time and each company's
    # first day of listings (the closed jobs that fix it are not readable with the anon key). It sits in a
    # JSON data block of index.html (not executed, so the page stays free of inline script); `<` is escaped so the
    # data can never close the block.
    page_data = dump_json(
        {
            "gaps": gaps,
            "generated": generated,
            "firstDay": first,
            # The filters are built from these lists (classify.py and regions.py), in display order.
            "levels": list(classify.LEVELS),
            "roleTypes": list(classify.ROLE_TYPE_NAMES),
            "regions": list(regions.REGION_NAMES),
        }
    ).replace("<", "\\u003c")
    for source in STATIC_FILES:
        text = Path(source).read_text(encoding="utf-8")
        text = text.replace("__SUPABASE_URL__", SUPABASE_URL).replace("__SUPABASE_ANON_KEY__", SUPABASE_ANON_KEY)
        text = text.replace("__PAGE_DATA__", page_data)
        (out / OUTPUT_NAMES.get(source, Path(source).name)).write_text(text, encoding="utf-8")
    for source in VENDORED_FILES:
        shutil.copyfile(source, out / Path(source).name)
    for name in PAGES:
        page = out / name
        # __SRI_DASHBOARD_CSS__ becomes the integrity hash of docs/dashboard.css; one pass over each page
        page.write_text(
            INTEGRITY.sub(lambda m: integrity(out / f"{m[1]}.{m[2]}".lower()), page.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    print(f"gaps: {[g['c'] for g in gaps['blocked']]} failing, {len(untracked)} untracked, {gaps['total']} in all")
    print(
        f"dashboard: {len(jobs)} open jobs, {sum(j['il'] for j in jobs)} in Israel, jobs.json {len(payload) // 1024} KB"
    )


if __name__ == "__main__":
    main()
