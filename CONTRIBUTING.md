# Contributing

## Setup

```bash
nvm install && nvm use
npm ci
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

GitHub Pages serves the dashboard: Settings, Pages, Deploy from a branch, `main`, `/docs`.

## Workflow

1. Branch from `main`.
2. Make changes. Hooks run lint on commit and the full suite on push; never bypass them.
3. Commit subjects are at least 15 characters.
4. If the PR changes `package.json`, `pyproject.toml`, or a Python module listed in `RELEASE_TRIGGER_PATHS`, update `CHANGELOG.md` and bump the version in both `package.json` and `pyproject.toml` (one SemVer bump per PR).
5. Open a **draft** PR. Link an issue with `Closes #<issue>` only when the PR resolves one.

## Standards

- Strict ruff, mypy, ESLint, Stylelint, html-validate, and markdownlint must pass.
- Python test coverage must be 100% (lines and branches). It is enforced on push and in CI; never lower `fail_under` or add blanket omits.
- The only UI is the static dashboard. `build_dashboard.py` generates it in `docs/` from `templates/`: edit the templates, never `docs/`.
- See [AGENTS.md](./AGENTS.md) for the full rule list.

## Adding a company

1. Add the company to `data/seed_companies.csv` (`name,domain`).
2. Run `python3 discover.py`. It writes each company's careers URL and job feed to `data/companies.json`. Commit both.
3. If `discover.py` finds the wrong feed or none, pin the company in `data/overrides.json`, to an ATS and slug or to a
   career page URL (`"ats": "browser"`).

Add `"israel_only": true` to an override to keep only the jobs located in Israel. Use it for large multinational boards.

The slug for the systems where it is not obvious:

| System | Slug |
| --- | --- |
| Comeet plugin page (WordPress) | The page address. Its job links look like `/co/<place>/<uid>/<title>`. |
| Workday | `tenant/wdN/site`, from `<tenant>.<wdN>.myworkdayjobs.com/<site>`. Israel only. |
| JazzHR | `<slug>` of `<slug>.applytojob.com`. Read from the board page. |
| BambooHR | `<slug>` of `<slug>.bamboohr.com`. Read from its `/careers/list` JSON. |
| Oracle Fusion HCM | `host/site`. Read from the public requisition search. Israel only. |
| Amazon | `ISR`. Read from the amazon.jobs search JSON for that one country. |
| Phenom | `host/country/lang`. Read from the search widget, filtered to Israel. |
| Eightfold | `"ats": "browser"` with the `...careers?location=Israel` URL. |

`survey.py` is a dev tool, not part of the daily run: it finds which ATS a custom career page uses.

A company seen for the first time is baselined silently: its current jobs are recorded but not sent, so adding companies
never floods the job alerts.

## Removing a company on request

The policy (the lawyer's answer, PRD "Before launch"): when a company asks to be removed, its data is deleted at once
and it goes on an exclusion list, so it is never crawled again. `TERMS.md` promises "removed promptly". It takes minutes.

1. Find the exact name the crawl uses: the `name` of the company in `data/companies.json` (for example `Acme`). A company
   that appears under two names needs a row for each.
2. Add one row to `data/excluded_companies.csv`: `company,reason,date_added`, for example
   `Acme,asked to be removed by email,2026-11-20`. The reason is a few words: no person's name, no address, no copy of the
   message. The date is `YYYY-MM-DD`. Never delete a row: the list is what keeps a company from coming back.
3. Merge it through a draft PR (`main` is protected: `gh pr ready` then `gh pr merge --admin --squash`).
4. Run the daily workflow from `main` (Actions, `daily-job-digest`, Run workflow). It does the rest in one run: the crawl
   step skips the company and deletes its jobs and crawl log from the scratch history `data/jobs.db` (pulled from
   Supabase at the start of the run); the sync step deletes its rows from Supabase `jobs` and `crawl_log`,
   and the stars and discards (`user_jobs`, which keep a title and a link) and emailed keys (`emailed`) of every user,
   by exact name; the build drops it from `docs/jobs.json`, `docs/jobs.jsonld` and the coverage gap card; the commit step
   publishes. A user's star for that company is deleted too: the removal is complete, and the star row would keep the
   title and the link.
5. Verify, and reply to the company the same day.

### The test of success

All of these hold after the run:

- The log of the crawl step has `exclusion list: deleted <n> jobs of Acme` (a later run has no such line: there is
  nothing left), and the sync step has `Supabase: exclusion list, deleted {...} for Acme`.
- In the Supabase SQL editor, `select (select count(*) from public.jobs where company = 'Acme') as jobs, (select count(*) from public.crawl_log where company = 'Acme') as crawl_log` prints `0` and `0` (the history lives there since step 9.1; the repository holds no copy of it any more, only the old commits of `data/jobs.db`).
- `curl -s https://ravidorr.github.io/misra/jobs.json | grep -c Acme` and the same for `jobs.jsonld` print `0` once
  GitHub Pages has published (a minute or two).
- In the Supabase SQL editor,
  `select (select count(*) from jobs where company='Acme') + (select count(*) from user_jobs where company='Acme') + (select count(*) from emailed where company='Acme')`
  is `0`.
- The coverage gap card on the page does not mention the company.

### What to know

- The match is the exact name when deleting (`Acme` does not delete `Acme Corp`) and ignores case when skipping a
  crawl, so a list that spells a company differently from the data (`acme` for `Acme`) stops the run with a message: use
  the stored spelling. Read the log line: if it says nothing was deleted, the name is not the one in the data. The
  database file is vacuumed after a deletion, so the deleted titles and links do not stay in it.
- The list is checked on every run. A missing file, a wrong header, a blank or one-letter name, a repeated name, a
  missing reason or a bad date stops the run, and the failure alert is sent: that is deliberate, because nobody could
  say who must not be crawled. `tests/test_politeness.py` reads the real file, so CI catches a typo before it merges.
- At most 3 companies that still have data are deleted in one run; more is refused before anything is deleted. Add them
  in separate runs.
- Earlier commits of `data/jobs.db` (it was committed until step 9.1) and `docs/jobs.json` still hold the old links. A removal request does not rewrite
  history; if a company insists, that is a separate decision.
- With a non-empty list and empty Supabase secrets the sync step fails on purpose: a removal must not look done.
- Nothing here is applied to the Supabase project by hand: no migration is involved, and the service-role key that the
  sync step already has does the deleting.
- The removal takes the descriptions with it: they are columns of the `jobs` row in Supabase (see "Job descriptions"), and
  the row is deleted. They are in no other place: `data/jobs.db` and the repository never hold a description.

## How the crawler behaves

- One User-Agent everywhere: `Misra/<version> (+https://ravidorr.github.io/misra/; raanan@avidor.org)`, the constant
  `USER_AGENT` in `common.py`. Do not put another string in a module.
- At most one request a second to a host, for the adapters and the browser crawl alike (`reserve_slot` in `common.py`),
  with 8 companies in parallel for the adapters and 3 pages at once for the browser.
- `robots.txt`: fetched once per host and run; our `Misra` group wins over `*`. A missing file, a 4xx, a 5xx or a timeout
  means allowed (a 5xx or a timeout is logged). An ATS's public API (Greenhouse, Lever, Ashby, Workable,
  Workday, Oracle, Phenom) counts as published for programs: a blanket `Disallow: /` does not stop it, a rule naming its
  path does. A company whose host forbids the address is skipped, not failed: it is not retried, does not count as a
  failure, sends no alert and shows on the coverage gap card under "We choose not to read these".
- The switch is `ROBOTS_ENFORCED` in `common.py`. Set to `False`, the crawl ignores the rules but logs which hosts it would
  have refused. Every run prints which state it is in.
- Audit on 2026-11-15 (read-only: only each host's `/robots.txt` was fetched, once, politely): 234 companies on 75
  hosts. The one that blocks us is SmartRecruiters: `api.smartrecruiters.com` answers `User-agent: *` with `Disallow: /`
  and allows only `LinkedInBot` under `/v1/companies/`. Its documentation says nothing about aggregators, and the
  maintainer has asked SmartRecruiters for permission. Until it is given, Armis, Kaltura, Atera, Nexar and ServiceNow
  are skipped like any company a host forbids.
- No host is exempt. `ROBOTS_API_EXCEPTION_HOSTS` in `common.py` is the place for a host that has given written
  permission to be called despite a blanket `Disallow: /`; it is empty on purpose (a test pins that), and such a
  permission is the only reason to add a host back. A rule that names an API path is honoured either way.
- A company skipped because of `robots.txt` loses its jobs at once: we hold no role of a site whose policy does not
  allow us to read it. `run.py` deletes its rows from the scratch history (so the dashboard build and the upsert no longer
  see them) and the sync deletes its `jobs` rows in Supabase, open and closed, with the stored text, snippet and details
  (`delete_robots_skipped` in `sync_supabase.py`). Only `jobs` is touched, by exact company name: the crawl log stays (it
  is what puts the company on the coverage card, under "We choose not to read these"), and so do the stars and emailed
  keys of users (a star keeps its own title, company and link and shows as no longer listed). A company counts as skipped
  only when every crawl of that name in the run was a `robots.txt` skip, so a second board of the same name that crawled
  or failed protects it, and a failure of any other kind (a timeout, a 403, bot protection) never deletes anything. A
  `robots.txt` that cannot be read counts as allowed (see "Known limits"), so a glitch at a host is a normal crawl, not a
  skip. A delete that fails is a warning and the next run tries again (meanwhile the sync closes the jobs). More than
  `ROBOTS_MAX_PURGE_COMPANIES` companies with jobs in one run are refused with a warning, the same safety as the
  exclusion list's: a host that answered `Disallow: /` by mistake must not empty the table. The next run has nothing
  left to delete and sends no delete; the log says `Supabase: robots.txt, deleted <n> jobs of <m> companies`, counts only.
  If the host later allows us, the next crawl brings the jobs back, and the digest treats them as the baseline of a
  company seen for the first time (they share one `first_seen`), so nobody gets them as new.

### Known limits

- A `robots.txt` that cannot be fetched (a timeout, a connection error or a 5xx) counts as allowed, and so does a 404
  or another 4xx. RFC 9309 would treat a 5xx as a full disallow; the lawyer's rule is "respect robots.txt or rate-limit
  politely", so we choose to keep crawling at one request a second and log the failure.
- Only the address a request starts from is checked, with its query parameters. Redirects, and requests a browser page
  makes on its own, are paced but not checked.
- At most 3 companies with data are removed per run; a list that asks for more stops the daily run until the
  companies are added in separate runs.

## Free-tier limits and what we watch

Checked on 2026-11-15 from the providers' own pages (PLAN.md step 0.2). Provider pages change: re-check the figures
before relying on them, and re-run this note when a plan changes. "Not confirmed" means the page did not say.

### The limits

| Service | Limit on the free plan | Source |
| --- | --- | --- |
| Supabase database | 500 MB per project | [pricing](https://supabase.com/pricing), [billing](https://supabase.com/docs/guides/platform/billing-on-supabase) |
| Supabase egress | 5 GB, plus 5 GB cached | [pricing](https://supabase.com/pricing) |
| Supabase Edge Functions | 500,000 invocations a month | [pricing](https://supabase.com/pricing) |
| Supabase pausing | Paused after low activity over 7 days; restorable for up to 1 year | [pausing](https://supabase.com/docs/guides/platform/free-project-pausing) |
| Supabase backups | Not included on Free (they start with Pro, kept 7 days); the daily export below is the backup | [pricing](https://supabase.com/pricing) |
| Resend sending | 100 emails a day (UTC day) and 3,000 a month; every To, CC or BCC recipient counts | [pricing](https://resend.com/pricing), [quotas](https://resend.com/docs/knowledge-base/account-quotas-and-limits) |
| Resend domains | 3 custom domains | [pricing](https://resend.com/pricing) |
| GitHub Actions | Standard runners are free in a public repository | [billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions) |
| GitHub Pages | Site at most 1 GB; soft limits of 100 GB bandwidth a month and 10 builds an hour | [limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) |

Notes on the table:

- Pausing: the exact threshold is not published. The page says "a few user requests to the database each day over the
  previous week" is typically enough, and that dashboard visits and API calls count. A warning email arrives about a week
  before a pause. Whether the daily sync counts is not confirmed by the page: it is an API call to the project every
  day (`sync_supabase.py`), which fits "making API calls", but nothing says so in terms. Treat it as likely, not proven.
- Resend: the pages do not say whether the quota is per account, team or domain (received mail counts too). Assume it is
  shared with `easy-theory` on the same account. How much `easy-theory` uses is not confirmed (it is not visible from this repository).
  Whether the free plan's 3 custom domains are already used up is also not confirmed.
- GitHub Pages: the 10 builds an hour soft limit does not apply to a site published by a custom Actions workflow. No
  workflow here uses the Pages deploy actions, so the site is published from a branch and the limit applies; one build a
  day is far below it.
- GitHub Actions: the page says standard runners in a public repository are free, so there is no minutes budget to
  watch (larger or self-hosted runners are not covered by that and are not used here).

### Real data against the limits

- `data/jobs.db` was 3,067,904 bytes (about 3 MB) on 2026-11-15, holding 6,766 open and 2,722 closed jobs (9,488 rows). Since step 9.1 it is a scratch file rebuilt from Supabase on every run, so it no longer grows the repository.
- Supabase holds every open job (6,773 at the last two live syncs on 2026-11-14, from PLAN.md step 2.2) and, since the
  sync marks a missing job `closed` and never deletes it, every job that has closed since: those rows pile up, so the
  trim plan (step 7.4) must cover closed jobs too. The size of
  the Supabase tables is not known here: reading it needs the dashboard (Reports, Database) or a service-role query, and
  no secret is used for this note. As an estimate only, the same rows in SQLite are about 3 MB with its indexes, so the
  `jobs` table with its indexes is expected to be a few MB, far under 500 MB. Measure it once in the Supabase dashboard
  and write the number here.
- `docs/jobs.json` (the open-data download) is 2.1 MB, about 0.23 MB compressed. The page reads the same rows from
  Supabase, so one page load is of that order (an estimate, not measured). At that size 5 GB of egress is roughly 2,500
  loads a month if egress is counted before compression, and up to ten times that if it is counted after (the pricing
  page does not say which). Tens of users are far below it; thousands of visitors a
  month would not be.
- Full descriptions (PLAN.md step 7.1) live in Supabase only, so they cost the free 500 MB database and nothing in the
  repository: the scratch `data/jobs.db` stays about 3 MB. Measured on 2026-11-16 on live samples of the feeds (one board each, counts
  and sizes only, no text kept): the text of a job is 2,400 to 5,400 characters on average (Greenhouse 4,266, Lever 2,369,
  Ashby 2,900, Workable 5,439, Comeet 3,461, Amazon 3,959) and 4,300 to 12,400 at most, nowhere near the 20,000-character
  cap, and it is almost all ASCII (1 byte a character; Hebrew would be 2). Weighted by the open jobs of those feeds
  (about 4,760 of 6,814, 70 percent), that is about 18 MB of text for the open jobs, about 3.7 KB a job, and a little
  more for `details` and the hash. The full-text index of step 7.3 can add up to the size of the text again, so plan for
  about 40 MB for the open jobs. The worst case is every job at the cap in Hebrew (40 KB a job): 4,760 jobs would be
  190 MB, still under 500 MB. The size of the Supabase tables is not known here (the dashboard's Reports, Database
  shows it; no secret is used for this note): measure it before and after the first sync with descriptions.
- What grows without bound is the closed jobs: the sync marks a job closed and never deletes it, and a closed job keeps its
  text. The database is 3 days old (2,847 closed jobs, much of it the first days' churn), so the rate is not known. At
  200 jobs closing a day, the text adds 0.7 MB a day, about 22 MB a month, and the free 500 MB would be used in about 18
  months; at 1,000 a day, in about 4. Step 7.4 measures the real rate after a month and sets the trim (drop the text of
  a job some days after it closes). The 500 MB limit is the Free plan's, from supabase.com/pricing on 2026-11-16 (the
  page says nothing about what happens above it).
- Resend: `report-site`, the confirmation email and the job alerts (`send_digests.py`, PLAN.md step 6.4) send
  through it; Gmail is left for the failure alert only (see "The daily digest", below). The volume arithmetic is in "Resend volume".

### Pause prevention and what the dashboard does if the backend is paused

- The daily workflow writes to Supabase every day at 04:23 UTC, or at the 09:53 UTC second try if that one was missed (`sync_supabase.py`; the step runs only when the crawl
  succeeded, and scheduled runs can be delayed or skipped), and the dashboard reads from it on every visit. Both are requests to the project's API, which the pausing page says count as activity. Together they are
  the pause-prevention plan; no extra keep-alive job is added.
- That plan has a hole: the pages do not say that an API call wakes a paused project, so assume the sync cannot. The
  page does not say how a paused project answers API calls either; any failed sync (a refused request, an error answer or
  no open jobs) fails the step, and the failure alert (`send_alert.py`, Gmail) reaches the maintainer the same
  day. The project is then restored from the Supabase dashboard ("Resume project", up to 1 year).
  Watch for the "about to pause" email from Supabase as well, and open the dashboard of the project when it arrives.
- If the project is paused, the page cannot load the jobs and shows its error card: the summary reads "Could not load
  the roles." and a notice "The roles could not be loaded" with a "Try again" button (`showLoadError` in
  `templates/dashboard.js`). There is no offline copy of the jobs by design (PRD constraint), so nothing else is shown.
  Signing in, stars and the site report fail the same way until the project is restored.
- Backups: the free plan has none, so the daily export (below, "Backups of the account tables") is the only copy of the
  user data (stars, discards, filters, email settings) and, since step 9.1, of the job history (`jobs`, open and closed,
  and `crawl_log`; the text of the postings is not exported, the next crawl writes it again): a private repository, at
  most 28 days of history, restorable with `restore_accounts.py`. The old commits of `data/jobs.db` in this repository
  hold the history up to the move.

### Upgrade triggers

Move to a paid plan, or trim, when any of these is true:

- The Supabase database passes 400 MB of the 500 MB (80%). Trim first (the plan in PLAN.md step 7.4); upgrade if that is
  not enough.
- Supabase egress passes 4 GB of 5 GB in a month, or Edge Function invocations pass 400,000 of 500,000.
- Resend sends pass 80 of 100 in a day, or 2,400 of 3,000 in a month (this includes `easy-theory` and received mail). In
  subscribers that is about 60 once reports are counted, since the monthly cap binds first. "Resend volume" (below)
  has the arithmetic and the subscriber count query.
- A pause happens, or the "about to pause" warning arrives twice. The account tables are backed up daily (below); move
  to a plan with backups if that export ever stops being enough (a restore that is too slow, or a need for point-in-time recovery).
- The repository passes 800 MB (80% of the recommended 1 GB).

## Job descriptions

PLAN.md step 7.1. Where the text of a posting lives, and how it gets there:

- **Where.** In Supabase only, in the `description` (plain text) and `details` (`jsonb`) columns of `jobs`. Never in
  `data/jobs.db`, `docs/` or any other file of the repository: the daily workflow commits those to a public repository,
  so a description there would grow every daily commit and publish other companies' full postings on GitHub. The
  database has no description column, and `tests/test_descriptions_flow.py` fails if the workflow stages anything that
  could carry one.
- **Not public.** The full text is never readable through the public API (the lawyer's decision of 2026-11-16): it stays
  in Supabase for search and the assistant. The migration `20261121100000_jobs_text_not_public.sql` takes the table-level
  `select` on `jobs` from `anon` and `authenticated` and gives it back on every column except `description` and `details`,
  signed in or not. Only three things ever read it: the sync (service role), the full-text search of step 7.3 (a database
  function that returns matching job keys only, never the text) and `get_job` of step 8.1 (the full text, to the assistant,
  rate limited). The dashboard drill-down never shows the full text: it shows the snippet and a "Read full description &
  apply on (company name)" link to the employer. The service role (the sync) keeps full access. A column added to `jobs` later has no column grant, so
  it is closed by default: the migration that adds it must grant it, and `tests/test_migrations.py` fails until its list
  of public and closed columns agrees with the table. `supabase/tests/job_text_private.sql` and
  `supabase/tests/job_snippet_and_trim.sql` (the `sql-tests` CI job) pin it. The maintainer applies a migration before the
  first daily run that needs it.
- **The snippet.** The only public part of the text is `snippet`, a column the sync fills (migration
  `20261122100000_jobs_snippet.sql`): the first 255 characters of the description as written (`descriptions.snippet`), cut at
  a word boundary, no ellipsis in the data (the page adds one), trailing punctuation trimmed when the text was cut, the
  whole text when it fits, none when the job has no description or its first 255 characters have no space. The dashboard
  shows it with a link to the employer. It is refreshed with the text, and also when the stored snippet is not the one
  computed (so a stored text gets its snippet on the next run). Next to it, `snippet_cut` (migration `20261126100000_jobs_snippet_cut.sql`, `descriptions.snippet_cut`) says whether the snippet is
  shorter than the text: the page shows the ellipsis exactly when it is true, and a row without it shows none. The migration fills it
  in for the rows stored before it, and the sync corrects any row whose flag differs from what it computes. Apply the migration before merging the change that reads it.
- **The 30-day trim.** The full text of a closed job is deleted 30 days after the job closes. The table has no closed-at
  column; `last_seen` is the last crawl that saw the job open, and the sync does not change it when it closes the job, so
  `jobs_text_trim(p_day)` (migration `20261123100000_jobs_text_trim.sql`, service role only) sets `description`, `details`
  and `snippet` to null for the closed jobs whose `last_seen` is more than 30 days before `p_day`, and keeps the row (title,
  company, link, dates). `details` goes whole because it holds the text fingerprint (`text_sha256`): with it gone, a job
  that reopens gets its text and snippet written again by the next sync. The trim is early at worst, never late: `last_seen` is never after the real close, so a company whose crawl failed or was
  skipped before its jobs closed has them trimmed sooner than 30 days after the close. A removal request deletes the whole row,
  see "Removing a company on request".
- **How it travels.** The crawl (`python run.py`) writes the texts of the day to a work file outside the checkout,
  `$RUNNER_TEMP/misra-descriptions.jsonl` (the system's temporary directory when run elsewhere), and the sync step
  (`python sync_supabase.py`) of the same job reads it. A step after the crawl reads what the crawl left, and a sync
  without that file (the crawl did not run, or it failed) sends no description and changes none. The file is not a
  committed path and is never uploaded as an artifact.
- **The sync.** After the jobs are upserted, a second pass sends `description` and `details` for the open jobs that have a
  text today, in batches of 25 and in requests of their own (a job without a text keeps the one it has: a missing key,
  never `null`). A hash of the text, kept in `details` as `text_sha256`, skips a job whose text and snippet did not change (the
  snippet goes in the same request). A job that closes keeps its text until the trim above; the removal of a company (see
  "Removing a company on request") deletes the whole row. After this pass the sync calls `jobs_text_trim`. A failure
  in this pass is a `::warning::` with the HTTP status and nothing else (an answer can quote the row, and the log is
  public); it never fails the step, so the digests and the deploy still run.
- **What is read.** Only what a site publishes for applicants, and only from the request the adapter already makes
  (no new host, no new path, no request per job; Workday below is the exception): Greenhouse (`?content=true` on the board list), Lever (the posting
  list: description, the named lists, the closing text), Ashby (the posting list), Workable (`?details=true` on the widget
  list), Comeet (`custom_fields.details` of each position in the page's embedded data) and Amazon (the search JSON: description
  and qualifications). `greenhouse_url` takes the content when the company's proxy gives it. Every request stays inside
  the one-request-a-second rule and the `robots.txt` gate. `details` holds a short list of fields a site gives
  (employment type, department, experience, workplace type, offices) and nothing else: Greenhouse `metadata` and the
  compliance fields are left out on purpose.
- **Job pages (Workday).** The Workday list carries no text, so after the list is final the crawl (`run.crawl`, then
  `adapters.add_descriptions`) opens one page per job: `https://<tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/job/...`
  (the JSON the site loads itself; the text is `jobPostingInfo.jobDescription`, of the rest only `timeType` is kept). It is
  the same `/wday/cxs/` path as the list, so the `robots.txt` gate, the one-request-a-second-per-host delay and the User-Agent
  are the same (the tenants' own `robots.txt` files, read on 2026-11-17, do not disallow `/wday/`). The budget: a page is
  asked for only for a job Supabase holds no text for (the pull, `sync_supabase.py --pull`, leaves the keys of those that do in
  `$RUNNER_TEMP/misra-held-texts.jsonl`, keys only; no file means none known; the crawl prints how many it knows) and at most 40 per
  company and run (`DETAIL_CAP`, the rest next run, in a window that moves each day so a page that never answers cannot starve the
  others; five pages in a row without a text end that company's job pages for the run, so a host that refuses is left alone). The first fill of the 318 Israeli jobs is therefore about 200 requests on the first day
  (Palo Alto Networks 115 and KLA 82 are capped at 40 each, so they take three days), over 24 tenant hosts, and a steady day
  asks only for the new jobs, a handful. A text edited in place is not fetched again until the job closes and reopens (a rolling
  refresh is in `TODO.md`). A page that fails, is refused by `robots.txt` or has no text costs only its own text: the list is
  already final, so the company is never skipped and nothing closes. The log line is `job pages: workday N requests, N with a
  text, N left for later runs`, and a job whose text is stored counts in the share of the `descriptions:` line. A site that answers
  a job page with a block or challenge page is never worked around: no text.
- **The browser crawl.** Three ways, none of them page text: (1) HiBob career pages (Sapiens, Hibob, Fundbox) return the
  description in the list JSON the crawl already captures: no request. A `JobPosting` JSON-LD on a list page also gives its
  description, no request. (2) Eightfold boards (Nvidia, Applied Materials, Qualcomm, Microsoft, HP, PayPal, Citi; Amdocs has no
  jobs today) give the job's own JSON at `/api/pcsx/position_details?position_id=...&domain=...&hl=en`, the call the board
  makes, on the path its search uses (every host's `robots.txt` was checked on 2026-11-17 and allows it). (3) The job pages of
  Weka, eToro, Perion and Digital Turbine, whose static HTML has a schema.org `JobPosting` JSON-LD with the description
  (`adapters.JSONLD_PAGES`, found by hand on 2026-11-17); only that JSON is read. (2) and (3) go through `common.get`, so
  `robots.txt`, the delay and the budget (held keys, 40 a company and run, five misses in a row end the run) are the Workday
  ones; they run after the lists, so a refusal never skips a company. The first fill: about 160 requests on the first day
  (Nvidia's 400 jobs take ten days at 40 a day), then the new jobs. Estimated, not yet seen live: about 830 of the 1,589 browser
  jobs. **Not read, and not to be tried again without a reason:** Check Point (231 jobs; a plain request answers HTTP 403, bot
  protection, never worked around) and every other browser company (monday.com, Tower, Priority,
  Xtend, Komodor, Intezer, Oligo, Mesh, Weebit, Pecan, Freightos, Panorays, Lightbits, ForSight, Valens, Lasso): a sample of one
  job page each shows no JobPosting markup in the static HTML (the text is drawn by JavaScript). Digital Turbine is a Workday site
  read through its JSON-LD; its Workday JSON would be an alternative.
- **Not read yet.** Phenom (89 jobs, the list carries a teaser, not the text), Oracle HCM (9 jobs), `comeet_page` (11), JazzHR
  (1) and BambooHR (none open today): each would need its own per-job path and adapter for a handful of jobs, so they wait until
  one has enough jobs to matter (judged on 2026-11-17 by job count only; their `robots.txt` and detail endpoints are not
  checked yet). SmartRecruiters stays skipped by `robots.txt`.
- **The text.** Plain text made from the site's HTML with the standard library: scripts, styles and embedded frames are
  dropped with their content, no tag survives (an angle bracket a posting wrote, such as `List<T>`, is kept as text, to be shown as text), paragraphs and list items keep their line breaks, control characters
  (NUL breaks a Postgres insert) are removed. At most 20,000 characters a job (`MAX_CHARS` in `descriptions.py`, cut at a
  word); a cut text is marked `description_truncated` in `details` and counted in the log. A value that is not text is no
  description: nothing in this path can stop a company's crawl.
- **The log.** One line in the crawl step, counts and percentages only: `descriptions: greenhouse 1900/2083 (91%); comeet
  ...; all N/M (P%); cut at 20000 characters C; K characters kept`. The share is of the jobs the crawl returned today, per
  ATS (a company that failed or was skipped has no count). That is the list of what to improve next (FR-4 AC 3). The
  sync step logs `Supabase: descriptions N written, N unchanged, N failed` and `Supabase: N closed jobs lost their text (30
  days after closing)`: counts only, never a text. A trim that fails is `::warning::the text of closed jobs could not be
  trimmed: ...` followed by `Supabase: the text of closed jobs was not trimmed today`, and the next run tries again.
- **Adding an adapter.** Return the description with `attach(job, text, details)` from `descriptions.py` (`html_to_text`
  for HTML, `plain_text` for text), from the request the adapter already makes. A site whose list has no text gets a function in `adapters.DETAILS`
  (`fetch(slug, job) -> (text, details) | None`, like `workday_description`): it makes one request per job through
  `common.get`, so `robots.txt` and the delay apply, and `add_descriptions` budgets it (held text, cap per company). Check the
  per-job path against the host's `robots.txt` and count the requests before adding one; skip and record a site that
  blocks or challenges the request.

## The daily digest

`.github/workflows/daily.yml` sends the job alerts with `send_digests.py` (PLAN.md step 6.4) right after the Supabase sync and before the build and the deploy push.

- **Schedule.** Three starts a day, the third from outside GitHub (see "The third start" below). Two are scheduled: 04:23 UTC (06:23 or 07:23 in Israel) is the real run, and 09:53 UTC (11:53 or 12:53) is a second try for a day when GitHub drops or badly delays the first (it dropped the whole run on 2026-11-16, and has started others about 7 hours late). Neither is on :00 or :30, the minutes GitHub is busiest. The `guard` job runs first on a scheduled start and on a start from cron-job.org, uses no secret and writes nothing: it asks the Actions API for a green run of this workflow created today (UTC) that was scheduled or was started by cron-job.org. If there is one, the `run` job is skipped; if there is none, or the question fails, it runs (fail open: a double run is harmless, because the digest records what it emailed and the crawl is idempotent). Any other manual start skips the guard and always runs. Other manual runs do not count as "already ran", since one may have been a dry run.
- **Reading the Actions tab.** On a normal day the 04:23 run is green, the cron-job.org run of about 04:30 waits for it in the concurrency group and then shows `guard` green and `run` skipped, and so does the 09:53 run (grey "skipped", not a failure, and no alert email). The guard of a start that arrives while an earlier run is still going does not see that run, but it does not need to: the concurrency group is on the whole workflow, so the new run waits, its guard starts only after the earlier run finished, and then it counts that run if it was green and scheduled or from cron-job.org. A plain manual run is never guarded, so a manual start next to a cron-job.org one (2026-11-17, 04:30:59 and 04:31:45) runs twice by design; the second run is harmless (the crawl is idempotent, the digest records what it emailed). A 09:53 run in which `run` did the work means the 04:23 run never started or failed. Two runs close together can also happen when GitHub starts the first one late. Both runs share the concurrency group `daily-job-digest` with `publish-docs`, so a started run is never cancelled, but GitHub keeps only one waiting run per group: if the 09:53 run waits behind a running `publish-docs` and another run is then queued, the older waiting run is dropped. If the dropped run is the second try and the later run is a `publish-docs` run (which only rebuilds `docs/`), the day gets no crawl and no digest, and no alert fires (the alert is a step of the `run` job, which never started). That needs the 04:23 run to have been lost too, a `publish-docs` run in progress at 09:53 and a second one queued behind it, so it is rare, and the cron-job.org start at about 10:30 UTC then picks the day up (the guard does not skip it, since no green scheduled run exists). The group is still shared on purpose (separate groups could let two runs push to `main` at the same time). If that start is lost as well, the Actions tab is the only signal: start the run by hand.
- **The third start (cron-job.org).** The two scheduled starts both depend on GitHub's own scheduler, which lost the whole 2026-11-16 run. So a job at cron-job.org also starts the workflow, at about 04:30 UTC, a few minutes after the first scheduled start (it was 10:30 UTC, after the second try, until 2026-11-17): it sends `POST /repos/ravidorr/misra/actions/workflows/daily.yml/dispatches` with the body `{"ref":"main","inputs":{"source":"cron-job.org"}}`, using a fine-grained token (see `SECURITY.md`). The workflow declares the `source` input for this, because GitHub refuses an input it does not know, and its `run-name` titles such a run `daily-job-digest (cron-job.org)` (scheduled runs are `daily-job-digest (scheduled)`, other manual runs `daily-job-digest (manual)`). A dry run (`digest_dry_run`) never gets that title, so it never counts, and the guard counts only runs on `main`. The guard finds those runs by that title, since the runs API does not return the inputs. On a normal day the guard is green and `run` is skipped (grey, no alert), like the 09:53 try. When it did the work, the Actions tab shows `daily-job-digest (cron-job.org)` with a green `run`, and the first two starts were lost or did not finish green. If cron-job.org cannot start the run (wrong or expired token, GitHub down), it emails the maintainer; a 204 answer means GitHub accepted it. A disabled workflow raises no alert and shows only as a missing daily commit of `docs/`.
- **Secrets.** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY` and `UNSUBSCRIBE_SECRET` are set on that step only (the sync step also has the first two), never on the job and never during the crawl. All four empty: the step prints a message and exits 0; some empty: it fails and names them.
- **Failure.** The step runs only after a good sync (the job alerts and the dashboard then read the same rows). If it fails, the run is red, the build and the data commit still run, and the failure email (`send_alert.py`) goes to the maintainer; its `STEPS` line shows `digests=failure`.
- **Dry run.** Start the workflow by hand (Actions, "daily-job-digest", Run workflow, leaving `source` empty) with `digest_dry_run` ticked: the job alerts step gets `--dry-run`, sends nothing and records nothing, and its log holds counts only (the Actions logs are public). Note that a manual run still crawls, still writes the jobs and the crawl log to Supabase and still commits and pushes `docs/` to `main`.
- **One sender.** `send_digests.py` is the only sender of job alerts. The maintainer's own single-recipient email (`send_email.py`, through Gmail) and the selection logic of `digest.py` that FR-9 replaced were removed in v0.47.0, after the maintainer, an ordinary subscriber, received a job alert from `send_digests.py`. Signups are open (PLAN.md step 6.6, v0.31.0: the build constant `DIGEST_SIGNUP_ENABLED = True` and the function secret of the same name `true`; to close them again unset the secret, and never set the build constant back to `False` once anyone has subscribed, because that also removes "End job alerts" from the page).
- **Crawl problems are in the log, not in an email.** The workflow step "Log the coverage gaps" (`python run.py --gaps`, right after the crawl) prints one line of counts and company names, `coverage gaps: N (M new): failing ... held ...`, when any company is failing (its last two crawls failed), held after a sharp drop, or closed after a believed sharp drop. A gap that is new makes the line a `::warning::` annotation on the run: it began on the newest crawl day or up to two days before, so an outage is new on the crawl that finds it and for two days after, which a lost daily run does not hide (nothing remembers what was logged: the history is rebuilt from Supabase on every run, so a gap that is still there days later is a plain line). A broken check prints `::warning::coverage check failed, ...` and fails that step, so the run is red and the failure email goes out; the sync, the digests and the commit still run (`always()`). Read the line in that step of the run, or on the coverage gap card of the dashboard.
- **Gmail is for the failure alert only.** `send_alert.py` is the only user of `GMAIL_USER`, `GMAIL_APP_PASSWORD` and `DIGEST_TO` (the alert's recipient; empty means the sender). All three stay as Actions secrets.

### Resend volume

Checked on 2026-11-15 against [Resend pricing](https://resend.com/pricing) and [account quotas and limits](https://resend.com/docs/knowledge-base/account-quotas-and-limits).

- Free plan: 100 emails a day (a UTC calendar day, resets at 00:00 UTC) and 3,000 a month, 3 custom domains, an API rate limit of 10 requests a second. Each To, CC or BCC recipient counts as one email, and received mail counts too. Over the limit the API answers 429 (`daily_quota_exceeded` or `monthly_quota_exceeded`) and Resend does not queue the email. The pages do not say whether the quota is per team or per domain, so assume it is shared with the maintainer's other project, `easy-theory`, and with `report-site` and the confirmation emails (site reports are capped at 20 a day).
- Daily subscribers: at most one email each a day, and none when nothing is new, so the count is a ceiling. 100 a day caps daily subscribers at 100 minus the other uses (20 for reports, plus whatever `easy-theory` sends and receives). 3,000 a month is about 100 daily subscribers (30 emails each), and 96 in a 31-day month, before any other use. Taking the 20 report emails off the daily cap leaves about 76 to 80 for daily subscribers; about 60 is the working limit, which keeps headroom for `easy-theory`, confirmation emails and a bad day, and is the number the trigger below uses.
- Weekly subscribers: one email a week, about 4.3 a month, so about a seventh of the cost of a daily subscriber (the plan's rough figure was a fourth; seven is the arithmetic). A weekly send is on Sundays, so those emails land in one day's 100: 60 or more weekly subscribers would take most of a Sunday on their own, so count them in the daily cap on Sundays.
- Today (2026-11-16): one real subscriber, the maintainer. Signup has been open since 0.31.0, so anyone can turn job alerts on. Count the rows with the query below.
- Upgrade trigger: when confirmed daily subscribers reach about 60 (or the equivalent mix), or when Resend sends pass 80 of 100 in a day or 2,400 of 3,000 in a month, either move Resend to Pro (the pricing page lists $20 a month for 50,000 emails with no daily limit) or close signups until it is done (unset the function secret `DIGEST_SIGNUP_ENABLED`, see `supabase/README.md`). Read this month's use in the Resend dashboard (Emails and Usage), which also shows `easy-theory`.

```sql
-- confirmed subscribers by frequency (run in the Supabase SQL editor; the count that the trigger above is read from)
select frequency, count(*) as subscribers
from public.subscriptions
where consent_confirmed_at is not null and unsubscribed_at is null
group by frequency;
```

### Monthly success metrics

Read these on the first of each month in the Supabase SQL editor (they need the service role, so they are never run from the page), and read delivery and bounce numbers from the Resend reports. Every column below exists in `supabase/migrations/`. The PRD's targets are in `PRD.md`.

```sql
-- sign-ins: accounts created, and accounts that signed in, in the last 30 days (auth.users is Supabase's own table)
select count(*) filter (where created_at >= now() - interval '30 days') as new_accounts,
       count(*) filter (where last_sign_in_at >= now() - interval '30 days') as signed_in_30d,
       count(*) as accounts
from auth.users;

-- 30-day returns: accounts older than 30 days that signed in during the last 30 days
select count(*) as returning_accounts
from auth.users
where created_at < now() - interval '30 days' and last_sign_in_at >= now() - interval '30 days';

-- subscribers: confirmed and active, by frequency (see "Resend volume"); and how many unsubscribed
select count(*) filter (where consent_confirmed_at is not null and unsubscribed_at is null) as active,
       count(*) filter (where unsubscribed_at is not null) as unsubscribed,
       count(*) filter (where consent_confirmed_at is null and unsubscribed_at is null) as waiting_for_confirmation
from public.subscriptions;

-- share starring: accounts with at least one star, as a share of all accounts
select count(distinct u.user_id) filter (where u.state = 'star') as accounts_with_a_star,
       (select count(*) from public.profiles) as accounts,
       round(100.0 * count(distinct u.user_id) filter (where u.state = 'star')
             / nullif((select count(*) from public.profiles), 0), 1) as share_pct
from public.user_jobs u;

-- region and description shares among open jobs (a job has a region when the array is not empty)
select count(*) as open_jobs,
       round(100.0 * count(*) filter (where cardinality(regions) > 0) / nullif(count(*), 0), 1) as with_region_pct,
       round(100.0 * count(*) filter (where description is not null and description <> '')
             / nullif(count(*), 0), 1) as with_description_pct
from public.jobs
where status = 'open';
```

Accepted sites are not in Supabase: a report is only an email, and accepting it adds a row to `data/seed_companies.csv` (see "Reported sites"). Count the additions of the month from git, for example `git log --since=2026-11-01 --until=2026-12-01 -p --format= -- data/seed_companies.csv | grep -c '^+[^+]'`, and compare with the number of report emails received.

## Backups of the account tables and the job history

Supabase Free has no backups, so the daily workflow (`.github/workflows/daily.yml`, step `backup`, PLAN.md step 9.1) copies the account tables and the job history to a private repository, `ravidorr/misra-backups` (branch `main`). This repository is public: no account data may be committed to it, logged, uploaded as an artifact or left in the checkout.

- **What.** `export_accounts.py` reads every row of `profiles`, `user_prefs`, `user_jobs`, `subscriptions` and `emailed`, all columns (the confirmation and unsubscribe token hashes too), with the service-role key, and writes one `<table>.json` per table: one row per line, keys sorted, rows sorted by primary key, so a day's diff is only what changed. The job history goes with them since step 9.1, when Supabase became its only home: `jobs` (open and closed, every column except `description`, `details` and `snippet`: the text of a posting never leaves Supabase, a backup would outlive the 30-day trim of closed jobs' text, and the next crawl writes it again for every open job) and `crawl_log`. Not exported: the abuse counters (`report_*`, `confirm_*`, short-lived) and Supabase's own `auth` tables. A test fails when a column or an account table in the migrations is missing from the export (`tests/test_export_accounts.py`), and when a text column could be read.
- **Where it runs.** After the deploy push, so it never delays the data. It runs on every run, even when the crawl failed. It writes into a temporary directory under `RUNNER_TEMP`, outside the checkout (the module refuses a directory inside the repository), and then `publish_backup.py` clones the backup repository, replaces the seven files, commits only when something changed ("backup 2026-11-16: profiles 3, user_prefs 3, ... jobs 9722, crawl_log 7536 (n rows)", counts only) and pushes to `main`. The Actions log shows counts only.
- **Secrets.** `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (also on the sync and digest steps) and `BACKUP_DEPLOY_KEY` are set on the backup step only. `BACKUP_DEPLOY_KEY` is the private half of a read-write deploy key of `ravidorr/misra-backups`; it is written to disk only in that step, by `scripts/backup-key.sh`, in a file with a unique name, used through `GIT_SSH_COMMAND` with `IdentitiesOnly=yes` and no agent, and removed with the work directory when the step ends. No checkout gets `ssh-key:`. `tests/test_backup_workflow.py` pins all of it. The deploy key belongs to that one repository and reaches no other, and a deploy key has no expiry date, so rotate it by hand at the yearly review (by 2027-11-15, `SECURITY.md`): make a new key pair, add it to the deploy keys of `ravidorr/misra-backups`, update the Actions secret, run the workflow once, then delete the old key in the repository's deploy keys.
- **Guards.** The export fails when a table has columns that are not in `TABLES` (the day a migration adds one) or when `profiles` is empty beside non-empty tables (a wrong key sees nothing). `publish_backup.py` refuses to commit, and never replaces the history, when a table that held 5 or more rows now holds under half of them; if that is real, run it by hand with `--allow-shrink`.
- **Failure.** The step fails (non-zero) when a secret is empty, a table cannot be read, a table keeps changing under the read (three tries), git cannot clone or push. The run goes red and the maintainer alert (`send_alert.py`) lists `backup=failure`. Nothing is written for a table that failed: every table is read before any file is written.
- **Retention.** The repository keeps history, so a deleted account would stay in old commits. When the first commit of `main` is more than 28 days old, `publish_backup.py` pushes one new commit with no parent that holds the current files, over `main`, with `--force-with-lease` (it replaces only the history it read). A deleted account therefore leaves the backups within 28 days plus one daily run, under the 30 days `PRIVACY.md` promises. GitHub may keep the replaced commits reachable by their ids for a while; if someone must be erased at once, delete and recreate `ravidorr/misra-backups` (and the deploy key), then run the workflow. The force push goes only to the remote named in the workflow, which is the backup repository.
- **If the backup stops.** The alert says so. If it fails for more than a few days, fix it before the history gets older than 30 days; a missed day only delays the replacement, so the 30 days in `PRIVACY.md` hold only while the daily job runs.

### Restore

1. Clone the backup repository to a directory outside this checkout: `git clone --depth 1 git@github.com:ravidorr/misra-backups.git ~/misra-backup` (the latest commit only: an older one would bring back accounts deleted since). The clone holds plaintext email addresses: delete it as soon as the restore is done (or the test is over), so that no copy sits on a disk outside the 30-day rotation (`SECURITY.md`). The repository is private; the files are not encrypted by us.
2. `python restore_accounts.py ~/misra-backup ~/restore.sql` (add `--exclude-user-ids=<file>`, one id per line, for the people who deleted their account after the backup: they are left out of every table; `--create-auth-users` refuses to run without it, an empty file meaning nobody left; add `--pause-subscriptions` to mark every still-active subscription unsubscribed so that nobody who opted out after the backup is emailed; and for a NEW Supabase project use `--create-auth-users` that has no `auth.users` rows: it creates a bare row, id and email from `profiles`, for each one that is missing, and each person then signs in with Google as before). **Without `--create-auth-users` (the live project, where `auth.users` is intact) the script restores only the rows of people who still have an `auth.users` row**: each insert is filtered by the database itself (`where exists (select 1 from auth.users u where u.id = r.user_id)`, on the exported user id, no email involved), so an account deleted after the snapshot is skipped in every table and is never brought back, and the run does not fail on it. Use `--create-auth-users` only in a fresh project: it recreates the sign-in rows, so it cannot tell who deleted their account, and it refuses to run without `--exclude-user-ids`. The script is written readable by you only and holds everyone's data: delete it after use.
3. Read it, then run it in the SQL editor of the project (or `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f ~/restore.sql`). It is one transaction, inserts in foreign-key order, uses `on conflict do nothing` (running it twice, or into a project that already has some of the rows, changes nothing that exists, and it deletes nothing), and switches the seven tables' own triggers off while it runs and on again at the end, so the restored rows keep their timestamps and the consent record. `jobs` and `crawl_log` hold no user, so every row of them goes in (never overwriting one that exists); a crawl log row keeps its `id` and the id sequence is moved past the largest one.
4. Check the counts against the log line of the backup commit (in the live project the counts of the script are the upper bound: the people who deleted their account since are the difference, and you never need their emails to know that), and run the daily workflow once: the job texts are not in the backup, so that run's sync writes them again for every open job.
5. Undo what the backup cannot know. In the live project, accounts deleted after the snapshot are already skipped (step 2). Anyone who unsubscribed after the time of the backup commit comes back from it (and, in a fresh project with `--create-auth-users`, so does anyone who deleted their account): before the next job alerts run, delete those accounts again and mark those subscriptions unsubscribed (find them in Supabase's auth logs and the `unsubscribe` and `delete-account` function logs for that window, or from any request the maintainer received by email). A restored `subscriptions` row of a person who had unsubscribed that day is a person who would be emailed against their wish. If the window cannot be checked, do not send job alerts until it can.

### Rehearsal, 2026-11-15

What was run, against a local Postgres 17 (`postgres:17-alpine` in Docker, database `misra_rehearsal`) built from `supabase/migrations/*.sql` plus a stub of Supabase's roles and the `auth` schema (`auth.users` with the columns the `--create-auth-users` step names). Fixture: 3 users (one with an apostrophe in the address, one with an accent), 2 prefs rows including filter text with `$restore$`, a backslash and quotes, 2,601 `user_jobs` (two users over the 1,000-row page size, plus a discard), 2 subscriptions (one confirmed, one unsubscribed, token hashes set) and 1 emailed row. The real `export_accounts.Source` read it through a stand-in for PostgREST that pages with `limit` and `offset` and answers `Content-Range`.

1. Export: 3 profiles, 2 prefs, 2,601 user_jobs, 2 subscriptions, 1 emailed, files written mode 0600.
2. Emptied everything by deleting every `auth.users` row (the foreign keys cascade). Restored with `--create-auth-users`: all five tables back to the same counts, and a second export was byte-identical to the first (every column, timestamps and token hashes included).
3. Ran the same script a second time: counts unchanged, export byte-identical (idempotent).
4. Truncated the five tables, kept the users, restored without the flag: export byte-identical.
5. Changed one restored row, ran the script again: the changed row stayed as it was (nothing is overwritten, nothing deleted). Afterwards no trigger was left disabled.
6. Deleted accounts (second rehearsal, 2026-11-15, same kind of local Postgres 17 with the committed migrations): two users, `profiles` and `user_prefs` rows for both, exported to JSON, then one user deleted from `auth.users` (the foreign keys cascade) and the public tables emptied. The script made without `--create-auth-users` restored the remaining user's rows and skipped the deleted user's in both tables (and the run did not fail), and no trigger was left disabled. `tests/test_restore_accounts.py` pins the filter in the SQL of every table.

Not covered, and said plainly: the real PostgREST endpoint and a real Supabase project were not used (no key was used or printed), so the read path with a real response shape, and `--create-auth-users` against Supabase's real `auth.users` and its sign-in linking, are unproven; the force-push retention step was tested against local bare repositories, not GitHub. First live checks: after the first daily run with `BACKUP_DEPLOY_KEY` set, look at `ravidorr/misra-backups` (one commit per day with changes, files `*.json`), and rehearse a restore into a scratch Supabase project.

## The history lives in Supabase

PLAN.md step 9.1. The crawl's history (`jobs`, open and closed, and `crawl_log`) is in Supabase and nowhere else; `data/jobs.db` is a per-run scratch file that is not committed (it is in `.gitignore`).

- **A run.** `sync_supabase.py --pull` (the first step after the deploy-key check) rebuilds `data/jobs.db` from Supabase: every row of `jobs` without the text of the postings, and the whole `crawl_log`, each row with its Supabase id. It reads through the export's `Source`, which checks every table against its exact row count and reads it again when it changed under the read. The crawl, the coverage-gap step and the dashboard build then work on that file exactly as before; the sync step writes the open jobs, the closed ones and the descriptions as before and the log rows that have no id yet (this run's), and deletes the log older than 90 days (`CRAWL_LOG_DAYS`, so the table, the pull and the backup stay a fixed size). Nothing else is remembered between runs: a robots.txt streak and a "new" coverage gap are read from the crawl log.
- **It fails closed.** A pull that cannot read Supabase, finds an empty `jobs` table or has no secret exits non-zero, so the run goes red and the alert says `pull=failure`; the crawl does not start, and the dashboard is not built or published from nothing. An empty `jobs` table is refused on purpose: a crawl that started from nothing would take every job for a new one, upsert them with a new `first_seen` and the digest would email them all. To start a new project, restore the backup first (below). A missing `data/jobs.db` stops the crawl and the build (`common.open_history`), and the sync refuses a file the pull did not write (`PRAGMA user_version`): a history made any other way, such as a `touch`ed one for a local trial, would overwrite every `first_seen` in Supabase.
- **A partial pull is refused too.** A `jobs` table with under half of the open jobs the dashboard last published (`docs/jobs.json`, which every checkout has; a count of 5 or more is needed to judge, the same rule as the backup's shrink guard, `export_accounts.fell_by_half`) fails the pull: a table that is partly hidden or lost would look like jobs that closed and the rest like new ones. If the loss is real, start the workflow by hand with `allow_shrink` ticked (it passes `--allow-shrink` to the pull; the build then rewrites `docs/jobs.json` and the check is the same again). A `docs/jobs.json` that cannot be read fails the pull as well (`--allow-shrink` skips the file); an empty `crawl_log` is a `::warning::` (counts only, never a failure: the robots.txt streaks and the held drops of the browser crawl start again).
- **Order of the steps.** The dashboard is built only after a good sync, and committed only after a good build: a failed pull or sync never publishes. The sync also runs after a crawl that failed part of the way, because `run.py` commits to the scratch file company by company (the API companies before the slow browser crawl starts): what it recorded reaches Supabase, and the run is red anyway. What is not saved: a crash or the 20-minute timeout inside the browser crawl itself loses that crawl's results (the API companies are kept), and a company's rows are all-or-nothing.
- **The checkout is the tip of `main`.** The daily job checks out `ref: main`, not the commit its run was queued at. A run that waited in the `daily-job-digest` group behind a `publish-docs` run (the two share it) otherwise built on the commit from before that run's push, and its own push of `docs/` conflicted (2026-11-17, two runs: "push to main failed after 3 attempts"). The group itself was right: the runs did not overlap, the checkout was stale.
- **The key.** The service-role key is on the pull, sync, digest and backup steps of the daily workflow and on the pull step of `publish-docs` (the dashboard build needs the history); the crawl never has it. No migration was needed for the move: `crawl_log` already existed with the service role as its only reader.
- **The dashboard files are the only thing committed.** The daily workflow pushes `docs/` with the deploy key, as before, so the `protect-main` ruleset (the deploy key as its bypass actor) does not change. The first build after the move may reorder `docs/jobs.json` once: the open jobs are now listed by company and job id.
- **What the old history was.** The commits of `data/jobs.db` up to the move stay in git, so a company that asks for its jobs to be erased from the history has that limit (see "Removing a company on request").

### The move, once

Before the first run of the new code, copy what Supabase does not have yet from the last committed `data/jobs.db`: the jobs that closed before the sync began (2026-11-15) and the crawl log. Without it the first run still works (every open job is in Supabase, the log only starts later: a sharp drop is believed three days late, a failing company shows after two crawls, "last good crawl" on the coverage card says "never") but that history is gone from the live data. Do it in this order, with the service-role key from the Supabase dashboard (never in chat):

1. Merge nothing yet. Take the last committed database: `git show origin/main:data/jobs.db > ~/old-jobs.db` (the file exists in `main` until the PR of step 9.1 merges).
2. Save this as `seed_history.py` in the repository root (not committed) and run it: `SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... .venv/bin/python seed_history.py ~/old-jobs.db`. It refuses to run when `crawl_log` in Supabase is not empty, never touches an open job and writes only what is missing:

   ```python
   """One-off for the move of the history to Supabase (PLAN.md step 9.1): copy the closed jobs and the crawl log of the last
   committed data/jobs.db into Supabase. Run from the repository root with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY set:
       python seed_history.py <the old data/jobs.db>
   Open jobs are not touched (the daily sync owns them). Writes only when the crawl log in Supabase is still empty."""

   import os
   import sqlite3
   import sys

   import requests

   import classify
   import regions
   import sync_supabase as s

   client = s.Client(requests.Session(), os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
   old = sqlite3.connect(sys.argv[1])
   if client.call("GET", {"select": "id", "limit": 1}, table="crawl_log").json():
       sys.exit("crawl_log in Supabase is not empty: the history was moved already, nothing written")
   closed = []
   for values in old.execute(f"SELECT {', '.join(s.HISTORY_COLUMNS)} FROM jobs WHERE status='closed'"):
       row = dict(zip(s.HISTORY_COLUMNS, values, strict=True))
       place = regions.place(row["location"])
       row.update(
           posted=row["posted"] or None,
           level=classify.level(row["title"] or ""),
           role_types=classify.role_types(row["title"] or ""),
           regions=list(place.regions),
           locations=list(place.locations),
       )
       closed.append(row)
   open_keys = client.open_keys()
   closed = [row for row in closed if (row["company"], row["job_id"]) not in open_keys]  # never turn an open job closed
   client.upsert(closed)
   query = f"SELECT {', '.join(s.LOG_COLUMNS)} FROM crawl_log ORDER BY rowid"
   log = [dict(zip(s.LOG_COLUMNS, v, strict=True)) for v in old.execute(query)]
   for start in range(0, len(log), s.BATCH):
       client.call("POST", {}, log[start : start + s.BATCH], table="crawl_log", prefer="return=minimal")
   print(f"seeded {len(closed)} closed jobs and {len(log)} crawl log rows")
   ```

3. It prints `seeded N closed jobs and M crawl log rows`. Check in the SQL editor: `select count(*) from public.crawl_log` is M, and `select count(*) filter (where status = 'closed') from public.jobs` went up by about N.
4. Merge the PR, then start the workflow by hand with `digest_dry_run` ticked: the pull should say `pulled <jobs> jobs and <log> crawl log rows`, the sync `<n> crawl log rows written`, and `docs/` changes only by the ordering and the build time. Then delete `seed_history.py` and `~/old-jobs.db`.

### Rehearsal, 2026-11-17

Run against a local Postgres 17 built from the committed migrations plus the Supabase stub, behind a throwaway stand-in for PostgREST (about 100 lines over `psql`: filters, `order`, `limit` and `offset`, `Content-Range` with the exact count, upsert with `on_conflict`, `return=representation` deletes, `rpc` calls), with the real data of the last committed `data/jobs.db` (9,721 jobs, 6,560 log rows) as the history and a stand-in for the crawl that returned each company's open jobs (the five SmartRecruiters companies skipped for `robots.txt`):

1. Seed (the script above, in the PostgREST form, and, separately, `restore_accounts.py` from export-format files built from the old database): 2,913 closed jobs and 6,560 log rows in; a second run refuses (the log is not empty), the restore run twice changes nothing.
2. `--pull` rebuilt a file whose `jobs` and `crawl_log` (order included) were identical to the old database's. A crawl without the file stopped with the "is missing" message.
3. A steady day (nothing changed): the sync upserted 6,808 open jobs, wrote 6,769 descriptions and 244 log rows; a second sync wrote nothing (all texts unchanged, no log rows). `build_dashboard.py` gave the same 6,808 jobs as the committed `docs/jobs.json` (only the failing-company flags differ, because the stand-in crawl succeeded for companies that were failing then), the same `firstDay` data and the same coverage page data; a second build was byte-identical.
4. A day with changes: one new job at one company, one fewer at another, one company failing. The new job was in Supabase with its `first_seen` and its text, the missing job was closed, the failing company's jobs stayed open, its log row said `request failed`, and `::warning::coverage gaps: 1 (1 new): failing 1 (monday.com)` was logged. A fresh pull gave a history equal to the file the run had just left.
5. Export, then restore into a second, empty database: 9,722 jobs (2,914 closed) and 7,536 log rows, the same counts as the first; the export held no `description`, `details` or `snippet` key and no description text; a second restore changed nothing; the next crawl in the restored project wrote 6,641 descriptions; the id sequence continued after the largest log id; no trigger was left disabled.
6. Failures: with Supabase unreachable, with an empty `jobs` table and with an empty secret the pull exits non-zero and writes no file.
7. A `touch`ed history crawled from nothing (6,769 jobs, all new) was refused by the sync (`PRAGMA user_version`), nothing was sent. The steps 2 to 4 were run again on the final code.

Not covered, and said plainly: a real PostgREST and a real Supabase project (the stand-in is not PostgREST, so a response header or a filter that behaves differently there is unproven), a real GitHub Actions run (the workflow steps are pinned by tests and the first live run after the move is the check), and the browser crawl, which was replaced by the stand-in. First live checks: the first daily run after the move (the log lines above; `docs/` committed; a `backup` commit in `ravidorr/misra-backups` with `jobs` and `crawl_log` counts), and a restore of that backup into a scratch Supabase project.

## Reported sites

Visitors report a missing company with "Add a company" on the dashboard's coverage gap card (PLAN.md step 5.2). Each report reaches the maintainer by email (company, career page link and why it matters). Nothing is stored about it, so the email is the record: act on it or answer it, then do one of these in a PR:

1. **Accept it for the crawl.** Check that the link is the company's own career page and that it lists roles in Israel, then add the company to `data/seed_companies.csv` (`name,domain`). Run `python3 discover.py` (it rewrites `data/companies.json` with each company's careers URL and job feed) and commit the result; the daily crawl then reads the company. If `discover.py` finds no feed for it, the next step applies.
2. **Keep it on the gap card, with a reason.** Add a row to `data/unresolved_candidates.csv` (`name,domain,careers_url,segment,reason,note`, CRLF line endings). `reason` must be one of the keys of `REASONS` in `build_dashboard.py` (for example `no_feed_found` or `needs_reader`) and `note` says in a few words why we cannot read the site; the dashboard shows both on the coverage gap card. `careers_url` is the company's career page over `https` (leave it empty only for `no_careers_url`, which has none); the card links every site, to the career page, or to `https://<domain>` when there is none, and `tests/test_build_dashboard.py` fails when a row has no usable address.
3. **Decline it** when the link is not a career page, the company has no roles in Israel, or the report looks like spam. Nothing needs to be recorded.

Rows in `data/seed_companies.csv` and `data/unresolved_candidates.csv` are public, so never copy anything from a report beyond the company and its public site.

## The legal pages

The privacy notice, the terms and the security note are on the site as `privacy.html`, `terms.html` and `security.html`. They are generated, never written by hand: `build_dashboard.py` converts `PRIVACY.md`, `TERMS.md` and `SECURITY.md` into `templates/legal.html`.

- **Edit the Markdown, never the HTML.** The Markdown file is the source of truth. A change to `docs/privacy.html` (or to the body of `templates/legal.html`) is overwritten at the next build. Raw HTML in the Markdown is shown as text, not run.
- **Links.** Write a link to another legal document as `[terms](./TERMS.md)`: it becomes a link to `terms.html` on the site and still works on GitHub. A link to any other file of the repository (`./CONTRIBUTING.md`) becomes a link to it on GitHub. Keep an unambiguous heading: its anchor is the text lowercased with dashes, as on GitHub. No images.
- **Look at it locally.** `.venv/bin/python build_dashboard.py` writes the pages to `docs/`; open `docs/privacy.html` in a browser. Do not commit the generated files in a PR: the daily run and the `publish-docs` workflow do.
- **Tests.** `tests/test_legal_pages.py` checks that every heading, link, paragraph and the date line of each document reach its page, so a Markdown file that the build cannot show whole fails the tests. Keep the "Last updated" line (or, in `SECURITY.md`, the "Written ... Next full review due ..." sentence) in its usual form, or update the test with it.
- **Publishing.** When a Markdown file, `templates/`, `design-system/`, `vendor/` or `build_dashboard.py` changes on `main`, `.github/workflows/publish-docs.yml` rebuilds `docs/` and pushes it with the deploy key, so the site follows within minutes. It waits for the daily run (same concurrency group) and never runs the crawl.
- **Pages that link to them.** Link to `https://ravidorr.github.io/misra/privacy.html` and `.../terms.html`, not to the Markdown files on GitHub (the repository `README.md` may keep relative links, as people read it on GitHub).

## Release policy

When a PR or push to `main` changes `package.json`, `pyproject.toml`, or a Python module listed in `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), CI runs the release gate: the package version must increase (SemVer) and `CHANGELOG.md` must have a non-empty section for that version. PRs that only touch docs, tests, or workflows do not need a bump. Tagged releases (`vX.Y.Z`) must match `package.json` and have non-empty notes extracted from the changelog.
