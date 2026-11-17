"""Job descriptions (PLAN.md step 7.1): turning what a career site gives into plain text, and carrying it from the crawl
to the Supabase sync.

The full text of a posting lives in Supabase only. It is never written to `data/jobs.db` or to any file in the
repository, because the daily workflow commits those to a public repository (the size of a daily commit, and the
postings of other companies are not ours to publish there). The crawl (`run.py`) writes the texts to a work file outside
the checkout, in the runner's temporary directory, and the sync step (`sync_supabase.py`) of the same job reads it.

Nothing here raises for the content of a posting: a value that is not text means no description, and the job appears as
it did before."""

import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAX_CHARS = 20_000  # the cap per job: it protects the free Supabase database (500 MB), see CONTRIBUTING.md
CUT_WINDOW = 200  # a cut moves back to a space when there is one this close to the cap, so no word is cut in half
SNIPPET_CHARS = 255  # the public snippet: the first characters of a description (PLAN.md step 7.2)
MAX_DETAIL_CHARS = 200
MAX_DETAIL_ITEMS = 20
TRUNCATED = "description_truncated"  # the details key that records a text cut at the cap
HASH_KEY = "text_sha256"  # the details key the sync keeps the fingerprint of the stored text in
WORK_FILE = "misra-descriptions.jsonl"
# Elements whose content is never text for a reader: dropped with their content.
_DROPPED = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "object", "head", "title"})
_BLOCKS = frozenset(
    {"p", "div", "section", "article", "header", "footer", "ul", "ol", "table", "tr", "blockquote", "pre", "hr"}
    | {"h1", "h2", "h3", "h4", "h5", "h6"}
)
# A script, style or frame that came out of the text itself (escaped twice): dropped up to its end tag.
_DANGEROUS = re.compile(r"<(script|style|iframe)\b.*?(?:</\1\s*>|\Z)", re.S | re.I)
_SPACE = re.compile(r"[^\S\n]+")


class _Text(HTMLParser):
    """HTML to text: block elements and list items start a line, a `<li>` starts with "- ", dropped elements vanish."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.dropped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED:
            self.dropped += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br" or tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self.dropped = max(0, self.dropped - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.dropped:
            self.parts.append(data)


def _tidy(raw: str) -> str:
    """Whitespace and control characters: no NUL or other control character (Postgres refuses NUL in text and one bad
    row would fail a whole batch), spaces collapsed, lines trimmed, at most one blank line in a row."""
    text = "".join(ch if ch == "\n" or unicodedata.category(ch) not in {"Cc", "Cs"} else " " for ch in raw)
    lines = [_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def html_to_text(raw: Any) -> str:
    """Plain text from a posting's HTML, or "" when `raw` is not text or cannot be read. Escaped HTML (Greenhouse sends
    its content as `&lt;p&gt;...`) is unescaped once first. What survives is text only: no tag, no script or style."""
    if not isinstance(raw, str):
        return ""
    if "<" not in raw and "&lt;" in raw:
        raw = html.unescape(raw)
    parser = _Text()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # a posting must never stop its company's crawl, whatever its markup is
        return ""
    # What is left is plain text: an angle bracket in it is a character a posting wrote (`List<T>`), not markup, and is
    # kept; the consumer shows it as text. Only a script, style or frame escaped twice is taken out.
    return _tidy(_DANGEROUS.sub(" ", "".join(parser.parts)))


def plain_text(raw: Any) -> str:
    """The same tidy-up for text a site already gives as plain text ("" when it is not text)."""
    return _tidy(raw) if isinstance(raw, str) else ""


def sections(*parts: tuple[str, str]) -> str:
    """Joins (heading, text) pairs into one text: a non-empty text follows its non-empty heading on the next line."""
    return "\n\n".join(f"{head}\n{text}" if head else text for head, text in parts if text)


def cap(text: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    """The text cut to `limit` characters (at a space when one is near, so a word is not cut in half) and whether it
    was cut."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space >= 0 and space >= limit - CUT_WINDOW:
        cut = cut[:space]
    return cut.rstrip(), True


_SNIPPET_TAIL = (
    " \t\n,.;:!?-\u2013\u2014\u05be(/\\|&*\"'[{"  # punctuation a cut must not end on; `#` and `+` stay (C#, C++)
)


def snippet(text: str | None) -> str | None:
    """The public snippet of a description: its first SNIPPET_CHARS characters as written, cut at a word boundary (a
    word is never cut in half), with trailing punctuation and spaces trimmed when it was cut, and no ellipsis (the page
    adds one). A text that fits is returned whole. None for no text, and for a text that has no space in its first
    SNIPPET_CHARS characters (one word that long has no honest cut). Any white space is a boundary, a line break too."""
    text = (text or "").strip()
    if len(text) <= SNIPPET_CHARS:
        return text or None
    cut = text[:SNIPPET_CHARS]
    if not text[SNIPPET_CHARS].isspace():  # the cut falls inside a word: back to the space before it
        space = max((m.start() for m in re.finditer(r"\s", cut)), default=-1)
        if space < 0:
            return None
        cut = cut[:space]
    return cut.rstrip(_SNIPPET_TAIL) or None


def snippet_cut(text: str | None) -> bool | None:
    """Whether `snippet(text)` is shorter than the text, so the page shows an ellipsis after it: True when it was cut,
    False when it is the whole text, None when there is no snippet (no text, or one word longer than the snippet)."""
    short = snippet(text)
    return None if short is None else len(short) < len((text or "").strip())


def clean_details(details: dict[str, Any]) -> dict[str, Any]:
    """The details a site gave, as short text, a flag or a short list of text: nothing else is kept (a site's own
    structures can carry fields nobody meant to publish), empty values are dropped and control characters removed."""
    out: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, str):
            if text := plain_text(value)[:MAX_DETAIL_CHARS].strip():
                out[key] = text
        elif isinstance(value, list):
            items = [plain_text(v)[:MAX_DETAIL_CHARS].strip() for v in value if isinstance(v, str)]
            if items := [v for v in items if v][:MAX_DETAIL_ITEMS]:
                out[key] = items
    return out


def attach(job: dict[str, Any], description: str, details: dict[str, Any]) -> dict[str, Any]:
    """Adds `description` and `details` to a job dict when there is something to add, and returns the job. A job with
    neither stays exactly as the adapter built it."""
    text, cut = cap(description)
    kept = clean_details(details)
    if cut:
        kept[TRUNCATED] = True
    if text:
        job["description"] = text
    if kept:
        job["details"] = kept
    return job


def fingerprint(description: str, details: dict[str, Any]) -> str:
    """A hash of the stored text and details, to tell a posting that changed from one that did not (the details do
    not include the hash itself)."""
    body = {k: v for k, v in details.items() if k != HASH_KEY}
    blob = json.dumps([description, body], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def work_path() -> Path:
    """Where the crawl leaves the texts for the sync: the runner's temporary directory on GitHub Actions
    (`RUNNER_TEMP`), the system's temporary directory elsewhere. Never a path in the checkout."""
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / WORK_FILE


def write_work_file(rows: Iterable[dict[str, Any]], path: Path | None = None) -> int:
    """Writes one JSON line per job with a description (company, job_id, description, details), replacing the file, and
    returns the number of lines. The file is written whole and then moved into place, so a reader never sees half of
    one."""
    target = path or work_path()
    tmp = target.with_name(target.name + ".part")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    tmp.replace(target)
    return count


def read_work_file(path: Path | None = None) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    """The texts the crawl left, by (company, job_id). A missing file, a line that is not what the crawl writes, or a
    file that cannot be read is no text: the sync goes on without it, it never stops for it."""
    source = path or work_path()
    found: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return found
    for line in lines:
        try:
            row = json.loads(line)
            company, job_id, text, details = row["company"], row["job_id"], row["description"], row.get("details") or {}
        except (ValueError, KeyError, TypeError):
            continue
        if all(isinstance(v, str) for v in (company, job_id, text)) and text and isinstance(details, dict):
            found[(company, job_id)] = (text, details)
    return found


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def share_report(jobs_by_ats: Iterable[tuple[str, list[dict[str, Any]]]]) -> str:
    """The FR-4 AC 3 log line: per ATS the jobs with a full description out of the jobs the crawl returned, then all of
    them, the jobs cut at the cap and the characters kept. Counts and percentages only, no text."""
    total: Counter[str] = Counter()
    with_text: Counter[str] = Counter()
    cut = chars = 0
    for ats, jobs in jobs_by_ats:
        for job in jobs:
            total[ats] += 1
            if job.get("description"):
                with_text[ats] += 1
                chars += len(job["description"])
                cut += bool((job.get("details") or {}).get(TRUNCATED))
    by_size = sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))
    parts = [f"{ats} {with_text[ats]}/{n} ({_pct(with_text[ats], n)})" for ats, n in by_size]
    done, n_all = sum(with_text.values()), sum(total.values())
    return (
        f"descriptions: {'; '.join(parts)}; all {done}/{n_all} ({_pct(done, n_all)}); "
        f"cut at {MAX_CHARS} characters {cut}; {chars} characters kept"
    )
