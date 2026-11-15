-- The part of a Supabase project that the migrations and the SQL tests rely on, for a plain Postgres 17 database (the CI
-- job `sql-tests`, and a local check: scripts/run-sql-tests.sh). Never run this against a real Supabase project: it
-- creates roles and an `auth` schema that Supabase already owns.
--
-- What it stands in for, and why each piece is here:
--   roles        anon, authenticated, service_role (the three roles PostgREST switches to; service_role bypasses
--                row-level security), and supabase_auth_admin (the role Supabase Auth runs as; the access token hook is
--                granted to it).
--   auth.users   the table Google sign-in fills. Only the columns the migrations and tests read or write: id, email and
--                the two metadata columns (the tests check that the profile copy never reads them).
--   auth.uid()   what the row-level security policies call. Like Supabase's, it reads the claims PostgREST sets from
--                the user's token (`request.jwt.claim.sub`, or `sub` in `request.jwt.claims`).
--   privileges   Supabase lets the three roles use the `auth` and `public` schemas and, by default privileges, gives them
--                every privilege on each new object in `public`. The migrations then revoke what they must, which is
--                what the tests check, so the same defaults are needed here to test the revokes honestly.
-- A difference that matters is stated here and nowhere else: this database's `postgres` user is a superuser, which a
-- Supabase project's is not. The tests switch roles with `set local role`, which works for both.

create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
create role supabase_auth_admin nologin noinherit;

create schema auth;

create table auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text,
  raw_user_meta_data jsonb,
  raw_app_meta_data  jsonb,
  created_at         timestamptz not null default now()
);

create function auth.uid() returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

grant usage on schema auth to anon, authenticated, service_role, supabase_auth_admin;
grant usage on schema public to anon, authenticated, service_role;

alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
