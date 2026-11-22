"""The Oracle Taleo reader (`adapters.taleo`, Radware). The answers are built the way the live ones are shaped (read
2026-11-22): a list page whose form holds the first page of the list, and the page's own call for the others. No
network."""

import html
from typing import Any

import pytest

import adapters
import common
import regions
from tests.fakes import FakeHttpResponse

SLUG = "radware.taleo.net/careersection/ex"
BASE = "https://radware.taleo.net/careersection/ex"
LIST_HEAD = "requisitionListInterface!|!listRequisition!|!rlPager"
JOB_FIELDS = 31


def taleo_row(
    number: str = "260000B2",
    title: str = "Customer Marketing Manager",
    place: str = "IL-IL-Tel Aviv ",
    posted: str = "Nov 18, 2026",
    ident: str = "55375",
) -> list[str]:
    """The 31 fields of one job, then the 11 the list adds after it (42 in all, the last three flags too)."""
    ids = [ident] * 5
    job = [ident, title, ident, title, *ids, number, place, "false", "", "", "", "", "Full-time", posted, "Apply"]
    job += [f"Apply for this position ({title})", ident, "true", "Re-apply", "Re-apply for this job", ident]
    job += [
        "false",
        "false",
        ident,
        "false",
        "false",
        f"Submission for the position%5C: {title} - (Job Number%5C: {number})",
    ]
    assert len(job) == JOB_FIELDS
    tail = ["false", "true", "Add to My Job Cart", f"Add this position to the job cart%5C: {title}", ident]
    return [*job, *tail, "false", "true", "false", "false", "false", "false"]


def taleo_fields(rows: list[list[str]]) -> str:
    """The fields of a page: three flags, then the jobs; the last three fields of the last job are not there."""
    flat = ["false"] * 3 + [f for row in rows for f in row]
    return "!|!".join(flat[:-3])


def taleo_tail(total: int, **over: Any) -> str:
    values = {
        "listRequisition.size": "25",
        "listRequisition.nbElements": str(total),
        "listRequisition.isEmpty": "false",
        "dropListSize": "25",
        "dropSortBy": "10",
        **over,
    }
    return "ftlerrors!|!!|!" + "!|!".join(f"{k}!|!{v}" for k, v in values.items() if v is not None)


def taleo_history(rows: list[list[str]], total: int, **over: Any) -> str:
    """The value of the form field `initialHistory`: the first page (`!%24!` between its parts)."""
    return "!%24!".join(
        ["ftlx0!|!ftlUtil_resetPage", LIST_HEAD, taleo_fields(rows), taleo_tail(total, **over), "allJobsInterface"]
    )


def taleo_page(rows: list[list[str]], total: int, size: int = 25, history: str | None = None, **over: Any) -> str:
    fields = {
        "ftlpageid": "reqListAllJobsPage",
        "csrftoken": "/Hmlum2xc=",
        "rlPager.currentPage": "1",
        "listRequisition.nbElements": str(total),
        "listRequisition.size": str(size),
        "initialHistory": taleo_history(rows, total) if history is None else history,
        **over,
    }
    inputs = "".join(
        f'<input type="hidden" name="{k}" id="{k}" value="{html.escape(v)}" />'
        for k, v in fields.items()
        if v is not None
    )
    return f'<html><form name="ftlform" method="post" action="joblist.ftl">{inputs}<input type="text" /></form></html>'


def taleo_answer(rows: list[list[str]], page: int, total: int, **over: Any) -> FakeHttpResponse:
    head = f"ftlx{page}!|!ftlPager_processResponse"
    mid = f"requisitionListInterface!|!rlPager!|!listRequisition!|!{page}!|!54295!|!5!|!Go to page {{0}}"
    return FakeHttpResponse(text="!$!".join([head, mid, taleo_fields(rows), taleo_tail(total, **over)]))


def fake_taleo(monkeypatch: pytest.MonkeyPatch, page: Any, *answers: FakeHttpResponse | None) -> list[dict[str, Any]]:
    """The list page answers the one GET; each POST gets the next answer. The calls are kept."""
    calls: list[dict[str, Any]] = []
    queue = list(answers)

    def _get(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"method": "get", "url": url, **kw})
        return page if isinstance(page, FakeHttpResponse) or page is None else FakeHttpResponse(text=page)

    def _post(url: str, **kw: Any) -> FakeHttpResponse | None:
        calls.append({"method": "post", "url": url, **kw})
        return queue.pop(0)

    monkeypatch.setattr(adapters, "get", _get)
    monkeypatch.setattr(adapters, "post", _post)
    return calls


def two_pages(monkeypatch: pytest.MonkeyPatch, **over: Any) -> list[dict[str, Any]]:
    """A list of three jobs, two to a page."""
    first = [taleo_row("A1"), taleo_row("A2", "Tom %26 Jerry", place="IL-IL-Haifa", posted="x")]
    answer = taleo_answer([taleo_row("A3", ident="3")], 2, 3, **over)
    return fake_taleo(monkeypatch, taleo_page(first, 3, 2), answer)


def test_taleo_reads_each_page_with_the_pages_own_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = two_pages(monkeypatch)
    jobs = adapters.taleo(SLUG)
    assert jobs == [
        {
            "job_id": "A1",
            "title": "Customer Marketing Manager",
            "location": "IL-IL-Tel Aviv",
            "url": f"{BASE}/jobdetail.ftl?job=A1",
            "team": "",
            "posted": "2026-11-18",
        },
        {
            "job_id": "A2",
            "title": "Tom & Jerry",  # `%26` is undone
            "location": "IL-IL-Haifa",
            "url": f"{BASE}/jobdetail.ftl?job=A2",
            "team": "",
            "posted": "",  # not a date
        },
        {
            "job_id": "A3",
            "title": "Customer Marketing Manager",
            "location": "IL-IL-Tel Aviv",
            "url": f"{BASE}/jobdetail.ftl?job=A3",
            "team": "",
            "posted": "2026-11-18",
        },
    ]
    assert [c["method"] for c in calls] == ["get", "post"]
    assert calls[0]["url"] == f"{BASE}/joblist.ftl"
    post = calls[1]
    assert post["url"] == f"{BASE}/joblist.ajax"
    assert post["params"] == {
        "ftlpageid": "reqListAllJobsPage",
        "ftlinterfaceid": "requisitionListInterface",
        "ftlcompid": "rlPager",
        "jsfCmdId": "rlPager",
        "ftlcompclass": "PagerComponent",
        "ftlcallback": "ftlPager_processResponse",
        "ftlajaxid": "ftlx2",
    }
    # the page's own form, the page number, and the two selects its script fills in (the order is the first page's)
    assert post["data"]["csrftoken"] == "/Hmlum2xc="
    assert post["data"]["rlPager.currentPage"] == "2"
    assert (post["data"]["dropListSize"], post["data"]["dropSortBy"]) == ("25", "10")
    assert "type" not in post["data"]  # an input with no name is not sent


def test_taleo_a_list_of_one_page_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_taleo(monkeypatch, taleo_page([taleo_row()], 1))
    jobs = adapters.taleo(SLUG)
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["260000B2"]
    assert [c["method"] for c in calls] == ["get"]


def test_taleo_places_of_the_list_are_israeli_where_they_say_so() -> None:
    assert common.ISRAEL.search("IL-IL-Tel Aviv")
    assert not common.ISRAEL.search("US-NJ-Mahwah")
    assert not common.ISRAEL.search("IN-IN-Mumbai")


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "radware",
        "radware.taleo.net",
        "radware.taleo.net/careersection",
        "radware.taleo.net/jobs/ex",
        "x/careersection/ex",
    ],
)
def test_taleo_a_slug_that_is_not_a_career_section_is_refused_without_a_request(
    monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    calls = fake_taleo(monkeypatch, taleo_page([taleo_row()], 1))
    assert adapters.taleo(slug) is None
    assert calls == []


ROW = taleo_row()


def bad(**over: Any) -> list[str]:
    row = taleo_row()
    for index, value in over.items():
        row[int(index.removeprefix("f"))] = value
    return row


@pytest.mark.parametrize(
    "page",
    [
        None,
        FakeHttpResponse(status_code=500),
        # the form
        taleo_page([ROW], 1, ftlpageid=None),
        taleo_page([ROW], 1, ftlpageid=""),
        taleo_page([ROW], 1, **{"rlPager.currentPage": "2"}),
        taleo_page([ROW], 1, **{"listRequisition.nbElements": "many"}),
        taleo_page([ROW], 1, **{"listRequisition.nbElements": None}),
        taleo_page([ROW], 1, **{"listRequisition.size": ""}),
        taleo_page([ROW], 0),  # an empty list is a failure
        taleo_page([ROW], adapters.TALEO_MAX + 1),  # a list that long may be cut short
        taleo_page([ROW], 5, size=0),
        taleo_page([ROW], adapters.TALEO_MAX_PAGES + 1, size=1),
        # the first page
        taleo_page([ROW], 1, history=""),
        taleo_page([ROW], 1, history=taleo_history([ROW], 1).replace("requisitionListInterface", "otherInterface", 1)),
        taleo_page([ROW], 1, history=taleo_history([ROW], 1).replace("ftlerrors", "errors")),
        taleo_page([ROW], 1, history=taleo_history([ROW], 1).replace("ftlerrors!|!!|!", "ftlerrors!|!oops!|!")),
        taleo_page([ROW], 1, history=taleo_history([ROW], 2)),  # a list that is not the one the form counts
        taleo_page([ROW], 1, history=taleo_history([ROW], 1, dropSortBy=None)),
        taleo_page([ROW], 1, history=taleo_history([ROW], 1, dropListSize="many")),
        taleo_page([ROW, ROW], 1, history=taleo_history([ROW, ROW], 1)),  # more jobs than the list has
        taleo_page([], 1, history=taleo_history([], 1)),  # fewer
        # the jobs
        taleo_page([bad(f0="")], 1),
        taleo_page([bad(f2="1")], 1),  # the ids do not agree
        taleo_page([bad(f8="1")], 1),
        taleo_page([bad(f3="Other")], 1),  # the titles do not
        taleo_page([bad(f1=" ", f3=" ")], 1),
        taleo_page([bad(f9="")], 1),
        taleo_page([bad(f9="a b")], 1),
        taleo_page([bad(f10=" ")], 1),
        taleo_page([bad(f30="Submission for the position%5C: Customer Marketing Manager - (Job Number%5C: 7)")], 1),
    ],
)
def test_taleo_a_list_page_that_is_not_the_whole_readable_list_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
    fake_taleo(monkeypatch, page)
    assert adapters.taleo(SLUG) is None


def with_second_page(monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None) -> None:
    first = [taleo_row("A1"), taleo_row("A2")]
    fake_taleo(monkeypatch, taleo_page(first, 3, 2), answer)


@pytest.mark.parametrize(
    "answer",
    [
        None,
        FakeHttpResponse(status_code=500),
        FakeHttpResponse(text=""),
        taleo_answer([taleo_row("A3")], 3, 3),  # the answer of another page
        taleo_answer([taleo_row("A3")], 2, 4),  # of a list that has another size
        taleo_answer([taleo_row("A3"), taleo_row("A4")], 2, 3),  # more jobs than the page has
        taleo_answer([], 2, 3),
        taleo_answer([taleo_row("A1")], 2, 3),  # a job of the first page again: the pages overlap
        taleo_answer([bad(f9="")], 2, 3),
        taleo_answer([taleo_row("A3")], 2, 3, **{"listRequisition.nbElements": "x"}),
    ],
)
def test_taleo_a_page_that_is_not_the_one_asked_for_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse | None
) -> None:
    with_second_page(monkeypatch, answer)
    assert adapters.taleo(SLUG) is None


def test_taleo_a_failing_page_stops_the_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    first = [taleo_row("A1"), taleo_row("A2")]
    calls = fake_taleo(monkeypatch, taleo_page(first, 6, 2), FakeHttpResponse(status_code=500))
    assert adapters.taleo(SLUG) is None
    assert [c["method"] for c in calls] == ["get", "post"]  # not the third page


def test_taleo_the_pages_at_the_cap_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    total = adapters.TALEO_MAX_PAGES
    first = [taleo_row("A0")]
    answers = [taleo_answer([taleo_row(f"A{i}", ident=str(i))], i, total) for i in range(2, total + 1)]
    calls = fake_taleo(monkeypatch, taleo_page(first, total, 1), *answers)
    jobs = adapters.taleo(SLUG)
    assert jobs is not None
    assert len(jobs) == total
    assert len(calls) == total


def test_taleo_goes_through_the_robots_gate_and_the_polite_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "get", common.get)
    monkeypatch.setattr(common, "robots_gate", lambda url: False)
    assert adapters.taleo(SLUG) is None
    first = [taleo_row("A1"), taleo_row("A2")]
    monkeypatch.setattr(adapters, "get", lambda url, **kw: FakeHttpResponse(text=taleo_page(first, 3, 2)))
    monkeypatch.setattr(adapters, "post", common.post)
    assert adapters.taleo(SLUG) is None  # the call is refused too


def test_taleo_is_registered() -> None:
    assert adapters.ADAPTERS["taleo"] is adapters.taleo


# The distinct places of the live Radware list (39 jobs, 8 in Israel, read 2026-11-22), as the list writes them. Only
# `IL-IL-Tel Aviv` (7 jobs) names a city. The country alone, with a second country and no city (one job), is in Israel
# and has no region: that job keeps its text, as for any place that names no city.
RADWARE_ISRAEL = [
    ("IL-IL-Tel Aviv", (regions.TEL_AVIV,)),
    ("IL-IL-Tel Aviv ", (regions.TEL_AVIV,)),
]
RADWARE_ABROAD = [
    "AU-AU-North Sydney",
    "AU-Australia",
    "CA-Ontario-Toronto",
    "CN-CN-Shanghai",
    "CO-CO-Bogota",
    "ES-ES-Madrid",
    "GB-GB-London",
    "Germany-Germany-Langen",
    "IN-IN-Bengaluru",
    "IN-IN-Mumbai",
    "IN-IN-New Delhi",
    "IT-IT-Agrate Brianza",
    "IT-IT-Roma",
    "JP-JP-Tokyo",
    "KR-KR-Seoul",
    "Mexico",
    "SE-Sweden",
    "SE-Sweden-Stockholm",
    "SG-SG-Trivex",
    "US-Arizona",
    "US-Colorado",
    "US-NJ-Mahwah",
    "US-New Jersey",
    "US-North Carolina",
    "Uruguay",
]


@pytest.mark.parametrize(("location", "expected"), RADWARE_ISRAEL)
def test_the_regions_of_radware_are_placed(location: str, expected: tuple[str, ...]) -> None:
    assert common.ISRAEL.search(location)
    assert regions.place(location).regions == expected


@pytest.mark.parametrize("location", RADWARE_ABROAD)
def test_the_places_of_radware_abroad_are_not_israeli(location: str) -> None:
    assert not common.ISRAEL.search(location)
    assert not regions.place(location).israel


def test_radware_israel_and_a_country_has_no_region_and_keeps_its_text() -> None:
    placed = regions.place("Israel, Netherlands")
    assert placed.israel
    assert placed.regions == ()
    assert placed.locations == ("Israel, Netherlands",)
