"""The company list is a table in Supabase (PLAN_2.md step 3.2); the rules of a record are its database checks
(supabase/migrations, tested in supabase/tests). The pipeline reads a pulled scratch copy, data/companies.json. Here the
Python side only checks that a pulled file loads, who is crawled, and that a file nobody can trust stops the run. The
tests that want realistic records read the frozen copy, tests/fixtures/companies.json, never a live list."""

import json
import sqlite3
from pathlib import Path

import pytest

import common
import run
from common import COMPANIES, ExclusionError, crawled_companies, load_companies, load_excluded
from tests.conftest import FROZEN_COMPANIES, excluded_record, write_json

MANY = 300  # the frozen list holds 347


def test_the_frozen_list_loads_and_is_a_list_of_records_with_names() -> None:
    records = load_companies(str(FROZEN_COMPANIES))
    assert len(records) > MANY
    assert all(isinstance(r["name"], str) for r in records)
    assert load_excluded(str(FROZEN_COMPANIES)) == []  # the list had no exclusion when it was frozen
    assert 0 < len(crawled_companies(records)) < len(records)  # some are only on the coverage card


def test_the_keys_of_a_record_are_the_columns_a_pull_writes() -> None:
    keys = {k for r in load_companies(str(FROZEN_COMPANIES)) for k in r}
    assert keys <= set(common.RECORD_KEYS)
    assert "excluded" in common.RECORD_KEYS


def test_a_company_that_is_not_crawled_is_never_requested(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: asked.append(slug) or [])
    row = {"ats": "fake", "status": "ok", "source": "override"}
    write_json(
        workdir / COMPANIES,
        [
            {**row, "name": "In", "slug": "in", "crawl": True},
            {**row, "name": "Out", "slug": "out", "crawl": False, "gap": {"reason": "needs_reader", "note": ""}},
            {**excluded_record("Gone"), **row, "slug": "gone"},  # an excluded record is never requested either
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


def test_a_pulled_file_that_does_not_load_stops_the_run(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: asked.append(slug) or [])
    (workdir / COMPANIES).write_text(json.dumps([{"name": "In", "crawl": True}])[:-3], encoding="utf-8")  # cut short
    with pytest.raises(ExclusionError, match="cannot be read"):
        run.main()
    (workdir / COMPANIES).unlink()  # a pull that did not run
    with pytest.raises(ExclusionError, match="cannot be read"):
        run.main()
    assert asked == []
