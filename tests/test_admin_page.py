"""The admin page (PLAN_2.md step 1.2), built and run in a real browser against fake backends: the sign-in service and
the `is_admin()` function. No real account or credential is used. Locally the tests skip when Chromium is missing; in CI
(CI=true) a missing browser is a failure."""

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.fake_companies import REST_CORS, FakeTables
from tests.test_dashboard_page import (
    AXE,
    CORS,
    EMAIL,
    NAME,
    ORIGIN,
    PHONE,
    TAP_MIN,
    AuthBackend,
    site,  # noqa: F401  (a fixture)
)
from tests.test_sign_in import AXE_RUN, STORAGE_KEY

REPO = Path(__file__).resolve().parents[1]
RPC = f"{build_dashboard.SUPABASE_URL}/rest/v1/rpc/is_admin"
HEALTH = f"{build_dashboard.SUPABASE_URL}/rest/v1/rpc/admin_crawl_health"
ALERTS = f"{build_dashboard.SUPABASE_URL}/rest/v1/rpc/admin_alert_counts"
REST = f"{build_dashboard.SUPABASE_URL}/rest/v1/**"
FUNCTIONS = f"{build_dashboard.SUPABASE_URL}/functions/v1/**"
ACCOUNT = f"{build_dashboard.SUPABASE_URL}/functions/v1/admin-account"
ACCOUNT_CORS = {
    **CORS,
    "access-control-allow-headers": "authorization, apikey, content-type",
    "access-control-allow-methods": "POST, OPTIONS",
}
DONE = {"ok": True, "result": "done", "message": "Done."}
DESKTOP = {"width": 1280, "height": 900}
AREAS = ["Crawl", "Companies", "Alerts", "Log"]
CRAWL_EMPTY = "No crawl results yet."
STATES = ["signed-out", "not-admin", "check-failed", "ready"]
WIDE_MIN = 761  # wider than the 760px reading width of the other pages
WIDE_MAX = 1000
POLL_SECONDS = 0.05
CHECK_TIMEOUT_MS = 15000
CHECKS_WITH_RENEWAL = 2  # the check, then one renewal of the token and the check again
FOOTER_LINKS = 7


class FailingAuth(AuthBackend):
    """The sign-in service answering a token request with a 500, which the library does not retry."""

    async def handle(self, route: Route) -> None:
        if route.request.method != "OPTIONS" and urlparse(route.request.url).path.endswith("/token"):
            await route.fulfill(status=500, headers=CORS, body='{"message":"boom"}', content_type="application/json")
            return
        await super().handle(route)


class Backend:
    """The fake `is_admin()`, `admin_crawl_health()` and sign-in service. `answer` is one of: admin, not_admin,
    unauthorized (401 always), unauthorized_once (401, then true), down (no answer) or broken (503). `health` is the
    list of rows the crawl function returns (or None for a JSON null); `health_answer` is rows, unauthorized (401
    always), unauthorized_once, broken_once (503, then rows), broken (503 always) or not_a_list. `recheck_answer`, when
    set, replaces `answer` for the checks made after the crawl function was refused. Every request to the project is
    recorded."""

    def __init__(  # noqa: PLR0913  (each choice is one thing a test varies)
        self,
        answer: str = "admin",
        auth: AuthBackend | None = None,
        *,
        health: list[dict[str, object]] | None = None,
        health_answer: str = "rows",
        recheck_answer: str | None = None,
        companies: list[dict[str, object]] | None = None,
        jobs: list[tuple[str, str]] | None = None,
        alerts: list[dict[str, object]] | None = None,
        alerts_answer: str = "rows",
        log: list[dict[str, object]] | None = None,
        log_answer: str = "rows",
        account_answers: list[tuple[int, object]] | None = None,
    ) -> None:
        self.answer = answer
        # What the fake `admin-account` function answers, one entry for each call (the last one repeats): a status and
        # a JSON body (None for a bare answer), or the status 0 for no answer at all.
        self.account_answers = [(200, DONE)] if account_answers is None else account_answers
        self.account_calls: list[dict[str, object]] = []
        self.account_gate: asyncio.Event | None = None
        self.alerts = [] if alerts is None else alerts
        self.alerts_answer = alerts_answer  # rows, unauthorized, unauthorized_once, broken, broken_once, not_a_list
        self.alerts_asked = 0
        self.alerts_gate: asyncio.Event | None = None
        self.alerts_bodies: list[str | None] = []
        self.log = [] if log is None else log
        self.log_answer = log_answer  # the same choices, for a read of `admin_log`
        self.log_asked = 0
        self.log_gate: asyncio.Event | None = None
        self.log_queries: list[dict[str, list[str]]] = []
        self.health = [] if health is None else health
        self.health_answer = health_answer
        self.recheck_answer = recheck_answer
        self.health_asked = 0
        self.health_gate: asyncio.Event | None = None
        self.health_bodies: list[str | None] = []
        self.auth = auth or AuthBackend()
        self.tables = FakeTables(companies or [], jobs or [])
        self.strays: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.asked = 0
        self.gate: asyncio.Event | None = None  # when set, the answer waits for it

    async def rpc(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append((request.method, urlparse(request.url).path))
        self.asked += 1
        if self.gate is not None:
            await self.gate.wait()
        answer = self.recheck_answer if self.recheck_answer and self.health_asked > 0 else self.answer
        match answer:
            case "admin" | "unauthorized_once" if answer == "admin" or self.asked > 1:
                await route.fulfill(headers=CORS, body="true", content_type="application/json")
            case "not_admin":
                await route.fulfill(headers=CORS, body="false", content_type="application/json")
            case "unauthorized" | "unauthorized_once":
                await route.fulfill(
                    status=401, headers=CORS, body='{"message":"JWT expired"}', content_type="application/json"
                )
            case "down":
                await route.abort()
            case _:
                await route.fulfill(status=503, headers=CORS, body="unavailable")

    async def crawl_health(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append((request.method, urlparse(request.url).path))
        self.health_bodies.append(request.post_data)
        self.health_asked += 1
        if self.health_gate is not None:
            await self.health_gate.wait()
        refused = self.health_answer == "unauthorized" or (
            self.health_answer == "unauthorized_once" and self.health_asked == 1
        )
        broken = self.health_answer == "broken" or (self.health_answer == "broken_once" and self.health_asked == 1)
        if refused:
            await route.fulfill(
                status=401, headers=CORS, body='{"message":"JWT expired"}', content_type="application/json"
            )
        elif broken:
            await route.fulfill(status=503, headers=CORS, body="unavailable")
        elif self.health_answer == "not_a_list":
            await route.fulfill(headers=CORS, body='{"rows": 1}', content_type="application/json")
        else:
            await route.fulfill(headers=CORS, body=json.dumps(self.health), content_type="application/json")

    async def answering(self, route: Route, kind: str, rows: object, asked: int) -> None:
        """The answer of a call that is counted in the database: rows, or the refusal the test chose."""
        choice = getattr(self, f"{kind}_answer")
        refused = choice == "unauthorized" or (choice == "unauthorized_once" and asked == 1)
        broken = choice == "broken" or (choice == "broken_once" and asked == 1)
        if refused:
            await route.fulfill(
                status=401, headers=REST_CORS, body='{"message":"JWT expired"}', content_type="application/json"
            )
        elif broken:
            await route.fulfill(status=500, headers=REST_CORS, body="unavailable")  # not 503: the library retries a GET
        elif choice == "not_a_list":
            await route.fulfill(headers=REST_CORS, body='{"rows": 1}', content_type="application/json")
        else:
            await route.fulfill(headers=REST_CORS, body=json.dumps(rows), content_type="application/json")

    async def alert_counts(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append((request.method, urlparse(request.url).path))
        self.alerts_bodies.append(request.post_data)
        self.alerts_asked += 1
        if self.alerts_gate is not None:
            await self.alerts_gate.wait()
        await self.answering(route, "alerts", self.alerts, self.alerts_asked)

    async def admin_log(self, route: Route) -> None:
        """A read of `admin_log` as PostgREST answers it: newest first, `limit` rows, the columns the page asked for."""
        query = parse_qs(urlparse(route.request.url).query)
        self.log_queries.append(query)
        self.log_asked += 1
        if self.log_gate is not None:
            await self.log_gate.wait()
        columns = query.get("select", ["*"])[0].split(",")
        newest = sorted(self.log, key=lambda r: (str(r["ts"]), int(str(r["id"]))), reverse=True)
        rows = [{c: row[c] for c in columns} for row in newest[: int(query.get("limit", ["1000"])[0])]]
        await self.answering(route, "log", rows, self.log_asked)

    async def account(self, route: Route) -> None:
        """The `admin-account` function: records what the page sent (never printed) and answers as the test chose."""
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=ACCOUNT_CORS)
            return
        self.requests.append((request.method, urlparse(request.url).path))
        self.account_calls.append({"headers": request.headers, "body": request.post_data, "url": request.url})
        if self.account_gate is not None:
            await self.account_gate.wait()
        status, body = self.account_answers[min(len(self.account_calls), len(self.account_answers)) - 1]
        if status == 0:
            await route.abort()
        elif body is None:
            await route.fulfill(status=status, headers=ACCOUNT_CORS)
        else:
            await route.fulfill(
                status=status, headers=ACCOUNT_CORS, body=json.dumps(body), content_type="application/json"
            )

    async def functions(self, route: Route) -> None:
        """Any other function: refused and recorded, so a test never reaches a real one."""
        if route.request.method == "OPTIONS":
            await route.fulfill(status=204, headers=ACCOUNT_CORS)
            return
        self.strays.append(urlparse(route.request.url).path)
        await route.abort()

    async def rest(self, route: Route) -> None:
        """Every other request to the project's tables: the fake `companies` and `jobs` (tests/fake_companies.py), and
        anything else is refused and recorded, so a test never reaches a real project."""
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=REST_CORS)
            return
        path = urlparse(request.url).path
        self.requests.append((request.method, path))
        if path == "/rest/v1/admin_log" and request.method == "GET":
            await self.admin_log(route)
            return
        if path not in ("/rest/v1/companies", "/rest/v1/jobs"):
            self.strays.append(path)
            await route.abort()
            return
        await self.tables.handle(route)
        if path == "/rest/v1/companies" and request.method != "GET":  # the trigger of the table logs an admin's write
            self.log.append(
                {
                    "id": len(self.log) + 1,
                    "ts": "2026-11-18T12:00:00+00:00",
                    "action": "company changed",
                    "target": "Acme",
                }
            )

    async def auth_route(self, route: Route) -> None:
        if route.request.method != "OPTIONS":
            self.requests.append((route.request.method, urlparse(route.request.url).path))
        await self.auth.handle(route)

    def paths(self) -> list[str]:
        return [path for _method, path in self.requests]


async def open_admin(  # noqa: PLR0913  (each choice is one thing a test varies)
    page: Page,
    docs: Path,
    *,
    backend: Backend | None = None,
    signed_in: bool = True,
    expired: bool = False,
    hash_: str = "",
    wait: bool = True,
) -> tuple[Backend, list[str]]:
    """Serve docs/ at ORIGIN and the project from the fake, with (or without) the session the dashboard would have
    saved, and open admin.html. Returns the fake and the console errors seen."""
    backend = backend or Backend()
    problems: list[str] = []

    async def files(route: Route) -> None:
        name = urlparse(route.request.url).path.lstrip("/") or "index.html"
        file = docs / name
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", files)
    await page.route(REST, backend.rest)  # first: a later route wins, so the functions below take their own paths
    await page.route(RPC, backend.rpc)
    await page.route(HEALTH, backend.crawl_health)
    await page.route(ALERTS, backend.alert_counts)
    await page.route(FUNCTIONS, backend.functions)  # before the one function the page may call: a later route wins
    await page.route(ACCOUNT, backend.account)
    await page.route(f"{build_dashboard.SUPABASE_URL}/auth/v1/**", backend.auth_route)
    if signed_in:
        session = backend.auth.session()
        if expired:
            session["expires_at"] = 1  # the library renews it before use
        await page.add_init_script(f"localStorage.setItem({STORAGE_KEY!r}, {json.dumps(json.dumps(session))});")
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}/admin.html{hash_}")
    if wait:
        await expect(page.locator("main")).not_to_have_attribute("data-state", "checking")
    return backend, problems


async def state(page: Page) -> str | None:
    return await page.locator("main").get_attribute("data-state")


async def visible_text(page: Page) -> str:
    return await page.locator("main").inner_text()


async def visible_lines(page: Page) -> list[str]:
    return [line for line in (await visible_text(page)).splitlines() if line]


# ---- what is built ----


def test_the_page_is_built_noindex_under_the_csp_with_its_scripts_hashed(site: Path) -> None:
    html = (site / "admin.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in html
    assert "<title>Admin</title>" in html
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp
    assert csp[1] == (
        f"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        f"connect-src {build_dashboard.SUPABASE_URL}; base-uri 'none'; form-action 'none'"
    )
    assert "<script>" not in html
    assert " style=" not in html
    assert re.search(r"__[A-Z_]+__", html) is None  # every placeholder was filled
    for name in ("admin.js", "supabase.js", "theme.js", "dashboard.css"):
        assert re.search(rf'(src|href)="{re.escape(name)}" integrity="sha384-[A-Za-z0-9+/]+=*"', html), name
    assert (site / "session.js").is_file()
    # the module both pages import is hashed too: the integrity of a script does not cover what it imports
    assert re.search(r'<link rel="modulepreload" href="session.js" integrity="sha384-[A-Za-z0-9+/]+=*">', html)
    assert "__SUPABASE" not in (site / "admin.js").read_text(encoding="utf-8")


def test_the_admin_page_is_linked_from_nowhere_and_is_not_an_agent_tool(site: Path) -> None:
    """It is not a secret, only never advertised: no page, the footer, llms.txt, the JSON-LD or the tools name it."""
    others = [
        p for p in site.iterdir() if p.suffix in {".html", ".js", ".json", ".jsonld"} and not p.name.startswith("admin")
    ]
    assert others
    for file in others:
        if file.name in {"supabase.js"}:
            continue
        assert "admin.html" not in file.read_text(encoding="utf-8"), file.name
    for source in [*(REPO / "templates").rglob("*"), REPO / "llms.txt", REPO / "README.md"]:
        if source.is_file() and not source.name.startswith("admin"):
            assert "admin.html" not in source.read_text(encoding="utf-8"), source.name
    assert "admin" not in (site / "jobs.jsonld").read_text(encoding="utf-8").lower()
    tools = (site / "dashboard.js").read_text(encoding="utf-8")
    assert "admin" not in tools.lower()


def test_the_admin_page_is_among_the_files_the_daily_run_publishes() -> None:
    daily = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    for name in ("admin.html", "admin.js", "session.js"):
        assert f"docs/{name}" in daily
    publish = (REPO / ".github" / "workflows" / "publish-docs.yml").read_text(encoding="utf-8")
    assert "templates/**" in publish  # the glob that covers the page, its script and the shared session module


# ---- the five states ----


async def test_signed_out_it_points_to_the_dashboard_and_asks_for_nothing(page: Page, site: Path) -> None:
    backend, problems = await open_admin(page, site, signed_in=False)
    assert problems == []
    assert await state(page) == "signed-out"
    assert backend.requests == []
    link = page.get_by_role("link", name="Sign in on the dashboard first.")
    await expect(link).to_be_visible()
    assert await link.get_attribute("href") == "./"
    assert await page.title() == "Admin"
    assert await page.locator("#adminSignOut").is_hidden()
    assert await page.locator("nav[aria-label='Admin']").is_hidden()
    assert await page.locator("#adminExpired").inner_text() == ""
    assert await page.evaluate("location.pathname + location.search") == "/admin.html"  # never a redirect


async def test_the_page_shows_nothing_while_it_checks(page: Page, site: Path) -> None:
    """The default state has every part hidden, so nothing flashes (not even the title) before the answer."""
    backend = Backend()
    backend.gate = asyncio.Event()
    await open_admin(page, site, backend=backend, wait=False)
    while backend.asked == 0:  # the check is in flight
        await asyncio.sleep(POLL_SECONDS)
    assert await state(page) == "checking"
    assert await visible_text(page) == ""
    assert await page.title() == "Admin"
    backend.gate.set()
    await expect(page.locator("main")).to_have_attribute("data-state", "ready")


async def test_a_signed_in_non_admin_sees_one_line_and_only_the_check_was_asked(page: Page, site: Path) -> None:
    backend, problems = await open_admin(page, site, backend=Backend("not_admin"))
    assert problems == []
    assert await state(page) == "not-admin"
    assert backend.requests == [("POST", "/rest/v1/rpc/is_admin")]  # nothing but the check, and no area loaded
    assert await visible_lines(page) == ["Admin", "Sign out", "This account is not an admin."]
    assert await page.locator("nav[aria-label='Admin']").is_hidden()
    assert await page.locator(".admin-area:visible").count() == 0
    assert await page.title() == "Admin"


@pytest.mark.parametrize("answer", ["down", "broken"])
async def test_a_failed_check_is_not_the_same_as_not_an_admin(page: Page, site: Path, answer: str) -> None:
    _backend, _problems = await open_admin(page, site, backend=Backend(answer))
    assert await state(page) == "check-failed"
    alert = page.get_by_role("alert")
    await expect(alert).to_have_text("Could not check your account. Reload the page.")
    assert "not an admin" not in await visible_text(page)
    await expect(page.get_by_role("button", name="Sign out")).to_be_visible()
    assert await page.locator("nav[aria-label='Admin']").is_hidden()


async def test_without_the_sign_in_library_the_check_fails_and_says_so(page: Page, site: Path) -> None:
    (site / "supabase.js").unlink()
    backend, _problems = await open_admin(page, site)
    assert await state(page) == "check-failed"
    assert backend.requests == []


async def test_a_saved_session_the_service_will_not_renew_says_it_expired(page: Page, site: Path) -> None:
    """The token cannot be renewed (the service answers 400): the page is signed out and says why."""
    auth = AuthBackend("bad_code")  # every token request is refused with invalid_grant
    backend, _problems = await open_admin(page, site, backend=Backend(auth=auth), expired=True)
    assert await state(page) == "signed-out"
    await expect(page.locator("#adminExpired")).to_have_text("Your sign-in expired.")
    assert await page.evaluate("localStorage.getItem('misra-auth')") is None
    assert not any(path.endswith("/rpc/is_admin") for path in backend.paths())  # the check never reached the function


async def test_an_unauthorized_answer_renews_the_token_once_and_asks_again(page: Page, site: Path) -> None:
    backend, _problems = await open_admin(page, site, backend=Backend("unauthorized_once"))
    assert await state(page) == "ready"
    assert backend.asked == CHECKS_WITH_RENEWAL
    assert any(path.endswith("/token") for path in backend.paths())


async def test_a_failed_renewal_is_not_a_refused_one_and_keeps_the_session(page: Page, site: Path) -> None:
    """The check is refused (401), then the service keeps failing the renewal (500s, which the library retries for
    minutes): nothing says the sign-in ended, so after 15 seconds the page says the check failed and leaves the saved
    session alone. It never asks again with the token that was refused."""
    await page.clock.install()
    backend = Backend("unauthorized", auth=FailingAuth())
    await open_admin(page, site, backend=backend, wait=False)
    while backend.asked == 0:
        await asyncio.sleep(POLL_SECONDS)
    await page.clock.run_for(CHECK_TIMEOUT_MS + 1000)
    await expect(page.locator("main")).to_have_attribute("data-state", "check-failed")
    assert await page.evaluate("localStorage.getItem('misra-auth')") is not None
    assert backend.asked == 1
    await expect(page.locator("#adminExpired")).to_have_text("")


async def test_a_renewal_the_service_refuses_says_the_sign_in_expired(page: Page, site: Path) -> None:
    backend = Backend("unauthorized", auth=AuthBackend("bad_code"))
    await open_admin(page, site, backend=backend)
    assert await state(page) == "signed-out"
    await expect(page.locator("#adminExpired")).to_have_text("Your sign-in expired.")
    assert await page.evaluate("localStorage.getItem('misra-auth')") is None


async def test_an_unauthorized_answer_twice_says_the_sign_in_expired(page: Page, site: Path) -> None:
    backend, _problems = await open_admin(page, site, backend=Backend("unauthorized"))
    assert await state(page) == "signed-out"
    assert backend.asked == CHECKS_WITH_RENEWAL  # one renewal, then it gives up
    await expect(page.locator("#adminExpired")).to_have_text("Your sign-in expired.")
    assert await page.locator("#adminSignOut").is_hidden()


async def test_it_saves_a_renewed_session_without_the_providers_name_and_photo(page: Page, site: Path) -> None:
    """The admin page writes the same storage key through the same wrapper as the dashboard."""
    _backend, _problems = await open_admin(page, site, expired=True)
    assert await state(page) == "ready"
    saved = await page.evaluate("localStorage.getItem('misra-auth')")
    assert saved
    assert NAME not in saved
    assert "provider_token" not in saved
    assert EMAIL in saved


# ---- the areas ----


async def test_ready_shows_the_crawl_area_empty_and_loads_each_area_on_its_first_show(page: Page, site: Path) -> None:
    backend, problems = await open_admin(page, site)
    assert problems == []
    assert await state(page) == "ready"
    # the check, then the Crawl area's one call; the other areas ask for nothing
    assert backend.requests == [("POST", "/rest/v1/rpc/is_admin"), ("POST", "/rest/v1/rpc/admin_crawl_health")]
    nav = page.get_by_role("navigation", name="Admin")
    assert await nav.get_by_role("link").all_inner_texts() == AREAS
    assert [await link.get_attribute("href") for link in await nav.get_by_role("link").all()] == [
        "#crawl",
        "#companies",
        "#alerts",
        "#log",
    ]
    assert await page.locator(".admin-area:visible").count() == 1
    await expect(page.get_by_role("heading", name="Crawl", level=2)).to_be_visible()
    assert await page.locator("#crawl").get_attribute("data-state") == "empty"
    assert await page.locator("#companies").get_attribute("data-state") is None  # not shown yet: not loaded
    assert await page.title() == "Crawl - Admin"
    await expect(nav.get_by_role("link", name="Crawl")).to_have_attribute("aria-current", "page")
    assert await nav.locator("[aria-current]").count() == 1
    assert CRAWL_EMPTY in await visible_text(page)
    await expect(page.get_by_role("button", name="Sign out")).to_be_visible()


async def test_the_log_area_says_no_changes_yet_when_the_log_is_empty(page: Page, site: Path) -> None:
    backend, _problems = await open_admin(page, site, hash_="#log")
    await expect(page.get_by_role("heading", name="Log", level=2)).to_be_visible()
    await expect(page.locator("#log .admin-state")).to_have_text("No changes yet.")
    assert backend.log_asked == 1


async def test_choosing_an_area_moves_the_focus_to_its_heading_and_the_title(page: Page, site: Path) -> None:
    await open_admin(page, site)
    nav = page.get_by_role("navigation", name="Admin")
    for name in AREAS[1:]:
        await nav.get_by_role("link", name=name).click()
        heading = page.get_by_role("heading", name=name, level=2)
        await expect(heading).to_be_focused()
        assert await heading.get_attribute("tabindex") == "-1"
        assert await page.title() == f"{name} - Admin"
        assert await page.locator(".admin-area:visible").count() == 1
        await expect(nav.get_by_role("link", name=name)).to_have_attribute("aria-current", "page")
        assert await nav.locator("[aria-current]").count() == 1
        await expect(page.locator(f"#{name.lower()}")).to_have_attribute("data-state", "empty")
    await page.go_back()  # the history works, and the focus follows it
    await expect(page.get_by_role("heading", name="Alerts", level=2)).to_be_focused()


async def test_the_keyboard_reaches_the_areas_in_order_and_enter_opens_one(page: Page, site: Path) -> None:
    await open_admin(page, site)
    await page.get_by_role("button", name="Sign out").focus()
    await page.keyboard.press("Tab")
    await expect(page.get_by_role("link", name="Crawl")).to_be_focused()
    for name in AREAS[1:]:
        await page.keyboard.press("Tab")
        await expect(page.get_by_role("link", name=name)).to_be_focused()
    await page.keyboard.press("Enter")  # on Log
    await expect(page.get_by_role("heading", name="Log", level=2)).to_be_focused()
    await page.keyboard.press("Tab")  # on from the heading: the footer, not back into the nav
    assert await page.evaluate("document.activeElement.closest('nav')?.getAttribute('aria-label')") != "Admin"


async def test_an_unknown_hash_shows_the_first_area(page: Page, site: Path) -> None:
    await open_admin(page, site, hash_="#nothing")
    await expect(page.get_by_role("heading", name="Crawl", level=2)).to_be_visible()
    assert await page.title() == "Crawl - Admin"


async def test_the_skip_link_and_a_strange_hash_leave_the_area_alone(page: Page, site: Path) -> None:
    await open_admin(page, site, hash_="#companies")
    await page.evaluate("location.hash = 'main'")
    await page.evaluate("location.hash = 'constructor'")
    await expect(page.get_by_role("heading", name="Companies", level=2)).to_be_visible()
    assert await page.title() == "Companies - Admin"
    assert await page.evaluate("document.activeElement === document.body")


async def test_the_first_show_does_not_take_the_focus(page: Page, site: Path) -> None:
    await open_admin(page, site, hash_="#alerts")
    assert await page.evaluate("document.activeElement === document.body")


# ---- the sign-out button ----


@pytest.mark.parametrize("answer", ["admin", "not_admin", "broken"])
async def test_sign_out_ends_the_session_and_shows_the_signed_out_state(page: Page, site: Path, answer: str) -> None:
    backend, _problems = await open_admin(page, site, backend=Backend(answer))
    await page.get_by_role("button", name="Sign out").click()
    await expect(page.get_by_role("link", name="Sign in on the dashboard first.")).to_be_visible()
    assert await state(page) == "signed-out"
    assert any(path.endswith("/logout") for path in backend.paths())
    assert await page.evaluate("localStorage.getItem('misra-auth')") is None
    assert await page.locator("#adminSignOut").is_hidden()
    assert await page.title() == "Admin"


async def test_sign_out_still_signs_out_here_when_the_service_cannot_end_the_session(page: Page, site: Path) -> None:
    _backend, _problems = await open_admin(page, site, backend=Backend(auth=AuthBackend("logout_fails")))
    await page.get_by_role("button", name="Sign out").click()
    await expect(page.locator("main")).to_have_attribute("data-state", "signed-out")
    assert await page.evaluate("localStorage.getItem('misra-auth')") is None


# ---- the same checks the other pages pass ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("which", STATES)
async def test_every_state_is_accessible_fits_the_phone_and_has_44px_targets(
    page: Page, site: Path, which: str, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    answers = {"not-admin": "not_admin", "check-failed": "broken"}
    _backend, problems = await open_admin(
        page, site, backend=Backend(answers.get(which, "admin")), signed_in=which != "signed-out"
    )
    assert await state(page) == which
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    if which != "check-failed":  # a failed check is logged to the console on purpose
        assert problems == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    targets = page.locator("main a:visible, main button:visible")
    for target in await targets.all():
        box = await target.bounding_box()
        assert box
        assert box["height"] >= TAP_MIN, await target.inner_text()
        assert box["width"] >= TAP_MIN, await target.inner_text()


async def test_the_page_is_wider_than_a_reading_page_and_no_wider_than_1000px(page: Page, site: Path) -> None:
    await page.set_viewport_size({"width": 1480, "height": 900})
    await open_admin(page, site)
    width = await page.evaluate("document.querySelector('.page').getBoundingClientRect().width")
    assert WIDE_MIN <= width <= WIDE_MAX + 2 * 16  # the page's own side padding is inside its width
    assert await page.evaluate("document.querySelector('.page').getBoundingClientRect().width") > WIDE_MIN


async def test_the_header_and_footer_are_the_shared_ones(page: Page, site: Path) -> None:
    await open_admin(page, site)
    assert await page.locator("header.top a#home").count() == 1
    assert await page.locator("footer.foot nav.foot-links a").count() == FOOTER_LINKS
    assert await page.locator("footer.foot [aria-current]").count() == 0  # no footer link names this page
