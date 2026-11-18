-- Tests for supabase/migrations/20261130100000_companies.sql (PLAN_2.md step 3.1): the denial first (a signed-out visitor
-- and a signed-in non-admin), what each role reads, what an admin may write, every check refusing what it must by its
-- name, the limit of 200 excluded records, the log trigger and the load of the 347 records. Run it against a TEST
-- database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/companies.sql
-- Ends with "all company checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into auth.users (id, email) values
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'admin@test.example'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'other@test.example');
insert into public.admins (user_id) values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');

-- Acting as a role, signed in as `who` when given (the role and the claims PostgREST sets from the token).
create function pg_temp.act_as(role_name text, who uuid) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', coalesce(json_build_object('sub', who, 'role', role_name)::text, ''), true);
  perform set_config('request.jwt.claim.sub', coalesce(who::text, ''), true);
  perform set_config('role', role_name, true);
end;
$$;

-- The statement `sql` run as the role, true when it is refused with a permission error (also what row-level security
-- says to a row it does not allow in an insert).
create function pg_temp.denied(role_name text, who uuid, sql text) returns boolean language plpgsql as $$
begin
  perform pg_temp.act_as(role_name, who);
  execute sql;
  reset role;
  return false;
exception when insufficient_privilege then
  reset role;
  return true;
end;
$$;

-- How many rows the statement `sql` (an insert, update or delete) changed for the role. Row-level security does not
-- refuse an update or a delete of a row the role cannot see or change: it changes none.
create function pg_temp.changed(role_name text, who uuid, sql text) returns bigint language plpgsql as $$
declare n bigint;
begin
  perform pg_temp.act_as(role_name, who);
  execute sql;
  get diagnostics n = row_count;
  reset role;
  return n;
end;
$$;

-- How many rows the statement `sql` (a `select count(*) ...`) counts for the role.
create function pg_temp.count_as(role_name text, who uuid, sql text) returns bigint language plpgsql as $$
declare n bigint;
begin
  perform pg_temp.act_as(role_name, who);
  execute sql into n;
  reset role;
  return n;
end;
$$;

-- The name of the constraint (or index) that refuses the statement `sql`, 'accepted' when it goes through, or
-- 'error ' and the SQLSTATE when something else refuses it. Each call is its own subtransaction.
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

-- An exclusion as a jsonb literal.
create function pg_temp.exc(reason text, day text) returns text language sql as $$
  select quote_literal(jsonb_build_object('reason', reason, 'date_added', day)::text) || '::jsonb'
$$;

-- ---- The load: the records of data/companies.json at the commit of the migration ----

do $$
begin
  assert (select count(*) from public.companies) = 347, 'the load did not leave 347 records';
  assert (select count(*) from public.companies where crawl) = 267, 'not 267 crawled records';
  assert (select count(*) from public.companies where gap is not null) = 80, 'not 80 gap records';
  assert (select count(*) from public.companies where excluded is not null) = 0, 'an excluded record was loaded';
  assert (select count(*) from public.companies where crawl and gap is not null) = 0, 'a record is both crawled and gap';
  assert (select careers_url from public.companies where name = 'Verbit') = 'https://verbit.ai/careers/', 'Verbit is not https';
  assert (select count(*) from public.admin_log) = 0, 'the load wrote log rows';
  assert (select count(distinct id) from public.companies) = 347, 'ids are not distinct';
end $$;

-- Columns, in order: the keys of common.RECORD_KEYS, `excluded`, and the table's own two. The checks, the indexes and
-- the policies are exactly the documented ones.
do $$
begin
  assert (select array_agg(column_name::text order by ordinal_position) from information_schema.columns
          where table_schema = 'public' and table_name = 'companies') = array[
    'id', 'name', 'domain', 'segment', 'crawl', 'careers_url', 'ats', 'slug', 'source', 'status', 'israel_only',
    'verify', 'links', 'gap', 'excluded', 'updated_at'], 'companies has other columns than expected';
  assert (select count(*) from pg_index where indrelid = 'public.companies'::regclass and indisprimary) = 1, 'no primary key';
  assert (select relrowsecurity from pg_class where oid = 'public.companies'::regclass), 'row-level security is off';
  assert (select array_agg(conname::text order by conname) from pg_constraint
          where conrelid = 'public.companies'::regclass and contype = 'c') = array[
    'companies_ats_known', 'companies_careers_https', 'companies_domain_required', 'companies_exactly_one_status',
    'companies_excluded_date', 'companies_excluded_keys', 'companies_excluded_reason', 'companies_excluded_shape',
    'companies_gap_reason', 'companies_links_https', 'companies_name_min_length'], 'the checks are not the documented ones';
  assert (select array_agg(indexname::text order by indexname) from pg_indexes
          where schemaname = 'public' and tablename = 'companies') = array[
    'companies_domain_unique', 'companies_name_unique', 'companies_pkey'], 'the indexes are not the documented ones';
  assert (select array_agg(policyname::text order by policyname) from pg_policies where tablename = 'companies') = array[
    'an admin adds companies', 'an admin changes companies', 'an admin deletes companies', 'an admin reads every company',
    'anyone reads the companies that are not excluded'], 'the policies are not the documented ones';
end $$;

-- Grants: anon and signed-in read; only signed-in (and then only an admin, by the policies) writes; nothing else.
do $$
declare r text;
begin
  foreach r in array array['insert', 'update', 'delete', 'truncate', 'references', 'trigger'] loop
    assert not has_table_privilege('anon', 'public.companies', r), 'anon has ' || r;
    assert not has_table_privilege('public', 'public.companies', r), 'public has ' || r;
  end loop;
  assert has_table_privilege('anon', 'public.companies', 'select'), 'anon cannot select';
  assert not has_table_privilege('public', 'public.companies', 'select'), 'public can select';
  foreach r in array array['select', 'insert', 'update', 'delete'] loop
    assert has_table_privilege('authenticated', 'public.companies', r), 'authenticated misses ' || r;
  end loop;
  foreach r in array array['truncate', 'references', 'trigger'] loop
    assert not has_table_privilege('authenticated', 'public.companies', r), 'authenticated has ' || r;
  end loop;
end $$;

-- Both trigger functions are closed to everybody (the triggers run them); the log one is the definer.
do $$
declare f text;
begin
  foreach f in array array['public.companies_before_write()', 'public.companies_log_change()'] loop
    assert not has_function_privilege('anon', f, 'execute'), 'anon runs ' || f;
    assert not has_function_privilege('authenticated', f, 'execute'), 'authenticated runs ' || f;
    assert not has_function_privilege('public', f, 'execute'), 'public runs ' || f;
    assert (select proconfig = array['search_path=""'] from pg_proc where oid = f::regprocedure), f || ' does not pin its search path';
  end loop;
  assert (select prosecdef from pg_proc where oid = 'public.companies_log_change()'::regprocedure), 'the log function is not security definer';
  assert not (select prosecdef from pg_proc where oid = 'public.companies_before_write()'::regprocedure), 'the guard is security definer';
  assert (select array_agg(tgname::text order by tgname) from pg_trigger
          where tgrelid = 'public.companies'::regclass and not tgisinternal) = array['companies_before_write', 'companies_log_change'];
end $$;

-- ---- The denial first ----

-- An excluded record, written by the owner (no log row: not an admin), so the reads below have one to hide.
insert into public.companies (name, crawl, excluded)
values ('Hidden Co', false, '{"reason": "asked to be removed", "date_added": "2026-11-18"}');
do $$
begin
  assert (select count(*) from public.companies) = 348, 'the owner could not add an excluded record';
  assert (select count(*) from public.admin_log) = 0, 'a write by the owner wrote a log row';
end $$;

-- Signed out: reads the open list, and every write is a permission error.
do $$
declare sql text;
begin
  foreach sql in array array[
    'insert into public.companies (name, domain, crawl) values (''Anon Co'', ''anon.test'', true)',
    'update public.companies set slug = ''x''',
    'delete from public.companies',
    'truncate public.companies'
  ] loop
    assert pg_temp.denied('anon', null, sql), 'anon was not refused: ' || sql;
  end loop;
  assert pg_temp.count_as('anon', null, 'select count(*) from public.companies') = 347, 'anon does not read the 347 records';
  assert pg_temp.count_as('anon', null, 'select count(*) from public.companies where excluded is not null') = 0, 'anon read an excluded record';
  assert pg_temp.count_as('anon', null, 'select count(*) from public.companies where name = ''Hidden Co''') = 0, 'anon found an excluded record by name';
end $$;

-- Signed in but not listed (or unknown, or a token with no user): the same reads, no insert, and an update or a
-- delete changes no row. Nothing is logged.
do $$
declare
  who uuid;
begin
  foreach who in array array['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid, null] loop
    assert pg_temp.denied('authenticated', who, 'insert into public.companies (name, domain, crawl) values (''Other Co'', ''other.test'', true)'),
      who || ' could insert';
    assert pg_temp.denied('authenticated', who, 'truncate public.companies'), who || ' could truncate';
    assert pg_temp.changed('authenticated', who, 'update public.companies set slug = ''x''') = 0, who || ' changed a record';
    assert pg_temp.changed('authenticated', who, 'update public.companies set slug = ''x'' where name = ''Hidden Co''') = 0, who || ' changed an excluded record';
    assert pg_temp.changed('authenticated', who, 'delete from public.companies') = 0, who || ' deleted a record';
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.companies') = 347, who || ' does not read the 347 records';
    assert pg_temp.count_as('authenticated', who, 'select count(*) from public.companies where excluded is not null') = 0, who || ' read an excluded record';
  end loop;
  assert (select count(*) from public.companies) = 348, 'a refused statement changed the list';
  assert (select count(*) from public.companies where slug = 'x') = 0, 'a refused update changed a slug';
  assert (select count(*) from public.admin_log) = 0, 'a refused statement was logged';
end $$;

-- ---- What the admin does (as the role `authenticated`, so row-level security is tested and not bypassed) ----

do $$
declare
  admin uuid := 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
begin
  -- Reads every record, the excluded one included. A read writes no log row.
  assert pg_temp.count_as('authenticated', admin, 'select count(*) from public.companies') = 348, 'the admin does not read all 348';
  assert pg_temp.count_as('authenticated', admin, 'select count(*) from public.companies where excluded is not null') = 1, 'the admin does not read the excluded record';
  assert (select count(*) from public.admin_log) = 0, 'a read wrote a log row';
  assert pg_temp.denied('authenticated', admin, 'truncate public.companies'), 'the admin could truncate';

  -- Adds a company: one log row, with the name and nothing else.
  assert pg_temp.changed('authenticated', admin, 'insert into public.companies (name, domain, crawl) values (''Added Co'', ''added.test'', true)') = 1, 'the admin could not add';
  assert (select count(*) from public.admin_log) = 1, 'an add did not write exactly one log row';
  assert (select action = 'company added' and target = 'Added Co' and ts is not null from public.admin_log), 'the add row is wrong';

  -- Changes it: one more row. The client cannot set updated_at.
  assert pg_temp.changed('authenticated', admin, 'update public.companies set slug = ''s'', updated_at = ''2000-01-01'' where name = ''Added Co''') = 1, 'the admin could not change';
  assert (select updated_at = now() from public.companies where name = 'Added Co'), 'updated_at took the value of the client';
  assert (select count(*) from public.admin_log where action = 'company changed' and target = 'Added Co') = 1, 'a change did not write exactly one row';

  -- An exclusion that leaves another field set is refused by the shape check, for the admin as for anybody, and
  -- nothing is logged for it.
  begin
    perform pg_temp.changed('authenticated', admin, 'update public.companies set crawl = false, excluded = ' || pg_temp.exc('why', '2026-11-18') || ' where name = ''Added Co''');
    assert false, 'the admin could exclude without clearing the other fields';
  exception when check_violation then
    reset role;
  end;

  -- Excludes it the way the editor does: the reason and every other field in one write. The log names the company and
  -- never the reason.
  assert pg_temp.changed('authenticated', admin,
    'update public.companies set crawl = false, domain = null, segment = null, careers_url = null, ats = null, slug = null, source = null, status = null, '
    || 'israel_only = null, verify = null, links = null, gap = null, excluded = ' || pg_temp.exc('secret reason', '2026-11-18') || ' where name = ''Added Co''') = 1,
    'the admin could not exclude';
  assert (select count(*) from public.admin_log) = 3, 'the exclusion did not write exactly one row, or a refused write was logged';
  assert (select count(*) from public.admin_log where target = 'Added Co' and action in ('company added', 'company changed')) = 3, 'the log rows are not the three expected';
  assert (select count(*) from public.admin_log where action ilike '%secret%' or target ilike '%secret%') = 0, 'the reason reached the log';

  -- Deletes it: one more row.
  assert pg_temp.changed('authenticated', admin, 'delete from public.companies where name = ''Added Co''') = 1, 'the admin could not delete';
  assert (select count(*) from public.admin_log where action = 'company removed' and target = 'Added Co') = 1, 'a delete did not write one row';
  assert (select count(*) from public.admin_log) = 4, 'the log is not four rows';
  assert pg_temp.count_as('authenticated', admin, 'select count(*) from public.admin_log') = 4, 'the admin does not read the log';

  -- The service role (the pipeline of step 3.2) writes and logs nothing.
  assert pg_temp.changed('service_role', null, 'update public.companies set slug = slug where name = ''Adobe''') = 1, 'the service role could not write';
  assert pg_temp.changed('service_role', null, 'insert into public.companies (name, domain, crawl) values (''Pipeline Co'', ''pipeline.test'', true)') = 1;
  assert (select count(*) from public.admin_log) = 4, 'a write by the service role was logged';
  delete from public.companies where name = 'Pipeline Co';
  assert (select count(*) from public.admin_log) = 4, 'a write by the owner was logged';
end $$;

-- ---- The checks: each refuses what it must, by its name ----

do $$
declare
  ex text := pg_temp.exc('a reason', '2026-11-18');
  col text;
begin
  -- unique ignoring case
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''ADOBE'', ''new1.test'', true)') = 'companies_name_unique';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''adobe'', ''new1.test'', true)') = 'companies_name_unique';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''New1'', ''ADOBE.COM'', true)') = 'companies_domain_unique';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''hidden co'', false, ' || ex || ')') = 'companies_name_unique';
  -- the name
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''A'', ''a.test'', true)') = 'companies_name_min_length';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values ('' Ab'', ''a.test'', true)') = 'companies_name_min_length';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''Ab '', ''a.test'', true)') = 'companies_name_min_length';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''Ab'', ''ab.test'', true)') = 'accepted';
  -- the domain
  assert pg_temp.violated('insert into public.companies (name, crawl) values (''NoDomain'', true)') = 'companies_domain_required';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''Blank'', ''  '', true)') = 'companies_domain_required';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''Gone1'', false, ' || ex || ')') = 'accepted', 'an excluded record has no domain';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''Gone2'', false, ' || ex || ')') = 'accepted', 'two excluded records, both with no domain';
  -- exactly one of crawl, gap, excluded
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''None'', ''none.test'', false)') = 'companies_exactly_one_status', 'none set';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, gap) values (''Two'', ''two.test'', true, ''{"reason": "no_careers_url"}'')') = 'companies_exactly_one_status';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''Two'', true, ' || ex || ')') = 'companies_exactly_one_status';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, gap, excluded) values (''Three'', ''three.test'', false, ''{"reason": "no_openings"}'', ' || ex || ')') = 'companies_exactly_one_status';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl) values (''Crawled'', ''crawled.test'', true)') = 'accepted';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, gap) values (''Carded'', ''carded.test'', false, ''{"reason": "no_openings", "note": "closed"}'')') = 'accepted';
  -- the gap
  foreach col in array array['{}', '{"reason": "made_up"}', '{"reason": 5}', '{"reason": "no_openings", "who": "x"}', '{"reason": "no_openings", "note": 5}', '[]', 'null', '"no_openings"'] loop
    assert pg_temp.violated('insert into public.companies (name, domain, crawl, gap) values (''G'' || ' || quote_literal(col) || ', ''g.test'', false, ' || quote_literal(col) || '::jsonb)') = 'companies_gap_reason', 'gap ' || col;
  end loop;
  -- the careers address and the links
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, careers_url) values (''Http'', ''http.test'', true, ''http://x.test/careers'')') = 'companies_careers_https';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, careers_url) values (''Ftp'', ''ftp.test'', true, ''ftp://x.test/'')') = 'companies_careers_https';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, careers_url) values (''Https'', ''https.test'', true, ''https://x.test/careers'')') = 'accepted';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, careers_url) values (''Empty'', ''empty.test'', true, '''')') = 'accepted';
  foreach col in array array['{"israel": "http://x.test/"}', '{"global": "x.test"}', '{"other": "https://x.test/"}', '{"global": 5}', '{"global": null}', '[]', 'null'] loop
    assert pg_temp.violated('insert into public.companies (name, domain, crawl, links) values (''L'' || ' || quote_literal(col) || ', ''l.test'', true, ' || quote_literal(col) || '::jsonb)') = 'companies_links_https', 'links ' || col;
  end loop;
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, links) values (''Links'', ''links.test'', true, ''{"israel": "https://x.test/il", "global": "https://x.test/"}'')') = 'accepted';
  -- the feed
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats) values (''Taleo'', ''taleo.test'', true, ''taleo'')') = 'companies_ats_known';
  assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats) values (''Case'', ''case.test'', true, ''Greenhouse'')') = 'companies_ats_known';
  foreach col in array array['browser', 'phenom', 'comeet_page', 'greenhouse', ''] loop
    assert pg_temp.violated('insert into public.companies (name, domain, crawl, ats) values (''Ats-'' || ' || quote_literal(col) || ', ''ats' || col || '.test'', true, ' || quote_literal(col) || ')') = 'accepted', 'ats ' || col;
  end loop;
  -- the exclusion
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E1'', false, ''{"reason": "x", "date_added": "2026-11-18", "who": "y"}'')') = 'companies_excluded_keys';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E2'', false, ''[]'')') = 'companies_excluded_date', 'an array is refused, by the first check that sees it';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E3'', false, ''{"date_added": "2026-11-18"}'')') = 'companies_excluded_keys';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E4'', false, ' || pg_temp.exc('   ', '2026-11-18') || ')') = 'companies_excluded_reason';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E5'', false, ' || pg_temp.exc('', '2026-11-18') || ')') = 'companies_excluded_reason';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E6'', false, ''{"reason": 5, "date_added": "2026-11-18"}'')') = 'companies_excluded_reason';
  foreach col in array array['18/11/2026', '2026-1-5', '2026-11-18T10:00', 'today', ''] loop
    assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''D'' || ' || quote_literal(col) || ', false, ' || pg_temp.exc('x', col) || ')') = 'companies_excluded_date', 'date ' || col;
  end loop;
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E7'', false, ''{"reason": "x", "date_added": null}'')') = 'companies_excluded_date';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E8'', false, ''{"reason": "x"}'')') = 'companies_excluded_date';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''E9'', false, ' || pg_temp.exc('x', '2026-02-30') || ')') like 'error %', 'an impossible date is refused, but not by a named check';
  -- an excluded record holds nothing else: each other field set on its own is refused by the shape check
  foreach col in array array['domain = ''x.test''', 'segment = ''s''', 'careers_url = ''''', 'ats = ''''', 'slug = ''''', 'source = ''''',
                              'status = ''''', 'israel_only = false', 'verify = false', 'links = ''{}'''] loop
    assert pg_temp.violated('update public.companies set ' || col || ' where name = ''Gone1''') = 'companies_excluded_shape', 'shape ' || col;
  end loop;
  assert pg_temp.violated('update public.companies set crawl = true where name = ''Gone1''') = 'companies_exactly_one_status';
  assert pg_temp.violated('update public.companies set gap = ''{"reason": "no_openings"}'' where name = ''Gone1''') = 'companies_exactly_one_status';
  -- and an excluded record can be included again: crawl or a gap, the exclusion and nothing else changed
  assert pg_temp.violated('update public.companies set excluded = null, crawl = true, domain = ''gone1.test'' where name = ''Gone1''') = 'accepted';
  assert pg_temp.violated('update public.companies set excluded = null, crawl = false, domain = ''gone2.test'', gap = ''{"reason": "needs_reader", "note": ""}'' where name = ''Gone2''') = 'accepted';
end $$;

-- ---- At most 200 excluded records (a trigger) ----

do $$
declare n int;
begin
  insert into public.companies (name, crawl, excluded)
  select 'Bulk ' || i, false, '{"reason": "r", "date_added": "2026-11-18"}'
  from generate_series(1, 200 - (select count(*) from public.companies where excluded is not null)::int) i;
  assert (select count(*) from public.companies where excluded is not null) = 200, 'could not fill up to 200';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''One too many'', false, ' || pg_temp.exc('r', '2026-11-18') || ')') = 'companies_excluded_limit', 'the 201st insert';
  assert pg_temp.violated('update public.companies set crawl = false, domain = null, segment = null, careers_url = null, ats = null, slug = null, source = null, status = null, '
    || 'israel_only = null, verify = null, links = null, gap = null, excluded = ' || pg_temp.exc('r', '2026-11-18') || ' where name = ''Adobe''') = 'companies_excluded_limit', 'excluding a 201st by update';
  assert (select excluded is null from public.companies where name = 'Adobe'), 'a refused exclusion changed the record';
  assert pg_temp.violated('update public.companies set excluded = ' || pg_temp.exc('changed', '2026-11-19') || ' where name = ''Bulk 1''') = 'accepted', 'a record already excluded can change at 200';
  delete from public.companies where name = 'Bulk 1';
  assert pg_temp.violated('insert into public.companies (name, crawl, excluded) values (''One more'', false, ' || pg_temp.exc('r', '2026-11-18') || ')') = 'accepted', 'a place was freed';
  assert (select count(*) from public.companies where excluded is not null) = 200;
end $$;

-- Removing the admin from the list closes the writes again.
delete from public.admins;
do $$
begin
  assert pg_temp.denied('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'insert into public.companies (name, domain, crawl) values (''Late Co'', ''late.test'', true)'), 'a removed admin can still add';
  assert pg_temp.changed('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'delete from public.companies') = 0, 'a removed admin can still delete';
  assert pg_temp.count_as('authenticated', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'select count(*) from public.companies where excluded is not null') = 0, 'a removed admin still reads the excluded';
end $$;

rollback;

select 'all company checks passed' as result;
