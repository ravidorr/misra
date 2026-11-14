"""The job-card page script (browser_crawl.CARD_JS) run in a real browser on small static pages.

The fakes in the other tests never run the script, so its climb (how far up from a title link a card reaches) is
pinned here. Locally the tests skip when Chromium is missing; in CI (CI=true) a missing browser is a failure."""

from typing import Any

import pytest
from playwright.async_api import Page

from browser_crawl import CARD_ARGS, CARD_JS, CARD_MAX

BASE = "https://x.test"
MAX_LEVELS = 4
EXTRA_LEVELS = 2
TEXT_WIDTH = 600
OVERSIZE = 700


async def cards(page: Page, html: str) -> dict[str, str]:
    """Card text by link text, for every link on the page."""
    await page.set_content(html)
    triples: list[list[Any]] = await page.eval_on_selector_all("a[href]", CARD_JS, CARD_ARGS)
    return {text: card for _href, text, card in triples}


def item(path: str, title: str, place: str, cls: str = "item") -> str:
    return f'<li class="{cls}"><div><a href="{BASE}{path}">{title}</a></div><div>{place}</div></li>'


async def test_a_flat_list_stops_at_the_list_item(page: Page) -> None:
    html = f"<ul>{item('/careers/a-role', 'A Role', 'Remote')}{item('/careers/b-role', 'B Role', 'Haifa')}</ul>"
    assert await cards(page, html) == {"A Role": "A Role\nRemote", "B Role": "B Role\nHaifa"}


async def test_a_wrapper_with_one_job_is_climbed_through(page: Page) -> None:
    html = f'<section><div><div><a href="{BASE}/jobs/x-role">X Role</a></div></div><div>Tel Aviv</div></section>'
    assert (await cards(page, html))["X Role"] == "X Role\nTel Aviv"


async def test_an_apply_button_with_its_own_url_is_not_a_second_job(page: Page) -> None:
    def html(button: str) -> str:
        return (
            f'<div><div><div><a href="{BASE}/jobs/x-role">X Role</a></div>'
            f'<div><a href="{BASE}/jobs/x-role/apply">{button}</a></div></div><div>Haifa</div></div>'
        )

    assert (await cards(page, html("Apply now")))["X Role"] == "X Role\nApply now\nHaifa"
    # the same link with text that does not read as a button is a second job: the card stops short of Haifa
    assert (await cards(page, html("Join us")))["X Role"] == "X Role"


async def test_links_that_python_would_not_count_as_jobs_do_not_stop_the_climb(page: Page) -> None:
    html = (
        f'<div><div><div><a href="{BASE}/jobs/x-role">X Role</a></div>'
        f'<div><a href="{BASE}/jobs/x-role#apply">Quick apply</a></div>'
        f'<div><a href="{BASE}/careers/department/rnd">R&amp;D</a></div>'
        f'<div><a href="{BASE}/jobs/x-role?src=list">X Role</a></div></div><div>Haifa</div></div>'
    )
    assert (await cards(page, html))["X Role"].endswith("\nHaifa")


async def test_the_climb_stops_after_four_levels(page: Page) -> None:
    inner = f'<a href="{BASE}/jobs/x-role">X Role</a>'
    for level in range(1, 7):
        inner = f"<div>{inner}<div>t{level}</div></div>"
    card = (await cards(page, inner))["X Role"]
    assert [f"t{n}" in card for n in range(1, 7)] == [True] * MAX_LEVELS + [False] * EXTRA_LEVELS


@pytest.mark.parametrize(("filler", "climbs"), [(TEXT_WIDTH - 2, True), (TEXT_WIDTH - 1, False)])
async def test_the_climb_stops_before_an_ancestor_with_too_much_text(page: Page, filler: int, climbs: bool) -> None:
    assert CARD_MAX == TEXT_WIDTH
    html = f'<div><div><a href="{BASE}/jobs/x-role">T</a></div><div>{"x" * filler}</div></div>'
    card = (await cards(page, html))["T"]
    assert len(card) == (TEXT_WIDTH if climbs else 1)


async def test_a_link_with_too_much_text_of_its_own_is_cut_just_past_the_limit(page: Page) -> None:
    html = f'<div><a href="{BASE}/jobs/x-role">{"y" * OVERSIZE}</a></div>'
    assert all(len(card) == CARD_MAX + 1 for card in (await cards(page, html)).values())


async def test_the_patterns_reach_the_script_with_their_flags(page: Page) -> None:
    upper = f"<ul>{item('/JOBS/Foo-Bar', 'Foo', 'Haifa')}{item('/Jobs/Baz-Qux', 'Baz', 'Eilat')}</ul>"
    assert await cards(page, upper) == {"Foo": "Foo\nHaifa", "Baz": "Baz\nEilat"}
    one, two = item("/job-description/?job_id=1", "One", "Haifa"), item("/job-description/?job_id=2", "Two", "Eilat")
    by_id = f"<ul>{one}{two}</ul>"
    assert await cards(page, by_id) == {"One": "One\nHaifa", "Two": "Two\nEilat"}


async def test_a_link_inside_an_svg_does_not_break_the_script(page: Page) -> None:
    html = f'<svg><a href="/icon"><text>icon</text></a></svg><div><a href="{BASE}/jobs/x-role">X Role</a></div>'
    await page.set_content(html)
    triples = await page.eval_on_selector_all("a[href]", CARD_JS, CARD_ARGS)
    assert [href for href, _text, _card in triples] == ["", f"{BASE}/jobs/x-role"]
