import runpy
import sqlite3
import sys
from typing import Any

import pytest
import requests

import classify
import sync_supabase
from sync_supabase import Client, Result, SyncError, sync
from tests.conftest import add_job
from tests.fakes import FakeSupabase

URL = "https://abc.supabase.co"
KEY = "service-role-key-xyz"


def client(fake: FakeSupabase) -> Client:
    return Client(fake, URL, KEY)  # type: ignore[arg-type]


@pytest.fixture
def jobs(db: sqlite3.Connection) -> sqlite3.Connection:
    add_job(db, "Acme", "1", "Product Manager Intern", location="Tel Aviv, Israel")
    add_job(db, "Acme", "2", "Senior Backend Engineer", location="Remote, US")
    add_job(db, "Beta", "9", "Junior Data Scientist", location="Haifa; Remote, Israel")
    add_job(db, "Beta", "old", "Gone", status="closed")
    return db


def test_upserts_every_open_job_with_its_classification(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    assert sync(jobs, client(fake)) == Result(upserted=3, closed=0)
    assert sorted(fake.table) == [("Acme", "1"), ("Acme", "2"), ("Beta", "9")]  # the closed job is not sent
    intern = fake.table[("Acme", "1")]
    assert intern["level"] == "Intern"
    assert intern["role_types"] == classify.role_types("Product Manager Intern")
    assert (intern["regions"], intern["locations"], intern["status"]) == (["Tel Aviv area"], [], "open")
    assert intern["posted"] is None  # "" in SQLite
    foreign = fake.table[("Acme", "2")]  # outside Israel: kept, with no region
    assert (foreign["regions"], foreign["locations"], foreign["level"]) == ([], [], "Not specified")
    assert fake.table[("Beta", "9")]["regions"] == ["Haifa and the north", "Remote"]


def test_rows_have_identical_keys_and_never_carry_description_or_details(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    (call,) = fake.calls[:1]
    assert len({tuple(sorted(row)) for row in call["json"]}) == 1
    assert not {"description", "details"} & set(call["json"][0])


def test_request_shape_and_key_use(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    post, get = fake.calls[0], fake.calls[1]
    assert post["url"] == f"{URL}/rest/v1/jobs"
    assert post["params"] == {"on_conflict": "company,job_id"}
    assert post["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert post["headers"]["apikey"] == KEY
    assert post["headers"]["Authorization"] == f"Bearer {KEY}"
    assert get["method"] == "GET"
    assert (get["params"]["status"], get["params"]["limit"]) == ("eq.open", 1000)
    assert "Prefer" not in get["headers"]
    assert get["timeout"] == sync_supabase.TIMEOUT


def test_a_repeat_run_changes_nothing(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    before = fake.snapshot()
    fake.calls.clear()
    assert sync(jobs, client(fake)) == Result(upserted=3, closed=0)
    assert fake.snapshot() == before
    assert not any(c["method"] == "POST" and c["json"] and c["json"][0].get("status") == "closed" for c in fake.calls)


def test_jobs_that_are_no_longer_open_are_closed_and_a_second_run_closes_none(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    jobs.execute("UPDATE jobs SET status='closed' WHERE job_id='2'")
    fake.table[("Zed", "ghost")] = {"company": "Zed", "job_id": "ghost", "status": "open", "title": "Kept"}
    assert sync(jobs, client(fake)) == Result(upserted=2, closed=2)
    assert fake.table[("Acme", "2")]["status"] == "closed"
    assert fake.table[("Zed", "ghost")] == {"company": "Zed", "job_id": "ghost", "status": "closed", "title": "Kept"}
    assert fake.table[("Acme", "2")]["title"] == "Senior Backend Engineer"  # closing leaves the other columns
    assert sync(jobs, client(fake)) == Result(upserted=2, closed=0)


def test_a_job_that_reopens_is_open_again(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    jobs.execute("UPDATE jobs SET status='closed' WHERE job_id='2'")
    sync(jobs, client(fake))
    jobs.execute("UPDATE jobs SET status='open' WHERE job_id='2'")
    sync(jobs, client(fake))
    assert fake.table[("Acme", "2")]["status"] == "open"


def test_large_runs_go_in_batches_and_read_every_page(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_supabase, "BATCH", 2)
    monkeypatch.setattr(sync_supabase, "PAGE", 2)
    for n in range(5):
        add_job(db, "Acme", str(n), "Designer")
    fake = FakeSupabase(page=2)
    sync(db, client(fake))
    db.execute("UPDATE jobs SET status='closed' WHERE job_id IN ('0','3','4')")
    fake.calls.clear()
    assert sync(db, client(fake)) == Result(upserted=2, closed=3)
    sizes = [len(c["json"]) for c in fake.calls if c["method"] == "POST"]
    assert sizes == [2, 2, 1]  # two rows, then the three closes in batches of two and one
    assert [c["params"]["offset"] for c in fake.calls if c["method"] == "GET"] == [0, 2, 4]  # 5 rows: 2, 2, 1


def test_an_empty_local_database_refuses_to_close_everything(db: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    with pytest.raises(SyncError, match="refusing to close"):
        sync(db, client(fake))
    assert fake.calls == []


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_an_http_error_is_a_sync_error(jobs: sqlite3.Connection, method: str) -> None:
    fake = FakeSupabase(fail_on=method)
    with pytest.raises(SyncError, match=f"{method} jobs failed: HTTP 500 boom"):
        sync(jobs, client(fake))


def test_a_network_error_is_a_sync_error_without_the_message_of_the_exception(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase(error=requests.ConnectionError(f"cannot reach {URL} with {KEY}"))
    with pytest.raises(SyncError) as info:
        sync(jobs, client(fake))
    assert str(info.value) == "POST jobs failed: ConnectionError"
    assert KEY not in str(info.value)


def test_an_unexpected_read_response_is_a_sync_error(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    original = fake.request

    def odd(method: str, url: str, **kw: Any) -> Any:
        response = original(method, url, **kw)
        if method == "GET":
            response._data = {"message": "nope"}
        return response

    fake.request = odd  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="other than a list"):
        sync(jobs, client(fake))


def test_the_url_must_be_https_and_a_trailing_slash_is_dropped() -> None:
    with pytest.raises(SyncError, match="https"):
        Client(FakeSupabase(), "http://abc.supabase.co", KEY)  # type: ignore[arg-type]
    assert client_base(f"{URL}/") == f"{URL}/rest/v1/jobs"


def client_base(url: str) -> str:
    return Client(FakeSupabase(), url, KEY).base  # type: ignore[arg-type]


def test_quality_report_has_both_shares(jobs: sqlite3.Connection) -> None:
    report = sync_supabase.quality_report(jobs)
    assert "Role types: " in report
    assert "jobs in Israel are in a region" in report


@pytest.mark.parametrize("env", [{}, {"SUPABASE_URL": URL}, {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": KEY}])
def test_main_is_skipped_cleanly_without_the_secrets(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Any
) -> None:
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sync_supabase.main()
    assert capsys.readouterr().out.strip() == sync_supabase.SKIP_MESSAGE


def test_main_syncs_and_prints_the_log(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs.commit()
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", f" {URL} ")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()
    out = capsys.readouterr().out
    assert "Supabase: 3 open jobs upserted, 0 closed" in out
    assert "Role types: " in out
    assert KEY not in out
    assert ("Beta", "9") in fake.table


def test_main_exits_non_zero_when_the_sync_fails(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs.commit()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: FakeSupabase(fail_on="POST"))
    with pytest.raises(SystemExit) as info:
        sync_supabase.main()
    assert str(info.value) == "Supabase sync failed: POST jobs failed: HTTP 500 boom"
    assert KEY not in capsys.readouterr().out


def test_running_the_module_is_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.delitem(sys.modules, "sync_supabase", raising=False)
    runpy.run_module("sync_supabase", run_name="__main__")
    assert sync_supabase.SKIP_MESSAGE in capsys.readouterr().out
