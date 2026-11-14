# Misra

[![CI](https://github.com/ravidorr/misra/actions/workflows/ci.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/ci.yml) [![Release](https://github.com/ravidorr/misra/actions/workflows/release.yml/badge.svg)](https://github.com/ravidorr/misra/actions/workflows/release.yml)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ravidorr/misra/pulls) [![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/ravidorr/misra/graphs/commit-activity) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Node.js](https://img.shields.io/badge/Node.js-26-339933?logo=node.js&logoColor=white)](https://nodejs.org/)

```bash
python3 discover.py        # data/seed_companies.csv -> data/companies.json (careers URL + ATS)
python3 run.py             # crawl, diff into data/jobs.db
python3 digest.py          # preview digest (data/digest.html)
python3 digest.py --commit # only after sending manually; send_email.py does this itself
python3 send_email.py      # send the digest (add --dry-run to preview)
```

Adapters: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Comeet (public hosted pages), Workday (the public job search, Israel only; slug `tenant/wdN/site`), JazzHR (the board page of `<slug>.applytojob.com`), BambooHR (the `/careers/list` JSON of `<slug>.bamboohr.com`), and a generic
Playwright crawler (`browser_crawl.py`, JSON-LD or job-looking links) for custom career pages. It also reads HiBob boards and
Eightfold career sites (the page's own search results, paged with "Next jobs"); pin an Eightfold site with its
`...careers?location=Israel` URL.
`data/overrides.json` pins a company to an ATS/slug or to a career page URL (`"ats": "browser"`).
Add `"israel_only": true` to an override to keep only jobs located in Israel; use it for large multinational boards.
`survey.py` is a dev tool that sniffs which ATS a custom career page uses.
A company seen for the first time is baselined silently, so adding companies never floods the digest.
Not covered yet: see status=careers_page_only / not_found in data/companies.json.

## Daily run (GitHub Actions)

`.github/workflows/daily.yml` runs at 04:30 UTC: crawl, email new jobs, commit `data/jobs.db` and the built dashboard in `docs/` straight to `main`.
Repo secrets: `GMAIL_USER`, `GMAIL_APP_PASSWORD` (Gmail app password), `DIGEST_TO` (optional recipient), `DEPLOY_KEY` (private half of a write deploy key; it must be the deploy-key bypass actor of the `protect-main` ruleset, see AGENTS.md).
The digest ends with the companies that were not crawled properly (failing, held after a sharp drop, or closed after a believed sharp drop). On a day with no new jobs it is only sent when a gap has not been emailed yet.
If a step fails or the run is cancelled, a last step emails a short alert listing the step outcomes with a link to the run (`send_alert.py`, standard library only). It uses the same Gmail account as the digest, so a Gmail outage is not reported that way; GitHub's failed-run notice is then the only signal.
Manual run: Actions tab, "daily-job-digest", Run workflow.

## Dashboard

Live page: <https://ravidorr.github.io/misra/>

`python3 build_dashboard.py` writes the dashboard to `docs/`: `index.html` plus `dashboard.css`, `dashboard.js` and `theme.js` (cached by the browser) and the roles in `jobs.json`, which the page fetches. It is a mobile-first daily feed of open jobs (light and dark), with filters and per-job "Not for me" saved in the browser. It is readable by agents too: the roles as schema.org JobPosting JSON-LD in `jobs.jsonld`, `data-*` hooks, and WebMCP tools (`search_jobs`, `list_new_jobs`, `get_job`, `discard_job`, `restore_job`, `list_coverage_gaps`).
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
- [Support](./SUPPORT.md)
- [Design system](./design-system/README.md) (tokens, components, logo brief, WebMCP tools)

## License

See [LICENSE](./LICENSE).
