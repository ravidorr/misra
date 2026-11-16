"""The HTML part of the digest (send_digests.render_digest_html) and the colours both HTML emails share. The rule: the
HTML changes the layout and never a word, so the text of the HTML, tags removed and spaces collapsed, equals the plain
text (without its `---` line)."""

import json
import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from send_digests import HTML_BUDGET, Filters, Job, Subscriber, build_payload, render_digest, render_digest_html

REPO = Path(__file__).resolve().parents[1]
GMAIL_CLIP = 102_000  # bytes; Gmail cuts a longer message and shows "View entire message"
UNSUBSCRIBE = "https://abc.supabase.co/functions/v1/unsubscribe?t=" + "1" * 36 + "." + "a" * 64
BLOCK_TAGS = re.compile(r"<(?:/?(?:p|td|tr|table|div|h1|body|html|head|title)\b)[^>]*>", re.IGNORECASE)
TAG = re.compile(r"<[^>]+>")


def job(
    company: str = "Acme", title: str = "Product Manager", location: str = "Tel Aviv, Israel", url: str = ""
) -> Job:
    when = datetime(2030, 1, 5, tzinfo=UTC)
    return Job(company, "1", title, "", location, url, when, "", (), (), ())


def words_of(html: str) -> str:
    """The visible text of an email body: inline tags vanish, block tags are spaces, entities are decoded."""
    body = re.sub(r"<head>.*?</head>", "", html, flags=re.DOTALL)
    body = TAG.sub("", BLOCK_TAGS.sub(" ", body))
    return " ".join(unescape(body).split())


def plain_words(text: str) -> str:
    return " ".join(" ".join(line for line in text.split("\n") if line != "---").split())


FIXTURES = {
    "one role, no place, no link": ("daily", [job(location="")]),
    "one role": ("weekly", [job(url="https://jobs.test/a")]),
    "several": (
        "daily",
        [
            job(url="https://jobs.test/a"),
            job("Beta", "Analyst & Co", "Haifa", "https://jobs.test/b?x=1&y=2"),
            job("Gimel", "מנהל מוצר", ""),
        ],
    ),
}


@pytest.mark.parametrize("name", FIXTURES)
def test_the_html_carries_exactly_the_words_and_links_of_the_plain_text(name: str) -> None:
    frequency, jobs = FIXTURES[name]
    _, text = render_digest(frequency, jobs, UNSUBSCRIBE)
    html = render_digest_html(frequency, jobs, UNSUBSCRIBE)
    assert words_of(html) == plain_words(text)
    links = [unescape(href) for href in re.findall(r'href="([^"]*)"', html)]
    assert links and all(link in text for link in links)
    assert UNSUBSCRIBE in links


def test_the_html_is_a_plain_email_document() -> None:
    html = render_digest_html("daily", FIXTURES["several"][1], UNSUBSCRIBE)
    assert html.startswith("<!doctype html>") and '<html lang="en" dir="ltr">' in html
    for forbidden in ("<script", "<img", "<link", "<iframe", "<button", "src=", "url(", "@import", "http-equiv"):
        assert forbidden not in html
    assert not re.search(r"(?:href|src)=\"(?!https?://)", html)
    assert 'role="presentation"' in html and "<h1" in html
    assert 'dir="auto"' in html  # a title in Hebrew reads right to left


def test_what_the_crawl_gave_is_escaped_and_only_web_addresses_are_links() -> None:
    bad = job("<b>Evil</b>", '<script>alert("x")</script>', "A&B", "javascript:alert(1)")
    html = render_digest_html("daily", [bad], UNSUBSCRIBE)
    assert "<script" not in html and "<b>Evil" not in html
    assert "&lt;script&gt;" in html and "A&amp;B" in html
    assert 'href="javascript:' not in html and "javascript:alert(1)" in html  # shown as text, not linked


def test_over_the_budget_the_email_goes_out_as_text_only_and_under_it_with_the_html() -> None:
    subscriber = Subscriber("u", "ann@example.com", "weekly", datetime(2030, 1, 1, tzinfo=UTC), Filters())
    small = build_payload(subscriber, [job(url="https://jobs.test/a")], UNSUBSCRIBE)
    assert small["html"].startswith("<!doctype html>") and small["text"]
    assert small["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    long_url = "https://company.example/careers/" + "x" * 650  # the longest address in the data is 657 characters
    many = [
        job("Company", "Senior Product Manager, Growth (מנהל)", "Tel Aviv-Yafo, Israel", long_url) for _ in range(300)
    ]
    assert len(render_digest_html("weekly", many, UNSUBSCRIBE).encode()) > HTML_BUDGET
    assert "html" not in build_payload(subscriber, many, UNSUBSCRIBE)


def test_a_hundred_roles_with_typical_addresses_fit_the_budget_below_gmails_clip() -> None:
    url = "https://company.example/careers/" + "x" * 90  # the average address in the data is 68 characters
    jobs = [job("Company", "Senior Product Manager, Growth", "Tel Aviv-Yafo, Israel", url) for _ in range(100)]
    size = len(render_digest_html("weekly", jobs, UNSUBSCRIBE).encode())
    assert size < HTML_BUDGET <= GMAIL_CLIP - 10_000


def css_values(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*([^;]+);", block))


def test_the_email_colours_are_the_values_of_design_system_tokens_css() -> None:
    tokens = (REPO / "design-system/tokens.css").read_text()
    light = css_values(re.search(r":root \{(.*?)\n\}", tokens, re.DOTALL).group(1))  # type: ignore[union-attr]
    dark = css_values(re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', tokens, re.DOTALL).group(1))  # type: ignore[union-attr]
    colors = json.loads((REPO / "supabase/functions/_shared/email-colors.json").read_text())
    assert colors["light"] and colors["dark"]
    for name, value in colors["light"].items():
        assert light[name] == value, name
    for name, value in colors["dark"].items():
        assert dark[name] == value, name
