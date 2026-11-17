# Plan: accounts, personalisation and reach

Step plan for `PRD.md`.

Plan of 2026-11-14 for the eight gaps in `PRD.md`. Each step is one PR. Strike a step through (`~~text~~`) when it is done. Conventions for
every step: a change to a listed Python module bumps `package.json` and `pyproject.toml` together and adds a
`CHANGELOG.md` entry; a new Python module is added to `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), `py-modules`
and mypy `files`; coverage stays at 100%; browser work edits `templates/`, follows `design-system/README.md`, keeps the
`data-*` hooks, JSON-LD and WebMCP tools, and adds `connect-src`/`script-src` entries to the page CSP only for the
Supabase host, with SRI on any CDN script. "AC" below means the acceptance criteria of that FR in `PRD.md`.

Decisions made in planning (they change the PRD's wording, so update the PRD in step 0.3):

- Role types start as: Product, R&D (software, hardware, QA, algorithms), DevOps & SRE (added 2026-11-16; its titles
  are not R&D), Data & AI, Design, IT, Finance, Sales & BD, Customer success & support, Other. Marketing, HR, Legal and Operations stay in Other until they have volume.
  Levels: Intern, Junior, Not specified.
- FR-7 spam guard: rate limit per hashed IP and per day, a honeypot field and a daily cap on emails to the maintainer.
  No captcha, so `PRIVACY.md` keeps its no-tracking line.
- Order differs from the PRD's list: step 0 (backend trial) first, then jobs in Supabase, then the filters that depend on
  the new columns (levels, role types, regions), then accounts and stars, the digest, descriptions and the assistant.
  Reason: regions, role types and descriptions are columns of the Supabase jobs table, and the "jobs come from the
  backend" constraint retires `docs/jobs.json` as the data source. The coverage card (FR-5) can ship first.
- No server exists, so everything that needs a secret is a Supabase Edge Function (report a site, confirm and unsubscribe,
  delete account, assistant endpoint). The anon key is public, so row-level security is the whole security model for
  "only their own data". The service-role key lives only in Actions secrets and is used in its own late workflow step,
  never during the crawl, in the same way as `DEPLOY_KEY`.
- The digest is sent from Actions after the daily run (one send per subscriber, each in its own try block), reading
  subscribers with the service-role key. Resend replaces Gmail SMTP for the digest only; `send_alert.py` keeps Gmail
  so the maintainer's failure alerts keep working.
- Current state worth knowing: adapters store no description, the `jobs` table has no description, level or region
  columns, `digest.py` mixes Product with Intern/Junior (the FR-9 bug), the digest goes to one address, and 1,094 of
  6,026 open jobs (2026-11-14) are in Israel under dozens of spellings ("Tel Aviv-Yafo, IL", "Tel-Aviv, IL", "TLV",
  "Israel, Yokneam; Israel, Tel Aviv"), so region grouping needs a normaliser, and 95% is realistic.

## Step 0: Backend ground work (the maintainer does the account steps; nothing ships to users)

- [x] ~~0.1 Create accounts: a free Supabase project, a Google Cloud OAuth client (consent screen limited to email scope),
  a Resend account with a verified project sending address. Store keys as Actions secrets only.~~
  Done in full: the Google test user is added and the Resend sending domain `send.avidor.org` is verified.
- [x] ~~0.2 Trial and limits (PR: a short `docs/`-free note in `CONTRIBUTING.md`, docs-only so no bump). Check on the
  day, from the providers' own pages, not from memory: Supabase free database size, pausing after inactivity, backups,
  Edge Function and egress limits; Resend free daily and monthly send limits and whether they are shared with
  `easy-theory`. Compare with the real data: `data/jobs.db` is 2.7 MB, 6,026 open and 2,681 closed jobs, and full
  descriptions will add perhaps 5 to 20 times the text. Done when: the note states the limits, the projected size
  and the pause-prevention plan (the daily sync counts as activity, to be confirmed). Decide here whether the Supabase
  project can be paused by inactivity and what the dashboard shows when it is.~~
  Done 2026-11-15: the note is in `CONTRIBUTING.md` ("Free-tier limits and what we watch"). Two figures are not confirmed by the providers' pages: whether the daily sync counts as activity (likely, not stated) and how much `easy-theory` uses of the shared Resend quota. The size of the Supabase tables still has to be read once in the Supabase dashboard.
- [x] ~~0.3 Update `PRD.md` with the decisions above (docs-only). Done when: the Open questions section still says none.~~
  Done 2026-11-15, with the decisions made since (the lawyer outcome, Resend, the account and privacy facts) and a decisions log at the end of the PRD. New acceptance criteria were added at the end of their lists, so existing numbers hold: FR-1 AC 7 (sign-in notice), FR-4 AC 12 (full-text care), FR-8 AC 11 and 12 (consent log, footer).

## FR-5 first (quick win, no backend)

- [x] ~~1.1 Complete the coverage gap card. Generate the list from `data/unresolved_candidates.csv` plus the failing and
  blocked crawls, with a reason per site; add a test that parses the counts in the `TODO.md` research item (the 93, the four segment counts and the "(30)") and fails when they disagree with the rows and segments of the CSV (`build_dashboard.py`, `templates/dashboard.js`, `templates/dashboard.css`, tests). Group counts by
  reason, readable at phone width. Done when: FR-5 all three AC hold, the existing gaps tests still pass.~~

## Jobs in Supabase

- [x] ~~2.1 Schema migration files in `supabase/migrations/` (new directory, SQL only, no Python): `jobs` (company, job_id,
  title, team, location, url, posted, first_seen, last_seen, status, plus empty `description`, `details jsonb`,
  `level`, `role_types text[]`, `regions text[]`, `locations text[]`), `crawl_log`, and RLS: public read of open jobs,
  no public write. Done when: migrations apply to a fresh project and a read with the anon key works.~~
  ~~Remaining check, live only: apply the migrations to the Supabase project (`supabase/README.md`) and read with the anon key.~~ Done: migration applied 2026-11-14, anon read and write refusal verified live.
- [x] ~~2.2 `sync_supabase.py`: after each daily run upsert every open job (all 6,026 on 2026-11-14, as in `docs/jobs.json`, not only Israeli ones, so the signed-out dashboard stays whole) and mark missing ones closed, using the
  service-role key; new module (release lists), tests with a fake client. Add a daily-workflow step after the crawl and
  before the deploy push, with the secret exposed only to that step, and the maintainer failure alert covering it.
  Done when: a manual daily run fills the table and a second run changes nothing.~~
  ~~Remaining check, live only: save `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as Actions secrets, run the daily workflow by hand, see the table filled and a second run change nothing (also confirms the key format works as both `apikey` and bearer).~~ Done: two daily runs each synced 6,773 open jobs with no duplicates.
- [x] ~~2.3 Dashboard reads jobs from Supabase (PostgREST with the anon key) instead of `docs/jobs.json`; clear error card
  when the backend is unreachable, no offline copy; CSP `connect-src` gains the project host only. Keep `jobs.jsonld`
  and the WebMCP tools working from the loaded data. Decide in this PR whether `jobs.json` is still published (the
  PRD says no offline copy for the dashboard; keeping it as a public open-data download is allowed but not read).
  Done when: Constraints "Job data is served from the backend" holds, the card script tests and page tests pass, and
  the jobs on screen equal the table (supports FR-6 "jobs match the dashboard").~~
  Decision: `jobs.json` stays published as a public open-data download (the Dataset JSON-LD and the no-script notice point to it); the page never reads it.

## FR-9 and FR-3 (data classification)

- [x] ~~3.1 `classify.py`: `level(title)` giving Intern, Junior or Not specified, and `role_types(title)` giving a list
  from the nine types above (more than one allowed, Other when none), as data tables so a new type is one entry.
  Tests on a fixture of real titles, including "Product with every level" and "Product with Intern and Junior".
  Done when: FR-9 AC 3, 4, 5, 6 and 10 hold in tests, and a report prints the share not in Other.~~
- [x] ~~3.2 `regions.py`: normalise Israeli location strings to the seven regions, handle several cities per job, hybrid
  as office region, "Israel" alone as "Israel, city not given", remote only when open to Israel (otherwise unknown,
  not Remote); unmatched Israeli places keep their own location string; non-Israel jobs untouched. Tests from the real
  distinct-location list in `jobs.db`. Add an unmapped-locations and coverage report to the sync log.
  Done when: FR-3 AC 1 to 7 and 11 hold in tests and the report shows at least 95% of Israeli jobs in a region.~~
- [x] ~~3.3 Sync writes `level`, `role_types`, `regions`, `locations`; migration adds an index for filtering; the
  quality shares (FR-3 95%, FR-9 non-Other) are printed in the daily log. Done when: the columns are populated for all
  open Israeli jobs after a run.~~
  ~~Remaining check, live only: after that manual run, the columns are populated for all open Israeli jobs and the log shows the shares.~~ Done: populated for every open job after the two live runs.
- [x] ~~3.4 Dashboard: level and role-type become separate filters (replacing the blended KIND filter; `build_dashboard.py` moves its `INTERN`, `JUNIOR` and `PM_CORE` imports from `digest.py` to `classify.py`), region and
  location filter with the full list of distinct Israeli locations, "include remote roles that do not say" option,
  region, level and role type shown on the card and in `data-*` hooks and JSON-LD; existing Israel / all locations
  choice stays for non-Israeli jobs. No role type selected means all. Done when: FR-3 AC 8 to 10, FR-9 AC 1, 2, 7 and 8 hold.~~

## FR-1 and FR-2 (accounts, stars, discards, filters)

- [x] ~~4.1 Auth: Google sign-in and sign-out with supabase-js (pinned version, SRI, vendored or CDN allowed by the CSP),
  optional, signed-out dashboard unchanged. Trigger or DB rule that keeps only the email and discards name and photo
  from the provider metadata. Tables `user_prefs` (filters, email settings) and `user_jobs` (star or discard, job
  keys), RLS with `auth.uid() = user_id`, tests for the policies (pgTAP or a script against a test project).
  Done when: FR-1 AC 1 to 3 and 6 hold and a second account cannot read the first one's rows.~~
  Remaining check, live only: apply the migration (`supabase/README.md`), enable the Google provider (live
  `/auth/v1/settings` said `google: false` on 2026-11-15) and switch the Email provider off, switch on the access token
  hook, set the Auth redirect URLs, complete one real sign-in, confirm `public.profiles` holds only your email and the
  access token's `user_metadata` is `{}`, and run `supabase/tests/accounts_rls.sql` against a test project. These need the maintainer's Google login and the Supabase
  dashboard.
- [x] ~~4.2 Stars and discards sync: star button and starred list on the card, star and discard mutually exclusive, local
  queue when offline or the backend fails (no loss), merge on sign-in. Closed-job rules: starred job kept with a
  "no longer listed" mark (needs title, company, location and link kept on the star row), discard removed when its
  job closes, reopened job shows again (daily sync step or a database trigger). Done when: FR-2 AC 1 to 3, 7 to 10 hold.~~
  Remaining check, live only: apply `supabase/migrations/20261115180000_job_choices_cap_and_closed_jobs.sql` (the row
  limit and the trigger that drops a discard when its job closes), run `supabase/tests/job_choices.sql` against a test
  project, then star and discard on one device and see both on a second after signing in. Left to 4.3: the browser's own
  discards and filters are not offered for import at the first sign-in (while signed in, the page shows the account's
  choices only).
- [x] ~~4.3 First-sign-in import of browser discards and filters without duplicates, and filter sync across devices
  (regions, locations, levels, search, company, sort, show/hide options). Done when: FR-2 AC 4 to 6 hold; the launch
  test (sign in on a phone, see laptop stars) passes by hand.~~
  Remaining check, live only: the launch test against the real project: sign in on the laptop and star, discard and set
  filters, then sign in on a phone and see them all; sign in on a browser that holds its own discards, stars and
  filters, accept the offer and see them in the account and on a second device. No migration is needed for this step.
- [x] ~~4.4 Delete account in one step (Edge Function `delete-account`, verifies the caller's token, removes the auth
  user and cascades rows), a sign-out and delete control in the page, `PRIVACY.md` rewritten: email, stars, discards,
  preferences, technical records, never a CV, who can see it, providers abroad, how to delete. Done when: FR-1 AC 4, 5
  hold. This lands before any real user is invited.~~
  Remaining checks, live only (`supabase/README.md`, "Delete account"): deploy with
  `supabase functions deploy delete-account --project-ref dncodqeqpoqwcxdctzco`, run the curl checks (401 without a
  token, 403 for the wrong origin, 405 for GET), then with a throwaway Google account delete the account from the page and
  confirm the user and its rows are gone in the Supabase dashboard, that the old token now answers 401, and that signing
  in again makes a new empty account. This needs the step 4.1 live setup (Google provider, redirect URLs) first.
  Before the first real user is invited, the maintainer also reads `PRIVACY.md` once against the live setup (the access
  token hook switched on, Supabase backups and log retention).

## FR-7 (report a site)

- [x] ~~5.1 Edge Function `report-site`: validates input, honeypot, per-hashed-IP and daily rate limit, a daily cap on
  emails to the maintainer, sends through Resend, stores nothing personal. Done when: a flood in a test run sends at
  most the cap and returns a clear error after it.~~
  ~~Remaining checks, live only, in `supabase/README.md` ("Report a site"): apply the migration `20261116110000_report_site_limits.sql`,
  set the secrets `RESEND_API_KEY` (in Supabase: the GitHub Actions secret of the same name is not visible to functions),
  `REPORT_TO_EMAIL` and `IP_HASH_SECRET`, deploy with `supabase functions deploy report-site`, then run the curl checks:
  a good report arrives by email, a bad input is a 400, the wrong origin a 403, and the per-visitor limit still holds when
  `X-Forwarded-For` and `CF-Connecting-IP` are written by the caller (confirms the visitor address the platform reports).~~
  Added in v0.29.0: a daily cleanup of the two counter tables, so the old hashes really are gone daily (migration `20261119100000_report_site_cleanup.sql`,
  called by `sync_supabase.py` after a good sync), and the lawyer's one-sentence wording ("expires after 24 hours") in the form and `PRIVACY.md`. Live check
  left: apply that migration before the first daily run that includes it, or the sync step fails on the missing function (`supabase/README.md`, "Daily cleanup of the counters").
- [x] ~~5.2 Dashboard: "Report a site" action on the coverage card opens a short form (company, career page link, why it
  matters), confirmation and error messages, no account needed, CSP `connect-src` already covers the host. Process note
  in `CONTRIBUTING.md` for accepting a site (moves to `data/seed_companies.csv` or the gap card with a reason).
  Done when: FR-7 AC 1 to 6 hold.~~
  Remaining checks, live only, once the PR is merged and the page is deployed (and step 5.1's live checks are done):
  open the page at <https://ravidorr.github.io/misra/>, open the coverage gap card and press "Report a site"; send one real
  report and confirm the email reaches the maintainer and the page shows "Report sent"; press Send with the fields empty
  and with a bad link and confirm the messages appear beside the fields and nothing is sent (the browser's network tab
  shows no request); and, if the daily limit of 3 can be spent, send a fourth report and confirm the page says to try
  again tomorrow. Also confirm in the browser console that the request goes to the project's `report-site` function and
  that no `Content-Security-Policy` error appears.

## FR-8 (personalised digest; the lawyer check is a gate in front of step 6.6)

- [x] ~~6.1 Subscriber tables and functions: `subscriptions` (user_id, address, frequency default daily, filters
  snapshot, the consent log `consent_given_at`, `consent_confirmed_at`, `unsubscribed_at`, hashed confirm and
  unsubscribe tokens), `emailed` (user_id, job key) to avoid repeats. Edge Functions `confirm-email` and `unsubscribe`
  (one click, no sign-in, token-based) and the static page `email.html`. Done when: FR-8 AC 7 first half and AC 11 (consent log) hold in tests.~~
  Remaining checks, live only (`supabase/README.md`, "Subscribers: confirm and unsubscribe"): apply the migration
  `20261117100000_subscriptions_and_emailed.sql` and record it with `supabase migration repair 20261117100000 --status applied --remote`
  if it was pasted into the SQL editor, deploy with `supabase functions deploy confirm-email --project-ref dncodqeqpoqwcxdctzco`
  and `supabase functions deploy unsubscribe --project-ref dncodqeqpoqwcxdctzco`, wait for the next daily build to publish
  `email.html`, then run the curl checks with a throwaway row: a GET never confirms (405), a POST confirms and a repeat
  says already confirmed, an RFC 8058 POST with no key unsubscribes (proves `verify_jwt = false`), a GET unsubscribe answers
  303 to `email.html#unsubscribed` on the default domain, and an unsubscribe keeps the timestamps and clears the filters and
  `emailed` rows.
- [x] ~~6.2 Dashboard digest settings: on/off, address (Google email by default), daily or weekly, "Use these filters for
  my email" copying regions, levels, role types and keywords only. Sends the confirmation email through Resend.
  Done when: FR-8 AC 1 to 3 hold.~~
  The confirmation email is plain text plus an HTML version of the same words (multipart), subject "Confirm your Misra job alerts", with `Reply-To` `raanan@avidor.org`, the
  lawyer's body ("Someone (hopefully you) turned on {{schedule}} job alerts for {{user_email}} on Misra." and the
  rest) and this footer: `Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org). If you did not ask for this email, ignore it, and nothing will be sent to you.` then the privacy and terms links.
  It does not carry the digest's "you signed up" sentence, because its reader may be someone who did not ask.
  Built as the Edge Function `email-settings` (turn on, turn off; the user id only from the verified token; limits of 3
  confirmation emails per user, 3 per address and 30 in all per day, counted in the database; the same unsubscribe
  clean-up as the link), the migration `20261118100000_digest_signup_and_limits.sql` and the "Job alerts" dialog
  (`templates/digest.js`). The whole signup is behind two switches, both off at first: `DIGEST_SIGNUP_ENABLED = False` in
  `build_dashboard.py` (the page has no button, dialog or script; on since v0.31.0) and the function secret `DIGEST_SIGNUP_ENABLED` (the
  function refuses to create a subscription unless it is `true`). Step 6.6 switches them on (`supabase/README.md`,
  "Job alerts"): set `DIGEST_SIGNUP_ENABLED = True` in `build_dashboard.py` and run
  `supabase secrets set DIGEST_SIGNUP_ENABLED=true --project-ref dncodqeqpoqwcxdctzco`. The control carries the lawyer's wording (label "Email me new matching roles", Daily or Weekly, helper text with the
  address and links to the Terms and the Privacy Notice, and "Check ADDRESS for a confirmation link..." after turning on);
  the confirmation email is his text verbatim. To close signups again, unset the secret; never set the build constant back
  to `False` once anyone has subscribed (it also removes "Turn the email off" from the page).
  Done live on 2026-11-15: the migration is applied and `email-settings` is deployed; the checks that create no subscription passed
  (no token 401, the publishable key as the bearer 401, the preflight 204). Still to check, signed in, when signups open: turning on is 403 `signup_closed` while the secret is unset, turning off with no row is a 200, and the maintainer's own turn-on flow below.
  (The original list of checks, for reference: apply the migration and record it, deploy `email-settings`, then run the five curl checks of
  `supabase/README.md` ("Check it live", none of them creates a subscription): no token and the publishable key as the bearer
  are 401 from the gateway, the preflight answers 204 with `authorization` allowed (if the gateway refuses it, set
  `verify_jwt = false`), turning on is 403 `signup_closed` while the secret is unset, and turning off with no row is a 200. Once signups open (step 6.6),
  with the maintainer's own account: turn it on, read the confirmation email (plain text and HTML, `Reply-To` `raanan@avidor.org`, the
  link opens `email.html`), confirm it, and see the dialog say "on".) Settled in step 6.3: the unsubscribe link is a stateless
  HMAC token, so this function no longer makes an unsubscribe token (the `NOT NULL` hash column gets a random value that nothing reads).
- [x] ~~6.3 `send_digests.py` replaces the per-recipient logic of `send_email.py`: match each confirmed subscriber's
  filters against jobs first seen after their confirmation, never emailed before, via the new `level`/`role_types`
  columns (this removes the Product-plus-Intern/Junior bug); no email when nothing new; weekly summary; per-user
  try block; project From address via Resend; unsubscribe link and List-Unsubscribe headers; the confirmation email, the digest footer and the headers are exactly the texts in `supabase/email-texts.md` (copy them line for line, line breaks included); set Reply-To to `raanan@avidor.org`, because the lawyer said people can reply "Remove" and the sending address `digest@send.avidor.org` cannot receive mail; and send these headers in the Resend payload of EVERY digest email (RFC 8058 one-click, as Gmail and Yahoo require): `List-Unsubscribe: <https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/unsubscribe?t={{token}}>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (the function reads the token from `?t=`). The `unsubscribe` function accepts both GET (a person clicks the footer link) and POST (the mail client's own button), and HEAD never changes anything. Coverage-gap content
  never appears in it. Remove `digest.py` selection logic that FR-9 replaces; keep the maintainer failure email on
  `send_alert.py`. The old single-recipient digest keeps running until this path has delivered to the maintainer. Done when: FR-8 AC 4 to 9 and AC 12 (footer) hold in tests, FR-9 AC 9 holds.~~
  Built (v0.27.0), not wired into the workflow and not enabled (the signup switches stay off). Decisions:
  - Unsubscribe token (the open point): stateless, `<subscription id>.<HMAC-SHA256(UNSUBSCRIBE_SECRET, id)>` (64 hex characters; the id is
    `subscriptions.user_id`). `send_digests.py` makes it for every email; the `unsubscribe` function recomputes the MAC (constant-time
    compare) and only then calls the existing SQL function `unsubscribe_user`, so no migration is needed. The `unsubscribe_token_hash`
    column is now a random placeholder that nothing reads (dropping it is a later clean-up). `UNSUBSCRIBE_SECRET` is a function secret
    and an Actions secret (at least 32 characters, for example `openssl rand -hex 32`; both sides must hold the same value).
  - Jobs and subscribers are read from Supabase with the service-role key (jobs after the sync), so the digest and the dashboard
    see the same rows. A job counts as "new for this user" when it is open, in Israel (a region or a place of its own), first seen after
    `consent_confirmed_at` and not in `emailed`. The silent baseline of `run.py` is kept: the jobs a company had when first seen are
    never sent (limit: an Israel-only company with no Israeli job on its first crawl has its first one treated as baseline).
  - Weekly is sent on Sundays (`WEEKLY_DAY`), the first working day in Israel, and holds what is still open, new and not yet emailed.
    A failed Sunday is not lost: the jobs stay un-emailed and go out the next Sunday.
  - Sends go out one by one, 0.6 s apart (Resend's default limit is 2 a second), with one retry after a 429 and an `Idempotency-Key`
    so a re-run within a day after a failed record does not send the same email again (a record failure left to the next day can still repeat jobs). Jobs are recorded in `emailed` only after Resend accepted the email. The log
    is public, so it holds counts only. Exit code 1 after every user was handled when any failed (the workflow's alert fires in 6.4).
  - The digest subject and the list of roles are the project's own wording (the lawyer gave the footer and the headers); they are in
    `supabase/email-texts.md` and pinned. `digest.py` and `send_email.py` are untouched: they keep running as the maintainer's digest
    until 6.4 makes the maintainer an ordinary subscriber; that step (v0.47.0) removed both, with the selection logic (`select`, `ROLE`, `EXCLUDE`) that the FR-9 rule replaces.
  Remaining checks, live only: set `UNSUBSCRIBE_SECRET` (function secret and Actions secret, same value), deploy `unsubscribe`
  (`supabase functions deploy unsubscribe --project-ref dncodqeqpoqwcxdctzco`), run the curl checks of `supabase/README.md`
  ("Subscribers: confirm and unsubscribe") with a throwaway row, and run `send_digests.py --dry-run` once with the real secrets.
- [x] ~~6.4 Daily workflow: add the digest step after the sync with the service-role key and Resend key in that step
  only, keep the failure alert, and make the maintainer an ordinary subscriber. Done when: FR-8 AC 10 holds, and a manual run with a test
  subscriber delivers one email and a second run delivers none.~~
  Built in v0.28.0 in two halves, both done. Verified live on 2026-11-16: one digest email was delivered to the subscriber (the maintainer), and a second run delivered none.
  - Done: `daily.yml` runs `send_digests.py` after the sync and before the build and the deploy push, only after a good sync; the four secrets are on that
    step only; a failure fails the run, sends the `send_alert.py` email (its step list shows `digests=`) and does not stop the build or the data commit;
    the manual input `digest_dry_run` passes `--dry-run`. `tests/test_daily_workflow.py` pins all of it. No built file was added, so the staging list is unchanged.
  - Switch, done in v0.47.0: the old single-recipient digest (`send_email.py`) and the workflow constant `LEGACY_MAINTAINER_DIGEST` are gone, with the selection logic that FR-9
    replaced in `digest.py` (`select`, `ROLE`, `EXCLUDE`); `digest.py` is deleted (its `DB` constant moved to `common.py`). The coverage gaps stay in the job log: the workflow step "Log the coverage gaps" (`python run.py --gaps`, `log_coverage_gaps`) prints the
    `coverage gaps: ...` line and a `::warning::` annotation for a new gap, as `send_email.py` did, so a crawl problem is never silent. A broken check turns that step red, so `send_alert.py` (Gmail, kept) emails it like any failed step.
  - Live checks, done 2026-11-16: the workflow run with a subscriber delivered one email, and the second run delivered none.
- [x] ~~6.5 Volume check before opening: count subscribers against the Resend free limit shared with `easy-theory`, write
  the number and the upgrade trigger in `CONTRIBUTING.md`. Done when: the limit and current use are written down.~~
  Done in v0.28.0: `CONTRIBUTING.md`, "Resend volume", states the free limits as Resend's pricing and quotas pages give them on 2026-11-15, the arithmetic, zero real subscribers today, the
  upgrade trigger (about 60 daily subscribers, or 80 of 100 a day or 2,400 of 3,000 a month in use, then Pro or close signups) and a query for the count. Not confirmed by the pages: whether the quota is per team or per domain
  and how much `easy-theory` uses; read both in the Resend dashboard.
- [x] ~~6.6 GATE: the lawyer review from "Before launch" and the addendum, with written answers (answered 2026-11-15; the
  outcome is in the PRD's "Before launch"; the gate stays until its wording is in). Nothing sends to a real
  user, and the sign-up control stays hidden behind a flag, until the answers are in and any required wording is added
  to the emails, `PRIVACY.md`, a new `TERMS.md` (as-is disclaimer) and the notice shown when the sign-in button is pressed (FR-1 AC 7). Done when: the answers are filed, those four are in, and the flag is switched on (both switches of step 6.2: the constant in `build_dashboard.py` and the function secret).~~
  Closed: the wording is in, the constant is `True` (v0.31.0), and the function secret is set (signups are open, and the maintainer subscribed, confirmed and received a digest on 2026-11-16).
  Progress: the wording is in for `PRIVACY.md` (controller, why and voluntary, providers and countries, rights under
  Sections 13 and 14, contact), `TERMS.md` (linked from the site footer and the sign-in notice), the notice shown when the
  sign-in button is pressed (FR-1 AC 7, with links to both) and the footer links on the page. The email footer text is written in
  step 6.3 above and is built there. Crawler politeness is in (v0.23.0): one honest User-Agent
  (`Misra/<version> (+site; contact)`), a per-host delay for the browser crawl equal to the adapters', the `robots.txt`
  check (enforced: `ROBOTS_ENFORCED` in `common.py`; the published ATS APIs are not treated as pages, but SmartRecruiters'
  `api.smartrecruiters.com` disallows everyone, so its five companies are skipped until SmartRecruiters gives permission;
  a company skipped for 7 days in a row has its open jobs closed, from v0.26.0), and the company exclusion list `data/excluded_companies.csv` (a company
  on it is never crawled, is deleted from `data/jobs.db` and from Supabase `jobs`, `user_jobs` and `emailed`, and is
  off the exports and the coverage card; the process is in `CONTRIBUTING.md`). ~~Still to do before the gate closes: the
  digest email itself (step 6.3, with its footer and headers), the flag, and the present-tense `PRIVACY.md` edits below.~~ Done in v0.31.0 except the function secret (see "Opening day" below). The secret is set, so the gate is closed.
  Small wording and checks done in v0.29.0: the weekly choice reads "Weekly (Sundays)" (the lawyer's suggestion), the coverage card says what happens to the roles of a
  company skipped for robots.txt in both phases (kept for up to 7 days, then closed, with the days skipped), and the SQL policy tests now run in CI (job `sql-tests`).
  Second lawyer answer (2026-11-15), applied: the `auth.users` sentence in `PRIVACY.md` now says why Supabase keeps
  Google's name and photo, and `TERMS.md` gained the liability, acceptable-use and governing-law points. ~~Still to do on
  the day the flag is switched on (the text is true only then, so it is not in `PRIVACY.md` yet): in `PRIVACY.md`, (a)
  "Why we collect it": "If you turn on the email digest, your email address is also used to send you the job alerts you
  asked for, and for nothing else."; (b) "Email settings": rewrite the paragraph in the present tense (the address, the
  filters you copied, daily or weekly, when you turned it on, when you confirmed, when you unsubscribed, the jobs
  already emailed to you, and what an unsubscribe keeps and deletes); (c) "Resend": "Resend (Ireland): sends the
  site-report email to the maintainer and, if you turn on the email digest, processes your email address and the
  matched job links to deliver your confirmation and digest emails." Also on that day, the line "By turning this on you
  agree to the Terms and the Privacy notice" next to the email control (built in 6.2) must be visible.~~
  Done in v0.31.0 (the PR that sets `DIGEST_SIGNUP_ENABLED = True`): (a), (b) and (c) are in `PRIVACY.md` in the present tense (and the short version says the email
  settings are kept too), the "not live" and "planned" wording is gone from it and from the Providers section, the guard test in `tests/test_legal_wording.py`
  now requires the present-tense wording while the constant is on, and the agreement line is in the dialog (visible, links of at least 44px; tested in a browser
  and pinned in the markup). Third lawyer answer (2026-11-15), applied in the same PR: the spam-guard wording is "is deleted in the next daily cleanup" (form and `PRIVACY.md`,
  no "24 hours"); the confirmation-email counters get the same daily cleanup (migration `20261120100000_confirm_counters_cleanup.sql`, `confirm_email_cleanup`, called by `sync_supabase.py`
  after `report_site_cleanup`); `SECURITY.md` scopes `BACKUP_DEPLOY_KEY` to `ravidorr/misra-backups` only, adds its rotation to the yearly review (by 2027-11-15) and the
  rule to delete any local clone of the backup repository at once; and the restore without `--create-auth-users` skips every user id no longer in `auth.users` (never resurrects a
  deleted account; `CONTRIBUTING.md`, "Restore", with a second rehearsal). No encryption of our own is claimed anywhere.
  Opening day, the order the maintainer followed (signups are open now: the maintainer's turn-on, confirmation and a delivered digest on 2026-11-16 show the function secret is set; that the signed-in checks with the secret unset were run is not recorded here): apply the two cleanup migrations (before merging, or the next daily sync fails), merge, wait for `publish-docs`, run the signed-in checks that need the
  secret unset (`supabase/README.md`, "Check it live"), then `supabase secrets set DIGEST_SIGNUP_ENABLED=true --project-ref dncodqeqpoqwcxdctzco`, then the maintainer's own turn-on flow
  and `send_digests.py` twice with a test subscriber. The legacy maintainer digest was removed in v0.47.0, after one digest from `send_digests.py` reached the maintainer (step 6.4).

## FR-4 (richer job data)

- [ ] 7.1 Descriptions in the crawl: extend adapters one site at a time (Greenhouse, Lever, Comeet, Ashby and so on,
  best effort) to return `description` and `details`; a site that gives none, or fails to, does not stop the daily
  update. Start with the adapters that cover the most jobs. **Decision (the maintainer's): the full text lives in Supabase
  only**, never in `data/jobs.db` or any committed file (the size of the daily commit, and other companies' full postings
  are not published on GitHub), so the SQLite `jobs` table does not gain the columns. Done when: FR-4 AC 1, 2, 3 and 9
  hold for the first adapters (AC 3: the share with a full description is printed in the daily log).
  First slice done in v0.49.0: `greenhouse`, `greenhouse_url`, `lever`, `ashby`, `workable`, `comeet` and `amazon`, from the
  request each already makes (no new request, host or path), 4,760 of the 6,814 open jobs (70 percent). The crawl leaves
  the texts in a work file in `$RUNNER_TEMP`; the sync step writes them to the `description` and `details` columns that
  migration 2.1 already created (no migration), with a hash in `details` to skip an unchanged text; the log line per ATS
  is in the crawl step. CONTRIBUTING.md, "Job descriptions", has the design, the size cap (20,000 characters) and the
  storage estimate. The text is not readable through the public API (migration `20261121100000_jobs_text_not_public.sql`, column-level `select`): step 7.3 searches it through a function that returns keys only and step 8.1 (`get_job`) returns it to the assistant, as the lawyer approved on 2026-11-16 (PRD decisions log). Left: the browser crawl (1,589 jobs, 23 percent: no standard field), Workday (318, one request per job
  and a new path to check against `robots.txt`), Phenom (89, a teaser only), Oracle HCM, `comeet_page`, JazzHR and BambooHR
  (a request per job), SmartRecruiters (skipped by `robots.txt`). After the first daily run with descriptions: check the
  log line and the size of the `jobs` table in the dashboard (Reports, Database), and write both in CONTRIBUTING.md.
- [ ] 7.2 Short description: a public `snippet` column holding the first 255 characters of the description as written, cut
  at a word boundary, none when no description; sync stores it; the card and the detail view show it, with the link
  "Read full description & apply on `<Company>`" to the employer's page. Done when: FR-4 AC 4 and 8 hold.
- [x] 7.3 Detail view from the card (it shows the snippet and the "Read full description & apply" button; the full text is
  not shown) and search over the full text (Postgres full-text index, also used by the digest keyword filter) through
  a database function that returns matching job keys only. Done when: FR-4 AC 5 and 6 hold. FR-4 AC 11 (AI summary only if users ask) is a standing policy, not a step.
- [ ] 7.4 Storage check after one month of descriptions against the free database size, and the trim of closed jobs'
  text: set the full text to NULL 30 days after the job closes (the lawyer's retention rule; the snippet and the other
  columns stay). Done when: FR-4 AC 10 and 12 hold, written in the note.

## FR-6 (assistant connection)

- [ ] 8.1 Edge Function MCP endpoint, public and read-only: `search_jobs` (region, level, role type, keyword incl. full
  text; it returns keys, titles and snippets), `get_job` (everything collected, including the full text, for the
  assistant), no user data, rate limited. Same query code path as the dashboard (shared SQL function) so the results
  match. The text is exposed to assistants and search only by this step and 7.3, as the lawyer approved on 2026-11-16. Done when: FR-6 AC 1 to 4 and 8 hold against a real MCP client.
  **Built, off, waiting for the lawyer (2026-11-17), so not ticked.** The Edge Function `mcp` (MCP over HTTP, JSON answers,
  no sessions) with `search_jobs` (the SQL function `search_jobs`, the dashboard's rules, `search_job_keys` for the text) and
  `get_job` (one job by company and job_id, the full text, open or closed for less than 30 days), rate limited per caller
  and for everyone through hashed addresses (`assistant_ip_limits`, cleaned by the daily cleanup). It answers 404 unless
  the function secret `ASSISTANT_ENDPOINT_ENABLED` is `true`; it is not deployed and nobody sets the secret before the lawyer
  confirms (his request of 2026-11-17). The migration `20261125100000_assistant_endpoint.sql` is harmless while it is off and
  is applied by the maintainer. Tested with the protocol over HTTP in vitest and once with the official TypeScript client.
  To finish: the lawyer's answers, the migration, `supabase functions deploy mcp`, the secret, the live check in
  `supabase/README.md` ("Assistant endpoint"), a real assistant, then tick this step.
- [ ] 8.2 "Connect your assistant" page on the dashboard with plain step-by-step instructions for the common assistants
  tested by hand, README cut to a short developer note, `llms.txt` updated. Done when: FR-6 AC 5 to 7 hold; a person with
  no technical skills connects in a couple of minutes (hand test).
  **Built, off, waiting for the lawyer (2026-11-17), so not ticked.** `ASSISTANT.md` (the words, by the technical writer, steps for
  Claude and ChatGPT checked against their current help pages on 2026-11-17, and any other assistant) becomes `docs/assistant.html`
  in the template of the privacy page, with a footer link, both behind `ASSISTANT_PAGE_ENABLED` in `build_dashboard.py`
  (`False`); the README has a short developer note. To finish: the lawyer's answers (his wording goes into the page, the
  terms and the privacy notice), then set the constant to `True` together with the function secret, add the page to `llms.txt`
  (a test requires it), and do the hand test with a person who is not technical (Claude and ChatGPT).

## Retire SQLite (last, only when the Supabase path has run clean for a while)

- [ ] 9.1 Move the crawl's writes into Supabase, with a regular export of all tables into the repository (history and a
  restore path) before `data/jobs.db` stops being committed. Update `AGENTS.md`, the workflow, the deploy flow and the
  `protect-main` ruleset note together. Done when: a daily run works without `data/jobs.db`, a restore from the export
  is rehearsed, and the Notes for the plan in `PRD.md` are all met.
  Backups/export before the first subscriber (part of the restore path), built in v0.30.0: the account tables (`profiles`, `user_prefs`, `user_jobs`,
  `subscriptions`, `emailed`; not `jobs`, which the crawl rebuilds) go every day to the PRIVATE repository `ravidorr/misra-backups`, not into this public one.
  `export_accounts.py` (JSON per table, sorted, counts only in the log), `publish_backup.py` (commit if changed; replaces the history with one commit when it
  is 28 days old, so a deleted account leaves the backups within 30 days) and `restore_accounts.py` (one idempotent SQL script, foreign-key order, never deletes).
  The workflow step `backup` runs last, after the deploy push, with `BACKUP_DEPLOY_KEY` and the service-role key on that step only, and a failure triggers
  the maintainer alert. The restore was rehearsed on 2026-11-15 against a local Postgres (`CONTRIBUTING.md`, "Backups of the account tables").
  Send to lawyer: `PRIVACY.md` now says that a daily backup copy of the account data is kept in a private GitHub repository (United States, access only
  the maintainer) for up to 30 days, that a deleted account leaves the backups within that time, and that GitHub may keep replaced copies briefly;
  `SECURITY.md` changed with it. The lawyer should see the wording ("What we store", "Providers", "How long we keep it", "How to delete your data") before the first real subscriber.
  Still to do: live checks (set up is done; confirm the first daily run commits to `ravidorr/misra-backups`, and rehearse a restore into a scratch Supabase project, which also
  tests `--create-auth-users` against the real `auth.users`); the other half of 9.1 (move the crawl's writes into Supabase and stop committing `data/jobs.db`, with the
  `AGENTS.md`, deploy-flow and `protect-main` updates) is not started; the export of `jobs` and `crawl_log` into the repository is only needed then.

## Checks that close the plan

- [ ] Every AC is named in a step's "Done when". Mapping: FR-1 AC 1-3, 6: 4.1; AC 4, 5: 4.4. FR-2 AC 1-3, 7-10: 4.2;
  AC 4-6: 4.3. FR-3 AC 1-7, 11: 3.2; AC 8-10: 3.4. FR-4 AC 1-3, 9: 7.1; AC 4, 8: 7.2; AC 5, 6: 7.3; AC 7: 8.1;
  AC 10, 12: 7.4; AC 11: policy. FR-5: 1.1. FR-6 AC 1-4, 8: 8.1; AC 5-7: 8.2. FR-7: 5.1 (spam AC), 5.2. FR-8 AC 1-3: 6.2;
  AC 4-6, 8, 9: 6.3; AC 7: 6.1; AC 10: 6.4; AC 11 (consent log): 6.1; AC 12 (footer): 6.3. FR-1 AC 7 (sign-in notice) and the `TERMS.md` disclaimer: 6.6. FR-4 AC 12: also applied in 7.1. FR-9 AC 3-6, 10: 3.1; AC 1, 2, 7, 8: 3.4; AC 9: 6.3. Re-check this when a
  step is split.
- [x] ~~Success metrics are read from Supabase tables and Resend reports monthly; add the queries to `CONTRIBUTING.md`
  with step 6.4 (sign-ins, 30-day returns, subscribers, share starring, region and description shares, accepted sites).~~
  Done in v0.28.0 ("Monthly success metrics" in `CONTRIBUTING.md`). Accepted sites are not in a table (a report is an email), so that one is a `git log` count of additions to `data/seed_companies.csv`.
