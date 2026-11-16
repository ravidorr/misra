"""Send the personalised digest to every confirmed subscriber (PLAN.md step 6.3; PRD FR-8 and FR-9).

Run after the Supabase sync: `python send_digests.py` (`--dry-run` prints counts and sends and records nothing).
Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_API_KEY and UNSUBSCRIBE_SECRET (the key of the MAC in the
unsubscribe link, the same value as the `unsubscribe` Edge Function's secret). All four empty: the step is skipped with
a message and exit code 0, so the daily workflow keeps working until step 6.4 turns this on; some empty: it fails and
names them.

For each subscriber who confirmed and has not unsubscribed it
- skips the user unless the digest is due: daily every day, weekly on `WEEKLY_DAY`;
- takes the open jobs in Israel that were first seen AFTER the user confirmed, that no earlier digest carried, that
  do not belong to a company seen for the first time in that run (the silent baseline of run.py: adding a company never
  floods anyone), and that fit the user's saved filters (regions, levels, role types, keywords; the same rules as the
  dashboard: within one filter any chosen value matches, the filters combine, nothing chosen means everything, the
  keywords match the title, company, team or location or, through the database's keys-only full-text search
  (PLAN.md step 7.3), the full text of the posting, and the
  levels and role types are the separate `level` and `role_types` columns, so Product with Intern and Junior gives only
  the entry-level Product roles);
- sends nothing when no job is left;
- otherwise sends ONE email (plain text and HTML, the same words) through Resend and only after Resend accepted it
  records the jobs in `emailed`.
  A send that fails records nothing, so the jobs are tried again at the next run.

Each user is handled in its own try block: a failure for one person (a bad address, Resend refusing, a database error)
is counted and logged and never stops the others. The run exits non-zero when any user failed, after all were handled,
so the workflow's failure alert fires. Coverage-gap content is never in this email (it stays on the dashboard card and
in the maintainer's own alert, send_alert.py). The email texts are exactly the ones in `supabase/email-texts.md`
(`tests/test_send_digests.py` pins them).

The log is public (this repository's Actions), so it holds counts only: never an address, a user id, a token, a job
title or a response body. The service-role key and the Resend key are sent only in request headers."""

import hashlib
import hmac
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from typing import Any, Protocol, cast

import requests

from classify import OTHER

SITE_URL = "https://ravidorr.github.io/misra/"
FROM = "Misra <digest@send.avidor.org>"
REPLY_TO = "raanan@avidor.org"  # the sending address cannot receive mail; people may reply "Remove"
RESEND_URL = "https://api.resend.com/emails"
UNSUBSCRIBE_PATH = "/functions/v1/unsubscribe"
WEEKLY_DAY = 6  # Sunday, the first working day in Israel (date.weekday(): Monday is 0)
PAGE = 1000  # rows asked per read; Supabase caps a response at 1,000 rows by default
TIMEOUT = 60
HTTP_OK, HTTP_REDIRECT, HTTP_TOO_MANY = 200, 300, 429
SEARCH_PATH = "rpc/search_job_keys"  # the full-text search of step 7.3: job keys only
KEY_LENGTH = 2  # a key is [company, job_id]
SEND_INTERVAL = 0.6  # seconds between emails: Resend's default limit is 2 requests a second
RETRY_SECONDS = 2.0  # wait after a 429 from Resend, then one more try
SKIP_MESSAGE = "the Supabase, Resend or unsubscribe secrets are empty: skipping the digests"
SECRETS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "RESEND_API_KEY", "UNSUBSCRIBE_SECRET")
MIN_SECRET_LENGTH = 32

JOB_COLUMNS = "company,job_id,title,team,location,url,status,first_seen,level,role_types,regions,locations"

# The digest footer of supabase/email-texts.md, line for line. {{schedule}} and {{unsubscribe_url}} are filled in.
FOOTER = (
    "---\n"
    "You are receiving this {{schedule}} email because you confirmed job alerts on Misra "
    "(https://ravidorr.github.io/misra/).\n"
    "Misra is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org), Ditsa 8, Herzliya 4627825, "
    "Israel. Job listings are collected automatically from public career pages and are provided as-is, without "
    "guarantee that they are accurate, complete or still open.\n"
    "\n"
    "Unsubscribe in one click (no sign-in needed):\n"
    "{{unsubscribe_url}}\n"
    "\n"
    "To change your filters, or to delete your account and all stored data, sign in at "
    "https://ravidorr.github.io/misra/\n"
    "Privacy: https://ravidorr.github.io/misra/privacy.html | Terms: https://ravidorr.github.io/misra/terms.html\n"
)
ONE_CLICK = "List-Unsubscribe=One-Click"

# The HTML part. Colours are the fixed values of one file that both emails use (tests/test_email_html.py keeps them
# equal to design-system/tokens.css). Gmail clips a message near 102 KB and would hide the footer with the unsubscribe
# link, so a digest whose HTML would pass this many bytes goes out as plain text only (the same words).
COLORS = json.loads((Path(__file__).parent / "supabase/functions/_shared/email-colors.json").read_text())
LIGHT, DARK = COLORS["light"], COLORS["dark"]
HTML_BUDGET = 90_000
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
DARK_CSS = (
    ":root{color-scheme:light dark}@media (prefers-color-scheme:dark){"
    f".bg{{background-color:{DARK['bg']}!important}}"
    f".panel{{background-color:{DARK['panel']}!important;border-color:{DARK['line']}!important}}"
    f".ink{{color:{DARK['ink']}!important}}.muted{{color:{DARK['muted']}!important}}"
    f".accent{{color:{DARK['accent']}!important}}.rule{{border-color:{DARK['line']}!important}}}}"
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"()]*[^\s<>\"().,;:]")


class DigestError(Exception):
    """A problem whose message is safe to print: names a table or a status, never a key, address or body."""


class Response(Protocol):
    status_code: int
    text: str
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class HttpSession(Protocol):
    def request(self, method: str, url: str, **kw: Any) -> Response: ...


@dataclass(frozen=True)
class Filters:
    regions: frozenset[str] = frozenset()
    levels: frozenset[str] = frozenset()
    role_types: frozenset[str] = frozenset()
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Subscriber:
    user_id: str
    address: str
    frequency: str
    confirmed_at: datetime
    filters: Filters


@dataclass(frozen=True)
class Job:
    company: str
    job_id: str
    title: str
    team: str
    location: str
    url: str
    first_seen: datetime
    level: str
    role_types: tuple[str, ...]
    regions: tuple[str, ...]
    locations: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.company, self.job_id)

    @property
    def in_israel(self) -> bool:
        """The sync gives a job in Israel at least a region or a place of its own; a job abroad has neither."""
        return bool(self.regions or self.locations)


@dataclass
class Summary:
    """What a run did, as counts (the run's log): no address, user id, job or token."""

    due: int = 0
    sent: int = 0
    jobs_sent: int = 0
    nothing_new: int = 0
    withdrawn: int = 0  # consent withdrawn or the address changed during the run: nothing was sent
    not_due: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


# ---- The unsubscribe link ------------------------------------------------------------------------


def unsubscribe_token(secret: str, user_id: str) -> str:
    """`<subscription id>.<HMAC-SHA256(secret, id)>`, 64 lower-case hex characters for the MAC. The `unsubscribe` Edge
    Function recomputes it (constant-time compare); nothing about it is stored. `test/unsubscribe.test.ts` pins the same
    vector, so the two sides cannot drift apart."""
    mac = hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return f"{user_id}.{mac}"


def unsubscribe_url(supabase_url: str, secret: str, user_id: str) -> str:
    return f"{supabase_url.rstrip('/')}{UNSUBSCRIBE_PATH}?t={unsubscribe_token(secret, user_id)}"


# ---- Reading rows --------------------------------------------------------------------------------


def parse_time(value: object) -> datetime:
    """A timestamp as an aware UTC datetime. `jobs.first_seen` is text from the crawl clock (UTC, no zone);
    `consent_confirmed_at` is an ISO timestamp with a zone."""
    if not isinstance(value, str):
        raise DigestError("a timestamp is missing")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise DigestError("a timestamp is not ISO 8601") from None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _names(raw: object, what: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise DigestError(f"the saved {what} filter is not a list of names")
    return frozenset(raw)


def parse_filters(raw: object) -> Filters:
    """The `subscriptions.filters` object (regions, levels, role_types, keywords). Anything that is not that shape
    raises, so one broken row skips that user rather than sending them every job."""
    if not isinstance(raw, dict):
        raise DigestError("the saved filters are not an object")
    keywords = raw.get("keywords", "")
    if not isinstance(keywords, str):
        raise DigestError("the saved keywords are not text")
    return Filters(
        regions=_names(raw.get("regions"), "regions"),
        levels=_names(raw.get("levels"), "levels"),
        role_types=_names(raw.get("role_types"), "role types"),
        terms=tuple(keywords.lower().split()),
    )


def parse_subscriber(row: Mapping[str, Any]) -> Subscriber:
    address, frequency = row.get("address"), row.get("frequency")
    if not isinstance(row.get("user_id"), str) or not isinstance(address, str) or not address:
        raise DigestError("a subscription has no user or address")
    if frequency not in ("daily", "weekly"):
        raise DigestError("a subscription has an unknown frequency")
    return Subscriber(
        user_id=row["user_id"],
        address=address,
        frequency=frequency,
        confirmed_at=parse_time(row.get("consent_confirmed_at")),
        filters=parse_filters(row.get("filters")),
    )


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _list(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def parse_job(row: Mapping[str, Any]) -> Job:
    return Job(
        company=_text(row.get("company")),
        job_id=str(row.get("job_id")),
        title=_text(row.get("title")),
        team=_text(row.get("team")),
        location=_text(row.get("location")),
        url=_text(row.get("url")),
        first_seen=parse_time(row.get("first_seen")),
        level=_text(row.get("level")),
        role_types=_list(row.get("role_types")),
        regions=_list(row.get("regions")),
        locations=_list(row.get("locations")),
    )


# ---- Choosing the jobs of one subscriber ---------------------------------------------------------


def matches(job: Job, filters: Filters, text_hits: frozenset[tuple[str, str]] = frozenset()) -> bool:
    """The dashboard's rules for the four digest filters (templates/dashboard.js `select`). A job with no role type
    counts as Other; a chosen name nobody has matches nothing. The keywords match when every word is in the company,
    title, team or location, or when the job is among `text_hits`, the keys the database found for the same words in the
    full text of the posting (`Supabase.search_keys`; the text itself never leaves the database)."""
    if filters.regions and not filters.regions.intersection(job.regions):
        return False
    if filters.levels and job.level not in filters.levels:
        return False
    if filters.role_types and not filters.role_types.intersection(job.role_types or (OTHER,)):
        return False
    haystack = f"{job.company} {job.title} {job.team} {job.location}".lower()
    return all(term in haystack for term in filters.terms) or job.key in text_hits


def baseline_keys(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """The jobs a company had when it was first seen (all of them share one `first_seen`, the time of the run), open or
    closed. run.py baselines them silently so that adding a company never floods the digest; the same rule applies here.
    (Known limit: a company that only lists Israeli jobs and had none on its first crawl has its first Israeli job
    treated as baseline here, where run.py would call it new. Likewise a company run.py knew but that has no row in
    Supabase, because its jobs closed before the sync began or the exclusion list purged it, has its next job treated as
    baseline. Both lose a first email, never send a flood.)"""
    earliest: dict[str, str] = {}
    for row in rows:
        company, seen = str(row.get("company")), str(row.get("first_seen"))
        earliest[company] = min(seen, earliest.get(company, seen))
    return {
        (str(row.get("company")), str(row.get("job_id")))
        for row in rows
        if str(row.get("first_seen")) == earliest[str(row.get("company"))]
    }


def is_due(subscriber: Subscriber, today: date) -> bool:
    return subscriber.frequency == "daily" or today.weekday() == WEEKLY_DAY


def search_query(filters: Filters) -> str:
    """The keywords as the full-text search gets them: the words, one space apart; empty when there are none."""
    return " ".join(filters.terms)


def select_jobs(
    subscriber: Subscriber,
    jobs: Sequence[Job],
    emailed: set[tuple[str, str]],
    text_hits: Mapping[str, frozenset[tuple[str, str]]] = {},
) -> list[Job]:
    """The open jobs this subscriber has not been emailed that appeared after they confirmed and fit their filters,
    in a fixed order (company, then title). `text_hits` holds the full-text keys by search query."""
    hits = text_hits.get(search_query(subscriber.filters), frozenset())
    chosen = [
        job
        for job in jobs
        if job.first_seen > subscriber.confirmed_at
        and job.key not in emailed
        and job.in_israel
        and matches(job, subscriber.filters, hits)
    ]
    return sorted(chosen, key=lambda job: (job.company.lower(), job.title.lower(), job.job_id))


# ---- The email -----------------------------------------------------------------------------------


def render_footer(frequency: str, url: str) -> str:
    return FOOTER.replace("{{schedule}}", frequency).replace("{{unsubscribe_url}}", url)


def render_digest(frequency: str, jobs: Sequence[Job], url: str) -> tuple[str, str]:
    """(subject, plain-text body; render_digest_html is the HTML of the same words): a count line, one block per job
    (company, title and place, then the link), and the lawyer's footer. The email carries job listings only."""
    count = len(jobs)
    noun = "role" if count == 1 else "roles"
    subject = (
        f"Misra weekly: {count} new matching {noun}" if frequency == "weekly" else f"Misra: {count} new matching {noun}"
    )
    blocks = []
    for job in jobs:
        place = f" ({job.location})" if job.location else ""
        link = f"\n{job.url}" if job.url else ""
        blocks.append(f"{job.company} - {job.title}{place}{link}")
    body = f"{count} new {noun} matching your filters:\n\n" + "\n\n".join(blocks) + "\n\n"
    return subject, body + render_footer(frequency, url)


def _anchor(url: str, text: str, block: bool = False) -> str:
    """A link in the accent colour. Only http(s) addresses become links; anything else stays text. `block` is a link on
    a line of its own: padded to a 44px target and allowed to break anywhere (the addresses are long)."""
    if not url.startswith(("https://", "http://")):
        return escape(text)
    style = f"color:{LIGHT['accent']};text-decoration:underline"
    if block:
        style += ";display:inline-block;padding:11px 0;line-height:22px;word-break:break-all;overflow-wrap:anywhere"
    return f'<a class="accent" dir="ltr" href="{escape(url)}" style="{style}">{escape(text)}</a>'


def _linkify(text: str) -> str:
    """The text with each web address a link whose text is that address, so no word changes."""
    out, last = [], 0
    for found in URL_PATTERN.finditer(text):
        out += [escape(text[last : found.start()]), _anchor(found.group(), found.group())]
        last = found.end()
    return "".join(out) + escape(text[last:])


def html_document(subject: str, content: str) -> str:
    """One 600px column on the page colour: table layout, inline styles, a lime band, no image, no script."""
    return (
        '<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f"<title>{escape(subject)}</title><style>{DARK_CSS}</style></head>"
        f'<body class="bg" style="margin:0;padding:0;background-color:{LIGHT["bg"]};-webkit-text-size-adjust:100%">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg" '
        f'style="background-color:{LIGHT["bg"]}"><tr><td align="center" style="padding:24px 16px">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="panel" '
        f'style="width:100%;max-width:600px;table-layout:fixed;background-color:{LIGHT["panel"]};'
        f"border:2px solid {LIGHT['line']};"
        f'border-radius:20px"><tr><td aria-hidden="true" height="8" bgcolor="{LIGHT["lime"]}" '
        f'style="height:8px;line-height:8px;font-size:8px;background-color:{LIGHT["lime"]};'
        f'border-radius:18px 18px 0 0">&nbsp;</td></tr><tr><td class="ink" style="padding:24px 24px 8px 24px;'
        f'font-family:{FONT};font-size:16px;line-height:1.5;color:{LIGHT["ink"]}">{content}</td></tr></table>'
        "</td></tr></table></body></html>"
    )


def footer_html(text: str) -> str:
    """The footer text as muted paragraphs under a rule, one per line; a line that is only an address is a link line."""
    rows = [
        f'<div class="rule" style="border-top:2px solid {LIGHT["line"]};height:0;line-height:0;font-size:0;'
        'margin:16px 0">&nbsp;</div>'
    ]
    for line in text.splitlines():
        if line in ("---", ""):
            continue
        body = _anchor(line, line, block=True) if URL_PATTERN.fullmatch(line) else _linkify(line)
        rows.append(f'<p class="muted" style="margin:0 0 16px 0;font-size:14px;color:{LIGHT["muted"]}">{body}</p>')
    return "".join(rows)


def render_digest_html(frequency: str, jobs: Sequence[Job], url: str) -> str:
    """The digest as HTML: the same words as render_digest's text, in the same order, only laid out. The count line is
    the heading; each role is its company, title and place in bold with its link under it."""
    subject, _ = render_digest(frequency, jobs, url)
    count = len(jobs)
    noun = "role" if count == 1 else "roles"
    rows = []
    for job in jobs:
        place = f'<span class="muted" style="color:{LIGHT["muted"]};font-weight:400"> ({escape(job.location)})</span>'
        link = _anchor(job.url, job.url, block=True) if job.url else ""
        rows.append(
            f'<tr><td class="rule" style="padding:12px 0;border-top:1px solid {LIGHT["line"]}">'
            f'<p dir="auto" style="margin:0;font-weight:700">{escape(job.company)} - {escape(job.title)}'
            f"{place if job.location else ''}</p>{link}</td></tr>"
        )
    head = (
        f'<h1 style="margin:0 0 16px 0;font-size:18px;line-height:1.5">{count} new {noun} matching your filters:</h1>'
    )
    listing = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'
    )
    return html_document(subject, head + listing + footer_html(render_footer(frequency, url)))


def build_payload(subscriber: Subscriber, jobs: Sequence[Job], url: str) -> dict[str, Any]:
    subject, text = render_digest(subscriber.frequency, jobs, url)
    html = render_digest_html(subscriber.frequency, jobs, url)
    return {
        "from": FROM,
        "to": [subscriber.address],
        "reply_to": REPLY_TO,
        "subject": subject,
        "text": text,
        # Multipart: the HTML is the designed version. Over the budget it is left out, so the footer is never clipped.
        **({"html": html} if len(html.encode()) <= HTML_BUDGET else {}),
        # RFC 8058 one-click, which Gmail and Yahoo require: the mail client's own Unsubscribe button POSTs here.
        "headers": {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": ONE_CLICK},
    }


def idempotency_key(subscriber: Subscriber, jobs: Sequence[Job]) -> str:
    """The same user and the same jobs give the same key, so a re-run within a day after a failed record does not send
    the same email again (Resend remembers an `Idempotency-Key` for 24 hours; it cannot help once the jobs change)."""
    digest = hashlib.sha256("|".join([subscriber.user_id, *(f"{j.company}\x1f{j.job_id}" for j in jobs)]).encode())
    return f"digest-{digest.hexdigest()[:40]}"


# ---- The two services ----------------------------------------------------------------------------


class Supabase:
    """The PostgREST endpoint of the project, with the service-role key. Errors carry the status only: a response body
    can echo a row (an address, a user id), and the run's log is public."""

    def __init__(self, session: HttpSession, url: str, key: str) -> None:
        if not url.startswith("https://"):
            raise DigestError("SUPABASE_URL must start with https://")
        self.session = session
        self.rest = url.rstrip("/") + "/rest/v1/"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(
        self,
        method: str,
        table: str,
        params: Mapping[str, str | int],
        body: list[dict[str, Any]] | dict[str, Any] | None = None,
    ) -> Response:
        headers = dict(self.headers)
        if isinstance(body, list):  # rows to insert; the body of a function call (a dict) wants its answer back
            headers["Prefer"] = "resolution=ignore-duplicates,return=minimal"
        try:
            response = self.session.request(
                method,
                self.rest + table,
                params=params,
                json=body,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DigestError(f"{method} {table} failed: {type(exc).__name__}") from None
        if not HTTP_OK <= response.status_code < HTTP_REDIRECT:
            raise DigestError(f"{method} {table} failed: HTTP {response.status_code}")
        return response

    def read_all(self, table: str, params: Mapping[str, str | int]) -> Iterator[dict[str, Any]]:
        """Every row of a query, page by page; only an empty page ends the read (a server may cap a page below PAGE)."""
        offset = 0
        while True:
            try:
                page = self.call("GET", table, {**params, "limit": PAGE, "offset": offset}).json()
            except ValueError:
                raise DigestError(f"GET {table} returned something that is not JSON") from None
            if not isinstance(page, list):
                raise DigestError(f"GET {table} returned something other than a list")
            if not page:
                return
            yield from page
            offset += len(page)

    def subscribers(self) -> list[dict[str, Any]]:
        """Confirmed, not unsubscribed, in a fixed order."""
        return list(
            self.read_all(
                "subscriptions",
                {
                    "select": "user_id,address,frequency,filters,consent_confirmed_at",
                    "consent_confirmed_at": "not.is.null",
                    "unsubscribed_at": "is.null",
                    "order": "user_id.asc",
                },
            )
        )

    def current(self, subscriber: Subscriber) -> bool:
        """Is this very subscription still confirmed and not unsubscribed, with the same address, schedule, filters and
        confirmation as when the run read it? Read again from the database right before a send and before the record, so
        consent withdrawn or an address changed during the run is honoured. Anything else is a different consent and
        waits for the next run, which reads it fresh."""
        rows = self.read_all(
            "subscriptions",
            {
                "select": "user_id,address,frequency,filters,consent_confirmed_at",
                "consent_confirmed_at": "not.is.null",
                "unsubscribed_at": "is.null",
                "user_id": f"eq.{subscriber.user_id}",
                "order": "user_id.asc",
            },
        )
        return any(parse_subscriber(row) == subscriber for row in rows)

    def jobs(self) -> list[dict[str, Any]]:
        """Every job, open and closed: the closed ones still say when a company was first seen."""
        return list(self.read_all("jobs", {"select": JOB_COLUMNS, "order": "company.asc,job_id.asc"}))

    def search_keys(self, query: str) -> frozenset[tuple[str, str]]:
        """The keys of the open jobs whose full text holds the words of `query` (the database function of migration
        20261124100000_jobs_text_search.sql, which answers keys only). Never the text, and the query is not logged."""
        try:
            rows = self.call("POST", SEARCH_PATH, {}, {"p_query": query}).json()
        except ValueError:
            raise DigestError(f"POST {SEARCH_PATH} returned something that is not JSON") from None
        if not isinstance(rows, list) or not all(isinstance(pair, list) and len(pair) == KEY_LENGTH for pair in rows):
            raise DigestError(f"POST {SEARCH_PATH} returned something other than a list of keys")
        return frozenset((str(company), str(job_id)) for company, job_id in rows)

    def emailed(self, user_id: str) -> set[tuple[str, str]]:
        rows = self.read_all(
            "emailed", {"select": "company,job_id", "user_id": f"eq.{user_id}", "order": "company.asc,job_id.asc"}
        )
        return {(str(row["company"]), str(row["job_id"])) for row in rows}

    def record_emailed(self, user_id: str, jobs: Sequence[Job]) -> None:
        rows = [{"user_id": user_id, "company": job.company, "job_id": job.job_id} for job in jobs]
        self.call("POST", "emailed", {"on_conflict": "user_id,company,job_id"}, rows)


class Resend:
    def __init__(self, session: HttpSession, key: str, sleep: Callable[[float], None] = time.sleep) -> None:
        self.session = session
        self.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.sleep = sleep

    def post(self, payload: dict[str, Any], key: str) -> int:
        try:
            response = self.session.request(
                "POST",
                RESEND_URL,
                json=payload,
                headers={**self.headers, "Idempotency-Key": key},
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DigestError(f"Resend failed: {type(exc).__name__}") from None
        return response.status_code

    def send(self, payload: dict[str, Any], key: str) -> None:
        """Send one email; returns when Resend accepted it, raises when it did not. A 429 is retried once after a pause
        (the default limit is 2 requests a second). The error holds the status only, never the body."""
        status = self.post(payload, key)
        if status == HTTP_TOO_MANY:
            self.sleep(RETRY_SECONDS)
            status = self.post(payload, key)
        if not HTTP_OK <= status < HTTP_REDIRECT:
            raise DigestError(f"Resend answered HTTP {status}")


# ---- The run -------------------------------------------------------------------------------------


@dataclass
class Services:
    """Everything a run talks to, and the secret of the unsubscribe links."""

    supabase: Supabase
    resend: Resend
    supabase_url: str
    unsubscribe_secret: str
    sleep: Callable[[float], None] = time.sleep


def due_subscribers(rows: Sequence[Mapping[str, Any]], today: date, summary: Summary) -> list[Subscriber]:
    """The subscribers whose digest is due today; a broken row is counted as a failure and skipped."""
    due: list[Subscriber] = []
    for row in rows:
        try:
            subscriber = parse_subscriber(row)
        except DigestError as exc:
            summary.failed += 1
            summary.failures.append(f"a subscription was skipped: {exc}")
            continue
        if is_due(subscriber, today):
            due.append(subscriber)
        else:
            summary.not_due += 1
    summary.due = len(due)
    return due


def new_jobs(rows: Sequence[Mapping[str, Any]], summary: Summary) -> list[Job]:
    """The open jobs that are not a company's baseline; a broken row is counted and skipped, never stops the digest."""
    baseline = baseline_keys(rows)
    jobs: list[Job] = []
    for row in rows:
        if row.get("status") != "open" or (str(row.get("company")), str(row.get("job_id"))) in baseline:
            continue
        try:
            jobs.append(parse_job(row))
        except DigestError as exc:
            summary.failed += 1
            summary.failures.append(f"a job was skipped: {exc}")
    return jobs


def full_text_hits(
    supabase: Supabase, due: Sequence[Subscriber], summary: Summary
) -> dict[str, frozenset[tuple[str, str]]]:
    """The full-text keys of every distinct set of keywords among the subscribers, one search each. A search that fails
    is counted (the run exits non-zero, so the alert fires) and its subscribers get the title match alone until the next
    run: a job found only in the text is not recorded as emailed, so it is still new then."""
    hits: dict[str, frozenset[tuple[str, str]]] = {}
    for query in sorted({search_query(subscriber.filters) for subscriber in due} - {""}):
        try:
            hits[query] = supabase.search_keys(query)
        except DigestError as exc:
            summary.failed += 1
            summary.failures.append(f"a keyword search failed: {exc}")
    return hits


def run(services: Services, today: date, dry_run: bool = False) -> Summary:
    """Handle every confirmed subscriber and return the counts. Reading the subscribers or the jobs failing stops the
    run (nothing can be decided); after that each subscriber is its own try block."""
    supabase = services.supabase
    summary = Summary()
    due = due_subscribers(supabase.subscribers(), today, summary)
    if not due:
        return summary
    jobs = new_jobs(supabase.jobs(), summary)
    text_hits = full_text_hits(supabase, due, summary)
    for position, subscriber in enumerate(due):
        try:
            chosen = select_jobs(subscriber, jobs, supabase.emailed(subscriber.user_id), text_hits)
            if not chosen:
                summary.nothing_new += 1
                continue
            if dry_run:
                summary.jobs_sent += len(chosen)
                continue
            if summary.sent:
                services.sleep(SEND_INTERVAL)
            if not supabase.current(subscriber):
                summary.withdrawn += 1
                continue
            url = unsubscribe_url(services.supabase_url, services.unsubscribe_secret, subscriber.user_id)
            services.resend.send(build_payload(subscriber, chosen, url), idempotency_key(subscriber, chosen))
            summary.sent += 1
            summary.jobs_sent += len(chosen)
            try:
                # An unsubscribe that landed during the send has just deleted the emailed rows: do not make them again.
                if supabase.current(subscriber):
                    supabase.record_emailed(subscriber.user_id, chosen)
            except DigestError as exc:
                raise DigestError(f"the email was sent but not recorded ({exc})") from None
        except DigestError as exc:
            summary.failed += 1
            summary.failures.append(f"subscriber {position + 1} of {len(due)}: {exc}")
        except Exception as exc:  # one person's unexpected error must never stop the others
            summary.failed += 1
            summary.failures.append(f"subscriber {position + 1} of {len(due)}: {type(exc).__name__}")
    return summary


def report(summary: Summary, dry_run: bool) -> str:
    verb = "would send" if dry_run else "sent"
    return (
        f"digests: {summary.due} due, {summary.not_due} not due today, {summary.nothing_new} with nothing new, "
        f"{summary.withdrawn} withdrawn, "
        f"{verb} {summary.jobs_sent} jobs{'' if dry_run else f' in {summary.sent} emails'}, {summary.failed} failed"
    )


def main() -> None:
    values = {name: os.environ.get(name, "").strip() for name in SECRETS}
    empty = [name for name, value in values.items() if not value]
    if len(empty) == len(SECRETS):  # unset repository secrets reach the step as empty strings
        print(SKIP_MESSAGE)
        return
    if empty:
        sys.exit(f"digests failed: these are empty: {', '.join(empty)}")
    if len(values["UNSUBSCRIBE_SECRET"]) < MIN_SECRET_LENGTH:
        sys.exit(f"digests failed: UNSUBSCRIBE_SECRET must have at least {MIN_SECRET_LENGTH} characters")
    dry_run = "--dry-run" in sys.argv
    session = cast("HttpSession", requests.Session())
    try:
        supabase = Supabase(session, values["SUPABASE_URL"], values["SUPABASE_SERVICE_ROLE_KEY"])
        services = Services(
            supabase, Resend(session, values["RESEND_API_KEY"]), values["SUPABASE_URL"], values["UNSUBSCRIBE_SECRET"]
        )
        summary = run(services, datetime.now(UTC).date(), dry_run)
    except DigestError as exc:
        sys.exit(f"digests failed: {exc}")
    print(report(summary, dry_run))
    for failure in summary.failures:
        print(f"::error::{failure}")
    if summary.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
