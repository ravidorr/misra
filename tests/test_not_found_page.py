"""The 404 page (docs/404.html, built from templates/not-found.html and templates/parts/not-found.inc), built and run in
a real browser. GitHub Pages serves it with a 404 status for any missing address at any depth. Locally the tests skip
when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import Page, Route

import build_dashboard
from tests.test_admin_page import DESKTOP, NOT_FOUND_LINE, NOT_FOUND_TITLE, WIDE_MIN
from tests.test_dashboard_page import (
    AXE,
    ORIGIN,
    PHONE,
    TAP_MIN,
    site,  # noqa: F401  (a fixture)
)
from tests.test_sign_in import AXE_RUN

REPO = Path(__file__).resolve().parents[1]
READING_WIDTH = 760  # the width of the pages other than the admin page


async def open_missing(page: Page, docs: Path, address: str = "/misra/no/such/page") -> list[str]:
    """Serve docs/ under /misra/ as GitHub Pages does, and answer any other address with 404.html and the status 404."""
    problems: list[str] = []

    async def files(route: Route) -> None:
        path = urlparse(route.request.url).path
        name = path.removeprefix(build_dashboard.SITE_PATH) if path.startswith(build_dashboard.SITE_PATH) else ""
        file = docs / (name or "index.html")
        if name and file.is_file():
            await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(name)[0] or "text/plain")
        else:
            await route.fulfill(status=404, body=(docs / "404.html").read_bytes(), content_type="text/html")

    await page.route(f"{ORIGIN}/**", files)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    await page.goto(f"{ORIGIN}{address}")
    return problems


# ---- what is built ----


def test_the_page_is_built_noindex_under_the_csp_with_its_scripts_hashed(site: Path) -> None:
    html = (site / "404.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in html
    assert f"<title>{NOT_FOUND_TITLE}</title>" in html
    assert 'name="description"' not in html
    assert 'rel="canonical"' not in html
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp
    policy = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"
    )
    assert csp[1] == policy
    assert "<script>" not in html
    assert " style=" not in html
    assert re.search(r"__[A-Z_]+__", html) is None  # every placeholder was filled
    for name in ("theme.js", "dashboard.css"):
        assert re.search(
            rf'(src|href)="{re.escape(build_dashboard.SITE_PATH + name)}" integrity="sha384-[A-Za-z0-9+/]+=*"', html
        )
    assert html.count("<h1") == 1
    assert html.count("See open roles</a>") == 1


def test_every_link_and_asset_is_absolute_so_the_page_works_at_any_depth(site: Path) -> None:
    html = (site / "404.html").read_text(encoding="utf-8")
    addresses = re.findall(r'\b(?:href|src)="([^"]*)"', html)
    assert addresses
    for address in addresses:
        assert address.startswith((build_dashboard.SITE_PATH, "#", "https://")), address
    assert f'href="{build_dashboard.SITE_PATH}"' in html  # the logo, the home button and "See open roles"
    assert f'href="{build_dashboard.SITE_PATH}about.html"' in html


def test_the_pages_that_are_not_the_404_keep_their_relative_paths(site: Path) -> None:
    html = (site / "about.html").read_text(encoding="utf-8")
    assert 'href="dashboard.css"' in html
    assert 'href="./"' in html


def test_absolute_paths_leave_other_addresses_alone() -> None:
    text = '<a href="./">h</a><a href="x.html">x</a><a href="#top">t</a><a href="/abs">a</a><a href="mailto:a@b">m</a>'
    assert build_dashboard.absolute_paths(text) == (
        '<a href="/misra/">h</a><a href="/misra/x.html">x</a><a href="#top">t</a><a href="/abs">a</a>'
        '<a href="mailto:a@b">m</a>'
    )


def test_the_404_page_is_linked_from_nowhere_and_is_not_in_llms_txt_or_the_json_ld(site: Path) -> None:
    for file in site.iterdir():
        if file.suffix in {".html", ".js", ".json", ".jsonld"} and file.name != "404.html":
            assert "404.html" not in file.read_text(encoding="utf-8"), file.name
    assert "404" not in (REPO / "llms.txt").read_text(encoding="utf-8")
    assert "404" not in (site / "jobs.jsonld").read_text(encoding="utf-8")
    assert "admin" not in (site / "404.html").read_text(encoding="utf-8").lower()


def test_the_404_page_is_among_the_files_the_daily_run_publishes() -> None:
    daily = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert "docs/404.html" in daily


# ---- in the browser ----


async def test_a_missing_address_deep_in_the_site_is_styled_and_leads_home(page: Page, site: Path) -> None:
    problems = await open_missing(page, site, "/misra/a/b/c/d")
    assert problems == [
        "Failed to load resource: the server responded with a status of 404 (Not Found)"
    ]  # the page itself
    assert await page.title() == NOT_FOUND_TITLE
    assert await page.locator("main h1").inner_text() == "Page not found"
    lines = [line for line in (await page.locator("main").inner_text()).splitlines() if line]
    assert lines == ["Page not found", NOT_FOUND_LINE, "See open roles"]
    background = await page.evaluate("getComputedStyle(document.querySelector('.not-found-title')).backgroundColor")
    assert background != "rgba(0, 0, 0, 0)"  # the stylesheet loaded from /misra/, not from the deep address
    assert await page.locator("main a").get_attribute("href") == build_dashboard.SITE_PATH
    await page.get_by_role("link", name="About").click()
    assert urlparse(page.url).path == f"{build_dashboard.SITE_PATH}about.html"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
async def test_it_is_accessible_fits_the_phone_and_has_44px_targets(
    page: Page, site: Path, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    await open_missing(page, site)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    targets = page.locator("main a:visible")
    assert await targets.count() == 1
    box = await targets.first.bounding_box()
    assert box
    assert box["height"] >= TAP_MIN
    assert box["width"] >= TAP_MIN


async def test_it_keeps_the_reading_width_and_the_shared_header_and_footer(page: Page, site: Path) -> None:
    await page.set_viewport_size({"width": 1480, "height": 900})
    await open_missing(page, site)
    width = await page.evaluate("document.querySelector('.page').getBoundingClientRect().width")
    assert width <= READING_WIDTH < WIDE_MIN
    assert await page.locator("header.top a#home").count() == 1
    assert await page.locator("footer.foot nav.foot-links a").count() == 7  # noqa: PLR2004  (Home and the six pages)
