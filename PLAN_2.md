# Plan 2: the admin page

Step plan for an admin page for the maintainer. Drafted 2026-11-18; nothing here is built yet.

Each numbered step is one or more PRs; the sub-steps (3.1, 3.2 and so on) are the PRs, and step 4 is part of 3.3. Strike a step through
(`~~text~~`) when it is done. Conventions for every step are those of `PLAN.md`: a change to a listed Python module
bumps `package.json` and `pyproject.toml` together and adds a `CHANGELOG.md` entry; a new Python module is added to
`RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), `py-modules` and mypy `files`; coverage stays at 100%; browser
work edits `templates/`, follows `design-system/README.md`, and allows only the Supabase host in the page CSP. A new
workflow follows the secret rules of `daily.yml` (see D2).

## What the admin page is

One page, `admin.html`, for one person (the maintainer). After the sign in it has four areas: Crawl, Companies,
Alerts and Log. Adding a site a visitor reported is the Add button of the Companies editor.

## Rules for the whole plan

- **Nothing lawyer-dependent.** The admin page stays inside what `PRIVACY.md` and `TERMS.md` already say: the
  maintainer has access to the database, and a reported site is emailed and not stored. A step that would store or show
  personal data in a way those pages do not describe is cut, not reworded.
- **No new personal data.** The admin page never lists email addresses, never writes an address to a log, an audit row
  or a database column that does not already hold it, and never emails anyone.
- **One list.** Every fact about a company, an exclusion included, is a field of its one record. No second list, no
  second file, no copy kept in step by hand. The list is public to read (D1), so an excluded record is never readable by
  anyone but the admin, and nothing about who asked is stored.
- **The page has no power of its own.** `admin.html` is static and public, like the rest of `docs/`, and the anon key is
  public. Security is row-level security (RLS) plus a server-side admin check. The service-role key never reaches a
  browser or the crawl (see `AGENTS.md`); anything that needs it runs in an Edge Function that checks the caller first,
  or in a named workflow step (D2).
- **One admin.** A single row in `admins`, inserted by hand as in 1.1; the first sign in never claims it. No roles, no
  invitations.
- **Not discoverable.** `noindex`, no link from any public page or from `llms.txt`, left out of `jobs.jsonld` and the
  WebMCP tools. It is not a secret (it is in the repository); it is only never advertised.
- **Every step tests the denial first.** A signed-out user and a signed-in non-admin are refused on every new table and
  function (a permission error from the database, a 404 from a function), proved by an SQL test in `supabase/tests/`
  (the pattern of `accounts_rls.sql`) or a vitest test of the function.
- **Status is text.** A status is always words, styled with the existing tokens; there is no new colour and colour is
  never the only signal.
- **Accessibility, per area.** Each area has an `h2`, a labelled search where there is one, a live count, focus that
  returns to the control that opened a dialog, and 44px targets.
- **One name.** Say "admin page" everywhere, never "office" or "back office".

## Step 1: Foundation (sign in and the admin check)

- [x] 1.1 (done: `20261127100000_admins.sql`, `supabase/tests/admins.sql`, the admin lint of `tests/test_migrations.py`.) Migration: `public.admins(user_id uuid primary key references auth.users on delete cascade)`, and
  `public.is_admin()` (`security definer`, `stable`, `set search_path = ''`; true when `auth.uid()` is in `admins`),
  with `revoke ... from public` and `grant execute ... to anon, authenticated`. Revoke all on `admins` from `anon` and
  `authenticated`. SQL test: no role but the owner reads or writes `admins` ("the owner" is `postgres`; the service role
  bypasses RLS); `is_admin()` is false for anon and for a signed-in user who is not listed.
  - The existing lint `policy_problem` in `tests/test_migrations.py` refuses any policy on a table outside
    `USER_TABLES`, and accepts on `jobs` only `using (status = 'open')`. Extend it with a list of admin tables (`jobs`, `crawl_log`, `admins`, `admin_log`, `companies`) whose
    only accepted `using` is `public.is_admin()`; keep the open-jobs policy of `jobs`, and accept the public read policy
    of `companies` (D1, `excluded is null`) as its one other policy. Never loosen the existing checks. Steps 1.3, 2.1 and 3.1 cite this.
  - The `admins` row has a foreign key to `auth.users`, so the maintainer signs in once on the dashboard first, then
    runs one statement in the SQL editor: `insert into public.admins select id from auth.users where email = '...'`
    (the exact text goes in `supabase/README.md`). There is no way to become an admin from the page.
- [x] 1.2 (done: `templates/admin.html`, `admin.js`, `session.js`, `tests/test_admin_page.py`.) `templates/admin.html`, `templates/admin.js`, built into `docs/admin.html` and `docs/admin.js` by
  `build_dashboard.py`, with SRI and the CSP rules of the dashboard. The page lives at `/misra/admin.html`. Add both
  files to the generated-files list in `AGENTS.md`, to the SRI placeholder and page lists in `build_dashboard.py`, and
  to the source paths of `publish-docs.yml`. (Later, v0.80.0: to anyone who is not an admin the page shows the site's 404 page, see `AGENTS.md`; `docs/404.html` is built from `templates/not-found.html`.)
  - **Sign in.** `templates/auth.js` is bound to dashboard ids, and the design README requires the Sign in button to
    open the approved notice word for word, so the admin page builds no sign-in UI. `admin.js` makes its own Supabase
    client with the same storage key and storage wrapper as `auth.js`; the session is shared on the origin. Signed out,
    the page says "Sign in on the dashboard first." with a link to `./`. A Sign out button sits in the page body in
    every signed-in state. The page never starts a redirect, so the Supabase Auth redirect allow-list is unchanged; any
    change to the production Auth config needs the maintainer's consent.
  - (Superseded in v0.80.0: the states and messages below, and the signed-out link, are replaced by the 404 page, see `AGENTS.md`.)
  - **Page states** (`data-state` on the page): `checking` (the default; all content hidden), `signed-out`,
    `not-admin` ("This account is not an admin."), `check-failed` ("Could not check your account. Reload the page.";
    a network or backend error, not the same as not admin) and `ready`. A 401 shows "Your sign-in expired" and runs the
    check again. Nothing but the check loads for a non-admin.
  - **Areas.** One page with hash sections and plain links: `nav aria-label="Admin"` with `aria-current`, each
    section hidden with an `h2`, focus moved to the `h2` and `document.title` updated on a change. No ARIA tabs. A
    section loads on its first show. Area states: loading (plain text in `role="status"`), empty, error (a `notice`
    with `role="alert"` and "Try again") and ready. The page title is "Admin".
  - **Layout.** `.page` is 760px wide at most; add an `.admin-*` wide wrapper (about 1000px) at the end of
    `templates/dashboard.css`. One CSS bundle, no CSP change.
  - **Tests.** The page is built, is not linked from any other page, and is not in `jobs.jsonld`; a browser test of
    the signed-out, not-admin and check-failed states.
- [x] 1.3 (done: `20261128100000_admin_log.sql` and `supabase/tests/admin_log.sql`; the Log area reads it, last 50 rows, in the PR of step 5.1.) The log: `public.admin_log(id bigint generated always as identity primary key, ts timestamptz not null default
  now(), action text not null, target text)`. It is written only by the trigger of 3.1 and by `admin-account` (5.2),
  and readable when `is_admin()`; no public role has insert, update or delete (the function writes with the service
  role). `target` is a company name or a table name,
  never an address and never a user id. The Log area (a `ul` of time, a plain-English action and the target, the last
  50 rows, empty text "No changes yet.") ships in step 3, when the first rows appear.
- Done when: the maintainer signs in on the dashboard, opens `admin.html` and sees the empty areas; a second Google
  account sees "This account is not an admin." and no request but the check.

## Step 2: Crawl health (read-only)

The data is already in Supabase, so this step adds no table and no pipeline change.

- [x] 2.1 (done: `20261129100000_admin_crawl_health.sql`, `supabase/tests/admin_crawl_health.sql`.) Read access for `is_admin()` only (see the lint of 1.1). On `jobs`, add a select policy
  `using (public.is_admin())`. The column grant that hides `description` and `details` already exists
  (`20261121100000_jobs_text_not_public.sql`) and stays, and `tests/test_migrations.py` still refuses any grant of
  either. The grants of the short `snippet` columns (`20261122100000_jobs_snippet.sql` and later) stay as they are. On `crawl_log`, which has `revoke all` and no grant, add
  a `grant select` to `authenticated` and the same policy. The lawyer's rule of 2026-11-16 stays: the admin page never
  shows posting text.
- [x] 2.2 (done: the Crawl area, `tests/test_admin_crawl.py`.) The Crawl area. PostgREST returns 1000 rows at most by default and `crawl_log` holds about 24,000 (90 days of
  267 companies), so a count made in the browser would stop at 1000 rows. Use a view or a `security invoker` function
  `admin_crawl_health()` gated by `is_admin()`, with one row per company. Terms, from `run.py` and `common.py`:
  - failing: the last two crawls have `ok = 0` (`FAILING_RUNS`), not counting robots.txt skips; last good crawl: the last row with `ok = 1`;
  - streak: the number of consecutive days whose last run has `ok = 0`, robots.txt skips left out;
  - held: the day's last note starts with `DROP_NOTE` (`run.sharp_drop`);
  - skipped for robots.txt: the note carries `ROBOTS_NOTE`;
  - open jobs now: counted in `jobs`. Fetched jobs 30 days ago: `crawl_log.n_jobs`, labelled "fetched", since it is
    zero after a failed fetch and the fetched count in a held crawl. Two numbers, no chart.

  Layout: a `ul` of `li` in the `.gap-card` look, the name as a heading (`dir="auto"`, `overflow-wrap: anywhere`), a
  `dl` of the values and a `.pill` for the status. The order is fixed, worst first; no column sort and no data table.
  Filters are `.chip` toggles with counts (Failing, Robots.txt, Held); the default view is "needs attention" and the
  healthy companies sit behind "All". The page notes that `crawl_log` may read short until about 2026-11-20, after the
  move of step 9.1 of `PLAN.md`.
- Done when: the Crawl area lists the failing, held and robots.txt-skipped companies of the last run, worst first, and
  its counts match a query of `crawl_log` in the SQL editor.

## Step 3: Company list editing

This moves the home of the list from `data/companies.json` to Supabase, as the job history moved in `PLAN.md` step 9.1.
It is the one step that changes the pipeline. The records of the file at the 3.1 commit are the start (347 on
2026-11-18: 267 crawled and 80 gap records, none both).

Decisions made before 3.1:

- **D1. `companies.json` leaves the repository, and the list stays public to read (decided by the maintainer,
  2026-11-18).** The table is readable by `anon` and `authenticated` through the public API, on the records that are not
  excluded only (a policy `excluded is null`); only `is_admin()` writes, and reads excluded ones. The sync step pulls the
  table into a git-ignored scratch `data/companies.json`, as it does for `data/jobs.db`; `run.py`, `build_dashboard.py`
  and the surveys read that file and are unchanged. The tests that read the real file move to a fixture
  (`tests/fixtures/companies.json`, a frozen copy of the file). Reason: two homes for the list is the state `PLAN.md`
  was written to remove. Cost: no git diff of edits. Step 3.4 adds the daily backup of `companies` to
  `ravidorr/misra-backups`, which is then the history, and the only record of which companies asked to be removed, and
  when; until then the table is the only copy.
- **D2. `discover.py` runs without the key, in a workflow.** It makes requests to career sites, so it never holds the
  service-role key. The manual-dispatch workflow `discover-companies.yml` (step 3.2, done) follows `daily.yml`: the
  pull step reads the list; `discover.py` runs in its own step without the key; only a final separate step holds the
  service-role key and runs `sync_supabase.py --push-companies`, which pushes the discovered fields
  (`discover.DISCOVERED`) and nothing else. No checkout gets an `ssh-key:`. The key-exposure rule in `AGENTS.md` names
  the steps that hold the key.
- **D3. Exclusions are a field of the company record, edited on the admin page.** `data/excluded_companies.csv` and its
  second loader go away. An excluded record holds `name`, `crawl: false` and `excluded: {reason, date_added}` and
  nothing else, `domain` included. Excluding sets every other field to null in the same write, and `domain` may be null
  only on an excluded record. A company that asked to be removed and was never in the list gets this minimal record.
  The checks of D3 are: a name of 2 characters or more, a name unique ignoring case, a reason that is not blank, a date
  in ISO form, at most 200 excluded records, and the shape above. The database refuses a record that breaks them (the limit of 200 needs a trigger).
  `load_excluded` keeps every guard it has today and still raises `ExclusionError` on a pulled file that does not
  load, so a record nobody can trust stops the run. `MAX_PURGE_COMPANIES` and `check_spelling` are unchanged.
- **D4. A pull that cannot reach Supabase stops the run (the same as today).** `sync_supabase.py --pull` already does
  so for the job history, so pulling the list adds no new stop, and a run must not crawl without the exclusions.

Steps:

- [x] 3.1 (done: PR #214, migration `20261130100000_companies.sql` applied to production on 2026-11-18, 347 rows.)
  Migration, alone: it lands before any sync or pipeline change, so the table exists and holds the list while
  `companies.json` is still the source. `public.companies` has the columns of `common.RECORD_KEYS`; `links`, `gap` and
  `excluded` are `jsonb`.
  - Checks, each with a stable name: `name` and `domain` unique ignoring case; exactly one of `crawl` is true, `gap`
    is not null, `excluded` is not null; the checks of D3; a gap reason in the migration's list, with a test that fails
    when that list differs from the keys of `REASONS` in `build_dashboard.py`; a careers link that is empty or
    starts with `https://` (the 3.1 PR also corrects the one `http://` record in `data/companies.json`, Verbit, so the file and the table agree).
  - RLS (see the lint of 1.1): the policy of D1 for reading; `select, insert, update, delete` for `is_admin()`; a
    trigger writes `admin_log`. The same migration loads the records of the file at that commit.
  - SQL tests for each denial and each check. The editor (3.3) asks for a gap reason and a
    note when a record goes on the card, and for a gap reason or crawling when an excluded record is included again.
- [x] 3.2 (done: v0.69.0, PR #215.) The table matched `companies.json` when it was checked (347 records, 267 crawled and
  80 on the card), so no reload was needed. `sync_supabase.py --pull` also writes `data/companies.json` from the table
  (sorted by name, the format of `discover.py`, every record that is not excluded with its five discovered fields,
  empty where the table holds null) and its baseline `data/companies.pulled.json`, and refuses an empty table, or one
  with fewer than half of the yardstick (the baseline's records or `covered` in `docs/jobs.json`).
  `--pull-companies` does only that. `--push-companies` writes back the discovered fields that discovery changed
  against the baseline, only while the row still holds its pulled values, and holds back a working feed that discovery
  no longer finds. The pulled file carries the excluded records, and `load_excluded` reads them from it (the CSV and
  `EXCLUDED_COLUMNS` are gone). `common.RECORD_KEYS` gained `excluded`, and `crawled_companies` skips an excluded
  record. `.gitignore` lists the files, and the discover-and-push workflow of D2 was added. The tests moved in the same
  PR: `tests/test_companies_data.py` only checks that the pulled file loads and that `load_excluded` raises
  `ExclusionError`; the `load_excluded` and purge tests were ported from the CSV to excluded records; the tests that
  read the real file read the frozen `tests/fixtures/companies.json`; `test_todo_counts` was dropped.
- [x] 3.3 The editor. (done: PR for step 3.3. Templates, a small `components/choice.css` and tests only: no Python module
  changed, so no version bump. Built as `templates/admin.js` and the dialogs of `templates/admin.html`; the tests run
  against a fake of the `companies` and `jobs` tables, `tests/fake_companies.py`, which refuses a write as the
  migration's checks do. Differences from the text below, all on purpose: the name of a listed company is read-only
  (`jobs` and `crawl_log` are keyed by it, so a rename would orphan the history); the page chooses between "Crawl it" and
  "Show it on the coverage card" and never sets `excluded` except through "Exclude…" and "Exclude a company"; the hand
  choice of `ats` and `slug` was left to the table editor at first, and "Find its job feed again" empties the
  `status` (a later PR built "Pin the job feed" in the editor: the feed from the adapters list, the slug, `source`
  `override`, an empty `status`, and an unlock of a card record in one save, all in the admin Log); rows do not
  say "applies at the next run" (the saved line does, for the change just made); no `updated_at` conflict banner,
  but every update and delete carries `updated_at`, and one that finds the row changed says so; removing a company
  says its open jobs stay in the history, because no run closes the jobs of a company that is not in the list (move it
  to the card first to close them). The count of excluded companies waiting to be purged reads the jobs of the
  excluded names (a thousand rows at most) and is shown in the Exclude dialog.)
  - **List.** A searchable `ul` with "Showing n of 347" in `role="status"`, chips Crawled, On the card (a company with a
    gap, shown with its reason, note and career link) and Excluded, and an Edit button
    on each row. Rows edited since the last crawl say "applies at the next run".
  - **Editor.** One shared modal `dialog` (`dialog.css`, corner x, Cancel, Save; a sticky action bar on a phone with
    the `sheet-head` and `sheet-foot` styles; focus returns to the row's Edit button). Status is a fieldset of
    radios (Crawling, On the coverage card, Excluded) that shows the fields of the choice: a gap reason select from
    `REASONS` and a note. A checkbox "Choose the job feed by hand" shows `ats` and `slug`. One Save.
    "Include again" shows only on an excluded record. A small `components/choice.css` is needed (add it to
    `design-system/index.html` and `CHANGELOG.md` as the design README says).
  - **Fields.** Name; domain; two URL inputs for `links`, Israel and global (not JSON); a segment select; checkboxes
    `israel_only` and `verify`; the five discovered fields (careers URL included) read-only; an `ats` list from a database check (the keys of `adapters.ADAPTERS` plus `browser`, which `run.py` routes to
    `browser_crawl`, and empty for an undiscovered record) that a test
    keeps equal to `adapters.py`.
  - **Exclude.** A separate button opens its own confirm dialog (focus on Cancel, `btn-danger`, `role="alert"`
    errors). The date defaults to today. The reason hint is "Write why, not who asked. No names or email addresses."
  - **Errors.** A map from each constraint name to one plain sentence, shown as a `.field-error` with `aria-invalid`
    where a field is named, else in a `role="alert"` notice.
  - **Preview.** A confirm dialog opens when a save takes a crawled company off the crawl or excludes it, and runs a count query and has three states (counting, a number, "Could not count",
    with the action still allowed). Off the crawl: "N open jobs will be closed at the next daily run" (`run.py` closes
    them with `close_uncrawled`; the rows stay as history). Exclude: "N jobs will be deleted at the next daily run"
    (every job, open and closed, and the company's crawl log rows). The run stops when more than `MAX_PURGE_COMPANIES`
    (3) companies wait to be purged, so the page shows how many wait.
  - **Saved.** "Saved. Takes effect at the next daily run." in a status region. Optional and cheap: `updated_at` on
    `companies`, with conditional updates so two tabs cannot overwrite each other.
  - **Add.** The Add button opens the same editor, empty. For a site a visitor reported, the maintainer reads the report
    email and types the company, the domain and a career link. The link goes into `links.israel`; `careers_url` stays
    empty for `discover.py`. The database checks are the rules: the browser does not re-implement them.
  - **Timing.** `run.py` crawls only records with `status == "ok"`, and a new company has no `ats`, `slug` or status
    until `discover.py` runs. A new company is therefore crawled after the next discover-and-push run (D2); every other
    change takes effect at the next daily run. The page says which of the two applies.
- [x] 3.4 One source of truth for the company rules: the SQL checks of 3.1 are the rules.
  - Status: done. The backup and restore of `companies` landed in v0.71.0; the documentation that waited for the editor
    ("Adding a company", "Removing a company on request" and "Reported sites" now start on the admin page, with the SQL
    path kept as the fallback; the exclusion paragraph of `AGENTS.md`) landed with step 3.3.
  - [x] `companies` is backed up with the `Table(..., account=False)` pattern of `JOBS` and `CRAWL_LOG` in
    `export_accounts.py`, and `restore_accounts.py` restores it.
  - Docs: `AGENTS.md` (the exclusion paragraph and one line in the backup paragraph; the single-list rule and the key-exposure rule
    were done in 3.2); `CONTRIBUTING.md` ("Adding a company" now says how a visitor or contributor suggests
    one, through the report form or the contact page, and that the maintainer adds it on the admin page, replacing the
    interim table-editor steps of 3.2; "Removing a
    company on request"; "Reported sites": the three outcomes of accept, keep on the card and decline now start on the
    admin page); `README.md` (done in 3.2: the quickstart starts with `sync_supabase.py --pull`).
- Done when: a company is added, put on the card or excluded from the page; an added company is crawled after the next
  discover-and-push run, and the other changes take effect at the next daily run (its jobs closed, kept or deleted as
  the page said).

## Step 4: Adding the sites visitors report

A report today is an email, and `PRIVACY.md` says it is not stored. This step keeps that true: no table of reports.
The work is the Add button of step 3.3 and its `CONTRIBUTING.md` text; nothing more is built here, and the report
daily cap is not shown (it is not actionable for one person).

- Done when: a report email becomes a company record through the Add button, and nothing about the report is stored.

Status: met by the Add button of step 3.3 (done) and the `CONTRIBUTING.md` text of "Reported sites"; nothing more was built here, so step 4 has no box of its own.

## Step 5: Alerts and accounts

The maintainer already has database access (`PRIVACY.md`); this step puts a narrow, audited front on what the
maintainer would do by hand. It shows counts, never lists.

- [x] 5.1 (done: `20261201100000_admin_alert_counts.sql`, `public.admin_alert_counts()`, rows of kind, day and n; `supabase/tests/admin_alert_counts.sql`; the Alerts area shows the counts, read-only. The maintainer applies the migration after merge.) The Alerts counts, from a view or a `security definer` function gated by `is_admin()` that returns numbers
  only: subscriptions by state (pending, confirmed, off), and the number of distinct users emailed in each of the last 7
  days (from `emailed`; not the number of jobs). No address and no user id in the result.
- [x] 5.2 (done: PR #220, `supabase/functions/admin-account/`, migration `20261202100000_admin_account_ids.sql`, not yet deployed or applied; request and answers in `supabase/README.md`.) The Edge Function `admin-account`, which first checks `is_admin()` with the caller's token and answers 404
  otherwise. It takes an address the maintainer types (from a support email) and does one of two things.
  - End job alerts: the same as the unsubscribe link (`PRIVACY.md`, "Email settings"). `unsubscribe_subscription` takes
    a token hash, so this is a new path by address, not a reuse.
  - Delete the account: the deletion code moves from `delete-account/handler.ts` into `_shared/` (it is not there
    yet) and both functions call it.

  For the admin caller only, it answers "Done." when it acted and "No account uses that address." when none matches.
  `admin_log` records the action and the time only: no hash of the address and no user id. When no account matches,
  nothing is written to the log. It never logs the address (the rule of `AGENTS.md`), never returns a row, and never
  emails the person.
- [x] 5.3 (done: `templates/admin.html`, `templates/admin.js`, `templates/dashboard.css`, `tests/test_admin_account_forms.py`. Below the counts, "End job alerts" and "Delete an account" each have one address field and one button; deleting asks first in a dialog that names the action and shows the typed address, ending asks nothing. The answers are fixed sentences of the page ("Done.", "No account uses that address.", and plain ones for 409, 5xx and no answer), never the text of the function; a 404 renews the session once and tries again, then runs the page's own check. The address is in the body of one request only and the field is emptied after every answer that is not retryable. After a "Done." the counts reload and the Log is read again when next shown.) The Alerts area: two forms and the counts of 5.1. There is no list of accounts and no search by partial text.
- [x] 5.4 (done with 5.2: `test/admin-account.test.ts` and the pinned lookup in `tests/test_migrations.py`.) `PRIVACY.md` is not touched. `tests/test_migrations.py` pins that the new migrations add no personal column
  (the `PERSONAL_COLUMN_ALLOWED` pattern), and a vitest test in `test/` checks the 404 of the function for a non-admin
  and that the function never logs the address.
- Done when: a request to stop alerts or delete an account is done on the admin page with the two answers above, the
  log shows the action and the time, and no address is stored or shown anywhere.

Status: every box of steps 1 to 5 is ticked, so the code of PLAN_2 is complete. The maintainer did the live check of
both Alerts forms on the real project on 2026-11-18 and they behaved as expected, so PLAN_2 is complete. `PLAN_3.md` is
the next work.

## Out of scope

- Editing or showing the text of postings, moderating postings, and anything that reads the `description` column.
- A list or search of accounts, subscribers or addresses.
- More than one admin, roles, or an admin invitation flow.
- Changing the privacy, terms or security pages, the digest or contact switches, or the assistant endpoint (all lawyer
  matters or maintainer secrets).
