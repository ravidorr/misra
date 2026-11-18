-- The admin check (PLAN_2.md step 1.1). `public.admins` lists the one person who may use the admin page; the admin
-- page itself, the audit log and every policy that uses the check come in later steps.
--
-- Nobody but the table's owner (the SQL editor) and the service role (it bypasses row-level security) can read or write
-- `admins`: there is no policy and no grant for `anon` or `authenticated`, so a signed-out visitor and a signed-in
-- non-admin get a permission error on the table. `is_admin()` is how every later policy and function asks the question.
-- It is SECURITY DEFINER because the callers cannot read `admins`, it pins its search path like every other function
-- here, and it answers only a boolean about the caller (`auth.uid()`), so it cannot be used to look anyone else up.
-- A row is inserted by hand once the maintainer has signed in on the dashboard (supabase/README.md, "The admin
-- check"); the first sign in never claims it, so there is no way to become an admin from the page. Nothing here is
-- applied by the pull request: apply it once, see supabase/README.md.

create table public.admins (
  user_id uuid primary key references auth.users (id) on delete cascade
);

alter table public.admins enable row level security;
revoke all on public.admins from anon, authenticated, public;

create function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.admins where user_id = (select auth.uid()));
$$;

revoke all on function public.is_admin() from public, anon, authenticated;
grant execute on function public.is_admin() to anon, authenticated;
