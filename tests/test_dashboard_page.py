"""The built dashboard (docs/) run in a real browser: it loads from the page's own files under its
Content-Security-Policy, passes axe in both themes, and keeps focus and announcements sensible.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import base64
import json
import mimetypes
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
import sync_supabase
from tests.conftest import add_job, write_json

REPO = Path(__file__).resolve().parents[1]
AXE = REPO / "node_modules" / "axe-core" / "axe.min.js"
ORIGIN = "https://misra.test"
ISRAEL_JOBS = 12
PAGE_SIZE = 10
DEBOUNCE_MS = 600
LONG_NOTE = "A very long note without a break " + "x" * 120
PHONE = {"width": 375, "height": 800}
TAP = 44
# Chrome measures a 44 px box as 43.999999 now and then; the rule is still 44 (a 43 px target fails).
TAP_MIN = TAP - 0.01
SIGN_MIN = 28  # the chevron of the coverage card: wider than the text caret it replaced
REST_PAGE = 1000
REQUESTS_FOR_TOTAL = 3  # 2,345 rows at 1,000 a request
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-expose-headers": "Content-Range",
}
USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EMAIL = "ann@test.example"
NAME = "Ann Example"
PHOTO = "https://photos.test/ann.png"
# What the card shows: two coarse parts, never the eight internal reasons.
SITES = {
    "Walled": "https://walled.test/jobs",  # blocked: the careers page itself
    "Parked": "https://parked.test",  # no careers address: the own site
    "LongName": "https://long.test/careers",
    "Nowhere": "https://nowhere.test/jobs",
    "Guarded": "https://guarded.test/careers",
}
GAP_PARTS = [("We choose not to include these companies", 1), ("We miss roles at these companies", 4)]


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built with the daily-email signup switch off, so these tests (written before the signup opened, with
    stand-in backends that know nothing of the `subscriptions` table) keep checking the rest of the page. The page
    with the switch on is the `digest_site` fixture of tests/test_digest_settings.py."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


def gap_row(name: str, careers_url: str, reason: str, note: str) -> dict[str, object]:
    """A researched company that is not crawled, as data/companies.json holds it."""
    return {
        "name": name,
        "domain": f"{name.lower()}.test",
        "crawl": False,
        "careers_url": careers_url,
        "status": "careers_page_only",
        "gap": {"reason": reason, "note": note},
    }


def build_site(db: sqlite3.Connection, workdir: Path, fill: Callable[[sqlite3.Connection], None] | None = None) -> Path:
    """The real templates and design system built over a small database (the jobs `fill` adds, else a few of Acme's);
    returns docs/."""
    shutil.copytree(REPO / "templates", workdir / "templates", dirs_exist_ok=True)
    shutil.copytree(REPO / "design-system", workdir / "design-system")
    shutil.copytree(REPO / "vendor", workdir / "vendor")
    for legal in (*build_dashboard.LEGAL_PAGES, build_dashboard.ASSISTANT_PAGE, build_dashboard.ABOUT_PAGE):
        shutil.copy(REPO / legal.source, workdir / legal.source)
    write_json(
        workdir / "data" / "companies.json",
        [
            {"name": "Acme", "crawl": True, "status": "ok"},
            {
                "name": "Nowhere",
                "crawl": True,
                "status": "x",
                "domain": "nowhere.test",
                "careers_url": "https://nowhere.test/jobs",
            },
            gap_row("Parked", "", "site_unusable", "Domain parked at GoDaddy"),
            gap_row("LongName", "https://long.test/careers", "needs_reader", LONG_NOTE),
            gap_row("Walled", "https://walled.test/jobs", "bot_protection", ""),
            gap_row("Guarded", "https://guarded.test/careers", "robots_disallowed", ""),
        ],
    )
    if fill is not None:
        fill(db)
    else:
        old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
        add_job(db, "Acme", "0", "Accountant", first_seen=old)
        for n in range(1, ISRAEL_JOBS):
            add_job(db, "Acme", str(n), f"Product Manager {n}", first_seen=today)
        add_job(db, "Globex", "1", "Designer", location="London", first_seen=old)
    write_json(workdir / "rows.json", list(sync_supabase.open_rows(db)))
    db.close()
    build_dashboard.main()
    return workdir / "docs"


def table_rows(count: int) -> list[dict[str, Any]]:
    """`count` open Israeli jobs of one company, as the jobs table holds them."""
    return [
        {
            "company": "Bulk",
            "job_id": f"{n:05d}",
            "title": f"Role {n}",
            "team": None,
            "location": "Haifa, Israel",
            "url": "https://x.test/j",
            "first_seen": "2026-01-02T00:00:00",
            "level": "Not specified",
            "role_types": ["Other"],
            "regions": ["Haifa and the north"],
            "locations": [],
            "snippet": None,
        }
        for n in range(count)
    ]


class Backend:
    """A stand-in for the project's PostgREST endpoint: the rows of the jobs table, a thousand at a time, with the
    total in Content-Range, and the requests it saw. `mode` breaks it: down, error, no_range or short."""

    def __init__(self, rows: list[dict[str, Any]], mode: str = "ok") -> None:
        self.rows = rows
        self.mode = mode
        self.requests: list[Any] = []
        # The full-text search (search_job_keys): the keys it finds by query, the queries it was asked (in the body, as
        # the page sends them), how long it takes, and how it breaks: down, error (HTTP 503) or not_a_list.
        self.text_hits: dict[str, list[list[str]]] = {}
        self.searches: list[str] = []
        self.search_mode = "ok"
        self.search_seconds = 0.0
        self.search_delays: dict[str, float] = {}  # a query that takes its own time

    async def search(self, route: Route) -> None:
        request = route.request
        body = json.loads(request.post_data or "{}")
        assert set(body) == {"p_query"}
        query = body["p_query"]
        self.searches.append(query)
        assert "?" not in request.url  # the words are in the body, never in the address
        key = build_dashboard.SUPABASE_ANON_KEY
        assert request.headers.get("apikey") == key and request.headers.get("authorization") == f"Bearer {key}"
        await asyncio.sleep(self.search_delays.get(query, self.search_seconds))
        if self.search_mode == "down":
            await route.abort()
            return
        if self.search_mode == "error":
            await route.fulfill(status=503, headers=CORS, body="unavailable")
            return
        answer = {"not_a_list": {"keys": []}}.get(self.search_mode, self.text_hits.get(query, []))
        await route.fulfill(body=json.dumps(answer), content_type="application/json", headers=CORS)

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        if urlparse(request.url).path.endswith("/rpc/search_job_keys"):
            await self.search(route)
            return
        self.requests.append(request)
        query = parse_qs(urlparse(request.url).query)
        offset, limit = int(query["offset"][0]), min(int(query["limit"][0]), REST_PAGE)
        page = self.rows[offset : offset + limit]
        total = len(self.rows) - (1 if self.mode == "short" else 0)
        headers = {**CORS, "content-range": f"{offset}-{offset + len(page) - 1}/{total}"}
        key = build_dashboard.SUPABASE_ANON_KEY
        authorised = request.headers.get("apikey") == key and request.headers.get("authorization") == f"Bearer {key}"
        if self.mode == "down":
            await route.abort()
        elif not authorised:
            await route.fulfill(status=401, headers=CORS, body="no key")
        elif self.mode == "error" or (self.mode == "later_page" and offset > 0):
            await route.fulfill(status=503, headers=CORS, body="unavailable")
        else:
            if self.mode == "no_range":
                del headers["content-range"]
            await route.fulfill(body=json.dumps(page), content_type="application/json", headers=headers)


class AuthBackend:
    """A stand-in for the project's sign-in service (/auth/v1): the Google redirect, the code exchange and the log-out.
    It records every request. The session it hands out carries what Google sends besides the email (a name and a
    photo), so a test can check that none of it is kept or shown. `mode` breaks it: bad_code, token_down (no token
    can be issued or renewed) or logout_fails."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.requests: list[Any] = []

    def session(self) -> dict[str, Any]:
        def b64(part: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")

        expires = int(datetime.now(UTC).timestamp()) + 3600
        # The token as Supabase issues it once the access token hook of the migration has emptied `user_metadata`.
        # Without the hook that claim holds the name and photo from Google, and the browser would save them with it.
        claims = {
            "sub": USER_ID,
            "role": "authenticated",
            "aal": "aal1",
            "email": EMAIL,
            "exp": expires,
            "app_metadata": {"provider": "google", "providers": ["google"]},
            "user_metadata": {},
        }
        google = {"full_name": NAME, "name": NAME, "avatar_url": PHOTO, "picture": PHOTO, "email": EMAIL, "sub": "1111"}
        return {
            "access_token": f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(claims)}.signature",
            "token_type": "bearer",
            "expires_in": 3600,
            "expires_at": expires,
            "refresh_token": "refresh-1",
            "provider_token": "google-access-token",
            "provider_refresh_token": "google-refresh-token",
            "user": {
                "id": USER_ID,
                "aud": "authenticated",
                "role": "authenticated",
                "email": EMAIL,
                "created_at": "2026-11-15T10:00:00Z",
                "app_metadata": {"provider": "google", "providers": ["google"]},
                "user_metadata": google,
                "identities": [{"provider": "google", "identity_data": google}],
            },
        }

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append(request)
        path = urlparse(request.url).path
        if path.endswith("/authorize"):  # the browser is sent to Google: a stand-in page
            await route.fulfill(body="<title>Google</title>", content_type="text/html")
        elif path.endswith("/token") and self.mode == "token_down":
            await route.fulfill(status=503, headers=CORS, body="unavailable")
        elif path.endswith("/token") and self.mode != "bad_code":
            await route.fulfill(body=json.dumps(self.session()), content_type="application/json", headers=CORS)
        elif path.endswith("/token"):
            body = json.dumps({"error": "invalid_grant", "error_description": "code expired"})
            await route.fulfill(status=400, body=body, content_type="application/json", headers=CORS)
        elif path.endswith("/logout") and self.mode != "logout_fails":
            await route.fulfill(status=204, headers=CORS)
        else:
            await route.fulfill(status=500, headers=CORS, body="unavailable")


ROWS_CORS = {**CORS, "access-control-allow-methods": "*", "access-control-allow-headers": "*"}
LIMIT_BODY = {"code": "54000", "message": "A user can keep at most 2000 starred and discarded jobs"}
REFUSED_BODY = {"code": "23514", "message": "new row violates check constraint"}


class ChoicesBackend:
    """A stand-in for the `user_jobs` table behind PostgREST, as the signed-in user sees it (row-level security leaves
    them their own rows only): the rows, the writes it accepted, and every request it saw. `mode` breaks it: down
    (no answer), error (503), expired (401), limit (the database's row limit refuses a new row), limit_star (the limit
    refuses stars only), refused (any other refusal), load_down (reads fail, writes work). `delay` holds every
    write that long; `gate`, when set, holds every read until it is set."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, mode: str = "ok") -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {(r["company"], r["job_id"]): r for r in rows or []}
        self.mode = mode
        self.delay = 0.0
        self.gate: asyncio.Event | None = None
        self.requests: list[Any] = []
        self.writes: list[dict[str, Any]] = []

    def state_of(self, company: str, job_id: str) -> str | None:
        row = self.rows.get((company, job_id))
        return row["state"] if row else None

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=ROWS_CORS)
            return
        self.requests.append(request)
        reading = request.method == "GET"
        rows = sorted(
            self.rows.values(), key=lambda r: (r["company"], r["job_id"])
        )  # as of the request, not the answer
        if self.gate is not None and reading:
            await self.gate.wait()
        if not reading and self.delay:
            await asyncio.sleep(self.delay)
        failure = self.failure(reading, request)
        if failure == "down":
            await route.abort()
        elif failure:
            status, body = failure
            await route.fulfill(
                status=status, headers=ROWS_CORS, body=json.dumps(body), content_type="application/json"
            )
        elif reading:
            first, last = self.window(request, len(rows))
            columns = parse_qs(urlparse(request.url).query)["select"][0].split(",")
            body = [{c: r.get(c) for c in columns} for r in rows[first : last + 1]]
            span = f"{first}-{last}" if rows else "*"  # PostgREST says `*/0` for an empty table
            headers = {**ROWS_CORS, "content-range": f"{span}/{len(rows)}"}
            await route.fulfill(body=json.dumps(body), content_type="application/json", headers=headers)
        else:
            self.write(request)
            await route.fulfill(status=204 if request.method == "DELETE" else 201, headers=ROWS_CORS)

    def failure(self, reading: bool, request: Any) -> Any:
        """None when the request goes through, "down", or the (status, body) of the answer."""
        answers: dict[str, Any] = {
            "down": "down",
            "error": (503, {}),
            "expired": (401, {"code": "PGRST301", "message": "JWT expired"}),
        }
        if self.mode == "load_down" and reading:
            return "down"
        if self.mode in answers:
            return answers[self.mode]
        if not reading and (
            self.mode == "limit" or (self.mode == "limit_star" and '"star"' in (request.post_data or ""))
        ):
            return 500, LIMIT_BODY  # PostgREST answers class 54 with 500, so the code is what tells it from an outage
        if not reading and self.mode == "refused":
            return 400, REFUSED_BODY
        return None

    @staticmethod
    def window(request: Any, total: int) -> tuple[int, int]:
        """The first and last row asked for: supabase-js sends `offset` and `limit` as query parameters."""
        query = parse_qs(urlparse(request.url).query)
        first = int(query["offset"][0])
        return first, min(first + int(query["limit"][0]) - 1, REST_PAGE + first - 1, total - 1)

    def write(self, request: Any) -> None:
        query = parse_qs(urlparse(request.url).query)
        if request.method == "DELETE":
            ids = {name: query[name][0].removeprefix("eq.") for name in ("user_id", "company", "job_id")}
            self.writes.append({"op": "delete", **ids})
            self.rows.pop((ids["company"], ids["job_id"]), None)
        else:
            body = json.loads(request.post_data or "{}")
            ignore = "ignore-duplicates" in request.headers.get("prefer", "")
            if isinstance(
                body, list
            ):  # a chunk of choices brought over from a browser: existing rows are left as they are
                assert ignore, "a chunk is only sent as an import that never replaces a row"
                self.writes.append(
                    {"op": "import", "on_conflict": query["on_conflict"][0], "rows": body, "ignore_duplicates": ignore}
                )
                for row in body:
                    self.rows.setdefault((row["company"], row["job_id"]), row)
                return
            self.writes.append({"op": "upsert", "on_conflict": query["on_conflict"][0], **body})
            self.rows[(body["company"], body["job_id"])] = body


class PrefsBackend:
    """A stand-in for the `user_prefs` table behind PostgREST, as the signed-in user sees it: their one row (or none),
    every body written, and every request seen. `mode` breaks it like ChoicesBackend does: down, error, expired,
    refused (400) or load_down (reads fail, writes work). `gate`, when set, holds every read until it is set."""

    def __init__(self, filters: dict[str, Any] | None = None, mode: str = "ok") -> None:
        self.row: dict[str, Any] | None = None if filters is None else {"user_id": USER_ID, "filters": filters}
        self.mode = mode
        self.gate: asyncio.Event | None = None
        self.requests: list[Any] = []
        self.writes: list[dict[str, Any]] = []

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=ROWS_CORS)
            return
        self.requests.append(request)
        reading = request.method == "GET"
        if self.gate is not None and reading:
            await self.gate.wait()
        answers = {"error": (503, {}), "expired": (401, {"code": "PGRST301", "message": "JWT expired"})}
        if self.mode == "down" or (self.mode == "load_down" and reading):
            await route.abort()
        elif self.mode in answers:
            status, body = answers[self.mode]
            await route.fulfill(
                status=status, headers=ROWS_CORS, body=json.dumps(body), content_type="application/json"
            )
        elif not reading and self.mode == "refused":
            await route.fulfill(
                status=400, headers=ROWS_CORS, body=json.dumps(REFUSED_BODY), content_type="application/json"
            )
        elif reading:
            rows = [{"filters": self.row["filters"]}] if self.row else []
            headers = {**ROWS_CORS, "content-range": f"0-{len(rows) - 1}/{len(rows)}" if rows else "*/0"}
            await route.fulfill(body=json.dumps(rows), content_type="application/json", headers=headers)
        else:
            body = json.loads(request.post_data or "{}")
            self.writes.append({"on_conflict": parse_qs(urlparse(request.url).query)["on_conflict"][0], **body})
            self.row = {**(self.row or {}), **body}
            await route.fulfill(status=201, headers=ROWS_CORS)


async def account_action(page: Page, selector: str) -> None:
    """Pick an item of the account menu with the mouse: open the menu if it is closed, then press the item."""
    if await page.locator("#accountMenu").is_hidden():
        await page.click("#accountMenuButton")
    await page.click(selector)


async def account_action_by_keyboard(page: Page, selector: str) -> None:
    """The same with the keyboard: Enter on the profile button, then Enter on the item."""
    await page.focus("#accountMenuButton")
    await page.keyboard.press("Enter")
    await page.focus(selector)
    await page.keyboard.press("Enter")


async def serve(  # noqa: PLR0913  (each stand-in is optional, and a test names the ones it wants)
    page: Page,
    docs: Path,
    *,
    wait: bool = True,
    backend: Backend | None = None,
    auth: AuthBackend | None = None,
    choices: ChoicesBackend | None = None,
    prefs: PrefsBackend | None = None,
) -> list[str]:
    """Serve docs/ at ORIGIN without a network, the jobs from a fake backend and the sign-in from a fake service;
    returns the console errors and page errors seen. The backend defaults to the rows of the site fixture."""
    problems: list[str] = []
    rows_file = docs.parent / "rows.json"
    if backend is None:
        backend = Backend(json.loads(rows_file.read_text(encoding="utf-8")) if rows_file.exists() else [])

    async def handler(route: Route) -> None:
        name = urlparse(route.request.url).path.lstrip("/") or "index.html"
        file = docs / name
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", handler)
    await page.route(f"{build_dashboard.SUPABASE_URL}/**", backend.handle)
    # the later route wins: sign-in requests never reach the jobs backend
    await page.route(f"{build_dashboard.SUPABASE_URL}/auth/v1/**", (auth or AuthBackend()).handle)
    await page.route(f"{build_dashboard.SUPABASE_URL}/rest/v1/user_jobs**", (choices or ChoicesBackend()).handle)
    await page.route(f"{build_dashboard.SUPABASE_URL}/rest/v1/user_prefs**", (prefs or PrefsBackend()).handle)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}/index.html")
    if wait:
        await page.wait_for_selector("#list article")
    return problems


async def test_loads_its_files_under_the_csp_and_lists_roles(page: Page, site: Path) -> None:
    problems = await serve(page, site)
    assert problems == []
    assert await page.locator("#list > li").count() == PAGE_SIZE
    assert (
        await page.locator("#summary").inner_text()
        == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    )
    assert await page.locator("#upd").get_attribute("datetime") is not None
    assert await page.locator("#hiring li button.story").count() == 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_has_no_axe_violations(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await serve(page, site)
    await open_gaps(page)  # the card, every group open, is part of the page
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_design_system_showcase_has_no_axe_violations(page: Page, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await serve(page, REPO / "design-system", wait=False)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


async def test_links_that_open_a_new_tab_say_so(page: Page, site: Path) -> None:
    await serve(page, site)
    for link in await page.locator("#list a[target=_blank]").all():
        assert "opens in a new tab" in (await link.text_content() or "")


async def test_paging_moves_focus_to_the_list_heading(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.click("#next")
    assert await page.evaluate("document.activeElement.id") == "list-h"
    box = await page.locator("#list-h").bounding_box()
    assert box and box["width"] <= 1 and box["height"] <= 1  # a click is not the keyboard: the heading stays hidden
    assert await page.locator("#pageInfo").inner_text() == "Page 2 of 2"
    assert await page.locator("#list > li").count() == ISRAEL_JOBS - PAGE_SIZE


async def test_search_is_announced_once_typing_pauses(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#status").inner_text() == ""
    await page.locator("#q").press_sequentially("Prod", delay=50)
    await page.locator("#q").press_sequentially("uct", delay=50)
    assert await page.locator("#status").inner_text() == ""
    await expect(page.locator("#status")).not_to_have_text("", timeout=DEBOUNCE_MS * 4)
    assert await page.locator("#status").inner_text() == await page.locator("#summary").inner_text()


async def test_theme_button_names_its_state(page: Page, site: Path) -> None:
    await serve(page, site)
    button = page.locator("#theme")
    assert await button.get_attribute("aria-label") == "Theme: auto"
    await button.click()
    assert await button.get_attribute("aria-label") == "Theme: light"
    assert await page.evaluate("document.documentElement.dataset.theme") == "light"


async def test_the_jobs_are_read_from_the_backend_with_the_anon_key(page: Page, site: Path) -> None:
    backend = Backend(json.loads((site.parent / "rows.json").read_text(encoding="utf-8")))
    assert await serve(page, site, backend=backend) == []
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.headers["apikey"] == build_dashboard.SUPABASE_ANON_KEY
    assert request.headers["authorization"] == f"Bearer {build_dashboard.SUPABASE_ANON_KEY}"
    assert request.headers["prefer"] == "count=exact"
    assert "status=eq.open" in request.url
    assert (site / "jobs.json").exists()  # published for download, never read by the page


async def test_every_job_of_the_table_is_loaded_across_the_row_limit(page: Page, site: Path) -> None:
    total = 2 * REST_PAGE + 345
    backend = Backend(table_rows(total))
    await serve(page, site, backend=backend)
    assert len(backend.requests) == REQUESTS_FOR_TOTAL
    assert [r.headers.get("prefer") for r in backend.requests] == ["count=exact", None, None]
    summary = await page.locator("#summary").inner_text()
    assert summary == f"Showing {total} of {total} open roles in Israel. 0 discarded."


async def test_filters_work_on_the_loaded_jobs(page: Page, site: Path) -> None:
    await serve(page, site)
    await choose(page, "types", "Product")
    assert await page.locator("#summary").inner_text() == (
        f"Showing {ISRAEL_JOBS - 1} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    )
    await choose(page, "types", "")
    await page.click(ABROAD_CHIP)  # a region like the others: only the roles abroad, out of all 13
    assert "of 13 open roles. 0 discarded." in await page.locator("#summary").inner_text()
    await page.select_option("#company", "Globex")
    assert await page.locator("#list > li").count() == 1


@pytest.mark.parametrize("mode", ["down", "error", "no_range", "short", "later_page"])
async def test_an_unreachable_or_incomplete_backend_shows_an_error_card(page: Page, site: Path, mode: str) -> None:
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    extra = table_rows(REST_PAGE) if mode == "later_page" else []  # more than one page, so a later one can fail
    await serve(page, site, wait=False, backend=Backend(rows + extra, mode))
    card = page.locator("#load-error")
    await expect(card).to_be_visible()
    assert await card.get_attribute("role") == "alert"
    assert await card.locator("h2").inner_text() == "The roles could not be loaded"
    assert await page.locator("#summary").inner_text() == "Could not load the roles."
    assert await page.locator("#list > li").count() == 0
    box = await card.locator("#retry").bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN


async def test_an_empty_table_is_an_error_not_an_empty_dashboard(page: Page, site: Path) -> None:
    await serve(page, site, wait=False, backend=Backend([]))
    await expect(page.locator("#load-error")).to_be_visible()


async def test_every_request_asks_for_open_jobs_in_a_stable_order_page_by_page(page: Page, site: Path) -> None:
    backend = Backend(table_rows(2 * REST_PAGE + 1))
    await serve(page, site, backend=backend)
    queries = [parse_qs(urlparse(r.url).query) for r in backend.requests]
    assert sorted(int(q["offset"][0]) for q in queries) == [0, REST_PAGE, 2 * REST_PAGE]
    for query in queries:
        assert query["order"] == ["company,job_id"]
        assert query["status"] == ["eq.open"]
        assert query["limit"] == [str(REST_PAGE)]
        columns = set(query["select"][0].split(","))
        assert {"level", "role_types", "regions", "locations", "first_seen"} <= columns
        assert "snippet" in columns
        assert "description" not in columns
        assert "details" not in columns


def row(company: str, job_id: str, title: str | None, first_seen: str, **kw: Any) -> dict[str, Any]:
    return {**table_rows(1)[0], "company": company, "job_id": job_id, "title": title, "first_seen": first_seen, **kw}


async def test_cards_take_level_role_type_and_place_from_the_table_columns(page: Page, site: Path) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    rows = [
        row("Acme", "1", "Product Intern", today, level="Intern", role_types=["Product"]),
        row("Acme", "2", "Student", today, level="Intern"),
        row("Acme", "3", "Junior Dev", today, level="Junior", role_types=["R&D"]),
        row("Acme", "4", "Engineer", today),
        row("Nowhere", "5", None, today, regions=[], locations=["Somewhere, Israel"]),
        row("Abroad", "6", "Engineer", today, regions=[], locations=[]),
    ]
    await serve(page, site, backend=Backend(rows))
    assert await page.locator("#summary").inner_text() == "Showing 5 of 5 open roles in Israel. 0 discarded."
    assert await page.locator("article[data-level=Intern]").count() == len(["Product Intern", "Student"])
    assert await page.locator("article[data-level=Junior]").count() == 1
    assert await page.locator("article[data-role-types=Other]").count() == len(["Student", "Engineer", "untitled"])


async def test_a_company_whose_first_jobs_have_closed_still_shows_its_new_roles(page: Page, site: Path) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    # the build knows Acme's first day (30 days ago, a job that is no longer open); Acme's only open role is from today
    await serve(page, site, backend=Backend([row("Acme", "9", "Engineer", today)]))
    assert await page.locator("article[data-company=Acme]").get_attribute("data-new") == "true"
    sticker = page.locator("article[data-company=Acme] .sticker")
    assert await sticker.text_content() == "New"  # sentence case for screen readers
    assert await sticker.evaluate("e => getComputedStyle(e).textTransform") == "uppercase"  # uppercase on screen


async def test_a_company_in_the_failing_list_is_marked_possibly_old(page: Page, site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '"blocked":[]' in html
    (site / "index.html").write_text(
        html.replace('"blocked":[]', '"blocked":[{"c":"Acme","why":"x","last":"never"}]'), encoding="utf-8"
    )
    await serve(page, site)
    assert await page.locator("article[data-company=Acme]").first.get_attribute("data-stale") == "true"
    sticker = page.locator("article[data-company=Acme] .sticker").first
    assert await sticker.text_content() == "Old?"
    assert await sticker.evaluate("e => getComputedStyle(e).textTransform") == "uppercase"
    assert await page.locator("article[data-company=Globex]").count() == 0  # not in Israel


async def test_the_error_card_retries_by_reloading(page: Page, site: Path) -> None:
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    backend = Backend(rows, "down")
    await serve(page, site, wait=False, backend=backend)
    await expect(page.locator("#load-error")).to_be_visible()
    backend.mode = "ok"
    await page.click("#retry")
    await page.wait_for_selector("#list article")
    assert await page.locator("#load-error").count() == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_error_card_has_no_axe_violations(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    await serve(page, site, wait=False, backend=Backend(rows, "down"))
    await expect(page.locator("#load-error")).to_be_visible()
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


async def test_the_page_csp_allows_only_itself_and_the_project_host(site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    assert f"connect-src 'self' {build_dashboard.SUPABASE_URL};" in html
    assert "__SUPABASE" not in html
    assert "__PAGE_DATA__" not in html
    assert build_dashboard.SUPABASE_ANON_KEY.startswith("sb_publishable_")  # never a secret or service-role key
    vendored = {Path(name).name for name in build_dashboard.VENDORED_FILES}  # supabase-js names "sb_secret_" itself
    for built in site.iterdir():
        text = built.read_text(encoding="utf-8") if built.suffix in {".html", ".js", ".css", ".json"} else ""
        if built.name in vendored:
            continue
        assert "sb_secret_" not in text
        assert "service_role" not in text
    assert "__SUPABASE" not in (site / "dashboard.js").read_text(encoding="utf-8")
    assert "unsafe-inline" not in html


async def open_gaps(page: Page) -> None:
    await page.evaluate("document.querySelectorAll('#gaps, #gaps details').forEach(d => { d.open = true; })")


async def test_the_coverage_gap_card_has_two_parts_and_no_internal_reasons(page: Page, site: Path) -> None:
    await serve(page, site)
    await open_gaps(page)
    assert (
        await page.locator("#gaps-title").inner_text() == "Coverage gaps: 5 companies whose roles may be missing or old"
    )
    assert await page.locator("#gaps details").count() == 0  # no group to open one by one
    shown = []
    for part in await page.locator("#gaps .gap-part").all():
        label = await part.locator(".gap-part-title span").first.text_content()
        visible = await part.locator(".gap-count").text_content()
        assert (visible, visible) == (await part.get_attribute("data-count"), str(await part.locator("li").count()))
        shown.append((label, int(visible)))
    assert shown == GAP_PARTS
    text = await page.locator("#gaps").inner_text()
    for internal in ("Blocks automated access", "Site down", "No job feed found yet", "Failing in the latest crawl"):
        assert internal not in text
    # the reason stays in the page for agents
    assert await page.locator("li[data-company=Parked]").get_attribute("data-reason") == "site_unusable"
    assert await page.locator("li[data-company=Walled]").text_content() == "WalledCareers page (opens in a new tab)"
    # the cards are in alphabetical order inside a part
    names = await page.locator("[data-part=cannot-read] li").evaluate_all("els => els.map(e => e.dataset.company)")
    assert names == ["LongName", "Nowhere", "Parked", "Walled"]


async def test_the_coverage_gap_card_says_in_plain_words_which_sites_we_choose_not_to_include(
    page: Page, site: Path
) -> None:
    await serve(page, site)
    await open_gaps(page)
    skipped = page.locator("[data-part=skipped]")
    assert await skipped.locator("h3 span").first.inner_text() == "We choose not to include these companies"
    note = await skipped.locator(".gap-part-note").inner_text()
    assert note == "Their policy does not allow automated access, so we respect it."
    card = skipped.locator("li")
    assert await card.get_attribute("data-company") == "Guarded"
    assert await card.get_attribute("data-reason") == "robots_disallowed"
    assert await card.text_content() == "GuardedCareers page (opens in a new tab)"  # the name, where it goes, the cue
    assert await skipped.locator("a").get_attribute("href") == "https://guarded.test/careers"
    unread = await page.locator("[data-part=cannot-read] .gap-part-note").inner_text()
    assert unread == "We cannot find or read open roles in Israel at these companies."


async def test_each_gap_link_says_where_it_goes(page: Page, site: Path) -> None:
    await serve(page, site)
    await open_gaps(page)
    hints = await page.locator("#gaps .gap-card").evaluate_all(
        "els => Object.fromEntries(els.map(e => [e.dataset.company, e.querySelector('.gap-to').textContent]))"
    )
    # a careers page is named as such; a company with no career page opens its own website
    assert hints == {
        "Guarded": "Careers page",
        "LongName": "Careers page",
        "Nowhere": "Careers page",
        "Parked": "Website",
        "Walled": "Careers page",
    }
    assert await page.get_by_role("link", name="Walled Careers page (opens in a new tab)").count() == 1
    link = page.locator("li[data-company=Parked] a")
    assert await link.evaluate("a => a.textContent") == "ParkedWebsite (opens in a new tab)"
    box = await link.bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN


async def test_the_gap_card_shows_with_a_sign_that_it_can_be_opened(page: Page, site: Path) -> None:
    await page.emulate_media(reduced_motion="reduce")  # the turn is instant, so the value read is the final one
    await serve(page, site)
    sign = page.locator("#gaps summary .gaps-sign")
    box = await sign.bounding_box()
    assert box is not None
    assert box["width"] >= SIGN_MIN  # larger than the old text caret
    assert await sign.get_attribute("aria-hidden") == "true"  # the state is read from the details itself
    closed = await sign.locator("svg").evaluate("e => getComputedStyle(e).transform")
    assert closed == "none"
    await page.locator("#gaps summary").click()
    assert await page.locator("#gaps").evaluate("d => d.open") is True
    opened = await sign.locator("svg").evaluate("e => getComputedStyle(e).transform")
    assert opened == "matrix(-1, 0, 0, -1, 0, 0)"  # turned half way round: it points up


async def test_every_site_on_the_coverage_gap_card_is_a_safe_link_that_the_keyboard_reaches(
    page: Page, site: Path
) -> None:
    await serve(page, site)
    await open_gaps(page)
    links = page.locator("#gaps .gap-list li a")
    assert await page.locator("#gaps .gap-list li").count() == await links.count() == len(SITES)
    found = {}
    for link in await links.all():
        name = await link.locator(".gap-name-text").text_content() or ""
        found[name] = await link.get_attribute("href")
        assert await link.get_attribute("target") == "_blank"
        assert await link.get_attribute("rel") == "noopener noreferrer"
        assert await link.locator(".sr-only").text_content() == " (opens in a new tab)"
        assert await link.get_attribute("tabindex") is None
        box = await link.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["width"] >= TAP_MIN
    assert found == SITES
    assert all(href is not None and href.startswith("https://") for href in found.values())
    # Tab reaches every link, in order, and the focus is drawn
    await page.locator("#gaps summary").focus()
    reached = []
    for _ in range(40):
        await page.keyboard.press("Tab")
        active = await page.evaluate(
            "document.activeElement.matches('.gap-list a') && document.activeElement.getAttribute('href')"
        )
        if active:
            reached.append(active)
            outline = await page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
            assert outline == "solid"
        if len(reached) == len(found):
            break
    assert sorted(reached) == sorted(found.values())


async def test_a_site_without_an_https_address_is_shown_as_plain_text(page: Page, site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '"url":"https://walled.test/jobs"' in html
    html = html.replace('"url":"https://walled.test/jobs"', '"url":"javascript:alert(1)"')
    (site / "index.html").write_text(html, encoding="utf-8")
    await serve(page, site)
    await open_gaps(page)
    assert await page.locator("#gaps .gap-list li a").count() == len(SITES) - 1
    assert await page.locator("li[data-company=Walled] b").text_content() == "Walled"


async def test_the_coverage_gap_card_opens_and_closes_and_says_so(page: Page, site: Path) -> None:
    await serve(page, site)
    details = page.locator("#gaps")
    assert await details.evaluate("d => d.open") is False
    await page.locator("#gaps-title").click()
    assert await details.evaluate("d => d.open") is True
    await page.locator("#gaps-title").click()
    assert await details.evaluate("d => d.open") is False


async def test_add_a_company_stays_in_view_while_the_coverage_gap_card_is_closed(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#gaps").evaluate("d => d.open") is False
    button = page.locator("#gapsCard #reportOpen")
    await expect(button).to_be_visible()
    assert await button.inner_text() == "Add a company"
    assert await page.locator("#gapReport p").count() == 0  # the button stands alone, with no sentence
    assert await page.locator("#gaps .gap-part").first.is_hidden()  # the list is closed, the report row is not


@pytest.mark.parametrize("width", [320, 393])
async def test_the_coverage_gap_card_reads_well_on_a_phone(page: Page, site: Path, width: int) -> None:
    await page.set_viewport_size({"width": width, "height": PHONE["height"]})
    await serve(page, site)
    await open_gaps(page)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    summary = await page.locator("#gaps summary").bounding_box()
    assert summary is not None
    assert summary["height"] >= TAP_MIN
    for note in await page.locator("#gaps .gap-part-note").all():
        box = await note.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width
    for card in await page.locator("#gaps .gap-card").all():
        box = await card.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["x"] + box["width"] <= width


# ---- Filters: level, role type, region and place (PLAN 3.4; FR-3 AC 8 to 10, FR-9 AC 1, 2, 7 and 8) ----

TEL_AVIV = "Tel Aviv area"
SHARON = "Sharon"
JERUSALEM = "Jerusalem"
NORTH = "Haifa and the north"
REMOTE = "Remote"
KFAR = "Kfar Foo, Israel"
NO_CITY = "Israel, city not given"
MULTI = "Israel, Yokneam; Israel, Tel Aviv"
PRODUCT = {"pm", "pmi", "pmj", "pd", "multi"}
ABROAD = {"bare", "us", "lon"}  # not in Israel: the bare remote role does not say where, "Remote, US" does
PLACED_ABROAD = {"us", "lon"}  # the "Abroad" region: roles outside Israel that name a place (not the bare "Remote")
FEW = {"width": 375, "height": 700}
# The tools the page registers on navigator.modelContext are kept here, so a test can call them as an assistant would.
WEBMCP = """
window.__tools = {};
Object.defineProperty(navigator, 'modelContext', {
  configurable: true,
  value: { registerTool(tool) { window.__tools[tool.name] = tool; } },
});
"""
CALL = "async ([name, args]) => JSON.parse((await window.__tools[name].execute(args)).content[0].text)"
AXE_RUN = "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
ABROAD_CHIP = ".chip[data-group=regions][data-value=Abroad]"


def job(job_id: str, title: str, location: str, level: str = "Not specified", **kw: Any) -> dict[str, Any]:
    """A row of the jobs table; the columns the filters read are keyword arguments (role_types, regions, locations)."""
    return row("Acme", job_id, title, "2026-01-02T00:00:00", location=location, level=level, **kw)


def filter_rows() -> list[dict[str, Any]]:
    """One job for each case of the filters: every level and several role types, regions, places and remote roles."""
    return [
        job("pm", "Product Manager", "Tel Aviv, Israel", role_types=["Product"], regions=[TEL_AVIV]),
        job("pmi", "Product Intern", "Tel Aviv, Israel", "Intern", role_types=["Product"], regions=[TEL_AVIV]),
        job("pmj", "Junior PM", "Herzliya, Israel", "Junior", role_types=["Product"], regions=[SHARON]),
        job("dev", "Software Engineer", "Haifa, Israel", role_types=["R&D"], regions=[NORTH]),
        job("fin", "Finance Intern", KFAR, "Intern", role_types=["Finance"], regions=[], locations=[KFAR]),
        job("pd", "Product Designer", "Jerusalem, Israel", role_types=["Product", "Design"], regions=[JERUSALEM]),
        job("da", "Data Analyst", "Israel", role_types=["Data & AI"], regions=[], locations=[NO_CITY]),
        job("rem", "Account Executive", "Remote, Israel", role_types=["Sales & BD"], regions=[REMOTE]),
        job("multi", "Product Lead", MULTI, role_types=["Product"], regions=[TEL_AVIV, NORTH]),
        job("bare", "Developer", "Remote", role_types=["R&D"], regions=[], locations=[]),
        job("us", "Developer", "Remote, US", role_types=["R&D"], regions=[], locations=[]),
        job("lon", "Product Manager", "London, UK", role_types=["Product"], regions=[], locations=[]),
    ]


def ids_of(rows: list[dict[str, Any]]) -> set[str]:
    return {r["job_id"] for r in rows}


ISRAEL_IDS = ids_of(filter_rows()) - ABROAD


async def shown(page: Page) -> set[str]:
    """The ids of the cards on the page (the fixtures keep every answer within one page of results)."""
    ids = await page.eval_on_selector_all("#list article", "els => els.map(e => e.dataset.jobId.split('|')[1])")
    return set(ids)


async def choose(page: Page, group: str, value: str) -> None:
    """Press the chip of a filter group; the empty value is the group's All chip."""
    await page.click(f'.chip[data-group={group}][data-value="{value}"]')


async def serve_filters(page: Page, site: Path, *, tools: bool = False) -> None:
    if tools:
        await page.add_init_script(WEBMCP)
    await serve(page, site, backend=Backend(filter_rows()))


async def ask(page: Page, name: str, args: dict[str, Any] | None = None) -> Any:
    return await page.evaluate(CALL, [name, args or {}])


async def found(page: Page, args: dict[str, Any]) -> set[str]:
    """The ids search_jobs gives an assistant for these arguments."""
    return {j["id"].split("|")[1] for j in (await ask(page, "search_jobs", {"limit": 100, **args}))["jobs"]}


async def tags(page: Page, job_id: str) -> str:
    return await page.locator(f"article[data-job-id='Acme|{job_id}'] .card-tags").text_content() or ""


async def test_fr9_ac1_every_card_shows_a_level_and_a_role_type(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    cards = await page.locator("#list article").all()
    assert len(cards) == len(ISRAEL_IDS)
    for card in cards:
        text = await card.locator(".card-tags").text_content() or ""
        assert "Level: " in text
        assert "Role type: " in text
        assert await card.get_attribute("data-level")
        assert await card.get_attribute("data-role-types")
    assert "Level: Intern" in await tags(page, "fin")
    assert "Role type: Finance" in await tags(page, "fin")
    assert "Level: Not specified" in await tags(page, "pm")  # a level the title does not state is shown too
    assert await page.locator("article[data-job-id='Acme|pd']").get_attribute("data-role-types") == "Product|Design"
    assert "Role type: Product" in await tags(page, "pd")
    assert "Role type: Design" in await tags(page, "pd")


async def test_fr9_ac1_a_job_without_a_role_type_shows_other(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend([job("x", "Mystery", "Haifa, Israel", role_types=[], regions=[NORTH])]))
    assert await page.locator("article[data-job-id='Acme|x']").get_attribute("data-role-types") == "Other"
    assert "Role type: Other" in await tags(page, "x")


async def test_fr9_ac2_level_and_role_type_are_separate_filters(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    for group, name in (("levels", "Level"), ("types", "Role type")):
        label = await page.locator(f"#{group}").get_attribute("aria-labelledby")
        assert await page.locator(f"#{label}").inner_text() == name
    levels = await page.locator("#levels .chip").all_text_contents()
    types = await page.locator("#types .chip").all_text_contents()
    assert levels == ["All", "Intern", "Junior", "Not specified"]
    assert "Product" in types
    assert not {"Intern", "Junior"} & set(types)  # no chip blends the two any more
    assert await page.locator(".chip[data-cat]").count() == 0
    await choose(page, "levels", "Intern")
    assert await shown(page) == {"pmi", "fin"}
    await choose(page, "levels", "")
    await choose(page, "types", "Design")
    assert await shown(page) == {"pd"}


async def test_fr9_ac2_the_assistant_has_the_same_separate_filters(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    properties = await page.evaluate("window.__tools.search_jobs.inputSchema.properties")
    assert {"levels", "roleTypes", "regions", "includeRemoteNotSaid"} <= set(properties)
    assert not {"category", "locations", "allLocations"} & set(properties)
    assert properties["levels"]["items"]["enum"] == ["Intern", "Junior", "Not specified"]
    assert "Product" in properties["roleTypes"]["items"]["enum"]
    assert properties["regions"]["items"]["enum"][0] == TEL_AVIV
    assert properties["regions"]["items"]["enum"][-1] == "Abroad"
    assert await found(page, {"levels": ["Intern"]}) == {"pmi", "fin"}
    assert await found(page, {"roleTypes": ["Design"]}) == {"pd"}


async def test_fr9_ac7_no_role_type_selected_means_every_type(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    assert await shown(page) == ISRAEL_IDS
    assert await page.locator("#types .chip[aria-pressed=true]").all_text_contents() == ["All"]
    await choose(page, "types", "Product")
    assert await shown(page) == PRODUCT
    await choose(page, "types", "Finance")  # two types: a job of either
    assert await shown(page) == PRODUCT | {"fin"}
    await choose(page, "types", "")  # All clears them
    assert await shown(page) == ISRAEL_IDS
    await choose(page, "types", "Finance")
    await choose(page, "types", "Finance")  # pressing a chosen type again takes it out
    assert await shown(page) == ISRAEL_IDS
    total = len(ISRAEL_IDS)
    assert (
        await page.locator("#summary").inner_text() == f"Showing {total} of {total} open roles in Israel. 0 discarded."
    )


async def test_fr9_ac7_the_assistant_gets_every_type_when_it_names_none(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    for none in ({"roleTypes": []}, {"roleTypes": None}, {}):
        assert await found(page, none) == ISRAEL_IDS


async def test_fr9_ac8_a_job_must_fit_both_the_levels_and_the_role_types(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "types", "Product")
    assert await shown(page) == PRODUCT
    for level in ("Intern", "Junior", "Not specified"):  # Product with every level is Product at any seniority
        await choose(page, "levels", level)
    assert await shown(page) == PRODUCT
    await choose(page, "levels", "Not specified")  # Product with Intern and Junior is only the entry-level ones
    assert await shown(page) == {"pmi", "pmj"}
    await choose(page, "types", "")
    assert await shown(page) == {"pmi", "pmj", "fin"}  # the levels alone: entry-level jobs of any field
    await choose(page, "levels", "")
    await choose(page, "levels", "Junior")
    await choose(page, "types", "Finance")
    assert await shown(page) == set()  # a Junior job of the Finance type: there is none
    assert await page.locator("#list").inner_text() == "No roles match. Change the search or the filters."
    await choose(page, "levels", "Intern")
    assert await shown(page) == {"fin"}


async def test_fr9_ac8_the_assistant_applies_the_same_rule(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    every = ["Intern", "Junior", "Not specified"]
    assert await found(page, {"roleTypes": ["Product"]}) == PRODUCT
    assert await found(page, {"roleTypes": ["Product"], "levels": every}) == PRODUCT
    assert await found(page, {"roleTypes": ["Product"], "levels": ["Intern", "Junior"]}) == {"pmi", "pmj"}
    assert await found(page, {"roleTypes": "Product", "levels": "Intern"}) == {"pmi"}  # one string is a list of one
    for unknown in ({"levels": ["Senior"]}, {"roleTypes": ["Legal"]}, {"regions": ["Narnia"]}):
        assert await found(page, unknown) == set()  # a value nobody has matches nothing


async def test_fr3_ac8_jobs_outside_israel_are_the_abroad_region(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    assert "lon" not in await shown(page)
    total = len(ISRAEL_IDS)
    expected = f"Showing {total} of {total} open roles in Israel. 0 discarded."
    assert await page.locator("#summary").inner_text() == expected
    await page.click(ABROAD_CHIP)
    summary = await page.locator("#summary").inner_text()
    assert summary == f"Showing {len(PLACED_ABROAD)} of {len(filter_rows())} open roles. 0 discarded."  # no "Israel"
    assert await shown(page) == PLACED_ABROAD  # "Remote" with no country is not "abroad": it has its own switch
    await choose(page, "types", "Product")  # the other filters keep working on jobs abroad
    assert await shown(page) == {"lon"}
    await choose(page, "regions", SHARON)  # any of the chosen regions: Abroad and the Sharon
    assert await shown(page) == {"lon", "pmj"}
    await page.click(ABROAD_CHIP)
    assert await shown(page) == {"pmj"}
    names = await page.locator("#regions .chip").all_text_contents()
    assert names[:2] == ["All", TEL_AVIV]
    assert names[-2:] == [REMOTE, "Abroad"]


async def test_fr3_ac8_the_assistant_asks_for_abroad_as_a_region(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    assert await found(page, {}) == ISRAEL_IDS
    assert await found(page, {"regions": ["Abroad"]}) == PLACED_ABROAD
    assert await found(page, {"regions": ["Abroad", SHARON]}) == PLACED_ABROAD | {"pmj"}
    old = await ask(page, "search_jobs", {"allLocations": True, "locations": ["London, UK"]})  # the removed arguments
    assert {j["id"].split("|")[1] for j in old["jobs"]} == ISRAEL_IDS
    assert len(old["warnings"]) == len(["allLocations", "locations"])
    assert all("Abroad" in w for w in old["warnings"])


async def test_fr3_ac9_a_user_can_pick_one_or_more_regions(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "regions", SHARON)
    assert await shown(page) == {"pmj"}
    await choose(page, "regions", TEL_AVIV)  # more than one region: a job in either
    assert await shown(page) == {"pm", "pmi", "pmj", "multi"}
    await choose(page, "regions", JERUSALEM)
    assert await shown(page) == {"pm", "pmi", "pmj", "multi", "pd"}
    await choose(page, "regions", "")
    assert await shown(page) == ISRAEL_IDS  # nothing chosen: every role in Israel, those without a region too
    assert {"fin", "da"} <= await shown(page)


async def test_fr3_ac4_a_job_with_several_cities_belongs_to_every_region_it_names(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "regions", NORTH)
    assert await shown(page) == {"dev", "multi"}
    await choose(page, "regions", NORTH)
    await choose(page, "regions", TEL_AVIV)
    assert "multi" in await shown(page)


async def test_fr3_ac9_choices_are_remembered_in_this_browser(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "regions", TEL_AVIV)
    await choose(page, "levels", "Intern")
    await choose(page, "types", "Product")
    await page.click("#remote")
    await page.reload()
    await page.wait_for_selector("#list article")
    for group, value in (("regions", TEL_AVIV), ("levels", "Intern"), ("types", "Product")):
        assert (
            await page.locator(f'.chip[data-group={group}][data-value="{value}"]').get_attribute("aria-pressed")
            == "true"
        )
    assert await page.locator("#remote").get_attribute("aria-pressed") == "true"
    assert await shown(page) == {"pmi"}  # Intern and Product, in Tel Aviv
    await page.click("#reset")
    assert await page.locator("#levels .chip[aria-pressed=true]").all_text_contents() == ["All"]
    assert await page.locator("#remote").get_attribute("aria-pressed") == "false"


async def test_choices_saved_by_an_older_page_or_for_a_value_that_is_gone_do_not_hide_roles(
    page: Page, site: Path
) -> None:
    stale = {"cats": ["pm"], "regions": ["Narnia"], "levels": "Intern", "types": ["Legal", "Product"], "places": 7}
    await page.add_init_script(f"localStorage.setItem('il-jobs-filters-v3', {json.dumps(json.dumps(stale))})")
    await page.add_init_script("localStorage.setItem('il-jobs-filters-v2', '{\"cats\":[\"intern\"]}')")
    await serve_filters(page, site)
    assert await page.locator("#regions .chip[aria-pressed=true]").all_text_contents() == ["All"]
    assert await page.locator("#levels .chip[aria-pressed=true]").all_text_contents() == ["All"]
    assert await page.locator("#types .chip[aria-pressed=true]").all_text_contents() == ["Product"]
    assert await shown(page) == PRODUCT


@pytest.mark.parametrize(
    ("location", "not_said"),
    [
        ("Remote", True),
        ("remote", True),
        ("Fully Remote", True),
        ("Remote - Flexible", True),
        ("Remote, US", False),
        ("US Remote", False),
        ("Remote (Portugal)", False),
        ("Remote - Worldwide", False),
        ("London, UK", False),
        ("", False),
    ],
)
async def test_fr3_ac6_remote_roles_that_do_not_say_where_are_left_out_unless_asked(
    page: Page, site: Path, location: str, not_said: bool
) -> None:
    rows = [
        job("x", "Developer", location, regions=[], locations=[]),
        job("tlv", "Dev", "Tel Aviv, Israel", regions=[TEL_AVIV]),
    ]
    await serve(page, site, backend=Backend(rows))
    assert await shown(page) == {"tlv"}
    await page.click("#remote")
    assert await shown(page) == ({"tlv", "x"} if not_said else {"tlv"})


async def test_fr3_ac6_the_remote_option_adds_them_under_remote_and_says_so_on_the_card(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "regions", REMOTE)
    assert await shown(page) == {"rem"}  # Remote only when the posting is open to Israel
    await page.click("#remote")
    assert await shown(page) == {"rem", "bare"}  # "Remote, US" stays out
    in_scope = len(ISRAEL_IDS | {"bare"})
    assert await page.locator("#summary").inner_text() == f"Showing 2 of {in_scope} open roles in Israel. 0 discarded."
    card = page.locator("article[data-job-id='Acme|bare']")
    assert await card.get_attribute("data-remote-country-not-given") == "true"
    assert await card.get_attribute("data-regions") == ""  # never labelled Remote
    assert "Region: Remote, country not given" in await tags(page, "bare")
    await choose(page, "regions", "")
    assert await shown(page) == ISRAEL_IDS | {"bare"}


async def test_fr3_ac10_the_region_or_place_is_on_the_card_and_in_the_data_hooks(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    multi = page.locator("article[data-job-id='Acme|multi']")
    assert await multi.get_attribute("data-regions") == f"{TEL_AVIV}|{NORTH}"
    assert f"Region: {TEL_AVIV}" in await tags(page, "multi")
    assert f"Region: {NORTH}" in await tags(page, "multi")
    assert MULTI in await tags(page, "multi")  # the location as written stays
    fin = page.locator("article[data-job-id='Acme|fin']")
    assert await fin.get_attribute("data-locations") == KFAR
    assert await fin.get_attribute("data-regions") == ""
    assert (await tags(page, "fin")).count(KFAR) == 1  # not shown twice
    assert f"Place: {NO_CITY}" in await tags(page, "da")


async def test_fr3_ac10_the_assistant_can_read_the_region_level_and_role_type(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    got = await ask(page, "get_job", {"id": "Acme|multi"})
    assert got["regions"] == [TEL_AVIV, NORTH]
    assert (got["level"], got["roleTypes"], got["inIsrael"]) == ("Not specified", ["Product"], True)
    assert (await ask(page, "get_job", {"id": "Acme|fin"}))["locations"] == [KFAR]
    bare = await ask(page, "get_job", {"id": "Acme|bare"})
    assert (bare["regions"], bare["remoteCountryNotGiven"], bare["inIsrael"]) == ([], True, False)
    assert await found(page, {"regions": [NORTH]}) == {"dev", "multi"}
    assert await found(page, {"regions": [REMOTE], "includeRemoteNotSaid": True}) == {"rem", "bare"}
    assert await found(page, {"regions": [REMOTE]}) == {"rem"}
    both = {"regions": ["Abroad", REMOTE], "includeRemoteNotSaid": True}
    assert await found(page, both) == PLACED_ABROAD | {
        "rem",
        "bare",
    }  # Remote is a region, and so is Abroad: any of them


async def test_the_assistant_can_list_the_values_of_every_filter(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    filters = await ask(page, "list_filters")
    levels = [(f["name"], f["roles"]) for f in filters["levels"]]
    assert levels == [("Intern", 2), ("Junior", 1), ("Not specified", len(ISRAEL_IDS) - 3)]
    assert {f["name"]: f["roles"] for f in filters["roleTypes"]}["Product"] == len(PRODUCT)
    assert {f["name"]: f["roles"] for f in filters["regions"]}[NORTH] == len({"dev", "multi"})
    assert "locations" not in filters
    assert {f["name"]: f["roles"] for f in filters["regions"]}["Abroad"] == len(PLACED_ABROAD)


async def test_a_role_type_added_later_needs_no_change_to_the_filters(page: Page, site: Path) -> None:
    rows = [*filter_rows(), job("law", "Counsel", "Haifa, Israel", role_types=["Legal"], regions=[NORTH])]
    await page.add_init_script(WEBMCP)
    await serve(page, site, backend=Backend(rows))
    await choose(page, "types", "Legal")
    assert await shown(page) == {"law"}
    enum = await page.evaluate("window.__tools.search_jobs.inputSchema.properties.roleTypes.items.enum")
    assert enum[-2:] == ["Other", "Legal"]


async def test_the_page_data_carries_the_names_the_filters_are_built_from(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    meta = json.loads(await page.locator("#page-data").text_content() or "{}")
    assert meta["levels"] == ["Intern", "Junior", "Not specified"]
    assert meta["roleTypes"][0] == "Product"
    assert meta["roleTypes"][-1] == "Other"
    assert meta["regions"] == [TEL_AVIV, SHARON, "Center", JERUSALEM, NORTH, "South", REMOTE]


async def test_the_filters_read_well_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(FEW)
    await serve_filters(page, site)
    await page.click("#filtersOpen")  # on a phone the filters are a bottom sheet
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    targets = page.locator(".chip, #filtersOpen, #company, #sort")
    for target in await targets.all():
        box = await target.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= FEW["width"]
    for group in ("levels", "types", "regions"):  # the chips wrap instead of scrolling sideways
        assert await page.locator(f"#{group}").evaluate("e => e.scrollWidth <= e.clientWidth")


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_filters_have_no_axe_violations_when_chosen_and_open(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await serve_filters(page, site)
    await choose(page, "levels", "Intern")
    await choose(page, "types", "Product")
    await choose(page, "regions", TEL_AVIV)
    await page.click("#remote")
    await page.click(ABROAD_CHIP)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


async def test_the_assistant_is_told_about_values_that_match_nothing(page: Page, site: Path) -> None:
    await serve_filters(page, site, tools=True)
    answer = await ask(page, "search_jobs", {"levels": ["intern"], "category": "product", "regions": [5]})
    assert answer["total"] == 0  # the lower-case level matches nothing, as on the page
    assert len(answer["warnings"]) == len(["level", "category", "region"])
    assert any("intern" in w for w in answer["warnings"])
    assert "warnings" not in await ask(page, "search_jobs", {"levels": ["Intern"]})


async def test_a_row_without_the_classification_columns_is_an_error_not_unclassified_roles(
    page: Page, site: Path
) -> None:
    bad = {k: v for k, v in filter_rows()[0].items() if k != "regions"}
    await serve(page, site, wait=False, backend=Backend([bad]))
    await expect(page.locator("#load-error")).to_be_visible()


async def test_a_saved_choice_of_the_wrong_type_or_a_company_that_is_gone_is_dropped(page: Page, site: Path) -> None:
    saved = {"q": 7, "company": "Gone Inc", "sort": 3, "remote": "yes", "onlyNew": 1}
    await page.add_init_script(f"localStorage.setItem('il-jobs-filters-v3', {json.dumps(json.dumps(saved))})")
    await serve_filters(page, site)
    assert await shown(page) == ISRAEL_IDS
    assert await page.locator("#company").input_value() == ""


async def test_reset_keeps_the_search_words(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await page.fill("#q", "product")
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await page.click("#reset")
    assert await page.input_value("#q") == "product"
    assert await page.locator("#levels .chip[aria-pressed=true]").all_text_contents() == ["All"]
    assert json.loads(await page.evaluate("localStorage.getItem('il-jobs-filters-v3')"))["q"] == "product"


async def test_reset_can_be_undone_for_a_few_seconds(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await page.fill("#q", "product")
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await page.click("#reset")
    note = page.locator("#resetNote")
    assert await note.get_attribute("role") == "status"
    await expect(note).to_contain_text("Filters reset.")
    undo = page.locator("#undoReset")
    box = await undo.bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN
    await page.fill("#q", "product manager")  # typed while the offer shows: the search words stay what they are now
    assert await note.inner_text() == ""  # any other change ends the offer: it would restore stale state
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await page.click("#reset")
    await page.click("#undoReset")
    assert await page.locator("#levels .chip[aria-pressed=true]").all_text_contents() == ["Junior"]
    assert await page.input_value("#q") == "product manager"
    assert await note.inner_text() == ""
    assert await page.evaluate("document.activeElement.id") == "reset"  # the focus goes back, never to the page
    saved = json.loads(await page.evaluate("localStorage.getItem('il-jobs-filters-v3')"))
    assert saved["levels"] == ["Junior"]


async def test_reset_puts_the_sort_and_the_checkbox_back_and_undo_restores_them(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await page.select_option("#sort", "co")
    await page.check("#hideStale")
    await page.click("#reset")
    assert await page.input_value("#sort") == "new"
    assert not await page.is_checked("#hideStale")
    await page.click("#undoReset")
    assert await page.input_value("#sort") == "co"
    assert await page.is_checked("#hideStale")


async def test_the_reset_offer_goes_away_by_itself(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await page.click("#reset")
    await expect(page.locator("#undoReset")).to_have_count(1)
    await expect(page.locator("#resetNote")).to_be_empty(timeout=9000)
    assert await page.locator("#levels .chip[aria-pressed=true]").all_text_contents() == ["All"]


async def test_a_filter_saved_with_the_removed_location_or_places_shows_the_default_and_is_not_written_back(
    page: Page, site: Path
) -> None:
    old = {"loc": "all", "places": [KFAR], "levels": ["Intern"]}
    await page.add_init_script(f"localStorage.setItem('il-jobs-filters-v3', {json.dumps(json.dumps(old))})")
    await serve_filters(page, site)
    assert await shown(page) == {"pmi", "fin"}  # the level is kept; neither a place nor "all locations" hides roles
    assert await page.locator("#filtersOpen").inner_text() == "Filters (1)"  # only the level counts
    await choose(page, "types", "Finance")  # a change saves the clean filters
    saved = json.loads(await page.evaluate("localStorage.getItem('il-jobs-filters-v3')"))
    assert "loc" not in saved and "places" not in saved


async def test_the_company_has_a_heading_and_the_old_roles_have_a_count(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    assert await page.locator("h3#company-h").inner_text() == "Company"
    assert await page.locator("#company").get_attribute("aria-labelledby") == "company-h"
    assert await page.locator("#reset").inner_text() == "Reset"
    assert await page.locator("label:has(#hideStale)").inner_text() == "Hide roles marked Old? (0)"
    assert await page.locator("#hideStale").is_enabled()  # a plain checkbox, whatever the count


@pytest.mark.parametrize("control", ["company", "sort"])
async def test_the_selects_have_a_heading_like_the_other_groups_and_keep_their_name(
    page: Page, site: Path, control: str
) -> None:
    await serve_filters(page, site)
    group = page.locator(f".sheet-body > .filter-group:has(> h3.label#{control}-h)")
    assert await group.count() == 1
    assert await group.locator("> h3").inner_text() == control.capitalize()
    assert await group.locator(f"> select#{control}[aria-labelledby={control}-h]").count() == 1
    assert await page.locator(f"label:has(#{control})").count() == 0  # one name, from the heading, not announced twice
    assert await page.get_by_role("combobox", name=control.capitalize(), exact=True).count() == 1


async def test_the_two_checkboxes_are_a_named_group_and_the_headings_nest_under_the_sheet_title(
    page: Page, site: Path
) -> None:
    await serve_filters(page, site)
    group = page.get_by_role("group", name="Visibility", exact=True)
    assert await group.locator("input[type=checkbox]").evaluate_all("els => els.map(e => e.id)") == [
        "hideStale",
        "showGone",
    ]
    heads = await page.locator("#moreFilters :is(h2, h3)").evaluate_all(
        "els => els.map(e => e.tagName + e.textContent)"
    )
    assert heads == ["H2Filters", "H3Level", "H3Role type", "H3Region", "H3Company", "H3Sort", "H3Visibility"]


async def test_the_old_roles_count_is_what_ticking_hide_would_remove(page: Page, site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    (site / "index.html").write_text(
        html.replace('"blocked":[]', '"blocked":[{"c":"Acme","why":"x","last":"never"}]'), encoding="utf-8"
    )
    await serve_filters(page, site)
    label = page.locator("label:has(#hideStale)")
    assert await label.inner_text() == f"Hide roles marked Old? ({len(ISRAEL_IDS)})"  # Acme's roles in Israel
    await choose(page, "levels", "Intern")  # the count follows the other filters
    assert await label.inner_text() == "Hide roles marked Old? (2)"
    await page.check("#hideStale")
    assert await label.inner_text() == "Hide roles marked Old? (2)"  # ticking it does not zero the count
    assert await shown(page) == set()


async def test_the_abroad_roles_are_not_copied_into_the_job_alert_filters(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await page.click(ABROAD_CHIP)
    await choose(page, "regions", SHARON)
    copied = await page.evaluate("globalThis.misraEmailFilters()")
    assert copied["regions"] == [SHARON]  # the email carries roles in Israel only
    await page.click(".chip[data-group=regions][data-value=Sharon]")
    assert (await page.evaluate("globalThis.misraEmailFilters()"))["regions"] == []


async def test_the_remote_option_is_remembered_and_changes_what_is_shown(page: Page, site: Path) -> None:
    await serve_filters(page, site)
    await choose(page, "regions", REMOTE)
    await page.click("#remote")
    await page.reload()
    await page.wait_for_selector("#list article")
    assert await shown(page) == {"rem", "bare"}


CUT = "A text of two hundred characters or more that was cut at a word boundary and so does not end a sentence " * 2
WHOLE = "A short posting that ends here."
HTML = "<script>window.pwned = 1</script><b>bold</b> text"


async def test_a_card_shows_its_snippet_as_text_and_none_when_there_is_none(page: Page, site: Path) -> None:
    rows = [
        job("cut", "Cut", "Haifa, Israel", snippet=CUT.strip(), snippet_cut=True),
        job("whole", "Whole", "Haifa, Israel", snippet=WHOLE, snippet_cut=False),
        job("html", "Html", "Haifa, Israel", snippet=HTML),
        job("none", "None", "Haifa, Israel", snippet=None),
        job("blank", "Blank", "Haifa, Israel", snippet="  "),
        job("hebrew", "Hebrew", "Haifa, Israel", snippet="משרה בחיפה"),
    ]
    problems = await serve(page, site, backend=Backend(rows))
    assert problems == []

    def snippet(job_id: str) -> Any:
        return page.locator(f"article[data-job-id='Acme|{job_id}'] .card-snippet")

    assert await snippet("whole").text_content() == WHOLE  # whole text: no ellipsis
    assert await snippet("cut").text_content() == CUT.strip() + "…"  # a cut text gets the page's ellipsis
    assert await snippet("cut").locator("[aria-hidden=true]").text_content() == "…"  # which a screen reader skips
    assert await snippet("html").text_content() == HTML  # text, never markup
    assert await snippet("html").locator("*").count() == 0
    assert await page.evaluate("window.pwned") is None
    assert await snippet("hebrew").get_attribute("dir") == "auto"
    for none in ("none", "blank"):
        assert await snippet(none).count() == 0  # nothing is shown, not even an empty line
    title = page.locator("article[data-job-id='Acme|whole'] .card-title")
    assert await title.evaluate("e => e.nextElementSibling.className") == "card-snippet"  # read right after the title


async def test_an_assistant_gets_the_snippet_and_null_when_a_role_has_none(page: Page, site: Path) -> None:
    await page.add_init_script(WEBMCP)
    rows = [job("a", "A", "Haifa, Israel", snippet=WHOLE), job("b", "B", "Haifa, Israel")]
    await serve(page, site, backend=Backend(rows))
    jobs = {j["id"]: j for j in (await ask(page, "search_jobs", {"limit": 10}))["jobs"]}
    assert jobs["Acme|a"]["snippet"] == WHOLE
    assert jobs["Acme|b"]["snippet"] is None


async def test_the_ellipsis_follows_the_stored_cut_flag_and_nothing_else(page: Page, site: Path) -> None:
    cases = {
        "cut-short": ("a" * 50, True),  # a cut under 200 characters (a very long word follows): the flag decides
        "whole-long-bracket": ("a" * 254 + ")", False),  # a whole text of 200 or more that ends in a bracket
        "whole-long-no-end": ("a" * 230, False),
        "cut-sentence": ("a" * 199 + ".", True),  # a cut can end a sentence too
        "missing": ("a" * 230, None),  # a row from before the column: nothing, not a guess
    }
    rows = [
        job(job_id, job_id, "Haifa, Israel", snippet=text, **({} if flag is None else {"snippet_cut": flag}))
        for job_id, (text, flag) in cases.items()
    ]
    assert await serve(page, site, backend=Backend(rows)) == []

    async def ellipses(job_id: str) -> int:
        return await page.locator(f"article[data-job-id='Acme|{job_id}'] .card-snippet [aria-hidden=true]").count()

    assert [await ellipses(j) for j in cases] == [1, 0, 0, 1, 0]


async def test_the_page_asks_for_the_cut_flag_with_the_other_public_columns(page: Page, site: Path) -> None:
    backend = Backend([job("a", "A", "Haifa, Israel", snippet="Some text", snippet_cut=True)])
    await serve(page, site, backend=backend)
    assert "snippet_cut" in parse_qs(urlparse(backend.requests[0].url).query)["select"][0].split(",")
