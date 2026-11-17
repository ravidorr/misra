"""The detail view of a role (PLAN.md step 7.3, FR-4 AC 5), in a real browser against a fake backend: a modal dialog
with the snippet and the link to the employer's page, and nothing of the full text of the posting (the lawyer's rule,
PRD FR-4: the page never reads it)."""

import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Locator, Page, expect

import build_dashboard
from tests.test_dashboard_page import AXE, AXE_RUN, PHONE, TAP_MIN, Backend, build_site, job, serve

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
DETAIL = "#detailDialog"


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The page built with the daily-email signup off, like the `site` fixture of tests/test_dashboard_page.py."""
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


def button(page: Page, job_id: str) -> Locator:
    return page.locator(f"article[data-job-id='Acme|{job_id}'] [data-detail]")


async def open_detail(page: Page, job_id: str) -> None:
    await button(page, job_id).click()
    await expect(page.locator(DETAIL)).to_be_visible()


async def test_only_a_card_with_a_snippet_has_a_details_button_named_after_the_role(page: Page, site: Path) -> None:
    assert await serve(page, site, backend=Backend(ROWS)) == []
    assert await page.locator("[data-detail]").count() == len(ROWS) - 1
    assert await button(page, "none").count() == 0
    assert await button(page, "whole").get_attribute("aria-label") == "Details: Designer at Acme"
    assert await button(page, "whole").text_content() == "Details"
    assert not await page.locator(DETAIL).is_visible()


async def test_the_detail_view_shows_the_snippet_as_text_and_the_link_to_the_employer(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await open_detail(page, "whole")
    assert await page.evaluate("document.querySelector('#detailDialog').matches(':modal')")
    assert await page.locator("#detailCompany").text_content() == "Acme"
    assert await page.locator("#detailTitle").text_content() == "Designer"
    assert await page.locator("#detailText").text_content() == "Short posting that ends here."  # whole: no ellipsis
    assert "Haifa" in (await page.locator("#detailTags").text_content() or "")
    link = page.locator("#detailLink")
    assert (await link.text_content() or "").startswith("Read full description & apply on Acme ↗")
    assert "opens in a new tab" in (await link.text_content() or "")
    assert await link.get_attribute("href") == "https://x.test/j"
    assert await link.get_attribute("target") == "_blank"
    assert await link.get_attribute("rel") == "noopener noreferrer"
    assert await page.locator(DETAIL).get_attribute("aria-labelledby") == "detailCompany detailTitle"
    assert await page.evaluate("document.activeElement.id") == "detailTitle"  # the other dialogs do the same


async def test_the_snippet_is_text_never_markup_runs_right_to_left_for_hebrew_and_marks_a_cut(
    page: Page, site: Path
) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await open_detail(page, "html")
    assert await page.locator("#detailText").text_content() == HTML
    assert await page.locator("#detailText *").count() == 0
    assert await page.evaluate("window.pwned") is None
    await page.keyboard.press("Escape")
    await open_detail(page, "cut")
    text = page.locator("#detailText")
    assert (
        await text.text_content() == CUT * 3 + "…"
    )  # the data has no ellipsis: the page adds one, hidden from readers
    assert await text.locator("[aria-hidden=true]").text_content() == "…"
    assert await text.evaluate("e => e.scrollHeight <= e.clientHeight + 1")  # not clamped here, unlike on the card
    await page.keyboard.press("Escape")
    await open_detail(page, "short-cut")  # a cut under 200 characters: the flag decides, not the length
    assert await page.locator("#detailText").text_content() == "A short start…"
    await page.keyboard.press("Escape")
    await open_detail(page, "hebrew")
    assert await page.locator("#detailText").get_attribute("dir") == "auto"
    assert await page.locator("#detailTitle").text_content() == "מנהל מוצר"


async def test_the_page_never_asks_for_or_holds_the_full_text(page: Page, site: Path) -> None:
    backend = Backend([{**r, "description": "SECRET-FULL-POSTING", "details": {"x": 1}} for r in ROWS])
    await serve(page, site, backend=backend)
    await open_detail(page, "whole")
    for request in backend.requests:
        assert "description" not in request.url and "details" not in request.url
    assert await page.evaluate("document.documentElement.outerHTML.includes('SECRET-FULL-POSTING')") is False


async def test_escape_and_close_return_the_focus_to_the_button_that_opened_it(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await button(page, "whole").focus()
    await page.keyboard.press("Enter")  # by keyboard
    await expect(page.locator(DETAIL)).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(page.locator(DETAIL)).to_be_hidden()
    await expect(button(page, "whole")).to_be_focused()
    await open_detail(page, "whole")
    await page.click("#detailClose")
    await expect(button(page, "whole")).to_be_focused()


async def test_the_focus_stays_inside_the_dialog_and_the_link_is_reachable_by_keyboard(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await open_detail(page, "whole")
    seen = []
    for _ in range(4):
        await page.keyboard.press("Tab")
        seen.append(await page.evaluate("document.activeElement.id"))
    assert {"detailClose", "detailLink"} <= set(seen)
    assert await page.evaluate("document.activeElement.closest('dialog')?.id") == "detailDialog"


async def test_the_focus_goes_to_the_list_heading_when_the_button_is_gone_on_closing(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await open_detail(page, "whole")
    await page.evaluate("document.getElementById('list').replaceChildren()")  # the list was drawn again without it
    await page.keyboard.press("Escape")
    await expect(page.locator("#list-h")).to_be_focused()


async def test_the_details_button_does_not_move_the_focus_after_a_star(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    star = page.locator("article[data-job-id='Acme|whole'] [data-act=star]")
    await star.click()  # the list is drawn again; the focus comes back to the same star, not to the button beside it
    assert await page.evaluate("document.activeElement.dataset.act") == "star"
    assert await page.evaluate("document.activeElement.dataset.k") == "Acme|whole"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, {"width": 1280, "height": 900}])
async def test_the_detail_view_has_no_axe_violations_44px_targets_and_no_sideways_scroll(
    page: Page, site: Path, scheme: str, size: dict[str, int]
) -> None:
    await page.emulate_media(color_scheme=scheme)
    await page.set_viewport_size(size)
    await serve(page, site, backend=Backend(ROWS))
    assert await button(page, "cut").bounding_box() is not None
    for card_button in await page.locator("[data-detail]").all():
        box = await card_button.bounding_box()
        assert box is not None and box["height"] >= TAP_MIN and box["width"] >= TAP_MIN
    await open_detail(page, "cut")
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    for control in ("#detailClose", "#detailLink"):
        box = await page.locator(control).bounding_box()
        assert box is not None and box["height"] >= TAP_MIN
    box = await page.locator(DETAIL).bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= size["width"]
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
