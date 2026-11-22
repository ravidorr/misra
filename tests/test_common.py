import pytest
import requests

import common
from tests.fakes import FakeHttpResponse


class FakeSession:
    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kw: object) -> FakeHttpResponse:
        self.calls.append((url, kw))
        if self.error:
            raise requests.ConnectionError("down")
        return FakeHttpResponse()

    def post(self, url: str, **kw: object) -> FakeHttpResponse:
        self.calls.append((url, {"post": True, **kw}))
        if self.error:
            raise requests.ConnectionError("down")
        return FakeHttpResponse()


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    s = FakeSession()
    monkeypatch.setattr(common, "_s", s)
    monkeypatch.setattr(common, "_last", {})
    monkeypatch.setattr(common, "DELAY", 1.0)  # the conftest turns the delay off
    return s


def test_get_sets_default_timeout_and_throttles(session: FakeSession, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    assert common.get("https://a.test/x") is not None
    assert sleeps == []
    common.get("https://a.test/y", timeout=5)
    assert len(sleeps) == 1
    assert session.calls[0][1] == {"timeout": 20}
    assert session.calls[1][1] == {"timeout": 5}


def test_get_returns_none_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_s", FakeSession(error=True))
    monkeypatch.setattr(common, "_last", {})
    assert common.get("https://a.test/x") is None


def test_post_sends_the_body_with_the_default_timeout_and_shares_the_throttle(
    session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    assert common.get("https://a.test/x") is not None
    assert common.post("https://a.test/y", json={"k": 1}) is not None  # the same host: it waits like a second get
    assert len(sleeps) == 1
    assert session.calls[1] == ("https://a.test/y", {"post": True, "json": {"k": 1}, "timeout": 20})


def test_post_returns_none_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_s", FakeSession(error=True))
    monkeypatch.setattr(common, "_last", {})
    assert common.post("https://a.test/x", json={}) is None


def test_slug_candidates_dedupes_and_skips_empty() -> None:
    assert common.slug_candidates("Acme", "acme.com") == ["acme", "acmehq", "acmeinc"]
    assert common.slug_candidates("Foo Bar!", "foo.io") == ["foo", "foobar", "foo-bar", "foohq", "fooinc"]
    assert common.slug_candidates("!!!", ".com") == ["hq", "inc"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://acme.test/jobs", "https://acme.test/jobs"),
        ("HTTP://Acme.test/Jobs?x=http://y", "https://Acme.test/Jobs?x=http://y"),
        ("https://acme.test/jobs", "https://acme.test/jobs"),
        ("", ""),
        ("//acme.test/jobs", "//acme.test/jobs"),
        ("ftp://acme.test", "ftp://acme.test"),
    ],
)
def test_https_only_changes_only_the_scheme_http_to_https(url: str, expected: str) -> None:
    assert common.https_only(url) == expected


@pytest.mark.parametrize(
    "place",
    [
        "Ashkelon", "Ashqelon, Israel", "Kiryat Gat", "Qiryat Shmona", "Yokneam", "Yoqneam Illit", "Petah Tikva",
        "Petach Tikva", "Be'er Sheva", "Beer Sheva", "Dimona", "Ramat Gan", "Herzliya", "Rehovot", "Nes Ziona", "Lod",
        "Modiin", "Modi'in", "Kfar Saba", "Hod Hasharon", "Netanya", "Haifa", "Caesarea", "Misgav", "Karmiel", "Eilat",
        "Ashdod", "Givatayim", "Bnei Brak", "Bat Yam", "Rosh Ha'ayin", "Hadera", "Afula", "Tiberias",
    ],
)  # fmt: skip
def test_israeli_cities_and_their_spellings_are_israel(place: str) -> None:
    assert common.ISRAEL.search(place)


@pytest.mark.parametrize("place", ["London", "Lodz, Poland", "Odessa", "Nazareth, PA, United States"])
def test_other_places_are_not_israel_by_the_new_cities(place: str) -> None:
    assert not common.ISRAEL.search(place)


class ScriptedSession:
    """Answers a request with the next item: a response, or an exception to raise."""

    def __init__(self, *answers: FakeHttpResponse | Exception) -> None:
        self.answers = list(answers)
        self.calls = 0

    def post(self, url: str, **kw: object) -> FakeHttpResponse:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    get = post


def scripted(
    monkeypatch: pytest.MonkeyPatch, *answers: FakeHttpResponse | Exception
) -> tuple[ScriptedSession, list[float]]:
    s = ScriptedSession(*answers)
    sleeps: list[float] = []
    monkeypatch.setattr(common, "_s", s)
    monkeypatch.setattr(common, "_last", {})
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    return s, sleeps


def test_a_request_is_not_repeated_unless_it_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    s, sleeps = scripted(monkeypatch, FakeHttpResponse(status_code=503))
    r = common.post("https://a.test/x")
    assert r is not None
    assert (s.calls, sleeps, common.last_failure()) == (1, [], "HTTP 503")


def test_a_transient_status_is_repeated_after_the_wait_the_server_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    busy = FakeHttpResponse(status_code=429)
    busy.headers["Retry-After"] = "7"
    s, sleeps = scripted(monkeypatch, busy, FakeHttpResponse())
    r = common.post("https://a.test/x", retry=True)
    assert r is not None
    assert (s.calls, sleeps, common.last_failure()) == (2, [7.0], "")


def test_the_wait_is_capped_and_without_a_header_it_doubles_and_there_are_two_repeats_at_most(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long = FakeHttpResponse(status_code=503)
    long.headers["Retry-After"] = "3600"
    s, sleeps = scripted(monkeypatch, long, FakeHttpResponse(status_code=502), FakeHttpResponse(status_code=500))
    r = common.get("https://a.test/x", retry=True)
    assert r is not None
    assert (s.calls, sleeps, common.last_failure()) == (3, [30.0, 10.0], "HTTP 500")


def test_a_timeout_is_repeated_and_a_second_one_is_reported_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    s, sleeps = scripted(monkeypatch, requests.Timeout("slow"), requests.Timeout("slow"), requests.Timeout("slow"))
    assert common.post("https://a.test/x", retry=True) is None
    assert (s.calls, sleeps, common.last_failure()) == (3, [5.0, 10.0], "Timeout")


@pytest.mark.parametrize("answer", [FakeHttpResponse(status_code=403), FakeHttpResponse(status_code=404)])
def test_a_block_or_a_missing_page_is_never_repeated(monkeypatch: pytest.MonkeyPatch, answer: FakeHttpResponse) -> None:
    s, sleeps = scripted(monkeypatch, answer)
    assert common.post("https://a.test/x", retry=True) is answer
    assert (s.calls, sleeps, common.last_failure()) == (1, [], f"HTTP {answer.status_code}")


def test_a_connection_error_is_not_repeated_and_is_reported_by_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    s, sleeps = scripted(monkeypatch, requests.ConnectionError("down"))
    assert common.post("https://a.test/x", retry=True) is None
    assert (s.calls, sleeps, common.last_failure()) == (1, [], "ConnectionError")
    common.reset_robots_block()
    assert common.last_failure() == ""


def paced(monkeypatch: pytest.MonkeyPatch, gap: float = 2.0, budget: float = 180.0) -> None:
    monkeypatch.setattr(common, "PACES", {"workday": common.Pace(gap=gap, retry_after_max=60.0, budget=budget)})


def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frozen clock, so the turns of a test do not depend on how fast it runs."""
    monkeypatch.setattr(common.time, "time", lambda: 1000.0)


def test_pooled_requests_to_different_hosts_wait_for_one_shared_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    paced(monkeypatch)
    clock(monkeypatch)
    s, sleeps = scripted(monkeypatch, *[FakeHttpResponse()] * 3)
    for host in ("a.test", "b.test", "c.test"):
        assert common.post(f"https://{host}/x", pool="workday") is not None
    assert (s.calls, sleeps) == (3, [2.0, 4.0])  # the first goes at once, then one every gap, whatever the host
    assert "workday 3 requests" in common.pool_report()


def test_an_unpooled_request_ignores_the_pace(monkeypatch: pytest.MonkeyPatch) -> None:
    paced(monkeypatch)
    clock(monkeypatch)
    s, sleeps = scripted(monkeypatch, *[FakeHttpResponse()] * 2)
    common.post("https://a.test/x", pool="workday")
    common.post("https://b.test/x")
    assert (s.calls, sleeps) == (2, [])


def test_a_pooled_refusal_pushes_back_the_whole_group_and_honours_a_long_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paced(monkeypatch)
    clock(monkeypatch)
    busy = FakeHttpResponse(status_code=429)
    busy.headers["Retry-After"] = "45"
    s, sleeps = scripted(monkeypatch, busy, FakeHttpResponse())
    assert common.post("https://a.test/x", retry=True, pool="workday") is not None
    assert (s.calls, sleeps, common.last_failure()) == (2, [47.0], "")  # the 45 s asked for, then the turn
    assert common.reserve_slot("workday", 2.0) > 0  # another board waits behind it too
    assert "45s waited" in common.pool_report()


def test_a_persistent_429_gives_the_host_up_for_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    paced(monkeypatch)
    clock(monkeypatch)
    s, sleeps = scripted(monkeypatch, *[FakeHttpResponse(status_code=429)] * 3, FakeHttpResponse())
    r = common.post("https://a.test/x", retry=True, pool="workday")
    assert r is not None
    assert (s.calls, sleeps) == (3, [7.0, 12.0])  # the try and two repeats, each after the doubled wait and the turn
    assert common.post("https://a.test/y", retry=True, pool="workday") is None  # no request any more
    assert (s.calls, common.last_failure()) == (3, "HTTP 429")
    assert common.post("https://b.test/x", retry=True, pool="workday") is not None  # another host is still asked
    assert "1 hosts not asked again" in common.pool_report()


def test_the_waiting_after_refusals_is_bounded_by_the_group_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    paced(monkeypatch, budget=7.0)
    clock(monkeypatch)
    s, sleeps = scripted(monkeypatch, *[FakeHttpResponse(status_code=503)] * 2)
    r = common.post("https://a.test/x", retry=True, pool="workday")
    assert r is not None
    assert (s.calls, sleeps, common.last_failure()) == (2, [7.0], "HTTP 503")  # 5 s fit the budget, 10 more do not
    assert common._pool_given_up == set()  # a 503 is not a 429: the host is asked again later


def test_a_pooled_timeout_is_repeated_and_never_gives_the_host_up(monkeypatch: pytest.MonkeyPatch) -> None:
    paced(monkeypatch, budget=0.0)
    clock(monkeypatch)
    s, _ = scripted(monkeypatch, requests.Timeout("slow"))
    assert common.post("https://a.test/x", retry=True, pool="workday") is None
    assert (s.calls, common.last_failure(), common._pool_given_up) == (1, "Timeout", set())
