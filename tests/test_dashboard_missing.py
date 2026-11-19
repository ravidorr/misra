"""The prompt under the list that offers the report dialog (PLAN_3.md step F1), in a real browser against the built page
and a fake backend. It shows for a search or a company with fewer than three roles, and for any list with none; the
unfiltered view never has it. Its button opens the dialog of the coverage card (the dialog, its words and the
`report-site` function are tested in tests/test_report_site.py and are not changed)."""

import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import AXE, AXE_RUN, PHONE, TAP_MIN, Backend, build_site, row, serve

DAY = "2026-01-02T00:00:00"
SEARCH_WAIT = 4000  # ms: the page waits 400 ms after the last key, then asks
PROMPT = "#missing"
SENTENCE = "Is a company missing? Tell us which one."
THREE = 3
ROWS = [
    *(row("Acme", str(n), f"Product Manager {n}", DAY) for n in range(5)),
    *(row("Cafe", str(n), title, DAY) for n, title in enumerate(["Barista", "Cook", "Cashier"])),
    *(row("Tiny", str(n), title, DAY) for n, title in enumerate(["Janitor", "Guard"])),
]
VIEWS = {"desktop": {"width": 1280, "height": 900}, "phone": PHONE}


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


async def pick_company(page: Page, company: str) -> None:
    """Choose a company in the filters, which on a phone are a sheet that its button opens."""
    if await page.locator("#filtersOpen").is_visible():
        await page.click("#filtersOpen")
    await page.select_option("#company", company)
    if await page.locator("#filtersDone").is_visible():
        await page.click("#filtersDone")


async def test_the_unfiltered_view_has_no_prompt(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await expect(page.locator("#list article").first).to_be_visible()  # the list is drawn, so render() has run
    await expect(page.locator(PROMPT)).to_be_hidden()


async def test_a_search_with_no_roles_shows_the_prompt(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "zzzz")
    await expect(page.locator("#list .empty")).to_be_visible()
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)
    assert await page.locator(f"{PROMPT} span").inner_text() == SENTENCE
    assert await page.locator("#missingOpen").inner_text() == "Add a company"
    assert await page.locator(f"{PROMPT}").get_attribute("data-report") == "prompt"
    assert await page.locator("article").count() == 0  # the prompt is not a card


async def test_a_search_with_one_role_shows_the_prompt_and_the_role(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "barista")
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)
    assert await page.locator("#list article").count() == 1


async def test_a_search_with_three_roles_has_no_prompt(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "product")
    await page.wait_for_timeout(1000)  # past the pause, so the answer is in
    assert await page.locator("#list article").count() == len(ROWS) - THREE - 2
    await expect(page.locator(PROMPT)).to_be_hidden()


async def test_a_company_with_two_roles_shows_the_prompt_and_one_with_three_does_not(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await pick_company(page, "Tiny")
    assert await page.locator("#list article").count() == len(["Janitor", "Guard"])
    await expect(page.locator(PROMPT)).to_be_visible()
    await pick_company(page, "Cafe")
    assert await page.locator("#list article").count() == THREE
    await expect(page.locator(PROMPT)).to_be_hidden()
    await pick_company(page, "")
    await expect(page.locator(PROMPT)).to_be_hidden()


async def test_the_prompt_waits_for_the_description_search(page: Page, site: Path) -> None:
    fake = Backend(ROWS)
    fake.search_seconds = 1.0
    await serve(page, site, backend=fake)
    await page.fill("#q", "zzzz")
    await expect(page.locator(PROMPT)).to_be_hidden()  # the answer may bring roles
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)


async def test_the_starred_list_has_no_prompt(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "zzzz")
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)
    await page.click("#starredView")
    await expect(page.locator("#list .empty")).to_have_text("No starred roles yet. Use Star on a role to keep it here.")
    await expect(page.locator(PROMPT)).to_be_hidden()


async def test_the_button_opens_the_report_dialog_of_the_coverage_card(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "zzzz")
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)
    await page.click("#missingOpen")
    await expect(page.locator("#reportDialog")).to_be_visible()
    assert await page.locator("#reportTitle").inner_text() == "Add a company"
    assert await page.evaluate("document.activeElement.id") == "reportCompany"
    await page.click("#reportCancel")
    await expect(page.locator("#reportDialog")).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "missingOpen"


async def test_the_prompt_stays_away_when_the_report_script_does_not_load(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.route("**/report.js", lambda route: route.abort())  # after serve(): the later route wins
    await page.reload()
    await page.wait_for_selector("#list article")
    await page.fill("#q", "zzzz")
    await expect(page.locator("#list .empty")).to_be_visible()
    await page.wait_for_timeout(1000)  # past the pause, so the answer is in
    await expect(page.locator(PROMPT)).to_be_hidden()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("view", VIEWS)
async def test_the_prompt_reads_well_and_has_no_axe_violations(page: Page, site: Path, scheme: str, view: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(VIEWS[view])
    await serve(page, site, backend=Backend(ROWS))
    await page.fill("#q", "zzzz")
    await expect(page.locator(PROMPT)).to_be_visible(timeout=SEARCH_WAIT)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    button = await page.locator("#missingOpen").bounding_box()
    assert button is not None
    assert button["height"] >= TAP_MIN
    assert button["width"] >= TAP_MIN
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
