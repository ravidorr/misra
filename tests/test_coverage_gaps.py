import runpy
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

import run
from common import DROP_BELIEVED, DROP_NOTE
from tests.conftest import add_crawls, write_json

HELD = f"{DROP_NOTE}: got 0, 20 open before"
BELIEVED = f"{DROP_BELIEVED}: got 0, 5 open before"
PARTIAL = f"{DROP_BELIEVED}: got 4, 20 open before"
FAIL = "HTTP 403, title='Access Denied'"
DAY = "2026-01-{:02d}T06:00:00"
STALE_BOUNDARY = run.GAP_STALE_DAYS
TWO_GAPS = 2


def gaps_of(db: sqlite3.Connection, company: str) -> list[dict[str, Any]]:
    return [g for g in run.coverage_gaps(db) if g["company"] == company]


def only_gap(db: sqlite3.Connection, company: str) -> dict[str, Any]:
    (gap,) = gaps_of(db, company)
    return gap


def alert(db: sqlite3.Connection) -> None:
    """What logging the gaps does for them."""
    run.mark_alerted(db, run.coverage_gaps(db))
    db.commit()


def test_coverage_gaps_ignore_healthy_and_one_off_failures(db: sqlite3.Connection) -> None:
    add_crawls(db, "Healthy", [(1, ""), (1, "")])
    add_crawls(db, "OneOff", [(1, ""), (0, FAIL)])  # failed once: not failing yet, as in the dashboard
    add_crawls(db, "Recovered", [(0, FAIL), (0, FAIL), (1, "")])
    assert run.coverage_gaps(db) == []


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


def test_a_gap_is_logged_once_per_outage_and_a_new_outage_is_new_again(db: sqlite3.Connection) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    assert only_gap(db, "Blocked")["new"] is True
    alert(db)
    add_crawls(db, "Blocked", [(0, FAIL), (0, FAIL)], start_day=4)  # the same outage, days later
    assert only_gap(db, "Blocked")["new"] is False
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)], start_day=6)  # recovered, then blocked again
    gap = only_gap(db, "Blocked")
    assert (gap["since"], gap["new"]) == (DAY.format(7), True)


def test_computing_the_gaps_leaves_the_gap_new(db: sqlite3.Connection) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    run.coverage_gaps(db)  # computing the gaps alone alerts nothing
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
    assert run.coverage_gaps(db) == []


@pytest.mark.parametrize("note", [BELIEVED, PARTIAL])
def test_a_believed_drop_is_listed_until_the_log_carried_it(db: sqlite3.Connection, note: str) -> None:
    add_crawls(db, "Small", [(0, HELD)] * 3 + [(1, note), (1, ""), (1, "")])  # healthy since, and still listed
    gap = only_gap(db, "Small")
    assert (gap["kind"], gap["note"], gap["since"], gap["new"]) == ("closed", note, DAY.format(4), True)
    alert(db)
    assert gaps_of(db, "Small") == []  # logged once: gone for good


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
    assert late["since"] == DAY.format(2)
    assert only_gap(db, "Tie")["kind"] == "failing"  # [ok, fail, fail]: the last two failed


def test_a_null_note_counts_as_no_note_and_a_null_company_is_skipped(db: sqlite3.Connection) -> None:
    add_crawls(db, "Quiet", [(0, None), (0, None)])
    db.execute(
        "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)", (DAY.format(1), None, "x", 0, 0, "")
    )
    assert only_gap(db, "Quiet")["note"] == ""
    assert [g["company"] for g in run.coverage_gaps(db)] == ["Quiet"]


def test_coverage_gaps_without_a_crawl_log_a_note_column_or_any_rows(db: sqlite3.Connection) -> None:
    assert run.coverage_gaps(db) == []  # an empty crawl log
    bare = sqlite3.connect(":memory:")
    assert run.coverage_gaps(bare) == []  # no crawl_log table at all
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
    run.mark_alerted(bare, gaps)
    run.mark_alerted(bare, gaps)
    assert bare.execute("SELECT company, since, kind FROM gap_alerts").fetchall() == [("A", DAY.format(1), "failing")]


def test_a_problem_in_the_gap_check_comes_back_as_text(db: sqlite3.Connection) -> None:
    assert run.safe_coverage_gaps(db) == ([], "")
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    gaps, error = run.safe_coverage_gaps(db)  # a malformed timestamp
    assert gaps == []
    assert error.startswith("ValueError")
    db.execute("DROP TABLE crawl_log")
    db.execute("CREATE TABLE crawl_log(x)")  # a table of the wrong shape
    assert run.safe_coverage_gaps(db)[1].startswith("OperationalError")


def gap(company: str, kind: str, note: str, new: bool = True) -> dict[str, object]:
    return {"company": company, "kind": kind, "note": note, "new": new, "since": DAY.format(1)}


def test_gap_summary_is_counts_and_names_only() -> None:
    gaps = [
        gap("Blocked & Co", "failing", FAIL),
        gap("Held", "held", HELD, new=False),
        gap("Gone", "closed", PARTIAL),
        gap("Silent", "failing", "", new=False),
    ]
    line = run.gap_summary(gaps)
    assert line == "coverage gaps: 4 (2 new): failing 2 (Blocked & Co, Silent); held 1 (Held); closed 1 (Gone)"
    assert "403" not in line  # no crawl notes, only names and counts


def test_a_new_gap_is_a_warning_annotation_and_is_then_logged_once(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Blocked", [(1, ""), (0, FAIL), (0, FAIL)])
    add_crawls(db, "Held", [(0, HELD), (0, HELD)])
    run.log_coverage_gaps(db)
    out = capsys.readouterr().out
    assert out == "::warning::coverage gaps: 2 (2 new): failing 1 (Blocked); held 1 (Held)\n"
    assert db.execute("SELECT COUNT(*) FROM gap_alerts").fetchone()[0] == TWO_GAPS
    run.log_coverage_gaps(db)  # the next day: still listed, no longer new, so no annotation
    assert capsys.readouterr().out == "coverage gaps: 2 (0 new): failing 1 (Blocked); held 1 (Held)\n"


def test_no_gaps_log_nothing(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    add_crawls(db, "Healthy", [(1, ""), (1, "")])
    run.log_coverage_gaps(db)
    assert capsys.readouterr().out == ""
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='gap_alerts'").fetchall()


def test_a_failing_gap_check_is_a_warning_and_reports_false(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    assert run.log_coverage_gaps(db) is False
    assert capsys.readouterr().out.startswith("::warning::coverage check failed, ValueError")


def test_the_gaps_step_logs_and_is_green(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Broken", [(0, "request failed"), (0, "request failed")], ats="fake")
    db.commit()
    monkeypatch.setattr(sys, "argv", ["run.py", "--gaps"])
    with pytest.raises(SystemExit) as done:
        runpy.run_module("run", run_name="__main__")
    assert done.value.code == 0
    assert capsys.readouterr().out == "::warning::coverage gaps: 1 (1 new): failing 1 (Broken)\n"
    assert run.gaps_step() == 0
    assert capsys.readouterr().out == "coverage gaps: 1 (0 new): failing 1 (Broken)\n"


def test_the_gaps_step_goes_red_when_the_check_is_broken(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    db.commit()
    assert run.gaps_step() == 1
    assert capsys.readouterr().out.startswith("::warning::coverage check failed, ValueError")


def test_the_crawl_itself_logs_no_gaps(
    db: sqlite3.Connection, workdir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Broken", [(0, "request failed")], ats="fake")
    db.commit()
    write_json(
        workdir / "data" / "companies.json", [{"name": "Broken", "ats": "fake", "slug": "broken", "status": "ok"}]
    )
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda _slug: None)
    monkeypatch.setattr(sys, "argv", ["run.py"])
    run.main()  # failed twice now, yet the crawl step stays quiet: the gaps have their own step
    assert "coverage gaps" not in capsys.readouterr().out
