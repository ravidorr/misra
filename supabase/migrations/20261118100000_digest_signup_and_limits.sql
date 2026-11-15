-- Turning the personalised digest on and off from the dashboard (PLAN.md step 6.2; PRD FR-8 AC 1 to 3). Four functions and
-- two counter tables, all for the service role alone (the Edge Function `email-settings` calls them; the signed-in role
-- gets nothing new). The `subscriptions` table already holds everything a subscription needs, including the expiry of the
-- confirmation link (`confirm_token_expires_at`), so it gets no new column.
--
-- 1. subscribe_start: creates or restarts a user's subscription in one statement, under a row lock. It sets the address,
--    frequency and filters snapshot, stores the two token hashes and the expiry made by the function, sets
--    consent_given_at to now, and clears consent_confirmed_at and unsubscribed_at, so nothing is sent until the new
--    link is confirmed. It answers 'started', or 'already_on' (the row is confirmed, active and for the same address: it
--    is left exactly as it is, so a repeated call can never switch a running email off). A change of address is a new
--    consent and goes through here too.
-- 2. unsubscribe_user: the same unsubscribe as the one-click link, for a signed-in user. The raw unsubscribe token is
--    never stored, so the function reads the row's hash and hands it to unsubscribe_subscription (step 6.1), which does
--    the whole clean-up: nothing is repeated here, so the two ways of unsubscribing cannot drift apart.
-- 3. confirm_email_reserve and confirm_email_release: the spam guard for the confirmation email, with the same shape as
--    report_site_reserve. A signed-in user could otherwise mail any address over and over, so three counters apply:
--    per user and day, per address and day, and one cap for all of these emails per day. Keys are keyed hashes made in
--    the function (never the user id or the address itself), the day is part of the key, and rows older than yesterday
--    are deleted on every call. A place is taken before the email is sent and given back when the email service
--    definitely refused it.
--
-- The tables have row-level security on and no policy, and nothing is granted to `anon` or `authenticated`.

create table public.confirm_limits (
  day      date    not null,
  key_hash text    not null,
  sent     integer not null default 0,
  primary key (day, key_hash)
);

create table public.confirm_daily_cap (
  day  date    primary key,
  sent integer not null default 0
);

alter table public.confirm_limits enable row level security;
alter table public.confirm_daily_cap enable row level security;

revoke all on public.confirm_limits from anon, authenticated;
revoke all on public.confirm_daily_cap from anon, authenticated;

-- Answers 'ok', 'user_limit', 'address_limit' or 'daily_cap'. The user's row is always taken first, then the address's,
-- then the day's (confirm_email_release uses the same order, so the calls cannot deadlock). A refusal gives back what
-- the call had taken before it, so a refused request uses up nothing.
create function public.confirm_email_reserve(
  p_day date, p_user_hash text, p_address_hash text, p_user_limit integer, p_address_limit integer, p_daily_cap integer
)
returns text
language plpgsql
set search_path = ''
as $$
declare
  used integer;
begin
  delete from public.confirm_limits where day < p_day - 1;
  delete from public.confirm_daily_cap where day < p_day - 1;

  if p_user_limit < 1 then
    return 'user_limit';
  end if;
  insert into public.confirm_limits as l (day, key_hash, sent) values (p_day, p_user_hash, 1)
    on conflict (day, key_hash) do update set sent = l.sent + 1 where l.sent < p_user_limit
    returning l.sent into used;
  if used is null then
    return 'user_limit';
  end if;

  used := null;
  if p_address_limit >= 1 then
    insert into public.confirm_limits as l (day, key_hash, sent) values (p_day, p_address_hash, 1)
      on conflict (day, key_hash) do update set sent = l.sent + 1 where l.sent < p_address_limit
      returning l.sent into used;
  end if;
  if used is null then
    update public.confirm_limits set sent = greatest(sent - 1, 0) where day = p_day and key_hash = p_user_hash;
    return 'address_limit';
  end if;

  used := null;
  if p_daily_cap >= 1 then
    insert into public.confirm_daily_cap as c (day, sent) values (p_day, 1)
      on conflict (day) do update set sent = c.sent + 1 where c.sent < p_daily_cap
      returning c.sent into used;
  end if;
  if used is null then
    update public.confirm_limits set sent = greatest(sent - 1, 0) where day = p_day and key_hash = p_user_hash;
    update public.confirm_limits set sent = greatest(sent - 1, 0) where day = p_day and key_hash = p_address_hash;
    return 'daily_cap';
  end if;
  return 'ok';
end;
$$;

create function public.confirm_email_release(p_day date, p_user_hash text, p_address_hash text)
returns text
language plpgsql
set search_path = ''
as $$
begin
  update public.confirm_limits set sent = greatest(sent - 1, 0) where day = p_day and key_hash = p_user_hash;
  update public.confirm_limits set sent = greatest(sent - 1, 0) where day = p_day and key_hash = p_address_hash;
  update public.confirm_daily_cap set sent = greatest(sent - 1, 0) where day = p_day;
  return 'ok';
end;
$$;

create function public.subscribe_start(
  p_user_id uuid, p_address text, p_frequency text, p_filters jsonb, p_confirm_hash text,
  p_confirm_expires timestamptz, p_unsubscribe_hash text, p_now timestamptz
)
returns text
language plpgsql
set search_path = ''
as $$
declare
  sub public.subscriptions%rowtype;
begin
  select * into sub from public.subscriptions where user_id = p_user_id for update;
  if found and sub.unsubscribed_at is null and sub.consent_confirmed_at is not null
     and lower(sub.address) = lower(p_address) then
    return 'already_on';
  end if;
  insert into public.subscriptions (
    user_id, address, frequency, filters, confirm_token_hash, confirm_token_expires_at, unsubscribe_token_hash,
    consent_given_at, consent_confirmed_at, unsubscribed_at
  ) values (
    p_user_id, p_address, p_frequency, p_filters, p_confirm_hash, p_confirm_expires, p_unsubscribe_hash,
    p_now, null, null
  )
  on conflict (user_id) do update set
    address = excluded.address,
    frequency = excluded.frequency,
    filters = excluded.filters,
    confirm_token_hash = excluded.confirm_token_hash,
    confirm_token_expires_at = excluded.confirm_token_expires_at,
    unsubscribe_token_hash = excluded.unsubscribe_token_hash,
    consent_given_at = excluded.consent_given_at,
    consent_confirmed_at = null,
    unsubscribed_at = null;
  return 'started';
end;
$$;

-- 'unsubscribed', 'already_unsubscribed' or 'unknown' (this user has no subscription row), exactly as the one-click
-- link answers.
create function public.unsubscribe_user(p_user_id uuid, p_now timestamptz)
returns text
language plpgsql
set search_path = ''
as $$
declare
  token_hash text;
begin
  select unsubscribe_token_hash into token_hash from public.subscriptions where user_id = p_user_id for update;
  if not found then
    return 'unknown';
  end if;
  return public.unsubscribe_subscription(token_hash, p_now);
end;
$$;

revoke all on function public.confirm_email_reserve(date, text, text, integer, integer, integer) from public, anon, authenticated;
revoke all on function public.confirm_email_release(date, text, text) from public, anon, authenticated;
revoke all on function public.subscribe_start(uuid, text, text, jsonb, text, timestamptz, text, timestamptz) from public, anon, authenticated;
revoke all on function public.unsubscribe_user(uuid, timestamptz) from public, anon, authenticated;
grant execute on function public.confirm_email_reserve(date, text, text, integer, integer, integer) to service_role;
grant execute on function public.confirm_email_release(date, text, text) to service_role;
grant execute on function public.subscribe_start(uuid, text, text, jsonb, text, timestamptz, text, timestamptz) to service_role;
grant execute on function public.unsubscribe_user(uuid, timestamptz) to service_role;
