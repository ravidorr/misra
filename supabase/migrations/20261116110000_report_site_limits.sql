-- Spam guard of the `report-site` Edge Function (PLAN.md step 5.1; PRD FR-7). Two counters, both touched only by the
-- function through the service role:
--
-- 1. report_ip_limits: how many reports one visitor has sent today. The key is a keyed hash of the visitor's address and
--    the day, made in the function (the raw address never reaches the database). The key changes every day, so a hash
--    from yesterday matches nothing today, and rows older than yesterday are deleted on every call.
-- 2. report_daily_cap: how many report emails the maintainer has been sent today, whoever sent them. Once it is reached
--    the function sends nothing more that day, so a flood cannot fill the inbox.
--
-- Nothing personal is stored: no address, no company, link or text of a report. Both tables have row-level security on
-- and no policy, and nothing is granted to `anon` or `authenticated`, so only the service role can read or write them.

create table public.report_ip_limits (
  day     date    not null,
  ip_hash text    not null,
  reports integer not null default 0,
  primary key (day, ip_hash)
);

create table public.report_daily_cap (
  day  date    primary key,
  sent integer not null default 0
);

alter table public.report_ip_limits enable row level security;
alter table public.report_daily_cap enable row level security;

revoke all on public.report_ip_limits from anon, authenticated;
revoke all on public.report_daily_cap from anon, authenticated;

-- One call takes a place for a report, atomically (the row lock of the upserts serialises concurrent requests), so a
-- flood of parallel requests cannot all pass the check. It answers 'ok', 'ip_limit' (this visitor has used today's
-- reports) or 'daily_cap' (the maintainer's inbox is full for today). A visitor over the limit does not use up the cap,
-- and a visitor refused because of the cap does not use up their own reports. A place is taken before the email is sent
-- and given back (report_site_release) when Resend definitely did not send it.
create function public.report_site_reserve(p_day date, p_ip_hash text, p_ip_limit integer, p_daily_cap integer)
returns text
language plpgsql
set search_path = ''
as $$
declare
  used integer;
  sent_now integer;
begin
  delete from public.report_ip_limits where day < p_day - 1;
  delete from public.report_daily_cap where day < p_day - 1;

  insert into public.report_ip_limits as l (day, ip_hash, reports) values (p_day, p_ip_hash, 1)
    on conflict (day, ip_hash) do update set reports = l.reports + 1
    returning l.reports into used;
  if used > p_ip_limit then
    return 'ip_limit';
  end if;

  if p_daily_cap < 1 then
    update public.report_ip_limits set reports = greatest(reports - 1, 0) where day = p_day and ip_hash = p_ip_hash;
    return 'daily_cap';
  end if;
  insert into public.report_daily_cap as c (day, sent) values (p_day, 1)
    on conflict (day) do update set sent = c.sent + 1 where c.sent < p_daily_cap
    returning c.sent into sent_now;
  if sent_now is null then
    update public.report_ip_limits set reports = greatest(reports - 1, 0) where day = p_day and ip_hash = p_ip_hash;
    return 'daily_cap';
  end if;
  return 'ok';
end;
$$;

create function public.report_site_release(p_day date, p_ip_hash text)
returns void
language plpgsql
set search_path = ''
as $$
begin
  update public.report_daily_cap set sent = greatest(sent - 1, 0) where day = p_day;
  update public.report_ip_limits set reports = greatest(reports - 1, 0) where day = p_day and ip_hash = p_ip_hash;
end;
$$;

revoke all on function public.report_site_reserve(date, text, integer, integer) from public, anon, authenticated;
revoke all on function public.report_site_release(date, text) from public, anon, authenticated;
grant execute on function public.report_site_reserve(date, text, integer, integer) to service_role;
grant execute on function public.report_site_release(date, text) to service_role;
