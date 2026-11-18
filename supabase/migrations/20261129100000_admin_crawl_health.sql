-- Crawl health for the admin page (PLAN_2.md steps 2.1 and 2.2). No table, no pipeline change: two read policies for a
-- signed-in admin and one function that turns the crawl log into one row per company.
--
-- 2.1 Read access. `crawl_log` had no grant and no policy, so only the service role read it. It gets `select` for
-- `authenticated` and one policy, `using (public.is_admin())`: a signed-in user who is not listed reads no row, and
-- `anon` gets a permission error. `jobs` gets a second select policy for `authenticated` with the same test, so an admin
-- reads closed jobs too (the public policy shows open ones only). The column grant on `jobs` is untouched: `description`
-- and `details` stay closed to every browser role, an admin included, and the lint of tests/test_migrations.py still
-- refuses any grant of either. The admin page never shows the text of a posting.
--
-- 2.2 `public.admin_crawl_health()`. PostgREST returns 1000 rows at most and `crawl_log` holds about 24,000, so the
-- counting is done here. It is `security invoker`: it runs as the caller, so the two policies above are the gate. A
-- signed-in non-admin gets zero rows, `anon` a permission error (execute is granted to `authenticated` only). It reads
-- `crawl_log` and the key columns of `jobs` (company, status), never a text column.
--
-- One row per company that has a row in the log in the last 7 days before the newest crawl day (a company that is no
-- longer crawled drops out, as in the coverage gaps of run.py). Ordered by company; the page sorts worst first. The
-- columns, in order:
--   company          text     the company name
--   last_crawl       text     `ts` of the company's last log row (ISO local time, like the log)
--   last_good        text     `ts` of its last row with ok = 1, null when it never had one in the 90 days kept
--   last_ok          boolean  the last row's result (ok = 1)
--   last_note        text     the last row's note, null when it is empty
--   streak           integer  consecutive crawl days whose last run has ok = 0, robots.txt skips left out (a day with
--                             only skips is not counted and does not break the streak); 0 when the last such day is good
--   failing          boolean  the last `failing_runs` (2) crawls that are not robots.txt skips all have ok = 0, and the
--                             last row is neither a skip nor a held crawl (a held company is `held`, not `failing`, as
--                             in run.coverage_gaps)
--   held             boolean  the last row's note starts with `drop_note` (a sharp drop: the crawl was held, nothing closed)
--   robots_skipped   boolean  the last row's note starts with `robots_note` (skipped on purpose, a decision not a failure)
--   open_jobs        integer  jobs of the company in `jobs` with status = 'open' now
--   fetched_30d_ago  integer  `n_jobs` of the company's last row 30 to 37 days ago, null when there is none (the log may
--                             read short); a FETCHED count, so it is 0 after a failed fetch or a robots.txt skip and the
--                             fetched count in a held crawl, not the number of open jobs then
-- The four literals of the `const` CTE are copies of DROP_NOTE and ROBOTS_NOTE (common.py), FAILING_RUNS and
-- GAP_STALE_DAYS (run.py): tests/test_migrations.py fails when they differ. Nothing here is applied by the pull request:
-- apply it once, see supabase/README.md.

grant select on public.crawl_log to authenticated;

create policy "an admin reads the crawl log" on public.crawl_log
  for select to authenticated using (public.is_admin());

create policy "an admin reads all jobs" on public.jobs
  for select to authenticated using (public.is_admin());

create function public.admin_crawl_health()
returns table (
  company text,
  last_crawl text,
  last_good text,
  last_ok boolean,
  last_note text,
  streak integer,
  failing boolean,
  held boolean,
  robots_skipped boolean,
  open_jobs integer,
  fetched_30d_ago integer
)
language sql
stable
security invoker
set search_path = ''
as $$
  with const as (
    select
      'sharp drop'::text as drop_note,
      'robots.txt does not allow it'::text as robots_note,
      2 as failing_runs,
      7 as stale_days,
      30 as back_days,
      7 as back_slack
  ),
  runs as (
    select l.company, l.ts, left(l.ts, 10) as day, l.ok, l.n_jobs, l.id, coalesce(l.note, '') as note,
           starts_with(coalesce(l.note, ''), c.robots_note) as skipped
    from public.crawl_log l cross join const c
    where l.company is not null
  ),
  last_run as (
    select distinct on (company) company, ts, day, ok, note, skipped
    from runs
    order by company, ts desc, id desc
  ),
  good as (select company, max(ts) as ts from runs where ok = 1 group by company),
  days as (
    select distinct on (company, day) company, day, ok
    from runs
    where not skipped
    order by company, day, ts desc, id desc
  ),
  good_day as (select company, max(day) as day from days where ok = 1 group by company),
  streaks as (
    select d.company, count(*) as n
    from days d left join good_day g on g.company = d.company
    where d.ok = 0 and d.day > coalesce(g.day, '')
    group by d.company
  ),
  recent as (
    select company, ok, row_number() over (partition by company order by ts desc, id desc) as n
    from runs
    where not skipped
  ),
  recent_failed as (
    select r.company, count(*) filter (where r.ok = 1) = 0 as failed
    from recent r cross join const c
    where r.n <= c.failing_runs
    group by r.company
  ),
  open_now as (select company, count(*) as n from public.jobs where status = 'open' group by company),
  back as (
    select distinct on (r.company) r.company, r.n_jobs
    from runs r cross join const c
    where r.day <= to_char(current_date - c.back_days, 'yyyy-mm-dd')
      and r.day >= to_char(current_date - c.back_days - c.back_slack, 'yyyy-mm-dd')
    order by r.company, r.ts desc, r.id desc
  ),
  newest as (select max(day) as day from runs)
  select
    lr.company,
    lr.ts,
    g.ts,
    lr.ok = 1,
    nullif(lr.note, ''),
    coalesce(s.n, 0)::integer,
    not lr.skipped and not starts_with(lr.note, c.drop_note) and coalesce(rf.failed, false),
    starts_with(lr.note, c.drop_note),
    lr.skipped,
    coalesce(o.n, 0)::integer,
    b.n_jobs
  from last_run lr
  cross join const c
  cross join newest nw
  left join good g on g.company = lr.company
  left join streaks s on s.company = lr.company
  left join recent_failed rf on rf.company = lr.company
  left join open_now o on o.company = lr.company
  left join back b on b.company = lr.company
  where lr.day >= to_char(nw.day::date - c.stale_days, 'yyyy-mm-dd')
  order by lr.company;
$$;

revoke all on function public.admin_crawl_health() from public, anon, authenticated;
grant execute on function public.admin_crawl_health() to authenticated;
