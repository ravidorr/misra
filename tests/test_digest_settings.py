"""The daily email settings (PLAN.md step 6.2; PRD FR-8 AC 1 to 3), run in a real browser against the built page with a
fake sign-in service, a fake `subscriptions` table behind PostgREST and a fake `email-settings` Edge Function. The
function (who can mail whom, the limits, the secrets) is tested in test/email-settings.test.ts and the SQL in
supabase/tests/digest_signup.sql; here the dialog, its states and each error message are checked, with the signup switch
on. The switch itself (on and off) is tested in tests/test_build_dashboard.py.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.test_dashboard_page import (
    AXE,
    CALL,
    EMAIL,
    ORIGIN,
    PHONE,
    TAP,
    USER_ID,
    WEBMCP,
    AuthBackend,
    serve,
    site,  # noqa: F401  (a fixture)
)
from tests.test_sign_in import AXE_RUN, come_back, start_sign_in

FUNCTION = f"{build_dashboard.SUPABASE_URL}/functions/v1/email-settings"
TABLE = f"{build_dashboard.SUPABASE_URL}/rest/v1/subscriptions**"
HEADERS = "authorization, apikey, content-type, x-client-info"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": HEADERS,
    "access-control-allow-methods": "POST, PATCH, GET, OPTIONS",
}
DIALOG = "#digestDialog"
OTHER = "someone.else@example.test"
EMPTY = {"regions": [], "levels": [], "role_types": [], "keywords": ""}
FAST_TIMEOUT = (
    "const t = AbortSignal.timeout.bind(AbortSignal); AbortSignal.timeout = (ms) => t(ms >= 20000 ? 300 : ms);"
)
HOLD_SECONDS = 3
MAX_KEYWORDS = 200
READS_AFTER_A_WAIT = 2  # opening the dialog, and the read after the answer
RUNNING = ("waiting", "on")

# (status, body, words the user must read). Every one says whether anything was changed or sent.
FAILURES = [
    (
        400,
        {"code": "invalid_input", "message": "x", "fields": {"address": "Enter one email address."}},
        "Some details were not accepted",
    ),
    (400, {"code": "invalid_request", "message": "x"}, "could not be read, so nothing was changed"),
    (403, {"code": "origin_not_allowed", "message": "x"}, "only be done from the live page"),
    (403, {"code": "signup_closed", "message": "x"}, "not open yet, so nothing was turned on"),
    (401, {"code": "not_signed_in", "message": "x"}, "sign-in was not accepted, so nothing was changed"),
    (503, {"code": "service_unavailable", "message": "x"}, "sign-in service did not answer, so nothing was changed"),
    (429, {"code": "rate_limited", "message": "x"}, "already asked for 3 confirmation emails today"),
    (429, {"code": "address_limited", "message": "x"}, "That address has already been sent as many"),
    (503, {"code": "daily_cap_reached", "message": "x"}, "as many confirmation emails as we can today"),
    (502, {"code": "send_failed", "message": "x"}, "could not be sent, so nothing is waiting for you"),
    (502, {"code": "send_unconfirmed", "message": "x"}, "could not confirm that the email was sent"),
    (500, {"code": "server_error", "message": "x"}, "Something went wrong on our side"),
    (405, {"code": "method_not_allowed", "message": "x"}, "Something went wrong on our side"),
    (415, {"code": "unsupported_media_type", "message": "x"}, "Something went wrong on our side"),
    (413, {"code": "payload_too_large", "message": "x"}, "could not be read, so nothing was changed"),
    (
        429,
        {"code": "something_new", "message": "x"},
        "already asked for 3 confirmation emails today",
    ),  # status fallback
    (503, {"code": "something_new", "message": "x"}, "as many confirmation emails as we can today"),
    (401, {"code": "something_new", "message": "x"}, "sign-in was not accepted"),
    (403, {"code": "something_new", "message": "x"}, "only be done from the live page"),
    (400, {"code": "something_new", "message": "x"}, "Some details were not accepted"),
    (504, {"code": "something_new", "message": "x"}, "could not confirm what happened"),
]


class World:
    """The account's subscription row as the database holds it (None: no row), the requests the two fakes saw, and the
    failures they can be told to show. The function fake does what the real one does to the row."""

    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.table_mode = "ok"  # ok, read_down, read_error, patch_error, patch_none
        self.function_mode = "ok"  # ok, an answer in `answer`, garbage, down, hang, html, not_ok
        self.answer: tuple[int, dict[str, Any]] | None = None
        self.state_after = "waiting"  # what a turn_on that answers ok leaves: waiting, on (the "already on" answer)
        self.gate: asyncio.Event | None = None
        self.function_requests: list[Any] = []
        self.table_requests: list[Any] = []

    @property
    def calls(self) -> list[dict[str, Any]]:
        return [json.loads(r.post_data) for r in self.function_requests]

    def headers(self, index: int = 0) -> dict[str, str]:
        return {k.lower(): v for k, v in self.function_requests[index].headers.items()}

    async def table(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.table_requests.append(request)
        reading = request.method == "GET"
        if self.table_mode == "read_down" and reading:
            await route.abort()
        elif self.table_mode == "read_error" and reading:
            await route.fulfill(status=503, headers=CORS, body="{}", content_type="application/json")
        elif reading:
            rows = [self.row] if self.row else []
            columns = parse_qs(urlparse(request.url).query)["select"][0].split(",")
            body = json.dumps([{c: r.get(c) for c in columns} for r in rows])
            await route.fulfill(headers=CORS, body=body, content_type="application/json")
        elif self.table_mode == "patch_error":
            await route.fulfill(status=403, headers=CORS, body='{"code":"42501"}', content_type="application/json")
        else:
            patch = json.loads(request.post_data or "{}")
            changed = [] if self.row is None or self.table_mode == "patch_none" else [{"user_id": USER_ID}]
            if changed and self.row:
                self.row.update(patch)
            await route.fulfill(headers=CORS, body=json.dumps(changed), content_type="application/json")

    async def function(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.function_requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        mode = self.function_mode
        if mode == "down":
            await route.abort()
        elif mode == "hang":
            await asyncio.sleep(HOLD_SECONDS)
            await route.abort()
        elif mode == "garbage":
            await route.fulfill(status=200, headers=CORS, body="<html>", content_type="text/html")
        elif mode == "html":
            await route.fulfill(status=404, headers=CORS, body="<html>", content_type="text/html")
        elif mode == "not_ok":
            await route.fulfill(status=200, headers=CORS, body='{"ok":false}', content_type="application/json")
        elif self.answer is not None:
            status, error = self.answer
            body = json.dumps({"ok": False, "error": error})
            await route.fulfill(status=status, headers=CORS, body=body, content_type="application/json")
        else:
            await route.fulfill(
                headers=CORS,
                body=json.dumps(self.apply(json.loads(request.post_data or "{}"))),
                content_type="application/json",
            )

    def apply(self, call: dict[str, Any]) -> dict[str, Any]:
        """What the real function leaves behind, and what it says."""
        if call["action"] == "turn_off":
            if self.row is None:
                return {"ok": True, "status": "not_subscribed"}
            self.row.update({"unsubscribed_at": "2030-01-02T09:00:00Z", "filters": {}, "frequency": "daily"})
            return {"ok": True, "status": "unsubscribed"}
        if self.state_after == "on":
            return {"ok": True, "state": "on"}
        self.row = {
            "address": call["address"] or EMAIL,
            "frequency": call["frequency"],
            "filters": call["filters"],
            "consent_confirmed_at": None,
            "unsubscribed_at": None,
        }
        return {"ok": True, "state": "waiting"}


def stored(state: str, **extra: Any) -> dict[str, Any]:
    """The row of a subscription in a state: waiting, on or unsubscribed."""
    row: dict[str, Any] = {
        "address": EMAIL,
        "frequency": "daily",
        "filters": EMPTY,
        "consent_confirmed_at": "2030-01-01T10:00:00Z" if state in {"on", "unsubscribed"} else None,
        "unsubscribed_at": "2030-01-02T10:00:00Z" if state == "unsubscribed" else None,
    }
    if state == "unsubscribed":
        row["filters"] = {}
    return {**row, **extra}


@pytest.fixture
def digest_site(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built with the signup switch on."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", True)
    return request.getfixturevalue("site")  # type: ignore[no-any-return]


async def signed_in(page: Page, docs: Path, world: World, *, wait: bool = True) -> list[str]:
    """The page, the fakes, the sign-in (the click, Google, the way back with a code); returns the console errors."""
    auth = AuthBackend()
    problems = await serve(page, docs, auth=auth)
    await page.route(FUNCTION, world.function)  # after serve(): the later route wins over the jobs backend
    await page.route(TABLE, world.table)
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#accountUser:not([hidden])")
    if wait:
        await settled(page)
    return problems


async def settled(page: Page) -> None:
    """The button carries the state once the row was read."""
    await page.wait_for_selector("#digestOpen:not([data-digest-state=loading])")


async def open_dialog(page: Page) -> None:
    await page.click("#digestOpen")
    await expect(page.locator(DIALOG)).to_be_visible()
    await page.wait_for_selector("#digestDialog:not([data-state=loading])")


async def state_of(page: Page) -> str:
    return await page.locator(DIALOG).get_attribute("data-state") or ""


async def lines(page: Page) -> list[str]:
    return await page.locator("#digestFilters li").all_text_contents()


async def choose(page: Page, group: str, value: str) -> None:
    await page.click(f'.chip[data-group={group}][data-value="{value}"]')


# ---- FR-8 AC 1: a signed-in user can turn the email on or off and choose the address ----


async def test_a_signed_out_visitor_sees_no_daily_email_control_and_nothing_is_asked_of_the_database(
    page: Page, digest_site: Path
) -> None:
    world = World()
    problems = await serve(page, digest_site)
    await page.route(TABLE, world.table)
    await page.route(FUNCTION, world.function)
    assert problems == []
    assert await page.locator("#digestOpen").is_hidden()
    assert await page.locator("#accountUser").is_hidden()
    assert world.table_requests == []
    assert world.function_requests == []


async def test_fr8_ac1_a_signed_in_user_has_a_daily_email_button_that_opens_the_settings(
    page: Page, digest_site: Path
) -> None:
    world = World()
    problems = await signed_in(page, digest_site, world)
    button = page.locator("#digestOpen")
    assert await button.inner_text() == "Daily email"
    assert await button.get_attribute("aria-haspopup") == "dialog"
    assert await button.get_attribute("data-digest") == "open"
    assert await button.get_attribute("data-digest-state") == "off"
    await expect(page.locator(DIALOG)).to_be_hidden()
    await button.click()
    dialog = page.locator(DIALOG)
    await expect(dialog).to_be_visible()
    assert await dialog.get_attribute("aria-labelledby") == "digestTitle"
    assert await dialog.get_attribute("aria-describedby") == "digestIntro"
    assert await page.locator("#digestTitle").inner_text() == "Daily email"
    assert await page.evaluate("document.activeElement.id") == "digestTitle"
    await page.wait_for_selector("#digestDialog:not([data-state=loading])")
    assert await state_of(page) == "off"
    assert await page.locator("#digestState").inner_text() == "The email is off. Nothing is sent to you."
    # the address defaults to the Google email, daily is the default, and the email starts off with every role
    assert await page.locator("#digestAddress").input_value() == EMAIL
    assert await page.locator("input[name=digestFrequency]:checked").get_attribute("value") == "daily"
    assert await lines(page) == ["Every new role (no filters chosen)."]
    assert await page.locator("#digestSend").inner_text() == "Turn the email on"
    assert await page.locator("#digestOff").is_hidden()
    assert problems == []
    # the row of this user, and only that, was read, and no token hash was asked for
    (request,) = [r for r in world.table_requests if r.method == "GET"][:1]
    query = parse_qs(urlparse(request.url).query)
    assert query["user_id"] == [f"eq.{USER_ID}"]
    assert not {"confirm_token_hash", "unsubscribe_token_hash", "confirm_token_expires_at"} & set(
        query["select"][0].split(",")
    )


async def test_fr8_ac1_turning_it_on_sends_the_choices_and_waits_for_the_confirmation(
    page: Page, digest_site: Path
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text(f"Check {EMAIL} for a confirmation link")
    assert world.calls == [{"action": "turn_on", "address": None, "frequency": "daily", "filters": EMPTY}]
    headers = world.headers()
    assert headers["authorization"].startswith("Bearer ey")  # the user's own access token
    assert headers["content-type"] == "application/json"
    assert headers["apikey"] == build_dashboard.SUPABASE_ANON_KEY
    # what is shown is what the database holds after the call
    assert await state_of(page) == "waiting"
    assert await page.locator("#digestOpen").get_attribute("data-digest-state") == "waiting"
    text = await page.locator("#digestState").inner_text()
    assert text.startswith(f"Waiting for you to confirm {EMAIL}.")
    assert "until then nothing is sent" in text
    assert await page.locator("#digestSend").inner_text() == "Send the confirmation again"
    assert await page.locator("#digestOff").is_visible()
    assert await page.locator("#digestError").is_hidden()


async def test_fr8_ac1_a_different_address_is_sent_as_typed_and_the_google_email_as_none(
    page: Page, digest_site: Path
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.fill("#digestAddress", f"  {OTHER}  ")
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text(f"Check {OTHER} for")
    assert world.calls[0]["address"] == OTHER
    assert await page.locator("#digestAddress").input_value() == OTHER
    # typing the Google email again, in capitals, means "my own": the function takes it from the account
    await page.fill("#digestAddress", EMAIL.upper())
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text(f"Check {EMAIL} for")
    assert world.calls[1]["address"] is None


@pytest.mark.parametrize(
    "bad", ["not an address", "a@b", "two@example.test, other@example.test", "x" * 250 + "@example.test"]
)
async def test_a_bad_address_is_refused_before_anything_is_sent(page: Page, digest_site: Path, bad: str) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.fill("#digestAddress", bad)
    await page.click("#digestSend")
    error = page.locator("#digestAddressError")
    await expect(error).to_be_visible()
    assert await page.locator("#digestAddress").get_attribute("aria-invalid") == "true"
    assert await page.evaluate("document.activeElement.id") == "digestAddress"
    assert world.function_requests == []
    await page.fill("#digestAddress", EMAIL)  # fixing it clears the message as soon as the value is fine
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert await error.is_hidden()


async def test_fr8_ac1_turning_it_off_does_the_one_click_unsubscribe_and_the_state_follows(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("on", filters={**EMPTY, "regions": ["Haifa and the north"]}, frequency="weekly"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    assert await state_of(page) == "on"
    assert "The email is on. New roles that match your filters go to" in await page.locator("#digestState").inner_text()
    assert await page.locator("#digestOff").inner_text() == "Turn the email off"
    await page.click("#digestOff")
    await expect(page.locator("#digestNote")).to_contain_text("The email is off. Nothing more will be sent")
    assert world.calls == [{"action": "turn_off"}]
    assert await state_of(page) == "unsubscribed"
    assert await page.locator("#digestOpen").get_attribute("data-digest-state") == "unsubscribed"
    assert "You unsubscribed, so nothing is sent" in await page.locator("#digestState").inner_text()
    assert await page.locator("#digestSend").inner_text() == "Turn the email on"
    assert await page.locator("#digestOff").is_hidden()
    assert await lines(page) == ["Every new role (no filters chosen)."]  # the saved filters were deleted
    assert await page.locator("#digestAddress").input_value() == EMAIL


async def test_turning_off_an_email_that_was_never_on_changes_nothing_and_says_so(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("waiting"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.row = None  # another device removed it meanwhile
    await page.click("#digestOff")
    await expect(page.locator("#digestNote")).to_have_text("The email was not on, so nothing was changed.")
    assert await state_of(page) == "off"


async def test_turning_it_on_again_after_an_unsubscribe_asks_for_a_new_confirmation(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("unsubscribed"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    assert await state_of(page) == "unsubscribed"
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert await state_of(page) == "waiting"


async def test_an_already_running_email_is_left_alone_and_the_page_says_so(page: Page, digest_site: Path) -> None:
    world = World(stored("on"))
    world.state_after = "on"
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    assert await page.locator("#digestSend").is_hidden()  # nothing to send while the address is the running one
    await page.fill("#digestAddress", OTHER)  # a new address is a new consent
    assert await page.locator("#digestSend").inner_text() == "Change the address and confirm it"
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_have_text(
        "The email was already on for this address, so nothing was changed and nothing was sent."
    )
    assert world.calls[0]["address"] == OTHER
    assert world.calls[0]["frequency"] == "daily"
    assert await state_of(page) == "on"


async def test_changing_the_address_of_a_running_email_says_it_pauses_until_confirmed(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("on"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.fill("#digestAddress", OTHER)
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("The email stays paused until then.")
    assert await state_of(page) == "waiting"


# ---- FR-8 AC 2: "Use these filters for my email" copies four filters, and only that button changes them ----


async def test_fr8_ac2_the_button_copies_exactly_regions_levels_role_types_and_keywords(
    page: Page, digest_site: Path
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await choose(page, "regions", "Tel Aviv area")
    await choose(page, "levels", "Intern")
    await choose(page, "levels", "Junior")
    await choose(page, "types", "Product")
    await page.fill("#q", "  growth   marketing ")
    await page.click("#onlyNew")  # a filter the email does not copy
    await page.select_option("#sort", "co")
    await page.check("#hideStale")
    await open_dialog(page)
    assert await lines(page) == ["Every new role (no filters chosen)."]  # nothing is copied until the button is pressed
    await page.click("#digestUseFilters")
    assert await lines(page) == [
        "Regions: Tel Aviv area",
        "Levels: Intern, Junior",
        "Role types: Product",
        "Keywords: growth   marketing",  # the words as typed, trimmed: the function collapses the spaces
    ]
    await expect(page.locator("#digestNote")).to_contain_text("Copied.")
    assert world.table_requests[-1].method == "GET"  # a draft: nothing is written until the email is turned on
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    (body,) = world.calls
    assert set(body["filters"]) == {"regions", "levels", "role_types", "keywords"}
    assert body["filters"]["regions"] == ["Tel Aviv area"]
    assert body["filters"]["levels"] == ["Intern", "Junior"]
    assert body["filters"]["role_types"] == ["Product"]
    assert body["filters"]["keywords"] == "growth   marketing"
    assert world.row is not None
    assert world.row["filters"] == body["filters"]


async def test_fr8_ac2_later_changes_to_the_dashboard_filters_do_not_change_the_email_until_the_button_is_pressed_again(
    page: Page, digest_site: Path
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await choose(page, "levels", "Intern")
    await open_dialog(page)
    await page.click("#digestUseFilters")
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    saved = {**EMPTY, "levels": ["Intern"]}
    assert world.row is not None
    assert world.row["filters"] == saved
    await page.click("#digestClose")
    # a quick search and a new level on the page
    await page.fill("#q", "designer")
    await choose(page, "levels", "Junior")
    await choose(page, "regions", "Tel Aviv area")
    await page.click("#digestOpen")
    await page.wait_for_selector("#digestDialog[data-state=waiting]")
    assert await lines(page) == ["Levels: Intern"]
    assert world.row["filters"] == saved
    assert [r.method for r in world.table_requests if r.method != "GET"] == []  # nothing was written
    # pressing it again saves the new set to the account's own row, and only the filters
    await page.click("#digestUseFilters")
    await expect(page.locator("#digestNote")).to_have_text("Saved. The next email uses this.")
    patch = [r for r in world.table_requests if r.method == "PATCH"]
    assert [json.loads(r.post_data) for r in patch] == [{"filters": world.row["filters"]}]
    assert await lines(page) == ["Regions: Tel Aviv area", "Levels: Intern, Junior", "Keywords: designer"]
    assert world.row["filters"] == {
        "regions": ["Tel Aviv area"],
        "levels": ["Intern", "Junior"],
        "role_types": [],
        "keywords": "designer",
    }


async def test_the_page_exposes_a_read_only_copy_of_the_four_filters(page: Page, digest_site: Path) -> None:
    await serve(page, digest_site)
    assert await page.evaluate("globalThis.misraEmailFilters()") == EMPTY
    await choose(page, "types", "Design")
    await page.fill("#q", "x" * 300)
    copy = await page.evaluate("globalThis.misraEmailFilters()")
    assert copy["role_types"] == ["Design"]
    assert len(copy["keywords"]) == MAX_KEYWORDS  # the function's limit
    await page.evaluate("globalThis.misraEmailFilters().role_types.push('Hacked')")
    assert await page.evaluate("globalThis.misraEmailFilters().role_types") == [
        "Design"
    ]  # a copy, not the page's own list


async def test_using_the_filters_before_the_roles_are_ready_is_explained(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.evaluate("delete globalThis.misraEmailFilters")
    await page.click("#digestUseFilters")
    await expect(page.locator("#digestError")).to_contain_text("still loading")


# ---- FR-8 AC 3: daily is the default, weekly is a choice ----


async def test_fr8_ac3_daily_is_the_default_and_weekly_is_sent_when_chosen(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    labels = await page.locator(".digest-frequency label").all_inner_texts()
    assert [t.strip() for t in labels] == ["Daily", "Weekly"]
    assert await page.locator("input[name=digestFrequency][value=daily]").is_checked()
    await page.check("input[name=digestFrequency][value=weekly]")
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert world.calls[0]["frequency"] == "weekly"
    assert await page.locator("input[name=digestFrequency][value=weekly]").is_checked()


async def test_changing_the_frequency_of_a_running_email_is_saved_to_the_row_and_nothing_else(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("on"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.check("input[name=digestFrequency][value=weekly]")
    await expect(page.locator("#digestNote")).to_have_text("Saved. The next email uses this.")
    assert [json.loads(r.post_data) for r in world.table_requests if r.method == "PATCH"] == [{"frequency": "weekly"}]
    assert world.function_requests == []  # no email, no function: a preference, not a consent
    assert "once a week" in await page.locator("#digestState").inner_text()
    assert await page.locator("#digestOpen").get_attribute("data-digest-frequency") == "weekly"


@pytest.mark.parametrize("mode", ["patch_error", "patch_none"])
async def test_a_frequency_that_could_not_be_saved_is_not_called_saved(
    page: Page, digest_site: Path, mode: str
) -> None:
    world = World(stored("waiting"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.table_mode = mode
    await page.check("input[name=digestFrequency][value=weekly]")
    await expect(page.locator("#digestError")).to_contain_text("We could not save that")
    assert await page.locator("#digestNote").is_hidden()
    assert await page.locator("input[name=digestFrequency][value=daily]").is_checked()  # what the account still holds


# ---- The states, and "send the confirmation again" ----


async def test_waiting_offers_to_send_the_confirmation_again_and_says_when_the_limit_is_reached(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("waiting", address=OTHER))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    assert await state_of(page) == "waiting"
    assert await page.locator("#digestAddress").input_value() == OTHER
    assert "Waiting for you to confirm" in await page.locator("#digestState").inner_text()
    assert await page.locator("#digestOff").is_visible()
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text(f"Check {OTHER} for a confirmation link")
    assert world.calls[0]["address"] == OTHER  # the address of the waiting row, kept
    world.answer = (429, {"code": "rate_limited", "message": "x"})
    await page.click("#digestSend")
    problem = page.locator("#digestError")
    await expect(problem).to_contain_text(
        "already asked for 3 confirmation emails today, the most allowed, so nothing was sent"
    )
    assert "tomorrow" in await problem.inner_text()
    assert await page.locator(
        "#digestNote"
    ).is_hidden()  # the earlier "sent" is gone: it does not describe this attempt


# ---- Honest errors ----


@pytest.mark.parametrize(("status", "error", "words"), FAILURES)
async def test_each_failure_is_explained_and_never_called_a_success(
    page: Page, digest_site: Path, status: int, error: dict[str, Any], words: str
) -> None:
    world = World()
    world.answer = (status, error)
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.click("#digestSend")
    problem = page.locator("#digestError")
    await expect(problem).to_be_visible()
    assert words in await problem.inner_text()
    assert await problem.get_attribute("role") == "alert"
    assert await page.locator("#digestNote").is_hidden()
    assert await state_of(page) == "off"  # read again: the account holds nothing
    assert await page.evaluate("document.activeElement.id") == "digestError"
    assert await page.locator("#digestSend").inner_text() == "Turn the email on"
    if error["code"] == "invalid_input":
        assert await page.locator("#digestAddressError").inner_text() == "Enter one email address."
    world.answer = None
    await page.click("#digestSend")  # a retry that works
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert await page.locator("#digestError").is_hidden()


@pytest.mark.parametrize("mode", ["garbage", "html", "not_ok"])
async def test_an_answer_that_is_not_the_functions_is_never_called_a_success(
    page: Page, digest_site: Path, mode: str
) -> None:
    world = World()
    world.function_mode = mode
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.click("#digestSend")
    await expect(page.locator("#digestError")).to_be_visible()
    assert await page.locator("#digestNote").is_hidden()
    assert await state_of(page) == "off"


async def test_a_function_that_cannot_be_reached_says_it_cannot_be_sure_and_reads_the_account_again(
    page: Page, digest_site: Path
) -> None:
    world = World()
    world.function_mode = "down"
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    reads = len(world.table_requests)
    await page.click("#digestSend")
    await expect(page.locator("#digestError")).to_contain_text("could not get an answer from the service")
    assert len(world.table_requests) > reads  # a reset connection can come after the work: the account is read again
    assert await page.locator("#digestNote").is_hidden()
    assert await state_of(page) == "off"


async def test_a_frequency_changed_while_a_request_runs_goes_back_to_what_the_account_holds(
    page: Page, digest_site: Path
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.gate = asyncio.Event()
    await page.click("#digestSend")
    await expect(page.locator("#digestSend")).to_have_attribute("aria-busy", "true")
    await page.click("input[name=digestFrequency][value=weekly]", force=True)
    assert await page.locator("input[name=digestFrequency][value=daily]").is_checked()
    world.gate.set()
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert world.calls[0]["frequency"] == "daily"
    assert world.table_requests[-1].method == "GET"  # nothing was saved for the weekly choice


async def test_a_slow_function_says_it_cannot_tell_and_shows_what_the_account_holds(
    page: Page, digest_site: Path
) -> None:
    world = World()
    world.function_mode = "hang"
    await page.add_init_script(FAST_TIMEOUT)
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.click("#digestSend")
    await expect(page.locator("#digestError")).to_contain_text("took too long to answer, so we cannot tell")
    assert await state_of(page) == "off"
    assert len([r for r in world.table_requests if r.method == "GET"]) >= READS_AFTER_A_WAIT  # read again


async def test_a_row_that_cannot_be_read_after_an_answer_is_said_too(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.table_mode = "read_error"
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    await expect(page.locator("#digestError")).to_contain_text("We could not read your email settings")


@pytest.mark.parametrize("mode", ["read_down", "read_error"])
async def test_settings_that_cannot_be_read_say_so_and_can_be_tried_again(
    page: Page, digest_site: Path, mode: str
) -> None:
    world = World(stored("on"))
    world.table_mode = mode
    await signed_in(page, digest_site, world, wait=False)
    await page.click("#digestOpen")
    await expect(page.locator("#digestError")).to_contain_text(
        "We could not read your email settings. Nothing was changed."
    )
    assert await state_of(page) == "unknown"
    assert await page.locator("#digestState").inner_text() == "We could not read your email settings."
    assert await page.locator("#digestForm").is_hidden()  # no controls that could act on a state the page does not know
    world.table_mode = "ok"
    await page.click("#digestRetry")
    await page.wait_for_selector("#digestDialog[data-state=on]")
    assert await page.locator("#digestError").is_hidden()
    assert await page.locator("#digestRetryRow").is_hidden()


async def test_when_the_settings_cannot_be_read_the_dialog_can_still_be_closed_and_tried_again(
    page: Page, digest_site: Path
) -> None:
    """The form is hidden then, so Close must not live in it: on a touchscreen it is the only way out."""
    world = World(stored("on"))
    world.table_mode = "read_down"
    await page.set_viewport_size(PHONE)
    await signed_in(page, digest_site, world, wait=False)
    await page.click("#digestOpen")
    await expect(page.locator("#digestError")).to_be_visible()
    assert await page.locator("#digestForm").is_hidden()
    close = page.locator("#digestClose")
    await expect(close).to_be_visible()
    size = await close.bounding_box()
    assert size is not None
    assert size["height"] >= TAP
    assert size["width"] >= TAP
    await close.click()
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "digestOpen"  # the focus goes back
    # opened again it can be tried again, and then Close still works
    await page.click("#digestOpen")
    await expect(page.locator("#digestRetry")).to_be_visible()
    world.table_mode = "ok"
    await page.click("#digestRetry")
    await page.wait_for_selector("#digestDialog[data-state=on]")
    await page.click("#digestClose")
    await expect(page.locator(DIALOG)).to_be_hidden()


async def test_the_dialog_is_busy_while_it_waits_and_one_click_sends_one_request(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.gate = asyncio.Event()
    await page.click("#digestSend")
    send = page.locator("#digestSend")
    await expect(send).to_have_attribute("aria-busy", "true")
    for name in ("digestSend", "digestClose", "digestUseFilters"):
        assert await page.locator(f"#{name}").get_attribute("aria-disabled") == "true"
    await page.click("#digestSend", force=True)  # aria-disabled does not stop Playwright: a real second click
    await page.focus("#digestAddress")
    await page.keyboard.press("Enter")
    await page.click("#digestClose", force=True)
    await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_visible()
    world.gate.set()
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert len(world.function_requests) == 1
    assert await send.get_attribute("aria-busy") == "false"


async def test_an_answer_is_shown_even_if_the_dialog_was_closed_while_it_waited(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    world.gate = asyncio.Event()
    await page.click("#digestSend")
    await expect(page.locator("#digestSend")).to_have_attribute("aria-busy", "true")
    await page.evaluate("document.getElementById('digestDialog').close()")  # what a second Esc does in Chromium
    await expect(page.locator(DIALOG)).to_be_hidden()
    world.gate.set()
    await expect(page.locator(DIALOG)).to_be_visible()
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")


async def test_the_focus_goes_to_the_outcome_when_the_pressed_button_goes_away(page: Page, digest_site: Path) -> None:
    world = World(stored("on"))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.fill("#digestAddress", OTHER)
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")
    assert await page.evaluate("document.activeElement.id") in {"digestSend", "digestNote"}
    world.row = stored("on")
    await page.click("#digestClose")
    await page.click("#digestOpen")
    await page.wait_for_selector("#digestDialog[data-state=on]")
    await page.click("#digestOff")
    await expect(page.locator("#digestNote")).to_contain_text("The email is off")
    assert await page.evaluate("document.activeElement.id") == "digestNote"  # the Turn off button is gone


# ---- The account comes and goes ----


async def test_signing_out_closes_the_dialog_and_forgets_the_settings(page: Page, digest_site: Path) -> None:
    world = World(stored("on", address=OTHER))
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    assert await page.locator("#digestAddress").input_value() == OTHER
    await page.evaluate("globalThis.misraAccount = null; document.dispatchEvent(new CustomEvent('misra:account'))")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.locator("#digestForm").is_hidden()
    world.row = None
    await page.evaluate(
        "globalThis.misraAccount = { client: { from: () => { throw new Error('x'); } }, userId: 'other', email: null };"
        "document.dispatchEvent(new CustomEvent('misra:account'))"
    )
    await page.wait_for_selector("#digestOpen[data-digest-state=unknown]")
    assert await page.locator("#digestForm").is_hidden()  # nothing of the earlier account is shown or kept
    assert OTHER not in await page.locator("#digestState").inner_text()


async def test_the_same_user_again_does_not_reload_the_settings(page: Page, digest_site: Path) -> None:
    world = World(stored("on"))
    await signed_in(page, digest_site, world)
    reads = len(world.table_requests)
    await page.evaluate("document.dispatchEvent(new CustomEvent('misra:account'))")
    await page.wait_for_timeout(100)
    assert len(world.table_requests) == reads


# ---- Agents and the hooks ----


async def test_no_webmcp_tool_can_turn_the_email_on_or_send_mail_and_the_state_is_readable_from_the_page(
    page: Page, digest_site: Path
) -> None:
    world = World(stored("on", frequency="weekly"))
    await page.add_init_script(WEBMCP)
    await signed_in(page, digest_site, world)
    names = await page.evaluate("Object.keys(window.__tools)")
    assert names  # the tools did register
    for name in names:
        lowered = name.lower()
        assert not any(
            word in lowered for word in ("subscribe", "email", "mail", "digest", "confirm", "unsubscribe")
        ), name
    # a tool that reads the account's state does not carry the email settings either
    sync = json.dumps(await page.evaluate(CALL, ["get_sync_status", {}])).lower()
    assert "subscri" not in sync
    assert EMAIL not in sync
    assert world.function_requests == []
    button = page.locator("#digestOpen")
    assert await button.get_attribute("data-digest-state") == "on"
    assert await button.get_attribute("data-digest-frequency") == "weekly"
    assert EMAIL not in (
        await button.evaluate("e => JSON.stringify(Object.assign({}, e.dataset))")
    )  # no address in the hooks


# ---- Accessibility, keyboard and the phone ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["off", "waiting", "on", "unsubscribed"])
async def test_the_dialog_has_no_axe_violations_in_any_state_or_theme(
    page: Page, digest_site: Path, scheme: str, state: str
) -> None:
    world = World(None if state == "off" else stored(state))
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    world.answer = (429, {"code": "rate_limited", "message": "x"})
    if state != "on":
        await page.click("#digestSend")
        await expect(page.locator("#digestError")).to_be_visible()
        assert await page.evaluate(AXE_RUN) == []


async def test_the_dialog_reads_well_on_a_phone_and_every_control_is_big_enough(page: Page, digest_site: Path) -> None:
    world = World(stored("waiting"))
    await page.set_viewport_size(PHONE)
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    box = await page.locator(DIALOG).bounding_box()
    assert box is not None
    assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in (
        "#digestAddress",
        "#digestUseFilters",
        "#digestOff",
        "#digestSend",
        "#digestClose",
        ".digest-frequency label",
    ):
        for item in await page.locator(selector).all():
            size = await item.bounding_box()
            assert size is not None
            assert round(size["height"]) >= TAP, selector
    button = await page.locator("#digestClose").bounding_box()
    assert button is not None
    assert await page.evaluate("document.getElementById('digestOpen').getBoundingClientRect().width") > 0


async def test_the_lawyers_wording_is_on_the_control_with_big_links(page: Page, digest_site: Path) -> None:
    """The lawyer's microcopy: the toggle label, the helper text with the address and both links, Daily and Weekly."""
    world = World()
    await page.set_viewport_size(PHONE)
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await expect(page.locator("#digestToggleLabel")).to_have_text("Email me new matching roles")
    line = page.locator("#digestConsent")
    await expect(line).to_be_visible()
    text = (await line.evaluate("el => el.textContent")).replace(" (opens in a new tab)", "")
    assert text == (
        f"We will first send a confirmation link to {EMAIL}. Nothing is sent until you confirm, and you get an email "
        "only when there are new matches. One-click unsubscribe is in every email. By turning this on you agree to "
        "the Terms and the Privacy Notice."
    )
    blob = "https://github.com/ravidorr/misra/blob/main"
    terms = line.get_by_role("link", name="Terms (opens in a new tab)")
    privacy = line.get_by_role("link", name="Privacy Notice (opens in a new tab)")
    assert await terms.get_attribute("href") == f"{blob}/TERMS.md"
    assert await privacy.get_attribute("href") == f"{blob}/PRIVACY.md"
    for link in await line.locator("a").all():
        assert await link.get_attribute("target") == "_blank"
        assert await link.get_attribute("rel") == "noopener noreferrer"
        size = await link.bounding_box()
        assert size is not None
        assert size["height"] >= TAP
        assert size["width"] >= TAP
    # the helper names the address that is typed, and follows it
    await page.fill("#digestAddress", OTHER)
    await expect(page.locator("#digestConsentTo")).to_have_text(OTHER)
    labels = await page.locator(".digest-frequency label").all_inner_texts()
    assert [t.strip() for t in labels] == ["Daily", "Weekly"]


@pytest.mark.parametrize("word", ["daily", "weekly"])
async def test_after_turning_it_on_the_status_names_the_address_and_the_schedule(
    page: Page, digest_site: Path, word: str
) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await open_dialog(page)
    await page.click(f"input[name=digestFrequency][value={word}]")
    await page.click("#digestSend")
    await expect(page.locator("#digestNote")).to_have_text(
        f"Check {EMAIL} for a confirmation link. Your {word} digest will start only after you click it."
    )


async def test_with_the_switch_off_the_page_has_no_agreement_line(page: Page, request: pytest.FixtureRequest) -> None:
    await serve(page, request.getfixturevalue("site"))
    assert await page.locator("#digestConsent").count() == 0


async def test_the_dialog_works_from_the_keyboard_and_gives_the_focus_back(page: Page, digest_site: Path) -> None:
    world = World()
    await signed_in(page, digest_site, world)
    await page.focus("#digestOpen")
    await page.keyboard.press("Enter")
    await expect(page.locator(DIALOG)).to_be_visible()
    await page.wait_for_selector("#digestDialog[data-state=off]")
    seen = []
    for _ in range(7):
        await page.keyboard.press("Tab")
        seen.append(
            await page.evaluate("const e = document.activeElement; e.id || e.name || e.textContent.slice(0, 5)")
        )
    # the two agreement links (Terms, Privacy Notice) come with the helper text, before the frequency
    assert seen == [
        "digestAddress",
        "Terms",
        "Priva",
        "digestFrequency",
        "digestUseFilters",
        "digestSend",
        "digestClose",
    ]
    await page.keyboard.press("Escape")
    await expect(page.locator(DIALOG)).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "digestOpen"
    await page.focus("#digestOpen")
    await page.keyboard.press("Enter")
    await page.fill("#digestAddress", EMAIL)
    await page.keyboard.press("Enter")  # submitting the form from a field turns it on
    await expect(page.locator("#digestNote")).to_contain_text("for a confirmation link")


async def test_the_page_loads_the_digest_script_from_its_own_origin_with_an_integrity_hash(digest_site: Path) -> None:
    html = (digest_site / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="digest.js" integrity="sha384-' in html
    assert "__SRI_" not in html
    assert "<!--digest-->" not in html
    assert (digest_site / "digest.js").is_file()
    assert "__SUPABASE_URL__" not in (digest_site / "digest.js").read_text(encoding="utf-8")
    assert ORIGIN  # the fake origin the tests serve it from
