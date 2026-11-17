"""How a description travels (PLAN.md step 7.1): from the adapters through run.py to a work file outside the checkout,
then through sync_supabase.py to Supabase, and never into data/jobs.db, docs/ or any committed file."""

import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import adapters
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


def test_job_pages_are_asked_only_for_jobs_without_a_stored_text_and_the_log_has_counts_only(
    workdir: Path, crawl: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []

    def page(slug: str, j: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        asked.append(j["job_id"])
        return (SENTINEL, {}) if j["job_id"] != "bad" else None

    monkeypatch.setitem(adapters.DETAILS, "fake", page)
    monkeypatch.setattr(adapters, "DETAIL_CAP", 2)
    descriptions.write_held([("Acme", "held")])
    crawl(
        [co("Acme", "acme")],
        {"acme": [posting("held", None), posting("bad", None), posting("1", None), posting("2", None)]},
    )
    assert asked == ["bad", "1"]  # the stored one is not asked, and two is the cap: "2" waits
    assert read_work_file() == {("Acme", "1"): (SENTINEL, {})}  # nothing is written for the held job, it keeps its text
    out = capsys.readouterr().out
    assert "descriptions: fake 2/4 (50%)" in out  # the held job counts: its text is stored
    assert "job pages: fake 2 requests, 1 with a text, 1 left for later runs" in out
    assert SENTINEL not in out
    crawl([co("Acme", "acme")], {"acme": [posting("held", None)]})  # a run with nothing to ask prints no such line
    assert "job pages" not in capsys.readouterr().out


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
        frozenset({"company", "job_id", "description", "snippet", "details"})
    }
    assert one["snippet"] == "Text one"  # short enough to be shown whole
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
    assert client(fake).stored_texts() == {("Acme", str(n)): ((f"h{n}" if n % 2 else None), None) for n in range(5)}


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
    assert paths  # the dashboard files; the history (data/jobs.db) is a scratch file and is not committed any more
    assert not [p for p in paths if p.startswith("data/")]
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


# ---- the snippet and the 30-day trim (PLAN.md steps 7.2 and 7.4) ----

WORDS = "word " * 80  # 400 characters of five-character words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("", None),
        ("   \n ", None),
        ("Short text.", "Short text."),  # a text that fits is kept whole, with its final period
        ("  \n Leading space.", "Leading space."),
        ("a" * 255, "a" * 255),  # exactly the limit: whole, not cut
        ("a" * 254 + " b", "a" * 254),  # the 255th character is the space: nothing is cut off a word
        ("a" * 250 + " bbbbbbbbb", "a" * 250),  # the cut falls inside a word: back to the space before it
        ("a" * 256, None),  # one word longer than the limit has no honest cut
        ("a" * 10 + "\n" + "b" * 300, "a" * 10),  # a line break is a boundary too
        ("word " * 51 + "end", "word " * 50 + "word"),  # 255 characters end on a space
        (f"{'x' * 250}, yyyyyyyy", "x" * 250),  # the cut word goes, and the comma before it
        (f"{'x' * 250} - yyyyyyyy", "x" * 250),  # a dangling dash goes
        (f"{'x' * 250}. Yyyyyyyy", "x" * 250),  # no full stop on a cut text: no sentence ended here
        (f"{'x' * 248} C# yyyyyyyy", "x" * 248 + " C#"),  # `#` can belong to the word
        ("שלום " * 80, "שלום " * 50 + "שלום"),  # Hebrew: characters, not bytes
        (f"{'ש' * 250} אבגדהוזחטי", "ש" * 250),
        ("- " * 200, None),  # nothing but punctuation is no snippet
    ],
)
def test_the_snippet_is_the_first_255_characters_cut_at_a_word_boundary(text: str | None, expected: str | None) -> None:
    result = descriptions.snippet(text)
    assert result == expected
    if result:
        assert len(result) <= descriptions.SNIPPET_CHARS
        assert text is not None and text.strip().startswith(result)


def test_a_cut_snippet_never_ends_inside_a_word_or_on_punctuation() -> None:
    for length in range(240, 300):
        text = ("Build things, ship them; learn (fast) - repeat: " * 10)[:length]
        result = descriptions.snippet(text)
        assert result is not None
        assert result[-1].isalnum() or length <= descriptions.SNIPPET_CHARS
        assert result in text and text.startswith(result)


def stored(fake: FakeSupabase, key: tuple[str, str]) -> dict[str, Any]:
    return fake.table[key]


def test_the_sync_writes_the_snippet_with_the_text_and_nothing_else_of_it(seeded: sqlite3.Connection) -> None:
    long_text = "Build it. " * 60
    work({("Acme", "1"): long_text})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    row = stored(fake, ("Acme", "1"))
    assert row["description"] == long_text
    assert row["snippet"] == descriptions.snippet(long_text)
    assert len(row["snippet"]) <= descriptions.SNIPPET_CHARS
    assert "snippet" not in stored(fake, ("Beta", "9"))  # no text today: nothing sent, nothing erased


def test_a_stored_text_without_a_snippet_gets_one_on_the_next_sync_and_then_is_left_alone(
    seeded: sqlite3.Connection,
) -> None:
    work({("Acme", "1"): "Text one"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    del fake.table[("Acme", "1")]["snippet"]  # a text stored before the snippet existed
    assert sync(seeded, client(fake)).texts == 1
    assert stored(fake, ("Acme", "1"))["snippet"] == "Text one"
    assert sync(seeded, client(fake)).texts == 0  # the hash and the snippet agree: not written again


def test_a_text_with_no_snippet_is_not_rewritten_every_day(seeded: sqlite3.Connection) -> None:
    work({("Acme", "1"): "a" * 400})  # one long word: the text is stored, the snippet is null
    fake = FakeSupabase()
    assert sync(seeded, client(fake)).texts == 1
    assert stored(fake, ("Acme", "1"))["snippet"] is None
    assert sync(seeded, client(fake)).texts == 0


def test_the_snippet_follows_an_edited_text(seeded: sqlite3.Connection) -> None:
    work({("Acme", "1"): "First text"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    work({("Acme", "1"): "Second text"})
    sync(seeded, client(fake))
    assert stored(fake, ("Acme", "1"))["snippet"] == "Second text"


def test_a_job_that_closes_keeps_its_text_and_snippet_until_the_trim(seeded: sqlite3.Connection) -> None:
    work({("Acme", "1"): "Text one", ("Acme", "2"): "Text two"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    seeded.execute("UPDATE jobs SET status='closed' WHERE company='Acme' AND job_id='1'")
    work({("Acme", "2"): "Text two"})
    sync(seeded, client(fake))
    closed = stored(fake, ("Acme", "1"))
    assert closed["status"] == "closed"
    assert (closed["description"], closed["snippet"]) == ("Text one", "Text one")


def test_a_trimmed_job_that_reopens_gets_its_text_and_snippet_back(seeded: sqlite3.Connection) -> None:
    work({("Acme", "1"): "Text one"})
    fake = FakeSupabase()
    sync(seeded, client(fake))
    # what jobs_text_trim leaves of a closed job: the text, its details (the hash with them) and the snippet are null
    fake.table[("Acme", "1")].update(status="closed", description=None, details=None, snippet=None)
    result = sync(seeded, client(fake))  # the crawl sees the job again: it is open and has a text
    assert (result.texts, result.texts_same) == (1, 0)
    row = stored(fake, ("Acme", "1"))
    assert row["status"] == "open"
    assert (row["description"], row["snippet"]) == ("Text one", "Text one")
    assert row["details"][HASH_KEY] == fingerprint("Text one", {"employment_type": "Full-time"})


def test_the_trim_calls_the_function_with_today_and_logs_the_count(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSupabase(rpc_answer=3)
    assert sync_supabase.trim_texts(client(fake), date(2026, 11, 16)) == THREE_JOBS
    (call,) = fake.calls
    assert (call["method"], call["url"]) == ("POST", f"{URL}/rest/v1/rpc/jobs_text_trim")
    assert call["json"] == {"p_day": "2026-11-16"}
    assert capsys.readouterr().out == ""  # nothing here prints: main logs the count only


@pytest.mark.parametrize("answer", ["x", None, True, [1], {"a": 1}])
def test_a_trim_answer_that_is_not_a_count_is_a_warning_not_a_failure(
    answer: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sync_supabase.trim_texts(client(FakeSupabase(rpc_answer=answer)), date(2026, 11, 16)) is None
    assert (
        "::warning::the text of closed jobs could not be trimmed: rpc/jobs_text_trim returned"
        in capsys.readouterr().out
    )


def test_a_trim_that_cannot_reach_the_server_is_a_warning_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSupabase(error=sync_supabase.requests.ConnectionError("down"))
    assert sync_supabase.trim_texts(client(fake), date(2026, 11, 16)) is None
    assert (
        "::warning::the text of closed jobs could not be trimmed: POST rpc/jobs_text_trim failed"
        in capsys.readouterr().out
    )


def test_a_failing_trim_is_a_warning_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert sync_supabase.trim_texts(client(FakeSupabase(fail_on="POST")), date(2026, 11, 16)) is None
    out = capsys.readouterr().out
    assert "::warning::the text of closed jobs could not be trimmed: POST rpc/jobs_text_trim failed" in out


def test_main_trims_after_the_descriptions_and_logs_the_count_only(
    seeded: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded.commit()
    work({("Acme", "1"): SENTINEL})
    fake = FakeSupabase(rpc_answer=5)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()
    urls = [c["url"] for c in fake.calls]
    trim = urls.index(f"{URL}/rest/v1/rpc/jobs_text_trim")
    assert trim > max(i for i, c in enumerate(fake.calls) if c["method"] == "POST" and "description" in str(c["json"]))
    assert trim < urls.index(f"{URL}/rest/v1/rpc/report_site_cleanup")
    out = capsys.readouterr().out
    assert "Supabase: 5 closed jobs lost their text" in out
    assert SENTINEL not in out


def test_a_failing_trim_does_not_stop_the_step_and_is_not_logged_as_a_count(
    seeded: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class TrimFails(FakeSupabase):
        def request(self, method: str, url: str, **kw: Any) -> Any:
            if url.endswith("jobs_text_trim"):
                self.calls.append({"method": method, "url": url, **kw})
                return FakeHttpResponse(status_code=404, text="no such function")
            return super().request(method, url, **kw)

    seeded.commit()
    fake = TrimFails(rpc_answer=7)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    monkeypatch.setattr(sync_supabase.requests, "Session", lambda: fake)
    sync_supabase.main()  # no SystemExit
    out = capsys.readouterr().out
    assert "::warning::the text of closed jobs could not be trimmed" in out
    assert "was not trimmed today" in out
    assert "closed jobs lost their text" not in out
    assert "Supabase: 14 spam-guard counter rows deleted" in out  # the cleanups after it still ran


def test_a_crawl_that_died_in_the_browser_leaves_a_history_the_sync_stores_without_texts(
    workdir: Path, crawl: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workflow syncs after a failed crawl: the companies recorded before the crash are stored; the texts of that
    day are not (the work file is written at the end of the crawl), and the sync does not stop for it."""
    descriptions.work_path().unlink(missing_ok=True)

    def crash(_urls: list[str]) -> Any:
        raise RuntimeError("browser died")

    monkeypatch.setattr(run, "crawl_many", crash)
    with pytest.raises(RuntimeError, match="browser died"):
        crawl(
            [co("Acme", "acme"), {"name": "Br", "ats": "browser", "slug": "https://br", "status": "ok"}],
            {"acme": [posting("1")]},
        )
    fake = FakeSupabase()
    conn = sqlite3.connect(run.DB)
    result = sync(conn, client(fake))
    conn.close()
    assert (result.upserted, result.texts, result.logged) == (1, 0, 1)
    assert list(fake.table) == [("Acme", "1")]
