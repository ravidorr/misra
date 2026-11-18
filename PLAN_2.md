# Plan 2: the back office

Step plan for an admin page for the maintainer. Drafted 2026-11-18; nothing here is built yet.

Each step is one PR. Strike a step through (`~~text~~`) when it is done. Conventions for every step are those of
`PLAN.md`: a change to a listed Python module bumps `package.json` and `pyproject.toml` together and adds a
`CHANGELOG.md` entry; a new Python module is added to `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`),
`py-modules` and mypy `files`; coverage stays at 100%; browser work edits `templates/`, follows
`design-system/README.md`, and adds `connect-src` and `script-src` entries to the page CSP only for the Supabase host.

## What the back office is

One page, `admin.html`, for one person (the maintainer). It does five jobs, built in this order: sign in, crawl health
and the coverage gaps, company list editing, adding the sites visitors report, and looking after accounts and job
alerts.

## Rules for the whole plan

- **Nothing lawyer-dependent.** The back office stays inside what `PRIVACY.md` and `TERMS.md` already say: the
  maintainer has access to the database, and a reported site is emailed and not stored. A step that would store or show
  personal data in a way those pages do not describe is cut, not reworded. Steps 4 and 5 are written to fit.
- **No new personal data.** The office never lists email addresses, never writes an address to a log, an audit row or a
  database column that does not already hold it, and never emails anyone.
- **One list.** Every fact about a company, an exclusion included, is a field of its one record. No second list, no
  second file, no copy kept in step by hand.
- **The page has no power of its own.** `admin.html` is static and public, like the rest of `docs/`, and the anon key is
  public. Security is row-level security (RLS) plus a server-side admin check. The service-role key never reaches a
  browser or the crawl (see `AGENTS.md`); anything that needs it runs in an Edge Function that checks the caller first.
- **One admin.** A single row in `admins`, inserted by hand as in 1.1; the first sign in never claims it. No roles, no invitations.
- **Not discoverable.** `noindex`, no link from any public page or from `llms.txt`, left out of `jobs.jsonld` and the
  WebMCP tools. It is not a secret (it is in the repository); it is only never advertised.
- **Every step tests the denial first.** A signed-out user and a signed-in non-admin get a permission error on every
  new table and function, proved by an SQL test in `supabase/tests/` (the pattern of `accounts_rls.sql`) or a vitest
  test of the function.

## Step 1: Foundation (sign in and the admin check)

- [ ] 1.1 Migration: `public.admins(user_id uuid primary key references auth.users on delete cascade)`; the function
  `public.is_admin()` (`security definer`, `stable`, true when `auth.uid()` is in `admins`); revoke all on `admins` from
  `anon` and `authenticated`. SQL test: no role but the owner reads or writes `admins`; `is_admin()` is false for anon
  and for a signed-in user who is not listed. The maintainer inserts their own row by hand in the SQL editor (one
  statement, in `supabase/README.md`); there is no way to become an admin from the page.
- [ ] 1.2 `templates/admin.html`, `templates/admin.js`: built into `docs/admin.html` and `docs/admin.js` by
  `build_dashboard.py`, with the shared header and footer parts, SRI and the same CSP rules as the dashboard. Sign in
  with the existing Google sign-in of `auth.js`. After sign in the page calls `is_admin()`; a non-admin sees one line
  ("This account is not an admin.") and nothing else loads. `<meta name="robots" content="noindex">`. Tests: the page
  is built, is not linked from any other page, and is not in `jobs.jsonld`; a browser test of the signed-out and the
  non-admin states.
- [ ] 1.3 An audit log: `public.admin_log(id, ts, action text, target text)`, written only by the admin policies and the
  functions below; `target` is a company name, a table name or a user id, never an address. Readable and insertable
  only when `is_admin()`. The page shows the last 50 rows.
- Done when: the maintainer signs in and sees an empty office; a second Google account sees only the refusal.

## Step 2: Crawl health and the coverage gaps (read-only)

The data is already in Supabase, so this step adds no table and no pipeline change.

- [ ] 2.1 Read access for `is_admin()` only: a row policy `using (is_admin())` on `crawl_log` and `jobs`, and a column
  grant on `jobs` that leaves out `description` and `details` (row-level security cannot hide columns; the pattern is
  that of `20261122100000_jobs_snippet.sql`, and the lint in `tests/test_migrations.py` still refuses a grant of
  either). The lawyer's rule of 2026-11-16 stays: the office never shows posting text.
- [ ] 2.2 "Crawl health": per company, the last good crawl, the last result and note, and a streak of failures; sorted
  with the worst first; filters for failing, skipped for robots.txt, and held (a sharp drop). Counts of open jobs per
  company over 30 days. Reads `crawl_log` and `jobs` with the admin session; no function.
- [ ] 2.3 "Coverage gaps": the groups of the public card (`REASONS` in `build_dashboard.py`), with the reason, note and
  career link of each company. Until step 3 the list comes from the same `jobs.json` the card uses, so it needs no new
  data; from step 3 it reads the `companies` table and also shows the excluded companies, marked as such.
- Done when: the page answers "what failed last night and which companies are we blind to?" without opening Actions.

## Step 3: Company list editing

This moves the home of the list from `data/companies.json` to Supabase, as the jobs moved in `PLAN.md` step 1. It is the
one step that changes the pipeline. Today `data/companies.json` holds 347 records: 267 crawled and 80 gap records, none
both.

Decisions made before 3.1:

- **D1. `companies.json` leaves the repository (decided by the maintainer, 2026-11-18).** The sync step pulls the table into a git-ignored scratch
  `data/companies.json`, as it does for `data/jobs.db`; `run.py`, `build_dashboard.py` and the surveys read that file
  and are unchanged. The tests that read the real file move to a fixture (`tests/fixtures/companies.json`, a frozen
  copy of today's file). Reason: two homes for the list is the state `PLAN.md` was written to remove. Cost: no git diff
  of edits; the daily backup to `ravidorr/misra-backups` is the history (a `TABLES` entry in `export_accounts.py`). The
  same holds for exclusions, which makes the backup the only record of who asked to be removed and when.
- **D2. `discover.py` stays offline of the key.** No workflow runs it today: the maintainer runs it by hand and commits
  the file (`CONTRIBUTING.md`, "Adding a company"). It makes requests to career sites, so it never holds the
  service-role key. It reads and rewrites only the scratch file. After a hand run, `sync_supabase.py --push-companies`,
  a separate command run with the key in the maintainer's shell, pushes the five discovered fields back to the table
  and nothing else. No new workflow step.
- **D3. Exclusions are a field of the company record, edited in the office.** `data/excluded_companies.csv` and its
  second loader go away. An excluded company is a record with `excluded: {reason, date_added}` and `crawl: false`; a
  company that asked to be removed and was never in the list gets a minimal record (name and `excluded`). Excluding a
  company clears its `careers_url`, `links`, `gap` and `segment` in the same write (a database check: an excluded
  record holds nothing else), so the public data keeps only what the asker needs to be honoured. The guards of
  `load_excluded` all stay, in two places: the database refuses a blank or one-character name, a repeated name (case
  insensitive), a blank reason, a date that is not a date, and a 201st exclusion; and `load_excluded` still raises
  `ExclusionError` on a pulled file that does not load, so a record nobody can trust still stops the run. The purge limit
  (`MAX_PURGE_COMPANIES`) and the spelling check (`check_spelling`) are unchanged.
- **D4. A pull that cannot reach Supabase stops the run (the same as today).** `sync_supabase.py --pull` already does
  so for the jobs history, so pulling the list adds no new stop, and a run must not crawl without the exclusions.

- [ ] 3.1 Migration, alone: it lands before any sync or pipeline change, so the table exists and holds the list while
  `companies.json` is still the source. `public.companies` with the columns of `common.RECORD_KEYS`, which gains `excluded` (`links`, `gap`
  and `excluded` as `jsonb`, a unique case-insensitive `name` and `domain`, a check that at most one of `crawl`, `gap`
  and `excluded` is set, the checks of D3 for an excluded record, the gap reasons checked against a list kept in step
  with `REASONS`); RLS: no access for anon and authenticated except `is_admin()`, which gets
  `select, insert, update, delete`; a trigger writes `admin_log`. The same migration loads the 347 records of today's
  file. SQL tests for each denial and for the checks.
- [ ] 3.2 `sync_supabase.py --pull` also writes `data/companies.json` from the table (sorted by name, the format of
  `discover.py`, refusing an empty table as it refuses an empty `jobs`); `--push-companies` (run by hand after `discover.py`, see D2)
  writes back the discovered fields of crawled records. The pulled file carries the `excluded` records, and `load_excluded` reads them from it
  (the CSV and `EXCLUDED_COLUMNS` are removed); `run.py`, `discover.py`, `build_dashboard.py` and the sync keep
  calling it as they do today. `.gitignore` lists the file. `export_accounts.py` backs the table up; `restore_accounts.py`
  restores it. Tests, including the refusal of an empty table.
- [ ] 3.3 The editor: a searchable list, one form per company with the fields of `CONTRIBUTING.md` ("Adding a
  company"), a "Pin" action (sets `source: override`, `ats`, `slug`), a "Move to gaps" and a "Start crawling" action
  (the `crawl` flip; the database checks are the only rules, and the page shows their error), and the
  `israel_only` and `verify` switches. Since v0.67.4 `run.py` closes the open jobs of every record that is not crawled
  (`close_uncrawled`, the rows stay as history), so "Move to gaps", like any flip of `crawl` to false, closes that
  company's jobs at the next run, and "Exclude" deletes them. The page shows the number of jobs the next run will close
  or delete for both (a run can be started by hand from Actions when it must be done today). An "Exclude" action asks
  for the reason (with the line "no personal data in the reason") and the date, and clears the other fields as D3 says.
  "Include again" removes the exclusion. A change takes effect at the next daily run; the page says so.
- [ ] 3.4 One source of truth for the company rules: the SQL checks of 3.1 are the rules, and
  `tests/test_companies_data.py` stops duplicating them. The Python side only checks that the pulled file loads and
  that `load_excluded` still raises `ExclusionError`. Port the `load_excluded` and purge tests (`tests/test_politeness.py`, `tests/conftest.py`) from the CSV to excluded
  records, keeping every case; update `AGENTS.md` (the exclusion paragraph and the single-list rule),
  `CONTRIBUTING.md` ("Removing a company") and `README.md` (the quickstart now starts with `sync_supabase.py --pull`).
- Done when: a company is added, pinned, put on the card or excluded from the page, and the next daily run crawls it,
  lists it, or deletes its jobs.

## Step 4: Adding the sites visitors report

A report today is an email, and `PRIVACY.md` says it is not stored. This step keeps that true: no table of reports.

- [ ] 4.1 "Add a site" in the office: the maintainer reads the report email and types the company, domain and career
  link into the form of step 3. The form validates the way `discover.py` does (https only, a host, no tracking
  parameters, no duplicate domain) and offers "Accept for the crawl" (`crawl: true`, then the next discover run fills
  the feed) or "Keep on the card" (`crawl: false` with a `reason` and `note`). It is the editor of step 3 with a shorter
  form, not a new store.
- [ ] 4.2 Show the existing report counters (`report_ip_limits` is not shown: it holds hashed addresses; only the
  `report_daily_cap` count of today), so the maintainer sees whether the daily cap is near.
- [ ] 4.3 `CONTRIBUTING.md`, "Reported sites": the three outcomes (accept, keep on the card, decline) now start in the
  office.
- Done when: a report email becomes a company record in under a minute, and nothing about the report is stored.

## Step 5: Accounts and job alerts

The maintainer already has database access (`PRIVACY.md`); this step puts a narrow, audited front on what the
maintainer would do by hand. It shows counts, never lists.

- [ ] 5.1 "Alerts" counts, from tables the office may read with an `is_admin()` policy that exposes counts only (a view
  or a `security definer` function returning numbers): subscriptions by state (pending, confirmed, off), digests
  emailed per day for 30 days from `emailed`, the confirm and report daily caps. No address, no user id in the result.
- [ ] 5.2 The Edge Function `admin-account`, which first checks `is_admin()` with the caller's token and answers 404
  otherwise. It takes an address the maintainer types (from a support email) and does one of: end that person's job
  alerts, delete that person's account (the same code path as `delete-account`, shared in `_shared/`). It answers
  "done" or "no such account", the same text for both a missing and a deleted one, and it writes `admin_log` with the
  action and a hash of the address, never the address. It never logs the address (the rule of `AGENTS.md`), never
  returns a row, and never emails the person.
- [ ] 5.3 The page: two forms and the counts of 5.1. There is no list of accounts and no search by partial text.
- [ ] 5.4 `PRIVACY.md` is not touched. A test (`tests/test_legal_wording.py`, the pattern already there) pins that the
  office adds no address to any table, log or response by grepping the new migrations and functions, and checks the
  function's 404 for a non-admin.
- Done when: a request by email to stop alerts or delete an account is done in the office in one minute, audited, and
  no address is stored or shown anywhere.

## Out of scope

- Editing or showing the text of postings, moderating postings, and anything that reads the `description` column.
- A list or search of accounts, subscribers or addresses.
- More than one admin, roles, or an admin invitation flow.
- Changing the privacy, terms or security pages, the digest or contact switches, or the assistant endpoint (all lawyer
  matters or maintainer secrets).
