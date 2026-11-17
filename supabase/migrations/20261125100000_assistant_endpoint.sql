-- The database half of the read-only assistant endpoint (PLAN.md step 8.1; PRD FR-6). The `mcp` Edge Function answers
-- the MCP tools `search_jobs` and `get_job` for anyone's AI assistant. It is built but switched off until the lawyer has
-- reviewed it (the function answers 404 unless the secret ASSISTANT_ENDPOINT_ENABLED is "true"), so applying this
-- migration changes nothing anyone can see. Three things:
--
-- 1. assistant_ip_limits: how many calls one caller has made today, per tool. The key is a keyed hash made in the
--    function (the raw address never reaches the database), of the tool, the visitor's network and the day, so it changes
--    every day. Same design as report_ip_limits: row-level security on and no policy, nothing granted to `anon` or
--    `authenticated`, so only the service role can read or write it. A row of an earlier day is deleted by the next call
--    and by report_site_cleanup (below), which the daily run already calls, so a hash does not outlive its day by more
--    than the hours until that run.
-- 2. assistant_reserve: takes a place for one call, atomically (the row lock of the upsert serialises concurrent calls),
--    and answers 'ok' or 'limit'. A refused call does not count. The function also uses it for the cap on `get_job` for
--    everyone together (a row whose key is not a hash of anyone).
-- 3. search_jobs: the search the tool runs. It applies the rules of the dashboard's own search (templates/dashboard.js,
--    `select`) so the assistant sees the same roles: open jobs in Israel (a region or a place), the level, the role
--    types and the regions chosen (any of each, a role with no type counts as 'Other'), and every word of the query in the
--    company, title, team or location, or the job's key among those of `search_job_keys` (the full text, step 7.3; this
--    function never reads `description` itself). Newest first, then by company. It returns the columns the dashboard
--    reads and nothing else: never `description` or `details`. At most 50 jobs per call. Only the service role runs it,
--    so the rate limit cannot be skipped by calling it directly.
--
-- `get_job` has no function: the Edge Function reads the one job by its key with the service role. Its text is the one
-- thing the public roles cannot read (migration 20261121100000_jobs_text_not_public.sql); no function returns it in bulk.
-- Nothing here is applied by the pull request: apply it once, see supabase/README.md.

create table public.assistant_ip_limits (
  day     date    not null,
  ip_hash text    not null,
  calls   integer not null default 0,
  primary key (day, ip_hash)
);

alter table public.assistant_ip_limits enable row level security;

revoke all on public.assistant_ip_limits from anon, authenticated;

create function public.assistant_reserve(p_day date, p_ip_hash text, p_limit integer)
returns text
language plpgsql
set search_path = ''
as $$
declare
  used integer;
begin
  delete from public.assistant_ip_limits where day < p_day - 1;
  if p_limit < 1 then
    return 'limit';
  end if;
  insert into public.assistant_ip_limits as l (day, ip_hash, calls) values (p_day, p_ip_hash, 1)
    on conflict (day, ip_hash) do update set calls = l.calls + 1 where l.calls < p_limit
    returning l.calls into used;
  if used is null then
    return 'limit';
  end if;
  return 'ok';
end;
$$;

-- The daily cleanup the sync step already calls: now it also deletes the assistant counters of every day before p_day.
-- Same rules as before (counts only, a day later than today is refused); the grants of the function stay as they were.
create or replace function public.report_site_cleanup(p_day date)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  removed_ips integer;
  removed_caps integer;
  removed_calls integer;
begin
  if p_day is null or p_day > current_date then
    raise exception 'report_site_cleanup: p_day must be today (UTC) or earlier' using errcode = '22023';
  end if;
  delete from public.report_ip_limits where day < p_day;
  get diagnostics removed_ips = row_count;
  delete from public.report_daily_cap where day < p_day;
  get diagnostics removed_caps = row_count;
  delete from public.assistant_ip_limits where day < p_day;
  get diagnostics removed_calls = row_count;
  return removed_ips + removed_caps + removed_calls;
end;
$$;

create function public.search_jobs(
  p_query text,
  p_levels text[],
  p_role_types text[],
  p_regions text[],
  p_limit integer,
  p_offset integer
)
returns jsonb
language sql
stable
set search_path = ''
as $$
  with q as (
    select array_remove(regexp_split_to_array(lower(btrim(left(coalesce(p_query, ''), 200))), '\s+'), '') as terms
  ), text_hits as (
    select e ->> 0 as company, e ->> 1 as job_id
      from q, jsonb_array_elements(public.search_job_keys(array_to_string(q.terms, ' '))) e
  ), matched as (
    select j.company, j.job_id, j.title, j.team, j.location, j.url, j.first_seen, j.level,
           case when cardinality(j.role_types) = 0 then array['Other'] else j.role_types end as role_types,
           j.regions, j.locations, j.snippet
      from public.jobs j, q
     where j.status = 'open'
       and cardinality(j.regions) + cardinality(j.locations) > 0
       and (coalesce(cardinality(p_levels), 0) = 0 or j.level = any (p_levels))
       and (coalesce(cardinality(p_role_types), 0) = 0
            or j.role_types && p_role_types
            or (cardinality(j.role_types) = 0 and 'Other' = any (p_role_types)))
       and (coalesce(cardinality(p_regions), 0) = 0 or j.regions && p_regions)
       and (cardinality(q.terms) = 0
            or (select bool_and(strpos(lower(concat_ws(' ', j.company, btrim(j.title), btrim(j.team), btrim(j.location))), t) > 0)
                  from unnest(q.terms) t)
            or exists (select 1 from text_hits h where h.company = j.company and h.job_id = j.job_id))
  )
  select jsonb_build_object(
    'total', (select count(*) from matched),
    'jobs', coalesce((
      select jsonb_agg(to_jsonb(page) order by left(coalesce(page.first_seen, ''), 10) desc, page.company, page.job_id)
        from (
          select * from matched
           order by left(coalesce(first_seen, ''), 10) desc, company, job_id
           limit greatest(1, least(coalesce(p_limit, 20), 50))
          offset greatest(coalesce(p_offset, 0), 0)
        ) page
    ), '[]'::jsonb)
  );
$$;

revoke all on function public.assistant_reserve(date, text, integer) from public, anon, authenticated;
revoke all on function public.search_jobs(text, text[], text[], text[], integer, integer) from public, anon, authenticated;
grant execute on function public.assistant_reserve(date, text, integer) to service_role;
grant execute on function public.search_jobs(text, text[], text[], text[], integer, integer) to service_role;
