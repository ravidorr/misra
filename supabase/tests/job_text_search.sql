-- Tests for supabase/migrations/20261124100000_jobs_text_search.sql: search_job_keys gives anon and authenticated the keys
-- of the open jobs whose text holds the words and nothing else (the text itself stays closed), refuses what is too short,
-- never raises on what a person types, is cut at 3000 keys, and the service role may call it. Run it against a TEST
-- database, never production.
--   psql "$TEST_DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/tests/job_text_search.sql
-- Ends with "all job text search checks passed". Plain SQL (assert), one transaction that is rolled back.

begin;

insert into public.jobs (company, job_id, title, status, description) values
  ('Acme', 'k1', 'Engineer',  'open',   'We build rockets with Rust and Postgres in Haifa'),
  ('Acme', 'k2', 'Designer',  'open',   'Figma and typography, no engines here'),
  ('Acme', 'k3', 'Closed',    'closed', 'Rust rockets, but this job is closed'),
  ('Beta', 'k4', 'No text',   'open',   null),
  ('Beta', 'k5', 'Hebrew',    'open',   'משרה בחיפה עם Rust'),
  ('Beta', 'k6', 'Short word', 'open',   'We write go and a little C');

create function pg_temp.keys(role_name text, query text) returns text language plpgsql as $$
declare found jsonb;
begin
  execute format('set local role %I', role_name);
  select public.search_job_keys(query) into found;
  reset role;
  return found::text;
end;
$$;

-- anon, authenticated and the service role find the keys of the open jobs, in a stable order, and only the keys.
do $$
declare who text;
begin
  foreach who in array array['anon', 'authenticated', 'service_role'] loop
    assert pg_temp.keys(who, 'rust') = '[["Acme", "k1"], ["Beta", "k5"]]', who || ' got ' || pg_temp.keys(who, 'rust');
    assert pg_temp.keys(who, 'Rust ROCKETS') = '[["Acme", "k1"]]', who || ' does not match every word';
    assert pg_temp.keys(who, 'typography') = '[["Acme", "k2"]]', who || ' misses a word in another job';
    assert pg_temp.keys(who, 'בחיפה') = '[["Beta", "k5"]]', who || ' misses Hebrew';
  end loop;
end $$;

-- A closed job, a job with no text and a word nobody has are not returned; the answer is an empty array, not null.
do $$
begin
  assert pg_temp.keys('anon', 'closed') = '[]', 'a word of a closed job''s text found it';
  assert pg_temp.keys('anon', 'unobtainium') = '[]', 'a word nobody has found something';
  assert not (pg_temp.keys('anon', 'rust') like '%k3%'), 'a closed job is returned';
  assert not (pg_temp.keys('anon', 'rust') like '%k4%'), 'a job without text is returned';
end $$;

-- Too short (after trimming), empty or null: nothing, and no error. Long: cut, no error.
do $$
begin
  assert pg_temp.keys('anon', 'ru') = '[]', 'two characters searched';
  assert pg_temp.keys('anon', '  ru  ') = '[]', 'two characters and spaces searched';
  assert pg_temp.keys('anon', '"a"') = '[]' and pg_temp.keys('anon', 'a a') = '[]', 'a short word probe searched';
  assert pg_temp.keys('anon', 'a b') = '[]', 'two short words searched';
  -- punctuation must not make a short word long enough: k6's text has the words "go", "a" and "c"
  assert pg_temp.keys('anon', 'go!') = '[]' and pg_temp.keys('anon', '"go"') = '[]' and pg_temp.keys('anon', 'a!!') = '[]',
    'a short word padded with punctuation searched';
  assert pg_temp.keys('anon', 'little') = '[["Beta", "k6"]]', 'a word of three letters or more does not search';
  assert pg_temp.keys('anon', '') = '[]', 'the empty query searched';
  assert pg_temp.keys('anon', null) = '[]', 'null searched';
  assert pg_temp.keys('anon', 'rust ' || repeat('x', 5000)) = '[]', 'a long query errored or matched';
end $$;

-- What a person types never raises: operators, quotes, unbalanced brackets, a minus sign, a lone `&`.
do $$
declare q text;
begin
  foreach q in array array['rust & | !', '"rust rockets"', 'rust -rockets', '(rust', 'rust)))', '''', 'rust:*', 'a b c <-> d'] loop
    perform pg_temp.keys('anon', q);
  end loop;
  -- no operator works: a phrase, a minus sign and "or" are words, so they cannot ask for order or for the jobs without a word
  assert pg_temp.keys('anon', '"rust rockets"') = pg_temp.keys('anon', 'rust rockets'), 'a quoted phrase is an operator';
  assert pg_temp.keys('anon', '-rockets') = pg_temp.keys('anon', 'rockets'), 'a minus sign is a negation';
  assert pg_temp.keys('anon', 'rust -rockets') = pg_temp.keys('anon', 'rust rockets'), 'a minus sign excludes';
  assert pg_temp.keys('anon', 'figma or rust') = '[]', 'or is an operator';
end $$;

-- The function is the only way in: it is a definer function with a pinned search path that returns jsonb, the two roles
-- may run it, the public pseudo-role may not, and the text columns are still closed.
do $$
declare who text; q record;
begin
  select prosecdef, prorettype::regtype::text as ret, proconfig, provolatile into q
    from pg_proc where oid = 'public.search_job_keys(text)'::regprocedure;
  assert q.prosecdef, 'the search is not a definer function';
  assert q.ret = 'jsonb', 'the search returns ' || q.ret;
  assert q.proconfig = array['search_path=""'], 'the search path is not pinned';
  assert q.provolatile = 's', 'the search is not stable';
  assert has_function_privilege('anon', 'public.search_job_keys(text)', 'execute'), 'anon cannot search';
  assert has_function_privilege('authenticated', 'public.search_job_keys(text)', 'execute'), 'authenticated cannot search';
  assert has_function_privilege('service_role', 'public.search_job_keys(text)', 'execute'), 'service_role cannot search';
  assert not has_function_privilege('public', 'public.search_job_keys(text)', 'execute'), 'public can search';
  foreach who in array array['anon', 'authenticated'] loop
    assert not has_column_privilege(who, 'public.jobs', 'description', 'select'), who || ' can read description';
    assert not has_column_privilege(who, 'public.jobs', 'details', 'select'), who || ' can read details';
    assert not has_table_privilege(who, 'public.jobs', 'select'), who || ' has a table-level select';
  end loop;
end $$;

-- The index is the one the function's expression uses (a bitmap scan on it, not a sequential one, once the table is big
-- enough to prefer it).
insert into public.jobs (company, job_id, title, status, description)
  select 'Bulk', 'b' || n, 'Bulk ' || n, 'open', 'filler words number ' || n from generate_series(1, 2000) n;
analyze public.jobs;
do $$
declare plan text; line text;
begin
  set local enable_seqscan = off;
  for line in explain select company from public.jobs
    where to_tsvector('simple', coalesce(description, '')) @@ websearch_to_tsquery('simple', 'rockets') loop
    plan := coalesce(plan, '') || line;
  end loop;
  assert plan like '%jobs_description_search%', 'the full-text index is not used: ' || plan;
end $$;

-- At most 3000 keys: more matches than that are cut, in company and id order, and the answer is still one value.
insert into public.jobs (company, job_id, title, status, description)
  select 'Many', to_char(n, 'FM00000'), 'Many ' || n, 'open', 'commonword ' || n from generate_series(1, 3100) n;
do $$
declare found jsonb;
begin
  found := public.search_job_keys('commonword');
  assert jsonb_array_length(found) = 3000, 'the answer has ' || jsonb_array_length(found) || ' keys, not 3000';
  assert found -> 0 = '["Many", "00001"]'::jsonb, 'the order is not company, id';
  assert found -> 2999 = '["Many", "03000"]'::jsonb, 'the cut is not at 3000';
end $$;

rollback;
\echo all job text search checks passed
