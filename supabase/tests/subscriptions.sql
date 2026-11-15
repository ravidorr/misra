-- Tests for supabase/migrations/20261117100000_subscriptions_and_emailed.sql (PLAN.md step 6.1; PRD FR-8 AC 7).
--
-- Run it against a TEST project (or a local one), never production: it creates two fake auth users, and although
-- everything happens in one transaction that ends with a rollback, a test project is the safe place for it.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/subscriptions.sql
-- It stops at the first failed check with a message that names it, and ends with "all subscription checks passed".
--
-- Needs the jobs, accounts and subscriptions migrations applied. Uses only plain SQL (assert), no pgTAP.
-- Hashes below are 64 hex characters, as the Edge Functions send them (SHA-256 of the raw token).

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

-- What a trusted function (the service role) writes when a user turns the email on.
insert into public.subscriptions (user_id, address, frequency, filters, confirm_token_hash, confirm_token_expires_at, unsubscribe_token_hash, consent_given_at)
values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'ann@test.example', 'weekly', '{"levels": ["Intern"]}',
   repeat('a', 64), timestamptz '2030-01-08 10:00+00', repeat('1', 64), timestamptz '2030-01-01 10:00+00'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob@test.example', 'daily', '{"regions": ["Haifa"]}',
   repeat('b', 64), timestamptz '2030-01-08 10:00+00', repeat('2', 64), timestamptz '2030-01-01 10:00+00');
insert into public.emailed (user_id, company, job_id) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Acme', '1'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Acme', '1');

----------------------------------------------------------------------------------------------------------------------
-- The shape of the rows: bad values are refused by the table itself.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
begin
  begin update public.subscriptions set frequency = 'hourly' where user_id = ann; assert false, 'a frequency other than daily or weekly was accepted';
  exception when check_violation then null; end;
  begin update public.subscriptions set filters = '[]' where user_id = ann; assert false, 'filters that are not an object were accepted';
  exception when check_violation then null; end;
  begin update public.subscriptions set filters = jsonb_build_object('q', repeat('x', 17000)) where user_id = ann; assert false, 'oversized filters were accepted';
  exception when check_violation then null; end;
  begin update public.subscriptions set address = 'not an address' where user_id = ann; assert false, 'a malformed address was accepted';
  exception when check_violation then null; end;
  begin update public.subscriptions set address = 'a@b.c' || chr(10) || 'bcc: x@y.z' where user_id = ann; assert false, 'an address with a line break was accepted';
  exception when check_violation then null; end;
  begin update public.subscriptions set confirm_token_hash = 'plain-token' where user_id = ann; assert false, 'a raw token was accepted where a hash belongs';
  exception when check_violation then null; end;
  begin update public.subscriptions set unsubscribe_token_hash = repeat('2', 64) where user_id = ann; assert false, 'two subscriptions share an unsubscribe token';
  exception when unique_violation then null; end;
  begin update public.subscriptions set unsubscribed_at = now() where user_id = ann; assert false, 'an unsubscribed row kept its filters';
  exception when check_violation then null; end;
  begin insert into public.emailed (user_id, company, job_id) values (ann, 'Acme', '1'); assert false, 'a job was recorded as emailed twice';
  exception when unique_violation then null; end;
  begin insert into public.emailed (user_id, company, job_id) values (ann, '', '2'); assert false, 'an empty company was accepted';
  exception when check_violation then null; end;
  assert (select updated_at from public.subscriptions where user_id = ann) >= now() - interval '1 minute', 'updated_at is not kept by the trigger';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- The signed-in user: reads own row without the token hashes, changes only frequency and filters, nothing else.
----------------------------------------------------------------------------------------------------------------------
select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  changed integer;
  seen record;
begin
  -- reading: own row only, named columns only
  select user_id, address, frequency, filters, consent_given_at, consent_confirmed_at, unsubscribed_at, created_at, updated_at
    into seen from public.subscriptions;
  assert seen.user_id = ann and seen.address = 'ann@test.example' and seen.frequency = 'weekly', 'the user cannot read their own subscription';
  assert (select count(*) from public.subscriptions) = 1, 'a user can read a subscription that is not their own';
  begin perform confirm_token_hash from public.subscriptions; assert false, 'the user can read the confirmation token hash';
  exception when insufficient_privilege then null; end;
  begin perform unsubscribe_token_hash from public.subscriptions; assert false, 'the user can read the unsubscribe token hash';
  exception when insufficient_privilege then null; end;
  begin perform confirm_token_expires_at from public.subscriptions; assert false, 'the user can read the token expiry';
  exception when insufficient_privilege then null; end;
  begin perform * from public.subscriptions; assert false, 'select * works for a user, so a token hash is readable';
  exception when insufficient_privilege then null; end;

  -- what the user may change
  update public.subscriptions set frequency = 'daily', filters = '{"levels": ["Junior"]}' where user_id = ann;
  get diagnostics changed = row_count;
  assert changed = 1, 'the user cannot change their own frequency and filters';
  assert (select filters from public.subscriptions) = '{"levels": ["Junior"]}'::jsonb, 'the filters were not saved';

  -- what the user may not change: the address, the consent log, the tokens, the key
  begin update public.subscriptions set address = 'victim@test.example'; assert false, 'the user can change the address';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set consent_confirmed_at = now(); assert false, 'the user can confirm themselves';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set consent_given_at = now(); assert false, 'the user can rewrite consent_given_at';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set unsubscribed_at = null; assert false, 'the user can change unsubscribed_at';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set confirm_token_hash = repeat('c', 64); assert false, 'the user can set the confirmation token';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set unsubscribe_token_hash = repeat('c', 64); assert false, 'the user can set the unsubscribe token';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'; assert false, 'the user can move the row to another user';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set filters = '{}' where confirm_token_hash is not null; assert false, 'a token column can be used in a where clause by the user';
  exception when insufficient_privilege then null; end;

  -- no new rows (not even their own) and no deleting the consent log
  begin
    insert into public.subscriptions (user_id, address, unsubscribe_token_hash) values (ann, 'ann@test.example', repeat('9', 64));
    assert false, 'the user can insert a subscription';
  exception when insufficient_privilege then null; end;
  begin delete from public.subscriptions; assert false, 'the user can delete the subscription and its consent log';
  exception when insufficient_privilege then null; end;

  -- the second user's row is out of reach even for the two writable columns
  update public.subscriptions set frequency = 'weekly' where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  get diagnostics changed = row_count;
  assert changed = 0, 'a user changed another user''s subscription';

  -- emailed is not theirs at all
  begin perform 1 from public.emailed; assert false, 'the user can read emailed';
  exception when insufficient_privilege then null; end;
  begin insert into public.emailed (user_id, company, job_id) values (ann, 'Acme', '9'); assert false, 'the user can write emailed';
  exception when insufficient_privilege then null; end;
  begin perform public.confirm_subscription(repeat('a', 64), now()); assert false, 'the user can run confirm_subscription';
  exception when insufficient_privilege then null; end;
  begin perform public.unsubscribe_subscription(repeat('1', 64), now()); assert false, 'the user can run unsubscribe_subscription';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

-- A user with a subscription that is not theirs to see: Bob sees only his.
select pg_temp.sign_in_as('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
do $$
begin
  assert (select count(*) from public.subscriptions) = 1 and (select address from public.subscriptions) = 'bob@test.example',
    'the second user does not see exactly their own subscription';
end $$;
reset role;

-- The anonymous role has nothing.
set local role anon;
do $$
begin
  begin perform 1 from public.subscriptions; assert false, 'anon can read subscriptions';
  exception when insufficient_privilege then null; end;
  begin update public.subscriptions set frequency = 'daily'; assert false, 'anon can update subscriptions';
  exception when insufficient_privilege then null; end;
  begin perform 1 from public.emailed; assert false, 'anon can read emailed';
  exception when insufficient_privilege then null; end;
  begin perform public.confirm_subscription(repeat('a', 64), now()); assert false, 'anon can run confirm_subscription';
  exception when insufficient_privilege then null; end;
  begin perform public.unsubscribe_subscription(repeat('1', 64), now()); assert false, 'anon can run unsubscribe_subscription';
  exception when insufficient_privilege then null; end;
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Confirming (as the service role, which is who the Edge Function is).
----------------------------------------------------------------------------------------------------------------------
set local role service_role;
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  bob constant uuid := 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  first_time constant timestamptz := timestamptz '2030-01-02 09:00+00';
begin
  assert public.confirm_subscription(repeat('f', 64), first_time) = 'unknown', 'an unknown token was not "unknown"';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) is null, 'unconfirmed rows must start without a confirmation';

  -- an expired link does not confirm
  assert public.confirm_subscription(repeat('a', 64), timestamptz '2030-01-08 10:00+00') = 'expired', 'a link at its expiry still confirmed';
  assert public.confirm_subscription(repeat('a', 64), timestamptz '2031-01-01 00:00+00') = 'expired', 'an expired link confirmed';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) is null, 'an expired link left a confirmation';

  -- a good link confirms once and keeps the first time; a replay is not an error and changes nothing
  assert public.confirm_subscription(repeat('a', 64), first_time) = 'confirmed', 'a valid link did not confirm';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) = first_time, 'the confirmation time is wrong';
  assert public.confirm_subscription(repeat('a', 64), timestamptz '2030-01-03 09:00+00') = 'already_confirmed', 'a replay was not "already confirmed"';
  assert public.confirm_subscription(repeat('a', 64), timestamptz '2032-01-03 09:00+00') = 'already_confirmed', 'a replay after the expiry was not "already confirmed"';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) = first_time, 'a replay moved the confirmation time';

  -- confirming one user touches nothing of another's
  assert (select consent_confirmed_at from public.subscriptions where user_id = bob) is null, 'confirming one user confirmed another';

  -- a row with no expiry never confirms
  update public.subscriptions set confirm_token_expires_at = null where user_id = bob;
  assert public.confirm_subscription(repeat('b', 64), first_time) = 'expired', 'a token without an expiry confirmed';
  update public.subscriptions set confirm_token_expires_at = timestamptz '2030-01-08 10:00+00' where user_id = bob;
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- A new address needs a new confirmation.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  stored public.subscriptions%rowtype;
begin
  -- a change of address that brings no new token drops the confirmation and the old token
  update public.subscriptions set address = 'ann.new@test.example' where user_id = ann;
  select * into stored from public.subscriptions where user_id = ann;
  assert stored.consent_confirmed_at is null, 'a new address kept the old confirmation';
  assert stored.confirm_token_hash is null and stored.confirm_token_expires_at is null, 'a new address kept the token that went to the old address';
  assert public.confirm_subscription(repeat('a', 64), timestamptz '2030-01-02 09:00+00') = 'unknown', 'the old token still confirms';

  -- one that brings a new token keeps it
  update public.subscriptions set address = 'ann.third@test.example', confirm_token_hash = repeat('d', 64),
    confirm_token_expires_at = timestamptz '2030-02-01 00:00+00' where user_id = ann;
  select * into stored from public.subscriptions where user_id = ann;
  assert stored.confirm_token_hash = repeat('d', 64) and stored.consent_confirmed_at is null, 'the new token was dropped or the address stayed confirmed';
  assert public.confirm_subscription(repeat('d', 64), timestamptz '2030-01-20 00:00+00') = 'confirmed', 'the new token did not confirm the new address';

  -- a change that leaves the address alone keeps the confirmation
  update public.subscriptions set frequency = 'daily' where user_id = ann;
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) is not null, 'a frequency change dropped the confirmation';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- Unsubscribing: stops sending, keeps the audit record, clears the choices, and a replay is not an error.
----------------------------------------------------------------------------------------------------------------------
set local role service_role;
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  bob constant uuid := 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  first_time constant timestamptz := timestamptz '2030-03-01 08:00+00';
  stored public.subscriptions%rowtype;
begin
  update public.subscriptions set filters = '{"levels": ["Junior"]}', frequency = 'weekly' where user_id = ann;
  assert public.unsubscribe_subscription(repeat('f', 64), first_time) = 'unknown', 'an unknown unsubscribe token was not "unknown"';
  assert public.unsubscribe_subscription(repeat('a', 64), first_time) = 'unknown', 'a confirmation token unsubscribed someone';

  assert public.unsubscribe_subscription(repeat('1', 64), first_time) = 'unsubscribed', 'a valid unsubscribe token did not unsubscribe';
  select * into stored from public.subscriptions where user_id = ann;
  assert stored.unsubscribed_at = first_time, 'the unsubscribe time is wrong';
  assert stored.filters = '{}'::jsonb and stored.frequency = 'daily', 'the choices were not cleared';
  assert stored.confirm_token_hash is null and stored.confirm_token_expires_at is null, 'the confirmation token was kept';
  -- the audit record stays
  assert stored.address = 'ann.third@test.example' and stored.consent_given_at = timestamptz '2030-01-01 10:00+00'
    and stored.consent_confirmed_at = timestamptz '2030-01-20 00:00+00' and stored.unsubscribe_token_hash = repeat('1', 64),
    'the audit record (address, consent timestamps, unsubscribe token) was not kept';
  assert not exists (select 1 from public.emailed where user_id = ann), 'the emailed list was kept';
  assert exists (select 1 from public.emailed where user_id = bob), 'unsubscribing one user cleared another''s emailed list';

  -- a replay keeps the first time, is not an error, and still finishes the clean-up
  insert into public.emailed (user_id, company, job_id) values (ann, 'Late', '1');
  assert public.unsubscribe_subscription(repeat('1', 64), timestamptz '2030-03-05 08:00+00') = 'already_unsubscribed', 'a replay was not "already unsubscribed"';
  assert (select unsubscribed_at from public.subscriptions where user_id = ann) = first_time, 'a replay moved the unsubscribe time';
  assert not exists (select 1 from public.emailed where user_id = ann), 'a replay did not clear the emailed list';

  -- the confirmation link of an unsubscribed user can never turn sending back on
  assert public.confirm_subscription(repeat('d', 64), timestamptz '2030-03-06 00:00+00') = 'unknown', 'the old confirmation link works after an unsubscribe';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) = timestamptz '2030-01-20 00:00+00', 'the confirmation changed after an unsubscribe';

  -- even with a token still present (as if a new one was issued without clearing the flag), an unsubscribed row never confirms
  update public.subscriptions set confirm_token_hash = repeat('e', 64), confirm_token_expires_at = timestamptz '2031-01-01 00:00+00', consent_confirmed_at = null where user_id = ann;
  assert public.confirm_subscription(repeat('e', 64), timestamptz '2030-03-06 00:00+00') = 'unsubscribed', 'an unsubscribed row was confirmed';
  assert (select consent_confirmed_at from public.subscriptions where user_id = ann) is null, 'an unsubscribed row got a confirmation';

  -- a user cannot put choices back on an unsubscribed row either
  assert (select count(*) from public.subscriptions where user_id = bob and unsubscribed_at is null) = 1, 'Bob should still be subscribed';
end $$;
reset role;

select pg_temp.sign_in_as('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
do $$
begin
  begin update public.subscriptions set filters = '{"levels": ["Intern"]}'; assert false, 'a user saved filters on an unsubscribed subscription';
  exception when check_violation then null; end;
end $$;
reset role;

----------------------------------------------------------------------------------------------------------------------
-- Deleting the auth user removes the subscription, its consent log and the emailed list, and only that user's.
----------------------------------------------------------------------------------------------------------------------
delete from auth.users where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
do $$
begin
  assert (select count(*) from public.subscriptions where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'the subscription was left behind';
  assert (select count(*) from public.emailed where user_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') = 0, 'the emailed list was left behind';
  assert (select count(*) from public.subscriptions where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1
    and (select count(*) from public.emailed where user_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb') = 1,
    'the other account lost rows';
end $$;

rollback;

select 'all subscription checks passed' as result;
