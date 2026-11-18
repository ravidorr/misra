-- The admin log (PLAN_2.md step 1.3): one row per change made on the admin page, with the time, a plain action and the
-- thing it touched. `target` is a company name or a table name, never an address and never a user id, and it may be
-- empty (an action with nothing to name), so the log holds no personal data.
--
-- Nothing writes here yet. The trigger of step 3.1 (edits of `companies`) and the `admin-account` function of step 5.2
-- (with the service role, which bypasses row-level security) are the only writers: no public role has insert, update,
-- delete or truncate, and not even a listed admin can change a row from a browser. Only a signed-in admin reads it, by
-- the one policy below (`public.is_admin()`, step 1.1). Nothing here is applied by the pull request: apply it once,
-- see supabase/README.md.

create table public.admin_log (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  action text not null,
  target text
);

alter table public.admin_log enable row level security;
revoke all on public.admin_log from anon, authenticated, public;
grant select on public.admin_log to authenticated;

create policy "an admin reads the log" on public.admin_log
  for select to authenticated using (public.is_admin());
