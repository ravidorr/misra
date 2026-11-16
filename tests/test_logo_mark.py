"""The Misra mark is a lime disc with a violet spark and an ink pulse. These tests pin that assignment in every place
the colors are fixed, so it cannot flip back unnoticed: the design tokens, the standalone SVG (the source of the
favicon) and the dashboard styles that paint the header logo."""

import re
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOKENS = (REPO / "design-system" / "tokens.css").read_text(encoding="utf-8")
MARK = (REPO / "design-system" / "assets" / "mark.svg").read_text(encoding="utf-8")
APPLE_TOUCH_ICON = REPO / "design-system" / "assets" / "apple-touch-icon.png"
APPLE_TOUCH_SIZE = 180
BYTES_PER_PIXEL = 4


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
    for part in ("disc", "spark"):
        rule = re.search(rf"\.mark-{part}\s*\{{([^}}]*)\}}", css)
        assert rule is not None
        assert re.search(rf"(^|;|\s)fill:\s*var\(--mark-{part}\);", rule[1])
    page = (REPO / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert re.search(r'<circle class="mark-disc"', page)
    assert re.search(r'<path class="mark-spark"', page)


def test_apple_touch_icon_is_a_180_px_png() -> None:
    data = APPLE_TOUCH_ICON.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (APPLE_TOUCH_SIZE, APPLE_TOUCH_SIZE)


def decode_png_rgba(data: bytes) -> list[bytearray]:
    """The rows of an 8-bit RGBA PNG (no interlacing), unfiltered; enough for the committed icon."""
    width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", data[16:29])
    assert (depth, color_type, interlace) == (8, 6, 0)
    chunks, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        if data[pos + 4 : pos + 8] == b"IDAT":
            chunks.append(data[pos + 8 : pos + 8 + length])
        pos += 12 + length
    raw, stride, rows = zlib.decompress(b"".join(chunks)), width * 4, []
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        prev = rows[-1] if rows else bytearray(stride)
        for i in range(stride):
            a = line[i - BYTES_PER_PIXEL] if i >= BYTES_PER_PIXEL else 0
            b, c = prev[i], (prev[i - BYTES_PER_PIXEL] if i >= BYTES_PER_PIXEL else 0)
            predictor = (0, a, b, (a + b) // 2, paeth(a, b, c))[kind]
            line[i] = (line[i] + predictor) & 0xFF
        rows.append(line)
    return rows


def paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def test_apple_touch_icon_has_a_lime_disc_and_a_violet_spark() -> None:
    rows = decode_png_rgba(APPLE_TOUCH_ICON.read_bytes())

    def pixel(x: int, y: int) -> str:
        return "#" + bytes(rows[y][x * 4 : x * 4 + 3]).hex()

    scale = APPLE_TOUCH_SIZE / 64
    assert pixel(round(32 * scale), round(53 * scale)) == "#d4ff4f"  # the disc, below the pulse
    assert pixel(round(47 * scale), round(15 * scale)) == "#6c47ff"  # the middle of the spark
    assert pixel(1, 1) == "#ffffff"  # opaque white corners
