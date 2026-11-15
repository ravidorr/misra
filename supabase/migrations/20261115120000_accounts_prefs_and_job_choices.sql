-- Accounts (PLAN.md step 4.1). Sign-in is Google through Supabase Auth, which owns the `auth` schema. This migration adds
-- what the dashboard keeps per user in `public`: the email, the user's preferences, and the jobs a user starred or
-- discarded. Row-level security is the access model: a signed-in user reads and writes only the rows whose user_id is
-- their own id, and the anonymous role gets nothing here.
--
-- Privacy (PRD FR-1): the only personal detail kept in `public` is the email address. What Google sends besides it (a
-- name, a photo, the Google account id) arrives in auth.users.raw_user_meta_data and auth.identities, which Supabase
-- Auth manages and which `public` never copies: the trigger below reads new.email and nothing else. supabase/README.md
-- says what stays in `auth`.
--
-- Every table points at auth.users with `on delete cascade`, so deleting the auth user (step 4.4) removes all of a
-- user's rows in one step.

-- The profile: user id and email, filled by a trigger on auth.users. Users can read their own row and cannot write it
-- (the email always comes from Google); only the trigger and the service role write.
create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  email   text not null
);

-- A user's saved dashboard filters (step 4.3) and email settings (step 6.2), as JSON objects the page owns. The size
-- checks bound one row. There is no cap on the number of user_jobs rows per user yet.
create table public.user_prefs (
  user_id        uuid primary key references auth.users (id) on delete cascade,
  filters        jsonb not null default '{}'
                 check (jsonb_typeof(filters) = 'object' and octet_length(filters::text) <= 16384),
  email_settings jsonb not null default '{}'
                 check (jsonb_typeof(email_settings) = 'object' and octet_length(email_settings::text) <= 4096),
  updated_at     timestamptz not null default now()
);

-- One row per user and job: the state is a single column, so a job cannot be starred and discarded at once (FR-2). The
-- job is named by company and job_id, the key of public.jobs, but there is no foreign key: a starred job that has since
-- closed must stay (step 4.2), with the title, location and link kept here to show it as "no longer listed". The link
-- must be http(s), so it is safe to render as a link.
create table public.user_jobs (
  user_id    uuid not null references auth.users (id) on delete cascade,
  company    text not null check (char_length(company) between 1 and 200),
  job_id     text not null check (char_length(job_id) between 1 and 200),
  state      text not null check (state in ('star', 'discard')),
  title      text check (char_length(title) <= 500),
  location   text check (char_length(location) <= 500),
  url        text check (char_length(url) <= 2000 and url ~ '^https?://'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, company, job_id)
);

alter table public.profiles enable row level security;
alter table public.user_prefs enable row level security;
alter table public.user_jobs enable row level security;

-- Supabase grants every privilege on new public tables to anon and authenticated. Take back everything, then give the
-- signed-in role what it needs. The anonymous role gets nothing on these tables.
revoke all on public.profiles from anon, authenticated;
revoke all on public.user_prefs from anon, authenticated;
revoke all on public.user_jobs from anon, authenticated;
grant select on public.profiles to authenticated;
grant select, insert, update, delete on public.user_prefs to authenticated;
grant select, insert, update, delete on public.user_jobs to authenticated;

-- Own rows only. (select auth.uid()) is the same test as auth.uid() = user_id, evaluated once per statement.
create policy "own profile" on public.profiles
  for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "own prefs read" on public.user_prefs
  for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "own prefs insert" on public.user_prefs
  for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "own prefs update" on public.user_prefs
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own prefs delete" on public.user_prefs
  for delete to authenticated
  using ((select auth.uid()) = user_id);

create policy "own job choices read" on public.user_jobs
  for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "own job choices insert" on public.user_jobs
  for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "own job choices update" on public.user_jobs
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own job choices delete" on public.user_jobs
  for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Keeps updated_at honest: a client cannot back-date or freeze it, on insert or update.
create function public.touch_updated_at() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.touch_updated_at() from public, anon, authenticated;

create trigger touch_user_prefs before insert or update on public.user_prefs
  for each row execute function public.touch_updated_at();
create trigger touch_user_jobs before insert or update on public.user_jobs
  for each row execute function public.touch_updated_at();

-- Copies the email, and only the email, of a new or changed auth user into public.profiles. It runs as its owner
-- (security definer) because the signed-in role may not write profiles. It never reads raw_user_meta_data, so a name or
-- a photo from Google cannot reach `public`. An update that clears the email is skipped, not an error that would block
-- Supabase Auth.
create function public.sync_profile() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, email) values (new.id, new.email)
  on conflict (user_id) do update set email = excluded.email;
  return new;
end;
$$;

revoke all on function public.sync_profile() from public, anon, authenticated;

create trigger sync_profile after insert or update of email on auth.users
  for each row when (new.email is not null) execute function public.sync_profile();

-- The access token. Supabase Auth copies auth.users.raw_user_meta_data (for Google: the name and the photo URL) into
-- every access token as the `user_metadata` claim, and the browser saves the token. This hook empties that claim. It
-- does nothing until it is chosen in the Supabase dashboard (Authentication, Auth Hooks, Customize Access Token (JWT)
-- Claims, Postgres function `public.custom_access_token_hook`); supabase/README.md has the steps. `user_metadata` is an
-- optional claim, so the token stays valid. Only Supabase Auth may run it.
create function public.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql
stable
set search_path = ''
as $$
begin
  return jsonb_set(event, '{claims,user_metadata}', '{}'::jsonb);
end;
$$;

grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook(jsonb) from public, anon, authenticated;

-- People who signed in before this migration get their profile too.
insert into public.profiles (user_id, email) select id, email from auth.users where email is not null on conflict do nothing;
