-- Subscribers of the personalised digest (PLAN.md step 6.1; PRD FR-8 AC 7). Two tables and two functions:
--
-- 1. subscriptions: one row per user who turned the email on. It holds the address the digest goes to, the filters
--    snapshot, and the consent log the lawyer asked for: consent_given_at (the user turned the email on),
--    consent_confirmed_at (the user pressed Confirm on the page the confirmation email links to) and unsubscribed_at.
--    The digest may be sent to a row when consent_confirmed_at is set and unsubscribed_at is null, and only then.
-- 2. emailed: which jobs were already emailed to a user, so a job is never sent twice.
--
-- Who can write what. The user's access token (the `authenticated` role) can READ every column except the two token
-- hashes, and can UPDATE only `frequency` and `filters`. It can neither insert nor delete: a row appears when a trusted
-- function creates it, and it goes only with the account (deleting the row would erase the consent log). The address,
-- the consent columns and the token hashes are written by the service role alone, so a user cannot confirm themselves,
-- cannot send the digest to an address that never confirmed, and cannot undo an unsubscribe. `emailed` is the service
-- role's alone. The anonymous role gets nothing on either table.
--
-- Tokens. Two random 256-bit tokens per subscription, one for each purpose (confirm, unsubscribe). Only the SHA-256 of a
-- token is stored (64 hex characters, made in the Edge Function), so a database leak can neither confirm nor unsubscribe
-- anyone. The raw token appears only in the emailed link. The two functions below look a token up by that hash; a hash
-- of a 256-bit secret cannot be chosen or approached by a guesser, so an index lookup leaks nothing through timing.
--
-- Every row points at auth.users with `on delete cascade`, so deleting the auth user (the `delete-account` function)
-- removes the subscription, its consent log and the emailed list in one step.

create table public.subscriptions (
  user_id                  uuid primary key references auth.users (id) on delete cascade,
  -- The address the digest goes to (a signed-in user's Google email by default, or another one they typed). Not
  -- writable by the user: a change of address needs a new confirmation, so it goes through a trusted function.
  address                  text not null
                           check (char_length(address) between 3 and 254
                                  and address ~ '^[^[:space:]<>@,"]+@[^[:space:]<>@,"]+[.][^[:space:]<>@,"]+$'),
  frequency                text not null default 'daily' check (frequency in ('daily', 'weekly')),
  -- The snapshot of the dashboard filters copied by "Use these filters for my email" (regions, levels, role types and
  -- keywords). Empty after an unsubscribe.
  filters                  jsonb not null default '{}'
                           check (jsonb_typeof(filters) = 'object' and octet_length(filters::text) <= 16384),
  -- SHA-256 of the confirmation token, and when it stops working. Kept after the confirmation so that pressing the
  -- link again is answered "already confirmed"; cleared by an unsubscribe and by a change of address.
  confirm_token_hash       text unique check (confirm_token_hash ~ '^[0-9a-f]{64}$'),
  confirm_token_expires_at timestamptz,
  -- SHA-256 of the unsubscribe token: no expiry, stable for the life of the row and kept after an unsubscribe, so a
  -- second press is answered "already unsubscribed". A new subscription replaces it.
  unsubscribe_token_hash   text not null unique check (unsubscribe_token_hash ~ '^[0-9a-f]{64}$'),
  -- The consent log. consent_given_at is the latest time the user turned the email on; consent_confirmed_at is when
  -- they confirmed the address from the email (null until then); unsubscribed_at is when they unsubscribed (null
  -- while subscribed). The row keeps the latest consent cycle only.
  consent_given_at         timestamptz not null default now(),
  consent_confirmed_at     timestamptz,
  unsubscribed_at          timestamptz,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  -- After an unsubscribe nothing of the user's choices is kept: only the audit record (address and timestamps).
  check (unsubscribed_at is null or filters = '{}'::jsonb)
);

-- One row per user and job, keyed so the digest can check a whole batch with the primary key and insert with
-- `on conflict do nothing`. Like user_jobs there is no foreign key to jobs: a job that closes does not erase the fact
-- that it was emailed.
create table public.emailed (
  user_id    uuid not null references auth.users (id) on delete cascade,
  company    text not null check (char_length(company) between 1 and 200),
  job_id     text not null check (char_length(job_id) between 1 and 200),
  emailed_at timestamptz not null default now(),
  primary key (user_id, company, job_id)
);

alter table public.subscriptions enable row level security;
alter table public.emailed enable row level security;

-- Take back everything Supabase grants by default, then give the signed-in role column by column what it may have. No
-- table-level grant: a table-level `select` would include the token hashes.
revoke all on public.subscriptions from anon, authenticated;
revoke all on public.emailed from anon, authenticated;
grant select (user_id, address, frequency, filters, consent_given_at, consent_confirmed_at, unsubscribed_at, created_at, updated_at)
  on public.subscriptions to authenticated;
grant update (frequency, filters) on public.subscriptions to authenticated;

-- Own row only. (select auth.uid()) is the same test as auth.uid() = user_id, evaluated once per statement. `emailed`
-- has no policy at all: with row-level security on, only the service role reads or writes it.
create policy "own subscription read" on public.subscriptions
  for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "own subscription update" on public.subscriptions
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create trigger touch_subscriptions before insert or update on public.subscriptions
  for each row execute function public.touch_updated_at();

-- A new address is a new consent: it must be confirmed again. If a trusted function changes the address without
-- supplying a new confirmation token, the old token (which went to the old address) is dropped too, so it cannot
-- confirm the new address. Nothing sends until consent_confirmed_at is set again. (A user cannot change the address:
-- this is the second lock behind the column grant.)
create function public.clear_confirmation_on_address_change() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.address is distinct from old.address then
    new.consent_confirmed_at := null;
    if new.confirm_token_hash is not distinct from old.confirm_token_hash then
      new.confirm_token_hash := null;
      new.confirm_token_expires_at := null;
    end if;
  end if;
  return new;
end;
$$;

revoke all on function public.clear_confirmation_on_address_change() from public, anon, authenticated;

create trigger clear_confirmation_on_address_change before update of address on public.subscriptions
  for each row execute function public.clear_confirmation_on_address_change();

-- The two functions behind the Edge Functions `confirm-email` and `unsubscribe`. Each takes the SHA-256 of the token and
-- the time (the function's clock is passed in, so tests can fix it), does its whole job in one statement block under a
-- row lock, and answers with a word the function turns into an HTTP answer. A replay is not an error. They run as the
-- caller, the service role, and nobody else may execute them.

-- 'confirmed' (consent_confirmed_at is set now), 'already_confirmed' (it was set before: the first time is kept),
-- 'expired' (the link is older than its expiry), 'unsubscribed' (the user has since unsubscribed: never turns sending
-- back on) or 'unknown' (no such token, which includes every confirmation link of a subscription that was unsubscribed,
-- because that clears the hash).
create function public.confirm_subscription(p_hash text, p_now timestamptz)
returns text
language plpgsql
set search_path = ''
as $$
declare
  sub public.subscriptions%rowtype;
begin
  select * into sub from public.subscriptions where confirm_token_hash = p_hash for update;
  if not found then
    return 'unknown';
  end if;
  if sub.unsubscribed_at is not null then
    return 'unsubscribed';
  end if;
  if sub.consent_confirmed_at is not null then
    return 'already_confirmed';
  end if;
  if sub.confirm_token_expires_at is null or sub.confirm_token_expires_at <= p_now then
    return 'expired';
  end if;
  update public.subscriptions set consent_confirmed_at = p_now where user_id = sub.user_id;
  return 'confirmed';
end;
$$;

-- 'unsubscribed' (sending stops now), 'already_unsubscribed' (the first time is kept) or 'unknown'. An unsubscribe keeps
-- the audit record (address, consent_given_at, consent_confirmed_at, unsubscribed_at, the unsubscribe hash) and clears
-- everything else of the user's choices: the filters snapshot, the frequency (back to the default), the confirmation
-- token (so an old confirmation link can never turn sending back on) and the list of emailed jobs. A repeat press
-- clears them again, which also finishes a clean-up that failed the first time.
create function public.unsubscribe_subscription(p_hash text, p_now timestamptz)
returns text
language plpgsql
set search_path = ''
as $$
declare
  sub public.subscriptions%rowtype;
begin
  select * into sub from public.subscriptions where unsubscribe_token_hash = p_hash for update;
  if not found then
    return 'unknown';
  end if;
  if sub.unsubscribed_at is not null then
    delete from public.emailed where user_id = sub.user_id;
    return 'already_unsubscribed';
  end if;
  update public.subscriptions
    set unsubscribed_at = p_now,
        filters = '{}'::jsonb,
        frequency = 'daily',
        confirm_token_hash = null,
        confirm_token_expires_at = null
    where user_id = sub.user_id;
  delete from public.emailed where user_id = sub.user_id;
  return 'unsubscribed';
end;
$$;

revoke all on function public.confirm_subscription(text, timestamptz) from public, anon, authenticated;
revoke all on function public.unsubscribe_subscription(text, timestamptz) from public, anon, authenticated;
grant execute on function public.confirm_subscription(text, timestamptz) to service_role;
grant execute on function public.unsubscribe_subscription(text, timestamptz) to service_role;
