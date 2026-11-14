"""One adapter per ATS. fetch(slug) -> list of job dicts, or None on failure
(None means 'could not crawl'; [] means 'crawled, no jobs')."""

import json
import re
from collections.abc import Callable, Iterator
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


def _workday_facets(facets: Any) -> Iterator[dict[str, Any]]:
    """Every facet in a list, and the facets nested inside a facet group (a value that is itself a facet)."""
    for f in facets if isinstance(facets, list) else []:
        if isinstance(f, dict):
            yield f
            values = f.get("values")
            yield from _workday_facets(
                [v for v in values if isinstance(v, dict) and "facetParameter" in v] if isinstance(values, list) else []
            )


def _is_israel(value: Any) -> bool:
    return isinstance(value, dict) and value.get("id") == WORKDAY_ISRAEL


def _workday_israel_facet(first: dict[str, Any]) -> str | None:
    """Name of the facet that lists Israel (by Workday's id) in the facets of an unfiltered search, else None."""
    for f in _workday_facets(first.get("facets")):
        param, values = f.get("facetParameter"), f.get("values")
        if isinstance(param, str) and isinstance(values, list) and any(_is_israel(v) for v in values):
            return param
    return None


def _workday_job(host: str, site: str, posting: dict[str, Any], via_facet: bool) -> Job | None:
    """A job from a posting, or None when it has no link or, on the word-search route, is not located in Israel."""
    path = posting.get("externalPath")
    if not path:
        return None
    loc = " ".join((posting.get("locationsText") or "").split())
    if "israel" not in loc.lower():
        if not via_facet:
            return None
        loc = f"{loc}, Israel" if loc else "Israel"  # "2 Locations" under the Israel facet: one of them is in Israel
    return _job(path, posting.get("title"), loc, f"{host}/en-US/{site}{path}")


def _workday_has_more(d: dict[str, Any], page: int, total: Any) -> bool:
    """Is there a page after this one? The total is trusted only when it is a positive number; otherwise a full page
    means there may be more."""
    postings = d["jobPostings"]
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        return bool(postings) and (page + 1) * WORKDAY_PAGE < total
    return len(postings) == WORKDAY_PAGE


def workday(slug: str) -> list[Job] | None:
    """slug is '<tenant>/<wdN>/<site>', from <tenant>.<wdN>.myworkdayjobs.com/<site>. Only jobs in Israel are read.

    The facets of an unfiltered first search are searched, nested groups included, for the one that lists Israel (its
    name differs per tenant): a search filtered by it gives the jobs in Israel, and a posting in several places,
    which says only "2 Locations", is tagged Israel. Without it the search is for the word Israel and only postings
    located in Israel are kept, so a tenant whose facets Workday reshaped still gives its Israel jobs, minus the
    multi-location ones. Any failure, including one after the first page, is None: a partial list would close the
    jobs it lacks."""
    parts = slug.split("/")
    if len(parts) != WORKDAY_SLUG_PARTS:
        return None
    tenant, dc, site = parts
    host = f"https://{tenant}.{dc}.myworkdayjobs.com"
    base = f"{host}/wday/cxs/{tenant}/{site}/jobs"
    first = _workday_page(base, {}, "", 0)
    if first is None:
        return None
    param = _workday_israel_facet(first)
    applied = {param: [WORKDAY_ISRAEL]} if param else {}
    text = "" if param else "Israel"
    out: list[Job] = []
    total: Any = None
    for page in range(WORKDAY_MAX_PAGES):
        d = _workday_page(base, applied, text, page * WORKDAY_PAGE)
        if d is None:
            return None
        if page == 0:
            total = d.get("total")  # only the first page carries it
        jobs = (_workday_job(host, site, p, bool(param)) for p in d["jobPostings"])
        out.extend(j for j in jobs if j)
        if not _workday_has_more(d, page, total):
            return out
    return None  # more pages than the limit: the list is not complete


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
