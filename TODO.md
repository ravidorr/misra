# TODO

Open work, as of 2026-11-14. Each item says what triggers it, how to do it and when it is done. When one is finished,
delete it and update the counts in the others: the 139 and the four segment counts in the research item (a pin removes
one row from one segment), its "(37)" if the company had no careers URL, and the "twelve" and "thirteen" in the Workday
item. Observations about live sites below come from checks made on 2026-11-14 and may have changed.

## Review the Node and Python pins

- [ ] Trigger: Node 27 becomes LTS (scheduled 2027-10-20) or Python 3.12 nears its end of life (October 2028), whichever
  comes first. Nothing to do before then.
  - State now (as of 2026-11-14, from the Node release schedule and the Python release page): Node 26 has been LTS since
    2026-10-28 and `.nvmrc` is on its latest release (26.13.1). Python has no LTS; 3.12 is the floor and the version CI
    and the daily job run.
  - Where the pins live, to change together:
    - Node: `.nvmrc`; `engines.node` in `package.json` (`>=26.13.1 <27`, so the cap must be raised too); the same
      `engines` value under `packages[""]` in `package-lock.json` (regenerate it with `npm install`; the other `engines`
      lines in the lock file belong to dependencies and are left alone); `packageManager` in `package.json` (npm 11.20.0
      today: check it still fits the new Node); the Node badge and "Node 26" in `README.md`. The `ci.yml` and
      `release.yml` workflows read `.nvmrc`, so they follow.
    - Python: `python-version` in `ci.yml`, `release.yml` and `daily.yml`; `requires-python`, ruff `target-version`
      and mypy `python_version` in `pyproject.toml`; "Python 3.12+" in `README.md`.
    - Playwright is pinned twice, `playwright==1.65.0` in `pyproject.toml` and in the `pip install` line of `daily.yml`,
      and the two must change together: Dependabot can bump `pyproject.toml` but not the workflow line, and `daily.yml`
      also installs `requests` unpinned. Check that the pinned version supports a new Python before moving it.
  - Steps:
    1. Check the dates at the Node release schedule and the Python release page; take the newest LTS (Node) or the
       newest release that Playwright and the dev tools support (Python).
    2. Change the pins above in one PR. It touches `package.json` or `pyproject.toml`, so it needs a version bump and a
       `CHANGELOG.md` entry (see `AGENTS.md`); the version moves in `package.json`, `pyproject.toml` and the root entry
       of `package-lock.json`.
    3. Run `npm run lint`, `npm run typecheck` and `npm run test:coverage` on the new versions, with Chromium installed
       so the browser tests run (a machine on an older Node cannot `npm ci`: use the new Node).
    4. After the merge, run the daily workflow once by hand (Actions, "daily-job-digest", Run workflow) and check it is
       green.
  - Done when: the pins name the new versions and that manual run passed. Put the next review date here.

## Workday companies waiting for Workday

- [ ] Twelve companies (thirteen sites: Kyndryl has two) run their careers on Workday, and the `workday` adapter is
  merged, but their hosts could not be checked: on 2026-11-14 every wd1 and wd5 host answered the job search with a 303
  redirect that ends at `static.community.workday.com/maintenance-page.html` ("Workday is currently unavailable. We are
  experiencing a service interruption", so an outage notice, with no planned window given), while the wd3 hosts (Samsung,
  Criteo, both already pinned) answered normally. Retries from 13:18 to 14:04 UTC changed nothing. Nothing is pinned for
  them, on purpose: a wrong address or a down host would show as a "not crawled" gap on the first day.
  - The addresses came from the companies' own careers pages:

    | Company | Slug |
    | --- | --- |
    | DoubleVerify | `doubleverify/wd5/DV_Careers` |
    | Integral Ad Science | `integralads/wd1/IAScareers` |
    | Marvell | `marvell/wd1/MarvellCareers` |
    | Broadcom | `broadcom/wd1/External_Career` |
    | Visa | `visa/wd5/Visa` |
    | Medtronic | `medtronic/wd1/MedtronicCareers` |
    | Motorola Solutions | `motorolasolutions/wd5/Careers` |
    | Palo Alto Networks | `paloaltonetworks/wd5/panwexternalcareers` |
    | CrowdStrike | `crowdstrike/wd5/crowdstrikecareers` |
    | Proofpoint | `proofpoint/wd5/proofpointcareers` |
    | Kyndryl | `kyndryl/wd5/KyndrylProfessionalCareers` and `kyndryl/wd5/KyndrylEarlyCareers` |
    | Workday | `workday/wd5/Workday` |

  - Steps:
    1. Probe a slug from the repository root (it needs the `.venv`, see the README): `.venv/bin/python -c "import adapters;
       print(adapters.workday('visa/wd5/Visa'))"`. `None` means the host did not answer with a job search (an outage, a
       wrong tenant or site, an error); `[]` means it answered and has no job in Israel; a list is the Israel jobs, each a
       dict with `job_id`, `title`, `location` and `url`.
    2. If it is `None`, retry on another day and at another time. If it stays `None` for several days, open the careers
       page in a browser: the tenant, data centre or site name may have changed.
    3. For each slug that answers, check the result by hand once: open two of the `url`s in a browser (they should be real
       postings in Israel) and, for an empty list, look at the company's own careers page, since several of these
       companies have Israeli offices. Then pin it with four edits. Edit the files by hand and append at the end: do not
       rerun `discover.py`, which re-probes every company and rewrites `data/companies.json`.
       - `data/overrides.json`: `"Visa": {"ats": "workday", "slug": "visa/wd5/Visa", "israel_only": true}`
       - `data/companies.json`: `{"name", "domain", "careers_url": "", "ats": "workday", "slug", "source": "override",
         "status": "ok", "n_jobs", "n_israel", "israel_only": true}`, with `n_jobs` and `n_israel` from the probe (the
         adapter returns Israel jobs only, so they are equal: Samsung is 3 and 3, Criteo 0 and 0; the domain is in
         `data/unresolved_candidates.csv`)
       - `data/seed_companies.csv`: one `Name,domain` row
       - remove the row from `data/unresolved_candidates.csv`
       - both CSV files use CRLF line endings: keep them, and update the counts named at the top
    4. A board with no Israel job today is fine to pin: the `israel_only` rule reports the first Israel job it ever lists.
    5. Kyndryl is one company, so one entry (overrides are keyed by name): probe both sites and pin the one that lists
       Israel jobs, the larger one if both do, and either (say which) if neither does.
    6. This is a data-only PR: no version bump. Run the gates and the review loop as for any PR.
  - Done when: each of the twelve is pinned or has a recorded reason it cannot be (move it to the research item).

## Research the remaining unresolved companies

- [ ] `data/unresolved_candidates.csv` lists 137 researched companies with no usable job feed (as of 2026-11-14: 51
  multinational, 48 deeptech, 19 cyber_infra, 19 biz_software). It includes the twelve Workday companies above. Each needs
  its own research; there is no batch fix left. Every observation about a live site below is from the checks of
  2026-11-14, a crawl of the 105 rows that had a careers URL that day (100 today) and a scan of their rendered pages. The
  remaining work falls into these groups:
  - **No careers URL (37).** Status `not_found` in `data/new/*_companies.json`: OX Security, Upsolver, Ceva, Speedata,
    Teramount, X-Globe, Q Factor, Gilat, Siklu, Silicom, Sight Diagnostics, Brainsway, EarlySign, Diagnostic Robotics,
    Tevel Aerobotics, CytoReason, Converge Bio, SolarEdge, Watergen, Stratasys, AIR, Intel, Cisco, SAP, eBay, GE HealthCare,
    HP, Dell Technologies, Mastercard, Cadence, Autodesk, Adobe, Akamai, Ford, Ericsson, Uber, Keysight. Find the real
    careers page first, then continue with the groups below. The large multinationals will need a location-filtered
    address or their ATS (see the last group).
  - **A job board was found but there is no adapter.** LinearB (JazzHR), Splitit (BambooHR), Playstudios (Jobvite) and
    Booking.com (iCIMS). Each adapter is its own PR: a function in `adapters.py`, an entry in `ADAPTERS`, tests with the
    fakes in `tests/test_adapters.py`, a version bump and a changelog entry. American Express showed an Eightfold
    signature, but `aexp.eightfold.ai/careers` is a 404: find the real host, and pin it as the Citi and PayPal entries are
    (the browser crawler reads Eightfold boards, with `?location=Israel` and `israel_only`). Palo Alto Networks is in the
    Workday item: pinning it there also closes its entry under the partial lists below.
  - **A board was found but its address is wrong or dead.**
    - Pliops: on 2026-11-14 the Comeet page for `pliops/05.004` said "This Spark Hire Recruit account has been
      deactivated", and it still does, but `pliops.com/careers` still links to that page, so the company has published no
      new board. A Workable account `pliops` exists (`adapters.workable("pliops")` answers with no job) but nothing links
      to it, so it is not pinned: pin it, or the board the careers page ends up linking to, once it lists a job.
    - Western Digital: the SmartRecruiters board (`WesternDigital`, 325 jobs) has no Israel job today, so there is
      nothing to show. It can be pinned with `israel_only` now that the matcher and the new-company rule are fixed, but it
      would fetch 325 jobs a day to store none.
    - Cloudflare: the Greenhouse board's locations are "Hybrid" or "Distributed", never a city, so an Israel job could
      not be recognised. The adapter would need to read the offices (`?content=true`) first.
  - **The page returned links, not jobs.** Ex Libris, Optibus, REE Automotive, Ormat and similar pages gave navigation
    links such as "View openings". Follow the link to the real list one level down; it may embed a board.
  - **The crawl parsed, but badly or partly.**
    - Valens Semiconductor: 3 jobs, but the title and team are swapped ("Engineer" with team "Senior Backend", for the
      page's "Senior Backend Engineer"). Fix `_parse_card` for a title split over two lines, then pin
      `https://www.valens.com/positions/`.
    - Palo Alto Networks: a partial list (4 Israel jobs) that would churn day to day. Use its Workday board.
  - **Large multinational boards that show a worldwide first page.** Google, Oracle, Intuit, Apple, IBM, JPMorgan Chase
    and others returned jobs, but almost none in Israel (Apple's page had one), because the page is a worldwide search.
    They need a career address filtered to Israel (as `citi.eightfold.ai/careers?location=Israel` is) with `israel_only`,
    or their ATS. Check that the filter works before pinning.
  - **Pages that loaded and showed no job links.** 72 of the 105 pages crawled on 2026-11-14 returned no job, five of
    them with an empty title: the list is probably drawn by JavaScript from a JSON feed. Open the page, watch its network
    requests for the feed, then decide between an adapter and a browser pin.
  - **Bot protection.** Some pages answered 403, "Access Denied" or a browser check. Leave them and record it. Do not try
    to get around bot protection: a few pinned companies (Check Point, Papaya Global, Unity) are already blocked from the CI
    runner this way and show up as gaps in the digest.
  - How to research a company:
    1. Find the real careers page, then look for the job board it uses: an embedded or linked Greenhouse, Lever, Ashby,
       Workable, SmartRecruiters, Comeet or Workday board (`discover.ATS_LINK` has the patterns, and the page's HTML,
       links and iframes all need a look, since scripts hide them) or a JSON feed in its network requests.
       A Workable job link is `apply.workable.com/j/<shortcode>` on a page: `ATS_LINK` read its `j` as the account, so
       open the link, which redirects to `apply.workable.com/<account>/j/<shortcode>`, and take the account from there.
    2. With an existing adapter, probe it from a shell (`adapters.ADAPTERS["greenhouse"]("slug")` and so on). Without
       one, the browser crawler is the fallback: `browser_crawl.crawl_many([url])` (set `PW_CHROMIUM` to a Chromium
       build, or run `playwright install chromium`) shows what it reads.
    3. Read the result before pinning. It needs real titles, distinct links and a location per job that the Israel matcher
       (`discover.ISRAEL`) can recognise, for the jobs that will appear and not only the ones there today. A list of
       navigation links, a worldwide first page or a partial list is not a feed.
    4. Pin with the four edits described under the Workday item (an `israel_only` company if it is a large multinational),
       one company or one source per PR so a bad pin is easy to drop. Code changes (an adapter, a parser fix) need a
       version bump and a changelog entry; data-only PRs do not.
    5. If a company cannot be done, say why. Adding a `reason` column to `data/unresolved_candidates.csv` is safe (no
       code reads that file) and keeps the next person from repeating the work.
  - Tools: `browser_crawl.crawl_many` over every row with a careers URL, and a scan of the rendered pages for ATS links,
    were the two passes behind this list; both were throwaway scripts and are not in the repo. Adding them as a dev tool
    next to `survey.py` and `survey2.py` would make a re-run easy (a new module must be registered as `AGENTS.md`
    describes).
  - Done when: `data/unresolved_candidates.csv` is empty, or every remaining row has a `reason`.
