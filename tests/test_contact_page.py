"""The Contact page, in a real browser against the built page.

The form is on in the repository (build_dashboard.CONTACT_FORM_ENABLED), so two builds are checked: one with the switch
on, run against a fake `contact` Edge Function, and one with it forced off (static contact information only). The
function itself is tested in test/contact.test.ts. Locally the tests skip when Chromium is missing; in CI a missing
browser fails."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.test_dashboard_page import AXE, PHONE, TAP_MIN, build_site
from tests.test_legal_pages import open_legal
from tests.test_sign_in import AXE_RUN

FUNCTION = f"{build_dashboard.SUPABASE_URL}/functions/v1/contact"
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
MIN_FILL_MS = 3000
SLOW_SECONDS = 2
MESSAGE_LIMIT_PARTS = 3


@pytest.fixture
def contact_site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    monkeypatch.setattr(build_dashboard, "CONTACT_FORM_ENABLED", True)
    return build_site(db, workdir)


@pytest.fixture
def off_site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    monkeypatch.setattr(build_dashboard, "CONTACT_FORM_ENABLED", False)
    return build_site(db, workdir)


class Backend:
    def __init__(self, status: int = 200, body: Any = None, raw: str | None = None) -> None:
        self.status = status
        self.body = {"ok": True} if body is None else body
        self.raw = raw
        self.requests: list[Any] = []

    async def handle(self, route: Route) -> None:
        if route.request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append(json.loads(route.request.post_data))
        if self.raw == "down":
            await route.abort()
            return
        content = self.raw if self.raw is not None else json.dumps(self.body)
        await route.fulfill(status=self.status, headers=CORS, body=content, content_type="application/json")


async def open_form(page: Page, site: Path, backend: Backend | None = None) -> Backend:
    backend = backend or Backend()
    problems = await open_legal(page, site, "contact.html")
    await page.route(FUNCTION, backend.handle)
    await expect(page.locator("#contactForm")).to_be_visible()
    assert problems == []
    return backend


# ---- the state that ships: no form, no way to send, no address ----


async def test_with_the_form_off_the_page_has_only_the_static_routes_and_no_address(page: Page, off_site: Path) -> None:
    html = (off_site / "contact.html").read_text(encoding="utf-8")
    assert "@" not in html.split("<footer")[0].replace("name@example.com", "")
    assert "connect-src" not in html
    assert not (off_site / "contact.js").exists()
    await open_legal(page, off_site, "contact.html")
    await expect(page.get_by_role("heading", name="Contact", level=1)).to_be_visible()
    for name, href in [
        ("privacy notice", "privacy.html#contact"),
    ]:
        assert await page.get_by_role("link", name=name).get_attribute("href") == href
    assert await page.locator("form, textarea, #contactFallback").count() == 0


@pytest.mark.parametrize("state", ["off_site", "contact_site"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}], ids=["phone", "desktop"])
async def test_the_page_has_no_axe_violations_form_off_and_on(
    page: Page, request: pytest.FixtureRequest, state: str, size: dict[str, int], scheme: str
) -> None:
    site: Path = request.getfixturevalue(state)
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    await open_legal(page, site, "contact.html")
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


# ---- the form on ----


async def test_the_csp_lets_the_page_call_the_function_and_nothing_else(contact_site: Path) -> None:
    html = (contact_site / "contact.html").read_text(encoding="utf-8")
    assert f"connect-src {build_dashboard.SUPABASE_URL}/functions/v1/contact;" in html
    assert "form-action 'none'" in html
    assert "default-src 'none'" in html
    assert "<!--" not in html


async def test_every_field_has_a_label_a_hint_and_a_tap_target_and_the_trap_is_off_the_tab_order(
    page: Page, contact_site: Path
) -> None:
    await page.set_viewport_size(PHONE)  # type: ignore[arg-type]
    await open_form(page, contact_site)
    for name in ("Name", "Email", "Message"):
        field = page.get_by_label(name, exact=True)
        assert await field.get_attribute("aria-describedby")
        box = await field.bounding_box()
        assert box
        assert box["height"] >= TAP_MIN
    assert await page.get_by_label("Message").get_attribute("aria-required") == "true"
    box = await page.locator("#contactSend").bounding_box()
    assert box
    assert box["height"] >= TAP_MIN
    assert await page.locator("#contactWebsite").get_attribute("tabindex") == "-1"
    assert await page.locator(".field-trap").get_attribute("aria-hidden") == "true"
    assert await page.locator("#contactSend").get_attribute("aria-describedby") == "contactPrivacy"
    line = page.locator("#contactPrivacy")
    assert await line.text_content() == (  # the lawyer's line (round 11, A4), then the link to the privacy notice
        "Your message (and your name and email address, if given) is emailed to the maintainer. To prevent spam, we "
        "store a daily non-reversible hash of your network address that is deleted in the next daily cleanup. "
        "Privacy notice (opens in a new tab)"
    )
    link = line.get_by_role("link", name="Privacy notice")
    assert await link.get_attribute("href") == "privacy.html"
    assert await link.get_attribute("target") == "_blank"
    assert await link.get_attribute("rel") == "noopener noreferrer"
    assert await link.locator("svg.icon-ext").count() == 1
    box = await link.bounding_box()
    assert box
    assert box["height"] >= TAP_MIN


async def test_sending_posts_the_fields_the_trap_and_the_time_and_then_confirms_with_focus_on_the_heading(
    page: Page, contact_site: Path
) -> None:
    backend = await open_form(page, contact_site)
    await page.wait_for_timeout(MIN_FILL_MS + 200)
    await page.fill("#contactName", "Dana")
    await page.fill("#contactEmail", "dana@example.test")
    await page.fill("#contactMessage", "Hello there")
    await page.click("#contactSend")
    await expect(page.locator("#contactDoneTitle")).to_be_visible()
    assert await page.evaluate("document.activeElement.id") == "contactDoneTitle"
    await expect(page.locator("#contactDone [role=status]")).to_contain_text("Your message was emailed")
    await expect(page.locator("#contactForm")).to_be_hidden()
    (body,) = backend.requests
    assert {k: body[k] for k in ("name", "email", "message", "website")} == {
        "name": "Dana",
        "email": "dana@example.test",
        "message": "Hello there",
        "website": "",
    }
    assert body["elapsed"] >= MIN_FILL_MS


async def test_a_message_alone_is_enough(page: Page, contact_site: Path) -> None:
    backend = await open_form(page, contact_site)
    await page.fill("#contactMessage", "Just this")
    await page.click("#contactSend")
    await expect(page.locator("#contactDoneTitle")).to_be_visible()
    assert backend.requests[0]["email"] == ""


@pytest.mark.parametrize(
    ("values", "field", "words"),
    [
        ({"message": " "}, "contactMessage", "Enter your message."),
        ({"message": "x" * 2001}, "contactMessage", "at most 2000 characters"),
        ({"message": "m", "name": "n" * 101}, "contactName", "The name can be at most 100 characters."),
        ({"message": "m", "email": "nope"}, "contactEmail", "Enter one email address, like name@example.com."),
        ({"message": "m", "email": "a@b.test, c@d.test"}, "contactEmail", "Enter one email address"),
        ({"message": "m", "email": "a" * 250 + "@b.test"}, "contactEmail", "The address can be at most 254"),
    ],
)
async def test_a_bad_field_is_named_next_to_the_field_focused_and_nothing_is_sent(
    page: Page, contact_site: Path, values: dict[str, str], field: str, words: str
) -> None:
    backend = await open_form(page, contact_site)
    for name, value in values.items():
        await page.fill(f"#contact{name.capitalize()}", value)
    await page.click("#contactSend")
    error = page.locator(f"#{field}Error")
    await expect(error).to_contain_text(words)
    assert await page.evaluate("document.activeElement.id") == field
    assert await page.locator(f"#{field}").get_attribute("aria-invalid") == "true"
    assert backend.requests == []
    await page.fill(f"#{field}", "fine" if field != "contactEmail" else "")
    await expect(error).to_be_hidden()  # fixed as soon as the value is fine


FAILURES = [
    (
        400,
        {"code": "invalid_input", "message": "x", "fields": {"message": "Enter your message."}},
        "Some details were not accepted",
    ),
    (400, {"code": "invalid_request", "message": "x"}, "could not be read, so nothing was sent"),
    (400, {"code": "too_fast", "message": "x"}, "came too soon, so it was not sent"),
    (403, {"code": "origin_not_allowed", "message": "x"}, "only be sent from the live page"),
    (413, {"code": "payload_too_large", "message": "x"}, "too large, so nothing was sent"),
    (429, {"code": "rate_limited", "message": "x"}, "as many messages as are allowed today"),
    (503, {"code": "daily_cap_reached", "message": "x"}, "Messages are paused until tomorrow"),
    (502, {"code": "send_failed", "message": "x"}, "was passed on to us. Try again later."),
    (500, {"code": "server_error", "message": "x"}, "Something went wrong on our side, so nothing was sent"),
    # no code of the function (the platform in front of it answered): the page cannot say whether anything was sent
    (503, {"code": "something_new", "message": "x"}, "could not confirm that your message was sent"),
    (429, {"code": "something_new", "message": "x"}, "could not confirm that your message was sent"),
    (404, {"code": "something_new", "message": "x"}, "form is not available right now, so your message was not sent"),
    (418, {"code": "something_new", "message": "x"}, "could not confirm that your message was sent"),
]


@pytest.mark.parametrize(("status", "error", "words"), FAILURES)
async def test_every_failure_says_whether_the_message_was_sent_in_an_alert_that_takes_focus(
    page: Page, contact_site: Path, status: int, error: dict[str, Any], words: str
) -> None:
    await open_form(page, contact_site, Backend(status, {"ok": False, "error": error}))
    await page.fill("#contactMessage", "Hello")
    await page.click("#contactSend")
    alert = page.locator("#contactError")
    await expect(alert).to_contain_text(words)
    assert await alert.get_attribute("role") == "alert"
    if error["code"] == "invalid_input":
        assert await page.evaluate("document.activeElement.id") == "contactMessage"
    else:
        assert await page.evaluate("document.activeElement.id") == "contactError"
    assert await page.locator("#contactSend").inner_text() == "Try again"
    await expect(page.locator("#contactForm")).to_be_visible()  # what was typed is kept


@pytest.mark.parametrize(
    ("backend", "words"),
    [
        (Backend(raw="down"), "could not reach the service, so your message was most likely not sent"),
        (Backend(raw="<html>"), "could not confirm that your message was sent"),
        (Backend(200, {"ok": False}), "could not confirm that your message was sent"),
    ],
)
async def test_an_unreachable_or_unclear_answer_is_never_shown_as_sent(
    page: Page, contact_site: Path, backend: Backend, words: str
) -> None:
    await open_form(page, contact_site, backend)
    await page.fill("#contactMessage", "Hello")
    await page.click("#contactSend")
    await expect(page.locator("#contactError")).to_contain_text(words)
    await expect(page.locator("#contactDone")).to_be_hidden()


async def test_a_slow_answer_says_it_cannot_tell(page: Page, contact_site: Path) -> None:
    await page.add_init_script(
        "const t = AbortSignal.timeout.bind(AbortSignal); AbortSignal.timeout = (ms) => t(ms >= 20000 ? 300 : ms);"
    )
    backend = Backend()

    async def hang(route: Route) -> None:
        if route.request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
        else:
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(2)
            await route.abort()

    await open_legal(page, contact_site, "contact.html")
    await page.route(FUNCTION, hang)
    await page.fill("#contactMessage", "Hello")
    await page.click("#contactSend")
    await expect(page.locator("#contactError")).to_contain_text("cannot tell whether your message arrived")
    assert backend.requests == []


async def test_with_the_form_on_the_fallback_note_goes_away_when_the_script_has_run(
    page: Page, contact_site: Path
) -> None:
    await open_form(page, contact_site)
    await expect(page.locator("#contactFallback")).to_be_hidden()


async def test_a_second_click_while_sending_does_not_send_twice(page: Page, contact_site: Path) -> None:
    backend = await open_form(page, contact_site)
    await page.fill("#contactMessage", "Hello")
    await page.evaluate(
        "document.getElementById('contactForm').requestSubmit(); document.getElementById('contactForm').requestSubmit()"
    )
    await expect(page.locator("#contactDoneTitle")).to_be_visible()
    assert len(backend.requests) == 1


async def test_without_javascript_the_form_stays_hidden_and_the_note_is_only_the_javascript_hint(
    page: Page, contact_site: Path
) -> None:
    assert page.context.browser
    context = await page.context.browser.new_context(java_script_enabled=False)
    page = await context.new_page()
    await page.route(
        "https://misra.test/**",
        lambda route: route.fulfill(body=(contact_site / "contact.html").read_bytes(), content_type="text/html"),
    )
    await page.goto("https://misra.test/contact.html")
    await expect(page.locator("#contactForm")).to_be_hidden()
    await expect(page.locator("#contactFallback")).to_be_visible()
    await context.close()


async def test_the_form_has_the_keyboard_order_name_email_message_privacy_send(page: Page, contact_site: Path) -> None:
    await open_form(page, contact_site)
    await page.focus("#contactName")
    order = []
    for _ in range(4):
        await page.keyboard.press("Tab")
        order.append(await page.evaluate("document.activeElement.id || document.activeElement.textContent"))
    # the trap is skipped; the privacy link sits between the message and the button
    assert order == ["contactEmail", "contactMessage", "Privacy notice (opens in a new tab)", "contactSend"]
