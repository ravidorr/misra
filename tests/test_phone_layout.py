"""The dashboard on a phone and on a desktop (the TODO item "Make the web app work well on a phone"): no screen
scrolls sideways, every control is a 44px target, the search is on the first screen, "New this week" is one line with
the rest behind a button, and the filters are a bottom sheet on a phone that the keyboard can use and a panel in the
page on a wide screen."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.conftest import add_job, write_json
from tests.test_dashboard_page import AXE, TAP_MIN, Backend, build_site, serve

HIRING_COMPANIES = 15  # the row shows up to 15
ROLES_PER_COMPANY = 2  # an old one and a new one
WIDTHS = [320, 375, 393, 430, 768, 1650]
WIDE = 768  # the footer text and links start at the left from here (centered below)
PHONE_WIDTHS = [320, 375, 393, 430]
FIRST_SCREEN = {"width": 393, "height": 852}  # an iPhone 16
SHEET = "#moreFilters"
NO_SIDEWAYS = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
CONTROLS = "a[href], button, summary, select, textarea, input:not([type=hidden]):not([tabindex='-1'])"
KEY_TAB_PRESSES = 40
# Past its last control the focus of a modal dialog goes to the browser's own controls (the body), never to the page.
IN_SHEET_OR_BROWSER = (
    "document.activeElement === document.body || document.activeElement.closest('#moreFilters') !== null"
)


def add_companies(db: sqlite3.Connection) -> None:
    """15 companies, each with an old role and a new one, so "New this week" is full (the row shows up to 15)."""
    old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    for n in range(HIRING_COMPANIES):
        company = f"Company number {n:02d} with a rather long name that must fit"
        add_job(db, company, "0", "Accountant", first_seen=old)
        add_job(db, company, "1", f"Software Engineering Intern {n}", first_seen=today)


@pytest.fixture
def crowd(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built over 15 hiring companies, the daily-email signup off like the `site` fixture."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, add_companies)


LONGEST_REAL_NAME = "Enlight Renewable Energy"  # the longest name with new roles in the live data: three lines
# The 15 names "New this week" showed on the day this was written (names only), most new roles first: one, two and three
# lines, and "monday.com", which must never break inside the word.
REAL_NAMES = {
    name: 15 - n
    for n, name in enumerate(
        [
            "Nvidia",
            "Mobileye",
            "monday.com",
            "NICE",
            "Verint",
            "Amazon",
            "Claroty",
            "Palo Alto Networks",
            "Wolt",
            "eToro",
            "Cisco",
            "Priority Software",
            LONGEST_REAL_NAME,
            "Jeeves",
            "Navan",
        ]
    )
}
# Extremes: names that sat above and below their row, a name of four lines and an unbroken word wider than a circle.
EXTREME_NAMES = {
    "ForSight Robotics": 9,
    "Aidoc": 8,
    LONGEST_REAL_NAME: 7,
    "Global Advanced Technologies Research Holdings International": 6,
    "Nvidia": 5,
    "Supercalifragilisticexpialidocious": 4,
    "monday.com": 3,
    "Palo Alto Networks": 2,
}
NAME_SETS = {"real": REAL_NAMES, "extremes": EXTREME_NAMES}
SAME_TOP = 0.5  # px
TOGGLE_STILL = 1  # px the button may move between "Show all" and "Show fewer"
NARROWEST = 320


@pytest.fixture(params=sorted(NAME_SETS))
def mixed(
    request: pytest.FixtureRequest, db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """The page built over companies with new roles whose names take one to four lines in "New this week"."""
    names: dict[str, int] = NAME_SETS[request.param]

    def fill(conn: sqlite3.Connection) -> None:
        old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
        for company, new_roles in names.items():
            add_job(conn, company, "old", "Accountant", first_seen=old)
            for n in range(new_roles):
                add_job(conn, company, f"new{n}", f"Software Engineering Intern {n}", first_seen=today)

    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, fill)


MANY_COMPANIES = 1234  # the widest the count can be for a long time: four digits and a plural


@pytest.fixture
def many(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built over a companies.json of MANY_COMPANIES working feeds, for the longest placeholder."""

    def fill(conn: sqlite3.Connection) -> None:
        add_companies(conn)
        write_json(
            workdir / "data" / "companies.json",
            [{"name": f"Company {n}", "crawl": True, "status": "ok"} for n in range(MANY_COMPANIES)],
        )

    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, fill)


async def open_page(page: Page, crowd: Path, width: int, height: int = 800, scheme: str = "light") -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size({"width": width, "height": height})
    problems = await serve(page, crowd)
    assert problems == []


async def listed(page: Page) -> int:
    """How many roles the summary line says the filters leave."""
    text = await page.locator("#summary").inner_text()
    return int(text.split()[1])


async def open_hiring(page: Page) -> None:
    """Expand "New this week" (it starts as one line)."""
    toggle = page.locator("#hiringToggle")
    if await toggle.is_visible() and await toggle.get_attribute("aria-expanded") == "false":
        await toggle.click()


async def visible_stories(page: Page) -> list[dict[str, float]]:
    boxes = []
    for story in await page.locator("#stories .story").all():
        if await story.is_visible():
            box = await story.bounding_box()
            if box is not None:  # a circle can be put away between the two questions, after a resize
                boxes.append(box)
    return boxes


async def settle(page: Page, expression: str) -> None:
    """Wait until the page says `expression` is true (the page script answers a resize a moment later; the page's CSP
    does not let Playwright's own polling evaluate a string)."""
    for _ in range(30):
        if await page.evaluate(expression):
            return
        await page.wait_for_timeout(100)
    raise AssertionError(expression)


async def lines_of_stories(page: Page) -> int:
    """How many lines of circles show, once the row has settled after a resize."""
    lines = 0
    for _ in range(20):
        lines = len({box["y"] for box in await visible_stories(page)})
        if lines == 1:
            break
        await page.wait_for_timeout(100)
    return lines


async def open_sheet(page: Page) -> None:
    await page.locator("#filtersOpen").click()
    assert await page.locator(SHEET).evaluate("e => e.matches(':modal')")


async def visible_controls(page: Page, root: str) -> list[tuple[str, dict[str, float]]]:
    """The box of every control under `root` that shows; a checkbox or radio is measured by its label, which is what the
    finger hits."""
    found: list[tuple[str, dict[str, float]]] = []
    for control in await page.locator(f"{root} :is({CONTROLS})").all():
        if not await control.is_visible() or await control.evaluate("e => e.closest('.sr-only, .skip') !== null"):
            continue
        target = control
        if await control.evaluate("e => e.type === 'checkbox' || e.type === 'radio'"):
            target = control.locator("xpath=ancestor::label[1]")
        box = await target.bounding_box()
        assert box is not None
        found.append((await control.evaluate("e => e.id || e.className || e.tagName"), box))
    return found


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
async def test_the_page_never_scrolls_sideways(page: Page, crowd: Path, width: int, scheme: str) -> None:
    await open_page(page, crowd, width, scheme=scheme)
    assert await page.evaluate(NO_SIDEWAYS)
    await open_hiring(page)  # and opened, at every width
    assert await page.evaluate(NO_SIDEWAYS)
    await page.locator("#gaps-title").click()
    assert await page.evaluate(NO_SIDEWAYS)
    await page.click("#starredView")
    assert await page.evaluate(NO_SIDEWAYS)


@pytest.mark.parametrize("width", [320, 393])
async def test_the_page_does_not_scroll_sideways_in_landscape_or_with_the_sheet_open(
    page: Page, crowd: Path, width: int
) -> None:
    await open_page(page, crowd, 852, height=width)  # a phone turned sideways
    assert await page.evaluate(NO_SIDEWAYS)
    assert await page.locator("#filtersOpen").is_visible()  # a short screen gets the sheet too
    assert await page.locator(SHEET).is_hidden()
    await open_sheet(page)
    assert await page.evaluate(NO_SIDEWAYS)
    await open_page(page, crowd, width, height=640)
    await open_sheet(page)
    assert await page.evaluate(NO_SIDEWAYS)
    box = await page.locator(SHEET).bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= width


@pytest.mark.parametrize("width", [320, 393, 1650])
async def test_hiring_now_is_one_line_with_the_rest_behind_a_button(page: Page, crowd: Path, width: int) -> None:
    await open_page(page, crowd, width)
    toggle = page.locator("#hiringToggle")
    one_line = await visible_stories(page)
    assert 1 <= len(one_line) < HIRING_COMPANIES
    assert len({box["y"] for box in one_line}) == 1  # exactly one line
    for box in one_line:
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width
    assert await toggle.get_attribute("aria-expanded") == "false"
    assert await toggle.get_attribute("aria-controls") == "stories"
    assert await toggle.inner_text() == f"Show all {HIRING_COMPANIES}"
    toggle_box = await toggle.bounding_box()
    assert toggle_box is not None
    assert toggle_box["height"] >= TAP_MIN
    heading = await page.locator("#hiring-h").bounding_box()
    assert heading is not None
    assert toggle_box["y"] + toggle_box["height"] <= one_line[0]["y"]  # in the heading row, above the circles
    assert toggle_box["x"] + toggle_box["width"] <= width
    assert await page.locator("#stories .story[hidden], #stories > li[hidden]").count() == HIRING_COMPANIES - len(
        one_line
    )
    assert await page.evaluate(NO_SIDEWAYS)
    assert await page.locator("#stories").evaluate("e => e.scrollWidth <= e.clientWidth")
    await toggle.click()  # open: all of them, wrapped below
    assert await toggle.get_attribute("aria-expanded") == "true"
    assert await toggle.inner_text() == "Show fewer"
    assert await page.evaluate("document.activeElement.id") == "hiringToggle"  # the focus stays on the button
    everyone = await visible_stories(page)
    assert len(everyone) == HIRING_COMPANIES
    assert len({box["y"] for box in everyone}) > 1  # wrapped
    for box in everyone:
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width
    assert await page.evaluate(NO_SIDEWAYS)
    assert await page.locator("#stories").evaluate("e => e.scrollWidth <= e.clientWidth")
    await toggle.click()
    assert await toggle.get_attribute("aria-expanded") == "false"
    assert len(await visible_stories(page)) == len(one_line)
    assert await page.evaluate("document.activeElement.id") == "hiringToggle"


async def test_hiring_now_is_reached_and_operated_by_keyboard(page: Page, crowd: Path) -> None:
    """The Tab order follows the picture: the heading's button first, then the circles it shows."""
    await open_page(page, crowd, 375)
    shown = len(await visible_stories(page))
    await page.locator("#filtersOpen").focus()
    await page.locator("#hiringToggle").focus()
    await page.keyboard.press("Enter")
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "true"
    assert len(await visible_stories(page)) == HIRING_COMPANIES
    assert shown < HIRING_COMPANIES
    for n in range(HIRING_COMPANIES):  # the next Tab goes into the circles that just appeared, all of them
        await page.keyboard.press("Tab")
        assert await page.evaluate("document.activeElement.classList.contains('story')"), n
    await page.locator("#hiringToggle").focus()
    await page.keyboard.press("Space")
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "false"
    assert await page.evaluate("document.activeElement.id") == "hiringToggle"
    for _ in range(shown):  # only the circles on the line are in the Tab order
        await page.keyboard.press("Tab")
        assert await page.evaluate("document.activeElement.classList.contains('story')")


async def test_hiring_now_keeps_its_choice_for_the_page_and_follows_the_width(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await page.click("#hiringToggle")
    await open_sheet(page)
    await page.click('.chip[data-group=levels][data-value="Intern"]')  # a new render keeps it open
    await page.click("#filtersDone")
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "true"
    await page.set_viewport_size({"width": 1650, "height": 800})
    await settle(page, "document.querySelectorAll('#stories > li:not([hidden])').length === 15")
    await page.click("#hiringToggle")
    assert await lines_of_stories(page) == 1
    await page.set_viewport_size(
        {"width": 320, "height": 800}
    )  # narrower: fewer on the line, the rest behind the button
    assert await lines_of_stories(page) == 1
    assert await page.evaluate(NO_SIDEWAYS)


@pytest.mark.parametrize("width", PHONE_WIDTHS)
async def test_every_control_is_a_44px_target_on_a_phone(page: Page, crowd: Path, width: int) -> None:
    await open_page(page, crowd, width)
    await open_hiring(page)
    await page.locator("#gaps-title").click()
    page_controls = await visible_controls(page, "body")  # the header and the footer too
    assert len(page_controls) > HIRING_COMPANIES
    for name, box in page_controls:
        assert box["height"] >= TAP_MIN, name
        assert box["width"] >= TAP_MIN, name
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width, name
    await open_sheet(page)
    assert await page.locator(SHEET).evaluate(
        "e => e.scrollWidth <= e.clientWidth"
    )  # the sheet does not scroll sideways
    sheet_controls = await visible_controls(page, SHEET)
    assert len(sheet_controls) > len(["levels", "types", "regions"])
    for name, box in sheet_controls:
        assert box["height"] >= TAP_MIN, name
        assert box["width"] >= TAP_MIN, name
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width, name


async def test_every_control_is_a_44px_target_on_a_desktop(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    for name, box in await visible_controls(page, "body"):
        assert box["height"] >= TAP_MIN, name
        assert box["width"] >= TAP_MIN, name


@pytest.mark.parametrize("size", [(375, 812), (320, 640), (1650, 900)])
async def test_the_search_is_on_the_first_screen_without_opening_anything(
    page: Page, crowd: Path, size: tuple[int, int]
) -> None:
    width, height = size
    await open_page(page, crowd, width, height)
    search = page.locator("#q")
    assert await search.is_visible()
    box = await search.bounding_box()
    assert box is not None
    assert box["y"] + box["height"] <= height  # no scrolling
    assert await page.locator("#moreFilters details[open], #gaps[open]").count() == 0  # nothing opened first
    words = count_words(await covered_count(page))
    assert await page.locator("#q").get_attribute("placeholder") == f"Search {words}"
    label = await page.locator("label[for=q]").inner_text()
    assert label == f"Search roles at {words} by title, company, team, location or description"


def count_words(n: int) -> str:
    return build_dashboard.coverage_words(n)["__COMPANIES__"]


async def covered_count(page: Page) -> int:
    """The number the page says it covers, read from the search field's hook; it must be the one of the page data."""
    hook = await page.locator("#q").get_attribute("data-companies-covered")
    assert hook is not None
    data = json.loads(await page.locator("#page-data").text_content() or "{}")
    assert data["covered"] == int(hook)
    return int(hook)


MEASURE_PLACEHOLDER = """() => {
  const q = document.getElementById('q');
  const style = getComputedStyle(q);
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
  probe.style.font = style.font;
  probe.style.letterSpacing = style.letterSpacing;
  probe.textContent = q.placeholder;
  document.body.append(probe);
  const text = probe.getBoundingClientRect().width;
  probe.remove();
  const room = q.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  return { text, room };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [(320, 640), (375, 667), (393, 852), (1650, 900)])
async def test_the_company_count_is_in_view_without_scrolling_and_fits_its_line(
    page: Page, many: Path, size: tuple[int, int], scheme: str
) -> None:
    """The count of companies is the search field's placeholder: on the first screen, at no height, on one line, even
    with four digits."""
    width, height = size
    await open_page(page, many, width, height=height, scheme=scheme)
    search = page.locator("#q")
    box = await search.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= width
    assert box["y"] + box["height"] <= height  # in view, nothing scrolled
    assert await page.evaluate("window.scrollY") == 0
    assert await covered_count(page) == MANY_COMPANIES
    assert await search.get_attribute("placeholder") == f"Search {count_words(MANY_COMPANIES)}"
    assert await search.evaluate("e => getComputedStyle(e, '::placeholder').color") != "rgba(0, 0, 0, 0)"
    measured = await page.evaluate(MEASURE_PLACEHOLDER)
    assert measured["text"] <= measured["room"]  # one line, not cut off
    assert await page.evaluate(NO_SIDEWAYS)


async def test_the_search_and_the_first_job_are_on_the_first_screen_of_an_iphone(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, FIRST_SCREEN["width"], FIRST_SCREEN["height"])
    first = page.locator("#list article").first
    title = await first.locator(".card-title").bounding_box()
    search = await page.locator("#q").bounding_box()
    assert title is not None
    assert search is not None
    assert search["y"] + search["height"] <= title["y"]  # the search comes before the roles
    assert title["y"] + title["height"] <= FIRST_SCREEN["height"]
    assert await first.locator(".card-company").is_visible()


async def test_the_filters_button_counts_the_active_filters_and_opens_the_sheet(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    button = page.locator("#filtersOpen")
    assert await button.inner_text() == "Filters"
    await open_sheet(page)  # the level chips and "New only" are in the sheet
    await page.click('.chip[data-group=levels][data-value="Intern"]')
    await page.click('.chip[data-group=levels][data-value="Junior"]')  # one group counts once
    await page.click("#onlyNew")
    assert await button.inner_text() == "Filters (2)"
    await page.click('.chip[data-group=types][data-value="R&D"]')
    await page.select_option("#sort", "co")
    await page.check("#hideStale")
    await page.click("#remote")
    assert await button.inner_text() == "Filters (6)"
    done = page.locator("#filtersDone")
    left = await listed(page)
    assert left < HIRING_COMPANIES * ROLES_PER_COMPANY  # the filters leave fewer than all the roles
    assert await done.inner_text() == f"Show {left} {'role' if left == 1 else 'roles'}"  # what the list will hold
    await page.locator(SHEET).locator("#reset").click()
    assert await button.inner_text() == "Filters"
    assert await done.inner_text() == f"Show {HIRING_COMPANIES * ROLES_PER_COMPANY} roles"
    await done.click()
    await page.fill("#q", "number 03")  # the search words are not a filter: they do not count
    assert await button.inner_text() == "Filters"
    await open_sheet(page)
    assert await done.inner_text() == "Show 2 roles"


async def test_the_sheet_works_by_keyboard(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    button = page.locator("#filtersOpen")
    await button.focus()
    await page.keyboard.press("Enter")
    sheet = page.locator(SHEET)
    assert await sheet.evaluate("e => e.matches(':modal')")
    assert await page.evaluate("document.activeElement.id") == "sheetTitle"
    assert await sheet.get_attribute("aria-labelledby") == "sheetTitle"
    for key in ("Tab", "Shift+Tab"):  # forwards, then backwards: the focus never lands on the page behind the sheet
        for _ in range(KEY_TAB_PRESSES):
            await page.keyboard.press(key)
            assert await page.evaluate(IN_SHEET_OR_BROWSER)
    await page.keyboard.press("Escape")
    assert not await sheet.evaluate("e => e.open")
    await expect(page.locator("#filtersOpen")).to_be_focused()  # the focus is back where it was
    await page.keyboard.press("Space")  # and it opens again
    assert await sheet.evaluate("e => e.matches(':modal')")


@pytest.mark.parametrize("closer", ["#filtersDone", "#filtersClose", "backdrop"])
async def test_the_sheet_closes_and_gives_the_focus_back(page: Page, crowd: Path, closer: str) -> None:
    await open_page(page, crowd, 375)
    await open_sheet(page)
    if closer == "backdrop":
        await page.mouse.click(187, 30)  # above the sheet
    else:
        await page.click(closer)
    assert not await page.locator(SHEET).evaluate("e => e.open")
    assert await page.locator(SHEET).is_hidden()
    await expect(page.locator("#filtersOpen")).to_be_focused()
    assert await page.evaluate(NO_SIDEWAYS)


@pytest.mark.parametrize("width", [320, 375])
async def test_the_sheets_corner_close_is_named_big_and_stays_in_view_while_the_filters_scroll(
    page: Page, crowd: Path, width: int
) -> None:
    await open_page(page, crowd, width, height=480)
    await open_sheet(page)
    close = page.locator("#filtersClose")
    assert await close.get_attribute("aria-label") == "Close"
    assert await close.inner_text() == ""  # a drawn (x), no text button
    await page.locator(".sheet-body").evaluate("e => e.parentElement.scrollTo(0, e.parentElement.scrollHeight)")
    assert (
        await page.locator(SHEET).evaluate("e => e.scrollTop") > 0
    )  # it did scroll: the sheet is taller than the screen
    box = await close.bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN
    assert box["width"] >= TAP_MIN
    assert box["y"] >= 0
    assert box["x"] + box["width"] <= width
    sheet_box = await page.locator(SHEET).bounding_box()
    assert sheet_box is not None
    assert box["y"] >= sheet_box["y"]  # inside the sheet, at the top of it, though the body is scrolled to its end
    title = await page.locator("#sheetTitle").bounding_box()
    assert title is not None
    assert title["x"] + title["width"] <= box["x"]
    await close.focus()
    await page.keyboard.press("Enter")
    await expect(page.locator("#filtersOpen")).to_be_focused()


async def test_the_sheet_filters_the_roles_behind_it(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await open_sheet(page)
    await page.select_option("#company", "Company number 02 with a rather long name that must fit")
    await page.click("#filtersDone")
    assert await page.locator("#list article").count() == ROLES_PER_COMPANY
    assert await page.locator("#filtersOpen").inner_text() == "Filters (1)"


async def test_the_sheet_has_no_axe_violations_open_in_both_themes(page: Page, crowd: Path) -> None:
    for scheme in ("light", "dark"):
        await open_page(page, crowd, 375, scheme=scheme)
        await page.evaluate(AXE.read_text(encoding="utf-8"))
        assert await page.evaluate("axe.run().then(r => r.violations.map(v => v.id))") == []
        await open_sheet(page)
        await page.evaluate(AXE.read_text(encoding="utf-8"))
        assert await page.evaluate("axe.run().then(r => r.violations.map(v => v.id))") == []


async def test_a_wide_screen_has_the_filters_in_the_page_and_no_filters_button(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    assert await page.locator("#filtersOpen").is_hidden()
    sheet = page.locator(SHEET)
    assert await sheet.is_visible()
    assert not await sheet.evaluate("e => e.matches(':modal')")
    assert await page.locator("#filtersClose, #filtersDone").evaluate_all(
        "els => els.every(e => e.offsetParent === null)"
    )
    await page.click('.chip[data-group=types][data-value="R&D"]')  # usable without opening anything
    assert await listed(page) <= HIRING_COMPANIES * ROLES_PER_COMPANY
    assert await page.locator("#filtersOpen").inner_text() == "Filters (1)"  # hidden here, but kept up to date
    await page.click("#reset")
    assert await page.locator("#filtersOpen").inner_text() == "Filters"
    assert await sheet.is_visible()
    assert await lines_of_stories(page) == 1  # "New this week" is one line here too


@pytest.mark.parametrize("width", [320, 393])
async def test_the_devops_and_sre_chip_is_listed_filters_and_fits_a_phone(page: Page, crowd: Path, width: int) -> None:
    """The role types come from classify.ROLE_TYPE_NAMES, so the extra chip needs no markup; it must not widen the page.
    The crowd has no DevOps role, so choosing the chip leaves nothing."""
    await open_page(page, crowd, width)
    await open_sheet(page)
    chip = page.locator('.chip[data-group=types][data-value="DevOps & SRE"]')
    names = await page.locator(".chip[data-group=types]").evaluate_all("els => els.map(e => e.dataset.value)")
    assert names.index("DevOps & SRE") == names.index("R&D") + 1
    assert await chip.is_visible()
    assert await page.evaluate(NO_SIDEWAYS)
    await chip.click()
    assert await chip.get_attribute("aria-pressed") == "true"
    assert await listed(page) == 0
    assert await page.evaluate(NO_SIDEWAYS)


async def test_the_sheet_follows_the_window_between_a_phone_and_a_desktop(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    sheet = page.locator(SHEET)
    assert await sheet.is_hidden()  # a phone does not show it until asked
    await open_sheet(page)
    await page.set_viewport_size({"width": 1200, "height": 800})  # turned into a desktop while open
    await settle(page, "!document.getElementById('moreFilters').matches(':modal')")  # the script answers later
    assert await sheet.is_visible()
    await page.set_viewport_size({"width": 375, "height": 800})
    assert await sheet.is_hidden()
    await page.set_viewport_size({"width": 1200, "height": 800})  # and back: closed on a phone, shown again here
    assert await sheet.is_visible()


async def test_the_level_chips_and_new_only_are_the_first_filters_in_the_sheet(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    for control in ("#levels", "#onlyNew"):
        assert await page.locator(f"{SHEET} .sheet-body {control}").count() == 1, control
    assert await page.locator(f"{SHEET} .sheet-body > .filter-group:first-child #levels").count() == 1
    assert await page.locator(f"{SHEET} .sheet-body > .filter-group:nth-child(2) #onlyNew").count() == 1
    assert await page.locator(f"{SHEET} #levels-h").evaluate("e => e.tagName") == "H3"
    assert await page.locator(".toolbar #starredView").count() == 1  # a view, not a filter: it stays outside the sheet
    assert await page.locator(".toolbar #onlyNew, .toolbar #levels").count() == 0
    assert await page.locator("#levels").is_hidden()  # a phone: closed sheet
    await open_sheet(page)
    assert await page.locator("#levels").is_visible()
    assert await page.locator("#onlyNew").is_visible()
    await page.set_viewport_size({"width": 1200, "height": 800})  # a wide screen: the sheet is a panel in the page
    await page.locator(SHEET).wait_for(state="visible")
    assert await page.locator("#levels").is_visible()
    assert await page.locator("#onlyNew").is_visible()


async def test_new_only_in_the_open_sheet_narrows_the_list_on_a_phone(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    before = await page.locator("#summary").inner_text()
    await open_sheet(page)
    await page.click("#onlyNew")
    await expect(page.locator("#summary")).not_to_have_text(before)  # the moved chip is still wired to the list
    assert await page.locator("#filtersOpen").inner_text() == "Filters (1)"


@pytest.mark.parametrize("width", PHONE_WIDTHS)
async def test_reset_filters_is_a_secondary_button_beside_the_primary_one(page: Page, crowd: Path, width: int) -> None:
    await open_page(page, crowd, width)
    await open_sheet(page)
    reset = page.locator("#reset")
    assert await reset.get_attribute("class") == "btn btn-pill"  # a button, not a link look, and not the primary one
    assert await page.locator("#filtersDone").get_attribute("class") == "btn btn-pill btn-primary sheet-done"
    foot = page.locator(f"{SHEET} .sheet-foot > button")
    assert await foot.evaluate_all("els => els.map(e => e.id)") == ["reset", "filtersDone"]  # Tab order: Reset first
    box = await reset.bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN
    assert box["width"] >= TAP_MIN
    assert await page.locator(SHEET).evaluate("e => e.scrollWidth <= e.clientWidth")


async def test_the_starred_list_puts_the_filter_controls_away(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await page.click("#starredView")
    for part in ("#filtersOpen", "#searchBar", SHEET):
        assert await page.locator(part).is_hidden(), part
    await page.click("#starredView")
    for part in ("#filtersOpen", "#searchBar"):
        assert await page.locator(part).is_visible(), part
    assert await page.locator(SHEET).is_hidden()  # a phone: the sheet opens from "Filters"


async def test_a_notice_and_a_card_have_the_same_border_radius_and_shadow(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 393)
    styles = (
        "e => { const s = getComputedStyle(e);"
        " return [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, s.borderTopLeftRadius, s.boxShadow]; }"
    )
    card = await page.locator("#list article").first.evaluate(styles)
    gaps = await page.locator("#gapsCard").evaluate(styles)  # a notice
    assert card == gaps
    await page.evaluate("document.querySelector('#authMessage').innerHTML = '<p class=\"notice\">Deleted.</p>'")
    assert await page.locator("#authMessage .notice").evaluate(styles) == card


async def test_a_card_on_a_phone_shows_the_title_and_apply_before_the_tags(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 393)
    card = page.locator("#list article").first
    ys = []
    for selector in (".card-company", ".card-title", ".btn-primary", ".card-tags"):
        box = await card.locator(selector).first.bounding_box()
        assert box is not None
        ys.append(box["y"])
    assert ys == sorted(ys)
    await open_page(page, crowd, 1650)  # a desktop keeps the tags between the title and Apply
    first = page.locator("#list article").first
    tags = await first.locator(".card-tags").bounding_box()
    apply = await first.locator(".btn-primary").bounding_box()
    assert tags is not None
    assert apply is not None
    assert tags["y"] < apply["y"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 375, 393, 1650])
async def test_report_a_site_is_in_view_with_the_coverage_card_closed_and_open(
    page: Page, crowd: Path, width: int, scheme: str
) -> None:
    await open_page(page, crowd, width, scheme=scheme)
    button = page.locator("#gapsCard #reportOpen")
    assert await page.locator("#gaps").get_attribute("open") is None
    await button.scroll_into_view_if_needed()
    await expect(button).to_be_visible()  # closed: the list is hidden, the report row is not
    box = await button.bounding_box()
    assert box is not None
    assert box["height"] >= TAP_MIN
    assert 0 <= box["x"] <= box["x"] + box["width"] <= width
    assert await page.evaluate(NO_SIDEWAYS)
    await page.locator("#gaps-title").click()
    await expect(page.locator("#gaps .gap-part").first).to_be_visible()
    assert await page.evaluate(NO_SIDEWAYS)
    below = await page.locator("#gaps").evaluate("d => d.getBoundingClientRect().bottom")
    opened = await button.bounding_box()
    assert opened is not None
    assert opened["y"] >= below - 1  # the row stays under the list


FOOT_DATE = re.compile(r"^\d{1,2} [A-Z][a-z]{2}( \d{4})?, \d{2}:\d{2} UTC$")


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 375, 393, 1650])
async def test_the_footer_fits_and_has_44px_links(page: Page, crowd: Path, width: int, scheme: str) -> None:
    await open_page(page, crowd, width, scheme=scheme)
    foot = page.locator("footer.foot")
    await foot.scroll_into_view_if_needed()
    assert await page.evaluate(NO_SIDEWAYS)
    stamp = await page.locator("#upd").inner_text()
    assert FOOT_DATE.match(stamp), stamp
    assert (
        await page.locator(".foot-updated").inner_text()
        == f"Career pages last read {stamp}. Career pages are read daily."
    )
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z?", await page.locator("#upd").get_attribute("datetime") or "")
    box = await foot.bounding_box()
    assert box is not None
    names = []
    for link in await foot.locator("nav a").all():
        link_box = await link.bounding_box()
        assert link_box is not None
        assert link_box["height"] >= TAP_MIN
        assert link_box["width"] >= TAP_MIN
        assert box["x"] <= link_box["x"] <= link_box["x"] + link_box["width"] <= box["x"] + box["width"]
        names.append(await link.inner_text())
    assert names == ["About", "Connect your assistant", "Contact", "Privacy", "Terms", "Security"]
    links = await foot.locator("nav").bounding_box()
    text = await page.locator(".foot-updated").bounding_box()
    assert links is not None
    assert text is not None
    assert links["y"] >= text["y"] + text["height"]  # the links are a line of their own below the text, at every width
    if width >= WIDE:
        first = await foot.locator("nav a").first.bounding_box()
        assert first is not None
        assert abs(first["x"] - text["x"]) < 1  # both start at the left
        assert await foot.evaluate("e => getComputedStyle(e).textAlign") in ("start", "left")
    else:
        assert await foot.evaluate("e => getComputedStyle(e).textAlign") == "center"


@pytest.mark.parametrize(
    ("stamp", "shown"),
    [
        ("2025-03-05 07:09 UTC", "5 Mar 2025, 07:09 UTC"),  # not this year: the year is said
        ("2020-12-31 23:59 UTC", "31 Dec 2020, 23:59 UTC"),
        ("not a date", "not a date"),  # anything else is shown as it is
    ],
)
async def test_the_footer_date_is_short_and_says_the_year_only_when_it_is_not_this_year(
    page: Page, crowd: Path, stamp: str, shown: str
) -> None:
    html = (crowd / "index.html").read_text(encoding="utf-8")
    match = re.search(r'"generated":"([^"]*)"', html)
    assert match is not None
    (crowd / "index.html").write_text(html.replace(match.group(0), f'"generated":"{stamp}"'), encoding="utf-8")
    await open_page(page, crowd, 393)
    assert await page.locator("#upd").inner_text() == shown
    year = str(datetime.now(UTC).year)
    today = datetime.now(UTC).strftime("%Y-%m-%d 06:30 UTC")
    html = (crowd / "index.html").read_text(encoding="utf-8")
    (crowd / "index.html").write_text(
        html.replace(f'"generated":"{stamp}"', f'"generated":"{today}"'), encoding="utf-8"
    )
    await open_page(page, crowd, 393)
    assert year not in await page.locator("#upd").inner_text()


async def hero(page: Page) -> str:
    return " ".join((await page.locator("#heroTop").inner_text(), await page.locator("#heroBottom").inner_text()))


async def pick_level(page: Page, level: str) -> None:
    await page.click(f'.chip[data-group=levels][data-value="{level}"]')


async def test_the_headline_and_the_row_say_the_same_with_no_filter(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    assert await page.locator("#hiring-h").inner_text() == "New this week"
    assert await hero(page) == f"{HIRING_COMPANIES} new this week."
    story = page.locator("#stories .story").first
    assert await story.get_attribute("data-count") == "1"
    assert await story.locator(".story-count").inner_text() == "1 new"
    assert await story.locator(".story-count").get_attribute("aria-hidden") == "true"
    assert await story.inner_text() == "C\nCompany number 00 with a rather long name that must fit\n1 new\n, 1 new role"


@pytest.mark.parametrize("level", ["Intern", "Not specified", "Junior"])
async def test_a_circle_never_promises_more_new_roles_than_a_tap_shows(page: Page, crowd: Path, level: str) -> None:
    """The bug: with Level = Junior a circle said "5 new" and the list was empty after the tap."""
    await open_page(page, crowd, 1650)
    await pick_level(page, level)
    await open_hiring(page)
    names = [await s.get_attribute("data-company") for s in await page.locator("#stories .story").all()]
    assert len(names) == (HIRING_COMPANIES if level == "Intern" else 0)  # the new roles are the interns
    for name in names:
        story = page.locator(f'#stories .story[data-company="{name}"]')
        promised = int(str(await story.get_attribute("data-count")))
        await story.click()
        assert await story.get_attribute("aria-pressed") == "true"
        shown = await page.locator("#list article[data-new=true]").count()
        assert shown == promised >= 1
        await story.click()  # a second tap clears the company
        assert await story.get_attribute("aria-pressed") == "false"
    assert await page.locator("#hiring").is_hidden() == (not names)


@pytest.mark.parametrize(
    ("level", "query", "words"),
    [
        ("", "", f"{HIRING_COMPANIES} new this week."),
        ("Intern", "", f"{HIRING_COMPANIES} new matches."),
        ("", "Intern 14", "1 new match."),
        ("Not specified", "", f"{HIRING_COMPANIES} open matches."),  # nothing new matches: the open roles that do
        ("Junior", "", "No matches."),
    ],
)
async def test_the_headline_follows_the_filters(page: Page, crowd: Path, level: str, query: str, words: str) -> None:
    await open_page(page, crowd, 1650)
    if level:
        await pick_level(page, level)
    if query:
        await page.fill("#q", query)
    shown = "document.getElementById('heroTop').textContent + ' ' + document.getElementById('heroBottom').textContent"
    await settle(page, f"{shown} === '{words}'")
    assert await hero(page) == words


async def test_the_headline_for_starred_names_no_place(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    assert await hero(page) == f"{HIRING_COMPANIES} new this week."
    await page.click("#starredView")
    assert await hero(page) == "Starred roles."
    await page.click("#starredView")
    assert await page.locator("#hiring").is_visible()


async def test_a_company_without_new_roles_stays_while_it_is_chosen(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    await page.locator("#stories .story").first.click()
    await pick_level(page, "Not specified")  # the chosen company's new role is an intern
    stories = page.locator("#stories .story")
    assert await stories.count() == 1
    assert await stories.get_attribute("aria-pressed") == "true"
    assert await stories.locator(".story-count").inner_text() == "0 new"
    assert (await stories.inner_text()).endswith(", no new roles")
    await stories.click()  # it can be turned off
    assert await page.locator("#hiring").is_hidden()


async def test_the_focus_stays_on_the_circle_after_a_tap(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 393)
    story = page.locator("#stories .story").nth(1)
    name = await story.get_attribute("data-company")
    await story.focus()
    await page.keyboard.press("Enter")
    assert await page.evaluate("document.activeElement.dataset.company") == name


@pytest.fixture
def stale(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built over roles that are all older than a week: nothing is new, one role is abroad."""
    old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")

    def fill(conn: sqlite3.Connection) -> None:
        for n in range(3):
            add_job(conn, f"Old company {n}", "0", "Accountant", first_seen=old)
        add_job(conn, "Far company", "0", "Accountant", first_seen=old, location="London, United Kingdom")

    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, fill)


async def test_the_headline_counts_open_roles_when_nothing_is_new(page: Page, stale: Path) -> None:
    await open_page(page, stale, 1650)
    assert await hero(page) == "3 open in Israel."
    assert await page.locator("#hiring").is_hidden()
    await page.click(".chip[data-group=regions][data-value=Abroad]")
    assert await hero(page) == "1 open match."  # Abroad is a region like the others: it shows the roles abroad


@pytest.fixture
def abroad(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The crowd, plus a new role abroad."""

    def fill(conn: sqlite3.Connection) -> None:
        add_companies(conn)
        today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
        old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        add_job(conn, "Far company", "0", "Accountant abroad", first_seen=old, location="London, United Kingdom")
        add_job(conn, "Far company", "1", "Intern abroad", first_seen=today, location="London, United Kingdom")

    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, fill)


async def test_the_abroad_region_shows_the_new_roles_abroad_in_the_headline_and_row(page: Page, abroad: Path) -> None:
    await open_page(page, abroad, 1650)
    assert await hero(page) == f"{HIRING_COMPANIES} new this week."
    await open_hiring(page)
    assert await page.locator("#stories .story").count() == HIRING_COMPANIES
    await page.click(".chip[data-group=regions][data-value=Abroad]")
    assert await hero(page) == "1 new match."  # only the roles abroad
    assert await page.locator("#stories .story").count() == 1
    assert await page.locator("#stories .story").get_attribute("data-company") == "Far company"


async def test_a_tap_leaves_the_headline_and_the_other_circles_alone(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    await open_hiring(page)
    await page.locator("#stories .story").first.click()
    assert await hero(page) == f"{HIRING_COMPANIES} new this week."  # Company is not a filter of the headline
    assert await page.locator("#stories .story").count() == HIRING_COMPANIES


@pytest.mark.parametrize("change", ["sort", "onlyNew", "spaces"])
async def test_sorting_new_only_and_a_blank_search_do_not_make_the_headline_a_filter(
    page: Page, crowd: Path, change: str
) -> None:
    await open_page(page, crowd, 1650)
    if change == "sort":
        await page.select_option("#sort", "co")
    elif change == "onlyNew":
        await page.click("#onlyNew")
    else:
        await page.fill("#q", "   ")
    assert await hero(page) == f"{HIRING_COMPANIES} new this week."


async def test_the_focus_goes_to_the_list_heading_when_the_chosen_circle_goes(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    await page.select_option("#company", "Company number 00 with a rather long name that must fit")
    await pick_level(page, "Not specified")  # nothing new matches: only the chosen company's circle is left
    await page.locator("#stories .story").focus()
    await page.keyboard.press("Enter")  # turns the company off, and the circle with it
    assert await page.evaluate("document.activeElement.id") == "list-h"


LONG_URL = "https://example.com/" + "a" * 200
SNIPPET_TEXT = "We are looking for a highly skilled engineer to join a team that builds products used by millions " * 6


async def serve_snippets(page: Page, crowd: Path, width: int, height: int = 800, scheme: str = "light") -> None:
    rows = json.loads((crowd.parent / "rows.json").read_text(encoding="utf-8"))
    for n, row in enumerate(rows):
        row["snippet"] = [SNIPPET_TEXT.strip(), f"Join {LONG_URL} now", None][n % 3]
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size({"width": width, "height": height})
    assert await serve(page, crowd, backend=Backend(rows)) == []


@pytest.mark.parametrize("width", PHONE_WIDTHS)
async def test_snippets_do_not_scroll_the_page_sideways_even_with_a_long_unbroken_word(
    page: Page, crowd: Path, width: int
) -> None:
    await serve_snippets(page, crowd, width)
    assert await page.locator(".card-snippet").count() > 0
    assert await page.evaluate(NO_SIDEWAYS)
    for snippet in await page.locator(".card-snippet").all():
        assert await snippet.evaluate("e => e.scrollWidth <= e.clientWidth")


@pytest.mark.parametrize("width", [375, 1650])
async def test_a_snippet_is_shown_whole_on_the_card(page: Page, crowd: Path, width: int) -> None:
    await serve_snippets(page, crowd, width)
    long = page.locator(".card-snippet", has_text="highly skilled").first
    assert await long.evaluate(
        "e => e.scrollHeight <= e.clientHeight + 1"
    )  # nothing is cut: there is no Details dialog
    assert await page.locator(".card-snippet").first.evaluate("e => getComputedStyle(e).webkitLineClamp") == "none"


async def test_the_first_job_and_the_search_are_still_on_the_first_screen_with_snippets(
    page: Page, crowd: Path
) -> None:
    await serve_snippets(page, crowd, FIRST_SCREEN["width"], FIRST_SCREEN["height"])
    first = page.locator("#list article").first
    assert await first.locator(".card-snippet").count() == 1
    title = await first.locator(".card-title").bounding_box()
    snippet = await first.locator(".card-snippet").bounding_box()
    search = await page.locator("#q").bounding_box()
    assert title is not None
    assert snippet is not None
    assert search is not None
    assert search["y"] + search["height"] <= title["y"]
    assert snippet["y"] + snippet["height"] <= FIRST_SCREEN["height"]  # the title and its description, both in view


BROKEN_WORDS = """() => {
  const broken = [];
  for (const name of document.querySelectorAll('#stories > li:not([hidden]) .story-name')) {
    for (const match of name.textContent.matchAll(/\\S+/g)) {
      const range = document.createRange();
      range.setStart(name.firstChild, match.index);
      range.setEnd(name.firstChild, match.index + match[0].length);
      if (new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size > 1) broken.push(match[0]);
    }
  }
  return broken;
}"""
LONG_WORD = "Supercalifragilisticexpialidocious"  # wider than a circle: the one word that may break


async def story_rows(page: Page) -> list[dict[str, list[float]]]:
    """The circles that show, grouped by the row they sit in: each row has the tops of its circles (the round badge, the
    thing the eye lines up) and the number of lines of each label."""
    return await page.evaluate(
        """() => {
          const rows = new Map();
          for (const li of document.querySelectorAll('#stories > li:not([hidden])')) {
            const key = Math.round(li.getBoundingClientRect().top);
            const row = rows.get(key) ?? { tops: [], lines: [] };
            const name = li.querySelector('.story-name');
            row.tops.push(li.querySelector('.story-badge').getBoundingClientRect().top);
            const line = parseFloat(getComputedStyle(name).lineHeight);
            row.lines.push(Math.round(name.getBoundingClientRect().height / line));
            rows.set(key, row);
          }
          return [...rows.values()];
        }"""
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["collapsed", "expanded"])
@pytest.mark.parametrize("width", [320, 375, 559, 768, 1280])
async def test_the_strip_is_lined_up_and_no_name_is_cut(
    page: Page, mixed: Path, width: int, state: str, scheme: str
) -> None:
    """The bugs: a label of two or three lines lifted its circle above the one-line labels (the buttons stretched to the
    tallest of their row and a button centres its content), a long name ended in an ellipsis, "monday.com" broke in the
    middle of the word, in both states of the strip."""
    await open_page(page, mixed, width, scheme=scheme)
    toggle = page.locator("#hiringToggle")
    if state == "expanded":
        await open_hiring(page)
        assert not await toggle.is_visible() or await toggle.get_attribute("aria-expanded") == "true"
    rows = await story_rows(page)
    assert rows
    if state == "collapsed":
        assert len(rows) == 1  # one line, so one top for all of them
    elif width > NARROWEST:  # at 320 px a row may hold one circle, so there is nothing to line up
        assert any(len(set(row["lines"])) > 1 for row in rows)  # a row that mixes one-line and longer labels: the case
    for row in rows:
        assert max(row["tops"]) - min(row["tops"]) <= SAME_TOP
    names = page.locator("#stories > li:not([hidden]) .story-name")
    assert await names.count() == sum(len(row["tops"]) for row in rows)
    for n in range(await names.count()):
        measure = await names.nth(n).evaluate(
            "e => ({cut: e.scrollHeight - e.clientHeight, wide: e.scrollWidth - e.clientWidth, "
            "ellipsis: getComputedStyle(e).textOverflow, clamp: getComputedStyle(e).webkitLineClamp})"
        )
        assert measure == {"cut": 0, "wide": 0, "ellipsis": "clip", "clamp": "none"}, await names.nth(n).inner_text()
    broken = await page.evaluate(BROKEN_WORDS)
    assert [word for word in broken if word != LONG_WORD] == []
    if LONG_WORD in await page.locator("#stories").inner_text():
        assert broken == [LONG_WORD]  # the detector sees a break when there is one
    assert await page.evaluate(NO_SIDEWAYS)
    assert await page.locator("#stories").evaluate("e => e.scrollWidth <= e.clientWidth")
    for story in await page.locator("#stories > li:not([hidden]) .story").all():
        box = await story.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate("axe.run('#hiring').then(r => r.violations.map(v => v.id))") == []


@pytest.mark.parametrize("width", [320, 375, 559])
async def test_show_all_and_show_fewer_stay_in_one_place_above_the_circles(page: Page, mixed: Path, width: int) -> None:
    """The button sits at the right of the heading, level with it, and does not move when it is pressed."""
    await open_page(page, mixed, width)
    toggle = page.locator("#hiringToggle")
    assert await toggle.is_visible()  # more circles than fit on the line at these widths
    await toggle.scroll_into_view_if_needed()  # the click would scroll it: measure where the page stays still
    before = await toggle.bounding_box()
    heading = await page.locator("#hiring-h").bounding_box()
    badge = await page.locator("#stories .story-badge").first.bounding_box()
    assert before is not None
    assert heading is not None
    assert badge is not None
    assert before["height"] >= TAP_MIN
    assert before["y"] + before["height"] <= badge["y"]  # above the circles, never beside or among them
    assert abs((before["y"] + before["height"] / 2) - (heading["y"] + heading["height"] / 2)) <= 1  # level with it
    assert before["x"] + before["width"] <= width
    scrolled = await page.evaluate("window.scrollY")
    await toggle.click()
    assert await toggle.inner_text() == "Show fewer"
    after = await toggle.bounding_box()
    assert after is not None
    assert await page.evaluate("window.scrollY") == scrolled  # pressing it moved nothing under the finger
    assert abs(after["y"] - before["y"]) <= TOGGLE_STILL
    assert abs(after["height"] - before["height"]) <= TOGGLE_STILL
    assert abs((after["x"] + after["width"]) - (before["x"] + before["width"])) <= TOGGLE_STILL  # the right edge holds


async def test_a_chosen_company_name_is_not_cut_either(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await page.locator("#stories .story").first.click()
    assert await page.locator("#stories .story").first.get_attribute("aria-pressed") == "true"
    name = page.locator("#stories .story-name").first
    assert await name.evaluate("e => e.scrollHeight <= e.clientHeight && e.scrollWidth <= e.clientWidth")
