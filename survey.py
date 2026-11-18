"""Survey custom career pages with a real browser: which ATS / JSON endpoints do they use?
Writes data/survey.json. Dev tool, not part of the daily run.

`python survey.py --baseline` is the read-only coverage report of PLAN_3.md step A1 (see `baseline`): counts only,
printed to stdout, from the pulled company list and the public docs/jobs.json. It needs no browser."""

import asyncio
import collections
import contextlib
import json
import os
import re
import sys
from typing import Any

from playwright.async_api import async_playwright

from common import COMPANIES, USER_AGENT, crawled_companies, load_companies

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
    asyncio.run(main())
