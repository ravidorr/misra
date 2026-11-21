import asyncio
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

import survey
from tests.conftest import excluded_record, write_json
from tests.fakes import FakeCtx, FakeFrame, FakePage, FakePlaywright, FakeReq, FakeResp, Status

BIG = '{"title": "x", "location": "y", "pad": "' + "z" * 900 + '"}'
COMPANY = {"name": "Acme", "domain": "acme.com", "careers_url": "https://acme.com/c"}


async def run(page: FakePage, co: dict[str, Any] = COMPANY) -> dict[str, Any]:
    return await survey.survey(FakeCtx(page), co, asyncio.Semaphore(1))


async def test_survey_collects_hits() -> None:
    page = FakePage(
        goto_result=Status(200),
        idle_error=True,
        title="Careers",
        requests=[FakeReq("https://boards.greenhouse.io/acme"), FakeReq("https://acme.com/x")],
        responses=[
            FakeResp("https://api/a", text=BIG),
            FakeResp("https://api/short", text='{"title": "x"}'),
            FakeResp("https://api/nolist", text='{"title": 1, "pad": "' + "z" * 900 + '"}'),
            FakeResp("https://api/nolocation", text='{"x": 1, "location": 1, "pad": "' + "z" * 900 + '"}'),
            FakeResp("https://api/html", text=BIG, content_type="text/html"),
            FakeResp("https://api/nohdr", text=BIG, content_type=None),
            FakeResp("https://api/doc", text=BIG, resource_type="document"),
            FakeResp("https://api/err", error=True),
        ],
        frames=[FakeFrame("https://job-boards.greenhouse.io/f"), FakeFrame("https://acme.com/frame")],
        link_fn=lambda _i: [
            ["https://acme.com/jobs/product-manager", "PM"],
            ["https://acme.com/jobs/product-manager", "PM again"],
            ["https://acme.com/jobs/no-text", ""],
            ["https://jobs.lever.co/acme", "Apply"],
            ["https://acme.com/about", "About"],
        ],
    )
    res = await run(page)
    assert res["json_urls"] == ["https://api/a"]
    assert res["ats_hits"] == [
        "FRAME https://job-boards.greenhouse.io/f",
        "LINK https://jobs.lever.co/acme",
        "https://boards.greenhouse.io/acme",
    ]
    assert res["n_job_links"] == 1
    assert res["job_links"] == [["https://acme.com/jobs/product-manager", "PM"]]
    assert res["title"] == "Careers"
    assert res["err"] == ""
    assert page.gotos == ["https://acme.com/c"]


async def test_survey_tries_all_urls_then_gives_up() -> None:
    page = FakePage(goto_seq=[RuntimeError("x"), Status(500), None, Status(404)])
    res = await run(page, {"name": "N", "domain": "n.com"})
    assert page.gotos == ["https://www.n.com/careers", "https://n.com/careers"]
    assert res["err"] == "RuntimeError"
    assert res["n_job_links"] == 0


async def test_survey_outer_error() -> None:
    def boom(_i: int) -> list[Any]:
        raise RuntimeError("no links")

    res = await run(FakePage(goto_result=Status(200), link_fn=boom))
    assert res["err"] == "RuntimeError"
    assert res["ats_hits"] == []


async def test_main_filters_and_writes(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    companies = [
        {"name": "A", "crawl": True, "status": "careers_page_only"},
        {"name": "B", "crawl": True, "status": "not_found"},
        {"name": "Source", "crawl": True, "status": "not_found"},
        {"name": "C", "crawl": True, "status": "ok"},
        {"name": "Parked", "crawl": False, "status": "not_found", "gap": {"reason": "needs_reader"}},  # never visited
        {"name": "NoFlag", "status": "not_found"},  # a record that does not say `crawl` is not crawled
    ]
    write_json(workdir / "data" / "companies.json", companies)
    launches: list[dict[str, Any]] = []
    seen: list[str] = []

    async def fake_survey(_ctx: object, co: dict[str, Any], _sem: asyncio.Semaphore) -> dict[str, Any]:
        seen.append(co["name"])
        return {"name": co["name"]}

    monkeypatch.setattr(survey, "async_playwright", lambda: FakePlaywright(launches))
    monkeypatch.setattr(survey, "survey", fake_survey)
    await survey.main()
    assert seen == ["A", "B"]
    assert json.loads((workdir / "data" / "survey.json").read_text()) == [{"name": "A"}, {"name": "B"}]
    assert launches == [{"executable_path": "/opt/pw-browsers/chromium", "args": ["--no-sandbox"]}]


def test_run_as_script(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "companies.json", [])
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: FakePlaywright([]))
    runpy.run_module("survey", run_name="__main__")
    assert json.loads((workdir / "data" / "survey.json").read_text()) == []


# the baseline report (PLAN_3.md steps A1 and A2): counts only, from frozen fixtures
FIXTURES = Path(__file__).parent / "fixtures"
FROZEN_JOBS = FIXTURES / "jobs.json"


def frozen_companies() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "companies.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_baseline_counts_the_frozen_list() -> None:
    jobs = json.loads(FROZEN_JOBS.read_text(encoding="utf-8"))
    report = survey.baseline(frozen_companies(), jobs)
    assert "Coverage baseline (jobs.json generated 2026-11-18 16:29 UTC)" in report
    assert "Crawled companies: 267\n" in report
    assert "Researched only: 80\n" in report
    assert "Excluded records: 0 (a count only)" in report
    # AI21 Labs and Adobe have Israel jobs; Aidoc has none; "Ghost Co" is not a crawled company
    assert "Crawled companies with at least one Israel job: 2\n" in report
    assert "Israel jobs (all companies in jobs.json): 4\n" in report
    assert "Israel jobs of crawled companies: 3\n" in report
    assert "not in the crawled list (name mismatch): 1\n" in report
    assert "Researched only by gap.reason (80):\n  no_careers_url: 23\n  needs_reader: 20\n" in report
    assert "Researched only by segment (80):\n  deeptech: 36\n  multinational: 22\n" in report
    assert "Crawled by ats (267):\n  comeet: 88\n  greenhouse: 57\n" in report
    assert "  multinational (israel_only): 73\n  startup (not israel_only): 194" in report
    assert "gh run list --workflow=daily.yml --limit 10" in report


def test_baseline_never_prints_an_excluded_name_or_a_job() -> None:
    companies: list[dict[str, Any]] = [
        {"name": "Alpha", "crawl": True, "ats": "comeet"},
        {"name": "Hidden Corp", "crawl": True, "excluded": {"reason": "asked", "date_added": "2026-11-01"}},
        {"name": "Beta", "crawl": False, "excluded": {"reason": "asked", "date_added": "2026-11-01"}},
        {"name": "Gamma", "crawl": False, "gap": "not a mapping"},
        {"name": "Delta", "gap": {"reason": ""}, "segment": "deeptech"},
    ]
    jobs = {"jobs": [{"c": "Alpha", "il": True, "t": "Secret Title"}, {"c": "Hidden Corp", "il": True}]}
    report = survey.baseline(companies, jobs)
    for private in ("Hidden Corp", "Beta", "Secret Title"):
        assert private not in report
    assert "Excluded records: 2 (a count only)" in report
    assert "Crawled companies: 1\n" in report and "Researched only: 2\n" in report
    assert "Crawled companies with at least one Israel job: 1\n" in report
    assert "  none: 2\n" in report  # gap that is not a mapping, and an empty reason
    assert "generated unknown" in report


def test_baseline_with_nothing() -> None:
    report = survey.baseline([], {})
    assert "Crawled companies: 0\n" in report and "Israel jobs (all companies in jobs.json): 0\n" in report
    assert "startup (not israel_only): 0" in report


def test_baseline_main_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    code = survey.baseline_main(str(FIXTURES / "companies.json"), str(FROZEN_JOBS))
    captured = capsys.readouterr()
    assert code == 0
    assert "Crawled companies: 267" in captured.out
    assert captured.err == ""


def test_baseline_main_stops_when_the_list_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = survey.baseline_main(str(tmp_path / "companies.json"), str(FROZEN_JOBS))
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "sync_supabase.py --pull-companies" in captured.err
    assert "SUPABASE_SERVICE_ROLE_KEY" in captured.err


def test_baseline_as_script(workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (workdir / "docs").mkdir(exist_ok=True)
    (workdir / "docs" / "jobs.json").write_text(FROZEN_JOBS.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(workdir / "data" / "companies.json", frozen_companies())
    monkeypatch.setattr("sys.argv", ["survey.py", "--baseline"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("survey", run_name="__main__")
    assert stop.value.code == 0
    assert "Crawled companies: 267" in capsys.readouterr().out


def test_baseline_ranks_ties_by_label() -> None:
    companies = [
        {"name": n, "crawl": True, "ats": a} for n, a in (("A", "zed"), ("B", "alpha"), ("C", "alpha"), ("D", "zed"))
    ]
    companies.append({"name": "E", "crawl": True, "ats": "mid", "israel_only": "true"})
    report = survey.baseline(companies, {"jobs": [{"c": "A", "il": "true"}, {"il": True}]})
    assert "Crawled by ats (5):\n  alpha: 2\n  zed: 2\n  mid: 1\n" in report
    assert "multinational (israel_only): 0\n  startup (not israel_only): 5" in report
    assert "Crawled companies with at least one Israel job: 0\n" in report


# the script-loaded pages tool (`--boards`): the board an embed or a script draws, found in the drawn page
WIDGET = 'COMEET.init({ "token": "0A1B2C", "company-uid": "71.008", "company-name":"All Cloud" });'
PAGES = [{"name": "A", "url": "https://a.test/c"}, {"name": "B", "url": "https://b.test/c"}]


async def test_survey_finds_the_board_a_script_draws() -> None:
    page = FakePage(goto_result=Status(200), html=f"<script>{WIDGET}</script>")
    assert (await run(page))["board"] == ("comeet", "all-cloud/71.008")


async def test_survey_finds_the_board_in_a_frame_or_a_request() -> None:
    frame = FakePage(goto_result=Status(200), frames=[FakeFrame("https://jobs.lever.co/acme")])
    assert (await run(frame))["board"] == ("lever", "acme")
    asked = FakePage(
        goto_result=Status(200), requests=[FakeReq("https://job-boards.greenhouse.io/embed/job_board/js?for=acme")]
    )
    assert (await run(asked))["board"] == ("greenhouse", "acme")


async def test_survey_has_no_board_to_report() -> None:
    assert (await run(FakePage(goto_result=Status(200))))["board"] is None


async def test_survey_never_loads_a_page_robots_txt_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(survey, "robots_allows", lambda url: url != "https://n.com/jobs")
    page = FakePage(goto_result=Status(200))
    res = await run(page, {"name": "N", "domain": "n.com", "careers_url": "https://n.com/jobs"})
    assert page.gotos == ["https://www.n.com/careers"]  # the refused page is skipped, the usual ones are tried
    assert res["err"] == ""  # a page was read in the end, so the refusal is not its state
    nothing = FakePage()
    monkeypatch.setattr(survey, "robots_allows", lambda _url: False)
    res = await run(nothing)
    assert nothing.gotos == []
    assert res["err"] == "robots.txt"


def test_read_board_counts_what_the_adapter_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [{"location": "Tel Aviv, Israel"}, {"location": "London"}, {"location": ""}]
    monkeypatch.setitem(survey.ADAPTERS, "comeet", lambda slug: jobs if slug == "a/1" else None)  # type: ignore[arg-type]
    assert survey.read_board(("comeet", "a/1")) == (3, 1)
    assert survey.read_board(("comeet", "b/2")) is None  # a board the adapter cannot read
    assert survey.read_board(("teamtailor", "x")) is None  # a board with no adapter


async def test_survey_sees_a_comeet_uid_and_the_wordpress_plugin() -> None:
    frame = FakeFrame("https://www.comeet.co/jobs/71.008/social?token=A1&company-uid=71.008&color=1")
    plugin = "https://x.test/wp-content/plugins/comeet-wp-plugin-3.0/js/comeet.js"
    page = FakePage(goto_result=Status(200), frames=[frame], requests=[FakeReq(plugin)])
    res = await run(page)
    assert res["board"] is None
    assert res["comeet_uid"] == "71.008"
    assert res["comeet_plugin"] is True
    plain = await run(FakePage(goto_result=Status(200)))
    assert plain["comeet_uid"] is None
    assert plain["comeet_plugin"] is False


def test_board_candidates_best_first() -> None:
    named = {
        "board": ("comeet", "acme/71.008"),
        "comeet_uid": "71.008",
        "comeet_plugin": True,
        "final": "https://acme.io/c",
    }
    assert survey.board_candidates(named, "Acme Labs", "acme.io") == [
        ("comeet", "acme/71.008"),  # the one the page names comes once, whatever else says the same
        ("comeet", "acmelabs/71.008"),
        ("comeet", "acme-labs/71.008"),
        ("comeet", "acmehq/71.008"),
        ("comeet", "acmeinc/71.008"),
        ("comeet_page", "https://acme.io/c"),
    ]
    lone = {"board": None, "comeet_uid": None, "comeet_plugin": False, "final": "https://acme.io/c"}
    assert survey.board_candidates(lone, "Acme", "acme.io") == []
    other = {"board": ["lever", "acme"], "comeet_uid": None, "comeet_plugin": False, "final": "https://acme.io/c"}
    assert survey.board_candidates(other, "Acme", "acme.io") == [("lever", "acme")]


async def test_boards_main_prints_one_line_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(
        tmp_path / "pages.json",
        [*PAGES, {"name": "C", "url": "https://www.c.test/c"}, {"name": "gone co", "url": "https://gone.test/c"}],
    )
    write_json(tmp_path / "companies.json", [excluded_record("Gone Co")])  # an opt-out is never visited
    companies = str(tmp_path / "companies.json")
    quiet = {"board": None, "comeet_uid": None, "comeet_plugin": False, "final": ""}
    results = {
        "A": {"name": "A", "board": ["comeet", "a/1"], "comeet_uid": "1", "comeet_plugin": False, "final": ""},
        "B": {"name": "B", **quiet},
        "C": {"name": "C", **quiet, "board": ["lever", "c"]},
    }
    seen: list[dict[str, Any]] = []

    async def fake_survey(_ctx: object, co: dict[str, Any], _sem: asyncio.Semaphore) -> dict[str, Any]:
        seen.append(co)
        return results[co["name"]]

    launches: list[dict[str, Any]] = []
    monkeypatch.setattr(survey, "async_playwright", lambda: FakePlaywright(launches))
    monkeypatch.setattr(survey, "survey", fake_survey)
    monkeypatch.setattr(survey, "EXE", "")
    monkeypatch.setitem(survey.ADAPTERS, "comeet", lambda slug: [{"location": "Haifa"}] if slug == "a/1" else None)  # type: ignore[arg-type]
    monkeypatch.setitem(survey.ADAPTERS, "lever", lambda _slug: None)  # a named board the adapter cannot read
    assert await survey.boards_main(str(tmp_path / "pages.json"), companies) == 0
    assert capsys.readouterr().out.splitlines() == [
        "A\tcomeet\ta/1\t1\t1",
        "B\t-\t-\t-\t-",
        "C\tlever\tc\t-\t-",
    ]
    assert seen[0] == {"name": "A", "domain": "a.test", "careers_url": "https://a.test/c"}
    assert seen[2]["domain"] == "c.test"  # `www.` is not part of the domain a name is guessed from
    assert launches == [{"args": ["--no-sandbox"]}]
    monkeypatch.setattr(survey, "EXE", "/x/chrome")
    launches.clear()
    await survey.boards_main(str(tmp_path / "pages.json"), companies)
    assert launches == [{"executable_path": "/x/chrome", "args": ["--no-sandbox"]}]


async def test_boards_main_needs_the_pulled_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert await survey.boards_main(str(tmp_path / "pages.json"), str(tmp_path / "missing.json")) == 1
    assert "missing.json" in capsys.readouterr().err


def test_run_as_script_with_boards(workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "companies.json", [])
    write_json(tmp_path / "pages.json", [])
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: FakePlaywright([]))
    monkeypatch.setattr("sys.argv", ["survey.py", "--boards", str(tmp_path / "pages.json")])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("survey", run_name="__main__")
    assert stop.value.code == 0
