import runpy
import smtplib
import sqlite3
import sys
from typing import Any, ClassVar

import pytest

import send_email
from tests.conftest import add_crawls, add_job


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.args = (host, port, timeout)
        self.login_args: tuple[str, str] | None = None
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def login(self, user: str, pw: str) -> None:
        self.login_args = (user, pw)

    def send_message(self, msg: Any) -> None:
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    monkeypatch.setattr(send_email.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(sys, "argv", ["send_email.py"])
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.delenv("DIGEST_TO", raising=False)
    return FakeSMTP


def test_nothing_to_send(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    send_email.main()
    assert "nothing sent" in capsys.readouterr().out
    assert FakeSMTP.instances == []


def test_dry_run(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    add_job(db, "A", "1", "Product Manager")
    monkeypatch.setattr(sys, "argv", ["send_email.py", "--dry-run"])
    send_email.main()
    assert "[dry-run] would send" in capsys.readouterr().out
    assert FakeSMTP.instances == []
    assert db.execute("SELECT reported FROM jobs").fetchone()[0] == 0


def test_send_marks_reported(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    add_job(db, "A", "1", "Product Manager")
    send_email.main()
    (server,) = FakeSMTP.instances
    assert server.args == ("smtp.gmail.com", 465, 60)
    assert server.login_args == ("me@example.com", "pw")
    assert server.sent[0]["To"] == "me@example.com"
    assert server.sent[0]["From"] == "me@example.com"
    assert "sent: New Israel jobs: 1" in capsys.readouterr().out
    assert db.execute("SELECT reported FROM jobs").fetchone()[0] == 1


def test_digest_to_override_via_main_entrypoint(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    add_job(db, "A", "1", "Product Manager")
    monkeypatch.setenv("DIGEST_TO", "other@example.com")
    runpy.run_module("send_email", run_name="__main__")
    assert FakeSMTP.instances[0].sent[0]["To"] == "other@example.com"


def html_of(msg: Any) -> str:
    return str(msg.get_body(preferencelist=("html",)).get_content())


TWO_GAPS = 2
BLOCKED = [(1, ""), (0, "HTTP 403"), (0, "HTTP 403")]


def test_a_coverage_gap_alone_sends_no_email_but_is_in_the_log(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Blocked", BLOCKED)
    send_email.main()
    out = capsys.readouterr().out
    assert FakeSMTP.instances == []
    assert "::warning::coverage gaps: 1 (1 new): failing 1 (Blocked)" in out
    assert "nothing sent" in out
    assert db.execute("SELECT COUNT(*) FROM gap_alerts").fetchone()[0] == 1  # logged: no longer new


def test_a_gap_is_new_in_the_log_once(db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]) -> None:
    add_crawls(db, "Blocked", BLOCKED)
    send_email.main()
    add_crawls(db, "Blocked", [(0, "HTTP 403")], start_day=4)  # still blocked tomorrow
    capsys.readouterr()
    send_email.main()
    out = capsys.readouterr().out
    assert "coverage gaps: 1 (0 new): failing 1 (Blocked)" in out
    assert "::warning::" not in out  # an old gap is only a log line


def test_the_email_never_has_the_not_crawled_section_even_with_crawl_problems(db: sqlite3.Connection) -> None:
    add_job(db, "A", "1", "Product Manager")
    add_crawls(db, "Blocked", BLOCKED)
    add_crawls(db, "Held", [(0, "sharp drop: got 0, 20 open before")])
    send_email.main()
    (server,) = FakeSMTP.instances
    assert server.sent[0]["Subject"].startswith("New Israel jobs: 1 (")
    body = html_of(server.sent[0])
    assert "Product roles (1)" in body
    for word in ("Not crawled", "Blocked", "Held", "last good crawl", "HTTP 403", "sharp drop"):
        assert word not in body
    assert db.execute("SELECT reported FROM jobs").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM gap_alerts").fetchone()[0] == TWO_GAPS


def test_dry_run_logs_the_gaps_without_sending_or_marking(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add_crawls(db, "Held", [(0, "sharp drop: got 0, 20 open before")])
    monkeypatch.setattr(sys, "argv", ["send_email.py", "--dry-run"])
    send_email.main()
    assert "coverage gaps: 1 (1 new): held 1 (Held)" in capsys.readouterr().out
    assert FakeSMTP.instances == []
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='gap_alerts'").fetchone()[0] == 0


def test_a_failed_send_tries_the_jobs_again_next_day(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    class Down(FakeSMTP):
        def send_message(self, msg: Any) -> None:
            raise smtplib.SMTPException("down")

    add_job(db, "A", "1", "Product Manager")
    add_crawls(db, "Blocked", BLOCKED)
    monkeypatch.setattr(send_email.smtplib, "SMTP_SSL", Down)
    with pytest.raises(smtplib.SMTPException):
        send_email.main()
    assert db.execute("SELECT reported FROM jobs").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM gap_alerts").fetchone()[0] == 1  # logged before the send, not tied to it
    monkeypatch.setattr(send_email.smtplib, "SMTP_SSL", FakeSMTP)
    send_email.main()
    assert FakeSMTP.instances[-1].sent[0]["Subject"].startswith("New Israel jobs: 1 (")


def broken_crawl_log(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO crawl_log(ts,company,ats,ok,n_jobs,note) VALUES('garbage','A','x',0,0,'')")
    db.commit()


def test_a_broken_coverage_check_does_not_stop_the_jobs_but_fails_the_step(
    db: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    add_job(db, "A", "1", "Product Manager")
    broken_crawl_log(db)
    with pytest.raises(SystemExit) as stopped:
        send_email.main()
    assert stopped.value.code == 1  # red step, after the email went out
    (server,) = FakeSMTP.instances
    assert "<b>Coverage check failed</b>: ValueError" in html_of(server.sent[0])
    assert db.execute("SELECT reported FROM jobs").fetchone()[0] == 1
    assert "::warning::coverage check failed" in capsys.readouterr().err


def test_a_broken_coverage_check_with_nothing_to_send_still_fails_the_step(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_crawl_log(db)
    with pytest.raises(SystemExit):
        send_email.main()
    assert FakeSMTP.instances == []
    monkeypatch.setattr(sys, "argv", ["send_email.py", "--dry-run"])
    with pytest.raises(SystemExit):
        send_email.main()  # the dry run reports it the same way
