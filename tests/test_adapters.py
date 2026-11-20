import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

import adapters
import common
import descriptions
import discover
from tests.conftest import COMEET_FEEDS, FROZEN_COMPANIES
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


@pytest.mark.parametrize(
    "name", ["greenhouse", "lever", "ashby", "workable", "comeet", "greenhouse_url", "jazzhr", "bamboohr"]
)
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
    mixed = {**posting("4"), "location": {"city": "Haifa", "country": "Il"}}
    canada = {**posting("5"), "location": {"city": "Toronto", "country": "ca"}}  # CA is also California
    postings = [israel, usa, nowhere, mixed, canada]
    fake_get(monkeypatch, FakeHttpResponse(data={"content": postings, "totalFound": len(postings)}))
    jobs = adapters.smartrecruiters("acme")
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["Omer, IL", "Chicago, US", "Remote", "Haifa, IL", "Toronto, CA"]
    assert [discover.ISRAEL.search(j["location"]) for j in jobs] == [True, False, False, True, False]


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


COMEET_HOSTED = "https://www.comeet.com/jobs/"


def comeet_feed(monkeypatch: pytest.MonkeyPatch, positions: list[dict[str, Any]]) -> list[adapters.Job]:
    fake_get(monkeypatch, FakeHttpResponse(text=f"var COMPANY_POSITIONS_DATA = {json.dumps(positions)};"))
    jobs = adapters.comeet("n/uid")
    assert jobs is not None
    return jobs


def position(uid: str, active: str | None) -> dict[str, Any]:
    return {"uid": uid, "name": uid, "url_active_page": active, "url_comeet_hosted_page": f"{COMEET_HOSTED}n/x/{uid}"}


def test_comeet_keeps_the_company_page_of_a_job_when_it_is_the_jobs_own(monkeypatch: pytest.MonkeyPatch) -> None:
    positions = COMEET_FEEDS["Claroty"]["positions"]
    jobs = {j["job_id"]: j["url"] for j in comeet_feed(monkeypatch, positions)}
    assert jobs["CE.079"] == "https://claroty.com/open-positions/CE.079"
    assert jobs["EE.471"] == "https://claroty.com/open-positions/EE.471?Share&employee"  # a share query is kept as is


def test_comeet_gives_a_job_whose_company_page_is_the_list_of_jobs_its_own_comeet_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claroty = {j["job_id"]: j["url"] for j in comeet_feed(monkeypatch, COMEET_FEEDS["Claroty"]["positions"])}
    assert claroty["25.57D"] == "https://www.comeet.com/jobs/Claroty/F2.004/tech-lead-platform-software-engineer/25.57D"
    qodo = {j["job_id"]: j["url"] for j in comeet_feed(monkeypatch, COMEET_FEEDS["Qodo"]["positions"])}
    assert qodo["55.57C"] == "https://www.comeet.com/jobs/qodo/B8.00B/bookkeeper/55.57C"
    assert qodo["CE.A69"] == "https://www.qodo.ai/careers/co/boston/CE.A69/forward-deployed-engineer/all/"


@pytest.mark.parametrize("company", ["Bizzabo", "Checkmarx"])
def test_comeet_a_page_that_several_jobs_share_is_not_any_ones_link(
    monkeypatch: pytest.MonkeyPatch, company: str
) -> None:
    positions = COMEET_FEEDS[company]["positions"]
    jobs = comeet_feed(monkeypatch, positions)
    assert [j["url"] for j in jobs] == [p["url_comeet_hosted_page"] for p in positions]


def test_comeet_a_job_page_that_carries_the_job_in_its_query_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    positions = COMEET_FEEDS["Sunbit"]["positions"]
    jobs = comeet_feed(monkeypatch, positions)
    assert [j["url"] for j in jobs] == [p["url_active_page"] for p in positions]


@pytest.mark.parametrize("company", sorted(COMEET_FEEDS))
def test_comeet_no_two_jobs_of_a_company_share_a_link_and_none_is_the_list_of_jobs(
    monkeypatch: pytest.MonkeyPatch, company: str
) -> None:
    """The audit of the stored jobs, on a real shape of each: a company whose feed has a link for every job must not
    end up with one link for them all."""
    jobs = comeet_feed(monkeypatch, COMEET_FEEDS[company]["positions"])
    urls = [j["url"] for j in jobs]
    assert len(set(urls)) == len(urls)
    names = {"career", "careers", "jobs", "open-positions", "positions"}
    listings = [u for u in urls if not urlparse(u).query and urlparse(u).path.rstrip("/").rsplit("/", 1)[-1] in names]
    assert listings == []


def test_comeet_a_changed_link_is_the_same_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the link changes: the id (the Comeet uid), the title and the place are what they were."""
    first = comeet_feed(monkeypatch, [position("25.57D", "https://claroty.com/open-positions/25.57D")])[0]
    both = [
        position("25.57D", "https://claroty.com/open-positions"),
        position("CE.079", "https://claroty.com/open-positions/CE.079"),
    ]
    second = comeet_feed(monkeypatch, both)[0]
    assert second["job_id"] == first["job_id"] == "25.57D"
    assert second["url"] != first["url"]
    assert {k: v for k, v in second.items() if k != "url"} == {k: v for k, v in first.items() if k != "url"}


@pytest.mark.parametrize(
    ("active", "generic"),
    [
        ("https://x.test/careers/", True),  # the list of jobs, one job in the feed
        ("https://x.test", True),  # the home page
        ("https://x.test/", True),
        ("https://x.test/careers?t=1794790605932", True),  # a tracking query does not make it a job's page
        ("https://x.test/Careers?utm_source=a&ref=b", True),
        ("https://x.test/careers/?job=AB.123", False),  # the job is in the query
        ("https://x.test/jobs", True),
        ("https://x.test/openings/", True),
        ("https://x.test/open-positions", True),
        ("https://x.test/job-openings", True),
        ("https://x.test/positions", True),
        ("https://x.test/vacancies", True),
        ("https://x.test/p?trakkr_fresh=1&gtm_latency=2&gclid=3&src=4&_gl=5", False),  # a page named p, tracking only
        ("https://x.test/careers-il/", False),  # not a listing name
        ("https://x.test/jobs/36-475?t=1", False),
        ("https://x.test/comeetitem/8b071/", False),  # the company's own id for the job
    ],
)
def test_comeet_which_company_pages_are_the_list_of_jobs(
    monkeypatch: pytest.MonkeyPatch, active: str, generic: bool
) -> None:
    (job,) = comeet_feed(monkeypatch, [position("AB.123", active)])
    assert job["url"] == (f"{COMEET_HOSTED}n/x/AB.123" if generic else active)


def test_comeet_the_same_page_with_other_tracking_is_one_page(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = comeet_feed(
        monkeypatch, [position("A", "https://x.test/p/?t=1&hsa_cam=2"), position("B", "https://x.test/p?utm_x=1#top")]
    )
    assert [j["url"] for j in jobs] == [f"{COMEET_HOSTED}n/x/A", f"{COMEET_HOSTED}n/x/B"]


def test_comeet_a_page_above_the_page_of_another_job_is_not_a_job_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/company/work` is not named as a list of jobs: only the page of another job below it says it is one."""
    jobs = comeet_feed(
        monkeypatch,
        [
            position("A", "https://x.test/company/work?trakkr_fresh=1&gtm_latency=2&gclid=3&src=4&_gl=5"),
            position("B", "https://x.test/company/work/B"),
        ],
    )
    assert [j["url"] for j in jobs] == [f"{COMEET_HOSTED}n/x/A", "https://x.test/company/work/B"]


def test_comeet_a_page_on_another_host_is_not_below_a_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = comeet_feed(monkeypatch, [position("A", "https://a.test/open/AB.123"), position("B", "https://b.test/open")])
    assert [j["url"] for j in jobs] == ["https://a.test/open/AB.123", "https://b.test/open"]


def test_comeet_without_a_comeet_page_the_company_page_stays_and_without_either_there_is_no_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = [
        {"uid": "A", "name": "A", "url_active_page": "https://x.test/careers"},
        {"uid": "B", "name": "B"},
        {"uid": "C", "name": "C", "url_active_page": "", "url_comeet_hosted_page": f"{COMEET_HOSTED}c"},
    ]
    urls = [j["url"] for j in comeet_feed(monkeypatch, positions)]
    assert urls == ["https://x.test/careers", None, f"{COMEET_HOSTED}c"]


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


def test_workday_on_a_recorded_philips_answer_uses_the_israel_value_with_its_own_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    philips_israel = "6e1b2a934716103c2adde8e0944f00ee"  # not Workday's id: this tenant gave the country its own
    page = wd_page([wd_posting(1, "Haifa"), wd_posting(2, "2 Locations")], total=10)
    bodies = fake_post(monkeypatch, recorded("philips"), page)
    jobs = adapters.workday("philips/wd3/jobs-and-careers")
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["Haifa, Israel", "2 Locations, Israel"]
    assert bodies[1]["appliedFacets"] == {"locationHierarchy1": [philips_israel]}  # not the State/Province "Haifa"


def test_workday_on_a_recorded_broadcom_answer_uses_the_israeli_places_of_its_location_facet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = wd_page([wd_posting(1, "ISR-Tel Aviv University"), wd_posting(2, "2 Locations")], total=1)
    bodies = fake_post(monkeypatch, recorded("broadcom"), page)
    jobs = adapters.workday("broadcom/wd1/External_Career")
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["ISR-Tel Aviv University, Israel", "2 Locations, Israel"]
    ids = bodies[1]["appliedFacets"]["locations"]
    assert len(ids) == 1  # the one ISR value; the Australian, Singapore and Illinois values are not Israel
    assert bodies[1]["searchText"] == ""


def places_facet(*values: tuple[str, int | None], param: str = "locations") -> dict[str, Any]:
    return {
        "facetParameter": param,
        "values": [
            {"descriptor": d, "id": f"id{n}", **({} if c is None else {"count": c})} for n, (d, c) in enumerate(values)
        ],
    }


def test_workday_a_country_value_wins_over_places_even_when_the_places_come_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facets = [places_facet(("Israel - Tel Aviv", 2)), israel_facet("Country")]
    bodies = fake_post(monkeypatch, wd_page([], facets=facets), wd_page([wd_posting(1)]))
    assert adapters.workday(SLUG) is not None
    assert bodies[1]["appliedFacets"] == {"Country": [ISRAEL_ID]}


def test_workday_a_country_named_israel_is_found_by_its_name_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    facets = [{"facetParameter": "Country", "values": [{"descriptor": " ISRAEL ", "id": "zz"}, {"id": "yy"}]}]
    bodies = fake_post(monkeypatch, wd_page([], facets=facets), wd_page([wd_posting(1)]))
    assert adapters.workday(SLUG) is not None
    assert bodies[1]["appliedFacets"] == {"Country": ["zz"]}


def test_workday_place_facet_applies_every_israeli_place_and_tags_what_the_filter_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facet = places_facet(("Office - Israel - Tel Aviv", 2), ("Spain-Madrid", 5), ("Haifa", 1), ("Madrid", None))
    page = wd_page([wd_posting(1, "Tel Aviv, Israel"), wd_posting(2, "2 Locations"), wd_posting(3, "Haifa")], total=3)
    bodies = fake_post(monkeypatch, wd_page([], facets=[facet]), page)
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["location"] for j in jobs] == ["Tel Aviv, Israel", "2 Locations, Israel", "Haifa, Israel"]
    assert bodies[1]["appliedFacets"] == {"locations": ["id0", "id2"]}


@pytest.mark.parametrize(
    "facet",
    [
        places_facet(("Remote (IL)", 3)),  # the bare code is Illinois as often as Israel
        places_facet(("AUS-Parramatta", 4)),  # "ramat" inside another word
        places_facet(("Israel - Tel Aviv", 2), param="jobFamily"),  # not a location facet
    ],
)
def test_workday_place_facet_ignores_values_that_are_not_clearly_israeli(
    monkeypatch: pytest.MonkeyPatch, facet: dict[str, Any]
) -> None:
    bodies = fake_post(monkeypatch, wd_page([], facets=[facet]), wd_page([wd_posting(1)]))
    assert adapters.workday(SLUG) is not None
    assert (bodies[1]["appliedFacets"], bodies[1]["searchText"]) == ({}, "Israel")


def test_workday_a_filtered_total_above_what_the_facet_holds_is_none_not_a_worldwide_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facet = places_facet(("Israel - Tel Aviv", 2), ("Israel - Haifa", 1))
    fake_post(monkeypatch, wd_page([], facets=[facet]), wd_page([wd_posting(1)], total=776))  # the filter was ignored
    assert adapters.workday(SLUG) is None
    country = {"facetParameter": "Country", "values": [{"descriptor": "Israel", "id": "zz", "count": 4}]}
    fake_post(monkeypatch, wd_page([], facets=[country]), wd_page([wd_posting(1)], total=776))
    assert adapters.workday(SLUG) is None


@pytest.mark.parametrize("count", [None, "3", True])
def test_workday_without_a_usable_count_the_total_cannot_be_checked_and_is_trusted(
    monkeypatch: pytest.MonkeyPatch, count: Any
) -> None:
    value = {"descriptor": "Israel", "id": "zz", **({} if count is None else {"count": count})}
    fake_post(
        monkeypatch,
        wd_page([], facets=[{"facetParameter": "Country", "values": [value]}]),
        wd_page([wd_posting(1)], total=776),
    )
    assert adapters.workday(SLUG) is not None


def test_workday_a_filtered_total_that_matches_the_facet_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    facet = places_facet(("Israel - Tel Aviv", 2), ("Israel - Haifa", 1))
    fake_post(monkeypatch, wd_page([], facets=[facet]), wd_page([wd_posting(1)], total=3))
    assert adapters.workday(SLUG) is not None


def test_workday_search_route_keeps_only_postings_located_in_israel(monkeypatch: pytest.MonkeyPatch) -> None:
    postings = [
        wd_posting(1, "Tel Aviv, Israel"),
        wd_posting(2, "Madrid, Spain"),  # mentions Israel in its text, is not there
        wd_posting(3, "2 Locations"),  # cannot be told: left out rather than guessed
        wd_posting(4, None),
        wd_posting(5, "HAIFA, ISRAEL"),
        wd_posting(6, "Ra'anana"),  # a place the matcher knows, without the country
        wd_posting(7, "Milano"),
        wd_posting(8, "Chicago, IL"),  # Illinois
        wd_posting(9, "Peoria, IL"),  # Illinois, in no list of US cities
        wd_posting(10, "Remote, IL"),
    ]
    fake_post(
        monkeypatch, wd_page([], facets=[{"facetParameter": "jobFamily", "values": [{"id": "bb"}]}]), wd_page(postings)
    )
    jobs = adapters.workday(SLUG)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job 1", "Job 5", "Job 6"]


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


JAZZHR_URL = "https://acme.applytojob.com/apply"


def jazzhr_page(*items: str) -> str:
    """A JazzHR board page, hand-built in the shape seen on linearb.applytojob.com on 2026-11-14. A board with no
    job is assumed to keep the same list wrapper: no empty board could be checked live."""
    return (
        "<main><div class='col col-xs-7 jobs-list'><h2 class='page-title'>Current Openings</h2>"
        "<ul class='list-group'>" + "".join(items) + "</ul></div></main><footer><ul><li>About</li></ul></footer>"
    )


def jazzhr_item(job_id: str, title: str, *facts: tuple[str, str]) -> str:
    return (
        "<li class=\"list-group-item\">\n<h3 class='list-group-item-heading'>\n"
        f'<a href="https://acme.applytojob.com/apply/{job_id}/Some-Title">\n  {title}   \n</a></h3>'
        "<ul class='list-inline list-group-item-text'>"
        + "".join(f"<li><i class='fa fa-{icon}'></i>{text}</li>" for icon, text in facts)
        + "</ul></li>\n"
    )


def test_jazzhr_reads_the_board_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page = jazzhr_page(
        jazzhr_item(
            "HSVVa69rsU", "Senior Software Developer", ("map-marker", "Tel Aviv, Israel"), ("sitemap", "R&amp;D")
        ),
        jazzhr_item("b2", "Sales &amp; Support", ("map-marker", "Haifa &amp; Remote"), ("map-marker", "Elsewhere")),
        jazzhr_item("c3", "Designer"),
    )
    urls = fake_get(monkeypatch, FakeHttpResponse(text=page, url=JAZZHR_URL))
    jobs = adapters.jazzhr("acme")
    assert urls == [JAZZHR_URL]
    assert jobs == [
        {
            "job_id": "HSVVa69rsU",
            "title": "Senior Software Developer",
            "location": "Tel Aviv, Israel",
            "url": "https://acme.applytojob.com/apply/HSVVa69rsU/Some-Title",
            "team": "R&D",
            "posted": "",
        },
        {
            "job_id": "b2",
            "title": "Sales & Support",
            "location": "Haifa & Remote",
            "url": "https://acme.applytojob.com/apply/b2/Some-Title",
            "team": "",
            "posted": "",
        },
        {
            "job_id": "c3",
            "title": "Designer",
            "location": "",
            "url": "https://acme.applytojob.com/apply/c3/Some-Title",
            "team": "",
            "posted": "",
        },
    ]
    assert [bool(discover.ISRAEL.search(j["location"])) for j in jobs] == [True, True, False]


def test_jazzhr_a_board_with_no_job_is_empty_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=jazzhr_page(), url=JAZZHR_URL))
    assert adapters.jazzhr("acme") == []


@pytest.mark.parametrize(
    ("text", "url"),
    [
        (jazzhr_page(jazzhr_item("a", "A")), "https://www.jazzhr.com/job-seekers"),  # unknown slug: redirected away
        ("<html>a layout without the job list</html>", JAZZHR_URL),
        (jazzhr_page('<li class="list-group-item">no link in this entry</li>'), JAZZHR_URL),
        (
            jazzhr_page(jazzhr_item("a", "A"), jazzhr_item("b", "  ")),
            JAZZHR_URL,
        ),  # a good entry, then an unreadable one
        (jazzhr_page(jazzhr_item("a", "<b></b>")), JAZZHR_URL),  # no title once the tags are gone
        (jazzhr_page(jazzhr_item("a", "A")), "https://other.applytojob.com/apply"),  # another tenant's host
        (jazzhr_page(jazzhr_item("a", "A")), "https://www.jazzhr.com/?x=.applytojob.com/"),
        # the container is there but the item markup changed: the job links are still on the page
        (jazzhr_page(jazzhr_item("a", "A").replace("list-group-item", "list-group-item job")), JAZZHR_URL),
    ],
)
def test_jazzhr_a_page_it_cannot_read_is_a_failure_not_an_empty_board(
    monkeypatch: pytest.MonkeyPatch, text: str, url: str
) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=text, url=url))
    assert adapters.jazzhr("acme") is None


def test_jazzhr_markup_inside_a_title_is_dropped_and_the_footer_is_not_read_as_a_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = jazzhr_page(jazzhr_item("a", "<span>Dev</span>   Lead", ("sitemap", "R&D")))
    page += "<footer><ul><li><i class='fa fa-map-marker'></i>Head office, Ohio</li></ul></footer>"
    fake_get(monkeypatch, FakeHttpResponse(text=page, url=JAZZHR_URL))
    jobs = adapters.jazzhr("acme")
    assert jobs is not None
    assert [(j["title"], j["location"], j["team"]) for j in jobs] == [("Dev Lead", "", "R&D")]


def test_jazzhr_is_registered() -> None:
    assert adapters.ADAPTERS["jazzhr"] is adapters.jazzhr


BAMBOO_URL = "https://acme.bamboohr.com/careers/list"


def bamboo_answer(result: Any, total: Any = None) -> FakeHttpResponse:
    meta = {"totalCount": len(result) if total is None else total}
    return FakeHttpResponse(data={"meta": meta, "result": result}, url=BAMBOO_URL)


def test_bamboohr_reads_the_careers_list(monkeypatch: pytest.MonkeyPatch) -> None:
    result = [
        {
            "id": "82",
            "jobOpeningName": "New Business Sales Executive",
            "departmentLabel": "Sales",
            "location": {"city": None, "state": None},
            "atsLocation": {"country": None, "state": None, "province": None, "city": None},
        },
        {
            "id": "121",
            "jobOpeningName": "Manager, Portfolio",
            "departmentLabel": "Card Programs",
            "location": {"city": "New York", "state": "New York"},
            "atsLocation": {"country": None},
        },
        {
            "id": "130",
            "jobOpeningName": "Backend Engineer",
            "departmentLabel": None,
            "location": {"city": "Tel Aviv", "state": None},
            "atsLocation": {"country": "Israel"},
        },
        {
            "id": "131",
            "jobOpeningName": "Data Engineer",
            "location": {"city": None, "state": None},
            "atsLocation": {"city": "Haifa", "state": "Haifa District", "province": "North", "country": "Israel"},
        },
        {"id": 132, "jobOpeningName": "Designer", "location": None, "atsLocation": None},
    ]
    urls = fake_get(monkeypatch, bamboo_answer(result))
    jobs = adapters.bamboohr("acme")
    assert urls == [BAMBOO_URL]
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"], j["team"], j["url"]) for j in jobs] == [
        ("82", "New Business Sales Executive", "", "Sales", "https://acme.bamboohr.com/careers/82"),
        ("121", "Manager, Portfolio", "New York", "Card Programs", "https://acme.bamboohr.com/careers/121"),
        ("130", "Backend Engineer", "Tel Aviv, Israel", "", "https://acme.bamboohr.com/careers/130"),
        ("131", "Data Engineer", "Haifa, Haifa District, North, Israel", "", "https://acme.bamboohr.com/careers/131"),
        ("132", "Designer", "", "", "https://acme.bamboohr.com/careers/132"),
    ]
    assert [bool(discover.ISRAEL.search(j["location"])) for j in jobs] == [False, False, True, True, False]


def test_bamboohr_a_board_with_no_job_is_empty_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, bamboo_answer([]))
    assert adapters.bamboohr("acme") == []


def test_bamboohr_without_a_count_trusts_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(data={"result": [{"id": "1", "jobOpeningName": "A"}]}, url=BAMBOO_URL))
    jobs = adapters.bamboohr("acme")
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["A"]


@pytest.mark.parametrize("data", [{}, {"result": None}, {"result": {"a": 1}}, [], None, {"meta": 3, "result": []}])
def test_bamboohr_an_answer_without_a_list_is_a_failure(monkeypatch: pytest.MonkeyPatch, data: Any) -> None:
    fake_get(monkeypatch, FakeHttpResponse(data=data, url=BAMBOO_URL))
    assert adapters.bamboohr("acme") is None


def test_bamboohr_an_html_answer_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text="<html>", json_error=True, url=BAMBOO_URL))
    assert adapters.bamboohr("acme") is None


@pytest.mark.parametrize("url", ["https://www.bamboohr.com", "https://other.bamboohr.com/careers/list"])
def test_bamboohr_an_answer_from_another_host_is_a_failure(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    fake_get(monkeypatch, FakeHttpResponse(data={"meta": {"totalCount": 0}, "result": []}, url=url))
    assert adapters.bamboohr("acme") is None


@pytest.mark.parametrize(
    "result",
    [
        [{"id": "1", "jobOpeningName": "A"}, {"id": "2"}],  # a good entry, then one with no title
        [{"id": "1", "jobOpeningName": "A"}, {"jobOpeningName": "B"}],  # no id
        [{"id": "1", "jobOpeningName": "A"}, "oops"],  # not an object
    ],
)
def test_bamboohr_one_unreadable_entry_fails_the_crawl_instead_of_closing_its_job(
    monkeypatch: pytest.MonkeyPatch, result: list[Any]
) -> None:
    fake_get(monkeypatch, bamboo_answer(result))
    assert adapters.bamboohr("acme") is None


def test_bamboohr_a_count_that_does_not_match_the_list_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, bamboo_answer([{"id": "1", "jobOpeningName": "A"}], total=2))
    assert adapters.bamboohr("acme") is None


def test_bamboohr_is_registered() -> None:
    assert adapters.ADAPTERS["bamboohr"] is adapters.bamboohr


HCM_SLUG = "acme.fa.oraclecloud.com/CX_1"
HCM_API = "https://acme.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"


def hcm_req(job_id: Any, title: Any, country: str, place: str, secondary: Any = None) -> dict[str, Any]:
    return {
        "Id": job_id,
        "Title": title,
        "PrimaryLocationCountry": country,
        "PrimaryLocation": place,
        "PostedDate": "2026-11-01",
        "secondaryLocations": secondary or [],
    }


def hcm_answer(reqs: Any, total: Any = None) -> FakeHttpResponse:
    count = len(reqs) if total is None and isinstance(reqs, list) else total
    return FakeHttpResponse(data={"items": [{"TotalJobsCount": count, "requisitionList": reqs}]})


def fake_hcm(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(responses)

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"url": url, **kw})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "get", _get)
    return calls


def test_oraclehcm_keeps_the_requisitions_located_in_israel(monkeypatch: pytest.MonkeyPatch) -> None:
    reqs = [
        hcm_req("1", "Backend Engineer", "IL", "Herzliya, Tel Aviv, Israel"),
        hcm_req("2", "Mentions Israel in its text", "GB", "READING, United Kingdom"),
        hcm_req(
            "3", "Field Engineer", "US", "New York, United States", [{"Name": "Haifa, Israel", "CountryCode": "IL"}]
        ),
        hcm_req("4", "Analyst", "IL", ""),
        hcm_req("5", "Manager", "US", "Austin", [{"Name": "", "CountryCode": "IL"}, "junk", {"CountryCode": "GB"}]),
        hcm_req("6", "Other", "US", "Austin", ["junk", {"Name": "Paris", "CountryCode": "FR"}]),
    ]
    calls = fake_hcm(monkeypatch, hcm_answer(reqs))
    jobs = adapters.oraclehcm(HCM_SLUG)
    assert len(calls) == 1
    assert calls[0]["url"] == HCM_API
    assert calls[0]["params"]["finder"] == "findReqs;siteNumber=CX_1,limit=50,offset=0,location=Israel"
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"], j["posted"]) for j in jobs] == [
        ("1", "Backend Engineer", "Herzliya, Tel Aviv, Israel", "2026-11-01"),
        ("3", "Field Engineer", "Haifa, Israel", "2026-11-01"),
        ("4", "Analyst", "Israel", "2026-11-01"),
        ("5", "Manager", "Israel", "2026-11-01"),
    ]
    assert jobs[0]["url"] == "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1"
    assert all(discover.ISRAEL.search(j["location"]) for j in jobs)


def test_oraclehcm_asks_for_the_location_not_the_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keyword search for the word Israel matches the text of a posting and missed most Israel requisitions of the
    tenants audited (PLAN_3.md B3); the location filter returns them all. An Israel requisition whose text never says
    Israel is kept; one in another country that the answer includes is still dropped, by its country code."""
    reqs = [
        hcm_req("7", "Software Principal Engineer (Beer-Sheva)", "IL", "Israel"),
        hcm_req("8", "Engineer", "GB", "READING, United Kingdom"),
    ]
    calls = fake_hcm(monkeypatch, hcm_answer(reqs))
    jobs = adapters.oraclehcm(HCM_SLUG)
    finder = calls[0]["params"]["finder"]
    assert "location=Israel" in finder
    assert "keyword" not in finder
    assert jobs is not None
    assert [(j["job_id"], j["location"]) for j in jobs] == [("7", "Israel")]


def test_oraclehcm_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    first = hcm_answer([hcm_req(str(i), "T", "IL", "Haifa, Israel") for i in range(50)], total=51)
    second = hcm_answer([hcm_req("50", "T", "IL", "Haifa, Israel")], total=51)
    calls = fake_hcm(monkeypatch, first, second)
    jobs = adapters.oraclehcm(HCM_SLUG)
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == [str(i) for i in range(51)]
    assert [c["params"]["finder"].split(",")[2] for c in calls] == ["offset=0", "offset=50"]


def test_oraclehcm_no_match_is_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_hcm(monkeypatch, hcm_answer([]))
    assert adapters.oraclehcm(HCM_SLUG) == []


@pytest.mark.parametrize("total", [3, 51, 500])
def test_oraclehcm_an_empty_page_the_total_contradicts_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, total: int
) -> None:
    fake_hcm(monkeypatch, hcm_answer([], total=total))
    assert adapters.oraclehcm(HCM_SLUG) is None


def test_oraclehcm_a_short_second_page_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = hcm_answer([hcm_req(str(i), "T", "IL", "Haifa, Israel") for i in range(50)], total=60)
    fake_hcm(monkeypatch, first, hcm_answer([hcm_req("50", "T", "IL", "Haifa, Israel")], total=60))
    assert adapters.oraclehcm(HCM_SLUG) is None


def test_oraclehcm_more_pages_than_the_limit_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "ORACLEHCM_MAX_PAGES", 2)
    full = hcm_answer([hcm_req(str(i), "T", "IL", "Haifa, Israel") for i in range(50)], total=500)
    fake_hcm(monkeypatch, full)
    assert adapters.oraclehcm(HCM_SLUG) is None


@pytest.mark.parametrize(
    "slug", ["acme", "acme.fa.oraclecloud.com", "a.com/b/c", "nodot/CX_1", "acme.fa.oraclecloud.com/"]
)
def test_oraclehcm_a_bad_slug_is_a_failure(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    calls = fake_hcm(monkeypatch, hcm_answer([]))
    assert adapters.oraclehcm(slug) is None
    assert calls == []


@pytest.mark.parametrize(
    "resp", [None, FakeHttpResponse(status_code=503), FakeHttpResponse(text="<html>", json_error=True)]
)
def test_oraclehcm_a_failed_request_or_html_answer_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, resp: FakeHttpResponse | None
) -> None:
    fake_hcm(monkeypatch, resp)
    assert adapters.oraclehcm(HCM_SLUG) is None


@pytest.mark.parametrize(
    "answer",
    [
        FakeHttpResponse(data={}),
        FakeHttpResponse(data={"items": []}),
        FakeHttpResponse(data={"items": [{"TotalJobsCount": 1}]}),
        hcm_answer({"a": 1}, total=1),
        hcm_answer([], total="1"),
        hcm_answer([{"Id": "1"}], total=1),  # no title
        hcm_answer([{"Title": "T"}], total=1),  # no id
        hcm_answer(["junk"], total=1),
    ],
)
def test_oraclehcm_an_answer_that_is_not_a_search_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse
) -> None:
    fake_hcm(monkeypatch, answer)
    assert adapters.oraclehcm(HCM_SLUG) is None


def test_oraclehcm_a_failure_after_the_first_page_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = hcm_answer([hcm_req(str(i), "T", "IL", "Haifa, Israel") for i in range(50)], total=100)
    fake_hcm(monkeypatch, first, FakeHttpResponse(status_code=500))
    assert adapters.oraclehcm(HCM_SLUG) is None


def test_oraclehcm_is_registered() -> None:
    assert adapters.ADAPTERS["oraclehcm"] is adapters.oraclehcm


AMAZON_PAGE_SIZE = 100


def amz_job(job_id: Any, title: Any = "Engineer", path: Any = "/en/jobs/1/x", country: str = "ISR") -> dict[str, Any]:
    return {
        "id_icims": job_id,
        "title": title,
        "job_path": path,
        "country_code": country,
        "normalized_location": "Tel Aviv-Yafo, Tel Aviv, ISR",
        "location": "IL, Tel Aviv",
        "job_category": "Software Development",
    }


def amz_answer(jobs: Any, hits: Any = None) -> FakeHttpResponse:
    return FakeHttpResponse(data={"hits": len(jobs) if hits is None else hits, "jobs": jobs})


def fake_amz(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(responses)

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"url": url, **kw})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "get", _get)
    return calls


def test_amazon_reads_the_jobs_of_a_country(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_in = [amz_job("10", "A", "/en/jobs/10/a"), amz_job("11", "B", "/en/jobs/11/b")]
    jobs_in[0]["posted_date"] = "November 12, 2026"
    jobs_in[1]["normalized_location"] = None  # falls back to the plain location
    calls = fake_amz(monkeypatch, amz_answer(jobs_in))
    jobs = adapters.amazon("ISR")
    assert len(calls) == 1
    assert calls[0]["url"] == "https://www.amazon.jobs/en/search.json"
    assert calls[0]["params"] == {"country": "ISR", "result_limit": 100, "offset": 0, "sort": "recent"}
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"], j["team"], j["url"]) for j in jobs] == [
        ("10", "A", "Tel Aviv-Yafo, Tel Aviv, ISR", "Software Development", "https://www.amazon.jobs/en/jobs/10/a"),
        ("11", "B", "IL, Tel Aviv", "Software Development", "https://www.amazon.jobs/en/jobs/11/b"),
    ]
    assert [j["posted"] for j in jobs] == ["November 12, 2026", ""]
    assert all(discover.ISRAEL.search(j["location"]) for j in jobs)


def test_amazon_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    first = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=AMAZON_PAGE_SIZE + 1)
    second = amz_answer([amz_job("last")], hits=AMAZON_PAGE_SIZE + 1)
    calls = fake_amz(monkeypatch, first, second)
    jobs = adapters.amazon("ISR")
    assert jobs is not None
    assert [j["job_id"] for j in jobs][-2:] == ["99", "last"]
    assert [c["params"]["offset"] for c in calls] == [0, AMAZON_PAGE_SIZE]


def test_amazon_no_job_at_all_is_a_failure_not_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_amz(monkeypatch, amz_answer([]))  # what a wrong country code answers
    assert adapters.amazon("XXX") is None


def test_amazon_a_total_that_moves_between_pages_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=AMAZON_PAGE_SIZE + 1)
    second = amz_answer([amz_job("last")], hits=AMAZON_PAGE_SIZE)  # a job closed between the two requests
    fake_amz(monkeypatch, first, second)
    assert adapters.amazon("ISR") is None


def test_amazon_a_job_listed_twice_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=AMAZON_PAGE_SIZE + 1)
    second = amz_answer([amz_job("0")], hits=AMAZON_PAGE_SIZE + 1)  # a job added in between shifted page one's last
    fake_amz(monkeypatch, first, second)
    assert adapters.amazon("ISR") is None


def test_amazon_fewer_jobs_than_the_hits_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_amz(monkeypatch, amz_answer([amz_job("1")], hits=3), amz_answer([], hits=3))
    assert adapters.amazon("ISR") is None


def test_amazon_more_jobs_than_the_hits_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_amz(monkeypatch, amz_answer([amz_job("1"), amz_job("2")], hits=1))
    assert adapters.amazon("ISR") is None


def test_amazon_an_empty_page_the_hits_contradict_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=250)
    fake_amz(monkeypatch, first, amz_answer([], hits=250))
    assert adapters.amazon("ISR") is None


def test_amazon_more_pages_than_the_limit_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "AMAZON_MAX_PAGES", 2)
    full = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=10_000)
    fake_amz(monkeypatch, full)
    assert adapters.amazon("ISR") is None


@pytest.mark.parametrize(
    "resp", [None, FakeHttpResponse(status_code=503), FakeHttpResponse(text="<html>", json_error=True)]
)
def test_amazon_a_failed_request_or_html_answer_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, resp: FakeHttpResponse | None
) -> None:
    fake_amz(monkeypatch, resp)
    assert adapters.amazon("ISR") is None


@pytest.mark.parametrize(
    "answer",
    [
        FakeHttpResponse(data={}),
        FakeHttpResponse(data=None),
        FakeHttpResponse(data={"hits": 1}),
        FakeHttpResponse(data={"hits": "1", "jobs": []}),
        FakeHttpResponse(data={"hits": 1, "jobs": {"a": 1}}),
        amz_answer(["junk"], hits=1),
        amz_answer([amz_job("")], hits=1),  # no id
        amz_answer([amz_job("1", title="")], hits=1),
        amz_answer([amz_job("1", path=None)], hits=1),
        amz_answer([amz_job("1", country="USA")], hits=1),  # not the asked country
    ],
)
def test_amazon_an_answer_that_is_not_a_clean_search_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse
) -> None:
    fake_amz(monkeypatch, answer)
    assert adapters.amazon("ISR") is None


def test_amazon_a_failure_after_the_first_page_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = amz_answer([amz_job(str(i)) for i in range(AMAZON_PAGE_SIZE)], hits=250)
    fake_amz(monkeypatch, first, FakeHttpResponse(status_code=500))
    assert adapters.amazon("ISR") is None


def test_amazon_is_registered() -> None:
    assert adapters.ADAPTERS["amazon"] is adapters.amazon


PHENOM_SLUG = "careers.acme.com/global/en"
PHENOM_PAGE_SIZE = 50


def ph_job(
    job_id: Any, title: Any = "Engineer", seq: Any = "SEQ", place: Any = "Haifa, Israel", **extra: Any
) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "title": title,
        "jobSeqNo": seq,
        "location": place,
        "multi_location": [place] if place else [],
        "category": "Engineering",
        "postedDate": "2026-10-06T00:00:00.000+0000",
        **extra,
    }


def ph_answer(jobs: Any, total: Any = None, israel: Any = None) -> FakeHttpResponse:
    count = len(jobs) if total is None and isinstance(jobs, list) else total
    facet = {"United States of America": 99, "Israel": count if israel is None else israel}
    data = {"jobs": jobs, "aggregations": [{"field": "city", "value": {}}, {"field": "country", "value": facet}]}
    return FakeHttpResponse(data={"refineSearch": {"totalHits": count, "data": data}})


def fake_ph(monkeypatch: pytest.MonkeyPatch, *responses: FakeHttpResponse | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(responses)

    def _post(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"url": url, **kw})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(adapters, "post", _post)
    return calls


def test_phenom_reads_the_israel_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_in = [
        ph_job("1", "A", "S1", "Tel Aviv-Yafo, Israel"),
        ph_job("2", "B", "S2", "Austin, United States", multi_location=["Austin, United States", "Haifa, Israel"]),
    ]
    calls = fake_ph(monkeypatch, ph_answer(jobs_in))
    jobs = adapters.phenom(PHENOM_SLUG)
    assert len(calls) == 1
    assert calls[0]["url"] == "https://careers.acme.com/widgets"
    body = calls[0]["json"]
    assert (body["lang"], body["country"], body["from"], body["size"]) == ("en_global", "global", 0, PHENOM_PAGE_SIZE)
    assert body["selected_fields"] == {"country": ["Israel"]}
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"], j["team"], j["url"]) for j in jobs] == [
        ("1", "A", "Tel Aviv-Yafo, Israel", "Engineering", "https://careers.acme.com/global/en/job/S1"),
        ("2", "B", "Haifa, Israel", "Engineering", "https://careers.acme.com/global/en/job/S2"),
    ]
    assert jobs[0]["posted"] == "2026-10-06T00:00:00.000+0000"
    assert all(discover.ISRAEL.search(j["location"]) for j in jobs)


def test_phenom_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ph_answer([ph_job(str(i), seq=f"S{i}") for i in range(PHENOM_PAGE_SIZE)], total=PHENOM_PAGE_SIZE + 1)
    second = ph_answer([ph_job("last", seq="SL")], total=PHENOM_PAGE_SIZE + 1)
    calls = fake_ph(monkeypatch, first, second)
    jobs = adapters.phenom(PHENOM_SLUG)
    assert jobs is not None
    assert [j["job_id"] for j in jobs][-2:] == ["49", "last"]
    assert [c["json"]["from"] for c in calls] == [0, PHENOM_PAGE_SIZE]


def test_phenom_no_job_is_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ph(monkeypatch, ph_answer([]))
    assert adapters.phenom(PHENOM_SLUG) == []


def test_phenom_no_israel_entry_in_the_country_facet_means_none_to_find(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"jobs": [], "aggregations": [{"field": "country", "value": {"France": 4}}]}
    fake_ph(monkeypatch, FakeHttpResponse(data={"refineSearch": {"totalHits": 0, "data": data}}))
    assert adapters.phenom(PHENOM_SLUG) == []


def test_phenom_a_filter_that_matches_nothing_while_the_facet_counts_jobs_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ph(monkeypatch, ph_answer([], total=0, israel=48))  # what a renamed facet value or a typo answers
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_total_the_facet_does_not_confirm_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ph(monkeypatch, ph_answer([ph_job("1")], total=1, israel=2))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_job_outside_israel_means_the_filter_was_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ph(monkeypatch, ph_answer([ph_job("1", place="Haifa, Israel"), ph_job("2", place="London, UK")]))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_job_with_no_place_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ph(monkeypatch, ph_answer([ph_job("1", place=None, multi_location=None)]))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_fewer_jobs_than_the_total_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ph(monkeypatch, ph_answer([ph_job("1")], total=3), ph_answer([], total=3))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_total_that_moves_between_pages_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ph_answer([ph_job(str(i), seq=f"S{i}") for i in range(PHENOM_PAGE_SIZE)], total=PHENOM_PAGE_SIZE + 1)
    second = ph_answer([ph_job("last", seq="SL")], total=PHENOM_PAGE_SIZE)
    fake_ph(monkeypatch, first, second)
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_job_listed_twice_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ph_answer([ph_job(str(i), seq=f"S{i}") for i in range(PHENOM_PAGE_SIZE)], total=PHENOM_PAGE_SIZE + 1)
    fake_ph(monkeypatch, first, ph_answer([ph_job("0", seq="S0")], total=PHENOM_PAGE_SIZE + 1))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_more_pages_than_the_limit_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "PHENOM_MAX_PAGES", 2)
    full = ph_answer([ph_job(str(i), seq=f"S{i}") for i in range(PHENOM_PAGE_SIZE)], total=10_000)
    fake_ph(monkeypatch, full)
    assert adapters.phenom(PHENOM_SLUG) is None


@pytest.mark.parametrize(
    "slug",
    [
        "acme",
        "careers.acme.com",
        "careers.acme.com/global",
        "a.com/b/c/d",
        "nodot/global/en",
        "a.com//en",
        "a.com/global/",
    ],
)
def test_phenom_a_bad_slug_is_a_failure(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    calls = fake_ph(monkeypatch, ph_answer([]))
    assert adapters.phenom(slug) is None
    assert calls == []


@pytest.mark.parametrize(
    "resp", [None, FakeHttpResponse(status_code=503), FakeHttpResponse(text="<html>", json_error=True)]
)
def test_phenom_a_failed_request_or_html_answer_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, resp: FakeHttpResponse | None
) -> None:
    fake_ph(monkeypatch, resp)
    assert adapters.phenom(PHENOM_SLUG) is None


@pytest.mark.parametrize(
    "answer",
    [
        FakeHttpResponse(data={}),
        FakeHttpResponse(data=None),
        FakeHttpResponse(data={"refineSearch": {"totalHits": 1}}),
        FakeHttpResponse(data={"refineSearch": {"totalHits": 1, "data": {"jobs": []}}}),  # no country facet
        FakeHttpResponse(data={"refineSearch": {"totalHits": 1, "data": {"jobs": [], "aggregations": []}}}),
        FakeHttpResponse(data={"refineSearch": {"totalHits": 1, "data": {"jobs": [], "aggregations": None}}}),
        FakeHttpResponse(
            data={"refineSearch": {"totalHits": 1, "data": {"jobs": [], "aggregations": [{"field": "country"}]}}}
        ),
        FakeHttpResponse(
            data={
                "refineSearch": {
                    "totalHits": 1,
                    "data": {"jobs": [], "aggregations": [{"field": "country", "value": 5}]},
                }
            }
        ),
        FakeHttpResponse(data={"refineSearch": {"totalHits": "1", "data": {"jobs": []}}}),
        FakeHttpResponse(data={"refineSearch": {"totalHits": 1, "data": {"jobs": {"a": 1}}}}),
        ph_answer({"a": 1}, total=1),
        ph_answer([], total="1"),
        ph_answer(["junk"], total=1),
        ph_answer([ph_job("")], total=1),  # no id
        ph_answer([ph_job("1", title="")], total=1),
        ph_answer([ph_job("1", seq=None)], total=1),
    ],
)
def test_phenom_an_answer_that_is_not_a_clean_search_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse
) -> None:
    fake_ph(monkeypatch, answer)
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_a_failure_after_the_first_page_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    first = ph_answer([ph_job(str(i), seq=f"S{i}") for i in range(PHENOM_PAGE_SIZE)], total=100)
    fake_ph(monkeypatch, first, FakeHttpResponse(status_code=500))
    assert adapters.phenom(PHENOM_SLUG) is None


def test_phenom_is_registered() -> None:
    assert adapters.ADAPTERS["phenom"] is adapters.phenom


# Trimmed from the real pages fetched on 2026-11-16: the plugin's own card (ree.auto, gauzy.com), a Next.js card
# (guesty.com) and the plugin's notice for no job (apester.com).
COMEET_REE = """<div class="comeet-outer-wrapper"><div id="d" class="comeet-groups-list">
<div class="comeet-g-r"><div class="comeet-list comeet-group-name"><a href="https://ree.test/careers-2/co/other/all">Other</a></div>
<ul class="comeet-positions-list"><li>
<a class="comeet-position" href="//ree.test/careers-2/co/israel-glil-yam/66.A56/motion-control-sw-engineer/all">
<div class="comeet-position-name">
<!--<a href="//ree.test/careers-2/co/israel-glil-yam/66.A56/motion-control-sw-engineer/all">Motion Control</a> -->
Motion Control SW Engineer </div>
<div class="comeet-position-meta">Israel, Glil Yam </div></a></li></ul></div></div></div>"""
COMEET_GAUZY = """<div class="comeet-outer-wrapper"><ul class="comeet-positions-list"><li>
<a class="comeet-position" href="//gauzy.test/gauzy-careers/co/lyon-france/D7.761/comptable-client-h-f/all">
<div class="comeet-position-name">
<!--<a href="//gauzy.test/gauzy-careers/co/lyon-france/D7.761/comptable-client-h-f/all">Comptable Client H/F</a> -->
Comptable Client H/F <br><span class="position">Finance &amp; Legal</span> </div>
<div class="comeet-position-meta"><br />A short description. &middot; Full-time </div></a></li></ul></div>"""
COMEET_GUESTY = (
    '<p aria-live="polite">2 open positions</p><ul class="Block_grid"><li>'
    '<a class="Block_card" href="/careers-open-positions/co/israel/D2.374/senior-ai-platform-engineer/">'
    '<span class="Block_department">Engineering</span><h3>Senior AI Platform Engineer</h3>'
    '<span class="Block_location"><svg><path d="M1 1"></path></svg>Tel Aviv, Israel</span></a></li>'
    '<li><a class="Block_card" href="/careers-open-positions/co/london-uk/3C.37D-3E.502/staff-product-manager/">'
    '<span class="Block_department">Product</span><h3>Staff Product Manager</h3>'
    '<span class="Block_location">London, UK</span></a></li></ul>'
)
COMEET_NONE = (
    '<div class="comeet-outer-wrapper">We don&#8217;t have any open positions at this time. Please visit again soon.'
    "<!-- Plugin version data --></div>"
)
COMEET_URL = "https://ree.test/careers/"


@pytest.mark.parametrize("resp", [None, FakeHttpResponse(status_code=500)])
def test_comeet_page_failure_returns_none(monkeypatch: pytest.MonkeyPatch, resp: FakeHttpResponse | None) -> None:
    fake_get(monkeypatch, resp)
    assert adapters.comeet_page(COMEET_URL) is None


def test_comeet_page_reads_the_plugin_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, FakeHttpResponse(text=COMEET_REE, url=COMEET_URL))
    assert adapters.comeet_page(COMEET_URL) == [
        {
            "job_id": "66.A56",
            "title": "Motion Control SW Engineer",  # the commented copy of the link does not end the card early
            "location": "israel glil yam",
            "url": "https://ree.test/careers-2/co/israel-glil-yam/66.A56/motion-control-sw-engineer/all",
            "team": "",
            "posted": "",
        }
    ]
    assert urls == [COMEET_URL]


def test_comeet_page_reads_the_department_and_the_place_of_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=COMEET_GAUZY, url="https://gauzy.test/careers/"))
    jobs = adapters.comeet_page("https://gauzy.test/careers/")
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["team"], j["location"]) for j in jobs] == [
        ("D7.761", "Comptable Client H/F", "Finance & Legal", "lyon france")
    ]
    assert jobs[0]["url"].startswith("https://gauzy.test/gauzy-careers/co/lyon-france/")


def test_comeet_page_an_empty_location_span_falls_back_to_the_place_of_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = COMEET_GUESTY.replace(
        '<span class="Block_location">London, UK</span>', '<span class="Block_location"></span>'
    )
    fake_get(monkeypatch, FakeHttpResponse(text=page, url="https://guesty.test/careers/"))
    jobs = adapters.comeet_page("https://guesty.test/careers/")
    assert jobs is not None
    assert jobs[1]["location"] == "london uk"


def test_comeet_page_a_slug_without_a_host_fails() -> None:
    assert adapters.comeet_page("not a url") is None


def test_comeet_page_reads_heading_cards_with_a_place_in_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=COMEET_GUESTY, url="https://guesty.test/careers/"))
    jobs = adapters.comeet_page("https://guesty.test/careers/")
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["team"], j["location"], j["url"]) for j in jobs] == [
        (
            "D2.374",
            "Senior AI Platform Engineer",
            "Engineering",
            "Tel Aviv, Israel",
            "https://guesty.test/careers-open-positions/co/israel/D2.374/senior-ai-platform-engineer/",
        ),
        (
            "3C.37D-3E.502",  # a job in a second place has a two-part id
            "Staff Product Manager",
            "Product",
            "London, UK",
            "https://guesty.test/careers-open-positions/co/london-uk/3C.37D-3E.502/staff-product-manager/",
        ),
    ]


def test_comeet_page_a_page_that_says_no_open_position_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=COMEET_NONE, url=COMEET_URL))
    assert adapters.comeet_page(COMEET_URL) == []


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("<main>Careers</main>", COMEET_URL),  # no job and no notice: not a page we understand
        (COMEET_NONE.replace("comeet-outer-wrapper", "other"), COMEET_URL),  # a notice, but not the plugin's
        (COMEET_REE, "https://parked.test/careers/"),  # redirected to another site
        (COMEET_REE.replace("comeet-position-name", "title"), COMEET_URL),  # changed card: no title to read
        (COMEET_GUESTY.replace("<h3>Staff Product Manager</h3>", "<h3> </h3>"), COMEET_URL),  # an empty heading
        # a job link outside any card we read (the markup changed) makes the list partial
        (COMEET_REE + '<script>{"href":"/co/israel/AA.111/lost-job/"}</script>', COMEET_URL),
        (COMEET_REE.replace("/co/israel-glil-yam/", "/co/Tel-Aviv/") + "<p>/co/israel/FF.666/lost/</p>", COMEET_URL),
        (COMEET_REE.replace("motion-control-sw-engineer", "%D7%9E%D7%94"), COMEET_URL),  # a slug the card regex skips
        (
            COMEET_NONE.replace("We don", "<script>We don").replace("soon.", "soon.</script>"),
            COMEET_URL,
        ),  # notice in a script
        (COMEET_REE.replace("<a class", "<span class").replace("</a></li>", "</span></li>"), COMEET_URL),
    ],
)
def test_comeet_page_a_page_it_cannot_read_is_a_failure_not_an_empty_board(
    monkeypatch: pytest.MonkeyPatch, text: str, url: str
) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=text, url=url))
    assert adapters.comeet_page(COMEET_URL) is None


# --- descriptions (PLAN.md step 7.1): the text a site already gives in the list it is read from ---------------------


def test_greenhouse_asks_for_the_content_in_the_same_request_and_reads_the_escaped_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {
        "jobs": [
            {
                "id": 1,
                "title": "A",
                "location": {"name": "Tel Aviv"},
                "absolute_url": "u1",
                "content": "&lt;p&gt;Build things.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
                "&lt;script&gt;x()&lt;/script&gt;",
                "departments": [{"id": 4, "name": "R&D", "child_ids": []}, {"name": ""}, "junk"],
                "offices": [{"name": "Tel Aviv", "location": "Israel"}],
                "metadata": [{"name": "Internal", "value": "secret"}],
                "data_compliance": [{"type": "gdpr"}],
            },
            {"id": 2, "title": "B", "location": None, "absolute_url": "u2", "content": None},
            {"id": 3, "title": "C", "location": None, "absolute_url": "u3", "content": 5, "departments": "x"},
        ]
    }
    urls = fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.greenhouse("acme")
    assert urls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"]  # one request, as before
    assert jobs is not None
    assert jobs[0]["description"] == "Build things.\n\n- Python"
    assert jobs[0]["details"] == {"departments": ["R&D"], "offices": ["Tel Aviv"]}  # not the metadata or compliance
    assert "secret" not in json.dumps(jobs)
    # a job whose content is missing or not text is exactly the job of before: no description, no details
    assert "description" not in jobs[1]
    assert "details" not in jobs[1]
    assert jobs[2] == adapters._job(3, "C", None, "u3")


def test_greenhouse_url_reads_the_content_when_the_proxy_gives_it(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"jobs": [{"id": 1, "title": "A", "absolute_url": "u", "content": "&lt;p&gt;Hi&lt;/p&gt;"}]}
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.greenhouse_url("https://x.example/jobs.json")
    assert jobs is not None
    assert jobs[0]["description"] == "Hi"


def test_lever_joins_the_description_the_lists_and_the_closing_text(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [
        {
            "id": "a",
            "text": "Engineer",
            "hostedUrl": "u",
            "categories": {"location": "Tel Aviv", "commitment": "Full-time", "department": "R&D", "team": "Core"},
            "descriptionPlain": "We build.",
            "lists": [
                {"text": "What you will do", "content": "<li>Ship</li><li>Learn</li>"},
                {"text": "", "content": "<li>Only items</li>"},
                "junk",
            ],
            "additionalPlain": "Equal opportunity.",
            "workplaceType": "hybrid",
            "country": "IL",
        },
        {"id": "b", "text": "No text", "hostedUrl": "u", "categories": None, "lists": "not a list"},
    ]
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.lever("acme")
    assert jobs is not None
    assert (
        jobs[0]["description"] == "We build.\n\nWhat you will do\n- Ship\n- Learn\n\n- Only items\n\nEqual opportunity."
    )
    assert jobs[0]["details"] == {
        "employment_type": "Full-time",
        "department": "R&D",
        "workplace_type": "hybrid",
        "country": "IL",
    }
    assert jobs[1] == adapters._job("b", "No text", "", "u")


def test_ashby_takes_the_plain_text_or_reads_the_html(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "jobs": [
            {
                "id": "1",
                "title": "A",
                "jobUrl": "u",
                "descriptionPlain": "Plain  text",
                "descriptionHtml": "<p>Ignored</p>",
                "employmentType": "FullTime",
                "department": "Eng",
                "workplaceType": None,
                "isRemote": False,
            },
            {"id": "2", "title": "B", "jobUrl": "u", "descriptionHtml": "<p>From <i>html</i></p>"},
            {"id": "3", "title": "C", "jobUrl": "u"},
        ]
    }
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.ashby("acme")
    assert jobs is not None
    assert (jobs[0]["description"], jobs[1]["description"]) == ("Plain text", "From html")
    assert jobs[0]["details"] == {"employment_type": "FullTime", "department": "Eng", "is_remote": False}
    assert "details" not in jobs[1]
    assert jobs[2] == adapters._job("3", "C", None, "u")


def test_workable_asks_for_the_details_in_the_same_request(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "jobs": [
            {
                "shortcode": "S1",
                "title": "A",
                "url": "u",
                "city": "Tel Aviv",
                "country": "Israel",
                "description": "<p>Do <b>it</b></p>",
                "employment_type": "Full-time",
                "function": "Engineering",
                "industry": None,
                "experience": "Mid",
                "education": None,
                "telecommuting": False,
            },
            {"shortcode": "S2", "title": "B", "url": "u"},
        ]
    }
    urls = fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.workable("acme")
    assert urls == ["https://apply.workable.com/api/v1/widget/accounts/acme?details=true"]
    assert jobs is not None
    assert jobs[0]["description"] == "Do it"
    assert jobs[0]["details"] == {
        "employment_type": "Full-time",
        "function": "Engineering",
        "experience": "Mid",
        "telecommuting": False,
    }
    assert jobs[1] == adapters._job("S2", "B", "", "u")


def comeet_page_with(positions: list[dict[str, Any]]) -> FakeHttpResponse:
    body = "<script>COMPANY_POSITIONS_DATA = " + json.dumps(positions) + ";</script>"
    return FakeHttpResponse(text=body)


def test_comeet_reads_the_parts_of_the_posting_in_their_order(monkeypatch: pytest.MonkeyPatch) -> None:
    base = {"url_active_page": "https://c.example/a", "url_comeet_hosted_page": "h", "location": {"city": "Tel Aviv"}}
    positions = [
        {
            **base,
            "uid": "01.A01",
            "name": "Dev",
            "employment_type": "Full-time",
            "experience_level": "Senior",
            "workplace_type": "Hybrid",
            "custom_fields": {
                "details": [
                    {"name": "Requirements", "order": 2, "value": "<ul><li>Python</li></ul>"},
                    {"name": "Description", "order": 1, "value": "<p>Build.</p>"},
                    {"name": "Empty", "order": 3, "value": ""},
                    {"name": "Odd order", "order": "x", "value": None},
                    "junk",
                ],
                "categories": [],
            },
        },
        {**base, "uid": "01.A02", "name": "Plain", "custom_fields": {"details": "not a list"}},
        {**base, "uid": "01.A03", "name": "None", "custom_fields": None},
        {**base, "uid": "01.A04", "name": "Missing"},
    ]
    fake_get(monkeypatch, comeet_page_with(positions))
    jobs = adapters.comeet("c/01.001")
    assert jobs is not None
    assert jobs[0]["description"] == "Description\nBuild.\n\nRequirements\n- Python"
    assert jobs[0]["details"] == {
        "employment_type": "Full-time",
        "experience_level": "Senior",
        "workplace_type": "Hybrid",
    }
    assert all("description" not in j and "details" not in j for j in jobs[1:])


def test_amazon_keeps_the_description_and_the_qualifications_it_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    job = amz_job("1")
    job.update(
        description="Build<br/>things",
        basic_qualifications="<li>3 years</li>",
        preferred_qualifications="<li>Go</li>",
        job_family="Software",
        job_schedule_type="full-time",
    )
    bare = amz_job("2", path="/en/jobs/2/y")
    fake_amz(monkeypatch, amz_answer([job, bare]))
    jobs = adapters.amazon("ISR")
    assert jobs is not None
    assert (
        jobs[0]["description"] == "Build\nthings\n\nBasic qualifications\n- 3 years\n\nPreferred qualifications\n- Go"
    )
    assert jobs[0]["details"] == {"job_family": "Software", "employment_type": "full-time"}
    assert "description" not in jobs[1]


def test_a_description_over_the_cap_is_cut_and_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    data = [{"id": "a", "text": "T", "hostedUrl": "u", "descriptionPlain": "word " * 10_000}]
    fake_get(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.lever("acme")
    assert jobs is not None
    assert len(jobs[0]["description"]) <= descriptions.MAX_CHARS
    assert jobs[0]["details"] == {"description_truncated": True}


WD_SLUG = "acme/wd3/Site"
WD_JOB = {"job_id": "/job/Haifa/Engineer_R1", "title": "T"}
WD_URL = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Site/job/Haifa/Engineer_R1"


def wd_detail(html_text: str = "<p>Build things</p><ul><li>Python</li></ul>", **info: Any) -> FakeHttpResponse:
    return FakeHttpResponse(data={"jobPostingInfo": {"jobDescription": html_text, "timeType": "Full time", **info}})


def test_a_workday_job_page_gives_the_text_and_the_employment_type_only(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, wd_detail(questionnaireId="q", externalUrl="https://x", id="abc"))
    assert adapters.workday_description(WD_SLUG, WD_JOB) == (
        "Build things\n\n- Python",
        {"employment_type": "Full time"},
    )
    assert urls == [WD_URL]  # the same /wday/cxs/ path as the list, on the tenant's own host


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=403),
        FakeHttpResponse(json_error=True),
        FakeHttpResponse(data={}),
        FakeHttpResponse(data=[]),
        FakeHttpResponse(data={"jobPostingInfo": "x"}),
    ],
)
def test_a_workday_job_page_that_does_not_answer_with_a_posting_gives_nothing(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_get(monkeypatch, answer)
    assert adapters.workday_description(WD_SLUG, WD_JOB) is None


def test_a_workday_job_without_a_path_of_its_own_is_never_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, wd_detail())
    assert adapters.workday_description(WD_SLUG, {"job_id": "@evil.test/x"}) is None
    assert urls == []


def test_a_workday_job_page_without_text_keeps_the_job_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, wd_detail(None))
    job = {**WD_JOB}
    adapters.add_descriptions("workday", WD_SLUG, "Acme", [job], set())
    assert "description" not in job


def jobs_n(n: int) -> list[dict[str, Any]]:
    return [{"job_id": f"/job/x/J{i}", "title": "T"} for i in range(n)]


def test_job_pages_are_asked_only_for_jobs_without_a_stored_text_and_at_most_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = fake_get(monkeypatch, wd_detail())
    monkeypatch.setattr(adapters, "DETAIL_CAP", 3)
    jobs = jobs_n(6)
    jobs[0]["description"] = "already given by the list"
    held = {("Acme", "/job/x/J1")}
    assert adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs, held) == (3, 3, 1)  # 4 to do: 3 now, 1 later
    assert [u.rsplit("/", 1)[1] for u in urls] == ["J2", "J3", "J4"]
    assert jobs[1].get("text_held") is True and "description" not in jobs[1]
    assert jobs[2]["description"] == "Build things\n\n- Python"
    assert "description" not in jobs[5] and "text_held" not in jobs[5]  # waits for the next run


def test_a_job_page_that_fails_or_raises_costs_only_its_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(slug: str, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if job["job_id"].endswith("J1"):
            raise ValueError("odd page")
        return "text", {}

    monkeypatch.setitem(adapters.DETAILS, "workday", boom)
    jobs = jobs_n(3)
    assert adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs, set()) == (3, 2, 0)
    assert [bool(j.get("description")) for j in jobs] == [True, False, True]


def test_an_adapter_with_the_text_in_its_list_asks_for_no_job_page(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, wd_detail())
    assert adapters.add_descriptions("lever", "acme", "Acme", jobs_n(2), set()) == (0, 0, 0)
    assert urls == []


def test_five_misses_in_a_row_end_a_companys_job_pages_for_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, FakeHttpResponse(status_code=429))
    jobs = jobs_n(20)
    assert adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs, set()) == (adapters.DETAIL_FAIL_STREAK, 0, 15)
    assert len(urls) == adapters.DETAIL_FAIL_STREAK  # a host that refuses is left alone


def test_the_window_of_job_pages_moves_each_day_and_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, wd_detail())
    monkeypatch.setattr(adapters, "DETAIL_CAP", 2)
    adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs_n(6), set(), day=1)
    assert [u.rsplit("/", 1)[1] for u in urls] == ["J5", "J0"]  # the window starts five jobs on and wraps


def test_a_hit_resets_the_miss_streak_so_only_misses_in_a_row_stop_the_company(monkeypatch: pytest.MonkeyPatch) -> None:
    pattern = [False] * 4 + [True] + [False] * 4 + [True]  # never five misses in a row

    def sometimes(slug: str, job: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        return ("text", {}) if pattern[int(job["job_id"].rsplit("J", 1)[1])] else None

    monkeypatch.setitem(adapters.DETAILS, "workday", sometimes)
    assert adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs_n(10), set()) == (10, 2, 0)


def test_jobs_behind_five_failing_pages_are_reached_on_later_days(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: set[str] = set()

    def first_five_fail(slug: str, job: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        asked.add(job["job_id"])
        return None if int(job["job_id"].rsplit("J", 1)[1]) < adapters.DETAIL_FAIL_STREAK else ("text", {})

    monkeypatch.setitem(adapters.DETAILS, "workday", first_five_fail)
    for day in range(3):  # ten pending jobs, a short list, and the first five never answer
        adapters.add_descriptions("workday", WD_SLUG, "Acme", jobs_n(10), set(), day=day)
    assert asked == {f"/job/x/J{i}" for i in range(10)}


EF = "https://jobs.acme.test/api/pcsx/position_details?position_id=1&domain=acme.test&hl=en"
LD_PAGE = "https://www.weka.io/careers"


def ld_html(*blocks: Any) -> str:
    return (
        "<html>" + "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks) + "</html>"
    )


def test_an_eightfold_job_reads_its_position_details_json(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, FakeHttpResponse(data={"data": {"jobDescription": "<p>Do it</p>", "x": 1}}))
    assert adapters.browser_description("slug", {"detail_url": EF, "url": "u"}) == ("Do it", {})
    assert urls == [EF]


@pytest.mark.parametrize(
    "answer",
    [None, FakeHttpResponse(status_code=403), FakeHttpResponse(json_error=True), FakeHttpResponse(data={"data": []})],
)
def test_an_eightfold_job_page_that_gives_nothing_is_no_text(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_get(monkeypatch, answer)
    assert adapters.browser_description("slug", {"detail_url": EF, "url": "u"}) is None


def test_a_job_page_gives_only_its_json_ld_job_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ld_html(
        {"@type": "Organization", "description": "not a job"},
        {
            "@graph": [
                {"@type": "JobPosting", "description": "&lt;p&gt;Real&lt;/p&gt;", "employmentType": ["FULL_TIME"]}
            ]
        },
    ).replace("<html>", "<html><script type='application/ld+json'>{broken</script><p>Page text that is never read</p>")
    urls = fake_get(monkeypatch, FakeHttpResponse(text=page))
    assert adapters.browser_description(LD_PAGE, {"url": "https://www.weka.io/careers/1"}) == (
        "Real",
        {"employment_type": ["FULL_TIME"]},
    )
    assert urls == ["https://www.weka.io/careers/1"]


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=403),  # bot protection is never worked around
        FakeHttpResponse(text="<p>no markup of a posting</p>"),
        FakeHttpResponse(text=ld_html({"@type": "Organization"}, ["x"], "y")),
    ],
)
def test_a_job_page_without_a_job_posting_is_no_text(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_get(monkeypatch, answer)
    assert adapters.browser_description(LD_PAGE, {"url": "u"}) is None


def test_a_json_ld_that_is_not_a_job_posting_gives_no_text() -> None:
    assert descriptions.jsonld_description("x") == ("", {})


def test_a_browser_company_is_asked_only_for_jobs_with_a_page_worth_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = fake_get(monkeypatch, FakeHttpResponse(data={"data": {"jobDescription": "Text"}}))
    jobs = [{"job_id": "a", "url": "u1"}, {"job_id": "b", "url": "u2", "detail_url": EF}]
    assert adapters.add_descriptions("browser", "https://x.test/careers", "X", jobs, set()) == (1, 1, 0)
    assert urls == [EF]  # a page not in the list of known ones gives no request
    other = [{"job_id": "c", "url": "https://www.weka.io/careers/c"}]
    fake_get(monkeypatch, FakeHttpResponse(text=ld_html({"@type": "JobPosting", "description": "Text"})))
    assert adapters.add_descriptions("browser", LD_PAGE, "Weka", other, set()) == (1, 1, 0)


def test_the_pages_known_to_carry_a_job_posting_are_career_pages_of_the_list() -> None:
    companies = json.loads(FROZEN_COMPANIES.read_text(encoding="utf-8"))
    browser = {c["slug"] for c in companies if c["ats"] == "browser"}
    assert browser >= adapters.JSONLD_PAGES


def test_a_job_posting_whose_type_is_a_list_is_found_and_a_page_is_read_to_a_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ld_html({"@type": ["JobPosting", "Thing"], "description": "Listed type"})
    assert adapters._job_posting(page) is not None
    late = "x" * adapters.JSONLD_MAX_BYTES + page
    assert adapters._job_posting(late) is None  # the cap bounds the work on a hostile page


def test_a_page_of_another_host_is_not_asked_for_its_job_posting() -> None:
    assert adapters._browser_wants_page(LD_PAGE, {"url": "https://www.weka.io/careers/1"})
    assert not adapters._browser_wants_page(LD_PAGE, {"url": "https://other.test/careers/1"})
    assert not adapters._browser_wants_page(LD_PAGE, {})


@pytest.mark.parametrize(
    "url", ["https://www.weka.io.evil.test/x", "https://careers.weka.io/x", "https://other.test/x"]
)
def test_no_request_goes_to_another_host_than_the_career_page(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    urls = fake_get(monkeypatch, FakeHttpResponse(text=ld_html({"@type": "JobPosting", "description": "Text"})))
    assert adapters.add_descriptions("browser", LD_PAGE, "Weka", [{"job_id": "1", "url": url}], set()) == (0, 0, 0)
    assert urls == []


def zesty_card(uid: str, place: str, title: str, dept: str, visible: str) -> str:
    """A theme whose card has the title outside the link: the link says "Tell me more" and sits in the list item."""
    href = f"//zesty.test/careers/co/{place}/{uid}/{title.lower().replace(' ', '-')}/all"
    return (
        f'<li class="all-loc {place}"><div class="comeet-position-dept-name">{dept}</div>'
        f'<div class="comeet-position-name">{title}</div>'
        f'<div class="comeet-position-meta"><div class="location_name">{visible}</div></div>'
        f'<div class="comeet_view_more"><a class="comeet-position" href="{href}">Tell me more</a></div></li>'
    )


ZESTY = (
    '<div class="comeet-outer-wrapper"><ul class="comeet-list">'
    + zesty_card("4C.378", "tel-aviv", "Business Development Representative", "Marketing", "Tel &#8211; Aviv")
    + zesty_card("A6.47F", "santa-clara", "Account Executive", "Sales", "Santa Clara")
    + "</ul></div>"
)


def test_comeet_page_reads_the_list_item_when_the_title_is_outside_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=ZESTY, url="https://zesty.test/careers/"))
    jobs = adapters.comeet_page("https://zesty.test/careers/")
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["team"], j["location"]) for j in jobs] == [
        ("4C.378", "Business Development Representative", "Marketing", "tel aviv"),
        ("A6.47F", "Account Executive", "Sales", "santa clara"),
    ]
    assert jobs[0]["url"] == "https://zesty.test/careers/co/tel-aviv/4C.378/business-development-representative/all"
    # the place of the link is the one `common.ISRAEL` knows; the visible "Tel - Aviv" is not, and a city outside is not
    assert [bool(discover.ISRAEL.search(j["location"])) for j in jobs] == [True, False]
    assert not discover.ISRAEL.search("Tel \u2013 Aviv")


def test_comeet_page_a_list_printed_twice_gives_each_job_once(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ZESTY.replace("</ul></div>", "</ul><ul>" + ZESTY.split('<ul class="comeet-list">')[1])
    fake_get(monkeypatch, FakeHttpResponse(text=page, url="https://zesty.test/careers/"))
    jobs = adapters.comeet_page("https://zesty.test/careers/")
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["4C.378", "A6.47F"]


@pytest.mark.parametrize(
    "page",
    [
        # the item has no name of its own to read
        ZESTY.replace('class="comeet-position-name"', 'class="other"'),
        # the link is in no list item
        '<div class="comeet-outer-wrapper"><a href="/co/tel-aviv/4C.378/bdr/all">Tell me more</a></div>',
        # one list item holds two jobs: whose title is it
        '<ul><li><div class="comeet-position-name">One</div><a href="/co/tel-aviv/4C.378/a/all">More</a>'
        '<a href="/co/haifa/A6.47F/b/all">More</a></li></ul>',
    ],
)
def test_comeet_page_a_card_with_no_title_anywhere_is_not_a_list(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    fake_get(monkeypatch, FakeHttpResponse(text=page, url="https://zesty.test/careers/"))
    assert adapters.comeet_page("https://zesty.test/careers/") is None


def test_comeet_page_a_job_read_two_ways_is_kept_once_with_the_first_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    again = '<a href="/careers/co/haifa/4C.378/bdr/all">Tell me more</a>'
    page = ZESTY.replace("</ul>", f'<li><div class="comeet-position-name">BDR</div>{again}</li></ul>')
    fake_get(monkeypatch, FakeHttpResponse(text=page, url="https://zesty.test/careers/"))
    jobs = adapters.comeet_page("https://zesty.test/careers/")
    assert jobs is not None
    assert (jobs[0]["job_id"], jobs[0]["title"], jobs[0]["location"]) == (
        "4C.378",
        "Business Development Representative",
        "tel aviv",
    )
    assert len(jobs) == len({j["job_id"] for j in jobs})


FIXTURES = Path(__file__).parent / "fixtures"
SYNOPSYS_SLUG = "synopsys.avature.net/careers?2001=21374"
SIEMENS_SLUG = "jobs.siemens.com/en_US/externaljobs?42386=811993"


def fake_avature(monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"url": url, **kw})
        return answer

    monkeypatch.setattr(adapters, "get", _get)
    return calls


def feed(*items: str, root: str = "rss") -> FakeHttpResponse:
    return FakeHttpResponse(text=f"<{root}><channel><title>t</title>{''.join(items)}</channel></{root}>")


def feed_item(
    job_id: Any = "7",
    title: str = "Engineer",
    place: str = "",
    host: str = "careers.acme.test",
    link: str | None = None,
) -> str:
    url = link if link is not None else f"https://{host}/p/JobDetail/Engineer-{job_id}/{job_id}"
    return (
        f"<item><title>{title}</title><description>{place}</description><link>{url}</link>"
        "<pubDate>Mon, 14 Sep 2026 00:00:00 +0000</pubDate></item>"
    )


def test_avature_reads_the_synopsys_feed_and_sets_israel_from_the_facet(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_avature(monkeypatch, FakeHttpResponse(text=(FIXTURES / "avature_synopsys.xml").read_text("utf-8")))
    jobs = adapters.avature(SYNOPSYS_SLUG)
    assert calls == [{"url": "https://synopsys.avature.net/careers/SearchJobs/feed/", "params": {"2001": "21374"}}]
    base = "https://synopsys.avature.net/careers/JobDetail"
    assert jobs == [
        {
            "job_id": "18752",
            "title": "Applications Engineering, Principal Engineer",  # the ` - 18752` the feed adds is dropped
            "location": "Israel",  # the feed has no place, its description is only a requisition number
            "url": f"{base}/Applications-Engineering-Principal-Engineer-18752/18752",
            "team": "",
            "posted": "2026-09-14",
        },
        {
            "job_id": "18822",
            "title": "Applications Engineering, Architect",
            "location": "Israel",
            "url": f"{base}/Applications-Engineering-Architect-18822/18822",
            "team": "",
            "posted": "2026-09-15",
        },
    ]


def test_avature_keeps_every_job_the_israel_facet_returns_including_multi_country_postings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Siemens posting for several countries lists Israel among them (checked on a job page) and appears in the
    filtered feed with another country as its description: it is kept, with Israel as its place."""
    calls = fake_avature(monkeypatch, FakeHttpResponse(text=(FIXTURES / "avature_siemens.xml").read_text("utf-8")))
    jobs = adapters.avature(SIEMENS_SLUG)
    assert calls[0]["url"] == "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/feed/"
    assert calls[0]["params"] == {"42386": "811993"}
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"]) for j in jobs] == [
        ("498470", "PLM Expert (Teamcenter) (m/f/d)", "Israel"),
        ("498931", "Sales Manager (all genders)", "Israel"),  # the description says Germany
        ("508055", "Senior Engineer", "Israel"),  # the description says the United Kingdom
        ("510794", "Quality Professional", "Israel"),  # no description
        ("521540", "C++ Software Engineer with Qt Expertise", "Israel Tel Aviv"),  # the description names the place
    ]
    assert all(discover.ISRAEL.search(j["location"]) for j in jobs)  # an israel_only company keeps every one


def test_avature_a_title_keeps_a_dash_number_that_is_not_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_avature(monkeypatch, feed(feed_item("7", title="Team 99 - 12")))
    jobs = adapters.avature("careers.acme.test/p?1=2")
    assert jobs is not None
    assert jobs[0]["title"] == "Team 99 - 12"


def test_avature_a_date_that_is_not_a_date_gives_no_date(monkeypatch: pytest.MonkeyPatch) -> None:
    item = feed_item().replace("Mon, 14 Sep 2026 00:00:00 +0000", "soon")
    no_date = feed_item("8").replace("<pubDate>", "<x>").replace("</pubDate>", "</x>")
    fake_avature(monkeypatch, feed(item, no_date))
    jobs = adapters.avature("careers.acme.test/p?1=2")
    assert jobs is not None
    assert [j["posted"] for j in jobs] == ["", ""]


def test_avature_no_job_is_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_avature(monkeypatch, feed())
    assert adapters.avature("careers.acme.test/p?1=2") == []


def test_avature_the_largest_list_that_can_be_trusted_is_one_below_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_avature(monkeypatch, feed(*(feed_item(i) for i in range(adapters.AVATURE_MAX - 1))))
    jobs = adapters.avature("careers.acme.test/p?1=2")
    assert jobs is not None
    assert len(jobs) == adapters.AVATURE_MAX - 1


def test_avature_a_list_as_long_as_the_cap_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feed cannot be paged: this long a list may be cut short, and is what an ignored facet gives."""
    fake_avature(monkeypatch, feed(*(feed_item(i) for i in range(adapters.AVATURE_MAX))))
    assert adapters.avature("careers.acme.test/p?1=2") is None


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "synopsys.avature.net",
        "synopsys.avature.net/careers",
        "synopsys.avature.net/careers?",
        "synopsys.avature.net/careers?2001",
        "synopsys.avature.net/careers?2001=",
        "synopsys.avature.net/careers?2001=abc",
        "synopsys.avature.net/careers?2001=1&3=4",
        "synopsys.avature.net/?2001=21374",
        "nohost/careers?2001=21374",
    ],
)
def test_avature_a_slug_that_is_not_host_portal_and_one_facet_is_a_failure_without_a_request(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    calls = fake_avature(monkeypatch, feed())
    assert adapters.avature(slug) is None
    assert calls == []


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=403),
        FakeHttpResponse(text="<html><body>Sign in</body></html>"),
        FakeHttpResponse(text="not xml at all"),
        feed(root="html"),
        feed("<item><title></title><link>https://careers.acme.test/p/JobDetail/E/1</link></item>"),
        feed("<item><title>Engineer</title></item>"),
        feed("<item><title>Engineer</title><link>https://careers.acme.test/p/JobDetail/Engineer</link></item>"),
        feed(feed_item(host="other.test")),
        feed(feed_item(link="http://careers.acme.test/p/JobDetail/Engineer-7/7")),
        feed(feed_item("1"), feed_item("2", host="other.test")),  # one unreadable job fails the list
        feed(feed_item("1"), feed_item("1")),  # a job twice
    ],
)
def test_avature_an_answer_that_is_not_a_clean_feed_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_avature(monkeypatch, answer)
    assert adapters.avature("careers.acme.test/p?1=2") is None


def test_avature_the_slugs_of_the_two_companies_are_accepted_by_the_admin_editor() -> None:
    admin = (Path(__file__).parent.parent / "templates" / "admin.js").read_text("utf-8")
    chars = re.search(r"const PIN_SLUG_CHARS = /(.*)/;", admin)
    longest = re.search(r"const PIN_SLUG_MAX = (\d+);", admin)
    assert chars
    assert longest
    for slug in (SYNOPSYS_SLUG, SIEMENS_SLUG):
        assert re.fullmatch(chars[1], slug)
        assert len(slug) <= int(longest[1])


def test_avature_is_registered() -> None:
    assert adapters.ADAPTERS["avature"] is adapters.avature


MICRON_SLUG = "careers.micron.com?domain=micron.com"
LAM_SLUG = "lamresearch.eightfold.ai?domain=lamresearch.com"
EF_URL = "https://careers.micron.com/api/pcsx/search"


def ef_position(job_id: Any = 7, **over: Any) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": "Engineer",
        "locations": ["Haifa, Israel"],
        "standardizedLocations": [],
        "postedTs": 1790000000,
        "department": "R&D",
        "positionUrl": f"/careers/job/{job_id}",
        **over,
    }


def ef_answer(positions: list[Any], count: Any = None) -> FakeHttpResponse:
    return FakeHttpResponse(
        data={"status": 200, "data": {"count": len(positions) if count is None else count, "positions": positions}}
    )


def fake_eightfold(monkeypatch: pytest.MonkeyPatch, *answers: FakeHttpResponse | None) -> list[dict[str, Any]]:
    """Each request gets the next answer; the calls are kept."""
    calls: list[dict[str, Any]] = []
    queue = list(answers)

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"url": url, **kw})
        return queue.pop(0)

    monkeypatch.setattr(adapters, "get", _get)
    return calls


def test_eightfold_reads_the_israel_jobs_and_drops_the_others_of_the_same_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = json.loads((FIXTURES / "eightfold_micron.json").read_text("utf-8"))
    calls = fake_eightfold(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.eightfold(MICRON_SLUG)
    assert calls == [{"url": EF_URL, "params": {"domain": "micron.com", "location": "Israel", "start": 0}}]
    assert jobs == [
        {
            "job_id": "100000001",
            "title": "ASIC Design Engineer",
            "location": "Israel",
            "url": "https://careers.micron.com/careers/job/100000001",
            "team": "Design",
            "posted": "2026-07-29",
        },
        {
            "job_id": "100000002",
            "title": "AI Engineering Manager",
            "location": "Hadera, Israel",
            "url": "https://careers.micron.com/careers/job/100000002",
            "team": "IT",
            "posted": "2026-09-04",
        },
        {  # several places: kept, Israel is one of them, and all are the place
            "job_id": "100000003",
            "title": "OSINT Analyst",
            "location": "Hadera, Israel; Boise, ID, United States",
            "url": "https://careers.micron.com/careers/job/100000003",
            "team": "IT",
            "posted": "2026-08-26",
        },
    ]


def test_eightfold_reads_a_board_whose_place_is_only_a_site_code(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads((FIXTURES / "eightfold_lam.json").read_text("utf-8"))
    fake_eightfold(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.eightfold(LAM_SLUG)
    assert jobs is not None
    assert [(j["job_id"], j["location"], j["url"]) for j in jobs] == [
        ("1099556387295", "IL-Israel Intel-02 (2511)", "https://lamresearch.eightfold.ai/careers/job/1099556387295")
    ]


def test_eightfold_a_standardized_location_in_israel_is_enough_and_survives_the_israel_only_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_code = ef_position(1, locations=["Site 4"], standardizedLocations=["Haifa, Haifa District, IL"])
    other = ef_position(2, locations=["Site 5"], standardizedLocations=["Austin, TX, US"])
    illinois = ef_position(3, locations=["Chicago, IL"], standardizedLocations=["Chicago, IL"])
    fake_eightfold(monkeypatch, ef_answer([only_code, other, illinois]))
    jobs = adapters.eightfold(MICRON_SLUG)
    assert jobs is not None
    assert [(j["job_id"], j["location"]) for j in jobs] == [("1", "Site 4; Haifa, Haifa District, IL")]
    # the check run.py makes of an israel_only company's jobs (location only)
    assert [j["job_id"] for j in jobs if common.ISRAEL.search(j["location"] or "")] == ["1"]


def test_eightfold_pages_on_from_the_number_of_positions_read(monkeypatch: pytest.MonkeyPatch) -> None:
    first = [ef_position(i) for i in range(10)]
    second = [ef_position(i) for i in range(10, 13)]
    calls = fake_eightfold(monkeypatch, ef_answer(first, 13), ef_answer(second, 13))
    jobs = adapters.eightfold(MICRON_SLUG)
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == [str(i) for i in range(13)]
    assert [c["params"]["start"] for c in calls] == [0, 10]


def test_eightfold_no_position_is_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_eightfold(monkeypatch, ef_answer([]))
    assert adapters.eightfold(MICRON_SLUG) == []
    assert len(calls) == 1


def test_eightfold_a_list_at_the_cap_is_read_and_one_above_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [ef_answer([ef_position(i) for i in range(s, s + 10)], adapters.EIGHTFOLD_MAX) for s in range(0, 100, 10)]
    fake_eightfold(monkeypatch, *pages)
    jobs = adapters.eightfold(MICRON_SLUG)
    assert jobs is not None
    assert len(jobs) == adapters.EIGHTFOLD_MAX
    calls = fake_eightfold(monkeypatch, ef_answer([ef_position(1)], adapters.EIGHTFOLD_MAX + 1))
    assert adapters.eightfold(MICRON_SLUG) is None  # a board that ignores the location: no second request
    assert len(calls) == 1


@pytest.mark.parametrize(
    "answers",
    [
        [None],  # no answer
        [FakeHttpResponse(status_code=403, data={"message": "PCSX is not enabled for this user."})],  # NetApp
        [FakeHttpResponse(status_code=404, data={})],
        [FakeHttpResponse(json_error=True)],  # not JSON
        [FakeHttpResponse(data=[])],  # JSON that is not an object
        [FakeHttpResponse(data={"data": None})],
        [FakeHttpResponse(data={"data": {"positions": []}})],  # no count
        [FakeHttpResponse(data={"data": {"count": "1", "positions": []}})],
        [FakeHttpResponse(data={"data": {"count": 1, "positions": {}}})],
        [ef_answer([ef_position(1)], 2), None],  # the second answer fails
        [ef_answer([ef_position(1)], 2), ef_answer([], 2)],  # the second has no position and the total is not reached
        [ef_answer([ef_position(1)], 2), ef_answer([ef_position(2)], 3)],  # the total moved
        [ef_answer([ef_position(1)], 2), ef_answer([ef_position(1)], 2)],  # a position twice
        [ef_answer([ef_position(1), ef_position(2)], 1)],  # more positions than the total
    ],
)
def test_eightfold_a_list_that_cannot_be_trusted_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answers: list[FakeHttpResponse | None]
) -> None:
    fake_eightfold(monkeypatch, *answers)
    assert adapters.eightfold(MICRON_SLUG) is None


@pytest.mark.parametrize(
    "bad",
    [
        "not a position",
        ef_position(1, name=""),
        ef_position(1, id="1"),  # the id is a number
        ef_position(1, positionUrl="https://evil.test/x"),  # a path of the board only
        ef_position(1, positionUrl=None),
    ],
)
def test_eightfold_a_position_that_cannot_be_read_fails_the_whole_list(
    monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    fake_eightfold(monkeypatch, ef_answer([ef_position(2), bad]))
    assert adapters.eightfold(MICRON_SLUG) is None


def test_eightfold_odd_fields_are_tolerated_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    odd = ef_position(3, locations="Israel", standardizedLocations=None, postedTs="soon", department=None)
    israel = ef_position(4, postedTs=10**30, department="")
    fake_eightfold(monkeypatch, ef_answer([odd, israel]))
    jobs = adapters.eightfold(MICRON_SLUG)  # `odd` has no place that can be read, so it is not Israel
    assert jobs is not None
    assert [(j["job_id"], j["posted"], j["team"]) for j in jobs] == [("4", "", "")]


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "careers.micron.com",
        "careers.micron.com?",
        "careers.micron.com?domain=",
        "careers.micron.com?domain=micron",  # a domain has a dot
        "careers.micron.com?domain=micron.com&location=Israel",  # one field only
        "careers.micron.com?location=Israel",
        "micron?domain=micron.com",  # a host has a dot
        "careers.micron.com/careers?domain=micron.com",  # the path is fixed, not part of the slug
        "https://careers.micron.com?domain=micron.com",
        "Careers.Micron.com?domain=micron.com",
        "careers.micron.com?domain=micron.com ",
    ],
)
def test_eightfold_a_slug_that_is_not_host_and_domain_is_a_failure_without_a_request(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    calls = fake_eightfold(monkeypatch, ef_answer([]))
    assert adapters.eightfold(slug) is None
    assert calls == []


def test_eightfold_goes_through_the_robots_gate_and_the_polite_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request is common.get's: a robots.txt refusal makes it None, so the list is a failure, never partial."""
    monkeypatch.setattr(adapters, "get", common.get)
    monkeypatch.setattr(common, "robots_gate", lambda url: False)
    assert adapters.eightfold(MICRON_SLUG) is None


def test_eightfold_the_slugs_are_accepted_by_the_admin_editor() -> None:
    admin = (Path(__file__).parent.parent / "templates" / "admin.js").read_text("utf-8")
    chars = re.search(r"const PIN_SLUG_CHARS = /(.*)/;", admin)
    longest = re.search(r"const PIN_SLUG_MAX = (\d+);", admin)
    assert chars
    assert longest
    for slug in (MICRON_SLUG, LAM_SLUG, "bostonscientific.eightfold.ai?domain=bostonscientific.com"):
        assert re.fullmatch(chars[1], slug)
        assert len(slug) <= int(longest[1])


def test_eightfold_is_registered() -> None:
    assert adapters.ADAPTERS["eightfold"] is adapters.eightfold


SE_SLUG = "careers.se.com"
ICE_SLUG = "careers.ice.com"


def jibe_job(number: Any = 7, **over: Any) -> dict[str, Any]:
    return {
        "data": {
            "slug": str(number),
            "title": "Engineer",
            "full_location": "Haifa, Israel",
            "posted_date": "2026-11-15T12:45:00+0000",
            "category": [" R&D"],
            **over,
        }
    }


def jibe_answer(items: list[Any], total: Any = None) -> FakeHttpResponse:
    return FakeHttpResponse(data={"totalCount": len(items) if total is None else total, "jobs": items})


def test_jibe_reads_the_israel_jobs_with_the_place_of_every_location(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads((FIXTURES / "jibe_ice.json").read_text("utf-8"))
    calls = fake_eightfold(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.jibe(ICE_SLUG)
    assert calls == [
        {
            "url": "https://careers.ice.com/api/jobs",
            "params": {"country": "Israel", "limit": adapters.JIBE_MAX, "page": 1},
        }
    ]
    assert jobs == [  # a job of several places is kept (Israel is one); the job of London only is dropped
        {
            "job_id": "14033",
            "title": "Senior Client Support Specialist",
            "location": "London, United Kingdom; Tel-Aviv, Israel",
            "url": "https://careers.ice.com/jobs/14033",
            "team": "",
            "posted": "2026-10-30",
        },
        {
            "job_id": "13991",
            "title": "Analyst, Trading Operations",
            "location": "Tel-Aviv, Israel",
            "url": "https://careers.ice.com/jobs/13991",
            "team": "",
            "posted": "2026-10-12",
        },
    ]


def test_jibe_reads_long_dates_and_the_category_as_the_team(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads((FIXTURES / "jibe_se.json").read_text("utf-8"))
    fake_eightfold(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.jibe(SE_SLUG)
    assert jobs is not None
    assert [(j["job_id"], j["team"], j["posted"], j["url"]) for j in jobs] == [
        ("104699", "Sales", "2026-03-24", "https://careers.se.com/jobs/104699"),
        ("104700", "Finance", "2026-06-01", "https://careers.se.com/jobs/104700"),
    ]


@pytest.mark.parametrize("stamp", [None, 5, "soon", "2026-13-45T00:00:00+0000"])
def test_jibe_a_date_it_cannot_read_is_no_date(monkeypatch: pytest.MonkeyPatch, stamp: Any) -> None:
    fake_eightfold(monkeypatch, jibe_answer([jibe_job(posted_date=stamp)]))
    jobs = adapters.jibe(SE_SLUG)
    assert jobs is not None
    assert jobs[0]["posted"] == ""


@pytest.mark.parametrize("category", [None, [], [5], "R&D"])
def test_jibe_a_category_it_cannot_read_is_no_team(monkeypatch: pytest.MonkeyPatch, category: Any) -> None:
    fake_eightfold(monkeypatch, jibe_answer([jibe_job(category=category)]))
    jobs = adapters.jibe(SE_SLUG)
    assert jobs is not None
    assert jobs[0]["team"] == ""


def test_jibe_no_job_is_an_empty_board(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_eightfold(monkeypatch, jibe_answer([]))
    assert adapters.jibe(SE_SLUG) == []
    assert len(calls) == 1


def test_jibe_a_list_at_the_cap_is_read_and_one_above_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_eightfold(monkeypatch, jibe_answer([jibe_job(i) for i in range(adapters.JIBE_MAX)]))
    jobs = adapters.jibe(SE_SLUG)
    assert jobs is not None
    assert len(jobs) == adapters.JIBE_MAX
    fake_eightfold(monkeypatch, jibe_answer([jibe_job(1)], adapters.JIBE_MAX + 1))
    assert adapters.jibe(SE_SLUG) is None  # a board that ignores the country, or one cut short


@pytest.mark.parametrize(
    "answer",
    [
        None,  # no answer
        FakeHttpResponse(status_code=403, data={}),
        FakeHttpResponse(status_code=404, data={}),
        FakeHttpResponse(json_error=True),  # not JSON
        FakeHttpResponse(data=[]),  # JSON that is not an object
        FakeHttpResponse(data={"jobs": []}),  # no total
        FakeHttpResponse(data={"totalCount": 0}),  # no jobs
        FakeHttpResponse(data={"totalCount": "1", "jobs": []}),
        FakeHttpResponse(data={"totalCount": 0, "jobs": {}}),
        jibe_answer([jibe_job(1)], 2),  # fewer jobs than the total
        jibe_answer([jibe_job(1), jibe_job(2)], 1),  # more jobs than the total
        jibe_answer([jibe_job(1), jibe_job(1)]),  # a job twice
        jibe_answer(["x"]),  # not an object
        jibe_answer([{"data": "x"}]),
        jibe_answer([jibe_job(1, slug=None)]),
        jibe_answer([jibe_job(1, slug="abc")]),
        jibe_answer([jibe_job(1, title="  ")]),
        jibe_answer([jibe_job(1, title=None)]),
        jibe_answer([jibe_job(1, full_location="")]),
        jibe_answer([jibe_job(1, full_location=None)]),
    ],
)
def test_jibe_an_answer_that_is_not_the_whole_readable_list_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_eightfold(monkeypatch, answer)
    assert adapters.jibe(SE_SLUG) is None


@pytest.mark.parametrize("slug", ["", "careers", "Careers.SE.com", "careers.se.com/x", "careers.se.com?a=b", "a b.com"])
def test_jibe_a_slug_that_is_not_a_host_is_not_read(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    calls = fake_eightfold(monkeypatch)
    assert adapters.jibe(slug) is None
    assert calls == []


def test_jibe_reads_nothing_of_the_job_text_or_the_apply_link(monkeypatch: pytest.MonkeyPatch) -> None:
    item = jibe_job(1, description="TEXT", apply_url="https://x.icims.com/jobs/1/login")
    fake_eightfold(monkeypatch, jibe_answer([item]))
    jobs = adapters.jibe(SE_SLUG)
    assert jobs is not None
    assert "TEXT" not in json.dumps(jobs)
    assert "icims" not in jobs[0]["url"]


def test_jibe_is_registered() -> None:
    assert adapters.ADAPTERS["jibe"] is adapters.jibe


VARONIS_URL = "https://careers.varonis.com/api/getRequisitions"


def varonis_item(job_id: Any = "oAbc1234", **over: Any) -> dict[str, Any]:
    return {
        "eId": job_id,
        "title": "Engineer",
        "department": "R&D",
        "locationsCount": 1,
        "jobLocations": [{"city": "Herzliya", "country": "Israel"}],
        **over,
    }


def varonis_answer(items: Any) -> FakeHttpResponse:
    return FakeHttpResponse(data={"data": items})


def test_varonis_keeps_the_jobs_with_a_place_in_israel_and_drops_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    data = json.loads((FIXTURES / "varonis_requisitions.json").read_text("utf-8"))
    calls = fake_eightfold(monkeypatch, FakeHttpResponse(data=data))
    jobs = adapters.varonis("careers.varonis.com")
    assert calls == [{"url": VARONIS_URL}]
    assert jobs == [
        {
            "job_id": "oIL00001",
            "title": "Senior Data Scientist",
            "location": "Herzliya, Israel",
            "url": "https://careers.varonis.com/careers?p=job/oIL00001",
            "team": "R&D",
            "posted": "",
        },
        {
            "job_id": "oIL00002",
            "title": "Technical Support Engineer",
            "location": "New York, United States; Herzliya, Israel",
            "url": "https://careers.varonis.com/careers?p=job/oIL00002",
            "team": "Support",
            "posted": "",
        },
    ]


def test_varonis_a_place_without_a_city_and_a_missing_team_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    item = varonis_item(department=None, jobLocations=[{"city": None, "country": "Israel"}, "x", {}])
    item["locationsCount"] = 3
    fake_eightfold(monkeypatch, varonis_answer([item]))
    assert adapters.varonis("careers.varonis.com") == [
        {
            "job_id": "oAbc1234",
            "title": "Engineer",
            "location": "Israel",
            "url": "https://careers.varonis.com/careers?p=job/oAbc1234",
            "team": "",
            "posted": "",
        }
    ]


def test_varonis_no_job_is_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_eightfold(monkeypatch, varonis_answer([]))
    assert adapters.varonis("careers.varonis.com") == []


def test_varonis_a_list_at_the_cap_is_read_and_one_above_it_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [f"o{i:06d}" for i in range(adapters.VARONIS_MAX + 1)]
    fake_eightfold(monkeypatch, varonis_answer([varonis_item(i) for i in ids[:-1]]))
    jobs = adapters.varonis("careers.varonis.com")
    assert jobs is not None
    assert len(jobs) == adapters.VARONIS_MAX
    fake_eightfold(monkeypatch, varonis_answer([varonis_item(i) for i in ids]))
    assert adapters.varonis("careers.varonis.com") is None  # a list that long may be cut short


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=403),
        FakeHttpResponse(json_error=True),
        FakeHttpResponse(data={}),
        FakeHttpResponse(data=[]),
        FakeHttpResponse(data={"data": "x"}),
        varonis_answer([varonis_item("oA1"), varonis_item("oA1")]),  # a job twice
        varonis_answer(["x"]),  # not an object
        varonis_answer([varonis_item(None)]),
        varonis_answer([varonis_item("a b")]),
        varonis_answer([varonis_item(title="  ")]),
        varonis_answer([varonis_item(title=None)]),
        varonis_answer([varonis_item(jobLocations=None)]),
        varonis_answer([varonis_item(locationsCount=2)]),  # fewer places than the count says
        varonis_answer([varonis_item(locationsCount=0)]),
    ],
)
def test_varonis_an_answer_that_is_not_the_whole_readable_list_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    fake_eightfold(monkeypatch, answer)
    assert adapters.varonis("careers.varonis.com") is None


@pytest.mark.parametrize("slug", ["", "varonis", "careers.varonis.com/x", "Careers.Varonis.com", "careers.se.com"])
def test_varonis_serves_no_other_host(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    calls = fake_eightfold(monkeypatch)
    assert adapters.varonis(slug) is None
    assert calls == []


def test_varonis_reads_nothing_of_the_apply_link(monkeypatch: pytest.MonkeyPatch) -> None:
    item = varonis_item(jobLocations=[{"city": "Herzliya", "country": "Israel", "applyUrl": "https://x.test/apply"}])
    fake_eightfold(monkeypatch, varonis_answer([item]))
    jobs = adapters.varonis("careers.varonis.com")
    assert jobs is not None
    assert "x.test" not in json.dumps(jobs)


def test_varonis_goes_through_the_robots_gate_and_the_polite_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "get", common.get)
    monkeypatch.setattr(common, "robots_gate", lambda url: False)
    assert adapters.varonis("careers.varonis.com") is None


def test_varonis_is_registered() -> None:
    assert adapters.ADAPTERS["varonis"] is adapters.varonis
