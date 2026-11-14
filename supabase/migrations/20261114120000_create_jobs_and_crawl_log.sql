-- Jobs backend (PLAN.md step 2.1). The daily pipeline fills these tables with the service-role key (step 2.2);
-- the dashboard reads open jobs with the public anon key (step 2.3). Row-level security is the access model.
--
-- posted, first_seen and last_seen stay text: they mirror data/jobs.db, where `posted` comes in several formats
-- (epoch milliseconds, a date, ISO with or without a zone, empty). Typed columns can be added in a later migration.

create table public.jobs (
  company     text not null,
  job_id      text not null,
  title       text,
  team        text,
  location    text,
  url         text,
  posted      text,
  first_seen  text,
  last_seen   text,
  status      text not null default 'open' check (status in ('open', 'closed')),
  description text,
  details     jsonb,
  level       text,
  role_types  text[] not null default '{}',
  regions     text[] not null default '{}',
  locations   text[] not null default '{}',
  primary key (company, job_id)
);

-- Same columns as the crawl_log table of data/jobs.db, plus a key (SQLite has none).
create table public.crawl_log (
  id      bigint generated always as identity primary key,
  ts      text,
  company text,
  ats     text,
  ok      integer,
  n_jobs  integer,
  note    text
);

-- Filtering: status and level are plain columns, the three arrays are searched with && and @>.
create index jobs_status_idx on public.jobs (status);
create index jobs_level_idx on public.jobs (level);
create index jobs_role_types_idx on public.jobs using gin (role_types);
create index jobs_regions_idx on public.jobs using gin (regions);
create index jobs_locations_idx on public.jobs using gin (locations);

alter table public.jobs enable row level security;
alter table public.crawl_log enable row level security;

-- Supabase grants every privilege on new public tables to anon and authenticated. Take back everything, then give
-- back SELECT on jobs only. TRUNCATE is not covered by row-level security, so it is revoked too.
revoke all on public.jobs from anon, authenticated;
revoke all on public.crawl_log from anon, authenticated;
grant select on public.jobs to anon, authenticated;

-- Anyone, signed in or not, reads open jobs only. There is no insert, update or delete policy: only the service role
-- (which bypasses row-level security) writes. crawl_log has no policy, so nobody but the service role sees it.
create policy "open jobs are public" on public.jobs
  for select to anon, authenticated
  using (status = 'open');
