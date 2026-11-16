-- Tests for supabase/migrations/20261122100000_jobs_snippet.sql and 20261123100000_jobs_text_trim.sql: the snippet is
-- readable by anon and authenticated (the text and its details still are not), and jobs_text_trim deletes the text of a
-- closed job more than 30 days after it closed, keeps the row, and may be run by the service role alone. Run it against a
-- TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/job_snippet_and_trim.sql
-- Ends with "all snippet and trim checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

-- last_seen is the text the crawl writes ("YYYY-MM-DDTHH:MM:SS"): today minus N days, so the test does not depend on the date.
create function pg_temp.seen(days integer) returns text language sql as
  $$ select to_char(current_date - days, 'YYYY-MM-DD') || 'T05:00:00' $$;

insert into public.jobs (company, job_id, title, url, first_seen, last_seen, status, description, details, snippet) values
  ('Acme', 'open',             'Open',       'u1', '2025-01-01T00:00:00', pg_temp.seen(400), 'open',   'text', '{"text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-29',        'Closed 29',  'u2', '2025-01-01T00:00:00', pg_temp.seen(29),  'closed', 'text', '{"text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-30',        'Closed 30',  'u3', '2025-01-01T00:00:00', pg_temp.seen(30),  'closed', 'text', '{"text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-31',        'Closed 31',  'u4', '2025-01-01T00:00:00', pg_temp.seen(31),  'closed', 'text', '{"text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-old',       'Closed old', 'u5', '2025-01-01T00:00:00', pg_temp.seen(300), 'closed', 'text', '{"employment_type": "Full-time", "text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-null-date', 'No date',    'u6', null,                  null,              'closed', 'text', '{"text_sha256": "h"}', 'snip'),
  ('Acme', 'closed-no-text',   'No text',    'u7', null,                  pg_temp.seen(300), 'closed', null, null, null);

create function pg_temp.readable(role_name text, columns text) returns boolean language plpgsql as $$
begin
  execute format('set local role %I', role_name);
  begin
    execute format('select %s from public.jobs', columns);
    reset role;
    return true;
  exception when insufficient_privilege then
    reset role;
    return false;
  end;
end;
$$;

-- The snippet is public, the text and the details are not, also next to the snippet.
do $$
declare who text;
begin
  foreach who in array array['anon', 'authenticated'] loop
    assert pg_temp.readable(who, 'snippet'), who || ' cannot read snippet';
    assert pg_temp.readable(who, 'company, title, url, snippet'), who || ' cannot read snippet with the public columns';
    assert not pg_temp.readable(who, 'description'), who || ' can read description';
    assert not pg_temp.readable(who, 'details'), who || ' can read details';
    assert not pg_temp.readable(who, 'snippet, description'), who || ' can read description next to snippet';
    assert not pg_temp.readable(who, 'snippet, details'), who || ' can read details next to snippet';
    assert not pg_temp.readable(who, '*'), who || ' can select *';
    assert not pg_temp.readable(who, 'length(description)'), who || ' can read a value computed from description';
  end loop;
end $$;

-- Row-level security is unchanged: the snippet of a closed job is not visible either.
set local role anon;
do $$
begin
  assert (select count(*) from public.jobs where snippet is not null) = 1, 'anon sees a snippet that is not of an open job';
end $$;
reset role;

-- The trim is closed to the public roles (execute is revoked from public, anon and authenticated).
do $$
declare who text;
begin
  foreach who in array array['anon', 'authenticated'] loop
    assert not has_function_privilege(who, 'public.jobs_text_trim(date)', 'execute'), who || ' can execute jobs_text_trim';
  end loop;
  assert has_function_privilege('service_role', 'public.jobs_text_trim(date)', 'execute'), 'service_role cannot execute it';
end $$;
set local role anon;
do $$
begin
  perform public.jobs_text_trim(date '2026-11-16');
  assert false, 'anon ran jobs_text_trim';
exception when insufficient_privilege then null;
end $$;
reset role;
set local role authenticated;
do $$
begin
  perform public.jobs_text_trim(date '2026-11-16');
  assert false, 'authenticated ran jobs_text_trim';
exception when insufficient_privilege then null;
end $$;
reset role;

-- A wrong day is refused: null, and a day later than today (a wrong date can never trim early).
set local role service_role;
do $$
begin
  begin
    perform public.jobs_text_trim(null);
    assert false, 'null day accepted';
  exception when sqlstate '22023' then null;
  end;
  begin
    perform public.jobs_text_trim(current_date + 1);
    assert false, 'future day accepted';
  exception when sqlstate '22023' then null;
  end;
end $$;

-- last_seen 29 and 30 days ago are not trimmed (a job closed more than 30 days ago is); 31 days, 300 days and no date are.
reset role;
set local role service_role;
do $$
declare n integer;
begin
  n := public.jobs_text_trim(current_date);
  -- closed-31, closed-old and closed-null-date lose their text; closed-29, closed-30, open and closed-no-text do not.
  assert n = 3, 'the trim reported ' || n || ' jobs, expected 3';
  assert public.jobs_text_trim(current_date) = 0, 'a second run trimmed something again';
end $$;
reset role;

do $$
declare
  r record;
begin
  for r in select * from public.jobs where job_id in ('open', 'closed-29', 'closed-30') loop
    assert r.description = 'text' and r.snippet = 'snip' and r.details is not null, r.job_id || ' lost its text too early';
  end loop;
  for r in select * from public.jobs where job_id in ('closed-31', 'closed-old', 'closed-null-date') loop
    assert r.description is null and r.snippet is null and r.details is null, r.job_id || ' kept some text';
    assert r.title is not null and r.url is not null and r.company = 'Acme' and r.status = 'closed', r.job_id || ' lost its row data';
  end loop;
  -- the dates are kept
  assert (select first_seen from public.jobs where job_id = 'closed-old') = '2025-01-01T00:00:00', 'first_seen changed';
  assert (select last_seen from public.jobs where job_id = 'closed-old') = pg_temp.seen(300), 'last_seen changed';
  -- the fingerprint went with the details: a reopened job has none, so the sync writes its text again
  assert (select details ->> 'text_sha256' from public.jobs where job_id = 'closed-31') is null, 'the fingerprint was kept';
end $$;

rollback;
\echo all snippet and trim checks passed
