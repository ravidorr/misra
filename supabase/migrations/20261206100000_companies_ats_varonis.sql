-- The job feed `varonis` (the dedicated reader of the careers site of Varonis, by the maintainer's decision of 2026-11-20) joins the feeds the
-- crawler knows. The check `companies_ats_known`, last set by 20261205100000_companies_ats_jibe.sql, lists the keys of
-- `adapters.ADAPTERS` plus `browser`; a new adapter is a new key, so the check is dropped and added again with the list
-- that has it. Nothing else changes, and no row is written: a row that names the feed is pinned afterwards on the
-- Companies area of the admin page. Every row already in the table passes (none names `varonis` yet, and the old names
-- are all still in the list). tests/test_migrations_companies.py and tests/test_admin_companies.py keep this list equal to
-- `adapters.ADAPTERS` and to the admin page's `PIN_ATS`; supabase/tests/companies_ats_varonis.sql tests the check. Not
-- applied by the pull request: apply it once after the merge, see supabase/README.md.

alter table public.companies drop constraint companies_ats_known;

alter table public.companies add constraint companies_ats_known check (
  ats is null or ats = '' or ats in (
    'greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet', 'workday', 'jazzhr',
    'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'avature', 'eightfold', 'jibe', 'varonis', 'browser'
  ));
