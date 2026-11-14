# Plan: accounts, personalisation and reach

Step plan for `PRD.md`.

Plan of 2026-11-14 for the eight gaps in `PRD.md`. Each step is one PR. Strike a step through (`~~text~~`) when it is done. Conventions for
every step: a change to a listed Python module bumps `package.json` and `pyproject.toml` together and adds a
`CHANGELOG.md` entry; a new Python module is added to `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), `py-modules`
and mypy `files`; coverage stays at 100%; browser work edits `templates/`, follows `design-system/README.md`, keeps the
`data-*` hooks, JSON-LD and WebMCP tools, and adds `connect-src`/`script-src` entries to the page CSP only for the
Supabase host, with SRI on any CDN script. "AC" below means the acceptance criteria of that FR in `PRD.md`.

Decisions made in planning (they change the PRD's wording, so update the PRD in step 0.3):

- Role types start as: Product, R&D (software, hardware, QA, DevOps, algorithms), Data & AI, Design, IT, Finance,
  Sales & BD, Customer success & support, Other. Marketing, HR, Legal and Operations stay in Other until they have volume.
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

- [ ] 0.1 Create accounts: a free Supabase project, a Google Cloud OAuth client (consent screen limited to email scope),
  a Resend account with a verified project sending address. Store keys as Actions secrets only.
- [ ] 0.2 Trial and limits (PR: a short `docs/`-free note in `CONTRIBUTING.md`, docs-only so no bump). Check on the
  day, from the providers' own pages, not from memory: Supabase free database size, pausing after inactivity, backups,
  Edge Function and egress limits; Resend free daily and monthly send limits and whether they are shared with
  `easy-theory`. Compare with the real data: `data/jobs.db` is 2.7 MB, 6,026 open and 2,681 closed jobs, and full
  descriptions will add perhaps 5 to 20 times the text. Done when: the note states the limits, the projected size
  and the pause-prevention plan (the daily sync counts as activity, to be confirmed). Decide here whether the Supabase
  project can be paused by inactivity and what the dashboard shows when it is.
- [ ] 0.3 Update `PRD.md` with the decisions above (docs-only). Done when: the Open questions section still says none.

## FR-5 first (quick win, no backend)

- [x] ~~1.1 Complete the coverage gap card. Generate the list from `data/unresolved_candidates.csv` plus the failing and
  blocked crawls, with a reason per site; add a test that parses the counts in the `TODO.md` research item (the 93, the four segment counts and the "(30)") and fails when they disagree with the rows and segments of the CSV (`build_dashboard.py`, `templates/dashboard.js`, `templates/dashboard.css`, tests). Group counts by
  reason, readable at phone width. Done when: FR-5 all three AC hold, the existing gaps tests still pass.~~

## Jobs in Supabase

- [x] ~~2.1 Schema migration files in `supabase/migrations/` (new directory, SQL only, no Python): `jobs` (company, job_id,
  title, team, location, url, posted, first_seen, last_seen, status, plus empty `description`, `details jsonb`,
  `level`, `role_types text[]`, `regions text[]`, `locations text[]`), `crawl_log`, and RLS: public read of open jobs,
  no public write. Done when: migrations apply to a fresh project and a read with the anon key works.~~
  Remaining check, live only: apply the migrations to the Supabase project (`supabase/README.md`) and read with the anon key.
- [ ] 2.2 `sync_supabase.py`: after each daily run upsert every open job (all 6,026 today, as in `docs/jobs.json`, not only Israeli ones, so the signed-out dashboard stays whole) and mark missing ones closed, using the
  service-role key; new module (release lists), tests with a fake client. Add a daily-workflow step after the crawl and
  before the deploy push, with the secret exposed only to that step, and the maintainer failure alert covering it.
  Done when: a manual daily run fills the table and a second run changes nothing.
- [ ] 2.3 Dashboard reads jobs from Supabase (PostgREST with the anon key) instead of `docs/jobs.json`; clear error card
  when the backend is unreachable, no offline copy; CSP `connect-src` gains the project host only. Keep `jobs.jsonld`
  and the WebMCP tools working from the loaded data. Decide in this PR whether `jobs.json` is still published (the
  PRD says no offline copy for the dashboard; keeping it as a public open-data download is allowed but not read).
  Done when: Constraints "Job data is served from the backend" holds, the card script tests and page tests pass, and
  the jobs on screen equal the table (supports FR-6 "jobs match the dashboard").

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
- [ ] 3.3 Sync writes `level`, `role_types`, `regions`, `locations`; migration adds an index for filtering; the
  quality shares (FR-3 95%, FR-9 non-Other) are printed in the daily log. Done when: the columns are populated for all
  open Israeli jobs after a run.
- [ ] 3.4 Dashboard: level and role-type become separate filters (replacing the blended KIND filter; `build_dashboard.py` moves its `INTERN`, `JUNIOR` and `PM_CORE` imports from `digest.py` to `classify.py`), region and
  location filter with the full list of distinct Israeli locations, "include remote roles that do not say" option,
  region, level and role type shown on the card and in `data-*` hooks and JSON-LD; existing Israel / all locations
  choice stays for non-Israeli jobs. No role type selected means all. Done when: FR-3 AC 8 to 10, FR-9 AC 1, 2, 7 and 8 hold.

## FR-1 and FR-2 (accounts, stars, discards, filters)

- [ ] 4.1 Auth: Google sign-in and sign-out with supabase-js (pinned version, SRI, vendored or CDN allowed by the CSP),
  optional, signed-out dashboard unchanged. Trigger or DB rule that keeps only the email and discards name and photo
  from the provider metadata. Tables `user_prefs` (filters, email settings) and `user_jobs` (star or discard, job
  keys), RLS with `auth.uid() = user_id`, tests for the policies (pgTAP or a script against a test project).
  Done when: FR-1 AC 1 to 3 and 6 hold and a second account cannot read the first one's rows.
- [ ] 4.2 Stars and discards sync: star button and starred list on the card, star and discard mutually exclusive, local
  queue when offline or the backend fails (no loss), merge on sign-in. Closed-job rules: starred job kept with a
  "no longer listed" mark (needs title, company, location and link kept on the star row), discard removed when its
  job closes, reopened job shows again (daily sync step or a database trigger). Done when: FR-2 AC 1 to 3, 7 to 10 hold.
- [ ] 4.3 First-sign-in import of browser discards and filters without duplicates, and filter sync across devices
  (regions, locations, levels, search, company, sort, show/hide options). Done when: FR-2 AC 4 to 6 hold; the launch
  test (sign in on a phone, see laptop stars) passes by hand.
- [ ] 4.4 Delete account in one step (Edge Function `delete-account`, verifies the caller's token, removes the auth
  user and cascades rows), a sign-out and delete control in the page, `PRIVACY.md` rewritten: email, stars, discards,
  preferences, technical records, never a CV, who can see it, providers abroad, how to delete. Done when: FR-1 AC 4, 5
  hold. This lands before any real user is invited.

## FR-7 (report a site)

- [ ] 5.1 Edge Function `report-site`: validates input, honeypot, per-hashed-IP and daily rate limit, a daily cap on
  emails to the maintainer, sends through Resend, stores nothing personal. Done when: a flood in a test run sends at
  most the cap and returns a clear error after it.
- [ ] 5.2 Dashboard: "Report a site" action on the coverage card opens a short form (company, careers link, why it
  matters), confirmation and error messages, no account needed, CSP `connect-src` already covers the host. Process note
  in `CONTRIBUTING.md` for accepting a site (moves to `data/seed_companies.csv` or the gap card with a reason).
  Done when: FR-7 AC 1 to 6 hold.

## FR-8 (personalised digest; the lawyer check is a gate in front of step 6.6)

- [ ] 6.1 Subscriber tables and functions: `subscriptions` (user_id, address, frequency default daily, filters
  snapshot, confirmed_at, unsubscribed_at, unsubscribe token), `emailed` (user_id, job key) to avoid repeats.
  Edge Functions `confirm-email` and `unsubscribe` (one click, no sign-in, token-based). Done when: FR-8 AC 7 first
  half holds in tests.
- [ ] 6.2 Dashboard digest settings: on/off, address (Google email by default), daily or weekly, "Use these filters for
  my email" copying regions, levels, role types and keywords only. Sends the confirmation email through Resend.
  Done when: FR-8 AC 1 to 3 hold.
- [ ] 6.3 `send_digests.py` replaces the per-recipient logic of `send_email.py`: match each confirmed subscriber's
  filters against jobs first seen after their confirmation, never emailed before, via the new `level`/`role_types`
  columns (this removes the Product-plus-Intern/Junior bug); no email when nothing new; weekly summary; per-user
  try block; project From address via Resend; unsubscribe link and List-Unsubscribe headers. Coverage-gap content
  never appears in it. Remove `digest.py` selection logic that FR-9 replaces; keep the maintainer failure email on
  `send_alert.py`. The old single-recipient digest keeps running until this path has delivered to the maintainer. Done when: FR-8 AC 4 to 9 hold in tests, FR-9 AC 9 holds.
- [ ] 6.4 Daily workflow: add the digest step after the sync with the service-role key and Resend key in that step
  only, keep the failure alert, and make the maintainer an ordinary subscriber. Done when: FR-8 AC 10 holds, and a manual run with a test
  subscriber delivers one email and a second run delivers none.
- [ ] 6.5 Volume check before opening: count subscribers against the Resend free limit shared with `easy-theory`, write
  the number and the upgrade trigger in `CONTRIBUTING.md`. Done when: the limit and current use are written down.
- [ ] 6.6 GATE: the lawyer review from "Before launch" and the addendum, with written answers. Nothing sends to a real
  user, and the sign-up control stays hidden behind a flag, until the answers are in and any required wording is added
  to the emails and `PRIVACY.md`. Done when: the answers are filed and the flag is switched on.

## FR-4 (richer job data)

- [ ] 7.1 Descriptions in the crawl: extend adapters one site at a time (Greenhouse, Lever, Comeet, Ashby and so on,
  best effort) to return `description` and `details`; `jobs` table gains the columns in SQLite too; a site that gives
  none, or fails to, does not stop the daily update. Start with the adapters that cover the most jobs. Done when: FR-4
  AC 1, 2, 3 and 9 hold for the first adapters (AC 3: the share with a full description is printed in the daily log).
- [ ] 7.2 Short description: first 255 characters as written, cut at a word boundary, none when no description; sync
  stores it; card shows it. Done when: FR-4 AC 4 and 8 hold.
- [ ] 7.3 Drill-down on the card (detail view without leaving the dashboard) and search over the full text (Postgres
  full-text index, also used by the digest keyword filter). Done when: FR-4 AC 5 and 6 hold. FR-4 AC 11 (AI summary only if users ask) is a standing policy, not a step.
- [ ] 7.4 Storage check after one month of descriptions against the free database size, with the trim plan (store
  description text only for open jobs, drop on close after N days). Done when: FR-4 AC 10 holds, written in the note.

## FR-6 (assistant connection)

- [ ] 8.1 Edge Function MCP endpoint, public and read-only: `search_jobs` (region, level, role type, keyword incl. full
  text), `get_job` (everything collected), no user data, rate limited. Same query code path as the dashboard (shared
  SQL function) so the results match. Done when: FR-6 AC 1 to 4 and 8 hold against a real MCP client.
- [ ] 8.2 "Connect your assistant" page on the dashboard with plain step-by-step instructions for the common assistants
  tested by hand, README cut to a short developer note, `llms.txt` updated. Done when: FR-6 AC 5 to 7 hold; a person with
  no technical skills connects in a couple of minutes (hand test).

## Retire SQLite (last, only when the Supabase path has run clean for a while)

- [ ] 9.1 Move the crawl's writes into Supabase, with a regular export of all tables into the repository (history and a
  restore path) before `data/jobs.db` stops being committed. Update `AGENTS.md`, the workflow, the deploy flow and the
  `protect-main` ruleset note together. Done when: a daily run works without `data/jobs.db`, a restore from the export
  is rehearsed, and the Notes for the plan in `PRD.md` are all met.

## Checks that close the plan

- [ ] Every AC is named in a step's "Done when". Mapping: FR-1 AC 1-3, 6: 4.1; AC 4, 5: 4.4. FR-2 AC 1-3, 7-10: 4.2;
  AC 4-6: 4.3. FR-3 AC 1-7, 11: 3.2; AC 8-10: 3.4. FR-4 AC 1-3, 9: 7.1; AC 4, 8: 7.2; AC 5, 6: 7.3; AC 7: 8.1;
  AC 10: 7.4; AC 11: policy. FR-5: 1.1. FR-6 AC 1-4, 8: 8.1; AC 5-7: 8.2. FR-7: 5.1 (spam AC), 5.2. FR-8 AC 1-3: 6.2;
  AC 4-6, 8, 9: 6.3; AC 7: 6.1; AC 10: 6.4. FR-9 AC 3-6, 10: 3.1; AC 1, 2, 7, 8: 3.4; AC 9: 6.3. Re-check this when a
  step is split.
- [ ] Success metrics are read from Supabase tables and Resend reports monthly; add the queries to `CONTRIBUTING.md`
  with step 6.4 (sign-ins, 30-day returns, subscribers, share starring, region and description shares, accepted sites).
