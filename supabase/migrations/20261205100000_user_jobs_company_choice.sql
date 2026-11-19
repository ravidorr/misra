-- "Company not for me" in the account (the lawyer approved it on 2026-11-19). The choice is one more row of
-- public.user_jobs, so it takes the table's row-level security (a user reads and writes only their own rows), its
-- cascade on account deletion, its cap of 2,000 rows per user in limit_user_jobs (stars, position choices and company
-- choices count together) and its place in the daily backup, and needs no new table. A company choice has the state
-- 'company' and the company's name, and its job_id is the empty text (the key of the row stays (user_id, company,
-- job_id), so the same user cannot hold it twice). No job has an empty id, so the row never matches a job. It keeps no
-- title, location or link. It has no expiry: it goes when the user restores the company or deletes the account.
-- drop_discards_of_closed_job and skip_discard_of_closed_job only act on the state 'discard', so a company choice is
-- never removed when a role closes. The migration changes two checks and nothing else; no row is written, and every
-- existing row passes both. Not applied by the pull request: apply it once after the merge, see supabase/README.md.
-- supabase/tests/company_choices.sql tests it.

alter table public.user_jobs drop constraint user_jobs_state_check;

alter table public.user_jobs add constraint user_jobs_state_check check (state in ('star', 'discard', 'company'));

alter table public.user_jobs drop constraint user_jobs_job_id_check;

alter table public.user_jobs add constraint user_jobs_job_id_check check (
  (state = 'company' and job_id = '') or (state <> 'company' and char_length(job_id) between 1 and 200)
);
