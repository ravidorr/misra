# Misra

[![CI](https://github.com/ravidorr/misra/actions/workflows/ci.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/ci.yml) [![Release](https://github.com/ravidorr/misra/actions/workflows/release.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/release.yml)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ravidorr/misra/pulls) [![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/ravidorr/misra/graphs/commit-activity) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Node.js](https://img.shields.io/badge/Node.js-26-339933?logo=node.js&logoColor=white)](https://nodejs.org/)

```bash
python3 discover.py        # data/seed_companies.csv -> data/companies.json (careers URL + ATS)
python3 run.py             # crawl, diff into data/jobs.db
python3 digest.py          # preview the maintainer digest (data/digest.html)
python3 digest.py --commit # only after sending by hand; send_email.py does this itself
python3 send_email.py      # send the maintainer digest (add --dry-run to preview)
```

Adapters: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Comeet (public hosted pages), Comeet plugin pages (a WordPress career page whose job links `/co/<place>/<uid>/<title>` are drawn on the server; the slug is the page address), Workday (the public job search, Israel only; slug `tenant/wdN/site`), JazzHR (the board page of `<slug>.applytojob.com`), BambooHR (the `/careers/list` JSON of `<slug>.bamboohr.com`), Oracle Fusion HCM (the public requisition search, Israel only; slug `host/site`), Amazon (the search JSON of amazon.jobs for one country; slug `ISR`), Phenom career sites (the search widget filtered to Israel; slug `host/country/lang`), and a generic
Playwright crawler (`browser_crawl.py`, JSON-LD or job-looking links) for custom career pages. It also reads HiBob boards and
Eightfold career sites (the page's own search results, paged with "Next jobs"); pin an Eightfold site with its
`...careers?location=Israel` URL.
`data/overrides.json` pins a company to an ATS/slug or to a career page URL (`"ats": "browser"`).
Add `"israel_only": true` to an override to keep only jobs located in Israel; use it for large multinational boards.
`survey.py` is a dev tool that sniffs which ATS a custom career page uses.
A company seen for the first time is baselined silently, so adding companies never floods the maintainer digest or job alerts.
The dashboard lists roles from every company whose career page the daily crawl reads, with the live count in its footer; the rest are listed on its Coverage gaps card.

## Daily run (GitHub Actions)

`.github/workflows/daily.yml` runs at 04:23 UTC, with a second try at 09:53 UTC that does nothing when the first one already finished green that day (CONTRIBUTING.md, "The daily digest"): crawl, email the maintainer digest, sync the jobs to Supabase, send the job alerts, build the dashboard, commit `data/jobs.db` and `docs/` straight to `main`, then back up the account tables.
Repo secrets: `GMAIL_USER`, `GMAIL_APP_PASSWORD` (Gmail app password), `DIGEST_TO` (optional recipient), `DEPLOY_KEY` (private half of a write deploy key; it must be the deploy-key bypass actor of the `protect-main` ruleset, see AGENTS.md). The service and backup secrets are in CONTRIBUTING.md ("The daily digest" and "Backups of the account tables").
The maintainer digest holds the new jobs only and is not sent on a day with none. Crawl problems (a company failing, held after a sharp drop, or closed after a believed sharp drop) are not in it: they are on the dashboard's coverage gap card, and `send_email.py` prints one line of counts and company names to the job log ("coverage gaps: ...").
If a step fails or the run is cancelled, a last step emails a short alert listing the step outcomes with a link to the run (`send_alert.py`, standard library only). It uses the same Gmail account as the maintainer digest, so a Gmail outage is not reported that way; GitHub's failed-run notice is then the only signal.
Manual run: Actions tab, "daily-job-digest", Run workflow.

## Dashboard

Live page: <https://ravidorr.github.io/misra/>

`python3 build_dashboard.py` writes the dashboard to `docs/`: `index.html`; `dashboard.css`, `dashboard.js`, `auth.js`, `report.js`, `digest.js`, `supabase.js` (the vendored supabase-js of `vendor/`) and `theme.js`, cached by the browser; `email.html` and `email.js`, the page that job alert emails link to (confirm, unsubscribe); and `jobs.json`, a public open-data download of the roles. The page does not read `jobs.json`: it loads the open roles from Supabase over PostgREST with the public anon key (kept in `build_dashboard.py`, which injects it and the project host into the page script and its `connect-src`), and shows an error card when the service is unreachable.
The page is a mobile-first daily feed of open roles (light and dark). It has filters, a Star and a "Not for me" button on each role (a role is one or the other) and a "Starred" list, all saved in the browser. An optional "Sign in" with Google (`auth.js`; the dashboard works the same without it) saves stars, discards and filters to the account instead, so they follow the user to other devices; the first sign-in offers to bring this browser's choices over. It is readable by agents too: the roles as schema.org JobPosting JSON-LD in `jobs.jsonld`, `data-*` hooks, and WebMCP tools (`search_jobs`, `list_filters`, `list_new_jobs`, `get_job`, `star_job`, `unstar_job`, `list_starred_jobs`, `discard_job`, `restore_job`, `get_sync_status`, `import_browser_choices`, `list_coverage_gaps`). The count of covered companies is computed at build time from `data/companies.json` (the companies with status `ok`) and read from `data-companies-covered` on the footer sentence, or `covered` in `jobs.json`.
The daily workflow rebuilds and commits it. Serve it with GitHub Pages: Settings, Pages, Deploy from a branch, `main`, `/docs`.

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
