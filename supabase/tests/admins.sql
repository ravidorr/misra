-- Tests for supabase/migrations/20261127100000_admins.sql (PLAN_2.md step 1.1): nobody but the owner reads or writes
-- `admins`, `is_admin()` is false for anon and for a signed-in user who is not listed and true for a listed one, and
-- removing the auth user removes the row. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/admins.sql
-- Ends with "all admin check passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'admin@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'other@test.example');
insert into public.admins (user_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');  -- as the owner, like the SQL editor

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

create function pg_temp.is_admin_as(role_name text, who uuid) returns boolean language plpgsql as $$
declare answer boolean;
begin
  perform pg_temp.act_as(role_name, who);
  select public.is_admin() into answer;
  reset role;
  return answer;
end;
$$;

-- The owner reads and writes `admins`, and row-level security is on, so a role that got a grant by mistake still sees nothing.
do $$
begin
  assert (select count(*) from public.admins) = 1, 'the owner cannot read admins';
  assert (select relrowsecurity from pg_class where oid = 'public.admins'::regclass), 'row-level security is off on admins';
  assert not exists (select 1 from pg_policies where tablename = 'admins'), 'admins has a policy';
end $$;

-- Signed out (anon) and signed in but not listed (authenticated): a permission error on every command, never an empty answer.
do $$
declare
  who uuid;
  r text;
  sql text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    who := case r when 'authenticated' then 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid end;
    foreach sql in array array[
      'select * from public.admins',
      'select user_id from public.admins',
      'insert into public.admins (user_id) values (''bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'')',
      'update public.admins set user_id = user_id',
      'delete from public.admins',
      'truncate public.admins'
    ] loop
      assert pg_temp.denied(r, who, sql), r || ' was not refused: ' || sql;
    end loop;
  end loop;
  -- even a listed user, signed in, cannot read or write the table: only the owner and the service role can
  assert pg_temp.denied('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select * from public.admins'), 'a listed admin read the table';
  assert pg_temp.denied('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'insert into public.admins (user_id) values (''bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'')'),
    'a listed admin added an admin';
  assert (select count(*) from public.admins) = 1, 'a refused statement changed admins';
end $$;

-- The service role bypasses row-level security: it is the other way in, and it is the key that never reaches a browser.
do $$
begin
  perform pg_temp.act_as('service_role', null);
  assert (select count(*) from public.admins) = 1, 'the service role cannot read admins';
  reset role;
end $$;

-- is_admin(): false for anon and for a signed-in user who is not listed, true for the listed one; callable by both roles.
do $$
begin
  assert not pg_temp.is_admin_as('anon', null), 'anon is an admin';
  assert not pg_temp.is_admin_as('authenticated', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), 'a user who is not listed is an admin';
  assert not pg_temp.is_admin_as('authenticated', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'), 'an unknown user is an admin';
  assert pg_temp.is_admin_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'), 'a listed user is not an admin';
  assert has_function_privilege('anon', 'public.is_admin()', 'execute'), 'anon cannot call is_admin';
  assert has_function_privilege('authenticated', 'public.is_admin()', 'execute'), 'authenticated cannot call is_admin';
  assert not has_function_privilege('public', 'public.is_admin()', 'execute'), 'public can call is_admin';
  assert (select prosecdef and provolatile = 's' and proconfig = array['search_path=""'] from pg_proc where oid = 'public.is_admin()'::regprocedure),
    'is_admin is not security definer, stable and pinned to an empty search path';
end $$;

-- Removing the auth user removes the admin row, so a deleted account is not an admin any more.
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
do $$
begin
  assert (select count(*) from public.admins) = 0, 'the admin row outlived the auth user';
end $$;

rollback;

select 'all admin check passed' as result;
