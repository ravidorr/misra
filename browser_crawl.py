"""Tier 3: generic crawler for career pages without a public ATS API. Real browser (Playwright).
Extracts JSON-LD JobPosting, else job-looking links. Returns list of jobs, or None if blocked/failed."""

import asyncio
import contextlib
import json
import os
import re
from typing import Any, cast

from playwright.async_api import async_playwright

from discover import ISRAEL

JOBLINK = re.compile(
    r"/(jobs?|positions?|careers?|openings?|vacanc\w+|job-?post\w*)/[^/?#]{4,}|[?&]job_id=[^&#]+", re.I
)
JOB_ID = re.compile(r"[?&](job_id=[^&#]+)", re.I)  # Tower: /job-description/?job_id=9672, the query is the job
NOT_JOB = re.compile(
    r"/(department|departments|category|team|teams|locations?|working-at|life-at|benefits|students?|early-careers?|login|search|saved|talent-network)\b|#",
    re.I,
)
NEXT_SELS = [
    'a[title="Next" i]',
    "a.link-next",
    'a[aria-label="Next page" i]',
    'button[title="Next" i]',
    'body a[rel="next"]',
    'a[aria-label="Next" i]',
    'button[aria-label="Next" i]',
    "li.next a",
    ".pagination-next",
    'a:text-is("Next")',
    'button:text-is("Next")',
    'a:text-is("Next >")',
]
EF_SEARCH = "/api/pcsx/search"  # Eightfold career sites: the page's own search request
EF_NEXT = 'button[aria-label="Next jobs" i]'
EF_MAX_PAGES = 60  # ten jobs a page
SETTLE_POLLS = 32  # at 250 ms: wait up to 8 s for a pagination click to show new job links
MORE = re.compile(r"load more|show more|see more|view more|more jobs|more positions", re.I)
LANGS = {
    "english",
    "español",
    "français",
    "português",
    "deutsch",
    "italiano",
    "日本語",
    "한국어",
    "中文",
    "русский",
    "עברית",
    "polski",
    "nederlands",
}
BLOCKED = re.compile(  # titles of bot-protection pages, some of which answer HTTP 200 with no jobs on them
    r"just a moment|attention required|security checkpoint|access denied|error 429|verify you are human"
    r"|captcha|request unsuccessful|access to this page has been denied|pardon our interruption",
    re.I,
)
EXE = os.environ.get("PW_CHROMIUM") or (
    "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


HTTP_OK = 200
HTTP_ERROR = 400
CARD_MAX = 600  # the card script never climbs to an ancestor with more text than this
CARD_JS = r"""(els, cfg) => {
    const job = new RegExp(cfg.job, 'i'), skip = new RegExp(cfg.skip, 'i'), id = new RegExp(cfg.id, 'i');
    const button = /^(apply|view|read|learn|see|more|details|save|share)\b/i;
    const href = a => (typeof a.href === 'string' ? a.href : '');
    const key = h => {
        const m = id.exec(h);
        return h.split('?')[0].replace(/\/+$/, '') + (m ? '?' + m[1] : '');
    };
    const text = n => (n.innerText || '').trim();
    const many = n => {  // does n hold the title links of two or more jobs?
        const seen = new Set();
        for (const a of n.querySelectorAll('a[href]')) {
            const h = href(a), t = text(a);
            if (job.test(h) && !skip.test(h) && t && !button.test(t)) {
                seen.add(key(h));
                if (seen.size > 1) return true;
            }
        }
        return false;
    };
    return els.map(e => {
        let n = e;
        for (let i = 0; i < 4 && n.parentElement; i++) {
            const up = n.parentElement;
            if ((up.innerText || '').length > cfg.max || many(up)) break;
            n = up;
        }
        return [href(e), text(e), text(n).slice(0, cfg.max + 1)];
    });
}"""  # the card: the widest ancestor, up to 4 levels, with one job's title link and a card's worth of text
CARD_ARGS = {"job": JOBLINK.pattern, "skip": NOT_JOB.pattern, "id": JOB_ID.pattern, "max": CARD_MAX}
CARD_LINE_MAX = 120  # a longer card line is a job description, not a title, place or team
CARD_BEFORE = 2  # lines above the title that can still be its place or team
CARD_AFTER = 3  # and below it
CARD_CHROME = frozenset({"save job", "share job", "apply", "apply now"})
BUTTON_LINES = frozenset({"view position", "view role", "view job", "view full details", "learn more", "read more"})
LINK_NOISE = CARD_CHROME | BUTTON_LINES | {"location", "location:", "department"}

WHY: dict[str, str] = {}  # url -> reason, filled for failed or empty crawls, printed by run.py


ROLE_WORD = re.compile(
    r"\b(engineer|developer|manager|analyst|specialist|director|lead|designer|architect|researcher|scientist"
    r"|administrator|executive|representative|associate|intern|internship|student|officer|technician|consultant"
    r"|account|head|vp|president|coordinator|recruiter|writer|marketer|operator|assistant|trainee|graduate"
    r"|programmer|tester|devops|sre|cto|ciso|cfo|coo)\b",
    re.I,
)
CARD_NOISE = {
    "full-time",
    "full time",
    "part-time",
    "part time",
    "contract",
    "temporary",
    "permanent",
    "view role",
    "view job",
    "view position",
    "read more",
    "learn more",
    "see more",
    "apply",
    "apply now",
    "see open positions",
    "see careers",
    "skip to main content",
    "save job",
    "share job",
    "location",
    "location:",
    "department",
}
_COUNTRIES = (
    "israel|united states|usa|united kingdom|uk|germany|india|canada|france|spain|poland|romania|brazil|singapore"
    "|japan|australia|netherlands|ireland|italy|mexico|argentina|colombia|south africa|south korea|korea|china"
    "|hong kong|ukraine|serbia|bulgaria|portugal|sweden|denmark|norway|finland|switzerland|austria|belgium"
    "|czech republic|turkey|uae|united arab emirates|philippines|vietnam|thailand|malaysia|indonesia|new zealand"
    "|chile|peru|costa rica|greece|hungary|lithuania|estonia|croatia|egypt|taiwan"
)
_CODES = (
    "IL|US|UK|GB|DE|IN|CA|FR|ES|PL|RO|BR|SG|JP|AU|NL|IE|IT|MX|AR|CO|ZA|KR|CN|HK|UA|RS|BG|PT|SE|DK|NO|FI|CH|AT|BE|CZ"
    "|TR|AE|PH|VN|TH|MY|ID|NZ|CL|PE|CR|GR|HU|LT|EE|HR|ISR|DEU|IRL|ITA|FRA|USA|GBR|IND|CAN"
    "|AL|AK|AZ|AR|CO|CT|DC|DE|FL|GA|HI|IA|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI"
    "|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY"
)
_BARE_CODE = re.compile(rf"(?:{_CODES})")
_ENDS_IN_PLACE = re.compile(rf",\s*(?:(?:{_CODES})|(?:{_COUNTRIES}))\s*$")
_ENDS_IN_COUNTRY = re.compile(rf",\s*(?:{_COUNTRIES})\s*$", re.I)
_REMOTE = re.compile(r"\b(remote|hybrid|on-?site|worldwide|anywhere)\b", re.I)
_COUNTRY_ONLY = re.compile(rf"(?:{_COUNTRIES})", re.I)
MAX_LOCATION_LEN = 60
MAX_TEAM_WORDS = 2
MAX_REMOTE_WORDS = 4


def _is_location(line: str) -> bool:
    """A card line that names a place: "TEL AVIV, ISRAEL", "Dallas, US", "REMOTE (US)", "IL". A line with a job-role
    word is a title even when it mentions a town ("Customer Engineer - Kiryat Gat")."""
    if len(line) > MAX_LOCATION_LEN or ROLE_WORD.search(line):
        return False
    text = line.strip()
    return bool(
        ISRAEL.search(text)
        or _BARE_CODE.fullmatch(text)
        or _COUNTRY_ONLY.fullmatch(text)
        or _ENDS_IN_PLACE.search(text)
        or _ENDS_IN_COUNTRY.search(text)
        or (_REMOTE.search(text) and len(text.split()) <= MAX_REMOTE_WORDS)
    )


def _parse_card(lines: list[str]) -> tuple[str, str, str] | None:
    """Title, location and team from the text lines of a job card, in whatever order the site prints them
    ([location, title, type], [team, title, location], [title, location]). None when there is no title."""
    # split table cells ("title<tab>team") and "Cyprus · Full-time" into their parts, and collapse padding
    parts = [p for ln in lines for p in re.split(r"\t+|\s+·\s+", ln)]
    lines = [" ".join(p.split()) for p in parts]
    lines = [ln for ln in lines if ln and ln.lower() not in CARD_NOISE]
    places = list(dict.fromkeys(ln for ln in lines if _is_location(ln)))
    rest = list(dict.fromkeys(ln for ln in lines if ln not in places))  # a table row repeats its title cell
    if not rest:
        return None
    title, others = rest[0], rest[1:]
    # a title wrapped over two lines ("Senior Backend" / "Engineer"): a lone role word after a short line with no
    # role word of its own finishes that line, it is not a title under a team name. An acronym ("CISO") stays a title.
    if (
        others
        and len(title.split()) <= MAX_TEAM_WORDS
        and not ROLE_WORD.search(title)
        and ROLE_WORD.fullmatch(others[0])
        and not others[0].isupper()
    ):
        title, others = f"{title} {others[0]}", others[1:]
    # a short leading line with no role word is a team name when the next line has one ("Analytics" / "... Engineer")
    if others and len(title.split()) <= MAX_TEAM_WORDS and not ROLE_WORD.search(title) and ROLE_WORD.search(others[0]):
        title, others = others[0], [title, *others[1:]]
    if places:
        return title[:140], " | ".join(places)[:100], others[0] if others else ""
    return title[:140], " | ".join(others[:2])[:100], ""


def _card_extras(card: str, title: str) -> list[str]:
    """The lines of a job card around its title that can hold the place and team, in the order the card prints
    them: up to two above the title and three below. A card can reach up to headings and badges that sit further
    away, so a window keeps them out while `_parse_card` still finds the place wherever it is printed."""
    low = title.lower()
    lines = [ln.strip() for ln in card.split("\n")]
    lines = [ln for ln in lines if ln and len(ln) <= CARD_LINE_MAX and ln.lower() not in CARD_CHROME | BUTTON_LINES]
    at = next((i for i, ln in enumerate(lines) if ln.lower() == low), None)
    if at is None:
        return lines[:2]
    after = [ln for ln in lines[at + 1 :] if ln.lower() != low]
    return [*lines[max(0, at - CARD_BEFORE) : at], *after[:CARD_AFTER]]


def _job_key(href: str) -> str:
    """The link without tracking parameters, but keeping a job_id query: there it is the only thing naming the job."""
    key = href.split("?", maxsplit=1)[0].rstrip("/")
    m = JOB_ID.search(href)
    return f"{key}?job_id={m[1][len('job_id=') :]}" if m else key


def _job(job_id: Any, title: Any, location: Any, url: Any, team: Any = "") -> dict[str, Any]:
    return {"job_id": job_id, "title": title, "location": location, "url": url, "team": team, "posted": ""}


def _eightfold_jobs(resp_url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    origin = cast("re.Match[str]", re.match(r"https?://[^/]+", resp_url))[0]
    return [
        _job(
            str(p["id"]),
            p.get("name", ""),
            "; ".join(p.get("locations") or []),
            origin + p["positionUrl"],
            team=p.get("department") or "",
        )
        for p in body["data"]["positions"]
    ]


async def _eightfold_pages(page: Any, ef_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eightfold boards: use the page's own search results and press "Next jobs" for the rest.

    The click is dispatched as an event because cookie banners often cover the button. Results come sorted by
    distance from the searched place, so a page after the first with no Israel job ends the list."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def drain() -> list[dict[str, Any]]:
        fresh = [j for j in ef_jobs if j["job_id"] not in seen]
        seen.update(j["job_id"] for j in fresh)
        out.extend(fresh)
        return fresh

    batch = drain()
    for n in range(EF_MAX_PAGES - 1):
        if n and not any(ISRAEL.search(j["location"]) for j in batch):
            break
        nxt = page.locator(EF_NEXT)
        if not await nxt.count():
            break
        el = nxt.first
        if await el.is_disabled() or await el.get_attribute("aria-disabled") == "true":
            break
        await el.dispatch_event("click")
        batch = []
        for _poll in range(SETTLE_POLLS):
            await page.wait_for_timeout(250)
            batch = drain()
            if batch:
                break
        if not batch:
            break
    return out


async def crawl_one(ctx: Any, url: str, sem: asyncio.Semaphore) -> list[dict[str, Any]] | None:  # noqa: PLR0912, PLR0915
    async with sem:
        page = await ctx.new_page()
        captured: list[dict[str, Any]] = []
        ef_jobs: list[dict[str, Any]] = []
        link_count = 0

        async def on_resp(resp: Any) -> None:  # HiBob career boards load every job in one JSON response
            with contextlib.suppress(Exception):
                if (
                    "/api/job-ad" in resp.url
                    and resp.request.resource_type in ("xhr", "fetch")
                    and resp.status == HTTP_OK
                ):
                    d = await resp.json()
                    if isinstance(d, dict) and isinstance(d.get("jobAdDetails"), list):
                        origin = cast("re.Match[str]", re.match(r"https?://[^/]+", resp.url))[0]
                        for j in d["jobAdDetails"]:
                            loc = ", ".join(x for x in (j.get("site"), j.get("country")) if x)
                            captured.append(
                                _job(
                                    j["id"],
                                    j.get("title", ""),
                                    loc,
                                    f"{origin}/jobs/{j['id']}",
                                    team=j.get("department") or "",
                                )
                            )

        async def on_search(resp: Any) -> None:  # Eightfold: every search page, first load and each "Next jobs"
            with contextlib.suppress(Exception):
                if EF_SEARCH in resp.url and resp.request.resource_type in ("xhr", "fetch") and resp.status == HTTP_OK:
                    ef_jobs.extend(_eightfold_jobs(resp.url, await resp.json()))

        page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
        page.on("response", lambda r: asyncio.ensure_future(on_search(r)))
        try:
            r = await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=15000)
            title = (await page.title())[:80]
            if r and r.status >= HTTP_ERROR:
                WHY[url] = f"HTTP {r.status}, title={title!r}, final={page.url[:80]}"
                return None
            if BLOCKED.search(title):
                WHY[url] = f"blocked page, title={title!r}, final={page.url[:80]}"
                return None
            prev = -1
            await page.wait_for_timeout(1000)
            if captured:
                return captured
            if ef_jobs:
                return await _eightfold_pages(page, ef_jobs)
            for _ in range(
                25
            ):  # lazy-loaded / paginated lists: scroll and press "load more" until the count stops growing
                await page.mouse.wheel(0, 6000)
                await page.wait_for_timeout(700)
                with contextlib.suppress(Exception):
                    btn = page.get_by_role("button", name=MORE).first
                    if await btn.is_visible(timeout=300):
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(900)
                n = await page.eval_on_selector_all("a[href]", "e=>e.length")
                if n == prev:
                    break
                prev = n
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            # 1. JSON-LD JobPosting
            for raw in await page.eval_on_selector_all(
                'script[type="application/ld+json"]', "e=>e.map(x=>x.textContent)"
            ):
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                for d in data if isinstance(data, list) else data.get("@graph", [data]):
                    if isinstance(d, dict) and d.get("@type") == "JobPosting" and d.get("url"):
                        loc = d.get("jobLocation") or {}
                        loc = loc[0] if isinstance(loc, list) and loc else loc
                        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                        L = ", ".join(
                            x for x in (addr.get("addressLocality"), addr.get("addressCountry")) if isinstance(x, str)
                        )
                        if d["url"] not in seen:
                            seen.add(d["url"])
                            out.append(_job(d["url"], d.get("title", ""), L, d["url"]))

            # 2. job-looking links, across paginated result pages
            async def collect() -> int:
                nonlocal link_count
                found = 0
                links = await page.eval_on_selector_all("a[href]", CARD_JS, CARD_ARGS)
                link_count = len(links)
                for href, text, card in links:
                    key = _job_key(href)
                    if not text or key in seen or not JOBLINK.search(href) or NOT_JOB.search(href):
                        continue
                    lines = [ln.strip() for ln in text.split("\n") if ln.strip().lower() not in LINK_NOISE | {""}]
                    if not lines:
                        continue
                    if lines[0].lower() in LANGS:
                        continue
                    if (
                        len(lines) == 1 and 0 < len(card) <= CARD_MAX
                    ):  # bare title link: location sits in the surrounding card
                        lines = [lines[0], *_card_extras(card, lines[0])]
                    parsed = _parse_card(lines)
                    if parsed is None:
                        continue
                    title, loc, team = parsed
                    seen.add(key)
                    found += 1
                    m = re.search(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[^/]+/job/([^/]+)/", href)
                    if m:  # Workday puts the location in the URL path
                        loc = m.group(1).replace("-", " ")
                    out.append(_job(key, title, loc, href, team=team))
                return found

            await collect()
            for _ in range(60):  # pagination: stop when "next" is missing/disabled or yields nothing new
                nxt = None
                for sel in NEXT_SELS:  # first *visible* match (a <link rel=next> in <head> is not clickable)
                    cand = page.locator("body " + sel if not sel.startswith("body") else sel).locator("visible=true")
                    if await cand.count():
                        nxt = cand.first
                        break
                if nxt is None:
                    break
                try:
                    cls = (
                        (await nxt.get_attribute("class") or "")
                        + " "
                        + (await nxt.get_attribute("aria-disabled") or "")
                    )
                    if re.search(r"disabled|true", cls, re.I) or await nxt.is_disabled():
                        break
                    await nxt.click(timeout=3000)
                except Exception:
                    break
                found = 0
                for _poll in range(SETTLE_POLLS):  # wait for new job links; a pager may clear the list first
                    await page.wait_for_timeout(250)
                    found = await collect()
                    if found:
                        break
                if not found:
                    break
            if not out:
                WHY[url] = f"no jobs found, title={title!r}, final={page.url[:80]}, links={link_count}"
            return out
        except Exception as e:
            WHY[url] = f"{type(e).__name__}: {str(e).splitlines()[0][:140] if str(e) else ''}"
            return None
        finally:
            await page.close()


async def _run(urls: list[str]) -> dict[str, list[dict[str, Any]] | None]:
    sem = asyncio.Semaphore(3)
    async with async_playwright() as p:
        b = (
            await p.chromium.launch(executable_path=EXE, args=["--no-sandbox"])
            if EXE
            else await p.chromium.launch(args=["--no-sandbox"])
        )
        ctx = await b.new_context(user_agent=UA, locale="en-US")
        res = await asyncio.gather(*[crawl_one(ctx, u, sem) for u in urls])
        await b.close()
    return dict(zip(urls, res, strict=True))


def crawl_many(urls: Any) -> dict[str, list[dict[str, Any]] | None]:
    return asyncio.run(_run(list(urls)))
