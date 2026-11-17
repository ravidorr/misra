# Misra

Live page: <https://ravidorr.github.io/misra/>

[![CI](https://github.com/ravidorr/misra/actions/workflows/ci.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/ci.yml) [![Release](https://github.com/ravidorr/misra/actions/workflows/release.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/release.yml)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ravidorr/misra/pulls) [![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/ravidorr/misra/graphs/commit-activity) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Node.js](https://img.shields.io/badge/Node.js-26-339933?logo=node.js&logoColor=white)](https://nodejs.org/)

## What it is

Misra is a daily list of entry-level, student and product roles in Israel.
It reads them from the companies' own career pages.
It is for people looking for one of those roles.

The dashboard has:

- Filters, and a Star or "Not for me" button on each role, saved in the browser.
- Light and dark themes.
- An optional "Sign in" with Google. It saves your stars, "Not for me" choices and filters to your account, so they follow you to other devices. The dashboard works the same without it.
- Optional email job alerts. An alert holds new jobs only, and none is sent on a day with none. See [Privacy](./PRIVACY.md).
- A Coverage gaps card: the companies whose roles may be missing or out of date.
- Data for agents: the roles as JSON-LD in `jobs.jsonld`, an open-data download in `jobs.json`, and WebMCP tools (listed in the [design system](./design-system/README.md)).

## Daily run

GitHub Actions runs the pipeline every day at 04:23 UTC.
A second try starts at 09:53 UTC, and cron-job.org starts a third at about 04:30 UTC.
Each start does nothing when an earlier one already finished green that day.

A run:

1. Pulls the history from Supabase.
2. Crawls the career pages.
3. Logs the coverage gaps.
4. Syncs the jobs back to Supabase.
5. Sends the job alerts.
6. Builds the dashboard.
7. Commits `docs/` to `main`.
8. Backs up the account tables and the job history.

The history lives in Supabase.
`data/jobs.db` is a scratch file, rebuilt on every run, git-ignored and never committed.
The daily commit holds `docs/` only.

If a step fails or the run is cancelled, a last step emails an alert from the maintainer's Gmail.
A Gmail outage is not reported that way: GitHub's failed-run notice is then the only signal.

To run it by hand: Actions tab, "daily-job-digest", Run workflow.

More in `CONTRIBUTING.md`:

- [The daily digest](./CONTRIBUTING.md#the-daily-digest): the schedule, the secrets and the guard.
- [Backups of the account tables and the job history](./CONTRIBUTING.md#backups-of-the-account-tables-and-the-job-history)
- [The history lives in Supabase](./CONTRIBUTING.md#the-history-lives-in-supabase)

## Career-page systems

Hosted job boards:

- Greenhouse, Lever, Ashby, Workable, SmartRecruiters
- Comeet: public pages and WordPress plugin pages
- JazzHR, BambooHR
- Workday, Oracle Fusion HCM and Phenom: jobs in Israel only
- Amazon (amazon.jobs)

The browser crawler reads:

- HiBob and Eightfold boards
- Any custom career page, from its JSON-LD or its job-looking links

A company seen for the first time is baselined silently, so adding companies never floods the job alerts.
To add one, see [Adding a company](./CONTRIBUTING.md#adding-a-company).

## Quickstart

```bash
python3 discover.py               # fills in data/companies.json (careers URL + ATS of each crawled company)
python3 sync_supabase.py --pull   # rebuild data/jobs.db from Supabase; needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
python3 run.py                    # crawl, diff into data/jobs.db
```

To try the crawl without Supabase, run `touch data/jobs.db` instead of the pull: it gives an empty history.
The sync refuses such a file: only a pulled one may be written back.

## Dashboard

`python3 build_dashboard.py` writes the dashboard to `docs/`.
The files in `docs/` are generated: edit `templates/`, never `docs/` (see [AGENTS.md](./AGENTS.md)).
GitHub Pages serves the site from `main`, `/docs`.

## Development

Requires Python 3.12+ and Node 26 (see `.nvmrc`). Python runs the pipeline; Node is dev tooling only (linters, git hooks, release gate).

```bash
nvm install && nvm use
npm ci
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

| Command | Purpose |
| --- | --- |
| `npm run lint` | ruff, ESLint, Stylelint, html-validate, markdownlint |
| `npm run typecheck` | mypy (strict) |
| `npm test` | Node release-gate tests and pytest |
| `npm run test:coverage` | Same, with the mandatory 100% Python coverage gate |

## Documentation

- [Contributing](./CONTRIBUTING.md)
- [Changelog](./CHANGELOG.md)
- [Security](./SECURITY.md)
- [Privacy](./PRIVACY.md)
- [Terms](./TERMS.md)
- [Support](./SUPPORT.md)
- [Design system](./design-system/README.md) (tokens, components, logo brief, WebMCP tools)

## License

See [LICENSE](./LICENSE).
