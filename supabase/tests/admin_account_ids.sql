-- Tests for supabase/migrations/20261201100000_admin_account_ids.sql (PLAN_2.md step 5.2): the lookup behind the
-- `admin-account` function finds an account by an address (ignoring case, whole address only), answers ids and nothing
-- else, finds a digest address only when asked to, and may be run by the service role alone. Run it against a TEST
-- database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/admin_account_ids.sql
-- Ends with "all admin account lookup checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Ann.Login@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob@test.example'),
  ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'cy@test.example');
-- Ann has her own sign-in email on the digest; Bob has a different address on his; Cy has no subscription.
insert into public.subscriptions (user_id, address, unsubscribe_token_hash) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'ann.login@test.example', repeat('1', 64)),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob.digest@test.example', repeat('2', 64));

create function pg_temp.ids(p_address text, p_login_only boolean) returns text language sql as $$
  select coalesce(string_agg(left(i::text, 1), ',' order by i), '') from public.admin_account_ids(p_address, p_login_only) i;
$$;

do $$
begin
  -- The sign-in email, in any case; one id even when the digest address is the same (a union, not a join).
  assert pg_temp.ids('ann.login@test.example', false) = 'a', 'the sign-in email did not find Ann';
  assert pg_temp.ids('ANN.LOGIN@TEST.EXAMPLE', true) = 'a', 'the match is not case-insensitive';
  assert pg_temp.ids('bob@test.example', false) = 'b' and pg_temp.ids('bob@test.example', true) = 'b', 'Bob login';
  assert pg_temp.ids('cy@test.example', true) = 'c', 'an account with no subscription is not found';
  -- The digest address finds the account only when the caller does not ask for the sign-in email alone.
  assert pg_temp.ids('bob.digest@test.example', false) = 'b', 'the digest address did not find Bob';
  assert pg_temp.ids('bob.digest@test.example', true) = '', 'the digest address found an account for a deletion';
  -- Whole addresses only: no partial match, no pattern characters, no null.
  assert pg_temp.ids('bob', false) = '' and pg_temp.ids('%', false) = '' and pg_temp.ids('%@test.example', false) = '', 'a partial match';
  assert pg_temp.ids('b_b@test.example', false) = '', 'an underscore matched';
  assert pg_temp.ids('nobody@test.example', false) = '', 'an unknown address matched';
  assert (select count(*) from public.admin_account_ids(null, false)) = 0, 'null matched';
end $$;

-- The way the Edge Function calls it: as the service role (which bypasses row-level security and has no other grant on
-- these tables than the default), so the function works without the superuser's rights.
do $$
begin
  set local role service_role;
  assert (select count(*) from public.admin_account_ids('bob@test.example', true)) = 1, 'the service role could not run the lookup';
  assert (select count(*) from public.admin_account_ids('bob.digest@test.example', false)) = 1, 'the service role could not find a digest address';
  reset role;
end $$;

-- The result is uuids only: a single column of type uuid.
do $$
begin
  assert (select data_type from information_schema.routines where routine_schema = 'public' and routine_name = 'admin_account_ids') = 'uuid',
    'the lookup does not return uuid';
  assert not (select prosecdef from pg_proc where oid = 'public.admin_account_ids(text, boolean)'::regprocedure), 'the lookup is security definer';
end $$;

-- Only the service role may run it; no other role, a signed-in admin included.
insert into public.admins (user_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
do $$
declare r text;
begin
  assert has_function_privilege('service_role', 'public.admin_account_ids(text, boolean)', 'execute'), 'service_role cannot run it';
  foreach r in array array['anon', 'authenticated', 'public'] loop
    assert not has_function_privilege(r, 'public.admin_account_ids(text, boolean)', 'execute'), r || ' can run the lookup';
  end loop;
  perform set_config('request.jwt.claims', json_build_object('sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'role', 'authenticated')::text, true);
  perform set_config('request.jwt.claim.sub', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', true);
  perform set_config('role', 'authenticated', true);
  begin
    perform public.admin_account_ids('bob@test.example', false);
    assert false, 'a signed-in admin ran the lookup from a browser';
  exception when insufficient_privilege then null;
  end;
  reset role;
end $$;

rollback;
\echo all admin account lookup checks passed
