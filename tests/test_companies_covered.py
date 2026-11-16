"""The count of companies the dashboard says it covers: computed in build_dashboard.py from data/companies.json at
build time, put in jobs.json, the page data and the search field, and never typed into a template."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

import build_dashboard
from tests.conftest import write_json
from tests.test_build_dashboard import log, prepare, read_payload

REPO = Path(__file__).resolve().parents[1]


def test_companies_covered_counts_the_working_feeds() -> None:
    companies = [
        {"name": "A", "status": "ok"},
        {"name": "B", "status": "ok"},
        {"name": "C", "status": "careers_page_only"},
        {"name": "D", "status": "not_found"},
    ]
    assert build_dashboard.companies_covered(companies) == len(["A", "B"])
    assert build_dashboard.companies_covered([]) == 0


@pytest.mark.parametrize(
    ("covered", "companies"),
    [(224, "224 companies"), (1, "1 company"), (0, "0 companies")],
)
def test_coverage_words(covered: int, companies: str) -> None:
    assert build_dashboard.coverage_words(covered) == {"__COVERED__": str(covered), "__COMPANIES__": companies}


def test_the_count_of_companies_reaches_the_page_and_the_data(db: sqlite3.Connection, workdir: Path) -> None:
    prepare(workdir)
    write_json(
        workdir / "data" / "companies.json",
        [
            {"name": "Zed", "status": "ok"},
            {"name": "Down", "status": "ok"},
            {"name": "Alpha", "status": "x"},
            {"name": "Gone", "status": "ok"},  # excluded below: on no list, so not counted either
        ],
    )
    (workdir / "data" / "excluded_companies.csv").write_text(
        "company,reason,date_added\nGone,asked to be removed,2026-01-01\n", encoding="utf-8"
    )
    log(db, "2024-01-01", "Down", 1)
    log(db, "2024-01-02", "Down", 0, "HTTP 403")
    log(db, "2024-01-03", "Down", 0, None)
    (workdir / "templates" / "dashboard.html").write_text(
        '<main data-companies-covered="__COVERED__">__COMPANIES__|__PAGE_DATA__</main>',
        encoding="utf-8",
    )
    db.close()
    build_dashboard.main()
    payload = read_payload(workdir)
    assert payload["covered"] == len(["Zed", "Down"])  # Down is failing today and also on the card; Alpha has no feed
    page = (workdir / "docs" / "index.html").read_text(encoding="utf-8")
    assert page.startswith('<main data-companies-covered="2">2 companies|')
    assert json.loads(page.split("|")[1].removesuffix("</main>"))["covered"] == payload["covered"]


def test_the_count_on_the_real_data_is_the_ok_status_of_companies_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counted here from the file alone (plain json and the exclusion list), not through the build's own count."""
    monkeypatch.chdir(REPO)
    excluded = build_dashboard.load_excluded()
    everyone = json.loads((REPO / "data" / "companies.json").read_text(encoding="utf-8"))
    ok = [c["name"] for c in everyone if c["status"] == "ok" and not build_dashboard.is_excluded(c["name"], excluded)]
    kept = [c for c in everyone if not build_dashboard.is_excluded(c["name"], excluded)]
    assert build_dashboard.companies_covered(kept) == len(ok)
    assert 0 < len(ok) < len(everyone)  # some companies have no working feed, and the claim is about the others


def test_no_template_or_doc_types_the_number_and_the_docs_say_where_to_find_it() -> None:
    template = (REPO / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "__COMPANIES__" in template
    assert 'data-companies-covered="__COVERED__"' in template
    assert re.search(r"\d+ compan", template) is None
    for name in ("README.md", "llms.txt"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "Coverage gaps" in text, name
        assert re.search(r"\d+ compan", text) is None, (
            name
        )  # a typed number goes stale; the search field is the live one
