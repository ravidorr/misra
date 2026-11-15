-- Policy tests for supabase/migrations/20261115120000_accounts_prefs_and_job_choices.sql (PLAN.md step 4.1).
--
-- Run it against a TEST project (or a local one), never production: it creates two fake auth users, and although
-- everything happens in one transaction that ends with a rollback, a test project is the safe place for it.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/accounts_rls.sql
--   (or paste it into the SQL editor of the test project; or `supabase test db --linked` for a linked test project)
-- It stops at the first failed check with a message that names it, and ends with "all account policy checks passed".
--
-- Needs the jobs migration and the accounts migration applied. Uses only plain SQL (assert), no pgTAP.

begin;

-- Two accounts, as Google sign-in would create them: the provider's name and photo arrive in raw_user_meta_data.
insert into auth.users (id, email, raw_user_meta_data) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'ann@test.example',
   '{"full_name": "Ann Example", "name": "Ann Example", "avatar_url": "https://photos.test/ann.png", "picture": "https://photos.test/ann.png", "sub": "1111"}'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob@test.example',
   '{"full_name": "Bob Example", "avatar_url": "https://photos.test/bob.png", "sub": "2222"}');

-- The jobs the choices below are about (a discard of a job that is not open is skipped by a trigger).
insert into public.jobs (company, job_id, status) values ('Acme', '1', 'open'), ('Acme', '2', 'open');

-- Acting as a signed-in user: the role and the claims PostgREST sets from the user's token.
create function pg_temp.sign_in_as(who uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', json_build_object('sub', who, 'role', 'authenticated')::text, true);
  perform set_config('request.jwt.claim.sub', who::text, true);
  perform set_config('role', 'authenticated', true);
end;
$$;

----------------------------------------------------------------------------------------------------------------------
-- FR-1 AC 6: the profile keeps the email and nothing else.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  stored jsonb;
begin
  assert (select count(*) from public.profiles where user_id in ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')) = 2, 'the trigger did not create a profile for each new user';
  select to_jsonb(p) into stored from public.profiles p where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  assert stored = '{"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "email": "ann@test.example"}'::jsonb,
    'the profile holds more than the user id and the email: ' || stored::text;
  assert not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'profiles' and column_name not in ('user_id', 'email')
  ), 'public.profiles has a column besides user_id and email';
  assert (select count(*) from public.profiles where to_jsonb(profiles)::text ilike '%Ann Example%') = 0,
    'the provider name reached public.profiles';
  assert (select count(*) from public.profiles where to_jsonb(profiles)::text ilike '%photos.test%') = 0,
    'the provider photo reached public.profiles';
end $$;

-- FR-1 AC 6, the access token: the hook Supabase Auth runs empties the user_metadata claim (the name and photo) and
-- keeps the rest, and only Supabase Auth may run it. (Whether it is switched on in the dashboard is a manual check.)
do $$
declare
  event jsonb := '{"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "authentication_method": "oauth", "claims": {
    "sub": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "email": "ann@test.example", "role": "authenticated", "aal": "aal1",
    "app_metadata": {"provider": "google"}, "user_metadata": {"full_name": "Ann Example", "avatar_url": "https://photos.test/ann.png"}}}';
  result jsonb := public.custom_access_token_hook(event);
begin
  assert result #> '{claims,user_metadata}' = '{}'::jsonb, 'the hook left user_metadata in the token: ' || result::text;
  assert result::text not ilike '%Ann Example%' and result::text not ilike '%photos.test%', 'the name or photo is still in the token';
  assert (result - 'claims') = (event - 'claims'), 'the hook changed more than the claims';
  assert (result -> 'claims') - 'user_metadata' = (event -> 'claims') - 'user_metadata', 'the hook changed a claim other than user_metadata';
  assert has_function_privilege('supabase_auth_admin', 'public.custom_access_token_hook(jsonb)', 'execute'), 'Supabase Auth cannot run the hook';
  assert not has_function_privilege('anon', 'public.custom_access_token_hook(jsonb)', 'execute'), 'anon can run the hook';
  assert not has_function_privilege('authenticated', 'public.custom_access_token_hook(jsonb)', 'execute'), 'authenticated can run the hook';
end $$;

-- A changed email follows; a cleared email is ignored instead of failing.
update auth.users set email = 'ann.new@test.example' where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
update auth.users set email = null where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
do $$
begin
  assert (select email from public.profiles where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 'ann.new@test.example',
    'a changed email did not reach the profile';
  assert (select email from public.profiles where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 'bob@test.example',
    'a cleared email replaced or removed the profile email';
end $$;
update auth.users set email = 'bob@test.example' where id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

----------------------------------------------------------------------------------------------------------------------
-- The setup that the checks below need: each user has rows of their own, written as that user.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
insert into public.user_prefs (user_id, filters) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '{"q": "ann"}');
insert into public.user_jobs (user_id, company, job_id, state, title, location, url)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1', 'star', 'Product Manager', 'Tel Aviv', 'https://acme.test/1');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '2', 'discard');
reset role;

select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
insert into public.user_prefs (user_id, filters) values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', '{"q": "bob"}');
insert into public.user_jobs (user_id, company, job_id, state)
  values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Acme', '1', 'discard');
reset role;

----------------------------------------------------------------------------------------------------------------------
-- FR-1 AC 3: a user sees and changes only their own information. Bob (the second account) first.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
do $$
declare
  changed integer;
begin
  -- reading
  assert (select count(*) from public.user_jobs) = 1, 'Bob can read job choices that are not his own';
  assert (select count(*) from public.user_jobs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0,
    'Bob can read the first account''s job choices';
  assert (select count(*) from public.user_prefs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0,
    'Bob can read the first account''s preferences';
  assert (select count(*) from public.profiles) = 1, 'Bob can read a profile that is not his own';
  assert (select email from public.profiles) = 'bob@test.example', 'Bob does not see his own profile';

  -- changing the first account's rows touches nothing
  update public.user_jobs set state = 'discard' where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob updated the first account''s job choices';
  delete from public.user_jobs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob deleted the first account''s job choices';
  update public.user_prefs set filters = '{"q": "pwned"}' where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob updated the first account''s preferences';
  delete from public.user_prefs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  get diagnostics changed = row_count;
  assert changed = 0, 'Bob deleted the first account''s preferences';
end $$;

do $$
begin
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Evil', '9', 'star');
    assert false, 'Bob wrote a job choice for the first account';
  exception when insufficient_privilege then null;
  end;
  begin  -- the check is reached because Bob's own row is fine and the id is not his: use a fresh id, not Ann's taken one
    insert into public.user_prefs (user_id) values ('dddddddd-dddd-4ddd-8ddd-dddddddddddd');
    assert false, 'Bob wrote preferences for another account';
  exception when insufficient_privilege then null;
  end;
  begin  -- handing his own row to the first account is refused by the update policy's check
    update public.user_jobs set user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' where company = 'Acme';
    assert false, 'Bob moved his job choice into the first account';
  exception when insufficient_privilege then null;
  end;
  begin  -- nobody writes a profile, not even their own
    update public.profiles set email = 'evil@test.example';
    assert false, 'a user changed a profile email';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.profiles (user_id, email) values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'x@test.example');
    assert false, 'a user wrote a profile';
  exception when insufficient_privilege then null;
  end;
  begin
    delete from public.profiles;
    assert false, 'a user deleted a profile';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

-- The first account sees only its own rows too, and can change them.
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
do $$
declare
  changed integer;
begin
  assert (select count(*) from public.user_jobs) = 2, 'Ann does not see exactly her two job choices';
  assert (select count(*) from public.user_jobs where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 0,
    'Ann can read the second account''s job choices';
  assert (select filters ->> 'q' from public.user_prefs) = 'ann', 'Ann does not see exactly her own preferences';
  update public.user_jobs set state = 'discard' where company = 'Acme' and job_id = '1';
  get diagnostics changed = row_count;
  assert changed = 1, 'Ann cannot change her own job choice';
  assert (select state from public.user_jobs where job_id = '1') = 'discard', 'the change did not stick';
  update public.user_prefs set filters = '{"q": "ann2"}';
  get diagnostics changed = row_count;
  assert changed = 1, 'Ann cannot change her own preferences';
  delete from public.user_jobs where job_id = '2';
  get diagnostics changed = row_count;
  assert changed = 1, 'Ann cannot delete her own job choice';
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- FR-2 AC 2 (one state per job) and the limits.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
do $$
begin
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1', 'star');
    assert false, 'a job can have two rows for one user: it could be starred and discarded at once';
  exception when unique_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '3', 'both');
    assert false, 'a state other than star or discard was accepted';
  exception when check_violation then null;
  end;
  begin
    update public.user_prefs set filters = '[1, 2]';
    assert false, 'filters that are not a JSON object were accepted';
  exception when check_violation then null;
  end;
  begin
    update public.user_prefs set email_settings = jsonb_build_object('x', repeat('x', 5000));
    assert false, 'oversized email settings were accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state, url)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '4', 'star', repeat('x', 2001));
    assert false, 'an oversized url was accepted';
  exception when check_violation then null;
  end;
  begin
    insert into public.user_jobs (user_id, company, job_id, state, url)
      values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '5', 'star', 'javascript:alert(1)');
    assert false, 'a url that is not http(s) was accepted';
  exception when check_violation then null;
  end;
  insert into public.user_jobs (user_id, company, job_id, state, updated_at)
    values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '6', 'star', '2000-01-01');
  assert (select updated_at from public.user_jobs where job_id = '6') > '2020-01-01', 'a client back-dated updated_at on insert';
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Nothing for the anonymous role, and no way to run the trigger functions by hand.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  t text;
  p text;
begin
  foreach t in array array['profiles', 'user_prefs', 'user_jobs'] loop
    foreach p in array array['select', 'insert', 'update', 'delete', 'truncate', 'references', 'trigger'] loop
      assert not has_table_privilege('anon', 'public.' || t, p), 'anon has ' || p || ' on ' || t;
    end loop;
  end loop;
  foreach t in array array['profiles', 'user_prefs', 'user_jobs'] loop
    foreach p in array array['truncate', 'references', 'trigger'] loop
      assert not has_table_privilege('authenticated', 'public.' || t, p), 'authenticated has ' || p || ' on ' || t;
    end loop;
  end loop;
  assert not has_table_privilege('authenticated', 'public.profiles', 'insert'), 'authenticated can insert profiles';
  assert not has_table_privilege('authenticated', 'public.profiles', 'update'), 'authenticated can update profiles';
  assert not has_table_privilege('authenticated', 'public.profiles', 'delete'), 'authenticated can delete profiles';
  assert not has_function_privilege('anon', 'public.sync_profile()', 'execute'), 'anon can run sync_profile';
  assert not has_function_privilege('authenticated', 'public.sync_profile()', 'execute'), 'authenticated can run sync_profile';
  assert not has_function_privilege('anon', 'public.touch_updated_at()', 'execute'), 'anon can run touch_updated_at';
  assert not has_function_privilege('authenticated', 'public.touch_updated_at()', 'execute'), 'authenticated can run touch_updated_at';
  assert not exists (select 1 from pg_tables where schemaname = 'public' and not rowsecurity),
    'a table in public has row-level security off';
end $$;

set local role anon;
do $$
begin
  begin
    perform 1 from public.user_jobs;
    assert false, 'anon can read user_jobs';
  exception when insufficient_privilege then null;
  end;
  begin
    perform 1 from public.profiles;
    assert false, 'anon can read profiles';
  exception when insufficient_privilege then null;
  end;
  begin
    insert into public.user_prefs (user_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
    assert false, 'anon can write user_prefs';
  exception when insufficient_privilege then null;
  end;
  perform 1 from public.jobs;  -- the public job list stays readable
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Deleting the auth user removes the user's rows in one step (step 4.4), and only theirs.
----------------------------------------------------------------------------------------------------------------------
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
do $$
begin
  assert (select count(*) from public.profiles where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'profile left behind';
  assert (select count(*) from public.user_prefs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'preferences left behind';
  assert (select count(*) from public.user_jobs where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'job choices left behind';
  assert (select count(*) from public.user_jobs where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1, 'the other account lost rows';
  assert (select count(*) from public.profiles where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1
    and (select count(*) from public.user_prefs where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1,
    'the other account lost a profile or preferences';
end $$;

rollback;

select 'all account policy checks passed' as result;
