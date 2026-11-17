"""The keyword search over the full text of a posting (PLAN.md step 7.3, FR-4 AC 6), in a real browser against a fake
backend. The page never holds the text: it asks the database function `search_job_keys` for the keys of the roles whose
text has the words, and shows a role when its title, company, team or location has them all or its key is among the
keys. The words go in the body of a POST, never in the address; the page keeps the title match when the search fails."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Locator, Page, expect

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
CLIPPED_PX = 1  # the hidden heading is a 1 px box
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


async def shown_ids(page: Page) -> list[str]:
    return [await card.get_attribute("data-job-id") or "" for card in await page.locator("#list article").all()]


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


BASE = "Showing 1 of 3 open roles in Israel. 0 discarded."
FAILED = "Could not search job descriptions. Showing matches in titles, companies, teams and locations only."


async def said(page: Page, text: str) -> None:
    """The summary line and the live region both say `text` (the live region after the typing pause)."""
    await expect(page.locator("#summary")).to_have_text(text, timeout=SEARCH_WAIT)
    await expect(page.locator("#status")).to_have_text(text, timeout=SEARCH_WAIT)


async def test_an_answer_that_is_not_a_list_is_a_failed_search_and_says_so(page: Page, site: Path) -> None:
    fake = backend(search_mode="not_a_list")
    assert await serve(page, site, backend=fake) == []
    await page.fill("#q", "rust")
    await asked(page, fake)
    await said(page, f"{BASE} {FAILED}")
    assert await shown(page) == {"title"}


async def test_a_failed_search_says_so_keeps_the_title_match_and_clears_when_the_next_ask_works(
    page: Page, site: Path
) -> None:
    fake = backend(search_mode="error")
    problems = await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await said(page, f"{BASE} {FAILED}")
    assert await shown(page) == {"title"}
    assert all("503" in problem for problem in problems)  # nothing logged by the page, and no words in it
    fake.search_mode = "ok"
    await page.fill("#q", "rust ")
    await page.fill("#q", "rust")
    await said(page, "Showing 2 of 3 open roles in Israel. 0 discarded.")


async def test_a_search_with_no_connection_says_so_and_still_shows_the_title_match(page: Page, site: Path) -> None:
    fake = backend(search_mode="down")
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await said(page, f"{BASE} {FAILED}")
    assert await shown(page) == {"title"}


async def test_an_answer_at_the_cap_says_some_matches_may_be_missing_and_shows_what_came(
    page: Page, site: Path
) -> None:
    fake = backend()
    fake.text_hits = {"rust": [["Acme", "text"], *([["Acme", f"x{n}"] for n in range(2999)])]}
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    capped = "Too many description matches, so some roles may be missing. Add a word to narrow the search."
    await said(page, f"Showing 2 of 3 open roles in Israel. 0 discarded. {capped}")
    assert await shown(page) == {"title", "text"}


async def test_a_search_of_one_or_two_characters_says_three_are_needed_and_the_note_goes_with_longer_words(
    page: Page, site: Path
) -> None:
    fake = backend()
    await serve(page, site, backend=fake)
    await page.fill("#q", "ru")
    assert (
        await page.locator("#summary").inner_text() == "Showing 1 of 3 open roles in Israel. 0 discarded."
    )  # not while typing
    hint = "Type a word of 3 or more letters or digits to search job descriptions too."
    await said(page, f"Showing 1 of 3 open roles in Israel. 0 discarded. {hint}")
    assert fake.searches == []
    await page.fill("#q", "ui ux")  # five characters, but no word of three: the function would answer nothing
    await said(page, f"Showing 0 of 3 open roles in Israel. 0 discarded. {hint}")
    assert fake.searches == []
    await page.fill("#q", "rust")
    await said(page, "Showing 2 of 3 open roles in Israel. 0 discarded.")
    await page.fill("#q", "")
    await said(page, "Showing 3 of 3 open roles in Israel. 0 discarded.")


def opener(page: Page, job_id: str = "title") -> Locator:
    return page.locator(f"article[data-job-id='Acme|{job_id}'] [data-act=toggle]")


ON_OPENER = "document.activeElement.matches(\"article[data-job-id='Acme|title'] [data-act=toggle]\")"


def with_snippets() -> Backend:
    fake = backend(search_seconds=1.0)
    for row in fake.rows:
        row["snippet"] = "A short posting."
    return fake


async def test_a_late_answer_does_not_take_the_focus_from_a_button_the_reader_is_on(page: Page, site: Path) -> None:
    fake = with_snippets()
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await asked(page, fake)
    await opener(page).focus()
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert await page.evaluate(ON_OPENER)


def crowded() -> Backend:
    """Eleven roles whose title has "rust" (two pages of ten) and one that only its posting has, which by title sorts
    first: when the answer comes, the last role of the first page moves to the second."""
    fake = backend(search_seconds=2.0)  # time to focus and press a key before the answer
    fake.rows = [job(f"r{n:02d}", f"Rust Engineer {n:02d}", "Haifa, Israel") for n in range(1, 12)]
    fake.rows.append(job("aard", "Aardvark Developer", "Haifa, Israel"))
    fake.text_hits = {"rust": [["Acme", "aard"]]}
    return fake


async def test_a_late_answer_that_moves_the_role_gives_the_focus_a_visible_ring_on_the_list_heading(
    page: Page, site: Path
) -> None:
    fake = crowded()
    await serve(page, site, backend=fake)
    await page.select_option("#sort", "title")
    await page.fill("#q", "rust")
    await asked(page, fake)
    last = page.locator("#list article").last
    assert await last.get_attribute("data-job-id") == "Acme|r10"
    await last.locator("a[href], button").first.focus()
    await page.keyboard.press("Shift")  # the reader is on the keyboard: the focus ring shows
    await expect(page.locator("#list article").first).to_have_attribute("data-job-id", "Acme|aard", timeout=SEARCH_WAIT)
    assert "Acme|r10" not in await shown_ids(page)  # its role moved to the next page
    seen = await page.evaluate(
        """() => {
          const el = document.activeElement;
          const box = el.getBoundingClientRect();
          const style = getComputedStyle(el);
          const probe = document.createElement('i');
          probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
          document.body.append(probe);
          const accent = getComputedStyle(probe).color;
          probe.remove();
          return {
            id: el.id, focusVisible: el.matches(':focus-visible'), width: box.width, height: box.height,
            top: box.top, bottom: box.bottom, inside: box.top >= 0 && box.bottom <= innerHeight,
            outlineWidth: style.outlineWidth, outlineColor: style.outlineColor, accent,
          };
        }"""
    )
    assert seen["id"] == "list-h" and seen["focusVisible"]
    assert min(seen["width"], seen["height"]) > CLIPPED_PX and seen["inside"]  # on screen, not a clipped 1 px box
    assert seen["outlineWidth"] == "3px" and seen["outlineColor"] == seen["accent"]


async def test_the_list_heading_shows_only_when_the_keyboard_puts_the_focus_on_it(page: Page, site: Path) -> None:
    fake = crowded()
    await serve(page, site, backend=fake)
    box = await page.locator("#list-h").bounding_box()
    assert box and box["width"] <= 1 and box["height"] <= 1
    top = "document.querySelector('#list article').getBoundingClientRect().top + scrollY"  # scrolling is not moving
    before = await page.evaluate(top)
    await page.locator("#list-h").focus()
    await page.keyboard.press("Shift")
    shown_box = await page.locator("#list-h").bounding_box()
    assert shown_box and shown_box["width"] > CLIPPED_PX  # the keyboard shows it
    assert await page.evaluate(top) == before  # showing the heading does not move the list


async def test_the_text_matches_combine_with_the_other_filters(page: Page, site: Path) -> None:
    fake = backend()
    fake.rows = [*ROWS, job("far", "Backend Engineer", "London, UK", regions=[], locations=[])]
    fake.text_hits = {"rust": [["Acme", "text"], ["Acme", "far"]]}
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert await shown(page) == {"title", "text"}  # "far" is abroad: Israel is the default
    await page.click(".chip[data-group=regions][data-value=Abroad]")
    assert await shown(page) == {"far"}


async def test_an_answer_one_short_of_the_cap_has_no_note_and_a_hebrew_word_is_a_word(page: Page, site: Path) -> None:
    fake = backend()
    fake.text_hits = {"rust": [["Acme", "text"], *([["Acme", f"x{n}"] for n in range(2998)])]}  # 2999 keys
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    assert await page.locator("#summary").inner_text() == "Showing 2 of 3 open roles in Israel. 0 discarded."
    await page.fill("#q", "מוצר")  # a word of three letters or more in Hebrew is sent
    for _ in range(100):
        if fake.searches[-1] == "מוצר":
            break
        await page.wait_for_timeout(50)
    assert fake.searches == ["rust", "מוצר"]


async def test_a_late_failure_of_an_old_query_does_not_clear_the_note_of_the_one_shown(page: Page, site: Path) -> None:
    fake = backend(search_mode="error", search_delays={"rust": 1.5})  # "rust" fails after "dog"
    await serve(page, site, backend=fake)
    await page.fill("#q", "rust")
    await asked(page, fake)  # asked, not yet failed
    await page.fill("#q", "dog")
    await said(page, "Showing 1 of 3 open roles in Israel. 0 discarded. " + FAILED)  # "dog" fails first
    await page.wait_for_timeout(2000)  # then "rust" fails, for a query that is no longer shown
    await page.locator("#list article").first.locator("[data-act=toggle]").click()  # a redraw
    await expect(page.locator("#summary")).to_contain_text(FAILED)


WATCH_STATUS = """() => {
  window.__said = [];
  new MutationObserver(() => window.__said.push(document.getElementById('status').textContent)).observe(
    document.getElementById('status'), { childList: true, characterData: true, subtree: true });
}"""


async def test_a_search_is_announced_once_when_its_answer_comes_after_the_pause_and_never_twice_the_same(
    page: Page, site: Path
) -> None:
    fake = backend(
        search_seconds=1.0
    )  # the answer comes after the live region's pause: the count before it is not said
    await serve(page, site, backend=fake)
    await page.evaluate(WATCH_STATUS)
    await page.fill("#q", "rust")
    await expect(page.locator("#status")).to_have_text(
        "Showing 2 of 3 open roles in Israel. 0 discarded.", timeout=SEARCH_WAIT
    )
    await page.wait_for_timeout(1500)
    assert await page.evaluate("window.__said") == ["Showing 2 of 3 open roles in Israel. 0 discarded."]
    await page.fill("#q", "engineer")  # another search with the same count: it is said again
    await expect(page.locator("#list article")).to_have_count(2, timeout=SEARCH_WAIT)
    await page.wait_for_function("() => window.__said.length === 2", timeout=SEARCH_WAIT)
    await page.fill("#q", "engineer ")  # the same words: the same text is not written again
    await page.locator("#q").press("Backspace")
    await page.wait_for_timeout(1500)
    assert await page.input_value("#q") == "engineer"
    assert await page.evaluate("window.__said.length") == len(["rust", "engineer"])  # one write for each search


async def test_a_pointed_hebrew_word_is_sent_and_the_search_box_follows_the_text_direction(
    page: Page, site: Path
) -> None:
    fake = backend()
    await serve(page, site, backend=fake)
    assert await page.get_attribute("#q", "dir") == "auto"
    await page.fill("#q", "\u05e9\u05b8\u05c1\u05dc\u05d5\u05b9\u05dd")  # the points sit between the letters
    await asked(page, fake)
    assert fake.searches == ["\u05e9\u05b8\u05c1\u05dc\u05d5\u05b9\u05dd"]
