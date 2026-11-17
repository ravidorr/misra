# Privacy

Last updated: 2026-11-17

This is a plain statement of what the Misra dashboard does with data. It describes what the code and the setup do today. It is not legal advice. The [terms](./TERMS.md) are short and go with it.

## The short version

- Signing in is optional and voluntary. Without it the dashboard works the same, and nothing about you is kept by us, apart from the short-lived spam counter described below if you report a site.
- If you sign in with Google, we keep your email address, the jobs you star or mark "Not for me" (the dashboard also calls them discards), and your saved filters. If you also turn on job alerts, we keep their settings (see "What we store"). Nothing else.
- We never store a CV. The dashboard has no place to upload one.
- Only you, and the maintainer who runs the database, can see what your account holds.
- A daily backup copy of your account data is kept in a private repository for up to 30 days. If you delete your account, it leaves the backups within that time.
- You can see, correct and delete your data (see "Your rights"). You can delete your account yourself, at any time, with **Delete my account** on the dashboard. It is immediate and permanent.
- There are no cookies, no analytics and no advertising or tracking scripts.

## Who is responsible

Misra is a free, non-commercial project run by a private individual. The person responsible for the data in your account (the database controller, בעל השליטה במאגר) is **Raanan Avidor**. Contact: <raanan@avidor.org>. In this notice and in the terms, "we" and "the maintainer" both mean Raanan Avidor.

## Why we collect it, and whether you have to give it

You are not required to give us any data. Signing in is voluntary, and the dashboard works fully without it. If you choose to sign in with Google, your email address is used only to sign you in and to keep your own choices (stars, "Not for me" and filters) so they follow you to your other devices. If you turn on job alerts, your email address is also used to send you the job alerts you asked for, and for nothing else. Without signing in you cannot save your choices across devices, and you cannot get the email.

## What we store

If you do not sign in, we store nothing about you. The one exception is the short-lived spam counter described below, if you report a site or use the contact form. The page keeps your own choices ("Not for me" choices, stars, filters and theme) in your browser's local storage (`localStorage`) and sends none of it anywhere.

If you sign in with Google, these are stored in our Supabase database (EU, Ireland):

- Your **email address**, as Google gives it to us. It is used to sign you in and to tell your account apart. It is not sold or shared.
- Your **stars and "Not for me" choices**. For a star we keep the role's title, company, location and link, so you can still see it after the role closes. For "Not for me" we keep only which role it was, and the choice is removed when that role closes. An account can keep a limited number of them (2,000).
- Your **filters** (regions, levels, role types, search words, company, sort order and the show or hide options), so they follow you to your other devices. The first time you sign in on a browser, the page offers to copy that browser's own "Not for me" choices, stars and filters into your account.
- **Email settings** (the personalised job alerts, with their own consent log). The "Job alerts" button on the dashboard, for signed-in users, turns job alerts on and off. Turning it on stores the address it goes to (your Google email, or another one you type), the filters you copied for it, whether you want it daily or weekly, the time you turned it on, the time you confirmed the address from the link we email you, and the time you unsubscribed, plus the jobs already emailed to you, so none is sent twice. Turning it on sends one confirmation email to that address, and nothing else is sent until you confirm. We store only a one-way code made from the confirmation link (a SHA-256 hash), never the link itself. The unsubscribe link is not stored at all: it is made again for each email from your account ID (a random code that identifies your account in our database; it is not your name or email address) and a secret key that only the project holds, which signs the link (a keyed signature), so nobody can make one for another person. The link contains your account ID in plain text, so anyone who sees the link (for example if you forward the email) can see that ID, and use the link to unsubscribe you. Job alerts start only after you confirm. Unsubscribing, with the link in every email and no sign-in, stops them at once, deletes your filters and the list of emailed jobs and resets daily or weekly, and keeps the address and those three times as a record of your consent. Deleting your account removes all of it, including that record. The unsubscribe link carries its secret code (a token) in the address, so Supabase's request log holds it for a short time (it holds the account ID and can only unsubscribe).

**Backups.** Every day a copy of the account data above (all of the tables that hold your email address, stars, "Not for me" choices, filters and email settings, including the hash of the confirmation link) is saved to a private repository at GitHub (United States). Only the maintainer has access to it. The repository keeps its history for up to 30 days: older history is replaced by a single new copy, so what you deleted does not stay in it. Nothing from it is sent to anyone, and the daily job's public log shows only how many rows each table held.

The sign-in service also keeps its own record of your account. Google sends your name and photo to Supabase's sign-in service, and Supabase keeps them in its own internal account record (the `auth` tables, as a fixed part of how its Google sign-in works) until the account is deleted. Our own tables never copy them, the dashboard never shows them, and the page does not save them in the sign-in it keeps in your browser. The signed sign-in pass (token) your browser holds would also carry them; a database function, switched on in the Supabase dashboard, empties that part of the token.

Technical records, none of them personal to an account:

- **Reporting a missing career site** needs no sign-in. The company, careers link and reason you type are emailed to the maintainer and not stored by us anywhere else. To prevent spam, we store a daily non-reversible hash of your network address that is deleted in the next daily cleanup. With it we keep how many reports came from that hash that day, and how many reports were emailed that day in all. The form is the "Add a company" button on the dashboard's coverage card.
- **Contact form** needs no sign-in. Your message, and your name and email address if you give them, are emailed to the maintainer's mailbox through Resend and not stored by us anywhere else. To prevent spam, we store a daily non-reversible hash of your network address that is deleted in the next daily cleanup.
- **AI assistant connection** needs no sign-in. We do not log your search words, the jobs your assistant reads, or your network address. To enforce daily rate limits, our database keeps a short-lived counter per day: a keyed, non-reversible hash of your network address (or /64 IPv6 prefix, made with a secret key that changes every day) and the number of calls made that day. Counters from earlier days are deleted in the next daily cleanup.
- **Limits on confirmation emails.** So that nobody can use the dashboard to flood an inbox, the database keeps short-lived daily counters: one-way codes (keyed hashes, made with a secret that changes every day; never your account ID or the address itself) of how many confirmation emails you and an address were sent, plus the number sent in all that day. The same daily cleanup deletes the rows of every earlier day.
- **Provider logs.** Supabase's own platform logs record each request to its services, including the small programs (Edge Functions) behind the report form, account deletion, job alerts, the confirmation and unsubscribe links, and the AI assistant connection: the caller's IP address, approximate location, user agent, and the request path. They are kept for a short period set by Supabase. Our tables never store your address, but these logs are Supabase's and we do not control them. The same kind of access log exists at GitHub, which serves the dashboard.
- **The jobs data is public.** Every visitor's browser loads the open roles from Supabase (so Supabase and GitHub see the request, as above). The job listings, job history, and full job descriptions (which may include business contact details published by the employer, such as a recruiter's name or work email) are stored in our Supabase database in the EU and contain only public job postings.

## What we never store

- A CV, or any document you write or upload. The dashboard has no document upload.
- A password. There is none: Google does the sign-in.
- Your Google name or photo in our own tables.
- Your IP address in our own tables (see the provider logs above for what the providers keep).

## Who can see it

Only you (signed in, through rules in the database that give each account its own rows only), and the maintainer, who has access to the database and to the private backup repository. The providers below process data as described, to run the service.

## Providers and where they are

- **Supabase** (EU, Ireland): the database, the sign-in service and the Edge Functions. Holds everything in "What we store".
- **Google** (United States): the sign-in. Google knows that you signed in to this dashboard, as with any "Sign in with Google".
- **GitHub** (United States): hosts the repository and the dashboard (GitHub Pages) and runs the daily crawl (GitHub Actions). The crawl reads public career pages and does not handle your account data. GitHub also hosts a separate private repository that holds the daily backup copy of the account tables (see "What we store"); only the maintainer has access to it.
- **Resend** (Ireland): sends the site-report and contact-form emails to the maintainer and, if you turn on job alerts, processes your email address and the matched job links to deliver your confirmation and job alert emails.
- **Gmail** (Google, United States): receives failure alerts about the daily run, as well as the emails sent from the site-report and contact forms to the maintainer's own mailbox.

By signing in you accept that your account data is processed on these providers' infrastructure, in the EU and in the United States: your account is stored at Supabase in the EU, a daily backup copy is kept at GitHub in the United States, and Google, in the United States, handles the sign-in. Supabase, Resend, and GitHub each include their standard data processing agreement in their terms of service, and it applies to our use of them.

The page loads no fonts, scripts or images from any other service, and its security policy only allows connections to its own files and to the Supabase project.

## Your rights

Under the Israeli Privacy Protection Law, you have the right to see the data we hold about you (Section 13) and to ask us to correct or delete data that is inaccurate, incomplete, unclear, or out of date (Section 14). Beyond what the law requires, you can also delete your entire account and all data we hold about you at any time, for any reason (see "How to delete your data" below). When you are signed in, the dashboard shows your own stars, "Not for me" choices and filters. To see everything we hold about you, to have something corrected, or to have your data deleted by hand, write to <raanan@avidor.org>. We answer within 30 days, and normally much sooner. Deleting your account yourself is described below.

## Cookies and tracking

The page sets no cookies and has no analytics or tracking. It uses your browser's local storage (`localStorage`) for: your signed-out "Not for me" choices, stars and filters, the theme, your sign-in session (`misra-auth`, while signed in), and notes about changes not yet saved to your account (`misra-pending-choices-v1`, `misra-pending-filters-v1`, `misra-imported-v1`). Nothing in it identifies you to anyone else.

## How long we keep it

Account data is kept until you delete your account. There is no automatic expiry. The consent record of an unsubscribed email also stays until you delete your account. The daily spam-guard counters are removed as described above. Provider logs are kept by the providers for the periods they set. Supabase's free plan keeps no backups for us. Our own daily backup copy of the account tables, in the private GitHub repository, is kept for up to 30 days: its history is replaced by a single new copy when it is 28 days old, so a deleted account leaves our backups within 30 days. GitHub may keep the replaced copies for a short time before it erases them; that is GitHub's own handling and we do not control it.

Emails sent through the contact form (and the site-report form) are not stored in our database. They remain in the maintainer's mailbox until the conversation is resolved or you ask us to delete them, and in Resend's delivery logs for the short retention period set by Resend.

## How to delete your data

Sign in, choose **Delete my account**, read the notice and confirm. The service checks that it is you, deletes your account at Supabase and, with it, every row that belongs to it: your email, stars, "Not for me" choices, filters and settings. It happens at once and cannot be undone. A copy remains in the daily backup until the backup is replaced, within 30 days; it is never put back into the live service for you. The page then signs you out and clears what it kept for the account in your browser. Choices saved in your browser while you were signed out are not part of the account and stay until you clear your browser's data. If you sign in again later you get a new, empty account. Google keeps your Google account: only Google can close it, and you can remove this dashboard's access in your Google account settings.

If the button does not work for you, write to <raanan@avidor.org> and the maintainer will delete the account by hand.

## Contact

Raanan Avidor, <raanan@avidor.org>, for any question, a request to see, correct or delete your data, or a complaint. The Contact page <https://ravidorr.github.io/misra/contact.html> lists every way to reach us. Its form emails your message, and your name and email address if you give them, to the maintainer's mailbox through Resend. We do not store them. To prevent spam, we store a daily non-reversible hash of your network address that is deleted in the next daily cleanup. General questions can also go in a [GitHub issue](https://github.com/ravidorr/misra/issues); do not put personal details in it. To report a security problem, use the private route in [the security note](./SECURITY.md).
