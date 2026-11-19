"""The "opens in a new tab" icon: one CSS rule (design-system/components/external-link.css) paints it after every
`a[target="_blank"]`, so a link written by hand, built in a script or converted from a legal Markdown file all get it
with no change to their words. The words for assistive technology ("(opens in a new tab)", `sr-only`) are the other
half, and a link without them fails here."""

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_admin_page import open_admin
from tests.test_dashboard_page import TAP_MIN, build_site, serve, site  # noqa: F401  (site is a fixture)
from tests.test_legal_pages import open_legal

REPO = Path(__file__).resolve().parent.parent
RULE = REPO / "design-system" / "components" / "external-link.css"
MASK = REPO / "design-system" / "assets" / "new-tab.svg"
WORDS = "(opens in a new tab)"
REL = 'rel="noopener noreferrer"'

# Everything that can write a link: the page templates, the shared header and footer, and the scripts that build links.
HTML_SOURCES = [*sorted((REPO / "templates").glob("*.html")), *sorted((REPO / "templates" / "parts").glob("*.inc"))]
SCRIPT_SOURCES = sorted((REPO / "templates").glob("*.js"))
ARROW_GLYPHS = ("↗", "→", "⤴", "⧉")  # the north-east arrow and its likes: the icon replaces them
LINK = re.compile(r"<a\b[^>]*\btarget=\"_blank\"[^>]*>.*?</a>", re.DOTALL)
# A script writes `target: '_blank'` in a props object, or `link.target = '_blank'`; the words must follow soon.
SCRIPT_LINK = re.compile(r"target(?:: | = )'_blank'")
SCRIPT_WINDOW = 700


def blank_links_in_markup() -> list[tuple[Path, str]]:
    return [(path, m[0]) for path in HTML_SOURCES for m in LINK.finditer(path.read_text(encoding="utf-8"))]


def test_every_link_in_the_markup_that_opens_a_new_tab_says_so_and_keeps_its_rel() -> None:
    links = blank_links_in_markup()
    assert len(links) >= 7  # noqa: PLR2004  (the sign-in notice 2, the digest consent 2, contact 1, admin 2)
    for path, link in links:
        assert REL in link, f"{path.name}: {link[:80]}"
        assert f'<span class="sr-only"> {WORDS}</span></a>' in link, f"{path.name}: {link[:80]}"
        assert "<svg" not in link, f"{path.name}: the icon is painted by CSS, not written in the link"


def test_every_link_a_script_builds_that_opens_a_new_tab_says_so_and_keeps_its_rel() -> None:
    found = 0
    for path in SCRIPT_SOURCES:
        text = path.read_text(encoding="utf-8")
        for match in SCRIPT_LINK.finditer(text):
            found += 1
            window = text[match.start() : match.start() + SCRIPT_WINDOW]
            assert "NEW_TAB" in window or WORDS in window, f"{path.name}: no words after {match[0]} at {match.start()}"
            assert "noopener noreferrer" in window, f"{path.name}: rel after {match[0]} at {match.start()}"
    assert found >= 8  # noqa: PLR2004  (dashboard.js 5, admin.js 3)


def test_no_template_or_component_writes_an_arrow_glyph_or_its_own_icon_for_a_new_tab_link() -> None:
    for path in [*HTML_SOURCES, *SCRIPT_SOURCES, *sorted((REPO / "design-system").rglob("*.css"))]:
        text = path.read_text(encoding="utf-8")
        assert "icon-ext" not in text, path.name
        assert "EXTERNAL_ARROW" not in text, path.name
        assert not any(glyph in text for glyph in ARROW_GLYPHS), path.name
    assert not any(
        glyph in (REPO / "design-system" / "index.html").read_text(encoding="utf-8") for glyph in ARROW_GLYPHS
    )


def test_the_rule_is_one_mask_file_that_the_page_policy_allows_and_the_build_copies() -> None:
    css = RULE.read_text(encoding="utf-8")
    assert 'a[target="_blank"]' in css
    assert "currentcolor" in css  # the link's own colour, so light, dark, a button and forced colors all follow it
    assert "forced-color-adjust: none" in css
    assert 'url("new-tab.svg")' in css
    assert 'url("data:' not in css  # the pages' `img-src 'self'` refuses a data: URI (a mask is an image fetch)
    assert "<svg" in MASK.read_text(encoding="utf-8")
    assert build_dashboard.ICON_FILES["design-system/assets/new-tab.svg"] == "new-tab.svg"
    for name in ("dashboard.html", "legal.html", "email.html", "contact.html", "admin.html"):
        page = (REPO / "templates" / name).read_text(encoding="utf-8")
        assert "img-src 'self'" in page, name
        assert "dashboard.css" in page, name  # every page loads the one stylesheet that holds the rule


async def icons(page: Page) -> list[dict[str, Any]]:
    """What the browser computed for the ::after of every link that opens in a new tab."""
    result: list[dict[str, Any]] = await page.evaluate(
        """() => [...document.querySelectorAll('a[target=_blank]')].map((a) => {
            const host = a.classList.contains('gap-link') ? a.querySelector('.gap-name-text') : a;
            const after = getComputedStyle(host, '::after');
            const a11y = a.textContent;
            return {
              text: a.textContent.trim().slice(0, 60),
              mask: after.getPropertyValue('mask-image') || after.getPropertyValue('-webkit-mask-image'),
              content: after.content,
              width: after.width,
              display: after.display,
              color: after.backgroundColor,
              link: getComputedStyle(a).color,
              hasSvg: a.querySelector('svg') !== null,
              glyph: /[\\u2197\\u2192]/.test(a.textContent),
              words: a11y.includes('(opens in a new tab)'),
            };
          })"""
    )
    return result


def assert_icons(found: list[dict[str, Any]], at_least: int) -> None:
    assert len(found) >= at_least
    for link in found:
        assert "new-tab.svg" in link["mask"], link
        assert link["content"] == '""', link  # empty: nothing is read for it, the sr-only words do that
        assert link["display"] != "none", link
        assert not link["hasSvg"], link
        assert not link["glyph"], link  # one arrow, never two
        assert link["words"], link


async def watch(page: Page) -> tuple[list[str], list[str]]:
    requested: list[str] = []
    problems: list[str] = []
    page.on("request", lambda r: requested.append(r.url) if r.url.endswith("new-tab.svg") else None)
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    return requested, problems


async def test_the_dashboard_cards_and_the_sign_in_and_alert_notices_show_the_icon(page: Page, site: Path) -> None:  # noqa: F811
    requested, problems = await watch(page)
    assert await serve(page, site) == []
    await page.evaluate("document.querySelectorAll('dialog').forEach((d) => d.setAttribute('open', ''))")
    found = await icons(page)
    assert_icons(found, 4)
    texts = " ".join(link["text"] for link in found)
    assert "Apply" in texts
    assert requested
    assert problems == []
    # the icon follows the colour of the link text, the button included
    for link in found:
        assert link["color"] == link["link"], link
    apply = page.locator(".card a.btn-primary").first
    assert "opens in a new tab" in (await apply.text_content() or "")
    box = await apply.bounding_box()
    assert box
    assert box["height"] >= TAP_MIN


async def test_the_icon_does_not_move_the_card_and_a_screen_reader_hears_the_words(page: Page, site: Path) -> None:  # noqa: F811
    await serve(page, site)
    links = page.locator("#list a[target=_blank]")
    assert await links.count() > 0
    named = page.get_by_role("link", name=re.compile(r"\(opens in a new tab\)$"))
    assert await named.count() >= await links.count()
    one = page.locator(".card-title a[target=_blank]").first
    size = await one.evaluate(
        "(a) => { const r = getComputedStyle(a, '::after'); return [r.width, r.height, r.position]; }"
    )
    assert size[2] == "static"
    assert float(size[0].removesuffix("px")) == pytest.approx(float(size[1].removesuffix("px")))
    assert 8 < float(size[0].removesuffix("px")) < 32  # noqa: PLR2004  (about 0.9em of the text)


async def test_a_new_tab_link_keeps_a_visible_focus_ring(page: Page, site: Path) -> None:  # noqa: F811
    await serve(page, site)
    for selector in (".card-title a[target=_blank]", ".card a.btn-primary[target=_blank]"):
        link = page.locator(selector).first
        await link.focus()
        await page.keyboard.press("Shift+Tab")
        await page.keyboard.press("Tab")
        ring = await link.evaluate("(a) => { const s = getComputedStyle(a); return [s.outlineStyle, s.outlineWidth]; }")
        assert ring[0] != "none"
        assert float(ring[1].removesuffix("px")) > 0


async def test_the_gap_company_links_show_the_icon_after_the_name(page: Page, site: Path) -> None:  # noqa: F811
    await serve(page, site)
    await page.locator("#gaps summary, #gaps button").first.click()
    link = page.locator("#gaps a.gap-link").first
    mask = await link.locator(".gap-name-text").evaluate(
        "(n) => getComputedStyle(n, '::after').getPropertyValue('mask-image')"
    )
    assert "new-tab.svg" in mask
    outer = await link.evaluate("(a) => getComputedStyle(a, '::after').content")
    assert outer == "none"  # the column layout keeps the icon after the name, never on a row of its own


@pytest.mark.parametrize("name", ["about.html", "assistant.html", "privacy.html", "contact.html"])
async def test_the_legal_pages_and_the_contact_page_show_the_icon_with_no_change_to_the_words(
    page: Page,
    site: Path,  # noqa: F811
    name: str,
) -> None:
    requested, problems = await watch(page)
    await open_legal(page, site, name)
    found = await icons(page)
    assert found, name  # each of these has an outside link or the privacy notice
    assert_icons(found, 1)
    assert requested
    assert problems == []
    # a converted page's links come from the Markdown with only the words after them: the icon adds no text
    assert all(link["content"] == '""' for link in found)


async def test_the_footer_has_no_new_tab_links(page: Page, site: Path) -> None:  # noqa: F811
    await open_legal(page, site, "privacy.html")
    assert await page.locator("footer a[target=_blank]").count() == 0


async def test_the_admin_page_links_show_the_icon(page: Page, site: Path) -> None:  # noqa: F811
    _backend, problems = await open_admin(page, site)
    found = await icons(page)
    assert_icons(found, 2)
    assert problems == []
    for link in found:
        assert link["width"] != "auto"


def test_the_converter_adds_the_words_and_never_an_icon_to_a_legal_link() -> None:
    html = build_dashboard.markdown_to_html("[out](https://example.test/x)\n")
    assert html == (
        '<p><a href="https://example.test/x" target="_blank" rel="noopener noreferrer">out'
        '<span class="sr-only"> (opens in a new tab)</span></a></p>\n'
    )


def test_the_build_copies_the_mask_beside_the_stylesheet(db: sqlite3.Connection, workdir: Path) -> None:
    docs = build_site(db, workdir)
    assert (docs / "new-tab.svg").read_bytes() == MASK.read_bytes()
    assert 'url("new-tab.svg")' in (docs / "dashboard.css").read_text(encoding="utf-8")
