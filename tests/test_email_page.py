"""The page the digest emails link to (PLAN.md step 6.1; PRD FR-8 AC 7), run in a real browser against the built page
with a fake `confirm-email` function. The function itself is tested in test/confirm-email.test.ts.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import json
import mimetypes
import re
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
CONFIRMED = "Your job alerts are confirmed"
NOT_VALID = "This link is no longer valid"
UNSUBSCRIBED = "You have been unsubscribed"
UNSUBSCRIBE_NOT_VALID = "This unsubscribe link no longer works"
UNSUBSCRIBE_NOT_VALID_TEXT = (
    "Your account may have been deleted, or we may have replaced the key that signs these links. To stop job alerts, "
    "sign in on the Misra dashboard and turn them off, or write to raanan@avidor.org and we will stop them for you."
)
KEEP = (
    "It may have expired (confirmation links work for 7 days), been replaced by a newer link, "
    "or belong to job alerts that were turned off."
)
ANSWERS = {
    "ok": (200, {"ok": True, "status": "confirmed", "schedule": "weekly"}),
    "daily": (200, {"ok": True, "status": "confirmed", "schedule": "daily"}),
    "no_schedule": (200, {"ok": True, "status": "confirmed"}),
    "already": (200, {"ok": True, "status": "already_confirmed", "schedule": "daily"}),
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
    await expect(page.locator("#heading")).to_have_text(CONFIRMED)
    await expect(page.locator("#message")).to_have_text(
        "You now get a weekly email when new roles match your saved filters. Your first email includes "
        "only roles Misra finds from now on, and you can unsubscribe in one click from any email."
    )
    await expect(page.locator("#confirm")).to_be_hidden()
    await expect(page.locator("#back")).to_be_visible()
    await expect(page.locator("#back")).to_have_text("Open Misra dashboard")
    assert await page.locator("#back").get_attribute("href") == "./"
    assert [r.method for r in confirm.requests] == ["POST"]
    request = confirm.requests[0]
    assert request.url == FUNCTION  # no token in the URL
    assert json.loads(request.post_data) == {"token": TOKEN}
    assert request.headers["content-type"] == "application/json"
    assert "no-referrer" in await page.locator('meta[name="referrer"]').get_attribute("content")
    assert problems == []


@pytest.mark.parametrize(
    ("mode", "words"),
    [
        ("ok", "a weekly email"),
        ("daily", "a daily email"),
        ("already", "a daily email"),
        ("no_schedule", "a daily or weekly email, as you chose, when new roles"),
    ],
)
async def test_the_confirmed_text_names_the_schedule_and_never_says_undefined(
    page: Page, site: Path, mode: str, words: str
) -> None:
    await open_page(page, site, f"#token={TOKEN}", mode)
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text(CONFIRMED)
    await expect(page.locator("#message")).to_contain_text(words)
    assert "undefined" not in await page.locator("#message").inner_text()
    assert CONFIRMED in await page.locator("#status").inner_text()


@pytest.mark.parametrize("mode", ["expired", "unsubscribed", "not_valid"])
async def test_every_link_that_cannot_confirm_gets_the_same_plain_words(page: Page, site: Path, mode: str) -> None:
    await open_page(page, site, f"#token={TOKEN}", mode)
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text(NOT_VALID)
    await expect(page.locator("#message")).to_contain_text(KEEP)
    await expect(page.locator("#message")).to_contain_text("sign in on the Misra dashboard")
    await expect(page.locator("#confirm")).to_be_hidden()
    await expect(page.locator("#back")).to_be_visible()


@pytest.mark.parametrize(("mode", "words"), [("down", "reach the service"), ("weird", "on our side")])
async def test_a_failure_leaves_the_button_to_try_again(page: Page, site: Path, mode: str, words: str) -> None:
    confirm, _ = await open_page(page, site, f"#token={TOKEN}", mode)
    await page.click("#confirm")
    await expect(page.locator("#message")).to_contain_text(words)
    await expect(page.locator("#confirm")).to_be_enabled()
    await expect(page.locator("#heading")).to_have_text("Confirm your email")
    confirm.mode = "ok"
    await page.click("#confirm")
    await expect(page.locator("#heading")).to_have_text(CONFIRMED)
    assert len(confirm.requests) == 2  # noqa: PLR2004


@pytest.mark.parametrize(
    ("fragment", "heading"),
    [
        ("#unsubscribed", UNSUBSCRIBED),
        ("#already-unsubscribed", UNSUBSCRIBED),
        ("#link-not-valid", NOT_VALID),
        ("#unsubscribe-link-not-valid", UNSUBSCRIBE_NOT_VALID),
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
@pytest.mark.parametrize(
    "fragment", [f"#token={TOKEN}", "#unsubscribed", "#link-not-valid", "#unsubscribe-link-not-valid", "#try-again"]
)
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


async def test_the_unsubscribed_text_says_what_was_deleted_and_what_was_not(page: Page, site: Path) -> None:
    await open_page(page, site, "#unsubscribed")
    message = await page.locator("#message").inner_text()
    assert message == (
        "Your job alerts are stopped. We deleted the filters saved for them and the list of jobs we already emailed "
        "you. We keep your address and the times you turned the email on, confirmed it and unsubscribed, as a "
        "record of your consent. "
        'Your Misra account (stars, "Not for me" choices, and dashboard filters) is unchanged. To delete your entire '
        'account and all stored data, sign in on the dashboard and choose "Delete my account".'
    )
    assert await page.locator("#status").inner_text() == f"{UNSUBSCRIBED}. {message}"
    await expect(page.locator("#back")).to_have_text("Open Misra dashboard")


async def test_a_failed_unsubscribe_link_says_what_is_true_for_it(page: Page, site: Path) -> None:
    await open_page(page, site, "#unsubscribe-link-not-valid")
    await expect(page.locator("#heading")).to_have_text(UNSUBSCRIBE_NOT_VALID)
    message = await page.locator("#message").inner_text()
    assert message == UNSUBSCRIBE_NOT_VALID_TEXT
    assert "7 days" not in message
    assert "newer link" not in message
    assert await page.locator("#status").inner_text() == f"{UNSUBSCRIBE_NOT_VALID}. {message}"
    await expect(page.locator("#status")).to_have_attribute("role", "status")


async def test_a_failed_confirmation_link_keeps_its_text(page: Page, site: Path) -> None:
    await open_page(page, site, "#link-not-valid")
    await expect(page.locator("#heading")).to_have_text(NOT_VALID)
    assert await page.locator("#message").inner_text() == (
        f"{KEEP} To manage your job alerts or delete your account, sign in on the Misra dashboard."
    )


async def test_the_button_works_from_the_keyboard(page: Page, site: Path) -> None:
    confirm, _ = await open_page(page, site, f"#token={TOKEN}")
    await page.keyboard.press("Tab")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "confirm"
    await page.keyboard.press("Enter")
    await expect(page.locator("#heading")).to_have_text(CONFIRMED)
    assert len(confirm.requests) == 1
    assert await page.locator("#status").inner_text() != ""  # announced to a screen reader


async def test_a_replay_is_told_as_a_confirmation_and_a_double_click_sends_one_request(page: Page, site: Path) -> None:
    confirm, _ = await open_page(page, site, f"#token={TOKEN}", "already")
    await page.dblclick("#confirm")
    await expect(page.locator("#heading")).to_have_text(CONFIRMED)
    assert len(confirm.requests) == 1
    request = confirm.requests[0]
    assert set(request.headers) & {"authorization", "apikey"} == set()  # no key or token but the one in the body


def test_the_page_knows_every_word_the_functions_send() -> None:
    """The unsubscribe redirect words and the confirm error codes are literals on both sides: they must agree."""
    root = Path(__file__).resolve().parents[1]
    script = (root / "templates" / "email.js").read_text(encoding="utf-8")
    unsubscribe = (root / "supabase" / "functions" / "unsubscribe" / "handler.ts").read_text(encoding="utf-8")
    confirm = (root / "supabase" / "functions" / "confirm-email" / "handler.ts").read_text(encoding="utf-8")
    sent = set(re.findall(r"redirectToPage\(\s*'([a-z-]+)'", unsubscribe))
    sent |= set(re.findall(r"LINK_NOT_VALID_WORD = '([a-z-]+)'", unsubscribe))
    words = sent | {"unsubscribed", "already-unsubscribed"}  # the two the handler maps from the database answer
    assert "unsubscribe-link-not-valid" in sent
    assert "link-not-valid" not in sent  # the confirmation text is not for an unsubscribe link
    assert words == {w for w in words if f"'{w}'" in script or f"{w}:" in script}
    codes = set(re.findall(r"fail\(origin, 4\d\d, '([a-z_]+)'", confirm)) - {"origin_not_allowed", "method_not_allowed"}
    assert {c for c in codes if c not in {"unsupported_media_type", "payload_too_large", "invalid_request"}} == {
        c for c in ("link_not_valid", "link_expired", "unsubscribed") if f"{c}:" in script
    }
    assert "/functions/v1/confirm-email" in script
    assert (root / "supabase" / "functions" / "confirm-email").is_dir()


STATES = {
    f"#token={TOKEN}": (
        "Confirm your email",
        "Press the button to start your Misra job alerts. Nothing is sent until you do.",
        "Confirm my email",
    ),
    "#link-not-valid": (
        NOT_VALID,
        f"{KEEP} To manage your job alerts or delete your account, sign in on the Misra dashboard.",
        "Open Misra dashboard",
    ),
    "#unsubscribe-link-not-valid": (UNSUBSCRIBE_NOT_VALID, UNSUBSCRIBE_NOT_VALID_TEXT, "Open Misra dashboard"),
    "#try-again": (
        "Something went wrong",
        "We could not finish this. Open the link in your email again in a moment.",
        "Open Misra dashboard",
    ),
    "": ("This link is incomplete", "Open the link from your email again.", "Open Misra dashboard"),
}


@pytest.mark.parametrize("fragment", list(STATES))
async def test_every_state_sits_in_one_card_with_the_dashboard_header_and_footer(
    page: Page, site: Path, fragment: str
) -> None:
    """The page looks like the dashboard (header, one card, footer) and shows exactly the approved words."""
    heading, message, button = STATES[fragment]
    await open_page(page, site, fragment)
    card = page.locator("main > section.card")
    await expect(card).to_have_count(1)
    await expect(card.locator("h1 #heading")).to_have_text(heading)
    await expect(card.locator("#message")).to_have_text(message)
    # The card holds the heading, the text, the one visible button and the screen-reader status: nothing else.
    visible = card.locator("#confirm, #back")
    await expect(visible.locator("visible=true")).to_have_text(button)
    assert await card.evaluate("c => [...c.children].map(e => e.id || e.tagName)") == [
        "H1",
        "message",
        "actions",
        "status",
        "back",
    ]
    assert await card.locator("#status").inner_text() == f"{heading}. {message}"
    top = page.locator("header.top")
    await expect(top.locator("svg.top-mark")).to_have_count(1)
    await expect(top.locator("a.wordmark")).to_have_text("misra")
    assert await top.locator("a.wordmark").get_attribute("href") == "./"
    links = page.locator("footer.foot nav.foot-links a")
    assert await links.all_inner_texts() == ["Privacy", "Terms", "Contact"]
    assert [await link.get_attribute("href") for link in await links.all()] == [
        "privacy.html",
        "terms.html",
        "mailto:raanan@avidor.org",
    ]
    for link in await links.all():
        box = await link.bounding_box()
        assert box
        assert box["height"] >= TAP


async def test_the_confirmed_state_is_in_the_same_card(page: Page, site: Path) -> None:
    await open_page(page, site, f"#token={TOKEN}")
    await page.click("#confirm")
    card = page.locator("main > section.card")
    await expect(card.locator("#back")).to_be_visible()
    await expect(card.locator("#heading")).to_have_text(CONFIRMED)


@pytest.mark.parametrize("width", [320, 1480])
@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_card_page_is_accessible_and_does_not_scroll_sideways_at_320_and_1480(
    page: Page, site: Path, width: int, scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size({"width": width, "height": 800})
    await open_page(page, site, "#unsubscribed")
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
