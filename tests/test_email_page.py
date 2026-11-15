"""The page the digest emails link to (PLAN.md step 6.1; PRD FR-8 AC 7), run in a real browser against the built page
with a fake `confirm-email` function. The function itself is tested in test/confirm-email.test.ts.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.test_dashboard_page import AXE, ORIGIN, PHONE, TAP, site  # noqa: F401  (site is a fixture)
from tests.test_sign_in import AXE_RUN

FUNCTION = f"{build_dashboard.SUPABASE_URL}/functions/v1/confirm-email"
TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE"
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
ANSWERS = {
    "ok": (200, {"ok": True, "status": "confirmed"}),
    "expired": (410, {"ok": False, "error": {"code": "link_expired", "message": "x"}}),
    "unsubscribed": (409, {"ok": False, "error": {"code": "unsubscribed", "message": "x"}}),
    "not_valid": (404, {"ok": False, "error": {"code": "link_not_valid", "message": "x"}}),
    "weird": (500, {"ok": False, "error": {"code": "server_error", "message": "x"}}),
}


class Confirm:
    """A stand-in for the function: records the requests, answers by `mode`, or drops the connection ('down')."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.requests: list[Any] = []

    async def handle(self, route: Route) -> None:
        if route.request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
        elif self.mode == "down":
            self.requests.append(route.request)
            await route.abort()
        else:
            self.requests.append(route.request)
            status, body = ANSWERS[self.mode]
            await route.fulfill(status=status, headers=CORS, body=json.dumps(body), content_type="application/json")


async def open_page(page: Page, docs: Path, fragment: str, mode: str = "ok") -> tuple[Confirm, list[str]]:
    confirm = Confirm(mode)
    problems: list[str] = []

    async def handler(route: Route) -> None:
        name = urlparse(route.request.url).path.lstrip("/") or "index.html"
        file = docs / name
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", handler)
    await page.route(FUNCTION, confirm.handle)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}/email.html{fragment}")
    return confirm, problems


async def test_a_confirmation_link_shows_a_button_and_confirms_nothing_until_it_is_pressed(
    page: Page, site: Path
) -> None:
    confirm, problems = await open_page(page, site, f"#token={TOKEN}")
    await expect(page.locator("#heading")).to_have_text("Confirm your email")
    await expect(page.locator("#confirm")).to_be_visible()
    assert confirm.requests == []  # opening the link (what a mail scanner does) sends nothing
    assert page.url == f"{ORIGIN}/email.html"  # the token is out of the address bar
    assert await page.evaluate("location.hash") == ""
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text("Your email is confirmed")
    await expect(page.locator("#confirm")).to_be_hidden()
    await expect(page.locator("#back")).to_be_visible()
    assert [r.method for r in confirm.requests] == ["POST"]
    request = confirm.requests[0]
    assert request.url == FUNCTION  # no token in the URL
    assert json.loads(request.post_data) == {"token": TOKEN}
    assert request.headers["content-type"] == "application/json"
    assert "no-referrer" in await page.locator('meta[name="referrer"]').get_attribute("content")
    assert problems == []


@pytest.mark.parametrize(
    ("mode", "heading"),
    [
        ("expired", "This link has expired"),
        ("unsubscribed", "You have unsubscribed"),
        ("not_valid", "This link does not work"),
    ],
)
async def test_the_answers_of_the_function_are_told_plainly(page: Page, site: Path, mode: str, heading: str) -> None:
    await open_page(page, site, f"#token={TOKEN}", mode)
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text(heading)
    await expect(page.locator("#confirm")).to_be_hidden()
    await expect(page.locator("#back")).to_be_visible()


@pytest.mark.parametrize("mode", ["down", "weird"])
async def test_a_failure_leaves_the_button_to_try_again(page: Page, site: Path, mode: str) -> None:
    confirm, _ = await open_page(page, site, f"#token={TOKEN}", mode)
    await page.click("#confirm")
    await expect(page.locator("#message")).to_contain_text("Confirm again")
    await expect(page.locator("#confirm")).to_be_enabled()
    await expect(page.locator("#heading")).to_have_text("Confirm your email")
    confirm.mode = "ok"
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text("Your email is confirmed")
    assert len(confirm.requests) == 2  # noqa: PLR2004


@pytest.mark.parametrize(
    ("fragment", "heading"),
    [
        ("#unsubscribed", "You are unsubscribed"),
        ("#already-unsubscribed", "You are unsubscribed"),
        ("#link-not-valid", "This link does not work"),
        ("#try-again", "Something went wrong"),
        ("", "This link is incomplete"),
        ("#token=short", "This link is incomplete"),
        ("#toString", "This link is incomplete"),
    ],
)
async def test_the_landing_for_the_unsubscribe_and_for_a_broken_link(
    page: Page, site: Path, fragment: str, heading: str
) -> None:
    confirm, problems = await open_page(page, site, fragment)
    await expect(page.locator("#heading")).to_have_text(heading)
    await expect(page.locator("#confirm")).to_be_hidden()
    await expect(page.locator("#back")).to_be_visible()
    assert confirm.requests == []
    assert problems == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("fragment", [f"#token={TOKEN}", "#unsubscribed"])
async def test_the_page_is_accessible_in_light_and_dark_on_a_phone(
    page: Page, site: Path, scheme: str, fragment: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(PHONE)
    await open_page(page, site, fragment)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in ("#confirm", "#back"):
        box = await page.locator(selector).bounding_box()
        if box:
            assert box["height"] >= TAP
    assert await page.evaluate("document.documentElement.lang") == "en"


async def test_the_button_works_from_the_keyboard(page: Page, site: Path) -> None:
    confirm, _ = await open_page(page, site, f"#token={TOKEN}")
    await page.keyboard.press("Tab")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "confirm"
    await page.keyboard.press("Enter")
    await expect(page.locator("#heading")).to_have_text("Your email is confirmed")
    assert len(confirm.requests) == 1
    assert await page.locator("#status").inner_text() != ""  # announced to a screen reader
