import re
import runpy
import smtplib
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

import send_alert

RUN_URL = "https://github.com/o/r/actions/runs/1"


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
def smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(send_alert.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(sys, "argv", ["send_alert.py", RUN_URL])
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.delenv("DIGEST_TO", raising=False)


def test_sends_a_plain_alert_with_the_run_link(capsys: pytest.CaptureFixture[str]) -> None:
    send_alert.main()
    (server,) = FakeSMTP.instances
    assert server.args == ("smtp.gmail.com", 465, 60)
    assert server.login_args == ("me@example.com", "pw")
    msg = server.sent[0]
    assert re.fullmatch(r"Daily job run failed \(\d{4}-\d{2}-\d{2}\)", msg["Subject"])
    assert msg.get_content_type() == "text/plain"  # one plain part, no html alternative
    assert not msg.is_multipart()
    assert (msg["From"], msg["To"]) == ("me@example.com", "me@example.com")
    assert RUN_URL in msg.get_content()
    assert "sent: Daily job run failed" in capsys.readouterr().out


def test_digest_to_overrides_the_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIGEST_TO", "other@example.com")
    send_alert.main()
    assert FakeSMTP.instances[0].sent[0]["To"] == "other@example.com"


def test_a_missing_link_is_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["send_alert.py"])
    send_alert.main()
    assert "no link to the run" in FakeSMTP.instances[0].sent[0].get_content()


@pytest.mark.parametrize("missing", ["GMAIL_USER", "GMAIL_APP_PASSWORD"])
@pytest.mark.parametrize("empty", [True, False])
def test_missing_or_empty_credentials_say_so(monkeypatch: pytest.MonkeyPatch, missing: str, empty: bool) -> None:
    """An unset repository secret reaches the step as an empty string, not as a missing variable."""
    if empty:
        monkeypatch.setenv(missing, "")
    else:
        monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as stopped:
        send_alert.main()
    assert "is empty: no alert could be sent" in str(stopped.value)
    assert FakeSMTP.instances == []


def test_the_step_outcomes_are_listed_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEPS", "verify=success crawl=failure digest=skipped commit=success")
    send_alert.main()
    body = FakeSMTP.instances[0].sent[0].get_content()
    assert "Steps: verify=success crawl=failure digest=skipped commit=success" in body
    assert RUN_URL in body
    assert "may be missing" not in body  # the alert does not claim what it does not know


def test_without_step_outcomes_there_is_no_steps_line() -> None:
    send_alert.main()
    assert "Steps:" not in FakeSMTP.instances[0].sent[0].get_content()


def test_as_a_script() -> None:
    runpy.run_module("send_alert", run_name="__main__")
    assert len(FakeSMTP.instances) == 1


def test_an_empty_digest_to_secret_falls_back_to_the_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIGEST_TO", "")  # a secret that is not set renders as an empty string in Actions
    send_alert.main()
    assert FakeSMTP.instances[0].sent[0]["To"] == "me@example.com"


def test_a_failed_send_fails_the_step_and_does_not_claim_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Down(FakeSMTP):
        def send_message(self, msg: Any) -> None:
            raise smtplib.SMTPException("down")

    monkeypatch.setattr(send_alert.smtplib, "SMTP_SSL", Down)
    with pytest.raises(smtplib.SMTPException):
        send_alert.main()
    assert "sent:" not in capsys.readouterr().out


ROOT = Path(__file__).resolve().parent.parent


def test_importing_the_module_needs_no_installed_dependency() -> None:
    """It must work on the day installing requests or playwright is what failed."""
    code = (
        "import sys\n"
        "for name in ('requests', 'playwright', 'digest', 'run', 'common', 'adapters', 'discover'):\n"
        "    sys.modules[name] = None\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import send_alert\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def alert_step() -> tuple[str, str]:
    """The alert step's text in daily.yml, and the text of every step before it."""
    workflow = (ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    header = "      - name: Email an alert if the run failed\n"
    before, step = workflow.split(header)
    return header + step, before


def test_the_workflow_runs_the_alert_last_and_only_after_a_failure_with_a_checkout() -> None:
    step, before = alert_step()
    assert "- name:" not in step.split("\n", 1)[1]  # nothing after it
    assert "id: commit" in before  # after the commit step
    condition = re.search(r"^ {8}if: (.*)$", step, re.M)
    assert condition
    assert "failure()" in condition[1]
    assert "cancelled()" in condition[1]  # a job that hits its timeout is cancelled, not failed
    assert "steps.checkout.outcome == 'success'" in condition[1]  # the script is there only after a checkout
    assert re.search(r"^ {8}run: python3 send_alert\.py \"\$RUN_URL\"$", step, re.M)


def test_the_alert_step_never_sees_the_deploy_key() -> None:
    step, _before = alert_step()
    assert "DEPLOY_KEY" not in step
    assert "deploy-key" not in step
    env = re.findall(r"^ {10}([A-Z_]+):", step, re.M)
    assert env == ["GMAIL_USER", "GMAIL_APP_PASSWORD", "DIGEST_TO", "RUN_URL", "STEPS"]


def test_the_steps_the_alert_reports_have_ids_and_the_slow_ones_a_timeout() -> None:
    workflow = (ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    for step_id in ("verify", "crawl", "digest", "digests", "commit"):
        assert f"        id: {step_id}\n" in workflow, step_id
        assert f"steps.{step_id}.outcome" in alert_step()[0]
    # a step that times out fails, so it is alerted without waiting for the job
    for step_id in ("crawl", "digest", "digests"):
        assert re.search(rf"id: {step_id}\n +timeout-minutes: \d+\n", workflow), step_id
