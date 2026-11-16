"""The dashboard on a phone and on a desktop (the TODO item "Make the web app work well on a phone"): no screen
scrolls sideways, every control is a 44px target, the search is on the first screen, "Hiring now" is one line with the
rest behind a button, and the filters are a bottom sheet on a phone that the keyboard can use and a panel in the page
on a wide screen."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.conftest import add_job
from tests.test_dashboard_page import AXE, TAP, build_site, serve

HIRING_COMPANIES = 15  # the row shows up to 15
ROLES_PER_COMPANY = 2  # an old one and a new one
WIDTHS = [320, 375, 393, 430, 768, 1650]
PHONE_WIDTHS = [320, 375, 393, 430]
FIRST_SCREEN = {"width": 393, "height": 852}  # an iPhone 16
SHEET = "#moreFilters"
NO_SIDEWAYS = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
CONTROLS = "a[href], button, summary, select, textarea, input:not([type=hidden]):not([type=file]):not([tabindex='-1'])"
KEY_TAB_PRESSES = 40
# Past its last control the focus of a modal dialog goes to the browser's own controls (the body), never to the page.
IN_SHEET_OR_BROWSER = (
    "document.activeElement === document.body || document.activeElement.closest('#moreFilters') !== null"
)


def add_companies(db: sqlite3.Connection) -> None:
    """15 companies, each with an old role and a new one, so "Hiring now" is full (the row shows up to 15)."""
    old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    for n in range(HIRING_COMPANIES):
        company = f"Company number {n:02d} with a long name"
        add_job(db, company, "0", "Accountant", first_seen=old)
        add_job(db, company, "1", f"Software Engineering Intern {n}", first_seen=today)


@pytest.fixture
def crowd(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built over 15 hiring companies, the daily-email signup off like the `site` fixture."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, add_companies)


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
    """Expand "Hiring now" (it starts as one line)."""
    toggle = page.locator("#hiringToggle")
    if await toggle.get_attribute("aria-expanded") == "false":
        await toggle.click()


async def visible_stories(page: Page) -> list[dict[str, float]]:
    boxes = []
    for story in await page.locator("#stories .story").all():
        if await story.is_visible():
            box = await story.bounding_box()
            if box is not None:  # a circle can be put away between the two questions, after a resize
                boxes.append(box)
    return boxes


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
    assert toggle_box["height"] >= TAP
    assert toggle_box["x"] >= one_line[-1]["x"] + one_line[-1]["width"]  # at the end of the line
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
    await open_page(page, crowd, 375)
    shown = len(await visible_stories(page))
    await page.locator("#stories .story").first.focus()
    for _ in range(shown - 1):  # only the circles on the line are in the Tab order
        await page.keyboard.press("Tab")
        assert await page.evaluate("document.activeElement.classList.contains('story')")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "hiringToggle"  # then the button, at the end of the line
    await page.keyboard.press("Enter")
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "true"
    assert len(await visible_stories(page)) == HIRING_COMPANIES
    await page.keyboard.press("Space")
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "false"
    assert await page.evaluate("document.activeElement.id") == "hiringToggle"


async def test_hiring_now_keeps_its_choice_for_the_page_and_follows_the_width(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await page.click("#hiringToggle")
    await page.click('.chip[data-group=levels][data-value="Intern"]')  # a new render keeps it open
    assert await page.locator("#hiringToggle").get_attribute("aria-expanded") == "true"
    await page.set_viewport_size({"width": 1650, "height": 800})
    await page.wait_for_function("document.querySelectorAll('#stories > li:not([hidden])').length === 15")
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
        assert box["height"] >= TAP, name
        assert box["width"] >= TAP, name
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width, name
    await open_sheet(page)
    await page.locator("#places-title").click()
    sheet_controls = await visible_controls(page, SHEET)
    assert len(sheet_controls) > len(["levels", "types", "regions"])
    for name, box in sheet_controls:
        assert box["height"] >= TAP, name
        assert box["width"] >= TAP, name
        assert 0 <= box["x"] <= box["x"] + box["width"] <= width, name


async def test_every_control_is_a_44px_target_on_a_desktop(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 1650)
    for name, box in await visible_controls(page, "body"):
        assert box["height"] >= TAP, name
        assert box["width"] >= TAP, name


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
    assert await page.locator("#q").get_attribute("placeholder") == "Search roles, companies, cities"
    assert await page.locator("label[for=q]").inner_text() == "Search roles, companies and cities"


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
    await page.click('.chip[data-group=levels][data-value="Intern"]')
    await page.click('.chip[data-group=levels][data-value="Junior"]')  # one group counts once
    await page.click("#onlyNew")
    assert await button.inner_text() == "Filters (2)"
    await open_sheet(page)
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


async def test_the_sheet_filters_the_roles_behind_it(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await open_sheet(page)
    await page.select_option("#company", "Company number 02 with a long name")
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
    assert await lines_of_stories(page) == 1  # "Hiring now" is one line here too


async def test_the_sheet_follows_the_window_between_a_phone_and_a_desktop(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    sheet = page.locator(SHEET)
    assert await sheet.is_hidden()  # a phone does not show it until asked
    await open_sheet(page)
    await page.set_viewport_size({"width": 1200, "height": 800})  # turned into a desktop while open
    assert await sheet.is_visible()
    assert not await sheet.evaluate("e => e.matches(':modal')")
    await page.set_viewport_size({"width": 375, "height": 800})
    assert await sheet.is_hidden()
    await page.set_viewport_size({"width": 1200, "height": 800})  # and back: closed on a phone, shown again here
    assert await sheet.is_visible()


async def test_the_starred_list_puts_the_filter_controls_away(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 375)
    await page.click("#starredView")
    for part in ("#filtersOpen", "#onlyNew", "#controlsBar", "#searchBar", SHEET):
        assert await page.locator(part).is_hidden(), part
    await page.click("#starredView")
    for part in ("#filtersOpen", "#onlyNew", "#controlsBar", "#searchBar"):
        assert await page.locator(part).is_visible(), part


async def test_a_notice_and_a_card_have_the_same_border_radius_and_shadow(page: Page, crowd: Path) -> None:
    await open_page(page, crowd, 393)
    styles = (
        "e => { const s = getComputedStyle(e);"
        " return [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, s.borderTopLeftRadius, s.boxShadow]; }"
    )
    card = await page.locator("#list article").first.evaluate(styles)
    gaps = await page.locator("#gaps").evaluate(styles)  # a notice
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
