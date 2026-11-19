"""The level pill of a role card: none for "Not specified", and no role-type pill for "Other" (they say nothing);
unchanged for every other value. Both stay in `data-level` and `data-role-types`, in the filters and in the assistant
tools."""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from playwright.async_api import Page

import build_dashboard
import classify
from tests.test_dashboard_page import REPO, WEBMCP, Backend, ask, build_site, row, serve

WIDTHS = [320, 375, 1280]
DAY = "2026-01-02T00:00:00"
PILLS = 3
NO_SIDEWAYS = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"


def rows() -> list[dict[str, object]]:
    place = {"regions": ["Tel Aviv area"], "role_types": ["Product"]}
    return [
        row("Acme", "plain", "Product Manager", DAY, level=classify.NOT_SPECIFIED, **place),
        row("Acme", "intern", "Product Intern", DAY, level=classify.INTERN_LEVEL, **place),
        row("Acme", "junior", "Junior PM", DAY, level=classify.JUNIOR_LEVEL, **place),
        row("Acme", "other", "Accountant", DAY, regions=["Tel Aviv area"], role_types=[classify.OTHER]),
        row("Acme", "mixed", "Product Ops", DAY, regions=["Tel Aviv area"], role_types=["Product", classify.OTHER]),
    ]


@pytest.fixture
def site(db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_dashboard, "DIGEST_SIGNUP_ENABLED", False)
    return build_site(db, workdir)


async def open_page(page: Page, site: Path, width: int, scheme: str = "light") -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size({"width": width, "height": 900})
    await page.add_init_script(WEBMCP)
    assert await serve(page, site, backend=Backend(rows())) == []


def card(page: Page, job_id: str):  # type: ignore[no-untyped-def]
    return page.locator(f"article[data-job-id='Acme|{job_id}']")


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
async def test_not_specified_has_no_pill_but_keeps_its_hook_and_the_row_stays_whole(
    page: Page, site: Path, width: int, scheme: str
) -> None:
    await open_page(page, site, width, scheme)
    plain = card(page, "plain")
    assert await plain.locator(".card-tags .pill-soft", has_text="Level:").count() == 0
    assert "Level:" not in (await plain.locator(".card-tags").text_content() or "")
    assert await plain.get_attribute("data-level") == classify.NOT_SPECIFIED  # the agent-readable hook is unchanged
    assert await plain.locator(".card-tags .pill").count() == PILLS  # place, region, role type: nothing blank
    for pill in await plain.locator(".card-tags .pill").all():
        assert (await pill.inner_text()).strip()
        assert await pill.is_visible()
    tags = await plain.locator(".card-tags").bounding_box()
    assert tags is not None
    assert tags["height"] > 0
    assert await page.evaluate(NO_SIDEWAYS)


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
async def test_every_other_level_keeps_its_pill(page: Page, site: Path, width: int, scheme: str) -> None:
    await open_page(page, site, width, scheme)
    for job_id, level in (("intern", "Intern"), ("junior", "Junior")):
        pill = card(page, job_id).locator(".card-tags .pill-soft", has_text=f"Level: {level}")
        assert await pill.count() == 1
        assert await pill.is_visible()
        assert await card(page, job_id).get_attribute("data-level") == level


async def test_the_filter_and_the_assistant_tools_still_offer_not_specified(page: Page, site: Path) -> None:
    await open_page(page, site, 1280)
    chips = await page.locator(".chip[data-group=levels]").all_inner_texts()
    assert any(classify.NOT_SPECIFIED in chip for chip in chips)
    result = await ask(page, "search_jobs", {"levels": [classify.NOT_SPECIFIED]})
    assert {j["id"] for j in result["jobs"]} == {"Acme|plain", "Acme|other", "Acme|mixed"}
    assert result["jobs"][0]["level"] == classify.NOT_SPECIFIED
    others = await ask(page, "search_jobs", {"roleTypes": [classify.OTHER]})
    assert {j["id"] for j in others["jobs"]} == {"Acme|other", "Acme|mixed"}


async def test_the_page_gets_the_level_name_from_the_build_so_it_cannot_drift(page: Page, site: Path) -> None:
    await open_page(page, site, 1280)
    meta = json.loads(await page.locator("#page-data").text_content() or "{}")
    assert meta["notSpecified"] == classify.NOT_SPECIFIED
    assert classify.NOT_SPECIFIED in meta["levels"]


def test_the_page_script_spells_the_role_type_the_way_the_classifier_does() -> None:
    script = (REPO / "templates" / "dashboard.js").read_text()
    assert f"const OTHER = '{classify.OTHER}';" in script  # the one place the page spells it


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
async def test_other_has_no_role_type_pill_but_keeps_its_hook_and_a_real_type_keeps_its_pill(
    page: Page, site: Path, width: int, scheme: str
) -> None:
    await open_page(page, site, width, scheme)
    only = card(page, "other")
    assert (
        await only.locator(".card-tags").text_content() == "Haifa, IsraelRegion: Tel Aviv area"
    )  # the places are left
    assert await only.get_attribute("data-role-types") == classify.OTHER
    assert await only.locator(".card-tags").is_visible()
    mixed = card(page, "mixed")  # real types plus Other: only the real one shows
    assert "Role type: Product" in (await mixed.locator(".card-tags").text_content() or "")
    assert "Role type: Other" not in (await mixed.locator(".card-tags").text_content() or "")
    assert await mixed.get_attribute("data-role-types") == f"Product|{classify.OTHER}"
    assert await card(page, "plain").locator(".card-tags .pill-soft", has_text="Role type: Product").count() == 1
    chips = await page.locator(".chip[data-group=types]").all_inner_texts()
    assert any(classify.OTHER in chip for chip in chips)
    assert await page.evaluate(NO_SIDEWAYS)


async def test_page_data_without_the_level_name_fails_loudly_instead_of_showing_every_pill(
    page: Page, site: Path
) -> None:
    html = (site / "index.html").read_text()
    meta = re.search(r'(<script[^>]*id="page-data"[^>]*>)(.*?)(</script>)', html, re.S)
    assert meta
    data = json.loads(meta[2].replace("\\u003c", "<"))
    del data["notSpecified"]
    (site / "index.html").write_text(html.replace(meta[0], meta[1] + json.dumps(data) + meta[3]))
    await serve(page, site, wait=False, backend=Backend(rows()))
    await page.locator("#loadError, .load-error").first.wait_for()
    assert await page.locator("#list article").count() == 0
    assert await page.locator("#loadError, .load-error").count() > 0
