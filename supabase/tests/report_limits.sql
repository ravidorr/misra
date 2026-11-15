-- Tests for supabase/migrations/20261116110000_report_site_limits.sql (PLAN.md step 5.1; PRD FR-7).
--
-- Run it against a TEST project (or a local one), never production. It works inside one transaction that ends with a
-- rollback, but it writes to the two counter tables and a test project is the safe place for that.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/report_limits.sql
-- It stops at the first failed check with a message that names it, and ends with "all report limit checks passed".
--
-- Needs the migration applied. Uses only plain SQL (assert), no pgTAP.

begin;

do $$
declare
  answer text;
  results text[] := '{}';
  i integer;
begin
  -- One visitor gets the limit and no more.
  for i in 1..5 loop
    results := results || public.report_site_reserve(date '2030-01-01', 'visitor-a', 3, 100);
  end loop;
  assert results = array['ok', 'ok', 'ok', 'ip_limit', 'ip_limit'], 'the per-visitor limit is wrong: ' || results::text;

  -- A visitor over the limit does not use up the daily cap: three places were taken, not five.
  assert (select sent from public.report_daily_cap where day = date '2030-01-01') = 3,
    'a refused visitor used up the daily cap';

  -- Another visitor is not affected by the first.
  assert public.report_site_reserve(date '2030-01-01', 'visitor-b', 3, 100) = 'ok', 'a second visitor was refused';

  -- The daily cap: a flood from many visitors fills it exactly and then everyone is refused.
  delete from public.report_daily_cap;
  delete from public.report_ip_limits;
  results := '{}';
  for i in 1..30 loop
    results := results || public.report_site_reserve(date '2030-01-02', 'flood-' || i, 3, 20);
  end loop;
  assert (select count(*) from unnest(results) r where r = 'ok') = 20, 'the flood was not stopped at the cap: ' || results::text;
  assert (select count(*) from unnest(results) r where r = 'daily_cap') = 10, 'the refused reports did not say daily_cap';
  assert (select sent from public.report_daily_cap where day = date '2030-01-02') = 20, 'the cap counter went past the cap';

  -- A place given back (the email could not be sent) can be taken again, and the counter never goes below zero.
  perform public.report_site_release(date '2030-01-02');
  assert (select sent from public.report_daily_cap where day = date '2030-01-02') = 19, 'the release did not free a place';
  assert public.report_site_reserve(date '2030-01-02', 'late', 3, 20) = 'ok', 'a freed place was not usable';
  assert public.report_site_reserve(date '2030-01-02', 'later', 3, 20) = 'daily_cap', 'the cap was exceeded after a release';
  perform public.report_site_release(date '2031-01-01');  -- a day with no row: nothing happens
  delete from public.report_daily_cap;
  insert into public.report_daily_cap (day, sent) values (date '2030-01-05', 0);
  perform public.report_site_release(date '2030-01-05');
  assert (select sent from public.report_daily_cap where day = date '2030-01-05') = 0, 'the counter went below zero';

  -- A cap of zero sends nothing.
  assert public.report_site_reserve(date '2030-01-06', 'zero', 3, 0) = 'daily_cap', 'a cap of zero let a report through';

  -- A new day starts from zero, and rows older than yesterday are deleted.
  delete from public.report_daily_cap;
  delete from public.report_ip_limits;
  perform public.report_site_reserve(date '2030-02-01', 'old', 3, 20);
  perform public.report_site_reserve(date '2030-02-02', 'old', 3, 20);
  assert (select count(*) from public.report_ip_limits) = 2, 'yesterday was deleted too early';
  assert public.report_site_reserve(date '2030-02-03', 'old', 3, 20) = 'ok', 'the new day did not start from zero';
  assert not exists (select 1 from public.report_ip_limits where day = date '2030-02-01'), 'a row older than yesterday was kept';
  assert not exists (select 1 from public.report_daily_cap where day = date '2030-02-01'), 'a cap row older than yesterday was kept';
end;
$$;

-- Nobody but the service role may touch the counters or call the functions.
do $$
declare
  who text;
  tables text[] := array['public.report_ip_limits', 'public.report_daily_cap'];
  t text;
begin
  assert (select count(*) from pg_class where relname in ('report_ip_limits', 'report_daily_cap') and relrowsecurity) = 2,
    'row-level security is not on for both tables';
  assert not exists (select 1 from pg_policies where tablename in ('report_ip_limits', 'report_daily_cap')),
    'a policy exists on a counter table';
  foreach who in array array['anon', 'authenticated'] loop
    foreach t in array tables loop
      assert not has_table_privilege(who, t, 'select, insert, update, delete, truncate'), who || ' has a privilege on ' || t;
    end loop;
    assert not has_function_privilege(who, 'public.report_site_reserve(date, text, integer, integer)', 'execute'),
      who || ' may call report_site_reserve';
    assert not has_function_privilege(who, 'public.report_site_release(date)', 'execute'), who || ' may call report_site_release';
  end loop;
  assert has_function_privilege('service_role', 'public.report_site_reserve(date, text, integer, integer)', 'execute'),
    'the service role may not call report_site_reserve';
  assert has_function_privilege('service_role', 'public.report_site_release(date)', 'execute'),
    'the service role may not call report_site_release';
end;
$$;

-- And the anonymous and signed-in roles really are refused when they try.
set local role anon;
do $$
begin
  begin
    perform public.report_site_reserve(date '2030-03-01', 'x', 3, 20);
    assert false, 'anon called report_site_reserve';
  exception when insufficient_privilege then
    null;
  end;
  begin
    perform count(*) from public.report_ip_limits;
    assert false, 'anon read report_ip_limits';
  exception when insufficient_privilege then
    null;
  end;
end;
$$;
reset role;

set local role authenticated;
do $$
begin
  begin
    perform public.report_site_release(date '2030-03-01');
    assert false, 'a signed-in user called report_site_release';
  exception when insufficient_privilege then
    null;
  end;
  begin
    insert into public.report_daily_cap (day, sent) values (date '2030-03-01', 0);
    assert false, 'a signed-in user wrote report_daily_cap';
  exception when insufficient_privilege then
    null;
  end;
end;
$$;
reset role;

do $$ begin raise notice 'all report limit checks passed'; end $$;

rollback;
