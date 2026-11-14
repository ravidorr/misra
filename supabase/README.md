# Supabase

The jobs backend of the dashboard. `supabase/migrations/` holds plain SQL, applied in file-name order. Each file name
starts with a UTC timestamp (`YYYYMMDDHHMMSS_name.sql`), the format of the Supabase CLI.

## Apply the migrations

Project region: EU (Ireland). Either way works on a fresh project; do it once, and again whenever a new file appears.

- Supabase CLI: `supabase link --project-ref <ref>`, then `supabase db push`.
- SQL editor: open each file of `supabase/migrations/` in order, paste it into the SQL editor of the project, run it.

Check: `select count(*) from public.jobs;` returns 0 before the first sync. With the anon key,
`GET <project url>/rest/v1/jobs?select=company&limit=1` returns `[]` before the first sync and one open job after it;
the same request to `crawl_log` returns `[]`, and any `POST`, `PATCH` or `DELETE` with the anon key is refused.

## Which key is used where

- Anon key (public): safe in the dashboard page. It can only read open jobs, because row-level security allows nothing
  else. The key is not a secret and may appear in `docs/`.
- Service-role key: bypasses row-level security, so it is a secret. It exists only as a GitHub Actions secret and is
  exposed only to the sync step of the daily workflow, never during the crawl. Never put it in the repository, the
  dashboard, a log or an issue. If it leaks, rotate it in the Supabase dashboard (Project Settings, API).

## Access model

- `jobs`: anonymous and signed-in users read rows whose `status` is `open`. Nobody but the service role writes.
- `crawl_log`: no policy, so only the service role reads or writes it.
- `tests/test_migrations.py` checks these rules on every push: row-level security on every table, no write policy,
  no write privilege for `anon` or `authenticated`.
