"""Email a short alert that a step of the daily workflow failed. `python3 send_alert.py <run url>`.
Standard library only, so it still works when the dependencies were never installed.
Env: GMAIL_USER, GMAIL_APP_PASSWORD, DIGEST_TO (optional, defaults to GMAIL_USER), STEPS (optional, the outcome of
each workflow step, for example "crawl=failure digests=skipped").
It uses the maintainer's Gmail account, so it cannot report a Gmail outage."""

import os
import smtplib
import sys
import time
from email.message import EmailMessage


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "(no link to the run was given)"
    today = time.strftime("%Y-%m-%d")
    user, pw = os.environ.get("GMAIL_USER", ""), os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:  # an unset repository secret reaches the step as an empty string
        sys.exit("GMAIL_USER or GMAIL_APP_PASSWORD is empty: no alert could be sent")
    steps = os.environ.get("STEPS", "").strip()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = (
        f"Daily job run failed ({today})",
        user,
        os.environ.get("DIGEST_TO") or user,
    )
    body = f"The daily job workflow failed or was cancelled on {today}. If no digest arrived, the crawl or the send "
    body += "step is what failed.\n\n"
    if steps:
        body += f"Steps: {steps}\n\n"
    msg.set_content(body + f"Details: {url}\n")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"sent: {msg['Subject']}")


if __name__ == "__main__":
    main()
