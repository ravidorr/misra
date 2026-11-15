# Contributing

## Setup

```bash
nvm install && nvm use
npm ci
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Workflow

1. Branch from `main`.
2. Make changes. Hooks run lint on commit and the full suite on push; never bypass them.
3. Commit subjects are at least 15 characters.
4. If the PR changes `package.json`, `pyproject.toml`, or a Python module, update `CHANGELOG.md` and bump the version in both `package.json` and `pyproject.toml` (one SemVer bump per PR).
5. Open a **draft** PR. Link an issue with `Closes #<issue>` only when the PR resolves one.

## Standards

- Strict ruff, mypy, ESLint, Stylelint, html-validate, and markdownlint must pass.
- Python test coverage must be 100% (lines and branches). It is enforced on push and in CI; never lower `fail_under` or add blanket omits.
- There is no UI; the dashboard is generated from `templates/dashboard.html`.
- See [AGENTS.md](./AGENTS.md) for the full rule list.

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
   step skips the company and deletes its jobs from `data/jobs.db`; the sync step deletes its rows from Supabase `jobs`,
   and the stars and discards (`user_jobs`, which keep a title and a link) and emailed keys (`emailed`) of every user,
   by exact name; the build drops it from `docs/jobs.json`, `docs/jobs.jsonld` and the coverage card; the commit step
   publishes. A user's star for that company is deleted too: the removal is complete, and the star row would keep the
   title and the link.
5. Verify, and reply to the company the same day.

### The test of success

All of these hold after the run:

- The log of the crawl step has `exclusion list: deleted <n> jobs of Acme` (a later run has no such line: there is
  nothing left), and the sync step has `Supabase: exclusion list, deleted {...} for Acme`.
- `sqlite3 data/jobs.db "select count(*) from jobs where company='Acme'"` prints `0` on the new commit of `main`.
- `curl -s https://ravidorr.github.io/misra/jobs.json | grep -c Acme` and the same for `jobs.jsonld` print `0` once
  GitHub Pages has published (a minute or two).
- In the Supabase SQL editor,
  `select (select count(*) from jobs where company='Acme') + (select count(*) from user_jobs where company='Acme') + (select count(*) from emailed where company='Acme')`
  is `0`.
- The coverage card on the page does not mention the company.

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
- Earlier commits of `data/jobs.db` and `docs/jobs.json` still hold the old links. A removal request does not rewrite
  history; if a company insists, that is a separate decision.
- With a non-empty list and empty Supabase secrets the sync step fails on purpose: a removal must not look done.
- Nothing here is applied to the Supabase project by hand: no migration is involved, and the service-role key that the
  sync step already has does the deleting.

## How the crawler behaves

- One User-Agent everywhere: `Misra/<version> (+https://ravidorr.github.io/misra/; raanan@avidor.org)`, the constant
  `USER_AGENT` in `common.py`. Do not put another string in a module.
- At most one request a second to a host, for the adapters and the browser crawl alike (`reserve_slot` in `common.py`),
  with 8 companies in parallel for the adapters and 3 pages at once for the browser.
- `robots.txt`: fetched once per host and run; our `Misra` group wins over `*`. A missing file, a 4xx, a 5xx or a timeout
  means allowed (a 5xx or a timeout is logged). An ATS's public API (Greenhouse, Lever, Ashby, Workable,
  Workday, Oracle, Phenom) counts as published for programs: a blanket `Disallow: /` does not stop it, a rule naming its
  path does. A company whose host forbids the address is skipped, not failed: it is not retried, does not count as a
  failure, sends no alert and shows on the coverage card as "robots.txt does not allow it".
- The switch is `ROBOTS_ENFORCED` in `common.py`. Set to `False`, the crawl ignores the rules but logs which hosts it would
  have refused. Every run prints which state it is in.
- Audit on 2026-11-15 (read-only: only each host's `/robots.txt` was fetched, once, politely): 234 companies on 75
  hosts. The one that blocks us is SmartRecruiters: `api.smartrecruiters.com` answers `User-agent: *` with `Disallow: /`
  and allows only `LinkedInBot` under `/v1/companies/`. Its documentation says nothing about aggregators, and the
  maintainer has asked SmartRecruiters for permission. Until it is given, Armis, Kaltura, Atera, Nexar and ServiceNow
  are skipped like any company a host forbids.
- No host is exempt. `ROBOTS_API_EXCEPTION_HOSTS` in `common.py` is the place for a host that has given WRITTEN
  permission to be called despite a blanket `Disallow: /`; it is empty on purpose (a test pins that), and such a
  permission is the only reason to add a host back. A rule that names an API path is honoured either way.
- A company skipped because of `robots.txt` on every run for 7 days in a row (`ROBOTS_CLOSE_DAYS` in `common.py`) gets
  its open jobs closed, by the same close the crawl uses, so the Supabase sync marks them closed and a later successful
  crawl reopens them. Not earlier: a temporary `robots.txt` glitch at a host must not wipe the jobs and bring them back
  as new. The first skipped day of the streak is kept in the table `robots_skips(company, since)` of `data/jobs.db`
  (created with `CREATE TABLE IF NOT EXISTS`), and a company that is not skipped in a run (crawled, failed another
  way, or off the list) loses its row, so the week starts again. The rule never applies to failures, to excluded
  companies (they have their own removal path) or to a name with any other result in the same run (a second board that crawled or failed). The run logs counts only.

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

- `data/jobs.db` is 3,067,904 bytes (about 3 MB) on 2026-11-15, holding 6,766 open and 2,722 closed jobs (9,488 rows).
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
- Full descriptions (PLAN.md step 7.1) add about 5 to 20 times today's text. Applied to the 3 MB above, that is about 15
  to 60 MB in SQLite. A full-text index and the other indexes in Postgres can add up to the size of the text again, so
  plan for up to about 120 MB in Supabase, which is under a quarter of the 500 MB. The multiplier is an assumption, not
  measured: step 7.4 measures it after a month of real descriptions.
- Watch the repository too. `data/jobs.db` is committed every day, so a 15 to 60 MB database means a new large binary in
  the history every day. GitHub recommends a repository under 1 GB, warns about a file over 50 MiB and refuses one over
  100 MiB (GitHub's large-files rules, from memory, not re-fetched on the day: check them). The daily deploy push would then fail.
  Step 7.4 and step 9.1 (the export that replaces the committed database) must decide this before descriptions go into
  SQLite. The trim plan already in PLAN.md (descriptions for open jobs only, dropped some days after a job closes) helps
  both.
- Resend: `report-site` and the confirmation email send through it, and so will the subscriber digests
  (`send_digests.py`, PLAN.md step 6.4); the maintainer's own digest still goes through Gmail until they subscribe
  (see "The daily digest", below). The volume arithmetic is in "Resend volume".

### Pause prevention and what the dashboard does if the backend is paused

- The daily workflow writes to Supabase every day at 04:30 UTC (`sync_supabase.py`; the step runs only when the crawl
  succeeded, and scheduled runs can be delayed or skipped), and the dashboard reads from it on every visit. Both are requests to the project's API, which the pausing page says count as activity. Together they are
  the pause-prevention plan; no extra keep-alive job is added.
- That plan has a hole: the pages do not say that an API call wakes a paused project, so assume the sync cannot. The
  page does not say how a paused project answers API calls either; any failed sync (a refused request, an error answer or
  no open jobs) fails the step, and the maintainer failure alert (`send_alert.py`, Gmail) reaches the maintainer the same
  day. The project is then restored from the Supabase dashboard ("Resume project", up to 1 year).
  Watch for the "about to pause" email from Supabase as well, and open the dashboard of the project when it arrives.
- If the project is paused, the page cannot load the jobs and shows its error card: the summary reads "Could not load
  the roles." and a notice "The roles could not be loaded" with a "Try again" button (`showLoadError` in
  `templates/dashboard.js`). There is no offline copy of the jobs by design (PRD constraint), so nothing else is shown.
  Signing in, stars and the site report fail the same way until the project is restored.
- Backups: the free plan has none, so the repository is the only copy of the history (`data/jobs.db`), and the daily
  export (below, "Backups of the account tables") is the only copy of the user data (stars, discards, filters, email
  settings): a private repository, at most 28 days of history, restorable with `restore_accounts.py`.

### Upgrade triggers

Move to a paid plan, or trim, when any of these is true:

- The Supabase database passes 400 MB of the 500 MB (80%). Trim first (the plan in PLAN.md step 7.4); upgrade if that is
  not enough.
- Supabase egress passes 4 GB of 5 GB in a month, or Edge Function invocations pass 400,000 of 500,000.
- Resend sends pass 80 of 100 in a day, or 2,400 of 3,000 in a month (this includes `easy-theory` and received mail). In
  subscribers that is about 60 once reports are counted, since the monthly cap binds first. Step 6.5 records the
  subscriber count against this before the digest opens.
- A pause happens, or the "about to pause" warning arrives twice. The account tables are backed up daily (below); move
  to a plan with backups if that export ever stops being enough (a restore that is too slow, or a need for point-in-time recovery).
- The repository passes 800 MB (80% of the recommended 1 GB), or `data/jobs.db` passes 50 MB (GitHub's warning size).

## The daily digest

`.github/workflows/daily.yml` runs `send_digests.py` (PLAN.md step 6.4) right after the Supabase sync and before the build and the deploy push.

- **Secrets.** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `RESEND_API_KEY` and `UNSUBSCRIBE_SECRET` are set on that step only (the sync step also has the first two), never on the job and never during the crawl. All four empty: the step prints a message and exits 0; some empty: it fails and names them.
- **Failure.** The step runs only after a good sync (the digests and the dashboard then read the same rows). If it fails, the run is red, the build and the data commit still run, and the failure email (`send_alert.py`) goes to the maintainer; its `STEPS` line shows `digests=failure`.
- **Dry run.** Start the workflow by hand (Actions, "daily-job-digest", Run workflow) with `digest_dry_run` ticked: the digest step gets `--dry-run`, sends nothing and records nothing, and its log holds counts only (the Actions logs are public). Note that a manual run still crawls, still commits and pushes `data/jobs.db` and `docs/` to `main`, and, while the switch below is on, still sends the maintainer digest.
- **The maintainer's own digest.** `send_email.py` (one recipient, Gmail) still runs, behind the workflow constant `LEGACY_MAINTAINER_DIGEST: "true"` at the top of `daily.yml`. Signups are open (PLAN.md step 6.6, v0.31.0: the build constant `DIGEST_SIGNUP_ENABLED = True` and the function secret of the same name `true`; to close them again unset the secret, and never set the build constant back to `False` once anyone has subscribed, because that also removes "Turn the email off" from the page). The maintainer is meant to become an ordinary subscriber through the dashboard. Switch over in this order: subscribe and confirm, wait for one daily email from `send_digests.py` to arrive, then in one PR set the constant to `"false"` and delete `send_email.py` and the selection logic that FR-9 replaced (`select`, `ROLE` and `EXCLUDE` in `digest.py`; keep `coverage_gaps` and its helpers only if something still reads them, since the dashboard card has its own). Remove `send_email` from `py-modules` and mypy `files` in `pyproject.toml`, and from `RELEASE_TRIGGER_PATHS` in `ci.yml` and `.husky/pre-push`. Keep `send_alert.py`.

### Resend volume

Checked on 2026-11-15 against [Resend pricing](https://resend.com/pricing) and [account quotas and limits](https://resend.com/docs/knowledge-base/account-quotas-and-limits).

- Free plan: 100 emails a day (a UTC calendar day, resets at 00:00 UTC) and 3,000 a month, 3 custom domains, an API rate limit of 10 requests a second. Each To, CC or BCC recipient counts as one email, and received mail counts too. Over the limit the API answers 429 (`daily_quota_exceeded` or `monthly_quota_exceeded`) and Resend does not queue the email. The pages do not say whether the quota is per team or per domain, so assume it is shared with the maintainer's other project, `easy-theory`, and with `report-site` and the confirmation emails (site reports are capped at 20 a day).
- Daily subscribers: at most one email each a day, and none when nothing is new, so the count is a ceiling. 100 a day caps daily subscribers at 100 minus the other uses (20 for reports, plus whatever `easy-theory` sends and receives). 3,000 a month is about 100 daily subscribers (30 emails each), and 96 in a 31-day month, before any other use. Taking the 20 report emails off the daily cap leaves about 76 to 80 for daily subscribers; about 60 is the working limit, which keeps headroom for `easy-theory`, confirmation emails and a bad day, and is the number the trigger below uses.
- Weekly subscribers: one email a week, about 4.3 a month, so about a seventh of the cost of a daily subscriber (the plan's rough figure was a fourth; seven is the arithmetic). A weekly send is on Sundays, so those emails land in one day's 100: 60 or more weekly subscribers would take most of a Sunday on their own, so count them in the daily cap on Sundays.
- Today (2026-11-15): zero real subscribers. Signup is off in the build and in the function, so no row can exist except the maintainer's own tests. Count them with the query below.
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

## Backups of the account tables

Supabase Free has no backups, so the daily workflow (`.github/workflows/daily.yml`, step `backup`, PLAN.md step 9.1) copies the account tables to a PRIVATE repository, `ravidorr/misra-backups` (branch `main`). This repository is public: no account data may be committed to it, logged, uploaded as an artifact or left in the checkout.

- **What.** `export_accounts.py` reads every row of `profiles`, `user_prefs`, `user_jobs`, `subscriptions` and `emailed`, all columns (the confirmation and unsubscribe token hashes too), with the service-role key, and writes one `<table>.json` per table: one row per line, keys sorted, rows sorted by primary key, so a day's diff is only what changed. Not exported: `jobs` and `crawl_log` (the crawl rebuilds them), the abuse counters (`report_*`, `confirm_*`, short-lived) and Supabase's own `auth` tables. A test fails when a column or an account table in the migrations is missing from the export (`tests/test_export_accounts.py`).
- **Where it runs.** After the deploy push, so it never delays the data. It runs on every run, even when the crawl failed. It writes into a temporary directory under `RUNNER_TEMP`, outside the checkout (the module refuses a directory inside the repository), and then `publish_backup.py` clones the backup repository, replaces the five files, commits only when something changed ("backup 2026-11-16: profiles 3, user_prefs 3, ... (n rows)", counts only) and pushes to `main`. The Actions log shows counts only.
- **Secrets.** `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (also on the sync and digest steps) and `BACKUP_DEPLOY_KEY` are set on the backup step only. `BACKUP_DEPLOY_KEY` is the private half of a read-write deploy key of `ravidorr/misra-backups`; it is written to disk only in that step, by `scripts/backup-key.sh`, in a file with a unique name, used through `GIT_SSH_COMMAND` with `IdentitiesOnly=yes` and no agent, and removed with the work directory when the step ends. No checkout gets `ssh-key:`. `tests/test_backup_workflow.py` pins all of it. The deploy key belongs to that one repository and reaches no other, and a deploy key has no expiry date, so rotate it by hand at the yearly review (by 2027-11-15, `SECURITY.md`): make a new key pair, add it to the deploy keys of `ravidorr/misra-backups`, update the Actions secret, run the workflow once, then delete the old key in the repository's deploy keys.
- **Guards.** The export fails when a table has columns that are not in `TABLES` (the day a migration adds one) or when `profiles` is empty beside non-empty tables (a wrong key sees nothing). `publish_backup.py` refuses to commit, and never replaces the history, when a table that held 5 or more rows now holds under half of them; if that is real, run it by hand with `--allow-shrink`.
- **Failure.** The step fails (non-zero) when a secret is empty, a table cannot be read, a table keeps changing under the read (three tries), git cannot clone or push. The run goes red and the maintainer alert (`send_alert.py`) lists `backup=failure`. Nothing is written for a table that failed: every table is read before any file is written.
- **Retention.** The repository keeps history, so a deleted account would stay in old commits. When the first commit of `main` is more than 28 days old, `publish_backup.py` pushes one new commit with no parent that holds the current files, over `main`, with `--force-with-lease` (it replaces only the history it read). A deleted account therefore leaves the backups within 28 days plus one daily run, under the 30 days `PRIVACY.md` promises. GitHub may keep the replaced commits reachable by their ids for a while; if someone must be erased at once, delete and recreate `ravidorr/misra-backups` (and the deploy key), then run the workflow. The force push goes only to the remote named in the workflow, which is the backup repository.
- **If the backup stops.** The alert says so. If it fails for more than a few days, fix it before the history gets older than 30 days; a missed day only delays the replacement, so the 30 days in `PRIVACY.md` hold only while the daily job runs.

### Restore

1. Clone the backup repository to a directory OUTSIDE this checkout: `git clone --depth 1 git@github.com:ravidorr/misra-backups.git ~/misra-backup` (the latest commit only: an older one would bring back accounts deleted since). The clone holds plaintext email addresses: delete it as soon as the restore is done (or the test is over), so that no copy sits on a disk outside the 30-day rotation (`SECURITY.md`). The repository is private; the files are not encrypted by us.
2. `python restore_accounts.py ~/misra-backup ~/restore.sql` (add `--exclude-user-ids=<file>`, one id per line, for the people who deleted their account after the backup: they are left out of every table; `--create-auth-users` refuses to run without it, an empty file meaning nobody left; add `--pause-subscriptions` to mark every still-active subscription unsubscribed so that nobody who opted out after the backup is emailed; and for a NEW Supabase project use `--create-auth-users` that has no `auth.users` rows: it creates a bare row, id and email from `profiles`, for each one that is missing, and each person then signs in with Google as before). **Without `--create-auth-users` (the live project, where `auth.users` is intact) the script restores only the rows of people who still have an `auth.users` row**: each insert is filtered by the database itself (`where exists (select 1 from auth.users u where u.id = r.user_id)`, on the exported user id, no email involved), so an account deleted after the snapshot is skipped in every table and is never brought back, and the run does not fail on it. Use `--create-auth-users` only in a fresh project: it recreates the sign-in rows, so it cannot tell who deleted their account, and it refuses to run without `--exclude-user-ids`. The script is written readable by you only and holds everyone's data: delete it after use.
3. Read it, then run it in the SQL editor of the project (or `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f ~/restore.sql`). It is one transaction, inserts in foreign-key order, uses `on conflict do nothing` (running it twice, or into a project that already has some of the rows, changes nothing that exists, and it deletes nothing), and switches the five tables' own triggers off while it runs and on again at the end, so the restored rows keep their timestamps and the consent record.
4. Check the counts against the log line of the backup commit (in the live project the counts of the script are the upper bound: the people who deleted their account since are the difference, and you never need their emails to know that), and run the daily workflow once (the sync restores the `jobs` table).
5. Undo what the backup cannot know. In the live project, accounts deleted after the snapshot are already skipped (step 2). Anyone who unsubscribed after the time of the backup commit comes back from it (and, in a fresh project with `--create-auth-users`, so does anyone who deleted their account): before the next digest run, delete those accounts again and mark those subscriptions unsubscribed (find them in Supabase's auth logs and the `unsubscribe` and `delete-account` function logs for that window, or from any request the maintainer received by email). A restored `subscriptions` row of a person who had unsubscribed that day is a person who would be emailed against their wish. If the window cannot be checked, do not send digests until it can.

### Rehearsal, 2026-11-15

What was run, against a local Postgres 17 (`postgres:17-alpine` in Docker, database `misra_rehearsal`) built from `supabase/migrations/*.sql` plus a stub of Supabase's roles and the `auth` schema (`auth.users` with the columns the `--create-auth-users` step names). Fixture: 3 users (one with an apostrophe in the address, one with an accent), 2 prefs rows including filter text with `$restore$`, a backslash and quotes, 2,601 `user_jobs` (two users over the 1,000-row page size, plus a discard), 2 subscriptions (one confirmed, one unsubscribed, token hashes set) and 1 emailed row. The real `export_accounts.Source` read it through a stand-in for PostgREST that pages with `limit` and `offset` and answers `Content-Range`.

1. Export: 3 profiles, 2 prefs, 2,601 user_jobs, 2 subscriptions, 1 emailed, files written mode 0600.
2. Emptied everything by deleting every `auth.users` row (the foreign keys cascade). Restored with `--create-auth-users`: all five tables back to the same counts, and a second export was byte-identical to the first (every column, timestamps and token hashes included).
3. Ran the same script a second time: counts unchanged, export byte-identical (idempotent).
4. Truncated the five tables, kept the users, restored without the flag: export byte-identical.
5. Changed one restored row, ran the script again: the changed row stayed as it was (nothing is overwritten, nothing deleted). Afterwards no trigger was left disabled.
6. Deleted accounts (second rehearsal, 2026-11-15, same kind of local Postgres 17 with the committed migrations): two users, `profiles` and `user_prefs` rows for both, exported to JSON, then one user deleted from `auth.users` (the foreign keys cascade) and the public tables emptied. The script made without `--create-auth-users` restored the remaining user's rows and skipped the deleted user's in both tables (and the run did not fail), and no trigger was left disabled. `tests/test_restore_accounts.py` pins the filter in the SQL of every table.

Not covered, and said plainly: the real PostgREST endpoint and a real Supabase project were not used (no key was used or printed), so the read path with a real response shape, and `--create-auth-users` against Supabase's real `auth.users` and its sign-in linking, are unproven; the force-push retention step was tested against local bare repositories, not GitHub. First live checks: after the first daily run with `BACKUP_DEPLOY_KEY` set, look at `ravidorr/misra-backups` (one commit per day with changes, files `*.json`), and rehearse a restore into a scratch Supabase project.

## Reported sites

Visitors report a missing company with "Report a site" on the dashboard's coverage card (PLAN.md step 5.2). Each report reaches the maintainer by email (company, careers link and why it matters). Nothing is stored about it, so the email is the record: act on it or answer it, then do one of these in a PR:

1. **Accept it for the crawl.** Check that the link is the company's own careers page and that it lists roles in Israel, then add the company to `data/seed_companies.csv` (`name,domain`). Run `python3 discover.py` (it rewrites `data/companies.json` with each company's careers URL and job feed) and commit the result; the daily crawl then reads the company. If `discover.py` finds no feed for it, the next step applies.
2. **Keep it on the gap card, with a reason.** Add a row to `data/unresolved_candidates.csv` (`name,domain,segment,reason,note`). `reason` must be one of the keys of `REASONS` in `build_dashboard.py` (for example `no_feed_found` or `needs_reader`) and `note` says in a few words why we cannot read the site; the dashboard shows both on the coverage gap card.
3. **Decline it** when the link is not a careers page, the company has no roles in Israel, or the report looks like spam. Nothing needs to be recorded.

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
