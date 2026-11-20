import datetime
import sqlite3

import pytest

import run

START = datetime.date(2026, 1, 1)


def crawls(db: sqlite3.Connection, company: str, rows: list[tuple[int, int]], first_day: int = 0) -> None:
    """One crawl_log row per day from START + first_day: (ok, n_jobs)."""
    for offset, (ok, n_jobs) in enumerate(rows, start=first_day):
        ts = f"{START + datetime.timedelta(days=offset)}T06:00:00"
        db.execute(
            "INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES(?,?,?,?,?,?)",
            (ts, company, "x", ok, n_jobs, ""),
        )
    db.commit()


def empty(days: int) -> list[tuple[int, int]]:
    return [(1, 0)] * days


def test_a_board_empty_for_thirty_days_is_dead(db: sqlite3.Connection) -> None:
    crawls(db, "Quiet", empty(31))  # day 0 to day 30: the run is 30 days old on the newest day
    crawls(db, "Busy", [(1, 5)] * 31)
    assert [b["company"] for b in run.dead_boards(db)] == ["Quiet"]


def test_a_company_crawled_for_less_than_thirty_days_is_not_judged(db: sqlite3.Connection) -> None:
    crawls(db, "Busy", [(1, 5)] * 40)
    crawls(db, "Young", empty(10), first_day=30)
    assert run.dead_boards(db) == []


def test_a_job_or_a_failing_last_crawl_is_not_dead(db: sqlite3.Connection) -> None:
    crawls(db, "Hired", [*empty(35), (1, 2), *empty(4)])  # a job 4 days ago: not dead
    crawls(db, "Failing now", [*empty(39), (0, 0)])  # failing now: a gap, not a dead board
    crawls(db, "Open now", [*empty(39), (1, 3)])
    assert run.dead_boards(db) == []


def test_a_failed_crawl_in_between_does_not_restart_the_count(db: sqlite3.Connection) -> None:
    crawls(db, "Flaky", [(1, 4), *empty(34), (0, 0), *empty(4)])
    (board,) = run.dead_boards(db)
    assert board["company"] == "Flaky"
    assert board["since"] == "2026-01-02T06:00:00"  # the first crawl after the last job


def test_the_thirty_day_boundary_from_both_sides(db: sqlite3.Connection) -> None:
    crawls(db, "Busy", [(1, 5)] * (run.DEAD_DAYS + 1))  # sets the newest day, 30 days after the first crawl
    crawls(db, "Almost", empty(run.DEAD_DAYS), first_day=1)  # first crawl 29 days before the newest day
    crawls(db, "Exact", empty(run.DEAD_DAYS + 1))
    assert [b["company"] for b in run.dead_boards(db)] == ["Exact"]


@pytest.mark.parametrize(("age", "is_new"), [(30, True), (32, True), (33, False)])
def test_new_covers_the_last_three_crawl_days(db: sqlite3.Connection, age: int, is_new: bool) -> None:
    crawls(db, "Quiet", [(1, 4), *empty(age + 1)])  # empty from day 1: `age` days before the newest day
    (board,) = run.dead_boards(db)
    assert board["new"] is is_new


def test_a_company_crawled_exactly_the_stale_limit_ago_still_shows(db: sqlite3.Connection) -> None:
    crawls(db, "Recent", empty(36))  # last crawl on day 35
    crawls(db, "Busy", [(1, 5)] * (36 + run.GAP_STALE_DAYS))  # newest day 42: seven days later
    assert [b["company"] for b in run.dead_boards(db)] == ["Recent"]


def test_a_company_not_crawled_lately_is_skipped(db: sqlite3.Connection) -> None:
    crawls(db, "Gone", empty(35))
    crawls(db, "Busy", [(1, 5)] * (35 + run.GAP_STALE_DAYS + 1))
    assert run.dead_boards(db) == []


def test_a_null_job_count_is_no_job(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('2025-12-01T06:00:00','N','x',1,NULL,'')")
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('2026-01-05T06:00:00','N','x',1,NULL,'')")
    assert [b["company"] for b in run.dead_boards(db)] == ["N"]


def test_no_history_is_no_dead_board(db: sqlite3.Connection) -> None:
    assert run.dead_boards(db) == []


def test_a_board_is_new_when_it_crossed_thirty_days_lately(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    crawls(db, "Fresh", [(1, 4)] * 10 + empty(31))  # empty from day 10 to 40: 30 days
    crawls(db, "Old", [(1, 4), *empty(40)])  # empty for 40 days
    run.log_dead_boards(db)
    assert capsys.readouterr().out == ("::warning::dead boards: 2 (1 new), no job for 30 days: Fresh, Old\n")
    boards = {b["company"]: b for b in run.dead_boards(db)}
    assert boards["Fresh"]["new"] and not boards["Old"]["new"]


def test_old_dead_boards_are_a_plain_line(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    crawls(db, "Old", empty(60))
    run.log_dead_boards(db)
    assert capsys.readouterr().out == "dead boards: 1 (0 new), no job for 30 days: Old\n"


def test_no_dead_board_logs_nothing(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    crawls(db, "Busy", [(1, 5)] * 40)
    run.log_dead_boards(db)
    assert capsys.readouterr().out == ""


def test_the_coverage_line_is_unchanged_and_the_alert_follows_it(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    crawls(db, "Quiet", empty(60))
    crawls(db, "Blocked", [(1, 3)] * 58 + [(0, 0), (0, 0)])
    assert run.log_coverage_gaps(db) is True
    assert capsys.readouterr().out == (
        "::warning::coverage gaps: 1 (1 new): failing 1 (Blocked)\ndead boards: 1 (0 new), no job for 30 days: Quiet\n"
    )


def test_the_dead_board_line_is_skipped_when_the_gap_check_failed(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    crawls(db, "Quiet", empty(60))
    assert run.log_coverage_gaps(db) is False
    assert "dead boards" not in capsys.readouterr().out


def test_a_broken_dead_board_check_warns_and_never_fails_the_step(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(_c: sqlite3.Connection) -> list[dict[str, object]]:
        raise ValueError("secret detail")

    monkeypatch.setattr(run, "dead_boards", boom)
    assert run.log_coverage_gaps(db) is True
    assert capsys.readouterr().out == "::warning::dead board check failed, ValueError\n"
