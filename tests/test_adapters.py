import json
from pathlib import Path
from typing import Any

import pytest

import adapters
import discover
from tests.fakes import FakeHttpResponse


def fake_get(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[str]:
    urls: list[str] = []
    queue = list(responses)

    def _get(url: str, **_kw: Any) -> FakeHttpResponse | None:
        urls.append(url)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "get", _get)
    return urls


def test_job_normalises() -> None:
    assert adapters._job(5, None, " x ", "u") == {
        "job_id": "5",
        "title": "",
        "location": "x",
        "url": "u",
        "team": "",
        "posted": "",
    }


@pytest.mark.parametrize("name", ["greenhouse", "lever", "ashby", "workable", "comeet", "greenhouse_url"])
@pytest.mark.parametrize("resp", [None, FakeHttpResponse(status_code=500)])
def test_failure_returns_none(monkeypatch: pytest.MonkeyPatch, name: str, resp: FakeHttpResponse | None) -> None:
    fake_get(monkeypatch, resp)
    assert adapters.ADAPTERS[name]("slug") is None


def test_greenhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "jobs": [
            {"id": 1, "title": "A", "location": {"name": "Tel Aviv"}, "absolute_url": "u1", "updated_at": "t"},
            {"id": 2, "title": "B", "location": None, "absolute_url": "u2"},
        ]
    }
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.greenhouse("acme")
    assert jobs is not None
    assert [(j["job_id"], j["location"], j["posted"]) for j in jobs] == [("1", "Tel Aviv", "t"), ("2", "", "")]


def test_lever_eu_and_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [
        {"id": "a", "text": "T1", "hostedUrl": "h1", "categories": {"location": "Haifa", "team": "R"}, "createdAt": 7},
        {"id": "b", "text": "T2", "hostedUrl": "h2", "categories": {"allLocations": ["X", "Y"]}},
        {"id": "c", "text": "T3", "hostedUrl": "h3", "categories": None},
    ]
    urls = fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.lever("eu:acme")
    assert urls == ["https://api.eu.lever.co/v0/postings/acme?mode=json"]
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["Haifa", "X, Y", ""]
    assert jobs[0]["team"] == "R"
    assert jobs[0]["posted"] == "7"
    urls = fake_get(monkeypatch, FakeHttpResponse(data=[]))
    assert adapters.lever("acme") == []
    assert urls == ["https://api.lever.co/v0/postings/acme?mode=json"]


def test_ashby_skips_unlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "jobs": [
            {"id": "1", "title": "A", "location": "L", "jobUrl": "u", "team": "T", "publishedAt": "p"},
            {"id": "2", "title": "B", "isListed": False},
        ]
    }
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.ashby("acme")
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["1"]


def test_workable(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "jobs": [
            {"shortcode": "s1", "title": "A", "city": "TLV", "country": "IL", "url": "u1", "department": "D"},
            {"shortcode": "s2", "title": "B", "application_url": "u2"},
        ]
    }
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.workable("acme")
    assert jobs is not None
    assert [(j["location"], j["url"]) for j in jobs] == [("TLV, IL", "u1"), ("", "u2")]


def posting(i: str) -> dict[str, Any]:
    return {
        "id": i,
        "name": f"N{i}",
        "location": {"city": "C", "country": "IL"},
        "department": {"label": "D"},
        "releasedDate": "r",
    }


def test_smartrecruiters_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = FakeHttpResponse(data={"content": [posting("1")], "totalFound": 150})
    page2 = FakeHttpResponse(data={"content": [{"id": "2", "name": "N2"}], "totalFound": 150})
    urls = fake_get(monkeypatch, page1, page2)
    jobs = adapters.smartrecruiters("acme")
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["1", "2"]
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/acme/1"
    assert jobs[0]["team"] == "D"
    assert urls[1].endswith("offset=100")


def test_smartrecruiters_writes_the_country_code_in_capitals(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API says "il" and "us"; the Israel matcher tells Israel's IL from Illinois by the capital code."""
    israel = {**posting("1"), "location": {"city": "Omer", "country": "il"}}
    usa = {**posting("2"), "location": {"city": "Chicago", "country": "us"}}
    nowhere = {**posting("3"), "location": {"city": "Remote", "country": None}}
    fake_get(monkeypatch, FakeHttpResponse(data={"content": [israel, usa, nowhere], "totalFound": 3}))
    jobs = adapters.smartrecruiters("acme")
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["Omer, IL", "Chicago, US", "Remote"]
    assert [discover.ISRAEL.search(j["location"]) for j in jobs] == [True, False, False]


def test_smartrecruiters_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, None)
    assert adapters.smartrecruiters("acme") is None
    page1 = FakeHttpResponse(data={"content": [posting("1")], "totalFound": 150})
    fake_get(monkeypatch, page1, FakeHttpResponse(status_code=429))
    jobs = adapters.smartrecruiters("acme")
    assert jobs is not None
    assert len(jobs) == 1
    fake_get(monkeypatch, FakeHttpResponse(data={}))
    assert adapters.smartrecruiters("acme") == []


def test_comeet(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        'var COMPANY_POSITIONS_DATA = [{"uid": "u1", "name": "A", "location": {"city": "TLV", "country": "IL"},'
        ' "url_active_page": "a", "department": "D", "time_updated": "t"},'
        ' {"uid": "u2", "name": "B", "url_comeet_hosted_page": "b"}];'
    )
    fake_get(monkeypatch, FakeHttpResponse(text=text))
    jobs = adapters.comeet("n/uid")
    assert jobs is not None
    assert [(j["job_id"], j["location"], j["url"]) for j in jobs] == [("u1", "TLV, IL", "a"), ("u2", "", "b")]


@pytest.mark.parametrize(
    "text", ["nothing here", "COMPANY_POSITIONS_DATA but no array", "COMPANY_POSITIONS_DATA = [not json];"]
)
def test_comeet_unparseable(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=text))
    assert adapters.comeet("n/uid") is None


def test_greenhouse_url(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"jobs": [{"id": 1, "title": "A", "location": {"name": "L"}, "absolute_url": "u"}]}
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.greenhouse_url("https://x.test/jobs.json")
    assert jobs is not None
    assert jobs[0]["location"] == "L"
    fake_get(monkeypatch, FakeHttpResponse(json_error=True))
    assert adapters.greenhouse_url("https://x.test/jobs.json") is None


FIXTURES = Path(__file__).parent / "fixtures"
ISRAEL_ID = "084562884af243748dad7c84c304d89a"


def fake_post(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[dict[str, Any]]:
    """Replace adapters.post; returns the url and JSON body of every request."""
    bodies: list[dict[str, Any]] = []
    queue = list(responses)

    def _post(url: str, **kw: Any) -> FakeHttpResponse | None:
        bodies.append({"url": url, **kw["json"]})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "post", _post)
    return bodies


def wd_posting(i: int, loc: str | None = "Herzliya, Israel") -> dict[str, Any]:
    return {"title": f"Job {i}", "externalPath": f"/job/x/Job-{i}_R{i}", "locationsText": loc}


def wd_page(postings: list[dict[str, Any]], total: Any = None, facets: list[Any] | None = None) -> FakeHttpResponse:
    data: dict[str, Any] = {"jobPostings": postings, "facets": facets or []}
    if total is not None:
        data["total"] = total
    return FakeHttpResponse(data=data)


def israel_facet(param: str = "Location_Country") -> dict[str, Any]:
    values = [{"descriptor": "Spain", "id": "aa"}, {"descriptor": "Israel", "id": ISRAEL_ID}]
    return {"facetParameter": param, "values": values}


def recorded(name: str) -> FakeHttpResponse:
    """A trimmed first page recorded from a real Workday tenant."""
    return FakeHttpResponse(data=json.loads((FIXTURES / f"workday_{name}_first_page.json").read_text(encoding="utf-8")))


SLUG = "acme/wd3/Site"
BASE = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Site/jobs"
TWO_PAGES_OF_JOBS = 25  # 20 and 5
ONE_PAGE = 20
PROBE_AND_ONE_PAGE = 2
PROBE_AND_TWO_PAGES = 3


def test_workday_israel_id_is_the_one_workday_uses() -> None:
    assert adapters.WORKDAY_ISRAEL == ISRAEL_ID


def test_workday_filters_by_the_israel_facet_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([wd_posting(99, "Madrid, Spain")], total=25, facets=[israel_facet("Country")])
    page0 = wd_page([wd_posting(i, "Herzliya,\xa0Israel") for i in range(20)], total=25)
    page1 = wd_page([wd_posting(i) for i in range(20, 25)], total=0)  # later pages do not repeat the total
    bodies = fake_post(monkeypatch, probe, page0, page1)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == TWO_PAGES_OF_JOBS
    assert "Job 99" not in [j["title"] for j in jobs]  # the unfiltered probe's own postings are never used
    assert [(b["url"], b["appliedFacets"], b["offset"], b["limit"], b["searchText"]) for b in bodies] == [
        (BASE, {}, 0, 20, ""),
        (BASE, {"Country": [ISRAEL_ID]}, 0, 20, ""),
        (BASE, {"Country": [ISRAEL_ID]}, 20, 20, ""),
    ]
    assert jobs[0] == {
        "job_id": "/job/x/Job-0_R0",
        "title": "Job 0",
        "location": "Herzliya, Israel",  # a no-break space is a space
        "url": "https://acme.wd3.myworkdayjobs.com/en-US/Site/job/x/Job-0_R0",
        "team": "",
        "posted": "",
    }


def test_workday_finds_the_israel_facet_inside_a_group(monkeypatch: pytest.MonkeyPatch) -> None:
    group = {"facetParameter": "locationMainGroup", "values": [israel_facet("locationCountry"), {"descriptor": "x"}]}
    bodies = fake_post(monkeypatch, wd_page([], facets=[group]), wd_page([wd_posting(1)]))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert bodies[1]["appliedFacets"] == {"locationCountry": [ISRAEL_ID]}


def test_workday_on_a_recorded_samsung_answer_uses_its_country_facet(monkeypatch: pytest.MonkeyPatch) -> None:
    israel = [wd_posting(1, "Azrieli Building G,\xa0Herzliya, Israel"), wd_posting(2, "Petah Tikva, Israel")]
    bodies = fake_post(monkeypatch, recorded("samsung"), wd_page(israel, total=2))
    jobs = adapters.workday("sec/wd3/Samsung_Careers")
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job 1", "Job 2"]
    assert bodies[1]["appliedFacets"] == {"Location_Country": [ISRAEL_ID]}  # the facet's real name at Samsung


def test_workday_on_a_recorded_criteo_answer_has_no_israel_facet_and_checks_by_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = fake_post(monkeypatch, recorded("criteo"), wd_page([], total=0))
    assert adapters.workday("criteo/wd3/Criteo_Career_Site") == []
    assert [(b["appliedFacets"], b["searchText"]) for b in bodies] == [({}, ""), ({}, "Israel")]  # not just assumed


def test_workday_search_route_keeps_only_postings_located_in_israel(monkeypatch: pytest.MonkeyPatch) -> None:
    postings = [
        wd_posting(1, "Tel Aviv, Israel"),
        wd_posting(2, "Madrid, Spain"),  # mentions Israel in its text, is not there
        wd_posting(3, "2 Locations"),  # cannot be told: left out rather than guessed
        wd_posting(4, None),
        wd_posting(5, "HAIFA, ISRAEL"),
    ]
    fake_post(
        monkeypatch, wd_page([], facets=[{"facetParameter": "jobFamily", "values": [{"id": "bb"}]}]), wd_page(postings)
    )
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job 1", "Job 5"]


def test_workday_tags_a_posting_found_through_the_israel_facet(monkeypatch: pytest.MonkeyPatch) -> None:
    page = wd_page(
        [
            wd_posting(1, "2 Locations"),
            wd_posting(2, ""),
            wd_posting(3, "TEL AVIV, ISRAEL"),
            wd_posting(4, None),
            {"title": "No place", "externalPath": "/job/y"},
        ],
        total=5,
    )
    fake_post(monkeypatch, wd_page([], facets=[israel_facet()]), page)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["2 Locations, Israel", "Israel", "TEL AVIV, ISRAEL", "Israel", "Israel"]


@pytest.mark.parametrize(
    "facets",
    [
        [{"values": [{"id": ISRAEL_ID}]}],  # the Israel id in a facet with no name
        [{"facetParameter": 5, "values": [{"id": ISRAEL_ID}]}],
        [{"facetParameter": "Country", "values": "junk"}],
        ["junk", None, {"facetParameter": "jobFamily", "values": ["junk", {"id": "bb"}]}],
        "not a list",
    ],
)
def test_workday_odd_facets_fall_back_to_the_search_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, facets: Any
) -> None:
    bodies = fake_post(monkeypatch, wd_page([], facets=facets), wd_page([wd_posting(1)]))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert (bodies[1]["appliedFacets"], bodies[1]["searchText"]) == ({}, "Israel")


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=500),
        FakeHttpResponse(json_error=True),  # a maintenance page
        FakeHttpResponse(data=[]),
        FakeHttpResponse(data={"total": 3}),
        FakeHttpResponse(data={"jobPostings": "none"}),
    ],
)
def test_workday_failure_is_none(monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None) -> None:
    fake_post(monkeypatch, answer)
    assert adapters.workday(SLUG) is None


def test_workday_any_failing_page_is_none_because_a_partial_list_would_close_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = wd_page([], total=40, facets=[israel_facet()])
    page0 = wd_page([wd_posting(i) for i in range(20)], total=40)
    fake_post(monkeypatch, probe, FakeHttpResponse(status_code=503))
    assert adapters.workday(SLUG) is None
    fake_post(monkeypatch, probe, page0, FakeHttpResponse(status_code=503))
    assert adapters.workday(SLUG) is None


@pytest.mark.parametrize("slug", ["acme", "acme/wd3", "acme/wd3/Site/extra", ""])
def test_workday_malformed_slug_is_none_without_a_request(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    bodies = fake_post(monkeypatch, wd_page([]))
    assert adapters.workday(slug) is None
    assert bodies == []


def test_workday_skips_postings_without_a_link(monkeypatch: pytest.MonkeyPatch) -> None:
    page = wd_page([{"title": "No link"}, wd_posting(1), {"title": "Empty link", "externalPath": ""}], total=3)
    fake_post(monkeypatch, wd_page([], facets=[israel_facet()]), page)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job 1"]


@pytest.mark.parametrize("total", [None, 0, "25", True, -3, 2.5])
def test_workday_without_a_usable_total_pages_until_a_page_is_not_full(
    monkeypatch: pytest.MonkeyPatch, total: Any
) -> None:
    probe = wd_page([], facets=[israel_facet()])
    full = wd_page([wd_posting(i) for i in range(20)], total=total)
    short = wd_page([wd_posting(i) for i in range(20, 25)])
    bodies = fake_post(monkeypatch, probe, full, short)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == TWO_PAGES_OF_JOBS
    assert [b["offset"] for b in bodies] == [0, 0, 20]


def test_workday_stops_at_an_empty_page_even_if_the_total_says_more(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([], total=1000, facets=[israel_facet()])
    bodies = fake_post(monkeypatch, probe, wd_page([wd_posting(1)], total=1000), wd_page([]))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == 1
    assert len(bodies) == PROBE_AND_TWO_PAGES


def test_workday_more_pages_than_the_limit_is_a_failure_not_a_partial_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "WORKDAY_MAX_PAGES", 2)
    probe = wd_page([], total=10**6, facets=[israel_facet()])
    full = wd_page([wd_posting(i) for i in range(20)], total=10**6)
    bodies = fake_post(monkeypatch, probe, full)
    assert adapters.workday(SLUG) is None
    assert len(bodies) == PROBE_AND_TWO_PAGES  # a probe and the two pages it allows


def test_workday_is_registered() -> None:
    assert adapters.ADAPTERS["workday"] is adapters.workday
