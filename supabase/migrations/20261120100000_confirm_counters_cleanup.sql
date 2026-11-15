-- Daily cleanup of the spam-guard counters of the confirmation email (PLAN.md step 6.6; PRIVACY.md), the same way
-- report_site_cleanup (20261119100000) cleans the counters of `report-site`.
--
-- confirm_email_reserve already deletes rows older than yesterday, but only when a confirmation email is requested, so on
-- a quiet day an old keyed hash would stay. The daily workflow now calls this function once a day through the sync step
-- (sync_supabase.py, with the service-role key) right after report_site_cleanup, so both counter families are deleted every
-- day whether or not anyone asks for an email. It deletes every row of a day before p_day, that is everything except
-- today's rows (the hashes are keyed with a salt that changes every UTC day, so an earlier day's hash matches nothing
-- anyway).
--
-- It returns how many rows it deleted (counts only, nothing personal). p_day is passed in rather than read from the clock
-- so the function is testable; null or a day later than today is refused, so a wrong date can never wipe today's counters.
-- Only the service role may run it. Nothing here is applied by the pull request: apply it once, see supabase/README.md.

create function public.confirm_email_cleanup(p_day date)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  removed_hashes integer;
  removed_caps integer;
begin
  if p_day is null or p_day > current_date then
    raise exception 'confirm_email_cleanup: p_day must be today (UTC) or earlier' using errcode = '22023';
  end if;
  delete from public.confirm_limits where day < p_day;
  get diagnostics removed_hashes = row_count;
  delete from public.confirm_daily_cap where day < p_day;
  get diagnostics removed_caps = row_count;
  return removed_hashes + removed_caps;
end;
$$;

revoke all on function public.confirm_email_cleanup(date) from public, anon, authenticated;
grant execute on function public.confirm_email_cleanup(date) to service_role;
