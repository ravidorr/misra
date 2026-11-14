import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import common
import discover
from tests.conftest import write_json
from tests.fakes import FakeHttpResponse

IL_JOB = {"location": "Tel Aviv"}
US_JOB = {"location": "NYC"}


def test_scan_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: dict[str, FakeHttpResponse | None] = {
        "none": None,
        "bad": FakeHttpResponse(status_code=404),
        "lever": FakeHttpResponse(text="see https://jobs.lever.co/acme now"),
        "generic": FakeHttpResponse(text="https://boards.greenhouse.io/embed/job_board?for=embed", url="https://f/"),
        "plain": FakeHttpResponse(text="nothing", url="https://final.test/c"),
    }
    monkeypatch.setattr(discover, "get", lambda url: pages[url])
    assert discover.scan_page("none") == (None, None)
    assert discover.scan_page("bad") == (None, None)
    assert discover.scan_page("lever") == ("lever", "acme")
    assert discover.scan_page("generic") == (None, "https://f/")
    assert discover.scan_page("plain") == (None, "https://final.test/c")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<a href='https://apply.workable.com/j/ABC123DEF4'>Job</a>", (None, "https://final.test/c")),
        (
            "<a href='https://apply.workable.com/j/ABC123DEF4'>Job</a> https://apply.workable.com/acme/",
            ("workable", "acme"),
        ),
        ("https://apply.workable.com/acme/j/ABC123DEF4/", ("workable", "acme")),
        ("<a href='https://acme.applytojob.com/apply/AbC12/Engineer'>x</a>", ("jazzhr", "acme")),
        ("https://www.applytojob.com/about", (None, "https://final.test/c")),
        ("https://acme-labs.bamboohr.com/careers", ("bamboohr", "acme-labs")),
        ("https://www.bamboohr.com/hr-software/", (None, "https://final.test/c")),
        ("https://jobs.lever.co/first and https://acme.bamboohr.com/careers", ("lever", "first")),
    ],
)
def test_scan_page_ats_links(
    monkeypatch: pytest.MonkeyPatch, text: str, expected: tuple[str | None, str | None]
) -> None:
    monkeypatch.setattr(discover, "get", lambda url: FakeHttpResponse(text=text, url="https://final.test/c"))
    assert discover.scan_page("page") == expected


def test_israel_count() -> None:
    assert discover.israel_count([IL_JOB, US_JOB, {"location": None}, {"location": "Haifa"}]) == len([IL_JOB, IL_JOB])


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Tel Aviv-Yafo, IL", True),
        ("Ramat Gan, IL", True),
        ("Caesarea, IL", True),
        ("Kiryat Ono, IL", True),
        ("Rosh Haayin, IL", True),
        ("Tirat Carmel, IL", True),
        ("IL-Israel-Remote", True),
        ("IL: Tel Aviv", True),
        ("IL", True),
        ("Remote, IL", True),
        ("Haifa", True),
        ("Kiryat Gat,ISR", True),
        ("Mig Ha'emek,ISR", True),
        ("Dresden,DEU; Dublin,IRL; Kiryat Gat,ISR", True),
        ("Dresden,DEU; Dublin,IRL", False),
        ("Chicago, IL", False),
        ("Naperville, IL", False),
        ("United States - IL Remote", False),
        ("United States: Chicago IL | Sales | Full-time", False),
        ("US-IL-Remote", False),
        ("US-IL-Chicago-MSO", False),
        ("Austin, TX; Chicago, IL", False),
        ("Boston, MA; Rehovot, IL", True),
        ("Migdal HaEmek", True),
        ("Ramat Gan", True),
        ("Kiryat Ramat-Hasharon", True),
        ("AUS-Parramatta", False),  # "ramat" inside another word
        ("ISR-Tel Aviv University", True),
        ("ISR-Remote", True),
        ("Omer, IL", True),  # SmartRecruiters' country code, written in capitals by its adapter
        ("Chicago, US", False),
        ("Haifa, IL", True),
        ("Toronto, CA", False),
        ("New York", False),
        ("il", False),
        ("", False),
    ],
)
def test_israel_matcher(location: str, expected: bool) -> None:
    assert common.ISRAEL.search(location) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tel Aviv", True),
        ("Israel - Remote", True),
        ("ISR-Haifa", True),
        ("Remote (IL)", False),
        ("Omer, IL", False),
        ("", False),
    ],
)
def test_israel_matcher_names_a_place_never_on_the_bare_code(text: str, expected: bool) -> None:
    assert common.ISRAEL.names_a_place(text) is expected


def test_load_overrides(workdir: Path) -> None:
    write_json(workdir / "data" / "overrides.json", {"A": {"ats": "x"}})
    (workdir / "data" / "new").mkdir()
    write_json(workdir / "data" / "new" / "b_overrides.json", {"B": {"ats": "y"}})
    assert discover.load_overrides() == {"A": {"ats": "x"}, "B": {"ats": "y"}}


class Env:
    """Fake network: pages maps URL -> (ats, value); adapters maps (ats, slug) -> jobs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pages: dict[str, tuple[str | None, str | None]] = {}
        self.jobs: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str]] = []
        monkeypatch.setattr(discover, "scan_page", lambda url: self.pages.get(url, (None, None)))
        for ats in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "comeet", "ov"):
            monkeypatch.setitem(discover.ADAPTERS, ats, self.make(ats))

    def make(self, ats: str) -> Any:
        def fetch(slug: str) -> Any:
            self.calls.append((ats, slug))
            return self.jobs.get((ats, slug))

        return fetch


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(discover, "OVERRIDES", {})
    return Env(monkeypatch)


ROW = {"name": "Acme Co", "domain": "acme.com"}


def test_override_browser(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover, "OVERRIDES", {"Acme Co": {"ats": "browser", "slug": "https://acme.com/jobs"}})
    res = discover.discover(ROW)
    assert (res["status"], res["careers_url"], res["source"]) == ("ok", "https://acme.com/jobs", "override")


def test_override_unverified(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover, "OVERRIDES", {"Acme Co": {"ats": "ov", "slug": "s", "verify": False}})
    res = discover.discover(ROW)
    assert (res["status"], res["ats"], res["careers_url"]) == ("ok", "ov", "")
    assert env.calls == []


def test_override_israel_only_flag(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    flagged = {"Acme Co": {"ats": "ov", "slug": "s", "verify": False, "israel_only": True}}
    monkeypatch.setattr(discover, "OVERRIDES", flagged)
    assert discover.discover(ROW)["israel_only"] is True
    monkeypatch.setattr(discover, "OVERRIDES", {"Acme Co": {"ats": "ov", "slug": "s", "verify": False}})
    assert "israel_only" not in discover.discover(ROW)


def test_override_verified(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover, "OVERRIDES", {"Acme Co": {"ats": "ov", "slug": "s"}})
    env.jobs[("ov", "s")] = [IL_JOB, US_JOB]
    res = discover.discover(ROW)
    assert (res["status"], res["n_jobs"], res["n_israel"]) == ("ok", 2, 1)
    monkeypatch.setattr(discover, "OVERRIDES", {"Acme Co": {"ats": "ov", "slug": "gone"}})
    res = discover.discover(ROW)
    assert (res["status"], res["n_jobs"], res["n_israel"]) == ("ats_failed", None, None)


def test_found_on_page_and_verified(env: Env) -> None:
    env.pages["https://www.acme.com/jobs"] = ("greenhouse", "acme")
    env.jobs[("greenhouse", "acme")] = [IL_JOB]
    res = discover.discover(ROW)
    assert (res["ats"], res["slug"], res["source"], res["status"]) == ("greenhouse", "acme", "page", "ok")
    assert res["careers_url"] == "https://www.acme.com/jobs"
    assert (res["n_jobs"], res["n_israel"]) == (1, 1)


def test_found_on_bare_domain_but_ats_fails(env: Env) -> None:
    env.pages["https://acme.com/"] = ("lever", "acme")
    res = discover.discover(ROW)
    assert (res["ats"], res["status"], res["careers_url"]) == ("lever", "ats_failed", "https://acme.com/")


def test_careers_page_only_keeps_first_url(env: Env) -> None:
    env.pages["https://www.acme.com/"] = (None, "https://www.acme.com/home")
    env.pages["https://www.acme.com/careers"] = (None, "https://www.acme.com/careers-landing")
    env.pages["https://www.acme.com/jobs"] = (None, "https://www.acme.com/other")
    res = discover.discover(ROW)
    assert (res["status"], res["ats"], res["careers_url"]) == (
        "careers_page_only",
        "",
        "https://www.acme.com/careers-landing",
    )


def test_guess_by_slug(env: Env) -> None:
    env.jobs[("greenhouse", "acme")] = [US_JOB]  # no Israel jobs: rejected
    env.jobs[("lever", "acmeco")] = []  # empty: rejected
    env.jobs[("ashby", "acme-co")] = [IL_JOB]
    res = discover.discover(ROW)
    assert (res["ats"], res["slug"], res["source"], res["status"]) == ("ashby", "acme-co", "guess", "ok")
    assert ("workable", "acme") not in env.calls


def test_nothing_found(env: Env) -> None:
    res = discover.discover(ROW)
    assert (res["status"], res["ats"], res["careers_url"]) == ("not_found", "", "")
    assert len(env.calls) == len(discover.PROBE_ORDER) * len(discover.slug_candidates("Acme Co", "acme.com"))


def test_main_defaults_and_explicit_paths(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_text = "name,domain\nA,a.com\nA again,a.com\nB,b.com\n"
    (workdir / "data" / "seed_companies.csv").write_text(csv_text)
    monkeypatch.setattr(discover, "discover", lambda row: {"name": row["name"], "status": "ok"})
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    discover.main()
    assert [r["name"] for r in json.loads((workdir / "data" / "companies.json").read_text())] == ["A", "B"]
    assert "Counter({'ok': 2})" in capsys.readouterr().out
    (workdir / "in.csv").write_text("name,domain\n")
    monkeypatch.setattr(sys, "argv", ["discover.py", "in.csv", "out.json"])
    discover.main()
    assert json.loads((workdir / "out.json").read_text()) == []


def test_run_as_script(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "overrides.json", {})
    (workdir / "data" / "seed_companies.csv").write_text("name,domain\n")
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    runpy.run_module("discover", run_name="__main__")
    assert json.loads((workdir / "data" / "companies.json").read_text()) == []
