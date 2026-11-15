-- Tests for supabase/migrations/20261115180000_job_choices_cap_and_closed_jobs.sql (PLAN.md step 4.2; PRD FR-2).
--
-- Run it against a TEST project (or a local one), never production: it creates fake auth users and jobs, and although
-- everything happens in one transaction that ends with a rollback, a test project is the safe place for it.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/job_choices.sql
-- It stops at the first failed check with a message that names it, and ends with "all job choice checks passed".
--
-- Needs the three migrations applied. Uses only plain SQL (assert), no pgTAP.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'ann@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob@test.example');

-- Acting as a signed-in user: the role and the claims PostgREST sets from the user's token.
create function pg_temp.sign_in_as(who uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', json_build_object('sub', who, 'role', 'authenticated')::text, true);
  perform set_config('request.jwt.claim.sub', who::text, true);
  perform set_config('role', 'authenticated', true);
end;
$$;

----------------------------------------------------------------------------------------------------------------------
-- The cap: 2,000 rows per user.
----------------------------------------------------------------------------------------------------------------------
insert into public.jobs (company, job_id, status) select 'Cap', n::text, 'open' from generate_series(1, 2003) n;
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state)
  select 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', n::text, 'discard' from generate_series(1, 2000) n;

do $$
begin
  assert (select count(*) from public.user_jobs) = 2000, 'the user could not fill the cap exactly';

  -- one more job is refused, with the code the page tells apart from a network failure
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', '2001', 'discard');
    assert false, 'a 2001st row was accepted';
  exception when sqlstate '54000' then
    null;
  end;
  assert (select count(*) from public.user_jobs) = 2000, 'the refused row was kept';

  -- a user at the cap can still change what they already have: an update, and an upsert of an existing row
  update public.user_jobs set state = 'star', title = 'Kept' where company = 'Cap' and job_id = '1';
  insert into public.user_jobs (user_id, company, job_id, state, title)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', '2', 'star', 'Upserted')
    on conflict (user_id, company, job_id) do update set state = excluded.state, title = excluded.title;
  assert (select state from public.user_jobs where company = 'Cap' and job_id = '1') = 'star', 'a state change at the cap was refused';
  assert (select title from public.user_jobs where company = 'Cap' and job_id = '2') = 'Upserted', 'an upsert at the cap was refused';
  assert (select count(*) from public.user_jobs) = 2000, 'a change at the cap added a row';

  -- but not an upsert that would add a row
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', '2002', 'star')
      on conflict (user_id, company, job_id) do update set state = excluded.state;
    assert false, 'an upsert past the cap was accepted';
  exception when sqlstate '54000' then
    null;
  end;

  -- removing one frees one place
  delete from public.user_jobs where company = 'Cap' and job_id = '3';
  insert into public.user_jobs (user_id, company, job_id, state)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', '2003', 'star');
  assert (select count(*) from public.user_jobs) = 2000, 'freeing a place did not allow one more';
end $$;
reset role;

-- The cap is per user: another account is not affected by the first one being full.
select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Cap', '1', 'discard');
do $$
begin
  assert (select count(*) from public.user_jobs) = 1, 'the second account sees or counts rows that are not its own';
end $$;
reset role;

-- Start the next checks from a small table.
delete from public.user_jobs;

----------------------------------------------------------------------------------------------------------------------
-- FR-2 AC 10: a discard goes when its job closes, a star stays, and a job that reopens shows again.
----------------------------------------------------------------------------------------------------------------------
insert into public.jobs (company, job_id, title, status) values
  ('Acme', '1', 'Product Manager', 'open'),
  ('Acme', '2', 'Designer', 'open'),
  ('Acme', '3', 'Analyst', 'open');

select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state, title, location, url) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1', 'discard', null, null, null),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '2', 'star', 'Designer', 'Haifa', 'https://acme.test/2'),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '3', 'discard', null, null, null);
reset role;
select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Acme', '1', 'discard');
reset role;

-- The daily sync closes a job with an upsert of status = 'closed' (the service role, which bypasses row-level security).
update public.jobs set status = 'closed' where company = 'Acme' and job_id in ('1', '2');
do $$
begin
  assert not exists (select 1 from public.user_jobs where company = 'Acme' and job_id = '1'),
    'a discard stayed after its job closed (for one of the users)';
  assert (select state from public.user_jobs where company = 'Acme' and job_id = '2') = 'star', 'a star was removed when its job closed';
  assert (select title || ' ' || location || ' ' || url from public.user_jobs where company = 'Acme' and job_id = '2') = 'Designer Haifa https://acme.test/2',
    'the star lost its title, location or link';
  assert (select state from public.user_jobs where company = 'Acme' and job_id = '3') = 'discard', 'the discard of a job that is still open was removed';
end $$;

-- A job that reopens: nothing hides it any more.
update public.jobs set status = 'open' where company = 'Acme' and job_id = '1';
do $$
begin
  assert not exists (select 1 from public.user_jobs where company = 'Acme' and job_id = '1'), 'a discard came back when its job reopened';
end $$;

-- A job first written as closed (an upsert that inserts) clears the discards of that job as well.
insert into public.jobs (company, job_id, status) values ('Acme', '4', 'open');
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '4', 'discard');
reset role;
delete from public.jobs where company = 'Acme' and job_id = '4';
insert into public.jobs (company, job_id, status) values ('Acme', '4', 'closed');
do $$
begin
  assert not exists (select 1 from public.user_jobs where company = 'Acme' and job_id = '4'), 'a discard stayed after its job was written as closed';
end $$;

-- A stale tab or an old offline queue cannot leave a discard behind for a job that is already closed (or unknown) ...
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '4', 'discard'),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1', 'discard'),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Nowhere', '9', 'discard');
-- ... while a star of such a job is kept, and so is the discard of a job that is open.
insert into public.user_jobs (user_id, company, job_id, state, title)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '4', 'star', 'Gone Role')
  on conflict (user_id, company, job_id) do update set state = excluded.state;
do $$
begin
  assert (select state from public.user_jobs where company = 'Acme' and job_id = '4') = 'star', 'a star of a closed job was refused';
end $$;
-- Replacing a star with a discard of a closed job leaves no choice, not the old star.
insert into public.user_jobs (user_id, company, job_id, state, title)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '4', 'discard', null)
  on conflict (user_id, company, job_id) do update set state = excluded.state, title = excluded.title;
reset role;
do $$
begin
  assert not exists (select 1 from public.user_jobs where company = 'Acme' and job_id = '4'), 'a star came back after it was replaced by a discard of a closed job';
  assert not exists (select 1 from public.user_jobs where company = 'Nowhere'), 'a discard of an unknown job was kept';
  assert (select count(*) from public.user_jobs where company = 'Acme' and job_id = '1' and state = 'discard') = 1, 'the discard of an open job was refused';
end $$;

-- Editing an open job does not touch anyone's choices.
update public.jobs set title = 'Analyst II' where company = 'Acme' and job_id = '3';
do $$
begin
  assert (select count(*) from public.user_jobs where company = 'Acme' and job_id = '3') = 1, 'editing an open job removed a discard';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- Nobody but the database runs the trigger functions by hand.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  fn text;
  who text;
begin
  foreach fn in array array['public.limit_user_jobs()', 'public.drop_discards_of_closed_job()', 'public.skip_discard_of_closed_job()'] loop
    foreach who in array array['anon', 'authenticated'] loop
      assert not has_function_privilege(who, fn, 'execute'), who || ' can run ' || fn;
    end loop;
  end loop;
end $$;

rollback;

select 'all job choice checks passed' as result;
