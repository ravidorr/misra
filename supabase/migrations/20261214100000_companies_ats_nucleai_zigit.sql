-- The job feeds `nucleai` and `zigit` join the feeds the crawler knows: the readers of the careers pages of Nucleai
-- (`nucleai.ai/careers/`) and ZIGIT (`www.zigit.co.il`), by the maintainer's decision of 2026-11-22 (both pages list
-- their roles as page text the browser reader does not read). The check `companies_ats_known`, last set by
-- 20261213100000_companies_ats_taleo.sql, lists the keys of `adapters.ADAPTERS` plus `browser`; a new adapter is a new
-- key, so the check is dropped and added again with the list that has them.
-- Nothing else changes, and no row is written: the rows that name the feeds are pinned afterwards on the Companies area
-- of the admin page. Every row already in the table passes (none names the new feeds yet, and the old names are all
-- still in the list). tests/test_migrations_companies.py and tests/test_admin_companies.py keep this list equal to
-- `adapters.ADAPTERS` and to the admin page's `PIN_ATS`; supabase/tests/companies_ats_nucleai_zigit.sql tests the
-- check. Not applied by the pull request: apply it once after the merge, see supabase/README.md.

alter table public.companies drop constraint companies_ats_known;

alter table public.companies add constraint companies_ats_known check (
  ats is null or ats = '' or ats in (
    'greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet', 'workday', 'jazzhr',
    'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'avature', 'eightfold', 'jibe', 'varonis', 'breezy',
    'teamtailor', 'pinpoint', 'jobvite', 'recruitee', 'deel', 'bulls_media', 'elbit', 'haat', 'taleo', 'nucleai', 'zigit',
    'browser'
  ));
