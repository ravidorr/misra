import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import run
from tests.conftest import write_json


def rec(name: str, reason: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "crawl": False,
        "careers_url": f"https://{name.lower()}.example/careers",
        "ats": "",
        "slug": "",
        "gap": {"reason": reason, "note": ""},
    }
    return base | kw


def job(location: str) -> dict[str, str]:
    return {"job_id": "1", "title": "T", "location": location, "url": "u", "team": "", "posted": ""}


def test_only_no_openings_and_crawl_failing_records_with_an_address_are_probed() -> None:
    everyone = [
        rec("Open", "no_openings"),
        rec("Failing", "crawl_failing"),
        rec("Protected", "bot_protection"),
        rec("Unusable", "site_unusable"),
        rec("Reader", "needs_reader"),
        rec("Nofeed", "no_feed_found"),
        rec("Nourl", "no_openings", careers_url=""),
        rec("Crawled", "no_openings", crawl=True),
        rec("Gone", "no_openings", excluded={"reason": "x", "date_added": "2026-01-01"}),
        rec("Listed", "no_openings"),
        {"name": "Plain", "crawl": False},
    ]
    names = [c["name"] for c in run.recheck_targets(everyone, ["listed"])]
    assert names == ["Open", "Failing"]


def test_a_known_board_goes_through_its_adapter_and_a_page_through_the_browser_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: [job("Tel Aviv")] if slug == "ok" else None)
    seen: list[list[str]] = []

    def crawl_many(urls: list[str]) -> Any:
        seen.append(urls)
        return iter([(u, [job("")] if "page" in u else None) for u in urls])

    monkeypatch.setattr(run, "crawl_many", crawl_many)
    targets = [
        rec("Board", "no_openings", ats="fake", slug="ok"),
        rec("Dead", "no_openings", ats="fake", slug="bad"),
        rec("Page", "no_openings", careers_url="https://page.example/c"),
        rec("Twin", "no_openings", careers_url="https://page.example/c"),
        rec("Shell", "no_openings", ats="browser", slug="https://shell.example/jobs"),
    ]
    found = run.recheck_jobs(targets)
    assert found == {
        "Board": [job("Tel Aviv")],
        "Dead": None,
        "Page": [job("")],
        "Twin": [job("")],
        "Shell": None,
    }
    assert seen == [["https://page.example/c", "https://shell.example/jobs"]]


def test_a_crash_keeps_what_was_read_before_it_and_a_record_without_a_slug_uses_its_address(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        workdir / "data" / "companies.json",
        [
            rec("Board", "no_openings", ats="fake", slug="ok"),
            rec("Shell", "no_openings", ats="browser", slug=""),
            rec("NoSlug", "no_openings", ats="fake", slug=""),
        ],
    )
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: [{"job_id": "1", "title": "T", "location": None}])
    urls: list[list[str]] = []

    def crash(pages: list[str]) -> Any:
        urls.append(pages)
        raise RuntimeError("detail")

    monkeypatch.setattr(run, "crawl_many", crash)
    assert run.recheck_step() == 0
    assert urls == [["https://shell.example/careers", "https://noslug.example/careers"]]
    assert capsys.readouterr().out == (
        "::warning::recheck failed, RuntimeError\n"
        "recheck: 1 probed, 0 with Israel jobs; 1 companies with jobs of no readable location, 0 unreachable\n"
    )


def test_no_page_to_read_starts_no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(urls: list[str]) -> Any:
        raise AssertionError("the browser must not start")

    monkeypatch.setattr(run, "crawl_many", boom)
    assert run.recheck_jobs([]) == {}


def test_the_summary_names_companies_with_israel_jobs_and_only_counts_the_rest() -> None:
    line = run.recheck_summary(
        {
            "B": [job("Tel Aviv, Israel"), job("Paris"), job("Herzliya")],
            "A": [job("Haifa")],
            "Unplaced": [job(""), job("Paris")],
            "Foreign": [job("Paris")],
            "Empty": [],
            "Down": None,
        }
    )
    assert line == (
        "::notice::recheck: 6 probed, 2 with Israel jobs: A (1), B (2); "
        "1 companies with jobs of no readable location, 1 unreachable"
    )
    assert "Unplaced" not in line


def test_a_quiet_recheck_is_no_notice() -> None:
    assert (
        run.recheck_summary({"Empty": [], "Down": None})
        == "recheck: 2 probed, 0 with Israel jobs; 0 companies with jobs of no readable location, 1 unreachable"
    )


def test_the_recheck_step_logs_one_line_and_is_green(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        workdir / "data" / "companies.json",
        [
            rec("Page", "no_openings"),
            rec("Protected", "bot_protection"),
            {"name": "Gone", "excluded": {"reason": "x", "date_added": "2026-01-01"}},
        ],
    )
    monkeypatch.setattr(run, "crawl_many", lambda urls: iter([(u, [job("Tel Aviv")]) for u in urls]))
    monkeypatch.setattr(sys, "argv", ["run.py", "--recheck"])
    assert run.recheck_step() == 0
    assert capsys.readouterr().out == (
        "::notice::recheck: 1 probed, 1 with Israel jobs: Page (1); "
        "0 companies with jobs of no readable location, 0 unreachable\n"
    )


def test_the_flag_runs_the_step(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["run.py", "--recheck"])  # the list is empty: no site is read, no browser starts
    with pytest.raises(SystemExit) as done:
        runpy.run_module("run", run_name="__main__")
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith("recheck: 0 probed")


def test_a_crash_is_a_warning_and_never_a_red_run(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(workdir / "data" / "companies.json", [rec("Page", "no_openings")])

    def crash(urls: list[str]) -> Any:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(run, "crawl_many", crash)
    assert run.recheck_step() == 0
    assert capsys.readouterr().out == "::warning::recheck failed, RuntimeError\n"


def test_a_missing_list_is_a_warning_too(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (workdir / "data" / "companies.json").unlink()
    assert run.recheck_step() == 0
    assert capsys.readouterr().out.startswith("::warning::recheck failed, ")
