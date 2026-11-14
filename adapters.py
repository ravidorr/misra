"""One adapter per ATS. fetch(slug) -> list of job dicts, or None on failure
(None means 'could not crawl'; [] means 'crawled, no jobs')."""

import json
import re
from collections.abc import Callable
from typing import Any

from common import get, post

HTTP_OK = 200
Job = dict[str, Any]
WORKDAY_ISRAEL = (
    "084562884af243748dad7c84c304d89a"  # Workday's id for Israel in a country facet, the same on every tenant
)
WORKDAY_PAGE = 20  # the most jobs Workday returns in one request
WORKDAY_MAX_PAGES = 100
WORKDAY_SLUG_PARTS = 3


def _job(  # noqa: PLR0913, PLR0917
    job_id: Any, title: Any, location: Any, url: Any, team: Any = "", posted: Any = ""
) -> Job:
    return {
        "job_id": str(job_id),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "team": (team or "").strip(),
        "posted": posted or "",
    }


def greenhouse(slug: str) -> list[Job] | None:
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if r is None or r.status_code != HTTP_OK:
        return None
    return [
        _job(
            j["id"],
            j["title"],
            (j.get("location") or {}).get("name"),
            j["absolute_url"],
            posted=j.get("updated_at", ""),
        )
        for j in r.json().get("jobs", [])
    ]


def lever(slug: str) -> list[Job] | None:
    host = "api.lever.co"
    if slug.startswith("eu:"):
        host, slug = "api.eu.lever.co", slug[3:]
    r = get(f"https://{host}/v0/postings/{slug}?mode=json")
    if r is None or r.status_code != HTTP_OK:
        return None
    out: list[Job] = []
    for j in r.json():
        c = j.get("categories") or {}
        out.append(
            _job(
                j["id"],
                j["text"],
                c.get("location") or ", ".join(c.get("allLocations") or []),
                j["hostedUrl"],
                team=c.get("team"),
                posted=str(j.get("createdAt", "")),
            )
        )
    return out


def ashby(slug: str) -> list[Job] | None:
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r is None or r.status_code != HTTP_OK:
        return None
    return [
        _job(
            j["id"], j["title"], j.get("location"), j.get("jobUrl"), team=j.get("team"), posted=j.get("publishedAt", "")
        )
        for j in r.json().get("jobs", [])
        if j.get("isListed", True)
    ]


def workable(slug: str) -> list[Job] | None:
    r = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if r is None or r.status_code != HTTP_OK:
        return None
    out: list[Job] = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        out.append(
            _job(
                j["shortcode"],
                j["title"],
                loc,
                j.get("url") or j.get("application_url"),
                team=j.get("department"),
                posted=j.get("published_on", ""),
            )
        )
    return out


def smartrecruiters(slug: str) -> list[Job] | None:
    out: list[Job] = []
    offset = 0
    while True:
        r = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        if r is None or r.status_code != HTTP_OK:
            return None if offset == 0 else out
        d = r.json()
        for j in d.get("content", []):
            loc = j.get("location") or {}
            out.append(
                _job(
                    j["id"],
                    j["name"],
                    ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                    f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                    team=(j.get("department") or {}).get("label"),
                    posted=j.get("releasedDate", ""),
                )
            )
        offset += 100
        if offset >= d.get("totalFound", 0):
            return out


def comeet(slug: str) -> list[Job] | None:
    """slug is '<name>/<uid>' taken from comeet.com/jobs/<name>/<uid>. Job data is embedded in the page."""
    r = get(f"https://www.comeet.com/jobs/{slug}")
    if r is None or r.status_code != HTTP_OK or "COMPANY_POSITIONS_DATA" not in r.text:
        return None
    m = re.search(r"COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\]);", r.text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    out: list[Job] = []
    for j in data:
        loc = j.get("location") or {}
        out.append(
            _job(
                j.get("uid"),
                j.get("name"),
                ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                j.get("url_active_page") or j.get("url_comeet_hosted_page"),
                team=j.get("department"),
                posted=j.get("time_updated", ""),
            )
        )
    return out


def greenhouse_url(url: str) -> list[Job] | None:
    """Company site proxies a Greenhouse board as JSON ({"jobs": [...]}, Greenhouse field names)."""
    r = get(url)
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        jobs = r.json().get("jobs", [])
    except ValueError:
        return None
    return [
        _job(
            j["id"],
            j["title"],
            (j.get("location") or {}).get("name"),
            j["absolute_url"],
            posted=j.get("updated_at", ""),
        )
        for j in jobs
    ]


def _workday_page(base: str, applied: dict[str, list[str]], text: str, offset: int) -> dict[str, Any] | None:
    """One page of a Workday job search, or None when the answer is not the JSON of a search (a maintenance
    redirect, an error page, a tenant or site that does not exist)."""
    body = {"appliedFacets": applied, "limit": WORKDAY_PAGE, "offset": offset, "searchText": text}
    r = post(base, json=body, headers={"Accept": "application/json"})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("jobPostings"), list) else None


def _workday_filter(first: dict[str, Any]) -> tuple[dict[str, list[str]], str] | None:
    """(facets, search text) that select the jobs in Israel, from the facets of an unfiltered search; None when the
    tenant lists its countries and Israel is not one of them."""
    facets = [f for f in first.get("facets") or [] if isinstance(f, dict)]
    for f in facets:
        if any(v.get("id") == WORKDAY_ISRAEL for v in f.get("values") or [] if isinstance(v, dict)):
            return {f["facetParameter"]: [WORKDAY_ISRAEL]}, ""
    if any("country" in str(f.get("facetParameter", "")).lower() for f in facets):
        return None
    return {}, "Israel"  # no country facet: search for the word instead


def _workday_job(host: str, site: str, posting: dict[str, Any], tag_israel: bool) -> Job | None:
    path = posting.get("externalPath")
    if not path:
        return None
    loc = " ".join((posting.get("locationsText") or "").split())
    if tag_israel and "israel" not in loc.lower():
        loc = f"{loc}, Israel" if loc else "Israel"
    return _job(path, posting.get("title"), loc, f"{host}/en-US/{site}{path}")


def workday(slug: str) -> list[Job] | None:
    """slug is '<tenant>/<wdN>/<site>', from <tenant>.<wdN>.myworkdayjobs.com/<site>. Only jobs in Israel are read.

    Workday lists a country facet whose name differs per tenant, so it is found in the first answer (see
    _workday_filter). A posting in several places says only "2 Locations", so a posting found through the Israel
    facet is tagged Israel when its text does not already say so."""
    parts = slug.split("/")
    if len(parts) != WORKDAY_SLUG_PARTS:
        return None
    tenant, dc, site = parts
    host = f"https://{tenant}.{dc}.myworkdayjobs.com"
    base = f"{host}/wday/cxs/{tenant}/{site}/jobs"
    first = _workday_page(base, {}, "", 0)
    if first is None:
        return None
    selected = _workday_filter(first)
    if selected is None:
        return []
    applied, text = selected
    out: list[Job] = []
    total = 0
    for page in range(WORKDAY_MAX_PAGES):
        d = _workday_page(base, applied, text, page * WORKDAY_PAGE)
        if d is None:
            return None if page == 0 else out
        if page == 0:
            total = d.get("total") or 0  # only the first page carries the total
        jobs = (_workday_job(host, site, p, bool(applied)) for p in d["jobPostings"])
        out.extend(j for j in jobs if j)
        if (page + 1) * WORKDAY_PAGE >= total or not d["jobPostings"]:
            break
    return out


ADAPTERS: dict[str, Callable[[str], list[Job] | None]] = {
    "greenhouse_url": greenhouse_url,
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
    "comeet": comeet,
    "workday": workday,
}
