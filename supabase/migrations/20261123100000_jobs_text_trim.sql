-- The full text of a closed job is deleted 30 days after the job closes (PLAN.md step 7.4; the lawyer's answers of
-- 2026-11-16). The daily workflow calls this function once a day through the sync step (sync_supabase.py, with the
-- service-role key), after the descriptions pass and a good sync, like report_site_cleanup and confirm_email_cleanup.
--
-- The table has no closed-at column. `last_seen` is the last crawl that saw the job open (the sync marks a job closed
-- without touching it), so it is the close time to the day. A closed job is trimmed when the day of its `last_seen`
-- (the first 10 characters of the ISO text) is more than 30 days before p_day; a closed job with no `last_seen` is
-- trimmed too, so it can never keep its text for good.
--
-- What is set to null: `description`, `details` and `snippet`. `details` goes whole: it holds the short fields a site
-- gives (employment type, department and so on) and `text_sha256`, the fingerprint the sync compares with the text it
-- holds. With the fingerprint gone, a job that reopens later has no stored fingerprint, so the next sync writes its text
-- again (a fingerprint left behind would make the sync think the text is still stored). Title, company, link and dates
-- stay: a closed job's row is kept (user stars and the digest history refer to its key).
--
-- It returns how many jobs lost text (counts only, no text is read out). p_day is passed in rather than read from the
-- clock so the function is testable; null or a day later than today is refused, so a wrong date can never trim early.
-- Only rows that still hold text are touched, so a second call on the same day returns 0. Only the service role may run
-- it. Nothing here is applied by the pull request: apply it once, see supabase/README.md.

create function public.jobs_text_trim(p_day date)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  trimmed integer;
begin
  if p_day is null or p_day > current_date then
    raise exception 'jobs_text_trim: p_day must be today (UTC) or earlier' using errcode = '22023';
  end if;
  update public.jobs
     set description = null, details = null, snippet = null
   where status = 'closed'
     and (description is not null or details is not null or snippet is not null)
     and coalesce(left(last_seen, 10), '') < to_char(p_day - 30, 'YYYY-MM-DD');
  get diagnostics trimmed = row_count;
  return trimmed;
end;
$$;

revoke all on function public.jobs_text_trim(date) from public, anon, authenticated;
grant execute on function public.jobs_text_trim(date) to service_role;
