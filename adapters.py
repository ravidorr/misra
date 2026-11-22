"""One adapter per ATS. fetch(slug) -> list of job dicts, or None on failure
(None means 'could not crawl'; [] means 'crawled, no jobs')."""

import html
import json
import re
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import pairwise
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

import requests

from common import ISRAEL, get, host_of, post, reset_robots_block
from descriptions import DETAIL_CAP, HELD, attach, html_to_text, jsonld_description, plain_text, sections

HTTP_OK = 200
REFRESH_DAYS = 14  # a stored text is asked again once in this many days, so an edited posting is picked up
DETAIL_FAIL_STREAK = 5  # job pages in a row without a text after which a company's job pages stop for this run
Job = dict[str, Any]
WORKDAY_ISRAEL = (
    "084562884af243748dad7c84c304d89a"  # Workday's id for Israel in a country facet, the same on every tenant
)
WORKDAY_PAGE = 20  # the most jobs Workday returns in one request
WORKDAY_MAX_PAGES = 100
WORKDAY_SLUG_PARTS = 3
ORACLEHCM_ISRAEL = "IL"
ORACLEHCM_PAGE = 50
ORACLEHCM_MAX_PAGES = 100
ORACLEHCM_SLUG_PARTS = 2
AMAZON_PAGE = 100
AMAZON_MAX_PAGES = 50
PHENOM_PAGE = 50
PHENOM_MAX_PAGES = 100
PHENOM_SLUG_PARTS = 3
AVATURE_MAX = 20  # the most jobs an Avature feed gives (it has no paging): a feed this long may be cut short
JIBE_MAX = 100  # the most jobs of one Jibe search read (one answer): a board with more is not read
DEEL_MAX = 1000  # the most jobs of the Deel careers page read (it lists about 230): a longer list is a failure
BULLS_MAX = 500  # the most positions of the Bulls Media feed read (it lists about 15): a longer list is a failure
ELBIT_MAX = 2000  # the most jobs of the Elbit Systems file read (it lists about 540): a longer list is a failure
VARONIS_MAX = 200  # the most requisitions of Varonis' list read (it has no paging): a longer list is a failure
EIGHTFOLD_MAX = 100  # the most positions of one Eightfold search read (ten an answer): a board with more is not read


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


def _names(items: Any) -> list[str]:
    """The `name` of each dict in a list a site gives (departments, offices), whatever else the entries hold."""
    return [str(i["name"]) for i in items if isinstance(i, dict) and i.get("name")] if isinstance(items, list) else []


def _greenhouse_job(j: dict[str, Any]) -> Job:
    """A job of a Greenhouse board JSON: the content is the posting as escaped HTML. Of what else the board gives only
    the departments and offices are kept (`metadata` and the compliance fields are the company's own and may hold
    anything)."""
    job = _job(
        j["id"], j["title"], (j.get("location") or {}).get("name"), j["absolute_url"], posted=j.get("updated_at", "")
    )
    details = {"departments": _names(j.get("departments")), "offices": _names(j.get("offices"))}
    return attach(job, html_to_text(j.get("content")), details)


def greenhouse(slug: str) -> list[Job] | None:
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")  # the same list, with the text
    if r is None or r.status_code != HTTP_OK:
        return None
    return [_greenhouse_job(j) for j in r.json().get("jobs", [])]


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
        job = _job(
            j["id"],
            j["text"],
            c.get("location") or ", ".join(c.get("allLocations") or []),
            j["hostedUrl"],
            team=c.get("team"),
            posted=str(j.get("createdAt", "")),
        )
        items = j.get("lists") if isinstance(j.get("lists"), list) else []
        lists = [(plain_text(x.get("text")), html_to_text(x.get("content"))) for x in items if isinstance(x, dict)]
        text = sections(("", plain_text(j.get("descriptionPlain"))), *lists, ("", plain_text(j.get("additionalPlain"))))
        details = {
            "employment_type": c.get("commitment"),
            "department": c.get("department"),
            "workplace_type": j.get("workplaceType"),
            "country": j.get("country"),
        }
        out.append(attach(job, text, details))
    return out


def ashby(slug: str) -> list[Job] | None:
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r is None or r.status_code != HTTP_OK:
        return None
    out: list[Job] = []
    for j in r.json().get("jobs", []):
        if not j.get("isListed", True):
            continue
        job = _job(
            j["id"], j["title"], j.get("location"), j.get("jobUrl"), team=j.get("team"), posted=j.get("publishedAt", "")
        )
        text = plain_text(j.get("descriptionPlain")) or html_to_text(j.get("descriptionHtml"))
        details = {
            "employment_type": j.get("employmentType"),
            "department": j.get("department"),
            "workplace_type": j.get("workplaceType"),
            "is_remote": j.get("isRemote"),
        }
        out.append(attach(job, text, details))
    return out


def workable(slug: str) -> list[Job] | None:
    r = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")  # the same list, with the text
    if r is None or r.status_code != HTTP_OK:
        return None
    out: list[Job] = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        job = _job(
            j["shortcode"],
            j["title"],
            loc,
            j.get("url") or j.get("application_url"),
            team=j.get("department"),
            posted=j.get("published_on", ""),
        )
        details = {
            "employment_type": j.get("employment_type"),
            "function": j.get("function"),
            "industry": j.get("industry"),
            "experience": j.get("experience"),
            "education": j.get("education"),
            "telecommuting": j.get("telecommuting"),
        }
        out.append(attach(job, html_to_text(j.get("description")), details))
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
                    ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x),  # "il" -> "IL"
                    f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                    team=(j.get("department") or {}).get("label"),
                    posted=j.get("releasedDate", ""),
                )
            )
        offset += 100
        if offset >= d.get("totalFound", 0):
            return out


# Query parameters that track a visit or a share instead of naming a page: they differ per link, not per job.
_COMEET_NOISE = frozenset(
    {"t", "ref", "src", "share", "employee", "employeeshare", "gh_src", "from_app", "iscomingfromapp", "gclid", "_gl"}
)
_COMEET_NOISE_PREFIXES = ("utm_", "hsa_", "trakkr_", "gtm_")
_COMEET_LISTING = frozenset(  # what a company calls the page that lists its jobs
    {"career", "careers", "jobs", "openings", "open-positions", "job-openings", "positions", "vacancies"}
)
PageKey = tuple[str, str, tuple[tuple[str, str], ...]]


def _page_key(url: str) -> PageKey:
    """Where a link points, without what only tracks the visit: host, path without its end slash, and the query pairs
    that are not tracking. Two links with one key are one page."""
    u = urlparse(url)
    query = sorted(
        (k.strip().lower(), v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if k.strip()
        and k.strip().lower() not in _COMEET_NOISE
        and not k.strip().lower().startswith(_COMEET_NOISE_PREFIXES)
    )
    return u.netloc.lower(), u.path.rstrip("/"), tuple(query)


def _is_above(key: PageKey, others: Iterable[PageKey]) -> bool:
    return any(o[0] == key[0] and o[1].startswith(key[1] + "/") for o in others)


def _generic_pages(pages: list[str]) -> set[PageKey]:
    """The links, among a company's `url_active_page`s, that are not the page of one job: a page that more than one job
    points to (a company's "careers" page, or one job's page that its other locations share), the site's home page,
    a page without a query that is above the page of another job (`/careers` above `/careers/co/boston/AB.123/`), and
    one that is named as the list of jobs (`/careers`), which is all there is to go by when a company has one job. The
    pages that carry a job in the path or the query (`?job=AB.123`) differ per job and are kept."""
    keys = [_page_key(p) for p in pages if p]
    seen = Counter(keys)
    return {
        k
        for k in seen
        if seen[k] > 1
        or (not k[2] and (not k[1] or _is_above(k, seen) or k[1].rsplit("/", 1)[-1].lower() in _COMEET_LISTING))
    }


def comeet(slug: str) -> list[Job] | None:
    """slug is '<name>/<uid>' taken from comeet.com/jobs/<name>/<uid>. Job data is embedded in the page.

    A job's link is the page of the company's own site (`url_active_page`) when that page is the job's, and the page of
    the job on Comeet (`url_comeet_hosted_page`, `/jobs/<name>/<uid>/<title>/<position uid>`) when it is not: some
    companies fill `url_active_page` with their general careers page for some or all of their jobs. The job's id is its
    Comeet uid in both cases, so a changed link never makes a job new."""
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
    generic = _generic_pages([j.get("url_active_page") or "" for j in data])
    out: list[Job] = []
    for j in data:
        loc = j.get("location") or {}
        own, hosted = j.get("url_active_page"), j.get("url_comeet_hosted_page")
        url = hosted if own and _page_key(own) in generic else own
        job = _job(
            j.get("uid"),
            j.get("name"),
            ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
            url or hosted or own,
            team=j.get("department"),
            posted=j.get("time_updated", ""),
        )
        details = {
            "employment_type": j.get("employment_type"),
            "experience_level": j.get("experience_level"),
            "workplace_type": j.get("workplace_type"),
        }
        out.append(attach(job, _comeet_text(j), details))
    return out


def _comeet_text(j: dict[str, Any]) -> str:
    """The posting a Comeet page embeds with each position: `custom_fields.details` is a list of named parts
    (Description, Requirements and so on) in the order the company set, each an HTML text."""
    fields = j.get("custom_fields")
    parts = fields.get("details") if isinstance(fields, dict) else None
    named = [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []
    named.sort(key=lambda p: p["order"] if _is_number(p.get("order")) else 0)
    return sections(*((plain_text(p.get("name")), html_to_text(p.get("value"))) for p in named))


_JAZZHR_ITEM = '<li class="list-group-item">'
_JAZZHR_ITEM_END = re.compile(r"</ul>\s*</li>")
_JAZZHR_LINK = re.compile(r'<a href="(https://[^"]+/apply/([A-Za-z0-9]+)/[^"]*)">(.*?)</a>', re.S)
_JAZZHR_ANY_JOB_LINK = re.compile(r'href="https://[^"]+/apply/([A-Za-z0-9]+)/')
_JAZZHR_FACT = re.compile(r"<li><i class='fa fa-(map-marker|sitemap)'></i>(.*?)</li>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _page_text(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", _TAG.sub(" ", raw))).strip()


def jazzhr(slug: str) -> list[Job] | None:
    """slug is the `<slug>` of `<slug>.applytojob.com`. JazzHR has no public feed: the board page lists the jobs as HTML
    (a title link, then a map-marker location and a sitemap department). Only a page that reads cleanly gives a list:
    anything else is None, because an empty list would close every stored job."""
    r = get(f"https://{slug}.applytojob.com/apply")
    # an unknown slug redirects to jazzhr.com, and a changed page layout loses the job list: neither is "no jobs"
    if r is None or r.status_code != HTTP_OK or urlparse(r.url).hostname != f"{slug}.applytojob.com":
        return None
    _, marker, board = r.text.partition("jobs-list")
    if not marker:
        return None
    out: list[Job] = []
    for chunk in board.split(_JAZZHR_ITEM)[1:]:
        item = _JAZZHR_ITEM_END.split(chunk)[0]  # the footer and anything after the list is not part of the last job
        link = _JAZZHR_LINK.search(item)
        title = _page_text(link.group(3)) if link else ""
        if not link or not title:
            return None  # an entry we cannot read: a partial list would close the jobs we missed
        facts: dict[str, str] = {}
        for kind, value in _JAZZHR_FACT.findall(item):
            facts.setdefault(kind, _page_text(value))
        out.append(
            _job(link.group(2), title, facts.get("map-marker"), html.unescape(link.group(1)), team=facts.get("sitemap"))
        )
    # job links the item markup did not yield (a changed layout) mean the list is not complete
    if len(set(_JAZZHR_ANY_JOB_LINK.findall(board))) != len({j["job_id"] for j in out}):
        return None
    return out


_COMEET_UID = r"[0-9A-F]{2}\.[0-9A-F]{3}(?:-[0-9A-F]{2}\.[0-9A-F]{3})*"
_COMEET_COMMENT = re.compile(r"<!--.*?-->", re.S)
_COMEET_LINK = re.compile(
    rf'<a\b[^>]*?\bhref="([^"]*/co/([a-z0-9-]+)/({_COMEET_UID})/[a-z0-9%-]+/?[^"]*)"[^>]*>(.*?)</a>', re.S | re.I
)
_COMEET_ANY_UID = re.compile(rf"/co/[^/\"'\s<>]+/({_COMEET_UID})/", re.I)  # wider than the card link on purpose
_COMEET_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_COMEET_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.S | re.I)
_COMEET_NAME = re.compile(
    r'class="(?:comeet-position-name|careers-listing-name)"[^>]*>(.*?)(?:<br|<span|</div>)', re.S | re.I
)
_COMEET_TEAM = re.compile(
    r'<span[^>]*class="[^"]*(?:department|(?<![\w-])position(?![\w-]))[^"]*"[^>]*>(.*?)</span>', re.S | re.I
)
_COMEET_DEPT_NAME = re.compile(r'class="comeet-position-dept-name"[^>]*>(.*?)</div>', re.S | re.I)
_COMEET_ITEM = re.compile(r"<li\b[^>]*>(?:(?!<li\b).)*?</li>", re.S | re.I)
_COMEET_PLACE = re.compile(r'<span[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
_COMEET_EMPTY = re.compile(r"don.t have any open positions", re.I)
_COMEET_PLUGIN = "comeet-outer-wrapper"


def comeet_page(slug: str) -> list[Job] | None:
    """slug is the address of a WordPress careers page that shows the Comeet plugin's jobs, drawn on the server, for
    example 'https://ree.auto/careers/'. Comeet's own board is not readable for these accounts (the API answers 400), so
    the page is the source. Whatever the theme's card markup, a job is a link `/co/<place>/<uid>/<title>` with a title
    (a heading, or the plugin's `comeet-position-name`), maybe a department and a place; with no place in the card the
    place in the link is used. The list is trusted only when every job link of the page was read: a changed layout, an
    unread card or a page that is neither a list nor the plugin's "no open positions" notice is None, never a partial
    list (which would close stored jobs)."""
    host = urlparse(slug).hostname
    if not host:
        return None  # not an address: nothing is requested
    r = get(slug)
    if r is None or r.status_code != HTTP_OK or urlparse(r.url).hostname != host:
        return None
    page = _COMEET_COMMENT.sub("", r.text)  # the plugin keeps a commented copy of every title link
    out: list[Job] = []
    items = [(m.start(), m.end()) for m in _COMEET_ITEM.finditer(page)]
    for link in _COMEET_LINK.finditer(page):
        href, place, uid, card = link.groups()
        named = _COMEET_HEADING.search(card) or _COMEET_NAME.search(card)
        team = _COMEET_TEAM.search(card)
        where = _COMEET_PLACE.search(card)
        if not named:
            # the title is outside the link (a "Tell me more" button): read the list item around it, whose visible
            # place is not used: "Tel &#8211; Aviv" is not a place `common.ISRAEL` knows, the link's `tel-aviv` is
            item = next((page[a:b] for a, b in items if a < link.start() and link.end() < b), "")
            if {u.upper() for u in _COMEET_ANY_UID.findall(item)} != {uid.upper()}:
                item = ""  # an item that holds other jobs as well has no title of its own to read
            named = _COMEET_NAME.search(item)
            team = _COMEET_DEPT_NAME.search(item)
            where = None
        title = _page_text(named.group(1)) if named else ""
        if not title:
            return None  # a card we cannot read: a partial list would close the jobs we missed
        location = (_page_text(where.group(1)) if where else "") or place.replace("-", " ")
        job = _job(
            uid, title, location, urljoin(r.url, html.unescape(href)), team=_page_text(team.group(1)) if team else ""
        )
        if uid.upper() not in {j["job_id"].upper() for j in out}:  # a page that prints its list twice: the first wins
            out.append(job)
    # job links the card markup did not yield (a changed layout) mean the list is not complete
    if {u.upper() for u in _COMEET_ANY_UID.findall(page)} != {j["job_id"].upper() for j in out}:
        return None
    if not out and not (_COMEET_PLUGIN in page and _COMEET_EMPTY.search(html.unescape(_COMEET_SCRIPT.sub("", page)))):
        return None  # no job and no notice that there is none: not a page we understand
    return out


def bamboohr(slug: str) -> list[Job] | None:
    """slug is the `<slug>` of `<slug>.bamboohr.com`. The careers page is fed by `/careers/list`, a public JSON list
    with a city and state per job and the country when the company filled it in. The list is trusted only when it is
    the board's own JSON, its count matches and every entry reads: a partial or empty list would close stored jobs."""
    r = get(f"https://{slug}.bamboohr.com/careers/list")
    if r is None or r.status_code != HTTP_OK or urlparse(r.url).hostname != f"{slug}.bamboohr.com":
        return None
    try:
        data = r.json()
        postings = data["result"]
        total = (data.get("meta") or {}).get("totalCount", len(postings))
    except (ValueError, KeyError, TypeError, AttributeError):  # an unknown slug is redirected to an HTML page
        return None
    if not isinstance(postings, list) or total != len(postings):
        return None
    out: list[Job] = []
    for j in postings:
        if not isinstance(j, dict) or not j.get("id") or not j.get("jobOpeningName"):
            return None
        loc, ats = j.get("location") or {}, j.get("atsLocation") or {}
        place = [loc.get("city"), loc.get("state"), ats.get("city"), ats.get("state"), ats.get("province")]
        place.append(ats.get("country"))
        out.append(
            _job(
                j["id"],
                j["jobOpeningName"],
                ", ".join(dict.fromkeys(x for x in place if x)),  # a place the two fields share is written once
                f"https://{slug}.bamboohr.com/careers/{j['id']}",
                team=j.get("departmentLabel"),
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
    return [_greenhouse_job(j) for j in jobs]


def _workday_page(base: str, applied: dict[str, list[str]], text: str, offset: int) -> dict[str, Any] | None:
    """One page of a Workday job search, or None when the answer is not the JSON of a search (a maintenance
    redirect, an error page, a tenant or site that does not exist)."""
    body = {"appliedFacets": applied, "limit": WORKDAY_PAGE, "offset": offset, "searchText": text}
    r = post(base, json=body, headers={"Accept": "application/json"}, retry=True, pool="workday")
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


def _workday_values(facet: dict[str, Any]) -> list[dict[str, Any]]:
    """The selectable values of a facet (those with an id), not the facet groups nested in it."""
    values = facet.get("values")
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict) and isinstance(v.get("id"), str) and "facetParameter" not in v]


def _workday_is_israel_country(value: dict[str, Any]) -> bool:
    """Workday's own id for Israel, or a value named "Israel" (a tenant may give the country an id of its own)."""
    return value["id"] == WORKDAY_ISRAEL or str(value.get("descriptor") or "").strip().casefold() == "israel"


def _workday_israel_filter(first: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    """The facet that lists Israel in the facets of an unfiltered search: its name and its Israeli values, else None.

    The country comes first, whichever facet holds it. Failing that, the values of a location facet whose name is an
    Israeli place (Broadcom writes "ISR-Tel Aviv University"); the bare code `IL` is not enough, it is also Illinois."""
    facets = [
        (f["facetParameter"], _workday_values(f))
        for f in _workday_facets(first.get("facets"))
        if isinstance(f.get("facetParameter"), str)
    ]
    for param, values in facets:
        country = [v for v in values if _workday_is_israel_country(v)]
        if country:
            return param, country
    for param, values in facets:
        places = [v for v in values if ISRAEL.names_a_place(str(v.get("descriptor") or ""))]
        if places and "location" in param.lower():
            return param, places
    return None


def _workday_job(host: str, site: str, posting: dict[str, Any], via_facet: bool) -> Job | None:
    """A job from a posting, or None when it has no link or, on the word-search route, is not located in Israel (a
    place the matcher knows: the bare code IL is Illinois on Workday). A posting found through the Israel facet is
    Israel whatever its text says: a posting in several places says only "2 Locations"."""
    path = posting.get("externalPath")
    if not path:
        return None
    loc = " ".join((posting.get("locationsText") or "").split())
    if via_facet:
        if "israel" not in loc.lower():
            loc = f"{loc}, Israel" if loc else "Israel"
    elif not ISRAEL.names_a_place(loc):
        return None
    return _job(path, posting.get("title"), loc, f"{host}/en-US/{site}{path}")


def _workday_has_more(d: dict[str, Any], page: int, total: Any) -> bool:
    """Is there a page after this one? The total is trusted only when it is a positive number; otherwise a full page
    means there may be more."""
    postings = d["jobPostings"]
    if _is_number(total) and total > 0:
        return bool(postings) and (page + 1) * WORKDAY_PAGE < total
    return len(postings) == WORKDAY_PAGE


def _workday_contradicts(d: dict[str, Any], values: list[dict[str, Any]]) -> bool:
    """Does the first filtered page say more jobs than the filter's values hold? Then Workday ignored the filter (a
    wrong parameter shape returns every job), and tagging them Israel would store a worldwide list."""
    total: Any = d.get("total")
    counts: list[Any] = [v.get("count") for v in values]
    if not _is_number(total) or not all(_is_number(c) for c in counts):
        return False
    return bool(total > sum(counts))


def _is_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def workday(slug: str) -> list[Job] | None:
    """slug is '<tenant>/<wdN>/<site>', from <tenant>.<wdN>.myworkdayjobs.com/<site>. Only jobs in Israel are read.

    The facets of an unfiltered first search are searched, nested groups included, for one that lists Israel (its name
    and the id of the country differ per tenant): the country by Workday's id or by the name "Israel", else the places
    of a location facet. A search filtered by it gives the jobs in Israel, and a posting in several
    places, which says only "2 Locations", is tagged Israel. The places route is complete only if the matcher knows
    every Israeli place the facet lists. Without any such facet the search is for the word Israel and only postings
    located in a place the matcher knows are kept, so a tenant
    whose facets Workday reshaped still gives its Israel jobs, minus the multi-location ones. Any failure, including
    one after the first page and a filtered total larger than the facet says, is None: a partial list would close the
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
    found = _workday_israel_filter(first)
    param, values = found if found else ("", [])
    applied = {param: [v["id"] for v in values]} if found else {}
    text = "" if found else "Israel"
    out: list[Job] = []
    total: Any = None
    for page in range(WORKDAY_MAX_PAGES):
        d = _workday_page(base, applied, text, page * WORKDAY_PAGE)
        if d is None:
            return None
        if page == 0:
            if found and _workday_contradicts(d, values):
                return None
            total = d.get("total")  # only the first page carries it
        jobs = (_workday_job(host, site, p, bool(found)) for p in d["jobPostings"])
        out.extend(j for j in jobs if j)
        if not _workday_has_more(d, page, total):
            return out
    return None  # more pages than the limit: the list is not complete


def workday_description(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    """The text of one Workday posting: the JSON of the job page the site itself loads (the same `/wday/cxs/` path as
    the list, so the same robots.txt rule), `jobPostingInfo.jobDescription` as HTML. Of what else it gives only the
    employment type is kept (the ids and the questionnaire are the tenant's own). None on any failure."""
    tenant, dc, site = slug.split("/")
    path = job["job_id"]
    if not path.startswith("/job/"):
        return None
    r = get(
        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}",
        headers={"Accept": "application/json"},
        pool="workday",
    )
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        info = r.json()["jobPostingInfo"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(info, dict):
        return None
    return html_to_text(info.get("jobDescription")), {"employment_type": info.get("timeType")}


def bamboohr_description(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    """The text of one BambooHR posting: `/careers/<id>/detail`, the JSON the careers page itself loads (the same host
    and `/careers/` path as the list), `result.jobOpening.description` as HTML. Of what else it gives only the
    employment type is kept (the compensation, the share link and the ids are not). None on any failure."""
    job_id = str(job["job_id"])
    if not (job_id.isascii() and job_id.isdigit()):
        return None
    r = get(f"https://{slug}.bamboohr.com/careers/{job_id}/detail", headers={"Accept": "application/json"})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        opening = r.json()["result"]["jobOpening"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(opening, dict):
        return None
    return html_to_text(opening.get("description")), {"employment_type": opening.get("employmentStatusLabel")}


def oraclehcm_description(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    """The text of one Oracle HCM requisition: `recruitingCEJobRequisitionDetails`, the same `/hcmRestApi/` REST path
    as the list (so the same robots.txt rule), asked for the requisition by its id. The text is `ExternalDescriptionStr`
    followed by the external responsibilities and qualifications when the tenant fills them in (the corporate and
    organisation boilerplate is left out); of the rest only the schedule and the workplace type are kept. None on any
    failure."""
    host, _, site = slug.partition("/")
    job_id = str(job["job_id"])
    if not (job_id.isascii() and job_id.isdigit()):
        return None
    r = get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
        params={"onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'},
    )
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        req = r.json()["items"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(req, dict):
        return None
    text = sections(
        ("", html_to_text(req.get("ExternalDescriptionStr"))),
        ("Responsibilities", html_to_text(req.get("ExternalResponsibilitiesStr"))),
        ("Qualifications", html_to_text(req.get("ExternalQualificationsStr"))),
    )
    return text, {"employment_type": req.get("JobSchedule"), "workplace_type": req.get("WorkplaceType")}


# Career pages of the browser crawl whose job pages carry a schema.org JobPosting with the description in static HTML,
# found by hand on 2026-11-17. Every other page of the crawl gives no text from a plain request (JavaScript only, no
# markup of a posting, or bot protection as at Check Point, never worked around), see CONTRIBUTING.md.
JSONLD_PAGES = frozenset(
    {
        "https://www.weka.io/careers",
        "https://www.etoro.com/about/careers/",
        "https://perion.com/careers/",
        "https://digitalturbine.wd501.myworkdayjobs.com/Digital_Turbine_External_Careers",
    }
)
JSONLD_MAX_BYTES = 500 * 1024  # a page is read no further than this for its JSON-LD
_JSONLD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _job_posting(page: str) -> dict[str, Any] | None:
    """The first schema.org JobPosting in the JSON-LD blocks of a page, or None."""
    for block in _JSONLD.findall(page[:JSONLD_MAX_BYTES]):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item  # type: ignore[no-any-return]
    return None


HTTP_REDIRECTS = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 3


def _get_on_host(url: str, host: str = "") -> requests.Response | None:
    """`get`, and with `host` the redirects are followed here, one request at a time, only to an https address on that
    same host (an added trailing slash is the usual one), each through `get`, so through robots.txt and the per-host
    delay: a redirect to any other host is never requested. None when the answer is not a 200."""
    r = get(url, allow_redirects=False) if host else get(url)
    for _ in range(MAX_REDIRECTS if host else 0):
        if r is None or r.status_code not in HTTP_REDIRECTS:
            break
        target = r.headers.get("Location")
        url = urljoin(url, target) if target else ""
        if not url.startswith("https://") or host_of(url) != host:  # no Location is no page either
            return None
        r = get(url, allow_redirects=False)
    return r if r is not None and r.status_code == HTTP_OK else None


def _jsonld_page_text(url: str, host: str = "") -> tuple[str, dict[str, Any]] | None:
    """The description of the schema.org JobPosting in the JSON-LD of a job page, and nothing else of the page (never
    page text). With `host`, redirects follow `_get_on_host`. None when the page has no answer or no JobPosting."""
    r = _get_on_host(url, host)
    if r is None:
        return None
    posting = _job_posting(r.text)
    return jsonld_description(posting) if posting else None


def _jsonld_job_pages(
    host_for: Callable[[str], str],
) -> tuple[Callable[[str, Job], tuple[str, dict[str, Any]] | None], Callable[[str, Job], bool]]:
    """The job-page reader and the filter of an ATS whose job pages carry a JobPosting JSON-LD. Only a job whose own
    https page is on the host the company's slug names (`host_for(slug)`) is asked for (a link to another host is not
    a request and not counted), and a redirect to another host is not followed."""

    def wants(slug: str, job: Job) -> bool:
        url = str(job.get("url"))
        return url.startswith("https://") and host_of(url) == host_for(slug)

    def fetch(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
        return _jsonld_page_text(str(job["url"]), host_for(slug))

    return fetch, wants


# Career pages of the browser crawl whose company embeds a public board of an ATS we already read (the page's own data
# names it, or its job pages load the ATS's script with the company's id), found by hand on 2026-11-20: the career page
# -> (the ATS's adapter, its slug). The crawl keeps its list and its job ids; the text is the board's, joined to the
# crawl's job by the id in the job's link, so the board is read once a run, not once a job, and nothing is re-pinned.
BOARD_PAGES: dict[str, tuple[str, str]] = {
    "https://monday.com/careers": ("ashby", "monday.com"),
    "https://www.classiq.io/careers-page": ("comeet", "classiq/F7.008"),
    "https://komodor.com/careers/": ("comeet", "komodor/96.005"),
    "https://panorays.com/careers/": ("comeet", "panorays/97.00D"),
    "https://www.oligo.security/company/careers": ("comeet", "oligosecurity/5A.00B"),
    "https://www.lasso.security/careers": ("comeet", "lassosecurity/F9.008"),
    "https://forsightrobotics.com/careers": ("comeet", "forsightrobotics/E9.008"),
}
_BOARD_UID = re.compile(r"(?<![0-9A-Z])[0-9A-F]{2}[._][0-9A-F]{3}(?![0-9A-Z])")  # Comeet uid 4E.47C, or 4E_47C
_boards: dict[str, dict[str, Job] | None] = {}  # per career page and process; a failed read is kept too (one request)


def _board(page: str) -> dict[str, Job] | None:
    """The jobs of the career page's board with a text, by upper-cased id (an Ashby id, a Comeet uid), read once a
    run; None when the board could not be read or holds none (one `::warning::` says which page, so a board that
    disappeared is seen, not silent). A page shared by two companies could be read twice: harmless."""
    if page not in _boards:
        ats, board_slug = BOARD_PAGES[page]
        try:
            jobs = ADAPTERS[ats](board_slug)
        except Exception as exc:  # a board that answers something unexpected costs the texts of its page only
            print(f"::warning::the {ats} board of {page} could not be read: {type(exc).__name__}")
            jobs = None
        board = {str(j["job_id"]).upper(): j for j in jobs or [] if j.get("description")}
        if not board:
            print(f"::warning::the {ats} board of {page} gave no job text")
        _boards[page] = board or None
    return _boards[page]


def _board_job(page: str, job: Job) -> Job | None:
    """The board's job of a crawl job: an id that is a segment of the link's path (Ashby, a Comeet link
    `/4E.47C/title/`), or a Comeet uid written in one (`position-4e_47c`). A link that names two board jobs matches
    none: a wrong text is worse than none."""
    board = _board(page)
    if not board:
        return None
    for segment in reversed(urlparse(str(job.get("url") or "")).path.upper().strip("/").split("/")):
        if segment in board:
            return board[segment]
        uids = {u.replace("_", ".") for u in _BOARD_UID.findall(segment)}
        if uids & board.keys():
            return board[uids.pop()] if len(uids) == 1 else None  # two uids in one segment: which job is it?
    return None


def _board_description(page: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    found = _board_job(page, job)
    return (found["description"], found.get("details") or {}) if found else None


# Career pages of the browser crawl whose company publishes its postings through its own WordPress REST API, found by
# hand on 2026-11-20 (the site's `/wp-json/wp/v2/types` names the post type, and each answer is public, no sign-in):
# the career page -> the exact endpoint. Its `robots.txt` allows it; the request goes through `common.get` (robots.txt,
# the delay, the User-Agent, redirects only on the same host), once per process for the page. A crawl job takes the
# `content` of the post whose link is its own link (host without `www.`, path without the trailing slash, lower case);
# no such post, or two, is no text. Pecan is not listed: its host did not answer on 2026-11-20, so nothing was checked.
WP_POSTS: dict[str, str] = {
    "https://www.priority-software.com/careers/": "https://www.priority-software.com/wp-json/wp/v2/careers?per_page=100",
    "https://www.meshpayments.com/careers": "https://www.meshpayments.com/wp-json/wp/v2/cpt_careers?per_page=100",
    "https://www.lightbitslabs.com/careers/": "https://www.lightbitslabs.com/wp-json/wp/v2/lb_position?per_page=100",
}
WP_PER_PAGE = 100  # the `per_page` of every endpoint above
_wp_texts: dict[str, dict[str, str] | None] = {}  # per career page and process; a failed read is kept too


def _link_key(url: str) -> str:
    """The host without `www.` and the path without the trailing slash, lower case; the scheme, query and fragment are
    ignored. Empty for an address that cannot be read (it matches nothing)."""
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/').lower()}"


def _wp_page_texts(page: str) -> dict[str, str] | None:
    """The text of each published post of the page's endpoint, by `_link_key` of its link, read once a run (the first
    100 posts: a full first page is a `::warning::`, since later posts would get no text). A key two posts share is
    dropped. None when the endpoint could not be read or has no text (one `::warning::` says which page, never the
    answer)."""
    if page not in _wp_texts:
        endpoint = WP_POSTS[page]
        r = _get_on_host(endpoint, host_of(endpoint))
        try:
            posts = r.json() if r is not None else None
        except ValueError:
            posts = None
        texts: dict[str, str] = {}
        clashes: set[str] = set()
        for entry in posts if isinstance(posts, list) else []:
            content = entry.get("content") if isinstance(entry, dict) else None
            rendered = content.get("rendered") if isinstance(content, dict) else None
            link = entry.get("link") if isinstance(entry, dict) else None
            text = html_to_text(rendered) if isinstance(rendered, str) and isinstance(link, str) else ""
            key = _link_key(str(link)) if text else ""
            if key in texts:
                clashes.add(key)
            elif key:
                texts[key] = text
        for key in clashes:
            del texts[key]
        if not texts:
            print(f"::warning::the WordPress posts of {page} could not be read or gave no job text")
        elif isinstance(posts, list) and len(posts) >= WP_PER_PAGE:
            print(f"::warning::the WordPress posts of {page} fill a whole page: later posts are not read")
        _wp_texts[page] = texts or None
    return _wp_texts[page]


def _wp_description(page: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    text = (_wp_page_texts(page) or {}).get(_link_key(str(job.get("url") or "")))
    return (text, {}) if text else None


def browser_description(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    """The text of a job found by the browser crawl: a job of a page in BOARD_PAGES takes the text of its company's
    public ATS board; an Eightfold job asks its own `position_details` JSON (the call the career page makes, under the
    `/api/pcsx` path its search already uses); a job of a page in JSONLD_PAGES asks its page and reads only the
    JobPosting JSON-LD; a job of a page in WP_POSTS takes the `content` of the post of its company's own WordPress
    REST API with its link. Never page text. None on any failure."""
    if slug in BOARD_PAGES:
        return _board_description(slug, job)
    if slug in WP_POSTS:
        return _wp_description(slug, job)
    detail = job.get("detail_url")
    if not detail:
        return _jsonld_page_text(job["url"], host_of(slug))
    r = get(detail, headers={"Accept": "application/json"})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        return html_to_text(r.json()["data"]["jobDescription"]), {}
    except (ValueError, KeyError, TypeError):
        return None


def _browser_wants_page(slug: str, job: Job) -> bool:
    if slug in BOARD_PAGES:
        return _board_job(slug, job) is not None  # no match, or a board that could not be read: no ask, no miss
    if slug in WP_POSTS:
        return _wp_description(slug, job) is not None  # the same: no match is no ask and no miss
    if job.get("detail_url"):
        return True
    return slug in JSONLD_PAGES and host_of(str(job.get("url"))) == host_of(slug)  # the career page's own host only


# Adapters whose list has no text: one request per job page, `fetch(slug, job) -> (text, details) | None`. `WANTS`
# limits an ATS to the jobs worth a request.
DETAILS: dict[str, Callable[[str, Job], tuple[str, dict[str, Any]] | None]] = {
    "workday": workday_description,
    "bamboohr": bamboohr_description,
    "oraclehcm": oraclehcm_description,
    "browser": browser_description,
}
WANTS: dict[str, Callable[[str, Job], bool]] = {"browser": _browser_wants_page}
for _ats, _host_for in (
    ("jazzhr", lambda slug: f"{slug}.applytojob.com"),
    ("phenom", lambda slug: slug.partition("/")[0]),
    ("comeet_page", host_of),
):
    DETAILS[_ats], WANTS[_ats] = _jsonld_job_pages(_host_for)


def _in_refresh_slice(key: tuple[str, str], day: int) -> bool:
    """True on the one day in REFRESH_DAYS when a job's stored text is asked again: the key fixes the job's turn and the
    day moves it, so every job comes up once every REFRESH_DAYS days, whatever its key."""
    return (zlib.crc32("\x00".join(key).encode()) + day) % REFRESH_DAYS == 0


def add_descriptions(  # noqa: PLR0913, PLR0917
    ats: str, slug: str, company: str, jobs: list[Job], held: set[tuple[str, str]], day: int = 0
) -> tuple[int, int, int]:
    """Gives the jobs of a company whose list carries no text their description, one request per job page, and
    returns (requested, got a text, new jobs deferred). Only jobs without a text are asked: one Supabase already
    holds (`held`, left by the pull) is marked and not fetched again, except on its turn of one day in
    REFRESH_DAYS, when it is asked again so an edited posting gets its new text (the sync writes it only if its
    hash changed), after the new jobs and with only what they leave of the cap. At most DETAIL_CAP are asked per
    company and run, the new jobs in a window that moves with `day` so a page that never answers cannot starve the
    others, and five misses in a row end the company's run (a host that refuses is left alone). The rest wait for
    the next run. A page that fails, is refused by robots.txt or has no text gives no description and changes
    nothing else: this never fails the list, and a refusal does not make the company a robots skip (the list was
    crawled)."""
    fetch_one = DETAILS.get(ats)
    if fetch_one is None:
        return 0, 0, 0
    wants = WANTS.get(ats)
    todo: list[Job] = []
    refresh: list[Job] = []  # stored texts whose turn it is: they only use what the new jobs leave of the cap
    for job in jobs:
        if job.get("description") or (wants and not wants(slug, job)):
            continue
        key = (company, str(job.get("job_id")))
        if key not in held:
            todo.append(job)
            continue
        job[HELD] = True
        if _in_refresh_slice(key, day):
            refresh.append(job)
    if todo:  # a different window each day, by the fewest pages a run can end after, so no job is starved
        start = day * DETAIL_FAIL_STREAK % len(todo)
        todo = todo[start:] + todo[:start]
    waiting = len(todo)  # the new jobs: a refresh that is not reached waits for its next turn, not the next run
    todo += refresh
    asked = got = streak = 0
    for job in todo[:DETAIL_CAP]:
        asked += 1
        try:
            found = fetch_one(slug, job)
            if found:
                attach(job, *found)
        except Exception:  # a posting must never stop its company's crawl, whatever its page is
            found = None
        if job.get("description"):
            got += 1
            streak = 0
        else:
            streak += 1
            if streak >= DETAIL_FAIL_STREAK:  # a host that keeps refusing or failing is left alone until the next run
                break
    reset_robots_block()  # a refused job page is not a refused company
    return asked, got, max(waiting - asked, 0)


def _oraclehcm_place(req: dict[str, Any]) -> str | None:
    """Where a requisition is in Israel (its primary place, else the first secondary place there), or None."""
    if req.get("PrimaryLocationCountry") == ORACLEHCM_ISRAEL:
        return str(req.get("PrimaryLocation") or "Israel")
    for place in req.get("secondaryLocations") or []:
        if isinstance(place, dict) and place.get("CountryCode") == ORACLEHCM_ISRAEL:
            return str(place.get("Name") or "Israel")
    return None


def _oraclehcm_page(host: str, site: str, offset: int) -> tuple[int, list[dict[str, Any]]] | None:
    """The total and one page of requisitions located in Israel, or None when the answer is not the JSON of a
    search (a failed request, a wrong site, which answers 200 with something else) or a requisition lacks an id or
    a title."""
    finder = f"findReqs;siteNumber={site},limit={ORACLEHCM_PAGE},offset={offset},location=Israel"
    r = get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
        params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
    )
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        search = r.json()["items"][0]
        total, reqs = search["TotalJobsCount"], search["requisitionList"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(reqs, list) or not isinstance(total, int):
        return None
    if not all(isinstance(q, dict) and q.get("Id") and q.get("Title") for q in reqs):
        return None
    return total, reqs


def oraclehcm(slug: str) -> list[Job] | None:
    """slug is '<host>/<site>' of an Oracle Fusion HCM career site, for example 'jpmc.fa.oraclecloud.com/CX_1001' (the
    page is `https://<host>/hcmUI/CandidateExperience/en/sites/<site>`). Only jobs in Israel are read: the public
    requisition search is asked for the location Israel and a requisition is kept when its primary or one of its
    secondary places is in Israel (country code IL). The location filter is used, not a keyword search for the word
    Israel: the keyword matches the text of a posting, and an audit (PLAN_3.md step B3) found it missed 10 of the 16
    Israel requisitions of one tenant and 17 of the 19 of another. Any failure, including one
    after the first page, is None: a partial list would close the jobs it lacks."""
    parts = slug.split("/")
    if len(parts) != ORACLEHCM_SLUG_PARTS or "." not in parts[0] or not parts[1]:
        return None
    host, site = parts
    out: list[Job] = []
    for page in range(ORACLEHCM_MAX_PAGES):
        found = _oraclehcm_page(host, site, page * ORACLEHCM_PAGE)
        if found is None:
            return None
        total, reqs = found
        if len(reqs) < min(ORACLEHCM_PAGE, total - page * ORACLEHCM_PAGE):  # fewer than the total promises
            return None
        for req in reqs:
            place = _oraclehcm_place(req)
            if place:
                url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req['Id']}"
                out.append(_job(req["Id"], req["Title"], place, url, posted=req.get("PostedDate")))
        if (page + 1) * ORACLEHCM_PAGE >= total:
            return out
    return None  # more pages than the limit: the list is not complete


def _amazon_page(slug: str, offset: int) -> tuple[int, list[Job]] | None:
    """The reported total and one page of jobs, or None when the answer is not the JSON of a search or a job lacks an
    id, a title or a path, or is not in the asked country."""
    r = get(
        "https://www.amazon.jobs/en/search.json",
        params={"country": slug, "result_limit": AMAZON_PAGE, "offset": offset, "sort": "recent"},
    )
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        data = r.json()
        hits, found = data["hits"], data["jobs"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(found, list) or not isinstance(hits, int):
        return None
    out: list[Job] = []
    for j in found:
        if not (isinstance(j, dict) and j.get("id_icims") and j.get("title") and j.get("job_path")):
            return None
        if j.get("country_code") != slug:
            return None
        where = j.get("normalized_location") or j.get("location")
        url = f"https://www.amazon.jobs{j['job_path']}"
        job = _job(j["id_icims"], j["title"], where, url, team=j.get("job_category"), posted=j.get("posted_date"))
        text = sections(
            ("", html_to_text(j.get("description"))),
            ("Basic qualifications", html_to_text(j.get("basic_qualifications"))),
            ("Preferred qualifications", html_to_text(j.get("preferred_qualifications"))),
        )
        details = {"job_family": j.get("job_family"), "employment_type": j.get("job_schedule_type")}
        out.append(attach(job, text, details))
    return hits, out


def amazon(slug: str) -> list[Job] | None:
    """slug is the country code of amazon.jobs ('ISR'). The site's own search JSON, filtered by `country`, lists every
    job there (the job page is `https://www.amazon.jobs<job_path>`). The list is trusted only when every page answers
    with that JSON, every job is in the asked country and has an id, a title and a path, and the pages add up to the
    reported `hits`, which must be the same on every page and above zero (a wrong country code answers 200 with no
    job), with no job twice: a partial list would close the jobs it lacks."""
    out: list[Job] = []
    first_hits: int | None = None
    for page in range(AMAZON_MAX_PAGES):
        got = _amazon_page(slug, page * AMAZON_PAGE)
        if got is None:
            return None
        hits, jobs = got
        if first_hits is None:
            first_hits = hits
        if hits == 0 or hits != first_hits:  # no job at all is a wrong code, not a board; a moving total is a torn list
            return None
        out.extend(jobs)
        if len(out) >= hits or not jobs:
            return out if len(out) == hits and len({j["job_id"] for j in out}) == hits else None
    return None  # more pages than the limit: the list is not complete


def _phenom_page(host: str, country: str, lang: str, offset: int) -> tuple[int, list[Job]] | None:
    """The reported total and one page of Israel jobs from a Phenom career site, or None when the answer is not the
    JSON of a search, the total is not the Israel count of the site's own country facet (the filter matched nothing),
    a job lacks an id, a title or a sequence number, or a job is not in Israel (the filter was ignored)."""
    body = {
        "lang": f"{lang}_{country}",
        "deviceType": "desktop",
        "country": country,
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "Most recent",
        "subsearch": "",
        "from": offset,
        "jobs": True,
        "counts": True,
        "all_fields": ["country"],
        "size": PHENOM_PAGE,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page15",
        "siteType": "external",
        "keywords": "",
        "global": True,
        "selected_fields": {"country": ["Israel"]},
        "locationData": {},
    }
    r = post(f"https://{host}/widgets", json=body)
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        search = r.json()["refineSearch"]
        total, found = search["totalHits"], search["data"]["jobs"]
        facet = next(a["value"] for a in search["data"]["aggregations"] if a.get("field") == "country")
        israel = facet.get("Israel", 0)
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None
    if not isinstance(found, list) or not isinstance(total, int) or israel != total:
        return (
            None  # the country facet counts the Israel jobs whatever the filter: a filter that matches nothing differs
        )
    out: list[Job] = []
    for j in found:
        if not (isinstance(j, dict) and j.get("jobId") and j.get("title") and j.get("jobSeqNo")):
            return None
        places = [str(x) for x in [j.get("location"), *(j.get("multi_location") or [])] if x]
        place = next((x for x in places if "israel" in x.lower()), None)
        if place is None:
            return None
        url = f"https://{host}/{country}/{lang}/job/{j['jobSeqNo']}"
        out.append(_job(j["jobId"], j["title"], place, url, team=j.get("category"), posted=j.get("postedDate")))
    return total, out


def phenom(slug: str) -> list[Job] | None:
    """slug is '<host>/<country>/<lang>' of a Phenom career site, for example 'careers.cisco.com/global/en' (the site
    is `https://<host>/<country>/<lang>/search-results`). The site's own search widget is asked for the country Israel,
    50 jobs a page. The list is trusted only when every page answers with that JSON, every job is located in Israel (a
    site that ignores the filter gives the worldwide list), and the jobs add up to the reported total, which is the
    same on every page, with no job twice: a partial list would close the jobs it lacks."""
    parts = slug.split("/")
    if len(parts) != PHENOM_SLUG_PARTS or "." not in parts[0] or not all(parts):
        return None
    host, country, lang = parts
    out: list[Job] = []
    first_total: int | None = None
    for page in range(PHENOM_MAX_PAGES):
        got = _phenom_page(host, country, lang, page * PHENOM_PAGE)
        if got is None:
            return None
        total, jobs = got
        if first_total is None:
            first_total = total
        if total != first_total:
            return None
        out.extend(jobs)
        if len(out) >= total or not jobs:
            return out if len(out) == total and len({j["job_id"] for j in out}) == total else None
    return None  # more pages than the limit: the list is not complete


_AVATURE_FACET = re.compile(r"(\d+)=(\d+)")
_AVATURE_JOB_ID = re.compile(r"/(\d+)/?$")


def _avature_posted(text: str | None) -> str:
    """The date of an RSS `pubDate` as YYYY-MM-DD, or '' when it is missing or not a date."""
    try:
        return parsedate_to_datetime(text or "").date().isoformat()
    except (TypeError, ValueError):
        return ""


def _avature_job(host: str, item: ET.Element) -> Job | None:
    """One job of the feed, or None when it lacks a title or a link, the link is not on the board's own host or does not
    end with the job's number. The feed has no place: its `description` holds the country of the posting (Siemens) or
    only a requisition number (Synopsys, ` - 50209`), so the place is the description when it names Israel, and else
    Israel, the country the feed was filtered to (a posting of several countries lists Israel among them)."""
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    found = _AVATURE_JOB_ID.search(link)
    if not (title and found and host_of(link) == host and link.startswith("https://")):
        return None
    job_id = found[1]
    title = title.removesuffix(f" - {job_id}").strip()  # Synopsys ends every title with its number
    description = (item.findtext("description") or "").strip()
    place = description if ISRAEL.search(description) else "Israel"
    return _job(job_id, title, place, link, posted=_avature_posted(item.findtext("pubDate")))


def avature(slug: str) -> list[Job] | None:
    """slug is '<host>/<portal>?<field>=<value>' of an Avature career site filtered to Israel, for example
    'synopsys.avature.net/careers?2001=21374' or 'jobs.siemens.com/en_US/externaljobs?42386=811993' (the site's own
    search page is `https://<host>/<portal>/SearchJobs`; the field and value are those of its Country facet, which a
    person reads once from the page: the option list is loaded by script, from the page's own search API). The
    public RSS feed `https://<host>/<portal>/SearchJobs/feed/` is asked for that facet, one request: the feed has
    no paging (it ignores jobOffset and jobRecordsPerPage) and gives at most AVATURE_MAX jobs, so a feed that long is
    a failure, not a list: it may be cut short (a partial list would close the jobs it lacks), and it is also what an
    ignored facet gives (the worldwide list). Any answer that is not an RSS feed, or a job that cannot be read, is
    None too."""
    path, _, query = slug.partition("?")
    host, _, portal = path.partition("/")
    if "." not in host or not portal or not _AVATURE_FACET.fullmatch(query):
        return None
    field, value = query.split("=")
    r = get(f"https://{host}/{portal.strip('/')}/SearchJobs/feed/", params={field: value})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return None
    items = root.findall("./channel/item")
    if root.tag != "rss" or len(items) >= AVATURE_MAX:
        return None
    out = [_avature_job(host, item) for item in items]
    jobs = [j for j in out if j]
    if len(jobs) != len(out) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return jobs


_EIGHTFOLD_SLUG = re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)\?domain=([a-z0-9-]+(?:\.[a-z0-9-]+)+)")


def _eightfold_posted(stamp: Any) -> str:
    """The date of an epoch-seconds `postedTs` as YYYY-MM-DD, or '' when it is missing or not a time."""
    try:
        return datetime.fromtimestamp(stamp, tz=UTC).date().isoformat() if isinstance(stamp, int) else ""
    except (OverflowError, OSError, ValueError):
        return ""


def _eightfold_job(host: str, p: Any) -> tuple[Job, bool] | None:
    """One position of the search answer as (job, located in Israel), or None when it cannot be read: not an object,
    no number, no title, or a `positionUrl` that is not a path of the board. The job has the id, title, place (the
    locations joined by '; ') and address that the browser crawl gives the same board, so a company read by either way
    keeps its jobs. The place is Israel when a location or a standardized one (`..., IL`) names it, by the matcher
    of the other adapters (a bare IL of Illinois is not Israel). When only the standardized locations name Israel, they
    are added to the place, so that the place alone shows it (run.py's israel_only filter reads nothing else)."""
    if not isinstance(p, dict):
        return None
    locations = [str(x) for x in p["locations"]] if isinstance(p.get("locations"), list) else []
    standard = [str(x) for x in p["standardizedLocations"]] if isinstance(p.get("standardizedLocations"), list) else []
    path = p.get("positionUrl")
    if not (isinstance(p.get("id"), int) and p.get("name") and isinstance(path, str) and path.startswith("/")):
        return None
    israel = [x for x in standard if ISRAEL.search(x)] if not any(ISRAEL.search(x) for x in locations) else []
    job = (
        _job(  # a place only the standardized locations name is added, so the pipeline's own check of the place sees it
            p["id"],
            p["name"],
            "; ".join([*locations, *israel]),
            f"https://{host}{path}",
            team=p.get("department"),
            posted=_eightfold_posted(p.get("postedTs")),
        )
    )
    return job, bool(israel) or any(ISRAEL.search(x) for x in locations)


def _eightfold_page(host: str, domain: str, start: int) -> tuple[int, list[Any]] | None:
    """One answer of the board's search, asked for the location Israel from position `start`: (total, positions), or
    None when the answer is not the board's JSON."""
    r = get(f"https://{host}/api/pcsx/search", params={"domain": domain, "location": "Israel", "start": start})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        data = r.json()["data"]
        total, positions = data["count"], data["positions"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(total, int) and isinstance(positions, list)):
        return None
    return total, positions


def eightfold(slug: str) -> list[Job] | None:
    """slug is '<host>?domain=<domain>' of an Eightfold career site, for example 'careers.micron.com?domain=micron.com'
    or 'lamresearch.eightfold.ai?domain=lamresearch.com' (the site is `https://<host>/careers`; the domain is the one
    its own search call carries). The site's search API, `https://<host>/api/pcsx/search`, the call the page makes
    itself, is asked for the location Israel, ten positions an answer, from the position `start` on (it ignores `num`).
    Its robots.txt allows `/api/pcsx` explicitly (`Allow: /api/pcsx` against `Disallow: /`, the longer rule wins; read
    on 2026-11-19 for Micron, Boston Scientific and Lam Research), and the requests go through `common.get`: that file's
    rules, one request a second to a host, the project's User-Agent. A board whose API is switched off (NetApp answers
    HTTP 403, "PCSX is not enabled") is None, with no other way tried. The list is trusted only when every answer is
    that JSON, the first reports at most EIGHTFOLD_MAX positions (a board that ignores the location reports its whole
    list, and a list that long may be cut short), the total is the same on every answer, the positions add up to it
    and no position comes twice: a partial list would close the jobs it lacks. Of those, the jobs located in Israel
    are kept (the search may list a place near Israel too); a position that cannot be read is None too."""
    found = _EIGHTFOLD_SLUG.fullmatch(slug)
    if not found:
        return None
    host, domain = found.groups()
    positions: list[Any] = []
    total: int | None = None
    while total is None or len(positions) < total:
        got = _eightfold_page(host, domain, len(positions))
        if (
            got is None
            or got[0] > EIGHTFOLD_MAX
            or (total is not None and got[0] != total)
            or (not got[1] and got[0] > len(positions))  # an answer with no position before the total is reached
        ):
            return None
        total = got[0]
        positions.extend(got[1])
    read = [_eightfold_job(host, p) for p in positions]
    jobs = [r for r in read if r]
    if len(jobs) != len(positions) or len({j["job_id"] for j, _ in jobs}) != len(jobs) or len(positions) != total:
        return None
    return [j for j, israel in jobs if israel]


_JIBE_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def _jibe_posted(text: Any) -> str:
    """The date of a `posted_date` as YYYY-MM-DD, or '' when it is missing or not a date. A board gives an ISO time
    (`2026-11-15T12:45:00+0000`) or a long date (`March 24, 2026`)."""
    if not isinstance(text, str):
        return ""
    for day, form in ((text[:10], "%Y-%m-%d"), (text, "%B %d, %Y")):
        try:
            return datetime.strptime(day, form).date().isoformat()
        except ValueError:
            continue
    return ""


def _jibe_job(host: str, item: Any) -> Job | None:
    """One job of the answer, or None when it cannot be read: not an object, no number, no title or no place. The
    place is `full_location`, which lists every place of a job of several (ICE: 'London, United Kingdom; Tel-Aviv,
    Israel'). The link is the board's own job page, never `apply_url` (that is the applicant system's login page)."""
    data = item.get("data") if isinstance(item, dict) else None
    if not isinstance(data, dict):
        return None
    slug, title, place, category = data.get("slug"), data.get("title"), data.get("full_location"), data.get("category")
    if not (isinstance(slug, str) and slug.isdigit() and isinstance(title, str) and title.strip()):
        return None
    if not (isinstance(place, str) and place.strip()):
        return None
    team = category[0].strip() if isinstance(category, list) and category and isinstance(category[0], str) else ""
    return _job(
        slug, title, place, f"https://{host}/jobs/{slug}", team=team, posted=_jibe_posted(data.get("posted_date"))
    )


def jibe(slug: str) -> list[Job] | None:
    """slug is the host of a Jibe career site (the career site of iCIMS' vendor on the company's own domain), for
    example 'www.pepsicojobs.com', 'careers.se.com' or 'careers.ice.com'. The site's public search API,
    `https://<host>/api/jobs`, the call the page makes itself, is asked for the country Israel and up to JIBE_MAX
    jobs, in one request: only that host is read, never the applicant system (iCIMS, whose robots.txt disallows
    everything). Each host's robots.txt allows everything (`Allow: /`, `crawl-delay: 5`, read on 2026-11-19), and the
    request goes through `common.get`: that file's rules, one request a second to a host, the project's User-Agent. The
    list is trusted only when the answer is the board's JSON, its total is at most JIBE_MAX (a board that ignores the
    country reports its whole list, and a list that long may be cut short), the jobs add up to that total and no job
    comes twice: a partial list would close the jobs it lacks. Of those, the jobs whose place names Israel are kept;
    a job that cannot be read is None too. The answer holds each job's text, which is not read."""
    if not _JIBE_HOST.fullmatch(slug):
        return None
    r = get(f"https://{slug}/api/jobs", params={"country": "Israel", "limit": JIBE_MAX, "page": 1})
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        data = r.json()
        total, items = data["totalCount"], data["jobs"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (isinstance(total, int) and isinstance(items, list)) or total > JIBE_MAX or len(items) != total:
        return None
    read = [_jibe_job(slug, i) for i in items]
    jobs = [j for j in read if j]
    if len(jobs) != len(items) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return [j for j in jobs if ISRAEL.search(j["location"] or "")]


VARONIS_HOST = "careers.varonis.com"


def _varonis_job(item: Any) -> Job | None:
    """One requisition of the answer, or None when it cannot be read: not an object, an id that is not letters and
    digits, no title, `jobLocations` that is not a list or holds a place with no city or country text, or a count of
    locations that is not the number listed. The place is `City, Country` of each location, joined by '; ' (a job of
    several lists every one). The link is the careers site's own page for the job, `/careers?p=job/<id>`, the address
    its own list links to (the `app.jobvite.com` links of the answer are the applicant system and are not used)."""
    if not isinstance(item, dict):
        return None
    job_id, title, places = item.get("eId"), item.get("title"), item.get("jobLocations")
    if not (
        isinstance(job_id, str) and job_id.isascii() and job_id.isalnum() and isinstance(title, str) and title.strip()
    ):
        return None
    if (
        not isinstance(places, list)
        or type(item.get("locationsCount")) is not int
        or item["locationsCount"] != len(places)
    ):
        return None
    where = [
        ", ".join(x for x in (p.get("city"), p.get("country")) if isinstance(x, str) and x.strip())
        if isinstance(p, dict)
        else ""
        for p in places
    ]
    if not all(where):
        return None  # a place that cannot be read may be the one in Israel: the job, and so the list, is a failure
    return _job(
        job_id,
        title,
        "; ".join(where),
        f"https://{VARONIS_HOST}/careers?p=job/{job_id}",
        team=item.get("department") if isinstance(item.get("department"), str) else "",
    )


def varonis(slug: str) -> list[Job] | None:
    """slug is 'careers.varonis.com', the only host this reader serves (a dedicated reader for one company, by the
    maintainer's decision of 2026-11-20: the careers site is a custom page over Jobvite and no other company uses it).
    The site's own list, `https://careers.varonis.com/api/getRequisitions`, the call its page makes itself, is read in
    one request; the site has no robots.txt of its own (it answers its page), so `common.get` finds no rule, and it is
    the same gate as every adapter: one request a second, the project's User-Agent. The list has no paging (it
    ignores page and limit) and no total, so it is trusted only when it is the site's JSON (`data`), has at most
    VARONIS_MAX requisitions (a list that long may be cut short), no job is unreadable or twice and every job's
    `locationsCount` is the number of places it lists: a partial list would close the jobs it lacks. Of those, the
    jobs whose place text (city and country of each location) names Israel, by `common.ISRAEL`, are kept. The
    answer has no job text and no date. An empty list, or one with no job in Israel, is a failure too (Varonis has
    jobs in Israel, so an empty answer is a broken one, and a failure keeps the jobs listed; an empty list would close
    them all)."""
    if slug != VARONIS_HOST:
        return None
    r = get(f"https://{VARONIS_HOST}/api/getRequisitions")
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        items = r.json()["data"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(items, list) or len(items) > VARONIS_MAX:
        return None
    read = [_varonis_job(i) for i in items]
    jobs = [j for j in read if j]
    if len(jobs) != len(items) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return [j for j in jobs if ISRAEL.search(j["location"])] or None  # Varonis has jobs in Israel: none is a bad answer


DEEL_HOST = "www.deel.com"
DEEL_JOBS_HOST = "jobs.deel.com"
_NEXT_PUSH = re.compile(r"<script[^>]*>self\.__next_f\.push\((.*?)\)</script>", re.S)
_DEEL_LISTING = '"__component":"product.career-job-listing"'
_DEEL_COUNT = re.compile(r',"jobsCount":(\d+)[,}]')
_ASHBY_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _deel_jobs(page: str) -> list[Any] | None:
    """The `jobs` of the careers page's job listing, or None when the page does not hold them as expected. The page
    is a Next.js page: its data is in the `self.__next_f.push([1, "<text>"])` scripts, whose texts joined are one
    stream, and the listing is the one `product.career-job-listing` component there, `"jobs": [...]` followed by its
    `jobsCount`. The list is trusted only when it is a list and its count is its length."""
    parts = []
    for m in _NEXT_PUSH.finditer(page):
        try:
            chunk = json.loads(m.group(1))
        except ValueError:
            return None
        if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):  # noqa: PLR2004
            parts.append(chunk[1])
    stream = "".join(parts)
    key = '"jobs":'
    start = stream.find(_DEEL_LISTING)
    at = stream.find(key, start) if stream.count(_DEEL_LISTING) == 1 else -1
    if at < 0:
        return None
    try:
        jobs, end = json.JSONDecoder().raw_decode(stream, at + len(key))
    except ValueError:
        return None
    count = _DEEL_COUNT.match(stream, end)
    if not isinstance(jobs, list) or not count or int(count.group(1)) != len(jobs):
        return None
    return jobs


def _deel_job(item: Any) -> Job | None:
    """One job of the listing, or None when it cannot be read: no `attributes` object, an `ashby_id` that is not an
    Ashby id, no title, `all_locations` that is not a non-empty list of place texts, or an `is_listed` that is not
    true (a job the page does not list is not shown). The place is the places of the job that name Israel (a job of
    several, EMEA ones list thirty countries, is kept when one is in Israel), '' when it has none. The link is the
    job's page on the Ashby-hosted board, `https://jobs.deel.com/deel/job-details/<ashby_id>` (its `external_link`
    is the application form, which robots.txt disallows, and is not used)."""
    attrs = item.get("attributes") if isinstance(item, dict) else None
    if not isinstance(attrs, dict):
        return None
    job_id, title, places = attrs.get("ashby_id"), attrs.get("title"), attrs.get("all_locations")
    if not (
        isinstance(job_id, str)
        and _ASHBY_ID.fullmatch(job_id)
        and isinstance(title, str)
        and title.strip()
        and attrs.get("is_listed") is True
        and isinstance(places, list)
        and places
        and all(isinstance(p, str) and p.strip() for p in places)
    ):
        return None
    team = attrs.get("team_name")
    return _job(
        job_id,
        title,
        "; ".join(p.strip() for p in places if ISRAEL.search(p)),
        f"https://{DEEL_JOBS_HOST}/deel/job-details/{job_id}",
        team=team if isinstance(team, str) and team != "$undefined" else "",
        posted=_day(attrs.get("ashby_published_date")),
    )


def deel(slug: str) -> list[Job] | None:
    """slug is 'www.deel.com', the only host this reader serves (a dedicated reader for one company, by the
    maintainer's decision of 2026-11-22: the careers page draws its jobs from its own page data, and the Ashby public
    board of Deel answers an empty list). The page `https://www.deel.com/careers/` is read in one request through
    `common.get` (its robots.txt allows `/careers/`; one request a second, the project's User-Agent), and only its
    page data is read, never a job page. The list is trusted only when `_deel_jobs` finds it whole, it has at most
    DEEL_MAX jobs and no job is unreadable or twice: a partial list would close the jobs it lacks. Of those, the jobs
    with a place in Israel (`common.ISRAEL`) are kept. The page data holds each job's text, which is not read. An
    empty list, or one with no job in Israel, is a failure too (Deel has jobs in Israel, so an empty answer is a
    broken one)."""
    if slug != DEEL_HOST:
        return None
    r = get(f"https://{DEEL_HOST}/careers/")
    if r is None or r.status_code != HTTP_OK:
        return None
    items = _deel_jobs(r.text)
    if items is None or len(items) > DEEL_MAX:
        return None
    read = [_deel_job(i) for i in items]
    jobs = [j for j in read if j]
    if len(jobs) != len(items) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return [j for j in jobs if j["location"]] or None  # Deel has jobs in Israel: none is a bad answer


BULLS_HOST = "bulls-media.com"
_BULLS_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z.\-]*")


def _bulls_hidden(item: Any) -> bool:
    """True for a position the careers page itself does not show: the department `discreet` (a confidential search)."""
    return (
        isinstance(item, dict) and isinstance(item.get("department"), str) and item["department"].lower() == "discreet"
    )


def _bulls_job(item: Any) -> Job | None:
    """One position of the feed, or None when it cannot be read: not an object, an `id` that is not letters, digits,
    dots and dashes, no `name`, or a `city` or `country` that is not text. The place is `City, Country` with the
    country as the feed gives it (a code: `Kiryat Ono, IL`). The department `empty` is the feed's word for none. The
    link is the careers page's own view of the position, `/career/positions/?id=<id>`."""
    if not isinstance(item, dict):
        return None
    job_id, title, city, country = item.get("id"), item.get("name"), item.get("city"), item.get("country")
    if not (
        isinstance(job_id, str)
        and _BULLS_ID.fullmatch(job_id)
        and isinstance(title, str)
        and title.strip()
        and isinstance(city, str)
        and city.strip()
        and isinstance(country, str)
        and country.strip()
    ):
        return None
    team = item.get("department")
    return _job(
        job_id,
        title,
        f"{city.strip()}, {country.strip()}",
        f"https://{BULLS_HOST}/career/positions/?id={job_id}",
        team=team if isinstance(team, str) and team.lower() != "empty" else "",
    )


def bulls_media(slug: str) -> list[Job] | None:
    """slug is 'bulls-media.com', the only host this reader serves (a dedicated reader for one company, by the
    maintainer's decision of 2026-11-22). The careers page draws its positions from its own feed,
    `https://bulls-media.com/career/positions/get-positions.php`, read in one request through `common.get` (the
    site's robots.txt allows everything; one request a second, the project's User-Agent). The list has no paging and
    no total, so it is trusted only when it is a JSON list of at most BULLS_MAX positions and no position is
    unreadable or twice: a partial list would close the jobs it lacks. The positions the page hides (`_bulls_hidden`)
    are not read. Of those, the jobs whose place names Israel, by `common.ISRAEL`, are kept. The feed holds each
    position's text, which is not read, and has no date. An empty list, or one with no job in Israel, is a failure too
    (Bulls Media has jobs in Israel, so an empty answer is a broken one)."""
    if slug != BULLS_HOST:
        return None
    r = get(f"https://{BULLS_HOST}/career/positions/get-positions.php")
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) > BULLS_MAX:
        return None
    items = [i for i in data if not _bulls_hidden(i)]
    read = [_bulls_job(i) for i in items]
    jobs = [j for j in read if j]
    if len(jobs) != len(items) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return [j for j in jobs if ISRAEL.search(j["location"])] or None  # Bulls Media has jobs in Israel: none is bad


ELBIT_HOST = "elbitsystemscareer.com"
ELBIT_AREAS = frozenset({"North", "Sharon", "Shfela", "Center", "South", "Jerusalem Area"})  # regions the site names


def _elbit_job(item: Any) -> Job | None:
    """One job of the file, or None when it cannot be read: not an object, a `jobId` that is not a positive whole
    number or a `jobTitle` that is not text. The file is the recruiting site of Elbit Systems in Israel, so every job
    is in Israel; the place is `<region>, Israel` when `area` is one of the regions the site names (`ELBIT_AREAS`),
    else just `Israel` (a few jobs carry a word such as `Relocation`). The division (`employerName`) is the team. The
    link is the site's own page of the job, `/job/?jid=<jobId>`."""
    if not isinstance(item, dict):
        return None
    job_id, title = item.get("jobId"), item.get("jobTitle")
    if not (isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0):
        return None
    if not (isinstance(title, str) and title.strip()):
        return None
    area, team = item.get("area"), item.get("employerName")
    return _job(
        str(job_id),
        title.strip(),
        f"{area}, Israel" if area in ELBIT_AREAS else "Israel",
        f"https://{ELBIT_HOST}/job/?jid={job_id}",
        team=team.strip() if isinstance(team, str) else "",
        posted=_day(item.get("openDate")),
    )


def elbit(slug: str) -> list[Job] | None:
    """slug is 'elbitsystemscareer.com', the only host this reader serves (a dedicated reader for one company). The
    recruiting site of Elbit Systems is an app that draws its jobs from one file of its own,
    `https://elbitsystemscareer.com/cron/jobs.json`, read in one request through `common.get` (the site's robots.txt
    answers 404, so nothing is disallowed; one request a second, the project's User-Agent). The file has no paging
    and no total, so it is trusted only when it is a JSON list of at most ELBIT_MAX jobs and no job is unreadable or
    twice: a partial list would close the jobs it lacks. The text of each job is in the file and is not read. An
    empty list is a failure too (the site always has jobs, so an empty answer is a broken one)."""
    if slug != ELBIT_HOST:
        return None
    r = get(f"https://{ELBIT_HOST}/cron/jobs.json")
    if r is None or r.status_code != HTTP_OK:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) > ELBIT_MAX:
        return None
    jobs = [_elbit_job(i) for i in data]
    read = [j for j in jobs if j]
    if not read or len(read) != len(jobs) or len({j["job_id"] for j in read}) != len(read):
        return None
    return read


_SUBDOMAIN_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


def _day(stamp: Any) -> str:
    """The date of an ISO time (`2026-11-17T21:03:28.209Z`) as YYYY-MM-DD, or '' when it is missing or not a date."""
    try:
        return datetime.strptime(stamp[:10], "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return ""


def _breezy_place(j: dict[str, Any]) -> str:
    """The places of a job: each location (a job of several lists them in `locations`, else the one in `location`) as
    'city, country', the country written out, so that the place alone shows Israel. A location with no city or country
    name gives its own `name` ('Tel Aviv, IL'); one with nothing readable gives no place."""
    places = j.get("locations") or [j.get("location")]
    out = []
    for loc in places:
        if isinstance(loc, dict):
            country = loc.get("country")
            parts = [loc.get("city"), country.get("name") if isinstance(country, dict) else None]
            text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
            name = loc.get("name")
            out.append(text or (name.strip() if isinstance(name, str) else ""))
    return "; ".join(dict.fromkeys(p for p in out if p))


def _breezy_job(host: str, j: Any) -> Job | None:
    """One job of the list, or None when it lacks an id or a title, or its link is not the board's own page."""
    if not isinstance(j, dict):
        return None
    job_id, title, url = j.get("id"), j.get("name"), j.get("url")
    if not (isinstance(job_id, str) and job_id and isinstance(title, str) and title.strip()):
        return None
    if not (isinstance(url, str) and url.startswith("https://") and host_of(url) == host):
        return None
    dept = j.get("department")
    return _job(
        job_id,
        title,
        _breezy_place(j),
        url,
        team=dept if isinstance(dept, str) else "",
        posted=_day(j.get("published_date")),
    )


def breezy(slug: str) -> list[Job] | None:
    """slug is the lowercase `<slug>` of `<slug>.breezy.hr`. The board page is drawn from `/json`, a public JSON list of
    every open job (the page's own data, for any visitor; the request goes through `common.get`, which enforces
    robots.txt). The list is trusted only when it is a JSON list, every entry reads and no job comes twice. A slug
    that is not a lowercase subdomain, any answer but HTTP 200 (an unknown slug gets 404), a redirect to another host
    and an entry on another host (a board on a custom domain) are failures: a partial list would close the jobs it
    lacks. Every job is returned (the company's `israel_only` decides, as for BambooHR). An empty list is `[]`; run.py
    holds a sudden empty list like a browser crawl. The list has no job text, so there is no description for now."""
    if not _SUBDOMAIN_SLUG.fullmatch(slug):
        return None
    host = f"{slug}.breezy.hr"
    r = get(f"https://{host}/json")
    if r is None or r.status_code != HTTP_OK or host_of(r.url) != host:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    read = [_breezy_job(host, j) for j in data]
    jobs = [j for j in read if j]
    if len(jobs) != len(data) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return jobs


def _recruitee_place(j: dict[str, Any]) -> str:
    """The places of an offer, each as 'city, country' (the country written out, so that the place alone shows Israel),
    from `locations` (an offer of several lists them all), else from the offer's own city and country. A location with
    neither gives its `name`; nothing readable gives no place."""
    places = [x for x in j.get("locations") or [] if isinstance(x, dict)] or [j]
    out = []
    for loc in places:
        parts = [loc.get("city"), loc.get("country")]
        name = loc.get("name")
        text = ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        out.append(text or (name.strip() if isinstance(name, str) else ""))
    return "; ".join(dict.fromkeys(p for p in out if p))


def _recruitee_job(host: str, j: Any) -> Job | None:
    """One offer of the list, or None when it lacks an id or a title, or its link is not the board's own page."""
    if not isinstance(j, dict):
        return None
    job_id, title, url = j.get("id"), j.get("title"), j.get("careers_url")
    if not (isinstance(job_id, int) and not isinstance(job_id, bool) and isinstance(title, str) and title.strip()):
        return None
    if not (isinstance(url, str) and url.startswith("https://") and host_of(url) == host):
        return None
    dept = j.get("department")
    return _job(
        str(job_id),
        title,
        _recruitee_place(j),
        url,
        team=dept if isinstance(dept, str) else "",
        posted=_day(j.get("published_at")),
    )


def recruitee(slug: str) -> list[Job] | None:
    """slug is the lowercase `<slug>` of `<slug>.recruitee.com`. The board page is drawn from `/api/offers/`, a public
    JSON list of every open offer (the page's own data, for any visitor; the request goes through `common.get`, which
    enforces robots.txt: the board's own disallows only `/v/`). The list is trusted only when it is `{"offers": [...]}`,
    every entry reads and no offer comes twice. A slug that is not a lowercase subdomain, any answer but HTTP 200 (an
    unknown slug gets 404), a redirect to another host and an offer on another host (a board on a custom domain) are
    failures: a partial list would close the jobs it lacks. Every offer is returned (the company's `israel_only`
    decides). An empty list is `[]`; run.py holds a sudden empty list like a browser crawl. The offer text is not
    read."""
    if not _SUBDOMAIN_SLUG.fullmatch(slug):
        return None
    host = f"{slug}.recruitee.com"
    r = get(f"https://{host}/api/offers/")
    if r is None or r.status_code != HTTP_OK or host_of(r.url) != host:
        return None
    try:
        data = r.json()["offers"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    read = [_recruitee_job(host, j) for j in data]
    jobs = [j for j in read if j]
    if len(jobs) != len(data) or len({j["job_id"] for j in jobs}) != len(jobs):
        return None
    return jobs


_BOARD_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_TEAMTAILOR_JOB_ID = re.compile(r"/jobs/(\d+)(?:-|$)")


def _teamtailor_job(host: str, item: ET.Element, ns: dict[str, str]) -> Job | None:
    """One job of the feed, or None when it lacks a title or a link, or the link is no job of the board's host."""
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    found = _TEAMTAILOR_JOB_ID.search(urlparse(link).path)
    if not (title and found and host_of(link) == host and link.startswith("https://")):
        return None
    places = [
        ", ".join(dict.fromkeys(x for x in (loc.findtext(f"tt:{f}", "", ns).strip() for f in ("city", "country")) if x))
        for loc in item.findall("tt:locations/tt:location", ns)
    ]
    return _job(
        found[1],
        title,
        "; ".join(dict.fromkeys(p for p in places if p)),
        link,
        team=item.findtext("tt:department", "", ns),
        posted=_avature_posted(item.findtext("pubDate")),
    )


def teamtailor(slug: str) -> list[Job] | None:
    """slug is the host of a Teamtailor career site: `<name>.teamtailor.com` or the company's own domain. Read from its
    public RSS feed `https://<host>/jobs.rss` (its `robots.txt` allows it), one request: every open job with its
    title, its place (city and country of each location, none for a remote job) and its department.
    Only a feed of the board's own host that reads cleanly is a list: a closed account answers 404, an own domain may
    redirect elsewhere, and a job that cannot be read or is there twice
    means a partial list, which would close the stored jobs."""
    if not _BOARD_HOST.fullmatch(slug):
        return None
    r = get(f"https://{slug}/jobs.rss")
    if r is None or r.status_code != HTTP_OK or host_of(r.url) != slug:
        return None
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return None
    if root.tag != "rss" or root.find("channel") is None:
        return None
    ns = {"tt": "https://teamtailor.com/locations"}
    out = [_teamtailor_job(slug, item, ns) for item in root.findall("./channel/item")]
    jobs = [j for j in out if j]
    unique = len({j["job_id"] for j in jobs}) == len(jobs)
    # jobs and not one place among them: the place fields changed, and a list the Israel filter empties would close all
    return jobs if len(jobs) == len(out) and unique and (not jobs or any(j["location"] for j in jobs)) else None


def _pinpoint_job(host: str, j: Any) -> Job | None:
    """One posting of the list, or None when it has no id or title, or its link is not on the board's own host."""
    if not isinstance(j, dict) or not j.get("id") or not (j.get("title") or "").strip():
        return None
    url = j.get("url") or ""
    if not (isinstance(url, str) and url.startswith("https://") and host_of(url) == host):
        return None
    where, job = j.get("location") or {}, j.get("job") or {}
    place = ", ".join(dict.fromkeys(x for x in (where.get("city"), where.get("province"), where.get("name")) if x))
    return _job(j["id"], j["title"], place, url, team=(job.get("department") or {}).get("name"))


def _pinpoint_jobs(host: str, postings: list[Any]) -> list[Job] | None:
    try:
        out = [_pinpoint_job(host, j) for j in postings]
    except (AttributeError, TypeError):  # a field of another type than the board's own
        return None
    jobs = [j for j in out if j]
    unique = len({j["job_id"] for j in jobs}) == len(jobs)
    # jobs and not one place among them: the place fields changed, and a list the Israel filter empties would close all
    return jobs if len(jobs) == len(out) and unique and (not jobs or any(j["location"] for j in jobs)) else None


def pinpoint(slug: str) -> list[Job] | None:
    """slug is the host of a Pinpoint career site: `<name>.pinpointhq.com` or the company's own domain. Read from the
    board's public list `https://<host>/postings.json`, one request (its `robots.txt` allows it): every open posting
    with its title, place (city, province and place name, no country: Pinpoint does not give one) and department.
    Only the board's own JSON that reads cleanly is a list: anything else is None, never a partial list."""
    if not _BOARD_HOST.fullmatch(slug):
        return None
    r = get(f"https://{slug}/postings.json")
    if r is None or r.status_code != HTTP_OK or host_of(r.url) != slug:
        return None
    try:
        postings = r.json()["data"]
    except (ValueError, KeyError, TypeError):  # not JSON, or not the board's list
        return None
    if not isinstance(postings, list):
        return None
    return _pinpoint_jobs(slug, postings)


_JOBVITE_HOST = "jobs.jobvite.com"
_JOBVITE_HEADING = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S | re.I)
_JOBVITE_ROW = re.compile(
    r'<td class="jv-job-list-name">\s*<a href="([^"]*)">(.*?)</a>\s*</td>\s*'
    r'<td class="jv-job-list-location">(.*?)</td>',
    re.S | re.I,
)
_JOBVITE_ANY_LINK = re.compile(r'class="jv-job-list-name"', re.I)
_JOBVITE_MANY = re.compile(r"(\d+) Locations?", re.I)  # what the list shows for a job of several places
_JOBVITE_META = re.compile(r'<p class="jv-job-detail-meta">(.*?)</p>', re.S | re.I)
_JOBVITE_SEPARATOR = re.compile(r"<span[^>]*jv-inline-separator[^>]*></span>", re.I)


def _jobvite_places(href: str, count: int) -> str | None:
    """The places of a job whose list row says only 'N Locations', read from its own page (the facts line: the
    department, then each place, one separator between), or None when the page does not read or does not list N places
    (a place dropped could be the one in Israel)."""
    r = get(f"https://{_JOBVITE_HOST}{href}")
    meta = _JOBVITE_META.search(r.text) if r is not None and r.status_code == HTTP_OK else None
    places = [_page_text(x) for x in _JOBVITE_SEPARATOR.split(meta[1])[1:]] if meta else []
    if len(places) != count or not all(places):
        return None
    return "; ".join(dict.fromkeys(places))


def jobvite(slug: str) -> list[Job] | None:
    """slug is the `<slug>` of `jobs.jobvite.com/<slug>` (its `robots.txt` answers 404, so no rule applies; one request
    a second and the project's User-Agent do). Jobvite has no public feed for it: the board page lists the jobs as HTML,
    under one heading per department, each with a link and the place as the company wrote it (a job of several places
    shows only '4 Locations': its own page is read for them, one more request a job). Only a page that reads
    cleanly is a list: a board that moved (Shutterfly's goes to another host), a changed layout, a job that cannot be
    read, a job twice or a page with no job at all is None, because an empty list would close every stored job."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        return None
    r = get(f"https://{_JOBVITE_HOST}/{slug}")
    if r is None or r.status_code != HTTP_OK or host_of(r.url) != _JOBVITE_HOST:
        return None
    board = r.text
    out: list[Job] = []
    parts = re.split(r'<table class="jv-job-list"', board)
    for before, chunk in pairwise(parts):
        team = _page_text((_JOBVITE_HEADING.findall(before) or [""])[-1])  # the heading just above the table
        for href, name, place in _JOBVITE_ROW.findall(chunk.partition("</table>")[0]):
            job_id = href.rstrip("/").rpartition("/")[2]
            title = _page_text(name)
            if not (job_id and title and href.startswith(f"/{slug}/job/")):
                return None
            where = _page_text(place)
            many = _JOBVITE_MANY.fullmatch(where)
            if many:
                where = _jobvite_places(href, int(many[1])) or ""
                if not where:
                    return None  # a job whose places we cannot read might be in Israel: the list is not complete
            out.append(_job(job_id, title, where, f"https://{_JOBVITE_HOST}{href}", team=team))
    # a job the row markup did not yield (a changed layout) means the list is not complete
    unique = len({j["job_id"] for j in out}) == len(out)
    # not one place among the jobs: the place cells changed, and a list the Israel filter empties would close all
    complete = len(_JOBVITE_ANY_LINK.findall(board)) == len(out) and unique and any(j["location"] for j in out)
    return out if out and complete else None


ADAPTERS: dict[str, Callable[[str], list[Job] | None]] = {
    "greenhouse_url": greenhouse_url,
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
    "comeet": comeet,
    "workday": workday,
    "jazzhr": jazzhr,
    "bamboohr": bamboohr,
    "oraclehcm": oraclehcm,
    "amazon": amazon,
    "phenom": phenom,
    "comeet_page": comeet_page,
    "avature": avature,
    "eightfold": eightfold,
    "jibe": jibe,
    "varonis": varonis,
    "breezy": breezy,
    "teamtailor": teamtailor,
    "pinpoint": pinpoint,
    "jobvite": jobvite,
    "recruitee": recruitee,
    "deel": deel,
    "bulls_media": bulls_media,
    "elbit": elbit,
}
