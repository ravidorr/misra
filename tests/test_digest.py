import re
import runpy
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

import digest
from common import DROP_BELIEVED, DROP_NOTE
from tests.conftest import add_crawls, add_job


def test_select_filters(db: sqlite3.Connection) -> None:
    add_job(db, "A", "1", "Product Manager")
    add_job(db, "A", "2", "Product Manager", location="London")
    add_job(db, "A", "3", "Accountant")
    add_job(db, "A", "4", "Senior Product Manager")
    add_job(db, "A", "5", "Junior Software Engineer")
    add_job(db, "A", "6", "Product Intern", reported=1)
    add_job(db, "A", "7", "Product Designer", status="closed")
    add_job(db, "B", "8", "Student", location=None)
    assert [j["title"] for j in digest.select(db)] == ["Junior Software Engineer", "Product Manager"]
    everything = digest.select(db, only_unreported=False)
    assert [j["title"] for j in everything] == ["Junior Software Engineer", "Product Intern", "Product Manager"]
    assert [j["pm"] for j in everything] == [False, True, True]


def test_render_groups_and_escapes() -> None:
    jobs = [
        {"company": "A&B", "title": "PM <1>", "location": "TLV", "url": "http://u?a=1&b=2", "pm": True},
        {"company": "C", "title": "Intern", "location": None, "url": None, "pm": False},
        {"company": "D", "title": "Junior Developer", "location": None, "url": None, "pm": False},
    ]
    subject, body = digest.render(jobs)
    assert subject.startswith("New Israel jobs: 3 (")
    assert "<h3>Product roles (1)</h3>" in body
    assert "<h3>Intern roles (non-product titles) (1)</h3>" in body
    assert "<h3>Junior roles (non-product titles) (1)</h3>" in body
    assert "Student / junior / intern" not in body
    assert "A&amp;B" in body
    assert "PM &lt;1&gt;" in body
    assert 'href=""' in body
    assert "No new matching jobs" not in body


@pytest.mark.parametrize(
    ("title", "bucket"),
    [
        ("Product Manager", "pm"),
        ("Junior Product Manager", "pm"),
        ("Product Intern", "pm"),
        ("Software Intern", "intern"),
        ("Engineering Internship", "intern"),
        ("Student QA", "intern"),
        ("סטודנט QA", "intern"),
        ("Junior Intern", "intern"),
        ("Graduate Student", "intern"),
        ("Junior Developer", "junior"),
        ("Jr Analyst", "junior"),
        ("Graduate Program", "junior"),
        ("Entry Level Analyst", "junior"),
        ("QA Trainee", "junior"),
        ("International Accountant", None),
        ("Internal Tools Engineer", None),
    ],
)
def test_title_buckets(title: str, bucket: str | None) -> None:
    got = (
        "pm"
        if digest.PM_CORE.search(title)
        else "intern"
        if digest.INTERN.search(title)
        else "junior"
        if digest.JUNIOR.search(title)
        else None
    )
    assert got == bucket
    assert bool(digest.ROLE.search(title)) is (bucket is not None)


def test_render_sections_partition_jobs() -> None:
    def job(title: str, *, pm: bool = False) -> dict[str, Any]:
        return {"company": "Co", "title": title, "location": "TLV", "url": "http://u", "pm": pm}

    jobs = [
        job("Product Intern", pm=True),
        job("Junior Intern"),
        job("Graduate Student"),
        job("Junior Developer"),
        job("QA Trainee"),
        job("Entry Level Analyst"),
    ]
    _, body = digest.render(jobs)
    product, rest = body.split("<h3>Intern roles", 1)
    interns, juniors = rest.split("<h3>Junior roles", 1)
    assert "Product roles (1)" in product
    assert "Intern roles" not in product
    assert body.index("Product roles") < body.index("Intern roles") < body.index("Junior roles")
    assert "Product Intern" in product
    assert "(non-product titles) (2)" in interns
    assert "Junior Intern" in interns
    assert "Graduate Student" in interns
    assert "Junior Developer" not in interns
    assert "(non-product titles) (3)" in juniors
    for title in ("Junior Developer", "QA Trainee", "Entry Level Analyst"):
        assert title in juniors
    assert body.count("Junior Intern</a>") == 1


def test_render_omits_empty_sections() -> None:
    intern_only = [{"company": "C", "title": "Intern", "location": None, "url": None, "pm": False}]
    _, body = digest.render(intern_only)
    assert "Intern roles" in body
    assert "Junior roles" not in body
    assert "Product roles" not in body
    junior_only = [{"company": "C", "title": "Junior Dev", "location": None, "url": None, "pm": False}]
    _, body = digest.render(junior_only)
    assert "Junior roles" in body
    assert "Intern roles" not in body


def test_render_empty() -> None:
    subject, body = digest.render([])
    assert subject.startswith("New Israel jobs: 0")
    assert "<p>No new matching jobs today.</p>" in body
    assert "<h3>" not in body


def test_main_preview_then_commit(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_job(db, "A", "1", "Product Manager")
    add_job(db, "A", "2", "Intern")
    db.close()
    monkeypatch.setattr(sys, "argv", ["digest.py"])
    digest.main()
    out = capsys.readouterr().out
    assert "PM  A" in out
    assert "marked reported" not in out
    assert "Product Manager" in Path("data/digest.html").read_text()
    check = sqlite3.connect(digest.DB)
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE reported=1").fetchone()[0] == 0
    monkeypatch.setattr(sys, "argv", ["digest.py", "--commit"])
    runpy.run_module("digest", run_name="__main__")
    assert "marked reported" in capsys.readouterr().out
    assert check.execute("SELECT COUNT(*) FROM jobs WHERE reported=0").fetchone()[0] == 0


HELD = f"{DROP_NOTE}: got 0, 20 open before"
BELIEVED = f"{DROP_BELIEVED}: got 0, 5 open before"
PARTIAL = f"{DROP_BELIEVED}: got 4, 20 open before"
FAIL = "HTTP 403, title='Access Denied'"
DAY = "2026-01-{:02d}T06:00:00"
STALE_BOUNDARY = digest.GAP_STALE_DAYS
TWO_GAPS = 2


def gaps_of(db: sqlite3.Connection, company: str) -> list[dict[str, Any]]:
    return [g for g in digest.coverage_gaps(db) if g["company"] == company]


def only_gap(db: sqlite3.Connection, company: str) -> dict[str, Any]:
    (gap,) = gaps_of(db, company)
    return gap


def alert(db: sqlite3.Connection) -> None:
    """What a successful send does for the gaps."""
    digest.mark_alerted(db, digest.coverage_gaps(db))
    db.commit()


def test_coverage_gaps_ignore_healthy_and_one_off_failures(db: sqlite3.Connection) -> None:
    add_crawls(db, "Healthy", [(1, ""), (1, "")])
    add_crawls(db, "OneOff", [(1, ""), (0, FAIL)])  # failed once: not failing yet, as in the dashboard
    add_crawls(db, "Recovered", [(0, FAIL), (0, FAIL), (1, "")])
    assert digest.coverage_gaps(db) == []


@pytest.mark.parametrize(
    ("rows", "since_day"),
    [
        ([(1, ""), (1, ""), (0, FAIL), (0, FAIL)], 3),  # the outage starts at its first failed crawl
        ([(1, ""), (0, FAIL), (0, FAIL), (0, FAIL)], 2),
        ([(0, FAIL)], 1),  # a company whose first crawl failed
        ([(0, FAIL), (0, FAIL)], 1),
        ([(1, ""), (0, HELD), (0, FAIL), (0, FAIL)], 2),  # held rows are failed crawls: one outage
    ],
)
def test_a_failing_company_is_one_gap_since_its_outage_began(
    db: sqlite3.Connection, rows: list[tuple[int, str | None]], since_day: int
) -> None:
    add_crawls(db, "Blocked", rows)
    gap = only_gap(db, "Blocked")
    assert (gap["kind"], gap["note"], gap["since"], gap["new"]) == ("failing", FAIL, DAY.format(since_day), True)


def test_an_alert_is_sent_once_per_outage_and_a_new_outage_alerts_again(db: sqlite3.Connection) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    assert only_gap(db, "Blocked")["new"] is True
    alert(db)
    add_crawls(db, "Blocked", [(0, FAIL), (0, FAIL)], start_day=4)  # the same outage, days later
    assert only_gap(db, "Blocked")["new"] is False
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)], start_day=6)  # recovered, then blocked again
    gap = only_gap(db, "Blocked")
    assert (gap["since"], gap["new"]) == (DAY.format(7), True)


def test_a_failed_send_leaves_the_gap_new(db: sqlite3.Connection) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    digest.coverage_gaps(db)  # computing the gaps alone alerts nothing
    add_crawls(db, "Blocked", [(0, FAIL)], start_day=4)  # tomorrow's crawl
    assert only_gap(db, "Blocked")["new"] is True


@pytest.mark.parametrize(
    ("rows", "since_day"),
    [
        ([(1, ""), (0, HELD)], 2),
        ([(0, HELD)], 1),
        ([(1, ""), (0, HELD), (0, HELD)], 2),
        ([(0, FAIL), (0, FAIL), (0, HELD)], 1),  # already failing when it was held: the same outage
    ],
)
def test_a_held_crawl_is_its_own_kind(
    db: sqlite3.Connection, rows: list[tuple[int, str | None]], since_day: int
) -> None:
    add_crawls(db, "Big", rows)
    gap = only_gap(db, "Big")
    assert (gap["kind"], gap["note"], gap["since"], gap["new"]) == ("held", HELD, DAY.format(since_day), True)


def test_an_outage_that_changes_kind_or_flaps_alerts_once(db: sqlite3.Connection) -> None:
    add_crawls(db, "Flap", [(1, ""), (0, HELD)])
    assert only_gap(db, "Flap")["kind"] == "held"
    alert(db)
    add_crawls(db, "Flap", [(0, FAIL), (0, HELD), (0, FAIL), (0, FAIL)], start_day=3)  # held, failing, held, failing
    gap = only_gap(db, "Flap")
    assert (gap["kind"], gap["since"], gap["new"]) == ("failing", DAY.format(2), False)
    add_crawls(db, "Flap2", [(1, ""), (0, HELD), (0, FAIL), (0, HELD)])
    assert only_gap(db, "Flap2")["since"] == DAY.format(2)  # one outage through held, failed, held


def test_a_held_row_that_is_ok_is_not_a_gap(db: sqlite3.Connection) -> None:
    add_crawls(db, "Odd", [(1, HELD)])
    assert digest.coverage_gaps(db) == []


@pytest.mark.parametrize("note", [BELIEVED, PARTIAL])
def test_a_believed_drop_is_listed_until_an_email_carried_it(db: sqlite3.Connection, note: str) -> None:
    add_crawls(db, "Small", [(0, HELD)] * 3 + [(1, note), (1, ""), (1, "")])  # healthy since, and still listed
    gap = only_gap(db, "Small")
    assert (gap["kind"], gap["note"], gap["since"], gap["new"]) == ("closed", note, DAY.format(4), True)
    alert(db)
    assert gaps_of(db, "Small") == []  # carried once: gone for good


def test_a_believed_drop_and_a_current_failure_are_two_gaps(db: sqlite3.Connection) -> None:
    add_crawls(db, "Both", [(0, HELD)] * 3 + [(1, BELIEVED), (0, FAIL), (0, FAIL)])
    assert sorted(g["kind"] for g in gaps_of(db, "Both")) == ["closed", "failing"]


@pytest.mark.parametrize(("days_before_newest", "listed"), [(STALE_BOUNDARY, True), (STALE_BOUNDARY + 1, False)])
def test_a_company_no_longer_crawled_is_not_a_gap(
    db: sqlite3.Connection, days_before_newest: int, listed: bool
) -> None:
    newest = 20
    add_crawls(db, "Current", [(1, "")], start_day=newest)
    add_crawls(db, "Gone", [(0, FAIL), (0, FAIL)], start_day=newest - days_before_newest - 1)
    assert bool(gaps_of(db, "Gone")) is listed


def test_last_good_is_the_last_successful_day_or_never(db: sqlite3.Connection) -> None:
    add_crawls(db, "Was", [(1, ""), (1, ""), (0, FAIL), (0, FAIL)])
    add_crawls(db, "Never", [(0, FAIL), (0, FAIL)])
    assert only_gap(db, "Was")["last_good"] == "2026-01-02"
    assert only_gap(db, "Never")["last_good"] == "never"


def test_any_kind_of_crawler_can_fail(db: sqlite3.Connection) -> None:
    add_crawls(db, "Acme", [(1, ""), (0, "request failed"), (0, "request failed")], ats="greenhouse")
    assert only_gap(db, "Acme")["kind"] == "failing"


def test_rows_are_read_in_time_order_with_the_later_row_winning_a_tie(db: sqlite3.Connection) -> None:
    rows = [
        ("Late", DAY.format(3), 0, FAIL),  # inserted before the older rows
        ("Late", DAY.format(2), 0, FAIL),
        ("Late", DAY.format(1), 1, ""),
        ("Tie", DAY.format(1), 1, ""),
        ("Tie", DAY.format(1), 0, FAIL),  # same time, later row
        ("Tie", DAY.format(1), 0, FAIL),
    ]
    db.executemany(
        "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
        [(t, c, "x", o, 0, n) for c, t, o, n in rows],
    )
    late = only_gap(db, "Late")
    assert (late["since"], late["last_good"]) == (DAY.format(2), "2026-01-01")
    assert only_gap(db, "Tie")["kind"] == "failing"  # [ok, fail, fail]: the last two failed


def test_a_null_note_counts_as_no_note_and_a_null_company_is_skipped(db: sqlite3.Connection) -> None:
    add_crawls(db, "Quiet", [(0, None), (0, None)])
    db.execute(
        "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)", (DAY.format(1), None, "x", 0, 0, "")
    )
    assert only_gap(db, "Quiet")["note"] == ""
    assert [g["company"] for g in digest.coverage_gaps(db)] == ["Quiet"]


def test_coverage_gaps_without_a_crawl_log_a_note_column_or_any_rows(db: sqlite3.Connection) -> None:
    assert digest.coverage_gaps(db) == []  # an empty crawl log
    bare = sqlite3.connect(":memory:")
    assert digest.coverage_gaps(bare) == []  # no crawl_log table at all
    bare.execute("CREATE TABLE crawl_log(ts TEXT, company TEXT, ats TEXT, ok INTEGER, n_jobs INTEGER)")
    bare.executemany(
        "INSERT INTO crawl_log VALUES(?,?,?,?,?)",
        [(DAY.format(1), "Old", "browser", 0, 0), (DAY.format(2), "Old", "browser", 0, 0)],
    )
    gap = only_gap(bare, "Old")  # a database from before the note column and the alert table
    assert (gap["kind"], gap["note"], gap["new"]) == ("failing", "", True)


def test_mark_alerted_creates_its_table_and_is_idempotent() -> None:
    bare = sqlite3.connect(":memory:")
    gaps = [{"company": "A", "since": DAY.format(1), "kind": "failing"}]
    digest.mark_alerted(bare, gaps)
    digest.mark_alerted(bare, gaps)
    assert bare.execute("SELECT company, since, kind FROM gap_alerts").fetchall() == [("A", DAY.format(1), "failing")]


def test_a_problem_in_the_gap_check_comes_back_as_text(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    assert digest.safe_coverage_gaps(db) == ([], "")
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    gaps, error = digest.safe_coverage_gaps(db)  # a malformed timestamp
    assert gaps == []
    assert error.startswith("ValueError")
    assert f"::warning::coverage check failed, {error}" in capsys.readouterr().err
    db.execute("DROP TABLE crawl_log")
    db.execute("CREATE TABLE crawl_log(x)")  # a table of the wrong shape
    assert digest.safe_coverage_gaps(db)[1].startswith("OperationalError")


def gap(company: str, kind: str, note: str, new: bool = True, last_good: str = "2026-01-02") -> dict[str, object]:
    return {"company": company, "kind": kind, "note": note, "new": new, "last_good": last_good, "since": DAY.format(1)}


def test_render_lists_the_gaps_under_the_jobs() -> None:
    jobs = [{"company": "A", "title": "PM", "location": "TLV", "url": "http://u", "pm": True}]
    gaps = [
        gap("Blocked & Co", "failing", FAIL),
        gap("Held", "held", HELD, new=False),
        gap("Gone", "closed", PARTIAL),
        gap("Silent", "failing", "", new=False, last_good="never"),
        gap("Long", "failing", "x" * 300),
    ]
    subject, body = digest.render(jobs, gaps)
    assert subject.startswith("New Israel jobs: 1 (")  # the gaps do not change the subject when there are jobs
    assert "<h3>Not crawled properly (5)</h3>" in body
    assert "<b>Blocked &amp; Co</b> <b>new</b>: not crawled: HTTP 403, title=&#x27;Access Denied&#x27; " in body
    assert "<b>Held</b>: crawl held, nothing closed: " in body  # not new: no tag
    assert "<b>Gone</b> <b>new</b>: jobs closed after a sharp drop was believed: " in body
    assert "got 4, 20 open before" in body  # the counts come from the note
    assert "<b>Silent</b>: not crawled <i>(last good crawl never)</i>" in body  # no note: no colon
    assert "x" * digest.GAP_NOTE_MAX in body
    assert "x" * (digest.GAP_NOTE_MAX + 1) not in body
    assert body.index("Product roles") < body.index("Not crawled properly")


def test_render_subject_is_an_alert_counting_only_the_new_gaps() -> None:
    gaps = [gap("A", "failing", FAIL), gap("B", "held", HELD, new=False)]
    subject, body = digest.render([], gaps)
    assert subject.startswith("Job crawl alert: 1 new (")
    assert "Not crawled properly (2)" in body  # the list shows both
    assert "<b>A</b> <b>new</b>" in body
    assert "<b>B</b>: " in body
    assert "No new matching jobs today." in body
    quiet, _ = digest.render([], [gap("Blocked", "failing", FAIL, new=False)])
    assert quiet.startswith("New Israel jobs: 0")  # an old gap alone is not news


def test_render_says_when_the_gap_check_failed() -> None:
    subject, body = digest.render([], [], "ValueError: bad <ts>")
    assert subject.startswith("New Israel jobs: 0")
    assert "<b>Coverage check failed</b>: ValueError: bad &lt;ts&gt;. See the workflow log." in body


def test_main_preview_prints_the_gaps_and_commit_marks_them_alerted(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    add_crawls(db, "Held", [(0, HELD), (0, HELD)])
    monkeypatch.setattr(sys, "argv", ["digest.py"])
    digest.main()
    out = capsys.readouterr().out
    assert out.startswith("Job crawl alert: 2 new (")
    assert re.search(r"^GAP new Blocked +failing +HTTP 403", out, re.M)
    assert re.search(r"^GAP new Held +held +sharp drop", out, re.M)
    assert "Not crawled properly (2)" in Path("data/digest.html").read_text()
    assert not db.execute(
        "SELECT name FROM sqlite_master WHERE name='gap_alerts'"
    ).fetchall()  # a preview alerts nothing
    monkeypatch.setattr(sys, "argv", ["digest.py", "--commit"])
    digest.main()
    assert db.execute("SELECT COUNT(*) FROM gap_alerts").fetchone()[0] == TWO_GAPS
    capsys.readouterr()
    digest.main()
    assert re.search(r"^GAP     Held", capsys.readouterr().out, re.M)  # listed still, but no longer new
