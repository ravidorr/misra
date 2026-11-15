# Changelog

All notable changes are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `TERMS.md`: a short as-is disclaimer (free, non-commercial hobby project, no warranties, listings collected automatically from public pages and not guaranteed, trademarks belong to their employers, signing in accepts the privacy notice including processing in the EU and the US, employers write to `raanan@avidor.org` to have their links removed). Linked from the page footer, the sign-in notice, `PRIVACY.md`, `README.md` and `llms.txt`.
- The dashboard footer links to the privacy notice, the terms and the contact address (44px targets, new-tab links say so).
- The notice next to the sign-in button (PLAN.md step 6.6, FR-1 AC 7): signing in is voluntary, the email is used only to save choices and send alerts the user turns on, with links to the privacy notice and the terms. It shows only while signed out and the button names it with `aria-describedby`.
- `tests/test_legal_wording.py` and tests of the notice and the footer links in `tests/test_sign_in.py`.

### Changed

- The lawyer's microcopy on three live touchpoints: the sign-in notice (two short paragraphs: sync across devices, signing in is optional, only the Google email address and job preferences are stored in the EU database, never a CV, password or tracking cookies, with links to the Terms and the Privacy Notice, plus one clause that Supabase's own sign-in record also holds the Google name and photo until the account is deleted, as `PRIVACY.md` says); the "Delete my account" confirmation dialog ("Delete your Misra account?", names the signed-in email address, shown as text, with "Cancel" and "Delete my account permanently"); and one line above Send in the "Report a site" form (reports go to the maintainer, the spam guard keeps a daily non-reversible hash of the network address, useless after that day and deleted when later reports are handled), with a Privacy Notice link.
- `PRIVACY.md` names the database controller (Raanan Avidor, a private individual) and the contact `raanan@avidor.org`, says why the data is collected and that giving it is voluntary, names the providers and where they are (Supabase and Resend in Ireland, Google and GitHub in the United States), gives the rights to inspect, correct and delete (Sections 13 and 14 of the Israeli Privacy Protection Law) with an answer within 30 days, and states that Supabase's and Resend's standard data processing agreements apply. The digest is still described as planned, not live.
- `PLAN.md` step 6.3 and the PRD (FR-8) carry the lawyer's exact footer text for every digest email and the Reply-To requirement.

## [0.22.0] - 2026-11-17

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

## [0.21.0] - 2026-11-16

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

## [0.20.0] - 2026-11-16

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
