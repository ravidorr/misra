# ruff: noqa: F811  (the `site` fixture is imported by name, as in the other browser tests)
"""The Crawl area of the admin page (PLAN_2.md step 2.2), run in a real browser against fake backends. The rows are a
frozen fixture (tests/fixtures/crawl_health.json: 30 companies, a Hebrew name, a very long one, null values); no real
account, credential or crawl data is used. Locally the tests skip when Chromium is missing; in CI a missing browser is
a failure."""

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import Locator, Page, expect

from tests.test_admin_page import (
    AXE,
    AXE_RUN,
    DESKTOP,
    PHONE,
    POLL_SECONDS,
    TAP_MIN,
    Backend,
    open_admin,
    site,  # noqa: F401  (a fixture)
    state,
    visible_text,
)

ROWS: list[dict[str, object]] = json.loads(
    (Path(__file__).parent / "fixtures" / "crawl_health.json").read_text(encoding="utf-8")
)
LONG = (
    "Internationally Distributed Multinational Telecommunications "
    + "Infrastructure Holdings Corporation of Greater Tel Aviv"
)
HEBREW = "חברת בדיקה"
# worst first: failing by the longest streak, then held, then skipped for robots.txt (each by name), then the rest
ATTENTION_ORDER = [
    LONG,
    "Acme Robotics",
    HEBREW,
    "Beta Labs",
    "Delta Security",
    "Gamma Cloud",
    "Epsilon Data",
    "Zeta AI",
]
TOTAL = 30
FAILING, HELD, ROBOTS = 4, 2, 2
FINE = TOTAL - FAILING - HELD - ROBOTS
CHIPS = {
    "attention": f"Needs attention ({FAILING + HELD + ROBOTS})",
    "failing": f"Failing ({FAILING})",
    "robots": f"Robots.txt ({ROBOTS})",
    "held": f"Held ({HELD})",
    "all": f"All ({TOTAL})",
}
HEADLINE = f"{FAILING + HELD + ROBOTS} need attention, {FINE} fine."
CALL = ("POST", "/rest/v1/rpc/admin_crawl_health")
CHECK = ("POST", "/rest/v1/rpc/is_admin")
SETTLE_SECONDS = 0.3
CALLS_WHEN_REFUSED_TWICE = 2  # refused, the check again, refused again


async def open_crawl(
    page: Page, docs: Path, backend: Backend | None = None, **kwargs: object
) -> tuple[Backend, list[str]]:
    return await open_admin(page, docs, backend=backend or Backend(health=ROWS), **kwargs)  # type: ignore[arg-type]


async def names(page: Page) -> list[str]:
    return await page.locator(".crawl-card .crawl-name").all_inner_texts()


def card(page: Page, company: str) -> Locator:
    return page.locator(".crawl-card").filter(has=page.get_by_role("heading", name=company, exact=True))


async def facts(page: Page, company: str) -> dict[str, str]:
    groups = await card(page, company).locator(".crawl-fact").all()
    return {(await g.locator("dt").inner_text()): (await g.locator("dd").inner_text()) for g in groups}


def chip(page: Page, view: str) -> Locator:
    return page.locator(f".chip[data-view='{view}']")


# ---- what it asks and when ----


async def test_nothing_but_the_check_is_asked_before_the_answer_and_the_call_names_no_column(
    page: Page, site: Path
) -> None:
    backend = Backend(health=ROWS)
    backend.gate = asyncio.Event()
    await open_crawl(page, site, backend, wait=False)
    while backend.asked == 0:
        await asyncio.sleep(POLL_SECONDS)
    await asyncio.sleep(SETTLE_SECONDS)
    assert backend.requests == [CHECK]  # the Crawl area waits for the admin check
    assert backend.health_asked == 0
    backend.gate.set()
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "ready")
    assert backend.requests == [CHECK, CALL]
    assert backend.health_bodies in ([None], [""], ["{}"])  # no argument, no columns, no filter: the function decides


async def test_a_non_admin_never_loads_the_area(page: Page, site: Path) -> None:
    backend, _problems = await open_crawl(page, site, Backend("not_admin", health=ROWS))
    assert await state(page) == "not-admin"
    assert backend.requests == [CHECK]
    assert await page.locator(".crawl").count() == 0


async def test_the_crawl_loads_on_its_first_show_only(page: Page, site: Path) -> None:
    backend, _problems = await open_crawl(page, site, hash_="#companies")
    assert backend.health_asked == 0  # Crawl is not shown yet: it has not loaded
    await page.get_by_role("link", name="Crawl").click()
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "ready")
    assert backend.health_asked == 1
    await page.get_by_role("link", name="Companies").click()
    await page.get_by_role("link", name="Crawl").click()
    assert backend.health_asked == 1  # shown again, not loaded again


# ---- ready ----


async def test_ready_shows_the_headline_the_chips_with_counts_and_the_companies_that_need_attention(
    page: Page, site: Path
) -> None:
    _backend, problems = await open_crawl(page, site)
    assert problems == []
    await expect(page.locator(".crawl-headline")).to_have_text(HEADLINE)
    for view, label in CHIPS.items():
        await expect(chip(page, view)).to_have_text(label)
    assert await page.get_by_role("group", name="Show").get_by_role("button").count() == len(CHIPS)
    await expect(chip(page, "attention")).to_have_attribute("aria-pressed", "true")
    for view in ("failing", "robots", "held", "all"):
        await expect(chip(page, view)).to_have_attribute("aria-pressed", "false")
    assert await names(page) == ATTENTION_ORDER
    assert await page.locator(".crawl-card").count() == FAILING + HELD + ROBOTS  # only the view is in the DOM
    await expect(page.get_by_role("status")).to_have_text(f"Showing 8 of {TOTAL} companies")
    text = await visible_text(page)
    assert "20 Nov 2026" in text  # the note about the short log
    assert "A failed crawl fetches 0." in text


async def test_a_company_is_one_card_with_a_heading_a_status_and_its_values(page: Page, site: Path) -> None:
    await open_crawl(page, site)
    acme = card(page, "Acme Robotics")
    assert await acme.get_attribute("data-status") == "failing"
    assert await acme.locator(".pill").inner_text() == "Failing"
    assert await acme.get_by_role("heading", level=3).get_attribute("dir") == "auto"
    values = await facts(page, "Acme Robotics")
    assert values["Failed days in a row"] == "5"
    assert values["Open jobs now"] == "3"
    assert values["Fetched about 30 days ago"] == "-"  # no log row 30 to 37 days ago
    assert values["Note"] == "timeout"
    assert values["Last result"].endswith(", failed")
    assert "Nov 12" in values["Last good crawl"]
    assert "Nov 17" in values["Last result"]
    assert (await facts(page, "Beta Labs"))["Fetched about 30 days ago"] == "9"
    hebrew = await facts(page, HEBREW)
    assert hebrew["Last good crawl"] == "None in the log"
    assert hebrew["Fetched about 30 days ago"] == "0"  # zero is a number, not "no data"
    for company, status, pill in [("Gamma Cloud", "held", "Held"), ("Zeta AI", "robots", "Robots.txt")]:
        assert await card(page, company).get_attribute("data-status") == status
        assert await card(page, company).locator(".pill").inner_text() == pill
    assert (await facts(page, "Gamma Cloud"))["Last result"].endswith(", OK")


async def test_no_raw_timestamp_is_shown(page: Page, site: Path) -> None:
    await open_crawl(page, site)
    await chip(page, "all").click()
    text = await visible_text(page)
    assert "2026-11" not in text
    assert "T06:" not in text


async def test_the_year_shows_when_it_is_not_this_year_and_an_odd_stamp_is_shown_as_it_is(
    page: Page, site: Path
) -> None:
    rows = [
        {**ROWS[0], "company": "Old", "last_crawl": "2020-03-02T06:00:00", "last_good": "2020-03-02T06:00:00"},
        {**ROWS[0], "company": "Odd", "last_good": "yesterday-ish"},
    ]
    await open_crawl(page, site, Backend(health=rows))
    assert (await facts(page, "Old"))["Last good crawl"] == "Mar 2, 2020"
    assert (await facts(page, "Odd"))["Last good crawl"] == "yesterday-ish"


async def test_the_headline_says_one_needs_and_none_need(page: Page, site: Path) -> None:
    some = [row for row in ROWS if row["company"] in {"Acme Robotics", "Company 01", "Company 02"}]
    await open_crawl(page, site, Backend(health=some))
    await expect(page.locator(".crawl-headline")).to_have_text("1 needs attention, 2 fine.")
    await expect(page.get_by_role("status")).to_have_text("Showing 1 of 3 companies")


async def test_with_nothing_to_attend_to_the_view_says_so_and_all_lists_everyone(page: Page, site: Path) -> None:
    fine = [row for row in ROWS if str(row["company"]).startswith("Company")]
    await open_crawl(page, site, Backend(health=fine))
    await expect(page.locator(".crawl-headline")).to_have_text(f"None need attention, {len(fine)} fine.")
    await expect(page.locator(".crawl-none")).to_have_text("No companies in this view.")
    assert await page.locator(".crawl-list").is_hidden()
    await page.get_by_role("button", name=f"All ({len(fine)})").click()
    assert await page.locator(".crawl-none").is_hidden()
    assert await page.locator(".crawl-card").count() == len(fine)


async def test_one_company_counts_once_even_if_the_function_flags_it_twice(page: Page, site: Path) -> None:
    """The function keeps the three apart; the page does not rely on it: held wins, then robots.txt, then failing."""
    both = [{**ROWS[0], "company": "Both", "failing": True, "held": True, "robots_skipped": True}]
    await open_crawl(page, site, Backend(health=both))
    await expect(page.locator(".crawl-headline")).to_have_text("1 needs attention, 0 fine.")
    await expect(chip(page, "held")).to_have_text("Held (1)")
    await expect(chip(page, "failing")).to_have_text("Failing (0)")
    await expect(chip(page, "robots")).to_have_text("Robots.txt (0)")


# ---- the views ----


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        ("failing", [LONG, "Acme Robotics", HEBREW, "Beta Labs"]),
        ("held", ["Delta Security", "Gamma Cloud"]),
        ("robots", ["Epsilon Data", "Zeta AI"]),
    ],
)
async def test_a_chip_shows_only_its_companies(page: Page, site: Path, view: str, expected: list[str]) -> None:
    await open_crawl(page, site)
    await chip(page, view).click()
    assert await names(page) == expected
    await expect(chip(page, view)).to_have_attribute("aria-pressed", "true")
    await expect(chip(page, "attention")).to_have_attribute("aria-pressed", "false")
    await expect(page.get_by_role("status")).to_have_text(f"Showing {len(expected)} of {TOTAL} companies")


async def test_all_lists_the_healthy_companies_after_the_others_each_by_name(page: Page, site: Path) -> None:
    await open_crawl(page, site)
    await chip(page, "all").click()
    listed = await names(page)
    assert len(listed) == TOTAL
    assert listed[:8] == ATTENTION_ORDER
    assert listed[8:] == [f"Company {i:02d}" for i in range(1, 23)]
    assert await card(page, "Company 05").get_attribute("data-status") == "ok"
    assert await card(page, "Company 05").locator(".pill").inner_text() == "OK"
    assert (await facts(page, "Company 05"))["Note"] == "3 new"
    assert "Note" not in await facts(page, "Company 01")  # no note, no row
    assert (await facts(page, "Company 07"))["Fetched about 30 days ago"] == "-"
    await chip(page, "attention").click()
    assert await names(page) == ATTENTION_ORDER  # and back


async def test_a_filter_keeps_the_focus_on_its_chip_and_announces_the_count(page: Page, site: Path) -> None:
    await open_crawl(page, site)
    button = chip(page, "held")
    await button.focus()
    await page.keyboard.press("Enter")
    await expect(button).to_be_focused()
    await expect(page.get_by_role("status")).to_have_text(f"Showing {HELD} of {TOTAL} companies")
    await page.keyboard.press("Space")  # pressing the pressed chip again keeps the view
    await expect(button).to_be_focused()
    assert await names(page) == ["Delta Security", "Gamma Cloud"]
    assert await page.get_by_role("status").count() == 1  # one live region, not one per card


async def test_the_keyboard_reaches_every_chip_in_order(page: Page, site: Path) -> None:
    await open_crawl(page, site)
    await chip(page, "attention").focus()
    for view in ("failing", "robots", "held", "all"):
        await page.keyboard.press("Tab")
        await expect(chip(page, view)).to_be_focused()
    await page.keyboard.press("Space")
    assert await page.locator(".crawl-card").count() == TOTAL


async def test_a_long_name_and_a_hebrew_name_stay_inside_the_card(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)  # type: ignore[arg-type]
    await open_crawl(page, site)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    inside = await page.evaluate(
        """[...document.querySelectorAll('.crawl-card')].map(c => {
            const r = c.getBoundingClientRect();
            return [...c.querySelectorAll('*')].every(e => e.getBoundingClientRect().right <= r.right + 0.5);
        })"""
    )
    assert all(inside)


# ---- the area's own states ----


@pytest.mark.parametrize("rows", [[], None], ids=["empty list", "null"])
async def test_no_rows_is_an_empty_area_not_an_error(page: Page, site: Path, rows: list[object] | None) -> None:
    backend = Backend()
    backend.health = rows  # type: ignore[assignment]  (a JSON null)
    _backend, problems = await open_crawl(page, site, backend)
    assert problems == []
    assert await page.locator("#crawl").get_attribute("data-state") == "empty"
    assert await page.locator("#crawl .admin-state").inner_text() == "No crawl results yet."
    assert await page.locator(".crawl").count() == 0


async def test_loading_is_plain_text_in_a_status_and_the_ready_area_is_not_a_status(page: Page, site: Path) -> None:
    backend = Backend(health=ROWS)
    backend.health_gate = asyncio.Event()
    await open_crawl(page, site, backend)
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "loading")
    await expect(page.get_by_role("status")).to_have_text("Loading…")
    backend.health_gate.set()
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "ready")
    assert await page.locator("#crawl .admin-state").get_attribute("role") is None
    await expect(page.get_by_role("status")).to_have_count(1)  # the one that counts the view
    await expect(page.get_by_role("status")).to_contain_text("Showing")


async def test_an_error_says_so_and_try_again_loads_it(page: Page, site: Path) -> None:
    backend = Backend(health=ROWS, health_answer="broken_once")
    await open_crawl(page, site, backend)
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "error")
    await expect(page.get_by_role("alert")).to_contain_text("Could not load this area.")
    assert await page.locator(".crawl").count() == 0
    await page.get_by_role("button", name="Try again").click()
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "ready")
    assert await names(page) == ATTENTION_ORDER
    await expect(page.get_by_role("heading", name="Crawl", level=2)).to_be_focused()
    assert backend.health_asked == CALLS_WHEN_REFUSED_TWICE


async def test_an_answer_that_is_not_a_list_is_an_error(page: Page, site: Path) -> None:
    await open_crawl(page, site, Backend(health_answer="not_a_list"))
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "error")
    await expect(page.get_by_role("alert")).to_contain_text("Could not load this area.")


# ---- the 401 path ----


async def test_a_refused_token_runs_the_admin_check_again_and_then_loads_the_rows(page: Page, site: Path) -> None:
    backend, _problems = await open_crawl(page, site, Backend(health=ROWS, health_answer="unauthorized_once"))
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "ready")
    assert await state(page) == "ready"
    assert backend.health_asked == CALLS_WHEN_REFUSED_TWICE  # refused, the check again, then the rows
    assert backend.asked == CALLS_WHEN_REFUSED_TWICE
    assert await names(page) == ATTENTION_ORDER


async def test_a_token_refused_twice_is_an_area_error_and_does_not_loop(page: Page, site: Path) -> None:
    backend, _problems = await open_crawl(page, site, Backend(health=ROWS, health_answer="unauthorized"))
    await expect(page.locator("#crawl")).to_have_attribute("data-state", "error")
    assert backend.health_asked == CALLS_WHEN_REFUSED_TWICE
    assert backend.asked == CALLS_WHEN_REFUSED_TWICE


async def test_a_refused_token_and_a_check_that_says_not_admin_leaves_the_page_not_admin(
    page: Page, site: Path
) -> None:
    backend = Backend(health=ROWS, health_answer="unauthorized", recheck_answer="not_admin")
    await open_crawl(page, site, backend)
    await expect(page.locator("main")).to_have_attribute("data-state", "not-admin")
    assert await page.locator(".crawl:visible").count() == 0
    assert backend.health_asked == 1


async def test_a_refused_token_and_a_check_that_cannot_be_made_says_so(page: Page, site: Path) -> None:
    await open_crawl(page, site, Backend(health=ROWS, health_answer="unauthorized", recheck_answer="broken"))
    await expect(page.locator("main")).to_have_attribute("data-state", "check-failed")


async def test_a_refused_token_and_an_expired_session_says_signed_out(page: Page, site: Path) -> None:
    await open_crawl(page, site, Backend(health=ROWS, health_answer="unauthorized", recheck_answer="unauthorized"))
    await expect(page.locator("main")).to_have_attribute("data-state", "signed-out")
    await expect(page.locator("#adminExpired")).to_have_text("Your sign-in expired.")


# ---- the same checks the other states pass ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("which", ["attention", "all", "empty", "error"])
async def test_the_crawl_area_is_accessible_fits_the_phone_and_has_44px_targets(
    page: Page, site: Path, which: str, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    backend = Backend(health=[] if which == "empty" else ROWS, health_answer="broken" if which == "error" else "rows")
    _backend, problems = await open_crawl(page, site, backend)
    if which == "all":
        await chip(page, "all").click()
    assert await state(page) == "ready"
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    if which != "error":  # a failed load is logged to the console on purpose
        assert problems == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("main a:visible, main button:visible").all():
        box = await target.bounding_box()
        assert box
        assert box["height"] >= TAP_MIN, await target.inner_text()
        assert box["width"] >= TAP_MIN, await target.inner_text()
