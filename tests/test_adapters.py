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
    assert calls[0]["params"]["finder"] == "findReqs;siteNumber=CX_1,limit=50,offset=0,keyword=Israel"
    assert jobs is not None
    assert [(j["job_id"], j["title"], j["location"], j["posted"]) for j in jobs] == [
        ("1", "Backend Engineer", "Herzliya, Tel Aviv, Israel", "2026-11-01"),
        ("3", "Field Engineer", "Haifa, Israel", "2026-11-01"),
        ("4", "Analyst", "Israel", "2026-11-01"),
        ("5", "Manager", "Israel", "2026-11-01"),
    ]
    assert jobs[0]["url"] == "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1"
    assert all(discover.ISRAEL.search(j["location"]) for j in jobs)


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
