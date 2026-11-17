"""The snippet of a role on its card (PLAN.md step 7.3, FR-4 AC 5), in a real browser against a fake backend: the whole
snippet and, under it, the link to the employer's page, and nothing of the full text of the posting (the lawyer's rule,
PRD FR-4: the page never reads it). There is no "Details" button: the card shows all the dialog did."""

import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Locator, Page

import build_dashboard
from tests.test_dashboard_page import AXE, AXE_RUN, PHONE, TAP, Backend, build_site, job, serve

SNIPPET = "We are looking for a product manager to join a small team that builds tools for"
CUT = SNIPPET + " people who plan their week and"  # 255-ish characters, no final punctuation: cut by the data
HTML = "<script>window.pwned = 1</script><b>bold</b> text"
ROWS = [
    job("cut", "Product Manager", "Haifa, Israel", snippet=CUT * 3, snippet_cut=True),
    job("whole", "Designer", "Haifa, Israel", snippet="Short posting that ends here.", snippet_cut=False),
    job("html", "Engineer", "Haifa, Israel", snippet=HTML),
    job("short-cut", "Cutter", "Haifa, Israel", snippet="A short start", snippet_cut=True),
    job("none", "Analyst", "Haifa, Israel", snippet=None),
    job("hebrew", "מנהל מוצר", "Haifa, Israel", snippet="משרה בחיפה"),
]


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built with the daily-email signup off, like the `site` fixture of tests/test_dashboard_page.py."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


def card(page: Page, job_id: str) -> Locator:
    return page.locator(f"article[data-job-id='Acme|{job_id}']")


async def test_there_is_no_details_button_and_only_a_card_with_a_snippet_links_to_the_employer(
    page: Page, site: Path
) -> None:
    assert await serve(page, site, backend=Backend(ROWS)) == []
    assert await page.locator("[data-detail], #detailDialog").count() == 0
    assert await page.locator(".card-more").count() == len(ROWS) - 1
    assert await card(page, "none").locator(".card-more").count() == 0
    link = card(page, "whole").locator(".card-more a")
    assert (await link.text_content() or "").startswith("Read full description & apply on Acme ↗")
    assert "opens in a new tab" in (await link.text_content() or "")
    assert await link.get_attribute("href") == "https://x.test/j"
    assert await link.get_attribute("target") == "_blank"
    assert await link.get_attribute("rel") == "noopener noreferrer"


async def test_the_snippet_is_text_never_markup_runs_right_to_left_for_hebrew_and_marks_a_cut(
    page: Page, site: Path
) -> None:
    await serve(page, site, backend=Backend(ROWS))
    html = card(page, "html").locator(".card-snippet")
    assert await html.text_content() == HTML
    assert await html.locator("*").count() == 0
    assert await page.evaluate("window.pwned") is None
    cut = card(page, "cut").locator(".card-snippet")
    assert await cut.text_content() == CUT * 3 + "…"  # the data has no ellipsis: the page adds one, hidden from readers
    assert await cut.locator("[aria-hidden=true]").text_content() == "…"
    assert await cut.evaluate("e => e.scrollHeight <= e.clientHeight + 1")  # not clamped: the card shows it whole
    assert await card(page, "short-cut").locator(".card-snippet").text_content() == "A short start…"
    assert await card(page, "whole").locator(".card-snippet").text_content() == "Short posting that ends here."
    assert await card(page, "hebrew").locator(".card-snippet").get_attribute("dir") == "auto"


async def test_the_page_never_asks_for_or_holds_the_full_text(page: Page, site: Path) -> None:
    backend = Backend([{**r, "description": "SECRET-FULL-POSTING", "details": {"x": 1}} for r in ROWS])
    await serve(page, site, backend=backend)
    for request in backend.requests:
        assert "description" not in request.url and "details" not in request.url
    assert await page.evaluate("document.documentElement.outerHTML.includes('SECRET-FULL-POSTING')") is False


async def test_a_star_keeps_the_focus_on_the_star(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    star = page.locator("article[data-job-id='Acme|whole'] [data-act=star]")
    await star.click()  # the list is drawn again; the focus comes back to the same star
    assert await page.evaluate("document.activeElement.dataset.act") == "star"
    assert await page.evaluate("document.activeElement.dataset.k") == "Acme|whole"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}])
async def test_the_card_has_no_axe_violations_44px_targets_and_no_sideways_scroll(
    page: Page, site: Path, scheme: str, size: dict[str, int]
) -> None:
    await page.emulate_media(color_scheme=scheme)
    await page.set_viewport_size(size)
    await serve(page, site, backend=Backend(ROWS))
    for control in await page.locator(".card-actions .btn").all():
        box = await control.bounding_box()
        assert box is not None and box["height"] >= TAP and box["width"] >= TAP
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
