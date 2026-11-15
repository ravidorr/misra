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
- Resend: the quota is described as the account's, and the pages do not split it by domain, so it is shared with
  `easy-theory` on the same account. How much `easy-theory` uses is not confirmed (it is not visible from this repository).
  Whether the free plan's 3 custom domains are already used up is also not confirmed.
- GitHub Pages: the 10 builds an hour soft limit does not apply to a site published by a custom Actions workflow.
  Whether this project publishes that way is not checked here; one deploy a day is far below it either way.
- GitHub Actions: the page says standard runners in a public repository are free, so there is no minutes budget to
  watch (larger or self-hosted runners are not covered by that and are not used here).

### Real data against the limits

- `data/jobs.db` is 3,067,904 bytes (about 3 MB) on 2026-11-15, holding 6,766 open and 2,722 closed jobs (9,488 rows).
- Supabase holds the open jobs only (6,773 at the last two live syncs on 2026-11-14, from PLAN.md step 2.2). The size of
  the Supabase tables is not known here: reading it needs the dashboard (Reports, Database) or a service-role query, and
  no secret is used for this note. As an estimate only, the same rows in SQLite are about 3 MB with its indexes, so the
  `jobs` table with its indexes is expected to be a few MB, far under 500 MB. Measure it once in the Supabase dashboard
  and write the number here.
- `docs/jobs.json` (the open-data download) is 2.1 MB, about 0.23 MB compressed. The page reads the same rows from
  Supabase, so one page load is of that order (an estimate, not measured). At that size 5 GB of egress is roughly 2,500
  loads a month uncompressed and ten times that compressed. Tens of users are far below it; thousands of visitors a
  month would not be.
- Full descriptions (PLAN.md step 7.1) add about 5 to 20 times today's text. Applied to the 3 MB above, that is about 15
  to 60 MB in SQLite. A full-text index and the other indexes in Postgres can add up to the size of the text again, so
  plan for up to about 120 MB in Supabase, which is under a quarter of the 500 MB. The multiplier is an assumption, not
  measured: step 7.4 measures it after a month of real descriptions.
- Watch the repository too. `data/jobs.db` is committed every day, so a 15 to 60 MB database means a new large binary in
  the history every day, and GitHub recommends a repository under 1 GB. Step 7.4 and step 9.1 (the export that replaces
  the committed database) must decide this before descriptions go into SQLite. The trim plan already in PLAN.md (descriptions for open jobs only,
  dropped some days after a job closes) helps both.
- Resend: the digest sends one email per subscriber a day, so 100 a day is about 100 daily subscribers, minus the
  site-report emails (at most 20 a day, the cap in `report-site`) and whatever `easy-theory` sends, and 3,000 a month
  is about 100 subscribers a day for 30 days. A site-report flood (up to 20 a day) and the digest draw on the same
  daily 100. Tens of users fit.

### Pause prevention and what the dashboard does if the backend is paused

- The daily workflow writes to Supabase every day at 04:30 UTC (`sync_supabase.py`), and the dashboard reads from it on
  every visit. Both are requests to the project's API, which the pausing page says count as activity. Together they are
  the pause-prevention plan; no extra keep-alive job is added.
- That plan has a hole: a paused project cannot be woken by the sync, because a paused project does not answer. If the
  daily sync step fails with a connection error, the maintainer failure alert (`send_alert.py`, Gmail) reaches the
  maintainer the same day, and the project is restored from the Supabase dashboard ("Resume project", up to 1 year).
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
- Resend sends pass 80 of 100 in a day, or 2,400 of 3,000 in a month, on any day (this includes `easy-theory`). Step 6.5
  records the subscriber count against this before the digest opens.
- A pause happens, or the "about to pause" warning arrives twice. Backups matter as soon as there are real users: upgrade
  to a plan with backups before the first digest subscriber, or ship the step 9.1 export first.
- The repository passes 1 GB, or `data/jobs.db` passes 100 MB.

## Release policy

When a PR or push to `main` changes `package.json`, `pyproject.toml`, or a Python module listed in `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), CI runs the release gate: the package version must increase (SemVer) and `CHANGELOG.md` must have a non-empty section for that version. PRs that only touch docs, tests, or workflows do not need a bump. Tagged releases (`vX.Y.Z`) must match `package.json` and have non-empty notes extracted from the changelog.
