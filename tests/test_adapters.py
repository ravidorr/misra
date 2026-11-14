from typing import Any

import pytest

import adapters
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


def fake_post(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[dict[str, Any]]:
    """Replace adapters.post; returns the url and JSON body of every request."""
    bodies: list[dict[str, Any]] = []
    queue = list(responses)

    def _post(url: str, **kw: Any) -> FakeHttpResponse | None:
        bodies.append({"url": url, **kw["json"]})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "post", _post)
    return bodies


def wd_posting(i: int, loc: str = "Herzliya, Israel") -> dict[str, Any]:
    return {"title": f"Job {i}", "externalPath": f"/job/x/Job-{i}_R{i}", "locationsText": loc}


def wd_page(
    postings: list[dict[str, Any]], total: int | None = None, facets: list[Any] | None = None
) -> FakeHttpResponse:
    data: dict[str, Any] = {"jobPostings": postings, "facets": facets or []}
    if total is not None:
        data["total"] = total
    return FakeHttpResponse(data=data)


def israel_facet(param: str = "Location_Country") -> dict[str, Any]:
    values = [{"descriptor": "Spain", "id": "aa"}, {"descriptor": "Israel", "id": adapters.WORKDAY_ISRAEL}]
    return {"facetParameter": param, "values": values}


SLUG = "acme/wd3/Site"
TWO_PAGES_OF_JOBS = 25  # 20 and 5
ONE_PAGE = 20
PROBE_AND_ONE_PAGE = 2
PROBE_AND_TWO_PAGES = 3
BASE = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Site/jobs"


def test_workday_filters_by_the_israel_facet_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([], total=25, facets=[israel_facet("Country")])
    page0 = wd_page([wd_posting(i, "Herzliya,\xa0Israel") for i in range(20)], total=25)
    page1 = wd_page([wd_posting(i) for i in range(20, 25)], total=0)  # later pages do not repeat the total
    bodies = fake_post(monkeypatch, probe, page0, page1)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == TWO_PAGES_OF_JOBS
    assert [(b["url"], b["appliedFacets"], b["offset"], b["limit"], b["searchText"]) for b in bodies] == [
        (BASE, {}, 0, 20, ""),
        (BASE, {"Country": [adapters.WORKDAY_ISRAEL]}, 0, 20, ""),
        (BASE, {"Country": [adapters.WORKDAY_ISRAEL]}, 20, 20, ""),
    ]
    assert jobs[0] == {
        "job_id": "/job/x/Job-0_R0",
        "title": "Job 0",
        "location": "Herzliya, Israel",  # a no-break space is a space
        "url": "https://acme.wd3.myworkdayjobs.com/en-US/Site/job/x/Job-0_R0",
        "team": "",
        "posted": "",
    }


def test_workday_tags_a_posting_found_through_the_israel_facet(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([], total=3, facets=[israel_facet()])
    page = wd_page([wd_posting(1, "2 Locations"), wd_posting(2, ""), wd_posting(3, "TEL AVIV, ISRAEL")], total=3)
    fake_post(monkeypatch, probe, page)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["2 Locations, Israel", "Israel", "TEL AVIV, ISRAEL"]


def test_workday_country_facet_without_israel_means_no_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    facet = {"facetParameter": "Location_Country", "values": [{"descriptor": "Spain", "id": "aa"}]}
    bodies = fake_post(monkeypatch, wd_page([wd_posting(1)], total=1, facets=[facet]))
    assert adapters.workday(SLUG) == []
    assert len(bodies) == 1  # nothing more to ask


def test_workday_without_a_country_facet_searches_for_israel(monkeypatch: pytest.MonkeyPatch) -> None:
    junk: list[Any] = ["junk", {"facetParameter": "jobFamily", "values": ["junk", {"descriptor": "R&D", "id": "bb"}]}]
    probe = wd_page([], total=1, facets=junk)
    bodies = fake_post(monkeypatch, probe, wd_page([wd_posting(1, "Tel Aviv")], total=1))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert (bodies[1]["appliedFacets"], bodies[1]["searchText"]) == ({}, "Israel")
    assert jobs[0]["location"] == "Tel Aviv"  # a search hit is not known to be in Israel: not tagged


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


def test_workday_a_failing_page_is_none_unless_an_earlier_page_worked(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([], total=40, facets=[israel_facet()])
    page0 = wd_page([wd_posting(i) for i in range(20)], total=40)
    fake_post(monkeypatch, probe, FakeHttpResponse(status_code=503))
    assert adapters.workday(SLUG) is None
    fake_post(monkeypatch, probe, page0, FakeHttpResponse(status_code=503))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == ONE_PAGE


@pytest.mark.parametrize("slug", ["acme", "acme/wd3", "acme/wd3/Site/extra", ""])
def test_workday_malformed_slug_is_none_without_a_request(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    bodies = fake_post(monkeypatch, wd_page([]))
    assert adapters.workday(slug) is None
    assert bodies == []


def test_workday_skips_postings_without_a_link_and_reads_one_page_without_a_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = wd_page([], facets=[israel_facet()])
    page = wd_page([{"title": "No link"}, wd_posting(1), {"title": "Empty link", "externalPath": ""}])
    bodies = fake_post(monkeypatch, probe, page)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job 1"]
    assert len(bodies) == PROBE_AND_ONE_PAGE  # no total: one page


def test_workday_stops_at_an_empty_page_and_at_the_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = wd_page([], total=1000, facets=[israel_facet()])
    first = wd_page([wd_posting(1)], total=1000)
    bodies = fake_post(monkeypatch, probe, first, wd_page([]))
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert len(jobs) == 1
    assert len(bodies) == PROBE_AND_TWO_PAGES  # the empty page ended it though the total said more
    monkeypatch.setattr(adapters, "WORKDAY_MAX_PAGES", 2)
    full = wd_page([wd_posting(i) for i in range(20)], total=10**6)
    bodies = fake_post(monkeypatch, probe, full)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert (len(jobs), len(bodies)) == (40, 3)  # a probe and two pages


def test_workday_is_registered() -> None:
    assert adapters.ADAPTERS["workday"] is adapters.workday
