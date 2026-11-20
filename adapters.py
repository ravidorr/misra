"""One adapter per ATS. fetch(slug) -> list of job dicts, or None on failure
(None means 'could not crawl'; [] means 'crawled, no jobs')."""

import html
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

from common import ISRAEL, get, host_of, post, reset_robots_block
from descriptions import DETAIL_CAP, HELD, attach, html_to_text, jsonld_description, plain_text, sections

HTTP_OK = 200
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
    rf'<a\b[^>]*?\bhref="([^"]*/co/([a-z0-9-]+)/({_COMEET_UID})/[a-z0-9-]+/?[^"]*)"[^>]*>(.*?)</a>', re.S | re.I
)
_COMEET_ANY_UID = re.compile(rf"/co/[^/\"'\s<>]+/({_COMEET_UID})/", re.I)  # wider than the card link on purpose
_COMEET_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_COMEET_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.S | re.I)
_COMEET_NAME = re.compile(r'class="comeet-position-name"[^>]*>(.*?)(?:<br|<span|</div>)', re.S | re.I)
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


def browser_description(slug: str, job: Job) -> tuple[str, dict[str, Any]] | None:
    """The text of a job found by the browser crawl: an Eightfold job asks its own `position_details` JSON (the call the
    career page makes, under the `/api/pcsx` path its search already uses); a job of a page in JSONLD_PAGES asks its
    page and reads only the JobPosting JSON-LD. Never page text. None on any failure."""
    detail = job.get("detail_url")
    r = get(detail, headers={"Accept": "application/json"}) if detail else get(job["url"])
    if r is None or r.status_code != HTTP_OK:
        return None
    if detail:
        try:
            return html_to_text(r.json()["data"]["jobDescription"]), {}
        except (ValueError, KeyError, TypeError):
            return None
    posting = _job_posting(r.text)
    return jsonld_description(posting) if posting else None


def _browser_wants_page(slug: str, job: Job) -> bool:
    if job.get("detail_url"):
        return True
    return slug in JSONLD_PAGES and host_of(str(job.get("url"))) == host_of(slug)  # the career page's own host only


# Adapters whose list has no text: one request per job page, `fetch(slug, job) -> (text, details) | None`. `WANTS`
# limits an ATS to the jobs worth a request.
DETAILS: dict[str, Callable[[str, Job], tuple[str, dict[str, Any]] | None]] = {
    "workday": workday_description,
    "browser": browser_description,
}
WANTS: dict[str, Callable[[str, Job], bool]] = {"browser": _browser_wants_page}


def add_descriptions(  # noqa: PLR0913, PLR0917
    ats: str, slug: str, company: str, jobs: list[Job], held: set[tuple[str, str]], day: int = 0
) -> tuple[int, int, int]:
    """Gives the jobs of a company whose list carries no text their description, one request per job page, and
    returns (requested, got a text, deferred). Only jobs without a text are asked: one Supabase already holds (`held`,
    left by the pull) is marked and not fetched again. At most DETAIL_CAP are asked per company and run, in a window
    that moves with `day` so a page that never answers cannot starve the others, and five misses in a row end the
    company's run (a host that refuses is left alone). The rest wait for the next run. A page that fails, is refused
    by robots.txt or has no text gives no description and changes nothing else: this never fails the list, and a
    refusal does not make the company a robots skip (the list was crawled)."""
    fetch_one = DETAILS.get(ats)
    if fetch_one is None:
        return 0, 0, 0
    wants = WANTS.get(ats)
    todo: list[Job] = []
    for job in jobs:
        if job.get("description") or (wants and not wants(slug, job)):
            continue
        if (company, str(job.get("job_id"))) in held:
            job[HELD] = True
        else:
            todo.append(job)
    if todo:  # a different window each day, by the fewest pages a run can end after, so no job is starved
        start = day * DETAIL_FAIL_STREAK % len(todo)
        todo = todo[start:] + todo[:start]
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
    return asked, got, len(todo) - asked


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
    digits, no title, `jobLocations` that is not a list, or a count of locations that is not the
    number listed. The place is `City, Country` of each location, joined by '; ' (a job of several lists every one).
    The link is the careers site's own page for the job, `/careers?p=job/<id>`, the address
    its own list links to (the `app.jobvite.com` links of the answer are the applicant system and are not used)."""
    if not isinstance(item, dict):
        return None
    job_id, title, places = item.get("eId"), item.get("title"), item.get("jobLocations")
    if not (isinstance(job_id, str) and job_id.isalnum() and isinstance(title, str) and title.strip()):
        return None
    if not isinstance(places, list) or item.get("locationsCount") != len(places):
        return None
    where = [
        ", ".join(x for x in (p.get("city"), p.get("country")) if isinstance(x, str) and x.strip())
        for p in places
        if isinstance(p, dict)
    ]
    return _job(
        job_id,
        title,
        "; ".join(w for w in where if w),
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
}
