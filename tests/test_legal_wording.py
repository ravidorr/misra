"""The legal wording of PLAN.md step 6.6: the privacy notice, the terms, and the links to them. The statements are
checked against the written lawyer outcome (PRD "Before launch"); what the code does is covered by the other tests."""

import re
from pathlib import Path

import build_dashboard
import publish_backup

REPO = Path(__file__).resolve().parents[1]
CONTACT = "raanan@avidor.org"
MAX_TERMS_BULLETS = 8
PROMISED_DAYS = 30
SECRET_MENTIONS = 2
FOOTERS = 2  # the confirmation email and the digest
DIGEST_FOOTER = (
    "You are receiving this {{schedule}} email because you confirmed job alerts on Misra "
    "(https://ravidorr.github.io/misra/). Misra is a free, non-commercial project run by Raanan Avidor "
    "(raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel. Job listings are collected automatically "
    "from public career pages and are provided as-is, without guarantee that they are accurate, complete or "
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
    assert (
        "If you turn on job alerts, your email address is also used to send you the job alerts you asked for, "
        "and for nothing else."
    ) in body  # the lawyer's sentence, true since the signup opened


def test_the_privacy_notice_names_the_providers_and_where_they_are() -> None:
    body = sections(read("PRIVACY.md"))["Providers and where they are"]
    for provider, place in PROVIDERS:
        assert re.search(rf"\*\*{provider}\*\* \({place}\)", body), provider
    assert "processed on these providers' infrastructure, in the EU and in the United States" in body
    assert "your account is stored at Supabase in the EU" in body
    assert (
        "Supabase, Resend, and GitHub each include their standard data processing agreement in their terms of "
        "service, and it applies to our use of them."
    ) in body
    assert (
        "your account is stored at Supabase in the EU, a daily backup copy is kept at GitHub in the United States, "
        "and Google, in the United States, handles the sign-in."
    ) in body
    assert (
        "**Resend** (Ireland): sends the site-report and contact-form emails to the maintainer and, if you turn on "
        "job alerts, processes your email address and the matched job links to deliver your confirmation and job "
        "alert emails."
    ) in body
    assert (  # the lawyer's answer of 2026-11-17, word for word (A1)
        "- **Gmail** (Google, United States): receives failure alerts about the daily run, as well as the emails sent "
        "from the site-report and contact forms to the maintainer's own mailbox.\n"
    ) in read("PRIVACY.md")
    assert "Google SMTP" not in body


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
    "To prevent spam, we store a daily non-reversible hash of your network address that is deleted in the next "
    "daily cleanup."
)


def test_the_spam_guard_sentence_is_the_lawyers_in_the_notice_and_the_form_and_a_daily_cleanup_backs_it() -> None:
    text = read("PRIVACY.md")
    assert SPAM_GUARD in text
    assert (
        "With it we keep how many reports came from that hash that day, and how many reports were emailed that "
        "day in all."
    ) in text
    assert ('The form is the "Add a company" button on the dashboard\'s coverage card.') in text.split(
        "**Reporting a missing career site**"
    )[1].split("\n")[0]  # the lawyer's answer of 2026-11-17
    assert 'The form is the "Report a site" button' not in text
    assert "whenever the next report is handled" not in text  # the old promise, replaced by the daily cleanup
    assert (
        "24 hours" not in text.split("**Reporting a missing career site**")[1].split("\n")[0]
    )  # the lawyer's last word
    assert f"Reports are emailed directly to the maintainer. {SPAM_GUARD} <a" in read("templates/dashboard.html")
    assert "report_site_cleanup" in read("sync_supabase.py")  # the promise is kept by this call
    assert "report_site_cleanup" in read("supabase/migrations/20261119100000_report_site_cleanup.sql")


def test_the_privacy_notice_contact_is_a_direct_address_not_only_a_public_issue() -> None:
    body = sections(read("PRIVACY.md"))["Contact"]
    assert CONTACT in body
    # the lawyer's text of 2026-11-17 (A3); the maintainer alone made the Contact link a word link and dropped the
    # GitHub-issue and security-note sentences on 2026-11-18
    assert body.strip() == (
        f"Raanan Avidor, <{CONTACT}>, for any question, a request to see, correct or delete your data, or a "
        "complaint. The [Contact page](https://ravidorr.github.io/misra/contact.html) lists every way to reach us. Its "
        "form emails your message, and your name and email address if you give them, to the maintainer's mailbox "
        f"through Resend. We do not store them. {SPAM_GUARD}"
    )


def test_the_privacy_notice_covers_the_contact_form_with_the_lawyers_words_of_2026_11_17() -> None:
    text = read("PRIVACY.md")
    parts = sections(text)
    assert (
        "we store nothing about you. The one exception is the short-lived spam counter described below, "
        "if you report a site or use the contact form."
    ) in parts["What we store"]
    assert (  # A: his bullet of round 12, word for word
        "- **Contact form** needs no sign-in. Your message, and your name and email address if you give them, are "
        "emailed to the maintainer's mailbox through Resend and not stored by us anywhere else. To prevent spam, we "
        "store a daily non-reversible hash of your network address that is deleted in the next daily cleanup.\n"
    ) in text
    assert (
        text.index("- **Reporting a missing career site**")
        < text.index("- **Contact form**")
        < text.index("- **AI assistant connection**")
    )
    assert (  # B1, his sentences of round 12
        "- Signing in is optional and voluntary. Without it the dashboard works the same, and nothing about you is "
        "kept by us, apart from the short-lived spam counter described below if you report a site or use the contact "
        "form.\n"
    ) in parts["The short version"]
    assert (  # A2
        "Emails sent through the contact form (and the site-report form) are not stored in our database. They remain "
        "in the maintainer's mailbox until the conversation is resolved or you ask us to delete them, and in "
        "Resend's delivery logs for the short retention period set by Resend."
    ) in parts["How long we keep it"]
    assert "places" not in text.split("Your **filters**")[1].split("\n")[0]  # B5
    assert "regions, levels, role types" in text


def test_the_privacy_notice_and_the_assistant_page_have_no_em_dash_and_the_assistant_heading_is_not_repeated() -> None:
    for name in ("PRIVACY.md", "ASSISTANT.md"):
        assert "\u2014" not in read(name), name  # commas, colons or parentheses instead (his answer of 2026-11-17, C2)
    assert read("ASSISTANT.md").count("What your assistant can see") == 1


def test_the_privacy_notice_says_where_full_job_descriptions_are_stored() -> None:
    text = read("PRIVACY.md")
    assert (  # his text of 2026-11-17 (C1), word for word; `data/jobs.db` is no longer in the public repository
        "- **The jobs data is public.** Every visitor's browser loads the open roles from Supabase (so Supabase and "
        "GitHub see the request, as above). The job listings, job history, and full job descriptions (which may "
        "include business contact details published by the employer, such as a recruiter's name or work email) are "
        "stored in our Supabase database in the EU and contain only public job postings.\n"
    ) in text
    assert "data/jobs.db" not in text


def test_the_privacy_notice_has_no_draft_marks_and_a_date() -> None:
    text = read("PRIVACY.md")
    assert not re.search(r"\((NEW|CHANGED)\)|Draft for legal review", text)
    assert re.search(r"^Last updated: \d{4}-\d{2}-\d{2}$", text, flags=re.MULTILINE)


STALE_DIGEST_WORDING = (
    "not live",
    "no real subscription exists",
    "planned and not live",
    "switched off and hidden",
    "until then no account data is sent to Resend",
    "When it is switched on",
    "once the daily email is switched on",
    "When the daily email is switched on",
)


def test_the_digest_is_described_in_the_present_tense_as_it_is_built() -> None:
    """PLAN.md step 6.6, the edits that are true only while the signup is open: the email settings, what an
    unsubscribe keeps and deletes, and the unsubscribe link carrying the account id."""
    text = read("PRIVACY.md")
    stale = [phrase for phrase in STALE_DIGEST_WORDING if phrase in text]
    assert not stale, stale
    settings = next(line for line in text.splitlines() if line.startswith("- **Email settings**"))
    for phrase in (
        'The "Job alerts" button on the dashboard, for signed-in users, turns job alerts on and off.',
        "the address it goes to (your Google email, or another one you type)",
        "the filters you copied for it",
        "whether you want it daily or weekly",
        "the time you turned it on, the time you confirmed the address",
        "and the time you unsubscribed",
        "plus the jobs already emailed to you",
        "Turning it on sends one confirmation email to that address, and nothing else is sent until you confirm.",
        "We store only a one-way code made from the confirmation link (a SHA-256 hash), never the link itself.",
        "from your account ID (a random code that identifies your account in our database; it is not your name "
        "or email address)",
        "The link contains your account ID in plain text",
        "and use the link to unsubscribe you",
        "deletes your filters and the list of emailed jobs and resets daily or weekly",
        "keeps the address and those three times as a record of your consent",
        "Deleting your account removes all of it, including that record.",
    ):
        assert phrase in settings, phrase
    assert 'we keep their settings (see "What we store")' in sections(text)["The short version"]


def test_the_signup_is_open_so_the_privacy_notice_must_describe_a_live_digest() -> None:
    """The guard of PLAN.md step 6.6, turned round now that the switch is on: while DIGEST_SIGNUP_ENABLED is True the
    notice must not say the digest is not live (the old guard), and it must carry the present-tense passages. The test
    fails the other way too: switching the signup off again needs the notice changed in the same PR."""
    assert build_dashboard.DIGEST_SIGNUP_ENABLED is True
    text = read("PRIVACY.md")
    assert not [phrase for phrase in ("not live", "no real subscription exists") if phrase in text]
    assert "If you turn on job alerts" in text
    assert "if you turn on job alerts, processes your email address" in text


def test_the_agreement_line_sits_next_to_the_email_control_and_is_in_the_dialog_that_the_switch_builds() -> None:
    """PLAN.md step 6.6: "By turning this on you agree to the Terms and the Privacy notice" must be visible. The text is
    in the dialog markup (cut out only by the switch) and is a paragraph that no rule hides; the browser test of
    tests/test_digest_settings.py checks that it is visible and its links are big enough."""
    page = read("templates/dashboard.html")
    region = "".join(build_dashboard.DIGEST_REGION.findall(page))
    new_tab = 'target="_blank" rel="noopener noreferrer"'  # his answer of 2026-11-17 (B4), like the sign-in dialog
    assert (
        f'By turning this on you agree to the <a href="https://ravidorr.github.io/misra/terms.html" {new_tab}>Terms'
        '<span class="sr-only"> (opens in a new tab)</span></a>'
    ) in region
    privacy = 'and the <a href="https://ravidorr.github.io/misra/privacy.html"'
    assert f'{privacy} {new_tab}>privacy notice<span class="sr-only">' in region
    assert region.count('<span class="sr-only"> (opens in a new tab)</span></a>') == len(("Terms", "privacy notice"))
    assert "Email me new matching roles" not in region  # B1: the heading is cut; the helper text stays whole
    # The lawyer's sentences stay whole and in this order; the script only hides the first two (the confirmation
    # disclosure) while the state line above says the same (round 11, B1), so neither span may be hidden in the markup.
    assert (
        '<span id="digestConsentFirst">We first send a confirmation link to <span id="digestConsentTo"></span>. '
        "Nothing is sent until you confirm, and you get an email only when there are new matches. </span>"
        "One-click unsubscribe is in every email. By turning this on you agree to the"
    ) in region
    assert not re.search(r'id="digestConsentFirst"[^>]*hidden', region)
    assert (
        '<span id="digestAddressHintMore"> Either way, the address gets a confirmation email first, and nothing else '
        "until its owner confirms.</span>"
    ) in region
    assert not re.search(r'id="digestAddressHintMore"[^>]*hidden', region)
    assert 'id="digestConsent"' in region
    assert not re.search(r'id="digestConsent"[^>]*hidden', region)


def test_the_terms_say_what_the_lawyer_asked_and_no_more() -> None:
    text = read("TERMS.md")
    for phrase in [
        "free, non-commercial project run by a private individual",
        "**Job alerts:** Turn on job alerts only for an email address you own or are authorized to use.",
        'provided "as is" and "as available", without warranties',
        "collected automatically from public career pages",
        "do not guarantee that they are accurate, complete or still open",
        "**Employer content and removal:** All job descriptions, trademarks, and company names belong to their",
        'shown as published, "as is", without endorsement',
        "want your listings or descriptions removed",
        "you accept the [privacy notice](./PRIVACY.md)",
        "third-party infrastructure in the EU and the United States",
        f"write to <{CONTACT}> and they will be deleted promptly",
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
    assert "To change your filters, or to delete your account and all stored data, sign in at https://" in flat
    assert CONFIRM_FOOTER in flat
    assert (
        "The link works for 7 days. If you get more than one of these emails, use the newest one: each new email "
        "stops the link in the older ones from working."
    ) in flat
    assert "Subject: `Confirm your Misra job alerts`" in texts
    assert "Someone (hopefully you) turned on {{schedule}} job alerts for {{user_email}} on Misra." in texts
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


def test_the_privacy_notice_describes_the_backup_as_it_is_built() -> None:
    """The daily export (PLAN.md step 9.1): a private GitHub repository, only the maintainer, up to 30 days. The
    numbers are the ones publish_backup.py enforces, so the promise cannot drift from the code."""
    text = read("PRIVACY.md")
    parts = sections(text)
    assert "**Backups.**" in parts["What we store"]
    assert "private repository at GitHub (United States)" in parts["What we store"]
    assert "Only the maintainer has access to it" in parts["What we store"]
    assert "only how many rows each table held" in parts["What we store"]
    assert "a private repository for up to 30 days" in parts["The short version"]
    assert "private backup repository" in parts["Who can see it"]
    assert "separate private repository that holds the daily backup copy" in parts["Providers and where they are"]
    keep = parts["How long we keep it"]
    assert f"replaced by a single new copy when it is {publish_backup.MAX_AGE_DAYS} days old" in keep
    assert "a deleted account leaves our backups within 30 days" in keep
    assert "GitHub may keep the replaced copies for a short time" in keep
    assert publish_backup.MAX_AGE_DAYS + 1 < PROMISED_DAYS
    assert (
        "A copy remains in the daily backup until the backup is replaced, within 30 days"
        in parts["How to delete your data"]
    )


def test_the_security_note_names_the_backup_its_secret_and_its_review() -> None:
    text = " ".join(read("SECURITY.md").split())  # the file is wrapped by hand
    assert "upgrade to a plan with backups or build the export" not in text  # the old gap: the export exists now
    assert "BACKUP_DEPLOY_KEY" in text
    assert "ravidorr/misra-backups" in text
    assert "restore was rehearsed on 2026-11-15" in text
    review = text.split("### Yearly review", 1)[1].split("### If there is a breach", 1)[0]
    assert "rehearse a restore again" in review
    assert "misra-backups" in review and "BACKUP_DEPLOY_KEY" in review


def test_the_security_note_scopes_and_rotates_the_backup_key_and_forbids_stray_clones() -> None:
    text = " ".join(read("SECURITY.md").split())
    assert "scoped to `ravidorr/misra-backups` only and reaches no other repository" in text
    assert "A deploy key has no expiry date" in text
    assert "then delete the old key in that repository's deploy keys" in text
    assert "The first rotation is due by 2027-11-15." in text
    assert "the local clone (and any restore script made from it) is deleted immediately afterwards" in text
    assert "plaintext email addresses never sit on a disk outside the 30-day rotation" in text
    for name in ("PRIVACY.md", "SECURITY.md"):
        assert "encrypted" not in read(name).lower(), name  # we claim no encryption of our own


def test_the_lawyers_round_of_2026_11_16_editorial_wording_is_in_the_notice_the_terms_and_the_security_note() -> None:
    privacy = read("PRIVACY.md")
    parts = sections(privacy)
    assert '"we" and "the maintainer" both mean Raanan Avidor' in parts["Who is responsible"]
    assert (
        "we store nothing about you. The one exception is the short-lived spam counter described below, "
        "if you report a site or use the contact form."
    ) in parts["What we store"]
    assert 'mark "Not for me" (the dashboard also calls them discards)' in parts["The short version"]
    assert (
        '- Your **stars and "Not for me" choices**. For a star, we keep the role\'s title, company, location, and '
        'link, so you can still see it after the role closes. For "Position not for me," we keep only which role it '
        'was, and that choice is removed when the role closes. For "Company not for me," we keep the company\'s name '
        'until you clear it or delete your account. An account can keep a limited number of stars and "Not for me" '
        "choices (2,000)."
    ) in parts["What we store"]
    assert "Supabase's free plan keeps no backups for us." in parts["How long we keep it"]
    assert (
        "apart from the short-lived spam counter described below if you report a site or use the contact form."
        in parts["The short version"]
    )
    assert "which signs the link (a keyed signature)" in privacy
    assert "The signed sign-in pass (token) your browser holds" in privacy
    assert "one-way codes (keyed hashes, made with a secret that changes every day;" in privacy
    assert "Supabase may keep database backups" not in privacy
    assert (
        "the small programs (Edge Functions) behind the report form, the contact form, account deletion, job alerts, "
        "the confirmation and" in privacy
    )
    assert "Gmail (Google SMTP)" not in privacy
    assert "daily summary email" not in privacy
    assert "the private route in [the security note]" not in privacy  # the maintainer dropped the pointer, 2026-11-18
    assert "[SECURITY.md]" not in privacy  # a file name is not link text
    assert "stars, discards" not in privacy
    terms = read("TERMS.md")
    assert "hobby" not in terms
    assert "- **Job alerts:** Turn on job alerts only for an email address you own or are authorized to use." in terms
    security = " ".join(read("SECURITY.md").split())
    assert "When the daily email exists" not in security
    assert (
        "For job alerts: the address they go to, the filters copied for them, daily or weekly, the jobs already"
        in security
    )
    assert (
        security.count("`UNSUBSCRIBE_SECRET` (the key that signs the unsubscribe links)") == SECRET_MENTIONS
    )  # secrets, breach
    assert "Changing `UNSUBSCRIBE_SECRET` stops the unsubscribe links in emails already sent;" in security
    assert 'people can still turn the email off on the dashboard or reply "Remove".' in security


def test_the_screens_carry_the_lawyers_round_of_2026_11_16_editorial_wording() -> None:
    page = read("templates/dashboard.html")
    for phrase in (
        "They are kept in our database in the EU (Supabase), with a daily backup copy at GitHub in the United States. "
        "We never store your CV or a password, and we set no tracking cookies.",
        "A copy remains in the daily backup until the backup is replaced, within 30 days; it is never put back into "
        "the live service.",
        "any job alert settings and the record of your consent to them from our database.",
        '<p id="digestIntro">Get the new roles that match your filters by email.</p>',
        " Either way, the address gets a confirmation email first, and nothing else until its owner confirms.",
    ):
        assert phrase in page, phrase
    assert "Privacy Notice" not in page
    digest = read("templates/digest.js")
    assert 'press "Confirm my email"; until then nothing is sent. The link works for 7 days.' in digest
    assert 'job alerts start only after you open it and press "Confirm my email".' in digest
    assert "The filters saved for the email were deleted; your dashboard filters stay as they are." in digest
    email = read("templates/email.js")
    assert (
        "It may have expired (confirmation links work for 7 days), been replaced by a newer link, or belong to job"
        in email
    )
    assert (
        "If you got more than one confirmation email, use the link in the newest one. To manage your job alerts or "
        "delete your account, sign in on the Misra dashboard. If your job alerts are waiting for confirmation, press "
        '"Resend confirmation" there to get a new link.'
    ) in email
    assert "This unsubscribe link no longer works" in email
    assert (
        "Your account may have been deleted, or we may have replaced the key that signs these links. To stop job "
        "alerts, sign in on the Misra dashboard and turn them off, or write to raanan@avidor.org and we will stop "
        "them for you."
    ) in email
    assert (
        "Your job alerts are stopped. We deleted the filters saved for them and the list of jobs we already "
        "emailed you." in email
    )
    assert "as a record of your consent." in email
    assert "roles Misra finds from now on" in email


def test_the_documents_carry_their_dates() -> None:
    for name, day in (("PRIVACY.md", "2026-11-17"), ("TERMS.md", "2026-11-17"), ("SECURITY.md", "2026-11-16")):
        assert f"\nLast updated: {day}\n" in read(name), name


def test_the_lawyers_assistant_wording_of_2026_11_17_is_in_the_notice_and_the_terms() -> None:
    privacy = read("PRIVACY.md")
    assert (
        "- **AI assistant connection** needs no sign-in. We do not log your search words, the jobs your assistant "
        "reads, or your network address. To enforce daily rate limits, our database keeps a short-lived counter per "
        "day: a keyed, non-reversible hash of your network address (or /64 IPv6 prefix, made with a secret key that "
        "changes every day) and the number of calls made that day. Counters from earlier days are deleted in the "
        "next daily cleanup."
    ) in privacy
    assert (
        "- **Provider logs.** Supabase's own platform logs record each request to its services, including the small "
        "programs (Edge Functions) behind the report form, the contact form, account deletion, job alerts, the "
        "confirmation and unsubscribe links, and the AI assistant connection: the caller's IP address, approximate "
        "location, user agent, and the request path."
    ) in privacy
    assert (
        "- **AI assistant connection:** You may connect an AI assistant to Misra solely for your own personal, "
        "non-commercial job search. Automated bulk querying, scraping, or republishing of job descriptions, or using "
        "Misra's endpoint or data to train or fine-tune AI models, is prohibited. Job descriptions remain the "
        'property of the respective employers, are provided "as is", and are removed from both the dashboard and the '
        "assistant connection promptly upon an employer's request."
    ) in read("TERMS.md")


def test_the_contact_form_is_on_with_the_lawyers_wording_and_the_page_never_writes_the_address() -> None:
    """The lawyer agreed on 2026-11-17 (round 11) to the form and gave his PRIVACY.md bullet on 2026-11-18
    (round 12): both are in, so the build constant is on. The function answers 404 until its secret is true.
    Closing the form later means unsetting that secret, and then setting the constant back to False."""
    assert build_dashboard.CONTACT_FORM_ENABLED is True
    function = (REPO / "supabase" / "functions" / "contact" / "handler.ts").read_text(encoding="utf-8")
    assert "CONTACT_FORM_ENABLED" in function
    assert "deps.enabled" in function
    assert CONTACT not in function
    page = (REPO / "templates" / "contact.html").read_text(encoding="utf-8")
    script = (REPO / "templates" / "contact.js").read_text(encoding="utf-8")
    assert CONTACT not in page + script
    assert (  # his line above the Send button (A4), word for word; the link is the privacy notice in a new tab
        'id="contactPrivacy">Your message (and your name and email address, if given) is emailed to the maintainer. '
        f'{SPAM_GUARD} <a href="privacy.html" target="_blank" rel="noopener noreferrer">Privacy notice'
        '<span class="sr-only"> (opens in a new tab)</span></a></p>'
    ) in page
    assert page.index('id="contactPrivacy"') < page.index('id="contactSend"')
    todo = (REPO / "TODO.md").read_text(encoding="utf-8")
    assert "Turn the contact form on" not in todo
    assert "Ask the lawyer to confirm one bullet" not in todo
