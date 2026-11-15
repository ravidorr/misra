-- Daily cleanup of the spam-guard counters of the `report-site` function (PLAN.md step 5.1; PRD FR-7; PRIVACY.md).
--
-- report_site_reserve already deletes rows older than yesterday, but only when a report arrives, so on a quiet day an old
-- hash would stay. The daily workflow now calls this function once a day through the sync step (sync_supabase.py, with the
-- service-role key), so the counters are deleted every day whether or not anyone reports a site. It deletes every row of
-- a day before p_day, that is everything except today's rows: a hash of a network address therefore matches nothing after its
-- day ends (UTC, the key changes) and its row is deleted by the first daily run after that, so it can outlive its day by the
-- hours until that run (see supabase/README.md). (The day-ago rows that report_site_reserve keeps are only a safety margin for requests
-- around midnight; this function does not need one, because a request that lands in a day already cleaned is deleted
-- by the next run.)
--
-- It returns how many rows it deleted (counts only, nothing personal). p_day is passed in rather than read from the
-- clock so the function is testable; a day later than today is refused, so a wrong date can never wipe today's
-- counters. Only the service role may run it. Nothing here is applied by the pull request: apply it once, see
-- supabase/README.md.

create function public.report_site_cleanup(p_day date)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  removed_ips integer;
  removed_caps integer;
begin
  if p_day is null or p_day > current_date then
    raise exception 'report_site_cleanup: p_day must be today (UTC) or earlier' using errcode = '22023';
  end if;
  delete from public.report_ip_limits where day < p_day;
  get diagnostics removed_ips = row_count;
  delete from public.report_daily_cap where day < p_day;
  get diagnostics removed_caps = row_count;
  return removed_ips + removed_caps;
end;
$$;

revoke all on function public.report_site_cleanup(date) from public, anon, authenticated;
grant execute on function public.report_site_cleanup(date) to service_role;
