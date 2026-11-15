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
  means allowed (a 5xx or a timeout is logged). An ATS's public API (Greenhouse, Lever, SmartRecruiters, Workable,
  Workday, Oracle, Phenom) counts as published for programs: a blanket `Disallow: /` does not stop it, a rule naming its
  path does. A company whose host forbids the address is skipped, not failed: it is not retried, does not count as a
  failure, sends no alert and shows on the coverage card as "robots.txt does not allow it".
- The switch is `ROBOTS_ENFORCED` in `common.py`. Set to `False`, the crawl ignores the rules but logs which hosts it would
  have refused. Every run prints which state it is in.
- Audit on 2026-11-15 (read-only: only each host's `/robots.txt` was fetched, once, politely): 234 companies on 75
  hosts, none blocked. The one close call is SmartRecruiters: `api.smartrecruiters.com` answers `User-agent: *` with
  `Disallow: /` and allows only `LinkedInBot` under `/v1/companies/`. Armis, Kaltura, Atera, Nexar and ServiceNow use that
  API. They are crawled because the API is documented for programs; removing `api.smartrecruiters.com` from `API_HOSTS`
  in `common.py` would skip those five instead.
- The SmartRecruiters exception, made knowingly: `api.smartrecruiters.com` answers `User-agent: *` with `Disallow: /`.
  It is still called, for Armis, Kaltura, Atera, Nexar and ServiceNow, because its Posting API is SmartRecruiters'
  documented public interface for career sites and programs, not a page. It is the only such host
  (`ROBOTS_API_EXCEPTION_HOSTS` in `common.py`, pinned by a test); an explicit rule for the API path would still be
  honoured.

### Known limits

- A `robots.txt` that cannot be fetched (a timeout, a connection error or a 5xx) counts as allowed, and so does a 404
  or another 4xx. RFC 9309 would treat a 5xx as a full disallow; the lawyer's rule is "respect robots.txt or rate-limit
  politely", so we choose to keep crawling at one request a second and log the failure.
- Only the address a request starts from is checked, with its query parameters. Redirects, and requests a browser page
  makes on its own, are paced but not checked.
- A company skipped because of `robots.txt` keeps its old jobs open (nothing closes on a skip); the page marks them as
  possibly old.
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
| Supabase backups | Not included on Free (they start with Pro, kept 7 days) | [pricing](https://supabase.com/pricing) |
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
- Resend: today only `report-site` sends through it; the digest still goes through Gmail (`send_email.py`) until step
  6.3. Once the digest moves, it will send one email per subscriber a day. The monthly cap binds first: 3,000 over a
  31-day month is about 96 emails a day, so even 100 daily subscribers would exceed it, and with site reports at their
  cap of 20 a day only about 75 subscribers fit, before whatever `easy-theory` sends. Tens of users fit.

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
- Backups: the free plan has none, so the repository is the only copy of the history (`data/jobs.db`), and user data
  (stars, discards, filters) has no copy anywhere but Supabase. Step 9.1 plans the export of all tables into the
  repository; until then a deleted project loses user data for good.

### Upgrade triggers

Move to a paid plan, or trim, when any of these is true:

- The Supabase database passes 400 MB of the 500 MB (80%). Trim first (the plan in PLAN.md step 7.4); upgrade if that is
  not enough.
- Supabase egress passes 4 GB of 5 GB in a month, or Edge Function invocations pass 400,000 of 500,000.
- Resend sends pass 80 of 100 in a day, or 2,400 of 3,000 in a month (this includes `easy-theory` and received mail). In
  subscribers that is about 60 once reports are counted, since the monthly cap binds first. Step 6.5 records the
  subscriber count against this before the digest opens.
- A pause happens, or the "about to pause" warning arrives twice. Backups matter as soon as there are real users: upgrade
  to a plan with backups before the first digest subscriber, or ship the step 9.1 export first.
- The repository passes 800 MB (80% of the recommended 1 GB), or `data/jobs.db` passes 50 MB (GitHub's warning size).

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
