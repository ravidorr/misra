import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import common
import discover
from tests.conftest import exclude, write_json
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
        ("https://Acme.BambooHR.com/careers", ("bamboohr", "acme")),
        ("https://API.BambooHR.com/x https://help.applytojob.com/y", (None, "https://final.test/c")),
        ("https%3A%2F%2Facme.applytojob.com", (None, "https://final.test/c")),
        ("https://sub.acme.bamboohr.com/careers", (None, "https://final.test/c")),
        ("https://jobs.lever.co/first and https://acme.bamboohr.com/careers", ("lever", "first")),
        ("https://job-boards.greenhouse.io/embed/job_board/js?for=acme", ("greenhouse", "acme")),
        (
            'COMEET.init({"token": "A1B2", "company-uid": "71.008", "company-name":"All Cloud"});',
            ("comeet", "all-cloud/71.008"),
        ),
        ('COMEET.init({"company-name": "Acme", "company-uid": "0B.00C", "token": "A1"})', ("comeet", "acme/0B.00C")),
        ('COMEET.init({"token": "A1B2", "company-uid": "71.008"})', (None, "https://final.test/c")),
        ('COMEET.init({"token": "A1B2", "company-name": "Acme"})', (None, "https://final.test/c")),
        ('"company-uid": "71.008", "company-name": "Acme"', (None, "https://final.test/c")),
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
    return Env(monkeypatch)


ROW = {"name": "Acme Co", "domain": "acme.com"}


def pinned(**pin: Any) -> dict[str, Any]:
    """A record of companies.json that someone pinned by hand: its `source` is `override`."""
    return {**ROW, "source": "override", **pin}


def test_pin_browser(env: Env) -> None:
    res = discover.discover(pinned(ats="browser", slug="https://acme.com/jobs"))
    assert (res["status"], res["careers_url"], res["source"]) == ("ok", "https://acme.com/jobs", "override")


def test_pin_unverified(env: Env) -> None:
    res = discover.discover(pinned(ats="ov", slug="s", verify=False))
    assert (res["status"], res["ats"], res["careers_url"]) == ("ok", "ov", "")
    assert env.calls == []


def test_a_record_that_was_not_pinned_is_discovered_even_when_it_names_an_ats(env: Env) -> None:
    res = discover.discover({**ROW, "source": "page", "ats": "ov", "slug": "s"})
    assert (res["status"], res["source"]) == ("not_found", "")


def test_pin_verified(env: Env) -> None:
    env.jobs[("ov", "s")] = [IL_JOB, US_JOB]
    res = discover.discover(pinned(ats="ov", slug="s"))
    assert (res["status"], res["n_jobs"], res["n_israel"]) == ("ok", 2, 1)
    res = discover.discover(pinned(ats="ov", slug="gone"))
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


def test_main_rewrites_only_the_discovered_fields_of_the_crawled_records(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    records = [
        {"name": "A", "domain": "a.com", "crawl": True, "israel_only": True, "links": {"global": "https://a.com/x"}},
        {"name": "B", "domain": "b.com", "crawl": False, "gap": {"reason": "no_openings", "note": "n"}},
        {"name": "C", "domain": "c.com", "crawl": True, "status": "old", "verify": False},
    ]
    write_json(workdir / "data" / "companies.json", records)
    found = {"careers_url": "u", "ats": "x", "slug": "s", "source": "page", "status": "ok", "n_jobs": 3, "n_israel": 1}
    monkeypatch.setattr(discover, "discover", lambda row: found)
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    discover.main()
    text = (workdir / "data" / "companies.json").read_text()
    assert text.endswith("]\n")
    out = json.loads(text)
    new = {k: v for k, v in found.items() if k in discover.DISCOVERED}
    assert out == [{**records[0], **new}, records[1], {**records[2], **new}]  # no n_jobs: it would make noisy diffs
    assert list(out[0]) == [k for k in common.RECORD_KEYS if k in out[0]]  # every key in its place, new ones too
    assert not (workdir / "data" / "companies.json.tmp").exists()  # written through a temporary file, then moved
    assert "Counter({'ok': 2})" in capsys.readouterr().out
    write_json(workdir / "other.json", [])
    monkeypatch.setattr(sys, "argv", ["discover.py", "other.json"])
    discover.main()
    assert json.loads((workdir / "other.json").read_text()) == []


def test_run_as_script(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "companies.json", [])
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    runpy.run_module("discover", run_name="__main__")
    assert json.loads((workdir / "data" / "companies.json").read_text()) == []


def run_main(workdir: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> list[dict[str, Any]]:
    monkeypatch.setattr(sys, "argv", ["discover.py", *args])
    discover.main()
    return json.loads((workdir / "data" / "companies.json").read_text())  # type: ignore[no-any-return]


def test_main_keeps_a_pin_and_everything_written_by_a_person(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = {"name": "Acme Co", "domain": "acme.com", "crawl": True, "israel_only": True, "verify": False}
    pin |= {"ats": "ov", "slug": "s", "source": "override", "status": "ok", "careers_url": ""}
    found = {"name": "Found", "domain": "found.com", "crawl": True, "ats": "x", "slug": "y", "source": "page"}
    found |= {"status": "ok", "careers_url": "u"}
    write_json(workdir / "data" / "companies.json", [pin, found])
    out = run_main(workdir, monkeypatch)
    assert out[0] == pin  # the pinned ats and slug stay (not checked here: verify is false), and so does every flag
    assert list(out[0]) == [k for k in common.RECORD_KEYS if k in pin]
    assert out[1]["status"] == "not_found"  # not pinned and nothing found any more: searched again, as before
    assert (out[1]["ats"], out[1]["slug"], out[1]["source"]) == ("", "", "")


def test_main_puts_a_key_added_by_discovery_in_its_place_and_leaves_the_excluded_as_they_are(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.pages["https://www.acme.com/"] = ("lever", "acme")
    env.jobs[("lever", "acme")] = [IL_JOB]
    gone = {"name": "Gone", "crawl": False, "excluded": {"reason": "asked to be removed", "date_added": "2026-01-01"}}
    write_json(
        workdir / "data" / "companies.json",
        [
            {"name": "Acme Co", "domain": "acme.com", "crawl": True, "israel_only": True, "links": {"global": "g"}},
            gone,
        ],
    )
    (out, kept) = run_main(workdir, monkeypatch)
    assert json.dumps(kept) == json.dumps(
        gone
    )  # byte for byte; its record is not looked at and not changed: the table holds it, the next pull has it
    assert list(out) == [
        "name",
        "domain",
        "crawl",
        "careers_url",
        "ats",
        "slug",
        "source",
        "status",
        "israel_only",
        "links",
    ]


def test_main_writes_a_careers_address_that_came_out_as_http_as_https(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page that redirects to http:// gave the address that the table's https check would refuse (Verbit)."""
    write_json(workdir / "data" / "companies.json", [{"name": "Acme Co", "domain": "acme.com", "crawl": True}])
    found = {"careers_url": "HTTP://acme.com/jobs", "ats": "", "slug": "", "source": "", "status": "careers_page_only"}
    monkeypatch.setattr(discover, "discover", lambda row: found)
    (out,) = run_main(workdir, monkeypatch)
    assert out["careers_url"] == "https://acme.com/jobs"


def test_main_never_looks_at_a_crawled_record_of_a_company_on_the_exclusion_list(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table cannot hold both, but a file that does (an old scratch copy) must not make us request that site."""
    asked: list[str] = []
    monkeypatch.setattr(
        discover, "discover", lambda row: asked.append(row["name"]) or dict.fromkeys(discover.DISCOVERED, "")
    )
    write_json(workdir / "data" / "companies.json", [{"name": "Acme Co", "domain": "acme.com", "crawl": True}])
    exclude(workdir, "Acme Co")
    run_main(workdir, monkeypatch)
    assert asked == []


def many(n: int, ok: bool) -> list[dict[str, Any]]:
    return [
        {"name": f"C{i}", "domain": f"c{i}.com", "crawl": True, "status": "ok" if ok else "not_found"} for i in range(n)
    ]


def test_main_writes_nothing_when_too_many_working_companies_would_fail(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = many(discover.MAX_LOST + 1, ok=True)  # an offline run: every probe finds nothing
    write_json(workdir / "data" / "companies.json", records)
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    with pytest.raises(
        SystemExit, match=r"6 companies would stop being ok \(C0, C1, C2, C3, C4, C5\); nothing written"
    ):
        discover.main()
    assert json.loads((workdir / "data" / "companies.json").read_text()) == records
    assert not (workdir / "data" / "companies.json.tmp").exists()
    out = run_main(workdir, monkeypatch, "--force")
    assert {r["status"] for r in out} == {"not_found"}


def test_main_accepts_a_few_companies_failing(workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(workdir / "data" / "companies.json", many(discover.MAX_LOST, ok=True))
    assert {r["status"] for r in run_main(workdir, monkeypatch)} == {"not_found"}


def test_main_new_only_discovers_just_the_records_without_a_status_and_leaves_the_rest_as_pulled(
    workdir: Path, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    working = many(discover.MAX_LOST + 3, ok=True)  # would be refused by the guard if they were looked at
    new = {"name": "New Co", "domain": "new.com", "crawl": True}
    write_json(workdir / "data" / "companies.json", [*working, new])
    out = run_main(workdir, monkeypatch, "--new-only")
    assert out[:-1] == working
    assert out[-1]["name"] == "New Co" and out[-1]["status"] == "not_found"


@pytest.mark.parametrize(
    ("text", "uid"),
    [
        ("https://www.comeet.co/jobs/71.008/social?token=A1&company-uid=71.008&color=1", "71.008"),
        ('COMEET.init({"token": "A1", "company-uid": "0B.00C"})', "0B.00C"),
        ("company-uid: '68.00C'", "68.00C"),
        ("company-uid=71.0089", None),
        ("company-uid=nothing", None),
        ("no widget", None),
    ],
)
def test_comeet_uid(text: str, uid: str | None) -> None:
    assert discover.comeet_uid(text) == uid
