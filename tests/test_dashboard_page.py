"""The built dashboard (docs/) run in a real browser: it loads from the page's own files under its
Content-Security-Policy, passes axe in both themes, and keeps focus and announcements sensible.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import mimetypes
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
from tests.conftest import add_job, write_json

REPO = Path(__file__).resolve().parents[1]
AXE = REPO / "node_modules" / "axe-core" / "axe.min.js"
ORIGIN = "https://misra.test"
ISRAEL_JOBS = 12
PAGE_SIZE = 10
DEBOUNCE_MS = 600
LONG_NOTE = "A very long note without a break " + "x" * 120
PHONE = {"width": 375, "height": 800}
TAP = 44
GAP_GROUPS = [
    ("Blocks automated access", 1),
    ("Site down, parked or unreachable", 1),
    ("Roles are listed, but we cannot read them yet", 1),
    ("No job feed found yet", 1),
]


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path) -> Path:
    """The real templates and design system built over a small database; returns docs/."""
    shutil.copytree(REPO / "templates", workdir / "templates", dirs_exist_ok=True)
    shutil.copytree(REPO / "design-system", workdir / "design-system")
    write_json(
        workdir / "data" / "companies.json", [{"name": "Acme", "status": "ok"}, {"name": "Nowhere", "status": "x"}]
    )
    (workdir / "data" / "unresolved_candidates.csv").write_text(
        "name,domain,segment,reason,note\r\n"
        "Parked,parked.test,deeptech,site_unusable,Domain parked at GoDaddy\r\n"
        f'LongName,long.test,deeptech,needs_reader,"{LONG_NOTE}"\r\n'
        "Walled,walled.test,multinational,bot_protection,\r\n",
        encoding="utf-8",
    )
    old = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    add_job(db, "Acme", "0", "Accountant", first_seen=old)
    for n in range(1, ISRAEL_JOBS):
        add_job(db, "Acme", str(n), f"Product Manager {n}", first_seen=today)
    add_job(db, "Globex", "1", "Designer", location="London", first_seen=old)
    db.close()
    build_dashboard.main()
    for icon in ("favicon.svg", "apple-touch-icon.png"):  # committed in docs/, not built
        shutil.copy(REPO / "docs" / icon, workdir / "docs" / icon)
    return workdir / "docs"


async def serve(page: Page, docs: Path, *, wait: bool = True) -> list[str]:
    """Serve docs/ at ORIGIN without a network; returns the console errors and page errors seen."""
    problems: list[str] = []

    async def handler(route: Route) -> None:
        name = urlparse(route.request.url).path.lstrip("/") or "index.html"
        file = docs / name
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", handler)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}/index.html")
    if wait:
        await page.wait_for_selector("#list article")
    return problems


async def test_loads_its_files_under_the_csp_and_lists_roles(page: Page, site: Path) -> None:
    problems = await serve(page, site)
    assert problems == []
    assert await page.locator("#list > li").count() == PAGE_SIZE
    assert (
        await page.locator("#summary").inner_text()
        == f"Showing {ISRAEL_JOBS} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    )
    assert await page.locator("#upd").get_attribute("datetime") is not None
    assert await page.locator("#hiring li button.story").count() == 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_has_no_axe_violations(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await serve(page, site)
    await open_gaps(page)  # the card, every group open, is part of the page
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_design_system_showcase_has_no_axe_violations(page: Page, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await serve(page, REPO / "design-system", wait=False)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


async def test_links_that_open_a_new_tab_say_so(page: Page, site: Path) -> None:
    await serve(page, site)
    for link in await page.locator("#list a[target=_blank]").all():
        assert "opens in a new tab" in (await link.text_content() or "")


async def test_paging_moves_focus_to_the_list_heading(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.click("#next")
    assert await page.evaluate("document.activeElement.id") == "list-h"
    assert await page.locator("#pageInfo").inner_text() == "Page 2 of 2"
    assert await page.locator("#list > li").count() == ISRAEL_JOBS - PAGE_SIZE


async def test_search_is_announced_once_typing_pauses(page: Page, site: Path) -> None:
    await serve(page, site)
    assert await page.locator("#status").inner_text() == ""
    await page.locator("#q").press_sequentially("Prod", delay=50)
    await page.locator("#q").press_sequentially("uct", delay=50)
    assert await page.locator("#status").inner_text() == ""
    await expect(page.locator("#status")).not_to_have_text("", timeout=DEBOUNCE_MS * 4)
    assert await page.locator("#status").inner_text() == await page.locator("#summary").inner_text()


async def test_theme_button_names_its_state(page: Page, site: Path) -> None:
    await serve(page, site)
    button = page.locator("#theme")
    assert await button.get_attribute("aria-label") == "Theme: auto"
    await button.click()
    assert await button.get_attribute("aria-label") == "Theme: light"
    assert await page.evaluate("document.documentElement.dataset.theme") == "light"


async def test_a_failed_data_load_says_so(page: Page, site: Path) -> None:
    (site / "jobs.json").unlink()
    problems = await serve(page, site, wait=False)
    await expect(page.locator("#summary")).to_contain_text("Could not load")
    assert any("jobs.json" in p for p in problems)


async def open_gaps(page: Page) -> None:
    await page.evaluate("document.querySelectorAll('#gaps, #gaps details').forEach(d => { d.open = true; })")


async def test_the_coverage_gap_card_counts_the_sites_by_reason(page: Page, site: Path) -> None:
    await serve(page, site)
    assert (
        await page.locator("#gaps-title").inner_text() == "Coverage gaps: 4 companies whose roles may be missing or old"
    )
    shown = []
    for group in await page.locator("#gaps details.gap-group").all():
        label = await group.locator("summary span").first.text_content()
        visible = await group.locator("summary .gap-count").text_content()
        listed = await group.locator("li").count()
        assert (visible, str(listed)) == (await group.get_attribute("data-count"), visible)
        shown.append((label, int(visible)))
    assert shown == GAP_GROUPS
    assert await page.locator("[data-reason=site_unusable] li b").text_content() == "Parked"
    assert await page.locator("[data-reason=site_unusable] li .gap-why").text_content() == "Domain parked at GoDaddy"
    assert await page.locator("[data-reason=bot_protection] li").text_content() == "Walled"


async def test_the_coverage_gap_card_opens_and_closes_and_says_so(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.locator("#gaps-title").click()
    group = page.locator("[data-reason=site_unusable]")
    assert await group.evaluate("d => d.open") is False
    marker = "d => getComputedStyle(d.querySelector('summary'), '::before').content"
    assert await group.evaluate(marker) == '"\u25b8"'
    await group.locator("summary").click()
    assert await group.evaluate("d => d.open") is True
    assert await group.evaluate(marker) == '"\u25be"'


async def test_the_coverage_gap_card_reads_well_on_a_phone(page: Page, site: Path) -> None:
    await page.set_viewport_size(PHONE)
    await serve(page, site)
    await open_gaps(page)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    summaries = await page.locator("#gaps summary").all()
    assert len(summaries) == 1 + len(GAP_GROUPS)
    for summary in summaries:
        box = await summary.bounding_box()
        assert box is not None
        assert box["height"] >= TAP
    note = await page.locator("[data-reason=needs_reader] .gap-why").bounding_box()
    assert note is not None
    assert note["x"] + note["width"] <= PHONE["width"]
