-- Tests for supabase/migrations/20261126100000_jobs_snippet_cut.sql: `snippet_cut` is readable by anon and authenticated
-- (the text and its details still are not, also next to it), only for open jobs, and the migration's backfill rule marks a
-- cut snippet true and a whole one false. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/job_snippet_cut.sql
-- Ends with "all snippet_cut checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into public.jobs (company, job_id, title, url, first_seen, last_seen, status, description, snippet, snippet_cut) values
  ('Acme', 'cut',   'Cut',   'u1', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open',   'word word word', 'word word', true),
  ('Acme', 'whole', 'Whole', 'u2', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open',   'word word', 'word word', false),
  ('Acme', 'old',   'Old',   'u3', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open',   'word', 'word', null),
  ('Acme', 'gone',  'Gone',   'u4', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'closed', 'a b', 'a', true);

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
declare who text;
begin
  foreach who in array array['anon', 'authenticated'] loop
    assert pg_temp.readable(who, 'snippet_cut'), who || ' cannot read snippet_cut';
    assert pg_temp.readable(who, 'company, snippet, snippet_cut'), who || ' cannot read snippet_cut with the public columns';
    assert not pg_temp.readable(who, 'snippet_cut, description'), who || ' can read description next to snippet_cut';
    assert not pg_temp.readable(who, 'snippet_cut, details'), who || ' can read details next to snippet_cut';
    assert not pg_temp.readable(who, '*'), who || ' can select *';
  end loop;
end $$;

-- Row-level security is unchanged: only open jobs are visible, with their flags as stored.
set local role anon;
do $$
begin
  assert (select count(*) from public.jobs where company = 'Acme' and snippet_cut is not null) = 2, 'anon sees a flag of a job that is not open';
  assert (select snippet_cut from public.jobs where job_id = 'cut'), 'the cut flag is not true';
  assert not (select snippet_cut from public.jobs where job_id = 'whole'), 'the whole flag is not false';
  assert (select snippet_cut from public.jobs where job_id = 'old') is null, 'a row with no flag is not null';
end $$;
reset role;

-- The migration's backfill, as written there, on rows stored before the column: shorter than the trimmed text, the whole
-- text, white space around, no snippet and no text.
insert into public.jobs (company, job_id, title, url, first_seen, last_seen, status, description, snippet) values
  ('Backfill', 'cut',      'a', 'u', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open', E'  word word word \n', 'word word'),
  ('Backfill', 'whole',    'b', 'u', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open', E' word word\n', 'word word'),
  ('Backfill', 'nosnip',   'c', 'u', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open', 'a' || repeat('b', 300), null),
  ('Backfill', 'notext',   'd', 'u', '2025-01-01T00:00:00', '2025-01-02T00:00:00', 'open', null, 'word');
update public.jobs set snippet_cut = length(snippet) < length(regexp_replace(description, '^\s+|\s+$', '', 'g')) where snippet is not null and description is not null;
do $$
begin
  assert (select snippet_cut from public.jobs where company = 'Backfill' and job_id = 'cut'), 'backfill: cut is not true';
  assert not (select snippet_cut from public.jobs where company = 'Backfill' and job_id = 'whole'), 'backfill: whole is not false';
  assert (select snippet_cut from public.jobs where company = 'Backfill' and job_id = 'nosnip') is null, 'backfill: no snippet is not null';
  assert (select snippet_cut from public.jobs where company = 'Backfill' and job_id = 'notext') is null, 'backfill: no text is not null';
end $$;

rollback;
\echo all snippet_cut checks passed
