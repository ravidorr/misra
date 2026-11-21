"""Find careers_url + ATS for each crawled company of the pulled data/companies.json and write the result back into it.

The list lives in the Supabase table `companies`; `sync_supabase.py --pull` (or `--pull-companies`) writes the scratch
copy this script reads, and `sync_supabase.py --push-companies` sends the result back (the discover-companies workflow
runs the three). Only the discovered fields (DISCOVERED) of a record that has `crawl` true are rewritten, in place and
in the same order; everything a person wrote (`crawl`, `israel_only`, `verify`, `links`, `gap`, `excluded`, `segment`)
is left as it is, and so are the records of the companies that are not crawled, the excluded ones included (they are
never looked at). A careers address that came out as `http://` is written as `https://` (the table refuses any other).
The file is written through a temporary file, and a run that would turn more than MAX_LOST working companies into
failures (an offline run) writes nothing unless it is given `--force`. With `--new-only` (what the workflow runs: a
runner cannot rebuild the whole list reliably) only the records with no status yet are discovered and every other record
stays exactly as pulled, so a site that blocks the runner can never downgrade a working company. A record whose
`source` is `override` is pinned by hand: its `ats` and `slug` are kept and only checked. A company is added in the
`companies` table (the admin page gets an editor in PLAN_2 step 3.3), never in this file."""

import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from adapters import ADAPTERS
from common import (
    COMPANIES,
    ISRAEL,
    RECORD_KEYS,
    get,
    https_only,
    is_excluded,
    load_companies,
    load_excluded,
    slug_candidates,
)

HTTP_OK = 200
MAX_LOST = 5  # more companies than this going from ok to not ok in one run is refused without --force
FLAGS = ("--force", "--new-only")
DISCOVERED = ("careers_url", "ats", "slug", "source", "status")  # the fields this script owns
PROBE_ORDER = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
ATS_LINK = [
    ("comeet", re.compile(r"comeet\.com/jobs/([A-Za-z0-9_.\-]+/[A-Z0-9.]+)", re.I)),
    (
        "greenhouse",
        re.compile(
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_\-]+)", re.I
        ),
    ),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_\-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.\-%]+)", re.I)),
    # `apply.workable.com/j/<shortcode>` is a job link, not a board: its `j` is not an account
    ("workable", re.compile(r"apply\.workable\.com/(?!j/)([A-Za-z0-9_\-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I)),
    ("jazzhr", re.compile(r"(?<![A-Za-z0-9_\-.%])(?!(?:www|api|app|help)\.)([A-Za-z0-9_\-]+)\.applytojob\.com", re.I)),
    ("bamboohr", re.compile(r"(?<![A-Za-z0-9_\-.%])(?!(?:www|api|app|help)\.)([A-Za-z0-9_\-]+)\.bamboohr\.com", re.I)),
    (
        "breezy",
        re.compile(
            r"(?<![A-Za-z0-9_\-.%])(?!(?:www|api|app|help|static|assets|cdn|login|support|developer|blog)\.)([A-Za-z0-9][A-Za-z0-9\-]*)\.breezy\.hr",
            re.I,
        ),
    ),
]
COMEET_INIT = re.compile(r"COMEET\.init\(\{(.*?)\}\)", re.S)  # the snippet of a Comeet careers widget
COMEET_NAME = re.compile(r'"company-name"\s*:\s*"([^"]+)"')
COMEET_UID = re.compile(r'"company-uid"\s*:\s*"([0-9A-Z]{2}\.[0-9A-Z]{3})"')
COMEET_UID_ANY = re.compile(r"""company-uid["']?\s*[:=]\s*["']?([0-9A-Z]{2}\.[0-9A-Z]{3})\b""")
COMEET_PLUGIN = "/wp-content/plugins/comeet-wp-plugin"  # the WordPress plugin that draws a Comeet list on the page
HOST_BASED = {"jazzhr", "bamboohr", "breezy"}  # the slug is a subdomain, not a path segment
CAREER_PATHS = ["/careers", "/jobs", "/careers/", "/company/careers", "/about/careers", "/join-us", "/careers/jobs"]


def ats_in_text(text: str) -> tuple[str, str] | None:
    """The first job board a page links to, as (ats, slug), or None. Also used by `survey.py --candidates`."""
    for ats, rx in ATS_LINK:
        m = rx.search(text)
        if m and m.group(1).lower() not in ("embed", "jobs", "careers"):
            # a host is case-insensitive and the adapters compare it with the lowercase one the response gives back
            return ats, m.group(1).lower() if ats in HOST_BASED else m.group(1)
    return comeet_widget(text)


def comeet_widget(text: str) -> tuple[str, str] | None:
    """A Comeet careers widget (`COMEET.init({"company-name": ..., "company-uid": ...})`, on the page itself or in the
    DOM a script drew) names the account but never links to comeet.com: the slug is '<name>/<uid>', with the name as
    Comeet writes it in its own address (lower case, a hyphen for each run of other characters)."""
    init = COMEET_INIT.search(text)
    name = init and COMEET_NAME.search(init[1])
    uid = init and COMEET_UID.search(init[1])
    slug = re.sub(r"[^a-z0-9]+", "-", name[1].lower()).strip("-") if name else ""
    if not (slug and uid):  # a name with no Latin letter or digit gives no address
        return None
    return "comeet", slug + "/" + uid[1]


def comeet_uid(text: str) -> str | None:
    """The uid of the Comeet account a page or a widget address (`company-uid=71.008`) shows, with no name with it."""
    m = COMEET_UID_ANY.search(text)
    return m[1] if m else None


def scan_page(url: str) -> tuple[str | None, str | None]:
    r = get(url)
    if r is None or r.status_code != HTTP_OK:
        return None, None
    found = ats_in_text(r.text)
    return found if found else (None, r.url)


def israel_count(jobs: list[dict[str, Any]]) -> int:
    return sum(1 for j in jobs if ISRAEL.search(j["location"] or ""))


def discover(row: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912
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
    ov = row if row.get("source") == "override" else None  # pinned by hand: its ats and slug are the answer
    if ov:
        res.update(
            ats=ov["ats"], slug=ov["slug"], source="override", careers_url=ov["slug"] if ov["ats"] == "browser" else ""
        )
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
    args = [a for a in sys.argv[1:] if a not in FLAGS]
    path = args[0] if args else COMPANIES
    excluded = load_excluded(path)  # a company that asked to be removed is never looked at again
    kept = load_companies(path)  # an excluded record stays in the file as it is: it is never looked at
    todo = [r for r in kept if r.get("crawl") is True and not is_excluded(r["name"], excluded)]
    if "--new-only" in sys.argv:  # a record that already has a status is left exactly as it is
        todo = [r for r in todo if not r.get("status")]
    with ThreadPoolExecutor(8) as ex:
        found = list(ex.map(discover, todo))
    pairs = list(zip(todo, found, strict=True))
    lost = [r["name"] for r, res in pairs if r.get("status") == "ok" and res["status"] != "ok"]
    if len(lost) > MAX_LOST and "--force" not in sys.argv:  # an offline or blocked run, not a change in the world
        sys.exit(f"{len(lost)} companies would stop being ok ({', '.join(lost)}); nothing written. Use --force.")
    for record, res in pairs:
        record.update({k: res[k] for k in DISCOVERED})
        record["careers_url"] = https_only(record["careers_url"])
    records = [{k: r[k] for k in RECORD_KEYS if k in r} for r in kept]  # a key added by update() goes in its place
    tmp = f"{path}.tmp"  # a failed write must not leave half of the file
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    print(Counter(r["status"] for r in found))


if __name__ == "__main__":
    main()
