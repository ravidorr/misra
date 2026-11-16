-- Search over the full text of a posting that returns job keys and nothing else (PLAN.md step 7.3; the lawyer's answers
-- of 2026-11-16: "the text is never readable via the public API; search runs through a DB function returning keys only").
--
-- `description` and `details` stay closed to anon and authenticated (migration 20261121100000_jobs_text_not_public.sql).
-- `search_job_keys` is the one way the public roles touch the text, and it hands back only which jobs contain the words:
-- a jsonb array of [company, job_id] pairs, never a character of the text, a rank or a snippet of it. It is SECURITY
-- DEFINER (it reads a column the caller cannot), so the guards are in the function and not in row-level security:
--   * only open jobs, the same rows the jobs policy lets the public roles read;
--   * a query with no word of 3 or more characters matches nothing (so "a" or "to be" cannot ask which jobs contain a
--     short word), and one longer than 200 is cut to 200, so a huge query cannot be sent to the parser;
--   * at most 3000 keys come back (the table holds about 7,000 open jobs, and a word in more than 3000 of them is a stop
--     word), in a stable order, and a single jsonb value is not cut by the API's 1,000-row page;
--   * plainto_tsquery: the words are ANDed and nothing a person types is an operator (no quoted phrase, "or" or minus
--     sign), so the function answers "which jobs contain all these words" and cannot be asked for word order or for
--     "every job without a word". It never raises a syntax error either.
-- The words are matched as words with no stemming ('simple'): the postings are in English and Hebrew, and what the
-- dashboard's own keyword search does is also "these words, as typed". The GIN index below is on the same expression, so
-- the match does not scan the table. The service role (the daily digest) calls it too. Nothing here is applied by the
-- pull request: apply it once, see supabase/README.md.

create index jobs_description_search on public.jobs using gin (to_tsvector('simple', coalesce(description, '')));

create function public.search_job_keys(p_query text)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_agg(jsonb_build_array(found.company, found.job_id)), '[]'::jsonb)
    from (
      select j.company, j.job_id
        from public.jobs j
       where j.status = 'open'
         and coalesce(p_query, '') ~ '[^[:space:]]{3}'
         and to_tsvector('simple', coalesce(j.description, '')) @@ plainto_tsquery('simple', left(p_query, 200))
       order by j.company, j.job_id
       limit 3000
    ) found;
$$;

revoke all on function public.search_job_keys(text) from public, anon, authenticated;
grant execute on function public.search_job_keys(text) to anon, authenticated, service_role;
