-- A short snippet of a posting, readable by everyone (PLAN.md step 7.2; the lawyer's answers of 2026-11-16).
--
-- The full text of a posting stays in `description` and `details`, which the public roles still cannot read (migration
-- 20261121100000_jobs_text_not_public.sql). The dashboard shows only `snippet`: the first 255 characters of the
-- description as written, cut at a word boundary with no ellipsis (descriptions.snippet in the daily sync computes it),
-- and null when the job has no description. The text is the employer's, so the dashboard links to the employer for the
-- rest.
--
-- Row-level security is unchanged (open jobs only). Only `snippet` is granted: `description` and `details` stay closed,
-- and the lint in tests/test_migrations.py refuses a grant of either.
alter table public.jobs add column snippet text;
grant select (snippet) on public.jobs to anon, authenticated;
