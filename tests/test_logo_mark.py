"""The Misra mark is a lime disc with a violet spark and an ink pulse. These tests pin that assignment in every place
the colors are fixed, so it cannot flip back unnoticed: the design tokens, the standalone SVG (the source of the
favicon) and the dashboard styles that paint the header logo."""

import re
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOKENS = (REPO / "design-system" / "tokens.css").read_text(encoding="utf-8")
MARK = (REPO / "design-system" / "assets" / "mark.svg").read_text(encoding="utf-8")
APPLE_TOUCH_ICON = REPO / "design-system" / "assets" / "apple-touch-icon.png"
APPLE_TOUCH_SIZE = 180


def token(name: str) -> str:
    """The value of a custom property; it must be declared exactly once, so no theme can override it."""
    values = re.findall(rf"^\s*--{name}:\s*(#[0-9a-f]{{3,6}});", TOKENS, re.MULTILINE)
    assert len(values) == 1, f"--{name} is declared {len(values)} times"
    return values[0]


def test_disc_is_lime_and_spark_is_violet() -> None:
    assert token("mark-disc") == token("lime") == "#d4ff4f"
    assert token("mark-spark") == "#6c47ff"
    assert token("mark-ink") == "#111"


def test_mark_svg_has_lime_disc_and_violet_spark() -> None:
    disc = re.search(r"<circle[^>]*>", MARK)
    assert disc is not None
    assert 'fill="#d4ff4f"' in disc[0]
    spark = [p for p in re.findall(r"<path[^>]*>", MARK) if 'fill="none"' not in p]
    pulse = [p for p in re.findall(r"<path[^>]*>", MARK) if 'fill="none"' in p]
    assert len(spark) == len(pulse) == 1
    assert 'fill="#6c47ff"' in spark[0]
    assert 'stroke="#111"' in pulse[0]
    assert MARK.count("#d4ff4f") == MARK.count("#6c47ff") == 1


def test_header_logo_paints_with_the_mark_tokens() -> None:
    css = (REPO / "templates" / "dashboard.css").read_text(encoding="utf-8")
    assert re.search(r"\.mark-disc\s*\{\s*fill:\s*var\(--mark-disc\);", css)
    assert re.search(r"\.mark-spark\s*\{\s*fill:\s*var\(--mark-spark\);", css)
    page = (REPO / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert re.search(r'<circle class="mark-disc"', page)
    assert re.search(r'<path class="mark-spark"', page)


def test_apple_touch_icon_is_a_180_px_png() -> None:
    data = APPLE_TOUCH_ICON.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (APPLE_TOUCH_SIZE, APPLE_TOUCH_SIZE)
