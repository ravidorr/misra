import json
import runpy
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

import classify
import common
import descriptions
import export_accounts
import sync_supabase
from sync_supabase import Client, Result, SyncError, sync
from tests.conftest import add_job
from tests.fakes import FakeHttpResponse, FakeSupabase

URL = "https://abc.supabase.co"
KEY = "service-role-key-xyz"
COUNT = 4  # rows a cleanup reports


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
    assert [len(c["json"]) for c in fake.calls if c["method"] == "POST"] == [2, 2, 1]  # the 5 open jobs
    assert sorted(fake.table) == [("Acme", str(n)) for n in range(5)]
    db.execute("UPDATE jobs SET status='closed' WHERE job_id IN ('0','3','4')")
    fake.calls.clear()
    assert sync(db, client(fake)) == Result(upserted=2, closed=3)
    sizes = [len(c["json"]) for c in fake.calls if c["method"] == "POST"]
    assert sizes == [2, 2, 1]  # two rows, then the three closes in batches of two and one
    assert [c["params"]["offset"] for c in fake.calls if c["method"] == "GET" and c["url"].endswith("/jobs")] == [
        0,
        2,
        4,
        5,
    ]  # 2, 2, 1 rows, then an empty page


def test_a_server_cap_below_the_page_size_still_closes_every_stale_job(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase(page=2)  # PAGE stays 1000: every response is short but not the last
    for n in range(5):
        fake.table[("Zed", str(n))] = {"company": "Zed", "job_id": str(n), "status": "open"}
    assert sync(jobs, client(fake)) == Result(upserted=3, closed=5)
    assert {row["status"] for key, row in fake.table.items() if key[0] == "Zed"} == {"closed"}


def test_the_read_asks_for_a_stable_order_and_only_open_rows(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    get = next(c for c in fake.calls if c["method"] == "GET")
    assert get["params"]["order"] == "company.asc,job_id.asc"
    assert get["params"]["select"] == "company,job_id"
    assert get["allow_redirects"] is False  # the key is in a header that a redirect would forward


def test_closed_jobs_are_not_closed_again(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    sync(jobs, client(fake))
    for n in range(3):
        fake.table[("Zed", str(n))] = {"company": "Zed", "job_id": str(n), "status": "closed"}
    fake.calls.clear()
    assert sync(jobs, client(fake)) == Result(upserted=3, closed=0)
    assert not any(c["method"] == "POST" and c["json"][0]["status"] == "closed" for c in fake.calls)


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
    # a failed upsert reads and closes nothing; a failed read closes nothing
    assert [c["method"] for c in fake.calls] == (["POST"] if method == "POST" else ["POST", "GET"])


def test_a_failed_read_after_the_first_page_closes_nothing(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_supabase, "PAGE", 2)
    fake = FakeSupabase(page=2)
    for n in range(3):
        fake.table[("Zed", str(n))] = {"company": "Zed", "job_id": str(n), "status": "open"}
    original = fake.request
    gets: list[int] = []

    def flaky(method: str, url: str, **kw: Any) -> Any:
        if method == "GET":
            gets.append(1)
            if len(gets) > 1:
                return FakeHttpResponse(status_code=503, text="down")
        return original(method, url, **kw)

    fake.request = flaky  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="HTTP 503"):
        sync(jobs, client(fake))
    assert {row["status"] for key, row in fake.table.items() if key[0] == "Zed"} == {"open"}


@pytest.mark.parametrize(("status", "ok"), [(200, True), (299, True), (300, False), (401, False)])
def test_only_2xx_statuses_are_success(status: int, ok: bool) -> None:
    fake = FakeSupabase()
    fake.request = lambda *_a, **_k: FakeHttpResponse(status_code=status, data=[], text="x" * 500)  # type: ignore[method-assign]
    if ok:
        assert client(fake).call("GET", {}).status_code == status
    else:
        with pytest.raises(SyncError, match=f"HTTP {status} x{{300}}$"):
            client(fake).call("GET", {})


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


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"SUPABASE_URL": URL},
        {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": KEY},
        {"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": " \n"},
    ],
)
def test_main_fails_without_the_secrets_it_is_not_a_skip(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, workdir: Any
) -> None:
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match="the history lives in Supabase"):
        sync_supabase.main([])
    with pytest.raises(SystemExit, match="the history lives in Supabase"):
        sync_supabase.main(["--pull"])


def test_main_syncs_and_prints_the_log(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs.commit()
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", f" {URL} ")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", f"{KEY}\n")  # a pasted secret often ends in a newline
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()
    assert fake.calls[0]["headers"]["apikey"] == KEY
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
    monkeypatch.setattr(sys, "argv", ["sync_supabase.py"])
    monkeypatch.delitem(sys.modules, "sync_supabase", raising=False)
    with pytest.raises(SystemExit, match="the history lives in Supabase"):
        runpy.run_module("sync_supabase", run_name="__main__")


def test_main_stops_on_a_bad_exclusion_list_before_it_sends_anything(
    monkeypatch: pytest.MonkeyPatch, workdir: Any
) -> None:
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    (workdir / "data" / "excluded_companies.csv").write_text("company,reason\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="header"):
        sync_supabase.main([])
    assert not fake.calls


# ---- the daily cleanup of the report-site spam-guard counters ----


def test_cleanup_calls_both_database_functions_with_today_and_adds_up_the_counts() -> None:
    fake = FakeSupabase(rpc_answer=COUNT)
    assert sync_supabase.cleanup(client(fake), date(2026, 11, 16)) == 2 * COUNT
    first, second = fake.calls
    assert (first["method"], first["url"]) == ("POST", f"{URL}/rest/v1/rpc/report_site_cleanup")
    assert (second["method"], second["url"]) == ("POST", f"{URL}/rest/v1/rpc/confirm_email_cleanup")
    for call in (first, second):
        assert call["json"] == {"p_day": "2026-11-16"}
        assert call["headers"]["apikey"] == KEY
        assert call["headers"]["Prefer"] == "return=representation"


@pytest.mark.parametrize("answer", [None, "4", [4], True])
def test_cleanup_refuses_an_answer_that_is_not_a_count(answer: Any) -> None:
    with pytest.raises(SyncError, match="something other than a count"):
        sync_supabase.cleanup(client(FakeSupabase(rpc_answer=answer)), date(2026, 11, 16))


def test_cleanup_fails_loudly_when_the_function_is_missing_or_the_call_fails() -> None:
    with pytest.raises(SyncError, match="POST rpc/report_site_cleanup failed: HTTP 500 boom"):
        sync_supabase.cleanup(client(FakeSupabase(fail_on="POST")), date(2026, 11, 16))


def test_a_cleanup_that_fails_on_the_second_function_is_not_hidden_by_the_first() -> None:
    class SecondFails(FakeSupabase):
        def request(self, method: str, url: str, **kw: Any) -> Any:
            if url.endswith("confirm_email_cleanup"):
                self.fail_on = "POST"
            return super().request(method, url, **kw)

    with pytest.raises(SyncError, match="rpc/confirm_email_cleanup"):
        sync_supabase.cleanup(client(SecondFails(rpc_answer=COUNT)), date(2026, 11, 16))


def test_main_cleans_the_counters_after_the_sync_and_logs_the_count(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs.commit()
    fake = FakeSupabase(rpc_answer=7)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()
    rpc = [c for c in fake.calls if "/rpc/" in c["url"]]
    assert (
        len(rpc) == 1 + len(sync_supabase.CLEANUP_FUNCTIONS) and fake.calls[-len(rpc) :] == rpc
    )  # the text trim, then one call per counter family, after the sync
    assert all(call["json"] == {"p_day": datetime.now(UTC).date().isoformat()} for call in rpc)
    assert "Supabase: 14 spam-guard counter rows deleted" in capsys.readouterr().out


def test_main_exits_non_zero_when_the_cleanup_fails_after_a_good_sync(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs.commit()
    fake = FakeSupabase(rpc_answer="not a count")
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    with pytest.raises(SystemExit, match="rpc/report_site_cleanup returned something other than a count"):
        sync_supabase.main()
    assert ("Beta", "9") in fake.table  # the jobs were already synced
    assert "3 open jobs upserted" in capsys.readouterr().out


# ---- the history lives in Supabase (PLAN.md step 9.1): the pull, the crawl log, the retention ----


def log_row(ts: str, company: str = "Acme", ok: int = 1, note: str | None = "") -> tuple[Any, ...]:
    return (ts, company, "greenhouse", ok, 3, note)


def add_log(db: sqlite3.Connection, *rows: tuple[Any, ...], pulled: bool = False) -> None:
    """Crawl log rows of the history: `pulled` ones carry the id Supabase gave them, the others are this run's."""
    db.executemany(
        "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note,id) VALUES(?,?,?,?,?,?,?)",
        [(*row, 7 if pulled else None) for row in rows],
    )
    db.commit()


def test_only_the_crawl_log_rows_of_this_run_are_written_and_then_marked_as_written(jobs: sqlite3.Connection) -> None:
    add_log(jobs, log_row("2026-11-15T04:30:00"), pulled=True)  # what the pull brought: Supabase has it
    add_log(jobs, log_row("2026-11-16T04:30:00", "Beta", 0, "boom"))
    fake = FakeSupabase()
    result = sync(jobs, client(fake))
    assert result.logged == 1
    assert fake.others["crawl_log"] == [
        {"ts": "2026-11-16T04:30:00", "company": "Beta", "ats": "greenhouse", "ok": 0, "n_jobs": 3, "note": "boom"}
    ]
    assert sync(jobs, client(fake)).logged == 0  # a second sync of the same file writes nothing more
    assert len(fake.others["crawl_log"]) == 1
    assert jobs.execute("SELECT COUNT(*) FROM crawl_log WHERE id IS NULL").fetchone() == (0,)


def test_a_log_row_in_supabase_with_any_ts_cannot_hold_back_this_runs_rows(jobs: sqlite3.Connection) -> None:
    """The rows to write are the ones with no id, not the ones newer than something: a wrong clock or a stray row in
    Supabase must not silence the log."""
    add_log(jobs, log_row("2026-11-16T04:30:00"))
    fake = FakeSupabase()
    fake.others["crawl_log"].append({"ts": "zzz-from-the-future", "company": "Acme"})
    assert sync(jobs, client(fake)).logged == 1


def test_a_crawl_log_goes_in_batches(jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_supabase, "BATCH", 2)
    rows_written = [log_row(f"2026-11-16T04:3{n}:00") for n in range(5)]
    add_log(jobs, *rows_written)
    fake = FakeSupabase()
    assert sync(jobs, client(fake)).logged == len(rows_written)
    posts = [c for c in fake.calls if c["method"] == "POST" and c["url"].endswith("/crawl_log")]
    assert [len(c["json"]) for c in posts] == [2, 2, 1]
    assert all(c["headers"]["Prefer"] == "return=minimal" for c in posts)  # no conflict key: a plain insert


def test_the_crawl_log_is_written_after_the_jobs(jobs: sqlite3.Connection) -> None:
    add_log(jobs, log_row("2026-11-16T04:30:00"))
    fake = FakeSupabase()
    sync(jobs, client(fake))
    urls = [c["url"].rsplit("/", 1)[1] for c in fake.calls]
    assert urls.index("crawl_log") > max(i for i, url in enumerate(urls) if url == "jobs")


def test_a_crawl_log_that_cannot_be_written_fails_the_sync(jobs: sqlite3.Connection) -> None:
    add_log(jobs, log_row("2026-11-16T04:30:00"))
    fake = FakeSupabase()
    real = fake.request

    def refuse(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "POST" and url.endswith("/crawl_log"):
            return FakeHttpResponse(status_code=400, text="column note does not exist")
        return real(method, url, **kw)

    fake.request = refuse  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="POST crawl_log failed: HTTP 400"):
        sync(jobs, client(fake))
    assert ("Acme", "1") in fake.table  # the jobs were stored before it
    assert jobs.execute("SELECT COUNT(*) FROM crawl_log WHERE id IS NULL").fetchone() == (1,)  # still to be written


def test_the_old_crawl_log_is_deleted_by_day_and_a_failure_is_only_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeSupabase()
    day = date(2026, 11, 16)
    cutoff = (day - timedelta(days=sync_supabase.CRAWL_LOG_DAYS)).isoformat()
    fake.others["crawl_log"] = [{"ts": f"{cutoff}T23:59:00"}, {"ts": "2026-01-01T00:00:00"}, {"ts": "2026-11-16T04:30"}]
    sync_supabase.trim_crawl_log(client(fake), day)
    assert [row["ts"] for row in fake.others["crawl_log"]] == [f"{cutoff}T23:59:00", "2026-11-16T04:30"]
    assert fake.calls[0]["params"] == {"ts": f"lt.{cutoff}"}
    failing = FakeSupabase(fail_on="DELETE")
    sync_supabase.trim_crawl_log(client(failing), day)
    assert (
        "::warning::the old crawl log could not be trimmed: DELETE crawl_log failed: HTTP 500"
        in capsys.readouterr().out
    )


def test_the_exclusion_list_deletes_the_crawl_log_of_the_company_too(jobs: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    fake.others["crawl_log"] = [
        {"ts": "2026-11-16T04:30", "company": "Acme"},
        {"ts": "2026-11-16T04:30", "company": "Beta"},
    ]
    sync(jobs, client(fake), ["Acme"])
    assert [row["company"] for row in fake.others["crawl_log"]] == ["Beta"]


def test_main_writes_the_log_and_trims_it(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_log(jobs, log_row("2026-11-16T04:30:00"))
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main([])
    assert "Supabase: 1 crawl log rows written" in capsys.readouterr().out
    assert any(c["method"] == "DELETE" and c["params"].get("ts", "").startswith("lt.") for c in fake.calls)


# the pull


class Pages:
    """PostgREST for the pull: the export's `Source` reads ordered pages with a Content-Range total."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kw: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kw})
        rows = self.tables[url.rsplit("/", 1)[1]]
        offset, limit = kw["params"]["offset"], kw["params"]["limit"]
        window = rows[offset : offset + limit]
        return type(
            "Answer",
            (),
            {
                "status_code": 200,
                "headers": {"Content-Range": f"{offset}-{offset + len(window) - 1}/{len(rows)}"},
                "json": lambda self_: window,
            },
        )()


def job_row(company: str, job_id: str, status: str = "open", **more: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(export_accounts.JOBS.columns)
    return {**row, "company": company, "job_id": job_id, "title": "T", "status": status, "role_types": [], **more}


def log_json(n: int, ts: str, company: str = "Acme", note: str | None = None) -> dict[str, Any]:
    return {"id": n, "ts": ts, "company": company, "ats": "lever", "ok": 1, "n_jobs": 4, "note": note}


def pull_from(tables: dict[str, list[dict[str, Any]]], path: Any, allow_shrink: bool = False) -> tuple[int, int]:
    source = export_accounts.Source(Pages(tables), URL, KEY)  # type: ignore[arg-type]
    return sync_supabase.pull(source, str(path), allow_shrink)


@pytest.fixture(autouse=True)
def published(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for docs/jobs.json that publishes no job, so no pull is compared with the repository's real file."""
    path = tmp_path_factory.mktemp("published") / "jobs.json"
    path.write_text('{"jobs": []}', encoding="utf-8")
    monkeypatch.setattr(sync_supabase, "PUBLISHED", str(path))
    return path


def publish_count(n: int | str) -> None:
    """The dashboard's last published jobs: n jobs, or the text given."""
    text = n if isinstance(n, str) else json.dumps({"jobs": [{}] * n})
    Path(sync_supabase.PUBLISHED).write_text(text, encoding="utf-8")


def test_the_pull_rebuilds_the_history_with_the_closed_jobs_and_the_whole_log(tmp_path: Any) -> None:
    tables = {
        "jobs": [job_row("Acme", "1", first_seen="2026-11-01T04:00:00"), job_row("Acme", "0", "closed", url="u")],
        "crawl_log": [log_json(1, "2026-11-15T04:00:00", note="x"), log_json(2, "2026-11-16T04:00:00")],
    }
    path = tmp_path / "jobs.db"
    assert pull_from(tables, path) == (2, 2)
    check = sqlite3.connect(path)
    assert check.execute("SELECT company, job_id, status, first_seen, url FROM jobs ORDER BY job_id").fetchall() == [
        ("Acme", "0", "closed", None, "u"),
        ("Acme", "1", "open", "2026-11-01T04:00:00", None),
    ]
    assert check.execute("SELECT ts, ats, ok, n_jobs, note, id FROM crawl_log ORDER BY ts").fetchall() == [
        ("2026-11-15T04:00:00", "lever", 1, 4, "x", 1),  # every pulled row keeps its Supabase id
        ("2026-11-16T04:00:00", "lever", 1, 4, None, 2),
    ]
    assert not (tmp_path / "jobs.db.part").exists()


def test_the_history_never_holds_the_text_of_a_posting(tmp_path: Any) -> None:
    pages = Pages({"jobs": [job_row("Acme", "1")], "crawl_log": []})
    sync_supabase.pull(export_accounts.Source(pages, URL, KEY), str(tmp_path / "jobs.db"))  # type: ignore[arg-type]
    asked = pages.calls[0]["params"]["select"].split(",")
    assert not {"description", "details", "snippet"} & set(asked)
    columns = [r[1] for r in sqlite3.connect(tmp_path / "jobs.db").execute("PRAGMA table_info(jobs)")]
    assert not {"description", "details", "snippet"} & set(columns)


def test_an_empty_jobs_table_is_refused_and_the_old_file_is_left_alone(tmp_path: Any) -> None:
    path = tmp_path / "jobs.db"
    path.write_text("previous", encoding="utf-8")
    with pytest.raises(SyncError, match="Supabase holds no jobs"):
        pull_from({"jobs": [], "crawl_log": [log_json(1, "2026-11-16T04:00:00")]}, path)
    assert path.read_text(encoding="utf-8") == "previous"
    assert not (tmp_path / "jobs.db.part").exists()


def test_an_empty_crawl_log_is_fine_the_first_days(tmp_path: Any) -> None:
    assert pull_from({"jobs": [job_row("Acme", "1")], "crawl_log": []}, tmp_path / "jobs.db") == (1, 0)


def test_a_stale_scratch_file_and_a_previous_history_are_replaced_whole(tmp_path: Any) -> None:
    path = tmp_path / "jobs.db"
    (tmp_path / "jobs.db.part").write_text("left behind", encoding="utf-8")
    pull_from({"jobs": [job_row("Old", "1")], "crawl_log": []}, path)
    pull_from({"jobs": [job_row("New", "1")], "crawl_log": []}, path)
    assert sqlite3.connect(path).execute("SELECT company FROM jobs").fetchall() == [("New",)]


def test_a_table_that_cannot_be_read_leaves_no_file(tmp_path: Any) -> None:
    class Down:
        def request(self, *_a: Any, **_k: Any) -> Any:
            raise requests.ConnectionError("down")

    with pytest.raises(export_accounts.ExportError, match="reading jobs failed"):
        sync_supabase.pull(export_accounts.Source(Down(), URL, KEY), str(tmp_path / "jobs.db"))  # type: ignore[arg-type]
    assert not list(tmp_path.iterdir())


def test_main_pull_writes_the_file_and_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Any
) -> None:
    (workdir / "data" / "jobs.db").unlink()
    pages = Pages({"jobs": [job_row("Acme", "1")], "crawl_log": [log_json(1, "2026-11-16T04:00:00")]})
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: pages)
    sync_supabase.main(["--pull"])
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "Supabase: pulled 1 jobs and 1 crawl log rows into data/jobs.db",
        "Supabase: 0 open jobs already hold a description",
    ]
    assert (workdir / "data" / "jobs.db").exists()
    assert pages.calls[0]["headers"]["apikey"] == KEY
    assert KEY not in out


def test_main_pull_fails_with_the_reason_and_no_secret(monkeypatch: pytest.MonkeyPatch, workdir: Any) -> None:
    (workdir / "data" / "jobs.db").unlink()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: Pages({"jobs": [], "crawl_log": []}))
    with pytest.raises(SystemExit) as info:
        sync_supabase.main(["--pull"])
    assert str(info.value).startswith("Supabase pull failed: Supabase holds no jobs")
    assert KEY not in str(info.value)
    assert not (workdir / "data" / "jobs.db").exists()


def test_the_crawl_and_the_dashboard_refuse_a_missing_history(workdir: Any) -> None:
    (workdir / "data" / "jobs.db").unlink()
    with pytest.raises(SystemExit, match=r"data/jobs\.db is missing: run `python sync_supabase\.py --pull`"):
        sync_supabase.open_history()
    with pytest.raises(SystemExit, match="is missing"):
        common.open_history()
    assert not (workdir / "data" / "jobs.db").exists()  # looking never creates an empty one


def test_the_pull_marks_its_file_and_the_sync_refuses_any_other(
    monkeypatch: pytest.MonkeyPatch, workdir: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """`touch data/jobs.db` and a crawl give a history without Supabase's first_seen values: syncing it would
    overwrite them, and the digest would email every job as new."""
    pull_from({"jobs": [job_row("Acme", "1")], "crawl_log": []}, workdir / "data" / "pulled.db")
    marked = sqlite3.connect(workdir / "data" / "pulled.db")
    assert marked.execute("PRAGMA user_version").fetchone() == (common.PULLED,)
    fake = FakeSupabase()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    plain = sqlite3.connect(workdir / "data" / "jobs.db")  # as `touch` and a local crawl leave it
    plain.execute("PRAGMA user_version = 0")
    plain.commit()
    with pytest.raises(SystemExit, match=r"data/jobs\.db was not written by --pull"):
        sync_supabase.main([])
    assert not fake.calls  # nothing was sent


FIRST_BATCH, SECOND, REST, TOTAL = 2, 2, 3, 5


def test_a_crawl_log_that_fails_half_way_is_finished_by_the_next_sync_without_a_duplicate(
    jobs: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_supabase, "BATCH", 2)
    add_log(jobs, *(log_row(f"2026-11-16T04:3{n}:00") for n in range(5)))
    fake = FakeSupabase()
    real = fake.request
    posts = {"n": 0}

    def second_fails(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "POST" and url.endswith("/crawl_log"):
            posts["n"] += 1
            if posts["n"] == SECOND:
                return FakeHttpResponse(status_code=500, text="boom")
        return real(method, url, **kw)

    fake.request = second_fails  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="POST crawl_log failed"):
        sync(jobs, client(fake))
    assert len(fake.others["crawl_log"]) == FIRST_BATCH  # the first batch went and is marked
    fake.request = real  # type: ignore[method-assign]
    assert sync(jobs, client(fake)).logged == REST
    assert len(fake.others["crawl_log"]) == TOTAL


def test_a_pull_with_under_half_of_the_published_open_jobs_is_refused_and_writes_nothing(
    tmp_path: Any, workdir: Any
) -> None:
    publish_count(11)
    rows = [job_row("Acme", str(n), "open" if n % 2 == 0 else "closed") for n in range(9)]  # 5 open, 4 closed
    path = tmp_path / "jobs.db"
    with pytest.raises(
        sync_supabase.SyncError, match=r"holds 5 open jobs and the dashboard last published 11.*--allow"
    ):
        pull_from({"jobs": rows, "crawl_log": []}, path)
    assert not path.exists()
    assert pull_from({"jobs": rows, "crawl_log": []}, path, allow_shrink=True) == (9, 0)  # a real loss, on purpose


def test_a_pull_with_half_or_more_of_the_published_jobs_or_too_few_to_judge_is_accepted(
    tmp_path: Any, workdir: Any
) -> None:
    publish_count(10)
    five = [job_row("Acme", str(n)) for n in range(5)]
    assert pull_from({"jobs": five, "crawl_log": []}, tmp_path / "a.db") == (5, 0)  # exactly half
    publish_count(4)
    assert pull_from({"jobs": five[:1], "crawl_log": []}, tmp_path / "b.db") == (
        1,
        0,
    )  # under 5 published: no yardstick


def test_a_pull_without_a_readable_published_file_fails_closed_unless_the_check_is_skipped(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    tables = {"jobs": [job_row("Acme", "1")], "crawl_log": []}
    for text in (None, "not json", "[]", '{"jobs": 3}'):
        if text is None:
            Path(sync_supabase.PUBLISHED).unlink()
        else:
            publish_count(text)
        with pytest.raises(sync_supabase.SyncError, match=r"could not be read.*--allow-shrink"):
            pull_from(tables, tmp_path / "x.db")
        assert not (tmp_path / "x.db").exists()
    assert pull_from(tables, tmp_path / "y.db", allow_shrink=True) == (1, 0)  # the override does not look at the file
    assert capsys.readouterr().out == ""


def test_main_pull_warns_when_the_crawl_log_is_empty_and_passes_the_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Any
) -> None:
    (workdir / "data" / "jobs.db").unlink()
    publish_count(100)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    pages = Pages({"jobs": [job_row("Acme", "1")], "crawl_log": []})
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: pages)
    with pytest.raises(SystemExit, match="refusing to crawl from a history that lost more than half"):
        sync_supabase.main(["--pull"])
    sync_supabase.main(["--pull", "--allow-shrink"])  # an empty log is a warning with no row counts, never a failure
    assert capsys.readouterr().out.splitlines() == [
        "Supabase: pulled 1 jobs and 0 crawl log rows into data/jobs.db",
        "Supabase: 0 open jobs already hold a description",
        "::warning::the crawl log is empty: robots.txt streaks and held drops start again",
    ]


def test_the_pull_leaves_the_keys_of_the_jobs_that_hold_a_text_and_never_the_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Any
) -> None:
    (workdir / "data" / "jobs.db").unlink()
    fake = FakeSupabase()
    fake.table = {
        ("Acme", "1"): {"status": "open", "details": {descriptions.HASH_KEY: "abc"}, "description": "SECRET-TEXT"},
        ("Acme", "2"): {"status": "open"},
        ("Beta", "9"): {"status": "closed", "details": {descriptions.HASH_KEY: "def"}},
    }
    descriptions.write_held([("Old", "1")])  # an earlier run's file is replaced, never kept
    assert sync_supabase.leave_held(client(fake)) == 1
    assert descriptions.read_held() == {("Acme", "1")}
    assert "SECRET-TEXT" not in descriptions.work_path(descriptions.HELD_FILE).read_text(encoding="utf-8")
    assert capsys.readouterr().out == ""


def test_the_pull_goes_on_without_the_keys_when_they_cannot_be_listed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    descriptions.write_held([("Old", "1")])
    assert sync_supabase.leave_held(client(FakeSupabase(fail_on="GET"))) is None
    assert descriptions.read_held() == set()  # a stale file is gone: nothing is held that is not known
    assert (
        "::warning::the stored descriptions could not be listed for the crawl: GET jobs failed: HTTP 500"
        in capsys.readouterr().out
    )
    descriptions.work_path(descriptions.HELD_FILE).mkdir()  # a file that cannot be written
    assert sync_supabase.leave_held(client(FakeSupabase())) is None
    assert "::warning::an older list of stored descriptions could not be removed: " in capsys.readouterr().out


def test_main_pull_stays_quiet_about_the_held_count_when_the_keys_could_not_be_listed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Any
) -> None:
    (workdir / "data" / "jobs.db").unlink()
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    monkeypatch.setattr(
        sync_supabase.requests,
        "Session",
        lambda: Pages({"jobs": [job_row("A", "1")], "crawl_log": [log_json(1, "2026-11-16T04:00:00")]}),
    )
    monkeypatch.setattr(sync_supabase, "leave_held", lambda _client: None)
    sync_supabase.main(["--pull"])
    assert capsys.readouterr().out.splitlines() == ["Supabase: pulled 1 jobs and 1 crawl log rows into data/jobs.db"]


def test_an_older_held_file_that_cannot_be_removed_is_a_warning_and_no_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(Path, "unlink", refuse)
    assert sync_supabase.leave_held(client(FakeSupabase())) is None
    assert (
        "::warning::an older list of stored descriptions could not be removed: PermissionError"
        in capsys.readouterr().out
    )
