-- Tests for supabase/migrations/20261129100000_admin_crawl_health.sql (PLAN_2.md steps 2.1 and 2.2): the denial first (a
-- signed-out visitor and a signed-in non-admin), then what a listed admin reads, the text of a posting that stays closed
-- to the admin, and every column of `admin_crawl_health()` on seeded crawl logs. Run it against a TEST database, never
-- production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/admin_crawl_health.sql
-- Ends with "all admin crawl health checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'admin@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'other@test.example');
insert into public.admins (user_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');

-- Acting as a role, signed in as `who` when given (the role and the claims PostgREST sets from the token).
create function pg_temp.act_as(role_name text, who uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', coalesce(json_build_object('sub', who, 'role', role_name)::text, ''), true);
  perform set_config('request.jwt.claim.sub', coalesce(who::text, ''), true);
  perform set_config('role', role_name, true);
end;
$$;

-- The statement `sql` run as the role, true when it is refused with a permission error.
create function pg_temp.denied(role_name text, who uuid, sql text) returns boolean language plpgsql as $$
begin
  perform pg_temp.act_as(role_name, who);
  execute sql;
  reset role;
  return false;
exception when insufficient_privilege then
  reset role;
  return true;
end;
$$;

-- How many rows the statement `sql` (a `select count(*) ...`) counts for the role.
create function pg_temp.count_as(role_name text, who uuid, sql text) returns bigint language plpgsql as $$
declare n bigint;
begin
  perform pg_temp.act_as(role_name, who);
  execute sql into n;
  reset role;
  return n;
end;
$$;

-- The row of `admin_crawl_health()` for a company, as the listed admin, as json (null when it has no row).
create function pg_temp.health(name text) returns jsonb language plpgsql as $$
declare r jsonb;
begin
  perform pg_temp.act_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  select to_jsonb(h) into r from public.admin_crawl_health() h where h.company = name;
  reset role;
  return r;
end;
$$;

-- A log timestamp `n` days ago at `hour`, the format of the crawl (local ISO time).
create function pg_temp.ts(n int, hour text default '03') returns text language sql as $$
  select to_char(current_date - n, 'yyyy-mm-dd') || 'T' || hour || ':00:00';
$$;

-- Jobs: the text of a posting is there so the tests can prove it never comes out.
insert into public.jobs (company, job_id, title, status, description, details) values
  ('Healthy Co', 'a', 'One', 'open', 'secret posting text', '{"k": "secret"}'),
  ('Healthy Co', 'b', 'Two', 'open', 'secret posting text', '{}'),
  ('Healthy Co', 'c', 'Three', 'closed', 'secret posting text', '{}'),
  ('Failing Co', 'a', 'One', 'closed', null, null);

-- Crawl logs. Day 0 is today; the newest crawl day is today. Notes are the real prefixes of common.py.
insert into public.crawl_log (ts, company, ats, ok, n_jobs, note) values
  -- healthy: good for three days, and a crawl 31 days ago that fetched 7
  (pg_temp.ts(31), 'Healthy Co', 'greenhouse', 1, 7, ''),
  (pg_temp.ts(2), 'Healthy Co', 'greenhouse', 1, 3, ''),
  (pg_temp.ts(1), 'Healthy Co', 'greenhouse', 1, 3, null),
  (pg_temp.ts(0), 'Healthy Co', 'greenhouse', 1, 3, ''),
  -- failing: good 3 days ago, then three failed days; a failed fetch 32 days ago (fetched 0)
  (pg_temp.ts(32), 'Failing Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(3), 'Failing Co', 'lever', 1, 5, ''),
  (pg_temp.ts(2), 'Failing Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(1), 'Failing Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(0), 'Failing Co', 'lever', 0, 0, 'HTTP 500'),
  -- one failed crawl after a good one is not failing yet
  (pg_temp.ts(1), 'One Off Co', 'lever', 1, 4, ''),
  (pg_temp.ts(0), 'One Off Co', 'lever', 0, 0, 'request failed'),
  -- two runs today, the failed one first: the day's last run is good, so the streak is 0
  (pg_temp.ts(1), 'Rerun Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(0, '03'), 'Rerun Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(0, '09'), 'Rerun Co', 'lever', 1, 4, ''),
  -- held: ok = 0 with the sharp drop note, two days in a row; held and not failing
  (pg_temp.ts(1), 'Held Co', 'browser', 0, 3, 'sharp drop: got 3, 40 open before'),
  (pg_temp.ts(0), 'Held Co', 'browser', 0, 2, 'sharp drop: got 2, 40 open before (empty page)'),
  -- robots.txt: a failure, then two skipped days; skips are left out of the streak and make no failing
  (pg_temp.ts(2), 'Robots Co', 'browser', 0, 0, 'request failed'),
  (pg_temp.ts(1), 'Robots Co', 'browser', 0, 0, 'robots.txt does not allow it (disallowed)'),
  (pg_temp.ts(0), 'Robots Co', 'browser', 0, 0, 'robots.txt does not allow it (disallowed)'),
  -- a skip between failures does not break the streak
  (pg_temp.ts(3), 'Skip Between Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(2), 'Skip Between Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(1), 'Skip Between Co', 'lever', 0, 0, 'robots.txt does not allow it'),
  (pg_temp.ts(0), 'Skip Between Co', 'lever', 0, 0, 'request failed'),
  -- never good, and a note that only resembles a prefix
  (pg_temp.ts(1), 'Never Co', 'lever', 0, 0, 'robots.txt does not allow it'),
  (pg_temp.ts(1, '04'), 'Never Co', 'lever', 0, 0, 'the sharp drop was not held'),
  (pg_temp.ts(0), 'Never Co', 'lever', 0, 0, 'request failed'),
  -- a company not crawled for 10 days drops out; a row with no company is ignored
  (pg_temp.ts(10), 'Stale Co', 'lever', 0, 0, 'request failed'),
  (pg_temp.ts(0), null, 'lever', 0, 0, 'request failed');

-- Denial first. Signed out: a permission error on the table and the function, never an empty answer.
do $$
declare sql text;
begin
  foreach sql in array array[
    'select * from public.crawl_log',
    'select count(*) from public.crawl_log',
    'select * from public.admin_crawl_health()',
    'insert into public.crawl_log (company) values (''x'')',
    'update public.crawl_log set note = note',
    'delete from public.crawl_log',
    'truncate public.crawl_log'
  ] loop
    assert pg_temp.denied('anon', null, sql), 'anon was not refused: ' || sql;
  end loop;
  assert not has_function_privilege('anon', 'public.admin_crawl_health()', 'execute'), 'anon may execute the function';
  assert not has_function_privilege('public', 'public.admin_crawl_health()', 'execute'), 'public may execute the function';
  assert has_function_privilege('authenticated', 'public.admin_crawl_health()', 'execute'), 'authenticated cannot execute';
end $$;

-- Signed in and not listed (or unknown, or a token with no user): the table and the function answer with no row, and
-- nobody signed in can write. A non-admin still sees open jobs only, the public policy.
do $$
declare
  who uuid;
  sql text;
begin
  foreach who in array array['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid, null] loop
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.crawl_log') = 0, who || ' read the crawl log';
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.admin_crawl_health()') = 0,
      who || ' got rows from the function';
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.jobs where status = ''closed''') = 0,
      who || ' read closed jobs';
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.jobs') = 2, who || ' does not see the open jobs';
  end loop;
  foreach who in array array['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid] loop
    foreach sql in array array[
      'insert into public.crawl_log (company) values (''x'')',
      'update public.crawl_log set note = note',
      'delete from public.crawl_log',
      'truncate public.crawl_log'
    ] loop
      assert pg_temp.denied('authenticated', who, sql), who || ' was not refused: ' || sql;
    end loop;
  end loop;
end $$;

-- The listed admin reads the log and every job, and never the text of a posting.
do $$
declare sql text;
begin
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.crawl_log') = 28,
    'the admin does not read the whole crawl log';
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.jobs') = 4,
    'the admin does not read closed jobs';
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.jobs where status = ''closed''') = 2,
    'the admin does not read the closed jobs';
  foreach sql in array array[
    'select description from public.jobs',
    'select details from public.jobs',
    'select * from public.jobs',
    'select count(*) from public.jobs where description is not null',
    'select count(*) from public.jobs where details is not null'
  ] loop
    assert pg_temp.denied('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', sql), 'the admin read posting text: ' || sql;
  end loop;
  assert not has_column_privilege('authenticated', 'public.jobs', 'description', 'select'), 'description is granted';
  assert not has_column_privilege('authenticated', 'public.jobs', 'details', 'select'), 'details is granted';
  assert not has_column_privilege('anon', 'public.jobs', 'description', 'select'), 'description is granted to anon';
  assert not has_table_privilege('authenticated', 'public.crawl_log', 'insert'), 'authenticated may insert into the log';
  assert not has_table_privilege('anon', 'public.crawl_log', 'select'), 'anon may select the log';
  -- the function returns no text of a posting, in any column
  assert (select count(*) from jsonb_object_keys((select to_jsonb(h) from public.admin_crawl_health() h limit 1))) = 11,
    'the function has other columns than the eleven documented ones';
  assert not exists (
    select 1 from public.admin_crawl_health() h where to_jsonb(h)::text like '%secret%'
  ), 'posting text leaked through the function';
end $$;

-- The function itself: invoker (not definer), stable, a pinned search path.
do $$
begin
  assert (select not prosecdef and provolatile = 's' and proconfig = array['search_path=""']
          from pg_proc where oid = 'public.admin_crawl_health()'::regprocedure),
    'the function is not security invoker, stable, with an empty search path';
  assert (select proargnames from pg_proc where oid = 'public.admin_crawl_health()'::regprocedure)
    = array['company', 'last_crawl', 'last_good', 'last_ok', 'last_note', 'streak', 'failing', 'held', 'robots_skipped',
            'open_jobs', 'fetched_30d_ago']::text[], 'the output columns changed (the admin page depends on them)';
end $$;

-- Which companies have a row: the stale one and the row with no company are out.
do $$
begin
  assert pg_temp.health('Stale Co') is null, 'a company not crawled for 10 days is listed';
  assert pg_temp.health('Healthy Co') is not null and pg_temp.health('Never Co') is not null, 'a company is missing';
end $$;

-- Healthy: last result, no note, no streak, not failing, two open jobs (the third is closed), the crawl 31 days
-- ago fetched 7.
do $$
declare r jsonb := pg_temp.health('Healthy Co');
begin
  assert r->>'last_crawl' = pg_temp.ts(0), 'last_crawl: ' || (r->>'last_crawl');
  assert r->>'last_good' = pg_temp.ts(0), 'last_good';
  assert (r->>'last_ok')::boolean, 'last_ok';
  assert r->'last_note' = 'null'::jsonb, 'an empty note is null';
  assert (r->>'streak')::int = 0, 'streak';
  assert not (r->>'failing')::boolean and not (r->>'held')::boolean and not (r->>'robots_skipped')::boolean, 'flags';
  assert (r->>'open_jobs')::int = 2, 'open jobs: ' || (r->>'open_jobs');
  assert (r->>'fetched_30d_ago')::int = 7, 'fetched 30 days ago';
end $$;

-- Failing: three failed days after a good one, last good 3 days ago, no open job, a failed fetch 32 days ago is a zero.
do $$
declare r jsonb := pg_temp.health('Failing Co');
begin
  assert (r->>'streak')::int = 3, 'streak: ' || (r->>'streak');
  assert (r->>'failing')::boolean and not (r->>'held')::boolean and not (r->>'robots_skipped')::boolean, 'flags';
  assert not (r->>'last_ok')::boolean, 'last_ok';
  assert r->>'last_good' = pg_temp.ts(3), 'last_good: ' || (r->>'last_good');
  assert r->>'last_note' = 'HTTP 500', 'last_note';
  assert (r->>'open_jobs')::int = 0, 'a closed job is counted as open';
  assert (r->>'fetched_30d_ago')::int = 0, 'a failed fetch is a zero';
end $$;

-- One failed crawl is not failing, but the streak is 1.
do $$
declare r jsonb := pg_temp.health('One Off Co');
begin
  assert (r->>'streak')::int = 1 and not (r->>'failing')::boolean, 'one off: ' || r::text;
  assert r->'fetched_30d_ago' = 'null'::jsonb, 'no crawl 30 days ago is null';
end $$;

-- Two runs in a day: the last run decides, and the earlier day's failure is behind a good day.
do $$
declare r jsonb := pg_temp.health('Rerun Co');
begin
  assert (r->>'streak')::int = 0 and not (r->>'failing')::boolean and (r->>'last_ok')::boolean, 'rerun: ' || r::text;
  assert r->>'last_crawl' = pg_temp.ts(0, '09'), 'the last run of the day is the last crawl';
end $$;

-- Held: ok = 0 with the sharp drop note is held, not failing, and the streak counts the two held days. The fetched count
-- of a held crawl is the count of the crawl.
do $$
declare r jsonb := pg_temp.health('Held Co');
begin
  assert (r->>'held')::boolean and not (r->>'failing')::boolean and not (r->>'robots_skipped')::boolean, 'held: ' || r::text;
  assert (r->>'streak')::int = 2, 'held streak';
  assert (r->>'last_good') is null, 'a held company never had a good crawl';
end $$;

-- Robots.txt: skipped, not failing, not held; the skipped days are left out of the streak (one failed day before them).
do $$
declare r jsonb := pg_temp.health('Robots Co');
begin
  assert (r->>'robots_skipped')::boolean and not (r->>'failing')::boolean and not (r->>'held')::boolean, 'robots: ' || r::text;
  assert (r->>'streak')::int = 1, 'robots streak: ' || (r->>'streak');
end $$;

-- A skip between failures: the streak is the three failed days and it is failing (the last two real crawls failed).
do $$
declare r jsonb := pg_temp.health('Skip Between Co');
begin
  assert (r->>'streak')::int = 3 and (r->>'failing')::boolean and not (r->>'robots_skipped')::boolean, 'skip between: ' || r::text;
end $$;

-- Never good: no last good crawl. A note that only contains a prefix does not count as held; one that starts with the
-- robots prefix on the earlier day is left out of the streak.
do $$
declare r jsonb := pg_temp.health('Never Co');
begin
  assert r->'last_good' = 'null'::jsonb, 'never good';
  assert (r->>'failing')::boolean and not (r->>'held')::boolean, 'never: ' || r::text;
  assert (r->>'streak')::int = 2, 'never streak: ' || (r->>'streak');
end $$;

-- The service role does not need the function; the owner and the service role still read the whole log directly.
do $$
begin
  perform pg_temp.act_as('service_role', null);
  assert (select count(*) from public.crawl_log) = 28, 'the service role cannot read the log';
  reset role;
end $$;

-- Removing the admin closes everything again.
delete from public.admins;
do $$
begin
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.crawl_log') = 0,
    'a removed admin still reads the log';
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.admin_crawl_health()') = 0,
    'a removed admin still gets rows';
end $$;

rollback;

select 'all admin crawl health checks passed' as result;
