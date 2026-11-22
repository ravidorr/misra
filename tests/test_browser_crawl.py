import asyncio
import json
import time
from typing import Any

import pytest

import browser_crawl
from browser_crawl import SETTLE_POLLS, WHY, crawl_one
from common import ISRAEL
from tests.fakes import FakeCtx, FakeEl, FakeFrame, FakeLoc, FakePage, FakePlaywright, FakeResp, Status

URL = "https://x.test/careers"
SCROLL_CAP = 25
SLOW_ANSWER = 4  # the fourth read of the link count is the first to see the new links
TWO = 2
POLL_MS = 250
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


def link(path: str, text: str, card: str = "", **fields: Any) -> list[Any]:
    """What the page script returns for one link: address, text, card, its own heading (`head`), whether the card
    holds another titled job link (`others`) and the card's text one line for each element (`leaves`)."""
    row = {"head": "", "others": False, "leaves": ""} | fields
    return [f"https://x.test{path}", text, card, row["head"], row["others"], row["leaves"]]


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
            {
                "id": "1",
                "title": "PM",
                "site": "Tel Aviv",
                "country": "IL",
                "department": "P",
                "description": "<p>Run the roadmap</p>",
                "employmentType": "Full-time",
                "workspaceType": "Hybrid",
            },
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
            "description": "Run the roadmap",  # the list gives the text: no request more
            "details": {"employment_type": "Full-time", "workplace_type": "Hybrid"},
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
        [
            posting("u1", jobLocation=[addr], description="<b>Hello</b>", employmentType="FULL_TIME"),
            posting("u1"),
            "str",
            {"@type": "Other", "url": "x"},
            posting(""),
        ],
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


async def test_load_more_waits_for_a_slow_list() -> None:
    # the list answers the click late (Priority Software's admin-ajax takes seconds from a runner): the crawl keeps
    # waiting until the link count changes, then goes on to press the button again
    btn = FakeEl()
    page = FakePage(btn=btn, count_fn=lambda i: 9 if i < SLOW_ANSWER else 21)
    await run(page)
    assert btn.clicks == TWO  # the second press finds nothing new within the wait and ends the loop


async def test_load_more_that_loads_nothing_is_pressed_once() -> None:
    btn = FakeEl()
    waits: list[int] = []
    page = FakePage(btn=btn, count_fn=lambda _i: 7)
    original = page.wait_for_timeout

    async def count_waits(ms: int) -> None:
        waits.append(ms)
        await original(ms)

    page.wait_for_timeout = count_waits  # type: ignore[method-assign]
    assert await run(page) == []
    assert btn.clicks == 1
    assert waits.count(POLL_MS) == SETTLE_POLLS  # one bounded wait, then the loop ends


class Call:
    """A request of the page: what `page.on("request")` hands over."""

    def __init__(self, url: str, resource_type: str = "xhr") -> None:
        self.url = url
        self.resource_type = resource_type


class AjaxButton(FakeEl):
    """A Load more button whose click sends one `calls` entry per click (the last repeated) and closes it after the
    matching `open_for` polls of 250 ms (None: never). `polls` counts those polls only."""

    def __init__(
        self, page: FakePage, calls: list[Call], open_for: list[int | None], before: Call | None = None
    ) -> None:
        super().__init__()
        self.before = before
        self.page = page
        self.calls = calls
        self.open_for = open_for
        self.polls = 0
        self.due: list[tuple[int, Call, str]] = []
        original = page.wait_for_timeout
        page.btn = self

        async def tick(ms: int) -> None:
            if ms == POLL_MS:
                self.polls += 1
            for item in [d for d in self.due if d[0] <= self.polls]:
                self.due.remove(item)
                self.emit(item[2], item[1])
            await original(ms)

        page.wait_for_timeout = tick  # type: ignore[method-assign]

    def emit(self, event: str, call: Call) -> None:
        for handler in self.page.handlers.get(event, []):
            handler(call)

    async def is_visible(self, timeout: int = 0) -> bool:
        if self.before is not None:
            self.emit("request", self.before)  # a call already open when the click comes
            self.before = None
        return True

    async def click(self, timeout: int = 0) -> None:
        await super().click(timeout)
        if not self.calls:
            return
        n = min(self.clicks, len(self.calls)) - 1
        self.emit("request", self.calls[n])
        end = self.open_for[min(n, len(self.open_for) - 1)]
        if end is not None:
            self.due.append((self.polls + end, self.calls[n], "requestfinished"))


SAME = Call("https://x.test/wp-admin/admin-ajax.php")
SLOW_REQUEST_POLLS = 100  # 25 s, far past SETTLE_POLLS
QUIET_POLLS = browser_crawl.QUIET_POLLS


async def test_load_more_waits_while_its_own_request_is_open() -> None:
    page = FakePage()
    btn = AjaxButton(page, [SAME, SAME], [SLOW_REQUEST_POLLS, 0])
    page.count_fn = lambda _i: 21 if btn.polls > SLOW_REQUEST_POLLS else 9
    await run(page)
    # the first press waited out its slow request; the second (answered at once) ends after the usual wait
    assert btn.clicks == TWO
    assert SLOW_REQUEST_POLLS + SETTLE_POLLS <= btn.polls < SLOW_REQUEST_POLLS + SETTLE_POLLS + 20


async def test_load_more_gives_up_on_a_request_that_never_answers() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    btn = AjaxButton(page, [SAME], [None])
    await run(page)
    assert btn.clicks == 1
    assert btn.polls == browser_crawl.SLOW_POLLS


@pytest.mark.parametrize(
    "call",
    [
        Call("https://tracker.test/collect"),
        Call("https://x.test/style.css", "stylesheet"),
        Call("https://x.test/app.js", "script"),
    ],
)
async def test_load_more_ignores_other_hosts_and_pages(call: Call) -> None:
    page = FakePage(count_fn=lambda _i: 9)  # never answered, yet it is not the list's request
    btn = AjaxButton(page, [call], [None])
    await run(page)
    assert btn.polls == SETTLE_POLLS


async def test_load_more_ignores_a_failed_call() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    btn = AjaxButton(page, [SAME], [None])
    page.on("request", lambda c: btn.due.append((btn.polls + 1, c, "requestfailed")))  # fails at once
    await run(page)
    assert btn.polls == SETTLE_POLLS  # the failed call is not waited for


async def test_load_more_does_not_wait_for_a_call_open_before_the_click() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    btn = AjaxButton(page, [], [None], before=SAME)  # a never-ending call is open when the button is pressed
    await run(page)
    assert btn.clicks == 1
    assert btn.polls == SETTLE_POLLS


async def test_load_more_waits_a_second_for_the_page_to_show_the_answer() -> None:
    page = FakePage()
    btn = AjaxButton(page, [SAME], [SLOW_REQUEST_POLLS])
    page.count_fn = lambda _i: 21 if btn.polls >= SLOW_REQUEST_POLLS + QUIET_POLLS - 1 else 9
    await run(page)
    assert btn.clicks == TWO  # the list showed up within the grace polls after its request closed


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
        [wd, "Workday Job\nElsewhere", "", "", False, ""],
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


async def test_a_pager_under_a_banner_is_pressed_with_a_dispatched_click() -> None:
    """Rapid7: a cookie banner over the pager makes the click fail; the control gets the click as an event instead,
    and the pages after the first are read (before, the list ended there and 30 of 44 jobs were all there was)."""
    el = FakeEl(click_error=True)
    page = FakePage(
        link_fn=lambda i: [link(f"/jobs/job-{i}", f"Job {i}")] if i <= THREE else [],
        locators={"body a.link-next": FakeLoc(1, el)},
    )
    jobs = await run(page)
    assert jobs is not None
    assert len(jobs) == THREE
    assert el.clicks == THREE  # three clicks, each sent as an event: the third shows nothing new and ends the list


async def test_a_pager_that_cannot_be_pressed_at_all_is_a_failure_not_a_short_list() -> None:
    el = FakeEl(click_error=2)
    jobs = await run(next_page(el, key="body a.link-next"))
    assert jobs is None
    assert WHY[URL].startswith("next page not pressed (RuntimeError)")


async def test_a_page_that_is_loading_is_read_again_and_one_that_never_reads_is_a_failure() -> None:
    def slow(i: int) -> list[Any]:
        if i == 1:
            return [link("/jobs/job-one", "Job One")]
        if i <= THREE:
            raise RuntimeError("Execution context was destroyed")  # the next page is being loaded
        return [link("/jobs/job-two", "Job Two")]

    el = FakeEl()
    jobs = await run(FakePage(link_fn=slow, locators={"body a.link-next": FakeLoc(1, el)}, count_fn=lambda _i: 5))
    assert jobs is not None
    assert [j["title"] for j in jobs][:2] == ["Job One", "Job Two"]

    def late(i: int) -> list[Any]:  # one stale read, then the page navigates and never reads again
        if i <= TWO:
            return [link("/jobs/job-one", "Job One")]
        raise RuntimeError("Execution context was destroyed")

    assert await run(FakePage(link_fn=late, locators={"body a.link-next": FakeLoc(1, FakeEl())})) is None
    assert WHY[URL].startswith("next page not readable (RuntimeError)")

    def never(i: int) -> list[Any]:
        if i == 1:
            return [link("/jobs/job-one", "Job One")]
        raise RuntimeError("Execution context was destroyed")

    assert await run(FakePage(link_fn=never, locators={"body a.link-next": FakeLoc(1, FakeEl())})) is None
    assert WHY[URL].startswith("next page not readable")


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

    def links(i: int) -> list[list[Any]]:
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


def detail(n: int) -> str:
    return f"https://jobs.acme.test/api/pcsx/position_details?position_id={n}&domain=acme.test&hl=en"


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
            "detail_url": detail(1),
        },
        {
            "job_id": "2",
            "title": "Role 2",
            "location": "Israel, Haifa; Israel, Beer Sheva",
            "url": "https://jobs.acme.test/careers/job/2",
            "team": "Eng",
            "posted": "",
            "detail_url": detail(2),
        },
        {
            "job_id": "3",
            "title": "",
            "location": "",
            "url": "https://jobs.acme.test/careers/job/3",
            "team": "",
            "posted": "",
            "detail_url": detail(3),
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
        # the same on a site that prints in capitals
        (
            ["SENIOR BACKEND", "ENGINEER", "TEL AVIV, ISRAEL"],
            ("SENIOR BACKEND ENGINEER", "TEL AVIV, ISRAEL", ""),
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
    assert sorted(browser_crawl.crawl_many(iter(["a", "b"]))) == [("a", []), ("b", None)]
    assert ("executable_path" in calls[0]) == bool(exe)


def test_crawl_many_hands_over_the_pages_done_before_a_crash_and_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_crawl(_ctx: object, url: str, _sem: asyncio.Semaphore) -> list[dict[str, Any]] | None:
        if url == "b":
            await asyncio.sleep(0.05)  # "a" is done by now
            raise RuntimeError("browser died")
        return []

    monkeypatch.setattr(browser_crawl, "async_playwright", lambda: FakePlaywright([]))
    monkeypatch.setattr(browser_crawl, "crawl_one", fake_crawl)
    got = []
    with pytest.raises(RuntimeError, match="browser died"):
        for item in browser_crawl.crawl_many(["a", "b"]):
            got.append(item)
    assert got == [("a", [])]


def test_crawl_many_stops_waiting_at_its_deadline_when_a_crawl_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_crawl(_ctx: object, url: str, _sem: asyncio.Semaphore) -> list[dict[str, Any]] | None:
        if url == "b":
            await asyncio.sleep(2)  # stalls past the deadline; the thread ends on its own a moment later
        return []

    monkeypatch.setattr(browser_crawl, "async_playwright", lambda: FakePlaywright([]))
    monkeypatch.setattr(browser_crawl, "crawl_one", fake_crawl)
    started = time.monotonic()
    got = list(browser_crawl.crawl_many(["a", "b"], deadline=started + 0.3))
    assert got == [("a", [])]  # what finished before the deadline is kept, the stalled page is given up
    assert time.monotonic() - started < 1.5  # noqa: PLR2004


async def test_an_eightfold_search_without_a_domain_gives_no_job_page_to_read() -> None:
    resp = FakeResp(
        "https://jobs.acme.test/api/pcsx/search?location=Israel", data={"data": {"positions": [position(1)]}}
    )
    jobs = await run(FakePage(responses=[resp]))
    assert jobs is not None
    assert "detail_url" not in jobs[0]


async def test_a_link_that_is_only_an_icon_or_a_button_label_takes_its_title_from_the_card() -> None:
    """env0 (an arrow), Rapyd ("Details" at the top of the card, the title further down)."""
    env0 = link(
        "/careers/senior-full-stack-engineer",
        "",
        "Senior Full Stack Engineer\n\nIsrael (Hybrid)\nFull-time",
        leaves="Senior Full Stack Engineer\nEngineering\nIsrael (Hybrid)\nFull-time\nRead more",
    )
    rapyd = link(
        "/careers/positions/bookkeeper-tel-aviv-israel/",
        "Details",
        "DetailsTel Aviv, IsraelJunior BookkeeperFinanceJunior (1-2 years)",
        leaves="Details\nTel Aviv, Israel\nJunior Bookkeeper\nFinance\nJunior (1-2 years)\nPermanent (Full time)",
    )
    arrow = link("/careers/arrow-role", "→", "Product Designer\nHaifa", leaves="→\nProduct Designer\nHaifa")
    assert await crawl_cards([env0, rapyd, arrow]) == {
        "Senior Full Stack Engineer": ("Israel (Hybrid)", "Engineering"),
        "Junior Bookkeeper": ("Tel Aviv, Israel", "Finance"),
        "Product Designer": ("Haifa", ""),
    }


@pytest.mark.parametrize(
    "extra",
    [
        {
            "others": True,
            "card": "X Role\nHaifa",
            "leaves": "X Role\nHaifa",
        },  # the card has a titled link: this repeats it
        {"card": "", "leaves": ""},
        {"card": "x" * (browser_crawl.CARD_MAX + 1), "leaves": "X Role\nHaifa"},  # not one job's card
        {
            "card": "A",
            "leaves": "Apply\nlocation\n" + "L" * (browser_crawl.CARD_LINE_MAX + 1),
        },  # only chrome and long text
    ],
)
async def test_a_label_link_without_a_card_of_its_own_is_not_a_job(extra: dict[str, Any]) -> None:
    kw: dict[str, Any] = {"card": "", "leaves": ""} | extra
    assert await crawl_cards([link("/careers/x-role", "Details", **kw)]) == {}


async def test_a_card_that_reads_as_a_banner_is_not_a_job() -> None:
    """Syte: a link to a city page, with one letter of text in its card."""
    banner = link("/careers/tel-aviv/", "", "A", leaves="A")
    one_word = link("/careers/tel-aviv/", "Details", "Offices", leaves="Offices")  # same key, still a banner
    two_words = link("/careers/product-role", "Details", "Product Lead", leaves="Product Lead\nHaifa")
    assert await crawl_cards([banner, one_word, two_words]) == {"Product Lead": ("Haifa", "")}


async def test_a_heading_in_the_link_is_the_title_whatever_the_line_order() -> None:
    """Kornit: department in capitals, then the heading, then the type and the place."""
    kornit = link(
        "/cmcareer/68-47a-4f-10e/",
        "CUSTOMER DELIVERY AND SUCCESS\nField Service Engineer - UK\nIntermediate, Full-time\nBirmingham, UK",
        head="Field Service Engineer - UK",
    )
    short = link("/cmcareer/d5-473/", "Finance\nStaff Engineer\nIsrael, Rosh Haayin", head="Staff Engineer")
    assert await crawl_cards([kornit, short]) == {
        "Field Service Engineer - UK": ("Birmingham, UK", "CUSTOMER DELIVERY AND SUCCESS"),
        "Staff Engineer": ("Israel, Rosh Haayin", "Finance"),  # not read as a title wrapped over two lines
    }


async def test_a_bare_city_under_a_location_label_is_the_place() -> None:
    """Apple: title, team, date, then "Location" and the city: past a window of three lines, but kept."""
    card = "Senior Security Researcher\nHardware\nNov 18, 2026\nLocation\nCupertino\nActions"
    links = [link("/en-il/details/200695651-0865/senior-security-researcher", "Senior Security Researcher", card)]
    assert await crawl_cards(links) == {"Senior Security Researcher": ("Cupertino", "Hardware")}


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        # a label with nothing under it, a label under the window, and a label before the title
        ("Pilot\nLocation", ["Pilot", "Location"]),
        ("Pilot\nA\nB\nC\nD\nLocation\nHaifa", ["Pilot", "A", "B", "C"]),
        ("Location\nHaifa\nPilot\nA", ["Location", "Haifa", "Pilot", "A"]),
        ("Pilot\nA\nLocation:\nHaifa\nB\nC", ["Pilot", "A", "Location:", "Haifa", "B"]),
    ],
)
def test_card_extras_keep_a_location_label_with_its_place(card: str, expected: list[str]) -> None:
    assert [ln for ln in browser_crawl._card_extras(card, "Pilot") if ln != "Pilot"] == [
        ln for ln in expected if ln != "Pilot"
    ]


def test_parse_card_reads_the_line_under_a_location_label_as_a_place() -> None:
    assert browser_crawl._parse_card(["Pilot", "Hardware", "Location", "Cupertino"]) == (
        "Pilot",
        "Cupertino",
        "Hardware",
    )
    assert browser_crawl._parse_card(["Pilot", "Location", "L" * 61]) == (
        "Pilot",
        "L" * 61,
        "",
    )  # too long: not a place


def test_parse_card_with_a_heading_title_skips_the_team_guesses() -> None:
    assert browser_crawl._parse_card(["Sales", "Analyst", "Haifa"]) == ("Sales Analyst", "Haifa", "")  # wrapped
    assert browser_crawl._parse_card(["Sales", "Analyst", "Haifa"], heading_first=True) == ("Sales", "Haifa", "Analyst")
    assert browser_crawl._parse_card(["Senior", "Engineer", "Haifa"], heading_first=True) == (
        "Senior",
        "Haifa",
        "Engineer",
    )


@pytest.mark.parametrize(
    ("href", "job"),
    [
        ("https://x.test/cmcareer/68-47a/", True),
        ("https://x.test/en-il/details/200695651-0865/senior-researcher?team=HRDWR", True),
        ("https://x.test/positions/account-executive", True),
        ("https://x.test/positions/ab", False),
        ("https://x.test/open-roles/account-executive", False),
        ("https://x.test/open-position/senior-software-engineer", True),  # Pillar Security
        ("https://x.test/open-positions/ab", False),
        ("https://x.test/2026/08/12/sales-specialist/", True),  # PayMe posts each job as a dated post
        ("https://x.test/2026/08/sales-specialist/", False),  # a monthly archive, not a post
        ("https://x.test/2026/08/12/ab/", False),
        ("https://x.test/careers/co/other/6E.358/global-cs-engineer/all", True),  # the Comeet plugin (Qwilt, SQream)
        ("https://x.test/co/other/6E.358/", False),
        ("https://x.test/co/thankyou", False),
    ],
)
def test_the_job_link_pattern(href: str, job: bool) -> None:
    assert bool(browser_crawl.JOBLINK.search(href)) is job


def test_a_country_chooser_is_not_a_job_link() -> None:
    assert browser_crawl.NOT_JOB.search("https://jobs.apple.com/careers/choose-country-region.html")
    assert not browser_crawl.NOT_JOB.search("https://jobs.apple.com/careers/choose-us")


def test_apple_next_page_button_is_a_pager() -> None:
    assert 'button[aria-label="Next page" i]' in browser_crawl.NEXT_SELS


def test_an_icims_welcome_page_is_not_a_job_link() -> None:
    assert browser_crawl.NOT_JOB.search("https://careers.x.test/jobs/intro?hashed=-1")
    assert not browser_crawl.NOT_JOB.search("https://careers.x.test/jobs/3159/engineer/job?in_iframe=1")


def test_the_icims_pager_is_a_pager() -> None:
    assert 'a:has-text("Next page of results")' in browser_crawl.NEXT_SELS


async def test_an_icims_card_gives_its_place_title_and_team_without_the_screen_reader_labels() -> None:
    """MaxLinear: "Location : Location" and "Job Posting Title" are hidden labels of the card; the description makes
    the card longer than the limit of 600 characters of a page (1000 in a frame)."""
    card = (
        "Location : Location\nISR-Petah Tikva\nJob Posting Title\nSenior Staff Software Engineer\n"
        + "A long description of the job. " * 20
        + "\nCategory Software Engineering\nID 2026-3159"
    )
    row = link("/jobs/3159/senior-staff/job", "Job Posting Title\nSenior Staff Software Engineer", card)
    assert await crawl_cards([row]) == {"Senior Staff Software Engineer": ("", "")}  # on the page: past the limit
    frame = FakeFrame("https://x.test/jobs/search?in_iframe=1", link_fn=lambda i: [row] if i == 1 else [])
    jobs = await run(FakePage(frames=[FakeFrame("https://x.test/careers"), frame]))
    assert jobs is not None
    assert [(j["title"], j["location"], j["team"]) for j in jobs] == [
        ("Senior Staff Software Engineer", "ISR-Petah Tikva", "Category Software Engineering")
    ]  # in the frame of the site: the limit is 1000


@pytest.mark.parametrize("label", ["SEND RESUME >>", "Send CV  »", "opens In A New Tab", "Submit CV"])
async def test_a_button_label_is_no_job_title(label: str) -> None:
    """SeeTrue ("SEND RESUME >>") and Nucleai ("opens In A New Tab") read as jobs titled by a button; their cards are
    a whole posting, longer than a card, so the link is skipped."""
    card = "A long posting. " * 80
    assert await crawl_cards([link("/jobs/x-role/apply", label, card)]) == {}
    assert await crawl_cards([link("/jobs/x-role/apply", "Engineer\n" + label, "Engineer\nHaifa")]) == {
        "Engineer": ("Haifa", "")
    }


@pytest.mark.parametrize(
    "label",
    ["View Full Position", "View The Position", "Apply for this position", "See Position Details", "View Opening"],
)
async def test_a_view_or_apply_label_link_takes_its_title_from_the_card(label: str) -> None:
    """Nanomotion, Variscite, Jungo, Balink and GeoEdge: the link says only what the button does; the title is a heading
    in its card."""
    row = link(
        "/jobs/hardware-engineer/",
        label,
        "Hardware Engineer\nDepartment: R&D",
        head="Hardware Engineer",
        leaves="Hardware Engineer\nDepartment: R&D\n" + label,
    )
    assert await crawl_cards([row]) == {"Hardware Engineer": ("Department: R&D", "")}


async def test_the_list_in_a_frame_of_the_same_site_is_read_and_paged() -> None:
    """iCIMS: the career page holds a frame of its own site that draws the list and its pager."""
    el = FakeEl()
    frame = FakeFrame(
        "https://x.test/jobs/search?in_iframe=1",
        link_fn=lambda i: [link(f"/jobs/role-{i}/job", f"Role {i}")] if i <= THREE else [],
    )
    frame.locators["body a.link-next"] = FakeLoc(1, el)
    other = FakeFrame("https://consent.test/banner", link_fn=lambda _i: [link("/jobs/9/banner/job", "Banner Role")])
    quiet = FakeFrame("https://x.test/widget")
    quiet.idle_error = True  # a frame that never goes quiet is read all the same
    jobs = await run(FakePage(frames=[FakeFrame("https://x.test/careers"), other, quiet, frame]))
    assert jobs is not None
    assert [j["title"] for j in jobs] == ["Role 1", "Role 2", "Role 3"]
    assert el.clicks == THREE


async def test_a_page_with_a_frame_of_its_site_that_holds_no_job_reads_its_own_links() -> None:
    frame = FakeFrame("https://x.test/widget")
    page = FakePage(frames=[FakeFrame("https://x.test/careers"), frame])
    assert await run(page) == []
    assert frame._links_calls == 1  # asked once, then the page's own links are the list again


async def test_a_bare_title_link_with_a_heading_inside_keeps_the_place_of_its_card() -> None:
    row = link("/jobs/be-role", "Backend Engineer", "Backend Engineer\nTel Aviv, Israel", head="Backend Engineer")
    assert await crawl_cards([row]) == {"Backend Engineer": ("Tel Aviv, Israel", "")}


async def test_a_heading_that_is_a_place_or_a_team_is_not_the_title() -> None:
    places = link("/jobs/a-role", "Haifa\nData\nBackend Engineer", head="Haifa")
    team = link("/jobs/b-role", "R&D\nBackend Developer\nHaifa", head="R&D")
    assert await crawl_cards([places, team]) == {
        "Backend Engineer": ("Haifa", "Data"),
        "Backend Developer": ("Haifa", "R&D"),
    }


async def test_a_label_link_never_claims_a_job_before_its_titled_twin() -> None:
    icon = link("/jobs/x-role", "", "Hot job", leaves="Hot job")
    titled = link("/jobs/x-role", "Backend Engineer", "Backend Engineer\nHaifa")
    assert await crawl_cards([icon, titled]) == {"Backend Engineer": ("Haifa", "")}


def test_a_location_label_before_the_title_does_not_make_the_title_a_place() -> None:
    lines = [
        "Backend Engineer",
        *browser_crawl._card_extras("Location\nBackend Engineer\nHaifa, Israel", "Backend Engineer"),
    ]
    assert browser_crawl._parse_card(lines) == ("Backend Engineer", "Haifa, Israel", "")


async def test_two_adjacent_label_cards_are_two_jobs_with_their_own_places() -> None:
    """Codex: both links used to inherit the whole list, so Berlin was read as the first job's Haifa."""
    haifa = link(
        "/jobs/backend-engineer",
        "Details",
        "Backend Engineer\nHaifa\nDetails",
        leaves="Backend Engineer\nHaifa\nDetails",
    )
    berlin = link(
        "/jobs/product-designer",
        "Details",
        "Product Designer\nBerlin, Germany\nDetails",
        leaves="Product Designer\nBerlin, Germany\nDetails",
    )
    jobs = await run(FakePage(link_fn=lambda i: [haifa, berlin] if i == 1 else []))
    assert jobs is not None
    assert [(j["title"], j["location"], bool(ISRAEL.search(j["location"]))) for j in jobs] == [
        ("Backend Engineer", "Haifa", True),
        ("Product Designer", "Berlin, Germany", False),
    ]


async def test_a_location_label_in_a_label_link_card_marks_its_bare_city() -> None:
    leaves = "Backend Engineer\nEngineering\nHybrid\nLocation\nCupertino"
    row = link("/jobs/backend-role", "Details", leaves.replace("\n", " "), leaves=leaves)
    assert await crawl_cards([row]) == {"Backend Engineer": ("Hybrid | Cupertino", "Engineering")}


@pytest.mark.parametrize(
    ("text", "head", "card", "expected"),
    [
        # what the reader of main gives for the same titled link; a department heading stays a team
        (
            "Customer Success\nAccount Manager\nHaifa",
            "Customer Success",
            "",
            ("Account Manager", "Haifa", "Customer Success"),
        ),
        ("Finance\nSenior Accountant\nHaifa", "Finance", "", ("Finance", "Haifa", "Senior Accountant")),
        ("Senior Engineer\nR&D\nHaifa", "Senior Engineer", "", ("Senior Engineer", "Haifa", "R&D")),
        ("Product\nProduct Manager\nTel Aviv, Israel", "", "", ("Product Manager", "Tel Aviv, Israel", "Product")),
        ("Senior Backend Engineer\nHaifa", "", "", ("Senior Backend Engineer", "Haifa", "")),
        (
            "Backend Engineer",
            "Backend Engineer",
            "Backend Engineer\nTel Aviv, Israel",
            ("Backend Engineer", "Tel Aviv, Israel", ""),
        ),
    ],
)
async def test_titled_links_read_as_they_always_did(
    text: str, head: str, card: str, expected: tuple[str, str, str]
) -> None:
    """Golden outputs of the reader before the card fallbacks: the heading rule needs a role word in the heading."""
    got = await crawl_cards([link("/jobs/some-role", text, card or text, head=head)])
    assert list(got.items()) == [(expected[0], (expected[1], expected[2]))]


async def test_a_titled_link_with_a_role_heading_and_a_long_department_reads_the_heading() -> None:
    """The one deliberate change (Kornit): main read the department line in capitals as the title."""
    row = link(
        "/jobs/some-role", "CUSTOMER DELIVERY AND SUCCESS\nField Engineer\nBirmingham, UK", head="Field Engineer"
    )
    assert await crawl_cards([row]) == {"Field Engineer": ("Birmingham, UK", "CUSTOMER DELIVERY AND SUCCESS")}


@pytest.mark.parametrize("label", ["Apply", "Apply now", "Save job", "Share job"])
async def test_adjacent_cards_with_only_an_apply_link_are_ignored_as_on_main(label: str) -> None:
    both = f"Backend Engineer\nHaifa\n{label}\nProduct Designer\nBerlin, Germany\n{label}"
    links = [
        link("/jobs/backend-engineer", label, both, leaves=both),
        link("/jobs/product-designer", label, both, leaves=both),
    ]
    assert await crawl_cards(links) == {}


class AnsweringButton(FakeEl):
    """A Load more button whose click makes the page's own XHR answer with `status` (or fail, when None)."""

    def __init__(self, page: FakePage, status: int | None) -> None:
        super().__init__()
        self.page = page
        self.status = status

    async def click(self, timeout: int = 0) -> None:
        await super().click(timeout)
        if self.status is None:
            for handler in self.page.handlers.get("requestfailed", []):
                handler(SAME)
        else:
            for handler in self.page.handlers.get("response", []):
                handler(FakeResp(SAME.url, status=self.status))


async def test_load_more_trace_names_the_answer_of_the_page_s_own_call() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    page.btn = AnsweringButton(page, 403)
    assert await run(page) == []
    assert WHY[URL].endswith("load more: 1 click(s); 9->9 links, xhr [403], failed 0")


async def test_load_more_trace_counts_a_failed_call() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    page.btn = AnsweringButton(page, None)
    await run(page)
    assert WHY[URL].endswith("9->9 links, xhr [], failed 1")


async def test_load_more_trace_of_a_crawl_with_jobs_is_kept_for_the_log() -> None:
    page = FakePage(count_fn=lambda _i: 9, ld=ld({"@type": "JobPosting", "url": "u1", "title": "T"}))
    page.btn = AnsweringButton(page, 200)
    assert await run(page) != []
    assert WHY[URL] == "load more: 1 click(s); 9->9 links, xhr [200], failed 0"


async def test_load_more_trace_ignores_other_hosts_and_resources() -> None:
    page = FakePage(count_fn=lambda _i: 9)
    btn = AnsweringButton(page, 200)
    page.btn = btn
    original = btn.click

    async def other(timeout: int = 0) -> None:
        await original(timeout)
        for handler in page.handlers.get("response", []):
            handler(FakeResp("https://tracker.test/collect", status=500))
            handler(FakeResp("https://x.test/app.js", status=500, resource_type="script"))
        for handler in page.handlers.get("requestfailed", []):
            handler(Call("https://tracker.test/collect"))
            handler(Call("https://x.test/app.js", "script"))

    btn.click = other  # type: ignore[method-assign]
    await run(page)
    assert "xhr [200], failed 0" in WHY[URL]


async def test_load_more_trace_names_a_click_that_raised() -> None:
    page = FakePage(count_fn=lambda _i: 9, btn=FakeEl(click_error=True))
    await run(page)
    assert WHY[URL].endswith("load more: 2 click(s); click raised RuntimeError; click raised RuntimeError")


async def test_load_more_trace_keeps_three_clicks_of_many() -> None:
    page = FakePage(btn=FakeEl(), count_fn=lambda i: i)
    await run(page)
    assert f"{SCROLL_CAP} click(s)" in WHY[URL]
    assert WHY[URL].count("links, xhr") == THREE


async def test_a_crawl_with_no_load_more_button_leaves_no_trace() -> None:
    await run(FakePage(ld=ld({"@type": "JobPosting", "url": "u1", "title": "T"})))
    assert WHY == {}
