# Security Policy

Last updated: 2026-11-16

## Supported versions

| Version | Supported |
| --- | --- |
| Latest `main` | Yes |
| Older | No |

## Reporting a vulnerability

Do not open a public issue. Report vulnerabilities privately through GitHub private vulnerability reporting (Security tab, then Report a vulnerability). If that is unavailable, email <raanan@avidor.org>. Expect an acknowledgement within 3 business days.

## How Misra protects its data

Written 2026-11-15 by the maintainer, Raanan Avidor. Next full review due by 2027-11-15. This is the short security note
that the Israeli Data Security Regulations (2017) expect for a database at the "basic" level (a computerised database
of email addresses of people outside the owner's own circle). It is public on purpose: the repository is public and so
are the rules that protect the data.

### What the database holds

- The email address of each person who signs in with Google, their stars, their "Not for me" choices and their saved
  filters. For job alerts: the address they go to, the filters copied for them, daily or weekly, the jobs already
  emailed, and a consent log (when they were turned on, confirmed and unsubscribed).
- Never a CV, a phone number, a postal address or a password. Google's name and photo sit only in Supabase's own
  sign-in record until the account is deleted. The jobs data is public job postings.
- Where it lives: Supabase (EU, Ireland) for the database, sign-in and Edge Functions; Resend (Ireland) for email;
  Google for sign-in; GitHub for the repository, the dashboard and the daily job, and for a private repository
  (`ravidorr/misra-backups`) that holds the daily backup copy of the account tables; cron-job.org, which starts the daily job and holds a GitHub
  token for it (see below), but no personal data. One maintainer runs all of them.

### Controls in place

- **Access to rows:** row-level security on every table. A signed-in person can read and change only their own rows,
  and the public (anon) key can read open jobs only. The policy tests are in `supabase/tests/`.
- **Secrets:** the service-role key lives only in Supabase's function settings and in GitHub Actions secrets, never in
  the browser or the repository. Other secrets by name: `RESEND_API_KEY` (limited to sending from `send.avidor.org`),
  `REPORT_TO_EMAIL`, `IP_HASH_SECRET`, `UNSUBSCRIBE_SECRET` (the key that signs the unsubscribe links), the GitHub deploy key and `BACKUP_DEPLOY_KEY` (a write deploy key of the private
  backup repository only, used by the backup step of the daily job and nothing else). Values are typed into the
  providers' own pages.
- **Backups:** every daily run copies the account tables and the job history (public data, without the text of the
  postings), as JSON files, to the private repository
  `ravidorr/misra-backups` (only the maintainer has access). The export runs with the service-role key in its own step,
  writes outside the checkout, never prints rows (the public log shows counts only) and uploads no artifact. The
  repository's history is replaced by one new commit when it is 28 days old, so a deleted account leaves the backups
  within 30 days. The restore script, `restore_accounts.py`, and the rehearsal are in `CONTRIBUTING.md`. The key that
  pushes the backup (`BACKUP_DEPLOY_KEY`) is a GitHub deploy key, which belongs to one repository: it is scoped to
  `ravidorr/misra-backups` only and reaches no other repository. A deploy key has no expiry date, so it is rotated by hand
  at the yearly review (see below).
- **No stray copies of the backup:** if the private backup repository is ever cloned to a laptop, to inspect it or to test a
  restore, the local clone (and any restore script made from it) is deleted immediately afterwards, so that plaintext email
  addresses never sit on a disk outside the 30-day rotation.
- **Accounts:** two-factor sign-in is on for Supabase, GitHub, Resend and Google. Users sign in with Google only: the
  email-and-password provider is switched off. A database hook empties the personal part of the sign-in token.
- **Policy tests:** the SQL policy tests of `supabase/tests` run in CI on every pull request and push to `main`, against a
  plain Postgres 17 database with a stub of Supabase's roles and `auth` schema. They are the real tests of the
  row-level security; a stub is not a Supabase project, so the live checks in `supabase/README.md` still apply.
- **The daily-job token:** cron-job.org holds a GitHub token limited to this repository with only "Actions: read and
  write", used to start the daily job. That permission can also cancel and disable this repository's workflows, but it
  cannot read secrets or change code. The token expires within a year and is stored only at cron-job.org.
- **Abuse limits:** the report form has a honeypot, a per-visitor daily limit and a daily cap; the email functions have
  per-user, per-address and daily limits.
- **Deletion:** a person can delete their account in one step, and every row tied to it is removed at once.
- **Transport:** all traffic is HTTPS, and the page's security policy only allows connections to its own files and to
  the Supabase project.

### Known gaps (accepted for now)

- Supabase's free plan keeps no backups for us. The daily export above is the backup: a restore was rehearsed on
  2026-11-15 against a local Postgres, not yet against a real Supabase project. The export holds the account tables of
  `public`, not Supabase's own sign-in records, so after a restore into a new project people sign in with Google again
  (the restore can recreate the bare sign-in rows). GitHub may keep the replaced history for a short time after it is
  replaced.
- Two low-risk Dependabot alerts in development tools (not shipped to users) were open on 2026-11-15.
- The cron-job.org token could be used to cancel or disable the daily job. This risk is accepted. A disabled workflow
  raises no alert and shows only as a missing daily commit of `docs/`.
- One maintainer: no second person for access or recovery.

### Yearly review (by 2027-11-15, then every year)

1. Read Supabase's auth and API logs for odd sign-ins or request patterns.
2. List who has access to the Supabase organisation, the GitHub repository (collaborators, deploy keys, tokens), the
   private backup repository `ravidorr/misra-backups` (collaborators and its one deploy key), the Resend team and the cron-job.org
   account (and the expiry date of its GitHub token, renewing it before it expires), and remove anyone or anything not
   needed.
3. Confirm two-factor sign-in is still on for Supabase, GitHub, Resend and Google (and for cron-job.org, if it offers it).
4. Rotate the service-role key and the Resend key, and update the secrets. Rotate `BACKUP_DEPLOY_KEY` (a deploy key has
   no expiry date): make a new key, add it to the deploy keys of `ravidorr/misra-backups`, update the Actions secret,
   run the workflow once, then delete the old key in that repository's deploy keys. The first rotation is due by
   2027-11-15.
5. Check Dependabot and CodeQL alerts and update dependencies.
6. Re-read `PRIVACY.md` and `TERMS.md` against what the product really does.
7. Check that the last backup is recent and rehearse a restore again (`CONTRIBUTING.md`, "Backups of the account tables").

Reviews done: none yet.

### If there is a breach

1. **Contain it:** rotate the service-role key, `BACKUP_DEPLOY_KEY`, `UNSUBSCRIBE_SECRET` (the key that signs the unsubscribe links) and any
   exposed secret, disable the affected Edge Function or sign-in provider, and fix the hole. Changing `UNSUBSCRIBE_SECRET`
   stops the unsubscribe links in emails already sent; people can still turn the email off on the dashboard or reply
   "Remove".
2. **Find out what was exposed:** which tables, which people, since when (use the provider logs).
3. **Tell the affected people** by a short, plain email: what was exposed (their email address and saved choices, for
   example), what was done, and how to delete their account. No report to the regulator is required at this level, but
   telling the people is the right thing to do.
4. **Write it down:** the date, what happened and what was changed. If the database ever grows past the basic level
   (sensitive data, or a very large number of people), ask a lawyer again whether a report to the authority is required.
