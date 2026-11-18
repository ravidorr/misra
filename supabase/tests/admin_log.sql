-- Tests for supabase/migrations/20261128100000_admin_log.sql (PLAN_2.md step 1.3): the columns and defaults, a signed-in
-- admin reads the log, nobody else sees a row, and no browser role can insert, update, delete or truncate, a listed admin
-- included. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/admin_log.sql
-- Ends with "all admin log checks passed". Plain SQL (assert), one transaction that is rolled back.

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

-- How many rows of the log the role sees.
create function pg_temp.rows_seen(role_name text, who uuid) returns bigint language plpgsql as $$
declare n bigint;
begin
  perform pg_temp.act_as(role_name, who);
  select count(*) into n from public.admin_log;
  reset role;
  return n;
end;
$$;

-- Columns, types and defaults: an identity key, a time that defaults to now, a required action, a nullable target.
do $$
begin
  assert (select array_agg(column_name::text order by ordinal_position) from information_schema.columns
          where table_schema = 'public' and table_name = 'admin_log') = array['id', 'ts', 'action', 'target'],
    'admin_log has other columns than id, ts, action, target';
  assert (select data_type = 'bigint' and is_identity = 'YES' and identity_generation = 'ALWAYS' and is_nullable = 'NO'
          from information_schema.columns where table_schema = 'public' and table_name = 'admin_log' and column_name = 'id'),
    'id is not bigint generated always as identity';
  assert (select data_type = 'timestamp with time zone' and is_nullable = 'NO' and column_default = 'now()'
          from information_schema.columns where table_schema = 'public' and table_name = 'admin_log' and column_name = 'ts'),
    'ts is not timestamptz not null default now()';
  assert (select data_type = 'text' and is_nullable = 'NO' and column_default is null
          from information_schema.columns where table_schema = 'public' and table_name = 'admin_log' and column_name = 'action'),
    'action is not text not null';
  assert (select data_type = 'text' and is_nullable = 'YES' and column_default is null
          from information_schema.columns where table_schema = 'public' and table_name = 'admin_log' and column_name = 'target'),
    'target is not nullable text';
  assert (select count(*) from pg_index where indrelid = 'public.admin_log'::regclass and indisprimary) = 1, 'no primary key';
  assert (select relrowsecurity from pg_class where oid = 'public.admin_log'::regclass), 'row-level security is off on admin_log';
  assert (select array_agg(policyname::text) from pg_policies where tablename = 'admin_log') = array['an admin reads the log'],
    'admin_log has other policies than the one select policy';
  assert (select cmd = 'SELECT' and roles = '{authenticated}' and qual = 'is_admin()' from pg_policies where tablename = 'admin_log'),
    'the policy is not a select for authenticated using is_admin()';
end $$;

-- The owner (and, below, the service role) writes rows the way the trigger and `admin-account` will. The time defaults
-- and the key is generated; a target is optional.
insert into public.admin_log (action, target) values ('company edited', 'Example Co'), ('account deleted', null);
do $$
begin
  assert (select count(*) from public.admin_log) = 2, 'the owner could not insert';
  assert (select count(*) from public.admin_log where ts > now() - interval '1 minute') = 2, 'ts does not default to now';
  assert (select count(distinct id) from public.admin_log) = 2, 'ids are not generated';
  assert (select count(*) from public.admin_log where target is null) = 1, 'target is not optional';
  begin
    insert into public.admin_log (action) values (null);
    assert false, 'a null action was accepted';
  exception when not_null_violation then null;
  end;
  begin
    insert into public.admin_log (id, action) values (100, 'x');
    assert false, 'an id was accepted from the writer';
  exception when generated_always then null;
  end;
end $$;

-- Grants: authenticated reads and nothing else; anon and public get nothing at all.
do $$
declare r text;
begin
  assert has_table_privilege('authenticated', 'public.admin_log', 'select'), 'authenticated cannot select';
  foreach r in array array['insert', 'update', 'delete', 'truncate', 'references', 'trigger'] loop
    assert not has_table_privilege('authenticated', 'public.admin_log', r), 'authenticated has ' || r;
  end loop;
  foreach r in array array['select', 'insert', 'update', 'delete', 'truncate', 'references', 'trigger'] loop
    assert not has_table_privilege('anon', 'public.admin_log', r), 'anon has ' || r;
    assert not has_table_privilege('public', 'public.admin_log', r), 'public has ' || r;
  end loop;
end $$;

-- Signed out: a permission error on every command, never an empty answer.
do $$
declare sql text;
begin
  foreach sql in array array[
    'select * from public.admin_log',
    'insert into public.admin_log (action) values (''x'')',
    'update public.admin_log set action = action',
    'delete from public.admin_log',
    'truncate public.admin_log'
  ] loop
    assert pg_temp.denied('anon', null, sql), 'anon was not refused: ' || sql;
  end loop;
end $$;

-- Signed in and listed, or signed in and not: nobody writes. A signed-in user who is not listed reads no row, and a
-- listed admin reads them all.
do $$
declare
  who uuid;
  sql text;
begin
  foreach who in array array['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid] loop
    foreach sql in array array[
      'insert into public.admin_log (action) values (''x'')',
      'update public.admin_log set action = action',
      'delete from public.admin_log',
      'truncate public.admin_log'
    ] loop
      assert pg_temp.denied('authenticated', who, sql), who || ' was not refused: ' || sql;
    end loop;
  end loop;
  assert pg_temp.rows_seen('authenticated', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 0, 'a user who is not listed read the log';
  assert pg_temp.rows_seen('authenticated', 'cccccccc-cccc-4ccc-8ccc-cccccccccccc') = 0, 'an unknown user read the log';
  assert pg_temp.rows_seen('authenticated', null) = 0, 'a signed-out token read the log';
  assert pg_temp.rows_seen('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 2, 'a listed admin cannot read the log';
  assert (select count(*) from public.admin_log) = 2, 'a refused statement changed the log';
end $$;

-- The service role writes (it is the key of `admin-account`, never in a browser) and bypasses row-level security.
do $$
begin
  perform pg_temp.act_as('service_role', null);
  insert into public.admin_log (action, target) values ('mail sent', 'accounts');
  assert (select count(*) from public.admin_log) = 3, 'the service role could not write and read the log';
  reset role;
end $$;

-- Removing the admin from the list closes the log to that account again.
delete from public.admins;
do $$
begin
  assert pg_temp.rows_seen('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'a removed admin still reads the log';
end $$;

rollback;

select 'all admin log checks passed' as result;
