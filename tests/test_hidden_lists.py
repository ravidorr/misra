"""The Hidden view (TODO.md, hidden roles and companies): one place that shows every role the user hid
one by one and every company they hid (also one with no open role), each with a way back, signed out and signed in. It
reads the choices that exist today and stores nothing new. Run in a real browser against the built page."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import (
    AXE,
    ISRAEL_JOBS,
    PHONE,
    TAP_MIN,
    WEBMCP,
    ChoicesBackend,
    build_site,
    serve,
)
from tests.test_job_choices import (
    DISCARDED,
    PM1,
    PM2,
    ask,
    choose_not_for_me,
    company_row,
    discard_row,
    sign_in,
    summary,
    until,
)
from tests.test_sign_in import AXE_RUN, stored


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


COMPANIES = "il-jobs-discarded-companies-v1"
GHOST = "Ghost"  # a hidden company with no open role
GHOST_ROW = "[data-hidden-company=Ghost]"
ACME_ROW = "[data-hidden-company=Acme]"
UNHIDE = "[data-act=unhide-company]"


async def start(page: Page, *, companies: dict[str, str] | None = None, roles: dict[str, str] | None = None) -> None:
    """Signed out with these choices already in this browser."""
    if companies is not None:
        await page.add_init_script(f"localStorage.setItem('{COMPANIES}', {json.dumps(json.dumps(companies))})")
    if roles is not None:
        await page.add_init_script(f"localStorage.setItem('{DISCARDED}', {json.dumps(json.dumps(roles))})")


async def load(page: Page, site: Path) -> None:
    """Serve the page without waiting for a card: a hidden company can leave none to wait for."""
    await serve(page, site, wait=False)
    await page.wait_for_function("document.querySelector('#summary').textContent !== 'Loading roles…'")


async def open_hidden(page: Page) -> None:
    await page.click("#hiddenView")


async def names(page: Page) -> list[str]:
    return await page.locator("#hiddenCompaniesList [data-hidden-company]").evaluate_all(
        "els => els.map(e => e.dataset.hiddenCompany)"
    )


async def label(page: Page) -> str:
    return await page.locator("#hiddenView").inner_text()


# ---- Signed out ----


async def test_signed_out_nothing_hidden_says_how_to_hide_and_the_chip_toggles(page: Page, site: Path) -> None:
    await load(page, site)
    chip = page.locator("#hiddenView")
    assert await label(page) == "Hidden (0)"
    assert await chip.get_attribute("aria-pressed") == "false"
    await open_hidden(page)
    assert await chip.get_attribute("aria-pressed") == "true"
    assert await page.locator("#starredView").get_attribute("aria-pressed") == "false"
    assert await page.locator("#hiddenCompanies").is_hidden()
    assert await page.locator("#hiddenRoles-h").is_hidden()
    assert "Nothing is hidden." in await page.locator("#list > li.empty").inner_text()
    assert await summary(page) == "0 hidden companies, 0 hidden roles. The filters do not apply to this list."
    assert await page.locator("#heroTop").inner_text() == "Hidden"
    for part in ("#moreFilters", "#searchBar", "#hiring", "#missing"):
        assert await page.locator(part).is_hidden()
    await chip.click()  # back to the roles
    assert await chip.get_attribute("aria-pressed") == "false"
    assert await page.locator("#moreFilters").is_visible()
    assert await page.locator("#hiddenCompanies").is_hidden()


async def test_signed_out_the_two_switches_swap_views_and_never_both_show_as_pressed(page: Page, site: Path) -> None:
    await load(page, site)
    await page.click("#starredView")
    await open_hidden(page)
    assert await page.locator("#starredView").get_attribute("aria-pressed") == "false"
    assert await page.locator("#hiddenView").get_attribute("aria-pressed") == "true"
    await page.click("#starredView")
    assert await page.locator("#starredView").get_attribute("aria-pressed") == "true"
    assert await page.locator("#hiddenView").get_attribute("aria-pressed") == "false"
    assert await page.locator("#hiddenCompanies").is_hidden()
    assert await page.locator("#hiddenRoles-h").is_hidden()


async def test_signed_out_a_hidden_company_with_no_open_role_is_listed_and_unhidden_without_a_card(
    page: Page, site: Path
) -> None:
    await start(page, companies={GHOST: "2026-01-02", "Acme": "2026-01-02"})
    await load(page, site)
    assert await label(page) == "Hidden (2)"
    assert await page.locator("article[data-company=Acme]").count() == 0  # the company is hidden in the feed
    await open_hidden(page)
    assert await names(page) == ["Acme", GHOST]  # A to Z
    assert await page.locator("#hiddenCompanies-h").inner_text() == "Hidden companies (2)"
    assert await page.locator(f"{GHOST_ROW} .hidden-company-count").inner_text() == "No open roles"
    assert await page.locator(f"{ACME_ROW} .hidden-company-count").inner_text() == f"{ISRAEL_JOBS} open roles"
    assert await page.locator(f"{GHOST_ROW} {UNHIDE}").get_attribute("aria-label") == "Unhide: Ghost"
    assert await summary(page) == "2 hidden companies, 0 hidden roles. The filters do not apply to this list."
    assert await page.locator("#list > li.empty").inner_text() == "No role is hidden on its own."
    await page.click(f"{GHOST_ROW} {UNHIDE}")
    assert await names(page) == ["Acme"]
    assert json.loads((await stored(page))[COMPANIES]) == {"Acme": "2026-01-02"}
    assert (
        await summary(page)
        == "Unhidden: Ghost. 1 hidden company, 0 hidden roles. The filters do not apply to this list."
    )
    assert await page.evaluate("document.activeElement.id") == "hiddenCompanies-h"  # the focus is not lost
    await page.click(f"{ACME_ROW} {UNHIDE}")  # the last one: the section goes, the focus goes to the list heading
    assert await page.locator("#hiddenCompanies").is_hidden()
    assert await page.evaluate("document.activeElement.id") == "list-h"
    assert await label(page) == "Hidden (0)"
    assert COMPANIES in await stored(page)  # saved as an empty map, nothing else stored
    await page.click("#hiddenView")
    assert await page.locator("article[data-company=Acme]").count() > 0  # its roles are back in the feed


async def test_signed_out_hidden_roles_are_cards_with_restore_and_a_company_hides_its_own_in_the_company_row(
    page: Page, site: Path
) -> None:
    await start(page, companies={"Globex": "2026-01-02"}, roles={"Acme|1": "2026-01-02", "Globex|1": "2026-01-02"})
    await load(page, site)
    assert await label(page) == "Hidden (2)"  # Globex (a company) and Acme|1; Globex|1 is covered by its company
    await open_hidden(page)
    assert await names(page) == ["Globex"]
    assert await page.locator("#hiddenRoles-h").inner_text() == "Hidden roles (1)"
    assert await page.locator("#list article").count() == 1
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await page.locator(f"{PM1} [data-act=toggle]").inner_text() == "Restore"
    await page.click(f"{PM1} [data-act=toggle]")
    assert await page.locator("#list > li.empty").inner_text() == "No role is hidden on its own."
    assert await page.locator("#hiddenRoles-h").is_hidden()
    assert json.loads((await stored(page))[DISCARDED]) == {"Globex|1": "2026-01-02"}
    await page.click("#hiddenView")  # the roles: Acme|1 is back in the feed
    assert await page.locator(PM1).get_attribute("data-discarded") == "false"


async def test_signed_out_hiding_a_company_from_a_card_shows_it_in_the_list_and_a_hidden_role_of_it_stays_off_it(
    page: Page, site: Path
) -> None:
    await load(page, site)
    await choose_not_for_me(page, PM1, "toggle")  # a role of its own
    assert await label(page) == "Hidden (1)"
    await choose_not_for_me(page, PM2, "company")
    assert await label(page) == "Hidden (1)"  # the company covers the role hidden before
    await open_hidden(page)
    assert await names(page) == ["Acme"]
    await page.click(f"{ACME_ROW} {UNHIDE}")
    assert await page.locator("#hiddenCompanies").is_hidden()
    # the role hidden on its own is still hidden: it is now listed as one
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await label(page) == "Hidden (1)"


async def test_signed_out_a_discard_of_a_closed_role_is_not_listed(page: Page, site: Path) -> None:
    await start(page, roles={"Acme|gone": "2026-01-02"})
    await load(page, site)
    assert await label(page) == "Hidden (0)"


async def test_signed_out_a_starred_role_is_not_a_hidden_one(page: Page, site: Path) -> None:
    await start(page, roles={"Acme|1": "2026-01-02"})
    await page.add_init_script(
        "localStorage.setItem('il-jobs-starred-v1', "
        "JSON.stringify({'Acme|1': {c: 'Acme', i: '1', t: 'x', l: '', u: null}}))"
    )
    await load(page, site)
    assert await label(page) == "Hidden (0)"


# ---- Signed in ----


async def test_signed_in_the_list_reads_the_account_and_unhide_is_one_delete(page: Page, site: Path) -> None:
    choices = ChoicesBackend([company_row(GHOST), company_row("Acme"), discard_row("Globex", "1")])
    await sign_in(page, site, choices)
    assert await label(page) == "Hidden (3)"
    await open_hidden(page)
    assert await names(page) == ["Acme", GHOST]
    assert await page.locator("#list article").count() == 1  # Globex|1, hidden on its own
    await page.click(f"{GHOST_ROW} {UNHIDE}")
    await until(lambda: ("Ghost", "") not in choices.rows)
    assert [w["op"] for w in choices.writes] == ["delete"]
    assert choices.state_of("Acme", "") == "company"
    assert await names(page) == ["Acme"]
    assert await label(page) == "Hidden (2)"
    await page.reload()
    await page.wait_for_selector("#list[data-choices=loaded], #list[data-choices=failed]")
    await open_hidden(page)
    assert await names(page) == ["Acme"]  # read back from the account


async def test_signed_in_a_hidden_company_a_hidden_role_and_a_pending_change_show_at_once(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend()
    choices.delay = 0.3  # the write is slow: the list must not wait for it
    await sign_in(page, site, choices)
    await choose_not_for_me(page, PM1, "company")
    assert await label(page) == "Hidden (1)"
    await open_hidden(page)
    assert await names(page) == ["Acme"]
    await page.click(f"{ACME_ROW} {UNHIDE}")
    assert await page.locator("#hiddenCompanies").is_hidden()  # at once, before the service answers
    await until(lambda: choices.rows == {})


async def test_signed_in_a_role_hidden_on_its_own_is_restored_from_the_list(page: Page, site: Path) -> None:
    choices = ChoicesBackend([discard_row("Acme", "1")])
    await sign_in(page, site, choices)
    await open_hidden(page)
    assert await page.locator("#list article").count() == 1
    await page.click(f"{PM1} [data-act=toggle]")
    await until(lambda: choices.rows == {})
    assert await page.locator("#list > li.empty").is_visible()
    assert await label(page) == "Hidden (0)"


# ---- Layout and access ----


async def test_on_a_phone_the_rows_fit_every_control_is_a_target_and_the_page_passes_axe(
    page: Page, site: Path
) -> None:
    await page.set_viewport_size({"width": 320, "height": 800})
    await start(page, companies={GHOST: "2026-01-02", "A long company name that wraps " * 3: "2026-01-02"})
    await load(page, site)
    await open_hidden(page)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    boxes = await page.locator(f"#hiddenCompaniesList {UNHIDE}").evaluate_all(
        "els => els.map(e => e.getBoundingClientRect().height)"
    )
    assert len(boxes) == 2 and min(boxes) >= TAP_MIN  # noqa: PLR2004
    assert (await page.locator("#hiddenView").bounding_box() or {})["height"] >= TAP_MIN
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    for scheme in ("light", "dark"):
        await page.emulate_media(color_scheme=scheme)
        assert await page.evaluate(AXE_RUN) == []
    await page.set_viewport_size(PHONE)


# ---- Assistant tools ----


async def test_the_assistant_lists_hidden_things_and_unhides_a_company(page: Page, site: Path) -> None:
    await start(page, companies={GHOST: "2026-01-02"}, roles={"Acme|1": "2026-01-02"})
    await page.add_init_script(WEBMCP)
    await load(page, site)
    listed: dict[str, Any] = await ask(page, "list_hidden")
    assert listed["companies"] == [{"company": GHOST, "openRoles": 0}]
    assert [r["id"] for r in listed["roles"]] == ["Acme|1"]
    assert listed["roles"][0]["discarded"] is True
    bad = await ask(page, "unhide_company", {"company": "Nobody"})
    assert bad["error"] == "unknown or not hidden company"
    assert await ask(page, "unhide_company", {}) == bad  # no company given
    done = await ask(page, "unhide_company", {"company": GHOST})
    assert done == {"company": GHOST, "saved": True, "failure": None}
    assert (await ask(page, "list_hidden"))["companies"] == []
    assert await label(page) == "Hidden (1)"


async def test_signed_in_a_refused_unhide_puts_the_company_back_and_says_so(page: Page, site: Path) -> None:
    choices = ChoicesBackend([company_row("Acme")], mode="refused")
    await sign_in(page, site, choices)
    await open_hidden(page)
    await page.click(f"{ACME_ROW} {UNHIDE}")
    await expect(page.locator("#summary")).to_contain_text("Could not unhide Acme.")
    assert await names(page) == ["Acme"]  # the service refused: still hidden, and the page does not say otherwise
    assert choices.state_of("Acme", "") == "company"


async def test_signed_in_unhiding_a_company_leaves_a_role_hidden_on_its_own_and_lists_it(
    page: Page, site: Path
) -> None:
    choices = ChoicesBackend([company_row("Acme"), discard_row("Acme", "1")])
    await sign_in(page, site, choices)
    await open_hidden(page)
    assert await page.locator("#list article").count() == 0  # covered by the company row
    await page.click(f"{ACME_ROW} {UNHIDE}")
    await until(lambda: ("Acme", "") not in choices.rows)
    assert choices.state_of("Acme", "1") == "discard"
    assert await page.locator(PM1).get_attribute("data-discarded") == "true"
    assert await page.evaluate("document.activeElement.id") == "hiddenRoles-h"
