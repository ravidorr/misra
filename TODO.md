# TODO

Open work, as of 2026-11-20. Each item says what triggers it, how to do it and when it is done. When one is finished,
delete it and update the counts in the others: the researched count and the segment, reason and no-careers-URL counts in the research item (recounted on
2026-11-19, see its status note; a pin removes one record from one segment and one reason).
An item with a screenshot in `todo-images/` goes with it: delete the image file in the same PR.
Observations about live sites below come from checks made on 2026-11-14 and may have changed.
This file replaced `PLAN.md`, `PLAN_2.md` and `PLAN_3.md` on 2026-11-20; they are in the git history.

## Dates

An item that has a date says `Due <date>` in its text. The dates were set on 2026-11-20 and accepted by the maintainer.

| Due | What | Item |
| --- | --- | --- |
| 2026-11-21 | Review the 04:23 UTC daily run: the 24 Goozali companies, REE Automotive, Priority Software | "Check the daily run of 2026-11-21" |
| 2026-11-27, then every Friday | Weekly triage of reported sites and the coverage card | "More companies" |
| 2026-12-04, then each quarter | Recheck `site_unusable`, `no_openings` and the `bot_protection` decision | "More companies" |
| 2026-12-16 | Size of `crawl_log` and `jobs`, and of the description text (30 days after the move) | "Measure the size" |
| 2026-12-20, then monthly | Monthly sweep of new company names, success metrics of the month | "More companies" |
| 2027-02-20, then each quarter | Diff the Goozali directory again | "Close the Goozali gap" |
| 2027-10-20 | Node 27 becomes LTS: review the pins (Python 3.12 ends in October 2028) | "Review the Node and Python pins" |
| 2027-11-15 | Yearly security review, with the cron-job.org token renewal | "Yearly security review" |
| 2028-03-10 | The `2022-11-28` GitHub API version ends: move the cron-job.org header before then | "Yearly security review" |

## Dashboard

- [ ] **A list of the roles the user hid, with a way to unhide each.** Trigger: the filters have "Show discarded (N)"
  (`templates/dashboard.html`, `showGone`). Ticking it shows every discarded role (a hidden company counts as discarded
  roles) and each card has a "Restore" button (`discardControl` in `templates/dashboard.js`). Nothing lists the hidden roles
  together, and a discarded role whose posting closed disappears. Where: first a UI/UX plan (Claude Design, the UI/UX expert
  and the advisor write it before any code, as for the Share button, PR 238), then the list, signed in and out, from the same
  data as today. No new data is stored, so no `PRIVACY.md` change; if a plan needs new data, the lawyer comes first. Signed
  out, a discard keeps only the job key and a date, so a closed role can show a title only signed in unless the plan says
  otherwise. Design this
  together with the next item. Done when: a user can see all hidden roles in one place and unhide one from there; the tests,
  the design-system rules (tokens, WCAG AA, 44px targets) and the WebMCP tools stay consistent.

- [ ] **A list of the companies the user hid, with an Unhide button.** Trigger: "Company not for me" hides every role of the
  company (account: a `user_jobs` row with state `company`; signed out: local storage `il-jobs-discarded-companies-v1`). The
  only way back is to tick "Show discarded" and press "Restore" on any role of that company, which brings the whole company
  back. A company with no open roles has no card, so it cannot be unhidden, and nothing lists which companies are hidden.
  Where: the same UI/UX plan step as the previous item (one design), then a "Hidden companies" list with "Unhide", signed in
  and out. No new data. Done when: a user sees every hidden company and unhides one without finding one of its roles; a hidden
  company with no open roles is listed too.

- [ ] **Make the new-tab icon smaller.** Trigger: the icon after every `a[target="_blank"]` link (PR 240,
  `design-system/components/external-link.css`, mask `design-system/assets/new-tab.svg`) is `0.9em` square, so it is as tall
  as the job title's cap height or more (title `1.5rem`, `1.25rem` on a phone) and heavy inside the Apply button. Where: that
  one rule only, after a UI/UX read (Claude Design and the UI/UX expert checked the CSS, the tokens and the 2.5 stroke of the
  mask): a decorative cue should sit below the x-height of the text it follows, so the base size is `0.75em` (12px in 16px
  text and in the button) with `margin-inline-start: 0.3em` and `vertical-align: -0.05em`, and the card title link gets
  `0.6em` (about 14px at `1.5rem`, 12px at `1.25rem`) through one `.card-title a::after` override, because `em` scales with
  the large title and would stay too big; the Apply button needs no override. The icon stays a visual cue, so the 44px
  targets (`.btn` `min-height: var(--tap)`, the title link padding) and the "(opens in a new tab)" words are untouched.
  Keep it one token, `--icon-new-tab-size` in `tokens.css`, if a second value is needed. Done when: the icon is `0.75em` in
  link lines and the Apply button and `0.6em` in the job title, still readable (3:1 against its background) in light, dark and
  forced-colors mode, checked on the dashboard at desktop and phone width; the only code touched is that rule, the README
  line and the tests or snapshots that pin the size (`tests/test_new_tab_icon.py`).

## Descriptions

- [ ] **Text for the browser companies that give none.** Trigger: Check Point (231 jobs, bot protection), monday.com (128),
  Tower (75), Priority and a dozen small career pages draw the posting with JavaScript, so a plain request has no text and
  the browser crawl has no standard field (CONTRIBUTING.md, "Job descriptions", has the list). Where: per company, a JSON
  endpoint the page itself calls (as HiBob and Eightfold), or the company's ATS if it has a public one. Done when: a company on
  that list gives a text without scraping page text and without working around bot protection. Do not retry a plain request.

- [ ] **Descriptions for the adapters not read yet (FR-4).** Trigger: the description slices cover Greenhouse, Lever,
  Ashby, Workable, Comeet and Amazon (70 percent of open jobs at the first slice), Workday (a request per job, 40 a company
  a run), HiBob, Eightfold and four JSON-LD career pages. Left: Phenom (89 jobs, a teaser only), Oracle HCM, `comeet_page`,
  JazzHR and BambooHR (a request per job), and SmartRecruiters (skipped by `robots.txt`). The full text lives in Supabase
  only, never in `data/jobs.db` or a committed file. Where: one adapter at a time, with the same per-company cap as Workday.
  Done when: FR-4 acceptance criteria 1, 2, 3 and 9 hold for each of them and the share with a full description is printed in
  the daily log.

## Assistant endpoint

- [ ] **Watch `rpc/search_job_keys` for abnormal query volume.** Standing note, not a task. The lawyer agreed on 2026-11-17
  that there is no per-caller limit and no `PRIVACY.md` change (reasons and what a limit would need: `supabase/README.md`,
  "Why `search_job_keys` has no per-caller limit"). Trigger: abnormal query volume on `rpc/search_job_keys` in the database
  or API logs. Then build the limit as that section describes.

## Yearly security review

- [ ] **Do the yearly security review by 2027-11-15, then every year.** Due 2027-11-15. Trigger: the date. Where: the
  security note in `SECURITY.md` (it lists the checks: provider logs, who has access, two-factor sign-in, key
  rotation, Dependabot and CodeQL alerts, and re-reading `PRIVACY.md` and `TERMS.md` against the product).
  - Also each year: renew the GitHub token that cron-job.org uses to start the daily run (about a year after it was
    created; delete the old token in your GitHub settings under personal access tokens), and check that the
    `X-GitHub-Api-Version` header in the cron-job.org job is still supported (`2022-11-28` is deprecated and ends on
    2028-03-10; move it to the newest version before then and test the job once; do it at the 2027-11-15 review, at the latest by 2028-02-10).
  - Done when: the checks are done and the date and result are written in the note, and this item is moved to the
    next year.

## Review the Node and Python pins

- [ ] Trigger: Node 27 becomes LTS (scheduled 2027-10-20) or Python 3.12 nears its end of life (October 2028), whichever
  comes first. Due 2027-10-20 (Node), or by 2028-10-01 (Python). Nothing to do before then.
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
      `markdown-it-py` is pinned the same way, in `pyproject.toml`, `daily.yml` and `publish-docs.yml` (a test keeps
      the three equal).
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

- [ ] The `companies` table holds researched companies with no usable job feed (the records with a `gap`; 85 on
  2026-11-19, in four segments: deeptech, multinational, biz_software, cyber_infra, and some with none). Each needs its own research; there is no batch fix
  left. Every observation about a live site below is from the checks of 2026-11-14, a crawl of the rows that had a
  careers URL that day and a scan of their rendered pages. The remaining work falls into these groups:
  - **Status of 2026-11-19 (PLAN_3).** The table holds 413 rows: 328 crawled, 85 researched only (with a `gap`),
    0 excluded. Since 2026-11-18 the table grew by 66 rows (some batch companies were researched rows that were
    unlocked) and the Avature, Eightfold and Jibe adapters, the browser reader fixes and the Load more wait were built
    ("More companies", Status). The 85 researched records, counted on 2026-11-19 from the live table: by
    segment, deeptech 33, multinational 19, biz_software 11, cyber_infra 10 and no segment 12; by gap reason,
    `no_careers_url` 22, `no_feed_found` 20, `needs_reader` 16, `bot_protection` 10, `site_unusable` 9 and
    `no_openings` 8. 24 of the 85 have no `careers_url`. Gilat, Landa, Synopsys,
    Siemens, Apple, Micron, Boston Scientific, Lam Research, Rapyd, env0, Kornit Digital, Zesty, PepsiCo, Schneider
    Electric and ICE are crawled now; the notes below that call one of them "not done" describe 2026-11-14 to 2026-11-16
    and are kept as history. Walnut and Pillar Security are not pinned: both are researched records with the reason
    `needs_reader`; Pillar was dropped from the reader work and needs a dedicated reader.
  - **No careers URL.** A list of 2026-11-14, kept as a snapshot (21 names, neither Gilat nor Landa among them; the table
    now has 24 researched records with no `careers_url`, and the coverage gap card and the table say which). They had no `careers_url` in the `companies` table: Upsolver, Speedata,
    Teramount, X-Globe, Q Factor, Siklu, Silicom, Sight Diagnostics, Brainsway, EarlySign, Diagnostic Robotics,
    Tevel Aerobotics, SolarEdge, Watergen, AIR, SAP, eBay,
    Akamai, Ford, Ericsson, Keysight. Find the real
    career page first, then continue with the groups below. The large multinationals will need a location-filtered
    address or their ATS (see the last group).
  - **A job board was found but there is no adapter.** Playstudios and Booking.com. Playstudios was listed as a Jobvite
    board, but on 2026-11-14 `jobs.jobvite.com/playstudios-inc` is an invalid board and the career page links to
    Dayforce (`jobs.dayforcehcm.com/en-US/playstudios/CANDIDATEPORTAL`). `browser_crawl.crawl_many` reads its 12 jobs with
    real titles and distinct links but an empty location for every one, so none could be recognised as Israel (the page
    shows 5 in Tel Aviv-Yafo), so it is not a browser pin. The portal has a JSON search (`POST
    /api/geo/playstudios/jobposting/search`, locations as `formattedAddress`); a Dayforce adapter would read it, but no
    other company in the lists is on Dayforce, so write one only when a second appears. Booking.com (iCIMS) is skipped: its iCIMS board answered
    403 to a plain request on 2026-11-14 (bot protection, not to be bypassed) and `careers.booking.com` is a custom site
    with no job feed; revisit it with the large multinationals. A new adapter is its own PR: a function in `adapters.py`,
    an entry in `ADAPTERS`, tests with the fakes in `tests/test_adapters.py`, a version bump and a changelog entry.
  - **A board was found but its address is wrong or dead.**
    - Pliops: on 2026-11-14 the Comeet page for `pliops/05.004` said "This Spark Hire Recruit account has been
      deactivated", and it still did on 2026-11-16, but `pliops.com/careers` still links to that page, so the company has published no
      new board. A Workable account `pliops` exists (`adapters.workable("pliops")` answers with no job) but nothing links
      to it, so it is not pinned: pin it, or the board the career page ends up linking to, once it lists a job.
    - Western Digital: the SmartRecruiters board (`WesternDigital`, 325 jobs) has no Israel job today, so there is
      nothing to show. It can be pinned with `israel_only` now that the matcher and the new-company rule are fixed, but it
      would fetch 325 jobs a day to store none.
    - Cloudflare: the Greenhouse board's locations are "Hybrid" or "Distributed", never a city, so an Israel job could
      not be recognised. The adapter would need to read the offices (`?content=true`) first.
  - **The page returned links, not jobs.** Checked on 2026-11-14: Ex Libris links to `careers.clarivate.com`, a
    worldwide Phenom board of its parent Clarivate (not Ex Libris only); Ormat is pinned on 2026-11-17 (see below); Optibus is a Comeet plugin
    page, done on 2026-11-16 below; REE Automotive is in the next group.
  - **Large multinational boards that show a worldwide first page.** Google, Intuit, IBM
    and others returned jobs, but almost none in Israel (Apple's page had one), because the page is a worldwide search.
    They need a career address filtered to Israel (as `citi.eightfold.ai/careers?location=Israel` is) with `israel_only`,
    or their ATS. Check that the filter works before pinning.
    Findings on the 24 multinationals left after the pins of 2026-11-14 (so the next person does not repeat them):
    - Workday guess that still does not answer: Akamai (`akamai/wd1/akamaicareers`) answers 500. The tenant, data
      centre or site name is still unknown: find it from the company's own career page, not by guessing. Intel
      (`intel/wd1/External`) and Autodesk (`autodesk/wd1/Ext`) were pinned on 2026-11-17 from the Workday addresses on
      their career pages.
    - Bot protection (403 to a plain request, 2026-11-14; not to be bypassed): Akamai, General Motors (`search-careers.gm.com`), Ford, SAP, Booking.com.
      Uber is the same (rechecked on 2026-11-19: `jobs.uber.com` answers a Cloudflare challenge, 403, although its
      `robots.txt` allows everything; its coverage link points to `https://jobs.uber.com/`).
    - Other platforms with no adapter yet: AMD and Keysight (iCIMS: `careers-amd.icims.com`,
      `careers-keysight.icims.com`), Intuit (Radancy: the search takes a
      location only with coordinates and shows "Multiple Locations" for many jobs), Google (the results page embeds
      its data; the crawler read only navigation links on `?location=Israel`; on 2026-11-19 `google.com/robots.txt`
      disallows the results pagination `?page=` and page 1 shows 20 of 65 Israel rows, so it stays a gap until there is
      an allowed feed or written permission), Apple (read by the browser crawl since the reader fix of 2026-11-19), IBM and Meta (no feed found).
    - Ericsson: `jobs.ericsson.com/careers?location=Israel` is an Eightfold board that shows "0 jobs" in Israel today,
      and the crawler reads its "Join Talent Network" link as a job, so it is not pinned; pin it once it lists one.
    - eBay (`careers.ebayinc.com`), Walmart Global Tech (`careers.walmart.com`, a timeout), Siemens
      (`jobs.siemens.com`, no DNS answer; crawled since 2026-11-19 through the Avature adapter), Align Technology (`jobs.aligntech.com`) and Rapid7
      (`careers.rapid7.com/jobs/search`): no feed found in the page HTML; Align and Rapid7 were looked at again on
      2026-11-16 (third batch, below).
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
    name in `url_comeet_hosted_page` (any name works for a valid uid; a wrong uid gives no data). Done: Retym
    (`retym/C6.003`), Qedma (`qedma/7A.006`), Qodo (`qodo/B8.00B`), Trigo (`trigo/A6.005`), Cyolo (`cyolo/F5.005`; its page
    shows a `company-uid` in the job page source, not the career page), Allot (`allot/C4.009`, 24 jobs, one in Israel;
    the uid is also in a vacancy page), and three boards with no job today, pinned with `israel_only` so the first Israel job
    is reported as new: Foretellix (`Foretellix/84.007`), XM Cyber (`xmcyber/15.005`) and Dataloop (`dataloop/B5.00A`); Sunbit (`sunbit/37.001`, 40 jobs, 8 in Israel; the uid is in the
    source of a `?job=` page) and Firefly (`firefly/39.000`, `israel_only`: the board holds one "Future Opportunities" entry
    with no place), and DarioHealth (`Dario.Health/34.006`, 2 jobs, both in India today), and OX Security (`ox_security/F8.006`, 36 jobs,
    7 in Israel; the uid is in the career page source, the board name `ox_security` in the careers-api answer),
    and, on 2026-11-16, four Comeet plugin pages whose account answers 400, with the new `comeet_page` adapter (the slug
    is the page address; it reads the plugin's links `/co/<place>/<uid>/<title>` from the page HTML), all `israel_only`:
    REE Automotive (`https://ree.auto/careers/`, 2 jobs, 1 in Israel), Gauzy (`https://www.gauzy.com/careers/`, 9 jobs, none
    in Israel), Guesty (`https://www.guesty.com/careers/`, 35 jobs, 8 in Israel) and Apester (`https://apester.com/careers/`:
    "We don't have any open positions" today, so the first Israel job is reported as new).
    On 2026-11-16, Ceva (`ceva/76.005`, found in the job-page source of its own `/career/` site; the page names the
    account SNC but the board answered under the name `ceva`, not `SNC`; 25 jobs, 14 in Israel, `israel_only`).
    Converge Bio (`convergebio/CA.00D`: the real domain is `converge-bio.com`, not `convergebio.com`; its `/careers`
    page is a Framer page that loads the Comeet widget, with `company-uid` `CA.00D` in the source; the board answers
    under the name `convergebio` and lists no job today, `israel_only` so the first Israel job is reported as new).
    Optibus (`https://optibus.com/company/careers/jobs/` with `comeet_page`, `israel_only`: the careers page only links
    to this plugin page, which lists 6 jobs, 1 in Tel Aviv).
    Done on 2026-11-19 (PLAN_3 batches 1 and 2; reader fixes of 0.75.0, `CHANGELOG.md`): Kornit Digital (`browser` pin
    on `https://careers.kornit.com/all-positions/`, `israel_only`: its own theme, links `/cmcareer/<id>/`, is handled by
    the browser reader, which also reads a heading in the link as the title), env0 (`browser` pin on
    `https://www.envzero.com/careers`: the cards link through button labels, which the reader now reads), Zesty
    (`comeet_page` on `https://zesty.co/careers/`: the title is outside the link, which `comeet_page` now reads from the
    list item), Landa and Gilat (Comeet), Apple (`browser` pin, `israel_only`: the reader matches `/details/<id>/<slug>`
    and presses "Next Page") and Rapyd (`browser` pin, `israel_only`: 7 Israel jobs with their titles). Kornit, env0,
    Zesty, Apple and Rapyd are in the 2026-11-19 PLAN_3 batch 2 (all `ok` with jobs on the manual run); the Comeet
    slugs of Landa and Gilat are in the table, not here.
    Nano Dimension (`nanodimension/62.003`: the `comeet_uid` is in the source of `nano-di.com/job-openings`, a Comeet
    plugin page the `comeet_page` adapter cannot read; the board holds 4 jobs, none in Israel, `israel_only`).
    Not done, and why (checked on 2026-11-14, or 2026-11-16 where dated; do not repeat):
    - Comeet plugin pages that the `comeet_page` adapter does not read (checked on 2026-11-16): SQream
      (`sqream.com/careers/` lists 1 job, "Controller", whose card names no place, and its `/co/` link holds the
      department, so the Israel filter would drop it; pin it when its cards show a place). Kornit, in this
      group on 2026-11-16 (its own theme and `/cmcareer/<id>/` links, not the plugin's), is done: see above.
    - Comeet account valid but not the company's own board: Magic Software (19.00F is Matrix IFS, 23 jobs, none in
      Israel). NeuReality (89.002) answers no job through the `comeet` adapter (its hosted page is not served), but its
      career page is a Comeet plugin page that the `comeet_page` adapter reads: pinned on 2026-11-16, `israel_only`,
      "We don't have any open positions" today, so the first Israel job is reported as new.
    - No openings at all, no feed to pin: Applitools ("No openings
      available"), H2Pro and Madgicx (no open positions; Madgicx takes CVs through a ClickUp form).
    - Own-site pages the crawler's link pattern does not match, no ATS behind them: Pillar Security
      (`/open-position/<slug>`, 1 Tel Aviv job; still `needs_reader`, it needs a dedicated reader because its city is a
      section heading above the cards), Walnut (`/jobs/<slug>`; still a researched record, `needs_reader`), Deep Instinct (PDF job
      descriptions, none in Israel), Firefly's own `/positions/` pages (Firefly itself is pinned above), Syte (job list
      by script, no job link), Tyto Care and StoreDot (list or CV form with no job link), Swimm (an email address).
    - Checked on 2026-11-16 (first batch, do not repeat): Stratasys is SuccessFactors (pinned on 2026-11-17, see below); Speedata's jobs are on Viola Group's Getro board and its own page is a Wix site with no feed; Siklu is
      now part of Ceragon, whose Comeet board is Ceragon's; CytoReason's old plugin page answers 404; Diagnostic
      Robotics' list shows "No items found"; Guesty's page is a Next.js app that shows no uid even on a job page. Guessing
      the Greenhouse, Lever, Ashby and Workable slugs of the 21 companies without a careers URL found only an unrelated
      company (`air` on Greenhouse). Western Digital's board is on SmartRecruiters, which robots.txt skips.
    - **Known limitation, checked on 2026-11-17: pinned Workday boards that fail only from GitHub's servers.** In the
      builds of 2026-11-15 to 2026-11-17 these companies show "request failed" on the coverage gap card: Adobe,
      Broadcom, Cadence, Criteo, CrowdStrike, DoubleVerify, Edwards Lifesciences, Integral Ad Science, Kyndryl,
      Medtronic, Motorola Solutions, Palo Alto Networks, Philips, Proofpoint, Salesforce, Samsung, Visa and Workday
      (KLA, Marvell and Zoom joined later, and Check Point is blocked the same way as a browser crawl). Tested from a
      normal network on 2026-11-17, one request each at our pace and with our User-Agent: Salesforce (17 Israeli
      jobs) and Broadcom (1) answer with jobs, Adobe, Visa, Criteo and Zoom answer with an empty list (read as no
      Israeli job there, not proven), none failed. The other tenants in the list were not tested. So the tenants
      probably refuse the runner's address range; the runner itself has not been tried again. It is not worked
      around (no proxy, no other address, no changed User-Agent): the companies stay on the card, which says its
      roles "may be missing or old", until a tenant lets the runner in or the crawl moves to a network it accepts.
      Their old jobs stay listed until a good crawl or the failure rules close them. The Workday pins of 2026-11-17
      (Intel, Autodesk, Ex Libris, Unity) may hit the same block on the runner.
      Rechecked on 2026-11-18 (PLAN_3 B2): all 25 pinned Workday tenants were in the failing list of the last two days
      (runs of 2026-11-17 04:23 UTC to 2026-11-18 16:28 UTC; Motorola Solutions, Ex Libris, Proofpoint, Samsung and
      CrowdStrike answered on some runs), and every one of the 25 answered from a normal network the same day with a
      list (6 of them an empty one: Adobe, Criteo, Integral Ad Science, Proofpoint, Visa, Zoom), so they are all
      runner-blocked, none a wrong slug. Not worked around, no change made. The five Oracle HCM tenants did not fail
      in those runs. Next look: when a tenant starts answering from the runner, or when the crawl can move.
    - Checked on 2026-11-17 (third batch, do not repeat): Ormat and Stratasys are SuccessFactors career sites whose
      `/search/?q=&locationsearch=Israel` page the generic browser crawl reads without an adapter (3 and 18 Israeli
      jobs, the same on two runs); robots.txt of `careers.ormat.com` and `careers.stratasys.com` only blocks
      `/applybutton/`, `/talentcommunity/`, `/services/` and similar, so both are pinned with `israel_only`. Jobvite
      (`jobs.jobvite.com`) has no robots.txt, but Varonis's public board `/varonis/jobs` shows no job to the crawl (the
      `varonis-internal` board did: it is not the public one), and Playstudios' Jobvite board has no job location; no
      adapter. Pinpoint (`jobs.aligntech.com`: robots.txt allows the pages and `jobs.rss`) serves Align Technology
      alone. SAP's SuccessFactors host answers 403 to a plain request: not read. Redefine Meat's Comeet board
      (`redefinemeat/28.004`) lists 1 job, in the Netherlands.
    - Checked on 2026-11-16 (second batch, do not repeat): no adapter for iCIMS (AMD, Keysight: robots.txt of
      `careers-amd.icims.com` and `careers-keysight.icims.com` is `Disallow: /`) or Avature (Synopsys alone: the
      robots.txt of `generalmotors.avature.net` disallows `/careers`) or SuccessFactors (Stratasys alone) or Pinpoint
      (Align Technology alone). Cloudflare's Greenhouse offices (`?content=true`) list no Israel or Tel Aviv office, so
      reading them would find no job. Pliops and Ericsson are unchanged. The homepages of Gilat (307 without a
      redirect target), Silicom (redirects to `silicom-usa.com`), Sight Diagnostics, Tevel, Watergen, AIR and X-Globe
      show no careers link and `/careers`, `/career`, `/jobs` answer 404; Upsolver answers 403 (bot protection);
      Brainsway's careers page is its LinkedIn page; Teramount, Q Factor and EarlySign do not answer (Converge
      Bio's real domain is `converge-bio.com`: done above).
      A scan of the careers pages and their job links for ATS markers found only Nano Dimension (done above).
    - Checked on 2026-11-16 (third batch, do not repeat): Converge Bio, Teramount, Q Factor and EarlySign were looked
      up under their real names. Converge Bio's domain is `converge-bio.com` (pinned above); Teramount's own domain
      does not answer and its jobs are on Grove Ventures' Getro board (`careers.grovevc.com/companies/teramount-2`, a
      venture fund's board, no adapter); the real domain of Q Factor and EarlySign could not be found (`q-factor.io`
      is another site). Slug guesses (Greenhouse, Lever, Ashby, Workable, three candidates each) for the other ~50 rows
      with a name and no known ATS found only an unrelated `air` account. Extra careers paths (`/company/careers`,
      `/join-us`, `/about/careers`, `/positions` and others) on X-Globe, Tevel, Sight, Watergen, AIR, Brainsway,
      Diagnostic Robotics, CytoReason and Gilat answered nothing. Rapid7's `careers.rapid7.com/jobs/search` is its own
      Rails and Turbo platform (`/jobs/<slug>-<uuid>`, server-drawn pages of 16): `?location=Israel` is ignored, so
      it returns a worldwide list and would need its own adapter. Align Technology's Pinpoint board
      has a `jobs.rss` feed, but Pinpoint is used by Align alone. The careers pages of the remaining own-site rows
      show no Comeet, Greenhouse, Lever, Ashby, Workable, BambooHR, Recruitee, Teamtailor or Personio marker besides
      those already listed.
    - Checked on 2026-11-16 (fourth batch, do not repeat): Getro boards (Grove Ventures, Viola Group; TLV Partners'
      board is `jobs.tlv.partners`) are not readable. The board pages (`careers.grovevc.com`) allow crawling in their
      robots.txt but hold no job list: the list is drawn by script from `POST
      api.getro.com/api/v2/collections/<network id>/search/jobs`, and the robots.txt of `api.getro.com` is `Disallow: /`
      for every agent, so no adapter was written. The boards' sitemaps list company pages and job pages with a
      JSON-LD `JobPosting`, but the list is incomplete (Teramount's one job is not in Grove's sitemap), so it is not a
      feed. Getro's own job link is the employer's page when it has one (`teramount.com/careers/?job=1`). Decided
      with the lawyer on 2026-11-19: leave Getro and similar talent boards out unless there is written permission, and
      find portfolio companies by hand and add their own career pages ("More companies", Decisions on sources). Q Factor's real domain is `q-factor.com` (a one-page site, a
      neutral-atom quantum company that emerged from stealth in April 2026; its only job route is a `careers@` email
      address, so nothing to pin); the lists now carry the right domain. Teramount was acquired by Molex (completed
      on 2026-05-07; `teramount.com` redirects to a Molex product page), so its jobs would be Molex's worldwide board.
      EarlySign (Medial EarlySign): `earlysign.com` has no DNS answer, `earlysign.ai` is a parked page, and a Start-Up
      Nation Central listing (403 to us, seen only in a search summary) says it ceased operations in October 2025; not
      confirmed by the company.
    - Dead or unusable: Jelly Button Games, Kahun, Nucleai and Quantum Art (domain parked at GoDaddy), Satixfy (now an
      MDA Space page), Line 5 (needs a login), lakeFS and Quantum Art (403 or "Access Denied": bot protection), Radware
      and Camtek (an empty page), Elbit Systems (timeout on the news page; its career site `elbitsystemscareer.com` is a
      Hebrew custom site with no feed seen), ReWalk (its list on `golifeward.com/company/careers/` is US jobs, no Israel
      job), Lumigo, Aniview, AnyClip, Pay-Me, Evogene, Aleph Farms, Remilk, Redefine Meat, Tabit,
      Cognata: no ATS link, feed or job link in the HTML or the network
      requests; not investigated further.
  - **Bot protection.** Some pages answered 403, "Access Denied" or a browser check. Leave them and record it. Do not try
    to get around bot protection: a few pinned companies (Check Point, Papaya Global, Unity) are already blocked from the CI
    runner this way and show up as gaps on the dashboard and in the job log.
  - How to research a company:
    1. Find the real career page, then look for the job board it uses: an embedded or linked Greenhouse, Lever, Ashby,
       Workable, SmartRecruiters, Comeet or Workday board (`discover.ATS_LINK` has the patterns, and the page's HTML,
       links and iframes all need a look, since scripts hide them) or a JSON feed in its network requests.
       A Workable job link on a page is `apply.workable.com/j/<shortcode>`: it names no account (open it: it redirects
       to `apply.workable.com/<account>/j/<shortcode>`). `ATS_LINK` skips these links since 0.10.1.
    2. With an existing adapter, probe it from a shell (`adapters.ADAPTERS["greenhouse"]("slug")` and so on). Without
       one, the browser crawler is the fallback: `browser_crawl.crawl_many([url])` (set `PW_CHROMIUM` to a Chromium
       build, or run `playwright install chromium`) shows what it reads.
    3. Read the result before pinning. It needs real titles, distinct links and a location per job that the Israel matcher
       (`discover.ISRAEL`) can recognise, for the jobs that will appear and not only the ones there today. A list of
       navigation links, a worldwide first page or a partial list is not a feed.
    4. Pin with one edit of the company's row in the `companies` table (Supabase Table Editor; one company at a time, so a
       bad pin is easy to undo): set `"crawl": true`, `"ats"`, `"slug"`, `"source": "override"` (that is what pins it: `discover.py`
       keeps the `ats` and `slug` of a pinned record and only checks them), and, for a large
       multinational, `"israel_only": true`; remove the `gap` (and the `segment`), and empty its `status` (the workflow
       discovers only records with no `status`). Then run the `discover-companies` workflow: it checks the pin and sets
       the `status` (`run.py` crawls only `status` `ok`) and leaves every other record as it is.
       A board with no Israel job today is fine to pin: the `israel_only` rule reports the first Israel job it ever
       lists. Code changes (an adapter, a parser fix) need a version bump and a changelog entry.
       Probe a Workday slug with `.venv/bin/python -c "import adapters; print(adapters.workday('visa/wd5/Visa'))"`:
       `None` is a host that did not answer with a job search (Workday tenants go to a maintenance page for hours at a
       time, and answer again later: probe on more than one day before giving up), `[]` is a board with no Israel job.
    5. If a company cannot be done, say why. Set the `reason` (one of the keys of `build_dashboard.REASONS`) and a short `note` in
       the `gap` of its row: the coverage gap card reads them. The counts quoted above are a snapshot of 2026-11-14 and
       nothing checks them any more; refresh them when you touch this item.
  - Tools: `browser_crawl.crawl_many` over every row with a careers URL, and a scan of the rendered pages for ATS links,
    were the two passes behind this list; both were throwaway scripts and are not in the repo. Adding them as a dev tool
    next to `survey.py` and `survey2.py` would make a re-run easy (a new module must be registered as `AGENTS.md`
    describes).
  - Done when: no row of the `companies` table has a `gap`, or every remaining one has a `reason` that says why it cannot be done.

## Check the daily run of 2026-11-21

- [ ] **Review the 04:23 UTC run of 2026-11-21.** Due 2026-11-21. Three things from 2026-11-20, counts only (Actions logs
  are public, so no row contents):
  - REE Automotive was pinned to the `browser` reader on 2026-11-20 (journal entry 218): its careers page no longer uses
    Comeet and says "No open positions at the moment". The first run on the new reader should read zero jobs and close its
    stored ones. Undo: `companyctl.py revert 218 --apply`.
  - Priority Software read 9 jobs against 23 on 2026-11-20 and the sharp-drop guard held it (nothing closed); a local crawl
    read 21 three times. If it is held again on two runs in a row, look at the runner's behaviour and the "Load more" wait
    (`SETTLE_POLLS` in `browser_crawl.py`).
  - The 24 Goozali companies: each `ok` with jobs, no more than 3 new failures against the known set (the 25 Workday
    tenants, the 5 SmartRecruiters companies skipped by `robots.txt`, Check Point).
  Done when: all three are checked and this item is deleted.

## More companies (the rules and open steps of the former PLAN_3)

Goal, to close the coverage gap users reported (too few startups, too few large multinationals): the targets confirmed on
2026-11-18 are +60 startups crawled, +40 multinationals that return jobs from the GitHub runner, and 30 of the 80 researched
records unlocked. On 2026-11-20 the table holds 352 crawled and 627 researched (the Goozali import included), 0 excluded.
The research item above and "Close the Goozali gap" hold the per-company findings; this section holds the rules and the
recurring work.

- **Rules.**
  - Public site data only: a candidate is accepted only if its career page is the company's own and lists roles in Israel.
  - `robots.txt` is honoured and bot protection is never worked around. An opt-out is an `excluded` record (readable only by
    the admin, never deleted, backed up daily to the private repository), checked with `is_excluded` before a candidate is
    probed; the check sees excluded records only when the pull used the service-role key. Never paste the key anywhere.
  - One list: no candidate file is committed (candidate lists live in the scratchpad or a PR description). Add, pin, park
    and exclude on the Companies area of the admin page, so each pin is in the admin Log; SQL is a fallback and leaves no log.
  - An adapter only for a platform that two or more companies share and `robots.txt` allows; one company gets a pinned
    `browser` or `comeet_page` record.
  - A company is added only if its first crawl returns jobs (a company that returned nothing is baselined late, so its
    real first jobs are lost or, for a known company, sent as a flood). Count a company only if the GitHub runner reads it.
  - A company with any row in `jobs` or `crawl_log` is known (`run.py` `is_known`): unlocking, re-pinning or fixing its
    feed can email its jobs as new, so only the maintainer makes those edits. Parking closes a company's open jobs.
  - Batches: at most 25 new companies before each `discover-companies` run. Check the budgets below first; after a batch
    review the next daily run (jobs per company, new failures, the coverage card, the alert) before the next batch.
    Pinning a researched record is one pin at a time. A bad pin is reverted by editing the row (`status` emptied to have it
    looked at again).
  - The lawyer's files (`ABOUT.md`, `PRIVACY.md`, `TERMS.md`, `ASSISTANT.md`) are not touched.
- **Budgets** (measured 2026-11-18; re-measure before a batch with `gh run list --workflow=daily.yml`, then
  `gh run view <id> --json jobs`, the `Crawl and diff` step's `completedAt` minus `startedAt`).
  - Crawl time: the step took 3.5 to 3.9 minutes with 267 companies; the ceiling is 15 of the 20 minutes of
    `timeout-minutes: 20` (900 s). A browser record costs more than a JSON feed.
  - Failures: more than 3 new failing companies after a batch, not in the known set (the 25 Workday tenants, the 5
    SmartRecruiters companies skipped by `robots.txt`, Check Point and the other bot-protected browser crawls), blocks the
    next batch until they are fixed or parked.
  - Size: `docs/jobs.json` was 2.0 MiB for 6,764 jobs; the ceiling proposed is 4 MB and 500 `crawl_log` rows a day (the log
    keeps 90 days, about 45k rows). The maintainer confirms the ceiling.
- **Holds and decisions.**
  - Google stays a gap: `google.com/robots.txt` disallows the results pagination (`?page=`) and page 1 shows 20 of 65 Israel
    rows. Revisit only with an allowed feed or written permission.
  - Uber stays a gap: `jobs.uber.com` answers a Cloudflare challenge (403) although its `robots.txt` allows everything.
  - Unity's 123 stale non-Israel rows are accepted; they close when its Workday tenant answers from the runner.
  - All 25 Workday tenants are runner-blocked and stay listed; the bot-protected browser crawls (Nayax, DriveNets, Check
    Point, Papaya Global) stay listed.
  - The 09:53 UTC scheduled run is only a fallback and skips when a green run exists that day, so a new company's first
    crawl is the next 04:23 UTC run unless a manual run is made (a manual run with `digest_dry_run` crawls without emailing).
- **Decisions on sources** (the lawyer, 2026-11-19; no change to `PRIVACY.md`, `TERMS.md` or `SECURITY.md`).
  - Israeli job boards (AllJobs, Drushim, JobMaster): do not scrape them, as a feed or as a list of employers. Left out for good.
  - Getro and other VC talent boards whose `robots.txt` disallows the job list: left out unless there is written
    permission; find the portfolio companies by hand and add their own career pages.
  - Vendor-hosted career sites (Jibe, Eightfold, Phenom, TalentBrew): obeying the vendor host's `robots.txt` is enough, with
    no per-employer approval, provided only intentionally configured employers are read and a removal request is honoured.
  - Removal requests: the current process is sufficient (the `excluded` record is kept, the rows are deleted, the company is
    never crawled). Keep the written request as a paper trail.
- **Recurring and open steps.**
  - [ ] **Weekly triage.** Due 2026-11-27, then every Friday. Run `survey.py --candidates` on a new list, review
    the `crawl_log` failures and the coverage card, accept, park or decline each reported site as in `CONTRIBUTING.md`,
    "Reported sites", add at most 25 companies, and verify each batch by the next daily crawl. Count the reports per month.
  - [ ] **Success metrics.** Due 2026-12-20, then monthly. Per batch and per month: reports received
    (`CONTRIBUTING.md` has the command), the share of crawled companies with at least one Israel job, the Israel job count
    before and after. Put the numbers in each batch description, and add one `CHANGELOG.md` line per batch.
  - [ ] **Monthly sweep.** Due 2026-12-20, then monthly. Run the candidate tool on the newest names from the
    sources (VC portfolio pages, public lists, Y Combinator and Techstars cohorts, reports), with the budgets checked. Add
    the companies the tool keeps (`keep`), a candidate with only a career page and no feed needs the maintainer's yes (it
    becomes a browser crawl). Done when: the targets above are met and each batch's next daily run is reviewed.
  - [ ] **Quarterly recheck of the gap records.** Due 2026-12-04, then each quarter (next 2027-03-04). Recheck
    `site_unusable` (9) and `no_openings` (8): a site that is back or a board that reopened moves to the crawl.
    `bot_protection` (10) stays as it is; record each decision in the `gap.note` with the date so nobody retries.
  - [ ] **Research `no_careers_url` (22), `no_feed_found` (20) and `needs_reader` (16).** No date: batches of at most 25,
    each followed by a review of the next daily run. Find the real career page by hand (not by slug guessing), look for a
    JSON endpoint the page calls, group `needs_reader` by platform and check `robots.txt` first. Pillar Security needs a
    dedicated reader (its cards carry the city only as a section heading); Walnut is a researched record, not pinned.
  - [ ] **Dead-board alert.** No date. Extend the daily coverage-gap log line (`Log the coverage gaps`) to flag a crawled
    company with no open job for 30 days, from `crawl_log`. An alert, not a test; a test covers the query on fixtures.
  - [ ] **Remove a company** only on request (an `excluded` record, `CONTRIBUTING.md`, "Removing a company on request") or
    when it has closed for good.
- **Risks.** More records can push the daily job past its timeout or bloat `jobs.json` (the budgets, JSON feeds first);
  a pin that works from a normal network can fail from the runner (the runner-only rule); slug probing can hit an
  unrelated company (compare the board's company name); a global company's feed may mix countries (spot checks and fixtures
  with non-Israel jobs); a company may object to being listed (the `excluded` record).

## Close the Goozali gap

- [ ] Goozali gave permission (2026-11-20) to use the company directory it publishes (992 rows: name, industry, website,
  career page, size, funding stage). On 2026-11-20 the list was diffed against the live `companies` table by website and
  name. 24 companies with a job feed we already read were added as crawled (`discover-companies` run 44715708214, all
  `ok`; the 12 foreign-headquartered ones have `israel_only`), and 542 more as researched records (`gap`), so the table
  holds 352 crawled and 627 researched. The gap records by reason: `no_feed_found` about 376, `no_careers_url` about 111,
  `needs_reader` about 36, `no_openings` about 13 and `bot_protection` about 7. About 380 of them were never probed (the
  note says "Not surveyed yet"). 177 directory rows had no usable website and were left out. The directory's contact
  emails, phones and personal LinkedIn links are never stored, and the PDF and its extracts stay outside the repository.
  The steps, in order:
  1. Review the first daily run after 2026-11-20 (the 04:23 UTC run of 2026-11-21, due 2026-11-21): jobs per company for the
     24, new failures, the coverage card and the maintainer alert. No further batch starts before that.
  2. Survey the unsurveyed `no_feed_found` records: put them in a CSV outside the repository and run
     `survey.py --candidates` (CONTRIBUTING.md, "Trying candidate companies") with a service-role pull. Each `keep` is
     added as crawled in a batch of at most 25, one batch per daily run ("More companies", Batches), and its gap record is
     turned into a crawled one on the admin page. Companies with a long history of closed jobs are known; only the
     maintainer edits those.
  3. Find the career page of the `no_careers_url` records, largest first (size is in the directory), and record it.
  4. Take the `needs_reader` records one at a time: a browser crawl counts against the crawl-time budget and needs the
     maintainer's yes; a Workday or Oracle board is tested from a daily run before it counts ("More companies", Count only
     what the runner can read).
  5. Leave the `bot_protection` records as they are: a refusal by robots.txt, 403 or 429 is never worked around.
  6. Re-check `no_openings` and dead sites twice, then drop a record that still has nothing.
  7. Repeat the diff against a newer export of the directory each quarter, keeping the same rules. Due 2027-02-20,
     then every three months.
  - Done when: every Goozali company with a usable website is crawled, or has a gap `reason` that says why not, and the
    unsurveyed note is gone.

## Step 9.1 live checks

Added when the history moved to Supabase (v0.53.0; `CONTRIBUTING.md`, "The history lives in Supabase"). What the rehearsal
could not prove: a real PostgREST, a real Supabase project and a real Actions run. The restore was rehearsed on 2026-11-19 against a local Supabase (`CONTRIBUTING.md`, "Rehearsal, 2026-11-19"); a hosted project and a real Actions run are still unproven.

- [ ] **Check the first daily runs after the move.** Due 2026-11-21 (the runs since 2026-11-17 have been green; read the log lines below once and delete). Trigger: the PR of step 9.1 is merged and the one-time copy of the
  old history is done. Status (2026-11-17): the 04:30 UTC cron-job.org run was green; two other runs that morning failed
  at the commit step on a stale checkout, fixed in #152 (`ref: main`); no run after #152 yet. How: in the log of the first run, `pull` says `pulled <jobs> jobs and <log> crawl log rows`, the
  sync says `<n> crawl log rows written` and a second run the next day does not repeat them; `docs/` is committed and
  `data/` is not; the `backup` step commits to `ravidorr/misra-backups` with `jobs` and `crawl_log` in its counts. Done
  when two runs in a row after #152 are green and the coverage card and the robots.txt deletion still work (the five SmartRecruiters
  companies lose their `jobs` rows on the first sync after v0.57.0: the log says `Supabase: robots.txt, deleted <n> jobs of 5 companies`).
- [ ] **Measure the size of `crawl_log` and `jobs`, and of the description text, after a month.** Due 2026-12-16 (30 days
  after the move; the first descriptions were written on 2026-11-16). How: Supabase dashboard, Reports, Database.
  `crawl_log` keeps 90 days (`CRAWL_LOG_DAYS`, about 250 rows a day, more since the table grew to 979 companies), the pull
  reads all of it every run, and the backup exports it; if the pull gets slow or the table is large, shorten it. The same
  check is the storage check of the descriptions against the free 500 MB database (FR-4 acceptance criterion 10); the trim
  that sets the full text of a job to NULL 30 days after it closes is built and runs in every sync. Done when: the numbers
  are in `CONTRIBUTING.md`, "Real data against the limits", and the result of the storage check (trim now or not) is
  written there.
- [ ] **Keep the browser crawl's results when the browser crawl itself dies.** Added with the step 9.1 leftovers (v0.53.1).
  `run.py` now commits each company as it is recorded and records the API companies before the browser crawl, so a crash
  while recording loses one company and a crash in the browser crawl keeps the API companies. The browser crawl
  (`browser_crawl.crawl_many`) still returns all its results at the end, so a crash or the 20-minute step timeout inside
  it loses the browser companies of that day. Trigger: it happens in a real run (the alert shows `crawl=failure` and the
  log stops in the browser crawl). How: let `crawl_many` hand over each company as it finishes (a callback or a
  generator) and record it then. Done when a timeout in the browser crawl keeps the companies already crawled.
