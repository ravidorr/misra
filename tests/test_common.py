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
