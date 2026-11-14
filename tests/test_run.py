import runpy
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import browser_crawl
import run
from tests.conftest import add_crawls, add_job, write_json


def job(job_id: str, title: str = "T") -> dict[str, str]:
    return {"job_id": job_id, "title": title, "location": "L", "url": "u", "team": "t", "posted": "p"}


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake 'fake' ATS: results[slug] is a list of outcomes, popped per call (last one repeats)."""
    results: dict[str, list[Any]] = {}

    def fetch(slug: str) -> Any:
        outcomes = results[slug]
        return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]

    monkeypatch.setitem(run.ADAPTERS, "fake", fetch)
    monkeypatch.setattr(run.time, "sleep", lambda _s: None)
    return results


def co(name: str, ats: str = "fake", slug: str | None = None, status: str = "ok") -> dict[str, str]:
    return {"name": name, "ats": ats, "slug": slug or name.lower(), "status": status}


def test_init_is_idempotent(workdir: Path) -> None:
    run.init().close()
    conn = run.init()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(crawl_log)")]
    assert "note" in cols
    conn.close()


def test_crawl_retries_once(adapter: dict[str, Any]) -> None:
    adapter["a"] = [None, [job("1")]]
    assert run.crawl(co("A")) == (co("A"), [job("1")])
    adapter["b"] = [[job("1")]]
    assert run.crawl(co("B"))[1] == [job("1")]
    adapter["c"] = [None]
    assert run.crawl(co("C"))[1] is None


def test_main_full_cycle(
    workdir: Path, adapter: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = run.init()
    add_job(conn, "Known", "old", "Old job")
    add_job(conn, "Known", "gone", "Gone job")
    add_job(conn, "Br2", "b2old", "Browser old")
    add_job(conn, "Br1", "x", "Anything", status="closed")
    conn.close()
    companies = [
        co("New"),
        co("Known"),
        co("Failing"),
        co("Skipped", status="ats_failed"),
        co("Br1", ats="browser", slug="https://br1"),
        co("Br2", ats="browser", slug="https://br2"),
        co("Br3", ats="browser", slug="https://br3"),
        co("Br4", ats="browser", slug="https://br4"),
        co("Br5", ats="browser", slug="https://br5"),
    ]
    write_json(workdir / "data" / "companies.json", companies)
    adapter["new"] = [[job("n1"), job("n2")]]
    adapter["known"] = [[job("old", "Renamed"), job("fresh")]]
    adapter["failing"] = [None]
    crawled = {
        "https://br1": [job("b1")],
        "https://br2": [],
        "https://br3": [],
        "https://br4": None,
    }
    monkeypatch.setattr(run, "crawl_many", lambda urls: {u: crawled[u] for u in urls if u in crawled})
    monkeypatch.setitem(browser_crawl.WHY, "https://br3", "empty page")
    monkeypatch.setitem(browser_crawl.WHY, "https://br4", "blocked")
    run.main()
    out = capsys.readouterr().out
    assert "failed 4 ['Failing', 'Br2', 'Br4', 'Br5']" in out
    assert "browser Br1" in out
    assert "<- empty page" in out
    assert "FAILED  <- blocked" in out

    check = sqlite3.connect(run.DB)
    rows = {(r[0], r[1]): r[2:] for r in check.execute("SELECT company,job_id,status,reported,title FROM jobs")}
    assert rows[("New", "n1")] == ("open", 1, "T")  # baseline: silently reported
    assert rows[("Known", "fresh")][:2] == ("open", 0)  # new job at known company: unreported
    assert rows[("Known", "old")] == ("open", 0, "Renamed")
    assert rows[("Known", "gone")][0] == "closed"
    assert rows[("Br2", "b2old")][0] == "open"  # empty browser result with history is not trusted
    assert rows[("Br1", "x")][0] == "closed"
    notes = dict(check.execute("SELECT company, note FROM crawl_log"))
    assert notes["Failing"] == "request failed"
    assert notes["Br3"] == "empty page"
    assert notes["Br2"] == f"{run.DROP_NOTE}: got 0, 1 open before"
    assert notes["Br5"] == "request failed"
    assert notes["New"] == ""
    check.close()


def test_main_israel_only_keeps_israel_jobs(workdir: Path, adapter: dict[str, Any]) -> None:
    conn = run.init()
    add_job(conn, "Big", "old-us", "Old US job", location="Austin, TX")
    add_job(conn, "Big", "old-il", "Old IL job", location="Tel Aviv, Israel")
    conn.close()
    write_json(workdir / "data" / "companies.json", [{**co("Big"), "israel_only": True}, co("Plain")])
    il = {**job("new-il"), "location": "Haifa, IL"}
    us = {**job("new-us"), "location": "Chicago, IL"}
    nowhere = {**job("no-loc"), "location": None}
    adapter["big"] = [[{**job("old-il"), "location": "Tel Aviv, Israel"}, il, us, nowhere]]
    adapter["plain"] = [[{**job("p1"), "location": "Austin, TX"}]]
    run.main()
    check = sqlite3.connect(run.DB)
    rows = {(r[0], r[1]): r[2] for r in check.execute("SELECT company,job_id,status FROM jobs")}
    assert rows[("Big", "old-il")] == "open"
    assert rows[("Big", "new-il")] == "open"
    assert ("Big", "new-us") not in rows  # not stored
    assert ("Big", "no-loc") not in rows
    assert rows[("Big", "old-us")] == "closed"  # was stored before the flag: closes
    assert rows[("Plain", "p1")] == "open"  # no flag: kept whatever the location
    logged = dict(check.execute("SELECT company, n_jobs FROM crawl_log"))
    assert logged["Big"] == 2 + 0
    check.close()


BIG = co("Big", ats="browser", slug="https://big")
OPEN = 20
ISRAEL_OPEN = 12
API_KEPT = 2
RETURNED = 5
HELD_RUNS = run.DROP_HOLD_DAYS


@pytest.fixture(autouse=True)
def fresh_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """One crawl-reason dict for the test, shared by run and browser_crawl so no reason leaks between tests."""
    why: dict[str, str] = {}
    monkeypatch.setattr(run, "WHY", why)
    monkeypatch.setattr(browser_crawl, "WHY", why)


@pytest.fixture
def day(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Pick the date main() stamps its crawl log rows with: day(3) is 2025-03-03."""

    def set_day(n: int) -> None:
        monkeypatch.setattr(run.time, "strftime", lambda _fmt: f"2025-03-{n:02d}T12:00:00")

    return set_day


def seed_open(conn: sqlite3.Connection, name: str, count: int, **kw: Any) -> None:
    for i in range(count):
        add_job(conn, name, f"j{i}", f"Job {i}", **kw)


def seed(name: str = "Big", count: int = OPEN, **kw: Any) -> None:
    conn = run.init()
    seed_open(conn, name, count, **kw)
    conn.commit()
    conn.close()


def held_note(n: int = RETURNED, prev: int = OPEN) -> str:
    return f"{run.DROP_NOTE}: got {n}, {prev} open before"


def log_runs(name: str, notes: list[str | None], date: str = "2024-01-{:02d}") -> None:
    """Earlier crawl log rows for a company, oldest first, one per day (or all on one day)."""
    conn = run.init()
    for i, note in enumerate(notes, start=1):
        conn.execute(
            "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
            (f"{date.format(i)}T00:00:00" if "{" in date else f"{date}T00:00:{i:02d}", name, "browser", 0, 0, note),
        )
    conn.commit()
    conn.close()


def crawl(workdir: Path, monkeypatch: pytest.MonkeyPatch, jobs: list[Any], company: dict[str, Any] = BIG) -> None:
    write_json(workdir / "data" / "companies.json", [company])
    monkeypatch.setattr(run, "crawl_many", lambda urls: dict.fromkeys(urls, jobs))
    run.main()


def returned(count: int) -> list[dict[str, str]]:
    return [job(f"j{i}") for i in range(count)]


def open_jobs(name: str = "Big") -> int:
    check = sqlite3.connect(run.DB)
    count: int = check.execute("SELECT COUNT(*) FROM jobs WHERE company=? AND status='open'", (name,)).fetchone()[0]
    check.close()
    return count


def last_log(name: str = "Big") -> tuple[int, int, str]:
    check = sqlite3.connect(run.DB)
    row: tuple[int, int, str] = check.execute(
        "SELECT ok, n_jobs, note FROM crawl_log WHERE company=? ORDER BY ts DESC LIMIT 1", (name,)
    ).fetchone()
    check.close()
    return row


def test_sharp_drop_keeps_jobs_open_records_new_ones_and_says_why(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed()
    crawl(workdir, monkeypatch, [*returned(RETURNED), job("brand-new")])
    assert open_jobs() == OPEN + 1  # nothing closed, and the new job the partial crawl did see is recorded
    check = sqlite3.connect(run.DB)
    assert check.execute("SELECT reported FROM jobs WHERE job_id='brand-new'").fetchone() == (0,)  # for the digest
    check.close()
    assert last_log() == (0, RETURNED + 1, held_note(RETURNED + 1))  # logged as a failed crawl, with the reason
    out = capsys.readouterr().out
    assert "failed 1 ['Big']" in out
    assert f"FAILED  <- {held_note(RETURNED + 1)}" in out


@pytest.mark.parametrize(
    ("open_before", "count", "open_after"),
    [(20, 9, 20), (20, 10, 10), (21, 10, 21), (21, 11, 11)],  # under half is held, half or more is believed
)
def test_sharp_drop_is_under_half(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, open_before: int, count: int, open_after: int
) -> None:
    seed(count=open_before)
    crawl(workdir, monkeypatch, returned(count))
    assert open_jobs() == open_after


@pytest.mark.parametrize(
    ("open_before", "open_after"), [(run.DROP_MIN_OPEN - 1, 1), (run.DROP_MIN_OPEN, run.DROP_MIN_OPEN)]
)
def test_small_companies_are_not_judged_on_a_drop(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, open_before: int, open_after: int
) -> None:
    seed(count=open_before)
    crawl(workdir, monkeypatch, returned(1))
    assert open_jobs() == open_after


@pytest.mark.parametrize(
    ("open_before", "count", "open_after"),
    [(run.DROP_MIN_OPEN - 5, 1, 1), (run.DROP_MIN_OPEN, 4, run.DROP_MIN_OPEN)],
)
def test_closed_jobs_do_not_count_as_open_before(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, open_before: int, count: int, open_after: int
) -> None:
    seed(count=open_before)
    conn = run.init()
    for i in range(30):  # a long history of closed jobs must not make a small company look big
        add_job(conn, "Big", f"closed{i}", "Closed", status="closed")
    conn.commit()
    conn.close()
    crawl(workdir, monkeypatch, returned(count))
    assert open_jobs() == open_after  # 5 open: believed. 10 open and 4 back: held, as without the closed rows


def test_the_first_crawl_of_a_company_is_a_silent_baseline(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crawl(workdir, monkeypatch, returned(OPEN))
    assert open_jobs() == OPEN
    assert last_log() == (1, OPEN, "")


@pytest.mark.parametrize(
    ("earlier", "open_after"),
    [
        ([held_note()] * (HELD_RUNS - 1), OPEN),  # held two days: held again
        ([held_note()] * HELD_RUNS, RETURNED),  # held three days in a row: believed
        ([held_note(), "", held_note()], OPEN),  # a day in between that was not held starts the count again
        ([held_note(), None, held_note()], OPEN),  # a log row with no note was not held
        ([held_note(), "request failed", held_note()], OPEN),  # a crawl that failed another way, too
        ([held_note(), "no jobs found, title='x'", held_note()], OPEN),
        (["request failed", *[held_note()] * HELD_RUNS], RETURNED),  # only the latest days matter
    ],
)
def test_a_sharp_drop_is_believed_after_three_held_days(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, earlier: list[str | None], open_after: int
) -> None:
    seed()
    log_runs("Big", earlier)
    crawl(workdir, monkeypatch, returned(RETURNED))
    assert open_jobs() == open_after


def test_runs_within_a_day_do_not_use_up_the_hold(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed()
    log_runs("Big", [held_note()] * (HELD_RUNS + 2), date="2024-01-05")  # five held runs, all on one day
    crawl(workdir, monkeypatch, returned(RETURNED))
    assert open_jobs() == OPEN
    today = time.strftime("%Y-%m-%d")
    conn = run.init()
    conn.execute("DELETE FROM crawl_log WHERE company='Big'")  # held runs earlier today are ignored too
    conn.commit()
    conn.close()
    log_runs("Big", [held_note()] * HELD_RUNS, date=today)
    crawl(workdir, monkeypatch, returned(RETURNED))
    assert open_jobs() == OPEN


def test_a_persistent_drop_is_held_three_days_then_believed_and_logged(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None], capsys: pytest.CaptureFixture[str]
) -> None:
    seed()
    for n in range(1, HELD_RUNS + 1):
        day(n)
        crawl(workdir, monkeypatch, returned(RETURNED))
        assert open_jobs() == OPEN, f"day {n} is held"
        assert last_log() == (0, RETURNED, held_note())
    capsys.readouterr()
    day(HELD_RUNS + 1)
    crawl(workdir, monkeypatch, returned(RETURNED))
    believed = f"{run.DROP_BELIEVED}: got {RETURNED}, {OPEN} open before"
    assert open_jobs() == RETURNED  # the fourth day believes it
    assert last_log() == (1, RETURNED, believed)  # a successful crawl, and the log says what happened
    out = capsys.readouterr().out
    assert f"browser Big             {RETURNED}  <- {believed}" in out
    day(HELD_RUNS + 2)
    crawl(workdir, monkeypatch, returned(RETURNED))
    assert last_log() == (1, RETURNED, "")  # back to normal: nothing left to judge


def test_the_same_drop_on_one_day_is_held_however_often_it_runs(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None]
) -> None:
    seed()
    day(1)
    for _ in range(HELD_RUNS + 1):
        crawl(workdir, monkeypatch, returned(RETURNED))
    day(2)
    crawl(workdir, monkeypatch, returned(RETURNED))
    assert open_jobs() == OPEN  # one earlier day is held, not three


SMALL = run.DROP_MIN_OPEN - 5
REASON = "no jobs found, title='Careers', links=81"


def job_row(job_id: str, name: str = "Big") -> tuple[str, int]:
    check = sqlite3.connect(run.DB)
    row: tuple[str, int] = check.execute(
        "SELECT status, reported FROM jobs WHERE company=? AND job_id=?", (name, job_id)
    ).fetchone()
    check.close()
    return row


def test_an_empty_result_at_a_small_company_is_held_three_days_then_believed(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None]
) -> None:
    seed(count=SMALL)
    for n in range(1, HELD_RUNS + 1):
        day(n)
        crawl(workdir, monkeypatch, [])
        assert open_jobs() == SMALL, f"day {n} is held"
        assert last_log() == (0, 0, held_note(0, SMALL))
    day(HELD_RUNS + 1)
    crawl(workdir, monkeypatch, [])
    assert open_jobs() == 0  # a small company that really closed every role is believed on the fourth day
    assert last_log() == (1, 0, f"{run.DROP_BELIEVED}: got 0, {SMALL} open before")


def test_an_empty_result_at_a_big_company_is_never_believed(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None], capsys: pytest.CaptureFixture[str]
) -> None:
    """A page that stopped working (a cookie wall, a 200 block page) must not close this many jobs, ever."""
    seed(count=run.DROP_MIN_OPEN)
    for n in range(1, HELD_RUNS * 2 + 1):
        day(n)
        crawl(workdir, monkeypatch, [])
        assert open_jobs() == run.DROP_MIN_OPEN, f"day {n} is still held"
        assert last_log() == (0, 0, held_note(0, run.DROP_MIN_OPEN))
    assert "FAILED" in capsys.readouterr().out  # and it keeps showing as failing


def test_an_empty_result_keeps_the_crawlers_reason_on_every_day_and_recovery_reopens_the_jobs(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None]
) -> None:
    seed(count=SMALL, reported=1)
    for n in range(1, HELD_RUNS + 1):
        day(n)
        monkeypatch.setitem(browser_crawl.WHY, BIG["slug"], REASON)
        crawl(workdir, monkeypatch, [])
        assert last_log() == (0, 0, f"{held_note(0, SMALL)} ({REASON})")
    day(HELD_RUNS + 1)
    monkeypatch.setitem(browser_crawl.WHY, BIG["slug"], REASON)
    crawl(workdir, monkeypatch, [])
    assert last_log() == (1, 0, f"{run.DROP_BELIEVED}: got 0, {SMALL} open before ({REASON})")  # one prefix, one reason
    assert open_jobs() == 0
    day(HELD_RUNS + 2)  # the site recovers, with one new job
    crawl(workdir, monkeypatch, [*returned(SMALL), job("new")])
    assert open_jobs() == SMALL + 1
    assert job_row("j0") == ("open", 1)  # reopened, and not reported again: no digest spam
    assert job_row("new") == ("open", 0)
    assert last_log() == (1, SMALL + 1, "")  # a believed note does not count as a held day or linger


@pytest.mark.parametrize(
    "stale", [f"{run.DROP_BELIEVED}: got 0, 20 open before", f"{run.DROP_NOTE}: got 0, 20 open before"]
)
def test_a_note_left_in_the_reasons_by_an_earlier_run_is_not_nested(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, stale: str
) -> None:
    seed()
    monkeypatch.setitem(browser_crawl.WHY, BIG["slug"], stale)
    crawl(workdir, monkeypatch, [])
    assert last_log() == (0, 0, held_note(0))  # no bracketed copy of the old note


def test_an_empty_result_without_history_is_just_an_empty_company(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(browser_crawl.WHY, BIG["slug"], "empty page")
    crawl(workdir, monkeypatch, [])
    assert last_log() == (1, 0, "empty page")  # nothing was open, so nothing to hold


@pytest.mark.parametrize("case", [(run.DROP_MIN_OPEN + 2, False), (SMALL, True)], ids=["big", "small"])
@pytest.mark.parametrize("fetched", ["raw empty", "no Israel jobs"])
def test_an_israel_only_company_with_no_israel_jobs_is_an_empty_crawl(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, day: Callable[[int], None], case: tuple[int, bool], fetched: str
) -> None:
    open_before, believed = case
    seed(count=open_before, location="Tel Aviv, Israel")
    board = [] if fetched == "raw empty" else [{**job(f"a{i}"), "location": "Austin, TX"} for i in range(50)]
    for n in range(1, HELD_RUNS + 1):
        day(n)
        crawl(workdir, monkeypatch, board, {**BIG, "israel_only": True})
        assert (open_jobs(), last_log()) == (open_before, (0, 0, held_note(0, open_before)))
    day(HELD_RUNS + 1)
    crawl(workdir, monkeypatch, board, {**BIG, "israel_only": True})
    assert open_jobs() == (0 if believed else open_before)


def test_one_held_company_does_not_hold_another(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed("Big")
    seed("Fine")
    write_json(workdir / "data" / "companies.json", [BIG, co("Fine", ats="browser", slug="https://fine")])
    crawled = {"https://big": returned(RETURNED), "https://fine": returned(OPEN - 1)}
    monkeypatch.setattr(run, "crawl_many", lambda urls: {u: crawled[u] for u in urls})
    run.main()
    assert (open_jobs("Big"), open_jobs("Fine")) == (OPEN, OPEN - 1)  # Fine closed its one job as usual
    out = capsys.readouterr().out
    assert "failed 1 ['Big']" in out
    assert f"browser Fine            {OPEN - 1}" in out
    assert "FAILED" in out.split("browser Big")[1].split("\n")[0]


def test_sharp_drop_counts_israel_jobs_for_an_israel_only_company(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(count=ISRAEL_OPEN, location="Tel Aviv, Israel")
    israel = [{**job(f"j{i}"), "location": "Haifa, Israel"} for i in range(3)]
    abroad = [{**job(f"a{i}"), "location": "Austin, TX"} for i in range(100)]
    crawl(workdir, monkeypatch, israel + abroad, {**BIG, "israel_only": True})
    assert open_jobs() == ISRAEL_OPEN  # 103 jobs came back, but only 3 are Israel jobs and 12 were open
    assert last_log()[2] == held_note(3, ISRAEL_OPEN)


def test_a_drop_in_an_api_company_is_still_believed(workdir: Path, adapter: dict[str, Any]) -> None:
    seed("Api")
    write_json(workdir / "data" / "companies.json", [co("Api")])
    adapter["api"] = [[job("j0"), job("j1")]]
    run.main()
    assert open_jobs("Api") == API_KEPT  # API feeds are complete lists: only browser crawls are held back


def reported_of(name: str, job_id: str) -> int:
    check = sqlite3.connect(run.DB)
    value: int = check.execute("SELECT reported FROM jobs WHERE company=? AND job_id=?", (name, job_id)).fetchone()[0]
    check.close()
    return value


@pytest.mark.parametrize(
    ("earlier", "reported"),
    [
        ([], 1),  # a company seen for the first time is baselined silently
        ([(0, "HTTP 403")], 1),  # a crawl that failed saw nothing: still new
        ([(0, "HTTP 403"), (1, "")], 0),  # it has worked once, with no job stored (israel_only found none)
        ([(1, "")], 0),
    ],
)
def test_a_company_is_new_until_a_crawl_has_worked(
    workdir: Path, adapter: dict[str, Any], earlier: list[tuple[int, str | None]], reported: int
) -> None:
    conn = run.init()
    add_crawls(conn, "Acme", earlier, ats="fake")
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Acme")])
    adapter["acme"] = [[job("first")]]
    run.main()
    assert reported_of("Acme", "first") == reported


def test_stored_jobs_also_make_a_company_known(workdir: Path, adapter: dict[str, Any]) -> None:
    conn = run.init()
    add_job(conn, "Acme", "old", "Old job")  # no crawl log at all: a database from before it existed
    conn.close()
    write_json(workdir / "data" / "companies.json", [co("Acme")])
    adapter["acme"] = [[job("old"), job("fresh")]]
    run.main()
    assert reported_of("Acme", "fresh") == 0


def test_main_without_companies_as_script(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_json(workdir / "data" / "companies.json", [])
    runpy.run_module("run", run_name="__main__")
    assert "crawled 0 companies, failed 0 [], open jobs 0" in capsys.readouterr().out
