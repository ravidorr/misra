-- Tests for supabase/migrations/20261205110000_user_jobs_company_choice.sql ("Company not for me" in the account).
--
-- Run it against a TEST project (or a local one), never production: it creates fake auth users and jobs, and although
-- everything happens in one transaction that ends with a rollback, a test project is the safe place for it.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/company_choices.sql
-- It stops at the first failed check with a message that names it, and ends with "all company choice checks passed".
--
-- Needs the migrations applied, that one included. Uses only plain SQL (assert), no pgTAP.

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

insert into public.jobs (company, job_id, status) values ('Acme', '1', 'open'), ('Acme', '2', 'open');

----------------------------------------------------------------------------------------------------------------------
-- A company choice is a row of its own: the state 'company', the company's name, an empty job id, nothing else kept.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '', 'company');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1', 'discard');
insert into public.user_jobs (user_id, company, job_id, state, title)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '2', 'star', 'Analyst');

do $$
begin
  assert (select count(*) from public.user_jobs) = 3, 'the user could not keep a company choice next to a discard and a star';

  -- one company choice per company, even by upsert
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '', 'company');
    assert false, 'a company was chosen twice';
  exception when unique_violation then null;
  end;
  insert into public.user_jobs (user_id, company, job_id, state)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '', 'company')
    on conflict (user_id, company, job_id) do nothing;
  assert (select count(*) from public.user_jobs where state = 'company') = 1, 'an upsert added a second company choice';

  -- the shape: a company choice has an empty job id, and nothing else has
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Beta', '7', 'company');
    assert false, 'a company choice with a job id was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Beta', '', 'star');
    assert false, 'a star with an empty job id was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '', '', 'company');
    assert false, 'a company choice with no company name was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', repeat('x', 201), '', 'company');
    assert false, 'a company name longer than 200 characters was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Beta', 'x', 'other');
    assert false, 'a state other than star, discard or company was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Beta', repeat('x', 201), 'star');
    assert false, 'a job id longer than 200 characters was accepted';
  exception when check_violation then null;
  end;
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Row-level security: a user reads and writes only their own company choices.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
do $$
declare
  changed integer;
begin
  assert (select count(*) from public.user_jobs where state = 'company') = 0, 'Bob can read the first account''s company choice';
  update public.user_jobs set company = 'Gone' where state = 'company';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob updated the first account''s company choice';
  delete from public.user_jobs where state = 'company';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob deleted the first account''s company choice';
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Evil', '', 'company');
    assert false, 'Bob wrote a company choice into the first account';
  exception when insufficient_privilege then null;
  end;
  -- his own works, and is his alone
  insert into public.user_jobs (user_id, company, job_id, state)
    values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Acme', '', 'company');
  assert (select count(*) from public.user_jobs) = 1, 'Bob does not see exactly his own company choice';
end $$;
reset role;

-- The anonymous role reads and writes nothing.
set role anon;
do $$
begin
  begin
    perform count(*) from public.user_jobs;
    assert false, 'the anonymous role can read user_jobs';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

-- Ann can restore (delete) her own company choice, and only that row goes.
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
delete from public.user_jobs where company = 'Acme' and job_id = '' and state = 'company';
do $$
begin
  assert (select count(*) from public.user_jobs) = 2, 'restoring the company removed more than the choice';
  assert (select count(*) from public.user_jobs where state = 'company') = 0, 'the company choice was not removed';
end $$;
insert into public.user_jobs (user_id, company, job_id, state)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '', 'company');
reset role;

----------------------------------------------------------------------------------------------------------------------
-- A company choice is kept when a role closes (only a discard of that role goes), and has no other expiry.
----------------------------------------------------------------------------------------------------------------------
update public.jobs set status = 'closed' where company = 'Acme' and job_id = '1';
update public.jobs set status = 'closed' where company = 'Acme' and job_id = '2';
do $$
begin
  assert (select count(*) from public.user_jobs where company = 'Acme' and state = 'company') = 2,
    'a company choice was removed when a role closed';
  assert not exists (select 1 from public.user_jobs where company = 'Acme' and job_id = '1'),
    'the discard of the closed role was kept';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- The cap: company choices count with stars and position choices, in the same 2,000.
----------------------------------------------------------------------------------------------------------------------
delete from public.user_jobs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_jobs (user_id, company, job_id, state)
  select 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap' || n, '', 'company' from generate_series(1, 1000) n;
insert into public.user_jobs (user_id, company, job_id, state, title)
  select 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', n::text, 'star', 'Kept' from generate_series(1, 1000) n;
do $$
begin
  assert (select count(*) from public.user_jobs) = 2000, 'the user could not fill the cap with company choices and stars';

  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Over', '', 'company');
    assert false, 'a 2001st row (a company choice) was accepted';
  exception when sqlstate '54000' then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap', '1001', 'star');
    assert false, 'a 2001st row (a star) was accepted';
  exception when sqlstate '54000' then null;
  end;
  assert (select count(*) from public.user_jobs) = 2000, 'a refused row was kept';

  -- at the cap, a company choice that exists can still be upserted, and restoring one makes room
  insert into public.user_jobs (user_id, company, job_id, state)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Cap1', '', 'company')
    on conflict (user_id, company, job_id) do nothing;
  assert (select count(*) from public.user_jobs) = 2000, 'an upsert of an existing company choice changed the count';
  delete from public.user_jobs where company = 'Cap1' and job_id = '';
  insert into public.user_jobs (user_id, company, job_id, state)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Over', '', 'company');
  assert (select count(*) from public.user_jobs) = 2000, 'restoring a company did not make room for another';
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Deleting the account removes its company choices with the rest (every row cascades from auth.users).
----------------------------------------------------------------------------------------------------------------------
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
do $$
begin
  assert not exists (select 1 from public.user_jobs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    'a company choice stayed behind after the account was deleted';
  assert (select count(*) from public.user_jobs where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1,
    'deleting one account touched another''s choices';
end $$;

rollback;

select 'all company choice checks passed' as result;
