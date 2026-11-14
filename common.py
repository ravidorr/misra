import re
import threading
import time
from typing import Any, cast

import requests

UA = "IL-JobWatch/0.1 (personal job search; low-volume)"
_s = requests.Session()
_s.headers["User-Agent"] = UA
_last: dict[str, float] = {}
_lock = threading.Lock()
DELAY = 1.0  # seconds between requests to the same host
DROP_HOLD_DAYS = 3  # a sharp drop in a browser crawl is held on this many days in a row; the next day believes it
DROP_NOTE = "sharp drop"  # crawl log note prefix of a held crawl
DROP_BELIEVED = f"drop believed after {DROP_HOLD_DAYS} held days"  # note prefix of the crawl that believed one


def _request(method: str, url: str, **kw: Any) -> requests.Response | None:
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    with _lock:
        wait = _last.get(host, 0) + DELAY - time.time()
        _last[host] = max(time.time(), _last.get(host, 0) + DELAY)
    if wait > 0:
        time.sleep(wait)
    kw.setdefault("timeout", 20)
    try:
        return cast("requests.Response", getattr(_s, method)(url, **kw))
    except requests.RequestException:
        return None


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
