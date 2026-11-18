# Plan 3: more companies

Step plan to close the coverage gap users reported: too few startups and too few large multinationals. Drafted
2026-11-18, revised the same day after two reviews; A1, A2, B1, B3 and D3 are done, B2 waits for the maintainer; the rest is not built yet. Targets are proposals for the

Conventions are those of `PLAN.md`: a change to a listed Python module bumps `package.json` and `pyproject.toml`
together and adds a `CHANGELOG.md` entry; a new Python module is registered in `RELEASE_TRIGGER_PATHS` (`ci.yml`,
`.husky/pre-push`), `py-modules` and mypy `files`; coverage stays at 100%. `survey.py` and `survey2.py` are under
mypy and the coverage rule too, so any change to them ships with tests. Adding, pinning, parking or excluding a company
is not a PR: it is an edit of the `companies` table (see "One list"). Each code or docs sub-step (A1, B2, ...) is one PR.

## Baseline (2026-11-18)

The Supabase table `public.companies` (PLAN_2.md step 3): 347 records, 267 crawled (`crawl: true`), 80 researched only
(`crawl: false`). `data/companies.json` is no longer in git: `sync_supabase.py --pull` writes a git-ignored scratch copy,
and `tests/fixtures/companies.json` is the frozen copy the tests use. A1 regenerates this table from the pulled copy;
the numbers below (as of 2026-11-18) are the ones to check A1 against. To recompute them later, run the A1 report or a
count query with the service role, never printing rows.

- Researched-only by `gap.reason`: `no_careers_url` 23, `needs_reader` 20, `no_feed_found` 14, `site_unusable` 10,
  `bot_protection` 7, `no_openings` 6 (total 80).
- Crawled ATS mix: Comeet 88, Greenhouse 57, browser 32, Workday 25, Ashby 16, Lever 7, `comeet_page` 6, `oraclehcm` 5,
  `smartrecruiters` 5, `workable` 5, Phenom 3, Amazon 1, JazzHR 1, none 14 (the 14 are browser crawls found from the
  career page: 12 `careers_page_only`, 2 `not_found`).
- 73 records are pinned `israel_only`.
- `segment` exists only on researched records (`deeptech` 36, `multinational` 22, `biz_software` 11, `cyber_infra` 11),
  by convention (it is set only on researched records today; nothing enforces it).
- Crawl step: `timeout-minutes: 20` inside a 30 minute job, 8 threads, 1 second per host (`common.py:24`).
- `docs/jobs.json` is 2.1 MB. The history is about 9.7k jobs and about 250 `crawl_log` rows a day.

## Prior findings: do not repeat

`TODO.md` ("Research the remaining unresolved companies") records what was already tried. Read it before any step.

- iCIMS (AMD, Keysight): `robots.txt` is `Disallow: /`. No adapter, ever.
- Avature, SuccessFactors, Pinpoint: each found with a single company. Dayforce: one company (PlayStudios), read by the
  browser crawl. Eightfold is covered by `browser` (`CONTRIBUTING.md`, "Job descriptions").
- Getro boards (Grove, Viola, TLV Partners): `api.getro.com` is `Disallow: /`. Use them for company names only, never as
  a feed. Getro's own job link points to the employer page when it has one.
- Slug guessing for the no-careers-URL group found only an unrelated `air` account on Greenhouse.
- About 20 pinned Workday tenants (Adobe, Broadcom, Cadence, Salesforce and others) show "request failed" only from
  GitHub's runner addresses. Not worked around: no proxy, no other address, no changed User-Agent.

## Rules for the whole plan

- **Public site data only.** Records are public. A candidate is accepted only if its career page is the company's own
  and lists roles in Israel. A company with no Israel role is not added to the crawl.
- **Respect the limits.** `robots.txt` is honoured, bot protection is never worked around, and
  an opt-out is an `excluded` record in the table (readable only by the admin, never deleted, backed up daily to the
  private repository), checked with `is_excluded` before any candidate is probed. That check sees the excluded records
  only when the pull used the service-role key (`SUPABASE_SERVICE_ROLE_KEY`); with any other key they are invisible
  and the check passes silently. Never paste the key in a PR or chat.
- **One list.** No candidate file is committed. Candidate lists live in the scratchpad or the PR description; only
  accepted records reach the `companies` table. Adding a company and excluding one are done on the Companies area of
  the admin page (`https://ravidorr.github.io/misra/admin.html`, admin only). Pins (`source`, `ats`, `slug`) are made
  there too ("Pin the job feed" in the editor), so every pin is in the admin Log. SQL stays only as a fallback
  (`CONTRIBUTING.md`, "Adding a company"); a SQL edit leaves no log entry, and the daily backup history is the only
  record of it.
- **Adapters need two.** Write an adapter only when two or more companies share a platform that `robots.txt` allows.
  One company gets a pinned `browser` or `comeet_page` record.
- **First run must return jobs.** A company is added only if its first crawl returns jobs. A company that returned
  nothing on its first run is baselined late (`run.py:92`, `send_digests.py:287`): its real first jobs would be lost
  or, for a known company, sent as a flood.
- **Count only what the runner can read.** A target counts a company only if it returns jobs from the GitHub runner,
  not from a normal network. Test a Workday or Oracle pin from a daily run before counting it.
- **Batches.** Add at most 25 new companies before each `discover-companies` run (steps D and E; it resolves only
  crawled companies with an empty `status` and never downgrades a working one). The cap is a plan rule, not enforced by
  the workflow. Before each batch, check the three budgets below. After it, review the next daily run (jobs per company,
  new failures, new entries on the coverage card and in the maintainer alert) before the next batch starts. Pinning a
  researched record (step C) is the exception: one pin at a time, as `TODO.md` ("How to research a company", step 4)
  says. A bad pin is reverted by editing the row, and to have a pinned record looked at again its `status` is emptied.
- **No counts to keep.** `tests/test_todo_counts.py` is deleted, so nothing enforces any number quoted in `TODO.md`.
  The current numbers come from the A1 report and each batch description (A4).
- **Known companies.** A company with any row in the history (`jobs` or `crawl_log`, closed rows included) is "known"
  (`run.py` `is_known`). Unlocking, re-pinning or fixing the feed of a known company can email its jobs as new to
  subscribers, so the maintainer makes those edits, not overnight automation. Only companies with no history are
  added or unlocked unattended, and their first crawl is baselined silently.
- **Parking.** Parking a company (`crawl` false) closes its open jobs (`close_uncrawled`); emptying a `status` does
  not. Park only companies with no open jobs unless the closing is intended.
- **Lawyer's files.** `ABOUT.md`, `PRIVACY.md`, `TERMS.md` and `ASSISTANT.md` are not touched by this plan.
- **Track in `TODO.md`.** This plan is the design; open items go to `TODO.md`.

### Budgets (set in A1, checked before every batch)

Measured 2026-11-18 with the A1 report and read-only `gh` commands (counts and times only).

- **Crawl time.** The `Crawl and diff` step took 235 s, 229 s, 212 s, 211 s, 211 s and 216 s (3.5 to 3.9 minutes) in
  the six full daily runs of 2026-11-16 to 2026-11-18 (run ids 44347723718, 44237849843, 44017802979, 44017712839,
  44017112557, 43957726843; the step runs inside a job of 5 to 11 minutes). Ceiling: 15 of the 20 minutes (900 s of
  the `timeout-minutes: 20`). That is about 4 times today's step, so the 267 crawled companies could grow to roughly
  1000 at the same mix; a browser record costs more than a JSON feed, so check the step before every batch. To
  re-measure: `gh run list --workflow=daily.yml --limit 10`, then `gh run view <id> --json jobs`, and take the
  `Crawl and diff` step's `completedAt` minus `startedAt`.
- **Failure budget.** At the last build the coverage card held 28 blocked companies (22 "request failed", 5 where
  `robots.txt` does not allow SmartRecruiters, 1 whose site blocks the runner) and 14 with no feed yet, 122 gaps in all
  with the researched records. These are the known baseline. More than 3 new failing companies after a batch (not in
  that list) blocks the next batch until they are fixed or parked.
- **Size.** `docs/jobs.json` is 2,131,197 bytes (2.0 MiB, about 237 KB gzipped) for 6,764 jobs. The history is about
  9.7k jobs and about 250 `crawl_log` rows a day (about one per crawled company). Estimate: 100 more companies is
  about 1.4 times the log rows a day (about 350) and, at about 25 jobs per company, about 40 percent more `jobs.json`. Ceiling
  proposal: 4 MB for `jobs.json` and 500 `crawl_log` rows a day (the log keeps 90 days, so about 45k rows). The maintainer confirms.

## Step A: measure first

- [x] **A1. Coverage report and budgets.** Extend `survey.py` (with tests) to print: crawled count, companies with at least
  one Israel job in the last run, the Israel job count, researched-only by reason and segment, crawled by `ats`, and the
  crawl step duration from the last daily runs. Record the three budgets above. Done when: one command prints the
  baseline and the budgets are written into this file.

  Status (2026-11-18): done. `python survey.py --baseline` prints the report from the pulled `data/companies.json`
  (`sync_supabase.py --pull-companies`; it stops with that instruction when the file is missing) and the public
  `docs/jobs.json`; it prints counts only, never a name of an excluded company, and for the crawl step duration it
  prints the `gh` commands (it calls no network). The budgets above are measured. The baseline check below was run on
  the frozen copy of the list (`tests/fixtures/companies.json`) with the committed `docs/jobs.json`, because no
  service-role key was available; the maintainer reruns it on the real pulled list.

  Baseline check (frozen list, `docs/jobs.json` generated 2026-11-18 16:29 UTC):

  - Crawled 267, researched only 80, excluded 0.
  - Crawled companies with at least one Israel job: 201 of 267. Israel jobs: 2,747 (of 6,764 open jobs).
  - Researched only by `gap.reason` and `segment`: the same as the Baseline section above (23, 20, 14, 10, 7, 6 and
    36, 22, 11, 11).
  - Crawled by `ats`: as in the Baseline section, which left out `bamboohr` 1 and `greenhouse_url` 1 (`jazzhr` 1 and
    none 14 are there); total 267.
  - Crawled split from `israel_only`: 73 multinational, 194 startup.
- [x] **A2. Startup versus multinational.** `segment` stays research-only (the test and `AGENTS.md` say so, and no code
  reads it for crawled companies). A1 reports the split from `israel_only` (a global company is pinned `israel_only`; an
  Israeli startup is not). Done when: A1 prints the split. If the maintainer wants `segment` on crawled records for a
  dashboard filter, that is a separate change with its own test and `AGENTS.md` edit.

  Status (2026-11-18): done. The A1 report prints the split: 73 multinational (`israel_only`) and 194 startup.
- [x] **A3. Targets.** Fix numeric targets from the A1 output. Confirmed by the maintainer on 2026-11-18: +60 startups
  crawled, +40 multinationals that return jobs from the runner, 30 of the 80 researched records unlocked.

  Status (2026-11-18): done. The three targets above are confirmed.
- **A4. Success metrics.** Per batch and per month: reports received (`CONTRIBUTING.md` has the command), share of
  crawled companies with at least one Israel job, Israel job count before and after. Done when: the numbers appear in
  each batch description.

## Step B: fix what is already listed (no new names)

- [x] **B1. The 14 crawled records with no `ats`.** They are browser crawls (Bigabid, Deel, Dynamic Yield, Hailo, Imperva,
  Innoviz, Rapyd, SentinelOne, Trax, Varonis, Verbit, Vimeo, Wix, Zesty). From the last runs, list those that return no
  jobs. Pin or park only those, one at a time: parking closes the company's jobs (`close_uncrawled`), so check each
  closing is intended. Done when: none is crawled while returning zero jobs without a note.

  Status (2026-11-18): done. Today these 14 records (`crawl: true`, no `ats`, `status` `careers_page_only` or
  `not_found`) were listed as crawled but never crawled: `run.py` crawls only `status == "ok"`, `discover.py` sets those
  two statuses when it finds no feed, and `build_dashboard.py` shows every crawled company that is not `ok` on the
  coverage card (`no_feed_found`, or `no_careers_url` for `not_found`), so they cost nothing but were counted as
  crawled. None had a row in `jobs` or `crawl_log`, so nothing closed and no digest could flood. Each career page was
  read politely (project User-Agent, robots.txt honoured, 1 request a second per host, no bot protection worked
  around), the ATS adapters were tried on the obvious slugs and the browser crawl on the pages. No feed qualified for a
  pin (a pin needs a real feed with Israel jobs): all 14 were parked with a dated note, journal entries 5 to 18
  (`plan3-b1`): `bot_protection` Innoviz (Incapsula), Bigabid (Cloudflare 403), SentinelOne (429);
  `no_openings` Trax (4 roles, none in Israel), Verbit (3 roles, none in Israel); `needs_reader` Zesty (Comeet page
  with 3 roles, 2 in Tel Aviv, that the `comeet_page` reader cannot parse) and Rapyd (7 Tel Aviv roles, half the
  titles read "DETAILS"); `no_feed_found` Deel, Hailo, Varonis, Vimeo, Wix, Dynamic Yield (career address is
  Mastercard's) and Imperva (Thales'). The table now has 253 crawled, 94 researched only, none crawled without `ok`.
  Two may become real work later: a `comeet_page` fix for Zesty, and a better title read for Rapyd.
- [ ] **B2. "Request failed" companies.** Split the failing list into (a) the Workday and Oracle tenants that fail only
  from the runner (known limitation, see above) and (b) real failures (wrong slug, changed feed). Re-test (a) from a
  daily run once; if still blocked, leave them and say so in `TODO.md`. Fix (b). Done when: every failing company is in
  (a) with a dated note or fixed.

  Status (2026-11-18): partly done, waiting for the maintainer. From the last two days of `crawl_log` (runs of
  2026-11-17 04:23 UTC to 2026-11-18 16:28 UTC), 39 companies had at least one failed crawl. (a) 25 Workday tenants,
  all runner-blocked (each answered from a normal network the same day; dated note in `TODO.md`). Oracle HCM: none
  failed. (b) The other 14 are not wrong slugs: 5 SmartRecruiters companies skipped by `robots.txt` (ServiceNow, Nexar,
  Atera, Kaltura, Armis; a decision, not a failure); 7 browser crawls behind bot protection from the runner (Nayax,
  DriveNets, Check Point, Papaya Global; Xtend, Weebit Nano, Freightos held back by the sharp-drop guard); and 2
  `comeet_page` companies (Optibus, REE Automotive) that failed on 2 of 4 runs and answer from a normal network, a
  transient failure. None needs a feed change, and each of the 14 has history, so no change is applied. Done when
  stays open until the maintainer decides on the bot-protected browser companies (keep listed, or park) and the
  Workday limitation (accept, or move the crawl to another network).
- [x] **B3. Audit `israel_only`.** For the 73 pinned records, confirm the filter drops only non-Israel jobs. Spot-check 10
  against the career page. Done when: no known Israel posting is dropped; a mismatch gets an adapter test.

  Status (2026-11-18): done. Method: the adapters were run read-only from a laptop (the project's own `get`: its
  User-Agent, robots.txt enforced, one request a second per host) against the frozen list
  (`tests/fixtures/companies.json`), and each feed's complete answer was compared with what the pipeline keeps. 29
  companies on 7 feed types were compared: Datadog, MongoDB, Okta, Zscaler (Greenhouse), Snowflake (Ashby), Splitit
  (BambooHR), Ceva, DriveNets, Nayax, Papaya Global, Quantum Machines (Comeet), Guesty, Optibus (Comeet pages); for
  Workday, Intel, KLA, Medtronic, Salesforce, Palo Alto Networks, Philips, CrowdStrike, Marvell and Unity (the facet
  list against a search for the word Israel); for Oracle HCM, Dell, JPMorgan Chase, Oracle, American Express and Nokia
  (the keyword route against the location route and, for Dell, against a scan of the whole site by country code).
  Results:

  - Filtered feeds (Greenhouse, Ashby, Comeet, Comeet pages, BambooHR): the places the filter dropped were all outside
    Israel (a few postings have no location at all, such as "Future Opportunities", and cannot be placed); the kept
    ones were all in Israel. No dropped Israel posting, and no spelling or city the matcher lacks, in this data.
  - Workday: the facet route returned every Israel posting the word search found, and no non-Israel posting; the
    word search found only extra postings outside Israel or in several places ("2 Locations").
  - Oracle HCM: a real miss. The keyword Israel matches the text of a posting, not its place: it returned 6 of the 16
    Israel requisitions of Dell (the full scan by country code is the truth), 2 of 19 at Oracle, 1 of 2 at JPMorgan
    Chase, 0 of 1 at Nokia (American Express lists no Israel requisition by either route). The location filter returned all of them, every one with country code IL. Fixed in 0.72.1
    (the adapter asks for the location Israel), with a test that has an Israel and a non-Israel requisition.
  - Not a filter fault: Unity shows 123 jobs outside Israel in `docs/jobs.json` (first seen 2026-11-13, titles such
    as a language name). They are old rows of an earlier browser crawl of the company site, not the Workday feed,
    which returns 8 Israel jobs from a laptop. The runner cannot read Unity's Workday feed (the known block), so the
    old rows are not closed. Step B2 should close them or settle the block.
  - Not checked: the browser crawls and Amazon (no public feed to compare with; every job kept in `docs/jobs.json`
    for them is flagged Israel), and ServiceNow (SmartRecruiters answers nothing from here).

## Step C: unlock the 80 researched records

Each unlocked company is one save in the editor of the admin page, one at a time, so the pin is in the admin Log: open
the company, choose "Crawl it", pin the job feed (`ats` and `slug`; the page writes `source` `override`, empties the
`status`, and drops the `gap` and the `segment` in the same write), and tick "Keep only its Israel jobs" for a
multinational. The page never sets `status` to `ok`. The SQL editor stays as a fallback and is not logged. Then run
`discover-companies`, which sets `status` to `ok` only when the GitHub runner can read the feed (it touches only crawled
companies with an empty `status`). Workday and Oracle pins are the known runner-blocked case, and step E has many of
them. A pin that comes back not `ok` is edited back or parked with a `gap`; never set `status` to `ok` by hand.
Target: 30. Read the "Prior findings" first; do not repeat a checked path.

- **C1. `no_careers_url` (23).** Find the real career page by hand (company site, LinkedIn page, VC portfolio page),
  not by slug guessing. Then read the ATS from the page source (Comeet `company-uid`, Greenhouse embed, Ashby, Lever,
  Workable). Done when: each has a `careers_url` or an updated `gap.note` saying what was tried.
- **C2. `no_feed_found` (14).** The page exists, no known feed. Look for a JSON endpoint the page calls (as HiBob and
  Eightfold). Pin with `source: override`. Done when: each is crawled or has a dated note.
- **C3. `needs_reader` (20).** Group by platform and check `robots.txt` first. An adapter only for a platform with two
  or more companies that `robots.txt` allows (none qualifies today; the count may change after C1 and C2). The rest use
  `browser` or `comeet_page`, within the crawl-time budget. One adapter per PR, with fixtures and 100% coverage.
- **C4. `site_unusable` (10) and `no_openings` (6).** Recheck once; a site that is back or a board that reopened moves
  to the crawl. Repeat each quarter (step G).
- **C5. `bot_protection` (7).** No change. Record the decision once so nobody retries.

## Step D: add new startups

Target: +60 crawled that return jobs from the runner.

- **D1. Comeet first.** Comeet is the largest source (88) and the likeliest home of Israeli startups. Check whether it
  publishes a company directory or sitemap that its `robots.txt` allows. If not, rely on D2 and D3. Done when: the
  finding is recorded here.
- **D2. Candidate sources** (names and domains only):
  - VC portfolio pages and Getro boards: Grove, Team8, Aleph, Pitango, Vertex, Viola, Insight, TLV Partners,
    Cyberstarts, YL Ventures, Glilot. Names only for Getro.
  - Public lists: Start-Up Nation Central finder, IVC, Forbes Israel, Calcalist and Geektime lists, Israel Innovation
    Authority grantee lists, the TASE tech listing.
  - Y Combinator and Techstars Israeli cohorts.
  - User reports (step F).
  - Israeli job boards as a source of employer names: only after the maintainer has checked each site's terms with the
    lawyer. Skipped until then.
- [x] **D3. Candidate script.** A dev tool in `survey.py` or `survey2.py` (typed, with tests, 100% coverage; the network
  calls faked as the other tests do). For each name it skips excluded (`is_excluded`) and already-listed domains, probes
  the career page link scan (`ATS_LINK`) and then the ATS list in `PROBE_ORDER`, counts Israel jobs with `israel_count`,
  and prints a keep or skip table. It needs a `sync_supabase.py --pull` with the service-role key first (see "Respect
  the limits"); never paste the key in a PR or chat. Output goes to the scratchpad. Done when: 100 names run in one
  command.
  Status: built in `survey.py` (`python3 survey.py --candidates FILE [--limit N] [--keep OUT.json]`, `CONTRIBUTING.md`,
  "Trying candidate companies"). It also skips excluded records by domain, checks the board's company name against the
  candidate and marks a Workday or Oracle board `needs-yes` with its runner risk. About 100 names take roughly 10
  minutes (one request a second per host).
- **D4. Accept rule.** Keep a candidate that has Israel jobs on its own board. A candidate with only a career page and
  no feed becomes a browser crawl and counts against the crawl-time budget, so it needs the maintainer's yes. Skip
  everything else and record nothing.
- **D5. Batches.** Up to 25 names per batch: add each as `{name, domain, crawl: true}` on the admin page, run the
  `discover-companies` workflow, review each result for a wrong feed (check the board's company name against the
  record), pin where needed (on the admin page, as in step C, which empties its `status` for a second run). Done when: the target is
  met and each batch's next daily run is reviewed.

## Step E: add large multinationals

Israeli R&D centres of global companies. Target: +40 crawled with `israel_only: true` that return jobs from the runner.

- **E1. Candidate list.** From the Start-Up Nation Central R&D centre list, IVC, Calcalist and Globes lists, and the
  22 `multinational` records already researched. Cover big tech, semiconductors, networking, cloud and security,
  fintech, defence electronics, automotive, and pharma and medtech.
- **E2. Platform map.** Start from the platform list in `TODO.md`, not from scratch. For each candidate, the ATS and
  whether `robots.txt` allows it. Count per platform. Existing adapters: Workday, Oracle HCM, Phenom, Amazon,
  SmartRecruiters (blocked by `robots.txt` for the five current companies; keep skipping).
- **E3. Adapters by count.** An adapter only for a platform with two or more allowed companies. Each needs an Israel
  filter, a per-company cap, and fixtures that include non-Israel jobs. One adapter per PR.
- **E4. Runner test.** Before counting a Workday or Oracle pin, confirm it returns jobs from a daily run (the runner
  block above). A pin that fails from the runner is parked with a `gap` note, not counted.
- **E5. Single companies.** `browser` crawl only inside the crawl-time budget; otherwise park with a `gap` reason.
- **E6. Batches.** As D5 with `israel_only: true`. The job count must match the career page filtered to Israel.

## Step F: feedback loop

- **F1. Make reporting visible.** The "Add a company" flow exists on the coverage gap card (`PLAN.md` step 5.2). Add a
  prompt to the empty and low-result states of the dashboard search ("Missing a company? Tell us"). Edit `templates/`,
  never `docs/`; follow `design-system/README.md`.
- **F2. Triage routine.** Reports arrive by email only and nothing is stored. Weekly: accept, park or decline each, as in
  `CONTRIBUTING.md`, "Reported sites". Count them per month for A4.
- **F3. Visible progress.** A `CHANGELOG.md` line per batch. No change to the About page or any lawyer's file.
- **F4. Admin Add button.** The Add button is live in the Companies area of the admin page; F2 accepts reports there.

## Step G: keep it up

- **G1. Monthly sweep.** Run D3 on the newest names from the D2 sources. One batch a month, with the budgets checked.
- **G2. Quarterly recheck.** Rerun C4 and C5 on the gap records; refresh the dated `gap.note`.
- **G3. Dead-board alert.** Extend the daily coverage-gap log line (`Log the coverage gaps`) to flag a crawled company
  with no open job for 30 days, from `crawl_log`. It is an alert, not a test; a test only covers the query on fixtures.
- **G4. Removal.** Only on request (an `excluded` record, see `CONTRIBUTING.md`, "Removing a company on request") or
  when a company has closed for good.

## Order and size

1. A1 to A4.
2. B1 to B3 (shortest path to more jobs).
3. C1 to C5 and D1 to D3 in parallel (D3 is the enabling tool).
4. D4, D5 and E1 to E6 in batches of 25 or fewer, each followed by a first-run review before the next.
5. F1 and F2 start after the first batch; G from the first month.

Roughly 10 PRs (code and docs: 4 for A, adapters and the D3 tool, F1) and about 5 to 8 batches of table edits (D and
E), plus the unlocks of C and the recurring G. About one batch a week, so about 10 to 12 weeks end to end.

## Risks

- **Crawl time and size.** More records can push the daily job past its timeout or bloat `jobs.json`. Mitigation: the
  three budgets, JSON feeds first.
- **Runner blocks.** A pin that works from a normal network can fail from the runner. Mitigation: E4 and the
  runner-only counting rule.
- **Late baseline.** A company that returns nothing on its first run is baselined later. Mitigation: the first-run
  rule and the first-run review.
- **Dead or empty boards.** Clutter on the coverage card. Mitigation: the accept rule (D4) and G3.
- **Wrong feed.** Slug probing can hit an unrelated company (`air`). Mitigation: compare the board's company name.
- **Legal.** A company may object to being listed; the opt-out stays an `excluded` record in the table. Job board
  terms are checked with the lawyer before D2 uses them.
- **Israel filter errors.** A global company's feed may mix countries. Mitigation: B3 and E6 spot checks, fixtures with
  non-Israel jobs.
