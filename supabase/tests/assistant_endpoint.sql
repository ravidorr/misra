-- Tests for supabase/migrations/20261125100000_assistant_endpoint.sql: the rate-limit counter of the assistant endpoint
-- (assistant_reserve) is closed to the public roles and counts per key and day, the daily cleanup deletes it, and
-- search_jobs gives the service role the dashboard's search (open jobs in Israel, filters, words in the title or in the
-- text through search_job_keys) with the public columns only. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/assistant_endpoint.sql
-- Ends with "all assistant endpoint checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

-- ---- The counter -----------------------------------------------------------------------------------------------------

do $$
begin
  assert public.assistant_reserve('2026-11-17', 'h1', 2) = 'ok', 'first call refused';
  assert public.assistant_reserve('2026-11-17', 'h1', 2) = 'ok', 'second call refused';
  assert public.assistant_reserve('2026-11-17', 'h1', 2) = 'limit', 'third call allowed';
  assert public.assistant_reserve('2026-11-17', 'h1', 2) = 'limit', 'a refused call opened the limit';
  assert (select calls from public.assistant_ip_limits where ip_hash = 'h1') = 2, 'a refused call was counted';
  assert public.assistant_reserve('2026-11-17', 'h2', 2) = 'ok', 'another caller shares the limit';
  assert public.assistant_reserve('2026-11-18', 'h1', 2) = 'ok', 'a new day does not reset';
  assert public.assistant_reserve('2026-11-18', 'h3', 0) = 'limit', 'a limit of 0 allowed a call';
  assert public.assistant_reserve('2026-11-18', 'h5', null) = 'limit', 'a null limit allowed a call';
  -- rows older than yesterday go with the next call
  assert not exists (select 1 from public.assistant_ip_limits where day < '2026-11-17'), 'an old row stayed';
  perform public.assistant_reserve('2026-11-20', 'h4', 2);
  assert not exists (select 1 from public.assistant_ip_limits where day < '2026-11-19'), 'rows older than yesterday stayed';
end $$;

-- assistant_release gives a place back and never goes below zero.
do $$
begin
  perform public.assistant_reserve('2026-11-21', 'r1', 1);
  assert public.assistant_reserve('2026-11-21', 'r1', 1) = 'limit', 'the place was free';
  assert public.assistant_release('2026-11-21', 'r1') = 'ok', 'release refused';
  assert public.assistant_reserve('2026-11-21', 'r1', 1) = 'ok', 'a released place was not free';
  perform public.assistant_release('2026-11-21', 'r1');
  perform public.assistant_release('2026-11-21', 'r1');
  assert (select calls from public.assistant_ip_limits where ip_hash = 'r1') = 0, 'release went below zero';
end $$;

-- The daily cleanup (which the sync step calls as report_site_cleanup) deletes the assistant counters of earlier days
-- along with the report counters, and counts them.
do $$
declare removed integer;
begin
  insert into public.report_ip_limits (day, ip_hash, reports) values ('2020-01-01', 'old', 1);
  insert into public.assistant_ip_limits (day, ip_hash, calls) values ('2020-01-01', 'old', 1), (current_date, 'today', 1);
  removed := public.report_site_cleanup(current_date);
  assert removed >= 2, 'cleanup counted ' || removed;
  assert not exists (select 1 from public.assistant_ip_limits where day < current_date), 'cleanup left an old counter';
  assert exists (select 1 from public.assistant_ip_limits where ip_hash = 'today'), 'cleanup deleted today''s counter';
  begin
    perform public.report_site_cleanup(current_date + 1);
    assert false, 'a day in the future was accepted';
  exception when sqlstate '22023' then null;
  end;
end $$;

-- Nobody but the service role reaches the counter or the functions.
do $$
declare who text;
begin
  foreach who in array array['anon', 'authenticated'] loop
    execute format('set local role %I', who);
    begin
      perform count(*) from public.assistant_ip_limits;
      assert false, who || ' read the counters';
    exception when insufficient_privilege then null;
    end;
    begin
      perform public.assistant_reserve(current_date, 'x', 5);
      assert false, who || ' ran assistant_reserve';
    exception when insufficient_privilege then null;
    end;
    begin
      perform public.assistant_release(current_date, 'x');
      assert false, who || ' ran assistant_release';
    exception when insufficient_privilege then null;
    end;
    begin
      perform public.search_jobs('rust', null, null, null, 5, 0);
      assert false, who || ' ran search_jobs';
    exception when insufficient_privilege then null;
    end;
    reset role;
  end loop;
end $$;

-- ---- The search --------------------------------------------------------------------------------------------------------

delete from public.jobs;
insert into public.jobs (company, job_id, title, team, location, url, first_seen, status, description, snippet, level, role_types, regions, locations) values
  ('Acme', 'a1', 'Junior Rust Developer', 'Platform', 'Tel Aviv', 'https://acme.example/a1', '2026-11-10', 'open', 'We build rockets with Postgres', 'We build rockets', 'Junior', '{R&D}', '{Tel Aviv area}', '{}'),
  ('Acme', 'a2', 'Product Intern', '', 'Haifa', 'https://acme.example/a2', '2026-11-16', 'open', 'Own the roadmap and write specs', 'Own the roadmap', 'Intern', '{Product}', '{"Haifa and the north"}', '{}'),
  ('Beta', 'b1', 'Office Manager', null, 'Jerusalem', 'https://beta.example/b1', '2026-11-16', 'open', 'Rust is not needed. Rockets neither.', 'Rust is not needed', 'Not specified', '{}', '{Jerusalem}', '{}'),
  ('Beta', 'b2', 'Junior Rust Developer', null, 'Berlin', null, '2026-11-12', 'open', 'Rust in Berlin', 'Rust in Berlin', 'Junior', '{R&D}', '{}', '{}'),
  ('Beta', 'b3', 'Junior Rust Developer', null, 'Eilat', null, '2026-11-12', 'open', null, null, 'Junior', '{R&D}', '{}', '{Eilat}'),
  ('Gamma', 'g1', 'Junior Rust Developer', null, 'Tel Aviv', null, '2026-11-12', 'closed', 'Rust', 'Rust', 'Junior', '{R&D}', '{Tel Aviv area}', '{}');

create function pg_temp.find(query text, levels text[] default null, types text[] default null,
                              regions text[] default null, lim integer default 20, skip integer default 0)
returns jsonb language plpgsql as $$
declare found jsonb;
begin
  set local role service_role;
  select public.search_jobs(query, levels, types, regions, lim, skip) into found;
  reset role;
  return found;
end;
$$;

create function pg_temp.ids(found jsonb) returns text language sql as $$
  select coalesce(string_agg(j ->> 'job_id', ',' order by ord), '') from jsonb_array_elements(found -> 'jobs') with ordinality as t(j, ord);
$$;

do $$
begin
  -- no filter: open jobs in Israel only (b2 has no region or place, g1 is closed), newest first, then by company
  assert pg_temp.ids(pg_temp.find('')) = 'a2,b1,b3,a1', 'unfiltered: ' || pg_temp.ids(pg_temp.find(''));
  assert (pg_temp.find('') ->> 'total') = '4', 'total of the unfiltered search';
  assert pg_temp.ids(pg_temp.find(null)) = 'a2,b1,b3,a1', 'a null query is the empty query';
  -- b3 is in Israel by a place and has no text
  assert pg_temp.ids(pg_temp.find('eilat')) = 'b3', 'a word of the location';
  -- every word, in the title, company, team or location, in any case
  assert pg_temp.ids(pg_temp.find('JUNIOR  rust')) = 'b3,a1', 'words in the title: ' || pg_temp.ids(pg_temp.find('JUNIOR  rust'));
  assert pg_temp.ids(pg_temp.find('platform acme')) = 'a1', 'words in the team and the company';
  -- the full text counts through search_job_keys: b1's title has no "rust" but its text does; a2's does not
  assert pg_temp.ids(pg_temp.find('rust')) = 'b1,b3,a1', 'text match: ' || pg_temp.ids(pg_temp.find('rust'));
  assert pg_temp.ids(pg_temp.find('rockets')) = 'b1,a1', 'text match for a word that is in no title';
  -- a word shorter than 3 matches the title only (the text search ignores it), as on the dashboard
  assert pg_temp.ids(pg_temp.find('tel')) = 'a1', 'a short word matches the title';
  -- filters: any of each, combined
  assert pg_temp.ids(pg_temp.find('', array['Intern'])) = 'a2', 'level';
  assert pg_temp.ids(pg_temp.find('', array['Intern', 'Not specified'])) = 'a2,b1', 'two levels';
  assert pg_temp.ids(pg_temp.find('', null, array['Product'])) = 'a2', 'role type';
  assert pg_temp.ids(pg_temp.find('', null, array['Other'])) = 'b1', 'a job with no role type counts as Other';
  assert pg_temp.ids(pg_temp.find('', null, array['Product', 'Other'])) = 'a2,b1', 'two role types';
  assert pg_temp.ids(pg_temp.find('', null, null, array['Jerusalem', 'Tel Aviv area'])) = 'b1,a1', 'regions';
  assert pg_temp.ids(pg_temp.find('', array['Junior'], array['R&D'], array['Tel Aviv area'])) = 'a1', 'filters combine';
  assert pg_temp.ids(pg_temp.find('', array['Nope'])) = '', 'a level nobody has finds nothing';
  assert pg_temp.ids(pg_temp.find('', array[]::text[])) = 'a2,b1,b3,a1', 'an empty list means every value';
  -- paging: the total stays, the page is cut, a limit is at least 1 and at most 50, an offset is not negative
  assert pg_temp.ids(pg_temp.find('', lim => 2)) = 'a2,b1' and (pg_temp.find('', lim => 2) ->> 'total') = '4', 'limit';
  assert pg_temp.ids(pg_temp.find('', lim => 2, skip => 2)) = 'b3,a1', 'offset';
  assert pg_temp.ids(pg_temp.find('', lim => 2, skip => 9)) = '' and (pg_temp.find('', lim => 2, skip => 9) ->> 'total') = '4', 'past the end';
  assert pg_temp.ids(pg_temp.find('', lim => 0)) = 'a2', 'a limit of 0 gives one';
  assert pg_temp.ids(pg_temp.find('', lim => -5, skip => -3)) = 'a2', 'negative numbers are clamped';
  assert pg_temp.ids(pg_temp.find('', lim => null, skip => null)) = 'a2,b1,b3,a1', 'null limit and offset use the defaults';
  assert (pg_temp.find('', lim => 1000) -> 'jobs') is not null, 'a big limit';
end $$;

-- Only the columns the dashboard reads come back, and never the text or the details.
do $$
declare one jsonb;
begin
  one := pg_temp.find('postgres') -> 'jobs' -> 0;
  assert (select array_agg(k order by k collate "C") from jsonb_object_keys(one) k) =
    array['company', 'first_seen', 'job_id', 'level', 'location', 'locations', 'regions', 'role_types', 'snippet', 'team', 'title', 'url'],
    'columns: ' || (select string_agg(k, ',' order by k collate "C") from jsonb_object_keys(one) k);
  assert one::text not like '%Postgres%', 'the text leaked';
  assert one ->> 'snippet' = 'We build rockets', 'the snippet is missing';
  assert (pg_temp.find('office') -> 'jobs' -> 0 -> 'role_types') = '["Other"]'::jsonb, 'an empty role type list is shown as Other';
end $$;

-- The text of a closed job is not searched, and the function never opens the text to the public roles.
do $$
begin
  assert pg_temp.ids(pg_temp.find('gamma')) = '', 'a closed job was found';
  begin
    set local role anon;
    perform description from public.jobs limit 1;
    assert false, 'anon read the description';
  exception when insufficient_privilege then null;
  end;
  reset role;
end $$;

rollback;

\echo all assistant endpoint checks passed
