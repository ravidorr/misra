import asyncio
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

import survey
from tests.conftest import write_json
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
