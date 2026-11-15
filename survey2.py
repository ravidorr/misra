"""Deeper survey: follow 'open positions' links, record ATS hosts and job-list JSON responses. Dev tool."""

import asyncio
import contextlib
import json
import re
import sys
from typing import Any

from playwright.async_api import async_playwright

from common import USER_AGENT

ATS = re.compile(
    r"(myworkdayjobs\.com|comeet\.(?:com|co)|greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|"
    r"workable\.com|icims\.com|taleo\.net|oraclecloud\.com|successfactors|jobvite\.com|teamtailor\.com|"
    r"bamboohr\.com|recruitee\.com|personio\.|breezy\.hr|eightfold\.ai|phenompeople|avature\.net|"
    r"hibob\.com|pinpointhq|join\.com|freshteam|teamme\.link|jobs\.cz|gem\.com|rippling\.com|ultipro|"
    r"paylocity|dayforce|brassring)",
    re.I,
)
GO = re.compile(
    r"open positions|search jobs|view (all )?(jobs|positions|openings)|all jobs|see (all )?(openings|jobs|"
    r"roles|positions)|explore (roles|jobs|positions)|find your (role|job|next)|current openings|"
    r"browse jobs|job openings|apply now|join us",
    re.I,
)
NOISE = re.compile(
    r"google|facebook|doubleclick|segment|hotjar|cookie|gtm|analytics|linkedin|clarity|sentry|datadog|"
    r"fullstory|onetrust|cloudflare|newrelic|nr-data|zoominfo|optimizely|6sense|demandbase|6si",
    re.I,
)

HTTP_ERROR = 400
MAX_DEPTH = 6
MIN_LIST = 3


def joblists(obj: Any, path: str = "", depth: int = 0) -> list[tuple[str, int, list[str]]]:
    out: list[tuple[str, int, list[str]]] = []
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, list) and len(obj) >= MIN_LIST and all(isinstance(x, dict) for x in obj[:5]):
        keys: set[str] = set().union(*[set(x.keys()) for x in obj[:5]])
        if any(re.search(r"title|jobtitle|name|text|position", k, re.I) for k in keys) and any(
            re.search(r"location|city|url|link|id|department|country", k, re.I) for k in keys
        ):
            out.append((path, len(obj), sorted(keys)[:8]))
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += joblists(v, f"{path}.{k}", depth + 1)
    elif isinstance(obj, list):
        for v in obj[:3]:
            out += joblists(v, path + "[]", depth + 1)
    return out


async def survey(  # noqa: PLR0912, PLR0915
    ctx: Any, co: dict[str, Any], sem: asyncio.Semaphore
) -> dict[str, Any]:
    async with sem:
        res: dict[str, Any] = {
            "name": co["name"],
            "ats": set(),
            "lists": [],
            "final": "",
            "title": "",
            "err": "",
            "steps": [],
        }
        page = await ctx.new_page()
        tasks: list[Any] = []

        def on_req(r: Any) -> None:
            if ATS.search(r.url) and not NOISE.search(r.url):
                res["ats"].add(re.sub(r"\?.*", "", r.url)[:110])

        async def on_resp(r: Any) -> None:
            try:
                if (
                    r.request.resource_type in ("xhr", "fetch")
                    and "json" in (r.headers.get("content-type") or "")
                    and not NOISE.search(r.url)
                ):
                    d = await r.json()
                    for p, n, k in joblists(d):
                        res["lists"].append((re.sub(r"\?.*", "", r.url)[:100], p, n, k))
            except Exception:
                pass

        page.on("request", on_req)
        page.on("response", lambda r: tasks.append(asyncio.ensure_future(on_resp(r))))
        start = [co.get("careers_url")] if co.get("careers_url") else []
        start += [
            f"https://www.{co['domain']}/careers",
            f"https://{co['domain']}/careers",
            f"https://www.{co['domain']}/company/careers",
        ]
        loaded = False
        for u in dict.fromkeys(start):
            try:
                r = await page.goto(u, timeout=40000, wait_until="domcontentloaded")
                if r and r.status < HTTP_ERROR:
                    loaded = True
                    break
            except Exception as e:
                res["err"] = type(e).__name__
        if not loaded:
            res["err"] = res["err"] or "nopage"
        else:
            for hop in range(2):  # land, then follow one "open positions" style link
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=15000)
                await page.wait_for_timeout(1000)
                res["steps"].append(page.url[:90])
                for f in page.frames:
                    if ATS.search(f.url) and not NOISE.search(f.url):
                        res["ats"].add("FRAME " + re.sub(r"\?.*", "", f.url)[:100])
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "e=>e.map(x=>[x.href,(x.innerText||x.getAttribute('aria-label')||'').trim().slice(0,60)])",
                )
                for h, _t in links:
                    if ATS.search(h) and not NOISE.search(h):
                        res["ats"].add("LINK " + re.sub(r"\?.*", "", h)[:100])
                if hop == 0 and not res["ats"] and not res["lists"]:
                    cand = [
                        h
                        for h, t in links
                        if GO.search(t)
                        and h.startswith("http")
                        and "#" not in h.split("/")[-1][:1]
                        and h.rstrip("/") != page.url.rstrip("/")
                    ]
                    if cand:
                        try:
                            await page.goto(cand[0], timeout=40000, wait_until="domcontentloaded")
                            continue
                        except Exception:
                            pass
                    break
                if hop == 0:  # hits found on the landing page: nothing to follow
                    break
            res["final"] = page.url[:100]
            res["title"] = (await page.title())[:50]
        await asyncio.gather(*tasks, return_exceptions=True)
        await page.close()
        res["ats"] = sorted(res["ats"])[:5]
        res["lists"] = res["lists"][:4]
        print(
            f"{co['name']:16s} ats={len(res['ats'])} lists={len(res['lists'])} err={res['err']} | {res['title']}",
            flush=True,
        )
        return res


async def main() -> None:
    names = set(sys.argv[1:])
    with open("data/companies.json") as fh:
        cos = [c for c in json.load(fh) if (c["name"] in names if names else c["status"] != "ok")]
    sem = asyncio.Semaphore(4)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=USER_AGENT, locale="en-US")
        out = await asyncio.gather(*[survey(ctx, c, sem) for c in cos])
        await b.close()
    with open("data/survey2.json", "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    for r in out:
        if r["ats"] or r["lists"]:
            print("##", r["name"], "|", r["final"])
            for a in r["ats"]:
                print("   ATS ", a)
            for lst in r["lists"]:
                print("   LIST", lst[0], lst[1], lst[2], lst[3])


if __name__ == "__main__":
    asyncio.run(main())
