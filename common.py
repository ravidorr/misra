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
