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
    rows: list[list[Any]] = await page.eval_on_selector_all("a[href]", CARD_JS, CARD_ARGS)
    return {row[1]: row[2] for row in rows}


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
    assert [row[0] for row in triples] == ["", f"{BASE}/jobs/x-role"]


async def row_for(page: Page, html: str, path: str) -> list[Any]:
    """The script's whole row (address, text, card, own heading, others, leaves) for one link."""
    await page.set_content(html)
    rows: list[list[Any]] = await page.eval_on_selector_all("a[href]", CARD_JS, CARD_ARGS)
    return next(row for row in rows if row[0].endswith(path))


def icon_card(path: str, title: str, place: str) -> str:
    """env0: the title and place are plain text, the link is an arrow with hidden text."""
    return (
        f'<div class="card"><div><p>{title}</p></div><div>{place}</div>'
        f'<a href="{BASE}{path}"><svg width="9" height="9"></svg></a></div>'
    )


async def test_an_icon_only_link_does_not_let_the_card_swallow_the_next_job(page: Page) -> None:
    first, second = icon_card("/careers/a-role", "A Role", "Haifa"), icon_card("/careers/b-role", "B Role", "Eilat")
    html = f"<ul><li>{first}</li><li>{second}</li></ul>"
    row = await row_for(page, html, "/careers/a-role")
    assert row[1] == ""
    assert row[2] == "A Role\n\nHaifa"  # the card stops at its own list item
    assert row[5] == "A Role\nHaifa"


async def test_the_text_of_a_card_comes_back_one_line_for_each_element(page: Page) -> None:
    html = (
        f'<div><a href="{BASE}/careers/x-role" style="display:inline">Details</a>'
        '<ul><li style="display:inline">X Role</li><li style="display:inline">Haifa</li></ul>'
        '<script>var s = 1;</script><style>p { color: red }</style><div style="display:none">hidden</div></div>'
    )
    row = await row_for(page, html, "/careers/x-role")
    assert "X Role" not in row[2].split("\n")  # the page prints the two parts on one line
    assert row[5] == "Details\nX Role\nHaifa"


async def test_a_heading_inside_the_link_is_its_own_title(page: Page) -> None:
    html = (
        f'<a href="{BASE}/cmcareer/x-role"><div>Customer Delivery</div><h4>Field Engineer</h4><p>Haifa</p></a>'
        f'<a href="{BASE}/cmcareer/y-role"><div>Sales</div><p>Sales Manager</p></a>'
    )
    assert (await row_for(page, html, "/cmcareer/x-role"))[3] == "Field Engineer"
    assert (await row_for(page, html, "/cmcareer/y-role"))[3] == ""


async def test_a_card_says_whether_it_has_another_titled_job_link(page: Page) -> None:
    def html(button: str) -> str:
        return (
            f'<div><div><a href="{BASE}/jobs/x-role">X Role</a></div>'
            f'<div><a href="{BASE}/jobs/x-role/details">{button}</a></div></div>'
        )

    assert (await row_for(page, html("Details"), "/jobs/x-role/details"))[4] is True
    assert (await row_for(page, html("Details"), "/jobs/x-role"))[4] is False  # a button is not a title
    lone = f'<div><a href="{BASE}/jobs/z-role">Details</a></div>'
    assert (await row_for(page, lone, "/jobs/z-role"))[4] is False


async def test_an_icon_link_to_another_address_does_not_shrink_the_card_of_a_titled_link(page: Page) -> None:
    html = (
        f'<div><div><a href="{BASE}/jobs/x-role">X Role</a></div><div>Haifa</div>'
        f'<div><a href="{BASE}/jobs/x-role/apply"><svg></svg></a></div></div>'
    )
    assert (await row_for(page, html, "/jobs/x-role"))[2].startswith("X Role\nHaifa")


def label_card(path: str, title: str, place: str, label: str = "Details") -> str:
    return f'<div class="c"><span>{title}</span><span>{place}</span><a href="{BASE}{path}">{label}</a></div>'


async def test_adjacent_cards_that_link_through_a_label_each_keep_their_own_card(page: Page) -> None:
    html = (
        "<div>"
        + label_card("/jobs/backend-engineer", "Backend Engineer", "Haifa")
        + label_card("/jobs/product-designer", "Product Designer", "Berlin, Germany", "→")
        + "</div>"
    )
    assert "Berlin" not in (await row_for(page, html, "/jobs/backend-engineer"))[2]
    assert "Haifa" not in (await row_for(page, html, "/jobs/product-designer"))[2]


async def test_an_apply_link_that_accompanies_a_titled_link_does_not_split_its_card(page: Page) -> None:
    html = (
        f'<div><div><a href="{BASE}/jobs/x-role">X Role</a></div><div>Haifa</div>'
        f'<div><a href="{BASE}/jobs/x-role/apply">Apply</a><a href="{BASE}/jobs/x-role/details">Details</a></div></div>'
    )
    assert (await row_for(page, html, "/jobs/x-role/apply"))[2].startswith("X Role")


async def test_adjacent_cards_that_only_link_through_an_apply_link_each_keep_their_own_card(page: Page) -> None:
    """Mobilicom: the title and the place are text in the card, the only link is "APPLY NOW"."""
    html = (
        "<div>"
        + label_card("/careers/backend-engineer", "Backend Engineer", "Haifa", "APPLY NOW")
        + label_card("/careers/product-designer", "Product Designer", "Berlin, Germany", "Apply")
        + "</div>"
    )
    first = (await row_for(page, html, "/careers/backend-engineer"))[2]
    second = (await row_for(page, html, "/careers/product-designer"))[2]
    assert "Haifa" in first
    assert "Berlin" not in first
    assert "Berlin" in second
    assert "Haifa" not in second


async def test_adjacent_cards_with_different_button_styles_are_two_cards(page: Page) -> None:
    """An Apply card next to a Details card, or an icon card: neither link climbs into the shared container."""
    for other in ("Details", "→"):
        for apply_first in (True, False):
            apply_card = label_card("/careers/backend-engineer", "Backend Engineer", "Haifa", "Apply")
            other_card = label_card("/careers/product-designer", "Product Designer", "Berlin, Germany", other)
            html = "<div>" + (apply_card + other_card if apply_first else other_card + apply_card) + "</div>"
            first = (await row_for(page, html, "/careers/backend-engineer"))[2]
            second = (await row_for(page, html, "/careers/product-designer"))[2]
            assert "Haifa" in first
            assert "Berlin" not in first
            assert "Berlin" in second
            assert "Haifa" not in second


async def test_label_cards_told_apart_by_a_job_id_query_are_two_cards(page: Page) -> None:
    html = (
        "<div>"
        + label_card("/careers/details?job_id=1234", "Backend Engineer", "Haifa")
        + label_card("/careers/details?job_id=5678", "Product Designer", "Berlin, Germany")
        + "</div>"
    )
    assert "Berlin" not in (await row_for(page, html, "job_id=1234"))[2]
    assert "Haifa" not in (await row_for(page, html, "job_id=5678"))[2]


async def test_apply_and_details_links_of_one_job_id_are_companions(page: Page) -> None:
    html = (
        f'<div><div><a href="{BASE}/careers/job?job_id=1234">X Role</a></div><div>Haifa</div>'
        f'<div><a href="{BASE}/careers/job/apply?job_id=1234">Apply</a>'
        f'<a href="{BASE}/careers/job/details?job_id=1234">Details</a></div></div>'
    )
    assert (await row_for(page, html, "/apply?job_id=1234"))[2].startswith("X Role")


async def test_an_apply_link_does_not_stop_at_a_details_link_of_its_own_job(page: Page) -> None:
    html = (
        f'<div><div><a href="{BASE}/jobs/x-role">X Role</a></div><div>Haifa</div>'
        f'<div><a href="{BASE}/jobs/x-role/apply">Apply</a><a href="{BASE}/jobs/x-role/details">Details</a></div></div>'
    )
    assert (await row_for(page, html, "/jobs/x-role/apply"))[2].startswith("X Role")


async def test_adjacent_label_cards_with_numbered_addresses_are_two_cards(page: Page) -> None:
    html = (
        "<div>"
        + label_card("/jobs/1234", "Backend Engineer", "Haifa")
        + label_card("/jobs/12345", "Product Designer", "Berlin, Germany")
        + "</div>"
    )
    assert "Berlin" not in (await row_for(page, html, "/jobs/1234"))[2]
    assert "Haifa" not in (await row_for(page, html, "/jobs/12345"))[2]


async def test_emphasis_inside_a_title_is_one_line_and_a_br_or_a_block_is_a_boundary(page: Page) -> None:
    html = (
        f'<div><a href="{BASE}/careers/x-role">Details</a>'
        "<h3>Senior <em>Backend</em> <strong>Engineer</strong></h3><p>Haifa<br>Hybrid</p>"
        "<span>Sales</span><span>R&amp;D</span></div>"
    )
    leaves = (await row_for(page, html, "/careers/x-role"))[5]
    assert leaves == "Details\nSenior Backend Engineer\nHaifa\nHybrid\nSales\nR&D"


TABLE = (
    "<table><thead><tr><th>Title</th><th>Requisition Identifier</th><th>Department</th><th>Location</th></tr></thead>"
    '<tbody><tr><td><a href="{base}/jobs/a-role">A Role</a></td><td>R1</td><td>Sales</td>'
    "<td><ul><li>Tel Aviv, Israel</li><li>Berlin, Germany</li></ul></td></tr>"
    '<tr><td><a href="{base}/jobs/b-role">B Role</a></td><td>R2</td><td>R&amp;D</td><td>Haifa</td></tr></tbody></table>'
)


async def test_a_table_row_labels_the_cell_under_a_place_header_and_drops_the_id(page: Page) -> None:
    card = (await cards(page, TABLE.replace("{base}", BASE)))["A Role"]
    assert card == "A Role\nSales\nLocation\nTel Aviv, Israel | Berlin, Germany"


async def test_a_row_whose_cells_do_not_match_the_headers_is_read_as_it_is(page: Page) -> None:
    html = TABLE.replace("{base}", BASE).replace("<th>Department</th>", "")
    card = (await cards(page, html))["A Role"]
    assert "Location" not in card.split("\n")
    assert "Tel Aviv, Israel" in card
