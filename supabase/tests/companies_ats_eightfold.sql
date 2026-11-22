-- Tests for supabase/migrations/20261204100000_companies_ats_eightfold.sql (PLAN_3.md steps E2 and E3): the check
-- `companies_ats_known` accepts the new feed `eightfold`, still accepts every feed it accepted before (and `browser` and an
-- empty feed), still refuses a name it does not know and a name in another case, is still one check of that name on the
-- table, and left every other check alone. Run it against a TEST database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/companies_ats_eightfold.sql
-- Ends with "all eightfold feed checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

-- The name of the constraint that refuses the statement `sql`, 'accepted' when it goes through, or 'error ' and the
-- SQLSTATE when something else refuses it. Each call is its own subtransaction.
create function pg_temp.violated(sql text) returns text language plpgsql as $$
declare c text;
begin
  execute sql;
  return 'accepted';
exception
  when check_violation or unique_violation then
    get stacked diagnostics c = constraint_name;
    return c;
  when others then
    return 'error ' || sqlstate;
end;
$$;

do $$
declare feed text;
begin
  assert (select count(*) from pg_constraint
          where conrelid = 'public.companies'::regclass and conname = 'companies_ats_known' and contype = 'c') = 1,
    'companies_ats_known is not exactly one check';
  assert (select array_agg(conname::text order by conname) from pg_constraint
          where conrelid = 'public.companies'::regclass and contype = 'c') = array[
    'companies_ats_known', 'companies_careers_https', 'companies_domain_required', 'companies_exactly_one_status',
    'companies_excluded_date', 'companies_excluded_keys', 'companies_excluded_reason', 'companies_excluded_shape',
    'companies_gap_reason', 'companies_links_https', 'companies_name_min_length'], 'the checks are not the documented ones';

  -- the new feed, with the slugs of the companies it is for
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats, slug) values (''Micron Test'', ''micron-test.test'', true, ''eightfold'', ''careers.micron.com?domain=micron.com'')') = 'accepted';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats, slug) values (''Lam Test'', ''lam-test.test'', true, ''eightfold'', ''lamresearch.eightfold.ai?domain=lamresearch.com'')') = 'accepted';

  -- every feed that was known before still is, and so is no feed at all
  foreach feed in array array['greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet',
    'workday', 'jazzhr', 'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'avature', 'browser', ''] loop
    assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats) values (''Old-'' || ' || quote_literal(feed) || ', ''old' || feed || '.test'', true, ' || quote_literal(feed) || ')') = 'accepted', 'ats ' || feed;
  end loop;

  -- an unknown name and a name in another case are refused by this check, by its name
  foreach feed in array array['personio', 'Eightfold', 'EIGHTFOLD', 'eightfold ', 'eightfolds', 'icims'] loop
    assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats) values (''Bad-'' || ' || quote_literal(feed) || ', ''bad' || md5(feed) || '.test'', true, ' || quote_literal(feed) || ')') = 'companies_ats_known', 'ats ' || feed;
  end loop;

  -- an update to the new feed is accepted, to an unknown one refused
  assert pg_temp.violated('update public.companies set ats = ''eightfold'' where name = ''Old-lever''') = 'accepted';
  assert pg_temp.violated('update public.companies set ats = ''personio'' where name = ''Old-lever''') = 'companies_ats_known';
end $$;

rollback;

select 'all eightfold feed checks passed' as result;
