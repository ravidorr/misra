"""The "First seen" line of a role card: one in the meta row after the company, with the same place on every card
whatever the tags do, readable in both themes, and true to what the date is (the day Misra first saw the role)."""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
from tests.test_dashboard_page import TAP_MIN, Backend, build_site, row, serve

AA = 4.5

WIDTHS = [320, 375, 768, 1280]
LABEL = re.compile(r"^First seen (today|yesterday|\d+ days ago)$")
NO_SIDEWAYS = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
# The contrast of two CSS colors, as WCAG 2.2 defines it, read from the page (a color the browser resolves to rgb()).
CONTRAST = """([a, b]) => {
  const lum = (css) => {
    const c = css.match(/[\\d.]+/g).slice(0, 3).map(Number)
      .map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}"""


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


def day(ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=ago)).strftime("%Y-%m-%dT00:00:00")


def rows() -> list[dict[str, object]]:
    long_title = "Senior Principal Staff Software Engineer, Distributed Storage and Streaming Platform Infrastructure"
    return [
        row("Acme", "old", "Accountant", day(40), regions=["North"], role_types=["Finance"]),
        row("Acme", "today", "Designer", day(0), regions=["North"], role_types=["Design"]),
        row(
            "Acme",
            "many",
            long_title,
            day(1),
            regions=["Tel Aviv", "Sharon", "North"],
            locations=["Ramat Gan, Israel", "Petah Tikva, Israel"],
            role_types=["R&D", "Data & AI", "Product"],
            team="Platform Engineering",
        ),
        row("Acme", "five", "QA Engineer", day(5), regions=["Tel Aviv"], role_types=["R&D"]),
        row("Acme", "nodate", "No date", "", regions=["Tel Aviv"], role_types=["R&D"]),
    ]


async def open_page(page: Page, site: Path, width: int, scheme: str = "light") -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size({"width": width, "height": 900})
    assert await serve(page, site, backend=Backend(rows())) == []


async def test_each_card_with_a_date_has_one_visible_labelled_line_for_people_and_agents(
    page: Page, site: Path
) -> None:
    await open_page(page, site, 375)
    cards = await page.locator("#list article").all()
    assert len(cards) == len(rows())
    texts = {}
    for card in cards:
        seen = card.locator(".card-seen")
        job_id = (await card.get_attribute("data-job-id") or "").split("|")[1]
        if job_id == "nodate":
            assert await seen.count() == 0  # no date, no line (and no empty label)
            continue
        assert await seen.count() == 1
        assert await seen.evaluate("e => e.tagName") == "TIME"
        assert await seen.is_visible()
        text = (await seen.inner_text()).strip()
        assert LABEL.match(text), text
        texts[job_id] = text
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", await seen.get_attribute("datetime") or "")
        assert await seen.locator("svg").get_attribute("aria-hidden") == "true"
        assert await card.locator(".card-tags .card-seen").count() == 0  # not among the tags any more
    assert texts["today"] == "First seen today"
    assert texts["many"] == "First seen yesterday"
    assert texts["five"] == "First seen 5 days ago"
    assert texts["old"] == "First seen 40 days ago"


async def test_the_tooltip_gives_the_full_date_and_does_not_claim_a_posting_date(page: Page, site: Path) -> None:
    await open_page(page, site, 375)
    seen = page.locator("article[data-job-id='Acme|five'] .card-seen")
    date = await seen.get_attribute("datetime")
    assert date is not None
    title = await seen.get_attribute("title") or ""
    stamp = datetime.fromisoformat(date)
    assert title == (
        f"Misra first saw this role on {stamp.day} {stamp:%b %Y}. The employer may have posted it earlier."
    )
    assert "posted on" not in title.lower()


@pytest.mark.parametrize("width", WIDTHS)
async def test_the_line_is_in_the_meta_row_above_the_title_whatever_the_tags_do(
    page: Page, site: Path, width: int
) -> None:
    await open_page(page, site, width)
    tops = {}
    for job_id in ("today", "many"):  # three pills and a dozen
        card = page.locator(f"article[data-job-id='Acme|{job_id}']")
        seen = await card.locator(".card-seen").bounding_box()
        company = await card.locator(".card-company").bounding_box()
        title = await card.locator(".card-title").bounding_box()
        tags = await card.locator(".card-tags").bounding_box()
        outer = await card.bounding_box()
        assert seen is not None
        assert company is not None
        assert title is not None
        assert tags is not None
        assert outer is not None
        assert await card.locator(".card-meta .card-seen").count() == 1
        assert seen["y"] + seen["height"] <= title["y"] + 1  # above the title, never under the pills
        assert seen["y"] + seen["height"] <= tags["y"]
        assert seen["x"] >= company["x"]  # after the company, on the same row or just under it
        assert seen["x"] + seen["width"] <= outer["x"] + outer["width"]  # inside the card
        tops[job_id] = seen["y"] - outer["y"]
    assert tops["today"] == pytest.approx(tops["many"], abs=1)  # the same distance from the top of the card
    assert await page.evaluate(NO_SIDEWAYS)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
async def test_the_line_has_aa_contrast_and_adds_no_target(page: Page, site: Path, width: int, scheme: str) -> None:
    await open_page(page, site, width, scheme)
    card = page.locator("article[data-job-id='Acme|today']")  # not a discarded card: that one is faded
    seen = card.locator(".card-seen")
    color = await seen.evaluate("e => getComputedStyle(e).color")
    panel = await card.evaluate("e => getComputedStyle(e).backgroundColor")
    assert await page.evaluate(CONTRAST, [color, panel]) >= AA
    assert await seen.locator("a, button").count() == 0  # not a control
    for button in await card.locator(".card-actions .btn").all():
        box = await button.bounding_box()
        assert box is not None
        assert box["height"] >= TAP_MIN


async def test_a_long_company_name_keeps_the_line_inside_the_card_at_320(page: Page, site: Path) -> None:
    long_name = "A rather long company name that has to wrap on a phone"
    await page.emulate_media(color_scheme="light")
    await page.set_viewport_size({"width": 320, "height": 900})
    backend = Backend([row(long_name, "x", "Intern", day(2), regions=["Tel Aviv"], role_types=["R&D"])])
    assert await serve(page, site, backend=backend) == []
    seen = await page.locator(".card-seen").bounding_box()
    outer = await page.locator("#list article").bounding_box()
    title = await page.locator(".card-title").bounding_box()
    assert seen is not None
    assert outer is not None
    assert title is not None
    assert outer["x"] <= seen["x"] and seen["x"] + seen["width"] <= outer["x"] + outer["width"]
    assert seen["y"] + seen["height"] <= title["y"] + 1
    assert await page.evaluate(NO_SIDEWAYS)
