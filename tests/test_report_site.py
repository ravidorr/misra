"""Report a site (PLAN.md step 5.2; PRD FR-7), run in a real browser against the built page with a fake `report-site`
Edge Function. The function itself is tested in supabase/test/report-site.test.ts (the flood and spam limits, AC 5);
here the form, the confirmation and each error message are checked.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.test_dashboard_page import AXE, PHONE, TAP_MIN, WEBMCP, serve, site  # noqa: F401  (site is a fixture)
from tests.test_sign_in import AXE_RUN

FUNCTION = f"{build_dashboard.SUPABASE_URL}/functions/v1/report-site"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "*",
}
DIALOG = "#reportDialog"
REPO = Path(__file__).resolve().parents[1]
GOOD = {"company": "Acme Robotics", "link": "https://acme.test/careers", "why": "Hires interns in Tel Aviv"}
SENT = "Report sent"
FAST_TIMEOUT = (
    "const t = AbortSignal.timeout.bind(AbortSignal); AbortSignal.timeout = (ms) => t(ms >= 20000 ? 300 : ms);"
)
HOLD_SECONDS = 3
TRIES = 2  # the failed request and the retry
SERVER_FAULTS = {
    "company": "The name can be at most 100 characters.",
    "link": "The link must start with https:// or http://.",
}

LIMITED = "as many reports as are allowed today, so this one was not sent"
# (status, body, words the visitor must read). Every one says whether anything was sent.
FAILURES = [
    (400, {"code": "invalid_input", "message": "x", "fields": SERVER_FAULTS}, "Some details were not accepted"),
    (400, {"code": "invalid_request", "message": "x"}, "could not be read, so nothing was sent"),
    (403, {"code": "origin_not_allowed", "message": "x"}, "only be sent from the live page, so nothing was sent"),
    (405, {"code": "method_not_allowed", "message": "x"}, "Something went wrong on our side, so nothing was sent"),
    (413, {"code": "payload_too_large", "message": "x"}, "too large, so nothing was sent"),
    (415, {"code": "unsupported_media_type", "message": "x"}, "Something went wrong on our side, so nothing was sent"),
    (429, {"code": "rate_limited", "message": "x"}, LIMITED),
    (503, {"code": "daily_cap_reached", "message": "x"}, "Reports are paused until tomorrow"),
    (502, {"code": "send_failed", "message": "x"}, "could not confirm that your report was passed on"),
    (500, {"code": "server_error", "message": "x"}, "Something went wrong on our side, so nothing was sent"),
    (429, {"code": "something_new", "message": "x"}, LIMITED),  # an unknown code falls back to the status
    (504, {"code": "something_new", "message": "x"}, "could not confirm that your report was sent"),
    (418, {"code": "something_new", "message": "x"}, "could not confirm that your report was sent"),
]


class ReportBackend:
    """A stand-in for the `report-site` function. `mode`: ok, a status from FAILURES (`answer` is set), garbage (a 200
    that is not its answer), not_ok (a 200 with ok false), html (a 404 page), down (no answer) or hang (never answers
    in time). `gate`, when set, holds the answer until it is set."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.answer: tuple[int, dict[str, Any]] | None = None
        self.requests: list[Any] = []
        self.gate: asyncio.Event | None = None

    @property
    def bodies(self) -> list[Any]:
        return [json.loads(r.post_data) for r in self.requests]

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.mode == "down":
            await route.abort()
        elif self.mode == "hang":
            await asyncio.sleep(HOLD_SECONDS)
            await route.abort()
        elif self.mode == "garbage":
            await route.fulfill(status=200, headers=CORS, body="<html>", content_type="text/html")
        elif self.mode == "html":
            await route.fulfill(status=404, headers=CORS, body="<html>", content_type="text/html")
        elif self.mode == "not_ok":
            body = json.dumps({"ok": False})
            await route.fulfill(status=200, headers=CORS, body=body, content_type="application/json")
        elif self.answer is not None:
            status, error = self.answer
            body = json.dumps({"ok": False, "error": error})
            await route.fulfill(status=status, headers=CORS, body=body, content_type="application/json")
        else:
            await route.fulfill(status=200, headers=CORS, body='{"ok":true}', content_type="application/json")


async def ready(page: Page, site: Path, mode: str = "ok") -> ReportBackend:
    backend = ReportBackend(mode)
    problems = await serve(page, site)
    await page.route(FUNCTION, backend.handle)  # after serve(): the later route wins over the jobs backend
    assert problems == []
    return backend


async def open_form(page: Page) -> None:
    await page.click("#reportOpen")  # visible with the list closed
    await expect(page.locator(DIALOG)).to_be_visible()


async def confirmed(page: Page) -> None:
    title = page.locator("#reportDoneTitle")
    await expect(title).to_be_visible()  # the text is always there: it is the visibility that says "sent"
    await expect(title).to_have_text(SENT)


async def fill(page: Page, **values: str) -> None:
    for name, value in {**GOOD, **values}.items():
        await page.fill(f"#report{name.capitalize()}", value)


async def send_good(page: Page, **values: str) -> None:
    await fill(page, **values)
    await page.click("#reportSend")


# ---- FR-7 AC 1 and 2: the action on the card, the short form, no account ----


async def test_fr7_ac1_the_gap_card_has_a_report_a_site_action_that_opens_the_form(page: Page, site: Path) -> None:
    await ready(page, site)
    button = page.locator("#gapsCard #reportOpen")
    await page.locator("#gaps-title").click()
    assert await button.inner_text() == "Add a company"
    assert await button.get_attribute("data-report") == "open"
    assert await button.get_attribute("aria-haspopup") == "dialog"
    await expect(page.locator(DIALOG)).to_be_hidden()
    await button.click()
    dialog = page.locator(DIALOG)
    await expect(dialog).to_be_visible()
    assert await dialog.get_attribute("aria-labelledby") == "reportTitle"
    assert await dialog.get_attribute("aria-describedby") == "reportIntro"
    assert await page.locator("#reportTitle").inner_text() == "Add a company"
    assert await page.evaluate("document.activeElement.id") == "reportCompany"
    assert await dialog.get_attribute("data-state") == "idle"


async def test_the_form_says_where_the_report_goes_and_what_the_spam_guard_keeps(page: Page, site: Path) -> None:
    await ready(page, site)
    await open_form(page)
    note = page.locator("#reportForm #reportPrivacy")
    text = " ".join((await note.text_content() or "").split())
    assert text == (
        "Reports are emailed directly to the maintainer. To prevent spam, we store a daily non-reversible hash of your "
        "network address that is deleted in the next daily cleanup. Privacy notice"
    )
    link = note.get_by_role("link", name="Privacy notice")
    assert await link.get_attribute("href") == "privacy.html"
    assert await link.get_attribute("target") is None
    # a real element, not a placeholder, and the Send button names it so a screen reader reads it with the button
    assert await page.locator("#reportSend").get_attribute("aria-describedby") == "reportPrivacy"
    assert await page.evaluate(
        "document.getElementById('reportPrivacy').compareDocumentPosition(document.getElementById('reportSend'))"
        " & Node.DOCUMENT_POSITION_FOLLOWING"
    )


async def test_fr7_ac2_the_form_asks_for_company_link_and_reason_and_needs_no_account(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    assert await page.locator("#account").is_hidden() or await page.locator("#signOut").is_hidden()  # signed out
    await open_form(page)
    labels = [await page.locator(f"label[for=report{n}]").inner_text() for n in ("Company", "Link", "Why")]
    assert labels == ["Company", "Career page link", "Why it matters"]
    for name in ("company", "link", "why"):
        field = page.locator(f"[data-field={name}]")
        assert await field.get_attribute("aria-required") == ("true" if name == "link" else None)
        hint, error = await field.get_attribute("aria-describedby") or "", ""
        assert hint.split() == [f"report{name.capitalize()}Hint", f"report{name.capitalize()}Error"]
        assert error == ""
    assert "personal details" in await page.locator("#reportIntro").inner_text()
    await send_good(page)
    await expect(page.locator("#reportDoneTitle")).to_be_visible()
    (request,) = backend.requests
    assert request.method == "POST"
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert headers["content-type"] == "application/json"
    assert "authorization" not in headers  # no account, no key: nothing to leak
    assert "apikey" not in headers
    assert backend.bodies == [{**GOOD, "website": ""}]


async def test_the_card_offers_the_action_even_when_no_company_is_known_to_be_missing(
    page: Page,
    site: Path,
) -> None:
    (site.parent / "data" / "companies.json").write_text(
        '[{"name": "Acme", "crawl": true, "status": "ok"}]', encoding="utf-8"
    )
    build_dashboard.main()
    await ready(page, site)
    await expect(page.locator("#gaps")).to_be_hidden()  # no list to show, only the report row
    assert await page.locator("#gaps .gap-part").count() == 0
    await expect(page.locator("#gapsCard #reportOpen")).to_be_visible()
    await open_form(page)
    await expect(page.locator("#reportCompany")).to_be_focused()


# ---- FR-7 AC 3 and 4: the email goes out; the user sees a confirmation, or an error ----


async def test_fr7_ac4_a_sent_report_shows_a_confirmation_and_the_next_report_starts_blank(
    page: Page,
    site: Path,
) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await send_good(page)
    dialog = page.locator(DIALOG)
    await confirmed(page)
    await expect(page.locator("#reportDoneTitle")).to_be_focused()  # the confirmation is read out
    assert await page.locator("#reportDone [role=status]").inner_text() != ""
    assert await page.locator("#reportForm").is_hidden()
    assert await page.locator("#reportError").is_hidden()
    assert await dialog.get_attribute("data-state") == "sent"
    await expect(page.locator("#reportClose")).to_be_visible()  # the corner (x) is the way out of the confirmation
    await page.click("#reportClose")
    await expect(dialog).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "reportOpen"  # back where the user was
    await page.click("#reportOpen")
    assert await page.locator("#reportDone").is_hidden()
    for name in ("company", "link", "why"):
        assert await page.locator(f"[data-field={name}]").input_value() == ""
    assert len(backend.requests) == 1


async def test_the_button_is_busy_while_it_waits_and_one_click_sends_one_report(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    backend.gate = asyncio.Event()
    await open_form(page)
    await fill(page)
    await page.click("#reportSend")
    send = page.locator("#reportSend")
    await expect(send).to_have_text("Sending…")
    assert await send.get_attribute("aria-busy") == "true"
    assert await send.get_attribute("aria-disabled") == "true"
    assert await page.locator(DIALOG).get_attribute("data-state") == "sending"
    # the buttons are aria-disabled, which Playwright waits out unless forced: a real second click is a real click
    await page.click("#reportSend", force=True)
    await page.focus("#reportCompany")
    await page.keyboard.press("Enter")
    await page.click("#reportCancel", force=True)
    for name in ("reportSend", "reportCancel", "reportClose"):
        assert await page.locator(f"#{name}").get_attribute("aria-disabled") == "true"
    await page.click("#reportClose", force=True)  # the corner (x) does not abandon a request in flight either
    await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_visible()
    backend.gate.set()
    await confirmed(page)
    assert len(backend.requests) == 1


async def test_opening_the_form_while_a_request_runs_neither_resets_it_nor_sends_another(
    page: Page, site: Path
) -> None:
    backend = await ready(page, site)
    backend.gate = asyncio.Event()
    await open_form(page)
    await send_good(page)
    await expect(page.locator("#reportSend")).to_have_text("Sending…")
    await page.evaluate("document.getElementById('reportDialog').close()")
    await page.click("#reportOpen")
    await expect(page.locator(DIALOG)).to_be_visible()
    assert await page.locator("#reportCompany").input_value() == GOOD["company"]  # not wiped
    assert await page.locator("#reportSend").inner_text() == "Sending…"
    backend.gate.set()
    await confirmed(page)
    assert len(backend.requests) == 1


async def test_the_send_button_says_send_again_after_a_failed_check_following_a_failed_send(
    page: Page, site: Path
) -> None:
    backend = await ready(page, site)
    backend.answer = (503, {"code": "daily_cap_reached", "message": "x"})
    await open_form(page)
    await send_good(page)
    await expect(page.locator("#reportError")).to_be_visible()
    await page.fill("#reportLink", "")
    await page.click("#reportSend")
    assert await page.locator("#reportSend").inner_text() == "Send report"
    assert len(backend.requests) == 1


async def test_the_reason_is_counted_like_the_function_counts_it(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    padded = "w" * 990 + "\n\n\n\n\n" + "w" * 9 + "   \n" + "x" * 5  # 1001 after the function cleans it
    await send_good(page, why=padded)
    assert await page.locator("#reportWhyError").inner_text() == "The reason can be at most 1000 characters."
    await send_good(page, why="w" * 989 + "\n\n\n\n\n" + "w" * 9 + "   \n")  # 1000 once cleaned, 1007 raw: sent
    await confirmed(page)
    assert len(backend.requests) == 1


async def test_an_answer_is_shown_even_if_the_dialog_was_closed_while_it_waited(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    backend.gate = asyncio.Event()
    await open_form(page)
    await send_good(page)
    await expect(page.locator("#reportSend")).to_have_text("Sending…")
    await page.evaluate("document.getElementById('reportDialog').close()")  # what a second Esc does in Chromium
    await expect(page.locator(DIALOG)).to_be_hidden()
    backend.gate.set()
    await expect(page.locator(DIALOG)).to_be_visible()
    await confirmed(page)


@pytest.mark.parametrize(("status", "error", "words"), FAILURES)
async def test_fr7_ac4_each_failure_is_explained_and_never_called_a_success(
    page: Page,
    site: Path,
    status: int,
    error: dict[str, Any],
    words: str,
) -> None:
    backend = await ready(page, site)
    backend.answer = (status, error)
    await open_form(page)
    await send_good(page)
    problem = page.locator("#reportError")
    await expect(problem).to_be_visible()
    assert words in await problem.inner_text()
    assert await problem.get_attribute("role") == "alert"
    assert await page.locator("#reportDone").is_hidden()
    assert await page.locator(DIALOG).get_attribute("data-state") == "idle"
    assert await page.locator("#reportSend").inner_text() == "Try again"
    assert await page.locator("#reportCompany").input_value() == GOOD["company"]  # the text is kept for a retry
    assert await page.locator("#reportWhy").input_value() == GOOD["why"]
    assert await page.evaluate("document.activeElement.id") in {"reportError", "reportCompany"}
    backend.answer = None
    await page.click("#reportSend")  # a retry that works
    await confirmed(page)
    assert len(backend.requests) == TRIES


async def test_the_function_s_words_for_a_field_appear_beside_that_field(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    backend.answer = (400, FAILURES[0][1])
    await open_form(page)
    await send_good(page)
    await expect(page.locator("#reportCompanyError")).to_have_text(SERVER_FAULTS["company"])
    assert await page.locator("#reportLinkError").inner_text() == SERVER_FAULTS["link"]
    assert await page.locator("#reportWhyError").is_hidden()
    assert await page.locator("#reportCompany").get_attribute("aria-invalid") == "true"
    assert await page.evaluate("document.activeElement.id") == "reportCompany"  # the first field to fix


@pytest.mark.parametrize(
    ("mode", "words"),
    [
        ("down", "could not reach the service, so your report was most likely not sent"),
        ("garbage", "could not confirm that your report was sent"),
        ("not_ok", "could not confirm that your report was sent"),
        ("html", "could not confirm that your report was sent"),
        ("hang", "cannot tell whether your report arrived"),
    ],
)
async def test_fr7_ac4_no_answer_or_an_answer_that_is_not_ok_is_never_a_success(
    page: Page,
    site: Path,
    mode: str,
    words: str,
) -> None:
    if mode == "hang":
        await page.add_init_script(FAST_TIMEOUT)  # the real wait is 20 seconds
    await ready(page, site, mode)
    await open_form(page)
    await send_good(page)
    problem = page.locator("#reportError")
    await expect(problem).to_be_visible()
    assert words in await problem.inner_text()
    assert await page.locator("#reportDone").is_hidden()


# ---- Client-side checks mirror the function; the function stays the authority ----

LONG = "x" * 101
LONG_LINK = "https://a.test/" + "x" * 500
BAD_LINKS = [
    ("", "Enter the link to the career page."),
    ("   ", "Enter the link to the career page."),
    (LONG_LINK, "The link can be at most 500 characters."),
    ("https://a.test/a b", "The link cannot contain spaces or line breaks."),
    ("careers", "Enter a full link that starts with https:// or http://."),
    ("ftp://a.test/jobs", "The link must start with https:// or http://."),
    ("https://user:pw@a.test/", "The link cannot contain a user name or password."),
    ("https://localhost/jobs", "Enter a link to a public website."),
]


async def test_an_empty_form_asks_only_for_the_link_and_sends_nothing(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await page.click("#reportSend")
    assert await page.locator("#reportLinkError").inner_text() == "Enter the link to the career page."
    assert await page.locator("#reportLink").get_attribute("aria-invalid") == "true"
    for name in ("Company", "Why"):
        assert await page.locator(f"#report{name}Error").is_hidden()
        assert await page.locator(f"#report{name}").get_attribute("aria-invalid") is None
    assert await page.evaluate("document.activeElement.id") == "reportLink"
    assert backend.requests == []
    await page.fill("#reportLink", GOOD["link"])  # the message goes as soon as the value is fine
    assert await page.locator("#reportLinkError").is_hidden()


async def test_a_report_with_only_a_link_is_sent(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await page.fill("#reportLink", GOOD["link"])
    await page.click("#reportSend")
    await expect(page.locator("#reportDone")).to_be_visible()
    assert backend.bodies == [{"company": "", "link": GOOD["link"], "why": "", "website": ""}]


@pytest.mark.parametrize(("link", "message"), BAD_LINKS)
async def test_a_bad_link_is_explained_before_anything_is_sent(
    page: Page,
    site: Path,
    link: str,
    message: str,
) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await send_good(page, link=link)
    assert await page.locator("#reportLinkError").inner_text() == message
    assert await page.evaluate("document.activeElement.id") == "reportLink"
    assert backend.requests == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("company", LONG, "The name can be at most 100 characters."),
        ("why", "x" * 1001, "The reason can be at most 1000 characters."),
    ],
)
async def test_the_text_limits_match_the_function(
    page: Page,
    site: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await send_good(page, **{field: value})
    assert await page.locator(f"#report{field.capitalize()}Error").inner_text() == message
    assert backend.requests == []


async def test_values_at_the_limits_are_sent(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    link = "https://a.test/" + "x" * (500 - len("https://a.test/"))
    await send_good(page, company="c" * 100, link=link, why="w" * 1000)
    await confirmed(page)
    assert backend.bodies == [{"company": "c" * 100, "link": link, "why": "w" * 1000, "website": ""}]


# ---- The honeypot ----


async def test_the_honeypot_is_off_screen_not_hidden_and_never_reached_by_keyboard_or_screen_reader(
    page: Page,
    site: Path,
) -> None:
    await ready(page, site)
    await open_form(page)
    trap = page.locator("#reportWebsite")
    assert await trap.get_attribute("tabindex") == "-1"
    assert await trap.get_attribute("autocomplete") == "off"
    assert await page.locator(".field-trap").get_attribute("aria-hidden") == "true"
    style = await trap.evaluate("e => { const s = getComputedStyle(e); return [s.display, s.visibility]; }")
    assert style == ["inline-block", "visible"]  # not display: none and not visibility: hidden
    box = await page.locator(".field-trap").bounding_box()
    assert box is not None
    assert box["x"] + box["width"] < 0 or box["x"] > PHONE["width"] * 10  # off the screen, either direction
    reached = []
    for _ in range(9):  # a full lap round the dialog, the corner (x) included
        await page.keyboard.press("Tab")
        reached.append(await page.evaluate("document.activeElement.id"))
    assert "reportWebsite" not in reached
    assert {"reportCompany", "reportLink", "reportWhy", "reportCancel", "reportSend"} <= set(reached)


async def test_the_honeypot_goes_empty_for_a_person_and_is_sent_as_filled_by_a_bot(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await fill(page)
    await page.evaluate("document.getElementById('reportWebsite').value = 'https://spam.test'")  # what a bot does
    await page.click("#reportSend")
    await confirmed(page)  # the function answers a bot as if it worked
    assert backend.bodies[0]["website"] == "https://spam.test"


# ---- Keyboard, focus, screen readers ----


async def test_the_keyboard_alone_opens_fills_sends_and_closes(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await page.locator("#gaps summary").focus()
    await page.keyboard.press("Enter")  # opens the card
    await page.focus("#reportOpen")
    await page.keyboard.press("Enter")
    await expect(page.locator(DIALOG)).to_be_visible()
    await page.keyboard.type(GOOD["company"])
    await page.keyboard.press("Tab")
    await page.keyboard.type(GOOD["link"])
    await page.keyboard.press("Tab")
    await page.keyboard.type(GOOD["why"])
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.closest('#reportPrivacy') !== null")  # the Privacy Notice link
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "reportCancel"
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "reportSend"
    await page.keyboard.press("Enter")
    await expect(page.locator("#reportDoneTitle")).to_be_focused()
    await page.keyboard.press("Shift+Tab")  # the corner (x) comes first in the dialog, before the heading
    assert await page.evaluate("document.activeElement.id") == "reportClose"
    await page.keyboard.press("Enter")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "reportOpen"
    assert backend.bodies == [{**GOOD, "website": ""}]


async def test_enter_in_a_field_sends_and_esc_closes_an_idle_form_and_forgets_the_draft(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await fill(page)
    await page.focus("#reportLink")
    await page.keyboard.press("Enter")
    await expect(page.locator("#reportDoneTitle")).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_hidden()
    await page.click("#reportOpen")
    await fill(page, company="Draft")
    await page.keyboard.press("Escape")
    await page.click("#reportOpen")
    assert await page.locator("#reportCompany").input_value() == ""
    assert len(backend.requests) == 1


async def test_the_corner_close_closes_an_idle_form_sends_nothing_and_gives_the_focus_back(
    page: Page, site: Path
) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await fill(page)
    await page.click("#reportClose")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "reportOpen"
    assert backend.requests == []


async def test_cancel_closes_the_form_and_sends_nothing(page: Page, site: Path) -> None:
    backend = await ready(page, site)
    await open_form(page)
    await fill(page)
    await page.click("#reportCancel")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "reportOpen"
    assert backend.requests == []


async def test_a_message_names_its_field_for_a_screen_reader(page: Page, site: Path) -> None:
    await ready(page, site)
    await open_form(page)
    await fill(page, company=LONG)
    await page.click("#reportSend")
    field = page.locator("#reportCompany")
    described = await field.evaluate(
        "e => e.getAttribute('aria-describedby').split(' ').map(i => document.getElementById(i).textContent).join(' ')"
    )
    assert "The name can be at most 100 characters." in described
    assert "Up to 100 characters." in described


# ---- Design: axe, themes, phone ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["empty", "errors", "failed", "sent"])
async def test_the_form_has_no_axe_violations_in_either_theme(
    page: Page,
    site: Path,
    scheme: str,
    state: str,
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    backend = await ready(page, site)
    backend.answer = (429, {"code": "rate_limited", "message": "x"})
    await open_form(page)
    if state == "errors":
        await page.click("#reportSend")
    elif state == "failed":
        await send_good(page)
        await expect(page.locator("#reportError")).to_be_visible()
    elif state == "sent":
        backend.answer = None
        await send_good(page)
        await expect(page.locator("#reportDoneTitle")).to_be_visible()
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    panel = await page.locator(DIALOG).evaluate("e => getComputedStyle(e).backgroundColor")
    assert panel != "rgba(0, 0, 0, 0)"
    if scheme == "dark":
        assert panel != "rgb(255, 255, 255)"


async def test_the_form_reads_well_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    backend = await ready(page, site)
    backend.answer = (503, {"code": "daily_cap_reached", "message": "x"})
    await open_form(page)
    await page.click("#reportSend")  # every error shown at once
    await send_good(page)
    await expect(page.locator("#reportError")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    box = await page.locator(DIALOG).bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= PHONE["width"]
    controls = page.locator(f"{DIALOG} .btn, {DIALOG} .dialog-close, {DIALOG} .field").and_(page.locator(":visible"))
    for control in await controls.all():
        size = await control.bounding_box()
        assert size is not None
        assert size["height"] >= TAP_MIN
        assert size["width"] >= TAP_MIN
    target = await page.locator("#reportPrivacy a").bounding_box()  # the privacy link is a 44px target too
    assert target is not None
    assert target["height"] >= TAP_MIN
    assert target["width"] >= TAP_MIN
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


@pytest.mark.parametrize("width", [320, 375])
async def test_the_corner_close_is_named_big_and_clear_of_the_heading_in_both_states(
    page: Page, site: Path, width: int
) -> None:
    await page.set_viewport_size({"width": width, "height": 640})
    await ready(page, site)
    await open_form(page)
    close = page.locator("#reportClose")
    assert await close.get_attribute("aria-label") == "Close"
    assert await page.locator("#reportDone .btn").count() == 0  # no text "Close" button is left in the confirmation
    for heading in ("#reportTitle", "#reportDoneTitle"):
        if heading == "#reportDoneTitle":
            await send_good(page)
            await confirmed(page)
        box = await close.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert box["width"] >= TAP_MIN
        title = await page.locator(heading).evaluate(
            "e => { const r = document.createRange(); r.selectNodeContents(e);"
            " return Math.max(...[...r.getClientRects()].map(c => c.right)); }"
        )
        assert title <= box["x"]  # the heading's text never runs under the (x)


async def test_the_card_action_is_big_enough_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await ready(page, site)
    box = await page.locator("#reportOpen").bounding_box()  # with the list closed
    assert box is not None
    assert box["height"] >= TAP_MIN
    assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]


# ---- Agents ----


async def test_no_web_mcp_tool_sends_a_report_and_the_hooks_are_there(page: Page, site: Path) -> None:
    await page.add_init_script(WEBMCP)
    await ready(page, site)
    names = await page.evaluate("Object.keys(window.__tools)")
    assert "list_coverage_gaps" in names  # the tools are registered, and none of them reports a site
    assert not [n for n in names if "report" in n.lower()]
    for tool in ("dashboard.js", "auth.js"):
        assert "report-site" not in (REPO / "templates" / tool).read_text(encoding="utf-8")
    for hook in ("card", "open", "dialog", "form", "done"):
        assert await page.locator(f"[data-report={hook}]").count() == 1


# ---- What the page and the documents say ----


def test_the_page_calls_only_the_projects_function_with_no_secret() -> None:
    source = (REPO / "templates" / "report.js").read_text(encoding="utf-8")
    assert "'__SUPABASE_URL__/functions/v1/report-site'" in source
    for secret in ("__SUPABASE_ANON_KEY__", "authorization", "apikey", "localStorage", "sessionStorage"):
        assert secret not in source
    html = (REPO / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "connect-src 'self' __SUPABASE_URL__;" in html  # the policy already allows the host and nothing more


def test_fr7_ac6_contributing_md_says_how_a_reported_site_is_accepted() -> None:
    text = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Reported sites" in text
    for words in ("data/companies.json", "crawl", "gap", "reason", "note"):
        assert words in text, words


def test_privacy_md_says_where_the_form_is() -> None:
    text = (REPO / "PRIVACY.md").read_text(encoding="utf-8")
    assert "not on the dashboard yet" not in text
    assert "Add a company" in text


def test_the_daily_workflow_publishes_every_file_the_page_loads() -> None:
    workflow = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    names = {
        Path(s).name
        for s in (*build_dashboard.STATIC_FILES, *build_dashboard.VENDORED_FILES, *build_dashboard.VERIFICATION_FILES)
    }
    names |= {"dashboard.css", "jobs.json", "jobs.jsonld"}
    names.discard("dashboard.html")
    for name in names:
        assert f"docs/{name}" in workflow, name
    assert "docs/index.html" in workflow
