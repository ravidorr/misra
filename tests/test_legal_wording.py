"""The legal wording of PLAN.md step 6.6: the privacy notice, the terms, and the links to them. The statements are
checked against the written lawyer outcome (PRD "Before launch"); what the code does is covered by the other tests."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTACT = "raanan@avidor.org"
MAX_TERMS_BULLETS = 6
FOOTER = (
    "You are receiving this email because you signed up for job alerts on Misra (https://ravidorr.github.io/misra/). "
    "Misra is a free, non-commercial project. Unsubscribe in one click | Contact: raanan@avidor.org"
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


def test_the_documents_that_the_page_links_to_exist_in_the_repository() -> None:
    page = read("templates/dashboard.html")
    paths = set(re.findall(r"https://github\.com/ravidorr/misra/blob/main/([A-Z]+\.md)", page))
    assert paths == {"PRIVACY.md", "TERMS.md"}
    for name in paths:
        assert (REPO / name).is_file()


def test_the_documents_that_describe_the_project_link_to_the_terms() -> None:
    assert "(./TERMS.md)" in read("README.md")
    assert "(/TERMS.md)" in read("llms.txt")
    assert "(./TERMS.md)" in read("PRIVACY.md")


def test_the_digest_footer_wording_is_written_in_the_plan_and_the_prd_for_the_step_that_builds_the_email() -> None:
    plan = " ".join(read("PLAN.md").split())
    assert FOOTER in plan
    assert "Reply-To" in plan
    assert FOOTER in " ".join(read("PRD.md").split())
