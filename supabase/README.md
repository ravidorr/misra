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

`supabase/tests/job_choices.sql` checks the two rules of step 4.2 the same way (`all job choice checks passed`): a user
can keep at most 2,000 rows (the 2,001st is refused with error 54000, a change to a job the user already has is not, and
another account is not affected), and a discard is deleted when its job is closed by the sync while a star stays with its
title, location and link.

## Stars and discards

`user_jobs` holds one row per user and job with a single `state`, so a job is starred or discarded, never both. A star
keeps its title, location and link (http or https only) so it can still be shown after the job closes; a discard keeps
only the job's key. Two triggers (migration `20261115180000_job_choices_cap_and_closed_jobs.sql`):

- `limit_user_jobs` refuses a new row for a user who already has 2,000, with error code `54000`
  (`program_limit_exceeded`; PostgREST answers it with HTTP 500, so the page checks the code before the status). The page treats that code as a refusal to tell the user about, not
  as a network failure to retry.
- `drop_discards_of_closed_job` runs on `public.jobs` when a row is written with `status = 'closed'` (the daily sync
  does this) and deletes every user's discard of that job, so discards never pile up and a job that reopens shows again.
  Stars stay. `skip_discard_of_closed_job` (before insert or update on `user_jobs`) skips a discard of a job that is not
  open in `public.jobs`, so none is left behind for a job that has already closed. It does not clean up discards of jobs that were already closed before it was applied; the table is new and
  empty of such rows.

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

## Report a site (Edge Function `report-site`)

PLAN.md step 5.1, PRD FR-7. A visitor with no account reports a career site that is missing from the crawl; the
maintainer gets one plain-text email through Resend. Nothing in the repository or the browser holds a secret: the
function reads them from Supabase function secrets. The dashboard form is step 5.2 (`templates/report.js`, the "Report a site" button on the coverage gap card).

Files: `supabase/functions/report-site/` (`index.ts` starts the server and is the only file that knows Deno,
`handler.ts` holds the logic, `validate.ts` the input checks), the migration `20261116110000_report_site_limits.sql`
and `supabase/config.toml` (`verify_jwt = false`: the page calls the function without a signed-in user).

### The request and the answers

`POST <project url>/functions/v1/report-site` with `Content-Type: application/json` and the body
`{"company": "...", "link": "https://...", "why": "...", "website": ""}`. `website` is the honeypot: the page must
render it as a text input a person never sees (off-screen, `tabindex="-1"`, `autocomplete="off"`, `aria-hidden="true"`,
not `display: none`) and send it empty. Limits: company 1 to 100 characters, link an http or https address of at most
500 characters with a host name and no user name or password, why 3 to 1,000 characters, the whole body 8 KB.

Every answer is JSON, `{"ok": true}` or `{"ok": false, "error": {"code", "message", ...}}`:

- 200 `{"ok": true}`: sent. A filled honeypot gets the same answer and nothing is sent or counted.
- 400 `invalid_input` (with `error.fields`, a message per field) or `invalid_request` (not a JSON object).
- 403 `origin_not_allowed`, 405 `method_not_allowed`, 413 `payload_too_large`, 415 `unsupported_media_type`.
- 429 `rate_limited`: this visitor has used today's 3 reports. 503 `daily_cap_reached`: 20 reports have been emailed
  today. Both carry `Retry-After` (seconds until 00:00 UTC, when the day rolls over).
- 502 `send_failed` (Resend refused it with a 4xx: the place and the visitor's report are given back. After a timeout,
  a network error or a 5xx the email may have been sent, so the place is kept: the cap errs on the strict side),
  500 `server_error` (a missing secret or an unreachable database; nothing is sent).

### What protects the inbox

1. CORS: only `https://ravidorr.github.io` is answered with `Access-Control-Allow-Origin`, and a browser request from any
   other origin gets 403. A request with no `Origin` header (curl, a script) is allowed, since the next three checks
   hold for it too.
2. The honeypot, then validation, before anything is counted. The email is plain text; only the company name, as one
   line, reaches a header (the subject), so a visitor cannot inject markup or a header.
3. At most 3 reports per visitor per day. The visitor is the `cf-connecting-ip` header (Cloudflare sets it in front of
   Supabase and replaces what the caller sent), else the last entry of `x-forwarded-for` (a caller can only write the
   first ones). An IPv6 address counts as its /64 network. If neither header holds an address all such requests share
   one bucket, which is stricter, not looser. The database receives only an HMAC-SHA-256 of the day and the visitor,
   keyed by the secret `IP_HASH_SECRET`, so the salt changes every day and a stored hash cannot be turned back into an
   address.
4. At most 20 emails per day in all. The place is taken in the database (one atomic call, so parallel requests cannot
   all pass) before the email is sent. If the database is down nothing is sent. A visitor refused because of the cap
   keeps their own reports.

The limits are the constants `IP_DAILY_LIMIT` (3) and `DAILY_CAP` (20) in `handler.ts`; change them there.

The function reads the answer of `report_site_reserve` (text, sent by PostgREST as a bare JSON string) but not that of
`report_site_release` (`returns void`: PostgREST sends no body, 204 or an empty 200), whose success is the status alone.

### What is stored

`report_ip_limits` (day, hash, number of reports) and `report_daily_cap` (day, emails sent). Rows older than yesterday
are deleted on every call. No address, company, link or text is stored. Both tables have row-level security on, no
policy and no grant for `anon` or `authenticated`; the two functions that change them (`report_site_reserve`,
`report_site_release`) can be executed by the service role only. `tests/test_migrations.py` lints that on every push, and
`supabase/tests/report_limits.sql` tests the counting against a database, the same way as the other SQL tests
(`psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/report_limits.sql`, ending in
`all report limit checks passed`).

### How it is tested

The function is TypeScript for Deno, which the repository's tools do not run. The logic therefore lives in plain modules
that use only web standards (`fetch`, `Request`, `Response`, Web Crypto), with the secrets, `fetch` and the clock passed
in. `test/report-site.test.ts` runs them with vitest (part of `npm run test` and CI) against a fake Resend and a fake of
the two database functions, including a flood that must send at most the cap. `test/typecheck-functions.test.mjs`
type-checks the function and its tests with the compiler (`supabase/functions/tsconfig.json` and `test/tsconfig.json`, strict; there is no `tsconfig.json` at the root because the release gate treats that file as a release trigger), and ESLint (strict, type-aware)
lints them with the rest. Only the few lines of `index.ts` run untested until the function is deployed.

### Deploy (the maintainer; nothing here is done by the pull request)

1. Apply the migration (see "Apply the migrations" above): `supabase link --project-ref dncodqeqpoqwcxdctzco`, then
   `supabase db push`, or paste `supabase/migrations/20261116110000_report_site_limits.sql` in the
   [SQL editor](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/sql/new).
2. Set the secrets in [Edge Functions, Secrets](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/functions/secrets)
   or with the CLI. `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are supplied to every function by Supabase.
   - `RESEND_API_KEY`: a Resend API key that may send from `send.avidor.org`. The GitHub Actions secret of the same name
     is separate and Edge Functions cannot see it, so it must be set here too.
   - `REPORT_TO_EMAIL`: the address that receives the reports (the maintainer's).
   - `IP_HASH_SECRET`: a random string of at least 16 characters, for example from `openssl rand -hex 32`.
   - `REPORT_FROM_EMAIL` (optional): the sender, default `Misra reports <reports@send.avidor.org>`.

   ```sh
   supabase secrets set --project-ref dncodqeqpoqwcxdctzco \
     RESEND_API_KEY=... REPORT_TO_EMAIL=... IP_HASH_SECRET="$(openssl rand -hex 32)"
   ```

3. Deploy: `supabase functions deploy report-site --project-ref dncodqeqpoqwcxdctzco` (`config.toml` sets
   `verify_jwt = false`). Logs are under
   [Edge Functions, report-site](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/functions/report-site/logs).
   A function missing a secret answers 500 and logs the names of the secrets that are missing, never values.

### Check it live

Each successful call below sends a real email and uses one of today's 20 places.

```sh
URL=https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/report-site
# 1. a good report: {"ok":true} and an email at REPORT_TO_EMAIL
curl -s -X POST "$URL" -H 'Content-Type: application/json' -H 'Origin: https://ravidorr.github.io' \
  -d '{"company":"Test Co","link":"https://example.com/careers","why":"Live check of the report function","website":""}'
# 2. a bad input: 400 invalid_input with a message per field, no email
curl -s -i -X POST "$URL" -H 'Content-Type: application/json' -d '{"company":"","link":"ftp://x","why":""}'
# 3. a filled honeypot: {"ok":true} and no email
curl -s -X POST "$URL" -H 'Content-Type: application/json' \
  -d '{"company":"Bot","link":"https://example.com","why":"spam spam","website":"http://spam.example"}'
# 4. the wrong origin is refused (403), and the preflight answers only the dashboard's origin
curl -s -i -X POST "$URL" -H 'Content-Type: application/json' -H 'Origin: https://evil.example' -d '{}'
curl -s -i -X OPTIONS "$URL" -H 'Origin: https://ravidorr.github.io' -H 'Access-Control-Request-Method: POST'
# 5. the per-visitor limit cannot be dodged by writing the forwarding headers (run from ONE network)
for i in 2 3 4 5; do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL" -H 'Content-Type: application/json' \
    -H "X-Forwarded-For: 10.0.0.$i" -H "CF-Connecting-IP: 10.0.1.$i" \
    -d '{"company":"Test Co","link":"https://example.com/careers","why":"Live limit check","website":""}'
done
```

Expected for step 5: `200`, `200`, `429`, `429`. Step 1 was this visitor's first report, the loop makes the second and
third and then hits the limit of 3. This shows the headers written by the caller are ignored, but it does not show that
visitors are told apart: if the platform gave every request the same address (a proxy's), this check would pass too.
So also run step 1 once from a second network (a phone on mobile data): it must answer `200` although the first
network has used up its limit. If it answers `429`, or the logs show `no usable visitor address, using the shared bucket`,
the platform does not set `cf-connecting-ip` the way this function assumes: read the real headers and fix
`visitorNetwork` in `handler.ts` before the form goes live. The daily cap needs no live flood: the vitest flood test and
`supabase/tests/report_limits.sql` prove it. The limits reset at 00:00 UTC.

## Delete account (Edge Function `delete-account`)

PLAN.md step 4.4, PRD FR-1 AC 4. A signed-in user deletes their own account in one step from the dashboard ("Delete my
account", after a confirmation). The function removes the auth user through the Admin API, and because every table that
holds user data (`profiles`, `user_prefs`, `user_jobs`) points at `auth.users (id)` with `on delete cascade`, every row
goes with it. No migration and no new secret are needed: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are supplied to
every function by Supabase, and the service-role key exists only inside the function, never in the page or the repository.

Files: `supabase/functions/delete-account/` (`index.ts` starts the server and is the only file that knows Deno,
`handler.ts` holds the logic) and `supabase/config.toml` (`verify_jwt = false`).

### The delete request and the answers

`POST <project url>/functions/v1/delete-account` with `Authorization: Bearer <the user's access token>` and the public
key as `apikey`. There is no body, and nothing in the request can name a user: the function asks Supabase Auth who owns
the token (`GET /auth/v1/user`, which checks the signature, the expiry and that the session still exists), takes the id
from that answer only (it must be a UUID of an `authenticated` user), and deletes that user (`DELETE
/auth/v1/admin/users/<id>`, a hard delete). It can therefore delete only the caller.

Every answer is JSON, `{"ok": true}` or `{"ok": false, "error": {"code", "message"}}`:

- 200 `{"ok": true}`: the account is gone. Also when the admin call finds it already gone (two tabs both ask), and when Auth
  accepts the token's signature but answers `user_not_found` (a retry after the answer to the first request was lost):
  that reply carries `"alreadyDeleted": true`. Auth checks the signature before it looks the user up, so this says
  nothing about anyone else. (The exact `user_not_found` error code of Auth is assumed from its documented behaviour:
  check it in the live test, step 3. If it differs the retry gets the 401 below, which is safe.)
- 401 `not_signed_in`: no bearer token, or Supabase Auth did not accept it (expired or signed out). No admin call is made.
  The page never reads it as "deleted": it tells the user nothing was deleted by this request.
- 403 `origin_not_allowed`, 405 `method_not_allowed`.
- 502 `delete_failed`: the admin call refused (a 4xx); the account is still there. 502 `delete_unconfirmed`: a timeout, a
  dropped connection or a 5xx; the account may or may not be gone, and the page says it could not confirm and offers a
  retry. 503 `service_unavailable`: the token check could not be reached; nothing was deleted. 500 `server_error`: a
  missing secret.

CORS answers only `https://ravidorr.github.io`. Only status codes are logged: no token, email or user id.

`verify_jwt = false` in `config.toml`: the gateway's JWT check would reject the CORS preflight (it carries no
`Authorization` header), and the function has to ask Supabase Auth who owns the token anyway, to learn the id. The
function refuses everything that Auth does not accept.

### What the page does

`templates/auth.js` shows "Delete my account" next to "Sign out" for a signed-in user. It opens a native `dialog` (focus
trap, Esc and screen-reader support from the browser) that says what is removed and that it cannot be undone, with
"Keep my account" focused first. While the request runs the buttons are busy and Esc is ignored. If it fails, the
account stays, the dialog says what happened and the button reads "Try again". On success the page clears this user's
queued changes, unsent filters and first-sign-in answer from the browser (`misra-pending-choices-v1`,
`misra-pending-filters-v1`, `misra-imported-v1`, this user's entries only), signs out locally, and shows a short
confirmation. The browser's own signed-out stars, discards and filters are not the account's and are kept. No WebMCP tool
deletes an account: that needs a person to confirm.

### How delete-account is tested

`test/delete-account.test.ts` (vitest, part of `npm run test`) runs the handler against a fake Supabase Auth: the
caller is the only user deleted whatever the body, query string or headers say, a rejected token never reaches the admin
endpoint, replay, 404, 4xx, 5xx and timeouts, CORS, methods, and that nothing secret is logged. The same type-check and
lint as `report-site` apply. `tests/test_delete_account.py` runs the page in a real browser against a fake function. The
cascade itself is `supabase/tests/accounts_rls.sql` (deleting a user removes only that user's rows).

### Deploy delete-account (the maintainer; nothing here is done by the pull request)

1. `supabase functions deploy delete-account --project-ref dncodqeqpoqwcxdctzco` (`config.toml` sets `verify_jwt = false`).
   Logs: [Edge Functions, delete-account](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/functions/delete-account/logs).
2. Check the deploy and the gateway setting with the checks below.

### Check delete-account live

Use a throwaway Google account made for this test, never your own. It depends on the step 4.1 setup (Google provider on,
redirect URLs set). Signed in on the dashboard with the throwaway account, star one role first.

1. Preflight and method, from any shell (no token, nothing is deleted):

   ```sh
   URL=https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/delete-account
   curl -s -i -X OPTIONS "$URL" -H 'Origin: https://ravidorr.github.io' -H 'Access-Control-Request-Method: POST'   # 204, allow-origin is the dashboard only
   curl -s -i -X GET "$URL" -H 'Origin: https://ravidorr.github.io'                                                # 405
   curl -s -i -X POST "$URL" -H 'Origin: https://evil.example'                                                     # 403
   curl -s -i -X POST "$URL" -H 'Origin: https://ravidorr.github.io'                                               # 401 not_signed_in
   curl -s -i -X POST "$URL" -H 'Origin: https://ravidorr.github.io' -H 'Authorization: Bearer not.a.token'        # 401 not_signed_in
   ```

2. On the dashboard, choose "Delete my account", read the notice, confirm. The page signs out and says the account was
   deleted. In the Supabase dashboard,
   [Authentication, Users](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/auth/users) no longer lists the test
   user, and in the [SQL editor](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/sql/new)
   `select count(*) from public.profiles where email = '<test email>';` and the same for `public.user_jobs` (by the
   test user's former id) return 0.
3. Replay: the old access token (copied from the browser's `misra-auth` local storage entry before step 2) sent to the
   function with `-H "Authorization: Bearer <token>"` answers 200 with `"alreadyDeleted": true` (Auth: `user_not_found`)
   or, if the token was also revoked, 401. Either way nothing else is deleted. Note which one you get.
4. Signing in again with the same Google account makes a new, empty account.

## Subscribers: confirm and unsubscribe (Edge Functions `confirm-email` and `unsubscribe`)

PLAN.md step 6.1, PRD FR-8 AC 7: the address is confirmed before the first email, and every email has an unsubscribe
link that works in one click. This step delivers the tables, the two functions and the static page; turning the email on
(and sending the confirmation email) is step 6.2, the digest is 6.3. Nothing is live until the maintainer applies the
migration and deploys, below.

Files: `supabase/migrations/20261117100000_subscriptions_and_emailed.sql`, `supabase/functions/_shared/subscription.ts`
(secrets, token format and hash, the database call, answers), `supabase/functions/confirm-email/` and
`supabase/functions/unsubscribe/` (`index.ts` starts the server, `handler.ts` holds the logic), `templates/email.html` and
`templates/email.js` (built into `docs/email.html` and `docs/email.js`), `supabase/config.toml` (`verify_jwt = false` for both).

### The rule for sending

A digest may be sent to a subscription when `consent_confirmed_at is not null and unsubscribed_at is null`, and only then
(step 6.3 must select on exactly that). The row keeps the latest consent cycle: turning the email on again replaces the
timestamps, the tokens and the address of the cycle before.

### The tables and who can write what

- `subscriptions` (one row per user, `user_id` references `auth.users` with `on delete cascade`): `address`, `frequency`
  (`daily` default, or `weekly`), `filters` (jsonb object, at most 16 KB, like `user_prefs`), `confirm_token_hash` and
  `confirm_token_expires_at`, `unsubscribe_token_hash`, `consent_given_at` (the user turned the email on),
  `consent_confirmed_at` (the user pressed Confirm), `unsubscribed_at`, `created_at`, `updated_at`.
- `emailed` (`user_id`, `company`, `job_id`, `emailed_at`; primary key on the first three): the digest checks a batch with
  the primary key and inserts with `on conflict do nothing`. No foreign key to `jobs`, like `user_jobs`.
- A signed-in user (role `authenticated`) can read their own row, column by column, without the token hashes or the expiry
  (so `select *` is refused: name the columns), and can update `frequency` and `filters` of their own row, nothing else.
  They cannot insert or delete (a delete would erase the consent log), cannot set `consent_confirmed_at`, the tokens, the
  address or `unsubscribed_at`. The address, the consent columns and the tokens are written by the service role only, so a
  user cannot confirm themselves, send the digest to an address that never confirmed, or undo an unsubscribe. A trigger adds
  a second lock: a changed address clears `consent_confirmed_at` (and the old confirmation token, unless a new one is
  supplied in the same update). A check keeps `filters` empty once `unsubscribed_at` is set.
- `emailed`, and the functions `confirm_subscription` and `unsubscribe_subscription`, are for the service role only. `anon`
  has nothing on any of it.
- `tests/test_migrations.py` and `tests/test_migrations_subscriptions.py` lint this on every push (no table-level grant, the
  exact columns, no token column readable, no policy on `emailed`, the functions closed to the public roles).
  `supabase/tests/subscriptions.sql` tests the behaviour against a database
  (`psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/subscriptions.sql`, ending in
  `all subscription checks passed`): what the owner may and may not do, a second user and `anon`, confirm (valid, replay,
  expired, after an unsubscribe), a new address, unsubscribe (what is kept and cleared, replay), and the cascade on deleting
  the user.

### Tokens

One random 256-bit token (32 bytes, base64url, 43 characters) per purpose, so a confirmation link cannot unsubscribe and the
reverse. Only its SHA-256 (64 hex characters, made in the function with Web Crypto; the table refuses anything else) is stored,
so a database leak can neither confirm nor unsubscribe anyone, and the raw token exists only in the emailed link.
`newToken()` in `_shared/subscription.ts` makes a token and its hash: the `email-settings` function (step 6.2) uses it for both and keeps no copy.

The functions look a token up by its hash inside the database function. An attacker cannot choose a hash, so an index lookup
leaks nothing through timing, which is why there is no byte-by-byte comparison to make constant-time. Guessing a token is a
1 in 2^256 chance, so there is no rate limit or lock-out (a flood only costs reads). Unknown, malformed and wrong-purpose
tokens get the same answer, so the functions cannot be used to find out who is subscribed. The confirmation token expires
(`confirm_token_expires_at`, set by step 6.2, suggested 7 days); the unsubscribe token never does, and stays on the row
after an unsubscribe so a second press says "already unsubscribed". A new subscription replaces both.

### confirm-email

`POST <project url>/functions/v1/confirm-email`, `Content-Type: application/json`, body `{"token": "<token>"}`, no `apikey` or
sign-in needed. Answers (JSON, `{"ok": true, "status"}` or `{"ok": false, "error": {"code", "message"}}`):

- 200 `confirmed`, or 200 `already_confirmed` (a replay, which is not an error and keeps the first time).
- 404 `link_not_valid` (unknown or malformed token, or the old link of an unsubscribed subscription: an unsubscribe clears its
  confirmation token), 409 `unsubscribed`, 410 `link_expired`.
- 400 `invalid_request`, 403 `origin_not_allowed`, 405 `method_not_allowed`, 413 `payload_too_large`, 415
  `unsupported_media_type`, 500 `server_error`.

A GET (or HEAD, or any method but POST and OPTIONS) is 405 and never confirms. Mail scanners and link previews fetch every
link in a message; a confirmation a GET could complete would be given by a robot and would spoil the consent log. The
confirmation email therefore links to the static page, `https://ravidorr.github.io/misra/email.html#token=<token>`. The
token is in the fragment (no server ever sees it, so it is in no log and no Referer), the page removes it from the address bar
at once, shows a Confirm button, and only the click POSTs the token here, in the body. CORS answers
`https://ravidorr.github.io` only; a browser request from any other Origin is 403.

### unsubscribe

The unsubscribe link of every digest email, and the `List-Unsubscribe` header, is the same URL:
`https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/unsubscribe?t=<unsubscribe token>`. Step 6.3 adds the headers
`List-Unsubscribe: <that URL>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.

- GET (the user's click): unsubscribes at once, then answers 303 to `email.html#unsubscribed` (or
  `#already-unsubscribed`, `#link-not-valid`, `#try-again`): a function cannot render a page (Supabase serves its HTML as plain
  text), so the page shows the result. The token is not in the Location.
- POST (RFC 8058 one-click, from the mailbox provider, body `List-Unsubscribe=One-Click`, no sign-in, usually no Origin): the
  body is ignored, the token in the URL is the credential. 200 `{"ok": true, "status": "unsubscribed"}` or
  `already_unsubscribed`; 404 `link_not_valid`; 500 `server_error` (the provider may retry). A foreign Origin is not refused
  and no CORS header is sent: the token is the only credential and a browser has no use for the answer.
- HEAD, OPTIONS and other methods are 405 and never unsubscribe.

An unsubscribe stops sending, keeps the audit record (address, `consent_given_at`, `consent_confirmed_at`, the first
`unsubscribed_at`, the unsubscribe hash) and clears the rest of the user's choices: `filters` to `{}`, `frequency` to `daily`,
the confirmation token and expiry, and the user's `emailed` rows (a repeat press clears them again, which finishes a clean-up
that failed the first time). A second press is not an error and keeps the first time. A link scanner that follows every link
may fire a GET and unsubscribe someone who did not ask to: that is the safe direction and is accepted (the user turns the
email on again); confirming, in contrast, can never happen from a GET.

### What is logged

Only function names and status codes (`confirm-email: failed confirm_subscription answered 500`): never a token, a hash, an
address or a user id, and nothing from a request or a database error body. The platform's own request log is Supabase's, not
ours: it holds the request URL, so the unsubscribe URL (which has the token in `?t=`, needed for RFC 8058) appears there for the
log's retention. That token can only unsubscribe, and only that one subscription. The confirmation token never appears in a URL
a server sees.

### How the subscriber functions are tested

`test/confirm-email.test.ts` and `test/unsubscribe.test.ts` (vitest, part of `npm run test` and CI) run the handlers with a fake
of the database (`test/support/fake-subscriptions.ts`, which mirrors the two SQL functions): the exact HTTP answers above, that
the database only ever receives hashes, replays, expiry, unsubscribed-then-confirm, scanner methods, CORS, malformed tokens, and
that no log or answer contains a token, hash, address or key. They are type-checked and linted like the other functions.
`tests/test_email_page.py` runs the page in a real browser against a fake function (the button, no request before the click,
the fragment removed, every answer, retry, keyboard, light and dark, phone width, axe).

### Apply and deploy (the maintainer; nothing here is done by the pull request)

1. Apply the migration: `supabase link --project-ref dncodqeqpoqwcxdctzco` then `supabase db push`, or paste
   `supabase/migrations/20261117100000_subscriptions_and_emailed.sql` into the
   [SQL editor](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/sql/new) and run it. After the SQL editor, record it
   so `db push` does not run it again: `supabase migration repair 20261117100000 --status applied --remote` (it targets the
   linked project and asks for the database password, or pass `--password`). No secret is needed: both functions use the
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` that Supabase supplies.
2. Deploy: `supabase functions deploy confirm-email --project-ref dncodqeqpoqwcxdctzco` and
   `supabase functions deploy unsubscribe --project-ref dncodqeqpoqwcxdctzco` (`config.toml` sets `verify_jwt = false`).
   Logs: [confirm-email](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/functions/confirm-email/logs),
   [unsubscribe](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/functions/unsubscribe/logs).
3. The page `email.html` goes live with the next daily build of the dashboard (or run `build_dashboard.py` and push), after this
   pull request is merged.

### Check the subscriber functions live

Make test rows in the [SQL editor](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/sql/new) for a throwaway user
(any real `auth.users` id), with tokens you generate and hash yourself:
`T=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'); printf %s "$T" | shasum -a 256` gives the hash to insert. Make one
pair for confirm, one for unsubscribe and a second unsubscribe token, with `confirm_token_expires_at` in the future. Then:

```sh
BASE=https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1
# confirm: a GET never confirms (405), a POST does (200 confirmed), a repeat says already_confirmed, junk is 404, a foreign origin 403
curl -s -i "$BASE/confirm-email?token=$CONFIRM"
curl -s -X POST "$BASE/confirm-email" -H 'Content-Type: application/json' -H 'Origin: https://ravidorr.github.io' -d "{\"token\":\"$CONFIRM\"}"
curl -s -X POST "$BASE/confirm-email" -H 'Content-Type: application/json' -d "{\"token\":\"$CONFIRM\"}"
curl -s -i -X POST "$BASE/confirm-email" -H 'Content-Type: application/json' -d '{"token":"nope"}'
curl -s -i -X POST "$BASE/confirm-email" -H 'Content-Type: application/json' -H 'Origin: https://evil.example' -d '{}'
# unsubscribe, RFC 8058, with no apikey and no Authorization header (proves verify_jwt = false lets a mailbox provider in)
curl -s -i -X POST "$BASE/unsubscribe?t=$UNSUB" -d 'List-Unsubscribe=One-Click'   # 200 unsubscribed
curl -s -i -X POST "$BASE/unsubscribe?t=$UNSUB" -d 'List-Unsubscribe=One-Click'   # 200 already_unsubscribed
curl -s -i -I "$BASE/unsubscribe?t=$UNSUB"                                        # HEAD: 405, never acts
# a GET redirects to the page (use the second unsubscribe token): 303, Location .../email.html#unsubscribed
curl -s -i "$BASE/unsubscribe?t=$UNSUB2"
```

Then in the SQL editor `select consent_confirmed_at, unsubscribed_at, filters, frequency, confirm_token_hash from
public.subscriptions where user_id = '<id>'` shows the times set, `filters` empty and the confirmation hash null after an
unsubscribe, and `select count(*) from public.emailed where user_id = '<id>'` is 0. Delete the throwaway user afterwards: its
rows cascade. In a browser open `https://ravidorr.github.io/misra/email.html#token=<a fresh confirmation token>`: the page shows
the Confirm button, and the row stays unconfirmed until it is pressed. That a 303 from a function reaches the browser on the
default domain is assumed: the GET above confirms it.

## Daily email: turn it on and off (Edge Function `email-settings`)

PLAN.md step 6.2, PRD FR-8 AC 1 to 3: a signed-in user turns the personalised email on or off, chooses the address (their
Google email by default), daily or weekly, and copies four dashboard filters for it. This step delivers the dashboard
settings, one Edge Function and four database functions. It does not send the digest (steps 6.3 and 6.4), so even with the
switches on, the only email that ever leaves is the confirmation email.

Files: `supabase/migrations/20261118100000_digest_signup_and_limits.sql`, `supabase/functions/email-settings/`
(`index.ts` starts the server, `handler.ts` holds the logic, `validate.ts` the input checks),
`supabase/functions/_shared/identity.ts` (who the token belongs to), `supabase/config.toml` (`verify_jwt = true`),
`templates/digest.js` and the marked parts of `templates/dashboard.html` (the "Daily email" button and dialog),
`build_dashboard.py` (the switch).

### The two switches (both are off)

The signup stays hidden and closed until the maintainer opens it, because the sender of step 6.3 does not exist yet and the
lawyer gate of PLAN.md step 6.6 is still to be closed. There are two switches, and both must be on:

1. The function refuses to create a subscription unless its secret `DIGEST_SIGNUP_ENABLED` is exactly `true` (anything else, or
   unset, answers 403 `signup_closed` and stores nothing, sends nothing and uses no limit place). Turning the email off still
   works when the secret is off, so a user can always unsubscribe.
   `supabase secrets set --project-ref dncodqeqpoqwcxdctzco DIGEST_SIGNUP_ENABLED=true` opens it, and `supabase secrets unset
   --project-ref dncodqeqpoqwcxdctzco DIGEST_SIGNUP_ENABLED` closes it again.
2. The page shows the control only when `DIGEST_SIGNUP_ENABLED = True` in `build_dashboard.py`. While it is `False` the build cuts
   everything between `<!--digest-->` and `<!--/digest-->` out of the page (the button, the dialog and the script tag) and does
   not write `docs/digest.js` (it also removes one left by an earlier build). Change the constant in a pull request, merge, and
   the next daily build publishes it. `tests/test_build_dashboard.py` tests both values.
   To close signups again, unset the secret (turning off keeps working without it). Do not set the constant back to `False` once anyone
   has subscribed: it also removes "Turn the email off" from the page.

Because the function checks its own switch, calling the API directly does not get around the page's flag.

### The email-settings request and the answers

`POST <project url>/functions/v1/email-settings`, `Content-Type: application/json`, `Authorization: Bearer <the user's access
token>`, `apikey: <publishable key>`. The gateway checks the token (`verify_jwt = true`; the CORS preflight carries no token and
the platform lets `OPTIONS` through), then the function asks Supabase Auth who owns it (`/auth/v1/user`) and takes the user id
and the default address only from that answer: nothing in the body, the query string or the token's unchecked claims can name
another user. Bodies:

- `{"action": "turn_on", "address": null | "<address>", "frequency": "daily" | "weekly", "filters": {"regions": [], "levels": [], "role_types": [], "keywords": ""}}`.
  An empty or missing `address` means the Auth account's email (a Google account's email is verified); a different address is
  accepted only as typed input and gets the confirmation email first (see "Who can mail whom, and the limits"). `filters` has
  exactly those four keys (at most 30 names of at most 80 characters each, keywords at most 200 characters; the names are not
  checked against the data).
- `{"action": "turn_off"}`.

Answers: 200 `{"ok": true, "state": "waiting"}` (settings stored, one confirmation email sent) or `{"ok": true, "state": "on"}` (it
was running for that address already: nothing changed, nothing sent, no place used); 200 `{"ok": true, "status": "unsubscribed" |
"already_unsubscribed" | "not_subscribed"}` for turn off. Errors are `{"ok": false, "error": {"code", "message"}}`: 400
`invalid_input` (with `fields`) or `invalid_request`, 401 `not_signed_in`, 403 `origin_not_allowed` or `signup_closed`, 405, 413,
415, 429 `rate_limited` (the user) or `address_limited`, 503 `daily_cap_reached` or `service_unavailable` (Auth did not answer),
502 `send_failed` (Resend refused: nothing was sent) or `send_unconfirmed` (Resend did not answer clearly: it may have been sent),
500 `server_error`. No answer holds a token, a hash, an address or a user id. The page never takes its state from the answer: it
reads the user's own `subscriptions` row again (row-level security gives them their row without the token hashes).

What turn on writes (`subscribe_start`, one statement under a row lock): `address`, `frequency`, `filters`, `consent_given_at = now`,
`consent_confirmed_at = null`, `unsubscribed_at = null`, the SHA-256 of a fresh confirmation token (valid 7 days:
`CONFIRM_TTL_DAYS`) and of a fresh unsubscribe token. A new address is a new consent: nothing is sent until the new link is
confirmed. Asking again while waiting issues a new link (the old one stops working); asking for the running address leaves the
row alone. The `emailed` list is not touched. The raw unsubscribe token is not kept anywhere and is not in the confirmation email,
so a digest cannot rebuild an unsubscribe link from what is stored: step 6.3 has to decide how the digests get a working link
(step 6.1 documents the unsubscribe token as stable for the life of the row, and replacing it would break the links of earlier emails).

The confirmation email is plain text from `Misra <digest@send.avidor.org>` with `Reply-To: raanan@avidor.org` (the sending address
cannot receive mail), subject "Confirm your Misra job alerts". The body is the lawyer's wording, verbatim: "Someone (hopefully
you) turned on the daily (or weekly) job digest for ADDRESS on Misra", the link
`https://ravidorr.github.io/misra/email.html#token=<token>` (the token in the fragment, as step 6.1 designed), that nothing is sent
unless the link is clicked and that the first digest holds only roles that appear after confirmation, and a footer naming Misra and
Raanan Avidor, saying to ignore the email if it was not asked for, with links to the privacy notice and the terms
(`renderEmail` in `handler.ts`; `test/email-settings.test.ts` pins the whole text). The raw token exists only
in that email and in the one request to Resend, and is never logged or returned.

### Who can mail whom, and the limits

A signed-in user can ask for one email to any address they type, so the function limits it. In the order they run, before anything
is stored or sent:

1. The signup switch (above), then the validation of the body.
2. At most `USER_DAILY_LIMIT` (3) confirmation emails per user and day, `ADDRESS_DAILY_LIMIT` (3) per address and day (any
   number of users, the address compared in lower case) and `DAILY_CAP` (30) in all. The counters are keyed hashes (HMAC-SHA-256
   of the day and `user:<id>` or `address:<address>`, keyed by the secret `IP_HASH_SECRET`, the one `report-site` uses), so a
   key changes every day and the database never holds an id or an address; rows older than yesterday are deleted on every call.
   The three places are taken in one atomic database call (`confirm_email_reserve`), so parallel requests cannot all pass, and a
   refused request uses nothing.
3. A place is given back (`confirm_email_release`) when nothing was sent: Resend answered with a 4xx, or the subscription could
   not be stored. A timeout, a network error or a 5xx may have sent the email, so the place stays used and the answer says so. If the
   database cannot be reached nothing is sent (fail closed).

The caps are constants with a comment in `handler.ts`: Resend's free plan is 100 emails a day and 3,000 a month, shared with the
other project, and `report-site` may use 20 a day, so these use at most 30 of the rest. Change them there. A plus-address
(`name+1@example.com`) counts as another address, so a person with many Google accounts could still reach one inbox up to the
per-address limit for each; the daily cap bounds the total.

### Turning it off

`unsubscribe_user` reads the row's unsubscribe hash (the raw token is never stored) and calls the step 6.1 function
`unsubscribe_subscription`, so the clean-up is the very same code as the unsubscribe link's: it keeps the address and the consent
timestamps, sets `unsubscribed_at`, and clears `filters`, `frequency`, the confirmation token and the `emailed` rows. A repeat is
not an error. A user with no row gets `not_subscribed`. `supabase/tests/digest_signup.sql` runs both ways on identical rows and
compares them.

### The page

`templates/digest.js` (a module with its own integrity hash, built to `docs/digest.js` only while the switch is on). The "Daily
email" button sits next to "Sign out" and opens a native dialog with the state (not on, waiting for you to confirm, on, unsubscribed),
the address field, daily or weekly, the filters the email will use, "Use these filters for my email" and the actions "Turn the email
on" (or "Send the confirmation again" while waiting, "Change the address and confirm it" while on) and "Turn the email off". The
button copies exactly regions, levels, role types and keywords from the dashboard at that moment (`globalThis.misraEmailFilters()`
in `dashboard.js` returns copies); later changes to the page's filters, a quick search included, change nothing until it is pressed
again. While the email is off the choices are a draft sent with "Turn the email on"; while it waits or runs, a new frequency or
filter set is saved straight to the user's own row (the only two columns they may update). Every failure is explained and never
called a success, and the page reads the row again after every call.

There is deliberately no WebMCP tool for any of this (it would email a person). Agents can read `data-digest-state` (`off`,
`waiting`, `on`, `unsubscribed`, `unknown`, `loading`) and `data-digest-frequency` on the button, and `data-state` on the dialog;
no address is in a hook. `tests/test_digest_settings.py` checks that no registered tool name has anything to do with email.

### How email-settings and the page are tested

`test/email-settings.test.ts` (vitest, part of `npm run test` and CI) runs the handler with a fake Auth, a fake of the four database
functions that mirrors the SQL (`test/support/fake-email-settings.ts`) and a fake Resend: what is stored and sent, the user id
only from the token, the address rules, the limits (user, address, cap, a refusal using nothing, a new day), the give-back rules,
the switch, the failures, and that no log line or answer holds a token, hash, address, user id or key. `tests/test_digest_settings.py`
runs the dialog in a real browser against fakes (every state, each error message, the busy state, light and dark, phone width, axe,
keyboard). The SQL is tested against a database with `supabase/tests/digest_signup.sql`
(`psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/digest_signup.sql`, ending in `all digest signup checks passed`),
and `tests/test_migrations.py` lints the migration on every push.

### Apply and deploy email-settings (the maintainer; nothing here is done by the pull request)

1. Apply the migration `supabase/migrations/20261118100000_digest_signup_and_limits.sql` (`supabase db push`, or paste it into the
   [SQL editor](https://supabase.com/dashboard/project/dncodqeqpoqwcxdctzco/sql/new) and record it with
   `supabase migration repair --status applied 20261118100000 --remote=dncodqeqpoqwcxdctzco`).
2. Deploy: `supabase functions deploy email-settings --project-ref dncodqeqpoqwcxdctzco` (`config.toml` sets `verify_jwt = true`).
   Secrets it reads: `RESEND_API_KEY` and `IP_HASH_SECRET` (both already set for `report-site`), and `DIGEST_SIGNUP_ENABLED` (leave it
   unset until signups open). Optional: `DIGEST_FROM_EMAIL` (default `Misra <digest@send.avidor.org>`) and `DIGEST_REPLY_TO_EMAIL`
   (default `raanan@avidor.org`). Without the mail secrets turning on answers 500 and the log names what is missing, while turning
   off keeps working.

### Check it live (none of these creates a subscription or sends an email)

```sh
URL=https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/email-settings
# 1. no token: the gateway answers 401 and the function never runs. The same with the publishable key as the bearer.
curl -s -i -X POST "$URL" -H 'Content-Type: application/json' -d '{"action":"turn_off"}'
curl -s -i -X POST "$URL" -H "Authorization: Bearer $PUBLISHABLE_KEY" -H 'Content-Type: application/json' -d '{"action":"turn_off"}'
# 2. the preflight must pass the gateway without a token: 204, allow-origin the dashboard, authorization among the allowed headers
curl -s -i -X OPTIONS "$URL" -H 'Origin: https://ravidorr.github.io' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: authorization,content-type,apikey'
```

With the access token of the maintainer's own signed-in session (in the browser: `JSON.parse(localStorage['misra-auth']).access_token`)
as `$TOKEN`, and the secret `DIGEST_SIGNUP_ENABLED` unset:

```sh
# 3. turning on is refused while the switch is off: 403 signup_closed, and the SQL editor shows no row for you
curl -s -i -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Origin: https://ravidorr.github.io' \
  -d '{"action":"turn_on","frequency":"daily","filters":{"regions":[],"levels":[],"role_types":[],"keywords":""}}'
# 4. turning off with no subscription is a harmless 200 {"ok":true,"status":"not_subscribed"}
curl -s -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"action":"turn_off"}'
# 5. a body that names someone else is just ignored (still not_subscribed): the user is the token's
curl -s -X POST "$URL" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"action":"turn_off","user_id":"00000000-0000-4000-8000-000000000000"}'
```

In the SQL editor: `select count(*) from public.subscriptions;` is 0 after these, and
`select has_function_privilege('anon', 'public.subscribe_start(uuid,text,text,jsonb,text,timestamptz,text,timestamptz)', 'execute')`
(and the same for `authenticated`) is `false`. The checks that open the switch and send the first real confirmation email belong to
the day signups open (PLAN.md step 6.6): turn the secret on, build the page with the constant `True`, and with the maintainer's own
account turn the email on, confirm from the link and read the row. If the preflight in check 2 is refused (a gateway that wants a
token even for `OPTIONS`), or a real user token in check 3 gets a 401 from the gateway instead of `403 signup_closed` (a project
whose signing keys the gateway's JWT check does not accept), set `verify_jwt = false` for this function in `config.toml` and
redeploy: the function already asks Auth who the caller is and refuses everything without a token Auth accepts. Then update the
test in `tests/test_function_config.py` and the comment in `config.toml`.
