"""The way home (PRD: every page but the dashboard): an icon-only home button in the header, in the place the dashboard
gives to the account controls, and a "Home" link first in the footer. The dashboard has neither; the logo link stays."""

from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_dashboard_page import PHONE, TAP_MIN, site  # noqa: F401  (site is a fixture)
from tests.test_legal_pages import open_legal

OTHER_PAGES = [
    "about.html",
    "assistant.html",
    "contact.html",
    "email.html",
    "privacy.html",
    "security.html",
    "terms.html",
]
STYLE = ["border-top-color", "border-top-width", "background-color", "box-shadow", "border-top-left-radius", "color"]


def test_the_slots_fill_the_home_button_and_link_on_every_page_but_the_dashboard() -> None:
    page = "__HEADER__\n__FOOTER__"
    other = build_dashboard.apply_chrome(page, "about.html")
    assert other.count('id="home"') == 1
    assert other.count('<a href="./">Home</a>') == 1
    assert other.index('<a href="./">Home</a>') < other.index('href="about.html"')
    dashboard = build_dashboard.apply_chrome(page, "index.html", dashboard=True)
    assert 'id="home"' not in dashboard
    assert ">Home<" not in dashboard
    assert 'id="accountMenuButton"' in dashboard


def test_a_slot_the_build_does_not_fill_is_still_an_error(tmp_path: Path) -> None:
    parts = tmp_path / build_dashboard.PARTS
    parts.mkdir(parents=True)
    for source in ("header.inc", "footer.inc", "home-button.inc", "home-link.inc", "account.inc", "updated.inc"):
        (parts / source).write_text("__MISSING__\n" if source == "footer.inc" else "x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="slot that this build does not fill"):
        build_dashboard.apply_chrome("__FOOTER__", "about.html", tmp_path)


@pytest.mark.parametrize("output", OTHER_PAGES)
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}], ids=["phone", "desktop"])
async def test_the_home_button_is_once_in_the_header_44px_and_leads_to_the_root(
    page: Page,
    site: Path,  # noqa: F811
    output: str,
    size: dict[str, int],
) -> None:
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    await open_legal(page, site, output)
    button = page.locator("header.top a#home")
    assert await page.locator("#home").count() == 1
    assert await button.count() == 1
    assert await button.get_attribute("href") == "./"
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
    await open_legal(page, site, output)
    links = page.locator("footer.foot nav.foot-links a")
    first = links.first
    assert await first.inner_text() == "Home"
    assert await first.get_attribute("href") == "./"
    assert await first.get_attribute("aria-current") is None
    assert await page.locator("a.top-home").get_attribute("href") == "./"
    assert await page.locator("a.top-home").get_attribute("aria-label") is None  # still named by "misra"
    assert await page.locator("a.top-home").inner_text() == "misra"


@pytest.mark.parametrize("output", ["about.html", "contact.html", "email.html", "privacy.html"])
async def test_the_tab_order_is_skip_link_logo_home_theme(page: Page, site: Path, output: str) -> None:  # noqa: F811
    await open_legal(page, site, output)
    order = []
    for _ in range(4):
        await page.keyboard.press("Tab")
        order.append(await page.evaluate("document.activeElement.id || document.activeElement.className"))
    assert order == ["skip", "top-home", "home", "theme"]


async def test_the_dashboard_has_no_home_button_and_no_home_link(page: Page, site: Path) -> None:  # noqa: F811
    await open_legal(page, site, "index.html")
    assert await page.locator("#home").count() == 0
    assert await page.get_by_role("link", name="Home", exact=True).count() == 0
    assert await page.locator("#signIn").count() == 1
    assert await page.locator("#accountMenuButton").count() == 1
    assert await page.locator("footer.foot nav.foot-links a").first.inner_text() == "About"
