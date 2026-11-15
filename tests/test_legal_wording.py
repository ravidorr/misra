"""The legal wording of PLAN.md step 6.6: the privacy notice, the terms, and the links to them. The statements are
checked against the written lawyer outcome (PRD "Before launch"); what the code does is covered by the other tests."""

import re
from pathlib import Path

import build_dashboard

REPO = Path(__file__).resolve().parents[1]
CONTACT = "raanan@avidor.org"
MAX_TERMS_BULLETS = 6
FOOTERS = 2  # the confirmation email and the digest
DIGEST_FOOTER = (
    "You are receiving this {{schedule}} digest because you confirmed job alerts on Misra "
    "(https://ravidorr.github.io/misra/). Misra is a free, non-commercial project run by Raanan Avidor "
    "(raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel. Job listings are collected automatically "
    "from public career pages and are provided as-is without guarantee that they are complete or "
    "still open."
)
CONFIRM_FOOTER = (
    "Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project run by Raanan Avidor "
    "(raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel. If you did not ask for this email, "
    "ignore it, and nothing will be sent to you."
)
UNSUBSCRIBE_HEADERS = (
    "List-Unsubscribe-Post: List-Unsubscribe=One-Click",
    "functions/v1/unsubscribe?t={{token}}>",
)
PROVIDERS = [
    ("Supabase", "EU, Ireland"),
    ("Resend", "Ireland"),
    ("Google", "United States"),
    ("GitHub", "United States"),
]


def read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def sections(text: str) -> dict[str, str]:
    """The body of each `## ` section of a Markdown file, by its title."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def test_the_privacy_notice_names_the_controller_and_how_to_reach_them() -> None:
    body = sections(read("PRIVACY.md"))["Who is responsible"]
    assert "Raanan Avidor" in body
    assert "private individual" in body
    assert CONTACT in body


def test_the_privacy_notice_says_why_the_data_is_collected_and_that_it_is_voluntary() -> None:
    body = sections(read("PRIVACY.md"))["Why we collect it, and whether you have to give it"]
    assert "not required to give us any data" in body
    assert "Signing in is voluntary" in body
    assert "cannot save your choices across devices" in body
    assert "cannot get the email" in body
    assert "planned and not live" in body  # the digest does not exist yet


def test_the_privacy_notice_names_the_providers_and_where_they_are() -> None:
    body = sections(read("PRIVACY.md"))["Providers and where they are"]
    for provider, place in PROVIDERS:
        assert re.search(rf"\*\*{provider}\*\* \({place}\)", body), provider
    assert "processed on these providers' infrastructure, in the EU and in the United States" in body
    assert "your account is stored at Supabase in the EU" in body
    assert "standard data processing agreement" in body


def test_the_privacy_notice_gives_the_rights_to_inspect_correct_and_delete_with_a_time_limit() -> None:
    body = sections(read("PRIVACY.md"))["Your rights"]
    assert "Israeli Privacy Protection Law" in body
    assert "Section 13" in body
    assert "Section 14" in body
    assert CONTACT in body
    assert "within 30 days" in body


def test_the_privacy_notice_keeps_the_accurate_points_about_the_providers_own_records() -> None:
    text = read("PRIVACY.md")
    assert "Supabase keeps them in its own internal account record" in text
    assert "until the account is deleted" in text
    assert "the caller's IP address" in text  # the provider logs
    assert "Provider logs" in text


SPAM_GUARD = (
    "To prevent spam, we store a daily non-reversible hash of your network address that expires after 24 hours."
)


def test_the_spam_guard_sentence_is_the_lawyers_in_the_notice_and_the_form_and_a_daily_cleanup_backs_it() -> None:
    text = read("PRIVACY.md")
    assert SPAM_GUARD in text
    assert "A cleanup that runs every day deletes the counters of every earlier day." in text
    assert "whenever the next report is handled" not in text  # the old promise, replaced by the daily cleanup
    assert f"Reports are emailed directly to the maintainer. {SPAM_GUARD} <a" in read("templates/dashboard.html")
    assert "report_site_cleanup" in read("sync_supabase.py")  # the promise is kept by this call
    assert "report_site_cleanup" in read("supabase/migrations/20261119100000_report_site_cleanup.sql")


def test_the_privacy_notice_contact_is_a_direct_address_not_only_a_public_issue() -> None:
    body = sections(read("PRIVACY.md"))["Contact"]
    assert CONTACT in body
    assert "do not put personal details" in body


def test_the_privacy_notice_has_no_draft_marks_and_a_date() -> None:
    text = read("PRIVACY.md")
    assert not re.search(r"\((NEW|CHANGED)\)|Draft for legal review", text)
    assert re.search(r"^Last updated: \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE)


def test_the_digest_is_described_as_planned_and_not_live() -> None:
    text = read("PRIVACY.md")
    assert "not live" in text
    assert "no real subscription exists" in text


def test_the_signup_cannot_be_switched_on_while_the_privacy_notice_still_says_the_digest_is_not_live() -> None:
    if not build_dashboard.DIGEST_SIGNUP_ENABLED:
        return
    text = read("PRIVACY.md")
    stale = [phrase for phrase in ("not live", "no real subscription exists") if phrase in text]
    assert not stale, (
        f"DIGEST_SIGNUP_ENABLED is True in build_dashboard.py but PRIVACY.md still says {stale}. "
        "Before the signup opens, apply the present-tense PRIVACY.md edits written in PLAN.md step 6.6 "
        '(the "Why we collect it", "Email settings" and "Resend" passages), '
        "so the notice describes a digest that is live."
    )


def test_the_terms_say_what_the_lawyer_asked_and_no_more() -> None:
    text = read("TERMS.md")
    for phrase in [
        "free, non-commercial hobby project",
        'provided "as is" and "as available", without warranties',
        "collected automatically from public career pages",
        "do not guarantee that they are accurate, complete or still open",
        "All trademarks and company names belong to their respective employers",
        "you accept the [privacy notice](./PRIVACY.md)",
        "third-party infrastructure in the EU and the United States",
        f"write to <{CONTACT}> and they will be removed promptly",
        "not liable for any loss or damage",
        "personal, non-commercial job searching",
        "governed by the laws of the State of Israel",
        "courts of the Tel Aviv District",
    ]:
        assert phrase in text, phrase
    assert re.search(r"^Last updated: \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE)
    assert len(re.findall(r"^- ", text, flags=re.MULTILINE)) <= MAX_TERMS_BULLETS  # a short list, not boilerplate


def test_the_page_links_to_the_legal_pages_on_the_site_and_each_has_its_document_in_the_repository() -> None:
    page = read("templates/dashboard.html")
    assert "blob/main" not in page  # no link to the Markdown files on GitHub
    assert set(re.findall(r"href=\"((?:privacy|terms|security)\.html)\"", page)) == {"privacy.html", "terms.html"}
    sources = {legal.output: legal.source for legal in build_dashboard.LEGAL_PAGES}
    for name in ("privacy.html", "terms.html"):
        assert (REPO / sources[name]).is_file()


def test_the_documents_that_describe_the_project_link_to_the_terms() -> None:
    assert "(./TERMS.md)" in read("README.md")
    assert "(https://ravidorr.github.io/misra/terms.html)" in read("llms.txt")
    assert "(./TERMS.md)" in read("PRIVACY.md")


def test_the_email_texts_are_kept_exactly_in_one_file_for_the_steps_that_build_the_emails() -> None:
    texts = read("supabase/email-texts.md")
    flat = " ".join(texts.split())
    assert DIGEST_FOOTER in flat
    assert CONFIRM_FOOTER in flat
    assert "Subject: `Confirm your Misra job alerts`" in texts
    assert "Someone (hopefully you) turned on the {{schedule}} job digest for {{user_email}} on Misra." in texts
    assert "Unsubscribe in one click (no sign-in needed):\n{{unsubscribe_url}}" in texts
    assert "Reply-To: raanan@avidor.org" in texts
    for needle in UNSUBSCRIBE_HEADERS:
        assert needle in texts
    plan = " ".join(read("PLAN.md").split())
    assert "supabase/email-texts.md" in plan
    assert "Reply-To" in plan
    prd = " ".join(read("PRD.md").split())
    assert DIGEST_FOOTER in prd
    assert CONFIRM_FOOTER in prd


def test_the_email_footers_link_to_the_pages_on_the_site() -> None:
    links = (
        "Privacy: https://ravidorr.github.io/misra/privacy.html | Terms: https://ravidorr.github.io/misra/terms.html"
    )
    assert read("supabase/email-texts.md").count(links) == FOOTERS
    for name in ("supabase/email-texts.md", "PRD.md", "PLAN.md"):
        assert "blob/main/PRIVACY.md" not in read(name), name
        assert "blob/main/TERMS.md" not in read(name), name
    assert "https://ravidorr.github.io/misra/privacy.html" in read("PRD.md")
