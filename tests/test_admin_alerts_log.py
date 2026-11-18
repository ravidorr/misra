# ruff: noqa: F811  (the `site` fixture is imported by name, as in the other browser tests)
"""The Alerts and Log areas of the admin page (PLAN_2.md steps 5.1, 5.3 and 1.3), run in a real browser against fake
backends: `admin_alert_counts()` and `admin_log`, answered from frozen fixtures (tests/fixtures/admin_alert_counts.json,
tests/fixtures/admin_log.json: invented numbers and company names, a Hebrew name, a very long one, markup in a name, a
null target). Any other request to the project is refused and recorded. No real account or data is used. Locally the
tests skip when Chromium is missing; in CI a missing browser is a failure."""

import json
import re
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

from tests.test_admin_page import (
    AXE,
    AXE_RUN,
    DESKTOP,
    PHONE,
    TAP_MIN,
    Backend,
    open_admin,
    site,  # noqa: F401  (a fixture)
    state,
)

FIXTURES = Path(__file__).parent / "fixtures"
COUNTS: list[dict[str, object]] = json.loads((FIXTURES / "admin_alert_counts.json").read_text(encoding="utf-8"))
LOG: list[dict[str, object]] = json.loads((FIXTURES / "admin_log.json").read_text(encoding="utf-8"))
LONG = (
    "Internationally Distributed Multinational Telecommunications "
    + "Infrastructure Holdings Corporation of Greater Tel Aviv"
)
HEBREW = "חברת בדיקה"
MARKUP = "<b>Markup</b> & Co"
CAP = 50
CHECK = ("POST", "/rest/v1/rpc/is_admin")
CALL = ("POST", "/rest/v1/rpc/admin_alert_counts")
READ = ("GET", "/rest/v1/admin_log")
CALLS_WHEN_REFUSED_TWICE = 2  # refused, the check again, refused again
SUBSCRIPTIONS = {
    "Confirmed": "12",
    "Waiting for confirmation": "3",
    "Turned off": "5",
}
EMAILED = ["4 people", "0 people", "11 people", "9 people", "0 people", "12 people", "2 people"]
DAY = re.compile(
    r"Nov 1[2-8](, 2026)?|1[2-8] Nov( 2026)?"
)  # the year shows when it is not this year; the order is the locale's


async def open_area(page: Page, docs: Path, area: str, backend: Backend, **kwargs: object) -> tuple[Backend, list[str]]:
    opened = await open_admin(page, docs, backend=backend, hash_=f"#{area}", **kwargs)  # type: ignore[arg-type]
    if kwargs.get("wait", True) and backend.gate is None:
        await expect(page.locator(f"#{area}")).not_to_have_attribute("data-state", "loading")
    return opened


async def card_facts(page: Page, title: str) -> list[tuple[str, str]]:
    card = page.locator("#alerts .crawl-card").filter(has=page.get_by_role("heading", name=title, exact=True))
    await expect(card).to_be_visible()
    return [
        (await g.locator("dt").inner_text(), await g.locator("dd").inner_text())
        for g in await card.locator(".crawl-fact").all()
    ]


# ---- Alerts ----


async def test_alerts_asks_only_the_one_function_after_the_check_and_only_on_its_first_show(
    page: Page, site: Path
) -> None:
    backend, problems = await open_area(page, site, "crawl", Backend(alerts=COUNTS))
    assert backend.alerts_asked == 0  # not shown yet: not loaded
    await page.get_by_role("link", name="Alerts").click()
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "ready")
    await page.get_by_role("link", name="Log").click()
    await page.get_by_role("link", name="Alerts").click()
    assert backend.alerts_asked == 1  # shown again, not loaded again
    assert backend.requests.count(CALL) == 1
    assert backend.alerts_bodies in ([None], [""], ["{}"])  # no argument: the function decides what it counts
    assert backend.strays == []
    assert problems == []


async def test_alerts_shows_the_counts_and_nothing_to_type_into_or_press(page: Page, site: Path) -> None:
    backend, problems = await open_area(page, site, "alerts", Backend(alerts=COUNTS))
    assert problems == []
    await expect(page.locator(".alerts .crawl-headline")).to_have_text("12 confirmed, 3 waiting, 5 off.")
    assert await card_facts(page, "Job alerts") == list(SUBSCRIPTIONS.items())
    emailed = await card_facts(page, "Emailed in the last 7 days")
    assert [value for _day, value in emailed] == EMAILED  # a day nobody was emailed says 0, it is not left out
    assert all(DAY.fullmatch(day) for day, _value in emailed)
    await expect(page.locator("#alerts .crawl-note")).to_have_text(re.compile(r"however many jobs"))
    # read-only: the two forms of step 5.3 come later, and nothing stands in for them
    controls = "#alerts input, #alerts select, #alerts textarea, #alerts form, #alerts button"
    assert await page.locator(controls).count() == 0
    text = await page.locator("#alerts").inner_text()
    assert "@" not in text
    assert backend.strays == []


async def test_alerts_says_so_when_the_function_returns_nothing(page: Page, site: Path) -> None:
    await open_area(page, site, "alerts", Backend(alerts=[]))
    assert await state(page) == "ready"
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "empty")
    await expect(page.locator("#alerts .admin-state")).to_have_text("No counts yet.")


async def test_a_failed_alerts_load_is_an_error_with_try_again_that_works(page: Page, site: Path) -> None:
    backend = Backend(alerts=COUNTS, alerts_answer="broken_once")
    await open_area(page, site, "alerts", backend)
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "error")
    await expect(page.get_by_role("alert")).to_contain_text("Could not load this area.")
    assert "12 people" not in await page.locator("#alerts").inner_text()
    await page.get_by_role("button", name="Try again").click()
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "ready")
    await expect(page.get_by_role("heading", name="Alerts", level=2)).to_be_focused()
    assert backend.alerts_asked == CALLS_WHEN_REFUSED_TWICE


async def test_an_alerts_answer_that_is_not_a_list_is_an_error(page: Page, site: Path) -> None:
    await open_area(page, site, "alerts", Backend(alerts_answer="not_a_list"))
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "error")


async def test_a_refused_token_runs_the_admin_check_again_and_then_loads_the_counts(page: Page, site: Path) -> None:
    backend = Backend(alerts=COUNTS, alerts_answer="unauthorized_once")
    await open_area(page, site, "alerts", backend)
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "ready")
    assert backend.alerts_asked == CALLS_WHEN_REFUSED_TWICE
    assert backend.asked == CALLS_WHEN_REFUSED_TWICE  # the check, then the check again


async def test_alerts_with_a_token_refused_twice_is_an_error_and_does_not_loop(page: Page, site: Path) -> None:
    backend = Backend(alerts=COUNTS, alerts_answer="unauthorized")
    await open_area(page, site, "alerts", backend)
    await expect(page.locator("#alerts")).to_have_attribute("data-state", "error")
    assert backend.alerts_asked == CALLS_WHEN_REFUSED_TWICE


async def test_a_non_admin_never_loads_alerts_or_the_log(page: Page, site: Path) -> None:
    backend, _problems = await open_area(page, site, "alerts", Backend("not_admin", alerts=COUNTS, log=LOG))
    assert await state(page) == "not-admin"
    assert backend.requests == [CHECK]
    assert await page.locator(".alerts, .log").count() == 0


# ---- Log ----


async def test_log_asks_for_the_last_fifty_newest_first_with_the_columns_it_shows(page: Page, site: Path) -> None:
    backend, problems = await open_area(page, site, "crawl", Backend(log=LOG))
    assert backend.log_asked == 0  # Crawl is shown: the log has not loaded
    await page.get_by_role("link", name="Log").click()
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    assert backend.log_asked == 1
    [query] = backend.log_queries
    assert query["select"] == ["id,ts,action,target"]
    assert query["limit"] == [str(CAP)]
    assert query["order"] == ["ts.desc,id.desc"]  # the two keys, in this order
    assert backend.strays == []
    assert problems == []


async def test_log_lists_the_last_fifty_in_plain_sentences_and_says_how_many(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=LOG))
    await expect(page.locator("#log .crawl-count")).to_have_text(f"Showing the last {CAP} changes.")
    rows = page.locator("#log .log-row")
    assert await rows.count() == CAP
    what = await page.locator("#log .log-what").all_inner_texts()
    assert what[0] == "Company added: Newest Co"  # newest first
    assert what[1] == "Something else"  # an action without a sentence is shown as it is, and a null target adds nothing
    assert {re.sub(r": .*", "", w) for w in what[2:]} <= {"Company added", "Company changed", "Company removed"}
    assert "company added" not in " ".join(what)  # no raw action text for the known ones
    stamps = [await t.get_attribute("datetime") for t in await page.locator("#log time").all()]
    assert stamps[0] == "2026-11-18T12:00:00.000Z"
    assert stamps == sorted(stamps, reverse=True)
    assert all(await page.locator("#log time").all_inner_texts())


async def test_log_shows_a_name_as_text_in_its_own_direction(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=LOG))
    text = await page.locator("#log").inner_text()
    assert HEBREW in text
    assert MARKUP in text  # shown as typed, never as markup
    assert await page.locator("#log b").count() == 0
    assert LONG in text
    assert await page.locator("#log .log-target[dir='auto']").count() >= CAP - 2


async def test_a_short_log_counts_its_changes_and_a_single_one_is_singular(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=LOG[-3:]))
    await expect(page.locator("#log .crawl-count")).to_have_text("3 changes.")
    assert await page.locator("#log .log-row").count() == 3  # noqa: PLR2004


async def test_one_change_reads_in_the_singular(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=LOG[-1:]))
    await expect(page.locator("#log .crawl-count")).to_have_text("1 change.")


async def test_a_log_that_holds_exactly_the_cap_says_showing_the_last(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=LOG[-CAP:]))
    await expect(page.locator("#log .crawl-count")).to_have_text(f"Showing the last {CAP} changes.")


async def test_an_empty_log_says_no_changes_yet(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log=[]))
    await expect(page.locator("#log")).to_have_attribute("data-state", "empty")
    await expect(page.locator("#log .admin-state")).to_have_text("No changes yet.")


async def test_a_failed_log_load_is_an_error_with_try_again_that_works(page: Page, site: Path) -> None:
    backend = Backend(log=LOG, log_answer="broken_once")
    await open_area(page, site, "log", backend)
    await expect(page.locator("#log")).to_have_attribute("data-state", "error")
    await expect(page.get_by_role("alert")).to_contain_text("Could not load this area.")
    assert await page.locator("#log .log-row").count() == 0
    await page.get_by_role("button", name="Try again").click()
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    await expect(page.get_by_role("heading", name="Log", level=2)).to_be_focused()
    assert backend.log_asked == CALLS_WHEN_REFUSED_TWICE


async def test_a_log_answer_that_is_not_a_list_is_an_error(page: Page, site: Path) -> None:
    await open_area(page, site, "log", Backend(log_answer="not_a_list"))
    await expect(page.locator("#log")).to_have_attribute("data-state", "error")


async def test_a_refused_token_runs_the_admin_check_again_and_then_loads_the_log(page: Page, site: Path) -> None:
    backend = Backend(log=LOG, log_answer="unauthorized_once")
    await open_area(page, site, "log", backend)
    await expect(page.locator("#log")).to_have_attribute("data-state", "ready")
    assert backend.log_asked == CALLS_WHEN_REFUSED_TWICE
    assert backend.asked == CALLS_WHEN_REFUSED_TWICE


async def test_a_log_token_refused_twice_is_an_error_and_does_not_loop(page: Page, site: Path) -> None:
    backend = Backend(log=LOG, log_answer="unauthorized")
    await open_area(page, site, "log", backend)
    await expect(page.locator("#log")).to_have_attribute("data-state", "error")
    assert backend.log_asked == CALLS_WHEN_REFUSED_TWICE


# ---- the same checks the other areas pass ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("which", ["alerts", "log", "alerts-error", "log-error", "log-empty"])
async def test_the_alerts_and_log_areas_are_accessible_fit_the_phone_and_have_44px_targets(
    page: Page, site: Path, which: str, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    area = which.split("-", maxsplit=1)[0]
    backend = Backend(
        alerts=COUNTS,
        log=[] if which == "log-empty" else LOG,
        alerts_answer="broken" if which == "alerts-error" else "rows",
        log_answer="broken" if which == "log-error" else "rows",
    )
    _backend, problems = await open_area(page, site, area, backend)
    assert await state(page) == "ready"
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    if not which.endswith("error"):  # a failed load is logged to the console on purpose
        assert problems == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for target in await page.locator("main a:visible, main button:visible").all():
        box = await target.bounding_box()
        assert box
        assert box["height"] >= TAP_MIN, await target.inner_text()
        assert box["width"] >= TAP_MIN, await target.inner_text()
