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
