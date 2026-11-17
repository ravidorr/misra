"""The browser's own choices and filters brought into the account at the first sign-in, and filters that follow the user
(PLAN.md step 4.3; PRD FR-2 AC 4 to 6), run in a real browser against the built page with a fake sign-in service and
fake `user_jobs` and `user_prefs` tables. The signed-in stars and discards are step 4.2 (tests/test_job_choices.py).

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests.test_dashboard_page import (
    AXE,
    ISRAEL_JOBS,
    PHONE,
    TAP_MIN,
    USER_ID,
    WEBMCP,
    AuthBackend,
    ChoicesBackend,
    PrefsBackend,
    account_action,
    serve,
    site,  # noqa: F401  (a fixture)
)
from tests.test_job_choices import (
    DISCARDED,
    PENDING,
    PM1,
    STARRED,
    ask,
    discard_row,
    is_saved,
    pending,
    star_row,
    until,
)
from tests.test_sign_in import AXE_RUN, come_back, start_sign_in, stored

FILTERS = "il-jobs-filters-v3"
PENDING_FILTERS = "misra-pending-filters-v1"
IMPORTED = "misra-imported-v1"
DEFAULTS: dict[str, Any] = {
    "q": "",
    "company": "",
    "loc": "il",
    "sort": "new",
    "levels": [],
    "types": [],
    "regions": [],
    "places": [],
    "remote": False,
    "onlyNew": False,
    "showGone": False,
    "hideStale": False,
    "size": 10,
}
BOX = "#importBox"
OFFER_TEXT = "1 discard and 1 star"
CHUNK = 200
STARS_TO_MOVE = 450


def card(job_id: str) -> str:
    return f"article[data-job-id='Acme|{job_id}']"


def local_star(job_id: str, company: str = "Acme") -> dict[str, Any]:
    """A star kept in the browser, as the page stores it."""
    return {"c": company, "i": job_id, "t": f"Role {job_id}", "l": "Haifa", "u": "https://x.test/j"}


async def seed(page: Page, **items: Any) -> None:
    """What this browser already holds before the user signs in: each value is stored as JSON under its key."""
    await page.evaluate(
        "items => { for (const [k, v] of Object.entries(items)) localStorage.setItem(k, JSON.stringify(v)); }",
        {
            {
                "gone": DISCARDED,
                "starred": STARRED,
                "filters": FILTERS,
                "pending_filters": PENDING_FILTERS,
                "pending": PENDING,
            }[name]: value
            for name, value in items.items()
        },
    )


async def sign_in(  # noqa: PLR0913  (each stand-in is optional)
    page: Page,
    site: Path,
    choices: ChoicesBackend | None = None,
    prefs: PrefsBackend | None = None,
    *,
    tools: bool = False,
    local: dict[str, Any] | None = None,
    wait: bool = True,
) -> AuthBackend:
    """The whole sign-in, with this browser's own data stored first and the account's rows behind the page."""
    if tools:
        await page.add_init_script(WEBMCP)
    auth = AuthBackend()
    await serve(page, site, auth=auth, choices=choices or ChoicesBackend(), prefs=prefs or PrefsBackend())
    await seed(page, **(local or {}))
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#accountUser:not([hidden])")
    if wait:
        await settled(page)
    return auth


async def settled(page: Page) -> None:
    """Both of the account's lists have answered (loaded or failed), so the page has made up its mind."""
    await page.wait_for_selector("#list[data-choices=loaded], #list[data-choices=failed]")
    await expect(page.locator("#list")).not_to_have_attribute("data-filters", "loading")


async def filters_state(page: Page) -> str | None:
    return await page.locator("#list").get_attribute("data-filters")


async def stored_json(page: Page, key: str) -> Any:
    raw = (await stored(page)).get(key)
    return json.loads(raw) if raw else None


async def result(page: Page) -> str:
    return await page.locator("#importResult").inner_text()


async def sync_note(page: Page) -> str:
    return await page.locator("#syncNote").inner_text()


def saved_filters(prefs: PrefsBackend) -> dict[str, Any]:
    assert prefs.row is not None
    filters: dict[str, Any] = prefs.row["filters"]
    return filters


async def chosen(page: Page, group: str) -> list[str]:
    """The values chosen in one of the chip filters."""
    return await page.eval_on_selector_all(
        f".chip[data-group={group}][aria-pressed=true]", "els => els.map(e => e.dataset.value)"
    )  # type: ignore[no-any-return]


def mixed_local() -> dict[str, Any]:
    """A browser with a duplicate, a conflict of each kind and a closed job among its choices."""
    return {
        "gone": {"Acme|1": "2026-11-01", "Acme|2": "2026-11-01", "Acme|5": "2026-11-01", "Gone|9": "2026-11-01"},
        "starred": {"Acme|3": local_star("3"), "Acme|6": local_star("6")},
    }


def mixed_account() -> ChoicesBackend:
    """The account already holds a star on Acme|1 (the browser discarded it), the same discard as the browser on
    Acme|2, and a discard on Acme|6 (which the browser starred)."""
    return ChoicesBackend(
        [
            star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme"),
            discard_row("Acme", "2"),
            discard_row("Acme", "6"),
        ]
    )


# ---- AC 4: the browser's discards are offered once, without duplicates and without overriding the account ----


async def test_fr2_ac4_the_discards_in_this_browser_are_offered_at_the_first_sign_in(page: Page, site: Path) -> None:
    choices = mixed_account()
    await sign_in(page, site, choices, local=mixed_local())
    box = page.locator(BOX)
    await expect(box).to_be_visible()
    # Only what the account has no choice about, on a job that is still open, is offered.
    assert (await box.get_attribute("data-discards"), await box.get_attribute("data-stars")) == ("1", "1")
    assert await box.get_attribute("data-filters") == "none"
    assert await page.locator("#importChoicesText").inner_text() == OFFER_TEXT
    assert await page.locator("#importFiltersRow").is_hidden()
    assert choices.writes == []  # nothing is sent before the user says yes
    assert await page.locator("#status").inner_text() != ""  # and a screen reader is told
    await page.click("#importYes")
    await until(lambda: choices.writes)
    [write] = choices.writes
    assert write["op"] == "import"
    assert write["ignore_duplicates"] is True  # the service ignores a row it already has, even a newer one
    assert {(r["job_id"], r["state"]) for r in write["rows"]} == {("5", "discard"), ("3", "star")}
    star = next(r for r in write["rows"] if r["job_id"] == "3")
    assert (star["title"], star["location"], star["url"]) == ("Role 3", "Haifa", "https://x.test/j")
    # The account's own choices are untouched: no duplicate, nothing replaced.
    assert [choices.state_of("Acme", n) for n in "12356"] == ["star", "discard", "star", "discard", "discard"]
    assert choices.state_of("Gone", "9") is None  # a closed job's discard means nothing
    await expect(page.locator(BOX)).to_be_hidden()
    await expect(page.locator("#importResult .notice")).to_have_text("Brought over 2 choices from this browser.")
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert await page.locator(card("5")).count() == 0  # the imported discard hides the job
    assert await page.locator("#starredView").inner_text() == "Starred (2)"
    assert await page.evaluate("document.activeElement.id") == "list-h"
    assert await pending(page) == {}  # confirmed, so nothing waits in this browser
    # Copy, not move: this browser's own choices are as they were, for when the user is signed out.
    assert list(await stored_json(page, DISCARDED)) == list(mixed_local()["gone"])
    assert set(await stored_json(page, STARRED)) == {"Acme|3", "Acme|6"}
    await page.click("#importClose")
    assert await page.locator("#importResult").inner_text() == ""


async def test_fr2_ac4_the_offer_is_made_once_and_not_again_after_no_thanks(page: Page, site: Path) -> None:
    choices = mixed_account()
    await sign_in(page, site, choices, local=mixed_local())
    await page.click("#importNo")
    await expect(page.locator(BOX)).to_be_hidden()
    assert choices.writes == []
    assert await page.locator("#importResult").inner_text() == ""
    assert USER_ID in await stored_json(page, IMPORTED)
    await page.reload()
    await settled(page)
    assert await page.locator(BOX).is_hidden()  # decided: not asked again on this browser for this account
    assert choices.writes == []
    assert await page.locator(card("5")).count() == 1  # the browser's discard is not applied to the account's view


async def test_nothing_is_offered_when_this_browser_holds_nothing_new(page: Page, site: Path) -> None:
    choices = mixed_account()
    local = {"gone": {"Acme|2": "2026-11-01", "Gone|9": "2026-11-01"}, "starred": {"Acme|1": local_star("1")}}
    await sign_in(page, site, choices, local=local)
    assert await page.locator(BOX).is_hidden()  # a duplicate, a closed job and a star the account already has
    assert (await stored(page)).get(IMPORTED) is None  # and the offer stays open for a later, real one


async def test_nothing_is_offered_to_a_visitor_with_an_empty_browser(page: Page, site: Path) -> None:
    await sign_in(page, site, mixed_account())
    assert await page.locator(BOX).is_hidden()
    assert await page.locator("#importResult").inner_text() == ""


async def test_the_offer_waits_until_the_account_choices_are_loaded(page: Page, site: Path) -> None:
    choices = mixed_account()
    choices.mode = "load_down"
    await sign_in(page, site, choices, local=mixed_local())
    assert await page.locator(BOX).is_hidden()  # it cannot tell what is a duplicate, so it does not offer
    assert (await stored(page)).get(IMPORTED) is None
    choices.mode = "ok"
    await page.click("#syncRetry")
    await expect(page.locator(BOX)).to_be_visible()
    assert choices.writes == []


async def test_the_offer_waits_until_the_account_filters_are_loaded(page: Page, site: Path) -> None:
    prefs = PrefsBackend({"q": "x"}, mode="load_down")
    await sign_in(page, site, mixed_account(), prefs, local=mixed_local())
    assert await page.locator(BOX).is_hidden()
    prefs.mode = "ok"
    await page.click("#syncRetry")
    await expect(page.locator(BOX)).to_be_visible()


async def test_a_bringing_over_that_cannot_be_saved_now_is_kept_and_survives_a_reload(page: Page, site: Path) -> None:
    choices = mixed_account()
    await sign_in(page, site, choices, local=mixed_local())
    choices.mode = "down"
    await page.click("#importYes")
    await expect(page.locator("#importResult .notice")).to_have_text(
        "2 choices from this browser are kept on this device and saved when the service answers."
    )
    assert set(await pending(page)) == {"Acme|5", "Acme|3"}
    assert all(op["keep"] for op in (await pending(page)).values())
    assert await page.locator(card("5")).count() == 0  # shown at once, as any choice is
    assert "not saved to your account yet" in await sync_note(page)
    choices.mode = "ok"
    await page.reload()
    await settled(page)
    await until(lambda: choices.state_of("Acme", "5") == "discard" and choices.state_of("Acme", "3") == "star")
    assert await page.locator(BOX).is_hidden()  # answered: not offered again
    await until(lambda: is_saved(page))


async def test_a_bringing_over_the_row_limit_refuses_is_said_so(page: Page, site: Path) -> None:
    choices = mixed_account()
    await sign_in(page, site, choices, local=mixed_local())
    choices.mode = "limit"
    await page.click("#importYes")
    await expect(page.locator("#importResult .notice")).to_have_text("2 choices were not accepted by the service.")
    await expect(page.locator("#syncNote .notice")).to_contain_text("reached the limit")
    assert await pending(page) == {}  # not retried for ever
    assert await page.locator(card("5")).count() == 1  # and not shown as discarded: it was not saved


async def test_only_as_many_choices_as_fit_in_the_account_are_brought_over(page: Page, site: Path) -> None:
    full = [star_row(str(n), company="Old") for n in range(1998)]  # two places left of 2,000
    choices = ChoicesBackend(full)
    local = {"gone": {"Acme|1": "2026-11-01", "Acme|2": "2026-11-01", "Acme|3": "2026-11-01"}}
    await sign_in(page, site, choices, local=local)
    await page.click("#importYes")
    await until(lambda: choices.writes)
    assert len(choices.writes[0]["rows"]) == 2  # noqa: PLR2004
    await expect(page.locator("#importResult .notice")).to_have_text(
        "Brought over 2 choices from this browser. 1 choice was left out: an account keeps at most 2,000."
    )


async def test_a_large_bringing_over_is_sent_in_chunks(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    local = {"starred": {f"Old|{n}": local_star(str(n), "Old") for n in range(STARS_TO_MOVE)}}
    await sign_in(page, site, choices, local=local)
    await page.click("#importYes")
    await until(lambda: len(choices.rows) == STARS_TO_MOVE)
    assert [len(w["rows"]) for w in choices.writes] == [CHUNK, CHUNK, STARS_TO_MOVE - 2 * CHUNK]
    await expect(page.locator("#importResult .notice")).to_have_text(
        f"Brought over {STARS_TO_MOVE} choices from this browser."
    )
    assert await pending(page) == {}


# ---- AC 5 and 6: filters follow the user, and the browser's are offered with the discards ----


async def test_fr2_ac6_the_filters_in_this_browser_are_offered_and_saved_to_an_account_without_any(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend()
    local = {"filters": {**DEFAULTS, "q": "Manager", "sort": "co", "hideStale": True, "levels": ["Junior"]}}
    await sign_in(page, site, ChoicesBackend(), prefs, local=local)
    box = page.locator(BOX)
    await expect(box).to_be_visible()
    assert await box.get_attribute("data-filters") == "new"
    assert await page.locator("#importChoicesRow").is_hidden()
    assert await page.locator("#importFiltersText").inner_text() == "Your filters (your account has none saved yet)"
    assert prefs.writes == []
    await page.click("#importYes")
    await until(lambda: prefs.writes)
    [write] = prefs.writes
    assert set(write) == {"on_conflict", "user_id", "filters"}  # never email_settings: step 6.2 owns it
    assert write["on_conflict"] == "user_id"
    assert write["user_id"] == USER_ID
    assert write["filters"] == {**DEFAULTS, "q": "Manager", "sort": "co", "hideStale": True, "levels": ["Junior"]}
    await expect(page.locator("#importResult .notice")).to_have_text("Your filters are saved to your account.")
    assert await page.input_value("#q") == "Manager"
    await expect(page.locator("#list")).to_have_attribute("data-filters", "synced")
    assert (await stored_json(page, FILTERS))["q"] == "Manager"  # copy, not move
    assert not await stored_json(page, PENDING_FILTERS)


async def test_fr2_ac6_bringing_filters_over_replaces_the_account_ones_when_the_user_says_so(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "Designer", "sort": "title"})
    local = {"filters": {**DEFAULTS, "q": "Manager"}}
    await sign_in(page, site, ChoicesBackend(), prefs, local=local)
    assert await page.locator(BOX).get_attribute("data-filters") == "replace"
    assert await page.locator("#importFiltersText").inner_text() == (
        "Your filters (they replace the filters saved in your account)"
    )
    assert await filters_state(page) == "paused"  # nothing is saved over the account's until the user answers
    assert prefs.writes == []
    await page.click("#importYes")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs) == {**DEFAULTS, "q": "Manager"}
    assert await page.input_value("#sort") == "new"


async def test_a_filter_change_made_while_the_offer_waits_is_kept_and_saved_after_the_answer(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "Designer", "sort": "title"})
    local = {"filters": {**DEFAULTS, "q": "Manager"}}
    await sign_in(page, site, ChoicesBackend(), prefs, local=local)
    assert await page.input_value("#q") == "Manager"  # this browser's own filters until the user answers
    await page.fill("#q", "Analyst")
    await page.wait_for_timeout(1200)
    assert prefs.writes == []  # held while the offer waits
    await page.uncheck("#importFilters")
    await page.click("#importYes")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["q"] == "Analyst"  # the newest change is what is saved
    assert saved_filters(prefs)["sort"] == "new"


async def test_fr2_ac6_declining_the_filters_keeps_the_account_ones_and_the_browsers_own(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "Designer", "sort": "title", "regions": ["Sharon"], "company": "Acme"})
    local = {"filters": {**DEFAULTS, "q": "Manager"}}
    await sign_in(page, site, ChoicesBackend(), prefs, local=local)
    await page.click("#importNo")
    assert await page.input_value("#q") == "Designer"
    assert await page.input_value("#sort") == "title"
    assert await page.input_value("#company") == "Acme"
    assert await chosen(page, "regions") == ["Sharon"]
    assert prefs.writes == []
    assert (await stored_json(page, FILTERS))["q"] == "Manager"  # the browser's own are left alone
    await expect(page.locator("#syncNote .notice")).to_have_text("Your filters were restored from your account.")
    await account_action(page, "#signOut")
    await page.wait_for_selector("#signIn")
    assert await page.input_value("#q") == "Manager"  # back to the browser's own filters
    assert await page.input_value("#sort") == "new"
    assert await chosen(page, "regions") == [""]  # only "All"


async def test_fr2_ac5_the_filters_of_the_account_appear_on_another_device(page: Page, site: Path) -> None:
    saved = {
        **DEFAULTS,
        "q": "manager",
        "company": "Acme",
        "loc": "all",
        "sort": "co",
        "levels": ["Junior", "Intern"],
        "types": ["Product"],
        "regions": ["Tel Aviv area"],
        "places": ["Tel Aviv, Israel"],
        "remote": True,
        "onlyNew": True,
        "showGone": True,
        "hideStale": True,
        "size": 25,
    }
    prefs = PrefsBackend(saved)
    await sign_in(
        page, site, ChoicesBackend(), prefs
    )  # a browser that has never been used: no offer, the filters just appear
    assert await page.locator(BOX).is_hidden()
    assert await page.input_value("#q") == "manager"
    assert await page.input_value("#company") == "Acme"
    assert await page.input_value("#loc") == "all"
    assert await page.input_value("#sort") == "co"
    assert sorted(await chosen(page, "levels")) == ["Intern", "Junior"]
    assert await chosen(page, "types") == ["Product"]
    assert await chosen(page, "regions") == ["Tel Aviv area"]
    assert await page.is_checked("#hideStale")
    assert await page.is_checked("#showGone")
    assert await page.get_attribute("#onlyNew", "aria-pressed") == "true"
    assert await page.get_attribute("#remote", "aria-pressed") == "true"
    assert await page.input_value("#size") == "25"
    assert await page.is_checked("#places input[value='Tel Aviv, Israel']")
    assert await filters_state(page) == "synced"
    assert await sync_note(page) == "Your filters were restored from your account."
    assert prefs.writes == []  # reading the account's filters never writes them back
    assert await stored_json(page, FILTERS) is None  # and the browser's own were not touched


async def test_filters_the_data_no_longer_has_are_dropped_and_a_damaged_row_is_read_as_defaults(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend(
        {
            "levels": ["Wizard", "Junior"],
            "company": "Nobody",
            "sort": "sideways",
            "q": 5,
            "regions": "Sharon",
            "size": 7,
        }
    )
    await sign_in(page, site, ChoicesBackend(), prefs)
    assert await chosen(page, "levels") == ["Junior"]
    assert await page.input_value("#company") == ""
    assert await page.input_value("#sort") == "new"
    assert await page.input_value("#q") == ""
    assert await chosen(page, "regions") == [""]  # only "All"
    assert await page.input_value("#size") == "10"


async def test_fr2_ac5_a_filter_change_is_saved_to_the_account_once_typing_pauses(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    await page.locator("#q").press_sequentially("prod", delay=50)
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await until(lambda: prefs.writes)
    await expect(page.locator("#list")).to_have_attribute("data-filters", "synced")
    assert len(prefs.writes) == 1  # a search is saved once, not for each key
    assert prefs.writes[0]["filters"] == {**DEFAULTS, "q": "prod", "levels": ["Junior"]}
    assert set(prefs.writes[0]) == {"on_conflict", "user_id", "filters"}
    assert not await stored_json(page, PENDING_FILTERS)
    assert await stored_json(page, FILTERS) is None  # signed in, the browser's own filters are left alone
    await page.click("#reset")
    await until(lambda: len(prefs.writes) == 2)  # noqa: PLR2004
    assert saved_filters(prefs) == {**DEFAULTS, "q": "prod"}  # the search words are not a filter: reset keeps them
    await page.click("#undoReset")  # the undo is saved to the account too
    await until(lambda: len(prefs.writes) == 3)  # noqa: PLR2004
    assert saved_filters(prefs) == {**DEFAULTS, "q": "prod", "levels": ["Junior"]}


async def test_fr2_ac5_the_account_filters_win_over_this_browsers_when_the_page_loads(page: Page, site: Path) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "account"})
    local = {"filters": {**DEFAULTS, "q": "browser"}}
    await sign_in(page, site, ChoicesBackend(), prefs, local=local)
    await page.click("#importNo")
    await page.reload()
    await settled(page)
    assert await page.input_value("#q") == "account"
    assert prefs.writes == []


async def test_a_change_the_account_has_not_confirmed_is_kept_and_wins_over_it_after_a_reload(
    page: Page, site: Path
) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "account"}, mode="down")
    await sign_in(page, site, ChoicesBackend(), prefs)
    assert await filters_state(page) == "failed"
    await page.fill("#q", "mine")
    await expect(page.locator("#syncNote")).to_contain_text("Your filter change is kept on this device")
    await expect(page.locator("#syncNote")).to_contain_text("Your saved filters could not be loaded")
    assert (await stored_json(page, PENDING_FILTERS))[USER_ID]["q"] == "mine"
    prefs.mode = "ok"
    await page.reload()  # the tab closed with the change unsent
    await settled(page)
    assert await page.input_value("#q") == "mine"
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["q"] == "mine"  # the account's value did not win over a change made here
    await expect(page.locator("#list")).to_have_attribute("data-filters", "synced")
    assert not await stored_json(page, PENDING_FILTERS)


async def test_a_filter_change_made_while_the_service_is_down_is_saved_later(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    prefs.mode = "down"
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "Your filter change is kept on this device and saved to your account when the service answers."
    )
    assert await filters_state(page) == "later"
    assert (await stored_json(page, PENDING_FILTERS))[USER_ID]["levels"] == ["Junior"]
    assert await chosen(page, "levels") == ["Junior"]  # the page keeps using what the user chose
    prefs.mode = "ok"
    await page.click("#syncRetry")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["levels"] == ["Junior"]
    await expect(page.locator("#syncNote")).to_have_text("")
    assert await filters_state(page) == "synced"


async def test_a_filter_change_is_saved_when_the_browser_comes_back_online(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    prefs.mode = "error"
    await page.click("#onlyNew")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    prefs.mode = "ok"
    await page.evaluate("window.dispatchEvent(new Event('online'))")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["onlyNew"] is True


async def test_an_expired_session_keeps_the_filter_change_for_later(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    prefs.mode = "expired"
    await page.click("#remote")
    await expect(page.locator("#syncNote .notice")).to_contain_text("kept on this device")
    assert (await stored_json(page, PENDING_FILTERS))[USER_ID]["remote"] is True


async def test_filters_the_service_refuses_are_said_so_and_not_retried(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    prefs.mode = "refused"
    await page.click("#remote")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "The service did not accept your filters, so they were not saved to your account."
    )
    assert await filters_state(page) == "refused"
    assert not await stored_json(page, PENDING_FILTERS)
    assert await page.locator("#syncRetry").count() == 0
    prefs.mode = "ok"
    await page.click("#onlyNew")  # a later change works and clears the message
    await until(lambda: prefs.writes)
    await expect(page.locator("#syncNote")).to_have_text("")


async def test_search_words_are_cut_to_a_sane_length_before_they_are_saved(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    await page.fill("#q", "word " * 5000)
    await until(lambda: prefs.writes)
    assert len(saved_filters(prefs)["q"]) == 200  # noqa: PLR2004


async def test_nothing_is_saved_over_the_account_filters_when_they_could_not_be_loaded(page: Page, site: Path) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "account"}, mode="load_down")
    local = {"filters": {**DEFAULTS, "q": "browser"}}
    await sign_in(page, site, mixed_account(), prefs, local=local)
    assert await filters_state(page) == "failed"
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "Your saved filters could not be loaded, so this device uses its own filters for now."
    )
    assert await page.locator(BOX).is_hidden()
    await page.wait_for_timeout(1500)
    assert prefs.writes == []  # never by itself
    prefs.mode = "ok"
    await page.click("#syncRetry")
    await expect(page.locator("#list")).not_to_have_attribute("data-filters", "failed")
    await page.click("#importNo")
    assert await page.input_value("#q") == "account"
    assert prefs.writes == []


async def test_an_explicit_change_after_a_failed_load_is_saved(page: Page, site: Path) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "account"}, mode="load_down")
    await sign_in(page, site, ChoicesBackend(), prefs)
    await page.click("#onlyNew")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["onlyNew"] is True
    await expect(page.locator("#syncNote")).to_have_text("")  # the account's filters are known now: what was just saved


async def test_signing_out_ends_the_offer_to_undo_a_reset(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs, local={"filters": {**DEFAULTS, "q": "browser"}})
    await page.click("#importNo")
    await page.click(".chip[data-group=levels][data-value=Junior]")
    await page.click("#reset")
    await expect(page.locator("#undoReset")).to_have_count(1)
    await account_action(page, "#signOut")
    await page.wait_for_selector("#signIn")
    await expect(page.locator("#undoReset")).to_have_count(0)  # it would put the account's filters in this browser


async def test_signing_out_with_an_unsent_filter_change_says_so_and_keeps_it(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    auth = await sign_in(page, site, ChoicesBackend(), prefs, local={"filters": {**DEFAULTS, "q": "browser"}})
    await page.click("#importNo")
    prefs.mode = "down"
    await page.fill("#q", "mine")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    await account_action(page, "#signOut")
    await page.wait_for_selector("#signIn")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "1 change made while you were signed in could not be saved to your account. "
        "They stay on this device and are saved the next time you sign in."
    )
    assert await page.input_value("#q") == "browser"
    prefs.mode = "ok"
    await start_sign_in(page, auth)
    await come_back(page)
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["q"] == "mine"


async def test_another_account_does_not_get_the_first_ones_unsent_filters(page: Page, site: Path) -> None:
    prefs = PrefsBackend({})
    await sign_in(page, site, ChoicesBackend(), prefs)
    await page.evaluate(
        "localStorage.setItem('misra-pending-filters-v1', JSON.stringify({'other-user': {q: 'theirs'}}))"
    )
    await page.reload()
    await settled(page)
    assert await page.input_value("#q") == ""  # the pending filters are kept by user id


# ---- Signed out: exactly as before ----


async def test_fr2_ac7_signed_out_the_filters_stay_in_this_browser_and_nothing_is_sent(page: Page, site: Path) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "account"})
    choices = ChoicesBackend()
    await serve(page, site, choices=choices, prefs=prefs)
    await page.fill("#q", "Manager")
    await page.click(".chip[data-group=levels][data-value=Junior]")
    assert (await stored_json(page, FILTERS))["q"] == "Manager"
    await page.reload()
    await page.wait_for_selector("#list article, #list .empty")
    assert await page.input_value("#q") == "Manager"
    assert prefs.requests == [] == choices.requests  # a visitor who is not signed in never talks to the tables
    assert await page.locator(BOX).is_hidden()
    assert await page.locator("#list").get_attribute("data-filters") == "browser"
    assert await page.locator("#importResult").inner_text() == ""


# ---- Agents ----


async def test_assistants_can_see_the_sync_state_and_answer_the_offer(page: Page, site: Path) -> None:
    choices = mixed_account()
    prefs = PrefsBackend()
    local = {**mixed_local(), "filters": {**DEFAULTS, "q": "Manager"}}
    await sign_in(page, site, choices, prefs, tools=True, local=local)
    status = await ask(page, "get_sync_status")
    assert status["signedIn"] is True
    assert status["filters"] == "paused"
    assert status["offer"] == {"discards": 1, "stars": 1, "filters": "new"}
    assert status["pendingChanges"] == 0
    answer = await ask(page, "import_browser_choices", {"filters": True})
    assert answer["saved"] is True
    assert "Brought over 2 choices" in answer["result"]
    await until(lambda: prefs.writes)
    status = await ask(page, "get_sync_status")
    assert status["offer"] is None
    assert status["filtersInUse"]["q"] == "Manager"
    assert (await ask(page, "import_browser_choices"))["error"].startswith("There is nothing to bring over")


async def test_signed_out_an_assistant_is_told_there_is_no_account(page: Page, site: Path) -> None:
    await page.add_init_script(WEBMCP)
    await serve(page, site)
    status = await ask(page, "get_sync_status")
    assert (status["signedIn"], status["choices"], status["filters"], status["offer"]) == (
        False,
        "browser",
        "browser",
        None,
    )
    assert (await ask(page, "import_browser_choices"))["error"].startswith("There is nothing to bring over")


# ---- The offer is usable ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_offer_has_no_axe_violations_and_big_enough_targets(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(PHONE)
    prefs = PrefsBackend({**DEFAULTS, "q": "Designer"})
    local = {**mixed_local(), "filters": {**DEFAULTS, "q": "Manager"}}
    await sign_in(page, site, mixed_account(), prefs, local=local)
    await expect(page.locator(BOX)).to_be_visible()
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    await expect(page.locator("#importNo")).to_have_text("Not now")
    for selector in ("#importYes", "#importNo", f"{BOX} label"):
        for target in await page.locator(selector).all():
            box = await target.bounding_box()
            assert box is not None
            assert box["height"] >= TAP_MIN
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


async def test_the_page_still_lists_the_roles_after_the_offer(page: Page, site: Path) -> None:
    await sign_in(page, site, mixed_account(), local=mixed_local())
    await page.click("#importYes")
    await until(lambda: page.locator("#importResult .notice").count())
    # 2 discards the account already had and 1 brought over
    shown = ISRAEL_JOBS - 3
    assert (
        await page.locator("#summary").inner_text()
        == f"Showing {shown} of {ISRAEL_JOBS} open roles in Israel. 3 discarded."
    )


async def test_bringing_filters_over_keeps_a_change_made_while_the_offer_waited(page: Page, site: Path) -> None:
    prefs = PrefsBackend({**DEFAULTS, "q": "Designer"})
    await sign_in(page, site, ChoicesBackend(), prefs, local={"filters": {**DEFAULTS, "q": "Manager"}})
    await page.fill("#q", "Analyst")
    await page.click("#importYes")
    await until(lambda: prefs.writes)
    assert saved_filters(prefs)["q"] == "Analyst"
    assert await page.input_value("#q") == "Analyst"
