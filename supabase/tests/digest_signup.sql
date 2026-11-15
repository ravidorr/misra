-- Tests for supabase/migrations/20261118100000_digest_signup_and_limits.sql (PLAN.md step 6.2; PRD FR-8 AC 1 to 3).
--
-- Run it against a TEST project (or a local one), never production: it creates fake auth users and writes to the
-- counter tables, and although everything happens in one transaction that ends with a rollback, a test project is the
-- safe place for it.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/digest_signup.sql
-- It stops at the first failed check with a message that names it, and ends with "all digest signup checks passed".
--
-- Needs the jobs, accounts, subscriptions and digest signup migrations applied. Uses only plain SQL (assert), no pgTAP.
-- Hashes below are 64 hex characters, as the Edge Functions send them.

begin;
set local plpgsql.check_asserts = on;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'ann@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'bob@test.example'),
  ('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'cy@test.example');

----------------------------------------------------------------------------------------------------------------------
-- subscribe_start: turning the email on, and starting again.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  r public.subscriptions%rowtype;
  first_confirm text := repeat('a', 64);
begin
  -- A new subscription: the choices, the two hashes and the expiry are stored; nothing is confirmed.
  assert public.subscribe_start(ann, 'ann@test.example', 'weekly', '{"regions": ["Haifa"], "keywords": "growth"}',
    first_confirm, timestamptz '2030-01-09 10:00+00', repeat('1', 64), timestamptz '2030-01-02 10:00+00') = 'started',
    'a new subscription did not start';
  select * into r from public.subscriptions where user_id = ann;
  assert r.address = 'ann@test.example' and r.frequency = 'weekly', 'the address or frequency was not stored';
  assert r.filters = '{"regions": ["Haifa"], "keywords": "growth"}'::jsonb, 'the filters snapshot was not stored';
  assert r.confirm_token_hash = first_confirm and r.unsubscribe_token_hash = repeat('1', 64), 'the hashes were not stored';
  assert r.confirm_token_expires_at = timestamptz '2030-01-09 10:00+00', 'the expiry was not stored';
  assert r.consent_given_at = timestamptz '2030-01-02 10:00+00', 'consent_given_at is not the time passed in';
  assert r.consent_confirmed_at is null and r.unsubscribed_at is null, 'a new subscription is already confirmed';

  -- Asking again while waiting is a new link: the hashes are replaced, so the earlier link stops working, and nothing
  -- is confirmed.
  assert public.subscribe_start(ann, 'ann@test.example', 'daily', '{}', repeat('b', 64),
    timestamptz '2030-01-10 10:00+00', repeat('2', 64), timestamptz '2030-01-03 10:00+00') = 'started',
    'a second request while waiting was refused';
  select * into r from public.subscriptions where user_id = ann;
  assert r.confirm_token_hash = repeat('b', 64) and r.unsubscribe_token_hash = repeat('2', 64), 'the hashes were not replaced';
  assert public.confirm_subscription(first_confirm, timestamptz '2030-01-03 11:00+00') = 'unknown',
    'the earlier link still confirms';
  assert r.consent_given_at = timestamptz '2030-01-03 10:00+00' and r.frequency = 'daily', 'the new choices were not stored';

  -- Confirmed and running: the same address (in any case) leaves everything exactly as it is.
  assert public.confirm_subscription(repeat('b', 64), timestamptz '2030-01-03 12:00+00') = 'confirmed', 'confirm failed';
  insert into public.emailed (user_id, company, job_id) values (ann, 'Acme', '1');
  assert public.subscribe_start(ann, 'ANN@test.example', 'weekly', '{"levels": ["Intern"]}', repeat('c', 64),
    timestamptz '2030-01-11 10:00+00', repeat('3', 64), timestamptz '2030-01-04 10:00+00') = 'already_on',
    'a running email was restarted';
  select * into r from public.subscriptions where user_id = ann;
  assert r.consent_confirmed_at = timestamptz '2030-01-03 12:00+00' and r.confirm_token_hash = repeat('b', 64)
    and r.unsubscribe_token_hash = repeat('2', 64) and r.frequency = 'daily' and r.filters = '{}'::jsonb,
    'asking for the running address changed the row';

  -- A new address is a new consent: confirmation cleared, new hashes, and the sent-jobs list is kept.
  assert public.subscribe_start(ann, 'ann.new@test.example', 'daily', '{}', repeat('d', 64),
    timestamptz '2030-01-12 10:00+00', repeat('4', 64), timestamptz '2030-01-05 10:00+00') = 'started',
    'a change of address did not start';
  select * into r from public.subscriptions where user_id = ann;
  assert r.address = 'ann.new@test.example' and r.consent_confirmed_at is null and r.confirm_token_hash = repeat('d', 64),
    'a new address kept the old confirmation';
  assert (select count(*) from public.emailed where user_id = ann) = 1, 'starting again erased the sent-jobs list';

  -- After an unsubscribe, turning the email on again starts a new consent cycle.
  perform public.confirm_subscription(repeat('d', 64), timestamptz '2030-01-05 11:00+00');
  assert public.unsubscribe_user(ann, timestamptz '2030-01-06 10:00+00') = 'unsubscribed', 'unsubscribe failed';
  assert public.subscribe_start(ann, 'ann.new@test.example', 'weekly', '{"role_types": ["Product"]}', repeat('e', 64),
    timestamptz '2030-01-13 10:00+00', repeat('5', 64), timestamptz '2030-01-07 10:00+00') = 'started',
    'turning on again after an unsubscribe did not start';
  select * into r from public.subscriptions where user_id = ann;
  assert r.unsubscribed_at is null and r.consent_confirmed_at is null and r.frequency = 'weekly'
    and r.filters = '{"role_types": ["Product"]}'::jsonb and r.consent_given_at = timestamptz '2030-01-07 10:00+00',
    'a restart after an unsubscribe is not a clean new consent';
  assert public.confirm_subscription(repeat('d', 64), timestamptz '2030-01-07 11:00+00') = 'unknown',
    'a link from before the unsubscribe confirms again';

  -- The table's own checks still hold for what the function writes.
  begin
    perform public.subscribe_start(ann, 'x@test.example', 'hourly', '{}', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'a bad frequency was accepted';
  exception when check_violation then null; end;
  begin
    perform public.subscribe_start(ann, 'not an address', 'daily', '{}', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'a bad address was accepted';
  exception when check_violation then null; end;
  begin
    perform public.subscribe_start(ann, 'x@test.example', 'daily', '[]', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'filters that are not an object were accepted';
  exception when check_violation then null; end;
  begin
    perform public.subscribe_start(ann, 'x@test.example', 'daily', '{}', 'plain-token', now(), repeat('6', 64), now());
    assert false, 'a raw token was accepted where a hash belongs';
  exception when check_violation then null; end;
  begin
    perform public.subscribe_start(gen_random_uuid(), 'x@test.example', 'daily', '{}', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'a subscription for a user that does not exist was accepted';
  exception when foreign_key_violation then null; end;
end $$;

----------------------------------------------------------------------------------------------------------------------
-- unsubscribe_user does exactly what the unsubscribe link does.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  bob constant uuid := 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  cy constant uuid := 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  a public.subscriptions%rowtype;
  b public.subscriptions%rowtype;
begin
  -- Two identical confirmed subscriptions with sent jobs: one is turned off by the signed-in user, one by its link.
  perform public.subscribe_start(bob, 'same@test.example', 'weekly', '{"levels": ["Junior"]}', repeat('7', 64),
    timestamptz '2030-02-09 10:00+00', repeat('8', 64), timestamptz '2030-02-02 10:00+00');
  perform public.subscribe_start(cy, 'same@test.example', 'weekly', '{"levels": ["Junior"]}', repeat('9', 64),
    timestamptz '2030-02-09 10:00+00', repeat('0', 64), timestamptz '2030-02-02 10:00+00');
  perform public.confirm_subscription(repeat('7', 64), timestamptz '2030-02-02 11:00+00');
  perform public.confirm_subscription(repeat('9', 64), timestamptz '2030-02-02 11:00+00');
  insert into public.emailed (user_id, company, job_id) values (bob, 'Acme', '1'), (bob, 'Acme', '2'), (cy, 'Acme', '1');

  assert public.unsubscribe_user(bob, timestamptz '2030-02-03 09:00+00') = 'unsubscribed', 'the user was not unsubscribed';
  assert public.unsubscribe_subscription(repeat('0', 64), timestamptz '2030-02-03 09:00+00') = 'unsubscribed', 'the link did not unsubscribe';
  select * into a from public.subscriptions where user_id = bob;
  select * into b from public.subscriptions where user_id = cy;
  assert a.address = b.address and a.frequency = b.frequency and a.filters = b.filters
    and a.consent_given_at = b.consent_given_at and a.consent_confirmed_at = b.consent_confirmed_at
    and a.unsubscribed_at = b.unsubscribed_at
    and a.confirm_token_hash is null and b.confirm_token_hash is null
    and a.confirm_token_expires_at is null and b.confirm_token_expires_at is null,
    'the two ways of unsubscribing left different rows';
  assert a.address = 'same@test.example' and a.consent_given_at = timestamptz '2030-02-02 10:00+00'
    and a.consent_confirmed_at = timestamptz '2030-02-02 11:00+00' and a.unsubscribed_at = timestamptz '2030-02-03 09:00+00',
    'the audit record was not kept';
  assert a.filters = '{}'::jsonb and a.frequency = 'daily', 'the choices were not cleared';
  assert (select count(*) from public.emailed where user_id in (bob, cy)) = 0, 'the sent-jobs list was not cleared';

  -- A repeat is not an error, keeps the first time and finishes the clean-up, as the link does.
  insert into public.emailed (user_id, company, job_id) values (bob, 'Acme', '9');
  assert public.unsubscribe_user(bob, timestamptz '2030-02-04 09:00+00') = 'already_unsubscribed', 'a repeat was an error';
  assert (select unsubscribed_at from public.subscriptions where user_id = bob) = timestamptz '2030-02-03 09:00+00', 'a repeat moved the time';
  assert (select count(*) from public.emailed where user_id = bob) = 0, 'a repeat did not finish the clean-up';

  -- A user with no subscription: unknown (the function turns it into "not subscribed").
  delete from public.subscriptions where user_id = cy;
  assert public.unsubscribe_user(cy, now()) = 'unknown', 'a user with no subscription was not unknown';
  assert public.unsubscribe_user(gen_random_uuid(), now()) = 'unknown', 'a stranger was not unknown';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- The spam guard of the confirmation email.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  results text[] := '{}';
  i integer;
begin
  -- A user gets their limit and no more; a refused call uses up nothing (neither the address's nor the day's place).
  for i in 1..5 loop
    results := results || public.confirm_email_reserve(date '2030-03-01', 'user-a', 'addr-' || i, 3, 3, 100);
  end loop;
  assert results = array['ok', 'ok', 'ok', 'user_limit', 'user_limit'], 'the per-user limit is wrong: ' || results::text;
  assert (select sent from public.confirm_limits where day = date '2030-03-01' and key_hash = 'user-a') = 3, 'a refused call was counted against the user';
  assert not exists (select 1 from public.confirm_limits where key_hash in ('addr-4', 'addr-5')), 'a refused call used an address place';
  assert (select sent from public.confirm_daily_cap where day = date '2030-03-01') = 3, 'a refused call used the daily cap';

  -- An address gets its limit from any number of users, and a refusal gives the user's place back.
  delete from public.confirm_limits;
  delete from public.confirm_daily_cap;
  results := '{}';
  for i in 1..5 loop
    results := results || public.confirm_email_reserve(date '2030-03-02', 'user-' || i, 'victim', 3, 3, 100);
  end loop;
  assert results = array['ok', 'ok', 'ok', 'address_limit', 'address_limit'], 'the per-address limit is wrong: ' || results::text;
  assert (select count(*) from public.confirm_limits where key_hash in ('user-4', 'user-5') and sent > 0) = 0,
    'a refused address kept the user place';
  assert (select sent from public.confirm_daily_cap where day = date '2030-03-02') = 3, 'a refused address used the daily cap';

  -- The daily cap stops a flood from many users and addresses, and a refusal gives both places back.
  delete from public.confirm_limits;
  delete from public.confirm_daily_cap;
  results := '{}';
  for i in 1..30 loop
    results := results || public.confirm_email_reserve(date '2030-03-03', 'flood-user-' || i, 'flood-addr-' || i, 3, 3, 20);
  end loop;
  assert (select count(*) from unnest(results) r where r = 'ok') = 20, 'the flood was not stopped at the cap: ' || results::text;
  assert (select count(*) from unnest(results) r where r = 'daily_cap') = 10, 'the refused calls did not say daily_cap';
  assert (select sent from public.confirm_daily_cap where day = date '2030-03-03') = 20, 'the cap counter went past the cap';
  assert (select sent from public.confirm_limits where key_hash = 'flood-user-30') = 0
    and (select sent from public.confirm_limits where key_hash = 'flood-addr-30') = 0, 'a capped call kept its places';

  -- A place given back can be taken again, and no counter goes below zero.
  perform public.confirm_email_release(date '2030-03-03', 'flood-user-1', 'flood-addr-1');
  assert (select sent from public.confirm_daily_cap where day = date '2030-03-03') = 19, 'the release did not free the day''s place';
  assert (select sent from public.confirm_limits where key_hash = 'flood-user-1') = 0, 'the release did not free the user''s place';
  assert public.confirm_email_reserve(date '2030-03-03', 'flood-user-1', 'flood-addr-1', 3, 3, 20) = 'ok', 'a freed place was not usable';
  perform public.confirm_email_release(date '2031-01-01', 'nobody', 'nothing');
  delete from public.confirm_daily_cap;
  insert into public.confirm_daily_cap (day, sent) values (date '2030-03-05', 0);
  perform public.confirm_email_release(date '2030-03-05', 'nobody', 'nothing');
  assert (select sent from public.confirm_daily_cap where day = date '2030-03-05') = 0, 'the counter went below zero';

  -- A limit of zero refuses everything, in the right order.
  assert public.confirm_email_reserve(date '2030-03-06', 'z-user', 'z-addr', 0, 3, 10) = 'user_limit', 'a zero user limit allowed a call';
  assert public.confirm_email_reserve(date '2030-03-06', 'z-user', 'z-addr', 3, 0, 10) = 'address_limit', 'a zero address limit allowed a call';
  assert public.confirm_email_reserve(date '2030-03-06', 'z-user', 'z-addr', 3, 3, 0) = 'daily_cap', 'a zero cap allowed a call';
  assert not exists (select 1 from public.confirm_limits where day = date '2030-03-06' and sent > 0), 'refusals left places taken';

  -- Rows older than yesterday are deleted by the next call; yesterday's stay.
  delete from public.confirm_limits;
  delete from public.confirm_daily_cap;
  insert into public.confirm_limits (day, key_hash, sent) values (date '2030-03-07', 'old', 1), (date '2030-03-08', 'yesterday', 1);
  insert into public.confirm_daily_cap (day, sent) values (date '2030-03-07', 1), (date '2030-03-08', 1);
  perform public.confirm_email_reserve(date '2030-03-09', 'today', 'today-addr', 3, 3, 10);
  assert not exists (select 1 from public.confirm_limits where key_hash = 'old'), 'an old counter row was kept';
  assert exists (select 1 from public.confirm_limits where key_hash = 'yesterday'), 'yesterday''s counter row was deleted';
  assert not exists (select 1 from public.confirm_daily_cap where day = date '2030-03-07'), 'an old cap row was kept';
  assert exists (select 1 from public.confirm_daily_cap where day = date '2030-03-08'), 'yesterday''s cap row was deleted';

  -- Nothing personal is stored: the only text columns hold what the caller sent as keys.
  assert (select count(*) from information_schema.columns where table_schema = 'public' and table_name in ('confirm_limits', 'confirm_daily_cap')) = 5,
    'the counter tables have other columns than day, key_hash and sent';
end $$;

----------------------------------------------------------------------------------------------------------------------
-- Who can run what: the service role alone.
----------------------------------------------------------------------------------------------------------------------
do $$
declare
  ann constant uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
begin
  perform set_config('request.jwt.claim.sub', ann::text, true);
  perform set_config('role', 'authenticated', true);
  begin perform public.subscribe_start(ann, 'x@test.example', 'daily', '{}', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'a signed-in user ran subscribe_start';
  exception when insufficient_privilege then null; end;
  begin perform public.unsubscribe_user(ann, now());
    assert false, 'a signed-in user ran unsubscribe_user';
  exception when insufficient_privilege then null; end;
  begin perform public.confirm_email_reserve(current_date, 'a', 'b', 1, 1, 1);
    assert false, 'a signed-in user ran confirm_email_reserve';
  exception when insufficient_privilege then null; end;
  begin perform public.confirm_email_release(current_date, 'a', 'b');
    assert false, 'a signed-in user ran confirm_email_release';
  exception when insufficient_privilege then null; end;
  begin perform count(*) from public.confirm_limits;
    assert false, 'a signed-in user read the counters';
  exception when insufficient_privilege then null; end;
  begin perform count(*) from public.confirm_daily_cap;
    assert false, 'a signed-in user read the daily cap';
  exception when insufficient_privilege then null; end;
  perform set_config('role', 'anon', true);
  begin perform public.subscribe_start(ann, 'x@test.example', 'daily', '{}', repeat('f', 64), now(), repeat('6', 64), now());
    assert false, 'the anonymous role ran subscribe_start';
  exception when insufficient_privilege then null; end;
  begin perform public.unsubscribe_user(ann, now());
    assert false, 'the anonymous role ran unsubscribe_user';
  exception when insufficient_privilege then null; end;
  begin perform count(*) from public.confirm_limits;
    assert false, 'the anonymous role read the counters';
  exception when insufficient_privilege then null; end;
  perform set_config('role', 'service_role', true);
  assert public.confirm_email_reserve(date '2030-04-01', 'sr-user', 'sr-addr', 3, 3, 10) = 'ok', 'the service role cannot run the guard';
  perform set_config('role', 'postgres', true);
end $$;

----------------------------------------------------------------------------------------------------------------------
-- The daily cleanup of the confirmation counters (migration 20261120100000_confirm_counters_cleanup.sql): deletes every
-- row of an earlier day, keeps today's, says how many it deleted, is safe to run twice, refuses a day that cannot be
-- today, and only the service role may run it. Real dates, because the function refuses a day later than today.
----------------------------------------------------------------------------------------------------------------------
do $$
begin
  delete from public.confirm_limits;
  delete from public.confirm_daily_cap;
  insert into public.confirm_limits (day, key_hash, sent) values
    (current_date, 'a', 1), (current_date - 1, 'a', 1), (current_date - 1, 'b', 2), (current_date - 2, 'a', 1);
  insert into public.confirm_daily_cap (day, sent) values (current_date, 1), (current_date - 1, 4);
  assert public.confirm_email_cleanup(current_date) = 4, 'the cleanup did not count the rows it deleted';  -- 3 hash rows + 1 cap row
  assert (select count(*) from public.confirm_limits) = 1 and (select day from public.confirm_limits) = current_date,
    'the cleanup kept an earlier hash or deleted today''s';
  assert (select count(*) from public.confirm_daily_cap) = 1 and (select sent from public.confirm_daily_cap) = 1,
    'the cleanup kept an earlier cap row or changed today''s';
  assert public.confirm_email_cleanup(current_date) = 0, 'a second cleanup deleted something';
  begin
    perform public.confirm_email_cleanup(current_date + 1);  -- tomorrow would delete all of today's rows
    assert false, 'a cleanup of tomorrow was accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.confirm_email_cleanup(date '2999-01-01');
    assert false, 'a cleanup of a day in the future was accepted';
  exception when invalid_parameter_value then null; end;
  begin
    perform public.confirm_email_cleanup(null);
    assert false, 'a cleanup of no day was accepted';
  exception when invalid_parameter_value then null; end;
  assert (select count(*) from public.confirm_limits) = 1, 'a refused cleanup deleted rows';
  perform set_config('role', 'authenticated', true);
  begin perform public.confirm_email_cleanup(current_date);
    assert false, 'a signed-in user ran confirm_email_cleanup';
  exception when insufficient_privilege then null; end;
  perform set_config('role', 'anon', true);
  begin perform public.confirm_email_cleanup(current_date);
    assert false, 'the anonymous role ran confirm_email_cleanup';
  exception when insufficient_privilege then null; end;
  perform set_config('role', 'service_role', true);
  assert public.confirm_email_cleanup(current_date) = 0, 'the service role cannot run the cleanup';
  perform set_config('role', 'postgres', true);
end $$;

rollback;
select 'all digest signup checks passed' as result;
