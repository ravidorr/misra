"""The "Share" button on a job card, in a real browser against the built page and a fake backend. It hands the role to
the visitor's own device: the operating system's sheet on a touch device that has one, else the clipboard. It shares
"Title at Company (Location)" and the employer's own link and nothing else, stores nothing, sends nothing and changes
no choice."""

import json
import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Page, expect

import build_dashboard
from tests.test_dashboard_page import AXE, AXE_RUN, PHONE, TAP_MIN, WEBMCP, Backend, build_site, row, serve

DAY = "2026-01-02T00:00:00"
LINK = "https://acme.test/jobs/1?gh_src=abc#apply"
ONE = "article[data-job-id='Acme|1']"
HEBREW = "article[data-job-id='Acme|2']"
NO_PLACE = "article[data-job-id='Acme|3']"
LONG = "article[data-job-id='Acme|4']"
LONG_PLACE = "Tel Aviv, Israel " + "word " * 30  # over the 80 characters the card shows
ROWS = [
    row(
        "Acme",
        "1",
        "Product Manager",
        DAY,
        location="Tel Aviv, Israel",
        url=LINK,
        level="Intern",
        role_types=["Product"],
    ),
    row("Acme", "2", "מנהל מוצר", DAY, location="Haifa, Israel", url="https://acme.test/jobs/2"),
    row("Acme", "3", "Analyst", DAY, location=None, url="https://acme.test/jobs/3"),
    row("Acme", "4", "Planner", DAY, location=LONG_PLACE, url="https://acme.test/jobs/4"),
]
WAIT = 2600  # ms: the confirmation lasts 2 s
FAIL_WAIT = 4600
STORES = "[JSON.stringify(localStorage), JSON.stringify(sessionStorage), document.cookie]"
# The clipboard and the share sheet are stubs that record what they were given; `__fail` and `__shareError` break them.
CLIPBOARD = """
window.__copied = [];
window.__fail = false;
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: {
    writeText(text) {
      if (window.__fail) return Promise.reject(new Error('denied'));
      window.__copied.push(text);
      return Promise.resolve();
    },
  },
});
"""
TOUCH = """
window.__shared = [];
window.__shareError = null;
const realMatch = window.matchMedia.bind(window);
const coarse = { matches: true, media: '(pointer: coarse)', addEventListener() {}, removeEventListener() {} };
window.matchMedia = (q) => (q === '(pointer: coarse)' ? coarse : realMatch(q));
Object.defineProperty(navigator, 'share', {
  configurable: true,
  value(data) {
    window.__shared.push(data);
    return window.__shareError ? Promise.reject(window.__shareError) : Promise.resolve();
  },
});
"""
ABORT = "window.__shareError = Object.assign(new Error('cancelled'), { name: 'AbortError' })"
OTHER = "window.__shareError = Object.assign(new Error('nope'), { name: 'NotAllowedError' })"


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


async def open_page(page: Page, site: Path, *, touch: bool = False, tools: bool = False) -> list[str]:
    await page.add_init_script(CLIPBOARD)
    if touch:
        await page.add_init_script(TOUCH)
    if tools:
        await page.add_init_script(WEBMCP)
    return await serve(page, site, backend=Backend(ROWS))


async def copied(page: Page) -> list[str]:
    return await page.evaluate("window.__copied")


async def test_every_open_card_has_a_share_button_between_star_and_not_for_me(page: Page, site: Path) -> None:
    await open_page(page, site)
    cards = await page.locator("#list article").all()
    assert len(cards) == len(ROWS)
    for card in cards:
        button = card.locator("[data-act=share]")
        assert await button.count() == 1
        assert await button.get_attribute("data-k") == await card.get_attribute("data-job-id")
        assert await button.inner_text() == "Share"
        assert await button.get_attribute("type") == "button"
    assert await page.locator(f"{ONE} [data-act=share]").get_attribute("aria-label") == "Share: Product Manager at Acme"
    order = await page.locator(f"{ONE} .card-actions > *").evaluate_all(
        "els => els.map(e => e.matches('a.btn-primary') ? 'apply' : "
        "e.dataset.act || e.querySelector('[data-act]').dataset.act)"
    )
    assert order == ["apply", "star", "share", "menu"]


async def test_a_discarded_card_keeps_share_before_restore_and_a_closed_starred_card_has_none(
    page: Page, site: Path
) -> None:
    star = {"Gone|7": {"c": "Gone", "i": "7", "t": "Data Analyst", "l": "Haifa", "u": "https://gone.test/7"}}
    await page.add_init_script(f"localStorage.setItem('il-jobs-starred-v1', {json.dumps(json.dumps(star))})")
    await open_page(page, site)
    await page.click(f"{ONE} [data-act=menu]")
    await page.click(f"{ONE} [data-act=toggle]")
    await page.check("#showGone")
    discarded = page.locator(f"{ONE}[data-discarded=true]")
    await expect(discarded).to_be_visible()
    order = await discarded.locator(".card-actions > *").evaluate_all("els => els.map(e => e.dataset.act || 'apply')")
    assert order == ["apply", "star", "share", "toggle"]
    await page.click("#starredView")
    closed = page.locator("article[data-job-id='Gone|7']")
    await expect(closed).to_be_visible()
    assert await closed.locator("[data-act=share]").count() == 0


async def test_on_a_desktop_share_copies_the_text_and_the_employer_link_and_says_so(page: Page, site: Path) -> None:
    problems = await open_page(page, site)
    button = page.locator(f"{ONE} [data-act=share]")
    href = await page.locator(f"{ONE} a.btn-primary").get_attribute("href")
    await button.click()
    await expect(button).to_have_text("Copied")
    assert await button.get_attribute("aria-label") == "Copied: Product Manager at Acme"
    assert await copied(page) == [f"Product Manager at Acme (Tel Aviv, Israel)\n{href}"]
    assert href == LINK  # the employer's link, byte for byte: nothing was added
    await expect(page.locator("#shareStatus")).to_have_text("Copied: Product Manager at Acme.")
    await expect(button).to_have_text("Share", timeout=WAIT)
    assert await button.get_attribute("aria-label") == "Share: Product Manager at Acme"
    await expect(page.locator("#shareStatus")).to_have_text("", timeout=WAIT)
    assert problems == []


async def test_a_role_without_a_place_and_a_hebrew_title_make_clean_text(page: Page, site: Path) -> None:
    await open_page(page, site)
    await page.click(f"{NO_PLACE} [data-act=share]")
    await page.click(f"{HEBREW} [data-act=share]")
    await page.click(f"{LONG} [data-act=share]")
    first, second, third = await copied(page)
    assert first == "Analyst at Acme\nhttps://acme.test/jobs/3"  # no empty parentheses
    assert second == "מנהל מוצר at Acme (Haifa, Israel)\nhttps://acme.test/jobs/2"
    assert third.split("\n")[0] == f"Planner at Acme ({LONG_PLACE[:80]})"  # the card's cut of 80


async def test_on_a_touch_device_the_sheet_gets_title_text_and_link_apart(page: Page, site: Path) -> None:
    await open_page(page, site, touch=True)
    await page.click(f"{ONE} [data-act=share]")
    shared = await page.evaluate("window.__shared")
    assert shared == [{"title": "Product Manager", "text": "Product Manager at Acme (Tel Aviv, Israel)", "url": LINK}]
    assert LINK not in shared[0]["text"]  # the link is passed once, in url
    for word in ("Intern", "Product", "first seen", "New", "Star"):
        assert word not in shared[0]["text"].replace("Product Manager", "")
    assert await copied(page) == []  # the sheet took it
    assert await page.locator(f"{ONE} [data-act=share]").inner_text() == "Share"


async def test_a_cancelled_sheet_is_silent(page: Page, site: Path) -> None:
    problems = await open_page(page, site, touch=True)
    await page.evaluate(ABORT)
    await page.click(f"{ONE} [data-act=share]")
    assert len(await page.evaluate("window.__shared")) == 1
    assert await copied(page) == []
    assert await page.locator(f"{ONE} [data-act=share]").inner_text() == "Share"
    assert await page.locator("#shareStatus").inner_text() == ""
    await page.wait_for_timeout(200)
    assert await page.locator("#shareStatus").inner_text() == ""
    assert problems == []


async def test_a_sheet_that_fails_for_another_reason_falls_back_to_the_clipboard(page: Page, site: Path) -> None:
    await open_page(page, site, touch=True)
    await page.evaluate(OTHER)
    await page.click(f"{ONE} [data-act=share]")
    await expect(page.locator(f"{ONE} [data-act=share]")).to_have_text("Copied")
    assert len(await copied(page)) == 1


async def test_the_sheet_is_not_used_when_canshare_refuses_the_data(page: Page, site: Path) -> None:
    await page.add_init_script(
        "Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => false })"
    )
    await open_page(page, site, touch=True)
    await page.click(f"{ONE} [data-act=share]")
    assert await page.evaluate("window.__shared") == []
    assert len(await copied(page)) == 1


async def test_a_fine_pointer_copies_even_where_the_sheet_exists(page: Page, site: Path) -> None:
    await open_page(page, site)
    await page.add_init_script(
        "Object.defineProperty(navigator, 'share', { configurable: true, "
        "value: () => { window.__called = true; return Promise.resolve(); } })"
    )
    await page.reload()
    await page.wait_for_selector("#list article")
    await page.click(f"{ONE} [data-act=share]")
    assert not await page.evaluate("window.__called")
    assert len(await copied(page)) == 1


async def test_a_refused_copy_shows_the_failure_and_the_link_to_select(page: Page, site: Path) -> None:
    problems = await open_page(page, site)
    await page.evaluate("window.__fail = true")
    button = page.locator(f"{ONE} [data-act=share]")
    await button.click()
    await expect(button).to_have_text("Couldn't copy")
    assert await button.get_attribute("aria-label") == "Couldn't copy: Product Manager at Acme"
    assert await page.locator(f"{ONE} .share-fallback").inner_text() == LINK
    await expect(page.locator("#shareStatus")).to_have_text("Couldn't copy. The link is shown under the buttons.")
    assert await page.evaluate("document.activeElement.dataset.act") == "share"
    await expect(button).to_have_text("Share", timeout=FAIL_WAIT)
    assert await page.locator(f"{ONE} .share-fallback").inner_text() == LINK  # the link stays, to be selected
    assert problems == []


async def test_a_long_link_shown_after_a_refused_copy_stays_inside_the_card_at_320(page: Page, site: Path) -> None:
    await page.set_viewport_size({"width": 320, "height": 900})
    await page.add_init_script(CLIPBOARD)
    link = "https://acme.test/jobs/1?token=" + "a" * 150
    backend = Backend([row("Acme", "1", "Product Manager", DAY, location="Tel Aviv, Israel", url=link)])
    await serve(page, site, backend=backend)
    await page.evaluate("window.__fail = true")
    await page.click(f"{ONE} [data-act=share]")
    await expect(page.locator(f"{ONE} .share-fallback")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    card = await page.locator(ONE).bounding_box()
    note = await page.locator(f"{ONE} .share-fallback").bounding_box()
    assert card is not None
    assert note is not None
    assert note["x"] >= card["x"]
    assert note["x"] + note["width"] <= card["x"] + card["width"]


async def test_no_clipboard_at_all_is_the_same_failure(page: Page, site: Path) -> None:
    await serve(page, site, backend=Backend(ROWS))
    await page.evaluate("Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })")
    await page.click(f"{ONE} [data-act=share]")
    await expect(page.locator(f"{ONE} [data-act=share]")).to_have_text("Couldn't copy")


async def test_a_link_that_is_not_http_shares_nothing(page: Page, site: Path) -> None:
    await page.add_init_script(CLIPBOARD)
    backend = Backend(
        [row("Acme", "1", "Product Manager", DAY, location="Tel Aviv, Israel", url="javascript:alert(1)")]
    )
    await serve(page, site, backend=backend)
    await page.click(f"{ONE} [data-act=share]")
    await expect(page.locator(f"{ONE} [data-act=share]")).to_have_text("Couldn't copy")
    assert await copied(page) == []


async def test_sharing_sends_and_stores_nothing(page: Page, site: Path) -> None:
    await open_page(page, site, touch=True)
    before = await page.evaluate(STORES)
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    await page.click(f"{ONE} [data-act=share]")
    await page.evaluate("window.__shareError = Object.assign(new Error('x'), { name: 'NotAllowedError' })")
    await page.click(f"{ONE} [data-act=share]")
    await page.evaluate("window.__fail = true")
    await page.click(f"{ONE} [data-act=share]")
    await page.wait_for_timeout(200)
    assert requests == []
    assert await page.evaluate(STORES) == before
    assert before[2] == ""


async def test_focus_stays_the_card_is_not_redrawn_and_the_keys_work(page: Page, site: Path) -> None:
    await open_page(page, site)
    await page.evaluate(f'window.__card = document.querySelector("{ONE}")')
    button = page.locator(f"{ONE} [data-act=share]")
    await page.locator(f"{ONE} [data-act=star]").focus()
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.dataset.act") == "share"
    await page.keyboard.press("Enter")
    await expect(button).to_have_text("Copied")
    assert await page.evaluate("document.activeElement.dataset.act") == "share"
    await page.keyboard.press("Space")
    assert len(await copied(page)) == 2  # noqa: PLR2004
    assert await page.evaluate(f'window.__card === document.querySelector("{ONE}")')  # the same node: no render
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.dataset.act") == "menu"
    await page.keyboard.press("Shift+Tab")
    assert await page.evaluate("document.activeElement.dataset.act") == "share"
    assert await page.locator(f"{ONE}").get_attribute("data-starred") == "false"  # no choice changed


async def test_a_second_press_restarts_the_confirmation_and_a_redraw_in_between_throws_nothing(
    page: Page, site: Path
) -> None:
    problems = await open_page(page, site)
    button = page.locator(f"{ONE} [data-act=share]")
    await button.click()
    await page.wait_for_timeout(1500)
    await button.click()
    await page.wait_for_timeout(1000)  # 2.5 s after the first press: the first timer would have run
    assert await button.inner_text() == "Copied"
    await expect(page.locator("#shareStatus")).to_have_text("Copied: Product Manager at Acme.")
    await page.click(f"{ONE} [data-act=star]")  # the list is drawn again: the old button is gone
    await page.wait_for_timeout(WAIT)
    assert await page.locator(f"{ONE} [data-act=share]").inner_text() == "Share"
    assert problems == []


@pytest.mark.parametrize("width", [320, 360, 375, 1280])
async def test_the_row_fits_and_every_action_is_a_target(page: Page, site: Path, width: int) -> None:
    await page.set_viewport_size({"width": width, "height": 900})
    await open_page(page, site)
    await page.click(f"{ONE} [data-act=menu]")
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    boxes = await page.locator(f"{ONE} .card-actions .btn").evaluate_all(
        "els => els.map(e => { const b = e.getBoundingClientRect(); return [b.width, b.height, b.left, b.top]; })"
    )
    for w, h, *_ in boxes:
        assert w >= TAP_MIN
        assert h >= TAP_MIN
    share = await page.locator(f"{ONE} [data-act=share]").bounding_box()
    panel = await page.locator(f"{ONE} .card-menu-panel").bounding_box()
    assert share is not None
    assert panel is not None
    assert share["x"] + share["width"] <= width
    assert panel["x"] >= 0
    assert panel["x"] + panel["width"] <= width
    if width >= 768:  # noqa: PLR2004
        assert len({round(b[3]) for b in boxes}) == 1  # one row


async def test_the_card_has_no_accessibility_violations_in_light_and_dark_while_the_result_shows(
    page: Page, site: Path
) -> None:
    await open_page(page, site)
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    for scheme in ("light", "dark"):
        await page.emulate_media(color_scheme=scheme)
        await page.click(f"{ONE} [data-act=share]")
        assert await page.evaluate(AXE_RUN) == []
    await page.evaluate("window.__fail = true")
    await page.click(f"{ONE} [data-act=share]")
    assert await page.evaluate(AXE_RUN) == []
    await page.set_viewport_size(PHONE)
    assert await page.evaluate(AXE_RUN) == []


async def test_no_assistant_tool_shares_a_role(page: Page, site: Path) -> None:
    await open_page(page, site, tools=True)
    names = await page.evaluate("Object.keys(window.__tools)")
    assert names  # the tools did register
    assert not [name for name in names if "share" in name.lower()]
    assert "star_job" in names


async def test_under_forced_colors_the_button_keeps_a_border_and_works(page: Page, site: Path) -> None:
    await page.emulate_media(forced_colors="active")
    await open_page(page, site)
    button = page.locator(f"{ONE} [data-act=share]")
    assert await button.evaluate("e => parseFloat(getComputedStyle(e).borderTopWidth)") > 0
    await button.click()
    await expect(button).to_have_text("Copied")
