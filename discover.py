"""Find careers_url + ATS for each seed company. Writes data/companies.json"""

import csv
import glob
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from adapters import ADAPTERS
from common import ISRAEL, get, slug_candidates

HTTP_OK = 200
MIN_DST_ARGS = 2
PROBE_ORDER = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
ATS_LINK = [
    ("comeet", re.compile(r"comeet\.com/jobs/([A-Za-z0-9_.\-]+/[A-Z0-9.]+)", re.I)),
    (
        "greenhouse",
        re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)", re.I),
    ),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_\-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.\-%]+)", re.I)),
    # `apply.workable.com/j/<shortcode>` is a job link, not a board: its `j` is not an account
    ("workable", re.compile(r"apply\.workable\.com/(?!j/)([A-Za-z0-9_\-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I)),
    ("jazzhr", re.compile(r"(?<![A-Za-z0-9_\-.%])(?!(?:www|api|app|help)\.)([A-Za-z0-9_\-]+)\.applytojob\.com", re.I)),
    ("bamboohr", re.compile(r"(?<![A-Za-z0-9_\-.%])(?!(?:www|api|app|help)\.)([A-Za-z0-9_\-]+)\.bamboohr\.com", re.I)),
]
HOST_BASED = {"jazzhr", "bamboohr"}  # the slug is a subdomain, not a path segment
CAREER_PATHS = ["/careers", "/jobs", "/careers/", "/company/careers", "/about/careers", "/join-us", "/careers/jobs"]


def scan_page(url: str) -> tuple[str | None, str | None]:
    r = get(url)
    if r is None or r.status_code != HTTP_OK:
        return None, None
    for ats, rx in ATS_LINK:
        m = rx.search(r.text)
        if m and m.group(1).lower() not in ("embed", "jobs", "careers"):
            # a host is case-insensitive and the adapters compare it with the lowercase one the response gives back
            return ats, m.group(1).lower() if ats in HOST_BASED else m.group(1)
    return None, r.url


def israel_count(jobs: list[dict[str, Any]]) -> int:
    return sum(1 for j in jobs if ISRAEL.search(j["location"] or ""))


def load_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    # agents add data/new/<segment>_overrides.json
    for _p in ["data/overrides.json", *sorted(glob.glob("data/new/*_overrides.json"))]:
        with open(_p) as fh:
            out.update(json.load(fh))
    return out


OVERRIDES = load_overrides()


def discover(row: dict[str, str]) -> dict[str, Any]:  # noqa: PLR0912
    name, domain = row["name"], row["domain"]
    res: dict[str, Any] = {
        "name": name,
        "domain": domain,
        "careers_url": "",
        "ats": "",
        "slug": "",
        "source": "",
        "status": "unknown",
        "n_jobs": None,
        "n_israel": None,
    }
    ov = OVERRIDES.get(name)
    if ov:
        res.update(
            ats=ov["ats"], slug=ov["slug"], source="override", careers_url=ov["slug"] if ov["ats"] == "browser" else ""
        )
        if ov.get("israel_only"):
            res["israel_only"] = True  # the crawl keeps only jobs located in Israel
        if ov["ats"] == "browser":
            res["status"] = "ok"  # verified at crawl time
            print(f"{name:25s} ok(browser)        {ov['slug']}", flush=True)
            return res
        if ov.get("verify") is False:
            res["status"] = "ok"  # trusted: verified at crawl time (this network cannot reach it)
            print(f"{name:25s} ok(unverified)      {ov['ats']} {ov['slug']}", flush=True)
            return res
        jobs = ADAPTERS[ov["ats"]](ov["slug"])
        res.update(
            status="ok" if jobs is not None else "ats_failed",
            n_jobs=None if jobs is None else len(jobs),
            n_israel=None if jobs is None else israel_count(jobs),
        )
        print(
            f"{name:25s} {res['status']:18s} {ov['ats']:15s} {ov['slug']:30s} "
            f"{res['n_jobs']} il={res['n_israel']} src=override",
            flush=True,
        )
        return res
    # 1. look at the real career pages first (most reliable, finds comeet)
    for p in ["/", *CAREER_PATHS]:
        for host in (f"https://www.{domain}", f"https://{domain}"):
            ats, val = scan_page(host + p)
            if ats:
                res.update(ats=ats, slug=val, careers_url=host + p, source="page")
                break
            if val and p != "/" and not res["careers_url"]:
                res["careers_url"] = val
        if res["ats"]:
            break
    # 2. probe ATS APIs by guessed slug
    if not res["ats"]:
        for ats in PROBE_ORDER:
            for s in slug_candidates(name, domain):
                jobs = ADAPTERS[ats](s)
                if jobs and israel_count(jobs) > 0:
                    res.update(ats=ats, slug=s, source="guess")
                    break
            if res["ats"]:
                break
    # 3. verify by actually crawling
    if res["ats"]:
        jobs = ADAPTERS[res["ats"]](res["slug"])
        if jobs is None:
            res["status"] = "ats_failed"
        else:
            res.update(status="ok", n_jobs=len(jobs), n_israel=israel_count(jobs))
    elif res["careers_url"]:
        res["status"] = "careers_page_only"
    else:
        res["status"] = "not_found"
    print(
        f"{name:25s} {res['status']:18s} {res['ats']:15s} {res['slug']:30s} "
        f"{res['n_jobs']} il={res['n_israel']} src={res['source']}",
        flush=True,
    )
    return res


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "data/seed_companies.csv"
    dst = sys.argv[2] if len(sys.argv) > MIN_DST_ARGS else "data/companies.json"
    with open(src) as fh:
        rows = list(csv.DictReader(fh))
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for r in rows:
        if r["domain"] not in seen:
            seen.add(r["domain"])
            uniq.append(r)
    with ThreadPoolExecutor(8) as ex:
        out = list(ex.map(discover, uniq))
    with open(dst, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(Counter(r["status"] for r in out))


if __name__ == "__main__":
    main()
