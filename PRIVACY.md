# Privacy

Last updated: 2026-11-14

## Data we collect

No personal data is collected. The pipeline reads publicly available job postings from company career pages and stores them in a SQLite database (`data/jobs.db`). The daily digest is emailed to `GMAIL_USER`, or to `DIGEST_TO` if that secret is set.

The repository is public, so `data/jobs.db` and the generated dashboard (the files in `docs/`, published on GitHub Pages and marked `noindex`) are publicly readable. They contain only public job postings. The dashboard saves your Discard choices in your own browser (`localStorage`); nothing is sent anywhere.

## How data is used

No personal data is collected, so none is used or sold. Job postings are used only to produce the digest and the dashboard.

## Third parties

No analytics or tracking. The pipeline makes network calls to public career pages and applicant-tracking systems it crawls, sends the digest through Gmail SMTP using credentials stored as GitHub Actions secrets, and hosts the dashboard on GitHub Pages.

## Contact

Questions: open a [GitHub issue](https://github.com/ravidorr/misra/issues). For security matters, see [SECURITY.md](./SECURITY.md).
