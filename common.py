import csv
import datetime
import re
import threading
import time
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import requests

VERSION = "0.41.0"  # equal to the version in package.json and pyproject.toml (a test checks it)
SITE_URL = "https://ravidorr.github.io/misra/"
CONTACT = "raanan@avidor.org"
ROBOTS_TOKEN = "Misra"  # the product token that robots.txt groups name
# The one User-Agent of every request the project makes: it says who we are, where to read about us and how to write.
USER_AGENT = f"{ROBOTS_TOKEN}/{VERSION} (+{SITE_URL}; {CONTACT})"
_s = requests.Session()
_s.headers["User-Agent"] = USER_AGENT
_last: dict[str, float] = {}
_lock = threading.Lock()
DELAY = 1.0  # seconds between requests to the same host
DROP_HOLD_DAYS = 3  # a sharp drop in a browser crawl is held on this many days in a row; the next day believes it
DROP_NOTE = "sharp drop"  # crawl log note prefix of a held crawl
DROP_BELIEVED = f"drop believed after {DROP_HOLD_DAYS} held days"  # note prefix of the crawl that believed one

# robots.txt (the lawyer's answer, PLAN.md step 6.6). One switch: False logs what would be skipped and skips nothing.
ROBOTS_ENFORCED = True
ROBOTS_NOTE = "robots.txt does not allow it"  # crawl log note prefix of a company skipped because of robots.txt
ROBOTS_CLOSE_DAYS = 7  # a company skipped by robots.txt on every run for this many days gets its open jobs closed
ROBOTS_TIMEOUT = 10
ROBOTS_MAX_BYTES = 500 * 1024  # RFC 9309: a parser reads at least the first 500 KiB
HTTP_OK = 200
HTTP_SERVER_ERROR = 500
# Hosts and paths a service publishes for programmatic use (an ATS's public job-board API). A blanket `Disallow: /`
# written for crawlers of pages does not cover them; a rule that names the API path does.
# The mechanism for a host that has WRITTEN permission to be called despite a blanket `Disallow: /`. It is empty on
# purpose. api.smartrecruiters.com was the only entry: it answers `User-agent: *` with `Disallow: /` and allows only
# LinkedInBot under /v1/companies/, its documentation says nothing about aggregators, and the maintainer has asked
# SmartRecruiters for permission. Until that is given the host goes through the robots gate like any other, and its five
# companies (Armis, Kaltura, Atera, Nexar, ServiceNow) are skipped. A written permission from the host's owner is the
# only reason to add a host back here. See CONTRIBUTING.md, "How the crawler behaves".
ROBOTS_API_EXCEPTION_HOSTS: frozenset[str] = frozenset()
API_HOSTS = frozenset({"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}) | (
    ROBOTS_API_EXCEPTION_HOSTS
)
API_PATHS = ("/api/v1/widget/", "/wday/cxs/", "/hcmRestApi/", "/widgets")

EXCLUDED = "data/excluded_companies.csv"
EXCLUDED_COLUMNS = ["company", "reason", "date_added"]
EXCLUDED_MIN_NAME = 2  # a shorter name is a typo, not a company
EXCLUDED_MAX = 200
MAX_PURGE_COMPANIES = 3  # a run deletes the data of at most this many companies; more means a mistake in the list


class ExclusionError(Exception):
    """The exclusion list cannot be read or cannot be trusted; the message says which row and why."""


def load_excluded(path: str = EXCLUDED) -> list[str]:
    """The company names on the exclusion list (data/excluded_companies.csv: company, reason, date_added), stripped.

    A company on the list is never crawled and its data is deleted. A list that is missing, has other columns, holds a
    blank or very short name, a repeated name, a reason or date that is missing or not a date, or is very long raises
    ExclusionError: a list nobody can trust must stop the run, not be skipped."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:  # -sig: Excel writes a byte order mark
            reader = csv.DictReader(fh)
            if reader.fieldnames != EXCLUDED_COLUMNS:
                raise ExclusionError(f"{path}: the header must be {','.join(EXCLUDED_COLUMNS)}")
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ExclusionError(f"{path}: cannot be read ({type(exc).__name__})") from None
    if len(rows) > EXCLUDED_MAX:
        raise ExclusionError(f"{path}: more than {EXCLUDED_MAX} rows")
    names: list[str] = []
    for number, row in enumerate(rows, start=2):
        name = (row["company"] or "").strip()
        if len(name) < EXCLUDED_MIN_NAME:
            raise ExclusionError(f"{path} line {number}: the company name is blank or shorter than {EXCLUDED_MIN_NAME}")
        if name.casefold() in {n.casefold() for n in names}:
            raise ExclusionError(f"{path} line {number}: {name!r} is on the list twice")
        if not (row["reason"] or "").strip():
            raise ExclusionError(f"{path} line {number}: the reason is blank")
        try:
            datetime.date.fromisoformat((row["date_added"] or "").strip())
        except ValueError:
            raise ExclusionError(f"{path} line {number}: date_added is not a YYYY-MM-DD date") from None
        names.append(name)
    return names


def is_excluded(name: str, excluded: list[str]) -> bool:
    """Whether a company is on the list. Case does not matter here: skipping too much is the safe error."""
    return name.strip().casefold() in {n.casefold() for n in excluded}


def host_of(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*", r"\1", url)


def reserve_slot(host: str) -> float:
    """Claim the next turn on a host and return the seconds to wait before using it. Every client of the project (the
    adapters' requests and the browser crawl) takes its turns here, so one host sees at most one request a DELAY."""
    with _lock:
        wait = _last.get(host, 0) + DELAY - time.time()
        _last[host] = max(time.time(), _last.get(host, 0) + DELAY)
    return wait


def _send(method: str, url: str, **kw: Any) -> requests.Response | None:
    wait = reserve_slot(host_of(url))
    if wait > 0:
        time.sleep(wait)
    kw.setdefault("timeout", 20)
    try:
        return cast("requests.Response", getattr(_s, method)(url, **kw))
    except requests.RequestException:
        return None


_Rule = tuple[bool, str]  # (allow, path pattern)
_robots: dict[str, list[_Rule] | None] = {}  # origin -> our rules; None when there is no robots.txt to follow
_robots_locks: dict[str, threading.Lock] = {}
_robots_state = threading.local()  # per thread: the host that blocked the last request, if any
ROBOTS_WOULD_BLOCK: dict[str, str] = {}  # host -> first url, filled whether or not robots.txt is enforced


def _group_rules(text: str) -> list[_Rule]:
    """The Allow and Disallow rules that apply to ROBOTS_TOKEN: the groups that name it, else the `*` groups (RFC 9309).
    Groups for the same agent add up. An empty Disallow is no rule. Lines that are not directives are ignored."""
    groups: list[tuple[set[str], list[_Rule]]] = []
    agents_open = False
    for raw in text.lstrip("﻿").splitlines():
        key, sep, value = raw.split("#", 1)[0].strip().partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not agents_open:
                groups.append((set(), []))
            groups[-1][0].add(value.lower())
            agents_open = True
        elif key in ("allow", "disallow"):
            agents_open = False
            if value and groups:
                groups[-1][1].append((key == "allow", value))
    ours = [rules for agents, rules in groups if ROBOTS_TOKEN.lower() in agents]
    if not ours:
        ours = [rules for agents, rules in groups if "*" in agents]
    return [rule for rules in ours for rule in rules]


def _matches(pattern: str, path: str) -> bool:
    """Does a robots.txt pattern match the start of the path? `*` is any run of characters, a final `$` anchors the
    end. The pieces between the stars are found one after the other, leftmost first: that is exact for stars alone, and
    it cannot blow up on a hostile pattern the way a regular expression with many stars can."""
    anchored = pattern.endswith("$")
    parts = unquote(pattern[:-1] if anchored else pattern).split("*")
    if not path.startswith(parts[0]):
        return False
    if len(parts) == 1:
        return path == parts[0] if anchored else True
    pos = len(parts[0])
    for part in parts[1:-1]:
        found = path.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    last = parts[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= pos
    return path.find(last, pos) >= 0


def _api_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "") in API_HOSTS or parts.path.startswith(API_PATHS)


def rules_allow(rules: list[_Rule], url: str, api: bool = False) -> bool:
    """RFC 9309 matching: the longest matching pattern wins, Allow wins a tie, no match means allowed. For an API url a
    rule that blocks the whole site (`/` or `/*`) is left out: it is written for pages, and the API is published for
    programs. /robots.txt itself is always allowed."""
    parts = urlsplit(url)
    path = unquote((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
    if path == "/robots.txt":
        return True
    best_length, allowed = -1, True
    for allow, pattern in rules:
        if api and not allow and pattern in ("/", "/*"):
            continue
        if _matches(pattern, path) and (len(pattern) > best_length or (len(pattern) == best_length and allow)):
            best_length, allowed = len(pattern), allow
    return allowed


def _load_robots(origin: str) -> list[_Rule] | None:
    """Fetch and parse origin/robots.txt. A request that fails, a 404 or any other 4xx, and a 5xx all mean "no rules"
    (a failure and a 5xx are logged). The fetch takes its turn like any request, so it is as polite as a crawl."""
    r = _send("get", f"{origin}/robots.txt", timeout=ROBOTS_TIMEOUT)
    if r is None:
        print(f"robots.txt: {origin} did not answer, treating everything as allowed")
        return None
    if r.status_code >= HTTP_SERVER_ERROR:
        print(f"robots.txt: {origin} answered HTTP {r.status_code}, treating everything as allowed")
        return None
    if r.status_code != HTTP_OK:
        return None
    return _group_rules(r.text[:ROBOTS_MAX_BYTES])


def robots_allows(url: str) -> bool:
    """May we fetch this url under its host's robots.txt? Fetched once per host and run. Never raises."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return True
    origin = f"{parts.scheme}://{parts.netloc}"
    with _lock:
        guard = _robots_locks.setdefault(origin, threading.Lock())
    with guard:  # one fetch per host even when several threads ask at once
        if origin not in _robots:
            _robots[origin] = _load_robots(origin)
    rules = _robots[origin]
    if rules is None:
        return True
    allowed = rules_allow(rules, url, api=_api_url(url))
    if not allowed:
        ROBOTS_WOULD_BLOCK.setdefault(parts.netloc, url)
    return allowed


def robots_gate(url: str) -> bool:
    """True when the request may go ahead. A disallowed url is refused only while ROBOTS_ENFORCED, and then the thread
    is marked so the caller can tell a skip from a failure (see robots_blocked)."""
    if robots_allows(url):
        return True
    if not ROBOTS_ENFORCED:
        return True
    _robots_state.host = host_of(url)
    return False


def reset_robots_block() -> None:
    _robots_state.host = ""


def robots_blocked() -> str:
    """The host whose robots.txt refused a request of this thread since reset_robots_block, or ''."""
    return cast("str", getattr(_robots_state, "host", ""))


def _request(method: str, url: str, **kw: Any) -> requests.Response | None:
    # the address that is actually fetched, query parameters included, is the one robots.txt is asked about
    full = requests.Request(method, url, params=kw.get("params")).prepare().url or url
    if not robots_gate(full):
        return None
    return _send(method, url, **kw)


def get(url: str, **kw: Any) -> requests.Response | None:
    return _request("get", url, **kw)


def post(url: str, **kw: Any) -> requests.Response | None:
    return _request("post", url, **kw)


def slug_candidates(name: str, domain: str) -> list[str]:
    base = domain.split(".", 1)[0].lower()
    n = re.sub(r"[^a-z0-9]+", "", name.lower())
    nd = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out: list[str] = []
    for s in (base, n, nd, base + "hq", base + "inc"):
        if s and s not in out:
            out.append(s)
    return out


_PLACES = re.compile(
    r"israel|tel[ -]?aviv|jerusalem|haifa|herzliya|ra'?anana|petah|petach|\bramat|netanya|rehovot|yokneam|kfar"
    r"|beer[ -]?sheva|holon|rishon|hod hasharon|airport city|migdal|\bISR\b",
    re.I,
)
_US_WORDS = re.compile(
    r"united states|\busa\b|illinois|chicago|naperville|schaumburg|springfield|oak brook|rosemont", re.I
)
_US_CODES = re.compile(
    r"\bUS\b|,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV"
    r"|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY)\b"
)
_BARE_IL = re.compile(r"\bIL\b")


class _IsraelMatcher:
    """True for locations in Israel. A place name decides. The bare country code `IL` also counts ("Caesarea, IL",
    "IL: Tel Aviv") unless the location points to the US, where `IL` is Illinois ("Chicago, IL", "US-IL-Remote")."""

    def names_a_place(self, text: str) -> bool:
        """True when text names an Israeli place or the country itself, never on the bare code `IL` alone."""
        return bool(_PLACES.search(text))

    def search(self, text: str) -> bool:
        if _PLACES.search(text):
            return True
        if not _BARE_IL.search(text):
            return False
        return not (_US_WORDS.search(text) or _US_CODES.search(text))


ISRAEL = _IsraelMatcher()
