"""The way home (PRD: every page but the dashboard): an icon-only home button in the header, in the place the dashboard
gives to the account controls, and a "Home" link first in the footer. The dashboard has neither; the logo link stays."""

from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_dashboard_page import PHONE, TAP_MIN, site  # noqa: F401  (site is a fixture)
from tests.test_legal_pages import open_legal
from tests.test_not_found_page import open_missing

OTHER_PAGES = [
    "about.html",
    "admin.html",
    "404.html",
    "assistant.html",
    "contact.html",
    "email.html",
    "privacy.html",
    "security.html",
    "terms.html",
]
HOME_HREF = "/misra/"  # the 404 page is served at any depth, so it links from the root
STYLE = ["border-top-color", "border-top-width", "background-color", "box-shadow", "border-top-left-radius", "color"]


async def open_page(page: Page, docs: Path, output: str) -> None:
    """The 404 page is served for a missing address, as GitHub Pages does; the others at their own file."""
    if output == "404.html":
        await open_missing(page, docs)
    else:
        await open_legal(page, docs, output)


def test_the_slots_fill_the_home_button_and_link_before_the_other_footer_links() -> None:
    page = "__HEADER__\n__FOOTER__"
    other = build_dashboard.apply_chrome(page, "about.html")
    assert other.count('id="home"') == 1
    assert other.count('<a href="./">Home</a>') == 1
    assert other.index('<a href="./">Home</a>') < other.index('href="about.html"')


@pytest.mark.parametrize("output", OTHER_PAGES)
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}], ids=["phone", "desktop"])
async def test_the_home_button_is_once_in_the_header_44px_and_leads_to_the_root(
    page: Page,
    site: Path,  # noqa: F811
    output: str,
    size: dict[str, int],
) -> None:
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    await open_page(page, site, output)
    button = page.locator("header.top a#home")
    assert await page.locator("#home").count() == 1
    assert await button.count() == 1
    assert await button.get_attribute("href") == (HOME_HREF if output == "404.html" else "./")
    assert await button.get_attribute("aria-label") == "Home"
    assert await button.get_attribute("title") == "Home"
    assert await button.locator("svg[aria-hidden='true']").count() == 1
    box = await button.bounding_box()
    assert box is not None
    assert box["width"] >= TAP_MIN
    assert box["height"] >= TAP_MIN
    # same shape as the theme button beside it: the same classes and so the same computed style, nothing copied
    assert await button.get_attribute("class") == "btn btn-pill btn-icon"
    assert (await page.locator("#theme").get_attribute("class") or "").startswith("btn btn-pill btn-icon")
    styles = "(e, names) => { const s = getComputedStyle(e); return names.map(p => s.getPropertyValue(p)); }"
    assert await button.evaluate(styles, STYLE) == await page.locator("#theme").evaluate(styles, STYLE)
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


@pytest.mark.parametrize("output", OTHER_PAGES)
async def test_the_footer_says_home_first_and_the_logo_link_is_unchanged(page: Page, site: Path, output: str) -> None:  # noqa: F811
    await open_page(page, site, output)
    links = page.locator("footer.foot nav.foot-links a")
    first = links.first
    assert await first.inner_text() == "Home"
    assert await first.get_attribute("href") == (HOME_HREF if output == "404.html" else "./")
    assert await first.get_attribute("aria-current") is None
    assert await page.locator("a.top-home").get_attribute("href") == (HOME_HREF if output == "404.html" else "./")
    assert await page.locator("a.top-home").get_attribute("aria-label") is None  # still named by "misra"
    assert await page.locator("a.top-home").inner_text() == "misra"
