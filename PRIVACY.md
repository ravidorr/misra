# Privacy

Last updated: 2026-11-15

This is a plain statement of what the Misra dashboard does with data. It describes what the code and the setup do today. It is not legal advice.

## The short version

- Signing in is optional. Without it the dashboard works the same, and nothing about you is kept by us.
- If you sign in with Google, we keep your email address, the jobs you star or mark "Not for me", and your saved filters. Nothing else.
- We never store a CV. The dashboard has no place to upload one.
- Only you, and the maintainer who runs the database, can see what your account holds.
- You can delete your account yourself, at any time, with **Delete my account** on the dashboard. It is immediate and permanent.
- There are no cookies, no analytics and no advertising or tracking scripts.

## What we store

If you do not sign in, we store nothing about you. The page keeps your own choices (discards, stars, filters and theme) in your browser (`localStorage`) and sends none of it anywhere.

If you sign in with Google, these are stored in our Supabase database (EU, Ireland):

- Your **email address**, as Google gives it to us. It is used to sign you in and to tell your account apart. It is not sold or shared.
- Your **stars and "Not for me" choices**. For a star we keep the role's title, company, location and link, so you can still see it after the role closes. For "Not for me" we keep only which role it was, and the choice is removed when that role closes. An account can keep a limited number of them (2,000).
- Your **filters** (regions, places, levels, role types, search words, company, sort order and the show or hide options), so they follow you to your other devices. The first time you sign in on a browser, the page offers to copy that browser's own discards, stars and filters into your account.
- **Email settings** have a place in the database for a later feature (the personalised digest). That feature does not exist yet and nothing is stored there.

The sign-in service also keeps its own record of your account. Google sends your name and photo to Supabase's sign-in service, and Supabase keeps them in its own account record (in its `auth` tables) until the account is deleted. Our own tables never copy them, the dashboard never shows them, and the page does not save them in the sign-in it keeps in your browser. The signed token your browser holds would also carry them; a database function, switched on in the Supabase dashboard, empties that part of the token.

Technical records, none of them personal to an account:

- **Reporting a missing career site** needs no sign-in. The company, careers link and reason you type are emailed to the maintainer and not stored by us anywhere else. To stop spam, our database keeps a short-lived counter per day: a keyed hash of your network address (not the address, and not reversible, with a salt that changes every day) and how many reports it sent, plus the number of reports emailed that day. Rows older than yesterday are deleted whenever the next report is handled. The form is the "Report a site" button on the dashboard's coverage card.
- **Provider logs.** Supabase's own platform logs record each request to its services, including the Edge Functions `report-site` and `delete-account`: the caller's IP address, approximate location, user agent and the request path. They are kept for a short period set by Supabase. Our tables never store your address, but these logs are Supabase's and we do not control them. The same kind of access log exists at GitHub, which serves the dashboard.
- **The jobs data is public.** Every visitor's browser loads the open roles from Supabase (so Supabase and GitHub see the request, as above). The jobs, the history in `data/jobs.db` and the generated dashboard files are in a public repository, and contain only public job postings.

## What we never store

- A CV, or any document you write or upload. The dashboard has no document upload (the "Import discards" file is read inside your browser and goes nowhere).
- A password. There is none: Google does the sign-in.
- Your Google name or photo in our own tables.
- Your IP address in our own tables (see the provider logs above for what the providers keep).

## Who can see it

Only you (signed in, through rules in the database that give each account its own rows only), and the maintainer, who has access to the database. The providers below process data as described, to run the service.

## Providers and where they are

- **Supabase** (EU, Ireland): the database, the sign-in service and the Edge Functions. Holds everything in "What we store".
- **Google**: the sign-in. Google knows that you signed in to this dashboard, as with any "Sign in with Google".
- **GitHub**: hosts the repository and the dashboard (GitHub Pages) and runs the daily crawl (GitHub Actions). The crawl reads public career pages and does not handle your account data.
- **Resend** (Ireland): sends the site-report email to the maintainer. No account data is sent to Resend: the personalised digest does not exist yet.
- **Gmail (Google SMTP)**: the maintainer's own daily digest of public new roles, and failure alerts, go to the maintainer's own address. They hold public job data only, no account data.

The page loads no fonts, scripts or images from any other service, and its security policy only allows connections to its own files and to the Supabase project.

## Cookies and tracking

The page sets no cookies and has no analytics or tracking. It uses your browser's `localStorage` for: your signed-out discards, stars and filters, the theme, your sign-in session (`misra-auth`, while signed in), and notes about changes not yet saved to your account (`misra-pending-choices-v1`, `misra-pending-filters-v1`, `misra-imported-v1`). Nothing in it identifies you to anyone else.

## How long we keep it

Account data is kept until you delete your account. There is no automatic expiry. The daily spam-guard counters are removed as described above. Provider logs are kept by the providers for the periods they set. Supabase may keep database backups for a period it sets, and a deleted account can remain in a backup until that backup expires.

## How to delete your data

Sign in, choose **Delete my account**, read the notice and confirm. The service checks that it is you, deletes your account at Supabase and, with it, every row that belongs to it: your email, stars, discards, filters and settings. It happens at once and cannot be undone. The page then signs you out and clears what it kept for the account in your browser. Choices saved in your browser while you were signed out are not part of the account and stay until you clear your browser's data. If you sign in again later you get a new, empty account. Google keeps your Google account: only Google can close it, and you can remove this dashboard's access in your Google account settings.

If the button does not work for you, ask us (below) and the maintainer will delete the account by hand.

## Contact

Questions: open a [GitHub issue](https://github.com/ravidorr/misra/issues) and do not put personal details in it. A deletion request that has to name your account, and anything else private, goes through the private route in [SECURITY.md](./SECURITY.md), not a public issue.
