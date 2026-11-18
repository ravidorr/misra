# ruff: noqa: F811  (the `site` fixture is imported by name, as in the other browser tests)
"""The Companies area of the admin page (PLAN_2.md step 3.3), run in a real browser against a fake of the `companies`
and `jobs` tables (tests/fake_companies.py). The records are a frozen fixture of invented companies
(tests/fixtures/admin_companies.json: 26 records, a Hebrew name, a very long one, four exclusions). No real account,
credential, company or Supabase project is used; any request the fake does not know is refused and recorded. Locally the
tests skip when Chromium is missing; in CI a missing browser is a failure."""

import asyncio
import datetime
import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Locator, Page, expect

import adapters
import build_dashboard
from tests.fake_companies import SECRET
from tests.test_admin_page import (
    AXE,
    AXE_RUN,
    DESKTOP,
    PHONE,
    TAP_MIN,
    Backend,
    open_admin,
    site,  # noqa: F401  (a fixture)
    state,
)

ROOT = Path(__file__).resolve().parent.parent
ROWS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "admin_companies.json").read_text("utf-8")
)
JOBS = (
    [("Acme Robotics", "open")] * 3
    + [("Acme Robotics", "closed")] * 2
    + [("Phi Mobile", "open")]
    + [("Omega Retail", "closed")] * 2
    + [("Old Example Ltd", "open")]
    + [("Sample Gone A", "open")]
)
TOTAL, CRAWLED, EXCLUDED = 26, 18, 4
LONG = (
    "Internationally Distributed Multinational Telecommunications "
    + "Infrastructure Holdings Corporation of Greater Tel Aviv"
)
HEBREW = "חברת בדיקה"
CHIPS = {"all": 26, "crawled": 18, "card": 3, "pending": 1, "excluded": 4}
WORKFLOW = "https://github.com/ravidorr/misra/actions/workflows/discover-companies.yml"
CALL = ("GET", "/rest/v1/companies")
CHECK = ("POST", "/rest/v1/rpc/is_admin")
MIGRATION = ROOT / "supabase" / "migrations" / "20261130100000_companies.sql"
ADMIN_JS = (ROOT / "templates" / "admin.js").read_text(encoding="utf-8")
TODAY = datetime.datetime.now().astimezone().date().isoformat()
CHANGED_AGAIN = "changed somewhere else"
BLANKS = dict.fromkeys(
    ("domain", "segment", "careers_url", "ats", "slug", "source", "status", "israel_only", "verify", "links", "gap")
)


# ---- the sentences and the lists copied into the page ----


def migration_names() -> set[str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    checks = re.findall(r"^\s*constraint (companies_\w+) check", sql, re.MULTILINE)
    indexes = re.findall(r"create unique index (companies_\w+) on", sql)
    triggers = re.findall(r"constraint = '(companies_\w+)'", sql)
    return {*checks, *indexes, *triggers}


def test_every_constraint_of_the_migration_has_a_sentence_and_nothing_else_has() -> None:
    mapped = set(re.findall(r"^  (companies_\w+): \[", ADMIN_JS, re.MULTILINE))
    assert len(migration_names()) == 14, "the migration names 14 rules: update this test with the migration"  # noqa: PLR2004
    assert mapped == migration_names(), "admin.js CONSTRAINTS differs from the constraints the migration names"


def test_the_sentences_are_plain_and_leak_no_sql() -> None:
    block = ADMIN_JS[ADMIN_JS.index("const CONSTRAINTS = {") : ADMIN_JS.index("/* The inputs each dialog")]
    for sentence in re.findall(r"\['\w*', '([^']+)'\]", block):
        assert sentence.endswith("."), sentence
        assert not re.search(r"constraint|violat|relation|companies_|row", sentence, re.IGNORECASE), sentence


def test_the_gap_reasons_of_the_page_are_the_keys_and_labels_of_REASONS() -> None:
    block = ADMIN_JS[ADMIN_JS.index("const GAP_REASONS = {") : ADMIN_JS.index("const SEGMENTS")]
    found = dict(re.findall(r"^  (\w+): '([^']+)',$", block, re.MULTILINE))
    assert found == build_dashboard.REASONS


def quoted_names(block: str) -> list[str]:
    return re.findall(r"'(\w+)'", block)


def test_the_feeds_a_pin_can_name_are_the_adapters_plus_browser_and_the_migrations_check() -> None:
    block = ADMIN_JS[ADMIN_JS.index("const PIN_ATS = [") : ADMIN_JS.index("/* The form of the slug")]
    page_list = quoted_names(block[block.index("[") : block.index("];")])
    sql = MIGRATION.read_text(encoding="utf-8")
    check = sql[sql.index("ats = '' or ats in (") : sql.index("constraint companies_excluded_keys")]
    assert page_list == [*adapters.ADAPTERS, "browser"]
    assert page_list == quoted_names(check.split("ats in (", 1)[1])


def test_a_slug_hint_exists_only_for_a_feed_the_page_lists() -> None:
    block = ADMIN_JS[ADMIN_JS.index("const PIN_HINTS = {") : ADMIN_JS.index("const PIN_ADDRESS")]
    hinted = set(re.findall(r"^  (\w+): '", block, re.MULTILINE))
    listed = {*adapters.ADAPTERS, "browser"}
    assert hinted <= listed
    assert {"workday", "oraclehcm", "browser"} <= hinted  # the formats CONTRIBUTING.md documents


def test_the_fixture_is_valid_and_not_live_data() -> None:
    assert len(ROWS) == TOTAL
    names = {row["name"] for row in ROWS}
    live = {
        row["name"] for row in json.loads((Path(__file__).parent / "fixtures" / "companies.json").read_text("utf-8"))
    }
    assert names.isdisjoint(live), "the admin fixture must hold invented companies only"
    assert sum(1 for row in ROWS if row["excluded"]) == EXCLUDED


# ---- helpers ----


async def open_companies(page: Page, docs: Path, backend: Backend | None = None, **kwargs: Any) -> Backend:
    backend = backend or Backend(companies=ROWS, jobs=JOBS)
    await open_admin(page, docs, backend=backend, hash_="#companies", **kwargs)
    if await state(page) == "ready":
        await expect(page.locator("#companies")).not_to_have_attribute("data-state", "loading")
    return backend


def card(page: Page, name: str) -> Locator:
    return page.locator(".company-card").filter(has=page.get_by_role("heading", name=name, exact=True))


def edit_button(page: Page, name: str) -> Locator:
    return card(page, name).get_by_role("button", name=f"Edit {name}", exact=True)


async def names(page: Page) -> list[str]:
    return await page.locator(".company-card .crawl-name").all_inner_texts()


def chip(page: Page, view: str) -> Locator:
    return page.locator(f"#companies .chip[data-view='{view}']")


def editor(page: Page) -> Locator:
    return page.locator("#companyEditor")


def field(page: Page, label: str) -> Locator:
    return page.locator("#companyEditor").get_by_label(label, exact=True)


def confirm(page: Page) -> Locator:
    return page.locator("#companyConfirm")


async def open_editor(page: Page, name: str) -> None:
    await edit_button(page, name).click()
    await expect(editor(page)).to_be_visible()


def writes(backend: Backend) -> list[tuple[str, dict[str, Any] | None, dict[str, list[str]]]]:
    return backend.tables.writes


def row_of(backend: Backend, name: str) -> dict[str, Any]:
    return next(r for r in backend.tables.companies if r["name"] == name)


async def toast(page: Page) -> str:
    return await page.locator(".company-saved").inner_text()


# ---- what it asks and when ----


async def test_only_the_check_and_one_read_of_the_list_are_asked_and_nothing_else(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    assert backend.requests == [CHECK, CALL]
    assert backend.tables.reads == ["companies"]
    assert backend.strays == []
    assert writes(backend) == []  # nothing is written before an admin acts


async def test_a_non_admin_never_loads_the_list(page: Page, site: Path) -> None:
    backend = await open_companies(page, site, Backend("not_admin", companies=ROWS))
    assert await state(page) == "not-admin"
    assert backend.requests == [CHECK]


async def test_a_refused_list_is_checked_again_then_shows_the_error_with_try_again(page: Page, site: Path) -> None:
    backend = Backend(companies=ROWS)
    backend.tables.list_status = 401
    await open_companies(page, site, backend)
    await expect(page.locator("#companies .admin-state.notice")).to_have_text(re.compile("Could not load this area"))
    assert backend.tables.reads == ["companies", "companies"]
    backend.tables.list_status = 200
    await page.get_by_role("button", name="Try again").click()
    await expect(page.locator(".company-card").first).to_be_visible()


async def test_an_empty_table_says_nothing_here_yet(page: Page, site: Path) -> None:
    await open_companies(page, site, Backend())
    await expect(page.locator("#companies")).to_have_attribute("data-state", "empty")


# ---- the list ----


async def test_the_list_shows_every_company_with_counts_a_status_line_and_the_states(page: Page, site: Path) -> None:
    await open_companies(page, site)
    await expect(page.locator(".crawl-headline")).to_have_text(
        f"{TOTAL} companies: {CRAWLED} crawled, {EXCLUDED} excluded."
    )
    for view, count in CHIPS.items():
        await expect(chip(page, view)).to_contain_text(f"({count})")
    await expect(page.get_by_role("status").filter(has_text="Showing")).to_have_text(f"Showing {TOTAL} of {TOTAL}")
    listed = await names(page)
    assert len(listed) == TOTAL
    assert listed == sorted(listed, key=str.lower) or listed[0] == "Acme Robotics"
    assert await card(page, "Acme Robotics").get_attribute("data-state") == "crawled"
    assert await card(page, "Phi Mobile").get_attribute("data-state") == "card"
    assert await card(page, "Upsilon Works").get_attribute("data-state") == "pending"
    assert await card(page, "Omega Retail").get_attribute("data-state") == "excluded"
    phi = await card(page, "Phi Mobile").inner_text()
    assert "Roles are listed, but we cannot read them yet" in phi  # the label of the reason, not its key
    assert "Roles are in a script we cannot read." in phi
    assert await card(page, "Phi Mobile").get_by_role("link", name="Career page").get_attribute("href") == (
        "https://phi.test/il/careers"
    )
    assert "No roles in Israel; asked to be removed." in await card(page, "Omega Retail").inner_text()
    assert "Not run yet" in await card(page, "Upsilon Works").inner_text()
    assert await card(page, HEBREW).locator(".crawl-name").get_attribute("dir") == "auto"


async def test_the_page_says_a_new_company_waits_for_the_workflow_and_links_to_it(page: Page, site: Path) -> None:
    await open_companies(page, site)
    note = page.locator("#companies .crawl-note")
    await expect(note).to_contain_text(
        "A company you add is crawled only after the discover-companies workflow has run"
    )
    link = page.locator(".company-toolbar").get_by_role("link", name="Open the workflow")
    assert await link.get_attribute("href") == WORKFLOW
    assert await link.get_attribute("target") == "_blank"
    assert "noopener" in (await link.get_attribute("rel") or "")


async def test_the_search_matches_name_or_domain_and_the_chips_filter_by_state(page: Page, site: Path) -> None:
    await open_companies(page, site)
    await page.get_by_role("searchbox", name="Search companies").fill("acme")
    assert await names(page) == ["Acme Robotics"]
    await expect(page.get_by_role("status").filter(has_text="Showing")).to_have_text(f"Showing 1 of {TOTAL}")
    await page.get_by_role("searchbox", name="Search companies").fill("PHI.TEST")
    assert await names(page) == ["Phi Mobile"]
    await page.get_by_role("searchbox", name="Search companies").fill("nothing like this")
    await expect(page.get_by_text("No companies match.")).to_be_visible()
    await page.get_by_role("searchbox", name="Search companies").fill("")
    await chip(page, "excluded").click()
    await expect(chip(page, "excluded")).to_have_attribute("aria-pressed", "true")
    assert sorted(await names(page)) == ["Old Example Ltd", "Omega Retail", "Sample Gone A", "Sample Gone B"]
    await chip(page, "card").click()
    assert sorted(await names(page)) == ["Chi Networks", "Phi Mobile", "Psi Defence"]
    await expect(chip(page, "excluded")).to_have_attribute("aria-pressed", "false")


# ---- edit ----


async def test_edit_opens_one_dialog_with_the_values_and_the_name_cannot_change(page: Page, site: Path) -> None:
    await open_companies(page, site)
    await open_editor(page, "Delta Security")
    dialog = page.get_by_role("dialog", name="Edit Delta Security")
    await expect(dialog).to_be_visible()
    await expect(field(page, "Name")).to_have_value("Delta Security")
    assert await field(page, "Name").get_attribute("readonly") is not None
    await expect(field(page, "Domain")).to_have_value("delta.test")
    await expect(field(page, "Segment")).to_have_value("cyber_infra")
    await expect(field(page, "Global career page")).to_have_value("https://delta.test/careers")
    await expect(page.get_by_role("radio", name="Crawl it")).to_be_checked()
    await expect(page.locator("#cardFields")).to_be_hidden()
    await expect(page.locator("#discovered")).to_contain_text("lever")  # what discovery found is read-only text
    await expect(page.get_by_role("button", name="Exclude…")).to_be_visible()
    await expect(page.get_by_role("button", name="Remove…")).to_be_visible()
    await expect(page.locator("#editorTitle")).to_be_focused()


async def test_saving_an_edit_sends_only_the_editable_fields_for_that_row_and_that_stamp(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await field(page, "Segment").select_option("multinational")
    await field(page, "Israel career page").fill("https://acme.test/il")
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    [(method, body, query)] = writes(backend)
    assert method == "PATCH"
    assert body == {
        "domain": "acme.test",
        "segment": "multinational",
        "crawl": True,
        "israel_only": True,
        "verify": None,
        "links": {"israel": "https://acme.test/il"},
        "gap": None,
    }
    assert query["id"] == ["eq.1"]
    assert query["updated_at"] == ["eq.2026-11-18T08:00:00+00:00"]
    assert await toast(page) == "Saved. Takes effect at the next daily run."
    assert "Multinational" in await card(page, "Acme Robotics").inner_text()
    await expect(edit_button(page, "Acme Robotics")).to_be_focused()  # the focus is back where it started
    assert backend.tables.reads == ["companies"]  # a plain edit counts no jobs
    assert backend.strays == []


async def test_the_edit_that_sends_a_company_back_to_discovery_says_so_and_clears_its_status(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Beta Labs")
    await page.get_by_role("checkbox", name=re.compile("Find its job feed again")).check()
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    assert writes(backend)[0][1]["status"] == ""  # type: ignore[index]
    assert "discover-companies workflow has run" in await toast(page)
    assert await page.locator(".company-saved a").get_attribute("href") == WORKFLOW
    assert await card(page, "Beta Labs").get_attribute("data-state") == "pending"


async def test_a_saved_stamp_that_moved_is_refused_with_a_plain_sentence(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Epsilon Data")
    row_of(backend, "Epsilon Data")["updated_at"] = "2026-11-18T09:59:00+00:00"  # a discovery push landed meanwhile
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(page.locator("#editorNotice")).to_contain_text(CHANGED_AGAIN)
    await expect(editor(page)).to_be_visible()


async def test_a_company_that_leaves_the_crawl_asks_first_and_counts_the_open_jobs(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await expect(page.locator("#cardFields")).to_be_visible()
    await field(page, "Why it is not crawled").select_option("needs_reader")
    await field(page, "Note for the card").fill("Script only.")
    await page.get_by_role("button", name="Save", exact=True).click()
    dialog = page.get_by_role("dialog", name="Take Acme Robotics off the crawl?")
    await expect(dialog).to_be_visible()
    assert writes(backend) == []  # nothing is written until the admin says yes
    await expect(page.get_by_role("button", name="Cancel").and_(page.locator("#confirmCancel"))).to_be_focused()
    await expect(page.locator("#confirmCount")).to_have_text("3 open jobs will be closed at the next daily run.")
    assert backend.tables.reads == ["companies", "jobs-count"]  # open jobs only
    await page.get_by_role("button", name="Move to the card").click()
    await expect(confirm(page)).to_be_hidden()
    await expect(editor(page)).to_be_hidden()
    [(_method, body, _query)] = writes(backend)
    assert body["crawl"] is False  # type: ignore[index]
    assert body["gap"] == {"reason": "needs_reader", "note": "Script only."}  # type: ignore[index]
    assert await card(page, "Acme Robotics").get_attribute("data-state") == "card"
    await expect(edit_button(page, "Acme Robotics")).to_be_focused()


async def test_cancelling_the_leave_question_returns_to_the_editor_and_writes_nothing(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await field(page, "Why it is not crawled").select_option("needs_reader")
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(confirm(page)).to_be_visible()
    await page.locator("#confirmCancel").click()
    await expect(confirm(page)).to_be_hidden()
    await expect(editor(page)).to_be_visible()
    assert writes(backend) == []
    await page.keyboard.press("Escape")
    await expect(editor(page)).to_be_hidden()
    await expect(edit_button(page, "Acme Robotics")).to_be_focused()


async def test_the_job_count_has_three_states_and_a_failed_count_still_allows_the_action(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    backend.tables.jobs_gate = asyncio.Event()
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await field(page, "Why it is not crawled").select_option("needs_reader")
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(page.locator("#confirmCount")).to_have_text("Counting jobs…")  # counting
    backend.tables.jobs_status = 500
    backend.tables.jobs_gate.set()
    await expect(page.locator("#confirmCount")).to_have_text("Could not count the jobs. You can still continue.")
    await page.get_by_role("button", name="Move to the card").click()
    await expect(confirm(page)).to_be_hidden()
    assert await card(page, "Acme Robotics").get_attribute("data-state") == "card"


async def test_a_missing_reason_is_named_at_its_field_in_the_editor_not_in_sql(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await page.get_by_role("button", name="Save", exact=True).click()
    await page.get_by_role("button", name="Move to the card").click()
    await expect(
        confirm(page)
    ).to_be_hidden()  # the field is in the editor: the question closes and the editor shows it
    reason = field(page, "Why it is not crawled")
    await expect(reason).to_be_focused()
    assert await reason.get_attribute("aria-invalid") == "true"
    await expect(page.locator("#cReasonError")).to_have_text("Choose why it is not crawled.")
    assert row_of(backend, "Acme Robotics")["crawl"] is True  # the database refused it
    body = await page.locator("body").inner_text()
    assert "violates" not in body
    assert "companies_gap_reason" not in body


async def test_moving_a_company_back_to_crawling_puts_it_in_the_discovery_queue(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Chi Networks")
    await page.get_by_role("radio", name="Crawl it").check()
    await expect(page.locator("#timingNote")).to_be_visible()
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    [(_method, body, _query)] = writes(backend)
    assert body["status"] == ""  # type: ignore[index]
    assert body["gap"] is None  # type: ignore[index]
    assert await card(page, "Chi Networks").get_attribute("data-state") == "pending"
    assert backend.tables.reads == ["companies"]  # coming back asks no count


# ---- add ----


async def test_add_opens_an_empty_editor_that_says_a_new_company_waits_for_discovery(page: Page, site: Path) -> None:
    await open_companies(page, site)
    await page.get_by_role("button", name="Add a company").click()
    await expect(page.get_by_role("dialog", name="Add a company")).to_be_visible()
    await expect(field(page, "Name")).to_be_focused()
    assert await field(page, "Name").get_attribute("readonly") is None
    await expect(page.get_by_role("radio", name="Crawl it")).to_be_checked()
    timing = page.locator("#timingNote")
    await expect(timing).to_contain_text("Not crawled until discovery has run")
    assert await timing.get_by_role("link").get_attribute("href") == WORKFLOW
    await expect(page.get_by_role("button", name="Add company", exact=True)).to_be_visible()
    await expect(page.locator("#editorMore")).to_be_hidden()  # a new record has nothing to exclude or remove


async def test_adding_a_company_names_a_taken_domain_at_its_field_then_saves_with_the_discovered_fields_empty(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await page.get_by_role("button", name="Add a company").click()
    await field(page, "Name").fill("  Fresh Company  ")
    await field(page, "Domain").fill("acme.test")
    await field(page, "Israel career page").fill("https://fresh.test/il")
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(page.locator("#cDomainError")).to_have_text("A company with this domain already exists.")
    await expect(field(page, "Domain")).to_be_focused()
    assert await field(page, "Domain").get_attribute("aria-invalid") == "true"
    assert SECRET not in await page.locator("body").inner_text()  # the error's details quote a row: never shown
    await field(page, "Domain").fill("fresh.test")
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    method, body, _query = writes(backend)[-1]
    assert method == "POST"
    assert body == {
        "domain": "fresh.test",
        "segment": None,
        "crawl": True,
        "israel_only": None,
        "verify": None,
        "links": {"israel": "https://fresh.test/il"},
        "gap": None,
        "name": "Fresh Company",
        "careers_url": "",
        "ats": "",
        "slug": "",
        "source": "",
        "status": "",
    }
    assert "discover-companies workflow has run" in await toast(page)
    assert await card(page, "Fresh Company").get_attribute("data-state") == "pending"
    await expect(edit_button(page, "Fresh Company")).to_be_focused()


async def test_adding_shows_the_new_company_even_when_a_filter_would_hide_it(page: Page, site: Path) -> None:
    await open_companies(page, site)
    await chip(page, "excluded").click()
    await page.get_by_role("searchbox", name="Search companies").fill("omega")
    await page.get_by_role("button", name="Add a company").click()
    await field(page, "Name").fill("Hidden Else")
    await field(page, "Domain").fill("hidden.test")
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(card(page, "Hidden Else")).to_be_visible()
    await expect(page.get_by_role("searchbox", name="Search companies")).to_have_value("")


@pytest.mark.parametrize(
    ("name", "domain", "sentence", "where"),
    [
        ("A", "short.test", "The name needs at least 2 characters, with no space at either end.", "#cNameError"),
        ("Acme robotics", "other.test", "A company with this name already exists.", "#cNameError"),
        ("No Domain", "", "A domain is required.", "#cDomainError"),
    ],
)
async def test_each_refused_new_record_gets_its_own_sentence(  # noqa: PLR0913, PLR0917
    page: Page, site: Path, name: str, domain: str, sentence: str, where: str
) -> None:
    await open_companies(page, site)
    await page.get_by_role("button", name="Add a company").click()
    await field(page, "Name").fill(name)
    await field(page, "Domain").fill(domain)
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(page.locator(where)).to_have_text(sentence)


async def test_a_company_added_to_the_card_needs_a_reason_and_a_taken_career_page_is_plain(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await page.get_by_role("button", name="Add a company").click()
    await field(page, "Name").fill("Carded Co")
    await field(page, "Domain").fill("carded.test")
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await expect(page.locator("#timingNote")).to_be_hidden()  # a company on the card is not crawled at all
    await field(page, "Israel career page").fill("http://carded.test/jobs")
    await field(page, "Why it is not crawled").select_option("no_feed_found")
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(page.locator("#editorNotice")).to_have_text("A career page address must start with https://.")
    await field(page, "Israel career page").fill("https://carded.test/jobs")
    await page.get_by_role("button", name="Add company", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    assert row_of(backend, "Carded Co")["gap"] == {"reason": "no_feed_found", "note": ""}
    assert await card(page, "Carded Co").get_attribute("data-state") == "card"
    assert "Takes effect at the next daily run" in await toast(page)


# ---- exclude ----


async def test_excluding_a_listed_company_previews_the_deletion_and_the_waiting_purges(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("button", name="Exclude…").click()
    await expect(editor(page)).to_be_hidden()
    dialog = page.get_by_role("dialog", name="Exclude Acme Robotics?")
    await expect(dialog).to_be_visible()
    await expect(page.locator("#confirmCancel")).to_be_focused()  # the safe button
    await expect(page.locator("#xDate")).to_have_value(TODAY)
    await expect(page.get_by_text("Write why, not who asked. No names or email addresses.")).to_be_visible()
    await expect(page.locator("#xNameGroup")).to_be_hidden()  # the company is known
    count = page.locator("#confirmCount")
    await expect(count).to_contain_text("5 jobs will be deleted at the next daily run.")  # open and closed
    await expect(count).to_contain_text("4 companies wait to be purged at that run.")  # 3 others, and this one
    await expect(count).to_contain_text("stops when more than 3 wait")
    await expect(page.locator("#confirmText")).to_contain_text("never deletes the record")
    assert page.locator("#confirmGo").get_by_text("Exclude")
    await page.get_by_role("button", name="Exclude", exact=True).click()
    await expect(page.locator("#xReasonError")).to_have_text("Write the reason.")  # the database's rule, in its words
    await expect(page.locator("#xReason")).to_be_focused()
    await page.locator("#xReason").fill("Asked to be removed.")
    await page.get_by_role("button", name="Exclude", exact=True).click()
    await expect(confirm(page)).to_be_hidden()
    [(method, body, query)] = writes(backend)[-1:]
    assert method == "PATCH"
    assert body == {
        **BLANKS,
        "crawl": False,
        "excluded": {"reason": "Asked to be removed.", "date_added": TODAY},
    }
    assert query["id"] == ["eq.1"]
    assert await card(page, "Acme Robotics").get_attribute("data-state") == "excluded"
    assert await toast(page) == "Excluded. Its jobs are deleted at the next daily run."
    assert backend.strays == []


async def test_excluding_a_name_that_is_not_listed_adds_the_minimal_record(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await page.get_by_role("button", name="Exclude a company").click()
    dialog = page.get_by_role("dialog", name="Exclude a company")
    await expect(dialog).to_be_visible()
    await expect(page.locator("#xNameGroup")).to_be_visible()
    await page.locator("#xName").fill("Never Listed Co")
    await page.locator("#xName").dispatch_event("change")
    await expect(page.locator("#confirmCount")).to_contain_text("0 jobs will be deleted")
    await page.locator("#xReason").fill("Asked by email.")
    await page.get_by_role("button", name="Exclude", exact=True).click()
    await expect(confirm(page)).to_be_hidden()
    [(method, body, _query)] = writes(backend)
    assert method == "POST"
    assert body is not None
    assert set(body) == {"name", "crawl", "excluded"}  # nothing else is sent: the other columns stay null
    assert body["name"] == "Never Listed Co"
    assert await card(page, "Never Listed Co").get_attribute("data-state") == "excluded"
    await expect(page.get_by_role("button", name="Exclude a company")).to_be_focused()


async def test_an_exclusion_that_would_pass_the_limit_is_refused_in_plain_words(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    backend.tables.fail = (
        400,
        json.dumps({"code": "23514", "message": "at most 200 excluded companies (companies_excluded_limit)"}),
    )
    await page.get_by_role("button", name="Exclude a company").click()
    await page.locator("#xName").fill("One Too Many")
    await page.locator("#xReason").fill("Because.")
    await page.get_by_role("button", name="Exclude", exact=True).click()
    await expect(page.locator("#confirmNotice")).to_have_text(
        "The list already holds 200 excluded companies, the most it can."
    )
    await expect(confirm(page)).to_be_visible()


async def test_an_excluded_record_cannot_be_removed_and_can_only_be_included_again(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Old Example Ltd")
    await expect(page.get_by_role("dialog", name="Include Old Example Ltd again")).to_be_visible()
    await expect(page.locator("#editorMore")).to_be_hidden()
    assert await page.get_by_role("button", name=re.compile("Remove|Delete")).count() == 0
    await expect(page.locator("#discovered")).to_contain_text("2026-09-14")
    await page.locator("#editorSave").click()
    await expect(page.get_by_role("dialog", name="Include Old Example Ltd again?")).to_contain_text(
        "erases the exclusion"
    )
    await expect(page.locator("#confirmCancel")).to_be_focused()  # one click does not undo the opt-out
    await page.locator("#confirmGo").click()
    await expect(page.locator("#cDomainError")).to_have_text("A domain is required.")  # the record has none
    await field(page, "Domain").fill("old-example.test")
    await page.locator("#editorSave").click()
    before = len(writes(backend))
    await page.locator("#confirmCancel").click()
    assert len(writes(backend)) == before
    await page.locator("#editorSave").click()
    await page.locator("#confirmGo").click()
    await expect(editor(page)).to_be_hidden()
    body = writes(backend)[-1][1]
    assert body is not None
    assert body["excluded"] is None
    assert body["crawl"] is True
    assert body["status"] == ""
    assert "name" not in body  # the name is the key: it is never sent again
    assert await card(page, "Old Example Ltd").get_attribute("data-state") == "pending"


# ---- remove ----


async def test_removing_a_company_says_its_jobs_stay_and_deletes_the_row_by_id_and_stamp(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.get_by_role("button", name="Remove…").click()
    dialog = page.get_by_role("dialog", name="Remove Acme Robotics from the list?")
    await expect(dialog).to_be_visible()
    await expect(page.locator("#confirmFields")).to_be_hidden()
    await expect(page.locator("#confirmCount")).to_have_text("3 open jobs stay in the history.")
    await expect(page.locator("#confirmText")).to_contain_text("no run closes its open jobs")
    await page.get_by_role("button", name="Remove", exact=True).click()
    await expect(confirm(page)).to_be_hidden()
    [(method, body, query)] = writes(backend)
    assert (method, body, query["id"]) == ("DELETE", None, ["eq.1"])
    assert "updated_at" in query
    assert "Acme Robotics" not in await names(page)
    assert await toast(page) == "Removed from the list."
    await expect(
        page.locator("#companiesTitle")
    ).to_be_focused()  # the row is gone: the focus goes to the area's heading


async def test_removing_a_company_changed_elsewhere_is_refused(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Beta Labs")
    await page.get_by_role("button", name="Remove…").click()
    row_of(backend, "Beta Labs")["updated_at"] = "2026-11-18T09:58:00+00:00"
    await page.get_by_role("button", name="Remove", exact=True).click()
    await expect(page.locator("#confirmNotice")).to_contain_text(CHANGED_AGAIN)
    assert "Beta Labs" in [r["name"] for r in backend.tables.companies]


# ---- errors ----


@pytest.mark.parametrize(
    ("answer", "sentence"),
    [
        (
            (500, "<html>internal error select * from companies</html>"),
            "Could not save. Try again, or reload the page.",
        ),
        (
            (400, json.dumps({"code": "22008", "message": 'date/time field value out of range: "2026-02-30"'})),
            "Could not save. Try again, or reload the page.",
        ),
        ((401, '{"message":"JWT expired"}'), "Your sign-in expired. Reload the page"),
        (
            (403, json.dumps({"code": "42501", "message": "new row violates row-level security policy"})),
            "may not change",
        ),
    ],
    ids=["server-error", "unknown-database-error", "expired", "not-allowed"],
)
async def test_an_error_the_map_does_not_know_gets_a_generic_sentence_never_the_raw_text(
    page: Page, site: Path, answer: tuple[int, str], sentence: str
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Zeta AI")
    backend.tables.fail = answer
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(page.locator("#editorNotice")).to_contain_text(sentence)
    body = await page.locator("body").inner_text()
    for raw in ("select *", "out of range", "row-level", "violates", SECRET):
        assert raw not in body
    await expect(editor(page)).to_be_visible()


async def test_a_dialog_that_is_saving_ignores_escape_a_second_save_and_close(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    backend.tables.write_gate = asyncio.Event()
    await open_editor(page, "Zeta AI")
    save = page.get_by_role("button", name="Save", exact=True)
    await save.click()
    await expect(save).to_have_attribute("aria-disabled", "true")
    await save.click(force=True)  # a button that is aria-disabled still receives the click: the page must ignore it
    await page.keyboard.press("Escape")
    await page.locator("#editorClose").click()
    await expect(editor(page)).to_be_visible()
    backend.tables.write_gate.set()
    await expect(editor(page)).to_be_hidden()
    assert len(writes(backend)) == 1


# ---- the editor as a dialog ----


async def test_escape_and_the_corner_close_the_editor_and_the_focus_goes_back_to_its_row(
    page: Page, site: Path
) -> None:
    await open_companies(page, site)
    for close in ("Escape", "corner", "cancel"):
        await open_editor(page, "Gamma Cloud")
        if close == "Escape":
            await page.keyboard.press("Escape")
        elif close == "corner":
            await page.get_by_role("button", name="Close").click()
        else:
            await page.get_by_role("button", name="Cancel").click()
        await expect(editor(page)).to_be_hidden()
        await expect(edit_button(page, "Gamma Cloud")).to_be_focused()


async def test_the_keyboard_reaches_every_control_of_the_editor_in_order_and_stays_inside(
    page: Page, site: Path
) -> None:
    await open_companies(page, site)
    await open_editor(page, "Phi Mobile")
    seen: list[str] = []
    for _ in range(30):
        await page.keyboard.press("Tab")
        seen.append(
            await page.evaluate("document.activeElement.id || document.activeElement.getAttribute('name') || ''")
        )
    assert await page.evaluate(
        "document.querySelector('#companyEditor').contains(document.activeElement)"
    )  # never left
    for wanted in (
        "cName",
        "cDomain",
        "cCard",
        "cReason",
        "cNote",
        "cSegment",
        "cIsrael",
        "cLinkIsrael",
        "editorSave",
    ):
        assert wanted in seen, wanted
    assert seen.index("cDomain") < seen.index("cReason") < seen.index("cSegment") < seen.index("editorSave")


# ---- looks ----


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [PHONE, DESKTOP], ids=["phone", "desktop"])
@pytest.mark.parametrize("which", ["list", "editor", "card-editor", "pin", "unlock", "exclude", "remove", "add"])
async def test_the_area_and_its_dialogs_are_accessible_fit_the_phone_and_have_44px_targets(
    page: Page, site: Path, which: str, size: dict[str, int], scheme: str
) -> None:
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.set_viewport_size(size)  # type: ignore[arg-type]
    backend = Backend(companies=[*ROWS, {**ROWS[0], "id": 99, "name": LONG, "domain": "long.test"}], jobs=JOBS)
    await open_companies(page, site, backend)
    if which == "editor":
        await open_editor(page, "Acme Robotics")
    elif which == "card-editor":
        await open_editor(page, "Phi Mobile")
    elif which == "pin":
        await open_editor(page, "Gamma Cloud")
        await pin_slug(page).fill("a b")
        await save(page)
        await expect(page.locator("#cPinSlugError")).to_be_visible()
    elif which == "unlock":
        await open_editor(page, "Phi Mobile")
        await page.get_by_role("radio", name="Crawl it").check()
        await pin_ats(page).select_option("oraclehcm")
    elif which == "add":
        await page.get_by_role("button", name="Add a company").click()
    elif which == "exclude":
        await open_editor(page, "Acme Robotics")
        await page.get_by_role("button", name="Exclude…").click()
        await page.locator("#xReason").fill("x")
        await page.locator("#xReason").fill("")
        await page.get_by_role("button", name="Exclude", exact=True).click()
        await expect(page.locator("#xReasonError")).to_be_visible()
    elif which == "remove":
        await open_editor(page, "Acme Robotics")
        await page.get_by_role("button", name="Remove…").click()
        await expect(page.locator("#confirmCount")).to_contain_text("open jobs")
    await page.evaluate(AXE.read_text(encoding="utf-8"))
    assert await page.evaluate(AXE_RUN) == []
    assert await page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    scope = "dialog[open]" if which != "list" else "main"
    for target in await page.locator(f"{scope} a:visible, {scope} button:visible").all():
        box = await target.bounding_box()
        assert box
        assert box["height"] >= TAP_MIN, await target.inner_text()
        assert box["width"] >= TAP_MIN, await target.inner_text()
    if which in ("editor", "card-editor", "pin", "unlock", "add"):
        for control in await page.locator("dialog[open] input:visible, dialog[open] select:visible").all():
            box = await control.bounding_box()
            assert box
            kind = await control.get_attribute("type")
            if kind not in ("radio", "checkbox"):  # a radio's label is the target
                assert box["height"] >= TAP_MIN


async def test_on_a_phone_the_save_bar_stays_in_view_while_the_fields_scroll(page: Page, site: Path) -> None:
    await page.set_viewport_size({"width": 375, "height": 500})
    await open_companies(page, site)
    await open_editor(page, "Phi Mobile")
    save = page.get_by_role("button", name="Save", exact=True)
    head = page.locator("#companyEditor .sheet-head")
    for _ in range(2):
        box = await save.bounding_box()
        top = await head.bounding_box()
        assert box
        assert top
        assert box["y"] >= 0 and box["y"] + box["height"] <= 500  # noqa: PLR2004
        assert top["y"] >= 0
        await page.evaluate("document.querySelector('#companyEditor').scrollTo(0, 99999)")
    await expect(page.locator("#editorNotice")).to_be_hidden()


async def test_the_trust_checkbox_shows_only_for_a_feed_pinned_by_hand(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await expect(page.locator("#verifyRow")).to_be_hidden()  # discover.py ignores it for a discovered feed
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(page.locator("#companyEditor")).not_to_be_visible()  # the write is answered before the editor closes
    assert writes(backend)[0][1]["verify"] is None  # type: ignore[index]
    await open_editor(page, "Gamma Cloud")
    await expect(page.locator("#verifyRow")).to_be_visible()
    await expect(page.get_by_role("checkbox", name=re.compile("Trust its job board"))).to_be_checked()
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(page.locator("#companyEditor")).not_to_be_visible()
    assert writes(backend)[1][1]["verify"] is False  # type: ignore[index]


async def test_a_change_made_here_shows_in_the_log_when_it_is_next_opened(page: Page, site: Path) -> None:
    backend = Backend(companies=ROWS, jobs=JOBS)
    await open_admin(page, site, backend=backend, hash_="#log")
    await expect(page.locator("#log .admin-state")).to_have_text("No changes yet.")
    await page.get_by_role("link", name="Companies").click()
    await expect(page.locator("#companies")).to_have_attribute("data-state", "ready")
    await open_editor(page, "Acme Robotics")
    await field(page, "Segment").select_option("multinational")
    await page.get_by_role("button", name="Save", exact=True).click()
    await expect(editor(page)).to_be_hidden()
    await page.get_by_role("link", name="Log").click()
    await expect(page.locator("#log .log-what")).to_have_text(["Company changed: Acme"])
    assert backend.log_asked == 2  # noqa: PLR2004  (the first show, and the show after the change)


# ---- pinning the job feed ----


def pin_ats(page: Page) -> Locator:
    return page.locator("#cPinAts")


def pin_slug(page: Page) -> Locator:
    return page.locator("#cPinSlug")


async def save(page: Page) -> None:
    await page.get_by_role("button", name="Save", exact=True).click()


def only_empty_status(backend: Backend) -> None:
    """The page never writes a status other than empty, whatever the admin chose."""
    for _method, body, _query in writes(backend):
        assert body is None or body.get("status", "") == "", body


async def test_a_crawled_company_is_pinned_with_the_feed_by_hand_and_its_status_emptied(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await expect(page.locator("#pinGroup")).to_be_visible()
    await expect(pin_ats(page)).to_have_value("")  # a discovered feed is not prefilled as a pin
    await pin_ats(page).select_option("ashby")
    await pin_slug(page).fill("  acme-board  ")
    await save(page)
    await expect(editor(page)).to_be_hidden()
    [(method, body, query)] = writes(backend)
    assert method == "PATCH"
    assert body is not None
    assert (body["source"], body["ats"], body["slug"], body["status"]) == ("override", "ashby", "acme-board", "")
    assert query["updated_at"] == ["eq.2026-11-18T08:00:00+00:00"]
    only_empty_status(backend)
    assert "discover-companies workflow has run" in await toast(page)
    assert await card(page, "Acme Robotics").get_attribute("data-state") == "pending"
    await open_editor(page, "Acme Robotics")  # now a pin: the trust checkbox and the facts show
    await expect(page.locator("#verifyRow")).to_be_visible()
    await expect(pin_ats(page)).to_have_value("ashby")
    await expect(pin_slug(page)).to_have_value("acme-board")
    await expect(page.locator("#discovered")).to_contain_text("override")


async def test_the_pin_part_says_the_workflow_checks_it_and_a_workday_feed_may_fail_there(
    page: Page, site: Path
) -> None:
    await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    group = page.locator("#pinGroup")
    await expect(group).to_contain_text("discover-companies workflow")
    await expect(group).to_contain_text("Workday or Oracle feed may come back not ok")
    await expect(group).to_contain_text("never forced to ok")
    assert await group.get_by_role("link", name=re.compile("Open the workflow")).get_attribute("href") == WORKFLOW
    await expect(page.locator("#cPinHint")).to_have_text("Choose the feed first.")
    await pin_ats(page).select_option("workday")
    await expect(page.locator("#cPinHint")).to_contain_text("tenant/wdN/site")


async def test_the_pin_part_is_only_for_a_listed_company_that_is_crawled_or_becomes_crawled(
    page: Page, site: Path
) -> None:
    await open_companies(page, site)
    await page.get_by_role("button", name="Add a company").click()
    await expect(page.locator("#pinGroup")).to_be_hidden()
    await page.get_by_role("button", name="Cancel").click()
    await open_editor(page, "Phi Mobile")  # on the card: hidden until it is set to crawl
    await expect(page.locator("#pinGroup")).to_be_hidden()
    await page.get_by_role("radio", name="Crawl it").check()
    await expect(page.locator("#pinGroup")).to_be_visible()
    await page.get_by_role("radio", name=re.compile("Show it on the coverage card")).check()
    await expect(page.locator("#pinGroup")).to_be_hidden()


async def test_a_researched_company_is_unlocked_and_pinned_in_one_save_without_its_gap_and_segment(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Phi Mobile")
    await page.get_by_role("radio", name="Crawl it").check()
    await expect(page.locator("#segmentGroup")).to_be_hidden()
    await pin_ats(page).select_option("browser")
    await pin_slug(page).fill("https://phi.test/careers?location=Israel")
    await save(page)
    await expect(editor(page)).to_be_hidden()
    [(_method, body, _query)] = writes(backend)
    assert body is not None
    assert body["crawl"] is True
    assert body["gap"] is None
    assert body["segment"] is None
    assert (body["source"], body["ats"], body["status"]) == ("override", "browser", "")
    only_empty_status(backend)
    saved = row_of(backend, "Phi Mobile")
    assert saved["crawl"] is True
    assert saved["gap"] is None
    assert saved["segment"] is None
    assert await card(page, "Phi Mobile").get_attribute("data-state") == "pending"


async def test_unlocking_without_a_pin_still_drops_the_segment_and_empties_the_status(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Phi Mobile")
    await page.get_by_role("radio", name="Crawl it").check()
    await save(page)
    await expect(editor(page)).to_be_hidden()
    body = writes(backend)[0][1]
    assert body is not None
    assert (body["segment"], body["gap"], body["status"]) == (None, None, "")
    assert "ats" not in body
    only_empty_status(backend)


@pytest.mark.parametrize(
    ("ats", "slug", "text", "where"),
    [
        ("ashby", "", "Choose the job feed and write its slug", "#cPinAtsError"),
        ("", "acme", "Choose the job feed and write its slug", "#cPinAtsError"),
        ("ashby", "has a space", "no space", "#cPinSlugError"),
        ("ashby", "a<b>", "no space", "#cPinSlugError"),
        ("ashby", "x" * 301, "too long", "#cPinSlugError"),
        ("browser", "phi.test/careers", "starting with https://", "#cPinSlugError"),
        ("comeet_page", "http://phi.test/careers", "starting with https://", "#cPinSlugError"),
    ],
)
async def test_a_bad_pin_is_refused_by_the_page_in_a_plain_sentence_and_nothing_is_written(  # noqa: PLR0913, PLR0917
    page: Page, site: Path, ats: str, slug: str, text: str, where: str
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await pin_ats(page).select_option(ats)
    await pin_slug(page).fill(slug)
    await save(page)
    await expect(page.locator(where)).to_contain_text(text)
    await expect(editor(page)).to_be_visible()
    assert writes(backend) == []


async def test_a_feed_the_database_does_not_know_is_refused_in_a_plain_sentence_at_its_field(
    page: Page, site: Path
) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Acme Robotics")
    await page.evaluate(
        "() => { const o = document.createElement('option'); o.value = 'bogus'; o.textContent = 'bogus';"
        " document.querySelector('#cPinAts').append(o); }"
    )
    await pin_ats(page).select_option("bogus")
    await pin_slug(page).fill("acme")
    await save(page)
    await expect(page.locator("#cPinAtsError")).to_have_text("The job feed is not one the crawler knows.")
    await expect(pin_ats(page)).to_be_focused()
    assert row_of(backend, "Acme Robotics")["ats"] == "greenhouse"  # the database refused it
    body = await page.locator("body").inner_text()
    assert "violates" not in body
    assert "companies_ats_known" not in body
    assert SECRET not in body


async def test_a_pin_on_a_row_changed_somewhere_else_is_refused_and_writes_nothing(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Epsilon Data")
    await pin_ats(page).select_option("lever")
    await pin_slug(page).fill("epsilon")
    row_of(backend, "Epsilon Data")["updated_at"] = "2026-11-18T09:59:00+00:00"
    await save(page)
    await expect(page.locator("#editorNotice")).to_contain_text(CHANGED_AGAIN)
    assert row_of(backend, "Epsilon Data")["source"] == ""
    only_empty_status(backend)


async def test_a_saved_pin_that_is_not_changed_is_not_written_again(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Gamma Cloud")
    await expect(pin_ats(page)).to_have_value("workday")
    await expect(pin_slug(page)).to_have_value("gamma/wd1/external")
    await save(page)
    await expect(editor(page)).to_be_hidden()
    body = writes(backend)[0][1]
    assert body is not None
    assert not {"source", "ats", "slug", "status"} & body.keys()  # the status stays as the workflow set it
    assert row_of(backend, "Gamma Cloud")["status"] == "ok"


async def test_the_pin_of_a_company_is_removed_and_the_company_goes_back_to_discovery(page: Page, site: Path) -> None:
    backend = await open_companies(page, site)
    await open_editor(page, "Gamma Cloud")
    await page.get_by_role("checkbox", name=re.compile("Remove the pin")).check()
    await expect(page.locator("#pinFields")).to_be_hidden()
    await save(page)
    await expect(editor(page)).to_be_hidden()
    body = writes(backend)[0][1]
    assert body is not None
    assert (body["source"], body["ats"], body["slug"], body["status"]) == ("", "", "", "")
    only_empty_status(backend)
    await open_editor(page, "Acme Robotics")
    await expect(page.locator("#unpinRow")).to_be_hidden()  # nothing to remove from a discovered feed
