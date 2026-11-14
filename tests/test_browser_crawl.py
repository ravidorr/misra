import asyncio
import json
from typing import Any

import pytest

import browser_crawl
from browser_crawl import WHY, crawl_one
from tests.fakes import FakeCtx, FakeEl, FakeLoc, FakePage, FakePlaywright, FakeResp, Status

URL = "https://x.test/careers"
SCROLL_CAP = 25
TWO = 2
THREE = 3
FOUR = 4
OLD_CARD_MAX = 250  # the card length limit before the crawler read whole cards


@pytest.fixture(autouse=True)
def clear_why() -> None:
    WHY.clear()


async def run(page: FakePage) -> list[dict[str, Any]] | None:
    result = await crawl_one(FakeCtx(page), URL, asyncio.Semaphore(1))
    assert page.closed
    return result


def link(path: str, text: str, card: str = "") -> list[str]:
    return [f"https://x.test{path}", text, card]


async def test_http_error_recorded() -> None:
    assert await run(FakePage(goto_result=Status(404), idle_error=True)) is None
    assert WHY[URL].startswith("HTTP 404")


@pytest.mark.parametrize(
    "title",
    [
        "Just a moment...",
        "Attention Required! | Cloudflare",
        "Please complete the CAPTCHA to continue",
        "Request unsuccessful. Incapsula incident ID: 123",
        "Access to this page has been denied",
        "Pardon Our Interruption",
    ],
)
async def test_blocked_title_recorded(title: str) -> None:
    assert await run(FakePage(goto_result=None, title=title)) is None  # a failure, not an empty company
    assert WHY[URL].startswith("blocked page")


async def test_goto_exception_recorded() -> None:
    assert await run(FakePage(goto_error=RuntimeError("boom\nsecond line"))) is None
    assert WHY[URL] == "RuntimeError: boom"
    assert await run(FakePage(goto_error=RuntimeError())) is None
    assert WHY[URL] == "RuntimeError: "


async def test_hibob_json_response_short_circuits() -> None:
    api = "https://acme.hibob.com/api/job-ad"
    ads = {
        "jobAdDetails": [
            {"id": "1", "title": "PM", "site": "Tel Aviv", "country": "IL", "department": "P"},
            {"id": "2"},
        ]
    }
    responses = [
        FakeResp("https://acme.hibob.com/other", data=ads),
        FakeResp(api, data=ads, resource_type="document"),
        FakeResp(api, data=ads, status=500),
        FakeResp(api, data=["not a dict"]),
        FakeResp(api, data={"jobAdDetails": "nope"}),
        FakeResp(api, error=True),
        FakeResp(api, data=ads),
    ]
    jobs = await run(FakePage(responses=responses))
    assert jobs == [
        {
            "job_id": "1",
            "title": "PM",
            "location": "Tel Aviv, IL",
            "url": "https://acme.hibob.com/jobs/1",
            "team": "P",
            "posted": "",
        },
        {"job_id": "2", "title": "", "location": "", "url": "https://acme.hibob.com/jobs/2", "team": "", "posted": ""},
    ]


def ld(*items: Any) -> list[str]:
    return [json.dumps(i) if not isinstance(i, str) else i for i in items]


async def test_json_ld_variants() -> None:
    def posting(url: str, **kw: Any) -> dict[str, Any]:
        return {"@type": "JobPosting", "url": url, "title": "T", **kw}

    addr = {"address": {"addressLocality": "Haifa", "addressCountry": "IL"}}
    raws = ld(
        "{not json",
        [posting("u1", jobLocation=[addr]), posting("u1"), "str", {"@type": "Other", "url": "x"}, posting("")],
        {"@graph": [posting("u2", jobLocation=addr), posting("u3", jobLocation=[]), posting("u4", jobLocation="x")]},
        posting("u5", jobLocation={"address": {"addressLocality": 3, "addressCountry": "IL"}}),
    )
    jobs = await run(FakePage(ld=raws))
    assert jobs is not None
    assert [(j["job_id"], j["location"]) for j in jobs] == [
        ("u1", "Haifa, IL"),
        ("u2", "Haifa, IL"),
        ("u3", ""),
        ("u4", ""),
        ("u5", "IL"),
    ]
    assert WHY == {}


async def test_scroll_loop_load_more_and_growth() -> None:
    btn = FakeEl()
    page = FakePage(btn=btn, count_fn=lambda i: i)
    await run(page)
    assert btn.clicks == SCROLL_CAP
    assert WHY[URL].startswith("no jobs found")
    # a failing load-more click is swallowed, and an unchanged count stops the loop
    bad = FakeEl(click_error=True)
    page = FakePage(btn=bad, count_fn=lambda _i: 3)
    assert await run(page) == []


async def test_collect_link_filters_and_titles() -> None:
    wd = "https://acme.myworkdayjobs.com/en-US/ext/job/Tel-Aviv-Israel/Product-Manager_R1/apply"
    links = [
        link("/jobs/dup-title", "Seen Job\nTel Aviv"),
        link("/jobs/dup-title/", "Seen Job again"),
        link("/jobs/", ""),
        link("/about/us-page", "Not a job link"),
        link("/team/ceo-profile", "Team page with long name"),
        link("/jobs/only-junk", "Apply\nlocation"),
        link("/jobs/lang-link", "English"),
        link("/jobs/card-job", "Card Job", "Card Job\nSave job\nHaifa\nFull time\nExtra"),
        link("/jobs/long-card", "Long Card", "x" * 300),
        link("/jobs/empty-card", "Empty Card", ""),
        link("/jobs/short-title", "PM\nHerzliya\nIL"),
        link("/jobs/short-alone", "PM"),
        link("/jobs/junk-title", "Read more\nSecond Title\nJerusalem"),
        [wd, "Workday Job\nElsewhere", ""],
    ]
    page = FakePage(link_fn=lambda i: links if i == 1 else [], ld=ld({"@type": "JobPosting", "url": "u0"}))
    jobs = await run(page)
    assert jobs is not None
    got = [(j["title"], j["location"]) for j in jobs]
    assert got == [
        ("", ""),
        ("Seen Job", "Tel Aviv"),
        ("Card Job", "Haifa"),
        ("Long Card", ""),
        ("Empty Card", ""),
        ("PM", "Herzliya | IL"),  # a short title is still a title
        ("PM", ""),
        ("Second Title", "Jerusalem"),
        ("Workday Job", "Tel Aviv Israel"),
    ]


async def crawl_cards(links: list[list[str]]) -> dict[str, tuple[str, str]]:
    jobs = await run(FakePage(link_fn=lambda i: links if i == 1 else []))
    assert jobs is not None
    return {j["title"]: (j["location"], j["team"]) for j in jobs}


async def test_card_script_receives_the_job_link_pattern() -> None:
    seen: list[Any] = []

    class Spy(FakePage):
        async def eval_on_selector_all(self, selector: str, js: str, arg: Any = None) -> Any:
            if js == browser_crawl.CARD_JS:
                seen.append(arg)
            return await super().eval_on_selector_all(selector, js, arg)

    await run(Spy(link_fn=lambda _i: []))
    assert seen == [browser_crawl.CARD_ARGS]
    assert browser_crawl.CARD_ARGS["max"] == browser_crawl.CARD_MAX  # one limit for the script and the guard


async def test_list_item_card_gives_the_place_that_follows_the_title() -> None:
    """Intezer: the title link sits in a list item with its place tags after it, in a flat list."""
    links = [
        link(
            "/careers/senior-account-executive-dach",
            "Senior Account Executive (DACH)",
            "Senior Account Executive (DACH)\n\nRemote",
        ),
        link(
            "/careers/csm-international",
            "Customer Success Manager - International",
            "Customer Success Manager - International\n\nIsrael\n\nTel-Aviv",
        ),
    ]
    assert await crawl_cards(links) == {
        "Senior Account Executive (DACH)": ("Remote", ""),
        "Customer Success Manager - International": ("Israel | Tel-Aviv", ""),
    }


async def test_a_job_description_in_the_card_is_not_a_team_or_place() -> None:
    """Weebit: title, place, a long brief, then a View Position button that links to the same job."""
    brief = "Highly experienced, hands-on engineer to lead product engineering activities for advanced devices. " * 2
    card = f"Senior Product Engineer\nIsrael, Hod HaSharon (Hybrid)\n\n{brief}\n\nView Position"
    assert len(brief) > browser_crawl.CARD_LINE_MAX
    assert len(card) > OLD_CARD_MAX
    links = [
        link("/job/senior-product-engineer/", "Senior Product Engineer", card),
        link("/job/senior-product-engineer/", "View Position", card),
    ]
    assert await crawl_cards(links) == {"Senior Product Engineer": ("Israel, Hod HaSharon (Hybrid)", "")}


def padded_card(length: int) -> str:
    base = "Product Manager\nHaifa\n"
    return base + "x" * (length - len(base))  # one filler line, longer than a place or team can be


@pytest.mark.parametrize(
    ("length", "expected"),
    [(browser_crawl.CARD_MAX, ("Haifa", "")), (browser_crawl.CARD_MAX + 1, ("", ""))],
)
async def test_card_limit_is_inclusive(length: int, expected: tuple[str, str]) -> None:
    card = padded_card(length)
    assert len(card) == length
    assert await crawl_cards([link("/jobs/pm-role", "Product Manager", card)]) == {"Product Manager": expected}


@pytest.mark.parametrize(
    ("length", "kept"), [(browser_crawl.CARD_LINE_MAX, True), (browser_crawl.CARD_LINE_MAX + 1, False)]
)
async def test_card_line_limit_is_inclusive(length: int, kept: bool) -> None:
    card = "Product Manager\n" + "L" * length
    got = await crawl_cards([link("/jobs/pm-role", "Product Manager", card)])
    assert bool(got["Product Manager"][0]) is kept


async def test_card_limits_are_the_documented_numbers() -> None:
    assert (browser_crawl.CARD_MAX, browser_crawl.CARD_LINE_MAX) == (600, 120)


async def test_a_place_above_the_title_is_found_among_the_lines_around_it() -> None:
    """Lightbits: place, title, job type, work mode. The place above the title is not crowded out by the lines below."""
    card = "Israel\nSenior Systems Engineer\nFull-Time\nHybrid"
    assert await crawl_cards([link("/careers/systems-role", "Senior Systems Engineer", card)]) == {
        "Senior Systems Engineer": ("Israel | Hybrid", "")
    }


async def test_a_place_and_team_above_the_title_with_a_description_below() -> None:
    """Priority Software: place, team, title, then a long description that is dropped."""
    card = "Israel - Rosh Haayin\nCustomer Experience\nService Center Team Lead\n\n" + "d " * 100
    assert await crawl_cards([link("/careers/service-center-lead", "Service Center Team Lead", card)]) == {
        "Service Center Team Lead": ("Israel - Rosh Haayin", "Customer Experience")
    }


async def test_headings_far_above_the_title_are_left_out() -> None:
    card = "Careers\nOpen positions\nEngineering\nProduct Manager\nTel Aviv"
    assert await crawl_cards([link("/jobs/pm-role", "Product Manager", card)]) == {
        "Product Manager": ("Tel Aviv", "Open positions")
    }


async def test_only_three_lines_below_the_title_are_read() -> None:
    card = "Product Manager\nKfar Saba\nFull-time\nHybrid\nHaifa"
    assert await crawl_cards([link("/jobs/pm-role", "Product Manager", card)]) == {
        "Product Manager": ("Kfar Saba | Hybrid", "")
    }


async def test_a_place_above_the_title_is_used_when_nothing_follows_it() -> None:
    assert await crawl_cards([link("/jobs/designer-role", "Designer", "Remote\nDesigner")]) == {
        "Designer": ("Remote", "")
    }


async def test_a_button_link_before_the_title_link_does_not_take_the_job() -> None:
    card = "Senior Product Engineer\nIsrael, Hod HaSharon\nView Position"
    links = [
        link("/job/senior-product-engineer/", "View Position", card),
        link("/job/senior-product-engineer/", "Senior Product Engineer", card),
    ]
    assert await crawl_cards(links) == {"Senior Product Engineer": ("Israel, Hod HaSharon", "")}


async def test_multi_line_link_text_does_not_borrow_from_the_card() -> None:
    card = "Product Manager\nHaifa\nHybrid\nFull-time\nextra"
    assert await crawl_cards([link("/jobs/pm-role", "Product Manager\nHaifa", card)]) == {
        "Product Manager": ("Haifa", "")
    }


async def test_a_card_longer_than_the_limit_is_not_one_job() -> None:
    links = [link("/jobs/pm-role", "Product Manager", "Product Manager\nHaifa\n" + "x " * 300)]
    assert await crawl_cards(links) == {"Product Manager": ("", "")}


async def test_pagination_no_next() -> None:
    page = FakePage(link_fn=lambda i: [link("/jobs/job-one", "Job One")] if i == 1 else [])
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == 1


def next_page(first: FakeEl, key: str = 'body a[rel="next"]') -> FakePage:
    return FakePage(
        link_fn=lambda i: [link(f"/jobs/job-{i}", f"Job {i}")],
        locators={key: FakeLoc(1, first)},
    )


async def test_pagination_stops_when_disabled() -> None:
    for el in (FakeEl(attrs={"class": "btn disabled"}), FakeEl(attrs={"aria-disabled": "true"}), FakeEl(disabled=True)):
        jobs = await run(next_page(el))
        assert jobs is not None
        assert len(jobs) == 1
        assert el.clicks == 0


async def test_pagination_click_error_stops() -> None:
    jobs = await run(next_page(FakeEl(click_error=True), key="body a.link-next"))
    assert jobs is not None
    assert len(jobs) == 1


async def test_pagination_stops_when_nothing_new() -> None:
    el = FakeEl()
    page = FakePage(
        link_fn=lambda i: [link("/jobs/job-one", "Job One")] if i <= TWO else [],
        locators={'body a[rel="next"]': FakeLoc(1, el)},
    )
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == 1
    assert el.clicks == 1


async def test_pagination_runs_to_the_cap() -> None:
    el = FakeEl()
    jobs = await run(next_page(el))
    assert jobs is not None
    cap = 61
    assert len(jobs) == cap
    assert el.clicks == cap - 1


async def test_pagination_waits_while_the_pager_clears_the_list() -> None:
    """A pager may empty the list while it fetches the next page; an empty list is not a finished crawl."""
    el = FakeEl()
    page_two_at = 4  # collect calls: 1 = page one, 2 and 3 = list cleared mid-load, 4 = page two appears

    def links(i: int) -> list[list[str]]:
        if i == 1:
            return [link("/jobs/job-one", "Job One")]
        if i == page_two_at:
            return [link("/jobs/job-two", "Job Two")]
        return []

    page = FakePage(link_fn=links, locators={'body a[rel="next"]': FakeLoc(1, el)})
    jobs = await run(page)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job One", "Job Two"]
    assert el.clicks == TWO


async def test_pagination_gives_up_waiting_for_new_links() -> None:
    el = FakeEl()
    page = FakePage(
        link_fn=lambda i: [link("/jobs/job-one", "Job One")] if i == 1 else [],
        locators={'body a[rel="next"]': FakeLoc(1, el)},
    )
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == 1
    assert el.clicks == 1
    assert page._links_calls == 1 + browser_crawl.SETTLE_POLLS  # first collect, then one per poll until it gives up


SEARCH = "https://jobs.acme.test/api/pcsx/search?domain=acme.test&location=Israel&start=0"


def position(n: int, locations: list[str] | None = None) -> dict[str, Any]:
    where = ["Israel, Tel Aviv"] if locations is None else locations
    return {"id": n, "name": f"Role {n}", "locations": where, "positionUrl": f"/careers/job/{n}", "department": "Eng"}


def search_response(*positions: dict[str, Any]) -> FakeResp:
    return FakeResp(SEARCH, data={"status": 200, "data": {"positions": list(positions)}})


def fire(page: FakePage, resp: FakeResp) -> None:
    for handler in page.handlers.get("response", []):
        handler(resp)


def eightfold_page(first: FakeResp, batches: list[FakeResp], **el_kw: Any) -> tuple[FakePage, FakeEl]:
    """Page whose first search response is `first`; each click on "Next jobs" fires the next of `batches`."""
    queue = list(batches)
    holder: dict[str, FakePage] = {}

    def on_click() -> None:
        if queue:
            fire(holder["page"], queue.pop(0))

    el = FakeEl(on_click=on_click, **el_kw)
    page = FakePage(responses=[first], locators={browser_crawl.EF_NEXT: FakeLoc(1, el)})
    holder["page"] = page
    return page, el


async def test_eightfold_single_page() -> None:
    bare = {"id": THREE, "positionUrl": "/careers/job/3"}
    page = FakePage(
        responses=[search_response(position(1), position(TWO, ["Israel, Haifa", "Israel, Beer Sheva"]), bare)]
    )
    jobs = await run(page)
    assert jobs == [
        {
            "job_id": "1",
            "title": "Role 1",
            "location": "Israel, Tel Aviv",
            "url": "https://jobs.acme.test/careers/job/1",
            "team": "Eng",
            "posted": "",
        },
        {
            "job_id": "2",
            "title": "Role 2",
            "location": "Israel, Haifa; Israel, Beer Sheva",
            "url": "https://jobs.acme.test/careers/job/2",
            "team": "Eng",
            "posted": "",
        },
        {
            "job_id": "3",
            "title": "",
            "location": "",
            "url": "https://jobs.acme.test/careers/job/3",
            "team": "",
            "posted": "",
        },
    ]


async def test_eightfold_follows_next_jobs_until_a_page_has_no_israel_jobs() -> None:
    us = position(FOUR, ["United States, Austin"])
    page, el = eightfold_page(
        search_response(position(1), position(TWO)),
        [search_response(position(THREE)), search_response(us)],
    )
    jobs = await run(page)
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["1", "2", "3", "4"]
    assert el.clicks == TWO  # the second click brought a page with no Israel job: the list is over


async def test_eightfold_first_page_without_israel_jobs_does_not_stop_the_crawl() -> None:
    page, el = eightfold_page(
        search_response(position(1, ["United States, Austin"])),
        [search_response(position(TWO))],
    )
    jobs = await run(page)
    assert jobs is not None
    assert [j["job_id"] for j in jobs] == ["1", "2"]
    assert el.clicks == TWO  # the second click found nothing new and the crawl gave up


@pytest.mark.parametrize("el_kw", [{"attrs": {"aria-disabled": "true"}}, {"disabled": True}])
async def test_eightfold_stops_when_next_is_disabled(el_kw: dict[str, Any]) -> None:
    page, el = eightfold_page(search_response(position(1)), [search_response(position(TWO))], **el_kw)
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == 1
    assert el.clicks == 0


async def test_eightfold_gives_up_when_a_click_brings_nothing_new() -> None:
    page, el = eightfold_page(search_response(position(1)), [])
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == 1
    assert el.clicks == 1


async def test_eightfold_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_crawl, "EF_MAX_PAGES", THREE)
    page, el = eightfold_page(search_response(position(1)), [search_response(position(n)) for n in range(TWO, 10)])
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == THREE
    assert el.clicks == TWO


async def test_eightfold_ignores_responses_that_are_not_a_search_result() -> None:
    page = FakePage(
        responses=[
            FakeResp(SEARCH, data={"status": 500}),
            FakeResp(SEARCH, status=404, data={"data": {"positions": [position(1)]}}),
            FakeResp(SEARCH, resource_type="script", data={"data": {"positions": [position(1)]}}),
        ],
        link_fn=lambda i: [link("/jobs/job-one", "Job One")] if i == 1 else [],
    )
    jobs = await run(page)
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Job One"]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # location first (Oligo)
        (
            ["REMOTE (US)", "Director of Product Marketing", "FULL-TIME"],
            ("Director of Product Marketing", "REMOTE (US)", ""),
        ),
        (
            ["TEL AVIV, ISRAEL", "Software Engineering Team Lead", "FULL-TIME"],
            ("Software Engineering Team Lead", "TEL AVIV, ISRAEL", ""),
        ),
        # location first, no type line (Freightos)
        (
            ["Dallas, US", "Business Development Executive", "Read more"],
            ("Business Development Executive", "Dallas, US", ""),
        ),
        (["Modi'in, IL", "CISO", "Read more"], ("CISO", "Modi'in, IL", "")),
        # team first (Weka, Pecan, Lasso)
        (
            ["Customer Success", "Data Platform Administrator (Hybrid, Bangalore)", "Bengaluru, India", "View Role"],
            ("Data Platform Administrator (Hybrid, Bangalore)", "Bengaluru, India", "Customer Success"),
        ),
        (
            ["Customer Success", "Designated Service Engineer", "South Korea", "Remote", "View Role"],
            ("Designated Service Engineer", "South Korea | Remote", "Customer Success"),
        ),
        (
            ["Analytics", "Predictive Analytics Solution Engineer", "Ramat Gan, Tel Aviv District, Israel"],
            ("Predictive Analytics Solution Engineer", "Ramat Gan, Tel Aviv District, Israel", "Analytics"),
        ),
        (
            ["Product", "Product Manager", "Ramat Gan, Tel Aviv District, Israel"],
            ("Product Manager", "Ramat Gan, Tel Aviv District, Israel", "Product"),
        ),
        (["R&D", "Senior Backend Engineer", "IL", "Full-time", "Apply Now"], ("Senior Backend Engineer", "IL", "R&D")),
        # title first (Panorays, Check Point, Komodor)
        (
            ["Enterprise Account Executive", "Sales", "United Kingdom"],
            ("Enterprise Account Executive", "United Kingdom", "Sales"),
        ),
        (
            ["Business Operations Specialist", "Israel: Tel Aviv-Yafo", "Operations"],
            ("Business Operations Specialist", "Israel: Tel Aviv-Yafo", "Operations"),
        ),
        (
            ["Product Engineer", "Atlanta, GA > Product Management > Full Time"],
            ("Product Engineer", "Atlanta, GA > Product Management > Full Time", ""),
        ),
        # padded and dotted lines (Papaya Global, eToro): a short title is not a team name
        (["Senior SDR", "New York" + " " * 90 + "Remote"], ("Senior SDR", "New York Remote", "")),
        (["Junior Dealer", "Cyprus · Full-time"], ("Junior Dealer", "Cyprus", "")),
        # location glued to the title with tabs (Komodor)
        (
            ["Enterprise Account Executive - East " + "\t" * 6 + "Boston, MA"],
            ("Enterprise Account Executive - East", "Boston, MA", ""),
        ),
        (["Product Engineer - IL " + "\t" * 6 + "Tel Aviv"], ("Product Engineer - IL", "Tel Aviv", "")),
        # a table row: cells split by one tab, and the title cell printed twice (Tower)
        (
            ["Book Keeper", "Book Keeper\tCorporate", "Migdal Haemek, ISR"],
            ("Book Keeper", "Migdal Haemek, ISR", "Corporate"),
        ),
        # a location cell printed twice, and a title with its place in the same row
        (
            ["Book Keeper", "Book Keeper\tCorporate", "Migdal Haemek, ISR", "Migdal Haemek, ISR"],
            ("Book Keeper", "Migdal Haemek, ISR", "Corporate"),
        ),
        (["Book Keeper\tMigdal Haemek, ISR"], ("Book Keeper", "Migdal Haemek, ISR", "")),
        # titles that mention a town, a country code or one word are still titles
        (["Customer Engineer - Kiryat Gat"], ("Customer Engineer - Kiryat Gat", "", "")),
        (["Software Engineer, QA", "Haifa"], ("Software Engineer, QA", "Haifa", "")),
        (["CISO"], ("CISO", "", "")),
        (["Customer Success"], ("Customer Success", "", "")),
        # a title wrapped over two lines, with the team above it or below it (Valens)
        (
            ["Senior Backend", "Engineer", "Valens HQ, Hod Hasharon, Israel", "R&D", "Full Time"],
            ("Senior Backend Engineer", "Valens HQ, Hod Hasharon, Israel", "R&D"),
        ),
        (
            ["RT Embedded", "SW Engineer", "Valens HQ, Hod Hasharon, Israel", "R&D", "Full Time"],
            ("SW Engineer", "Valens HQ, Hod Hasharon, Israel", "RT Embedded"),
        ),
        (
            ["PMO Manager", "Valens HQ, Hod Hasharon, Israel", "Product, Application & Customer Success (PACS)"],
            ("PMO Manager", "Valens HQ, Hod Hasharon, Israel", "Product, Application & Customer Success (PACS)"),
        ),
        # an acronym under a team name is the title, not the end of the team's name
        (["Security", "CISO", "Haifa"], ("CISO", "Haifa", "Security")),
        # nothing but boilerplate and places
        (["Apply now", "Full-time"], None),
        (["Tel Aviv", "Remote"], None),
    ],
)
def test_parse_card(lines: list[str], expected: tuple[str, str, str] | None) -> None:
    assert browser_crawl._parse_card(lines) == expected


@pytest.mark.parametrize(
    ("href", "key"),
    [
        ("https://x.test/jobs/pm-role?utm_source=a#top", "https://x.test/jobs/pm-role"),
        ("https://x.test/jobs/pm-role/", "https://x.test/jobs/pm-role"),
        ("https://x.test/job-description/?job_id=9672", "https://x.test/job-description?job_id=9672"),
        ("https://x.test/job-description/?utm=a&JOB_ID=9672&x=1", "https://x.test/job-description?job_id=9672"),
        ("https://x.test/job-description/?job_id=AB-123.4#apply", "https://x.test/job-description?job_id=AB-123.4"),
        ("https://x.test/jd/?job_id=R-100", "https://x.test/jd?job_id=R-100"),
        ("https://x.test/jd/?job_id=R-200", "https://x.test/jd?job_id=R-200"),
    ],
)
def test_job_key_keeps_only_a_job_id_query(href: str, key: str) -> None:
    assert browser_crawl._job_key(href) == key


async def test_job_id_links_are_separate_jobs_and_apply_buttons_are_not() -> None:
    """Tower: /job-description/?job_id=N, each job linked twice (title and Apply), one table row per job."""
    row = "{t}\t{c}\t\nMigdal Haemek, ISR\t\nApply"
    links = [
        link("/job-description/?job_id=9794", "Book Keeper", row.format(t="Book Keeper", c="Corporate")),
        link("/job-description/?job_id=9794", "Apply", row.format(t="Book Keeper", c="Corporate")),
        link("/job-description/?job_id=9524", "Apply", row.format(t="Corporate Counsel", c="Corporate")),  # first
        link("/job-description/?job_id=9524", "Corporate Counsel", row.format(t="Corporate Counsel", c="Corporate")),
    ]
    jobs = await run(FakePage(link_fn=lambda i: links if i == 1 else []))
    assert jobs is not None
    assert [(j["title"], j["location"], j["team"], j["job_id"], j["url"]) for j in jobs] == [
        (
            "Book Keeper",
            "Migdal Haemek, ISR",
            "Corporate",
            "https://x.test/job-description?job_id=9794",
            "https://x.test/job-description/?job_id=9794",
        ),
        (
            "Corporate Counsel",
            "Migdal Haemek, ISR",
            "Corporate",
            "https://x.test/job-description?job_id=9524",
            "https://x.test/job-description/?job_id=9524",
        ),
    ]


async def test_job_id_links_sit_beside_ordinary_job_links() -> None:
    links = [
        link("/jobs/pm-role?utm_source=a", "Product Manager\nHaifa"),
        link("/jobs/pm-role/", "Product Manager again"),
        link("/job-description/?job_id=1", "Designer\nTel Aviv"),
        link("/job-description/?job_id=2", "Analyst\nTel Aviv"),
    ]
    jobs = await run(FakePage(link_fn=lambda i: links if i == 1 else []))
    assert jobs is not None
    assert {j["title"]: j["location"] for j in jobs} == {
        "Product Manager": "Haifa",
        "Designer": "Tel Aviv",
        "Analyst": "Tel Aviv",
    }


async def test_cards_keep_their_title_whatever_the_line_order() -> None:
    cards = {
        "/positions/position-a": ["TEL AVIV, ISRAEL", "Software Team Lead", "FULL-TIME"],
        "/careers/weka-role-1": ["Customer Success", "Designated Service Engineer", "South Korea", "View Role"],
        "/jobs/skip-me": ["Apply now"],
        "/jobs/places-only": ["Tel Aviv", "Remote"],  # no title line at all: skipped
    }
    page = FakePage(link_fn=lambda i: [link(path, "\n".join(lines)) for path, lines in cards.items()] if i == 1 else [])
    jobs = await run(page)
    assert jobs is not None
    got = {j["title"]: (j["location"], j["team"]) for j in jobs}
    assert got == {
        "Software Team Lead": ("TEL AVIV, ISRAEL", ""),
        "Designated Service Engineer": ("South Korea", "Customer Success"),
    }


@pytest.mark.parametrize("exe", ["/bin/chromium", None])
def test_crawl_many(monkeypatch: pytest.MonkeyPatch, exe: str | None) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_crawl(_ctx: object, url: str, _sem: asyncio.Semaphore) -> list[dict[str, Any]] | None:
        return [] if url == "a" else None

    monkeypatch.setattr(browser_crawl, "async_playwright", lambda: FakePlaywright(calls))
    monkeypatch.setattr(browser_crawl, "crawl_one", fake_crawl)
    monkeypatch.setattr(browser_crawl, "EXE", exe)
    assert browser_crawl.crawl_many(iter(["a", "b"])) == {"a": [], "b": None}
    assert ("executable_path" in calls[0]) == bool(exe)
