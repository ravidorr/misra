-- Tests for supabase/migrations/20261201100000_admin_alert_counts.sql (PLAN_2.md step 5.1): the denial first (a signed-out
-- visitor gets a permission error, a signed-in user who is not listed gets no row), then the counts a listed admin gets
-- on seeded subscriptions and emailed rows, and the shape of the answer (numbers only, no address and no user id). Run
-- it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/admin_alert_counts.sql
-- Ends with "all admin alert counts checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'admin@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'other@test.example'),
  ('00000001-0000-4000-8000-000000000001', 'u1@test.example'),
  ('00000002-0000-4000-8000-000000000002', 'u2@test.example'),
  ('00000003-0000-4000-8000-000000000003', 'u3@test.example'),
  ('00000004-0000-4000-8000-000000000004', 'u4@test.example'),
  ('00000005-0000-4000-8000-000000000005', 'u5@test.example'),
  ('00000006-0000-4000-8000-000000000006', 'u6@test.example');
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

-- What `admin_alert_counts()` returns for the role, as a jsonb array of its rows (empty when there is none).
create function pg_temp.counts_as(role_name text, who uuid) returns jsonb language plpgsql as $$
declare r jsonb;
begin
  perform pg_temp.act_as(role_name, who);
  select coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb) into r from public.admin_alert_counts() c;
  reset role;
  return r;
end;
$$;

-- The count of one kind, for the subscription kinds (day is null) or of 'emailed' for the day `n` days ago (UTC).
create function pg_temp.n_of(rows jsonb, k text, ago int default null) returns int language sql as $$
  select (e ->> 'n')::int
  from jsonb_array_elements(rows) e
  where e ->> 'kind' = k
    and (e ->> 'day') is not distinct from (case when ago is null then null else ((now() at time zone 'utc')::date - ago)::text end);
$$;

-- A moment `n` UTC days ago, at noon.
create function pg_temp.at(n int) returns timestamptz language sql as $$
  select ((now() at time zone 'utc')::date - n + time '12:00') at time zone 'utc';
$$;

-- Subscriptions: u1 waits for its confirmation, u2 and u3 are confirmed, u4 was confirmed and turned the email off, u5
-- turned it off before confirming. u6 has none.
insert into public.subscriptions (user_id, address, unsubscribe_token_hash, consent_confirmed_at, unsubscribed_at) values
  ('00000001-0000-4000-8000-000000000001', 'u1@test.example', repeat('1', 64), null, null),
  ('00000002-0000-4000-8000-000000000002', 'u2@test.example', repeat('2', 64), now(), null),
  ('00000003-0000-4000-8000-000000000003', 'u3@test.example', repeat('3', 64), now(), null),
  ('00000004-0000-4000-8000-000000000004', 'u4@test.example', repeat('4', 64), now(), now()),
  ('00000005-0000-4000-8000-000000000005', 'u5@test.example', repeat('5', 64), null, now());

-- Emailed: today two users (one with three jobs), yesterday one user with five jobs, six days ago one, and a user
-- seven days ago (outside the window), nobody three days ago.
insert into public.emailed (user_id, company, job_id, emailed_at) values
  ('00000002-0000-4000-8000-000000000002', 'A', '1', pg_temp.at(0)),
  ('00000002-0000-4000-8000-000000000002', 'A', '2', pg_temp.at(0)),
  ('00000002-0000-4000-8000-000000000002', 'A', '3', pg_temp.at(0)),
  ('00000003-0000-4000-8000-000000000003', 'A', '1', pg_temp.at(0)),
  ('00000002-0000-4000-8000-000000000002', 'B', '1', pg_temp.at(1)),
  ('00000002-0000-4000-8000-000000000002', 'B', '2', pg_temp.at(1)),
  ('00000002-0000-4000-8000-000000000002', 'B', '3', pg_temp.at(1)),
  ('00000002-0000-4000-8000-000000000002', 'B', '4', pg_temp.at(1)),
  ('00000002-0000-4000-8000-000000000002', 'B', '5', pg_temp.at(1)),
  ('00000003-0000-4000-8000-000000000003', 'C', '1', pg_temp.at(6)),
  ('00000002-0000-4000-8000-000000000002', 'D', '1', pg_temp.at(7));

-- Denial first. The function is closed to the public roles but the signed-in one.
do $$
begin
  assert not has_function_privilege('anon', 'public.admin_alert_counts()', 'execute'), 'anon can run it';
  assert not has_function_privilege('public', 'public.admin_alert_counts()', 'execute'), 'public can run it';
  assert has_function_privilege('authenticated', 'public.admin_alert_counts()', 'execute'), 'authenticated cannot run it';
  assert pg_temp.denied('anon', null, 'select * from public.admin_alert_counts()'), 'anon was not refused';
  -- the tables themselves stay closed to every browser role, the admin included
  assert pg_temp.denied('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select user_id from public.emailed'),
    'a listed admin reads emailed';
  -- (`subscriptions` is readable by its owner only: the admin is a stranger to every row, so it reads none)
  perform pg_temp.act_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  assert (select count(*) from public.subscriptions) = 0, 'a listed admin reads the subscriptions';
  reset role;
  assert pg_temp.counts_as('authenticated', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = '[]'::jsonb, 'a user who is not listed got rows';
  assert pg_temp.counts_as('authenticated', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc') = '[]'::jsonb, 'an unknown user got rows';
  assert pg_temp.counts_as('authenticated', null) = '[]'::jsonb, 'a token without a user got rows';
end $$;

-- A listed admin gets the numbers.
do $$
declare r jsonb := pg_temp.counts_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
begin
  assert jsonb_array_length(r) = 10, 'expected three subscription rows and seven days, got ' || jsonb_array_length(r);
  assert pg_temp.n_of(r, 'pending') = 1, 'pending';
  assert pg_temp.n_of(r, 'confirmed') = 2, 'confirmed';
  assert pg_temp.n_of(r, 'off') = 2, 'off';
  assert pg_temp.n_of(r, 'emailed', 0) = 2, 'today counts the users, not the three jobs';
  assert pg_temp.n_of(r, 'emailed', 1) = 1, 'yesterday counts the user, not the five jobs';
  assert pg_temp.n_of(r, 'emailed', 2) = 0, 'a day nobody was emailed is 0, not missing';
  assert pg_temp.n_of(r, 'emailed', 3) = 0, 'a day nobody was emailed is 0, not missing';
  assert pg_temp.n_of(r, 'emailed', 6) = 1, 'the oldest day of the window';
  assert pg_temp.n_of(r, 'emailed', 7) is null, 'a day outside the window is shown';
  assert (select array_agg(e ->> 'kind' order by ord) from jsonb_array_elements(r) with ordinality as t(e, ord))
    = array['confirmed', 'emailed', 'emailed', 'emailed', 'emailed', 'emailed', 'emailed', 'emailed', 'off', 'pending'],
    'the rows are not in the documented order';
  assert (select (e ->> 'day') from jsonb_array_elements(r) with ordinality as t(e, ord) where ord = 2)
    = ((now() at time zone 'utc')::date)::text, 'the days are not newest first';
end $$;

-- Numbers only: the answer has three columns and nothing in it is an address or a user id.
do $$
declare r jsonb := pg_temp.counts_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
begin
  assert pg_get_function_result('public.admin_alert_counts()'::regprocedure) = 'TABLE(kind text, day date, n integer)', 'other columns';
  assert not exists (select 1 from jsonb_array_elements(r) e, jsonb_object_keys(e) k where k not in ('kind', 'day', 'n')), 'an extra key';
  assert r::text !~* '@|test\.example|0000000|aaaaaaaa|bbbbbbbb', 'an address or a user id is in the answer';
end $$;

-- The count follows the data: turning the email off moves a user from confirmed to off, and the removed admin is shut out.
update public.subscriptions set unsubscribed_at = now() where user_id = '00000002-0000-4000-8000-000000000002';
do $$
declare r jsonb := pg_temp.counts_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
begin
  assert pg_temp.n_of(r, 'confirmed') = 1 and pg_temp.n_of(r, 'off') = 3, 'a user who turned the email off is still confirmed';
end $$;
delete from public.admins;
do $$
begin
  assert pg_temp.counts_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = '[]'::jsonb, 'a removed admin still gets the counts';
end $$;

rollback;

select 'all admin alert counts checks passed' as result;
