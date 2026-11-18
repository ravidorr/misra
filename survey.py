"""Survey custom career pages with a real browser: which ATS / JSON endpoints do they use?
Writes data/survey.json. Dev tool, not part of the daily run.

`python survey.py --baseline` is the read-only coverage report of PLAN_3.md step A1 (see `baseline`): counts only,
printed to stdout, from the pulled company list and the public docs/jobs.json. It needs no browser.

`python survey.py --candidates FILE` is the candidate script of PLAN_3.md step D3 (see `candidates_main`): it probes the
companies of a CSV politely and prints a keep-or-skip table. It needs no browser either."""

import argparse
import asyncio
import collections
import contextlib
import csv
import dataclasses
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from playwright.async_api import async_playwright

from adapters import ADAPTERS
from common import (
    COMPANIES,
    HTTP_OK,
    USER_AGENT,
    ExclusionError,
    crawled_companies,
    get,
    is_excluded,
    load_companies,
    load_excluded,
    reset_robots_block,
    robots_blocked,
    slug_candidates,
)
from discover import CAREER_PATHS, PROBE_ORDER, ats_in_text, israel_count

ATS = re.compile(
    r"(myworkdayjobs\.com|workdayjobs|comeet\.com|greenhouse\.io|lever\.co|ashbyhq\.com|"
    r"smartrecruiters\.com|workable\.com|icims\.com|taleo\.net|oraclecloud\.com/hcmUI|successfactors|"
    r"jobvite\.com|teamtailor\.com|bamboohr\.com|recruitee\.com|personio\.(?:de|com)|breezy\.hr|"
    r"rippling\.com|eightfold|phenom|avature|jobs\.cz|hibob\.com/careers|hirehive|pinpointhq|join\.com|"
    r"freshteam|zohorecruit|gem\.com)",
    re.I,
)
JOBLINK = re.compile(r"/(jobs?|positions?|careers?|openings?|vacanc\w+|job-?post\w*)/[^/?#]{4,}", re.I)
HTTP_ERROR = 400
MIN_JSON_LEN = 800
CAREER_TXT = re.compile(r"career|jobs|join us|we.?re hiring|open positions|קריירה|משרות", re.I)
JOBS_JSON = "docs/jobs.json"
PULL_HINT = (
    "{path} is missing: it is the company list that `sync_supabase.py --pull-companies` writes from Supabase "
    "(needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY). Run it first, then `python survey.py --baseline` again."
)
DURATION_HINT = (
    "Crawl step duration (not read here: no GitHub call): `gh run list --workflow=daily.yml --limit 10`, then "
    "`gh run view <id> --json jobs` and the `Crawl and diff` step's startedAt and completedAt."
)
SKIP = {"Source", "Bank Leumi Tech", "Noname Security", "Run:ai", "Zebra Medical"}


async def survey(ctx: Any, co: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        res: dict[str, Any] = {
            "name": co["name"],
            "domain": co["domain"],
            "ats_hits": set(),
            "json_urls": [],
            "job_links": [],
            "final": "",
            "err": "",
        }
        page = await ctx.new_page()

        def on_req(req: Any) -> None:
            m = ATS.search(req.url)
            if m:
                res["ats_hits"].add(req.url[:140])

        async def on_resp(resp: Any) -> None:
            try:
                if "json" in (resp.headers.get("content-type") or "") and resp.request.resource_type in (
                    "xhr",
                    "fetch",
                ):
                    t = await resp.text()
                    if (
                        re.search(r'"(title|jobTitle|name|text)"', t)
                        and re.search(r'(?i)"(location|department|jobId|requisition|posting|apply)', t)
                        and len(t) > MIN_JSON_LEN
                    ):
                        res["json_urls"].append(resp.url[:160])
            except Exception:
                pass

        page.on("request", on_req)
        page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
        urls = [co.get("careers_url")] if co.get("careers_url") else []
        urls += [f"https://www.{co['domain']}/careers", f"https://{co['domain']}/careers"]
        try:
            for u in dict.fromkeys(urls):
                try:
                    r = await page.goto(u, timeout=40000, wait_until="domcontentloaded")
                    if r and r.status < HTTP_ERROR:
                        break
                except Exception as e:
                    res["err"] = type(e).__name__
            else:
                pass
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=15000)
            res["final"] = page.url
            res["title"] = (await page.title())[:60]
            for f in page.frames:
                if ATS.search(f.url):
                    res["ats_hits"].add("FRAME " + f.url[:140])
            links = await page.eval_on_selector_all(
                "a[href]", "els=>els.map(e=>[e.href,(e.innerText||'').trim().slice(0,80)])"
            )
            seen: set[str] = set()
            for h, t in links:
                if JOBLINK.search(h) and h not in seen and t:
                    seen.add(h)
                    res["job_links"].append([h, t])
                if ATS.search(h):
                    res["ats_hits"].add("LINK " + h[:140])
            res["n_job_links"] = len(res["job_links"])
            res["job_links"] = res["job_links"][:5]
        except Exception as e:
            res["err"] = f"{type(e).__name__}"
        await page.close()
        res["ats_hits"] = sorted(res["ats_hits"])[:6]
        print(
            f"{co['name']:20s} ats={len(res['ats_hits'])} json={len(res['json_urls'])} "
            f"links={res.get('n_job_links')} {res['err']} {res.get('title', '')[:30]}",
            flush=True,
        )
        return res


def _lines(title: str, counts: collections.Counter[str]) -> list[str]:
    """One heading and its counts, most frequent first (ties by label), then the total."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [f"{title} ({sum(counts.values())}):", *(f"  {label}: {n}" for label, n in ranked)]


def _reason(company: dict[str, Any]) -> str:
    gap = company.get("gap")
    return str(gap.get("reason") or "none") if isinstance(gap, dict) else "none"


def baseline(companies: list[dict[str, Any]], jobs: dict[str, Any]) -> str:
    """The coverage report of PLAN_3.md step A1 and A2, as text. Counts only: no job, no excluded company and no
    company name is printed. `companies` is the pulled list, `jobs` the parsed public docs/jobs.json (its open jobs
    at the last build, with `c` the company, `il` true for an Israel job).

    A global company is pinned `israel_only` and an Israeli startup is not, so that flag gives the split (A2)."""
    excluded = sum(1 for c in companies if c.get("excluded") is not None)
    crawled = crawled_companies(companies)
    researched = [c for c in companies if c.get("crawl") is not True and c.get("excluded") is None]
    names = {c.get("name") for c in crawled}
    israel_jobs = [j for j in jobs.get("jobs", []) if j.get("il") is True]
    israel_companies = {j.get("c") for j in israel_jobs}
    pinned = sum(1 for c in crawled if c.get("israel_only") is True)
    out = [
        f"Coverage baseline (jobs.json generated {jobs.get('generated', 'unknown')})",
        f"Crawled companies: {len(crawled)}",
        f"Researched only: {len(researched)}",
        f"Excluded records: {excluded} (a count only)",
        f"Crawled companies with at least one Israel job: {len(israel_companies & names)}",
        f"Israel jobs (all companies in jobs.json): {len(israel_jobs)}",
        f"Israel jobs of crawled companies: {sum(1 for j in israel_jobs if j.get('c') in names)}",
        f"Companies with Israel jobs but not in the crawled list (name mismatch): {len(israel_companies - names)}",
        *_lines("Researched only by gap.reason", collections.Counter(_reason(c) for c in researched)),
        *_lines("Researched only by segment", collections.Counter(c.get("segment") or "none" for c in researched)),
        *_lines("Crawled by ats", collections.Counter(c.get("ats") or "none" for c in crawled)),
        "Crawled startup versus multinational (from israel_only):",
        f"  multinational (israel_only): {pinned}",
        f"  startup (not israel_only): {len(crawled) - pinned}",
        DURATION_HINT,
    ]
    return "\n".join(out)


def baseline_main(companies_path: str = COMPANIES, jobs_path: str = JOBS_JSON) -> int:
    """Print the baseline report to stdout. A missing company list stops with the way to produce it (exit 1)."""
    if not os.path.exists(companies_path):
        print(PULL_HINT.format(path=companies_path), file=sys.stderr)
        return 1
    with open(jobs_path, encoding="utf-8") as fh:
        jobs = json.load(fh)
    print(baseline(load_companies(companies_path), jobs))
    return 0


# The candidate script (PLAN_3.md step D3)
CANDIDATE_COLUMNS = ("name", "domain", "kind")
CANDIDATE_THREADS = 4  # a few threads; each host still sees one request a second (common.DELAY)
BOT_STATUSES = (403, 429)  # a site that answers this refuses us: never worked around, recorded as blocked
RUNNER_RISK = frozenset({"workday", "oraclehcm"})  # feeds that may answer only from a normal network (PLAN_3.md)
PAGE_HINTS = (
    ("workday", re.compile(r"myworkdayjobs\.com", re.I)),
    ("oraclehcm", re.compile(r"oraclecloud\.com/hcmUI", re.I)),
)
NAME_NOISE = frozenset({"inc", "ltd", "llc", "corp", "co", "the", "jobs", "careers", "hq"})
HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
FEED_ERRORS = (ValueError, KeyError, TypeError, AttributeError)  # a board that answers with something unreadable
CANDIDATES_PULL_HINT = (
    "{path} is missing: it is the company list that `python3 sync_supabase.py --pull` writes from Supabase (needs "
    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment; never paste the key). Run it first, then "
    "`python3 survey.py --candidates FILE` again."
)
NO_EXCLUDED_NOTE = (
    "note: the pulled list has no excluded record. If the pull was not made with the service-role key, excluded "
    "companies are invisible to this check: pull again with it before trusting a table."
)
TABLE_HEADER = (
    "name",
    "domain",
    "ats",
    "slug",
    "israel_jobs",
    "board_name",
    "name_match",
    "runner_risk",
    "verdict",
    "why",
)


class CandidateFileError(Exception):
    """The candidate CSV cannot be used; the message says why."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    name: str
    domain: str
    kind: str


@dataclasses.dataclass(frozen=True)
class Row:
    """One probed candidate. `verdict` is keep, skip or needs-yes (a career page with no feed of its own, or a feed
    that may fail on the runner: a browser crawl or a pin the maintainer must approve, PLAN_3.md step D4)."""

    candidate: Candidate
    ats: str = ""
    slug: str = ""
    israel_jobs: int | None = None
    board_name: str = ""
    name_match: str = "n/a"  # yes, no, unknown (no name to compare), page (the company's own page links the board)
    runner_risk: str = "no"
    verdict: str = "skip"
    why: str = ""

    def cells(self) -> tuple[str, ...]:
        jobs = "" if self.israel_jobs is None else str(self.israel_jobs)
        return (
            self.candidate.name,
            self.candidate.domain,
            self.ats,
            self.slug,
            jobs,
            self.board_name,
            self.name_match,
            self.runner_risk,
            self.verdict,
            self.why,
        )


@dataclasses.dataclass(frozen=True)
class PageScan:
    ats: str = ""
    slug: str = ""
    careers_url: str = ""
    hint: str = ""  # a Workday or Oracle board the page mentions: no slug is read from it
    blocked: bool = False


def clean_domain(raw: str) -> str:
    """A domain as the list writes it: lower case, no scheme, path or `www.`; empty when it is not a domain."""
    host = re.split(r"[/?#]", re.sub(r"^[a-z][a-z0-9+.-]*://", "", raw.strip().lower()))[0]
    host = host.removeprefix("www.").rstrip(".")
    return host if "." in host else ""


def read_candidates(path: str) -> tuple[list[Candidate], int]:
    """The rows of the CSV (columns name, domain, kind), in order, each domain once, and the count of rows left out
    because they had no name or no valid domain or repeated a domain."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if not set(CANDIDATE_COLUMNS) <= set(reader.fieldnames or ()):
                raise CandidateFileError(f"{path}: the first line must name the columns {', '.join(CANDIDATE_COLUMNS)}")
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise CandidateFileError(f"{path}: cannot be read ({type(exc).__name__})") from None
    out: list[Candidate] = []
    seen: set[str] = set()
    for row in rows:
        name, domain = (row["name"] or "").strip(), clean_domain(row["domain"] or "")
        if not name or not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(Candidate(name, domain, (row["kind"] or "").strip()))
    return out, len(rows) - len(out)


def split_candidates(
    candidates: list[Candidate], companies: list[dict[str, Any]], excluded: list[str]
) -> tuple[list[Candidate], int, int]:
    """(the candidates still to probe, how many are already in the list, how many are excluded). An excluded company
    is matched by name and by domain and only counted: its name is never printed."""
    excluded_domains = {clean_domain(str(c.get("domain") or "")) for c in companies if c.get("excluded") is not None}
    listed_names = {str(c.get("name") or "").strip().casefold() for c in companies}
    listed_domains = {clean_domain(str(c.get("domain") or "")) for c in companies}
    todo: list[Candidate] = []
    listed = barred = 0
    for c in candidates:
        if is_excluded(c.name, excluded) or c.domain in excluded_domains:
            barred += 1
        elif c.name.casefold() in listed_names or c.domain in listed_domains:
            listed += 1
        else:
            todo.append(c)
    return todo, listed, barred


def scan_career_pages(domain: str) -> PageScan:
    """The career page link scan of discover.py: the home page and the usual career paths, with and without `www.`,
    for a link to a job board. A refused request (robots.txt) shows in `common.robots_blocked`; a 403 or 429 is
    recorded here. Nothing is retried or worked around."""
    careers_url = hint = ""
    blocked = False
    for path in ["/", *CAREER_PATHS]:
        for host in (f"https://www.{domain}", f"https://{domain}"):
            r = get(host + path)
            if r is None:
                continue
            if r.status_code in BOT_STATUSES:
                blocked = True
            elif r.status_code == HTTP_OK:
                found = ats_in_text(r.text)
                if found:
                    return PageScan(found[0], found[1], host + path, blocked=blocked)
                hint = hint or next((ats for ats, rx in PAGE_HINTS if rx.search(r.text)), "")
                if path != "/" and not careers_url:
                    careers_url = r.url
    return PageScan(careers_url=careers_url, hint=hint, blocked=blocked)


def board_name(ats: str, slug: str) -> str:
    """The company name a public job board states for itself, or '' when it states none (or cannot be read)."""
    try:
        if ats == "greenhouse":
            r = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            return str(r.json().get("name") or "") if r is not None and r.status_code == HTTP_OK else ""
        if ats == "workable":
            r = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            return str(r.json().get("name") or "") if r is not None and r.status_code == HTTP_OK else ""
        if ats in ("lever", "ashby"):
            host = "jobs.lever.co" if ats == "lever" else "jobs.ashbyhq.com"
            r = get(f"https://{host}/{slug}")
            m = HTML_TITLE.search(r.text) if r is not None and r.status_code == HTTP_OK else None
            title = html.unescape(m.group(1)).strip() if m else ""
            return title.removesuffix(" Jobs").strip() if ats == "ashby" else title
        if ats == "smartrecruiters":
            r = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
            content = r.json().get("content") if r is not None and r.status_code == HTTP_OK else None
            return str(content[0]["company"]["name"]) if content else ""
    except FEED_ERRORS:
        return ""
    return ""


def _tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", name.casefold()) if t not in NAME_NOISE]


def name_match(candidate: str, board: str) -> str:
    """yes when the two names are the same company name (ignoring case, punctuation and `Inc`, `Ltd`, ...; one may
    be the other plus more words), no when they differ, unknown when the board states no name. `air` is not
    `airbnb`: a shorter name must match whole words, which is the mistake this check exists for."""
    if not board.strip():
        return "unknown"
    a, b = _tokens(candidate), _tokens(board)
    if not a or not b:
        return "no"
    same = "".join(a) == "".join(b) or a == b[: len(a)] or b == a[: len(b)]
    return "yes" if same else "no"


def _guess(candidate: Candidate) -> tuple[str, str, list[Any], str, str]:
    """The ATS slug candidates in discover.py's order. A board with Israel jobs whose name matches ends the search;
    the first one whose name does not is kept as the answer to report if no other turns up. Returns (ats, slug,
    jobs, board name, name match), the ats empty when nothing was found."""
    fallback: tuple[str, str, list[Any], str, str] | None = None
    for ats in PROBE_ORDER:
        for slug in slug_candidates(candidate.name, candidate.domain):
            jobs = _feed(ats, slug)
            if jobs and israel_count(jobs) > 0:
                board = board_name(ats, slug)
                hit = (ats, slug, jobs, board, name_match(candidate.name, board))
                if hit[4] == "yes":
                    return hit
                fallback = fallback or hit
    return fallback or ("", "", [], "", "n/a")


def probe(candidate: Candidate) -> Row:
    """Probe one candidate: the career page link scan, then the ATS slug guesses, then the verdict (see `Row`).
    Only a location is ever read from a posting, and only to count the Israel ones."""
    reset_robots_block()
    scan = scan_career_pages(candidate.domain)
    blocked = scan.blocked or bool(robots_blocked())  # of the company's own site, before any board is asked
    if scan.ats:
        ats, slug, jobs, board, match = scan.ats, scan.slug, _feed(scan.ats, scan.slug), "", "page"
    elif scan.hint:
        return Row(candidate, scan.hint, runner_risk="yes", verdict="needs-yes", why="pin by hand: runner may fail")
    else:
        ats, slug, jobs, board, match = _guess(candidate)
    if not ats:
        if scan.careers_url:
            return Row(candidate, "browser", verdict="needs-yes", why="career page only: browser crawl, time budget")
        return Row(candidate, why="blocked" if blocked else "not_found")
    return _judge(Row(candidate, ats, slug, None, board, match, _risk(ats)), jobs)


def _judge(row: Row, jobs: list[Any] | None) -> Row:
    """The verdict of a probed board: Israel jobs on it and a name that matches keep it."""
    if jobs is None:
        return dataclasses.replace(row, why="feed_failed")
    row = dataclasses.replace(row, israel_jobs=israel_count(jobs))
    if row.israel_jobs == 0:
        return dataclasses.replace(row, why="no_israel_jobs")
    if row.name_match in ("yes", "page"):
        return dataclasses.replace(row, verdict="keep")
    return dataclasses.replace(row, why="name_mismatch" if row.name_match == "no" else "name_unknown")


def _feed(ats: str, slug: str) -> list[Any] | None:
    """The jobs of a board, None when it cannot be read (a failed request or an answer that is not a job list)."""
    try:
        return ADAPTERS[ats](slug)
    except FEED_ERRORS:
        return None


def _risk(ats: str) -> str:
    return "yes" if ats in RUNNER_RISK else "no"


def format_table(rows: list[Row]) -> str:
    """The rows as aligned text, one line each, under a header."""
    cells = [TABLE_HEADER, *(row.cells() for row in rows)]
    widths = [min(max(len(line[i]) for line in cells), 32) for i in range(len(TABLE_HEADER))]
    return "\n".join("  ".join(c[:w].ljust(w) for c, w in zip(line, widths, strict=True)).rstrip() for line in cells)


def summary(rows: list[Row], listed: int, barred: int, left_out: int) -> str:
    """The counts at the end of a run. The excluded count is a number only."""
    verdicts = collections.Counter(row.verdict for row in rows)
    reasons = collections.Counter(row.why for row in rows if row.why)
    out = [
        f"Probed: {len(rows)}",
        f"Skipped before probing: {listed} already in the list, {barred} excluded (a count only), "
        f"{left_out} without a name or domain or a repeated domain",
        *_lines("Verdicts", verdicts),
    ]
    return "\n".join([*out, *(_lines("Why not keep", reasons) if reasons else [])])


def keep_records(rows: list[Row]) -> list[dict[str, Any]]:
    """The keep rows for the coordinator: what a new record and its pin need."""
    return [
        {
            "name": r.candidate.name,
            "domain": r.candidate.domain,
            "kind": r.candidate.kind,
            "ats": r.ats,
            "slug": r.slug,
            "israel_jobs": r.israel_jobs,
            "runner_risk": r.runner_risk,
        }
        for r in rows
        if r.verdict == "keep"
    ]


def candidates_main(argv: list[str], companies_path: str = COMPANIES) -> int:
    """`python3 survey.py --candidates FILE [--limit N] [--keep OUT.json]`: probe the companies of a CSV (name, domain,
    kind) and print a keep-or-skip table to stdout. Skips the companies already in the pulled list and the excluded
    ones (only counted). Dev tool: nothing is written to Supabase and nothing but the keep file is written."""
    parser = argparse.ArgumentParser(prog="survey.py")
    parser.add_argument("--candidates", required=True, metavar="FILE", help="CSV with the columns name,domain,kind")
    parser.add_argument("--limit", type=int, metavar="N", help="probe only the first N companies not skipped")
    parser.add_argument("--keep", metavar="OUT.json", help="write the keep rows to this JSON file")
    args = parser.parse_args(argv)
    if not os.path.exists(companies_path):
        print(CANDIDATES_PULL_HINT.format(path=companies_path), file=sys.stderr)
        return 1
    try:
        excluded = load_excluded(companies_path)
        candidates, left_out = read_candidates(args.candidates)
    except (ExclusionError, CandidateFileError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    if not excluded:
        print(NO_EXCLUDED_NOTE, file=sys.stderr)
    todo, listed, barred = split_candidates(candidates, load_companies(companies_path), excluded)
    if args.limit is not None:
        todo = todo[: max(args.limit, 0)]
    rows: list[Row] = []
    with ThreadPoolExecutor(CANDIDATE_THREADS) as pool:
        for row in pool.map(probe, todo):
            rows.append(row)
            print(f"probed {len(rows)}/{len(todo)}", file=sys.stderr)
    print(format_table(rows))
    print(summary(rows, listed, barred, left_out))
    if args.keep:
        with open(args.keep, "w", encoding="utf-8") as fh:
            json.dump(keep_records(rows), fh, indent=1, ensure_ascii=False)
            fh.write("\n")
    return 0


async def main() -> None:
    cos = [
        c
        for c in crawled_companies(load_companies())
        if c["status"] in ("careers_page_only", "not_found") and c["name"] not in SKIP
    ]
    sem = asyncio.Semaphore(4)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=USER_AGENT)
        out = await asyncio.gather(*[survey(ctx, c, sem) for c in cos])
        await b.close()
    with open("data/survey.json", "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    if "--baseline" in sys.argv[1:]:
        sys.exit(baseline_main())
    if "--candidates" in sys.argv[1:]:
        sys.exit(candidates_main(sys.argv[1:]))
    asyncio.run(main())
