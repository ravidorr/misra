# ruff: noqa: F811  (the `site` fixture is imported by name, as in the other browser tests)
"""The two forms of the Alerts area of the admin page (PLAN_2.md step 5.3), run in a real browser against the fakes of
tests/test_admin_page.py: the `admin-account` function answers from a script, and anything else the page asks of the
project is refused and recorded. The address is an invented one; the tests check that it is in no request but the one
body, no URL, no storage, no console line, and nowhere on the page after an answer. Locally the tests skip when Chromium
is missing; in CI a missing browser is a failure."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.async_api import Locator, Page, expect

from tests.test_admin_alerts_log import COUNTS, LOG, open_area
from tests.test_admin_page import (
    ACCOUNT,
    AXE,
    AXE_RUN,
    DESKTOP,
    DONE,
    PHONE,
    TAP_MIN,
    Backend,
    site,  # noqa: F401  (a fixture)
    state,
)

REPO = Path(__file__).resolve().parents[1]
ADDRESS = "pat.example@test.example"
NO_ACCOUNT = {"ok": True, "result": "no_account", "message": "No account uses that address."}
AMBIGUOUS = {"ok": False, "error": {"code": "ambiguous"}}
NOTHING = "Nothing was done. Try again."
MAYBE = "It may not have finished. Try again."
ONE = 1
TWO = 2


async def until(done: Callable[[], bool]) -> None:
    for _ in range(100):
        if done():
            return
        await asyncio.sleep(0.05)
    assert done()


def backend_with(*answers: tuple[int, object]) -> Backend:
    return Backend(alerts=COUNTS, log=LOG, account_answers=list(answers) or None)


class Watch:
    """Everything the browser sent and printed, so a test can say where the address went."""

    def __init__(self, page: Page) -> None:
        self.requests: list[tuple[str, str, str]] = []
        self.console: list[str] = []
        page.on("request", lambda r: self.requests.append((r.method, r.url, r.post_data or "")))
        page.on("console", lambda m: self.console.append(m.text))
        page.on("pageerror", lambda e: self.console.append(str(e)))

    def carrying(self, needle: str) -> list[tuple[str, str]]:
        return [(method, url) for method, url, body in self.requests if needle in url or needle in body]


async def start(page: Page, docs: Path, backend: Backend) -> Watch:
    watch = Watch(page)
    await open_area(page, docs, "alerts", backend)
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "ready")
    return watch


async def type_into(page: Page, form: str, address: str = ADDRESS) -> None:
    await page.locator(f"#{form}Address").fill(address)


async def end_alerts(page: Page, address: str = ADDRESS) -> None:
    await type_into(page, "end", address)
    await page.get_by_role("button", name="End job alerts").click()


async def ask_delete(page: Page, address: str = ADDRESS) -> None:
    await type_into(page, "delete", address)
    await page.get_by_role("button", name="Delete an account").click()


def dialog(page: Page) -> Locator:
    return page.locator("#accountConfirm")


async def dialog_open(page: Page) -> bool:
    return bool(await dialog(page).evaluate("d => d.open"))


async def delete_account(page: Page, address: str = ADDRESS) -> None:
    await ask_delete(page, address)
    await expect(dialog(page)).to_be_visible()
    await page.get_by_role("button", name="Delete the account").click()


async def values(page: Page) -> list[str]:
    return [await page.locator(f"#{name}Address").input_value() for name in ("end", "delete")]


# ---- the forms ----


async def test_two_small_forms_sit_below_the_counts_each_with_one_labelled_address_field_and_one_button(
    page: Page, site: Path
) -> None:
    backend = backend_with()
    await start(page, site, backend)
    box = await page.locator("#alerts .admin-state").bounding_box()
    forms = await page.locator("#alerts .alert-forms").bounding_box()
    assert box
    assert forms
    assert forms["y"] >= box["y"] + box["height"]  # below the counts
    assert await page.locator("#alerts form").count() == TWO
    for name, title in (("end", "End job alerts"), ("delete", "Delete an account")):
        form = page.locator(f"#{name}Form")
        await expect(form.get_by_role("heading", level=3, name=title)).to_be_visible()
        field = form.get_by_label("Email address")
        await expect(field).to_have_attribute("type", "email")
        await expect(field).to_have_attribute("autocomplete", "off")
        await expect(field).to_have_attribute("spellcheck", "false")
        assert await field.get_attribute("name") is None  # a field without a name is kept out of the browser's history
        assert await form.locator("input, select, textarea").count() == ONE
        assert await form.get_by_role("button").count() == ONE
    # no list of accounts, no search, no suggestions
    assert await page.locator("#alerts datalist, #alerts [role=listbox], #alerts input[type=search]").count() == 0
    assert backend.account_calls == []


async def test_the_forms_are_not_there_for_someone_who_is_not_an_admin(page: Page, site: Path) -> None:
    backend = Backend("not_admin")
    await open_area(page, site, "alerts", backend)
    assert await state(page) == "not-admin"
    assert await page.locator("#endForm:visible, #deleteForm:visible").count() == 0
    assert backend.account_calls == []


# ---- ending alerts ----


async def test_ending_alerts_sends_one_request_with_the_address_in_its_body_and_says_done(
    page: Page, site: Path
) -> None:
    backend = backend_with((200, DONE))
    watch = await start(page, site, backend)
    await end_alerts(page)
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    [call] = backend.account_calls
    assert call["url"] == ACCOUNT  # no query and no path after it: the address is not in the URL
    assert json.loads(str(call["body"])) == {"address": ADDRESS, "action": "end_alerts"}
    headers = dict(call["headers"])  # type: ignore[call-overload]
    assert headers["authorization"].startswith("Bearer ey")
    assert headers["apikey"]
    assert headers["content-type"] == "application/json"
    assert await values(page) == ["", ""]  # the field is empty after the answer
    await expect(page.locator("#endProblem")).to_be_hidden()
    assert not await dialog_open(page)  # ending alerts asks nothing first
    assert watch.carrying(ADDRESS) == [("POST", ACCOUNT)]
    assert backend.strays == []


async def test_the_other_answer_is_no_account_and_it_changes_nothing_else(page: Page, site: Path) -> None:
    backend = backend_with((200, NO_ACCOUNT))
    await start(page, site, backend)
    await page.get_by_role("link", name="Log").click()
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    await page.get_by_role("link", name="Alerts").click()
    await end_alerts(page)
    await expect(page.locator("#endStatus")).to_have_text("No account uses that address.")
    assert await values(page) == ["", ""]
    await page.get_by_role("link", name="Log").click()
    assert backend.log_asked == ONE  # nothing was logged: the log is not read again
    assert backend.alerts_asked == ONE  # nor the counts


async def test_a_done_answer_refreshes_the_counts_and_reloads_the_log_when_it_is_next_shown(
    page: Page, site: Path
) -> None:
    backend = backend_with((200, DONE))
    await start(page, site, backend)
    await page.get_by_role("link", name="Log").click()
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    assert backend.log_asked == ONE
    await page.get_by_role("link", name="Alerts").click()
    await end_alerts(page)
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    await expect(page.locator("#alerts .crawl-headline")).to_be_visible()
    assert backend.alerts_asked == TWO  # the counts moved
    await expect(page.locator("#endStatus")).to_have_text("Done.")  # the answer stays while the counts reload
    await page.get_by_role("link", name="Log").click()
    await until(lambda: backend.log_asked == TWO)
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    assert ADDRESS not in await page.locator("#log").inner_text()


async def test_an_address_that_is_not_one_is_refused_in_the_page_and_sends_nothing(page: Page, site: Path) -> None:
    backend = backend_with()
    await start(page, site, backend)
    await end_alerts(page, "not an address")
    await expect(page.locator("#endAddressError")).to_have_text("Enter the full email address.")
    await expect(page.locator("#endAddress")).to_be_focused()
    await expect(page.locator("#endAddress")).to_have_attribute("aria-invalid", "true")
    await page.locator("#endAddress").fill("")
    await page.get_by_role("button", name="End job alerts").click()
    await expect(page.locator("#endAddressError")).to_be_visible()
    await page.locator("#endAddress").press_sequentially("a")
    await expect(page.locator("#endAddressError")).to_be_hidden()  # typing clears it
    assert backend.account_calls == []


async def test_the_function_refusing_the_body_says_to_enter_the_address_and_keeps_the_field(
    page: Page, site: Path
) -> None:
    await start(page, site, backend_with((400, {"ok": False, "error": {"code": "bad_request"}})))
    await end_alerts(page)
    await expect(page.locator("#endProblem")).to_have_text("Enter the full email address.")
    assert await values(page) == [ADDRESS, ""]


async def test_more_than_one_account_says_nothing_was_done_and_clears_the_field(page: Page, site: Path) -> None:
    backend = backend_with((409, AMBIGUOUS))
    watch = await start(page, site, backend)
    await delete_account(page)
    problem = page.locator("#deleteProblem")
    await expect(problem).to_have_text("More than one account uses that address. Nothing was done.")
    assert not await dialog_open(page)
    assert await values(page) == ["", ""]
    assert ADDRESS not in await page.locator("main").inner_text()
    assert watch.carrying(ADDRESS) == [("POST", ACCOUNT)]
    assert backend.alerts_asked == ONE  # nothing was done: nothing refreshed


@pytest.mark.parametrize(
    ("answer", "words"),
    [
        ((500, {"ok": False, "error": {"code": "server_error"}}), NOTHING),
        ((503, {"ok": False, "error": {"code": "service_unavailable"}}), NOTHING),
        ((502, {"ok": False, "error": {"code": "delete_unconfirmed"}}), MAYBE),
        ((429, None), NOTHING),
        ((200, {"ok": True, "result": "surprise"}), NOTHING),
        ((200, {"ok": False}), NOTHING),
        ((0, None), MAYBE),
    ],
    ids=["500", "503", "502", "other-status", "unknown-result", "not-ok", "no-answer"],
)
async def test_a_failure_says_so_in_a_plain_sentence_keeps_the_address_and_lets_the_button_try_again(
    page: Page, site: Path, answer: tuple[int, object], words: str
) -> None:
    backend = backend_with(answer, (200, DONE))
    watch = await start(page, site, backend)
    await end_alerts(page)
    problem = page.locator("#endProblem")
    await expect(problem).to_have_text(words)
    await expect(problem).to_have_attribute("role", "alert")
    assert await values(page) == [ADDRESS, ""]  # retryable: the field keeps it
    await expect(page.get_by_role("button", name="End job alerts")).not_to_have_attribute("aria-disabled", "true")
    assert ADDRESS not in await page.locator("#alerts").inner_text()
    assert backend.alerts_asked == ONE
    await page.get_by_role("button", name="End job alerts").click()  # the same button, the same address
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    await expect(problem).to_be_hidden()
    assert len(backend.account_calls) == TWO
    assert await values(page) == ["", ""]
    assert all(ADDRESS not in line for line in watch.console)


def test_a_request_nobody_answers_is_given_up_with_a_timeout_like_the_other_calls() -> None:
    source = (REPO / "templates" / "admin.js").read_text(encoding="utf-8")
    assert "signal: AbortSignal.timeout(ACCOUNT_TIMEOUT_MS)" in source
    assert "ACCOUNT_TIMEOUT_MS = 20000" in source


# ---- deleting an account ----


async def test_deleting_asks_first_in_a_dialog_that_names_the_action_and_shows_the_address_only_there(
    page: Page, site: Path
) -> None:
    backend = backend_with()
    await start(page, site, backend)
    await ask_delete(page)
    box = dialog(page)
    await expect(box).to_be_visible()
    await expect(box.get_by_role("heading", name="Delete this account?")).to_be_visible()
    await expect(box).to_contain_text("This deletes the account and its data. It cannot be undone.")
    await expect(page.locator("#accountAddress")).to_have_text(ADDRESS)
    await expect(page.get_by_role("button", name="Cancel")).to_be_focused()  # the safe choice first
    assert backend.account_calls == []  # nothing is sent before the confirmation
    # the typed address is the value of its field and the text of the dialog, and no other text of the page
    outside = await page.evaluate(
        """(address) => [...document.querySelectorAll('main *')].filter(
            (e) => !e.closest('#accountConfirm') && !e.children.length && e.textContent.includes(address)).length""",
        ADDRESS,
    )
    assert outside == 0


async def test_cancelling_closes_the_dialog_sends_nothing_and_forgets_the_address_it_showed(
    page: Page, site: Path
) -> None:
    backend = backend_with()
    await start(page, site, backend)
    await ask_delete(page)
    await page.get_by_role("button", name="Cancel").click()
    assert not await dialog_open(page)
    await expect(page.get_by_role("button", name="Delete an account")).to_be_focused()  # back where it was
    await expect(page.locator("#accountAddress")).to_have_text("")
    assert backend.account_calls == []


async def test_escape_and_the_close_button_close_the_dialog_too(page: Page, site: Path) -> None:
    backend = backend_with()
    await start(page, site, backend)
    await ask_delete(page)
    await page.keyboard.press("Escape")
    assert not await dialog_open(page)
    await expect(page.get_by_role("button", name="Delete an account")).to_be_focused()
    await page.get_by_role("button", name="Delete an account").click()
    await expect(dialog(page)).to_be_visible()
    await page.get_by_role("button", name="Close").click()
    assert not await dialog_open(page)
    await expect(page.locator("#accountAddress")).to_have_text("")
    assert backend.account_calls == []


async def test_the_dialog_keeps_the_keyboard_inside_it(page: Page, site: Path) -> None:
    await start(page, site, backend_with())
    await ask_delete(page)
    seen = set()
    for _ in range(6):
        await page.keyboard.press("Tab")
        seen.add(await page.evaluate("document.activeElement.closest('dialog')?.id ?? document.activeElement.tagName"))
    assert seen <= {"accountConfirm", "BODY"}  # the page behind is inert: focus is in the dialog or leaves the page


async def test_confirming_deletes_says_done_empties_the_field_and_reloads_the_log(page: Page, site: Path) -> None:
    backend = backend_with((200, DONE))
    watch = await start(page, site, backend)
    await delete_account(page)
    await expect(page.locator("#deleteStatus")).to_have_text("Done.")
    assert not await dialog_open(page)
    [call] = backend.account_calls
    assert json.loads(str(call["body"])) == {"address": ADDRESS, "action": "delete_account"}
    assert await values(page) == ["", ""]
    await expect(page.locator("#accountAddress")).to_have_text("")
    await expect(page.locator("#alerts .crawl-headline")).to_be_visible()
    assert backend.alerts_asked == TWO
    await page.get_by_role("link", name="Log").click()
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    assert backend.log_asked == ONE  # first opened after the action
    assert watch.carrying(ADDRESS) == [("POST", ACCOUNT)]


async def test_deleting_an_address_nobody_uses_says_no_account(page: Page, site: Path) -> None:
    await start(page, site, backend_with((200, NO_ACCOUNT)))
    await delete_account(page)
    await expect(page.locator("#deleteStatus")).to_have_text("No account uses that address.")
    assert await values(page) == ["", ""]


async def test_a_failed_deletion_closes_the_dialog_shows_the_sentence_and_keeps_the_field(
    page: Page, site: Path
) -> None:
    await start(page, site, backend_with((502, {"ok": False, "error": {"code": "delete_unconfirmed"}})))
    await delete_account(page)
    await expect(page.locator("#deleteProblem")).to_have_text(MAYBE)
    assert not await dialog_open(page)
    assert await values(page) == ["", ADDRESS]
    await expect(page.locator("#accountAddress")).to_have_text("")


# ---- one request at a time ----


async def test_pressing_the_button_twice_sends_one_request(page: Page, site: Path) -> None:
    backend = backend_with((200, DONE))
    backend.account_gate = gate = asyncio.Event()
    await start(page, site, backend)
    await type_into(page, "end")
    button = page.get_by_role("button", name="End job alerts")
    await button.click()
    await expect(button).to_have_attribute("aria-disabled", "true")
    await expect(button).to_have_attribute("aria-busy", "true")
    await button.click(force=True)  # Playwright would wait for an enabled button: this one is only aria-disabled
    await page.locator("#endAddress").press("Enter")
    await type_into(page, "delete")
    await page.locator("#deleteAddress").press("Enter")  # the other form waits too: no dialog opens
    assert not await dialog_open(page)
    gate.set()
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    assert len(backend.account_calls) == ONE
    await expect(button).not_to_have_attribute("aria-disabled", "true")


async def test_while_a_deletion_is_on_its_way_escape_and_the_buttons_do_nothing(page: Page, site: Path) -> None:
    backend = backend_with((200, DONE))
    backend.account_gate = gate = asyncio.Event()
    await start(page, site, backend)
    await delete_account(page)
    go = page.get_by_role("button", name="Delete the account")
    await expect(go).to_have_attribute("aria-disabled", "true")
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name="Cancel").click(force=True)
    await page.get_by_role("button", name="Close").click(force=True)
    await go.click(force=True)
    await expect(dialog(page)).to_be_visible()
    gate.set()
    await expect(page.locator("#deleteStatus")).to_have_text("Done.")
    assert len(backend.account_calls) == ONE


# ---- the session ----


async def test_a_404_renews_the_session_once_and_tries_again(page: Page, site: Path) -> None:
    backend = backend_with((404, None), (200, DONE))
    await start(page, site, backend)
    await end_alerts(page)
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    assert len(backend.account_calls) == TWO
    refreshes = [r for r in backend.auth.requests if "refresh_token" in r.url]
    assert len(refreshes) == ONE  # the session was renewed in between, once


async def test_a_404_twice_with_a_live_session_says_nothing_was_done_and_keeps_the_field(
    page: Page, site: Path
) -> None:
    backend = backend_with((404, None))
    await start(page, site, backend)
    checks = backend.asked
    await end_alerts(page)
    await expect(page.locator("#endProblem")).to_have_text(NOTHING)
    assert len(backend.account_calls) == TWO  # one try and one retry, never more
    assert backend.asked == checks + ONE  # the page's own check ran
    assert await state(page) == "ready"
    assert await values(page) == [ADDRESS, ""]


async def test_a_404_twice_for_an_account_that_is_not_an_admin_ends_the_ready_state_and_clears_the_field(
    page: Page, site: Path
) -> None:
    backend = backend_with((404, None))
    await start(page, site, backend)
    backend.answer = "not_admin"
    await end_alerts(page)
    await expect(page.locator("main")).to_have_attribute("data-state", "not-admin")
    assert len(backend.account_calls) == TWO
    assert await values(page) == ["", ""]  # the hidden field holds nothing
    assert ADDRESS not in await page.content()


async def test_a_404_twice_for_a_session_that_is_over_shows_the_signed_out_page_and_clears_the_field(
    page: Page, site: Path
) -> None:
    backend = backend_with((404, None))
    await start(page, site, backend)
    backend.answer = "unauthorized"
    await end_alerts(page)
    await expect(page.locator("main")).to_have_attribute("data-state", "signed-out")
    await expect(page.locator("#adminExpired")).to_have_text("Your sign-in expired.")
    assert len(backend.account_calls) == TWO
    assert await values(page) == ["", ""]


async def test_a_deletion_that_ends_in_another_state_closes_the_dialog(page: Page, site: Path) -> None:
    backend = backend_with((404, None))
    await start(page, site, backend)
    backend.answer = "not_admin"
    await delete_account(page)
    await expect(page.locator("main")).to_have_attribute("data-state", "not-admin")
    assert not await dialog_open(page)
    assert await values(page) == ["", ""]


async def test_signing_out_clears_what_was_typed(page: Page, site: Path) -> None:
    await start(page, site, backend_with())
    await type_into(page, "end")
    await type_into(page, "delete")
    await page.get_by_role("button", name="Sign out").click()
    await expect(page.locator("main")).to_have_attribute("data-state", "signed-out")
    assert await values(page) == ["", ""]


# ---- where the address went ----


async def test_the_address_is_in_no_url_no_storage_no_console_line_and_not_on_the_page_after_an_answer(
    page: Page, site: Path
) -> None:
    backend = backend_with((200, DONE), (500, None), (200, NO_ACCOUNT))
    watch = await start(page, site, backend)
    await end_alerts(page)
    await expect(page.locator("#endStatus")).to_have_text("Done.")
    await end_alerts(page)  # a failure
    await expect(page.locator("#endProblem")).to_be_visible()
    await page.locator("#endAddress").fill("")
    await delete_account(page)
    await expect(page.locator("#deleteStatus")).to_have_text("No account uses that address.")
    assert all(ADDRESS not in url for _method, url, _body in watch.requests)
    assert watch.carrying(ADDRESS) == [("POST", ACCOUNT)] * 3  # the three bodies, nothing else
    storage = await page.evaluate(
        "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage), document.cookie])"
    )
    assert ADDRESS not in storage
    assert all(ADDRESS not in line for line in watch.console)
    assert ADDRESS not in await page.evaluate("location.href + document.title")
    html = await page.content()
    assert ADDRESS not in html, html[max(0, html.index(ADDRESS) - 200) : html.index(ADDRESS) + 100]
    assert ADDRESS not in await page.locator("main").inner_text()
    assert await values(page) == ["", ""]


# ---- looks ----


async def shown(page: Page, docs: Path, which: str) -> None:
    backend = backend_with((409, AMBIGUOUS) if which == "problem" else (200, DONE))
    await open_area(page, docs, "alerts", backend)
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "ready")
    if which == "confirm":
        await ask_delete(page)
        await expect(dialog(page)).to_be_visible()
    elif which == "answer":
        await end_alerts(page)
        await expect(page.locator("#endStatus")).to_have_text("Done.")
        await expect(page.locator("#alerts .crawl-headline")).to_be_visible()
    elif which == "problem":
        await delete_account(page)
        await expect(page.locator("#deleteProblem")).to_be_visible()
    elif which == "typed":
        await type_into(page, "end")
        await type_into(page, "delete")
    elif which == "invalid":
        await end_alerts(page, "nope")
        await expect(page.locator("#endAddressError")).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("which", ["empty", "typed", "invalid", "confirm", "answer", "problem"])
async def test_the_forms_are_accessible_fit_the_phone_and_have_44px_targets(
    page: Page, site: Path, which: str, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    await shown(page, site, which)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("main a:visible, main button:visible, main input:visible").all():
        box = await target.bounding_box()
        assert box
        label = await target.get_attribute("id")
        assert box["height"] >= TAP_MIN, label
        assert box["width"] >= TAP_MIN, label
