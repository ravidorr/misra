-- The job feeds `teamtailor`, `pinpoint` and `jobvite` (Vicarius, myInterview, ForeScout) join the feeds the crawler
-- knows. The check `companies_ats_known`, last set by 20261207100000_companies_ats_breezy.sql, lists the keys of
-- `adapters.ADAPTERS` plus `browser`; new adapters are new keys, so the check is dropped and added again with the list
-- that has them. Nothing else changes, and no row is written: a row that names a feed is pinned afterwards on the
-- Companies area of the admin page. Every row already in the table passes (none names the new feeds yet, and the old
-- names are all still in the list). tests/test_migrations_companies.py and tests/test_admin_companies.py keep this list
-- equal to `adapters.ADAPTERS` and to the admin page's `PIN_ATS`;
-- supabase/tests/companies_ats_teamtailor_pinpoint_jobvite.sql tests the check. Not applied by the pull request: apply it
-- once after the merge, see supabase/README.md.

alter table public.companies drop constraint companies_ats_known;

alter table public.companies add constraint companies_ats_known check (
  ats is null or ats = '' or ats in (
    'greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet', 'workday', 'jazzhr',
    'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'avature', 'eightfold', 'jibe', 'varonis', 'breezy',
    'teamtailor', 'pinpoint', 'jobvite', 'browser'
  ));
