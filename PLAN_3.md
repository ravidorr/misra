# Plan 3: more companies

Step plan to close the coverage gap users reported: too few startups and too few large multinationals. Drafted
2026-11-18; nothing here is built yet. Targets are proposals for the maintainer to confirm.

Conventions are those of `PLAN.md`: a change to a listed Python module bumps `package.json` and `pyproject.toml`
together and adds a `CHANGELOG.md` entry; a new Python module is registered in `RELEASE_TRIGGER_PATHS` (`ci.yml`,
`.husky/pre-push`), `py-modules` and mypy `files`; coverage stays at 100%. A PR that only edits
`data/companies.json` is data-only: no version bump. Each sub-step (A1, B2, ...) is one PR.

## Baseline (2026-11-18)

`data/companies.json`: 347 records.

| Group | Count |
| --- | --- |
| Crawled (`crawl: true`) | 269 |
| Researched only (`crawl: false`) | 78 |
| Crawled with no `ats` | 14 |
| Pinned `israel_only` | 73 |

Researched-only records by `gap.reason`: `no_careers_url` 23, `needs_reader` 20, `no_feed_found` 14,
`site_unusable` 10, `bot_protection` 7, `no_openings` 6. Crawled ATS mix: Comeet 88, Greenhouse 57, browser 32,
Workday 25, Ashby 16, Lever 7, other 40. `segment` is set only on researched records (`deeptech` 36, `multinational`
22, `biz_software` 11, `cyber_infra` 11).

## Rules for the whole plan

- **Public site data only.** Records are public. A candidate is accepted only if its career page is the company's own
  and lists roles in Israel. A company with no Israel role is not added to the crawl.
- **Respect the limits.** `robots.txt` is honoured (`delete_robots_skipped`), bot protection is never worked around,
  `data/excluded_companies.csv` is checked (`is_excluded`) before any candidate is probed, and the politeness tests
  stay green.
- **One list.** No candidate file is committed. Candidate lists live in the scratchpad or the PR description; only
  accepted records reach `data/companies.json`.
- **Adapters need two.** Write an adapter only when two or more companies share the ATS. One company gets a pinned
  `browser` or `comeet_page` record instead.
- **Batches.** At most 25 records per data PR, so a bad record is easy to find and revert. `tests/test_companies_data.py`
  must pass; `discover.py` is the only writer of the discovered fields.
- **Crawl budget.** Check the `daily.yml` job timeout and the per-company cap before each batch. Browser records are
  the expensive ones; prefer a JSON feed.
- **Track in `TODO.md`.** This plan is the design; the open items go to `TODO.md` as in the project rules.

## Step A: measure first

Without a number we cannot tell whether users feel less pain after the work.

- **A1. Coverage report.** Extend the dev tool `survey.py` (no new module) to print: crawled count, companies with at
  least one Israel job today, researched-only count by reason and segment, and crawled records by `ats`. Done when:
  one command prints the baseline table above plus the Israel-job count.
- **A2. Segment on every record.** Add `segment` to the 267 records that lack it. Values: `startup`, `multinational`
  and the existing ones. Rule: `multinational` = headquartered outside Israel with an Israel site; `startup` =
  Israel-founded, under about 500 staff. Done when: `test_companies_data.py` requires `segment` on every record, and A1
  reports per segment. Only the maintainer approves the final classification of borderline names.
- **A3. Targets.** After A1 and A2, fix numeric targets per segment. Proposal: +60 startups crawled, +40
  multinationals crawled, 30 of the 78 researched records unlocked. Done when: targets are written at the top of this
  file.

## Step B: fix what is already listed (fastest gain, no new names)

- **B1. The 14 crawled records with no `ats`.** Bigabid, Deel, Dynamic Yield, Hailo, Imperva, Innoviz, Rapyd,
  SentinelOne, Trax, Varonis, Verbit, Vimeo, Wix, Zesty. For each, run the crawl locally and record whether it
  returns jobs. Fix by pinning (`source: override`, `ats`, `slug`), or move to `crawl: false` with a `gap` reason.
  Done when: none is crawled while returning zero jobs unexplained.
- **B2. "Request failed" companies.** From the last three daily logs and the coverage gap card, list the crawled
  companies that failed (Adobe is on it). Per company, one of: fix the slug or adapter, widen the retry, or park with
  a `gap` reason. Done when: the failing list is empty or each entry has a reason in `TODO.md`.
- **B3. Audit `israel_only`.** For the 73 pinned records, confirm the filter hides only non-Israel jobs (the Workday
  facet, the `oraclehcm` place, the Phenom country). Spot-check 10 against the careers page. Done when: no known
  Israel posting is dropped by the filter; mismatches get an adapter test.

## Step C: unlock the 78 researched records

Work the buckets by cost. Each unlocked company changes `crawl` to `true`, drops its `gap`, and is run through
`discover.py`. Target: 30.

- **C1. `no_careers_url` (23).** Find the real career page by hand from the company site, LinkedIn page or a VC
  portfolio page (not by guessing a slug). Then probe the ATS from the page source (Comeet `company-uid`, Greenhouse
  embed, Ashby, Lever, Workable). Expect most to resolve to Comeet or Greenhouse. Done when: each record has a
  `careers_url` or an updated `gap.note` saying what was tried.
- **C2. `no_feed_found` (14).** Careers page exists, no known feed. Check the page for a JSON endpoint it calls
  (network tab, as with HiBob and Eightfold). Pin with `source: override`. Done when: each is crawled or has a note.
- **C3. `needs_reader` (20).** Group by platform. Write an adapter where two or more share one (the known open
  candidate in `TODO.md` is Dayforce; confirm the count before writing it). The rest use `browser` or
  `comeet_page`. One adapter per PR, with fixtures and 100% coverage, version bump and `CHANGELOG.md`. Done when: no
  platform with two or more companies is left without an adapter.
- **C4. `site_unusable` (10) and `no_openings` (6).** Recheck once: a site that is back, or a board that reopened,
  moves to the crawl. Otherwise update the note and date. Repeat each quarter (step G).
- **C5. `bot_protection` (7).** No change. Keep on the gap card with a link to the career page. Record the decision
  once so nobody retries.

## Step D: add new startups

Two parts: a source list, then a repeatable pipeline. Target: +60 crawled.

- **D1. Candidate sources.** Collect names and domains from:
  - VC portfolio pages and Getro boards: Grove (`careers.grovevc.com`), Team8, Aleph, Pitango, Vertex, Viola, Insight,
    TLV Partners, Cyberstarts, YL Ventures, Glilot. Getro boards list companies with open roles, which also marks the
    active ones.
  - Public lists: Start-Up Nation Central finder, IVC, Forbes Israel, Calcalist and Geektime startup lists, Israel
    Innovation Authority grantee lists, the TASE tech listing.
  - Meetup, Y Combinator and Techstars Israeli cohorts.
  - User reports (step F).
  - Reverse lookup: Israel job boards that name the employer, used as names only, never as a data source.
- **D2. Candidate pipeline.** A dev script (inside `survey.py` or `survey2.py`; no new module) that for each name:
  1. skips excluded and already-listed domains;
  2. derives slugs with `slug_candidates` and probes the ATS list in `PROBE_ORDER`, plus Comeet through the careers
     page link scan (`ATS_LINK`);
  3. fetches the board and counts Israel jobs with `israel_count`;
  4. prints a keep/skip table: name, domain, ATS, slug, total jobs, Israel jobs.
  Output goes to the scratchpad. Done when: 100 names run in one command and the table is reproducible.
- **D3. Accept rule.** Keep a candidate if it has at least one Israel job on its own board, or a career page that
  lists Israel roles. Skip otherwise and record nothing. Done when: each batch PR description lists kept and skipped
  counts.
- **D4. Batches.** Four to six PRs of up to 25 names: add `{name, domain, crawl: true, segment: "startup"}` in name
  order, run `discover.py`, review diffs for wrong feeds, pin where needed, run `tests/test_companies_data.py`.
  Done when: target met, each batch's first daily run is clean (no new failing entries).
- **D5. Comeet enumeration.** Comeet is the largest source (88) and the likeliest home of Israeli startups. Check
  whether Comeet exposes a public company directory or sitemap that is allowed by its `robots.txt`; if not, rely on
  D1 and D2. Do not scrape what `robots.txt` disallows. Done when: the finding is recorded here.

## Step E: add large multinationals

Israeli R&D centres of global companies. Target: +40 crawled with `israel_only: true`.

- **E1. Candidate list.** Sources: Start-Up Nation Central R&D centre list, IVC multinational list, "Israeli R&D centre"
  lists in Calcalist and Globes, the `multinational` records already researched (22). Cover at least: big tech,
  semiconductors, networking, cloud and security, fintech and payments, defence electronics, automotive and mobility,
  and pharma and medtech.
- **E2. Platform map.** For each, find the ATS (Workday, Oracle HCM, Phenom, Eightfold, iCIMS, SuccessFactors,
  Taleo, SmartRecruiters, own site). Count companies per platform. Existing adapters cover Workday, Oracle HCM, Phenom,
  Amazon and SmartRecruiters (the last blocked by `robots.txt` for the five current companies; keep skipping them).
  Done when: a platform-by-count table is in the PR.
- **E3. Adapters by count.** Write an adapter for every platform with two or more companies (likely Eightfold,
  iCIMS or SuccessFactors; confirm by count). Each needs an Israel filter (country facet or location query) and a
  per-company cap. Tests use committed fixtures. One adapter per PR.
- **E4. Single companies.** Pin with the `browser` crawler only if the daily time allows; otherwise park with a `gap`
  reason so the coverage card shows it and links the career page.
- **E5. Batches.** As D4, with `segment: "multinational"` and `israel_only: true`. Check the filter on each: the
  job count must match the career page filtered to Israel.

## Step F: feedback loop

- **F1. Make reporting visible.** The "Add a company" flow exists on the coverage gap card (PLAN.md 5.2). Add a short
  prompt to the dashboard empty and low-result states ("Missing a company? Tell us"). Done when: the link is reachable
  from search with no results. Edits are in `templates/`, never `docs/`; follow `design-system/README.md`.
- **F2. Triage routine.** Reports arrive by email only; nothing is stored. Weekly: accept, park or decline each, as in
  `CONTRIBUTING.md` "Reported sites". Decline reasons stay out of any file.
- **F3. Visible progress.** After each batch, add a line to `CHANGELOG.md` and the About page text only if the
  lawyer's files are not touched (`ABOUT.md` is source; confirm first). A count of crawled companies on the
  dashboard footer is optional and needs no personal data.
- **F4. Admin Add button.** PLAN_2 already specifies an Add button in the Companies editor. When that lands, the
  triage in F2 moves there; no change to this plan until then.

## Step G: keep it up

- **G1. Monthly sweep.** Run D2 on the newest names from the D1 sources. One data PR per month.
- **G2. Quarterly recheck.** Rerun C4 and C5 on the gap records; refresh `gap.note` dates.
- **G3. Regression guard.** A test that fails when crawled companies with zero jobs for 30 days exceed a threshold
  (read from `crawl_log`), so the list does not rot. Done when: it runs in CI against fixtures.
- **G4. Cleanup.** Remove a company only on request (`excluded_companies.csv`) or when it is closed for good.

## Order and size

1. A1, A2, A3 (one week).
2. B1 to B3 (one week; shortest path to more jobs).
3. C1 to C5 in parallel with D2 (adapters take the longest; D2 is the enabling tool).
4. D3 to D5 and E1 to E5 in batches, one data PR a week.
5. F1 and F2 start after the first batch so users see a result; G from the first month.

Roughly 25 PRs: 3 for A, 3 for B, 5 for C, 6 for D, 6 for E, 2 for F, plus recurring G.

## Risks

- **Crawl time.** More browser records can push the daily job over its timeout. Mitigation: budget check per batch,
  prefer JSON feeds, and cap per-company pages.
- **Dead boards.** A startup board with zero jobs clutters the coverage card. Mitigation: the accept rule (D3) and the
  G3 guard.
- **Wrong feed.** Slug probing can hit an unrelated company (`air` on Greenhouse is the known case). Mitigation: check
  the board's company name against the record before accepting.
- **Legal.** A company may object to being listed. The opt-out stays in `excluded_companies.csv` with its guards; a
  request is honoured within the policy in `PRIVACY.md`.
- **Israel filter errors.** A global company's feed may mix countries. Mitigation: B3 and E5 spot checks, adapter tests
  with fixtures that contain non-Israel jobs.
