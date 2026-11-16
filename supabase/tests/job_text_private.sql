-- Tests for supabase/migrations/20261121100000_jobs_text_not_public.sql: the text of a posting (`description`) and its
-- `details` are not readable by anon or authenticated, every other column of `jobs` is, a column added later is closed
-- until it is granted, and the service role writes and reads both. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/job_text_private.sql
-- Ends with "all job text checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into public.jobs (company, job_id, title, status, description, details)
values ('Acme', 'text-1', 'Engineer', 'open', 'secret posting text', '{"employment_type": "Full-time"}');

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

do $$
declare
  who text;
  other text := 'company, job_id, title, team, location, url, posted, first_seen, last_seen, status, level, role_types, regions, locations';
begin
  foreach who in array array['anon', 'authenticated'] loop
    assert pg_temp.readable(who, other), who || ' cannot read the public columns';
    assert not pg_temp.readable(who, 'description'), who || ' can read description';
    assert not pg_temp.readable(who, 'details'), who || ' can read details';
    assert not pg_temp.readable(who, 'company, description'), who || ' can read description next to others';
    assert not pg_temp.readable(who, '*'), who || ' can select *';
    assert not pg_temp.readable(who, 'jobs'), who || ' can read the whole row';
    assert not pg_temp.readable(who, 'length(description)'), who || ' can read a value computed from description';
  end loop;
end $$;

-- Row-level security is unchanged: only open jobs, and only the public columns.
update public.jobs set status = 'closed' where job_id = 'text-1';
set local role anon;
do $$ begin assert (select count(*) from public.jobs where job_id = 'text-1') = 0, 'a closed job is visible'; end $$;
reset role;
update public.jobs set status = 'open' where job_id = 'text-1';

-- The service role writes and reads both columns.
set local role service_role;
update public.jobs set description = 'new text', details = '{"a": 1}' where job_id = 'text-1';
insert into public.jobs (company, job_id, status, description, details) values ('Acme', 'text-2', 'open', 'two', '{}');
do $$
begin
  assert (select description from public.jobs where job_id = 'text-1') = 'new text', 'service role cannot read description';
  assert (select details ->> 'a' from public.jobs where job_id = 'text-1') = '1', 'service role cannot read details';
end $$;
reset role;

-- A column added later is closed to anon and authenticated until a migration grants it.
alter table public.jobs add column later_column text;
do $$
declare who text;
begin
  foreach who in array array['anon', 'authenticated'] loop
    assert not pg_temp.readable(who, 'later_column'), who || ' can read a column nobody granted';
    assert pg_temp.readable(who, 'company'), who || ' lost a granted column';
  end loop;
end $$;

rollback;
\echo all job text checks passed
