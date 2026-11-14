"""The built dashboard (docs/) run in a real browser: it loads from the page's own files under its
Content-Security-Policy, passes axe in both themes, and keeps focus and announcements sensible.

Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import json
import mimetypes
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import Page, Route, expect

import build_dashboard
import sync_supabase
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
REST_PAGE = 1000
REQUESTS_FOR_TOTAL = 3  # 2,345 rows at 1,000 a request
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-expose-headers": "Content-Range",
}
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
    write_json(workdir / "rows.json", list(sync_supabase.open_rows(db)))
    db.close()
    build_dashboard.main()
    for icon in ("favicon.svg", "apple-touch-icon.png"):  # committed in docs/, not built
        shutil.copy(REPO / "docs" / icon, workdir / "docs" / icon)
    return workdir / "docs"


def table_rows(count: int) -> list[dict[str, Any]]:
    """`count` open Israeli jobs of one company, as the jobs table holds them."""
    return [
        {
            "company": "Bulk",
            "job_id": f"{n:05d}",
            "title": f"Role {n}",
            "team": None,
            "location": "Haifa, Israel",
            "url": "https://x.test/j",
            "first_seen": "2026-01-02T00:00:00",
            "level": "Not specified",
            "role_types": ["Other"],
            "regions": ["Haifa and the north"],
            "locations": [],
        }
        for n in range(count)
    ]


class Backend:
    """A stand-in for the project's PostgREST endpoint: the rows of the jobs table, a thousand at a time, with the
    total in Content-Range, and the requests it saw. `mode` breaks it: down, error, no_range or short."""

    def __init__(self, rows: list[dict[str, Any]], mode: str = "ok") -> None:
        self.rows = rows
        self.mode = mode
        self.requests: list[Any] = []

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            await route.fulfill(status=204, headers=CORS)
            return
        self.requests.append(request)
        query = parse_qs(urlparse(request.url).query)
        offset, limit = int(query["offset"][0]), min(int(query["limit"][0]), REST_PAGE)
        page = self.rows[offset : offset + limit]
        total = len(self.rows) - (1 if self.mode == "short" else 0)
        headers = {**CORS, "content-range": f"{offset}-{offset + len(page) - 1}/{total}"}
        key = build_dashboard.SUPABASE_ANON_KEY
        authorised = request.headers.get("apikey") == key and request.headers.get("authorization") == f"Bearer {key}"
        if self.mode == "down":
            await route.abort()
        elif not authorised:
            await route.fulfill(status=401, headers=CORS, body="no key")
        elif self.mode == "error" or (self.mode == "later_page" and offset > 0):
            await route.fulfill(status=503, headers=CORS, body="unavailable")
        else:
            if self.mode == "no_range":
                del headers["content-range"]
            await route.fulfill(body=json.dumps(page), content_type="application/json", headers=headers)


async def serve(page: Page, docs: Path, *, wait: bool = True, backend: Backend | None = None) -> list[str]:
    """Serve docs/ at ORIGIN without a network and the jobs from a fake backend; returns the console errors and page
    errors seen. The backend defaults to the rows of the site fixture."""
    problems: list[str] = []
    rows_file = docs.parent / "rows.json"
    if backend is None:
        backend = Backend(json.loads(rows_file.read_text(encoding="utf-8")) if rows_file.exists() else [])

    async def handler(route: Route) -> None:
        name = urlparse(route.request.url).path.lstrip("/") or "index.html"
        file = docs / name
        if file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404)

    await page.route(f"{ORIGIN}/**", handler)
    await page.route(f"{build_dashboard.SUPABASE_URL}/**", backend.handle)
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


async def test_the_jobs_are_read_from_the_backend_with_the_anon_key(page: Page, site: Path) -> None:
    backend = Backend(json.loads((site.parent / "rows.json").read_text(encoding="utf-8")))
    assert await serve(page, site, backend=backend) == []
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.headers["apikey"] == build_dashboard.SUPABASE_ANON_KEY
    assert request.headers["authorization"] == f"Bearer {build_dashboard.SUPABASE_ANON_KEY}"
    assert request.headers["prefer"] == "count=exact"
    assert "status=eq.open" in request.url
    assert (site / "jobs.json").exists()  # published for download, never read by the page


async def test_every_job_of_the_table_is_loaded_across_the_row_limit(page: Page, site: Path) -> None:
    total = 2 * REST_PAGE + 345
    backend = Backend(table_rows(total))
    await serve(page, site, backend=backend)
    assert len(backend.requests) == REQUESTS_FOR_TOTAL
    assert [r.headers.get("prefer") for r in backend.requests] == ["count=exact", None, None]
    summary = await page.locator("#summary").inner_text()
    assert summary == f"Showing {total} of {total} open roles in Israel. 0 discarded."


async def test_filters_work_on_the_loaded_jobs(page: Page, site: Path) -> None:
    await serve(page, site)
    await page.click("[data-cat=pm]")
    assert await page.locator("#summary").inner_text() == (
        f"Showing {ISRAEL_JOBS - 1} of {ISRAEL_JOBS} open roles in Israel. 0 discarded."
    )
    await page.click("[data-cat=all]")
    await page.select_option("#loc", "all")
    assert "13 open roles" in await page.locator("#summary").inner_text()
    await page.select_option("#company", "Globex")
    assert await page.locator("#list > li").count() == 1


@pytest.mark.parametrize("mode", ["down", "error", "no_range", "short", "later_page"])
async def test_an_unreachable_or_incomplete_backend_shows_an_error_card(page: Page, site: Path, mode: str) -> None:
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    extra = table_rows(REST_PAGE) if mode == "later_page" else []  # more than one page, so a later one can fail
    await serve(page, site, wait=False, backend=Backend(rows + extra, mode))
    card = page.locator("#load-error")
    await expect(card).to_be_visible()
    assert await card.get_attribute("role") == "alert"
    assert await card.locator("h2").inner_text() == "The roles could not be loaded"
    assert await page.locator("#summary").inner_text() == "Could not load the roles."
    assert await page.locator("#list > li").count() == 0
    box = await card.locator("#retry").bounding_box()
    assert box is not None
    assert box["height"] >= TAP


async def test_an_empty_table_is_an_error_not_an_empty_dashboard(page: Page, site: Path) -> None:
    await serve(page, site, wait=False, backend=Backend([]))
    await expect(page.locator("#load-error")).to_be_visible()


async def test_every_request_asks_for_open_jobs_in_a_stable_order_page_by_page(page: Page, site: Path) -> None:
    backend = Backend(table_rows(2 * REST_PAGE + 1))
    await serve(page, site, backend=backend)
    queries = [parse_qs(urlparse(r.url).query) for r in backend.requests]
    assert sorted(int(q["offset"][0]) for q in queries) == [0, REST_PAGE, 2 * REST_PAGE]
    for query in queries:
        assert query["order"] == ["company,job_id"]
        assert query["status"] == ["eq.open"]
        assert query["limit"] == [str(REST_PAGE)]
        columns = set(query["select"][0].split(","))
        assert {"level", "role_types", "regions", "locations", "first_seen"} <= columns
        assert "description" not in columns


def row(company: str, job_id: str, title: str | None, first_seen: str, **kw: Any) -> dict[str, Any]:
    return {**table_rows(1)[0], "company": company, "job_id": job_id, "title": title, "first_seen": first_seen, **kw}


async def test_cards_take_kind_and_place_from_the_table_columns(page: Page, site: Path) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    rows = [
        row("Acme", "1", "Product Intern", today, level="Intern", role_types=["Product"]),
        row("Acme", "2", "Student", today, level="Intern"),
        row("Acme", "3", "Junior Dev", today, level="Junior"),
        row("Acme", "4", "Engineer", today),
        row("Nowhere", "5", None, today, regions=[], locations=["Somewhere, Israel"]),
        row("Abroad", "6", "Engineer", today, regions=[], locations=[]),
    ]
    await serve(page, site, backend=Backend(rows))
    assert await page.locator("#summary").inner_text() == "Showing 5 of 5 open roles in Israel. 0 discarded."
    for category in ("product", "intern", "junior"):  # Product wins over a level; one of each kind
        assert await page.locator(f"article[data-category={category}]").count() == 1
    assert await page.locator("article[data-category=other]").count() == len(["Engineer", "untitled"])


async def test_a_company_whose_first_jobs_have_closed_still_shows_its_new_roles(page: Page, site: Path) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
    # the build knows Acme's first day (30 days ago, a job that is no longer open); Acme's only open role is from today
    await serve(page, site, backend=Backend([row("Acme", "9", "Engineer", today)]))
    assert await page.locator("article[data-company=Acme]").get_attribute("data-new") == "true"


async def test_a_company_in_the_failing_list_is_marked_possibly_old(page: Page, site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '"blocked":[]' in html
    (site / "index.html").write_text(
        html.replace('"blocked":[]', '"blocked":[{"c":"Acme","why":"x","last":"never"}]'), encoding="utf-8"
    )
    await serve(page, site)
    assert await page.locator("article[data-company=Acme]").first.get_attribute("data-stale") == "true"
    assert await page.locator("article[data-company=Globex]").count() == 0  # not in Israel


async def test_the_error_card_retries_by_reloading(page: Page, site: Path) -> None:
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    backend = Backend(rows, "down")
    await serve(page, site, wait=False, backend=backend)
    await expect(page.locator("#load-error")).to_be_visible()
    backend.mode = "ok"
    await page.click("#retry")
    await page.wait_for_selector("#list article")
    assert await page.locator("#load-error").count() == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
async def test_the_error_card_has_no_axe_violations(page: Page, site: Path, scheme: str) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    rows = json.loads((site.parent / "rows.json").read_text(encoding="utf-8"))
    await serve(page, site, wait=False, backend=Backend(rows, "down"))
    await expect(page.locator("#load-error")).to_be_visible()
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    violations = await page.evaluate(
        "axe.run().then(r => r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.html).join(' | ')))"
    )
    assert violations == []


async def test_the_page_csp_allows_only_itself_and_the_project_host(site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")
    assert f"connect-src 'self' {build_dashboard.SUPABASE_URL};" in html
    assert "__SUPABASE" not in html
    assert "__PAGE_DATA__" not in html
    assert build_dashboard.SUPABASE_ANON_KEY.startswith("sb_publishable_")  # never a secret or service-role key
    for built in site.iterdir():
        text = built.read_text(encoding="utf-8") if built.suffix in {".html", ".js", ".css", ".json"} else ""
        assert "sb_secret_" not in text
        assert "service_role" not in text
    assert "__SUPABASE" not in (site / "dashboard.js").read_text(encoding="utf-8")
    assert "unsafe-inline" not in html


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
