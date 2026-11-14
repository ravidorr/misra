# TODO

Open work, as of 2026-11-14. Each item says what triggers it, how to do it and when it is done. When one is finished,
delete it and update the counts in the others: the 103 and the four segment counts in the research item (a pin removes
one row from one segment), its "(30)" if the company had no careers URL, else its "(73 today)".
Observations about live sites below come from checks made on 2026-11-14 and may have changed.

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

## Research the remaining unresolved companies

- [ ] `data/unresolved_candidates.csv` lists 103 researched companies with no usable job feed (as of 2026-11-14: 24
  multinational, 45 deeptech, 18 cyber_infra, 16 biz_software). Each needs
  its own research; there is no batch fix left. Every observation about a live site below is from the checks of
  2026-11-14, a crawl of the 105 rows that had a careers URL that day (73 today) and a scan of their rendered pages. The
  remaining work falls into these groups:
  - **No careers URL (30).** Status `not_found` in `data/new/*_companies.json`: OX Security, Upsolver, Ceva, Speedata,
    Teramount, X-Globe, Q Factor, Gilat, Siklu, Silicom, Sight Diagnostics, Brainsway, EarlySign, Diagnostic Robotics,
    Tevel Aerobotics, CytoReason, Converge Bio, SolarEdge, Watergen, Stratasys, AIR, Intel, SAP, eBay,
    Autodesk, Akamai, Ford, Ericsson, Uber, Keysight. Find the real
    careers page first, then continue with the groups below. The large multinationals will need a location-filtered
    address or their ATS (see the last group).
  - **A job board was found but there is no adapter.** Playstudios and Booking.com. Playstudios was listed as a Jobvite
    board, but on 2026-11-14 `jobs.jobvite.com/playstudios-inc` is an invalid board and the careers page links to
    Dayforce (`jobs.dayforcehcm.com/en-US/playstudios/CANDIDATEPORTAL`, a page that renders in a browser); it has a Tel
    Aviv office, so it needs a Dayforce adapter or a browser pin. Booking.com (iCIMS) is skipped: its iCIMS board answered
    403 to a plain request on 2026-11-14 (bot protection, not to be bypassed) and `careers.booking.com` is a custom site
    with no job feed; revisit it with the large multinationals. A new adapter is its own PR: a function in `adapters.py`,
    an entry in `ADAPTERS`, tests with the fakes in `tests/test_adapters.py`, a version bump and a changelog entry.
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
  - **Large multinational boards that show a worldwide first page.** Google, Intuit, Apple, IBM
    and others returned jobs, but almost none in Israel (Apple's page had one), because the page is a worldwide search.
    They need a career address filtered to Israel (as `citi.eightfold.ai/careers?location=Israel` is) with `israel_only`,
    or their ATS. Check that the filter works before pinning.
    Findings on the 24 multinationals left after the pins of 2026-11-14 (so the next person does not repeat them):
    - Workday guesses that still do not answer (checked again on 2026-11-14 after the adapter fix): Intel
      (`intel/wd1/External`) and Autodesk (`autodesk/wd1/Ext`, `autodesk/wd1/uni`) are sent to Workday's maintenance
      page, which is also what a tenant that does not exist on that data centre gets, and Akamai
      (`akamai/wd1/akamaicareers`) answers 500. The tenant, data centre or site name is still unknown: find it from the
      company's own careers page, not by guessing.
    - Bot protection (403 to a plain request, 2026-11-14; not to be bypassed): Intel (`jobs.intel.com`), Autodesk,
      Akamai, General Motors (`search-careers.gm.com`), Ford, SAP, Uber, Booking.com.
    - Other platforms with no adapter yet: AMD and Keysight (iCIMS: `careers-amd.icims.com`,
      `careers-keysight.icims.com`), Synopsys (Avature: `synopsys.avature.net`), Intuit (Radancy: the search takes a
      location only with coordinates and shows "Multiple Locations" for many jobs), Google (the results page embeds
      its data; the crawler read only navigation links on `?location=Israel`), Apple (`jobs.apple.com/en-il/search`
      is a script app; `/api/role/search` answers a redirect), IBM and Meta (no feed found).
    - Ericsson: `jobs.ericsson.com/careers?location=Israel` is an Eightfold board that shows "0 jobs" in Israel today,
      and the crawler reads its "Join Talent Network" link as a job, so it is not pinned; pin it once it lists one.
    - eBay (`careers.ebayinc.com`), Walmart Global Tech (`careers.walmart.com`, a timeout), Siemens
      (`jobs.siemens.com`, no DNS answer), Align Technology (`jobs.aligntech.com`) and Rapid7
      (`careers.rapid7.com/jobs/search`): no feed found in the page HTML; not investigated further.
    - Done on 2026-11-14: new adapters `oraclehcm` (Oracle, JPMorgan Chase, Dell, American Express, Nokia), `amazon`
      and `phenom` (Cisco, GE HealthCare, Mastercard); Workday pins (Salesforce, Cadence, Edwards, Adobe, Zoom,
      Marvell, Medtronic, KLA, Kyndryl, Workday, then Broadcom and Philips once the adapter read a country value with
      its own id and the places of a location facet); HP on Eightfold. American Express and Nokia list no job in
      Israel today, and Adobe and Zoom none either.
  - **Pages that loaded and showed no job links.** 72 of the 105 pages crawled on 2026-11-14 returned no job, five of
    them with an empty title: the list is probably drawn by JavaScript from a JSON feed. Open the page, watch its network
    requests for the feed, then decide between an adapter and a browser pin.
    Findings of 2026-11-14 (feed-finding pass): most of these pages were not JavaScript apps but WordPress sites with the
    Comeet plugin, whose job links look like `/careers/co/<place>/<XX.XXX>/<title>/all`, which the crawler's link pattern
    does not match. The page source holds `"comeet_uid":"<uid>"` (and a token) when the plugin is configured with the
    company's account; the `comeet` adapter then reads the board with slug `<name>/<uid>`. Try the careers-api first, as
    `https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>&details=true`: its jobs carry the board
    name in `url_comeet_hosted_page`. Done: Retym (`retym/C6.003`) and Qedma (`qedma/7A.006`).
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
    4. Pin with four edits (`israel_only` for a large multinational), one company or one source per PR so a bad pin is
       easy to drop. Edit the files by hand and append at the end: do not rerun `discover.py`, which re-probes every
       company and rewrites `data/companies.json`.
       - `data/overrides.json`: `"Visa": {"ats": "workday", "slug": "visa/wd5/Visa", "israel_only": true}`
       - `data/companies.json`: `{"name", "domain", "careers_url": "", "ats": "workday", "slug", "source": "override",
         "status": "ok", "n_jobs", "n_israel", "israel_only": true}`, with `n_jobs` and `n_israel` from the probe (an
         `israel_only` adapter returns Israel jobs only, so they are equal; the domain is in
         `data/unresolved_candidates.csv`)
       - `data/seed_companies.csv`: one `Name,domain` row
       - remove the row from `data/unresolved_candidates.csv`
       - both CSV files use CRLF line endings: keep them, and update the counts named at the top.
       A board with no Israel job today is fine to pin: the `israel_only` rule reports the first Israel job it ever
       lists. Code changes (an adapter, a parser fix) need a version bump and a changelog entry; data-only PRs do not.
       Probe a Workday slug with `.venv/bin/python -c "import adapters; print(adapters.workday('visa/wd5/Visa'))"`:
       `None` is a host that did not answer with a job search (Workday tenants go to a maintenance page for hours at a
       time, and answer again later: probe on more than one day before giving up), `[]` is a board with no Israel job.
    5. If a company cannot be done, say why. Adding a `reason` column to `data/unresolved_candidates.csv` is safe (no
       code reads that file) and keeps the next person from repeating the work.
  - Tools: `browser_crawl.crawl_many` over every row with a careers URL, and a scan of the rendered pages for ATS links,
    were the two passes behind this list; both were throwaway scripts and are not in the repo. Adding them as a dev tool
    next to `survey.py` and `survey2.py` would make a re-run easy (a new module must be registered as `AGENTS.md`
    describes).
  - Done when: `data/unresolved_candidates.csv` is empty, or every remaining row has a `reason`.
