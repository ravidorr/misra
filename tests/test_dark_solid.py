"""The purple (solid) primary button in dark mode: a hard shadow in a deeper shade of its own fill (the one hard-shadow
colour every control, card and dialog wears in dark), not the grey line colour and not the fill itself; text and edge
contrast; light mode unchanged; the two dark token blocks agree; forced colors keep a visible border."""

import re
import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_dashboard_page import build_site, serve
from tests.test_phone_layout import add_companies

TOKENS = Path(__file__).resolve().parent.parent / "design-system" / "tokens.css"
Rgb = tuple[int, int, int]
TRANSITION_MS = 300  # a button eases its shadow over 100ms when the theme changes
LABEL_CONTRAST = 4.5  # WCAG 1.4.3 (normal text)
EDGE_CONTRAST = 3  # WCAG 1.4.11 (the button's edge, the focus ring)
SLAB_CONTRAST = 1.5  # the shadow stays visible against the card, calmly
BORDER_MIN_PX = 2
SRGB_KNEE = 0.03928

# Reads any computed CSS colour (rgb, color(srgb ...), color-mix results) as 8-bit sRGB through a canvas pixel.
TO_RGB = """(css) => { const c = document.createElement('canvas'); c.width = c.height = 1;
  const x = c.getContext('2d'); x.fillStyle = css; x.fillRect(0, 0, 1, 1);
  return Array.from(x.getImageData(0, 0, 1, 1).data).slice(0, 3); }"""
PRIMARY_STYLE = """(e) => { const s = getComputedStyle(e); const shadow = s.boxShadow;
  const found = /(rgba?\\([^)]*\\)|color\\([^)]*\\))/.exec(shadow); const color = found ? found[1] : '';
  return { shadow: color, border: s.borderTopColor, borderWidth: s.borderTopWidth, borderStyle: s.borderTopStyle,
           fill: s.backgroundColor, text: s.color, panel: getComputedStyle(e.closest('article')).backgroundColor,
           line: getComputedStyle(document.documentElement).getPropertyValue('--line').trim() }; }"""
SECONDARY_SHADOW = (
    """(e) => { const s = getComputedStyle(e).boxShadow; return /(rgba?\\([^)]*\\)|color\\([^)]*\\))/.exec(s)[1]; }"""
)


def luminance(rgb: Rgb) -> float:
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= SRGB_KNEE else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Rgb, b: Rgb) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir, add_companies)


async def rgb(page: Page, css: str) -> Rgb:
    r, g, b = await page.evaluate(TO_RGB, css)
    return r, g, b


async def open_in(page: Page, site: Path, mode: str) -> None:
    """mode: "light", "dark" (the phone's setting) or "forced-dark" (the manual switch over a light phone)."""
    await page.emulate_media(color_scheme="dark" if mode == "dark" else "light")
    assert await serve(page, site) == []
    if mode == "forced-dark":
        await page.evaluate("document.documentElement.dataset.theme = 'dark'")
        await page.wait_for_timeout(TRANSITION_MS)


async def primary(page: Page) -> dict[str, str]:
    return await page.locator("#list article .btn-primary").first.evaluate(PRIMARY_STYLE)  # type: ignore[no-any-return]


@pytest.mark.parametrize("mode", ["dark", "forced-dark"])
async def test_the_dark_primary_shadow_is_a_deeper_shade_of_the_fill(page: Page, site: Path, mode: str) -> None:
    await open_in(page, site, mode)
    style = await primary(page)
    shadow, fill = await rgb(page, style["shadow"]), await rgb(page, style["fill"])
    line = await rgb(page, style["line"])
    assert shadow != line  # the old muddy grey-lilac slab
    assert shadow != fill  # not flat by accident
    assert luminance(shadow) < luminance(fill)
    # Same hue as the fill: its blue stays the strongest channel and red stays above green, like the fill's.
    assert shadow[2] > shadow[0] > shadow[1]
    # One family: a secondary button wears the same hard shadow.
    secondary = page.locator("#list article .btn:not(.btn-primary)").first
    assert await rgb(page, await secondary.evaluate(SECONDARY_SHADOW)) == shadow


@pytest.mark.parametrize("mode", ["dark", "forced-dark"])
async def test_the_dark_primary_colors_keep_their_contrast(page: Page, site: Path, mode: str) -> None:
    await open_in(page, site, mode)
    style = await primary(page)
    fill, text, panel = [await rgb(page, style[k]) for k in ("fill", "text", "panel")]
    shadow = await rgb(page, style["shadow"])
    assert contrast(text, fill) >= LABEL_CONTRAST  # the label
    assert contrast(fill, panel) >= EDGE_CONTRAST  # the button's edge against the card (WCAG 1.4.11)
    assert contrast(shadow, panel) >= SLAB_CONTRAST  # the slab stays visible, calmly
    accent = await rgb(
        page, await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent')")
    )
    assert contrast(accent, panel) >= EDGE_CONTRAST  # the focus ring around it
    assert (
        contrast(accent, fill) >= SLAB_CONTRAST
    )  # and the ring is told apart from the fill by its offset gap and width


async def test_the_light_primary_is_unchanged(page: Page, site: Path) -> None:
    await open_in(page, site, "light")
    style = await primary(page)
    ink = (17, 17, 17)
    assert await rgb(page, style["shadow"]) == ink
    assert await rgb(page, style["border"]) == ink
    assert await rgb(page, style["fill"]) == (108, 71, 255)


@pytest.mark.parametrize("mode", ["light", "dark"])
async def test_forced_colors_keep_the_primary_border(page: Page, site: Path, mode: str) -> None:
    await open_in(page, site, mode)
    await page.emulate_media(forced_colors="active", color_scheme=mode)  # type: ignore[arg-type]
    style = await primary(page)
    assert style["borderStyle"] == "solid"
    assert float(style["borderWidth"].removesuffix("px")) >= BORDER_MIN_PX


def test_the_two_dark_token_blocks_agree() -> None:
    css = TOKENS.read_text(encoding="utf-8")
    auto = re.search(
        r"prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme=\"light\"\]\) \{(.*?)\n  \}", css, re.S
    )
    manual = re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', css, re.S)
    assert auto
    assert manual

    def declarations(block: str) -> dict[str, str]:
        lines = (re.sub(r"/\*.*?\*/", "", line).strip() for line in block.splitlines())
        return dict(line.rstrip(";").split(":", 1) for line in lines if line.startswith("--"))

    assert declarations(auto.group(1)) == declarations(manual.group(1))


@pytest.mark.parametrize("mode", ["dark", "forced-dark"])
async def test_the_dark_primary_keeps_its_shadow_color_on_hover_and_press(page: Page, site: Path, mode: str) -> None:
    await open_in(page, site, mode)
    assert await page.evaluate("matchMedia('(hover: hover)').matches")  # else the hover rule is never tested
    button = page.locator("#list article .btn-primary").first
    rest = await rgb(page, (await primary(page))["shadow"])
    await button.hover()
    await page.wait_for_timeout(TRANSITION_MS)
    assert await rgb(page, await button.evaluate(SECONDARY_SHADOW)) == rest
    assert "4px 4px" in await button.evaluate("e => getComputedStyle(e).boxShadow")
    await page.mouse.down()
    await page.wait_for_timeout(TRANSITION_MS)
    assert await rgb(page, await button.evaluate(SECONDARY_SHADOW)) == rest
    assert "1px 1px" in await button.evaluate("e => getComputedStyle(e).boxShadow")
    await page.mouse.up()
