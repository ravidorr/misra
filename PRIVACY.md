# Privacy

Last updated: 2026-11-15

## Data we collect

No personal data is collected from visitors who do not sign in. The pipeline reads publicly available job postings from company career pages and stores them in a SQLite database (`data/jobs.db`). The daily digest is emailed to `GMAIL_USER`, or to `DIGEST_TO` if that secret is set.

The repository is public, so `data/jobs.db` and the generated dashboard (the files in `docs/`, published on GitHub Pages and marked `noindex`) are publicly readable. They contain only public job postings. The dashboard saves your Discard choices in your own browser (`localStorage`); nothing is sent anywhere.

Signing in is optional and the dashboard works the same without it. If you sign in with Google, your email address is kept in our Supabase database (EU, Ireland), and the page keeps your sign-in in your browser. Our own tables keep the email only, and the page never shows your name or photo or saves them in the sign-in it keeps. The signed token the page keeps can hold them too until the project's access token hook is switched on, which the maintainer does before inviting users. Google also sends them to Supabase's sign-in service, which holds them with the account until it is deleted. We never store a CV. When you are signed in, the jobs you star or mark "Not for me" are saved to your account so they follow you to your other devices: for a star we keep the role's title, company, location and link (so you can still see it after the role closes), for "Not for me" only which role it was, and a "Not for me" choice is removed when its role closes. An account can keep a limited number of them. Signed out, they stay in your browser only. This notice is rewritten in full, with how to delete your account and when preferences are saved, in a later step.

Reporting a missing career site needs no sign-in. The company, the careers link and the reason you type are emailed to the maintainer through Resend (a mail service) and are not stored by us anywhere else. To stop spam, a short-lived counter is kept per day: a keyed hash of your network address (not the address, and not reversible), with how many reports it sent, and the number of reports emailed that day. Rows older than yesterday are deleted whenever the next report is handled.

## How data is used

Without signing in no personal data is collected, so none is used. If you sign in, your email address is used to sign you in and is never sold.  Job postings are used only to produce the digest and the dashboard.

## Third parties

No analytics or tracking. The pipeline makes network calls to public career pages and applicant-tracking systems it crawls, sends the digest through Gmail SMTP using credentials stored as GitHub Actions secrets, and hosts the dashboard on GitHub Pages. Site reports are sent through Resend. If you choose to sign in, Google (the sign-in) and Supabase (accounts and sign-in service, EU Ireland) process your data too.

## Contact

Questions: open a [GitHub issue](https://github.com/ravidorr/misra/issues). For security matters, see [SECURITY.md](./SECURITY.md).
