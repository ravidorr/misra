"""Stars and discards that follow the user (PLAN.md step 4.2; PRD FR-2 AC 1 to 3 and 7 to 10), run in a real browser
against the built page with a fake sign-in service and a fake `user_jobs` table. The first-sign-in import and the
filters are step 4.3 (tests/test_account_sync.py). The database rules (the row limit, a discard going when its job
closes) are in supabase/tests/job_choices.sql and are linted in tests/test_migrations.py.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import (
    AXE,
    CALL,
    ISRAEL_JOBS,
    PAGE_SIZE,
    PHONE,
    TAP_MIN,
    USER_ID,
    WEBMCP,
    AuthBackend,
    ChoicesBackend,
    account_action,
    serve,
    site,  # noqa: F401  (a fixture)
)
from tests.test_sign_in import AXE_RUN, come_back, start_sign_in, stored

STARRED = "il-jobs-starred-v1"
DISCARDED = "il-jobs-discarded-v1"
PENDING = "misra-pending-choices-v1"
SECONDS = 0.05
TRIES = 100
PM1 = "article[data-job-id='Acme|1']"
PM2 = "article[data-job-id='Acme|2']"


async def choose_not_for_me(page: Page, card: str, act: str) -> None:
    """Signed out or in, "Not for me" is a menu: open it, then choose `toggle` (position) or `company`."""
    await page.click(f"{card} [data-act=menu]")
    await page.click(f"{card} [data-act={act}]")


def star_row(job_id: str, title: str = "Gone Role", location: str = "Haifa", company: str = "Gone") -> dict[str, Any]:
    """A star the account holds for a job that is not in the feed (it has closed)."""
    return {
        "user_id": USER_ID,
        "company": company,
        "job_id": job_id,
        "state": "star",
        "title": title,
        "location": location,
        "url": f"https://gone.test/{job_id}",
    }


def discard_row(company: str, job_id: str) -> dict[str, Any]:
    return {
        "user_id": USER_ID,
        "company": company,
        "job_id": job_id,
        "state": "discard",
        "title": None,
        "location": None,
        "url": None,
    }


def company_row(company: str) -> dict[str, Any]:
    """ "Company not for me" as the account keeps it: the company's name, an empty job id, nothing else."""
    return {**discard_row(company, ""), "state": "company"}


async def until(condition: Callable[[], bool | Awaitable[bool]]) -> None:
    """Wait for something that happens in the background (a request reaching the fake service)."""
    for _ in range(TRIES):
        result = condition()
        if (await result) if isinstance(result, Awaitable) else result:
            return
        await asyncio.sleep(SECONDS)
    pytest.fail("the page did not get there in time")


async def sign_in(page: Page, site: Path, choices: ChoicesBackend, *, tools: bool = False) -> AuthBackend:
    """The whole sign-in, as in test_sign_in, with this account's rows behind the page."""
    if tools:
        await page.add_init_script(WEBMCP)
    auth = AuthBackend()
    await serve(page, site, auth=auth, choices=choices)
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#accountUser:not([hidden])")
    await page.wait_for_selector(
        "#list[data-choices=loaded], #list[data-choices=failed]"
    )  # the account's list is applied
    return auth


async def starred_count(page: Page) -> str:
    return await page.locator("#starredView").inner_text()


async def is_saved(page: Page) -> bool:
    """Nothing of this user's waits in the browser any more: the service has confirmed every change."""
    return await pending(page) == {}


async def only_other_tab_left(page: Page) -> bool:
    return list(await pending(page)) == ["Acme|2"]


async def pending(page: Page) -> dict[str, Any]:
    raw = (await stored(page)).get(PENDING)
    return json.loads(raw).get(USER_ID, {}) if raw else {}


async def summary(page: Page) -> str:
    return await page.locator("#summary").inner_text()


async def note(page: Page) -> str:
    return await page.locator("#syncNote").inner_text()


async def ask(page: Page, name: str, args: dict[str, Any] | None = None) -> Any:
    return await page.evaluate(CALL, [name, args or {}])


# ---- Signed out: stars and discards stay in this browser, exactly as discards always did (FR-2 AC 7) ----


async def test_fr2_ac1_a_job_can_be_starred_and_the_star_removed_again(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await serve(page, site, choices=choices)
    star = page.locator(f"{PM1} [data-act=star]")
    assert await star.get_attribute("aria-pressed") == "false"
    assert await star.get_attribute("aria-label") == "Star: Product Manager 1 at Acme"
    assert await star.text_content() == "☆Star"
    await star.click()
    star = page.locator(f"{PM1} [data-act=star]")  # the card is drawn again
    assert await star.get_attribute("aria-pressed") == "true"
    assert await star.text_content() == "★Star"  # the state is also in the shape of the star, never colour alone
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert await page.evaluate("document.activeElement.dataset.act") == "star"  # focus stays on the button
    assert await starred_count(page) == "Starred (1)"
    await star.click()
    assert await page.locator(f"{PM1} [data-act=star]").get_attribute("aria-pressed") == "false"
    assert await page.locator(PM1).get_attribute("data-starred") == "false"
    assert await starred_count(page) == "Starred (0)"


async def test_fr2_ac1_the_starred_jobs_can_be_viewed_as_a_list(page: Page, site: Path) -> None:
    await serve(page, site)
    for card in (PM1, PM2):
        await page.click(f"{card} [data-act=star]")
    switch = page.locator("#starredView")
    assert await switch.get_attribute("aria-pressed") == "false"
    await switch.click()
    assert await switch.get_attribute("aria-pressed") == "true"
    assert await page.locator("#list article").count() == len(("Acme|1", "Acme|2"))
    assert await summary(page) == "2 starred roles. The filters do not apply to this list."
    for part in ("#moreFilters", "#searchBar", "#hiring"):
        assert await page.locator(part).is_hidden()
    await page.click(f"{PM1} [data-act=star]")  # take the star off from the list: the card goes
    assert await page.locator("#list article").count() == 1
    assert await summary(page) == "1 starred role. The filters do not apply to this list."
    await page.click(f"{PM2} [data-act=star]")
    assert (
        await page.locator("#list > li.empty").inner_text()
        == "No starred roles yet. Use Star on a role to keep it here."
    )
    await switch.click()  # back to the roles, with their filters
    assert await switch.get_attribute("aria-pressed") == "false"
    assert await page.locator("#moreFilters").is_visible()
    assert await summary(page) == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."


async def test_fr2_ac7_signed_out_a_star_stays_in_this_browser_and_nothing_is_sent(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await serve(page, site, choices=choices)
    await page.click(f"{PM1} [data-act=star]")
    saved = json.loads((await stored(page))[STARRED])
    assert saved == {
        "Acme|1": {"c": "Acme", "i": "1", "t": "Product Manager 1", "l": "Tel Aviv, Israel", "u": "https://x.test/j"}
    }
    await page.reload()
    await page.wait_for_selector("#list article")
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert await starred_count(page) == "Starred (1)"
    assert choices.requests == []  # a visitor who is not signed in never talks to the table
    assert await page.locator("#syncNote").inner_text() == ""


async def test_fr2_ac2_a_job_cannot_be_starred_and_discarded_at_the_same_time(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.click(f"{PM1} [data-act=star]")
    await choose_not_for_me(page, PM1, "toggle")  # Position not for me: the card goes and the star with it
    assert await page.locator(PM1).count() == 0
    assert await starred_count(page) == "Starred (0)"
    assert json.loads((await stored(page))[STARRED]) == {}
    assert list(json.loads((await stored(page))[DISCARDED])) == ["Acme|1"]
    await page.check("#showGone")  # the discarded job shows again, and starring it takes the discard away
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await page.locator(f"{PM1} [data-act=toggle]").inner_text() == "Restore"
    await page.click(f"{PM1} [data-act=star]")
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert json.loads((await stored(page))[DISCARDED]) == {}
    assert list(json.loads((await stored(page))[STARRED])) == ["Acme|1"]
    assert await summary(page) == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."


async def test_fr2_ac2_a_discarded_job_stays_hidden_until_it_is_restored(page: Page, site: Path) -> None:
    await serve(page, site)
    await choose_not_for_me(page, PM1, "toggle")
    assert await summary(page) == f"Showing {ISRAEL_JOBS - 1} of {ISRAEL_JOBS} open roles in Israel. 1 discarded."
    await page.reload()
    await page.wait_for_selector("#list article")
    assert await page.locator(PM1).count() == 0  # still hidden after a reload
    await page.check("#showGone")
    await page.click(f"{PM1} [data-act=toggle]")  # Restore
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"
    assert json.loads((await stored(page))[DISCARDED]) == {}


async def test_the_filters_card_has_no_discards_file_controls(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#exp, #imp, #file, input[type=file]").count() == 0  # accounts sync discards (step 4.3)


async def test_the_page_reads_a_damaged_star_store_as_empty(page: Page, site: Path) -> None:
    await page.add_init_script(
        f"localStorage.setItem('{STARRED}', JSON.stringify({{'Acme|1': 5, 'Acme|2': {{c: 'Acme', i: '2'}}}}))"
    )
    await serve(page, site)
    assert await starred_count(page) == "Starred (1)"  # the entry that is not a star is dropped; the bare one is kept
    await page.click("#starredView")
    await expect(page.locator(f"{PM2} .card-title")).to_contain_text("Product Manager 2")


async def test_blocked_storage_does_not_stop_a_star(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.evaluate("Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } })")
    await page.click(f"{PM1} [data-act=star]")  # kept for this page view only, without an error
    assert await page.locator(PM1).get_attribute("data-starred") == "true"


# ---- WebMCP: an assistant can star, unstar, discard and list too ----


async def test_assistants_can_star_unstar_discard_restore_and_list_starred_jobs(page: Page, site: Path) -> None:
    await page.add_init_script(WEBMCP)
    await serve(page, site)
    first = await ask(page, "star_job", {"id": "Acme|1"})
    assert first["starred"] is True
    assert first["discarded"] is False
    assert first["saved"] is True
    assert await page.locator(PM1).get_attribute("data-starred") == "true"  # the page follows
    listed = await ask(page, "list_starred_jobs")
    assert listed["total"] == 1
    assert listed["jobs"][0]["id"] == "Acme|1"
    assert listed["jobs"][0]["listed"] is True
    swapped = await ask(page, "discard_job", {"id": "Acme|1"})  # a discard replaces the star
    assert (swapped["starred"], swapped["discarded"]) == (False, True)
    assert (await ask(page, "list_starred_jobs"))["total"] == 0
    assert (await ask(page, "restore_job", {"id": "Acme|1"}))["discarded"] is False
    assert (await ask(page, "restore_job", {"id": "Acme|1"}))["saved"] is True  # nothing to restore: left as it is
    await ask(page, "star_job", {"id": "Acme|1"})
    assert (await ask(page, "restore_job", {"id": "Acme|1"}))["starred"] is True  # restore never takes a star away
    removed = await ask(page, "unstar_job", {"id": "Acme|1"})
    assert removed["starred"] is False
    assert (await ask(page, "unstar_job", {"id": "Acme|1"}))["starred"] is False
    assert (await ask(page, "get_job", {"id": "Acme|1"}))["starred"] is False
    for name in ("star_job", "unstar_job", "discard_job", "restore_job"):
        assert (await ask(page, name, {"id": "Nowhere|1"})) == {"error": "Unknown job id"}
        assert (await ask(page, name)) == {"error": "Unknown job id"}


# ---- Signed in: the choices are saved in the account and follow the user (FR-2 AC 3) ----


async def test_fr2_ac3_the_choices_of_the_account_appear_after_signing_in(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme"), discard_row("Acme", "2")])
    auth = await sign_in(page, site, choices)
    assert await starred_count(page) == "Starred (1)"
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert await page.locator(PM2).count() == 0  # discarded on the other device: hidden here
    assert await summary(page) == f"Showing {ISRAEL_JOBS - 1} of {ISRAEL_JOBS} open roles in Israel. 1 discarded."
    read = next(r for r in choices.requests if r.method == "GET")
    token = json.loads((await stored(page))["misra-auth"])["access_token"]
    assert read.headers["authorization"] == f"Bearer {token}"  # the user's own token, never the public key alone
    assert read.headers["authorization"] != f"Bearer {build_dashboard.SUPABASE_ANON_KEY}"
    assert read.headers["apikey"] == build_dashboard.SUPABASE_ANON_KEY
    assert "user_jobs" in read.url
    assert auth.requests  # the sign-in itself went through the fake service


async def test_fr2_ac3_a_reload_reads_the_choices_again(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    await page.click(f"{PM1} [data-act=star]")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await until(lambda: is_saved(page))
    await page.reload()
    await page.wait_for_selector("#accountUser:not([hidden])")
    await expect(page.locator(PM1)).to_have_attribute("data-starred", "true")
    assert await pending(page) == {}


async def test_a_star_is_saved_with_what_is_needed_to_show_it_after_the_job_closes(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    await page.click(f"{PM1} [data-act=star]")
    await until(lambda: choices.writes)
    assert choices.writes == [
        {
            "op": "upsert",
            "on_conflict": "user_id,company,job_id",
            "user_id": USER_ID,
            "company": "Acme",
            "job_id": "1",
            "state": "star",
            "title": "Product Manager 1",
            "location": "Tel Aviv, Israel",
            "url": "https://x.test/j",
        }
    ]
    write = choices.requests[-1]
    assert write.headers["authorization"] != f"Bearer {build_dashboard.SUPABASE_ANON_KEY}"
    await until(lambda: is_saved(page))  # saved, so nothing waits in this browser any more
    assert await note(page) == ""


async def test_a_discard_is_saved_without_the_title_location_or_link(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    await choose_not_for_me(page, PM1, "toggle")
    await until(lambda: choices.writes)
    assert choices.writes[0]["state"] == "discard"
    assert (choices.writes[0]["title"], choices.writes[0]["location"], choices.writes[0]["url"]) == (None, None, None)


async def test_fr2_ac2_starring_a_discarded_job_changes_its_one_row(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    await choose_not_for_me(page, PM1, "toggle")
    await until(lambda: choices.state_of("Acme", "1") == "discard")
    await page.check("#showGone")
    await page.click(f"{PM1} [data-act=star]")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    assert len(choices.rows) == 1  # one row per job: it cannot be both
    assert [w["state"] for w in choices.writes] == ["discard", "star"]
    await choose_not_for_me(page, PM1, "toggle")
    await until(lambda: choices.state_of("Acme", "1") == "discard")
    assert await starred_count(page) == "Starred (0)"


async def test_taking_a_choice_back_deletes_its_row(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme"), discard_row("Acme", "2")])
    await sign_in(page, site, choices)
    await page.click(f"{PM1} [data-act=star]")  # unstar
    await until(lambda: ("Acme", "1") not in choices.rows)
    await page.check("#showGone")
    await page.click(f"{PM2} [data-act=toggle]")  # restore
    await until(lambda: not choices.rows)
    deletes = [w for w in choices.writes if w["op"] == "delete"]
    assert [(w["user_id"], w["company"], w["job_id"]) for w in deletes] == [
        (USER_ID, "Acme", "1"),
        (USER_ID, "Acme", "2"),
    ]


async def test_fr2_ac9_a_starred_job_that_has_closed_is_still_shown_marked_as_no_longer_listed(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend([star_row("7", "Data Analyst", "Haifa"), discard_row("Gone", "8")])
    await sign_in(page, site, choices, tools=True)
    assert await starred_count(page) == "Starred (1)"
    assert await summary(page) == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    assert await page.locator("article[data-company=Gone]").count() == 0  # not in the feed
    await page.click("#starredView")
    card = page.locator("article[data-job-id='Gone|7']")
    assert await card.get_attribute("data-listed") == "false"
    assert await card.get_attribute("data-starred") == "true"
    # sentence case in the markup (screen readers read words), uppercase on screen
    assert await card.locator(".sticker").text_content() == "No longer listed"
    assert await card.locator(".sticker").evaluate("e => getComputedStyle(e).textTransform") == "uppercase"
    assert await card.locator(".sticker").inner_text() == "NO LONGER LISTED"
    assert await card.locator(".card-company").inner_text() == "Gone"
    link = card.locator(".card-title a")
    assert await link.get_attribute("href") == "https://gone.test/7"
    assert await link.get_attribute("rel") == "noopener noreferrer"
    assert "Data Analyst" in await link.inner_text()
    assert await card.locator(".pill").inner_text() == "Haifa"
    assert await card.locator("a.btn-primary").count() == 0  # there is nothing to apply to
    assert await summary(page) == "1 starred role, 1 no longer listed. The filters do not apply to this list."
    listed = await ask(page, "list_starred_jobs")
    assert listed["total"] == 1
    assert listed["jobs"] == [
        {
            "id": "Gone|7",
            "company": "Gone",
            "title": "Data Analyst",
            "location": "Haifa",
            "url": "https://gone.test/7",
            "listed": False,
            "starred": True,
        }
    ]


async def test_fr2_ac9_the_star_of_a_closed_job_can_be_taken_off(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("7", "Data Analyst", "Haifa")])
    await sign_in(page, site, choices)
    await page.click("#starredView")
    await page.click("article[data-job-id='Gone|7'] [data-act=star]")
    await until(lambda: not choices.rows)
    assert await starred_count(page) == "Starred (0)"
    assert await page.locator("#list > li.empty").count() == 1


async def test_a_star_with_no_title_or_link_is_still_listed(page: Page, site: Path) -> None:
    row = {**star_row("7"), "title": None, "location": None, "url": None}
    await sign_in(page, site, ChoicesBackend([row]))
    await page.click("#starredView")
    card = page.locator("article[data-job-id='Gone|7']")
    assert await card.locator(".card-title").inner_text() == "Untitled role"
    assert await card.locator(".card-title a").count() == 0
    assert await card.locator(".pill").count() == 0


async def test_fr2_ac10_a_discard_of_a_job_that_is_not_in_the_feed_is_not_counted_or_shown(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend([discard_row("Gone", str(n)) for n in range(5)])
    await sign_in(page, site, choices)
    assert await summary(page) == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    assert await page.locator("#showGone").is_disabled()
    assert await starred_count(page) == "Starred (0)"


async def test_every_row_of_the_account_is_loaded_across_the_page_limit(page: Page, site: Path) -> None:
    rows = [discard_row("Bulk", f"{n:05d}") for n in range(1000)] + [star_row(f"{n:05d}") for n in range(5)]
    choices = ChoicesBackend(rows)
    await sign_in(page, site, choices)
    reads = [r for r in choices.requests if r.method == "GET"]
    assert [parse_qs(urlparse(r.url).query)["offset"] for r in reads] == [["0"], ["1000"]]
    await expect(page.locator("#starredView")).to_have_text("Starred (5)")


async def test_the_page_does_not_send_what_belongs_to_another_account(page: Page, site: Path) -> None:
    other = {"Acme|1": {"c": "Acme", "i": "1", "t": "", "l": "", "u": None, "state": "star"}}
    await page.add_init_script(
        f"localStorage.setItem('{PENDING}', JSON.stringify({{'someone-else': {json.dumps(other)}}}))"
    )
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    await asyncio.sleep(0.3)
    assert choices.writes == []
    assert await starred_count(page) == "Starred (0)"
    await page.click(f"{PM2} [data-act=star]")
    await until(lambda: choices.writes)
    await until(lambda: is_saved(page))
    assert json.loads((await stored(page))[PENDING]) == {"someone-else": other}  # theirs stays, untouched


async def test_a_second_change_to_a_job_that_is_still_being_saved_wins(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.delay = 0.4
    await page.click(f"{PM1} [data-act=star]")
    await page.click(f"{PM1} [data-act=star]")  # the star is taken off before the first save has finished
    await choose_not_for_me(page, PM2, "toggle")
    await until(lambda: choices.state_of("Acme", "2") == "discard")
    await until(lambda: is_saved(page))
    assert [w["op"] for w in choices.writes] == ["upsert", "delete", "upsert"]  # in the order the user made them
    assert choices.state_of("Acme", "1") is None
    assert await note(page) == ""


async def test_the_list_that_arrives_late_does_not_undo_a_choice_confirmed_meanwhile(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    choices.gate = asyncio.Event()
    auth = AuthBackend()
    await serve(page, site, auth=auth, choices=choices)
    await start_sign_in(page, auth)
    await come_back(page)
    await page.wait_for_selector("#accountUser:not([hidden])")
    await page.click(f"{PM1} [data-act=star]")
    await until(lambda: choices.state_of("Acme", "1") == "star")  # confirmed while the read is still held
    async with page.expect_response(lambda r: r.request.method == "GET" and "user_jobs" in r.url):
        choices.gate.set()  # the read now answers with the table as it was when it was asked: without the star
    await asyncio.sleep(0.2)  # the page applies the answer
    assert await starred_count(page) == "Starred (1)"
    assert await page.locator(PM1).get_attribute("data-starred") == "true"


# ---- FR-2 AC 8: if the service cannot be reached, a star or a discard is not lost ----


async def test_fr2_ac8_a_star_made_while_the_service_is_down_is_kept_and_said_to_be_unsaved(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "1 change is not saved to your account yet. It is kept on this device and saved when the service answers."
    )
    assert await page.locator(PM1).get_attribute("data-starred") == "true"  # still starred, not undone
    assert await page.locator(PM1).get_attribute("data-pending") == "true"  # and marked as not saved yet
    assert await starred_count(page) == "Starred (1)"
    assert list(await pending(page)) == ["Acme|1"]
    assert await page.locator("#syncNote").get_attribute("role") == "status"
    box = await page.locator("#syncRetry").bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN


async def test_fr2_ac8_the_kept_choice_survives_a_reload_and_is_saved_when_the_service_is_back(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "error"
    await page.click(f"{PM1} [data-act=star]")
    await choose_not_for_me(page, PM2, "toggle")
    await expect(page.locator("#syncNote .notice")).to_contain_text("2 changes are not saved")
    await page.reload()  # the tab is closed and opened again while the service is still down
    await page.wait_for_selector("#accountUser:not([hidden])")
    await expect(page.locator(PM1)).to_have_attribute("data-starred", "true")
    assert await page.locator(PM2).count() == 0  # still discarded
    assert sorted(await pending(page)) == ["Acme|1", "Acme|2"]
    choices.mode = "ok"
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "1") == "star" and choices.state_of("Acme", "2") == "discard")
    await expect(page.locator("#syncNote")).to_have_text("")
    assert await pending(page) == {}
    assert await page.locator(PM1).get_attribute("data-pending") == "false"


async def test_a_change_waiting_to_be_saved_is_tried_again_by_itself(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    choices.mode = "ok"
    await expect(page.locator("#syncNote")).to_have_text("", timeout=8000)  # the timer, after a few seconds
    assert choices.state_of("Acme", "1") == "star"


async def test_the_browser_coming_back_online_saves_the_waiting_changes(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    choices.mode = "ok"
    await page.evaluate("window.dispatchEvent(new Event('online'))")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await expect(page.locator("#syncNote")).to_have_text("")


async def test_an_expired_session_keeps_the_change_for_later(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "expired"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_contain_text("not saved to your account yet")
    assert list(await pending(page)) == ["Acme|1"]
    assert await page.locator(PM1).get_attribute("data-starred") == "true"


async def test_fr2_ac8_an_assistant_is_told_when_a_change_is_not_saved_yet(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices, tools=True)
    choices.mode = "down"
    answer = await ask(page, "star_job", {"id": "Acme|1"})
    assert answer["starred"] is True
    assert answer["saved"] is False  # kept on this device, not in the account
    assert answer["failure"] == "later"
    choices.mode = "ok"
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "1") == "star")


async def test_a_choice_the_row_limit_refuses_is_undone_and_said_so(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "limit"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_contain_text("reached the limit")
    assert await page.locator(PM1).get_attribute("data-starred") == "false"  # not shown as saved: it was not
    assert await starred_count(page) == "Starred (0)"
    assert await pending(page) == {}  # and it is not retried for ever
    assert await page.locator("#syncRetry").count() == 0
    choices.mode = "ok"
    await choose_not_for_me(page, PM1, "toggle")  # a later choice works, and clears the message
    await until(lambda: choices.state_of("Acme", "1") == "discard")
    await expect(page.locator("#syncNote")).to_have_text("")


async def test_a_choice_the_service_refuses_for_another_reason_is_undone_and_said_so(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "refused"
    await choose_not_for_me(page, PM1, "toggle")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "The service did not accept the last star or discard, so it was not saved."
    )
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"
    assert await pending(page) == {}


async def test_a_list_of_choices_that_cannot_be_loaded_says_so_and_can_be_tried_again(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme")], mode="load_down")
    await sign_in(page, site, choices)
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "Your saved stars and discards could not be loaded, so only the changes made on this device show."
    )
    assert await starred_count(page) == "Starred (0)"
    assert await page.locator("#list article").count() == PAGE_SIZE  # the roles themselves are all there
    choices.mode = "ok"
    await page.click("#syncRetry")
    await expect(page.locator("#starredView")).to_have_text("Starred (1)")
    await expect(page.locator("#syncNote")).to_have_text("")


async def test_a_list_with_a_missing_total_is_an_error_not_an_empty_account(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1")])

    async def no_total(route: Any) -> None:
        if route.request.method == "GET":
            headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
            await route.fulfill(body="[]", content_type="application/json", headers=headers)
        else:
            await choices.handle(route)

    auth = AuthBackend()
    await serve(page, site, auth=auth, choices=choices)
    await page.route(f"{build_dashboard.SUPABASE_URL}/rest/v1/user_jobs**", no_total)
    await start_sign_in(page, auth)
    await come_back(page)
    await expect(page.locator("#syncNote .notice")).to_contain_text("could not be loaded")


# ---- Signing out, and signing in on another account ----


async def test_signing_out_returns_to_the_choices_of_this_browser(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme")])
    await sign_in(page, site, choices)
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    await account_action(page, "#signOut")
    await page.wait_for_selector("#signIn")
    assert await page.locator(PM1).get_attribute("data-starred") == "false"  # the account's stars do not stay on screen
    assert await starred_count(page) == "Starred (0)"
    assert await page.locator("#syncNote").inner_text() == ""
    assert (await stored(page)).get(STARRED) is None  # nothing of the account was copied to this browser


async def test_changes_not_saved_before_signing_out_stay_and_are_saved_at_the_next_sign_in(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    auth = await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    await account_action(page, "#signOut")
    await page.wait_for_selector("#signIn")
    await expect(page.locator("#syncNote .notice")).to_have_text(
        "1 change made while you were signed in could not be saved to your account. "
        "They stay on this device and are saved the next time you sign in."
    )
    assert list(await pending(page)) == ["Acme|1"]
    assert choices.rows == {}
    choices.mode = "ok"
    await start_sign_in(page, auth)
    await come_back(page)
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await expect(page.locator(PM1)).to_have_attribute("data-starred", "true")


async def test_a_new_token_for_the_same_account_changes_nothing(page: Page, site: Path) -> None:
    choices = ChoicesBackend([star_row("1", "Product Manager 1", "Tel Aviv, Israel", "Acme")])
    await sign_in(page, site, choices)
    reads = len(choices.requests)
    await page.evaluate("document.dispatchEvent(new CustomEvent('misra:account'))")  # what a token renewal does
    await asyncio.sleep(0.3)
    assert len(choices.requests) == reads  # not read again
    assert await page.locator(PM1).get_attribute("data-starred") == "true"


async def test_the_sign_in_script_not_loading_leaves_the_choices_in_the_browser(page: Page, site: Path) -> None:
    (site / "supabase.js").unlink()
    choices = ChoicesBackend()
    await serve(page, site, choices=choices)
    await page.click(f"{PM1} [data-act=star]")
    assert await page.locator(PM1).get_attribute("data-starred") == "true"
    assert choices.requests == []
    assert list(json.loads((await stored(page))[STARRED])) == ["Acme|1"]


# ---- The page, with stars: accessible, light and dark, readable on a phone ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["signed_out", "starred_list", "unsaved"])
async def test_the_page_with_stars_has_no_axe_violations(page: Page, site: Path, scheme: str, state: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    choices = ChoicesBackend([star_row("7", "Data Analyst", "Haifa")])
    if state == "signed_out":
        await serve(page, site)
        await page.click(f"{PM1} [data-act=star]")
    else:
        await sign_in(page, site, choices)
        await page.click(f"{PM1} [data-act=star]")
        if state == "starred_list":
            await page.click("#starredView")
        else:
            choices.mode = "down"
            await page.click(f"{PM2} [data-act=star]")
            await expect(page.locator("#syncNote .notice")).to_be_visible()
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []


@pytest.mark.parametrize("starred_list", [False, True])
async def test_the_cards_and_their_buttons_fit_a_phone(page: Page, site: Path, starred_list: bool) -> None:
    await page.set_viewport_size(PHONE)
    await sign_in(page, site, ChoicesBackend([star_row("7", "Data Analyst With A Rather Long Title", "Haifa")]))
    if starred_list:
        await page.click("#starredView")
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("#list .btn, #starredView").and_(page.locator(":visible")).all():
        box = await target.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
        assert 0 <= box["x"] <= box["x"] + box["width"] <= PHONE["width"]


async def test_an_assistant_is_told_when_the_row_limit_refused_a_choice(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices, tools=True)
    choices.mode = "limit"
    answer = await ask(page, "star_job", {"id": "Acme|1"})
    assert (answer["starred"], answer["saved"], answer["failure"]) == (False, False, "limit")
    choices.mode = "refused"
    answer = await ask(page, "discard_job", {"id": "Acme|1"})
    assert (answer["discarded"], answer["saved"], answer["failure"]) == (False, False, "refused")
    choices.mode = "ok"
    answer = await ask(page, "star_job", {"id": "Acme|1"})
    assert (answer["starred"], answer["saved"], answer["failure"]) == (True, True, None)


async def test_a_refusal_notice_stays_when_a_later_change_in_the_same_batch_is_saved(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await choose_not_for_me(page, PM2, "toggle")
    await expect(page.locator("#syncNote .notice")).to_contain_text("2 changes are not saved")
    choices.mode = "limit_star"  # the star is refused, the discard after it is accepted
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "2") == "discard")
    await expect(page.locator("#syncNote .notice")).to_contain_text("reached the limit")
    assert await page.locator(PM1).get_attribute("data-starred") == "false"


async def test_changes_of_another_tab_of_the_account_are_not_erased_from_the_browser(page: Page, site: Path) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    other = {"c": "Acme", "i": "2", "t": "", "l": "", "u": None, "state": "star"}
    await page.evaluate(  # what a second tab of the same account saved meanwhile
        "([key, user, entry]) => { const all = JSON.parse(localStorage.getItem(key)); all[user]['Acme|2'] = entry;"
        " localStorage.setItem(key, JSON.stringify(all)); }",
        [PENDING, USER_ID, other],
    )
    choices.mode = "ok"
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await until(lambda: only_other_tab_left(page))
    assert list(await pending(page)) == ["Acme|2"]  # this tab's change is forgotten, the other tab's is kept


async def test_a_newer_change_of_another_tab_to_the_same_job_is_not_erased_by_this_tabs_confirmation(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    choices.mode = "down"
    await page.click(f"{PM1} [data-act=star]")
    await expect(page.locator("#syncNote .notice")).to_be_visible()
    newer = {"c": "Acme", "i": "1", "t": "", "l": "", "u": None, "state": "discard", "id": "another-tab"}
    await page.evaluate(
        "([key, user, entry]) => { const all = JSON.parse(localStorage.getItem(key)); all[user]['Acme|1'] = entry;"
        " localStorage.setItem(key, JSON.stringify(all)); }",
        [PENDING, USER_ID, newer],
    )
    choices.mode = "ok"
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await expect(page.locator("#syncNote")).to_have_text("")
    assert (await pending(page))["Acme|1"] == newer  # this tab's star was confirmed, the other tab's newer choice stays


COMPANY_KEY = "il-jobs-discarded-companies-v1"


async def test_company_not_for_me_hides_every_role_of_the_company_and_restore_brings_them_back(
    page: Page, site: Path
) -> None:
    await serve(page, site)
    await page.click(f"{PM1} [data-act=star]")
    await page.click(f"{PM2} [data-act=star]")
    await choose_not_for_me(page, PM1, "company")
    assert await page.locator("article[data-company=Acme]").count() == 0  # every role of Acme, the starred ones too
    assert await starred_count(page) == "Starred (0)"
    assert list(json.loads((await stored(page))[COMPANY_KEY])) == ["Acme"]
    assert json.loads((await stored(page))[DISCARDED]) == {}  # no per-role discard was written
    assert (
        await summary(page) == f"Showing 0 of {ISRAEL_JOBS} open roles in Israel. {ISRAEL_JOBS} discarded."
    )  # every fixture role is Acme's
    await page.reload()
    await page.wait_for_selector("#list .empty")  # every role is hidden, still, after a reload
    await page.check("#showGone")
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await page.locator(PM2).get_attribute("data-discarded") == "true"
    assert await page.locator(f"{PM1} [data-act=toggle]").inner_text() == "Restore"
    assert await page.locator(f"{PM1} [data-act=menu]").count() == 0
    await page.click(f"{PM1} [data-act=toggle]")  # Restore: the company comes back, not one role of it
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"
    assert await page.locator(PM2).get_attribute("data-discarded") == "false"
    assert json.loads((await stored(page))[COMPANY_KEY]) == {}


async def test_position_not_for_me_hides_one_role_and_restore_brings_it_back(page: Page, site: Path) -> None:
    await serve(page, site)
    await choose_not_for_me(page, PM1, "toggle")
    assert await page.locator(PM1).count() == 0
    assert await page.locator(PM2).count() == 1  # the company's other role stays
    assert json.loads((await stored(page))[COMPANY_KEY]) == {}
    await page.check("#showGone")
    await page.click(f"{PM1} [data-act=toggle]")
    assert json.loads((await stored(page))[DISCARDED]) == {}


async def test_the_not_for_me_menu_is_a_menu_button_that_the_keyboard_and_screen_readers_can_use(
    page: Page, site: Path
) -> None:
    await serve(page, site)
    button = page.locator(f"{PM1} [data-act=menu]")
    panel = page.locator(f"{PM1} .card-menu-panel")
    assert await button.get_attribute("aria-haspopup") == "true"
    assert await button.get_attribute("aria-expanded") == "false"
    assert await panel.is_hidden()
    await button.focus()
    await page.keyboard.press("Enter")
    assert await button.get_attribute("aria-expanded") == "true"
    assert await panel.get_attribute("role") == "menu"
    first, second = page.locator(f"{PM1} [role=menuitem]").nth(0), page.locator(f"{PM1} [role=menuitem]").nth(1)
    assert await first.inner_text() == "Position not for me"
    assert await second.inner_text() == "Company not for me"
    assert await first.evaluate("(el) => el === document.activeElement")  # the focus moves into the menu
    for item in (first, second):
        box = await item.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
    await page.keyboard.press("ArrowDown")
    assert await second.evaluate("(el) => el === document.activeElement")
    await page.keyboard.press("ArrowDown")  # wraps
    assert await first.evaluate("(el) => el === document.activeElement")
    await page.keyboard.press("ArrowUp")
    assert await second.evaluate("(el) => el === document.activeElement")
    await page.keyboard.press("Escape")
    assert await button.get_attribute("aria-expanded") == "false"
    assert await panel.is_hidden()
    assert await button.evaluate("(el) => el === document.activeElement")  # the focus returns to the button
    await button.press("Enter")
    await page.keyboard.press("Tab")  # one tab stop: Tab leaves the menu and it closes
    assert await panel.is_hidden()
    await button.click()
    await page.click("#q")  # a click elsewhere closes it too
    assert await panel.is_hidden()
    await button.click()
    await button.click()  # the button closes it again
    assert await panel.is_hidden()
    await button.click()
    await page.locator(f"{PM2} [data-act=menu]").click()  # opening another closes the first
    assert await panel.is_hidden()


async def test_the_menu_stays_inside_the_screen_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await serve(page, site)
    width = PHONE["width"]
    for card in (PM1, PM2):
        await page.click(f"{card} [data-act=menu]")
        box = await page.locator(f"{card} .card-menu-panel").bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width


async def test_the_focus_lands_on_a_visible_control_when_the_last_card_is_removed_through_the_menu(
    page: Page, site: Path
) -> None:
    await serve(page, site)
    await page.click("#next")  # the last page holds two roles
    card = page.locator("#list li").last.locator("article")
    job = await card.get_attribute("data-job-id")
    assert job is not None
    await choose_not_for_me(page, f"article[data-job-id='{job}']", "toggle")
    assert await page.evaluate(
        "document.activeElement.matches('#list [data-act]') && document.activeElement.offsetParent !== null"
    )


async def test_signed_in_not_for_me_is_the_same_menu_and_a_company_choice_is_one_row_of_the_account(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    await sign_in(page, site, choices)
    assert await page.locator(f"{PM1} .card-menu").count() == 1
    await page.click(f"{PM1} [data-act=star]")
    await until(lambda: choices.state_of("Acme", "1") == "star")
    await choose_not_for_me(page, PM2, "company")
    await until(
        lambda: choices.state_of("Acme", "") == "company" and choices.state_of("Acme", "1") is None
    )  # a star of the company goes, as it does signed out
    assert choices.rows[("Acme", "")] == company_row("Acme")  # the name only: no title, location or link
    assert await page.locator("article[data-company=Acme]").count() == 0
    assert await starred_count(page) == "Starred (0)"
    assert await pending(page) == {}
    assert COMPANY_KEY not in await stored(page)  # the browser's own signed-out choices are not touched
    await page.reload()
    await page.wait_for_selector("#list[data-choices=loaded], #list[data-choices=failed]")
    assert await page.locator("article[data-company=Acme]").count() == 0  # it syncs: read back from the account
    await page.check("#showGone")
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await page.locator(f"{PM1} [data-act=toggle]").inner_text() == "Restore"
    await page.click(f"{PM1} [data-act=toggle]")  # Restore: the company, not one role of it
    await until(lambda: choices.rows == {})
    assert await page.locator(PM2).get_attribute("data-discarded") == "false"
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"


async def test_a_company_choice_of_the_account_hides_the_company_on_another_device(page: Page, site: Path) -> None:
    choices = ChoicesBackend([company_row("Acme")])
    await sign_in(page, site, choices)
    assert await page.locator("article[data-company=Acme]").count() == 0
    assert await summary(page) == f"Showing 0 of {ISRAEL_JOBS} open roles in Israel. {ISRAEL_JOBS} discarded."


async def test_restoring_a_role_of_a_hidden_company_also_removes_its_own_discard(page: Page, site: Path) -> None:
    choices = ChoicesBackend([company_row("Acme"), discard_row("Acme", "1")])
    await sign_in(page, site, choices)
    await page.check("#showGone")
    await page.click(f"{PM1} [data-act=toggle]")
    await until(lambda: choices.rows == {})
    assert [w["op"] for w in choices.writes] == ["delete", "delete"]


async def test_a_position_choice_next_to_a_company_choice_stays_when_the_company_is_restored_from_another_role(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend([company_row("Acme"), discard_row("Acme", "1")])
    await sign_in(page, site, choices)
    await page.check("#showGone")
    await page.click(f"{PM2} [data-act=toggle]")
    await until(lambda: ("Acme", "") not in choices.rows)
    assert choices.state_of("Acme", "1") == "discard"  # only the company came back, as signed out


@pytest.mark.parametrize("mode", ["limit", "refused"])
async def test_a_company_choice_the_database_refuses_is_undone_and_said_so(page: Page, site: Path, mode: str) -> None:
    choices = ChoicesBackend(mode=mode)
    await sign_in(page, site, choices)
    await choose_not_for_me(page, PM1, "company")
    await expect(page.locator("#syncNote .notice")).to_contain_text(
        "reached the limit" if mode == "limit" else "did not accept"
    )
    assert choices.rows == {}
    assert await page.locator(PM1).count() == 1  # the company is back in the feed
    assert await pending(page) == {}


async def test_a_company_choice_made_while_the_service_is_down_is_kept_and_saved_later(page: Page, site: Path) -> None:
    choices = ChoicesBackend(mode="down")
    await sign_in(page, site, choices)
    await choose_not_for_me(page, PM1, "company")
    assert await page.locator("article[data-company=Acme]").count() == 0  # shown at once
    assert list(await pending(page)) == ["Acme|"]
    choices.mode = "ok"
    await expect(page.locator("#syncRetry")).to_be_visible()
    await page.click("#syncRetry")
    await until(lambda: choices.state_of("Acme", "") == "company")
    await expect(page.locator("#syncRetry")).to_have_count(0)
    assert await pending(page) == {}
