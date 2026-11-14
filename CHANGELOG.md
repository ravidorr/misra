# Changelog

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.10.0] - 2026-11-14

### Changed

- The `workday` adapter recognises Israel on tenants the old routes missed. The facet filter now takes a country value named "Israel" with the tenant's own id (Philips) as well as Workday's id, and applies the value's id. When no such country exists it takes the values of a location facet whose name is an Israeli place (Broadcom's `ISR-Tel Aviv University`) and keeps only postings whose location `ISRAEL` recognises, without tagging them. The bare code `IL` never selects a facet value (it is Illinois as often as Israel). The word-search route now keeps a posting whose location `ISRAEL` recognises, not only one that says "Israel". A filtered first page whose total is above the counts of the values asked for (a filter Workday ignored would return every job) is a failed crawl. The 19 Workday boards already pinned return the same jobs.
- The Israel matcher moved from `discover.py` to `common.py` so the adapters can use it (`discover` imports `adapters`); `run`, `digest`, `build_dashboard` and `browser_crawl` import it from there. `ramat` now matches only at the start of a word, so "Parramatta" is no longer an Israeli place.

### Added

- Pinned with `israel_only` on Workday: Broadcom (`broadcom/wd1/External_Career`, 1 job on 2026-11-14) and Philips (`philips/wd3/jobs-and-careers`, 10 jobs in Haifa and Ra'anana).

## [0.9.0] - 2026-11-14

### Added

- A `phenom` adapter for career sites built on Phenom People (slug `<host>/<country>/<lang>`, for example `careers.cisco.com/global/en`). It asks the site's own search widget (`POST /widgets`) for the country Israel, 50 jobs a page, and links each job to `/<country>/<lang>/job/<jobSeqNo>`. The list is trusted only when every page is the search JSON, every job is located in Israel (a site that ignores the filter would answer with the worldwide list), and the reported total equals the Israel count of the site's own country facet (a filter that matches nothing, for example after a renamed value, is not an empty board) and the jobs add up to it, the same on every page, with no job twice; otherwise (including more than 100 pages) it is a failed crawl. Pinned with `israel_only`: Cisco (48 jobs on 2026-11-14), GE HealthCare (34) and Mastercard (3).

## [0.8.0] - 2026-11-14

### Added

- An `amazon` adapter that reads the search JSON of `amazon.jobs` for one country (slug: the country code, `ISR`), 100 jobs a page. The list is trusted only when every page answers with that JSON, every job is in the asked country and has an id, a title and a path, and the pages add up to the reported `hits`, which must be above zero (a wrong country code answers with no job) and the same on every page, with no job twice; otherwise (including more than 50 pages) it is a failed crawl. Amazon is pinned with `israel_only` on slug `ISR`: 171 jobs in Tel Aviv, Haifa and other Israeli sites on 2026-11-14, each recognised as Israel by `discover.ISRAEL`.

## [0.7.0] - 2026-11-14

### Added

- An `oraclehcm` adapter for career sites on Oracle Fusion HCM (slug `<host>/<site>`, from `https://<host>/hcmUI/CandidateExperience/en/sites/<site>`). It asks the public requisition search for the word Israel and keeps a requisition whose primary or one of whose secondary places has country code IL, so the descriptions that merely mention Israel are dropped. A bad slug, a failed request, an answer that is not a search, a requisition with no id or title, a page shorter than the reported total promises, and a list longer than 100 pages are failed crawls, not empty boards. Pinned with `israel_only`: Oracle (2 jobs on 2026-11-14), JPMorgan Chase (1), Dell Technologies (6), American Express (0) and Nokia (0).

## [0.6.0] - 2026-11-14

### Added

- A `bamboohr` adapter for boards on `<slug>.bamboohr.com`. It reads `/careers/list`, the public JSON the careers page is built from, and writes each location as city, state and country when the company filled them in. The answer must come from the board's own host, its `totalCount` must match the list and every entry must have an id and a title; otherwise (an unknown slug lands on a web page) it is a failed crawl, not an empty board. A job with no location at all cannot be recognised as an Israel job, so an `israel_only` board drops it. Its first board is Splitit (slug `splitit`, `israel_only`): on 2026-11-14 it listed 3 jobs, none in Israel, and two of them had no location at all.

## [0.5.0] - 2026-11-14

### Added

- A `jazzhr` adapter for boards on `<slug>.applytojob.com`. JazzHR has no public feed, so it reads the board page, where each job is a title link with a location and a department; a page it cannot read (an unknown slug, which redirects to jazzhr.com, or a changed layout) is a failed crawl, not an empty board. Its first board is LinearB (slug `linearb`, 1 job on 2026-11-14, in Tel Aviv).

## [0.4.1] - 2026-11-14

### Fixed

- The browser crawler no longer swaps the title and the team of a job card whose title is wrapped over two lines ("Senior Backend" above "Engineer"): a lone role word after a short line with no role word of its own now finishes that line, so the card reads "Senior Backend Engineer" instead of "Engineer" with the team "Senior Backend". An acronym such as "CISO" under a team name is still a title. Re-crawling 14 pinned browser pages before and after the change gave the same jobs for all of them; the one difference (Priority Software, 9 and 22 jobs) also showed with the old code, so it is the page, not the parser.

### Added

- Valens Semiconductor is pinned on `https://www.valens.com/positions/` (3 jobs on 2026-11-14, all at its Hod Hasharon office).

## [0.4.0] - 2026-11-14

### Changed

- The dashboard's "Intern & junior" filter is now two filters, "Intern" and "Junior". Titles with intern, internship, student or סטודנט are Intern; junior, jr, graduate, entry and trainee titles are Junior. A title matching both is Intern, and Product titles stay Product. The `category` option of the WebMCP search tool accepts `intern` and `junior`: `junior` no longer returns intern or student roles, use `intern` for those. The dashboard stores its filters under a new key, so a saved "Intern & junior" choice resets to Everything instead of silently narrowing to Junior.
- The digest email lists Intern roles and Junior roles in separate sections instead of one "Student / junior / intern" section. `ROLE`, the Intern and Junior patterns now live together in `digest.py`, and the dashboard imports them, so the email and the dashboard classify titles the same way.

## [0.3.2] - 2026-11-14

### Fixed

- The Israel location matcher now recognises SmartRecruiters jobs in Israeli towns it does not list by name ("Omer, il", "Migdal HaEmek, il"): the SmartRecruiters adapter writes the country code in capitals ("IL"), which the matcher tells apart from Illinois, and Migdal is a known place. On 2026-11-14 the ServiceNow and Western Digital boards held no such job (25 and 0 Israel jobs before and after), so no stored job changes; this closes the gap before one appears.

## [0.3.1] - 2026-11-14

### Added

- Workday career sites: the `workday` adapter (slug `<tenant>/<wdN>/<site>`, from `<tenant>.<wdN>.myworkdayjobs.com/<site>`) reads the jobs in Israel only. It looks in the facets of an unfiltered first search, nested groups included, for the one that lists Israel (its name differs per tenant) and filters by it; a posting in several places ("2 Locations") found that way is tagged Israel. Without such a facet it searches for the word Israel and keeps only the postings located in Israel, so multi-location postings are left out. Any failure, on any page, is a failed crawl and nothing is closed: a maintenance page, an answer that is not JSON, a later page that fails, or more pages than the limit. Pinned, with `israel_only`, for Samsung (3 Israel jobs) and Criteo (none today, by the facet and by the search).

### Fixed

- An `israel_only` company counts as new until a crawl of the same kind has worked once, not only until a job is stored. Before, the first Israel job of an `israel_only` company with none stored (a board with no Israel job when it was pinned) was baselined silently and never reached the digest. Other companies are unchanged: stored jobs still decide, so adding one never floods the digest.
- An adapter that raises fails its own company (the crawl log says "adapter error: ...", its jobs stay open) instead of stopping the whole daily run.

## [0.3.0] - 2026-11-14

### Changed

- The dashboard is no longer one 2.3 MB file. `build_dashboard.py` now writes `docs/index.html` (7 KB) next to `dashboard.css`, `dashboard.js`, `theme.js`, `jobs.json` and `jobs.jsonld`. The browser caches the stylesheet and scripts, and the page fetches the roles from `jobs.json` (about 190 KB compressed). The schema.org job list moved from an inline JSON-LD block to `jobs.jsonld`; the page keeps a small inline `Dataset` block that points to both files. The daily workflow commits all of these files. The dashboard script is now modern, linted JavaScript in `templates/dashboard.js`.
- The page has no inline script or style, so it ships a Content-Security-Policy (`default-src 'none'`, own-origin scripts, styles, images and fetches only) and Subresource Integrity hashes for its stylesheet and scripts.
- The logo's colors are tokens (`--mark-*`) instead of hard-coded values, and the icon-only theme switch is a design-system component.

### Fixed

- Accessibility: the result count is announced once typing pauses instead of on every keystroke; paging moves focus to the list heading; links that open a new tab say so; a story button's accessible name now contains its visible text; the design-system showcase has a meta description, no line break inside its heading, and the same icon theme switch as the dashboard.
- A failed `jobs.json` load shows "Could not load the roles" instead of an empty page.

### Added

- Browser tests run the built dashboard in Chromium: no console or CSP errors, no axe violations in light and dark (dashboard and showcase), focus after paging, delayed announcements, and the load-failure message. `axe-core` is a dev dependency.

## [0.2.10] - 2026-11-14

### Added

- The daily workflow emails a short alert ("Daily job run failed") when a step fails or the run is cancelled, for example by hitting its time limit. It lists the outcome of the verify, crawl, digest and commit steps and links to the run. Before, a failed crawl step skipped the digest and nothing reached the inbox; only GitHub's failed-run notice said so. The alert comes from `send_alert.py`, which uses only the standard library so it still works when installing the dependencies was what failed. It uses the same Gmail account as the digest, so it cannot report a Gmail outage.

### Changed

- The crawl and digest steps of the daily workflow now stop after 20 and 5 minutes. A step that times out fails and is alerted, instead of the whole 30-minute job being cancelled.

## [0.2.9] - 2026-11-14

### Added

- The digest email now ends with the companies that were not crawled properly: those whose last two crawls failed (blocked pages, HTTP errors), whose last crawl was held as a sharp drop, or whose jobs were closed after a sharp drop was believed. Each says why and when the last good crawl was. A company not crawled in the last 7 days is left out.
- On a day with no new jobs, an email still goes out ("Job crawl alert") when a gap has not been in an email yet. An outage alerts once, whether it is failing or held, and an alert whose email failed is sent the next day: the database remembers which gaps an email carried, and records it only after a successful send. A gap that was already emailed still appears in the footer of later digests.
- A problem in the coverage check never stops the job email: the jobs go out with a note that the check failed, and the workflow step fails afterwards so it is noticed (with no jobs to send, the failed step and the log are the only signs).

## [0.2.8] - 2026-11-14

### Fixed

- A browser crawl that finds no jobs at a company with open jobs is held the same way as a sharp drop: nothing closes and the crawl log says "sharp drop: got 0, N open before" with the crawler's own reason in brackets. At a company with fewer than 10 open jobs the jobs close on the fourth day if it was held on each of the three days before, so a small company that really closed every role no longer keeps them open for good. At 10 or more open jobs an empty crawl is never believed and the company keeps showing as failing: that many roles vanishing at once is far more likely a page that stopped working than closed roles, and closing them would raise no alert. A crawl that fails (blocked page, HTTP error) is a failure, not an empty result, and is unchanged. A small company behind a bot wall that answers with an empty page can still close its few jobs after three days; they reopen when the site is read again.
- More bot-protection pages are recognised by their title and count as a failed crawl: those mentioning a CAPTCHA, "request unsuccessful", "access to this page has been denied" and "pardon our interruption".

## [0.2.7] - 2026-11-14

### Fixed

- A browser crawl that returns under half the jobs open before no longer closes the rest. A site that starts paginating or capping its list looks like a company that closed most of its roles. Such a crawl is held: the jobs it did see are recorded (new ones included), nothing is closed, and the crawl log and the run summary say "sharp drop: got N, M open before". It applies to companies with at least 10 open jobs. The drop is believed on the fourth day, once the company was held on each of the three days before (runs within one day count once), and that run logs "drop believed after 3 held days". As of 2026-11-14 none of the 13 logged runs (over two days) would have been held. API feeds are complete lists and are not affected.

## [0.2.6] - 2026-11-14

### Added

- Pins for Intezer and Weebit Nano. Their job cards do carry a place, but the crawler read too little of each card to see it.
- Tests that run the job-card page script in a real browser (Chromium). They skip when none is installed, except in CI, which installs one.

### Fixed

- The browser crawler reads the whole job card: the widest ancestor of the title link, up to four levels up, that holds one job's title link and at most 600 characters of text. It used to read exactly two levels up, which was the whole list on Intezer and only the heading on Weebit. The script now counts jobs the way the crawler does (same link patterns, a `job_id` query counts, other query strings and fragments do not), and Apply or View Position buttons do not count as jobs.
- A card gives its place and team from the two lines above the title and the three below it, in the order the card prints them, so headings further up cannot push the place out. A job description in a card (a line over 120 characters) is no longer taken as the team or place, and a button link listed before the title link no longer takes the job.
- Across the 30 browser-crawled companies already pinned, nothing was lost; Priority Software went from 11 to 21 Israel roles and eToro gained its team field.

## [0.2.5] - 2026-11-14

### Added

- Pin for Tower Semiconductor: its career site has no feed, but one search page lists every open position (73, 24 of them in Israel, when this was added), so the browser crawler reads that page.

### Changed

- The browser crawler recognises `?job_id=N` job links and keeps the `job_id` when it tells jobs apart, instead of treating every such link as the same job.
- Table rows split into cells on a single tab, and a row that repeats its title cell no longer fills the team with the title.

## [0.2.4] - 2026-11-14

### Added

- Pins for Silverfort and Overwolf (Comeet boards behind their own career pages) and, now that their cards parse, for Oligo Security, Freightos, Weka, Pecan AI, Lasso Security, Panorays and Komodor.

### Changed

- The browser crawler reads a job card whatever the order of its lines (location, title, type; team, title, location; title, location) instead of taking the first line as the title and the next two as the location. It drops boilerplate such as "Full-time" and "View Role", recognises locations ("TEL AVIV, ISRAEL", "Dallas, US", "REMOTE (US)", "IL"), puts a team line in `team`, and splits a location glued to the title with tabs or a middle dot.

## [0.2.3] - 2026-11-14

### Added

- Eightfold career sites: the browser crawler reads the page's own search results and presses "Next jobs" through every page, stopping when a page has no Israel job. Pinned for Nvidia, Microsoft, Qualcomm, Citi, PayPal and Applied Materials (about 530 Israel roles), all with `israel_only` to drop nearby Palestine and Jordan results.

### Fixed

- The Israel location filter recognises the three-letter country code `ISR` ("Kiryat Gat,ISR"), which Applied Materials uses.

## [0.2.2] - 2026-11-14

### Added

- Optional `israel_only` flag on an override: the crawl then keeps only jobs located in Israel and closes the rest. Set for ServiceNow, MongoDB, Datadog, Snowflake, Zscaler and Okta, whose boards held about 2,700 jobs outside Israel.

## [0.2.1] - 2026-11-14

### Fixed

- The Israel location filter no longer counts US locations that use the code `IL` for Illinois ("Chicago, IL", "US-IL-Remote"). A bare `IL` still counts as Israel for towns written "Caesarea, IL", and the dashboard and digest share the one matcher.
- The browser crawler waits until a pagination click has produced new job links, instead of a fixed 1.5 seconds, so slow pagers such as monday.com no longer stop early, and a pager that empties the list before loading the next page does not end the crawl.

## [0.2.0] - 2026-11-14

### Added

- Design system: tokens with light and dark themes, component stylesheets, and a showcase page (`design-system/index.html`).
- Logo and favicon brief for the graphic designer (`design-system/logo-brief.md`), plus a provisional mark, favicon and apple-touch icon rebuilt from the designer's concept board.
- WebMCP tools on the dashboard (`search_jobs`, `list_new_jobs`, `get_job`, `discard_job`, `restore_job`, `list_coverage_gaps`), schema.org JobPosting JSON-LD, and `data-*` hooks on each role card.

### Changed

- Dashboard redesigned as a mobile-first feed (theme-color, safe-area insets, touch-friendly states) for students and early-career readers, with a theme switch. `build_dashboard.py` inlines the design-system CSS and emits the JSON-LD.

## [0.1.1] - 2026-11-14

### Changed

- Bump Playwright from 1.63.0 to 1.65.0 in `pyproject.toml` and in the daily workflow install step.

## [0.1.0] - 2026-11-14

### Added

- Initial project scaffold.
