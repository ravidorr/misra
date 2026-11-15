# Supabase

The jobs backend of the dashboard. `supabase/migrations/` holds plain SQL, applied in file-name order. Each file name
starts with a UTC timestamp (`YYYYMMDDHHMMSS_name.sql`), the format of the Supabase CLI.

## Apply the migrations

Project region: EU (Ireland). Either way works on a fresh project; do it once, and again whenever a new file appears.

- Supabase CLI: `supabase link --project-ref <ref>`, then `supabase db push`.
- SQL editor: open each file of `supabase/migrations/` in order, paste it into the SQL editor of the project, run it.

Check: `select count(*) from public.jobs;` returns 0 before the first sync. With the anon key,
`GET <project url>/rest/v1/jobs?select=company&limit=1` returns `[]` before the first sync and one open job after it;
a request to `crawl_log`, and any `POST`, `PATCH` or `DELETE` with the anon key, is refused with a permission error.

## Which key is used where

- Anon key (public): safe in the dashboard page. It can only read open jobs, because row-level security allows nothing
  else. The key is not a secret and may appear in `docs/`.
- Service-role key: bypasses row-level security, so it is a secret. It exists only as a GitHub Actions secret and is
  exposed only to the sync step of the daily workflow, never during the crawl. Never put it in the repository, the
  dashboard, a log or an issue. If it leaks, rotate it in the Supabase dashboard (Project Settings, API).

## Access model

- `jobs`: anonymous and signed-in users read rows whose `status` is `open`. Nobody but the service role writes.
- `crawl_log`: no policy, so only the service role reads or writes it.
- `profiles` (user id, email): a signed-in user reads their own row. Nobody writes it but the trigger below and the
  service role.
- `user_prefs` (filters, email settings) and `user_jobs` (star or discard per job, with the title, location and link
  kept on the row): a signed-in user selects, inserts, updates and deletes the rows whose `user_id` is their own id,
  through policies that test `(select auth.uid()) = user_id`. The anonymous role has no privilege on any of the three.
- `tests/test_migrations.py` checks these rules on every push: row-level security on every table, nothing for `anon`
  but reading open jobs, only the listed privileges for `authenticated`, own-rows-only policies, every function with a
  pinned search path and closed to the public roles, only the profile copy running on `auth.users`, and no column for
  anything but the email. It accepts only the statement kinds it knows, so a view, a default-privileges change or a new
  kind of function fails it until it is taught about it. It is a lint, not a proof: the live check above and the policy
  tests below are the real tests.

## Accounts and what is stored

Sign-in is Google through Supabase Auth, which owns the `auth` schema. The dashboard sends the browser to
`<project url>/auth/v1/authorize?provider=google` and gets it back with a one-time code (PKCE); there is no password.

- In `public`, the only personal detail is the email address, in `profiles`. A `security definer` trigger on
  `auth.users` (`sync_profile`) copies `new.email` there on sign-up and when the email changes, and reads nothing else.
- In `auth` (Supabase Auth's own tables, not copied anywhere by us), Google's answer is kept as Supabase Auth stores it:
  `auth.users.raw_user_meta_data` holds the claims Google sends (`email`, `email_verified`, `full_name`, `name`,
  `avatar_url`, `picture`, `iss`, `sub`, `provider_id`), `auth.identities.identity_data` holds the same, and
  `last_sign_in_at`, `created_at` and the refresh tokens sit beside them. This migration does not rewrite those tables:
  they are managed by Supabase and cannot be changed without risking the sign-in. The name and photo URL are therefore
  not in `public`, never shown and never saved by the page (it strips them from the session it keeps), but they do
  exist in `auth` until the user is deleted.
- The access token. Supabase Auth also copies `raw_user_meta_data` into every access token as the `user_metadata` claim,
  and the browser saves the token (the page cannot edit a signed token). `public.custom_access_token_hook` (in the
  migration) empties that claim and changes nothing else; `user_metadata` is an optional claim, so the token stays valid.
  It does nothing until it is switched on (see "Auth settings" below), and only Supabase Auth may run it.
- Every table points at `auth.users (id)` with `on delete cascade`: deleting the auth user (step 4.4) removes the
  profile, the preferences and every star and discard in one statement.

## Policy tests

`supabase/tests/accounts_rls.sql` creates two fake users and checks the policies: the profile holds only the email, the
access token hook empties `user_metadata`, a
second account cannot read, change, delete or move the first one's rows, nothing is open to `anon`, a job cannot be both
starred and discarded, and deleting a user removes only that user's rows. Run it against a test project or a local
database, never production (it works inside a transaction that ends in a rollback, but it creates users in
`auth.users`):

```sh
psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/accounts_rls.sql
```

It stops at the first failed check with a message that names it and ends with `all account policy checks passed`.
Pasting it into the SQL editor of the test project works too. CI does not run it (it needs a project); the lint above
runs everywhere.

## Auth settings the page depends on

In the Supabase dashboard, Authentication:

- Sign In / Providers: Google enabled with the Google OAuth client ID and secret. Check with
  `GET <project url>/auth/v1/settings` (send the anon key as `apikey`): `external.google` must be `true`. The Email
  provider should be switched off: the dashboard has no password sign-in, and an enabled Email provider lets anyone
  create an account with a password through the API.
- Auth Hooks, Customize Access Token (JWT) Claims: enable it, type Postgres function, schema `public`, function
  `custom_access_token_hook`. Check after a sign-in: the `user_metadata` claim of the access token (decode the middle
  part of the `access_token` in the browser's `misra-auth` local storage entry) is `{}`.
- URL Configuration: Site URL `https://ravidorr.github.io/misra/`, and under Redirect URLs
  `https://ravidorr.github.io/misra/**` (add the exact address as well as the pattern). The page asks Supabase to send the browser back to its own address without a
  query or a hash, `https://ravidorr.github.io/misra/`. For a local check add `http://localhost:8000/**`. Without a
  match Supabase falls back to the Site URL.
- The Google Cloud OAuth client's authorised redirect URI is `<project url>/auth/v1/callback`.
