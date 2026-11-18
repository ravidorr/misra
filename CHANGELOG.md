# Changelog

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Removed

- The "Filters reset." line with an "Undo" button that showed for 6 seconds after "Reset" (`#resetNote`, its timer and the code that restored the filters), and its `.reset-note` styles. "Reset" is unchanged and still acts at once; the `sr-only` status region now says "Filters reset." so screen readers still hear it.

### Added

- A Copy button under every code block of the legal pages (the address on "Connect your assistant", the command for Claude Code), in `templates/theme.js`.

## [0.67.1] - 2026-11-18

### Changed

- The Python tests use at most 4 parallel workers (`--maxprocesses=4` next to `-n auto`): several full runs at once (one per worktree) on one machine starved each other and made the browser tests time out.

- The About page sends people to the Contact page for a question, not to GitHub issues, and the comment on `CONTACT_FORM_ENABLED` describes the form-off Contact page as it is now.

## [0.67.0] - 2026-11-18

### Added

- A way home on every page but the dashboard: an icon-only house button in the header, in the place the dashboard gives to the account controls (`btn btn-pill btn-icon`, named "Home", so it looks and acts like the profile and theme buttons), and a "Home" link first in the footer. The logo link stays. `build_dashboard.SLOTS` now says what each slot holds on the dashboard and on every other page (`templates/parts/home-button.inc`, `home-link.inc`).

## [0.66.1] - 2026-11-18

### Changed

- The browser tests share one Chromium and one Playwright driver per worker (a new context for each test) instead of starting both for every test; the pytest event loop is shared for the session so the browser can be.

### Fixed

- The report form no longer loses its "Report sent" answer when the dialog's `close` event arrives late, after the answer has reopened the dialog (`templates/report.js`). It showed up as a flaky test on a busy machine.

## [0.66.0] - 2026-11-18

### Changed

- The contact form is on: `CONTACT_FORM_ENABLED` in `build_dashboard.py` is `True`, so the Contact page has the form, `contact.js` and the `connect-src` for the `contact` function. The function still answers 404 unless its secret `CONTACT_FORM_ENABLED` is `true`.
- `PRIVACY.md` carries the lawyer's wording of round 12, as he gave it: the "Contact form" bullet under "Technical records", the contact form in the spam-counter sentence of "The short version", and in the "Provider logs" bullet. `tests/test_legal_wording.py` pins all three, and the pin that the bullet is absent is gone.

## [0.65.0] - 2026-11-17

### Changed

- One list of every company: `data/companies.json` now holds the companies the pipeline crawls (`"crawl": true`) and the 77 researched ones it does not (`"crawl": false`, with a `gap` of `reason` and `note`), with each company's `links`, `segment`, `israel_only` and `verify`. It replaces `data/seed_companies.csv`, `data/overrides.json`, `data/career_links.json`, `data/unresolved_candidates.csv` and the research output in `data/new/`. A company is crawled only when its record says `crawl: true`.
- `discover.py` fills in each crawled record in place (`careers_url`, `ats`, `slug`, `source`, `status`) and leaves every other field and every other record as it is; a record with `"source": "override"` is a pin: its `ats` and `slug` are kept and checked. It no longer writes `n_jobs` and `n_israel`, which nothing read. `common.load_companies` and `common.crawled_companies` read the file; `run.py`, `build_dashboard.py`, `survey.py` and `survey2.py` use them.
- A record that does not say `crawl: true` is not crawled (`crawled_companies`, `discover.py` and the dashboard agree). `discover.py` writes through a temporary file, keeps the keys in `common.RECORD_KEYS` order, drops the records of excluded companies, and refuses to write when more than 5 working companies would fail (`--force` overrides).
- The crawl list, the coverage card, the career links and the covered count are the same as before (checked against the old files); the file is sorted by name.

### Fixed

- A `discover.py` run would have dropped `israel_only` from Datadog, MongoDB, Zscaler, Okta, Snowflake and ServiceNow: the research file `data/new/multinational_overrides.json` was read last and lacks the flag.

### Added

- `tests/test_companies_data.py` checks the real file: unique names and domains, known keys, a `crawl` that is a boolean on every record, and that a record is crawled or has a `gap`, never both.

## [0.64.1] - 2026-11-17

### Changed

- The Python tests run in parallel (`pytest-xdist`, `-n auto` in the pytest options): the full run with the 100% coverage gate takes about 2 minutes instead of about 10.

## [0.64.0] - 2026-11-17

### Added

- `npm run lint` now fails on an emoji or an em dash: `eslint-plugin-no-emoji` and `eslint-plugin-no-em-dash` check the strings of the JS and TS files, and `scripts/lint-text.mjs` (`npm run lint:text`) checks every Markdown, Python, HTML, CSS, YAML, SQL, JS and TS file, comments included. The lawyer's approved files (`PRIVACY.md`, `TERMS.md`, `SECURITY.md`, `ASSISTANT.md`, `supabase/email-texts.md`, `templates/email.js`) are skipped for the em dash rule only; `TODO.md` asks him about the five in `ASSISTANT.md`. A vitest file proves both checks fire.

### Changed

- The arrow after "Apply" and "Read full description" on a card comes from `String.fromCodePoint` (the same character), because the emoji rule reads the arrow as an emoji when it is a string literal.

## [0.63.0] - 2026-11-17

### Added

- A Contact page (`contact.html`, from `templates/contact.html`): the GitHub issues link, the security note and the privacy notice, with no address of the maintainer. The footer's Contact link now goes to it (the `mailto:` link is gone from every page).
- A contact form on that page and the Edge Function `contact` behind it, both OFF until the lawyer agrees: the build constant `CONTACT_FORM_ENABLED` (`False`) cuts the form, `contact.js` and the page's `connect-src`, and the function answers 404 unless its secret `CONTACT_FORM_ENABLED` is `true`. The function emails one plain-text message to the secret `CONTACT_TO` through Resend (fixed subject and From, the visitor's address only as `Reply-To`, never an email to the visitor), stores nothing, logs counts only, and refuses bots (hidden field answered with a fake success, minimum fill time answered with an honest error), a fourth message from one visitor in a day and the eleventh in all, using the existing `assistant_reserve` counters (no migration). `TODO.md` lists what the lawyer must decide.

### Changed

- The Resend call shared by `report-site` and `contact` is one module (`supabase/functions/_shared/resend.ts`). `report-site` no longer logs the first 200 characters of Resend's answer, only its status and Resend's short error name.

## [0.62.0] - 2026-11-17

### Added

- An About page (`about.html`, from the new `ABOUT.md`, in the same template as the privacy, terms, security and assistant pages) for people who use the dashboard: where the roles come from, how often they are read, what Misra does not do, the Coverage gaps card, sign-in, job alerts, the assistant and the open source code. `ABOUT.md` is in the `publish-docs` trigger paths and the daily workflow's docs commit list.

### Changed

- One header and one footer for every page (`templates/parts/`, put in by `build_dashboard.py`): the logo and "misra" are one link to the dashboard; the footer is the same on the dashboard, the email page, and the privacy, terms, security, assistant and About pages: About, Connect your assistant, Contact, Privacy, Terms, Security (the nav is named "Site pages"), with `aria-current="page"` on the link of the page shown. The dashboard still fills two slots only it can use: the account controls and the "last read" line. The theme button works on every page (its code moved from `dashboard.js` to `theme.js`).
- The privacy, terms, security and assistant pages are a card under the header with the title on the lime swipe, like the dashboard; their own footer and the "Open Misra dashboard" button are gone (the logo is the way back). The assistant link of the footer is now cut by the assistant switch on every page.

## [0.61.0] - 2026-11-17

### Changed

- The coverage gap card: a larger round chevron shows that the list can be opened; the two lists are "We choose not to include these companies" and "We miss roles at these companies" with the one-line note "We cannot find or read open roles in Israel at these companies."; each company says under its name where its link goes ("Israel careers", "Careers page" or "Website"). `build_dashboard.py` adds `to` (`israel`, `careers`, `site`) to every site of the coverage gaps, and `read_career_links` returns the address with it. A "1 company" summary is no longer "1 companies".

## [0.60.0] - 2026-11-17

### Added

- The Google Search Console ownership file `google0e1aa15dbd543e2f.html` (a public value, not a secret) is served at `https://ravidorr.github.io/misra/google0e1aa15dbd543e2f.html`: `build_dashboard.py` copies `templates/google0e1aa15dbd543e2f.html` to `docs/` unchanged on every build, the daily workflow commits it with the other files, and `scripts/lint-html.mjs` and lint-staged skip it (it is one line of text, not HTML). It must stay there for good.

## [0.59.0] - 2026-11-17

### Added

- Descriptions for the browser crawl (PLAN.md step 7.1): HiBob career pages (Sapiens, Hibob, Fundbox) and any list-page JSON-LD `JobPosting` give the text from what the crawl already captures; Eightfold boards (Nvidia, Applied Materials, Qualcomm, Microsoft, HP, PayPal, Citi) are read through their own `/api/pcsx/position_details` JSON; four career pages (Weka, eToro, Perion, Digital Turbine) from the JSON-LD `JobPosting` of the job page. Same budget, `robots.txt` gate and politeness as Workday (held keys, 40 a company and run, five misses in a row end a company's run, after the lists). Check Point (HTTP 403) and the other browser companies without JobPosting markup stay without a text: nothing is worked around. The `job pages:` log line now counts the browser companies too.

## [0.58.0] - 2026-11-17

### Added

- Descriptions for Workday (PLAN.md step 7.1, 24 companies, 318 jobs): the list has no text, so the crawl opens each job's page on the tenant's own host (`/wday/cxs/<tenant>/<site>/job/...`, the same `/wday/cxs/` path as the list, so the same `robots.txt` rule and the same one request a second) and reads `jobPostingInfo.jobDescription` and the employment type. A page that fails, is refused by `robots.txt` or has no text gives no description and never touches the list: the company is not skipped and no job closes.
- The request budget: a job page is asked for only when Supabase holds no text for the job. `sync_supabase.py --pull` leaves the keys (company and job id, never a text) of the open jobs that hold one in `$RUNNER_TEMP/misra-held-texts.jsonl`, the crawl reads it, and at most 40 job pages are opened per company and run (`descriptions.DETAIL_CAP`), the rest wait for the next run: the first fill of the 318 jobs takes up to three days (Palo Alto Networks 115 and KLA 82 are the largest), a steady day asks only for the new jobs. A text edited in place is not fetched again (`TODO.md`). A job that holds a text counts in the description share of the crawl log; a new log line, `job pages: workday N requests, N with a text, N left for later runs`, has the counts.

## [0.57.0] - 2026-11-17

### Changed

- A company whose `robots.txt` does not allow us to read its jobs loses them at once, not after 7 days: the crawl removes them from the scratch history and the sync deletes the company's `jobs` rows in Supabase (open and closed, with the stored text, snippet and details). Only `jobs` is touched, by exact name: the crawl log (the company stays on the coverage card), the stars and the emailed keys of users stay, and a star keeps its own title and link and shows as no longer listed. Only a read `robots.txt` that disallows us counts: a timeout, a 403 or any other failure deletes nothing, a `robots.txt` that cannot be read counts as allowed, and a name with another board that crawled or failed is left alone. More than 10 such companies in one run are refused with a warning (a host answering `Disallow: /` by mistake must not empty the table), a failed delete is a warning and the next run tries again, and the log holds counts only (`Supabase: robots.txt, deleted <n> jobs of <m> companies`). The 7-day streak, `ROBOTS_CLOSE_DAYS` and `track_robots_skips` are gone.
- The coverage card says "Their policy does not allow automated access, so we respect it." under "We choose not to read these" and each company shows only its name and link (the per-company text with the days skipped and the last good crawl is gone). `jobs.json` keeps its shape: the `why` of those sites is an empty string, the `days` of `gaps.blocked` and `gaps.closeDays` are removed.
- The button on the coverage card that opens the form reads "Add a company" (was "Report a site"), the sentence before it is gone, and the form's heading is "Add a company". The intro also invites a report about a company we read wrong. `PRIVACY.md` still names the old button (lawyer's text; question in `TODO.md`).

## [0.56.0] - 2026-11-17

### Changed

- The ellipsis after a job snippet is exact. A new column `jobs.snippet_cut` (migration `20261126100000_jobs_snippet_cut.sql`, readable by everyone like `snippet`) is true when the 255-character snippet is shorter than the posting, false when it is the whole text and null when there is no snippet. The sync sets it (`descriptions.snippet_cut`) and writes again a row whose flag differs, the migration fills it in for the rows already stored, and the page shows the ellipsis exactly when the flag is true (and nothing when it is missing) instead of guessing from the length and the last character. The column is left out of the account backup like `snippet`.

## [0.55.0] - 2026-11-17

### Changed

- The coverage gap card links each company to the career page the maintainer found, kept in the new `data/career_links.json` (`israel` and `global` pages per company): the Israeli page, else the global one, else the data's own address or the company's own site, as before. A test checks the file (https only, a real host, no wildcard or tracking parameter, a known company name, sorted). The page, `jobs.json` and the `list_coverage_gaps` tool keep their shape.

## [0.54.0] - 2026-11-17

### Added

- The assistant connection is on. The page "Connect your assistant" (`assistant.html`, from `ASSISTANT.md`, with the lawyer's wording) is built and linked from the footer and from `llms.txt` (`ASSISTANT_PAGE_ENABLED = True`), after the lawyer approved it and the migration, the function `mcp` and the secret `ASSISTANT_ENDPOINT_ENABLED` went live (limits: 100 searches and 15 job reads per caller per day, 500 job reads a day for everyone).

## [0.53.1] - 2026-11-17

### Fixed

- The daily job checks out `ref: main` (the tip of `main` when the job starts) instead of the commit the run was queued at. A run that waited in the `daily-job-digest` group behind a `publish-docs` run built on the older commit and its `docs/` push conflicted on `docs/jobs.json` and `docs/jobs.jsonld` ("push to main failed after 3 attempts", two runs on 2026-11-17). The two workflows never overlapped; the checkout was stale. The deploy-key flow and the `protect-main` ruleset are unchanged.
- A bad pull or a failed sync never publishes: the dashboard build runs only after a good sync, and the commit only after a good build (the alert lists `build=`). A failed crawl, digest or gaps step still builds.
- The pull refuses a `jobs` table with under half of the open jobs the dashboard last published (`docs/jobs.json`; a partly hidden or lost table would look like jobs that closed and the rest like new ones), by the same rule as the backup's shrink guard (`export_accounts.fell_by_half`). `--allow-shrink`, and the `allow_shrink` input of the manual run, override it for a real loss. An unreadable `docs/jobs.json` fails the pull too (it is committed, so it is always there); an empty `crawl_log` is a `::warning::` line (counts only), never a failure.
- A crash late in the crawl no longer loses the day: `run.py` commits to the scratch history company by company and records the API companies before the browser crawl starts, and the sync step also runs after a crawl that failed part of the way (the run is red anyway). A crash inside the browser crawl itself still loses its results (`TODO.md`), and a day that crashed has no descriptions (the work file is written at the end of the crawl). The digests still run after such a sync: a job recorded that day is emailed once (by `first_seen` and `emailed`), a company not reached is recorded and emailed the next day.
- The first backup after a table joins the export no longer switches the shrink guard off: `publish_backup.previous_counts` judges each table by its own file in the last backup, so the account tables stay guarded and the new ones are guarded from the next day.
- The dashboard's order of jobs (`ORDER BY company, job_id`, byte order) has been fixed since 0.53.0 and a rebuild over the same data is byte-identical (a test now says so). The one reorder of `docs/jobs.json` on 2026-11-17 was that switch: before 0.53.0 the query had no `ORDER BY` and listed the jobs in the order of the old database.

## [0.53.0] - 2026-11-17

### Changed

- The history of the crawl lives in Supabase, not in the repository (PLAN.md step 9.1). `data/jobs.db` is a per-run scratch file, rebuilt from Supabase by the new first step of the daily run, `python sync_supabase.py --pull` (every row of `jobs`, open and closed, without the text of the postings, and the whole `crawl_log` with the ids Supabase gave it), and it is no longer committed (`.gitignore`; the daily commit holds `docs/` only, so the `protect-main` ruleset and the deploy key are unchanged). The pull fails closed: no secret, no answer, a table that changes under the read or an empty `jobs` table (a crawl from nothing would take every job for new and the digest would email them all) is a red run with `pull=failure` in the alert, and the crawl, the coverage-gap step and the dashboard build refuse a missing `data/jobs.db` (`common.open_history`). The pull marks its file (`PRAGMA user_version`) and the sync refuses an unmarked one, so a `touch`ed history can never overwrite Supabase's `first_seen` values. The crawl itself is unchanged.
- The sync writes this run's `crawl_log` rows to Supabase (the rows with no id yet; a second sync of the same file writes none) and deletes the log older than 90 days (`CRAWL_LOG_DAYS`), so the table, the pull and the backup stay a fixed size. A failed log write fails the step; a failed trim is a warning. An empty `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is now an error for the sync too, not a skip: the history lives there.
- The exclusion list also deletes a company's `crawl_log` rows in Supabase (`PURGE_TABLES`), and the scratch file's purge no longer vacuums it (the file is not published any more).
- The backup now also holds the job history: `export_accounts.py` exports `jobs` (open and closed, every column except `description`, `details` and `snippet`, which are never read) and `crawl_log` beside the five account tables; `publish_backup.py` publishes the seven files and its shrink guard covers them; `restore_accounts.py` restores them (no `auth.users` check, since they hold no user; a log row keeps its `id`, the sequence moves past it, a second run changes nothing). The `Table` rows of the export carry `account`; `orphans` and the "profiles is empty" check look at the account tables only; the key of a table sorts as the database gives it (`crawl_log.id` as a number).
- `publish-docs` pulls the history before it builds (its pull step is the only one in it with the Supabase key), so a rebuild is never made from an empty history. The dashboard lists the open jobs by company and job id, so a rebuild over the same data is byte-identical to the daily build.
- The coverage gaps log (`python run.py --gaps`) and the robots.txt closing no longer need tables of their own: a gap is "new" when it began on the newest crawl day or up to two days before (so a lost daily run does not hide it; nothing remembers what was logged), and the streak of a robots.txt skip is read from `crawl_log` (the latest crawls with that note, from the first of them).
- `AGENTS.md`, `README.md`, `CONTRIBUTING.md` ("The history lives in Supabase", with the one-time copy of the old history and the rehearsal of 2026-11-17; "Backups of the account tables and the job history"), `PLAN.md` (step 9.1 done), `TODO.md` ("Step 9.1 live checks"), `PRD.md` and `SECURITY.md` describe it. `PRIVACY.md` is not changed: the old commits of `data/jobs.db` stay in the public repository, so what it says about the job history there is still true.

### Removed

- `data/jobs.db` from the repository (it stays in git history), the daily commit of it, its integrity check and the `jobs-db` artifact that kept it when the push failed (the data is in Supabase), and the tables `robots_skips` and `gap_alerts` with `mark_alerted`, the `ALTER TABLE` that added `note` to old databases, the `VACUUM` of a purge and the support for a database without a `note` column.

## [0.52.0] - 2026-11-17

### Added

- The assistant endpoint (PLAN.md step 8.1, FR-6), built and **switched off**: the Edge Function `mcp`, a public read-only MCP server
  (JSON-RPC over HTTP, no sessions, no sign-in, no user data) with two tools. `search_jobs` runs the new SQL function of the same
  name, which applies the dashboard's search (open jobs in Israel, levels, role types, regions, every word in the company, title,
  team or location or in the full text through `search_job_keys`), 50 roles at most, the snippet and never the text. `get_job`
  returns one job by company and job_id with everything collected, the full text included, only when it is open or closed for less
  than 30 days. Per-caller limits (200 searches and 30 `get_job` a day) and a cap of 1,000 `get_job` a day for everyone, counted
  in `assistant_ip_limits` against a keyed hash of the tool, the caller's network and the day (the address is never stored or
  logged), cleaned by the daily cleanup. It answers 404 to everything unless the function secret `ASSISTANT_ENDPOINT_ENABLED` is
  `true`, it is not deployed, and nobody sets the secret before the lawyer has reviewed it. Migration
  `20261125100000_assistant_endpoint.sql` (harmless while it is off; the maintainer applies it): the counter table, `assistant_reserve`,
  `search_jobs`, and `report_site_cleanup` replaced so it also clears the new counter. The lint in `tests/test_migrations.py` pins
  all of it (nothing for the public roles, `search_jobs` never names the text).
- The "Connect your assistant" page (PLAN.md step 8.2, FR-6 AC 5 to 7), built and **switched off**: `ASSISTANT.md`, plain step-by-step instructions
  for Claude, ChatGPT and other assistants with the address of the assistant endpoint, converted to `docs/assistant.html` in the
  template of the privacy page (`build_dashboard.py`, one new `LegalPage`), a "Connect your assistant" link in the dashboard footer
  (the region between `<!--assistant-->` and `<!--/assistant-->`), a `.legal pre` style so an address wraps on a phone, `ASSISTANT.md` in
  the paths of the `publish-docs` workflow and `docs/assistant.html` in the files the daily workflow stages. The constant
  `ASSISTANT_PAGE_ENABLED` is `False`, so nothing changes in `docs/` and the page is not published: it stays off until the lawyer has
  reviewed the assistant endpoint and the function secret `ASSISTANT_ENDPOINT_ENABLED` is set (see `TODO.md`). A test keeps `llms.txt`
  naming the page exactly when the constant is on.

### Changed

- The visitor's network and its daily keyed hash moved from `report-site` into `supabase/functions/_shared/visitor.ts`, and the
  call into a Postgres function got a `callRpc` in `_shared/subscription.ts`, so `report-site` and `mcp` share them. No change in behavior.
- The README keeps a short developer note on the assistant connection instead of describing it in full.

## [0.51.0] - 2026-11-17

### Added

- Search over the full text of the postings (PLAN.md step 7.3, FR-4 AC 6). Migration `20261124100000_jobs_text_search.sql` adds a GIN index on the description and `search_job_keys(p_query)`, a security definer function that returns only a jsonb array of `[company, job_id]` of open jobs (all the words, `plainto_tsquery` with the `simple` configuration, so a quote, `or` or minus sign is no operator; a query needs a run of 3 letters or digits, punctuation does not count; at most 3000 keys, so a word in more than 3000 jobs is cut in company order; never the text; `description` and `details` stay closed to the public roles, and the lint in `tests/test_migrations.py` pins the function). The dashboard asks it once typing pauses and shows a role when its title, company, team or location has every word or its key is in the answer; if the call fails the title match stays. The digest keyword filter uses the same rule (one search per distinct keywords; a failed search is counted and the run exits non-zero, the title match still goes out). Apply the migration before the next daily run.
- A detail view from the card (FR-4 AC 5): a "Details" button on a card that has a snippet opens a modal dialog with the whole snippet, the role's tags and the link "Read full description & apply on Company" to the employer. The full text is never in the page. Focus moves to the title and returns to the button on closing.
- Each job card shows the start of the posting under the title (PLAN.md step 7.2, the card half): the public `snippet` column, the start of the posting as written (up to 255 characters), in a muted paragraph with `dir="auto"` (a Hebrew posting reads right to left), shown as text and never as markup. It is clamped to two lines on a phone and three from 561px. The data has no ellipsis, so the page adds one (hidden from screen readers) to a snippet of 200 characters or more that does not end in `.`, `!` or `?` (a cut never does). It is a heuristic: a whole text of that length that ends in a quote or a bracket gets a false ellipsis. A job with no text shows nothing. The dashboard reads `snippet` with the other named columns (never `description` or `details`), and the WebMCP tools that return an open role (`search_jobs`, `list_new_jobs`, `get_job` and the others) carry it as `snippet` (`null` when there is none). `jobs.json` and `jobs.jsonld` are unchanged. The detail view with the link to the employer is step 7.3.

### Changed

- The "Level" chips (All, Intern, Junior, Not specified) and "New only" moved from under the search into the filters card, as its first controls, so the quick filters sit with the other filters and count in "Filters (n)" as before. Same ids, filter keys and WebMCP arguments, so saved filters, the account filter sync and the first-sign-in import are unchanged. "Starred (n)" stays beside "Filters": it is a view of its own, not a filter, and the starred list puts the filters card away, so the switch back has to stay outside it. On a phone the chips are one tap further away (open "Filters" first).

- "Reset filters" is a real button (`btn btn-pill`, the secondary look, below "Show roles" in weight) instead of a link look. Its behavior is unchanged: it resets the filters at once, with no confirmation.

- After "Reset filters" a line "Filters reset." with an "Undo" button shows for 6 seconds (`#resetNote`, `role="status"`, in the filters card's footer) and puts the filters back as they were, the account's saved filters included. Any other change to the filters ends the offer; there is no history and no dialog.
- "Reset filters" no longer clears the search box: the search words are not a filter (they are not in the "Filters (n)" count), and the box is outside the filters card.

### Removed

- The violet spark (star) in the top-right corner of the Misra logo: from `design-system/assets/mark.svg` (so the favicon), `design-system/assets/apple-touch-icon.png` (rendered again from the SVG, same 180 px size and white background), the header logo in `templates/dashboard.html`, the `.mark-spark` rule and the unused `--mark-spark` token. The logo is the lime disc with the ink pulse line, same size and position; the page, wordmark and layout are unchanged. `design-system/README.md` and `logo-brief.md` describe the new mark.
- "Export discards" and "Import discards" on the filters card, the hidden file input behind them and the code that read and wrote the file. Accounts sync discards across devices, and the first sign-in already offers to bring this browser's own choices over (PLAN.md step 4.3), so the file was redundant. A signed-out visitor's discards stay in the browser as before. `PRIVACY.md` no longer says the "Import discards" file is read inside the browser (the sentence about the missing document upload keeps its first half); the lawyer must approve that edit before the merge.

## [0.50.2] - 2026-11-16

### Changed

- The dashboard footer is two lines: "Career pages last read <time>. Career pages are read daily." and, on a line of its own below it, Privacy, Terms and Contact (centered on a phone, both lines start at the left from 768px). The links no longer sit to the right of the text.
- `data-companies-covered` (the number of covered companies, read by agents) moved from the footer sentence to the search field, next to its "Search N companies" placeholder; `covered` in `jobs.json` is unchanged. `coverage_words` takes only the count.

### Removed

- The footer sentences "Roles from N companies whose career pages we read daily." (the search placeholder says it) and "Coverage gaps lists N companies whose roles may be missing or old." with its link (the Coverage gaps card says it), the `__COVERAGE_GAPS__` placeholder, the `.foot-coverage`, `.foot-gaps` and `.foot-info` styles, the two-column footer layout and the click handler that opened the card from the footer link.

## [0.50.1] - 2026-11-16

### Added

- The two emails people receive are now designed HTML documents as well as plain text (multipart: the plain text stays, with the headers unchanged, and is sent together with the HTML). The confirmation email (Edge Function `email-settings`, `email-html.ts`) and the digest (`send_digests.py`, `render_digest_html`) use one 600px column on the page colour, a lime band, the product's colours (fixed values in `supabase/functions/_shared/email-colors.json`, kept equal to `design-system/tokens.css` by a test), a light and a dark version (`prefers-color-scheme`), table layout, inline styles, system fonts, no image, no script, no remote stylesheet. The confirmation link is a button (a link with a background and padding; a padded cell for Outlook). Not one word changed: the text of each HTML email equals its plain text, which a test checks for both emails.
- Gmail clips a message near 102 KB and would hide the footer with the unsubscribe link, so a digest whose HTML would pass 90,000 bytes (about 150 roles with the average 68-character address, 125 with 122-character ones) is sent as plain text only, the same words. The digest has no cap on the number of roles, and this adds none.
- The Edge Function `email-settings` must be redeployed after the merge for the confirmation email to carry the HTML.

## [0.50.0] - 2026-11-16

### Added

- A public `snippet` column on `jobs` (PLAN.md step 7.2, the backend half; migration `20261122100000_jobs_snippet.sql`): the first 255 characters of a posting's description as written, cut at a word boundary with no ellipsis (a cut that leaves trailing punctuation is trimmed to the last whole word; a text that fits is kept whole; none for a job without a text, or when the first 255 characters have no space). `descriptions.snippet` computes it and the sync writes it with the description, and rewrites it when the stored one is not the computed one (so the texts already stored get theirs on the first run). `anon` and `authenticated` can read `snippet`; `description` and `details` stay closed, as the lawyer decided on 2026-11-16: the full text is for search and the assistant, never for the public API. The dashboard does not show it yet.
- The 30-day trim (PLAN.md step 7.4's rule; migration `20261123100000_jobs_text_trim.sql`): `jobs_text_trim(p_day date)` sets `description`, `details` and `snippet` to null for the jobs with `status = 'closed'` whose `last_seen` (the last crawl that saw the job open: there is no closed-at column and the sync does not change `last_seen` when it closes a job) is more than 30 days before `p_day`, and returns how many jobs it changed. Title, company, link and dates stay. `details` is nulled whole so the text fingerprint goes with it: a job that reopens has none stored, and the next sync writes its text and snippet again. Service role only (execute revoked from public, anon and authenticated). `sync_supabase.py` calls it after the descriptions pass and logs the count (`Supabase: N closed jobs lost their text (30 days after closing)`); a failure is a `::warning::`, never a failed step. The trim is early at worst, never late: `last_seen` is never after the real close, so a company whose crawl failed or was skipped before its jobs closed has them trimmed sooner than 30 days after the close. Apply both migrations (`20261122100000_jobs_snippet.sql`, then `20261123100000_jobs_text_trim.sql`) before the first daily run of this version: without the first, the sync reads the stored `snippet` column, that read fails, and no description is written that day (one `::warning::`); without the second, only the trim warns.

## [0.49.1] - 2026-11-16

### Fixed

- The first footer sentence is no longer taller on one line. The "Coverage gaps" link was a 44px box inside the sentence, which made its line 44px tall and left a gap in the paragraph. The whole second sentence ("Coverage gaps lists N companies whose roles may be missing or old.") is now the link, a 44px block under the first sentence; the words are the same. The footer sentence about where the choices are saved (`#saveNote`, `savedHere`, `savedThere`) is removed: the sign-in dialog and PRIVACY.md say it, and the dialog and the privacy notice are unchanged. A browser test pins even lines at 320, 375 and 1480 px in light and dark.

## [0.49.0] - 2026-11-16

### Added

- Descriptions in the crawl (PLAN.md step 7.1, the first slice). The `greenhouse`, `greenhouse_url`, `lever`, `ashby`, `workable`, `comeet` and `amazon` adapters return `description` (plain text) and `details` (employment type, department, experience, workplace type, offices) for the jobs whose site gives them, from the request they already made: Greenhouse and Workable ask for the content in the same list request (`?content=true`, `?details=true`), the others read fields that were already in the answer. These adapters cover about 4,760 of the 6,814 open jobs (70 percent). The HTML is turned into plain text (`descriptions.py`): scripts, styles and frames are dropped, paragraph and list line breaks are kept, control characters are removed, and a text is cut at 20,000 characters (recorded as `description_truncated` in `details`).
- The full text lives in Supabase only. It is never written to `data/jobs.db` or any committed file (the daily workflow commits those to a public repository): `run.py` leaves the texts in a work file in the runner's temporary directory and `sync_supabase.py` reads it, writing `description` and `details` in a second pass in batches of its own. A hash in `details` (`text_sha256`) skips a text that did not change; a job without a text today keeps the one it has; a failure in this pass is a warning, never a failed step. Tests pin that no description reaches SQLite, `docs/` or the staging list of the daily workflow.
- The migration `20261121100000_jobs_text_not_public.sql` (SQL only; the maintainer applies it before the first daily run that writes text): `anon` and `authenticated` lose the table-level `select` on `jobs` and get it back on every column except `description` and `details`, so the text of other companies' postings is not readable through the public API until the lawyer has seen it. The service role is unchanged. A later column stays closed until a migration grants it. Pinned by `supabase/tests/job_text_private.sql` (CI `sql-tests`) and by the migration lint.
- The crawl log has one new line with the share of jobs with a full description per ATS (counts and percentages only), FR-4 AC 3.

### Changed

- `PLAN.md` step 7.1 no longer says the SQLite `jobs` table gains the columns: the description is in Supabase only. `CONTRIBUTING.md` has a "Job descriptions" section and a storage estimate from real sizes.

## [0.48.0] - 2026-11-16

### Fixed

- A Comeet job no longer links to its company's general careers page. The `comeet` adapter took `url_active_page` (the company's own page for the job) whenever it was set, but for some jobs that field is the careers page itself (Claroty "Tech Lead Platform Software Engineer" -> `claroty.com/open-positions`, Qodo "Bookkeeper" -> `qodo.ai/careers`), or one page that several jobs, or one job in several places, share. `url_active_page` is now kept only when it is the page of that one job; otherwise the job's own Comeet page (`url_comeet_hosted_page`, `comeet.com/jobs/<name>/<uid>/<title>/<position uid>`) is the link. A page is not one job's when more than one job of the feed points to it, when it is the home page, when it is above another job's page (`/careers` above `/careers/co/boston/AB.123/`), or when it is named as the list of jobs (`/careers`, `/jobs`, `/open-positions`). A page with the job in its path or its query (`?job=AB.123`) is kept, and tracking parameters (`t`, `utm_*`, `Share`, `gh_src` and the like) never make two links differ. Without a Comeet page the company's page is still used.
- The audit behind it, on the stored database (6,793 open jobs): 150 open jobs of 16 companies share their link with at least one other job (Coralogix 23, VAST Data 20, Checkmarx 18, Bizzabo 12, Stampli 12, UVeye 11, OX Security 10, Port 10, Allot 8, Nova 6, Sunbit 6, Wiliot 4, Tufin 3, Zenity 3, Ceragon 2, Upwind 2), and seven more have a job on the careers page alone (Claroty, Qodo, Global-e, Cycode, Cynet, BlueWhite, Elementor). On the live feeds of the 77 Comeet companies, 158 of 1,767 jobs of 24 companies change link, and none of them shares a link with another job afterwards. No other adapter is affected: the other API adapters build the link from the job's own id or take the job's own link, `comeet_page` and the browser crawler take the link of the job's card, and the stored jobs of those companies share no link.
- Only the link changes. The job's key is still `(company, job_id)` with the Comeet uid as the id, so a job is not new, is not closed and reopened, keeps its `first_seen` and its `reported` flag, and is not announced again (a test crawls a stored job with the careers page as its link and checks the row). The stored links, and the Supabase `jobs.url` (the sync upserts on `(company, job_id)` and merges the columns), are updated by the next daily crawl and sync, with no migration.

## [0.47.0] - 2026-11-16

### Removed

- The maintainer's own single-recipient digest: `send_email.py`, its workflow step "Send the maintainer digest (legacy, single recipient)" with the constant `LEGACY_MAINTAINER_DIGEST`, and `digest.py` with the selection logic that the FR-9 rule replaced (`select`, `ROLE`, `EXCLUDE`, `render` and the preview). The maintainer is an ordinary subscriber and gets `send_digests.py`'s email. `send_alert.py` (Gmail) still emails a failed run, and its step list no longer has `digest=`.
- `PRIVACY.md`, Providers, no longer says Gmail sends "the maintainer's own daily summary email": it says only that failure alerts go to the maintainer's own address and hold no account data.

### Changed

- The coverage gaps are still logged, now by their own workflow step, "Log the coverage gaps" (`python run.py --gaps`, `log_coverage_gaps`), right after the crawl: one line `coverage gaps: N (M new): ...` of counts and company names, as a `::warning::` annotation when a gap is new, and `gap_alerts` marks the gaps already logged. A broken check turns that step red, so the failure alert fires (its `STEPS` line has `gaps=`), and the sync, the digests and the commit still run.
- The `DB` path constant moved to `common.py`; `build_dashboard.py` and `sync_supabase.py` read it there.
- `CONTRIBUTING.md`, `PLAN.md` (steps 6.4 and 6.6 are done), `README.md` and `AGENTS.md` describe the one remaining sender.

## [0.46.0] - 2026-11-16

### Added

- A "DevOps & SRE" role type, right after R&D in `ROLE_TYPES` (`classify.py`). It matches DevOps, DevSecOps, SRE, site reliability, CI/CD, and "platform", "infrastructure" (or "infra"), "cloud", "release", "build", "kubernetes" (or "k8s") and "production" right before "engineer" ("Data Platform Engineer", "IT Infrastructure Engineer" and "Cross-Platform Engineer" do not; neither does a prefix inside a longer word). "platform", "cloud" and "infrastructure" alone do not match ("Cloud Solutions Architect", "Customer Platform Manager"). The dashboard filters, quick chips and Filters sheet, the `search_jobs` and `list_filters` assistant tools, the `data-role-types` hooks and the digest filter all read `ROLE_TYPE_NAMES`, so they list it with no other change.

### Changed

- DevOps and SRE titles move out of R&D: `devops|sre|site reliability` left the R&D patterns, and R&D's generic "engineer" pattern skips the titles above. A title another R&D pattern also matches (a developer, a tech lead, back end) keeps both types. On the 6,758 open jobs of 2026-11-16, 116 moved out of R&D; 134 are now DevOps & SRE (17 of them also R&D); R&D went from 2,373 to 2,257; the share of jobs not in Other went from 75.48% to 75.50%.
- A saved filter or email alert that lists "R&D" no longer matches the DevOps roles: add "DevOps & SRE" to it. Only one real account exists today. No migration: `jobs.role_types` is `text[]` and the daily sync rewrites every open job, so the live Supabase rows change at the next sync.

## [0.45.0] - 2026-11-16

### Changed

- A failed unsubscribe link (a bad or missing token, a bad signature, an account that no longer exists) now ends on its own page state, `email.html#unsubscribe-link-not-valid`, with the text the lawyer approved on 2026-11-16: "This unsubscribe link no longer works" and "Your account may have been deleted, or we may have replaced the key that signs these links. To stop job alerts, sign in on the Misra dashboard and turn them off, or write to raanan@avidor.org and we will stop them for you." (the lawyer's first sentence repeats the heading, so the body starts at the second one). It used to land on `#link-not-valid`, whose text (written for a failed confirmation link) (expired after 7 days, replaced by a newer link) was untrue for an unsubscribe link. The failed confirmation link keeps its text.
- The `unsubscribe` Edge Function's POST (RFC 8058 one-click: 404 JSON `link_not_valid`) and HEAD (200, no database) are unchanged.
- The `unsubscribe` Edge Function must be redeployed.

## [0.44.0] - 2026-11-16

### Changed

- The footer is redesigned and its text rewritten by the technical-writer agent. Layout: a rule in the card border colour, then three short lines in one block (the coverage sentence of #104, unchanged; where the choices are saved; when the career pages were last read), then the links. Stacked and centered on a phone, text left and links right from 768 px; `--muted` text on the page background as before, in light and dark. The "Coverage gaps" link keeps its 44 px box.
- Where the choices are saved (`#saveNote`, `savedHere` and `savedThere` in `templates/dashboard.js`): signed out, 'Your stars, "Not for me" choices and filters stay in this browser and are sent nowhere.' (was "Stars and discards are saved in this browser only."); signed in, 'Your stars, "Not for me" choices and filters are saved to your account and follow you to your other devices.' (was "Stars and discards are saved to your account."). Both name the choices as PRIVACY.md and the card buttons do, and claim no more than PRIVACY.md says.
- The date reads "Career pages last read 15 Nov, 20:28 UTC." (was "Data updated 2026-11-15 20:28 UTC."): a short month name, no leading zero, the year only when it is not the current year, an unrecognised value shown as it is. The `time#upd` hook and its `datetime` attribute are unchanged. It is the time of the latest crawl, which is what the build records.
- The footer links stay Privacy, Terms and Contact: `tests/test_legal_wording.py` pins the dashboard to link only the privacy and terms pages, so "Security" is not added.

### Removed

- The TODO item and its screenshot.

## [0.43.0] - 2026-11-16

### Changed

- The editorial changes the lawyer approved on 2026-11-16 (the technical writer's review, 26 items) are applied to the legal pages, the emails and the screens. Item 16 and item 19 use his own wording; item 22 (the postal address) needs no change.
- The confirmation email now says to open the link and press "Confirm my email" (the link only opens the page; nothing is confirmed until the button is pressed), adds "The link works for 7 days.", and says no emails are sent until the button is pressed. The "Job alerts" dialog, the confirm page and the `confirm-email` 405 message name the same button.
- The job alert email footer drops "immediately" from the deletion sentence and says the listings may not be "accurate, complete or still open", the same words as `TERMS.md`. `supabase/email-texts.md`, `handler.ts` and `send_digests.py` stay identical line for line.
- The sign-in dialog names the daily backup copy at GitHub (United States) and no longer mixes the database with cookies. The delete-account dialog says a copy remains in the daily backup for up to 30 days and speaks of "job alert settings and the record of your consent". The address hint says every address, the Google one included, gets a confirmation email first. The job alerts dialog drops the intro sentence that repeated the consent line, and says the filters saved for the email were deleted, not the dashboard filters, when the email is turned off. "Privacy Notice" is "privacy notice" in running text.
- The "link not valid" page names the real causes (a link older than 7 days, a replaced link, job alerts turned off). The confirmed page says the first email includes only roles Misra finds from now on. The unsubscribed page says what was deleted and that the consent record is kept.
- `PRIVACY.md`: the transfer sentence names the GitHub backup; the data processing agreement sentence names Supabase, Resend and GitHub; the exception for the spam counter is stated; the spam counter bullet is shorter; the Supabase backup sentence is replaced by "Supabase's free plan keeps no backups for us."; "account ID" is explained; "we" and "the maintainer" are defined; "Not for me" choices replace "discards"; technical terms come with plain words first; the provider log list names all five small programs; the maintainer's own email is "the daily summary email"; the last line links "the security note".
- `TERMS.md`: "hobby" is dropped and a "Job alerts" bullet is added. `SECURITY.md`: the database list is in the present tense and names the jobs already emailed; `UNSUBSCRIBE_SECRET` is listed and is part of breach step 1.
- `CONTRIBUTING.md` (one real subscriber, the maintainer; signup is open) and `PRD.md` ("career page link") are corrected.
- The Edge Functions `email-settings` and `confirm-email` must be redeployed to send the new confirmation email and the new 405 message.

## [0.41.0] - 2026-11-16

### Changed

- The lawyer's round of 2026-11-16 is applied: the email feature has one name, "job alerts". The dashboard button and dialog title read "Job alerts" (was "Daily email"). The confirmation email says "turned on {{schedule}} job alerts" and "your first email will include", the digest footer says "this {{schedule}} email", and the confirm page, the confirmed page title, the unsubscribed page, the note after sign-up and the delete-account dialog use "job alerts" instead of "digest" (the rest of the delete-account dialog is unchanged). The confirmed page is in the present tense ("You now get a daily email ...", "Your first email includes ..."). The consent helper reads "We first send a confirmation link to ...". `PRIVACY.md` names the feature "job alerts" in the same places; the subjects, the consent line, the addresses, the links and the headers are unchanged. `supabase/email-texts.md`, `handler.ts` and `send_digests.py` stay identical line for line. The Edge Function `email-settings` must be redeployed to send the new confirmation email.

## [0.40.0] - 2026-11-16

### Changed

- The Coverage gaps card no longer shows the eight internal reasons as its structure. Opened, it holds two plain lists: "We cannot read these yet" (every company we cannot fully crawl) and "We choose not to read these" (the companies whose `robots.txt` does not allow automated access, with the note that their roles are kept for up to 7 days and then closed, the number from `ROBOTS_CLOSE_DAYS`). Each company is a small card with its name as a link to the careers page, in alphabetical order; a skipped company also says how long it has been skipped. The reasons stay in the data (`gaps.groups`, the CSV, `REASONS`) and on each card as `data-reason`; only the page stops showing them. The part sections carry `data-part` and `data-count`.
- "Report a site" and its sentence now sit under the list, outside the `details`, so they stay in view while the card is collapsed. The card is `section#gapsCard`; the `details` inside it is still `#gaps` (the footer link `a.foot-gaps` opens it). With no company on the card, only the report row shows.
- `gaps.closeDays` (the `ROBOTS_CLOSE_DAYS` number) is added to the page data.

### Added

- Tests: the two parts and the absence of the internal reasons, the plain robots.txt wording and the 7-day sentence, the report button visible with the card closed at 320, 375, 393 and 1650 px in both themes, and the cards as 44 px targets on a phone.

### Removed

- The two TODO screenshots of the old card and the TODO item.

## [0.39.0] - 2026-11-16

### Changed

- Copy pass by the technical writer agent over the dashboard, the report and email dialogs, the error messages, the docs and the design-system showcase: no "Please" in errors (what happened, then what to do), "career page" and "open roles" everywhere users read them, "maintainer" for the person who gets reports, "service" for the backend, a next step in every error, and docs lists that match the real labels and WebMCP tools. The `REASONS` label for `no_careers_url` now reads "No career page found" (key unchanged). The lawyer-approved texts (`PRIVACY.md`, `TERMS.md`, `SECURITY.md`, `supabase/email-texts.md`, the sign-in dialog, the Daily email consent, the delete-account warning, the report privacy line, the email landing states) are unchanged. The import box button reads "Not now". The stickers New, Old? and No longer listed are sentence case in the markup and shown uppercase by `text-transform` in `sticker.css`, so screen readers read words, not letters. The `report-site`, `confirm-email`, `delete-account`, `email-settings` and `unsubscribe` function messages changed: redeploy them to show.

## [0.38.0] - 2026-11-16

### Added

- The dashboard says how many companies it covers: the companies with a working feed (status `ok` in `data/companies.json`; a few of them can be failing today and are then also on the coverage card, so the page never adds the two counts). The count is computed by `build_dashboard.py` at build time (`covered` in `jobs.json` and in the page data), never typed. It shows as the search placeholder ("Search N companies", with the same fact in the field's screen-reader label), so the first screen gains no height at 320 px or on a desktop, and in a footer sentence with the number of companies on the coverage card next to it and a "Coverage gaps" link (a 44 px target) that opens the card. Agents read `data-companies-covered` on the footer sentence. The placeholder no longer lists "roles, companies, cities"; the label still names title, company or city. The digest email is unchanged. README and `llms.txt` say where the live count is, without a number.

## [0.37.0] - 2026-11-16

### Added

- The `comeet_page` adapter reads a WordPress careers page that shows the Comeet plugin's jobs, drawn on the server, for companies whose Comeet account answers 400 so the `comeet` adapter cannot read them. The slug is the page address. A job is a link `/co/<place>/<uid>/<title>` (the uid is the job id, with two parts for a job in a second place); the title comes from a heading or from the plugin's `comeet-position-name`, the department from a `department` or `position` span, the place from a `location` span, else from the place in the link. The plugin's commented copy of each link is ignored. It returns `None` for a failed request, a redirect to another host, a card with no title, a job link that no card yielded (a changed layout) and a page with no job and no "don't have any open positions" notice of the plugin; that notice gives `[]`.
- Pinned on it, `israel_only` (they list worldwide jobs): REE Automotive, Gauzy, Guesty and Apester.
- Tests: the plugin cards of ree.auto and gauzy.com, the Next.js cards of guesty.com, the notice of apester.com (fixtures trimmed from the real pages), and each case that must return `None`.

### Changed

- `data/unresolved_candidates.csv` loses the four rows; the notes of SQream (its one job card names no place) and Kornit Digital (its own theme, links `/cmcareer/`) say why they are not on the adapter.

## [0.36.0] - 2026-11-16

### Changed

- The dashboard is built phone first. The search is the first control under the headline at every width, with a longer placeholder ("Search roles, companies, cities"). On a phone (under 561 px wide, or under 500 px tall) a "Filters" button, with the number of active filters ("Filters (2)": a group of values counts once, the search words do not), opens the filters as a bottom sheet; the level chips and "New only" stay in view as quick filters, and the sheet has "Close", "Reset filters" and "Show N roles". The sheet is one native `<dialog>` (new `design-system/components/sheet.css`): a panel in the page on a wide screen, `showModal()` on a phone, so the browser traps focus, closes it on Esc and the focus returns to "Filters". The headline is one line on a phone, and a card there shows the company, the title and Apply before its tags. "Hiring now" is one line at every width (as many circles as fit) with a "Show all N" / "Show fewer" button at its end (`aria-expanded`, `aria-controls`, 44 px, focus stays on it) that opens the rest in a wrapping area below; the sideways scroller is gone, and the choice lasts for the page session only.
- A notice has the border, radius and shadow of a card (`.notice` uses `--radius-card` and `--shadow-md`; none inside a dialog or a sheet). The "account deleted" message, `.sync-note`, the import offer and the load error now match the cards.

- The sign-in notice (the two approved paragraphs, unchanged word for word, with the Terms and Privacy Notice links) is no longer under the header: pressing "Sign in" opens a small native dialog with the same text, "Continue with Google" and "Cancel". The Google redirect starts only from "Continue with Google"; Cancel and Esc leave the page signed out and return the focus to "Sign in". The header is the logo, "Sign in" and the theme button. `PRD.md` and `PLAN.md` say the notice is shown when the button is pressed. The consent text has moved, its wording has not: tell the lawyer.

### Fixed

- The whole page scrolled sideways (seen at 1650 px, 87 px too wide): the hidden count in each "Hiring now" circle (`.sr-only`, absolute) had no positioned parent. `.story` is now `position: relative`.

### Added

- `tests/test_phone_layout.py`: no horizontal page scroll at 320, 375, 393, 430, 768 and 1650 px in both themes (and landscape, with the sheet open), every control a 44 px target on a phone and on a desktop, the search on the first screen at 375 px, the first role on the first screen of a 393x852 iPhone, "Hiring now" is one line with the rest behind the button at 320, 393 and 1650 px, the count on "Filters", the sheet by keyboard (focus trap, Esc, focus back), and a notice and a card sharing border, radius and shadow.
- The five finished items (and their screenshots) are gone from `TODO.md`.

## [0.35.0] - 2026-11-15

### Changed

- The Misra logo mark has a lime disc and a violet spark (it was the other way round), which matches the colors of the app. The ink pulse line and ring are unchanged: ink on lime is about 16:1, the violet spark on the lime disc is 4.6:1 and the violet spark on the dark page is 3.5:1 (the ink outline of the spark is 3.6:1 against violet), so no token other than `--mark-disc` and `--mark-spark` changed (they swap values; the dark theme does not redefine them). `design-system/assets/mark.svg`, the new `design-system/assets/apple-touch-icon.png` (180 px, white background, rendered from the SVG) and the design-system README follow.

### Fixed

- `docs/favicon.svg` and `docs/apple-touch-icon.png` were committed by hand and no build produced them, so a logo change could not reach the site through `publish-docs`. `build_dashboard.py` now copies them from `design-system/assets/` (`ICON_FILES`), and the daily workflow stages them. New tests pin the lime disc and violet spark in the tokens, the SVG and the header styles, and check that the built icons are the sources.

## [0.34.0] - 2026-11-15

### Changed

- The maintainer digest email (`send_email.py`, built by `digest.py`) no longer has the "Not crawled properly (N)" section: it holds the new Israel jobs only. It is no longer sent on a day with no new jobs, so the "Job crawl alert: N new" email (a day with only new crawl problems) is gone too. `render` lost its `gaps` argument and the unused `GAP_LABEL` and `GAP_NOTE_MAX` are removed.
- Crawl problems are not silent: the digest step prints one line to the job log, counts and company names only (for example `coverage gaps: 2 (1 new): failing 1 (Acme); held 1 (Beta)`), and marks the gaps as logged, so "new" still means the first day; a new gap is printed as a `::warning::` annotation of the run. The detection (crawl guards, closing rules, the dashboard's coverage gap card) and the failure alert of `send_alert.py` are unchanged; a broken coverage check still turns the step red.
- Tests: the digest never contains the section even with failing, held and believed-drop companies in the crawl log; the log line; a gap-only day sends nothing; the subscriber digest already had its own test that it has no coverage content.

## [0.33.0] - 2026-11-15

### Removed

- The "This page is built from ... (source and full history on GitHub)" line at the bottom of the generated `privacy.html`, `terms.html` and `security.html`, and everything that only existed for it: the `__SOURCE__`, `__BLOB_URL__` and `__HISTORY_URL__` placeholders, the `blob_url` and `history_url` properties of `LegalPage`, and the `.legal-source` styles. The footer navigation (Privacy, Terms, Security) stays and the Markdown files stay the source of truth. A test checks the built pages carry no "built from" line and no link to the GitHub blob or commits URLs.

## [0.32.0] - 2026-11-15

### Added

- Every site on the coverage gap card is now a link, so a visitor who wants those roles has somewhere to go. Each site in the `gaps` data of `jobs.json` has a `url`: the careers page for a site that blocks automated access, is skipped for robots.txt, has no open roles, needs a reader, has no feed or is failing; the company's own site (`https://<domain>`) for a company with no careers page. A company with no careers address on record gets the public board of its ATS (Ashby, Comeet, Greenhouse, Workable, SmartRecruiters, BambooHR, JazzHR), else its own site. Only `https:` addresses are ever linked; the link opens in a new tab with `rel="noopener noreferrer"` and says so to screen readers, has a visible focus ring and a 44px target.
- `data/unresolved_candidates.csv` has a `careers_url` column (CRLF line endings kept), filled from the discovery results; empty only for the rows with no careers page. `CONTRIBUTING.md` says so.
- Tests: a site without an address fails (against the real data, the CSV and `companies.json`), the reason-to-link table, the address helpers, and browser tests that the links render, are reached with Tab, show the focus and open safely.

## [0.31.0] - 2026-11-15

### Changed

- The daily-email signup is on (PLAN.md step 6.6, last part): `DIGEST_SIGNUP_ENABLED = True` in `build_dashboard.py`, so the next build of `docs/` carries the "Daily email" button, the dialog with "By turning this on you agree to the Terms and the Privacy Notice" (visible, with big links), `digest.js` (already in the staging list of `daily.yml`) and its integrity hash; the page still has no inline script or style and the same Content-Security-Policy. The function secret `DIGEST_SIGNUP_ENABLED=true` is set separately after the page is deployed. The legacy maintainer digest (`send_email.py`, `LEGACY_MAINTAINER_DIGEST`) is untouched.
- `PRIVACY.md` now describes a live digest, in the present tense: the lawyer's sentences for "Why we collect it" and "Resend", and the "Email settings" passage (what is stored, what an unsubscribe keeps and deletes, the account id inside the unsubscribe link). The short version says the email settings are kept too. "Not live", "planned" and "when it is switched on" wording is gone.
- The spam-guard wording is the lawyer's final form: the report form and `PRIVACY.md` say the hash of your network address "is deleted in the next daily cleanup" (no number of hours), and the confirmation-email counters are described the same way.
- `restore_accounts.py`: without `--create-auth-users` (the live project) every insert is filtered by the database itself with `where exists (select 1 from auth.users ...)`, so an account deleted after the snapshot is skipped in every table and never brought back, with no emails involved. `--create-auth-users` is for a fresh project only. `CONTRIBUTING.md` documents it, the clone-deletion rule and a second rehearsal (a deleted user skipped, the rest restored).
- `SECURITY.md`: `BACKUP_DEPLOY_KEY` is scoped to `ravidorr/misra-backups` only and has no expiry, so it is rotated by hand at the yearly review (first by 2027-11-15); a local clone of the private backup repository (and any restore script) is deleted right after use.
- The tests build the page with the switch off where they use stand-in backends that know nothing of the `subscriptions` table; the page with the switch on has its own tests (`digest_site`, and a build of the repository as it is). The guard in `tests/test_legal_wording.py` now requires the present-tense wording while the switch is on.

### Added

- Migration `20261120100000_confirm_counters_cleanup.sql` (not applied by the PR; apply it before the first daily run that includes this change, with `20261119100000` if that one is not applied yet): `confirm_email_cleanup(p_day date)` deletes every row of a day before `p_day` of `confirm_limits` and `confirm_daily_cap`, with the rules of `report_site_cleanup`. `sync_supabase.py` calls both cleanups after a good sync and logs the sum. SQL tests in `supabase/tests/digest_signup.sql`, lint in `tests/test_migrations*.py`.

## [0.30.0] - 2026-11-15

### Added

- A daily backup of the account tables (PLAN.md step 9.1, restore path), because Supabase Free has no backups. `export_accounts.py` reads `profiles`, `user_prefs`, `user_jobs`, `subscriptions` and `emailed` (all columns, token hashes included) with the service-role key and writes one sorted JSON file per table, counts only on screen, non-zero exit when a table cannot be read. `publish_backup.py` copies the files to the PRIVATE repository `ravidorr/misra-backups` and commits only when something changed; when the history is 28 days old it replaces it with one new commit (`--force-with-lease`), so a deleted account leaves the backups within 30 days. `restore_accounts.py` turns the files into one idempotent SQL script (foreign-key order, never deletes, `--create-auth-users` for a new project).
- The workflow step `backup` in `daily.yml`, after the deploy push and on every run, with `BACKUP_DEPLOY_KEY` and the service-role key on that step only, written outside the checkout, and a failure shows as `backup=` in the maintainer alert. `scripts/backup-key.sh` handles the key like `deploy-key.sh` (unique key file, `IdentitiesOnly`, removed at the end).
- `CONTRIBUTING.md`, "Backups of the account tables": what is exported, the secrets, failure, retention, the restore steps and the restore rehearsal of 2026-11-15 against a local Postgres. `tests/test_export_accounts.py`, `tests/test_restore_accounts.py`, `tests/test_publish_backup.py` (real temporary git repositories) and `tests/test_backup_workflow.py`.
- `TERMS.md`: a short as-is disclaimer (free, non-commercial hobby project, no warranties, listings collected automatically from public pages and not guaranteed, trademarks belong to their employers, signing in accepts the privacy notice including processing in the EU and the US, employers write to `raanan@avidor.org` to have their links removed). Linked from the page footer, the sign-in notice, `PRIVACY.md`, `README.md` and `llms.txt`.
- The dashboard footer links to the privacy notice, the terms and the contact address (44px targets, new-tab links say so).
- The notice next to the sign-in button (PLAN.md step 6.6, FR-1 AC 7): signing in is optional and what is stored is stated, with links to the terms and the privacy notice. It shows only while signed out and the button names it with `aria-describedby`.
- `tests/test_legal_wording.py` and tests of the notice and the footer links in `tests/test_sign_in.py`.

### Changed

- Privacy wording, to be shown to the lawyer (PLAN.md step 9.1, "send to lawyer"): `PRIVACY.md` says a daily backup copy of the account data is kept in a private GitHub repository (United States, access only the maintainer) for up to 30 days, that a deleted account leaves the backups within that time, and that GitHub may keep replaced copies briefly. The short version, "What we store", "Who can see it", "Providers", "How long we keep it" and "How to delete your data" changed; `tests/test_legal_wording.py` pins the wording to the numbers in `publish_backup.py`.
- `SECURITY.md`: the known gap "Supabase's free plan has no backups" is now the daily export with a restore rehearsed on 2026-11-15; `BACKUP_DEPLOY_KEY` and the private repository are in the controls, the yearly review and the breach steps.
- The lawyer's microcopy on three live touchpoints: the sign-in notice (two short paragraphs: sync across devices, signing in is optional, only the Google email address and job preferences are stored in the EU database, never a CV, password or tracking cookies, with links to the Terms and the Privacy Notice, plus one clause that Supabase's own sign-in record also holds the Google name and photo until the account is deleted, as `PRIVACY.md` says); the "Delete my account" confirmation dialog ("Delete your Misra account?", names the signed-in email address, shown as text, with "Cancel" and "Delete my account permanently"); and one line above Send in the "Report a site" form (reports go to the maintainer, the spam guard keeps a daily non-reversible hash of the network address, useless after that day and deleted when later reports are handled), with a Privacy Notice link.
- `PRIVACY.md` names the database controller (Raanan Avidor, a private individual) and the contact `raanan@avidor.org`, says why the data is collected and that giving it is voluntary, names the providers and where they are (Supabase and Resend in Ireland, Google and GitHub in the United States), gives the rights to inspect, correct and delete (Sections 13 and 14 of the Israeli Privacy Protection Law) with an answer within 30 days, and states that Supabase's and Resend's standard data processing agreements apply. The digest is still described as planned, not live.
- `PLAN.md` step 6.3 and the PRD (FR-8) carry the lawyer's exact footer text for every digest email and the Reply-To requirement.

## [0.29.0] - 2026-11-15

### Added

- A daily cleanup of the `report-site` spam-guard counters. Migration `20261119100000_report_site_cleanup.sql` adds `report_site_cleanup(p_day date)` (service role only): it deletes every row of both counter tables from a day before `p_day`, returns how many it deleted and refuses no day or a day later than tomorrow. `sync_supabase.py` calls it right after a good sync (`cleanup`, in the sync step that already holds the service-role key) and logs the count; a failed call exits non-zero like a failed sync, so the privacy promise cannot silently stop being kept. The migration is not applied by this PR: apply it before the first daily run that includes it, otherwise the sync step fails on the missing function (`supabase/README.md`, "Daily cleanup of the counters").
- The SQL policy tests run in CI: a separate job `sql-tests` in `ci.yml` (the `quality` check is unchanged) starts a `postgres:17-alpine` service container and runs `scripts/run-sql-tests.sh`, which loads the committed stub of Supabase's roles and `auth` schema (`supabase/ci/supabase_stub.sql`), applies every migration in order and runs every file of `supabase/tests/`, stopping at the first error. `SECURITY.md` no longer lists the missing CI run as a known gap, and `supabase/README.md` describes the job and the local run.
- `supabase/tests/report_limits.sql` tests the cleanup; `tests/test_sql_ci.py`, `tests/test_sync_supabase.py`, `tests/test_migrations.py` and `tests/test_legal_wording.py` pin the job, the call, the function and the wording.

### Changed

- The wording on the spam guard is the lawyer's shorter form, now that a daily cleanup keeps the promise: the "Report a site" form says "To prevent spam, we store a daily non-reversible hash of your network address that expires after 24 hours." and `PRIVACY.md` carries the same sentence, then says what is kept and that a cleanup that runs every day deletes the counters of every earlier day. The old "deleted when later reports are handled" is gone from both.
- The weekly choice of the Daily email dialog reads "Weekly (Sundays)" (the lawyer's suggestion; the digest sends weekly summaries on Sundays).
- The coverage card says what is true about a company skipped for `robots.txt` in both phases, with how long it has been skipped (counted from the first skip of the current run to the latest crawl): for fewer than 7 days "its roles are kept for up to 7 days and then closed", after that "its roles were closed after 7 days". It no longer says "its roles may be old" for these companies.

## [0.28.0] - 2026-11-15

### Added

- The daily workflow sends the subscriber digests (PLAN.md step 6.4, PRD FR-8 AC 10): a step that runs `send_digests.py` after the Supabase sync and before the build and the deploy push, only when the sync succeeded. `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY` and `UNSUBSCRIBE_SECRET` are exposed to that step only. A failure of the step fails the run and sends the maintainer failure alert (the alert's step list now shows `digests=`), while the build and the data commit still run. The manual-run input `digest_dry_run` passes `--dry-run`.
- `tests/test_daily_workflow.py`: step order, which step holds which secret, the dry-run input, the alert and the legacy switch.
- `CONTRIBUTING.md`: "The daily digest" (secrets, failure, dry run, how to retire the maintainer's own digest), "Resend volume" (free limits checked against Resend's pages, the arithmetic, the upgrade trigger of about 60 daily subscribers, the subscriber count query) and "Monthly success metrics" (SQL for sign-ins, 30-day returns, subscribers, share starring, region and description shares, and how to count accepted sites). PLAN.md step 6.5 and the success-metrics check are done.

### Changed

- The maintainer's single-recipient digest (`send_email.py`) now sits behind the workflow constant `LEGACY_MAINTAINER_DIGEST: "true"` and keeps running. The maintainer flips it to `"false"` and deletes `send_email.py` and the old selection logic of `digest.py` after receiving a digest as a subscriber (PLAN.md step 6.4, `CONTRIBUTING.md`); nothing was removed in this version.
- Dates of the 0.20.0 to 0.23.0 changelog entries corrected to 2026-11-15 (they were in the future).

## [0.27.0] - 2026-11-15

### Added

- `send_digests.py` (PLAN.md step 6.3, PRD FR-8 AC 4 to 9 and 12, FR-9 AC 9): the personalised digest for every confirmed, not unsubscribed subscriber. It reads subscribers and jobs from Supabase with the service-role key and, for each subscriber whose digest is due (daily every day, weekly on Sundays), takes the open jobs in Israel first seen after they confirmed, never emailed to them before and not the silent baseline of a newly added company, that fit their saved regions, levels, role types and keywords by the dashboard's rules. The levels and role types are the separate `level` and `role_types` columns, so Product with Intern and Junior gives only the entry-level Product roles. No email when nothing is new. One plain-text email through Resend from `Misra <digest@send.avidor.org>`, `Reply-To: raanan@avidor.org`, the RFC 8058 `List-Unsubscribe` and `List-Unsubscribe-Post` headers, and the lawyer's footer; jobs are recorded in `emailed` only after Resend accepts the email. Each user is its own try block (the run exits 1 after all were handled when any failed), sends are spaced for Resend's rate limit and carry an `Idempotency-Key` (a same-day re-run after a failed record does not repeat an email), and the log holds counts only. It is not in the daily workflow yet (step 6.4) and nothing sends while the signup switches are off. `--dry-run` sends and records nothing.
- `supabase/email-texts.md` gained the digest subject and the list of roles, and `tests/test_send_digests.py` pins the footer, the headers, the sender and the list against it.

### Changed

- The unsubscribe token is now stateless: `<subscription id>.<HMAC-SHA256(UNSUBSCRIBE_SECRET, id)>`. The `unsubscribe` function recomputes the MAC (constant-time compare) and asks the database to unsubscribe that id with the existing `unsubscribe_user`, so no migration is needed. It needs the new function secret `UNSUBSCRIBE_SECRET` (and the same value as an Actions secret for the digest step). `email-settings` no longer makes an unsubscribe token; the `NOT NULL` hash column gets a random value nothing reads. HEAD still never changes anything, and GET and POST unsubscribe at once. The link and the `List-Unsubscribe` header read `?t=`.
- `PRIVACY.md` says only the hash of the confirmation link is stored and that the unsubscribe link is made again for each email from the account number and a secret key.

## [0.26.0] - 2026-11-15

### Changed

- `api.smartrecruiters.com` is no longer crawled. Its `robots.txt` is `User-agent: LinkedInBot` / `Allow: /v1/companies/`, then `User-agent: *` / `Disallow: /`, its documentation says nothing about aggregators, and permission has been asked. `ROBOTS_API_EXCEPTION_HOSTS` in `common.py` is now empty (the mechanism and its docs stay; a written permission is the only reason to add a host back) and every API host goes through the robots gate. Armis, Kaltura, Atera, Nexar and ServiceNow are skipped, not counted as failures (no retry, no alert email) and appear on the coverage gap card with the reason "robots.txt does not allow it".
- A company skipped because of `robots.txt` on every run for 7 days in a row (`ROBOTS_CLOSE_DAYS`) has its open jobs closed, by the close the run already uses, so the Supabase sync marks them closed and a later crawl reopens them. This replaces the known limit that a skipped company kept its old jobs open forever. The first skipped day is kept in the new table `robots_skips(company, since)` in `data/jobs.db` (`CREATE TABLE IF NOT EXISTS`, so older databases are upgraded in place); any run in which the company is not skipped clears it, and it never applies to failures, excluded companies or a company crawled in the same run. The run logs counts only. The roughly 39 open jobs of the five SmartRecruiters companies close seven days after the first skipped run.

## [0.25.1] - 2026-11-15

### Fixed

- With the daily email signup switched off, the built `docs/index.html` kept a blank, indented line where the "Daily email" button was removed, and `html-validate` (CI lint) rejected it. The build now removes the whole line.

## [0.25.0] - 2026-11-15

### Added

- The daily email settings (PLAN.md step 6.2, PRD FR-8 AC 1 to 3), built and switched off. A signed-in user gets a "Daily email" button beside "Sign out" that opens a dialog with the state (not on, waiting for you to confirm, on, unsubscribed), the address (the Google email by default, or one they type), daily (the default) or weekly, the filters the email will use, and the actions turn on, send the confirmation again, change the address, and turn off. "Use these filters for my email" copies exactly regions, levels, role types and keywords from the dashboard at that moment; later changes to the page's filters, a quick search included, change nothing until it is pressed again. Every failure is explained in plain words (rate limits included) and never called a success, and the page reads the user's own row again after every call, so it shows what the database holds. No WebMCP tool can turn the email on or send mail; agents can read `data-digest-state` and `data-digest-frequency`.
- Edge Function `email-settings` (JWT verified by the gateway; the user id and the default address come only from Supabase Auth's answer about the token). Turning on stores the address, frequency and filters, sets `consent_given_at`, clears `consent_confirmed_at` and `unsubscribed_at`, makes both tokens (only their hashes are stored, the confirmation one expires in 7 days) and sends one plain-text confirmation email through Resend (`Reply-To: raanan@avidor.org`, the lawyer's exact wording) with the link `email.html#token=<token>`; the token is never logged or returned. Turning off does exactly what the unsubscribe link does: it calls the step 6.1 SQL function, so the address and consent times are kept and the filters, frequency, confirmation token and emailed list are cleared.
- Abuse limits for the confirmation email, counted in the database with keyed daily hashes (no id or address is stored): 3 per user, 3 per address and 30 in all per day (constants in `handler.ts`, with the Resend plan in a comment). A refused request uses nothing, and a place is given back only when Resend definitely refused (a 4xx), never after a timeout or a 5xx.
- Two switches, both off: `DIGEST_SIGNUP_ENABLED = False` in `build_dashboard.py` (the page has no button, dialog or script, and `docs/digest.js` is not built) and the function secret `DIGEST_SIGNUP_ENABLED`, without which the function refuses to create a subscription (403 `signup_closed`), so calling the API directly cannot get around the page. Turning off always works.
- Migration `20261118100000_digest_signup_and_limits.sql`: the counter tables `confirm_limits` and `confirm_daily_cap` and the service-role-only functions `subscribe_start`, `unsubscribe_user`, `confirm_email_reserve` and `confirm_email_release`. `supabase/tests/digest_signup.sql` tests them against a database, including that both ways of unsubscribing leave identical rows. The lint of `tests/test_migrations.py` knows the new tables and functions.
- The lawyer's wording for the control (shown only when the flag is on): the label "Email me new matching roles", "Daily" and "Weekly", the helper text "We will first send a confirmation link to <address>. Nothing is sent until you confirm, and you get an email only when there are new matches. One-click unsubscribe is in every email. By turning this on you agree to the Terms and the Privacy Notice." (links to `TERMS.md` and `PRIVACY.md`, 44px targets, new-tab links say so), and after turning on "Check <address> for a confirmation link. Your <daily or weekly> digest will start only after you click it." The confirmation email is the lawyer's text verbatim, subject "Confirm your Misra job alerts".
- `templates/digest.js`, `supabase/functions/email-settings/`, `supabase/functions/_shared/identity.ts`, `test/email-settings.test.ts` and `tests/test_digest_settings.py` (real browser, every state and error, light and dark, phone width, axe, keyboard). The daily workflow stages `docs/digest.js` once it exists.

### Changed

- The Terms and Privacy Notice links in the Daily email dialog and in the confirmation email point to the site's `terms.html` and `privacy.html` (same tab in the dialog), the same pages the rest of the site links to.
- `PRIVACY.md` says the email settings are built but switched off, what turning the email on stores, that one confirmation email is sent and nothing else until it is confirmed, the confirmation-email counters, and Resend's part.
- `auth.js` also shares the signed-in email with the page scripts, and `dashboard.js` exposes a read-only copy of the four email filters.

## [0.24.0] - 2026-11-15

### Added

- The privacy notice, the terms and the security note are pages of the site: `docs/privacy.html`, `docs/terms.html` and `docs/security.html`, built by `build_dashboard.py` from `PRIVACY.md`, `TERMS.md` and `SECURITY.md` with `templates/legal.html` (markdown-it-py, pinned `==4.0.0`, a normal dependency). The Markdown stays the source of truth: raw HTML in it is escaped, a heading gets the id GitHub gives it, a relative link to another legal document becomes a link to its page, a link to any other file becomes a link to it on GitHub, and an image or a link out of the repository stops the build. Each page says it is built from the file and links to the file and its history on GitHub. Prose styles in `templates/dashboard.css`.
- Sync tests (`tests/test_legal_pages.py`): every heading, link target, paragraph and the date line of each document must be on its page, read back with a parser of its own; plus tests of the converter, the template (same Content-Security-Policy as the email page, no inline script or style), the workflows, and axe in light and dark at phone and desktop width.
- `.github/workflows/publish-docs.yml`: rebuilds `docs/` and pushes it with the deploy key when `PRIVACY.md`, `TERMS.md`, `SECURITY.md`, `templates/`, `design-system/`, `vendor/` or `build_dashboard.py` change on `main`, so an edit to a legal document is on the site in minutes and not at the next daily run. It runs only `build_dashboard.py`, shares the daily workflow's concurrency group, and cannot start itself (its commit changes `docs/` only).
- `CONTRIBUTING.md`: how the legal pages are built.

### Changed

- The sign-in notice, the footer, the report-form line and the delete dialog link to `privacy.html` and `terms.html` on the site (same tab), not to the Markdown files on GitHub. The email footers in `supabase/email-texts.md`, `PRD.md` and `PLAN.md`, and `llms.txt`, use the site URLs.
- The daily workflow stages the three new pages and installs the pinned `markdown-it-py`.
- "Data updated" on the dashboard is the time of the latest crawl (it was the time of the build), so a rebuild over the same data writes the same files.

## [0.23.0] - 2026-11-15

### Added

- Crawler politeness, from the lawyer's answers (PLAN.md step 6.6). One honest User-Agent, `Misra/<version> (+https://ravidorr.github.io/misra/; raanan@avidor.org)`, from the single constant `USER_AGENT` in `common.py`, used by the adapters, the browser crawl, `discover.py` and the survey tools (a test fails when another string appears, and when `VERSION` differs from `package.json` and `pyproject.toml`). Nothing relied on the old `IL-JobWatch/0.1 (personal job search; low-volume)`.
- The browser crawl now waits for its turn on a host like the adapters do (one request a second, from the same `reserve_slot` in `common.py`), before each page load and each pagination click, with the same cap of 3 pages at once.
- A `robots.txt` check. Each host's `robots.txt` is fetched once per run (10 s timeout; a failed fetch, a 404 or any other 4xx, and a 5xx all mean allowed, and a failure or a 5xx is logged), read with a small RFC 9309 parser (our `Misra` group, else `*`; longest rule wins, Allow wins a tie; `*` and `$`), and a disallowed address is not fetched. An ATS's public API (Greenhouse, Lever, SmartRecruiters, Workable, Workday, Oracle, Phenom) is not treated as a page: a blanket `Disallow: /` does not stop it, a rule that names its path does. The switch is `ROBOTS_ENFORCED` in `common.py`; it ships on, and each run logs whether it is. A read-only audit of every crawl host found nothing blocked (see CONTRIBUTING.md).
- A company skipped because of `robots.txt` is not a failure: no retry, not in the failure count, no maintainer alert. Its crawl log note is "robots.txt does not allow it", it stays out of the failure e-mail, and the coverage gap card lists it at once under the reason "robots.txt does not allow it".
- The exclusion list `data/excluded_companies.csv` (`company`, `reason`, `date_added`; no personal data), read by `load_excluded` in `common.py`. A company on it is never crawled (`run.py`, `discover.py`), never listed on the coverage card or in the exports, and its rows are deleted: `run.py` deletes its jobs, crawl log and gap alerts from `data/jobs.db`, and `sync_supabase.py` deletes its rows in Supabase `jobs`, and the stars and discards (`user_jobs`, which keep a title and a link) and emailed keys (`emailed`) of users, by exact company name. Deleting a company's stars is deliberate: the removal must be complete. No migration: the service role already may delete from those tables. Guards: an empty list does nothing and sends no request; a missing or malformed list (blank or very short name, repeated name, missing reason, bad date, more than 200 rows) stops the run; more than 3 companies with data at once are refused before anything is deleted (add them in separate runs); counts are logged, never rows.
- A list that spells a company differently from `companies.json` or the database (case only) stops the run, so skipping and deleting never disagree; the database is vacuumed after a deletion; the sync step fails when the list is not empty and the Supabase secrets are empty; a byte order mark in the list is accepted and an unreadable list is a clear error; a robots skip counts neither as a failure nor as a success in the two-failures rule of the coverage gap card and the failure email.
- `CONTRIBUTING.md`: how to remove a company in minutes and the exact test of success, and the crawler's manners.
- `tests/test_politeness.py`.

### Changed

- The Supabase sync step deletes before it uploads, and never uploads a row of an excluded company.

## [0.22.0] - 2026-11-15

### Added

- Subscriber tables and the confirm and unsubscribe functions (PLAN.md step 6.1, FR-8 AC 7). Migration `20261117100000_subscriptions_and_emailed.sql`: `subscriptions` (one row per user: address, frequency, filters snapshot, the SHA-256 of a confirmation token and of an unsubscribe token, and the consent log `consent_given_at`, `consent_confirmed_at`, `unsubscribed_at`) and `emailed` (user and job key, so a job is never sent twice). Both cascade from `auth.users`, so deleting an account removes the consent log too. A signed-in user may read their own row without the token hashes and change only `frequency` and `filters`; they cannot insert, delete, confirm themselves, change the address or undo an unsubscribe. `emailed` and the two database functions `confirm_subscription` and `unsubscribe_subscription` are for the service role only. A change of address drops the confirmation.
- Edge Function `confirm-email`: a POST with `{token}` confirms; a GET never does (mail scanners fetch links), so the email links to a static page with a Confirm button. A replay says "already confirmed"; an expired link, an unknown token and an unsubscribed subscription get their own clear answers.
- Edge Function `unsubscribe`: one click, no sign-in. A GET unsubscribes and redirects to the static page; a POST answers the RFC 8058 one-click request of a mailbox provider. A repeat is not an error. An unsubscribe stops sending, keeps the address and the consent timestamps as the audit record, and clears the filters snapshot, the frequency, the confirmation token and the emailed list.
- Tokens are 256 random bits, stored only as a SHA-256 hash; the raw token is only in the emailed link. Nothing logs a token, hash, address or user id.
- The static page `email.html` (built from `templates/email.html` and `templates/email.js`, no new stylesheet): the Confirm button, and the landing of the unsubscribe redirect. The daily workflow now stages the two new files, and a test fails when the build writes a file the workflow does not stage.
- `supabase/tests/subscriptions.sql`, `test/confirm-email.test.ts`, `test/unsubscribe.test.ts`, `tests/test_email_page.py` (real browser, light and dark, phone width, axe, keyboard) and the lint of the new migration in `tests/test_migrations_subscriptions.py`.

### Changed

- `PRIVACY.md` describes the consent log, what an unsubscribe keeps and clears, and that deleting the account removes everything.
- `build_dashboard.py` builds a second page and puts integrity hashes into every page.

## [0.21.0] - 2026-11-15

### Added

- Report a site (PLAN.md step 5.2, PRD FR-7). The coverage gap card has a "Report a site" button (`data-report="open"`) that opens a native `dialog` with a short form: the company, the careers page link and why it matters. No account and no personal details are needed, and the call carries no key or token (the `report-site` function is public and checks the page's origin itself). The card now always shows, with the button, even when no company is known to be missing. The form checks the same limits as the function (company 1 to 100 characters, an http(s) link of up to 500 characters with a host name and no user name or password, a reason of 3 to 1,000 characters) with the function's own words, beside each field (`aria-invalid`, `aria-describedby`, the first field to fix gets the focus); the function stays the authority and its per-field answers are shown beside the fields too.
- A sent report shows a confirmation (the focus moves to it) and the next report starts blank; the only success is a 200 with `{ ok: true }`. Every failure says in plain words what happened and whether anything was sent: a refusal of the input, a request the function could not read, a forbidden origin, a report that is too large, a visitor over the daily limit (try again tomorrow), the daily cap (reports are paused until tomorrow), a report that could not be passed on, a server fault, a network failure (most likely nothing was sent), a timeout (it cannot be known whether it arrived) and an answer that is not the function's. The text is kept for a retry. While a request runs the button is busy and a second click, Enter, Cancel and Esc do nothing (a second Esc that closes the dialog anyway is answered by reopening it).
- The honeypot is an input off the screen (`tabindex="-1"`, `autocomplete="off"`, its wrapper `aria-hidden`, not `display: none`) sent as `website`, empty for a person.
- `templates/report.js` (a module with its own integrity hash, built to `docs/report.js`). No WebMCP tool sends a report, because a report is an email to the maintainer and only a person who has read the form may send it; agents find the action and the form through `data-report` hooks (`card`, `open`, `dialog`, `form`, `done`) and `data-state` on the dialog.
- Design system: `field-group`, `field-hint`, `field-error` and `field-trap`, and a multi-line `textarea.field`, shown in the showcase.
- `tests/test_report_site.py` runs the form in a real browser against a fake function: confirmation, every error and each field message, the honeypot, keyboard, focus, busy state, axe in both themes, phone width, and the documents.
- `CONTRIBUTING.md` says how a reported site is accepted (`data/seed_companies.csv`, or `data/unresolved_candidates.csv` with a reason and a note) or declined.

### Changed

- `PRIVACY.md` says where the report form is.
- The daily workflow stages `docs/report.js` with the other generated files, so the page it publishes can load the form.

## [0.20.0] - 2026-11-15

### Added

- Delete my account (PLAN.md step 4.4, FR-1 AC 4). A signed-in user has a "Delete my account" button next to "Sign out". It opens a native `dialog` that says exactly what is removed (the email address, the stars, the "Not for me" choices, the saved filters and settings), that anything not yet saved from this device is dropped, that it cannot be undone, that the roles list and the browser's own signed-out data are not affected, that Google keeps the Google account, and that signing in again makes a new, empty account. The focus starts on "Keep my account"; the focus stays in the dialog, Esc closes it and is ignored while the request runs; the buttons are busy while it waits; errors appear in a `role="alert"` notice and the button reads "Try again". A failure never pretends success: a refusal says the account was not deleted, a timeout or 5xx says it could not be confirmed, and a 401 says nothing was deleted by this request and to sign in again to check. On success the page clears this user's queued changes, unsent filters and first-sign-in answer from the browser (this user's entries only), signs out locally, returns to the signed-out dashboard with a short confirmation and moves the focus to "Sign in".
- Edge Function `delete-account` (`supabase/functions/delete-account/`, plain TypeScript with the secrets and `fetch` injected, tested by vitest, type-checked and linted like `report-site`). POST only, CORS for `https://ravidorr.github.io` only, JSON answers. It asks Supabase Auth who owns the caller's access token (`GET /auth/v1/user`), takes the id from that answer only (a UUID of an `authenticated` user), and deletes that user through the Admin API (`DELETE /auth/v1/admin/users/<id>`, hard delete), so `profiles`, `user_prefs` and `user_jobs` cascade. Nothing in the request can name another user. A rejected token never reaches the admin endpoint; a retry whose first answer was lost (Auth: genuine token, `user_not_found`) and an admin 404 count as done; Auth rate limits, timeouts and unreadable answers are "try again", not "sign in again". Only status codes are logged. `verify_jwt = false` in `supabase/config.toml` (the CORS preflight carries no token and the function has to ask Auth anyway). No migration and no new secret.
- No WebMCP tool deletes an account: that needs a person to confirm. `design-system/components/dialog.css` and `btn-danger` are new, shown in the design-system showcase.
- `test/delete-account.test.ts` (the handler against a fake Supabase Auth) and `tests/test_delete_account.py` (the page in a real browser: dialog, keyboard, busy, failure and retry, queue cleanup, light and dark, phone width, axe).

### Changed

- `PRIVACY.md` is rewritten from what the code and the setup do: what is stored (the email, stars, discards, filters, the spam-guard counters), what is not (a CV, a password, Google's name or photo in our tables, addresses in our tables), that Supabase's own sign-in record and platform logs hold more (Google's name and photo, and for requests to its services the IP address, approximate location, user agent and path, for a short period Supabase sets), who can see it, the providers (Supabase, Google, GitHub, Resend, Gmail) and where they are, cookies and local storage, how long data is kept, how to delete it and how to ask for help.
- `supabase/README.md` has the "Delete account" section with the deploy command and a live check with a throwaway account.

## [0.19.0] - 2026-11-15

### Added

- The browser's own choices are offered to the account at the first sign-in (PLAN.md step 4.3, FR-2 AC 4 and 6). When a user signs in on a browser that holds discards, stars or filters the account does not have, the page shows a notice with a checkbox for the choices and one for the filters, "Bring them over" and "No thanks" (both 44px, light and dark, axe-clean). It waits until both of the account's lists have loaded, so a failed load never leads to an import that cannot tell what is already there, and it is answered once per account and browser (`misra-imported-v1`). Only choices the account has no row for, on jobs that are still open, are offered: a duplicate is never sent, a star or a discard made in the account is never replaced (the browser's discard of a job the account starred leaves the star), and a discard of a closed job is skipped. They are copied, not moved: the browser's own discards, stars and filters stay as they were for when the user is signed out. Stars are brought over too, although the PRD names discards only, because a star made signed out lives on that device alone and would otherwise be stranded.
- The choices go through the existing pending queue (saved in the browser by user id until the service confirms, so an outage, a reload or a sign-out loses nothing), in requests of up to 200 rows that never replace a row the account has (`Prefer: resolution=ignore-duplicates`, so even a choice another device made meanwhile wins); as many as fit under the row limit are queued, the rest are named in the result, and a refusal is reported instead of retried. The result stays on the page until it is closed. "Export discards" and "Import discards" work while signed in too: the export is the account's discards, and a file's open roles the account has no choice about become discards the same way (so this is also the way to bring over discards from another browser later).
- Filters follow the user (FR-2 AC 5). Signed in, the filters (regions, places, levels, role types, search words, company, location scope, sort order, the show or hide options and the page size) are read from the account's `user_prefs` row and saved to it once a change has paused for 800 ms; only the `filters` column is ever written (`email_settings` belongs to step 6.2). The browser's own filters are left alone while signed in and come back at sign-out. The merge rule when the two differ: the account's filters win when the page loads, except for a change made on this device that the account has not confirmed yet (kept in the browser by user id in `misra-pending-filters-v1`), which is saved over them; after that the last save wins for the whole set. If the account's filters cannot be loaded, nothing is saved over them by itself, the page says so and offers "Try again", and only an explicit change is sent. A failed save (offline, session expired, service down) is kept, retried as the choices are (after 2, 5, 15 and then every 60 seconds, when the browser comes back online, and from "Try again") and reported; a refusal is reported and dropped; signing out with a change unsent says so. Values the data no longer has are dropped from what the account holds, and the search words are cut to 200 characters. At the first sign-in, the browser's filters are offered with the discards: if the account has none they are saved, and if it has some the offer says that bringing them replaces those (declining keeps the account's).
- WebMCP tools `get_sync_status` (where choices and filters are saved, whether the filters are in step, how many changes wait, and what the offer holds) and `import_browser_choices` (answers the offer). `#list` carries `data-filters` (`browser`, or `loading`, `paused` while the offer waits, `synced`, `saving`, `later`, `refused`, `failed`); the offer carries `data-discards`, `data-stars` and `data-filters` (`none`, `new`, `replace`).
- `tests/test_account_sync.py` covers all of this in a real browser against fake `user_jobs` and `user_prefs` tables (`PrefsBackend` in `tests/test_dashboard_page.py`).

### Changed

- No migration: `user_prefs` (own-row policies, the 16 KB check on `filters`, the `updated_at` trigger) and the 2,000-row limit already exist. The footer and notes say where the choices are saved. `PRIVACY.md` says the filters are saved to the account and that the first sign-in offers this browser's choices. The page-size control now follows the filters it is restored with.

## [0.18.0] - 2026-11-15

### Added

- Stars and discards that follow the user (PLAN.md step 4.2, FR-2). Every card has a Star toggle next to "Not for me", and a "Starred (n)" switch above the list shows the starred jobs as a list of their own (the filters are put away while it shows, since a closed role has no level or region). A job is starred, discarded or neither, never both: starring a discarded job takes the discard away, and discarding a starred job takes the star away. Signed out, both stay in the browser as discards always did (`il-jobs-starred-v1` beside `il-jobs-discarded-v1`) and nothing is sent anywhere. Signed in, they are saved to the user's `user_jobs` rows with the user's own token (one row per job, a single `state`; a star keeps its title, location and an http(s)-only link so it survives the job closing, a discard keeps nothing but the job's key), and read back when the user signs in on another device.
- A starred job that has closed is still listed, marked "NO LONGER LISTED", with its title, company, location and saved link and no Apply button; its star can be taken off. A discard of a job that is no longer open is not counted or shown.
- No choice is lost and none is shown as saved when it is not. What the user does is shown at once and kept as a pending change, also in the browser (by user id), until the service confirms it. When the network or the service is down, the session expired or a request timed out, the change stays pending, the page says how many changes are not saved to the account yet, the card carries `data-pending="true"`, and the page tries again by itself (after 2, 5, 15 and then every 60 seconds), when the browser comes back online, and from a "Try again" button; pending changes survive a reload and are saved at the next sign-in if the user signs out first. A change the database refuses for good (the row limit, any other refusal) is undone on screen and the page says so instead of retrying for ever. If the account's list cannot be loaded the page says so and offers "Try again" rather than showing an empty account; a list that arrives late never undoes a change confirmed meanwhile. Changes are sent one at a time in the order made, and supabase-js's own retries are off so the state is told at once.
- Migration `20261115180000_job_choices_cap_and_closed_jobs.sql`: a cap of 2,000 `user_jobs` rows per user (a trigger that refuses a new row with error 54000, takes a per-user advisory lock so two requests cannot both pass, and never refuses a change to a job the user already has a row for), and a trigger on `public.jobs` that deletes the discards of a job when it is written as closed by the daily sync (FR-2 AC 10; a job that reopens shows again because nothing hides it; stars stay), a trigger that skips a discard of a job that is not open (so a stale tab or an old offline queue cannot leave one behind), and an index for the close trigger's lookup. A refusal notice now stays until the user acts again, and pending changes are saved and forgotten per job in the browser, so another tab of the account keeps its own. `supabase/tests/job_choices.sql` tests both rules and runs like `accounts_rls.sql`. It must be applied to the project (see `supabase/README.md`).
- WebMCP tools `star_job`, `unstar_job` and `list_starred_jobs`; `discard_job`, `restore_job` and the new ones answer with `saved` (false when the change is only held on the device for now); `get_job` and the job objects carry `starred`. `#list` carries `data-choices` (`browser`, or `loading`, `loaded`, `failed` for the account's list). Card hooks `data-starred`, `data-pending` and, for a closed starred role, `data-listed="false"`.

### Changed

- `templates/auth.js` publishes the signed-in user and the client as `globalThis.misraAccount` and a `misra:account` event, which `dashboard.js` follows (in either start order); nothing else about the sign-in changes. While signed in, "Export discards" and "Import discards" (which are for this browser's discards) are hidden, and the footer says where stars and discards are saved. Importing a discards file signed out now also takes the star off those jobs. The browser's own choices are not offered for import at the first sign-in yet (step 4.3).
- `tests/test_migrations.py` covers the new functions and triggers. `PRIVACY.md` says what is saved for signed-in users. `.card-actions` may wrap onto a second row on a narrow phone.

## [0.17.0] - 2026-11-15

### Added

- Optional Google sign-in and sign-out in the page header (PLAN.md step 4.1, FR-1). A visitor who does not sign in sees the dashboard exactly as before: the sign-in control is hidden until `templates/auth.js` has what it needs, and a blocked or failing sign-in can never stop the roles from loading. There is no password anywhere: "Sign in" sends the browser to Google through Supabase (`/auth/v1/authorize?provider=google`, one-time code flow) and back to the page; the header then shows the email and "Sign out". A cancelled or failed sign-in shows a clear message, never the words from the address bar. The control is small, uses the existing button tokens, keeps 44px targets, and puts the signed-in email on its own row on a phone.
- `supabase-js` 2.118.0, vendored unmodified in `vendor/supabase.js` (provenance, hash and update steps in `vendor/README.md`) and served from the page's own origin with a Subresource Integrity hash computed by the build, so the Content-Security-Policy gains no CDN and no new `connect-src`. `build_dashboard.py` copies it and the new `auth.js` to `docs/`; the daily workflow commits both.
- The page never shows what Google sends besides the email (name, photo), and the session it saves has the provider metadata and identities stripped from the user, and Google's own access and refresh tokens dropped, every time it is written (also when the token renews). The signed access token is saved as issued: Supabase puts the name and photo in it as the `user_metadata` claim, which the page cannot edit, so the migration adds `public.custom_access_token_hook` (empties that claim, runnable only by Supabase Auth). It has no effect until it is switched on in the Supabase dashboard (Authentication, Auth Hooks).
- Migration `20261115120000_accounts_prefs_and_job_choices.sql`: `profiles` (user id and email only, filled by a `security definer` trigger on `auth.users` that reads `new.email` and nothing else, so no name, photo or provider id reaches `public`), the access token hook, `user_prefs` (filters and email settings as size-limited JSON objects, for steps 4.3 and 6.2) and `user_jobs` (one row per user and job with a single `state` of `star` or `discard`, so a job cannot be both, and the title, location and link kept on the row for steps 4.2). Row-level security on all three with own-rows-only policies (`(select auth.uid()) = user_id`) for select, insert, update and delete, nothing for `anon`, minimal grants, and `on delete cascade` from `auth.users` so deleting an account removes everything in one step.
- `supabase/tests/accounts_rls.sql`: policy tests to run against a test project or a local database (a second account cannot read, change or delete the first one's rows; nothing for `anon`; the profile holds only the email; the hook empties `user_metadata`; cascade delete). `supabase/README.md` says how to run them and what stays in `auth`.

### Changed

- `tests/test_migrations.py` learns functions, triggers and the own-row policies and stays strict: row-level security and a full revoke on every table, nothing for `anon` but the open jobs, only the listed privileges for `authenticated`, every user policy tests the own row, every function pins its search path and is closed to the public roles, only the profile copy may run on `auth.users`, no function reads provider metadata and no column holds personal data other than the email. New browser tests in `tests/test_sign_in.py` cover FR-1 AC 1, 2 and 6.
- The daily workflow also commits `docs/auth.js` and `docs/supabase.js`. ESLint and lint-staged skip `vendor/`; `.gitattributes` marks `vendor/supabase.js` as vendored and hides it from diffs.
- `PRIVACY.md` gets an interim paragraph about the optional sign-in; step 4.4 rewrites it in full. It says plainly that Google's name and photo still reach Supabase's own sign-in service (`auth.users`, `auth.identities`), which this migration does not rewrite; they stay out of `public`, out of the page and, once the hook is on, out of the access token.

## [0.16.0] - 2026-11-15

### Changed

- The dashboard's blended Product / Intern / Junior filter is replaced by separate filters: Level (Intern, Junior, Not specified) and Role type (every type of `classify.py`, from the page data so a new type needs no page change), plus a Region filter (the seven regions of `regions.py`), a "Places" list of every distinct place of the Israeli jobs as written in the data (with a find box, including "Israel, city not given"), and a chip "Include remote roles that do not say where". A job matches when it fits the levels and the role types chosen; nothing chosen means all; regions and places together mean "any of them" and apply to jobs in Israel only. The Israel only / All locations choice stays. The saved filters move to a new key, so older saved filters are ignored.
- A remote role whose location names no country ("Remote") is not Remote by default; the new option adds those to the Israel scope under Remote, and the card says "Remote, country not given".
- Every card shows its level, role types and region as tags (also for "Not specified" and "Other") and as `data-level`, `data-role-types`, `data-regions`, `data-locations` and `data-remote-country-not-given`; `data-category` is gone.
- The WebMCP `search_jobs` tool takes `levels`, `roleTypes`, `regions`, `locations` and `includeRemoteNotSaid` instead of `category`, and a new `list_filters` tool lists the accepted values with counts. Roles returned by the tools carry `level`, `roleTypes`, `regions`, `locations`, `inIsrael` and `remoteCountryNotGiven`.
- `build_dashboard.py` classifies with `classify.level` and `classify.role_types` and places with `regions.place`, the same functions the Supabase sync uses, so "in Israel" in `jobs.json` and `jobs.jsonld` now agrees with the page (a job in Givatayim or Ashdod is Israeli). `jobs.json` gets `lv`, `rt`, `rg` and `lo` in place of `k`; `jobs.jsonld` gets `occupationalCategory`, `experienceRequirements`, `employmentType` INTERN, one Place per region (`addressRegion`) and per ungrouped place (`addressLocality`), and `jobLocationType` TELECOMMUTE with an Israel `applicantLocationRequirements` for Remote. The list-valued card hooks are separated by `|` because places contain commas. A row without the classification columns is a load error (error card), saved filters of the wrong type are dropped, and `search_jobs` returns `warnings` for values no role has.

## [0.15.0] - 2026-11-15

### Changed

- The dashboard loads its jobs from the Supabase `jobs` table over PostgREST with the public anon key instead of reading `docs/jobs.json`. It pages 1,000 rows at a time (`limit` and `offset`, total from `Prefer: count=exact`), shows all of them, and treats a count that differs from the total as an error. The project URL and anon key are constants in `build_dashboard.py`, which injects them into `dashboard.js` and the URL into the page's `connect-src` (now `'self'` plus the project host). The kind filter reads `role_types` and `level`, "in Israel" reads `regions` and `locations`. The coverage gaps and build time ride in a JSON data block of `index.html` (no inline script). `jobs.json` and `jobs.jsonld` are still built and published as open data; the page does not read `jobs.json`.
- When the backend cannot be reached, answers with an error, or returns an incomplete list, the page shows an error card with a "Try again" button instead of a list; there is no offline copy.

## [0.14.0] - 2026-11-14

### Added

- `sync_supabase.py` copies every open job of `data/jobs.db` into the Supabase `jobs` table through the PostgREST API (`requests`, the service-role key from `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`), in batches of 500, jobs in Israel or not. Each row carries `level` and `role_types` (`classify.py`) and `regions` and `locations` (`regions.py`); `description` and `details` are never sent, so later steps can fill them. Jobs that are no longer open locally are set to closed (the open keys are read back in pages of 1,000, because Supabase caps a response). The upsert merges on (company, job_id), so a repeat run with an unchanged database writes the same values. It refuses to run against a database with no open jobs, so an empty file cannot close every job. It prints the role-type and region quality shares of `classify.report` and `regions.report` for the daily log. With either variable empty it prints a message and exits 0; any other failure exits non-zero and never prints the key.
- The daily workflow has a "Sync jobs to Supabase" step after the digest and before the dashboard build and the deploy push, with the two secrets exposed to that step only. It runs after a successful crawl even if the digest failed. A failed sync does not stop the push, and it is named (`sync=`) in the failure alert.

## [0.13.0] - 2026-11-14

### Added

- `regions.py` normalises a location string to the seven regions of Israel (Tel Aviv area, Sharon, Center, Jerusalem, Haifa and the north, South, Remote). `place(location)` gives every region a string names ("Israel, Yokneam; Israel, Tel Aviv" is two), reads a hybrid role as the region of its office, and gives a job with "Israel" alone the location "Israel, city not given". A remote role is Remote only when the same part of the string (parts are split on `;`) also names Israel. The middle field of "Israel, Tel Aviv, Herzliya" (country, district, city) is a district, so that job is Sharon only. A string with a city of the table is Israeli even when `common.ISRAEL` does not know the place (Givatayim, Ashdod), and the Hebrew names work. An Israeli place with no region keeps its own string, so no job in Israel is dropped, and jobs outside Israel are untouched. The cities are a table (`PLACES`), so a new city is one string. `report(locations)` prints the share of jobs in Israel that are in a region and the most common unmapped places, for the daily log later. On the 2,042 open jobs in Israel of `data/jobs.db`, 97.1% are in a region.

## [0.12.0] - 2026-11-14

### Added

- `classify.py` classifies a job by its title: `level(title)` gives Intern, Junior or Not specified, and `role_types(title)` gives every role type the title fits (Product, R&D, Data & AI, Design, IT, Finance, Sales & BD, Customer success & support) or Other when none. The types are a table (`ROLE_TYPES`), so a new type is one entry. `matches(title, levels, types)` applies the FR-9 rule (an empty choice means all), and `report(titles)` prints the share of open jobs not in Other, for the daily log later. On the 6,026 open jobs of `data/jobs.db` 75.2% are placed in a role type other than Other.

### Changed

- `INTERN`, `JUNIOR` and `PM_CORE` moved from `digest.py` to `classify.py` with the same patterns. The digest and the dashboard behave as before.

## [0.11.0] - 2026-11-14

### Changed

- The coverage gap card on the dashboard lists every site that is not crawled, with a reason for each, and shows the count of each reason: sites failing in the latest crawl, the companies with no feed in `data/companies.json` and the 93 researched companies of `data/unresolved_candidates.csv`. The card is a list of groups (a count, then the companies of that reason with a note), narrow enough for a phone. `gaps` in `jobs.json` (and the `list_coverage_gaps` tool) keeps `blocked` and `untracked` and gains `total` and `groups`.
- `data/unresolved_candidates.csv` has a `reason` (one of `build_dashboard.REASONS`) and a `note` for every row. `build_dashboard.py` stops on a reason it does not know.

### Added

- `tests/test_todo_counts.py` fails when the counts quoted in the `TODO.md` research item (the total, the four segments, the companies without a careers URL and the ones with) disagree with the CSV, so the card and `TODO.md` cannot drift apart.

## [0.10.1] - 2026-11-14

### Fixed

- `discover.ATS_LINK` no longer reads the `j` of `apply.workable.com/j/<shortcode>` (a job link) as a Workable account, which is how D-ID and Autofleet got bad slugs. A page that links a job and a board still finds the board.

### Added

- `ATS_LINK` recognises JazzHR (`<slug>.applytojob.com`) and BambooHR (`<slug>.bamboohr.com`) boards, so a rediscovery finds the companies pinned on those adapters. The `www`, `api`, `app` and `help` hosts of either site are not boards, and the slug is lowercased (the adapters compare it with the host the response gives back).

## [0.10.0] - 2026-11-14

### Changed

- The `workday` adapter recognises Israel on tenants the old routes missed. The facet filter now takes a country value named "Israel" with the tenant's own id (Philips) as well as Workday's id, and applies the value's id. When no such country exists it takes the values of a location facet whose name is an Israeli place (Broadcom's `ISR-Tel Aviv University`) and tags every posting it returns Israel like the country route. The bare code `IL` never selects a facet value and never makes a posting Israeli on Workday (it is Illinois as often as Israel). The word-search route now keeps a posting whose location names an Israeli place the matcher knows, not only one that says "Israel". A filtered first page whose total is above the counts of the values asked for (a filter Workday ignored would return every job) is a failed crawl. The 19 Workday boards already pinned return the same jobs, except Palo Alto Networks, which gains one multi-location posting in Israel.
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
