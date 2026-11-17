"""data/companies.json is the one list of every company. These tests read the real file: a record that breaks a rule
here would crawl a site nobody chose, or drop a note from the coverage card."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import build_dashboard
import common
import run
from adapters import ADAPTERS
from common import COMPANIES, RECORD_KEYS, crawled_companies, load_companies
from tests.conftest import write_json

REPO = Path(__file__).resolve().parents[1]
KEYS = list(RECORD_KEYS)
STATUSES = {"ok", "careers_page_only", "not_found", "ats_failed"}


@pytest.fixture(scope="module")
def records() -> list[dict[str, Any]]:
    return json.loads((REPO / COMPANIES).read_text(encoding="utf-8"))


def test_the_file_is_written_the_way_discover_writes_it(records: list[dict[str, Any]]) -> None:
    """A hand edit in another format would make the next discover.py run a diff of the whole file."""
    text = (REPO / COMPANIES).read_text(encoding="utf-8")
    assert text == json.dumps(records, indent=1, ensure_ascii=False) + "\n"


def test_the_file_is_sorted_by_name(records: list[dict[str, Any]]) -> None:
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


def test_a_crawled_feed_names_an_ats_the_crawl_has_an_adapter_for(records: list[dict[str, Any]]) -> None:
    for r in records:
        if r["crawl"] and r["status"] == "ok":
            assert r["slug"], r["name"]
            assert r["ats"] in ADAPTERS or r["ats"] == "browser", r["name"]  # a typo in a pin fails here, not at 04:30
        assert r["source"] in {"override", "guess", "page", ""}, r["name"]
        assert r.get("israel_only", True) is True, r["name"]  # the flag is there or it is not
        if r["gap"]["reason"] == "no_careers_url" if "gap" in r else False:
            assert r["status"] == "not_found", r["name"]


def test_a_company_that_is_not_crawled_is_never_requested(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    check = sqlite3.connect(common.DB)
    assert [r[0] for r in check.execute("SELECT DISTINCT company FROM crawl_log")] == ["In"]
    check.close()


def test_the_loaders(workdir: Path) -> None:
    rows = [{"name": "A", "crawl": True}, {"name": "B", "crawl": False}, {"name": "C"}, {"name": "D", "crawl": "yes"}]
    write_json(workdir / COMPANIES, rows)
    assert load_companies() == rows
    assert [c["name"] for c in crawled_companies(rows)] == ["A"]  # only a true `crawl` is consent to request a site
