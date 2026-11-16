"""How a description travels (PLAN.md step 7.1): from the adapters through run.py to a work file outside the checkout,
then through sync_supabase.py to Supabase, and never into data/jobs.db, docs/ or any committed file."""

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import descriptions
import run
import send_digests
import sync_supabase
from descriptions import HASH_KEY, fingerprint, read_work_file, write_work_file
from sync_supabase import SyncError, push_texts, sync
from tests.conftest import add_job, write_json
from tests.fakes import FakeHttpResponse, FakeSupabase
from tests.test_sync_supabase import URL, client

SENTINEL = "SENTINEL-posting-text-9f3a"
THREE_JOBS = 3
REPO = Path(__file__).resolve().parent.parent
WORKFLOW = (REPO / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
PUBLISH = (REPO / ".github" / "workflows" / "publish-docs.yml").read_text(encoding="utf-8")


@pytest.fixture
def seeded(db: sqlite3.Connection) -> sqlite3.Connection:
    add_job(db, "Acme", "1", "Product Manager Intern")
    add_job(db, "Acme", "2", "Senior Backend Engineer")
    add_job(db, "Beta", "9", "Junior Data Scientist")
    add_job(db, "Beta", "old", "Gone", status="closed")
    return db


def posting(job_id: str, text: str | None = SENTINEL, **kw: Any) -> dict[str, Any]:
    job: dict[str, Any] = {"job_id": job_id, "title": "T", "location": "L", "url": "u", "team": "t", "posted": "p"}
    if text:
        job["description"] = text
        job["details"] = {"employment_type": "Full-time"}
    return {**job, **kw}


@pytest.fixture
def crawl(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    def go(companies: list[dict[str, Any]], results: dict[str, Any]) -> None:
        write_json(workdir / "data" / "companies.json", companies)
        monkeypatch.setitem(run.ADAPTERS, "fake", lambda slug: results[slug])
        run.main()

    return go


def co(name: str, slug: str) -> dict[str, str]:
    return {"name": name, "ats": "fake", "slug": slug, "status": "ok"}


def test_the_crawl_leaves_the_texts_in_the_work_file_and_never_in_the_database(
    workdir: Path, crawl: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    crawl(
        [co("Acme", "acme"), co("Plain", "plain")],
        {"acme": [posting("1"), posting("2", None)], "plain": [posting("p1", None)]},
    )
    assert read_work_file() == {("Acme", "1"): (SENTINEL, {"employment_type": "Full-time"})}
    files = [f for f in workdir.rglob("*") if f.is_file()]  # the database, journal, WAL, docs, anything run.py wrote
    assert any(f.name == "jobs.db" for f in files)
    for path in files:
        assert SENTINEL.encode() not in path.read_bytes()
    check = sqlite3.connect(run.DB)
    columns = {c[1] for c in check.execute("PRAGMA table_info(jobs)")}
    assert not {c for c in columns if "descr" in c or "detail" in c}  # the database has no column for a text
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == THREE_JOBS  # the jobs are stored as always
    check.close()
    out = capsys.readouterr().out
    kept = len(SENTINEL)
    assert f"descriptions: fake 1/3 (33%); all 1/3 (33%); cut at 20000 characters 0; {kept} characters kept" in out
    assert SENTINEL not in out  # the log carries counts, never text


def test_the_work_file_is_outside_the_checkout(workdir: Path, crawl: Any) -> None:
    crawl([co("Acme", "acme")], {"acme": [posting("1")]})
    assert descriptions.work_path().exists()
    assert not descriptions.work_path().is_relative_to(workdir)
    assert not descriptions.work_path().is_relative_to(REPO)
    assert not list(workdir.rglob(descriptions.WORK_FILE + "*"))  # nothing of it anywhere in the project


def test_a_crawl_without_texts_replaces_the_old_work_file(crawl: Any) -> None:
    crawl([co("Acme", "acme")], {"acme": [posting("1")]})
    assert read_work_file()
    crawl([co("Acme", "acme")], {"acme": [posting("1", None)]})
    assert read_work_file() == {}


def test_a_job_whose_site_gave_no_description_is_stored_exactly_as_before(workdir: Path, crawl: Any) -> None:
    crawl([co("Acme", "acme")], {"acme": [posting("1", None)]})
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT title, status FROM jobs").fetchall() == [("T", "open")]
    check.close()
    assert read_work_file() == {}


def test_a_work_file_that_cannot_be_written_is_a_warning_and_the_jobs_are_stored(
    workdir: Path, crawl: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(workdir / "no" / "such" / "directory"))
    crawl([co("Acme", "acme")], {"acme": [posting("1")]})
    out = capsys.readouterr().out
    assert "::warning::the descriptions could not be handed to the sync step, FileNotFoundError" in out
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0] == 1
    check.close()


def test_a_company_that_failed_or_is_on_the_exclusion_list_leaves_no_text(workdir: Path, crawl: Any) -> None:
    (workdir / "data" / "excluded_companies.csv").write_text(
        "company,reason,date_added\nAsked,asked,2026-11-16\n", encoding="utf-8"
    )
    crawl(
        [co("Asked", "asked"), co("Down", "down"), co("Acme", "acme")],
        {"asked": [posting("1")], "down": None, "acme": [posting("2")]},
    )
    assert list(read_work_file()) == [("Acme", "2")]


# ---- the sync ----


def work(rows: dict[tuple[str, str], str]) -> None:
    write_work_file(
        {"company": c, "job_id": j, "description": text, "details": {"employment_type": "Full-time"}}
        for (c, j), text in rows.items()
    )


def test_the_sync_writes_the_description_and_details_with_a_hash_in_their_own_requests(
    seeded: sqlite3.Connection,
) -> None:
    work({("Acme", "1"): "Text one", ("Acme", "2"): "Text two", ("Gone", "9"): "Not open", ("Beta", "old"): "Closed"})
    fake = FakeSupabase()
    result = sync(seeded, client(fake))
    assert (result.texts, result.texts_same, result.texts_failed) == (2, 0, 0)
    one = fake.table[("Acme", "1")]
    assert one["description"] == "Text one"
    assert one["details"] == {
        "employment_type": "Full-time",
        HASH_KEY: fingerprint("Text one", {"employment_type": "Full-time"}),
    }
    assert "description" not in fake.table[("Beta", "9")]  # no text today: nothing sent, so nothing erased
    assert ("Gone", "9") not in fake.table
    assert ("Beta", "old") not in fake.table  # a job that is not open is never sent
    posts = [c["json"] for c in fake.calls if c["method"] == "POST"]
    assert {frozenset(r) for batch in posts for r in batch if "description" in r} == {
        frozenset({"company", "job_id", "description", "details"})
    }
    assert all(r.get("description") is not None for batch in posts for r in batch if "description" in r)  # never null


def test_a_description_that_did_not_change_is_not_written_again_and_a_changed_one_is(
    seeded: sqlite3.Connection,
) -> None:
    work({("Acme", "1"): "Text one", ("Acme", "2"): "Text two"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    fake.calls.clear()
    assert sync(seeded, client(fake)).texts == 0
    assert not [c for c in fake.calls if c["method"] == "POST" and "description" in c["json"][0]]
    work({("Acme", "1"): "Text one", ("Acme", "2"): "Text two, edited"})
    result = sync(seeded, client(fake))
    assert (result.texts, result.texts_same) == (1, 1)
    assert fake.table[("Acme", "2")]["description"] == "Text two, edited"


def test_a_job_that_closes_keeps_its_text_and_a_run_without_the_work_file_changes_no_text(
    seeded: sqlite3.Connection,
) -> None:
    work({("Acme", "1"): "Text one"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    seeded.execute("UPDATE jobs SET status='closed' WHERE job_id='1'")
    seeded.commit()
    descriptions.work_path().unlink()  # the crawl step did not leave a file
    result = sync(seeded, client(fake))
    assert result.texts == 0
    assert fake.table[("Acme", "1")]["status"] == "closed"
    assert fake.table[("Acme", "1")]["description"] == "Text one"


def test_a_failed_batch_is_a_warning_and_never_quotes_the_answer(
    seeded: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work({("Acme", "1"): "Text one", ("Acme", "2"): "Text two", ("Beta", "9"): "Text three"})
    fake = FakeSupabase()
    monkeypatch.setattr(sync_supabase, "TEXT_BATCH", 1)
    real = fake.request

    def request(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "POST" and kw["json"][0].get("description") == "Text two":
            return FakeHttpResponse(status_code=400, text="Failing row contains (Text two)")
        return real(method, url, **kw)

    fake.request = request  # type: ignore[method-assign]
    result = sync(seeded, client(fake))
    assert (result.texts, result.texts_failed) == (2, 1)  # one bad row costs its batch, not every description
    out = capsys.readouterr().out
    assert "::warning::a batch of 1 descriptions was not written: POST jobs failed: HTTP 400" in out
    assert "Text two" not in out
    assert result.upserted == THREE_JOBS  # the jobs themselves were stored


def test_a_failed_read_of_the_stored_hashes_writes_no_text_and_does_not_stop_the_sync(
    seeded: sqlite3.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    work({("Acme", "1"): "Text one"})
    fake = FakeSupabase()
    real = fake.request

    def request(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "GET" and "hash:" in str(kw["params"].get("select")):
            return FakeHttpResponse(status_code=400, text="no such column")
        return real(method, url, **kw)

    fake.request = request  # type: ignore[method-assign]
    result = sync(seeded, client(fake))
    assert (result.texts, result.texts_failed, result.upserted) == (0, 1, THREE_JOBS)
    assert "::warning::the stored descriptions could not be read" in capsys.readouterr().out


def test_nothing_to_write_asks_nothing(seeded: sqlite3.Connection) -> None:
    fake = FakeSupabase()
    assert push_texts(client(fake), {}, {("Acme", "1")}) == (0, 0, 0)
    assert not fake.calls


def test_the_hash_read_pages_through_every_open_job() -> None:
    fake = FakeSupabase(page=2)
    for n in range(5):
        fake.table[("Acme", str(n))] = {"status": "open", "details": {HASH_KEY: f"h{n}"} if n % 2 else None}
    assert client(fake).text_hashes() == {("Acme", str(n)): (f"h{n}" if n % 2 else None) for n in range(5)}


def test_an_excluded_company_loses_its_text_with_its_jobs(seeded: sqlite3.Connection) -> None:
    work({("Acme", "1"): "Text one", ("Beta", "9"): "Text three"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    assert fake.table[("Beta", "9")]["description"] == "Text three"
    seeded.execute("DELETE FROM jobs WHERE company='Beta'")  # the crawl's purge removed them locally
    result = sync(seeded, client(fake), ["Beta"])
    assert ("Beta", "9") not in fake.table  # the row, and with it the text, is deleted
    assert result.purged == 1
    assert fake.table[("Acme", "1")]["description"] == "Text one"


def test_an_excluded_company_is_never_sent_a_text(seeded: sqlite3.Connection) -> None:
    work({("Beta", "9"): "Text three"})
    fake = FakeSupabase()
    sync(seeded, client(fake), ["Beta"])
    assert not any("description" in r for c in fake.calls if c["method"] == "POST" for r in c["json"])


def test_main_prints_the_counts_of_the_description_pass(
    seeded: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seeded.commit()
    work({("Acme", "1"): SENTINEL})
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key")
    monkeypatch.setattr(sync_supabase.requests, "Session", FakeSupabase)
    sync_supabase.main()
    out = capsys.readouterr().out
    assert "Supabase: descriptions 1 written, 0 unchanged, 0 failed" in out
    assert SENTINEL not in out


def test_an_error_answer_to_a_text_request_can_leave_the_body_out() -> None:
    fake = FakeSupabase(fail_on="POST")
    with pytest.raises(SyncError) as quiet:
        client(fake).upsert([{"company": "A", "job_id": "1"}], quiet=True)
    assert str(quiet.value) == "POST jobs failed: HTTP 500"
    with pytest.raises(SyncError, match="HTTP 500 boom"):
        client(fake).upsert([{"company": "A", "job_id": "1"}])


# ---- nothing that carries a description can be committed ----


def staged_paths(workflow: str) -> list[str]:
    """Every path a `git add` in the workflow names (the daily workflow loops over an explicit list)."""
    paths: list[str] = []
    for block in re.findall(r"for f in ([^;]+); do", workflow):
        paths.extend(block.split())
    paths.extend(re.findall(r"git add (?!\"\$f\")(\S+)", workflow))
    return paths


def test_the_daily_workflow_stages_an_explicit_list_with_nothing_that_can_carry_a_description() -> None:
    paths = staged_paths(WORKFLOW)
    assert "data/jobs.db" in paths
    assert not [p for p in paths if p in {".", "-A", "--all", "data", "data/", "docs", "docs/"} or "*" in p]
    assert not [p for p in paths if "description" in p]
    assert re.findall(r"git add (\S+)", WORKFLOW) == ['"$f"']  # the only `git add` is the one in the loop
    assert "git add -A" not in WORKFLOW
    assert "git commit -a" not in WORKFLOW
    assert all(" -a" not in c for c in re.findall(r"git commit[^\n]*", WORKFLOW))


def test_the_publish_docs_workflow_stages_docs_only_and_no_description_file() -> None:
    adds = re.findall(r"git add ([^\n]+)", PUBLISH)
    assert adds
    assert all("description" not in a and not a.startswith(("-A", ".", "data")) for a in adds)


def test_the_crawl_step_has_no_environment_so_no_secret_reaches_it() -> None:
    crawl_step = WORKFLOW.split("name: Crawl and diff")[1].split("      - name:")[0]
    assert "env:" not in crawl_step  # no secret, and no variable, reaches the crawl


def test_the_dashboard_and_the_digest_read_named_columns_so_a_page_load_never_carries_the_texts() -> None:
    dashboard = (REPO / "templates" / "dashboard.js").read_text(encoding="utf-8")
    columns = re.search(r"const JOB_COLUMNS = '([^']+)'", dashboard)
    assert columns
    for named in (columns[1], send_digests.JOB_COLUMNS):
        assert "description" not in named
        assert "details" not in named
    assert "select=*" not in dashboard


def test_a_hash_read_of_an_unexpected_shape_is_a_warning_and_not_a_crash(
    seeded: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    work({("Acme", "1"): "Text one"})
    fake = FakeSupabase()
    real = fake.request

    def request(method: str, url: str, **kw: Any) -> FakeHttpResponse:
        if method == "GET" and "hash:" in str(kw["params"].get("select")):
            return FakeHttpResponse(data=[{"unexpected": 1}])
        return real(method, url, **kw)

    fake.request = request  # type: ignore[method-assign]
    assert sync(seeded, client(fake)).texts_failed == 1
    assert "could not be read, so none is written: KeyError" in capsys.readouterr().out


def test_texts_that_match_no_open_job_are_a_warning(
    seeded: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    work({("Nobody", "1"): "Text"})
    sync(seeded, client(FakeSupabase()))
    assert "1 descriptions were left by the crawl and none is for an open job" in capsys.readouterr().out
