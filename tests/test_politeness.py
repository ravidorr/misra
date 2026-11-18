"""The crawler's manners (PLAN.md step 6.6): the User-Agent, the per-host delay, robots.txt and the exclusion list.
Fake HTTP only: the conftest replaces the session with one that fails on any real request."""

import asyncio
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import requests

import browser_crawl
import build_dashboard
import common
import discover
import run
import sync_supabase
from common import ROBOTS_NOTE, ExclusionError
from tests.conftest import add_crawls, add_job, exclude, excluded_record, write_json
from tests.fakes import FakeCtx, FakeHttpResponse, FakePage, FakeSupabase
from tests.test_build_dashboard import prepare, read_payload, write_css, write_static

REAL_SESSION = common._s  # the conftest replaces it with one that fails on any request
REAL_LOAD_ROBOTS = common._load_robots  # the conftest replaces it with "no robots.txt anywhere"
ROOT = Path(__file__).resolve().parents[1]
TEST_DELAY = 5.0
FAR = 100  # seconds: a host that was used just now, for a wait that cannot be an accident
ACME_ROWS = 5  # two jobs, one star, one emailed key, one crawl log row
FOUR_DELETES = 4  # user_jobs, emailed, jobs, crawl_log


def waits_turns(wait: float, turns: int) -> bool:
    """Is the wait `turns` DELAYs, less the little time that passed since the turn was reserved?"""
    return turns * TEST_DELAY - 1 < wait <= turns * TEST_DELAY


# --- the User-Agent -------------------------------------------------------------------------------------------


def test_user_agent_names_the_project_the_version_the_site_and_the_contact() -> None:
    assert f"Misra/{common.VERSION} (+https://ravidorr.github.io/misra/; raanan@avidor.org)" == common.USER_AGENT
    assert re.fullmatch(r"Misra/\d+\.\d+\.\d+ \(\+https://[^;]+; [^@ ]+@[^@ ]+\)", common.USER_AGENT)
    assert "personal" not in common.USER_AGENT
    assert REAL_SESSION.headers["User-Agent"] == common.USER_AGENT  # every adapter request carries it


def test_the_version_in_common_is_the_released_version() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["version"]
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert pyproject
    assert common.VERSION == package == lock == pyproject[1]


def test_no_module_still_sends_another_user_agent() -> None:
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "IL-JobWatch" not in text, path.name
        assert "Mozilla/5.0" not in text, path.name


def test_the_browser_crawl_uses_the_shared_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Playwright:
        chromium = None

        def __init__(self) -> None:
            self.chromium = self

        async def launch(self, **_kw: Any) -> "Playwright":
            return self

        async def new_context(self, **kw: Any) -> FakeCtx:
            seen.update(kw)
            return FakeCtx(FakePage())

        async def close(self) -> None:
            return None

        async def __aenter__(self) -> "Playwright":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(browser_crawl, "async_playwright", Playwright)
    browser_crawl.crawl_many([])
    assert seen["user_agent"] == common.USER_AGENT


# --- the per-host delay ---------------------------------------------------------------------------------------


def test_reserve_slot_queues_requests_to_one_host_and_not_to_another(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "DELAY", TEST_DELAY)
    first, second, third, other = (
        common.reserve_slot("a.test"),
        common.reserve_slot("a.test"),
        common.reserve_slot("a.test"),
        common.reserve_slot("b.test"),
    )
    assert first <= 0
    assert waits_turns(second, 1)
    assert waits_turns(third, 2)  # turns queue up, one DELAY apart
    assert other <= 0


def test_host_of_keeps_the_host_and_port() -> None:
    assert common.host_of("https://a.test:8443/x?y=1") == "a.test:8443"
    assert common.host_of("http://b.test") == "b.test"


async def test_the_browser_waits_for_its_turn_like_the_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(browser_crawl.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(common, "DELAY", TEST_DELAY)
    await browser_crawl._polite("https://x.test/careers")
    assert slept == []  # the first turn is free
    # the adapters share the schedule: a request now has to wait for the browser's turn
    waits: list[float] = []
    monkeypatch.setattr(common.time, "sleep", waits.append)
    monkeypatch.setattr(common._s, "get", lambda *_a, **_k: FakeHttpResponse(), raising=False)
    common._send("get", "https://x.test/api")
    assert waits_turns(waits[0], 1)
    await browser_crawl._polite("https://x.test/next")
    assert waits_turns(slept[0], 2)


async def test_a_page_load_and_a_click_each_wait_for_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(browser_crawl.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(common, "DELAY", 5.0)
    common._last["x.test"] = time.time() + FAR  # the host was just used
    page = FakePage(url="https://x.test/careers")
    await browser_crawl.crawl_one(FakeCtx(page), "https://x.test/careers", asyncio.Semaphore(1))
    assert page.gotos == ["https://x.test/careers"]
    assert slept and slept[0] > FAR  # the load waited for the host's turn first


def test_the_concurrency_cap_stays_at_three(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    async def fake_crawl(_ctx: object, _url: str, sem: asyncio.Semaphore) -> None:
        sizes.append(sem._value)

    monkeypatch.setattr(browser_crawl, "crawl_one", fake_crawl)

    class Playwright:
        def __init__(self) -> None:
            self.chromium = self

        async def launch(self, **_kw: Any) -> "Playwright":
            return self

        async def new_context(self, **_kw: Any) -> None:
            return None

        async def close(self) -> None:
            return None

        async def __aenter__(self) -> "Playwright":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(browser_crawl, "async_playwright", Playwright)
    browser_crawl.crawl_many(["a"])
    assert sizes == [3]


# --- robots.txt: the parser -----------------------------------------------------------------------------------


def allowed(robots: str, url: str, api: bool = False) -> bool:
    return common.rules_allow(common._group_rules(robots), url, api=api)


@pytest.mark.parametrize(
    ("robots", "url", "expected"),
    [
        ("", "https://a.test/careers", True),
        ("User-agent: *\nDisallow:\n", "https://a.test/careers", True),
        ("User-agent: *\nDisallow: /\n", "https://a.test/careers", False),
        ("User-agent: *\nDisallow: /\n", "https://a.test/robots.txt", True),
        ("User-agent: *\nDisallow: /careers\n", "https://a.test/careers/jobs?id=1", False),
        ("User-agent: *\nDisallow: /careers\n", "https://a.test/about", True),
        # our own group beats the `*` group, in either direction
        ("User-agent: Misra\nAllow: /\n\nUser-agent: *\nDisallow: /\n", "https://a.test/jobs", True),
        ("User-agent: misra\nDisallow: /jobs\n\nUser-agent: *\nAllow: /\n", "https://a.test/jobs", False),
        ("User-agent: MISRA\nDisallow: /jobs\n", "https://a.test/jobs", False),
        ("User-agent: Googlebot\nDisallow: /\n", "https://a.test/jobs", True),  # another bot's group is not ours
        ("User-agent: Misrabot\nDisallow: /\n", "https://a.test/jobs", True),  # a product token is not a prefix match
        # several agents share one group; groups for the same agent add up
        ("User-agent: Googlebot\nUser-agent: Misra\nDisallow: /jobs\n", "https://a.test/jobs", False),
        ("User-agent: Misra\nDisallow: /a\n\nUser-agent: Misra\nDisallow: /b\n", "https://a.test/b", False),
        ("User-agent: Misra\nDisallow: /a\n\nUser-agent: Misra\nDisallow: /b\n", "https://a.test/c", True),
        # the longest rule wins, and Allow wins a tie
        ("User-agent: *\nDisallow: /a\nAllow: /a/b\n", "https://a.test/a/b/c", True),
        ("User-agent: *\nDisallow: /a\nAllow: /a/b\n", "https://a.test/a/c", False),
        ("User-agent: *\nAllow: /a/b\nDisallow: /a\n", "https://a.test/a/b/c", True),  # order does not matter
        ("User-agent: *\nDisallow: /a\nAllow: /a\n", "https://a.test/a", True),
        # wildcards and the end anchor
        ("User-agent: *\nDisallow: /*.json$\n", "https://a.test/x/y.json", False),
        ("User-agent: *\nDisallow: /*.json$\n", "https://a.test/x/y.json?z=1", True),
        ("User-agent: *\nDisallow: /*?finder\n", "https://a.test/api?finder=1", False),
        ("User-agent: *\nDisallow: /*?finder\n", "https://a.test/api", True),
        ("User-agent: *\nDisallow: /a*c\n", "https://a.test/abbbc", False),
        ("User-agent: *\nDisallow: /a.c\n", "https://a.test/abc", True),  # a dot is a dot, not a regex
        ("User-agent: *\nDisallow: /a*c\n", "https://a.test/abbb", True),
        ("User-agent: *\nDisallow: /a*b*c\n", "https://a.test/axbxc", False),
        ("User-agent: *\nDisallow: /a*b*c\n", "https://a.test/axb", True),
        ("User-agent: *\nDisallow: /a*b*c\n", "https://a.test/axc", True),  # a middle piece is missing
        ("User-agent: *\nDisallow: /a*b*c\n", "https://a.test/axcxb", True),  # the pieces come in order
        ("User-agent: *\nDisallow: /a*b$\n", "https://a.test/axb", False),
        ("User-agent: *\nDisallow: /a*abc$\n", "https://a.test/abc", True),  # the end cannot reuse the start
        ("User-agent: *\nDisallow: /a$\n", "https://a.test/a", False),
        ("User-agent: *\nDisallow: /a$\n", "https://a.test/ab", True),
        ("User-agent: *\nDisallow: /x*\n", "https://a.test/x", False),
        ("User-agent: *\nDisallow: /a*$\n", "https://a.test/anything", False),
        # comments, case of keys, line ends, a byte order mark, lines that are not directives
        ("﻿USER-AGENT: *  # everyone\r\nDISALLOW: /x # no\r\n", "https://a.test/x", False),
        ("<html><body>Not found</body></html>\nUser-agent *\nDisallow /\n", "https://a.test/x", True),
        ("# only a comment\n", "https://a.test/x", True),
        ("Disallow: /\n", "https://a.test/x", True),  # a rule before any User-agent line belongs to no group
        ("User-agent: *\nSitemap: https://a.test/s.xml\nDisallow: /x\n", "https://a.test/x", False),
        ("User-agent: *\nCrawl-delay: 5\nDisallow: /x\n", "https://a.test/x", False),
        # percent-encoding is compared decoded, on both sides
        ("User-agent: *\nDisallow: /caf%C3%A9\n", "https://a.test/café", False),
        ("User-agent: *\nDisallow: /café\n", "https://a.test/caf%C3%A9", False),
        ("User-agent: *\nDisallow: /a%2Fb\n", "https://a.test/a/b", False),
        ("User-agent: *\nDisallow: /careers\n", "https://a.test", True),  # no path means /
        ("User-agent: *\nDisallow: /$\n", "https://a.test", False),
    ],
)
def test_the_robots_parser(robots: str, url: str, expected: bool) -> None:
    assert allowed(robots, url) is expected


@pytest.mark.parametrize(
    ("robots", "url", "expected"),
    [
        # a blanket Disallow is written for pages: the published API is still fetched
        ("User-agent: *\nDisallow: /*\n", "https://boards-api.greenhouse.io/v1/boards/x/jobs", True),
        ("User-agent: *\nDisallow: /\n", "https://x.wd5.myworkdayjobs.com/wday/cxs/x/y/jobs", True),
        ("User-agent: *\nDisallow: /\n", "https://apply.workable.com/api/v1/widget/accounts/x", True),
        ("User-agent: *\nDisallow: /\n", "https://x.fa.oraclecloud.com/hcmRestApi/resources/latest/r", True),
        ("User-agent: *\nDisallow: /\n", "https://careers.cisco.com/widgets", True),
        # a host that is not in API_HOSTS has no exemption: SmartRecruiters' blanket Disallow stops it
        ("User-agent: *\nDisallow: /\n", "https://api.smartrecruiters.com/v1/companies/x/postings", False),
        # but a rule that names the API path is honoured
        ("User-agent: *\nDisallow: /v1/\n", "https://api.smartrecruiters.com/v1/companies/x/postings", False),
        ("User-agent: *\nDisallow: /wday/\n", "https://x.wd5.myworkdayjobs.com/wday/cxs/x/y/jobs", False),
        ("User-agent: *\nDisallow: /api/\n", "https://apply.workable.com/api/v1/widget/accounts/x", False),
        # an explicit Allow of the API path from a blanket Disallow keeps it allowed
        ("User-agent: *\nDisallow: /\nAllow: /v1/companies/\n", "https://api.smartrecruiters.com/v1/companies/x", True),
        # a page of the same site is not an API url
        ("User-agent: *\nDisallow: /\n", "https://apply.workable.com/acme/", False),
        ("User-agent: *\nDisallow: /\n", "https://www.comeet.com/jobs/acme/12.345", False),
        ("User-agent: *\nDisallow: /\n", "https://www.amazon.jobs/en/search.json", False),
    ],
)
def test_the_api_exemption_covers_a_blanket_rule_only(robots: str, url: str, expected: bool) -> None:
    assert allowed(robots, url, api=common._api_url(url)) is expected


# --- robots.txt: fetching, caching and the switch ---------------------------------------------------------------


class RobotsSession:
    """requests session: answers robots.txt from `robots`, anything else with a plain page. Records the urls."""

    def __init__(self, robots: dict[str, Any]) -> None:
        self.robots = robots
        self.urls: list[str] = []

    def get(self, url: str, **kw: Any) -> FakeHttpResponse:
        self.urls.append(url)
        if url.endswith("/robots.txt"):
            answer = self.robots.get(url.removesuffix("/robots.txt"), FakeHttpResponse(status_code=404))
            if isinstance(answer, Exception):
                raise answer
            assert kw["timeout"] == common.ROBOTS_TIMEOUT
            return answer  # type: ignore[no-any-return]
        return FakeHttpResponse(text="page")

    post = get


@pytest.fixture
def robots(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Real robots fetching against fake answers: origin -> response or exception."""
    answers: dict[str, Any] = {}
    session = RobotsSession(answers)
    monkeypatch.setattr(common, "_s", session)
    monkeypatch.setattr(common, "_load_robots", REAL_LOAD_ROBOTS)
    answers["session"] = session
    return answers


def disallow_all(robots: dict[str, Any], origin: str = "https://a.test") -> None:
    robots[origin] = FakeHttpResponse(text="User-agent: *\nDisallow: /\n")


def test_a_disallowed_url_is_not_fetched_and_the_thread_knows_why(robots: dict[str, Any]) -> None:
    disallow_all(robots)
    common.reset_robots_block()
    assert common.get("https://a.test/careers") is None
    assert common.post("https://a.test/careers", json={}) is None
    assert common.robots_blocked() == "a.test"
    assert robots["session"].urls == ["https://a.test/robots.txt"]  # only robots.txt was fetched, once
    assert common.ROBOTS_WOULD_BLOCK == {"a.test": "https://a.test/careers"}
    common.reset_robots_block()
    assert common.robots_blocked() == ""


def test_an_allowed_url_is_fetched_and_robots_txt_is_read_once_per_host(robots: dict[str, Any]) -> None:
    robots["https://a.test"] = FakeHttpResponse(text="User-agent: *\nDisallow: /private\n")
    assert common.get("https://a.test/careers") is not None
    assert common.get("https://a.test/jobs") is not None
    assert common.get("https://a.test/private") is None
    assert common.get("https://b.test/private") is not None  # another host has no robots.txt: allowed
    assert robots["session"].urls.count("https://a.test/robots.txt") == 1
    assert robots["session"].urls.count("https://b.test/robots.txt") == 1


def test_http_and_https_and_ports_are_different_origins(robots: dict[str, Any]) -> None:
    disallow_all(robots, "https://a.test")
    assert common.get("https://a.test/x") is None
    assert common.get("http://a.test/x") is not None
    assert common.get("https://a.test:8443/x") is not None


@pytest.mark.parametrize("status", [404, 410, 401, 403, 429])
def test_a_missing_or_refused_robots_txt_means_allowed(robots: dict[str, Any], status: int) -> None:
    robots["https://a.test"] = FakeHttpResponse(status_code=status, text="User-agent: *\nDisallow: /\n")
    assert common.get("https://a.test/x") is not None


@pytest.mark.parametrize("status", [500, 503])
def test_a_server_error_on_robots_txt_means_allowed_and_is_logged(
    robots: dict[str, Any], capsys: pytest.CaptureFixture[str], status: int
) -> None:
    robots["https://a.test"] = FakeHttpResponse(status_code=status, text="User-agent: *\nDisallow: /\n")
    assert common.get("https://a.test/x") is not None
    assert f"https://a.test answered HTTP {status}" in capsys.readouterr().out


def test_a_robots_txt_that_cannot_be_fetched_means_allowed_and_is_logged(
    robots: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    robots["https://a.test"] = requests.Timeout("slow")
    assert common.get("https://a.test/x") is not None
    assert "https://a.test did not answer" in capsys.readouterr().out


def test_only_the_first_half_megabyte_of_robots_txt_is_read(robots: dict[str, Any]) -> None:
    text = "User-agent: *\n" + "# padding\n" * 60000 + "Disallow: /late\n"
    assert len(text) > common.ROBOTS_MAX_BYTES
    robots["https://a.test"] = FakeHttpResponse(text=text)
    assert common.get("https://a.test/late") is not None


def test_addresses_without_a_host_or_a_web_scheme_are_never_blocked(robots: dict[str, Any]) -> None:
    assert common.robots_allows("ftp://a.test/x")
    assert common.robots_allows("/relative")
    assert robots["session"].urls == []


def test_the_switch_off_logs_what_would_be_blocked_and_blocks_nothing(
    robots: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    disallow_all(robots)
    monkeypatch.setattr(common, "ROBOTS_ENFORCED", False)
    common.reset_robots_block()
    assert common.get("https://a.test/careers") is not None
    assert common.robots_blocked() == ""  # nothing was refused, so nothing looks like a skip
    assert common.ROBOTS_WOULD_BLOCK == {"a.test": "https://a.test/careers"}


def test_several_threads_asking_for_one_host_fetch_robots_txt_once(robots: dict[str, Any]) -> None:
    robots["https://a.test"] = FakeHttpResponse(text="User-agent: *\nDisallow: /no\n")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(common.robots_allows, [f"https://a.test/p{n}" for n in range(16)]))
    assert all(results)
    assert robots["session"].urls.count("https://a.test/robots.txt") == 1


def test_the_robots_block_flag_belongs_to_the_thread(robots: dict[str, Any]) -> None:
    disallow_all(robots)
    common.reset_robots_block()

    def blocked_in_thread() -> str:
        common.reset_robots_block()
        common.get("https://a.test/x")
        return common.robots_blocked()

    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(blocked_in_thread).result() == "a.test"
    assert common.robots_blocked() == ""  # this thread was not the one refused


# --- robots.txt in the crawl ---------------------------------------------------------------------------------


URL = "https://x.test/careers"


async def test_the_browser_crawl_skips_a_page_robots_txt_forbids(monkeypatch: pytest.MonkeyPatch) -> None:
    browser_crawl.WHY.clear()
    common._robots["https://x.test"] = [(False, "/careers")]
    page = FakePage()
    assert await browser_crawl.crawl_one(FakeCtx(page), URL, asyncio.Semaphore(1)) is None
    assert browser_crawl.WHY[URL] == ROBOTS_NOTE
    assert page.gotos == []  # never opened


async def test_the_browser_crawl_goes_on_when_the_switch_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    browser_crawl.WHY.clear()
    monkeypatch.setattr(common, "ROBOTS_ENFORCED", False)
    common._robots["https://x.test"] = [(False, "/careers")]
    page = FakePage()
    await browser_crawl.crawl_one(FakeCtx(page), URL, asyncio.Semaphore(1))
    assert page.gotos == [URL]
    assert browser_crawl.WHY.get(URL, "") != ROBOTS_NOTE


def co(name: str, ats: str = "fake", slug: str | None = None, status: str = "ok") -> dict[str, Any]:
    return {"name": name, "crawl": True, "ats": ats, "slug": slug or name.lower(), "status": status}


def job(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "title": "T", "location": "L", "url": "u", "team": "t", "posted": "p"}


@pytest.fixture
def blocked_adapter(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """An adapter named `fake` that fetches https://<slug>.test/x through common.get, as the real adapters do."""
    calls: list[str] = []
    monkeypatch.setattr(common, "_s", RobotsSession({}))

    def fetch(slug: str) -> Any:
        calls.append(slug)
        response = common.get(f"https://{slug}.test/x")
        return None if response is None else [job("1")]

    monkeypatch.setitem(run.ADAPTERS, "fake", fetch)
    monkeypatch.setattr(run.time, "sleep", lambda _s: calls.append("slept"))
    return calls


def test_a_company_robots_txt_forbids_is_skipped_not_failed(
    workdir: Path, blocked_adapter: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    conn = run.init()
    add_job(conn, "Walled", "old", "Old job")
    add_job(conn, "Open", "old", "Old job")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Walled"), co("Open")])
    common._robots["https://walled.test"] = [(False, "/")]
    run.main()
    out = capsys.readouterr().out
    assert "failed 0 []" in out
    assert "skipped by robots.txt 1 ['Walled']" in out
    assert sorted(blocked_adapter) == ["open", "walled"]  # one try each, no retry
    assert "slept" not in blocked_adapter
    check = sqlite3.connect(run.DB)
    log = {r[0]: r[1:] for r in check.execute("SELECT company, ok, n_jobs, note FROM crawl_log")}
    assert log["Walled"][0] == 0
    assert log["Walled"][2].startswith(ROBOTS_NOTE)
    assert log["Open"][2] == ""
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE company='Walled'").fetchone() == (0,)  # its roles are gone
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE company='Open'").fetchone() == (2,)  # the others stay
    check.close()
    assert "robots.txt: 1 jobs of skipped companies deleted from the history" in out


def test_a_browser_company_robots_txt_forbids_is_skipped_not_failed(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(workdir / "data" / "companies.json", [co("Br", ats="browser", slug="https://br.test/careers")])
    monkeypatch.setattr(
        run,
        "crawl_many",
        lambda urls: browser_crawl.WHY.update(dict.fromkeys(urls, ROBOTS_NOTE)) or dict.fromkeys(urls),
    )
    run.main()
    out = capsys.readouterr().out
    assert "failed 0 []" in out
    assert "skipped by robots.txt 1 ['Br']" in out
    assert "browser Br" in out
    assert "SKIPPED" in out
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT ok, note FROM crawl_log").fetchall() == [(0, ROBOTS_NOTE)]
    check.close()


def test_the_switch_off_crawls_everything_and_the_log_says_so(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(workdir / "data" / "companies.json", [co("Walled")])
    monkeypatch.setattr(common, "ROBOTS_ENFORCED", False)
    common._robots["https://walled.test"] = [(False, "/")]
    run.main()
    out = capsys.readouterr().out
    assert "robots.txt: NOT enforced" in out
    assert "robots.txt would have refused 1 hosts ['walled.test']" in out
    assert "skipped by robots.txt 0 []" in out
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE company='Walled'").fetchone() == (1,)
    check.close()


def test_a_robots_skip_is_not_a_coverage_gap_for_the_maintainer(db: sqlite3.Connection) -> None:
    add_crawls(db, "Walled", [(0, ROBOTS_NOTE), (0, ROBOTS_NOTE), (0, f"{ROBOTS_NOTE} (walled.test)")], ats="fake")
    add_crawls(db, "Broken", [(0, "request failed"), (0, "request failed")], ats="fake")
    assert [g["company"] for g in run.coverage_gaps(db)] == ["Broken"]


def test_the_coverage_card_lists_a_robots_skip_at_once_with_its_reason(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    prepare(workdir)
    add_crawls(db, "Walled", [(1, ""), (0, f"{ROBOTS_NOTE} (walled.test)")], ats="fake")
    add_crawls(db, "Down", [(0, "boom"), (0, "boom")], ats="fake")
    db.close()
    build_dashboard.main()
    gaps = read_payload(workdir)["gaps"]
    groups = {g["reason"]: g for g in gaps["groups"]}
    assert groups["robots_disallowed"]["label"] == "robots.txt does not allow it"
    (site,) = groups["robots_disallowed"]["sites"]
    assert site["c"] == "Walled"
    assert site["why"] == ""  # the card says why once for the group, not per company
    assert [s["c"] for s in groups["crawl_failing"]["sites"]] == ["Down"]  # a failure stays a failure


def test_the_robots_card_has_no_text_per_company_and_no_days(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    prepare(workdir)
    add_crawls(db, "Walled", [(1, "")] + [(0, ROBOTS_NOTE)] * 9, ats="fake")
    db.close()
    build_dashboard.main()
    gaps = read_payload(workdir)["gaps"]
    groups = {g["reason"]: g for g in gaps["groups"]}
    (site,) = groups["robots_disallowed"]["sites"]
    assert set(site) == {"c", "why", "url", "to"}
    assert site["why"] == ""
    assert "closeDays" not in gaps
    assert all("days" not in gap for gap in gaps["blocked"])


# --- the exclusion list ---------------------------------------------------------------------------------------


def write_list(path: Path, *rows: tuple[str, str, str]) -> str:
    """A pulled list holding one excluded record for each (name, reason, date_added), and a company that is not."""
    records = [{"name": "Kept", "crawl": True}, *(excluded_record(c, r, d) for c, r, d in rows)]
    path.write_text(json.dumps(records), encoding="utf-8")
    return str(path)


def test_a_list_with_no_excluded_record_has_no_companies(tmp_path: Path) -> None:
    assert common.load_excluded(write_list(tmp_path / "x.json")) == []
    (tmp_path / "y.json").write_text("[]", encoding="utf-8")
    assert common.load_excluded(str(tmp_path / "y.json")) == []


def test_names_are_stripped_and_read_in_order_and_only_excluded_records_count(tmp_path: Path) -> None:
    path = write_list(
        tmp_path / "x.json", ("  Acme  ", "asked by email", "2026-11-20"), ("Beta", "asked", "2026-11-21")
    )
    assert common.load_excluded(path) == ["Acme", "Beta"]  # the record of "Kept" has `excluded` unset: not on the list


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([("", "r", "2026-11-20")], "record 2: the company name is blank"),
        ([("   ", "r", "2026-11-20")], "blank or shorter"),
        ([("A", "r", "2026-11-20")], "blank or shorter"),
        ([("Acme", "r", "2026-11-20"), ("acme", "r", "2026-11-20")], "record 3: 'acme' is on the list twice"),
        ([("Acme", "", "2026-11-20")], "the reason is blank"),
        ([("Acme", "  ", "2026-11-20")], "the reason is blank"),
        ([("Acme", "r", "")], "not a YYYY-MM-DD date"),
        ([("Acme", "r", "20/11/2026")], "not a YYYY-MM-DD date"),
        ([("Acme", "r", "2026-13-40")], "not a YYYY-MM-DD date"),
    ],
)
def test_a_bad_list_raises_and_says_where(tmp_path: Path, rows: list[tuple[str, str, str]], message: str) -> None:
    with pytest.raises(ExclusionError, match=message):
        common.load_excluded(write_list(tmp_path / "x.json", *rows))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{}", "it must be a list of records"),
        ('["Acme"]', "it must be a list of records"),
        ("not json", "cannot be read"),
        ('[{"crawl": false, "excluded": {"reason": "r", "date_added": "2026-11-20"}}]', "the company name is blank"),
        ('[{"name": 7, "excluded": {"reason": "r", "date_added": "2026-11-20"}}]', "the company name is blank"),
        ('[{"name": "Acme", "excluded": {"reason": "r"}}]', "not a YYYY-MM-DD date"),
        ('[{"name": "Acme", "excluded": {"reason": "r", "date_added": 20261120}}]', "not a YYYY-MM-DD date"),
        ('[{"name": "Acme", "excluded": {"reason": 5, "date_added": "2026-11-20"}}]', "the reason is blank"),
        ('[{"name": "Acme", "excluded": {}}]', "the reason is blank"),
        ('[{"name": "Acme", "excluded": "yes"}]', "the reason is blank"),
    ],
)
def test_a_pulled_file_of_another_shape_is_a_bad_list(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "x.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ExclusionError, match=message):
        common.load_excluded(str(path))


def test_a_missing_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ExclusionError, match="cannot be read"):
        common.load_excluded(str(tmp_path / "nope.json"))


def test_a_very_long_list_raises(tmp_path: Path) -> None:
    rows = [(f"Company {n}", "r", "2026-11-20") for n in range(common.EXCLUDED_MAX + 1)]
    with pytest.raises(ExclusionError, match="more than 200 excluded records"):
        common.load_excluded(write_list(tmp_path / "x.json", *rows))
    assert len(common.load_excluded(write_list(tmp_path / "y.json", *rows[:-1]))) == common.EXCLUDED_MAX


def test_is_excluded_ignores_case_and_padding_but_not_other_names() -> None:
    assert common.is_excluded("ACME", ["Acme"])
    assert common.is_excluded(" acme ", ["Acme"])
    assert not common.is_excluded("Acme Corp", ["Acme"])
    assert not common.is_excluded("Acm", ["Acme"])
    assert not common.is_excluded("Anything", [])


def test_an_excluded_company_is_never_crawled_and_its_data_is_deleted(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    crawled: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: crawled.append(slug) or [job("1")])
    conn = run.init()
    for name in ("Acme", "Acme Corp", "Other"):
        add_job(conn, name, "old", "Old job")
    add_crawls(conn, "Acme", [(1, "")], ats="fake")
    add_crawls(conn, "Acme Corp", [(1, "")], ats="fake")
    conn.commit()
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Acme"), co("Other")])
    exclude(workdir, "Acme")
    run.main()
    assert crawled == ["other"]  # Acme is not crawled
    assert "exclusion list: deleted 1 jobs of Acme" in capsys.readouterr().out
    check = sqlite3.connect(run.DB)
    left = {r[0] for r in check.execute("SELECT company FROM jobs")}
    assert left == {"Acme Corp", "Other"}  # deleted by the exact name only
    assert {r[0] for r in check.execute("SELECT company FROM crawl_log")} == {"Acme Corp", "Other"}
    check.close()


def test_the_purge_does_nothing_for_an_empty_list(db: sqlite3.Connection) -> None:
    add_job(db, "Acme", "1", "T")
    assert run.purge_excluded(db, []) == {}
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone() == (1,)


def test_the_purge_refuses_more_than_a_few_companies_and_deletes_nothing(db: sqlite3.Connection) -> None:
    names = ["A1", "B2", "C3", "D4"]
    for name in names:
        add_job(db, name, "1", "T")
    with pytest.raises(ExclusionError, match="refusing to delete the jobs of 4 companies at once"):
        run.purge_excluded(db, names)
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone() == (4,)
    # companies with nothing stored do not count towards the limit
    assert run.purge_excluded(db, [*names[:3], "Nobody", "Nobody Else"]) == {"A1": 1, "B2": 1, "C3": 1}


def test_a_bad_exclusion_list_stops_the_run_before_any_crawl(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crawled: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: crawled.append(slug) or [])
    write_json(workdir / "data" / "companies.json", [co("Acme")])
    exclude(workdir, "")  # an excluded record with no name
    with pytest.raises(ExclusionError):
        run.main()
    assert crawled == []


def rows_of(workdir: Path) -> list[dict[str, Any]]:
    return json.loads((workdir / "data" / "companies.json").read_text())  # type: ignore[no-any-return]


def test_discover_skips_an_excluded_seed_row(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_json(
        workdir / "data" / "companies.json",
        [
            {"name": "Acme", "domain": "acme.test", "crawl": True},
            {"name": "Beta", "domain": "beta.test", "crawl": True},
            {"name": "Parked", "domain": "parked.test", "crawl": False},
        ],
    )
    exclude(workdir, "Acme")
    seen: list[str] = []
    monkeypatch.setattr(
        discover,
        "discover",
        lambda row: seen.append(row["name"]) or dict.fromkeys(discover.DISCOVERED, "x"),
    )
    monkeypatch.setattr(sys, "argv", ["discover.py"])
    discover.main()
    assert seen == ["Beta"]  # not the excluded one, not the one that is not crawled
    rows = {r["name"]: r for r in rows_of(workdir)}
    assert rows["Beta"]["status"] == "x"
    assert [r for r in rows_of(workdir) if r["name"] == "Acme" and "status" in r] == []  # nothing is written for it


def test_the_dashboard_never_lists_an_excluded_company(db: sqlite3.Connection, workdir: Path) -> None:
    write_css(workdir)
    write_static(workdir)
    write_json(
        workdir / "data" / "companies.json",
        [
            {"name": "Acme", "crawl": True, "status": "careers_page_only"},
            {"name": "Beta", "crawl": True, "status": "not_found"},
            {"name": "Acme", "crawl": False, "domain": "a.test", "gap": {"reason": "no_openings", "note": ""}},
            {"name": "Gamma", "crawl": False, "domain": "g.test", "gap": {"reason": "no_openings", "note": ""}},
        ],
    )
    add_crawls(db, "Acme", [(0, "boom"), (0, "boom")], ats="fake")
    add_crawls(db, "Gamma", [(0, "boom"), (0, "boom")], ats="fake")
    add_job(db, "Acme", "1", "Product Manager")
    add_job(db, "Beta", "1", "Product Manager")
    db.close()
    exclude(workdir, "Acme")
    build_dashboard.main()
    payload = read_payload(workdir)
    names = {s["c"] for g in payload["gaps"]["groups"] for s in g["sites"]}
    assert names == {"Beta", "Gamma"}
    assert [j["c"] for j in payload["jobs"]] == ["Beta"]
    assert "Acme" not in (workdir / "docs" / "index.html").read_text(encoding="utf-8")
    assert "Acme" not in (workdir / "docs" / "jobs.jsonld").read_text(encoding="utf-8")


# --- the Supabase side ---------------------------------------------------------------------------------------


KEY = "service-role-key-xyz"
URL_SUPABASE = "https://abc.supabase.co"


def supabase_client(fake: FakeSupabase) -> sync_supabase.Client:
    return sync_supabase.Client(fake, URL_SUPABASE, KEY)  # type: ignore[arg-type]


def fill(fake: FakeSupabase, company: str, jobs: int = 2) -> None:
    for n in range(jobs):
        fake.table[(company, str(n))] = {"company": company, "job_id": str(n), "status": "open"}
    fake.others["user_jobs"].append({"company": company, "job_id": "0", "title": "kept title", "url": "https://x"})
    fake.others["emailed"].append({"company": company, "job_id": "0"})
    fake.others["crawl_log"].append({"company": company, "ts": "2026-11-16T04:30:00"})


@pytest.fixture
def local(db: sqlite3.Connection) -> sqlite3.Connection:
    add_job(db, "Beta", "9", "Junior Data Scientist")
    return db


def test_an_empty_list_sends_no_request_at_all(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    assert sync_supabase.sync(local, supabase_client(fake), []).purged == 0
    assert {c["method"] for c in fake.calls} == {"POST", "GET"}  # the upsert and the reads, nothing else
    assert all(c["url"].endswith(("/jobs", "/crawl_log")) and "company" not in c["params"] for c in fake.calls)


def test_the_purge_deletes_jobs_stars_and_emailed_keys_of_that_company_only(
    local: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeSupabase()
    for name in ("Acme", "Acme Corp", "acme", "Beta"):
        fill(fake, name)
    result = sync_supabase.sync(local, supabase_client(fake), ["Acme"])
    assert result.purged == ACME_ROWS
    assert {k[0] for k in fake.table} == {"Acme Corp", "acme", "Beta"}
    assert {r["company"] for r in fake.others["user_jobs"]} == {"Acme Corp", "acme", "Beta"}
    assert {r["company"] for r in fake.others["emailed"]} == {"Acme Corp", "acme", "Beta"}
    assert {r["company"] for r in fake.others["crawl_log"]} == {"Acme Corp", "acme", "Beta"}
    out = capsys.readouterr().out
    assert "deleted {'user_jobs': 1, 'emailed': 1, 'jobs': 2, 'crawl_log': 1} for Acme" in out
    assert "kept title" not in out and KEY not in out  # counts only, never rows
    deletes = [c for c in fake.calls if c["method"] == "DELETE"]
    assert len(deletes) == FOUR_DELETES
    assert all(c["params"]["company"] == "eq.Acme" for c in deletes)  # always filtered, by `eq`, never a pattern
    assert all(c["headers"]["Prefer"] == "return=representation" for c in deletes)


def test_a_second_run_finds_nothing_to_delete(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    fill(fake, "Acme")
    assert sync_supabase.sync(local, supabase_client(fake), ["Acme"]).purged == ACME_ROWS
    fake.calls.clear()
    assert sync_supabase.sync(local, supabase_client(fake), ["Acme"]).purged == 0
    assert not [c for c in fake.calls if c["method"] == "DELETE"]


def test_stars_alone_are_enough_to_make_a_company_present(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    fake.others["user_jobs"].append({"company": "Acme", "job_id": "7"})
    assert sync_supabase.sync(local, supabase_client(fake), ["Acme"]).purged == 1


def test_rows_of_an_excluded_company_in_the_local_database_are_never_uploaded(local: sqlite3.Connection) -> None:
    add_job(local, "Acme", "1", "Product Manager")
    fake = FakeSupabase()
    sync_supabase.sync(local, supabase_client(fake), ["acme"])
    assert {k[0] for k in fake.table} == {"Beta"}


def test_more_than_a_few_companies_at_once_are_refused_before_any_delete(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    names = ["A1", "B2", "C3", "D4"]
    for name in names:
        fill(fake, name)
    with pytest.raises(sync_supabase.SyncError, match="refusing to delete the data of 4 companies at once"):
        sync_supabase.sync(local, supabase_client(fake), names)
    assert len(fake.table) == len(names) * 2
    assert not [c for c in fake.calls if c["method"] == "DELETE"]
    assert not [c for c in fake.calls if c["method"] == "POST"]  # nothing was uploaded either


def test_names_without_rows_do_not_count_towards_the_limit(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    for name in ("A1", "B2", "C3"):
        fill(fake, name, jobs=1)
    names = ["A1", "B2", "C3", "N1", "N2", "N3"]
    assert sync_supabase.sync(local, supabase_client(fake), names).purged == FOUR_DELETES * 3


@pytest.mark.parametrize("name", ["", "A", " Acme", "Acme "])
def test_a_delete_needs_a_proper_name(name: str) -> None:
    fake = FakeSupabase()
    with pytest.raises(sync_supabase.SyncError, match="proper company name"):
        supabase_client(fake).delete_company("jobs", name)
    assert fake.calls == []


def test_a_failed_delete_is_a_sync_error_and_uploads_nothing(local: sqlite3.Connection) -> None:
    fake = FakeSupabase(fail_on="DELETE")
    fill(fake, "Acme")
    with pytest.raises(sync_supabase.SyncError, match="DELETE user_jobs failed: HTTP 500 boom"):
        sync_supabase.sync(local, supabase_client(fake), ["Acme"])
    assert not [c for c in fake.calls if c["method"] == "POST"]


def test_odd_answers_from_the_company_calls_are_sync_errors() -> None:
    fake = FakeSupabase()
    fake.request = lambda *_a, **_k: FakeHttpResponse(data={"message": "nope"})  # type: ignore[method-assign]
    with pytest.raises(sync_supabase.SyncError, match="GET jobs returned something other than a list"):
        supabase_client(fake).has_rows("jobs", "Acme")
    with pytest.raises(sync_supabase.SyncError, match="DELETE jobs returned something other than a list"):
        supabase_client(fake).delete_company("jobs", "Acme")


def test_main_purges_with_the_list_of_the_repository(
    local: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    local.commit()
    exclude(workdir, "Acme")
    fake = FakeSupabase()
    fill(fake, "Acme")
    monkeypatch.setenv("SUPABASE_URL", URL_SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()
    assert f"1 open jobs upserted, 0 closed, {ACME_ROWS} rows purged" in capsys.readouterr().out
    assert {k[0] for k in fake.table} == {"Beta"}


def test_main_stops_on_a_bad_list_and_touches_nothing(
    local: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local.commit()
    exclude(workdir, "A")
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", URL_SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    with pytest.raises(SystemExit, match="blank or shorter"):
        sync_supabase.main()
    assert fake.calls == []


def test_a_list_that_spells_a_company_differently_stops_the_run_and_deletes_nothing(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crawled: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: crawled.append(slug) or [])
    conn = run.init()
    add_job(conn, "Acme", "1", "T")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Acme")])
    exclude(workdir, "acme")  # skipping ignores case, deleting does not: the run must say so
    with pytest.raises(ExclusionError, match="spells a company differently"):
        run.main()
    assert crawled == []
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone() == (1,)
    check.close()


def test_a_list_that_is_not_text_is_a_bad_list(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ExclusionError, match=r"cannot be read \(UnicodeDecodeError\)"):
        common.load_excluded(str(path))


def test_a_robots_skip_between_crawls_does_not_turn_one_failure_into_a_streak(db: sqlite3.Connection) -> None:
    add_crawls(db, "W", [(1, ""), (0, common.ROBOTS_NOTE), (0, "boom"), (1, "")])
    add_crawls(db, "V", [(0, "boom"), (0, common.ROBOTS_NOTE), (1, "")])
    assert run.coverage_gaps(db) == []
    add_crawls(db, "U", [(0, "boom"), (0, common.ROBOTS_NOTE), (0, "boom"), (1, "")])
    assert run.coverage_gaps(db) == []
    add_crawls(db, "T", [(0, "boom"), (0, common.ROBOTS_NOTE), (0, "boom")])
    assert [g["company"] for g in run.coverage_gaps(db)] == ["T"]


def test_no_host_is_exempt_from_a_blanket_disallow_until_one_has_written_permission() -> None:
    # the mechanism stays, the set is empty (see common.py and CONTRIBUTING.md): adding a host is a decision
    assert set(common.ROBOTS_API_EXCEPTION_HOSTS) == set()
    assert "api.smartrecruiters.com" not in common.API_HOSTS
    assert {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"} == set(common.API_HOSTS)


SMARTRECRUITERS_ROBOTS = "User-agent: LinkedInBot\nAllow: /v1/companies/\nUser-agent: *\nDisallow: /\n"


def test_a_company_on_smartrecruiters_is_skipped_for_robots_txt_not_failed(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = RobotsSession({"https://api.smartrecruiters.com": FakeHttpResponse(text=SMARTRECRUITERS_ROBOTS)})
    monkeypatch.setattr(common, "_s", session)
    monkeypatch.setattr(common, "_load_robots", REAL_LOAD_ROBOTS)
    slept: list[float] = []
    monkeypatch.setattr(run.time, "sleep", slept.append)
    conn = run.init()
    add_job(conn, "Armis", "old", "Old job")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Armis", ats="smartrecruiters", slug="armis")])
    run.main()
    out = capsys.readouterr().out
    assert "failed 0 []" in out
    assert "skipped by robots.txt 1 ['Armis']" in out
    assert slept == []  # no retry
    assert not [u for u in session.urls if "/v1/companies/" in u]  # the postings were never requested
    check = sqlite3.connect(run.DB)
    ((ok, note),) = check.execute("SELECT ok, note FROM crawl_log").fetchall()
    assert ok == 0
    assert note == f"{ROBOTS_NOTE} (api.smartrecruiters.com)"
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE company='Armis'").fetchone() == (0,)  # gone at once
    check.close()
    assert run.coverage_gaps(run.init()) == []  # no alert for the maintainer


# --- a company skipped for robots.txt loses its jobs at once --------------------------------------------------------


def rows(sql: str, *args: Any) -> list[tuple[Any, ...]]:
    check = sqlite3.connect(run.DB)
    try:
        return check.execute(sql, args).fetchall()
    finally:
        check.close()


def run_on(day: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run.time, "strftime", lambda _fmt: f"{day}T06:00:00")
    run.main()


def seed_walled(*names: str) -> None:
    conn = run.init()
    for name in names:
        add_job(conn, name, "old", "Old job")
    conn.close()


def test_the_jobs_of_a_skipped_company_go_on_the_first_skip_and_a_second_run_has_nothing_to_delete(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_walled("Walled", "Other")
    write_json(workdir / "data" / "companies.json", [co("Walled"), co("Other")])
    common._robots["https://walled.test"] = [(False, "/")]
    run_on("2026-11-01", monkeypatch)
    assert rows("SELECT company FROM jobs ORDER BY company, job_id") == [("Other",), ("Other",)]
    assert "robots.txt: 1 jobs of skipped companies deleted from the history" in capsys.readouterr().out
    run_on("2026-11-02", monkeypatch)
    assert "robots.txt: 0 jobs of skipped companies deleted from the history" in capsys.readouterr().out


def test_a_robots_txt_that_cannot_be_read_deletes_nothing(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_walled("Walled")
    write_json(workdir / "data" / "companies.json", [co("Walled")])
    common._robots["https://walled.test"] = None  # a timeout, a 4xx or a 5xx: no rules, so allowed and crawled
    run_on("2026-11-01", monkeypatch)
    assert rows("SELECT job_id, status FROM jobs ORDER BY job_id") == [("1", "open"), ("old", "closed")]
    assert rows("SELECT note FROM crawl_log") == [("",)]


def test_a_failure_deletes_nothing(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run.time, "sleep", lambda _s: None)
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda _slug: None)  # fails: no robots involved
    seed_walled("Down")
    write_json(workdir / "data" / "companies.json", [co("Down")])
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT status FROM jobs") == [("open",)]


def test_a_company_crawled_this_run_keeps_its_jobs_even_when_a_namesake_board_is_skipped(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_walled("Twin")
    write_json(workdir / "data" / "companies.json", [co("Twin", slug="walled"), co("Twin", slug="fine")])
    common._robots["https://walled.test"] = [(False, "/")]
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT job_id, status FROM jobs ORDER BY job_id") == [("1", "open"), ("old", "closed")]


def test_a_name_with_one_board_skipped_and_another_failing_keeps_its_jobs(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "_s", RobotsSession({}))
    monkeypatch.setattr(run.time, "sleep", lambda _s: None)
    common._robots["https://walled.test"] = [(False, "/")]
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: common.get(f"https://{slug}.test/x") and None)
    seed_walled("Twin")
    write_json(workdir / "data" / "companies.json", [co("Twin", slug="walled"), co("Twin", slug="down")])
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT status FROM jobs") == [("open",)]


def test_a_later_crawl_brings_the_jobs_back_when_the_host_allows_us_again(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_walled("Walled")
    write_json(workdir / "data" / "companies.json", [co("Walled")])
    common._robots["https://walled.test"] = [(False, "/")]
    run_on("2026-11-01", monkeypatch)
    assert rows("SELECT COUNT(*) FROM jobs") == [(0,)]
    common._robots["https://walled.test"] = None
    run_on("2026-11-02", monkeypatch)
    assert rows("SELECT job_id, status FROM jobs") == [("1", "open")]


def test_a_browser_company_skipped_for_robots_loses_its_jobs(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://br.test/careers"
    seed_walled("Br")
    write_json(workdir / "data" / "companies.json", [co("Br", ats="browser", slug=url)])
    monkeypatch.setattr(
        run,
        "crawl_many",
        lambda urls: browser_crawl.WHY.update(dict.fromkeys(urls, ROBOTS_NOTE)) or dict.fromkeys(urls),
    )
    run_on("2026-11-08", monkeypatch)
    assert rows("SELECT COUNT(*) FROM jobs") == [(0,)]


def test_switching_enforcement_off_deletes_nothing(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_walled("Walled")
    write_json(workdir / "data" / "companies.json", [co("Walled")])
    monkeypatch.setattr(common, "ROBOTS_ENFORCED", False)
    common._robots["https://walled.test"] = [(False, "/")]
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT job_id, status FROM jobs ORDER BY job_id") == [("1", "open"), ("old", "closed")]  # a crawl


def test_a_company_that_left_the_list_keeps_its_jobs(
    workdir: Path, blocked_adapter: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_walled("Gone", "Here")
    write_json(workdir / "data" / "companies.json", [co("Gone", status="blocked"), co("Here")])
    common._robots["https://here.test"] = [(False, "/")]
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT status FROM jobs WHERE company='Gone'") == [("open",)]
    assert rows("SELECT COUNT(*) FROM jobs WHERE company='Here'") == [(0,)]


def test_a_held_crawl_deletes_nothing(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://br.test/careers"
    conn = run.init()
    for i in range(run.DROP_MIN_OPEN):
        add_job(conn, "Br", f"j{i}", "T")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Br", ats="browser", slug=url)])
    monkeypatch.setattr(run, "crawl_many", lambda urls: {key: [] for key in urls})
    run_on("2026-11-20", monkeypatch)
    assert rows("SELECT COUNT(*) FROM jobs WHERE status='open'") == [(run.DROP_MIN_OPEN,)]


def test_delete_robots_skips_counts_the_jobs_it_deletes_and_only_those_of_the_names_it_is_given(
    workdir: Path,
) -> None:
    seed_walled("A", "B")
    conn = run.init()
    add_job(conn, "A", "second", "Another")
    deleted = 2
    assert run.delete_robots_skips(conn, {"A"}) == deleted
    assert conn.execute("SELECT company FROM jobs").fetchall() == [("B",)]
    assert run.delete_robots_skips(conn, {"A"}) == 0


def test_the_query_parameters_are_part_of_the_address_robots_txt_is_asked_about(
    blocked_adapter: list[str],
) -> None:
    common._robots["https://a.test"] = [(False, "/*?country=ISR")]
    assert common.get("https://a.test/jobs", params={"country": "ISR"}) is None
    assert common.robots_blocked() == "a.test"
    common.reset_robots_block()
    assert common.get("https://a.test/jobs", params={"country": "US"}) is not None
    assert common.get("https://a.test/jobs") is not None


def test_a_partly_blocked_adapter_is_a_skip_and_its_jobs_are_deleted(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(common, "_s", RobotsSession({}))
    common._robots["https://paged.test"] = [(False, "/*offset=100")]

    def paged(slug: str) -> Any:  # the first page is allowed, the second is not, and the adapter returns what it has
        common.get(f"https://{slug}.test/jobs")
        common.get(f"https://{slug}.test/jobs", params={"offset": 100})
        return [job("1")]

    monkeypatch.setitem(run.ADAPTERS, "fake", paged)
    conn = run.init()
    add_job(conn, "Paged", "old", "Old job")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Paged")])
    run.main()
    assert "skipped by robots.txt 1 ['Paged']" in capsys.readouterr().out
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)  # a part of the list is not kept either
    check.close()


def test_main_stops_on_too_many_companies_before_crawling_or_deleting(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crawled: list[str] = []
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: crawled.append(slug) or [])
    names = ["A1", "B2", "C3", "D4"]
    conn = run.init()
    for name in names:
        add_job(conn, name, "1", "T")
    add_crawls(conn, "A1", [(1, "")], ats="fake")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co(n) for n in names] + [co("Keep")])
    exclude(workdir, *names)
    with pytest.raises(ExclusionError, match="refusing to delete the jobs of 4 companies"):
        run.main()
    assert crawled == []
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone() == (4,)
    assert check.execute("SELECT COUNT(*) FROM crawl_log").fetchone() == (1,)
    check.close()


def test_a_delete_that_fails_half_way_is_finished_by_the_next_run(local: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    fill(fake, "Acme")
    real = fake.request

    def flaky(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "DELETE" and url.endswith("/jobs"):
            return FakeHttpResponse(status_code=500, text="boom")
        return real(method, url, **kw)

    fake.request = flaky  # type: ignore[method-assign]
    with pytest.raises(sync_supabase.SyncError, match="DELETE jobs failed"):
        sync_supabase.sync(local, supabase_client(fake), ["Acme"])
    assert not fake.others["user_jobs"] and not fake.others["emailed"]  # the stars went first
    assert fake.table  # the jobs are still there, so the next run still sees the company
    assert not [c for c in fake.calls if c["method"] == "POST"]
    rows_left = len(fake.table) + len(fake.others["crawl_log"])  # the jobs and the crawl log are still there
    fake.request = real  # type: ignore[method-assign]
    assert sync_supabase.sync(local, supabase_client(fake), ["Acme"]).purged == rows_left
    assert not [key for key in fake.table if key[0] == "Acme"]


def test_a_local_trial_runs_on_a_touched_empty_file(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`touch data/jobs.db` (README) is a history with no tables yet: the crawl creates them."""
    (workdir / "data" / "jobs.db").unlink()
    (workdir / "data" / "jobs.db").touch()
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda _slug: [job("1")])
    write_json(workdir / "data" / "companies.json", [co("Acme")])
    run.main()
    assert rows("SELECT company, job_id FROM jobs") == [("Acme", "1")]


def test_a_job_page_robots_txt_forbids_gives_no_text_and_the_company_is_still_crawled(
    workdir: Path, robots: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    robots["https://acme.wd3.myworkdayjobs.com"] = FakeHttpResponse(
        text="User-agent: *\nDisallow: /wday/cxs/acme/Site/job/\n"
    )
    monkeypatch.setitem(run.ADAPTERS, "workday", lambda _slug: [job("/job/Haifa/Engineer_R1")])
    write_json(workdir / "data" / "companies.json", [co("Acme", ats="workday", slug="acme/wd3/Site")])
    run.main()
    out = capsys.readouterr().out
    assert "failed 0 []" in out
    assert "skipped by robots.txt 0 []" in out  # a refused job page is not a refused company
    assert "job pages: workday 1 requests, 0 with a text, 0 left for later runs" in out
    assert robots["session"].urls == [
        "https://acme.wd3.myworkdayjobs.com/robots.txt"
    ]  # the page itself was not fetched
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT company, status FROM jobs").fetchall() == [("Acme", "open")]
    check.close()
