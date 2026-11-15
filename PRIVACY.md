# Privacy

Last updated: 2026-11-15

This is a plain statement of what the Misra dashboard does with data. It describes what the code and the setup do today. It is not legal advice. The [terms](./TERMS.md) are short and go with it.

## The short version

- Signing in is optional and voluntary. Without it the dashboard works the same, and nothing about you is kept by us.
- If you sign in with Google, we keep your email address, the jobs you star or mark "Not for me", and your saved filters. Nothing else.
- We never store a CV. The dashboard has no place to upload one.
- Only you, and the maintainer who runs the database, can see what your account holds.
- You can see, correct and delete your data (see "Your rights"). You can delete your account yourself, at any time, with **Delete my account** on the dashboard. It is immediate and permanent.
- There are no cookies, no analytics and no advertising or tracking scripts.

## Who is responsible

Misra is a free, non-commercial project run by a private individual. The person responsible for the data in your account (the database controller, בעל השליטה במאגר) is **Raanan Avidor**. Contact: <raanan@avidor.org>.

## Why we collect it, and whether you have to give it

You are not required to give us any data. Signing in is voluntary, and the dashboard works fully without it. If you choose to sign in with Google, your email address is used only to sign you in and to keep your own choices (stars, "Not for me" and filters) so they follow you to your other devices. The daily email is planned and not live. When it exists, your email address will also be used to send you the email you ask for, and for nothing else. Without signing in you cannot save your choices across devices, and you cannot get the email.

## What we store

If you do not sign in, we store nothing about you. The page keeps your own choices (discards, stars, filters and theme) in your browser (`localStorage`) and sends none of it anywhere.

If you sign in with Google, these are stored in our Supabase database (EU, Ireland):

- Your **email address**, as Google gives it to us. It is used to sign you in and to tell your account apart. It is not sold or shared.
- Your **stars and "Not for me" choices**. For a star we keep the role's title, company, location and link, so you can still see it after the role closes. For "Not for me" we keep only which role it was, and the choice is removed when that role closes. An account can keep a limited number of them (2,000).
- Your **filters** (regions, places, levels, role types, search words, company, sort order and the show or hide options), so they follow you to your other devices. The first time you sign in on a browser, the page offers to copy that browser's own discards, stars and filters into your account.
- **Email settings** (the personalised digest, with its own consent log). The settings are built into the dashboard (a "Daily email" button for signed-in users) but switched off and hidden, and no digest is sent yet, so no real subscription exists. When it is switched on, turning the email on stores the address it goes to (your Google email, or another one you type), the filters you copied for it, daily or weekly, the time you turned it on, the time you confirmed the address from the link we email you, and the time you unsubscribed, plus the jobs already emailed to you. Turning it on sends one confirmation email to that address, and nothing else is sent until you confirm. Only the SHA-256 hash of the confirmation and unsubscribe links is stored, never the links. The email starts only after you confirm. Unsubscribing, with the link in every email and no sign-in, stops it at once, deletes your filters and the list of emailed jobs and resets daily or weekly, and keeps the address and those three times as a record of your consent. Deleting your account removes all of it, including that record. The unsubscribe link carries its token in the address, so Supabase's request log holds it for a short time (it can only unsubscribe).

The sign-in service also keeps its own record of your account. Google sends your name and photo to Supabase's sign-in service, and Supabase keeps them in its own internal account record (in its `auth` tables, as a fixed part of how its Google sign-in works) until the account is deleted. Our own tables never copy them, the dashboard never shows them, and the page does not save them in the sign-in it keeps in your browser. The signed token your browser holds would also carry them; a database function, switched on in the Supabase dashboard, empties that part of the token.

Technical records, none of them personal to an account:

- **Reporting a missing career site** needs no sign-in. The company, careers link and reason you type are emailed to the maintainer and not stored by us anywhere else. To stop spam, our database keeps a short-lived counter per day: a keyed hash of your network address (not the address, and not reversible, with a salt that changes every day) and how many reports it sent, plus the number of reports emailed that day. Rows older than yesterday are deleted whenever the next report is handled. The form is the "Report a site" button on the dashboard's coverage card.
- **Limits on confirmation emails** (only once the daily email is switched on). So that nobody can use the dashboard to flood an inbox, the database keeps short-lived daily counters: keyed hashes (never your account id or the address itself, with a salt that changes every day) of how many confirmation emails you and an address were sent, plus the number sent in all that day. Rows older than yesterday are deleted whenever the next one is handled.
- **Provider logs.** Supabase's own platform logs record each request to its services, including the Edge Functions `report-site`, `delete-account` and `email-settings`: the caller's IP address, approximate location, user agent and the request path. They are kept for a short period set by Supabase. Our tables never store your address, but these logs are Supabase's and we do not control them. The same kind of access log exists at GitHub, which serves the dashboard.
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
- **Google** (United States): the sign-in. Google knows that you signed in to this dashboard, as with any "Sign in with Google".
- **GitHub** (United States): hosts the repository and the dashboard (GitHub Pages) and runs the daily crawl (GitHub Actions). The crawl reads public career pages and does not handle your account data.
- **Resend** (Ireland): sends the site-report email to the maintainer. When the daily email is switched on it also sends the confirmation email, and later the digest, to the address you give it; until then no account data is sent to Resend today.
- **Gmail (Google SMTP)**: the maintainer's own daily digest of public new roles, and failure alerts, go to the maintainer's own address. They hold public job data only, no account data.

By signing in you accept that your account data is processed on these providers' infrastructure, in the EU and in the United States: your account is stored at Supabase in the EU, and Google, in the United States, handles the sign-in. Supabase and Resend each include their standard data processing agreement in their terms of service, and it applies to our use of them.

The page loads no fonts, scripts or images from any other service, and its security policy only allows connections to its own files and to the Supabase project.

## Your rights

Under the Israeli Privacy Protection Law, you have the right to see the data we hold about you (Section 13) and to ask us to correct or delete data that is inaccurate, incomplete, unclear, or out of date (Section 14). Beyond what the law requires, you can also delete your entire account and all data we hold about you at any time, for any reason (see "How to delete your data" below). When you are signed in, the dashboard shows your own stars, "Not for me" choices and filters. To see everything we hold about you, to have something corrected, or to have your data deleted by hand, write to <raanan@avidor.org>. We answer within 30 days, and normally much sooner. Deleting your account yourself is described below.

## Cookies and tracking

The page sets no cookies and has no analytics or tracking. It uses your browser's `localStorage` for: your signed-out discards, stars and filters, the theme, your sign-in session (`misra-auth`, while signed in), and notes about changes not yet saved to your account (`misra-pending-choices-v1`, `misra-pending-filters-v1`, `misra-imported-v1`). Nothing in it identifies you to anyone else.

## How long we keep it

Account data is kept until you delete your account. There is no automatic expiry. The consent record of an unsubscribed email also stays until you delete your account. The daily spam-guard counters are removed as described above. Provider logs are kept by the providers for the periods they set. Supabase may keep database backups for a period it sets, and a deleted account can remain in a backup until that backup expires.

## How to delete your data

Sign in, choose **Delete my account**, read the notice and confirm. The service checks that it is you, deletes your account at Supabase and, with it, every row that belongs to it: your email, stars, discards, filters and settings. It happens at once and cannot be undone. The page then signs you out and clears what it kept for the account in your browser. Choices saved in your browser while you were signed out are not part of the account and stay until you clear your browser's data. If you sign in again later you get a new, empty account. Google keeps your Google account: only Google can close it, and you can remove this dashboard's access in your Google account settings.

If the button does not work for you, write to <raanan@avidor.org> and the maintainer will delete the account by hand.

## Contact

Raanan Avidor, <raanan@avidor.org>, for any question, a request to see, correct or delete your data, or a complaint. General questions can also go in a [GitHub issue](https://github.com/ravidorr/misra/issues); do not put personal details in it. To report a security problem, use the private route in [SECURITY.md](./SECURITY.md).
