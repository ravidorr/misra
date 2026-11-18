-- The Alerts counts of the admin page (PLAN_2.md step 5.1): numbers only, no address and no user id. No table, no
-- column, no policy: one function.
--
-- `public.admin_alert_counts()` is `security definer` because `subscriptions` and `emailed` have no read for the admin
-- (and must not get one: a policy would let a browser read rows of accounts). It is gated by `public.is_admin()` in
-- every branch of its query, so a signed-in user who is not listed gets zero rows, and `anon` a permission error
-- (execute is granted to `authenticated` only). It pins its search path, only reads `subscriptions` and `emailed`, and
-- selects counts of them, never a column. The rows, in order of `kind`, then day, newest first:
--   kind  text     'confirmed', 'emailed', 'off' or 'pending'
--   day   date     for 'emailed' one of the last 7 UTC days, today first, with 0 for a day nobody was emailed; null for
--                  the three subscription kinds
--   n     integer  for a subscription kind, the subscriptions in that state; for 'emailed', the DISTINCT users who were
--                  emailed that day (not the number of jobs)
-- A subscription is 'off' when `unsubscribed_at` is set, 'confirmed' when the address was confirmed and not
-- unsubscribed (`consent_confirmed_at` set, the only state the digest sends in), and 'pending' otherwise (turned on, the
-- address not confirmed yet). Nothing here is applied by the pull request: apply it once, see supabase/README.md.

create function public.admin_alert_counts()
returns table (kind text, day date, n integer)
language sql
stable
security definer
set search_path = ''
as $$
  with states as (
    select case
             when unsubscribed_at is not null then 'off'
             when consent_confirmed_at is not null then 'confirmed'
             else 'pending'
           end as kind
    from public.subscriptions
  ),
  days as (
    select ((now() at time zone 'utc')::date - g) as day from generate_series(0, 6) as g
  )
  select k.kind, null::date, (select count(*) from states s where s.kind = k.kind)::integer
  from (values ('pending'), ('confirmed'), ('off')) as k(kind)
  where public.is_admin()
  union all
  select 'emailed', d.day,
         (select count(distinct e.user_id) from public.emailed e where (e.emailed_at at time zone 'utc')::date = d.day)::integer
  from days d
  where public.is_admin()
  order by 1, 2 desc;
$$;

revoke all on function public.admin_alert_counts() from public, anon, authenticated;
grant execute on function public.admin_alert_counts() to authenticated;
