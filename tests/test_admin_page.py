"""The admin page (PLAN_2.md step 1.2), built and run in a real browser against fake backends: the sign-in service and
the `is_admin()` function. No real account or credential is used. Locally the tests skip when Chromium is missing; in CI
(CI=true) a missing browser is a failure."""

import asyncio
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
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
DESKTOP = {"width": 1280, "height": 900}
AREAS = ["Crawl", "Companies", "Alerts", "Log"]
EMPTY = "Nothing here yet."
STATES = ["signed-out", "not-admin", "check-failed", "ready"]
WIDE_MIN = 761  # wider than the 760px reading width of the other pages
WIDE_MAX = 1000
POLL_SECONDS = 0.05
CHECKS_WITH_RENEWAL = 2  # the check, then one renewal of the token and the check again
FOOTER_LINKS = 7


class Backend:
    """The fake `is_admin()` and sign-in service. `answer` is one of: admin, not_admin, unauthorized (401 always),
    unauthorized_once (401, then true), down (no answer) or broken (503). Every request to the project is recorded."""

    def __init__(self, answer: str = "admin", auth: AuthBackend | None = None) -> None:
        self.answer = answer
        self.auth = auth or AuthBackend()
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
        match self.answer:
            case "admin" | "unauthorized_once" if self.answer == "admin" or self.asked > 1:
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
    await page.route(RPC, backend.rpc)
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
    assert "__" not in re.sub(r"<!--.*?-->", "", html)  # every placeholder was filled
    for name in ("admin.js", "supabase.js", "theme.js", "dashboard.css"):
        assert re.search(rf'(src|href)="{re.escape(name)}" integrity="sha384-[A-Za-z0-9+/]+=*"', html), name
    assert (site / "session.js").is_file()
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


async def test_ready_shows_four_empty_areas_and_loads_each_on_its_first_show(page: Page, site: Path) -> None:
    backend, problems = await open_admin(page, site)
    assert problems == []
    assert await state(page) == "ready"
    assert backend.requests == [("POST", "/rest/v1/rpc/is_admin")]  # no data call beyond the check
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
    assert EMPTY in await visible_text(page)
    await expect(page.get_by_role("button", name="Sign out")).to_be_visible()


async def test_the_log_area_is_a_shell_that_says_no_changes_yet(page: Page, site: Path) -> None:
    backend, _problems = await open_admin(page, site, hash_="#log")
    await expect(page.get_by_role("heading", name="Log", level=2)).to_be_visible()
    assert await page.locator("#log .admin-state").inner_text() == "No changes yet."
    assert not any("admin_log" in path for path in backend.paths())  # the log table is not read yet


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
        assert await page.locator(f"#{name.lower()}").get_attribute("data-state") == "empty"
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
