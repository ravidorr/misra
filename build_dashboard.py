"""Build the dashboard in docs/: a static page plus cacheable stylesheet, scripts and job data files."""

import base64
import hashlib
import json
import posixpath
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

import classify
import regions
from common import DROP_NOTE, ROBOTS_NOTE, crawled_companies, is_excluded, load_companies, load_excluded, open_history

# Stylesheets bundled into docs/dashboard.css, in order. Raw colors live only in tokens.css.
CSS_SOURCES = ("design-system/tokens.css", "design-system/components/*.css", "templates/dashboard.css")
# Files copied to docs/ as they are.
STATIC_FILES = (
    "templates/dashboard.html",
    "templates/dashboard.js",
    "templates/auth.js",
    "templates/session.js",
    "templates/report.js",
    "templates/theme.js",
    "templates/email.html",
    "templates/email.js",
    "templates/contact.html",
    "templates/admin.html",
    "templates/admin.js",
    "templates/not-found.html",
)
# The daily-email signup (PLAN.md steps 6.2 and 6.6). While False the page has no "Job alerts" button, no settings
# dialog and no script for them, and docs/digest.js is not built: everything between `<!--digest-->` and
# `<!--/digest-->` in templates/dashboard.html is cut out. It is True since PLAN.md step 6.6 (the signup is open),
# together with the function secret DIGEST_SIGNUP_ENABLED=true (supabase/README.md), which the function checks again on
# its own. Never set it back to False once anyone has subscribed: that also removes "Turn the email off" from the page.
DIGEST_SIGNUP_ENABLED = True
DIGEST_SCRIPT = "templates/digest.js"
DIGEST_REGION = re.compile(r"<!--digest-->(.*?)<!--/digest-->", re.DOTALL)
# The same region with its own line (indent and line break), so removing it leaves no whitespace-only line.
DIGEST_LINE = re.compile(r"^[ \t]*<!--digest-->.*?<!--/digest-->[ \t]*\n", re.DOTALL | re.MULTILINE)
# The form of the Contact page. While False, docs/contact.html has only the static contact information (the
# "Email the maintainer" section and the link to the privacy notice), docs/contact.js is not built (an old one is
# deleted) and the page's CSP has no connect-src: everything between `<!--contactform-->` and
# `<!--/contactform-->` in templates/contact.html is cut out. It is True since the lawyer's PRIVACY.md wording of
# 2026-11-18 (round 12), together with the function secret CONTACT_FORM_ENABLED=true (supabase/README.md), which the
# Edge Function `contact` checks again on its own. To close the form unset that secret (the function answers 404),
# then set this back to False.
CONTACT_FORM_ENABLED = True
CONTACT_SCRIPT = "templates/contact.js"
CONTACT_REGION = re.compile(r"<!--contactform-->(.*?)<!--/contactform-->", re.DOTALL)
CONTACT_LINE = re.compile(r"^[ \t]*<!--contactform-->.*?<!--/contactform-->[ \t]*\n", re.DOTALL | re.MULTILINE)
# The "Connect your assistant" page (PLAN.md step 8.2) and the link to it. While False, docs/assistant.html is not built
# (and an old one is deleted) and everything between `<!--assistant-->` and `<!--/assistant-->` in
# templates/dashboard.html is cut out. It is True since the lawyer approved the assistant endpoint and ASSISTANT.md
# (2026-11-17; PLAN.md step 8.1, the Edge Function `mcp`, on since the function secret ASSISTANT_ENDPOINT_ENABLED is
# "true"), together with llms.txt naming the page (a test keeps them in step). To close it, unset the secret.
ASSISTANT_PAGE_ENABLED = True
ASSISTANT_REGION = re.compile(r"<!--assistant-->(.*?)<!--/assistant-->", re.DOTALL)
ASSISTANT_LINE = re.compile(r"^[ \t]*<!--assistant-->.*?<!--/assistant-->[ \t]*\n", re.DOTALL | re.MULTILINE)
# The built pages that carry __SRI_*__ placeholders.
PAGES = ("index.html", "email.html", "contact.html", "admin.html", "404.html")
OUTPUT_NAMES = {"templates/dashboard.html": "index.html", "templates/not-found.html": "404.html"}
# GitHub Pages serves docs/404.html for a missing address at any depth, so its links and assets cannot be relative:
# this is the path the site is served from (the repository name; one line to change for a custom domain).
SITE_PATH = "/misra/"
NOT_FOUND_OUTPUT = "404.html"
RELATIVE_URL = re.compile(r'\b(href|src)="(?![a-z][a-z0-9+.-]*:|/|#)(?:\./)?([^"]*)"', re.IGNORECASE)
# Third-party scripts copied byte for byte to docs/ (vendor/README.md says where each one comes from). The page loads
# them from its own origin, so the Content-Security-Policy needs no CDN.
VENDORED_FILES = ("vendor/supabase.js",)
# The sources of the icons the pages link: the logo mark and a 180 px render of it on white, copied to docs/. The third
# is the shape of the "opens in a new tab" icon, which dashboard.css loads as a mask (a data: URI would break the CSP).
ICON_FILES = {
    "design-system/assets/new-tab.svg": "new-tab.svg",
    "design-system/assets/mark.svg": "favicon.svg",
    "design-system/assets/apple-touch-icon.png": "apple-touch-icon.png",
}
# The Google Search Console ownership file (a public value, not a secret): it must stay at this address for good, so it
# is copied to docs/ unchanged on every build. It is not HTML to lint (scripts/lint-html.mjs skips it).
VERIFICATION_FILES = ("templates/google0e1aa15dbd543e2f.html",)
# Placeholders in the page for the integrity hash of the file with that name in docs/.
INTEGRITY = re.compile(r"__SRI_([A-Z]+)_([A-Z]+)__")

REPO_URL = "https://github.com/ravidorr/misra"
LEGAL_TEMPLATE = "templates/legal.html"


@dataclass(frozen=True)
class LegalPage:
    """A page of docs/ built from a Markdown file of the repository, which stays the source of truth."""

    source: str
    output: str
    title: str
    description: str


LEGAL_PAGES = (
    LegalPage(
        "PRIVACY.md",
        "privacy.html",
        "Privacy notice - Misra",
        "What the Misra dashboard does with data: what is kept if you sign in, why, where, and how to see, correct "
        "or delete it.",
    ),
    LegalPage(
        "TERMS.md",
        "terms.html",
        "Terms of use - Misra",
        "The short as-is terms of the Misra dashboard, a free, non-commercial project.",
    ),
    LegalPage(
        "SECURITY.md",
        "security.html",
        "Security - Misra",
        "How to report a vulnerability in Misra and how the project protects its data.",
    ),
)
# The assistant page is the Markdown file ASSISTANT.md in the same template. It is built only with the switch on.
ASSISTANT_PAGE = LegalPage(
    "ASSISTANT.md",
    "assistant.html",
    "Connect your assistant - Misra",
    "How to connect your own AI assistant, such as Claude or ChatGPT, to the Misra jobs, step by step.",
)
# The About page (the Markdown file ABOUT.md, in the same template): always built.
ABOUT_PAGE = LegalPage(
    "ABOUT.md",
    "about.html",
    "About - Misra",
    "What Misra is, where its roles in Israel come from, how often it updates, what it does not do, and how the "
    "optional job alerts work.",
)


def built_pages() -> tuple[LegalPage, ...]:
    """The pages built from a Markdown file: the assistant page only while its switch is on."""
    return (*LEGAL_PAGES, ABOUT_PAGE, *((ASSISTANT_PAGE,) if ASSISTANT_PAGE_ENABLED else ()))


def legal_links() -> dict[str, str]:
    """A link from one of these pages to another page of the set stays on the site. The assistant page is in the set
    only while it is built: with its switch off a link to ASSISTANT.md goes to the file on GitHub, never to a missing
    page."""
    return {page.source: page.output for page in built_pages()}


NEW_TAB = '<span class="sr-only"> (opens in a new tab)</span>'

# The header and the footer of every page, written once (templates/parts/). A page template holds `__HEADER__` and
# `__FOOTER__`. Each slot names what the dashboard puts there and what every other page does (None: nothing). The
# dashboard has the account controls (they need auth.js) and the "last read" line (it needs dashboard.js); every other
# page has a home button in the place of the account controls and a "Home" link in the footer, which the dashboard
# does not need. The link to the page being shown gets aria-current.
PARTS = "templates/parts"
SLOTS: dict[str, tuple[str | None, str | None]] = {
    "__ACCOUNT__": ("account.inc", "home-button.inc"),
    "__FOOT_NOTE__": ("updated.inc", None),
    "__FOOT_HOME__": (None, "home-link.inc"),
}

# The backend the page reads the jobs from: the one place for the project URL and the PUBLIC anon key. Row-level
# security lets the anon key read open jobs only, so the key may live in docs/. The build puts both values into the
# page scripts (dashboard.js, auth.js, report.js and digest.js) and the URL into the page's connect-src; later steps
# (stars) read
# them too.
# Never put the service-role key here.
SUPABASE_URL = "https://dncodqeqpoqwcxdctzco.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_9gvh4YdsfErlFsgJfmNexQ_yHF-XwnA"


BLOCKED_WHY = "its site blocks automated access from GitHub's servers"
# Why a site is on the coverage gap card, in the order the card lists them. The keys are the only values the `reason`
# of a `gap` in data/companies.json may hold; the labels are what the visitor reads.
REASONS = {
    "crawl_failing": "Failing in the latest crawl",
    "bot_protection": "Blocks automated access",
    "no_careers_url": "No career page found",
    "site_unusable": "Site down, parked or unreachable",
    "no_openings": "No open roles in Israel, or the board is closed",
    "needs_reader": "Roles are listed, but we cannot read them yet",
    "robots_disallowed": "robots.txt does not allow it",
    "no_feed_found": "No job feed found yet",
}


def reason(note: str | None) -> str:
    n = note or ""
    if re.search(r"HTTP 40[13]|blocked page|Access Denied|Just a moment", n, re.I):
        return BLOCKED_WHY
    if n.startswith(DROP_NOTE):
        n = n.split(" (", 1)[0]  # the crawler's own trace after the hold is for the log, not the visitor
    return n[:120] or "the last crawl failed"


# The public job board of a company on an ATS whose board address is one fixed pattern; a person can open it in a
# browser even where the crawl is not allowed to read it. An ATS not listed here has no entry: the company's own site
# is the link then.
BOARD_URLS = {
    "smartrecruiters": "https://jobs.smartrecruiters.com/{slug}",
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "comeet": "https://www.comeet.com/jobs/{slug}",
    "bamboohr": "https://{slug}.bamboohr.com/careers",
    "jazzhr": "https://{slug}.applytojob.com/apply",
    "teamtailor": "https://{slug}/jobs",
    "pinpoint": "https://{slug}",
    "jobvite": "https://jobs.jobvite.com/{slug}",
}


def https_only(url: str | None) -> str:
    """The address when it is an https address with a host, else an empty string: the card never links to anything
    else (no http, no javascript:, no relative path)."""
    parts = urlsplit((url or "").strip())
    return parts.geturl() if parts.scheme == "https" and parts.netloc else ""


def own_site(domain: str | None) -> str:
    """The company's own site, or an empty string when the domain is missing or not a plain host name."""
    host = (domain or "").strip().lower()
    return f"https://{host}" if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", host) else ""


def careers_address(company: dict[str, Any]) -> str:
    """Where a person finds the company's roles: its careers page, else the public board of its ATS, else its own site
    (never empty when the company has a domain)."""
    board = BOARD_URLS.get(company.get("ats") or "", "")
    slug = company.get("slug") or ""
    return (
        https_only(company.get("careers_url"))
        or (https_only(board.format(slug=slug)) if board and slug else "")
        or own_site(company.get("domain"))
    )


def read_unresolved(companies: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The researched companies with no usable job feed: the records of companies.json that have a `gap` (its `reason`,
    a key of REASONS, and its `note`). A reason that is not in REASONS stops the build."""
    rows = []
    for company in companies:
        if "gap" in company:
            gap = company["gap"]
            row = {
                "name": company["name"],
                "domain": company.get("domain") or "",
                "careers_url": company.get("careers_url") or "",
                "segment": company.get("segment") or "",
                "reason": gap.get("reason"),
                "note": gap.get("note") or "",
            }
            if row["reason"] not in REASONS:
                raise ValueError(f"{company['name']} has the unknown reason {row['reason']!r}")
            rows.append(row)
    return rows


def read_career_links(companies: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """The address of each company with `links` in companies.json and where it goes: its Israeli career page
    ("israel"), else its global one ("careers"); https only, and a company with neither has no entry."""
    links = {}
    for company in companies:
        page = company.get("links") or {}
        if url := https_only(page.get("israel")):
            links[company["name"]] = (url, "israel")
        elif url := https_only(page.get("global")):
            links[company["name"]] = (url, "careers")
    return links


def gap_groups(
    blocked: list[dict[str, Any]],
    untracked: list[dict[str, Any]],
    unresolved: list[dict[str, str]],
    companies: list[dict[str, Any]] | None = None,
    links: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Every site that is not crawled, grouped by why, in REASONS order. A site named twice is counted once. Each site
    has the `url` its name links to (https only) and `to`, what the link opens: "israel" (the Israeli career page),
    "careers" (any other career page or job board) or "site" (the company's own site). The page is the one from
    `links` (Israeli, else global), else the one the data gives, or the company's own site when it has no careers page
    (`no_careers_url`) or when no careers address is known. `companies` is every crawled company of companies.json; it
    gives the address of the failing and robots.txt sites."""
    known = {c["name"]: c for c in [*untracked, *(companies or [])]}
    # a researched company (a `gap` record, not crawled) can still be in the crawl log: its record gives the address
    rows = {r["name"]: https_only(r.get("careers_url")) or own_site(r.get("domain")) for r in unresolved}
    sites: dict[str, tuple[str, str, str]] = {}
    for gap in blocked:
        url = careers_address(known.get(gap["c"], {})) or rows.get(gap["c"], "")
        if gap["why"].startswith(ROBOTS_NOTE):  # a decision to respect the site, not a failure
            sites.setdefault(gap["c"], ("robots_disallowed", "", url))  # the card says why once, for the whole group
            continue
        code = "bot_protection" if gap["why"] == BLOCKED_WHY else "crawl_failing"
        why = f"{gap['why'][:1].upper()}{gap['why'][1:]}; its roles may be old (last good crawl {gap['last']})"
        sites.setdefault(gap["c"], (code, why, url))
    for row in unresolved:  # the researched reason and note win over the bare status of companies.json
        url = own_site(row.get("domain")) if row["reason"] == "no_careers_url" else ""
        url = url or https_only(row.get("careers_url")) or own_site(row.get("domain"))
        sites.setdefault(row["name"], (row["reason"], row["note"], url))
    for company in untracked:
        code = "no_careers_url" if company["status"] == "not_found" else "no_feed_found"
        sites.setdefault(company["name"], (code, "", careers_address(company)))
    groups = []
    for code, label in REASONS.items():
        members = []
        for name, (c, why, found) in sorted(sites.items(), key=lambda site: site[0].lower()):
            if c != code:
                continue
            url, to = (links or {}).get(name) or (found, "")
            to = to or ("site" if url == own_site(urlsplit(url).netloc) else "careers")
            members.append({"c": name, "why": why, "url": url, "to": to if url else ""})
        if members:
            groups.append({"reason": code, "label": label, "count": len(members), "sites": members})
    return groups


def inline_text(token: Token) -> str:
    """The words of an inline token, without its markup."""
    return "".join(child.content for child in token.children or [] if child.type in {"text", "code_inline"})


def github_slug(text: str) -> str:
    """The anchor GitHub gives a heading, so a link to a section keeps working on the site."""
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-") or "section"


def add_heading_ids(state: StateCore) -> None:
    """Give every heading a stable id, the same one GitHub would give it; a repeated heading gets -1, -2 and so on."""
    used: set[str] = set()
    for index, token in enumerate(state.tokens):
        if token.type != "heading_open":
            continue
        base = github_slug(inline_text(state.tokens[index + 1]))
        slug, n = base, 0
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        token.attrSet("id", slug)


def rewrite_href(href: str) -> str:
    """A relative link of a Markdown file becomes a link that works on the site: to another legal page, or else to
    the file on GitHub. Absolute links, mailto links and links to an anchor of the same page stay as they are."""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path:
        return href
    path = posixpath.normpath(parts.path.lstrip("/"))
    if path == "." or path.startswith(".."):
        raise ValueError(f"{href!r} does not point to a file of the repository")
    links = legal_links()
    if path in links:
        target = links[path]
    else:
        kind = "tree" if parts.path.endswith("/") else "blob"
        target = f"{REPO_URL}/{kind}/main/{path}"
    return target + (f"?{parts.query}" if parts.query else "") + (f"#{parts.fragment}" if parts.fragment else "")


def rewrite_links(state: StateCore) -> None:
    """Rewrite every link and make an external one open in a new tab and say so (like the links of the dashboard). A
    table column's alignment becomes a style attribute, which the page forbids, so that is dropped."""
    for block in state.tokens:
        if block.type in {"th_open", "td_open"}:
            block.attrs.pop("style", None)  # the alignment of a column: the page allows no style attribute
        if block.type != "inline":
            continue
        children: list[Token] = []
        external = False
        for child in block.children or []:
            if child.type == "image":
                raise ValueError("an image in a legal document would break the page's Content-Security-Policy")
            if child.type == "link_open":
                href = rewrite_href(str(child.attrGet("href")))
                child.attrSet("href", href)
                external = urlsplit(href).scheme in {"http", "https"}
                if external:
                    child.attrSet("target", "_blank")
                    child.attrSet("rel", "noopener noreferrer")
            elif child.type == "link_close" and external:
                children.append(Token("html_inline", "", 0, content=NEW_TAB))
                external = False
            children.append(child)
        block.children = children


def markdown_to_html(text: str) -> str:
    """CommonMark with tables. Raw HTML in the Markdown is escaped, never passed through, and nothing is linkified."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    md.core.ruler.push("heading_ids", add_heading_ids)
    md.core.ruler.push("rewrite_links", rewrite_links)
    html: str = md.render(text)
    return html


def apply_chrome(text: str, output: str, root: Path = Path(), dashboard: bool = False) -> str:
    """Put the shared header and footer into a page template. `output` is the page's file name in docs/, so its own
    link in the header and the footer is marked `aria-current="page"`. Only the dashboard (`dashboard=True`) fills
    the slots of the account controls and of the "last read" line; every other page fills the home button and the
    footer's "Home" link instead (see SLOTS)."""
    parts = root / PARTS
    here = "./" if output == "index.html" else output
    shared = {
        "__HEADER__": (parts / "header.inc").read_text(encoding="utf-8"),
        "__FOOTER__": (parts / "footer.inc").read_text(encoding="utf-8"),
    }
    if "__NOT_FOUND__" in text:  # the words of the 404, shared by docs/404.html and the default markup of admin.html
        shared["__NOT_FOUND__"] = (parts / "not-found.inc").read_text(encoding="utf-8")
    for placeholder, (on_dashboard, elsewhere) in SLOTS.items():
        source = on_dashboard if dashboard else elsewhere
        filled = (parts / source).read_text(encoding="utf-8") if source else ""
        for name, part in shared.items():
            shared[name] = part.replace(f"{placeholder}\n", filled)
    for name, part in shared.items():
        if re.search(r"__[A-Z_]+__", part):
            raise ValueError(f"{name} of templates/parts has a slot that this build does not fill")
        text = text.replace(name, part.replace(f'href="{here}"', f'href="{here}" aria-current="page"'))
    return text


def absolute_paths(text: str) -> str:
    """Every relative `href` and `src` of the page made absolute under SITE_PATH ("./" is the site's home), for the
    page that is served at any address."""
    return RELATIVE_URL.sub(lambda m: f'{m[1]}="{SITE_PATH}{m[2]}"', text)


def render_legal(page: LegalPage, root: Path = Path(), docs: Path | None = None) -> str:
    """The page for one Markdown file. Everything but the body is the template, so the page can only say what the
    Markdown says. With `docs`, the integrity hashes of the files there are filled in (the template only, never the
    body)."""
    template = apply_assistant_switch(
        apply_chrome((root / LEGAL_TEMPLATE).read_text(encoding="utf-8"), page.output, root)
    )
    if docs:
        template = INTEGRITY.sub(lambda m: integrity(docs / f"{m[1]}.{m[2]}".lower()), template)
    body = markdown_to_html(apply_assistant_switch((root / page.source).read_text(encoding="utf-8")))
    for placeholder, value in (
        ("__TITLE__", escape(page.title)),
        ("__DESCRIPTION__", escape(page.description)),
        ("__OUTPUT__", page.output),
    ):
        template = template.replace(placeholder, value)
    return template.replace("__BODY__", body)  # last, so a word of the Markdown is never read as a placeholder


def read_css() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8").strip() for pattern in CSS_SOURCES for path in sorted(Path().glob(pattern))
    )


def job_posting(job: dict[str, Any]) -> dict[str, Any]:
    """Schema.org JobPosting for one role, so crawlers and agents can read the page without running scripts."""
    posting: dict[str, Any] = {
        "@type": "JobPosting",
        "title": job["t"],
        "hiringOrganization": {"@type": "Organization", "name": job["c"]},
        "url": job["u"],
        "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": job["l"]}},
    }
    if job["f"]:
        posting["datePosted"] = job["f"]
    if job["il"]:
        posting["jobLocation"]["address"]["addressCountry"] = "IL"
        # one Place per region and one per ungrouped place the job names, so "several cities" is several places
        places = [{"addressRegion": name} for name in job["rg"] if name != regions.REMOTE]
        places += [{"addressLocality": name} for name in job["lo"] if name != regions.CITY_NOT_GIVEN]
        if places:
            posting["jobLocation"] = [
                {"@type": "Place", "address": {"@type": "PostalAddress", **place, "addressCountry": "IL"}}
                for place in places
            ]
        if regions.REMOTE in job["rg"]:
            posting["jobLocationType"] = "TELECOMMUTE"
            posting["applicantLocationRequirements"] = {"@type": "Country", "name": "IL"}
    posting["experienceRequirements"] = job["lv"]
    if job["lv"] == classify.INTERN_LEVEL:
        posting["employmentType"] = "INTERN"
    posting["occupationalCategory"] = job["rt"]
    return posting


def integrity(path: Path) -> str:
    """Subresource Integrity value for a built file, so the browser rejects a stale or altered copy."""
    return "sha384-" + base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def companies_covered(companies: list[dict[str, Any]]) -> int:
    """How many companies have a working feed: status `ok` in companies.json (the excluded ones are already left out).
    Some of them may be failing today and then also sit on the coverage card, which is why the page names the card's
    count next to this one instead of adding the two."""
    return sum(1 for c in companies if c["status"] == "ok")


def count_words(n: int) -> str:
    return f"{n} {'company' if n == 1 else 'companies'}"


def coverage_words(covered: int) -> dict[str, str]:
    """The values of the `__COVERED__` and `__COMPANIES__` placeholders of the page: the count (the search field's
    `data-companies-covered` hook) and the count in words (its placeholder and label)."""
    return {"__COVERED__": str(covered), "__COMPANIES__": count_words(covered)}


def apply_switch(text: str, region: re.Pattern[str], line: re.Pattern[str], enabled: bool) -> str:
    """Keep the regions of a source file that the pattern marks when the switch is on, else remove them."""
    if enabled:
        return region.sub(r"\1", text)
    return region.sub("", line.sub("", text))


def apply_digest_switch(text: str) -> str:
    """Keep the daily email regions of a source file when the signup switch is on, else remove them."""
    return apply_switch(text, DIGEST_REGION, DIGEST_LINE, DIGEST_SIGNUP_ENABLED)


def apply_contact_switch(text: str) -> str:
    """Keep the form of the Contact page when its switch is on, else remove it."""
    return apply_switch(text, CONTACT_REGION, CONTACT_LINE, CONTACT_FORM_ENABLED)


def apply_assistant_switch(text: str) -> str:
    """Keep the link to the assistant page when its switch is on, else remove it."""
    return apply_switch(text, ASSISTANT_REGION, ASSISTANT_LINE, ASSISTANT_PAGE_ENABLED)


def failing_sites(c: sqlite3.Connection, excluded: list[str]) -> list[dict[str, Any]]:
    """The companies on the coverage card that are failing or skipped for robots.txt, each with why and the last good
    crawl."""
    # latest crawl per company, and the last time it worked
    latest: dict[str, tuple[Any, Any, str]] = {}
    q = "SELECT company, ts, ok, COALESCE(note, '') FROM crawl_log ORDER BY ts"
    recent: dict[str, list[Any]] = {}
    for company, ts, ok, note in c.execute(q):
        if is_excluded(company, excluded):
            continue
        latest[company] = (ts, ok, note)
        if not note.startswith(ROBOTS_NOTE):  # a skip is neither a failure nor a success for the streak
            recent.setdefault(company, []).append(ok)
    last_good = dict(c.execute("SELECT company, MAX(substr(ts,1,10)) FROM crawl_log WHERE ok=1 GROUP BY company"))
    # failing = the last two crawls both failed (one-off errors do not count)
    # a company skipped for robots.txt is listed at once: it is a decision, so there is no second crawl to wait for
    failing = {co: v for co, v in latest.items() if not any(recent.get(co, [])[-2:]) or v[2].startswith(ROBOTS_NOTE)}
    blocked: list[dict[str, Any]] = [
        {"c": co, "why": reason(v[2]), "last": last_good.get(co, "never")} for co, v in sorted(failing.items())
    ]
    return blocked


def render_static(source: str, covered: int, page_data: str) -> tuple[str, str]:
    """The file name in docs/ and the text of one source file that is copied with its placeholders filled in: the
    shared header and footer of a page, the switches, the backend, the company count and, last, the page data."""
    output = OUTPUT_NAMES.get(source, Path(source).name)
    text = Path(source).read_text(encoding="utf-8")
    if output.endswith(".html"):
        text = apply_chrome(text, output, dashboard=output == "index.html")
        if output == NOT_FOUND_OUTPUT:
            text = absolute_paths(text)
    text = apply_assistant_switch(apply_contact_switch(apply_digest_switch(text)))
    text = text.replace("__SUPABASE_URL__", SUPABASE_URL).replace("__SUPABASE_ANON_KEY__", SUPABASE_ANON_KEY)
    for placeholder, value in coverage_words(covered).items():
        text = text.replace(placeholder, value)
    return output, text.replace("__PAGE_DATA__", page_data)  # last: the data is never searched for placeholders


def main() -> None:
    excluded = load_excluded()  # an excluded company is on no list and no card, whatever the data still holds
    c = open_history()
    blocked = failing_sites(c, excluded)
    failing = {gap["c"] for gap in blocked}
    everyone = [x for x in load_companies() if not is_excluded(x["name"], excluded)]
    companies = crawled_companies(everyone)
    unfed = [x for x in companies if x["status"] != "ok"]
    untracked = sorted(x["name"] for x in unfed)
    groups = gap_groups(blocked, unfed, read_unresolved(everyone), companies, read_career_links(everyone))
    gaps = {
        "blocked": blocked,
        "untracked": untracked,
        "total": sum(g["count"] for g in groups),
        "groups": groups,
    }
    first = dict(c.execute("SELECT company, MIN(substr(first_seen,1,10)) FROM jobs GROUP BY company"))
    jobs: list[dict[str, Any]] = []
    for company, job_id, title, team, loc, url, fs in c.execute(
        "SELECT company, job_id, title, team, location, url, first_seen FROM jobs "
        "WHERE status='open' ORDER BY company, job_id"
    ):
        if is_excluded(company, excluded):
            continue
        f = (fs or "")[:10]
        place = regions.place(loc)
        jobs.append(
            {
                "c": company,
                "i": job_id,
                "t": title.strip(),
                "m": (team or "").strip(),
                "l": (loc or "").strip()[:80],
                "u": url,
                "f": f,
                "lv": classify.level(title),
                "rt": classify.role_types(title),
                "rg": list(place.regions),
                "lo": list(place.locations),
                "il": place.israel,
                "n": bool(f and f > first.get(company, f)),
                "s": company in failing,
            }
        )
    # The time of the latest crawl, not of the build: a rebuild over the same data (the publish-docs workflow) then
    # writes the same files and has nothing to commit. An empty log falls back to now.
    (last_crawl,) = c.execute("SELECT MAX(ts) FROM crawl_log").fetchone()
    generated = (
        f"{last_crawl[:16].replace('T', ' ')} UTC" if last_crawl else time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    )
    covered = companies_covered(companies)
    payload = dump_json({"jobs": jobs, "gaps": gaps, "covered": covered, "generated": generated})
    # A job whose place is Unknown is not claimed to be in Israel for search engines: it is in jobs.json only.
    listing = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": n, "item": job_posting(j)}
            for n, j in enumerate((j for j in jobs if j["il"] and j["rg"] != [regions.UNKNOWN]), start=1)
        ],
    }
    out = Path("docs")
    (out / "jobs.json").write_text(payload, encoding="utf-8")
    (out / "jobs.jsonld").write_text(dump_json(listing), encoding="utf-8")
    (out / "dashboard.css").write_text(read_css(), encoding="utf-8")
    # What the page needs besides the jobs it reads from Supabase: the coverage gaps, the build time and each company's
    # first day of listings (the closed jobs that fix it are not readable with the anon key). It sits in a
    # JSON data block of index.html (not executed, so the page stays free of inline script); `<` is escaped so the
    # data can never close the block.
    page_data = dump_json(
        {
            "gaps": gaps,
            "covered": covered,
            "generated": generated,
            "firstDay": first,
            # The filters are built from these lists (classify.py and regions.py), in display order.
            "levels": list(classify.LEVELS),
            "notSpecified": classify.NOT_SPECIFIED,
            "roleTypes": list(classify.ROLE_TYPE_NAMES),
            "regions": list(regions.REGION_NAMES),
        }
    ).replace("<", "\\u003c")
    if not DIGEST_SIGNUP_ENABLED:
        (out / Path(DIGEST_SCRIPT).name).unlink(
            missing_ok=True
        )  # a file of an earlier build must not outlive the switch
    if not CONTACT_FORM_ENABLED:
        (out / Path(CONTACT_SCRIPT).name).unlink(missing_ok=True)
    scripts = (
        *((DIGEST_SCRIPT,) if DIGEST_SIGNUP_ENABLED else ()),
        *((CONTACT_SCRIPT,) if CONTACT_FORM_ENABLED else ()),
    )
    for source in (*STATIC_FILES, *scripts):
        output, text = render_static(source, covered, page_data)
        (out / output).write_text(text, encoding="utf-8")
    if not ASSISTANT_PAGE_ENABLED:
        (out / ASSISTANT_PAGE.output).unlink(missing_ok=True)  # a file of an earlier build must not outlive the switch
    for legal in built_pages():
        (out / legal.output).write_text(render_legal(legal, docs=out), encoding="utf-8")
    for source in VENDORED_FILES:
        shutil.copyfile(source, out / Path(source).name)
    for source in VERIFICATION_FILES:
        shutil.copyfile(source, out / Path(source).name)
    for source, name in ICON_FILES.items():
        shutil.copyfile(source, out / name)
    for name in PAGES:
        page = out / name
        # __SRI_DASHBOARD_CSS__ becomes the integrity hash of docs/dashboard.css; one pass over each page
        page.write_text(
            INTEGRITY.sub(lambda m: integrity(out / f"{m[1]}.{m[2]}".lower()), page.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    unlinked = [s["c"] for g in groups for s in g["sites"] if not s["url"]]
    print(f"gaps: {[g['c'] for g in gaps['blocked']]} failing, {len(untracked)} untracked, {gaps['total']} in all")
    if unlinked:  # the tests catch this on the committed data; the daily run has no tests, so it says so here
        print(f"WARNING: no link for {unlinked}")
    print(
        f"dashboard: {len(jobs)} open jobs, {sum(j['il'] for j in jobs)} in Israel, jobs.json {len(payload) // 1024} KB"
    )


if __name__ == "__main__":
    main()
