import asyncio
import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import survey2
from tests.conftest import write_json
from tests.fakes import FakeCtx, FakeFrame, FakePage, FakePlaywright, FakeReq, FakeResp, Status

ITEMS = [{"title": f"T{i}", "location": "L", "extra": i} for i in range(4)]


def test_joblists() -> None:
    assert survey2.joblists(ITEMS) == [("", 4, ["extra", "location", "title"])]
    assert survey2.joblists({"data": {"jobs": ITEMS}}) == [(".data.jobs", 4, ["extra", "location", "title"])]
    assert survey2.joblists([[ITEMS]]) == [("[][]", 4, ["extra", "location", "title"])]
    assert survey2.joblists(ITEMS[:2]) == []
    assert survey2.joblists([1, 2, 3, 4]) == []
    assert survey2.joblists([{"a": 1}] * 3) == []
    assert survey2.joblists([{"title": 1}] * 3) == []
    assert survey2.joblists("text") == []
    deep: Any = ITEMS
    for _ in range(survey2.MAX_DEPTH + 1):
        deep = {"k": deep}
    assert survey2.joblists(deep) == []


COMPANY = {"name": "Acme", "domain": "acme.com", "careers_url": "https://acme.com/c"}
API = "https://api.acme.com/jobs?x=1"


async def run(page: FakePage, co: dict[str, Any] = COMPANY) -> dict[str, Any]:
    return await survey2.survey(FakeCtx(page), co, asyncio.Semaphore(1))


async def test_survey_hop_zero_finds_everything() -> None:
    page = FakePage(
        goto_result=Status(200),
        idle_error=True,
        requests=[
            FakeReq("https://acme.myworkdayjobs.com/x?y=1"),
            FakeReq("https://acme.com/other"),
            FakeReq("https://www.google.com/greenhouse.io"),
        ],
        responses=[
            FakeResp(API, data={"jobs": ITEMS}),
            FakeResp("https://google.com/api", data={"jobs": ITEMS}),
            FakeResp("https://api/doc", data={"jobs": ITEMS}, resource_type="document"),
            FakeResp("https://api/text", data={"jobs": ITEMS}, content_type="text/html"),
            FakeResp("https://api/nohdr", data={"jobs": ITEMS}, content_type=None),
            FakeResp("https://api/err", error=True),
        ],
        frames=[
            FakeFrame("https://x.lever.co/f?z=1"),
            FakeFrame("https://x.com/f"),
            FakeFrame("https://google.com/lever.co"),
        ],
        link_fn=lambda _i: [
            ["https://jobs.lever.co/acme?a=1", "Jobs"],
            ["https://google.com/lever.co", "x"],
            ["https://x.com", "y"],
        ],
        title="Careers Page",
    )
    res = await run(page)
    assert res["ats"] == [
        "FRAME https://x.lever.co/f",
        "LINK https://jobs.lever.co/acme",
        "https://acme.myworkdayjobs.com/x",
    ]
    assert res["lists"] == [(API.split("?", 1)[0], ".jobs", 4, ["extra", "location", "title"])]
    assert res["steps"] == ["https://x.test/final"]
    assert res["title"] == "Careers Page"
    assert page.gotos == ["https://acme.com/c"]


async def test_survey_follows_open_positions_link() -> None:
    links = [
        ["https://x.test/final", "Open positions"],
        ["mailto:a@b.c", "Open positions"],
        ["https://x.test/#top", "Open positions"],
        ["https://x.test/jobs/", "no keyword here"],
        ["https://x.test/open-roles", "See all openings"],
    ]
    page = FakePage(goto_result=Status(200), link_fn=lambda _i: links)
    res = await run(page)
    assert page.gotos == ["https://acme.com/c", "https://x.test/open-roles"]
    assert len(res["steps"]) == len(page.gotos)


async def test_survey_follow_failure_and_no_candidates() -> None:
    links = [["https://x.test/open-roles", "Open positions"]]
    page = FakePage(goto_seq=[Status(200), RuntimeError("nav")], link_fn=lambda _i: links)
    res = await run(page)
    assert page.gotos == ["https://acme.com/c", "https://x.test/open-roles"]
    assert len(res["steps"]) == 1
    page = FakePage(goto_result=Status(200), link_fn=lambda _i: [])
    res = await run(page)
    assert page.gotos == ["https://acme.com/c"]


async def test_survey_page_never_loads() -> None:
    page = FakePage(goto_seq=[RuntimeError("a"), Status(500), None, Status(404)])
    res = await run(page, {"name": "N", "domain": "n.com"})
    assert page.gotos == [
        "https://www.n.com/careers",
        "https://n.com/careers",
        "https://www.n.com/company/careers",
    ]
    assert res["err"] == "RuntimeError"
    page = FakePage(goto_result=Status(404))
    res = await run(page, {"name": "N", "domain": "n.com"})
    assert res["err"] == "nopage"


def test_main_selects_names_and_prints(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    companies = [
        {"name": "A", "crawl": True, "status": "ok"},
        {"name": "B", "crawl": True, "status": "x"},
        {"name": "C", "crawl": True, "status": "y"},
        {"name": "Parked", "crawl": False, "status": "x", "gap": {"reason": "needs_reader"}},  # never visited
    ]
    write_json(workdir / "data" / "companies.json", companies)
    seen: list[str] = []

    async def fake_survey(_ctx: object, co: dict[str, Any], _sem: asyncio.Semaphore) -> dict[str, Any]:
        seen.append(co["name"])
        if co["name"] == "A":
            return {"name": "A", "ats": ["https://ats"], "lists": [("u", ".p", 3, ["k"])], "final": "f"}
        return {"name": co["name"], "ats": [], "lists": [], "final": ""}

    monkeypatch.setattr(survey2, "async_playwright", lambda: FakePlaywright([]))
    monkeypatch.setattr(survey2, "survey", fake_survey)
    monkeypatch.setattr(sys, "argv", ["survey2.py", "A"])
    asyncio.run(survey2.main())
    out = capsys.readouterr().out
    assert "## A | f" in out
    assert "   ATS  https://ats" in out
    assert "   LIST u .p 3 ['k']" in out
    assert seen == ["A"]
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["survey2.py"])
    asyncio.run(survey2.main())
    assert seen == ["B", "C"]
    assert [r["name"] for r in json.loads((workdir / "data" / "survey2.json").read_text())] == ["B", "C"]


def test_run_as_script(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "companies.json", [])
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: FakePlaywright([]))
    monkeypatch.setattr(sys, "argv", ["survey2.py"])
    runpy.run_module("survey2", run_name="__main__")
    assert json.loads((workdir / "data" / "survey2.json").read_text()) == []
