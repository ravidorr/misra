-- Find an account by an address (PLAN_2.md step 5.2): the one lookup the `admin-account` Edge Function needs, so that the
-- maintainer can end job alerts or delete an account from a support email without opening the SQL editor.
--
-- It answers user ids only (a set that is empty when nothing matches) and changes nothing. An account "uses" an address
-- when it is the email the person signs in with (`profiles.email`), or, unless `p_login_only` is true, the address the
-- digest goes to (`subscriptions.address`). The comparison ignores case. Deleting an account asks for the sign-in
-- email only (`p_login_only = true`), so a digest address typed by someone else can never delete another person's account.
--
-- The address is an argument of a POST to PostgREST, so it is in a request body and not in a URL, which the platform's
-- request log would keep. Only the service role may run it (the Edge Function, after it has checked `is_admin()` with the
-- caller's token); it runs as the caller, so it needs no `security definer`, and no table gets a new column. Nothing here
-- is applied by the pull request: apply it once, see supabase/README.md.

create function public.admin_account_ids(p_address text, p_login_only boolean)
returns setof uuid
language sql
stable
set search_path = ''
as $$
  select p.user_id from public.profiles p where lower(p.email) = lower(p_address)
  union
  select s.user_id from public.subscriptions s where not p_login_only and lower(s.address) = lower(p_address);
$$;

revoke all on function public.admin_account_ids(text, boolean) from public, anon, authenticated;
grant execute on function public.admin_account_ids(text, boolean) to service_role;
