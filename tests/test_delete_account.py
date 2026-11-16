"""Delete my account (PLAN.md step 4.4; PRD FR-1 AC 4 and 5), run in a real browser against the built page with a fake
sign-in service, fake `user_jobs` and `user_prefs` tables and a fake `delete-account` Edge Function. The function itself
is tested in test/delete-account.test.ts; the cascade is in supabase/tests/accounts_rls.sql. PRIVACY.md (AC 5) is
checked by the last test.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.test_dashboard_page import (
    AXE,
    PHONE,
    TAP,
    USER_ID,
    WEBMCP,
    AuthBackend,
    ChoicesBackend,
    serve,
    site,  # noqa: F401  (a fixture)
)
from tests.test_job_choices import DISCARDED, PENDING, PM1, until
from tests.test_job_choices import sign_in as sign_in_with_choices
from tests.test_sign_in import AXE_RUN, stored

FUNCTION = f"{build_dashboard.SUPABASE_URL}/functions/v1/delete-account"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "*",
}
TRIES = 2  # the failed request and the retry
FILTERS = "il-jobs-filters-v3"
PENDING_FILTERS = "misra-pending-filters-v1"
IMPORTED = "misra-imported-v1"
SESSION = "misra-auth"
DIALOG = "#deleteDialog"
REPO = Path(__file__).resolve().parents[1]


class DeleteBackend:
    """A stand-in for the `delete-account` function. `mode`: ok, refused (a definite failure), unconfirmed (502),
    unauthorized (401), down (no answer) or garbage (a 200 that is not its answer). `gate` holds the answer."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.requests: list[Any] = []
        self.gate: Any = None

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        answers: dict[str, tuple[int, str]] = {
            "ok": (200, '{"ok":true}'),
            "already": (200, '{"ok":true,"alreadyDeleted":true}'),
            "refused": (502, '{"ok":false,"error":{"code":"delete_failed","message":"x"}}'),
            "unconfirmed": (502, '{"ok":false,"error":{"code":"delete_unconfirmed","message":"x"}}'),
            "unauthorized": (401, '{"ok":false,"error":{"code":"not_signed_in","message":"x"}}'),
            "garbage": (200, "<html>"),
        }
        if self.mode == "down":
            await route.abort()
        else:
            status, body = answers[self.mode]
            await route.fulfill(status=status, headers=CORS, body=body, content_type="application/json")


async def signed_in(
    page: Page, site: Path, mode: str = "ok", choices: ChoicesBackend | None = None
) -> tuple[AuthBackend, DeleteBackend]:
    """Signed in, with the function standing by."""
    deleter = DeleteBackend(mode)
    auth = await sign_in_with_choices(page, site, choices or ChoicesBackend())
    await page.route(FUNCTION, deleter.handle)  # after serve(): the later route wins over the jobs backend
    return auth, deleter


async def open_dialog(page: Page) -> None:
    await page.click("#deleteAccount")
    await expect(page.locator(DIALOG)).to_be_visible()


async def test_the_control_is_there_only_when_signed_in(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#deleteAccount").is_hidden()
    await signed_in(page, site)
    assert await page.locator("#deleteAccount").is_visible()
    assert await page.locator("#deleteAccount").inner_text() == "Delete my account"


async def test_the_dialog_says_exactly_what_is_removed_and_that_it_cannot_be_undone(page: Page, site: Path) -> None:
    await signed_in(page, site)
    await open_dialog(page)
    dialog = page.locator(DIALOG)
    assert await page.locator("#deleteTitle").inner_text() == "Delete your Misra account?"
    paragraphs = await page.locator("#deleteText p").all_inner_texts()
    assert paragraphs == [
        "This immediately and permanently deletes your account, your email address (ann@test.example), your saved "
        'filters, your stars, your "Not for me" choices, and any job alert settings and the record of your consent to '
        "them from our database. A copy remains in the daily backup until the backup is replaced, within 30 days; it "
        "is never put back into the live service.",
        "Choices saved locally in this browser while you were signed out are not affected. This action cannot be "
        "undone.",
    ]
    assert await page.locator("#deleteCancel").inner_text() == "Cancel"
    assert await dialog.get_attribute("aria-labelledby") == "deleteTitle"
    assert await dialog.get_attribute("aria-describedby") == "deleteText"
    assert await page.evaluate("document.activeElement.id") == "deleteCancel"  # the safe choice has the focus
    assert await page.locator("#deleteConfirm").inner_text() == "Delete my account permanently"


async def test_the_email_in_the_dialog_is_shown_as_text_never_as_html(
    page: Page, site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hostile = "<img src=x onerror=window.pwned=1>@test.example"
    monkeypatch.setattr("tests.test_dashboard_page.EMAIL", hostile)
    await signed_in(page, site)
    await open_dialog(page)
    assert await page.locator("#deleteEmail").inner_text() == hostile
    assert await page.locator(f"{DIALOG} img").count() == 0
    assert await page.evaluate("window.pwned === undefined")


async def test_without_an_address_the_dialog_does_not_show_empty_brackets(
    page: Page, site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tests.test_dashboard_page.EMAIL", "")
    await signed_in(page, site)
    await open_dialog(page)
    await expect(page.locator("#deleteEmailPart")).to_be_hidden()
    first = await page.locator("#deleteText p").first.inner_text()
    assert first.startswith("This immediately and permanently deletes your account, your email address, your saved ")


async def test_keeping_the_account_closes_the_dialog_and_sends_nothing(page: Page, site: Path) -> None:
    _, deleter = await signed_in(page, site)
    await open_dialog(page)
    await page.click("#deleteCancel")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "deleteAccount"  # focus goes back to the trigger
    await open_dialog(page)
    await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert deleter.requests == []
    assert await page.locator("#accountUser").is_visible()


async def test_the_keyboard_alone_can_open_move_through_and_confirm(page: Page, site: Path) -> None:
    _, deleter = await signed_in(page, site)
    await page.focus("#deleteAccount")
    await page.keyboard.press("Enter")
    await expect(page.locator(DIALOG)).to_be_visible()
    assert await page.evaluate("document.activeElement.id") == "deleteCancel"
    await page.keyboard.press("Tab")  # to the dangerous button: the focus stays inside the dialog
    assert await page.evaluate("document.activeElement.id") == "deleteConfirm"
    await page.keyboard.press("Shift+Tab")
    assert await page.evaluate("document.activeElement.id") == "deleteCancel"
    await page.keyboard.press("Tab")
    await page.keyboard.press("Space")
    await until(lambda: len(deleter.requests) == 1)


async def test_the_deletion_asks_the_function_with_the_users_own_token_and_signs_out_here(
    page: Page, site: Path
) -> None:
    auth, deleter = await signed_in(page, site)
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#authMessage")).to_contain_text("Your account and everything saved in it were deleted")
    (request,) = deleter.requests
    assert request.method == "POST"
    assert request.headers["authorization"].startswith("Bearer ey")  # the user's own access token
    assert request.headers["apikey"] == build_dashboard.SUPABASE_ANON_KEY  # the public key, never a secret
    assert request.post_data is None  # nothing names a user: the function takes the id from the token
    assert "service" not in request.headers["authorization"]
    # signed out here (the library may tell the service, which no longer knows the user and whose answer is ignored)
    del auth
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.locator("#signIn").is_visible()
    assert await page.locator("#accountUser").is_hidden()
    assert await page.locator("#accountEmail").inner_text() == ""
    assert await page.evaluate("document.activeElement.id") == "signIn"
    assert not [key for key in await stored(page) if key.startswith(SESSION)]
    await page.reload()
    await page.wait_for_selector("#signIn")
    assert await page.locator("#accountUser").is_hidden()  # still signed out after a reload


async def test_fr1_ac4_deleting_clears_this_accounts_queues_and_notes_and_nothing_else(page: Page, site: Path) -> None:
    choices = ChoicesBackend(mode="down")  # the star cannot be saved, so it waits in the browser
    _, deleter = await signed_in(page, site, choices=choices)
    await page.click(f"{PM1} .btn-star")
    await until(lambda: _has_pending(page))
    others = {"someone-else": {"Acme|9": {"state": "star"}}}
    await page.evaluate(
        """([own, others, pf, imported, user]) => {
          localStorage.setItem(own, JSON.stringify({ ...JSON.parse(localStorage.getItem(own)), ...others }));
          localStorage.setItem(pf, JSON.stringify({ [user]: { q: 'pm' }, 'someone-else': { q: 'ux' } }));
          localStorage.setItem(imported, JSON.stringify({ [user]: '2026-11-15', 'someone-else': '2026-11-14' }));
          localStorage.setItem('il-jobs-discarded-v1', JSON.stringify({ 'Acme|3': '2026-11-15' }));
          localStorage.setItem('il-jobs-filters-v3', JSON.stringify({ q: 'mine' }));
        }""",
        [PENDING, others, PENDING_FILTERS, IMPORTED, USER_ID],
    )
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#authMessage")).to_contain_text("were deleted")
    assert len(deleter.requests) == 1
    kept = await stored(page)
    assert json.loads(kept[PENDING]) == others  # only this account's entry is gone
    assert json.loads(kept[PENDING_FILTERS]) == {"someone-else": {"q": "ux"}}
    assert json.loads(kept[IMPORTED]) == {"someone-else": "2026-11-14"}
    assert json.loads(kept[DISCARDED]) == {"Acme|3": "2026-11-15"}  # the browser's own signed-out data stays
    assert FILTERS in kept
    # nothing is left to be "saved the next time you sign in", and nothing is sent for the deleted account
    await expect(page.locator("#syncNote")).to_have_text("")
    writes_before = len(choices.requests)
    await page.wait_for_timeout(300)
    assert len(choices.requests) == writes_before


async def _has_pending(page: Page) -> bool:
    raw = (await stored(page)).get(PENDING)
    return bool(raw) and bool(json.loads(raw).get(USER_ID))


async def test_a_retry_after_a_lost_answer_finishes_the_deletion(page: Page, site: Path) -> None:
    """The first answer never came back; the retry is told the account is already gone, and the page cleans up."""
    _, deleter = await signed_in(page, site, "down")
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#deleteError")).to_contain_text("could not confirm")
    deleter.mode = "already"
    await page.click("#deleteConfirm")
    await expect(page.locator("#authMessage")).to_contain_text("were deleted")
    assert await page.locator("#signIn").is_visible()


async def test_the_cleanup_does_not_depend_on_the_roles_having_loaded(page: Page, site: Path) -> None:
    """If the jobs request fails, dashboard.js never gets past loading them; the account is still cleaned up."""
    await signed_in(page, site)
    await page.evaluate(
        "([k, user]) => localStorage.setItem(k, JSON.stringify({ [user]: { 'Acme|1': {} }, other: { 'Acme|2': {} } }))",
        [PENDING, USER_ID],
    )
    await page.route(f"{build_dashboard.SUPABASE_URL}/rest/v1/jobs**", lambda route: route.abort())
    await page.reload()  # the saved session signs the user in again; the roles cannot load
    await page.wait_for_selector("#accountUser:not([hidden])")
    assert await page.locator("#list > li").count() == 0
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#authMessage")).to_contain_text("were deleted")
    assert json.loads((await stored(page))[PENDING]) == {"other": {"Acme|2": {}}}


async def test_the_dialog_is_busy_while_it_waits_and_cannot_be_dismissed(page: Page, site: Path) -> None:
    _, deleter = await signed_in(page, site)
    deleter.gate = asyncio.Event()
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#deleteConfirm")).to_have_text("Deleting…")
    assert await page.locator("#deleteConfirm").get_attribute("aria-busy") == "true"
    assert await page.locator("#deleteConfirm").get_attribute("aria-disabled") == "true"
    assert await page.locator("#deleteCancel").get_attribute("aria-disabled") == "true"
    await page.keyboard.press("Escape")
    await page.click("#deleteCancel", force=True)  # aria-disabled: Playwright would wait for it to be enabled
    await page.click("#deleteConfirm", force=True)  # a second click sends no second request
    await page.wait_for_timeout(200)
    await expect(page.locator(DIALOG)).to_be_visible()
    assert len(deleter.requests) == 1
    deleter.gate.set()
    await expect(page.locator("#authMessage")).to_contain_text("were deleted")


@pytest.mark.parametrize(
    ("mode", "words"),
    [
        ("refused", "Your account was not deleted"),
        ("unconfirmed", "could not confirm that your account was deleted"),
        ("down", "could not confirm that your account was deleted"),
        ("garbage", "could not confirm that your account was deleted"),
        ("unauthorized", "Your sign-in was not accepted"),
    ],
)
async def test_fr1_ac4_a_failure_never_pretends_success_and_offers_a_retry(
    page: Page, site: Path, mode: str, words: str
) -> None:
    _, deleter = await signed_in(page, site, mode)
    await open_dialog(page)
    await page.click("#deleteConfirm")
    error = page.locator("#deleteError")
    await expect(error).to_contain_text(words)
    assert await error.get_attribute("role") == "alert"
    assert "were deleted" not in await page.locator("#authMessage").inner_text()
    await expect(page.locator(DIALOG)).to_be_visible()  # the account stays, signed in
    assert await page.locator("#accountUser").is_visible()
    assert await page.locator("#deleteConfirm").inner_text() == "Try again"
    assert await page.locator("#deleteConfirm").get_attribute("aria-busy") == "false"
    assert SESSION in await stored(page)
    # the retry works
    deleter.mode = "ok"
    await page.click("#deleteConfirm")
    await expect(page.locator("#authMessage")).to_contain_text("were deleted")
    assert len(deleter.requests) == TRIES


async def test_a_failed_deletion_keeps_the_queues_and_the_session(page: Page, site: Path) -> None:
    choices = ChoicesBackend(mode="down")
    _, _ = await signed_in(page, site, "refused", choices=choices)
    await page.click(f"{PM1} .btn-star")
    await until(lambda: _has_pending(page))
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#deleteError")).to_be_visible()
    assert await _has_pending(page)


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_dialog_has_no_axe_violations_and_uses_the_theme(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await signed_in(page, site)
    await open_dialog(page)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    panel = await page.locator(DIALOG).evaluate("e => getComputedStyle(e).backgroundColor")
    assert panel != "rgba(0, 0, 0, 0)"
    if scheme == "dark":
        assert panel != "rgb(255, 255, 255)"
    backdrop = await page.locator(DIALOG).evaluate("e => getComputedStyle(e, '::backdrop').backgroundColor")
    assert backdrop not in {"rgba(0, 0, 0, 0)", ""}  # the token resolved on the backdrop too


async def test_the_dialog_and_the_error_read_well_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await signed_in(page, site, "refused")
    await open_dialog(page)
    await page.click("#deleteConfirm")
    await expect(page.locator("#deleteError")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    box = await page.locator(DIALOG).bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= PHONE["width"]
    for button in await page.locator(f"{DIALOG} .btn").all():
        size = await button.bounding_box()
        assert size is not None
        assert size["height"] >= TAP
        assert size["width"] >= TAP
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


async def test_the_header_buttons_are_big_enough_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await signed_in(page, site)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("header .btn").and_(page.locator(":visible")).all():
        box = await target.bounding_box()
        assert box is not None
        assert box["height"] >= TAP
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]


async def test_no_web_mcp_tool_can_delete_an_account(page: Page, site: Path) -> None:
    await page.add_init_script(WEBMCP)
    await signed_in(page, site)
    names = await page.evaluate("Object.keys(window.__tools)")
    assert "star_job" in names  # the tools are registered, and none of them deletes an account
    assert not [n for n in names if "delete_account" in n or "account" in n.lower()]
    source = (REPO / "templates" / "dashboard.js").read_text(encoding="utf-8")
    assert "delete-account" not in source  # the call lives in auth.js, behind the dialog


def test_fr1_ac5_privacy_md_states_what_is_kept_and_never_a_cv_and_how_to_delete() -> None:
    text = (REPO / "PRIVACY.md").read_text(encoding="utf-8")
    wanted = ("email address", "stars", "Not for me", "never store a CV", "Delete my account", "Supabase", "Resend")
    for words in wanted:
        assert words in text, words
