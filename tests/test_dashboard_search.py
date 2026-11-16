"""The keyword search over the full text of a posting (PLAN.md step 7.3, FR-4 AC 6), in a real browser against a fake
backend. The page never holds the text: it asks the database function `search_job_keys` for the keys of the roles whose
text has the words, and shows a role when its title, company, team or location has them all or its key is among the
keys. The words go in the body of a POST, never in the address; the page keeps the title match when the search fails."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import DEBOUNCE_MS, Backend, build_site, job, serve


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built with the daily-email signup off, like the `site` fixture of tests/test_dashboard_page.py."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


ROWS = [
    job("title", "Rust Engineer", "Haifa, Israel"),
    job("text", "Software Engineer", "Haifa, Israel"),  # "rust" only in its posting
    job("other", "Office Dog Trainer", "Haifa, Israel"),
]
SEARCH_WAIT = 4000  # ms: the page waits 400 ms after the last key, then asks


def backend(**kw: Any) -> Backend:
    found = Backend(ROWS)
    found.text_hits = {"rust": [["Acme", "text"], ["Acme", "closed-or-unknown"]]}
    for name, value in kw.items():
        setattr(found, name, value)
    return found


async def asked(page: Page, fake: Backend) -> None:
    """Wait until the page has sent a search."""
    for _ in range(100):
        if fake.searches:
            return
        await page.wait_for_timeout(50)
    raise AssertionError("the page never asked")


async def shown(page: Page) -> set[str]:
    return {
        (await card.get_attribute("data-job-id") or "").split("|")[1]
        for card in await page.locator("#list article").all()
    }


async def test_a_role_whose_text_has_the_words_is_shown_with_the_roles_whose_title_has_them(
    page: Page, site: Path
) -> None:
    fake = backend()
    await serve(page, site, backend=fake)
    await page.fill("#q", "  RUST ")
    assert await shown(page) == {"title"}  # at once: the title match, before the answer
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert await shown(page) == {"title", "text"}  # an unknown key in the answer shows nothing
    assert fake.searches == ["rust"]  # the words as typed, lower case, one space apart
    assert await page.locator("#summary").inner_text() == "Showing 2 of 3 open roles in Israel. 0 discarded."


async def test_the_search_is_asked_once_typing_pauses_and_not_again_for_the_same_words(page: Page, site: Path) -> None:
    fake = backend()
    await serve(page, site, backend=fake)
    await page.locator("#q").press_sequentially("rust", delay=60)
    assert fake.searches == []  # still typing
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert fake.searches == ["rust"]  # one request for four keys
    await page.fill("#q", "rus")
    await page.fill("#q", "rust")
    await page.wait_for_timeout(DEBOUNCE_MS)
    assert fake.searches == ["rust"]  # the answer is kept
    assert await shown(page) == {"title", "text"}


async def test_fewer_than_three_characters_are_not_sent_and_an_empty_box_shows_everything(
    page: Page, site: Path
) -> None:
    fake = backend()
    await serve(page, site, backend=fake)
    await page.fill("#q", "ru")
    await page.wait_for_timeout(DEBOUNCE_MS)
    assert fake.searches == []
    await page.fill("#q", "")
    assert await shown(page) == {"title", "text", "other"}


async def test_the_answer_for_an_old_query_does_not_change_the_list_of_a_new_one(page: Page, site: Path) -> None:
    fake = backend(search_seconds=1.0)
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await asked(page, fake)  # asked, not yet answered
    await page.fill("#q", "dog")
    await page.wait_for_timeout(2500)  # the answers for "rust" and "dog" have come
    assert await shown(page) == {"other"}  # "dog" is in a title; the keys of "rust" are not used for it
    await page.fill("#q", "rust")  # the answer was kept for its own query
    assert await shown(page) == {"title", "text"}


async def test_a_failing_search_leaves_the_title_match_and_asks_again_on_the_next_change(
    page: Page, site: Path
) -> None:
    fake = backend(search_mode="error")
    problems = await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await asked(page, fake)
    await page.wait_for_timeout(300)
    assert await shown(page) == {"title"}
    assert all(
        "503" in problem for problem in problems
    )  # the browser's note of the failed request, nothing from the page
    fake.search_mode = "ok"
    await page.fill("#q", "rust ")  # the same words: the failed answer was not kept
    await page.fill("#q", "rust")
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)


async def test_an_answer_that_is_not_a_list_changes_nothing(page: Page, site: Path) -> None:
    fake = backend(search_mode="not_a_list")
    assert await serve(page, site, backend=fake) == []
    await page.fill("#q", "rust")
    await asked(page, fake)
    await page.wait_for_timeout(300)
    assert await shown(page) == {"title"}


async def test_the_text_matches_combine_with_the_other_filters(page: Page, site: Path) -> None:
    fake = backend()
    fake.rows = [*ROWS, job("far", "Backend Engineer", "London, UK", regions=[], locations=[])]
    fake.text_hits = {"rust": [["Acme", "text"], ["Acme", "far"]]}
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert await shown(page) == {"title", "text"}  # "far" is abroad: the location filter still applies
    await page.select_option("#loc", "all")
    assert await shown(page) == {"title", "text", "far"}
