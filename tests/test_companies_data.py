"""data/companies.json is the one list of every company. These tests read the real file: a record that breaks a rule
here would crawl a site nobody chose, or drop a note from the coverage card."""

import json
from pathlib import Path
from typing import Any

import pytest

import build_dashboard
import run
from common import COMPANIES, crawled_companies, load_companies
from tests.conftest import write_json

REPO = Path(__file__).resolve().parents[1]
# every key a record may have, in the order discover.py and a person write them
KEYS = [
    "name",
    "domain",
    "segment",
    "crawl",
    "careers_url",
    "ats",
    "slug",
    "source",
    "status",
    "israel_only",
    "verify",
    "links",
    "gap",
]
STATUSES = {"ok", "careers_page_only", "not_found", "ats_failed"}


@pytest.fixture(scope="module")
def records() -> list[dict[str, Any]]:
    return json.loads((REPO / COMPANIES).read_text(encoding="utf-8"))


def test_the_file_is_sorted_by_name_and_ends_with_a_newline(records: list[dict[str, Any]]) -> None:
    assert (REPO / COMPANIES).read_text(encoding="utf-8").endswith("]\n")
    assert [r["name"] for r in records] == sorted((r["name"] for r in records), key=str.casefold)


def test_a_name_and_a_domain_name_one_company_only(records: list[dict[str, Any]]) -> None:
    for key in ("name", "domain"):
        values = [r[key].casefold() for r in records]
        assert sorted({v for v in values if values.count(v) > 1}) == [], key


def test_every_record_has_only_known_keys_in_order(records: list[dict[str, Any]]) -> None:
    for r in records:
        assert set(r) <= set(KEYS), r["name"]
        assert list(r) == [k for k in KEYS if k in r], r["name"]
        assert {"name", "domain", "crawl", "careers_url", "ats", "slug", "source", "status"} <= set(r), r["name"]
        assert isinstance(r["crawl"], bool), r["name"]  # a missing flag must not decide who is crawled
        assert r["status"] in STATUSES, r["name"]


def test_a_company_is_crawled_or_has_a_gap_never_both(records: list[dict[str, Any]]) -> None:
    for r in records:
        assert r["crawl"] != ("gap" in r), r["name"]
        if r["crawl"]:
            assert "segment" not in r, r["name"]
        else:
            assert r["gap"]["reason"] in build_dashboard.REASONS, r["name"]


def test_a_pinned_record_names_its_ats_and_slug(records: list[dict[str, Any]]) -> None:
    for r in records:
        if r["source"] == "override":
            assert r["ats"] and r["slug"], r["name"]
            assert r.get("verify") is not True, r["name"]  # `verify` only ever says false
        else:
            assert "verify" not in r, r["name"]  # only a pin is trusted without a check


def test_a_company_that_is_not_crawled_is_never_requested(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]
) -> None:
    asked: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: asked.append(slug) or [])
    row = {"ats": "fake", "status": "ok", "source": "override"}
    write_json(
        workdir / COMPANIES,
        [
            {**row, "name": "In", "slug": "in", "crawl": True},
            {**row, "name": "Out", "slug": "out", "crawl": False, "gap": {"reason": "needs_reader", "note": ""}},
        ],
    )
    run.main()
    assert asked == ["in"]


def test_the_loaders(workdir: Path) -> None:
    rows = [{"name": "A", "crawl": True}, {"name": "B", "crawl": False}, {"name": "C"}]
    write_json(workdir / COMPANIES, rows)
    assert load_companies() == rows
    assert [c["name"] for c in crawled_companies(rows)] == ["A", "C"]  # the real file always says it (tests above)
