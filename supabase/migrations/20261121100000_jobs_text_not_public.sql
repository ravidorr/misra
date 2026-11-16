-- The full text of a posting (`description`) and the details a site gives with it (`details`) are stored in `jobs` by the
-- daily sync (PLAN.md step 7.1) but are NOT readable through the public API until the lawyer has seen that other
-- companies' full postings are stored. Step 7.3 (the dashboard drill-down) and step 8.1 (`get_job`) will open them, with
-- a new migration, only after that approval.
--
-- Column-level privileges: the table-level SELECT that anon and authenticated got in the first migration is taken back,
-- and SELECT is given back on every column except those two. Row-level security is unchanged (open jobs only). The
-- service role is not affected: it bypasses row-level security and keeps its privileges, so the sync writes both.
--
-- How it stays closed: a column added to `jobs` later has no column grant, so anon and authenticated cannot read it until
-- a migration grants it. A new readable column therefore needs its own `grant select (...)`, and the lint in
-- tests/test_migrations.py refuses the migration until this list and the table agree. Nothing reads `jobs` with
-- `select=*`: the dashboard, the digest and the trigger of migration 20261115180000 name their columns.
revoke select on public.jobs from anon, authenticated;
grant select (company, job_id, title, team, location, url, posted, first_seen, last_seen, status, level, role_types, regions, locations) on public.jobs to anon, authenticated;
