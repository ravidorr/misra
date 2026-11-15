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

## Reported sites

Visitors report a missing company with "Report a site" on the dashboard's coverage card (PLAN.md step 5.2). Each report reaches the maintainer by email (company, careers link and why it matters). Nothing is stored about it, so the email is the record: act on it or answer it, then do one of these in a PR:

1. **Accept it for the crawl.** Check that the link is the company's own careers page and that it lists roles in Israel, then add the company to `data/seed_companies.csv` (`name,domain`). Run `python3 discover.py` (it rewrites `data/companies.json` with each company's careers URL and job feed) and commit the result; the daily crawl then reads the company. If `discover.py` finds no feed for it, the next step applies.
2. **Keep it on the gap card, with a reason.** Add a row to `data/unresolved_candidates.csv` (`name,domain,segment,reason,note`). `reason` must be one of the keys of `REASONS` in `build_dashboard.py` (for example `no_feed_found` or `needs_reader`) and `note` says in a few words why we cannot read the site; the dashboard shows both on the coverage gap card.
3. **Decline it** when the link is not a careers page, the company has no roles in Israel, or the report looks like spam. Nothing needs to be recorded.

Rows in `data/seed_companies.csv` and `data/unresolved_candidates.csv` are public, so never copy anything from a report beyond the company and its public site.

## Release policy

When a PR or push to `main` changes `package.json`, `pyproject.toml`, or a Python module listed in `RELEASE_TRIGGER_PATHS` (`ci.yml`, `.husky/pre-push`), CI runs the release gate: the package version must increase (SemVer) and `CHANGELOG.md` must have a non-empty section for that version. PRs that only touch docs, tests, or workflows do not need a bump. Tagged releases (`vX.Y.Z`) must match `package.json` and have non-empty notes extracted from the changelog.
