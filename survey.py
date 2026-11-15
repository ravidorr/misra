"""Survey custom career pages with a real browser: which ATS / JSON endpoints do they use?
Writes data/survey.json. Dev tool, not part of the daily run."""

import asyncio
import contextlib
import json
import re
from typing import Any

from playwright.async_api import async_playwright

from common import USER_AGENT

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


async def main() -> None:
    with open("data/companies.json") as fh:
        cos = [c for c in json.load(fh) if c["status"] in ("careers_page_only", "not_found") and c["name"] not in SKIP]
    sem = asyncio.Semaphore(4)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=USER_AGENT)
        out = await asyncio.gather(*[survey(ctx, c, sem) for c in cos])
        await b.close()
    with open("data/survey.json", "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
