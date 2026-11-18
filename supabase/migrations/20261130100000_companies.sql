-- The company list (PLAN_2.md step 3.1): `public.companies`, one row per company, loaded with the records of
-- data/companies.json as they were at this commit (347: 267 crawled, 80 on the coverage card, none excluded). Nothing
-- reads the table yet: the file stays the source of the pipeline until step 3.2 switches the sync over. Nothing here
-- is applied by the pull request: apply it once, see supabase/README.md. Applying it twice fails at `create table`.
--
-- Columns: one per key of `common.RECORD_KEYS`, plus `excluded` (step 3.2 adds the key to the Python side), plus two
-- of the table's own. `id` is the row's identity (so the editor of step 3.3 can rename a company without changing its
-- key; the name is unique ignoring case, below). `updated_at` is set by the trigger `companies_before_write` on every
-- write, so the editor can tell an edit from a crawl and refuse a stale one. `links`, `gap` and `excluded` are jsonb
-- ({israel, global}, {reason, note}, {reason, date_added}). A string a record does not have is null, and an
-- undiscovered one is '', as in the file, so the file loads back unchanged.
--
-- The checks, each with a name the editor maps to one plain sentence (supabase/README.md, "The company list"):
--   companies_name_unique        the name, ignoring case (unique index)
--   companies_domain_unique      the domain, ignoring case (unique index; excluded records have none)
--   companies_name_min_length    a name of 2 characters or more with no space at either end (common.EXCLUDED_MIN_NAME)
--   companies_domain_required    a domain, except on an excluded record
--   companies_exactly_one_status exactly one of: crawl is true, gap is not null, excluded is not null
--   companies_gap_reason         a gap is {reason, note} and the reason is a key of REASONS in build_dashboard.py
--   companies_careers_https      the careers address is empty or starts with https://
--   companies_links_https        a link of `links` (israel, global) starts with https://
--   companies_ats_known          ats is empty or a key of adapters.ADAPTERS, or `browser` (run.py routes it to the browser)
--   companies_excluded_keys      an exclusion is {reason, date_added} and nothing else
--   companies_excluded_reason    the reason of an exclusion is not blank
--   companies_excluded_date      the date of an exclusion is YYYY-MM-DD (see below for an impossible date)
--   companies_excluded_shape     an excluded record has crawl false and every other field null
--   companies_excluded_limit     at most 200 excluded records (common.EXCLUDED_MAX); a trigger, a count is not a check
-- tests/test_migrations_companies.py fails when the gap reasons, the ats names, the 200, the 2 or the loaded records
-- differ from the code and the file. An impossible date such as 2026-02-30 passes the pattern and is then refused by
-- the cast itself with an error that names no check; the editor's date input cannot send one.
--
-- Who may do what: everybody reads the records that are not excluded (decision D1: the list is public, as the dashboard
-- already shows every company); a signed-in admin reads, adds, changes and deletes every record, excluded ones
-- included; nobody else has any access, so an excluded record is never readable by anyone but the admin. Every
-- other privilege (truncate, references, trigger) is revoked.
--
-- The log: an after-trigger writes one row to `admin_log` for each change an admin makes ('company added',
-- 'company changed', 'company removed', target = the company name, never a reason or any other field). Only a signed-in
-- admin's change is logged: the load below runs before the trigger exists, and the pipeline (service role, step 3.2)
-- and the SQL editor are not the admin page. `admin_log` has no insert for any public role, so the function is
-- security definer, pins its search path and is executable by nobody.

create table public.companies (
  id bigint generated always as identity primary key,
  name text not null,
  domain text,
  segment text,
  crawl boolean not null,
  careers_url text,
  ats text,
  slug text,
  source text,
  status text,
  israel_only boolean,
  verify boolean,
  links jsonb,
  gap jsonb,
  excluded jsonb,
  updated_at timestamptz not null default now(),
  constraint companies_name_min_length check (name = btrim(name) and char_length(name) >= 2),
  constraint companies_domain_required check (excluded is not null or coalesce(btrim(domain), '') <> ''),
  constraint companies_exactly_one_status check (crawl::int + (gap is not null)::int + (excluded is not null)::int = 1),
  constraint companies_gap_reason check (
    case
      when gap is null then true
      when jsonb_typeof(gap) <> 'object' then false
      else coalesce(
        jsonb_typeof(gap -> 'reason') = 'string'
        and gap ->> 'reason' in (
          'crawl_failing', 'bot_protection', 'no_careers_url', 'site_unusable',
          'no_openings', 'needs_reader', 'robots_disallowed', 'no_feed_found'
        )
        and (gap - 'reason' - 'note') = '{}'::jsonb
        and coalesce(jsonb_typeof(gap -> 'note') = 'string', true),
        false)
    end),
  constraint companies_careers_https check (careers_url is null or careers_url = '' or starts_with(careers_url, 'https://')),
  constraint companies_links_https check (
    case
      when links is null then true
      when jsonb_typeof(links) <> 'object' then false
      else (links - 'israel' - 'global') = '{}'::jsonb
        and (links -> 'israel' is null
             or (jsonb_typeof(links -> 'israel') = 'string' and starts_with(links ->> 'israel', 'https://')))
        and (links -> 'global' is null
             or (jsonb_typeof(links -> 'global') = 'string' and starts_with(links ->> 'global', 'https://')))
    end),
  constraint companies_ats_known check (
    ats is null or ats = '' or ats in (
      'greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet', 'workday', 'jazzhr',
      'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'browser'
    )),
  constraint companies_excluded_keys check (
    case
      when excluded is null then true
      when jsonb_typeof(excluded) <> 'object' then false
      else (excluded - 'reason' - 'date_added') = '{}'::jsonb
        and excluded -> 'reason' is not null and excluded -> 'date_added' is not null
    end),
  constraint companies_excluded_reason check (
    excluded is null
    or coalesce(jsonb_typeof(excluded -> 'reason') = 'string' and btrim(excluded ->> 'reason') <> '', false)),
  constraint companies_excluded_date check (
    case
      when excluded is null then true
      when jsonb_typeof(excluded -> 'date_added') is distinct from 'string' then false
      when excluded ->> 'date_added' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then false
      else to_char((excluded ->> 'date_added')::date, 'YYYY-MM-DD') = excluded ->> 'date_added'
    end),
  constraint companies_excluded_shape check (
    excluded is null
    or (not crawl and domain is null and segment is null and careers_url is null and ats is null and slug is null
        and source is null and status is null and israel_only is null and verify is null and links is null
        and gap is null))
);

create unique index companies_name_unique on public.companies (lower(name));
create unique index companies_domain_unique on public.companies (lower(domain));

alter table public.companies enable row level security;
revoke all on public.companies from anon, authenticated, public;
grant select on public.companies to anon, authenticated;
grant insert, update, delete on public.companies to authenticated;

create policy "anyone reads the companies that are not excluded" on public.companies
  for select to anon, authenticated using (excluded is null);
create policy "an admin reads every company" on public.companies
  for select to authenticated using (public.is_admin());
create policy "an admin adds companies" on public.companies
  for insert to authenticated with check (public.is_admin());
create policy "an admin changes companies" on public.companies
  for update to authenticated using (public.is_admin()) with check (public.is_admin());
create policy "an admin deletes companies" on public.companies
  for delete to authenticated using (public.is_admin());

-- The records of data/companies.json at this commit, in the order of the file, as one jsonb array (a key a record does
-- not have loads as null). Generated from the file, never typed: json.dumps(record, ensure_ascii=False) per line with
-- the keys in common.RECORD_KEYS order. The tag $companies$ and the characters `--` and `/*` never occur in the file.
-- It runs before the two triggers below exist, so the load writes no `admin_log` row.
insert into public.companies (
  name, domain, segment, crawl, careers_url, ats, slug, source, status, israel_only, verify, links, gap
)
select name, domain, segment, crawl, careers_url, ats, slug, source, status, israel_only, verify, links, gap
from jsonb_to_recordset($companies$
[
{"name": "Adobe", "domain": "adobe.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "adobe/wd5/external_experienced", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://careers.adobe.com/"}},
{"name": "AI21 Labs", "domain": "ai21.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "ai21/E6.001", "source": "override", "status": "ok"},
{"name": "Aidoc", "domain": "aidoc.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "aidocmedical", "source": "override", "status": "ok"},
{"name": "AIR", "domain": "air.one", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.airev.aero/careers"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Akamai", "domain": "akamai.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"israel": "https://jobs.akamai.com/en/sites/CX_1/jobs?location=Israel", "global": "https://jobs.akamai.com/"}, "gap": {"reason": "no_careers_url", "note": "Blocks plain requests (403); the Workday address guessed answers 500"}},
{"name": "Aleph Farms", "domain": "aleph-farms.com", "segment": "deeptech", "crawl": false, "careers_url": "https://aleph-farms.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://aleph-farms.com/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Align Technology", "domain": "aligntech.com", "segment": "multinational", "crawl": false, "careers_url": "https://jobs.aligntech.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://jobs.aligntech.com/he", "global": "https://jobs.aligntech.com/careers"}, "gap": {"reason": "needs_reader", "note": "Pinpoint board with a jobs.rss feed, no adapter for one company"}},
{"name": "Allot", "domain": "allot.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "allot/C4.009", "source": "override", "status": "ok"},
{"name": "Amazon", "domain": "amazon.com", "crawl": true, "careers_url": "", "ats": "amazon", "slug": "ISR", "source": "override", "status": "ok", "israel_only": true},
{"name": "AMD", "domain": "amd.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.amd.com/en/corporate/careers.html", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://careers.amd.com/careers-home/jobs"}, "gap": {"reason": "needs_reader", "note": "iCIMS board, no adapter yet"}},
{"name": "Amdocs", "domain": "amdocs.com", "crawl": true, "careers_url": "https://jobs.amdocs.com/careers?location=Israel", "ats": "browser", "slug": "https://jobs.amdocs.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "American Express", "domain": "americanexpress.com", "crawl": true, "careers_url": "", "ats": "oraclehcm", "slug": "egug.fa.us2.oraclecloud.com/CX_1", "source": "override", "status": "ok", "israel_only": true},
{"name": "Aniview", "domain": "aniview.com", "segment": "biz_software", "crawl": false, "careers_url": "https://aniview.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://aniview.com/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "AnyClip", "domain": "anyclip.com", "segment": "biz_software", "crawl": false, "careers_url": "https://anyclip.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://anyclip.com/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Apester", "domain": "apester.com", "crawl": true, "careers_url": "", "ats": "comeet_page", "slug": "https://apester.com/careers/", "source": "override", "status": "ok", "israel_only": true},
{"name": "Apiiro", "domain": "apiiro.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "apiiro", "source": "override", "status": "ok"},
{"name": "Apple", "domain": "apple.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.apple.com/careers/us/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://jobs.apple.com/en-il/search?location=israel-ISR", "global": "https://www.apple.com/careers/"}, "gap": {"reason": "needs_reader", "note": "A script app; its search API redirects"}},
{"name": "Applied Materials", "domain": "appliedmaterials.com", "crawl": true, "careers_url": "https://careers.appliedmaterials.com/careers?location=Israel", "ats": "browser", "slug": "https://careers.appliedmaterials.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Applitools", "domain": "applitools.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.applitools.com/company/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://applitools.com/company/careers/"}, "gap": {"reason": "no_openings", "note": "No open roles right now"}},
{"name": "Appnext", "domain": "appnext.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "appnext/42.003", "source": "override", "status": "ok"},
{"name": "AppsFlyer", "domain": "appsflyer.com", "crawl": true, "careers_url": "https://careers.appsflyer.com/", "ats": "greenhouse", "slug": "appsflyer", "source": "guess", "status": "ok"},
{"name": "Aqua Security", "domain": "aquasec.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "aquasec/91.001", "source": "override", "status": "ok"},
{"name": "Arbe Robotics", "domain": "arberobotics.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "arbe/C6.001", "source": "override", "status": "ok"},
{"name": "Armis", "domain": "armis.com", "crawl": true, "careers_url": "", "ats": "smartrecruiters", "slug": "armis", "source": "guess", "status": "ok"},
{"name": "Atera", "domain": "atera.com", "crawl": true, "careers_url": "", "ats": "smartrecruiters", "slug": "atera", "source": "override", "status": "ok"},
{"name": "Augury", "domain": "augury.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "augury", "source": "override", "status": "ok"},
{"name": "Autobrains", "domain": "autobrains.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "autobrains/57.004", "source": "override", "status": "ok"},
{"name": "Autodesk", "domain": "autodesk.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "autodesk/wd1/Ext", "source": "override", "status": "ok", "israel_only": true},
{"name": "Autofleet", "domain": "autofleet.io", "crawl": true, "careers_url": "", "ats": "workable", "slug": "autofleet", "source": "override", "status": "ok"},
{"name": "Axonius", "domain": "axonius.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "axonius", "source": "guess", "status": "ok"},
{"name": "Bigabid", "domain": "bigabid.com", "crawl": true, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.bigabid.com/careers/"}},
{"name": "BigID", "domain": "bigid.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "bigid", "source": "override", "status": "ok"},
{"name": "BioCatch", "domain": "biocatch.com", "crawl": true, "careers_url": "", "ats": "lever", "slug": "biocatch", "source": "override", "status": "ok"},
{"name": "Bizzabo", "domain": "bizzabo.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "bizzabo/A5.000", "source": "override", "status": "ok"},
{"name": "Bluevine", "domain": "bluevine.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "bluevineisrael", "source": "override", "status": "ok"},
{"name": "BlueWhite", "domain": "bluewhite.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "bluewhite/D5.00D", "source": "override", "status": "ok"},
{"name": "Booking.com", "domain": "booking.com", "segment": "multinational", "crawl": false, "careers_url": "https://careers.booking.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://careers.booking.com/locations/tel-aviv/", "global": "https://careers.booking.com/"}, "gap": {"reason": "bot_protection", "note": "The iCIMS board answers 403; the careers site has no feed"}},
{"name": "Brainsway", "domain": "brainsway.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"israel": "https://brainsway.co.il/come-work-with-us/", "global": "https://www.brainsway.com/"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Bringg", "domain": "bringg.com", "crawl": true, "careers_url": "https://www.bringg.com/careers", "ats": "greenhouse", "slug": "bringg", "source": "page", "status": "ok"},
{"name": "Broadcom", "domain": "broadcom.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "broadcom/wd1/External_Career", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://broadcom.wd1.myworkdayjobs.com/External_Career", "global": "https://www.broadcom.com/company/careers"}},
{"name": "Cadence", "domain": "cadence.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "cadence/wd1/External_Careers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://cadence.wd1.myworkdayjobs.com/en-US/External_Careers", "global": "https://www.cadence.com/en_US/home/company/life-at-cadence/careers.html"}},
{"name": "Camtek", "domain": "camtek.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.camtek.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.camtek.com/careers/open-positions/"}, "gap": {"reason": "site_unusable", "note": "The page is empty"}},
{"name": "Capitolis", "domain": "capitolis.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "capitolis", "source": "override", "status": "ok"},
{"name": "Cato Networks", "domain": "catonetworks.com", "crawl": true, "careers_url": "https://www.catonetworks.com/careers", "ats": "greenhouse", "slug": "catonetworks", "source": "guess", "status": "ok"},
{"name": "Ceragon", "domain": "ceragon.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "ceragon/D3.003", "source": "override", "status": "ok"},
{"name": "Ceva", "domain": "ceva-ip.com", "crawl": true, "careers_url": "https://www.ceva-ip.com/career/", "ats": "comeet", "slug": "ceva/76.005", "source": "override", "status": "ok", "israel_only": true},
{"name": "Check Point", "domain": "checkpoint.com", "crawl": true, "careers_url": "https://careers.checkpoint.com/jobs", "ats": "browser", "slug": "https://careers.checkpoint.com/jobs", "source": "override", "status": "ok", "links": {"global": "https://careers.checkpoint.com/jobs"}},
{"name": "Checkmarx", "domain": "checkmarx.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "checkmarx/C0.008", "source": "override", "status": "ok"},
{"name": "CHEQ", "domain": "cheq.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cheq/65.005", "source": "override", "status": "ok"},
{"name": "Cisco", "domain": "cisco.com", "crawl": true, "careers_url": "", "ats": "phenom", "slug": "careers.cisco.com/global/en", "source": "override", "status": "ok", "israel_only": true},
{"name": "Citi", "domain": "citi.com", "crawl": true, "careers_url": "https://citi.eightfold.ai/careers?location=Israel", "ats": "browser", "slug": "https://citi.eightfold.ai/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Claroty", "domain": "claroty.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "Claroty/F2.004", "source": "override", "status": "ok"},
{"name": "Classiq", "domain": "classiq.io", "crawl": true, "careers_url": "https://www.classiq.io/careers-page", "ats": "browser", "slug": "https://www.classiq.io/careers-page", "source": "override", "status": "ok", "israel_only": true},
{"name": "Cloudflare", "domain": "cloudflare.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.cloudflare.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.cloudflare.com/careers/jobs/"}, "gap": {"reason": "needs_reader", "note": "Greenhouse locations are Hybrid or Distributed, never a city"}},
{"name": "Cloudinary", "domain": "cloudinary.com", "crawl": true, "careers_url": "https://cloudinary.com/careers", "ats": "lever", "slug": "cloudinary", "source": "guess", "status": "ok"},
{"name": "Cognata", "domain": "cognata.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.cognata.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.cognata.com/hiring/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Cognyte", "domain": "cognyte.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cognyte/F2.009", "source": "override", "status": "ok"},
{"name": "Converge Bio", "domain": "converge-bio.com", "crawl": true, "careers_url": "https://converge-bio.com/careers", "ats": "comeet", "slug": "convergebio/CA.00D", "source": "override", "status": "ok", "israel_only": true},
{"name": "Coralogix", "domain": "coralogix.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "coralogix/06.004", "source": "override", "status": "ok"},
{"name": "Criteo", "domain": "criteo.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "criteo/wd3/Criteo_Career_Site", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://careers.criteo.com/en/"}},
{"name": "CropX", "domain": "cropx.com", "crawl": true, "careers_url": "", "ats": "workable", "slug": "cropx", "source": "override", "status": "ok", "verify": false},
{"name": "CrowdStrike", "domain": "crowdstrike.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "crowdstrike/wd5/crowdstrikecareers", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://www.crowdstrike.com/en-us/careers/"}},
{"name": "Cycode", "domain": "cycode.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cycode/A8.00D", "source": "override", "status": "ok"},
{"name": "Cyera", "domain": "cyera.io", "crawl": true, "careers_url": "https://www.cyera.io/careers", "ats": "comeet", "slug": "cyera/17.008", "source": "page", "status": "ok"},
{"name": "Cymulate", "domain": "cymulate.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "cymulate", "source": "override", "status": "ok"},
{"name": "Cynet", "domain": "cynet.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cynet/33.00D", "source": "override", "status": "ok"},
{"name": "Cyolo", "domain": "cyolo.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cyolo/F5.005", "source": "override", "status": "ok"},
{"name": "CytoReason", "domain": "cytoreason.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "cytoreason/16.002", "source": "override", "status": "ok", "israel_only": true},
{"name": "D-ID", "domain": "d-id.com", "crawl": true, "careers_url": "", "ats": "workable", "slug": "d-id", "source": "override", "status": "ok"},
{"name": "DarioHealth", "domain": "dariohealth.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "Dario.Health/34.006", "source": "override", "status": "ok"},
{"name": "Datadog", "domain": "datadoghq.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "datadog", "source": "override", "status": "ok", "israel_only": true},
{"name": "Dataloop", "domain": "dataloop.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "dataloop/B5.00A", "source": "override", "status": "ok", "israel_only": true},
{"name": "Datarails", "domain": "datarails.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "datarails", "source": "override", "status": "ok"},
{"name": "DealHub", "domain": "dealhub.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "dealhub/86.005", "source": "override", "status": "ok"},
{"name": "Deel", "domain": "deel.com", "crawl": true, "careers_url": "https://www.deel.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://jobs.deel.com/deel/", "global": "https://www.deel.com/careers/"}},
{"name": "Deep Instinct", "domain": "deepinstinct.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.deepinstinct.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.deepinstinct.com/careers"}, "gap": {"reason": "needs_reader", "note": "Job descriptions are PDF files"}},
{"name": "Dell Technologies", "domain": "dell.com", "crawl": true, "careers_url": "", "ats": "oraclehcm", "slug": "enterpriseplatform.dell.com/careers", "source": "override", "status": "ok", "israel_only": true},
{"name": "Diagnostic Robotics", "domain": "diagnosticrobotics.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.diagnosticrobotics.com/"}, "gap": {"reason": "no_careers_url", "note": "The career page is a script list that shows no items"}},
{"name": "Digital Turbine", "domain": "digitalturbine.com", "crawl": true, "careers_url": "https://digitalturbine.wd501.myworkdayjobs.com/Digital_Turbine_External_Careers", "ats": "browser", "slug": "https://digitalturbine.wd501.myworkdayjobs.com/Digital_Turbine_External_Careers", "source": "override", "status": "ok"},
{"name": "DoubleVerify", "domain": "doubleverify.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "doubleverify/wd5/DV_Careers", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://doubleverify.com/en/company/careers"}},
{"name": "DriveNets", "domain": "drivenets.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "drivenets/72.006", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://www.comeet.com/jobs/drivenets/72.006"}},
{"name": "Duda", "domain": "duda.co", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "duda", "source": "override", "status": "ok"},
{"name": "Dynamic Yield", "domain": "dynamicyield.com", "crawl": true, "careers_url": "https://careers.mastercard.com/us/en/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://careers.mastercard.com/us/en/tel-aviv-israel", "global": "https://careers.mastercard.com/us/en/tech-jobs-dynamic-yield"}},
{"name": "EarlySign", "domain": "earlysign.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": "earlysign.com does not answer; one source says it ceased operations in October 2025, not confirmed"}},
{"name": "Earnix", "domain": "earnix.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "earnix/93.00B", "source": "override", "status": "ok"},
{"name": "eBay", "domain": "ebay.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://careers.ebayinc.com/"}, "gap": {"reason": "no_careers_url", "note": "No feed found in the career page HTML"}},
{"name": "Edwards Lifesciences", "domain": "edwards.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "edwards/wd5/edwardscareers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.edwards.com/careers/locations/israel", "global": "https://www.edwards.com/careers/jobsearch"}},
{"name": "Elbit Systems", "domain": "elbitsystems.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.elbitsystems.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://elbitsystems.com/careers/"}, "gap": {"reason": "site_unusable", "note": "Timeout; its careers site is a Hebrew custom site with no feed seen"}},
{"name": "Electreon", "domain": "electreon.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "electreon", "source": "override", "status": "ok"},
{"name": "Elementor", "domain": "elementor.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "elementor/A3.00F", "source": "override", "status": "ok"},
{"name": "Enlight Renewable Energy", "domain": "enlightenergy.co.il", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "enlightenergy/99.006", "source": "override", "status": "ok"},
{"name": "env0", "domain": "env0.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.envzero.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.envzero.com/careers"}, "gap": {"reason": "needs_reader", "note": "Jobs on its own site, no ATS behind them"}},
{"name": "Ericsson", "domain": "ericsson.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://jobs.ericsson.com/careers"}, "gap": {"reason": "no_careers_url", "note": "Eightfold board shows 0 jobs in Israel today"}},
{"name": "eToro", "domain": "etoro.com", "crawl": true, "careers_url": "https://www.etoro.com/about/careers/", "ats": "browser", "slug": "https://www.etoro.com/about/careers/", "source": "override", "status": "ok"},
{"name": "Evogene", "domain": "evogene.com", "segment": "deeptech", "crawl": false, "careers_url": "https://evogene.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://evogene.com/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Ex Libris", "domain": "exlibrisgroup.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "clarivate/wd3/Clarivate_Careers", "source": "override", "status": "ok", "israel_only": true},
{"name": "Explorium", "domain": "explorium.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "explorium/B4.00E", "source": "override", "status": "ok"},
{"name": "Fireblocks", "domain": "fireblocks.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "fireblocks", "source": "override", "status": "ok"},
{"name": "Firebolt", "domain": "firebolt.io", "crawl": true, "careers_url": "https://www.firebolt.io/careers", "ats": "comeet", "slug": "firebolt/26.004", "source": "page", "status": "ok"},
{"name": "Firefly", "domain": "firefly.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "firefly/39.000", "source": "override", "status": "ok", "israel_only": true},
{"name": "Fiverr", "domain": "fiverr.com", "crawl": true, "careers_url": "https://www.fiverr.com/jobs", "ats": "comeet", "slug": "fiverr/60.002", "source": "page", "status": "ok"},
{"name": "Ford", "domain": "ford.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.careers.ford.com/en"}, "gap": {"reason": "no_careers_url", "note": "Blocks plain requests (403)"}},
{"name": "Foretellix", "domain": "foretellix.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "Foretellix/84.007", "source": "override", "status": "ok", "israel_only": true},
{"name": "ForSight Robotics", "domain": "forsightrobotics.com", "crawl": true, "careers_url": "https://forsightrobotics.com/careers", "ats": "browser", "slug": "https://forsightrobotics.com/careers", "source": "override", "status": "ok"},
{"name": "Forter", "domain": "forter.com", "crawl": true, "careers_url": "https://www.forter.com/careers/", "ats": "greenhouse", "slug": "forter", "source": "guess", "status": "ok"},
{"name": "Freightos", "domain": "freightos.com", "segment": "biz_software", "crawl": false, "careers_url": "https://www.freightos.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.freightos.com/careers/"}, "gap": {"reason": "bot_protection", "note": "A challenge page with no links"}},
{"name": "Fundbox", "domain": "fundbox.com", "crawl": true, "careers_url": "https://fundbox.careers.hibob.com/jobs", "ats": "browser", "slug": "https://fundbox.careers.hibob.com/jobs", "source": "override", "status": "ok"},
{"name": "Gauzy", "domain": "gauzy.com", "crawl": true, "careers_url": "", "ats": "comeet_page", "slug": "https://www.gauzy.com/careers/", "source": "override", "status": "ok", "israel_only": true},
{"name": "GE HealthCare", "domain": "gehealthcare.com", "crawl": true, "careers_url": "", "ats": "phenom", "slug": "careers.gehealthcare.com/global/en", "source": "override", "status": "ok", "israel_only": true},
{"name": "General Motors", "domain": "gm.com", "segment": "multinational", "crawl": false, "careers_url": "https://search-careers.gm.com/en/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://search-careers.gm.com/en/locations/israel/", "global": "https://search-careers.gm.com/en/"}, "gap": {"reason": "bot_protection", "note": "Blocks plain requests (403)"}},
{"name": "Gett", "domain": "gett.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "gett/A0.002", "source": "override", "status": "ok"},
{"name": "Gilat", "domain": "gilat.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.gilat.com/career/"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Glassbox", "domain": "glassbox.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "glassboxltd", "source": "override", "status": "ok"},
{"name": "Gloat", "domain": "gloat.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "gloat/E5.000", "source": "override", "status": "ok"},
{"name": "Global-e", "domain": "global-e.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "global-e/62.002", "source": "override", "status": "ok"},
{"name": "Gong", "domain": "gong.io", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "gongio", "source": "override", "status": "ok"},
{"name": "Google", "domain": "google.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.google.com/about/careers/applications/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://www.google.com/about/careers/applications/locations/tel-aviv-haifa/", "global": "https://www.google.com/about/careers/applications/"}, "gap": {"reason": "needs_reader", "note": "The results page embeds its data; a worldwide search"}},
{"name": "Guardz", "domain": "guardz.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "guardz", "source": "override", "status": "ok"},
{"name": "Guesty", "domain": "guesty.com", "crawl": true, "careers_url": "", "ats": "comeet_page", "slug": "https://www.guesty.com/careers/", "source": "override", "status": "ok", "israel_only": true},
{"name": "H2Pro", "domain": "h2pro.co", "segment": "deeptech", "crawl": false, "careers_url": "https://www.h2pro.co/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.h2pro.co/careers"}, "gap": {"reason": "no_openings", "note": "No open roles"}},
{"name": "Hailo", "domain": "hailo.ai", "crawl": true, "careers_url": "https://hailo.ai/company-overview/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://hailo.ai/company-overview/careers/"}},
{"name": "Hibob", "domain": "hibob.com", "crawl": true, "careers_url": "https://hibob-fa0ad69d0cb34a.careers.hibob.com/jobs", "ats": "browser", "slug": "https://hibob-fa0ad69d0cb34a.careers.hibob.com/jobs", "source": "override", "status": "ok"},
{"name": "Hippo", "domain": "hippo.com", "crawl": true, "careers_url": "https://www.hippo.com/careers", "ats": "greenhouse", "slug": "hippo70", "source": "page", "status": "ok"},
{"name": "Honeybook", "domain": "honeybook.com", "crawl": true, "careers_url": "https://www.honeybook.com/company/careers", "ats": "ashby", "slug": "honeybook", "source": "guess", "status": "ok"},
{"name": "Houzz", "domain": "houzz.com", "crawl": true, "careers_url": "", "ats": "lever", "slug": "houzz", "source": "override", "status": "ok"},
{"name": "HP", "domain": "hp.com", "crawl": true, "careers_url": "https://apply.hp.com/careers?location=Israel", "ats": "browser", "slug": "https://apply.hp.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Hunters", "domain": "hunters.security", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "hunters/67.007", "source": "override", "status": "ok"},
{"name": "IBM", "domain": "ibm.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.ibm.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.ibm.com/careers"}, "gap": {"reason": "no_feed_found", "note": "No feed found"}},
{"name": "Imagry", "domain": "imagry.co", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "imagry/B7.00F", "source": "override", "status": "ok"},
{"name": "Immunai", "domain": "immunai.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "immunai/37.009", "source": "override", "status": "ok"},
{"name": "Imperva", "domain": "imperva.com", "crawl": true, "careers_url": "https://www.imperva.com/company/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://careers.thalesgroup.com/global/en", "global": "https://www.imperva.com/company/careers/"}},
{"name": "Incredibuild", "domain": "incredibuild.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "incredibuild/66.00F", "source": "override", "status": "ok"},
{"name": "Infinidat", "domain": "infinidat.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "infinidat/D6.003", "source": "override", "status": "ok"},
{"name": "Innoviz", "domain": "innoviz.tech", "crawl": true, "careers_url": "https://www.innoviz.tech/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://innoviz.tech/join-us", "global": "https://innoviz.tech/careers"}},
{"name": "InSightec", "domain": "insightec.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "insightec/4A.004", "source": "override", "status": "ok"},
{"name": "Integral Ad Science", "domain": "integralads.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "integralads/wd1/IAScareers", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://integralads.com/about-ias/careers/"}},
{"name": "Intel", "domain": "intel.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "intel/wd1/External", "source": "override", "status": "ok", "israel_only": true},
{"name": "Intezer", "domain": "intezer.com", "crawl": true, "careers_url": "https://intezer.com/careers", "ats": "browser", "slug": "https://intezer.com/careers", "source": "override", "status": "ok"},
{"name": "Intuit", "domain": "intuit.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.intuit.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://www.intuit.com/careers/locations/israel/", "global": "https://www.intuit.com/careers/"}, "gap": {"reason": "needs_reader", "note": "Radancy search; many jobs show Multiple Locations"}},
{"name": "Island", "domain": "island.io", "crawl": true, "careers_url": "https://www.island.io/careers", "ats": "greenhouse", "slug": "island", "source": "guess", "status": "ok"},
{"name": "Jeeves", "domain": "tryjeeves.com", "crawl": true, "careers_url": "https://www.tryjeeves.com/careers", "ats": "lever", "slug": "tryjeeves", "source": "page", "status": "ok"},
{"name": "Jelly Button Games", "domain": "jellybuttongames.com", "segment": "biz_software", "crawl": false, "careers_url": "https://www.jellybuttongames.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.playtika.com/careers/"}, "gap": {"reason": "site_unusable", "note": "Domain parked at GoDaddy"}},
{"name": "JFrog", "domain": "jfrog.com", "crawl": true, "careers_url": "https://join.jfrog.com/", "ats": "greenhouse", "slug": "jfrog", "source": "guess", "status": "ok"},
{"name": "JPMorgan Chase", "domain": "jpmorganchase.com", "crawl": true, "careers_url": "", "ats": "oraclehcm", "slug": "jpmc.fa.oraclecloud.com/CX_1001", "source": "override", "status": "ok", "israel_only": true},
{"name": "Justt", "domain": "justt.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "justt/36.001", "source": "override", "status": "ok"},
{"name": "Kahun", "domain": "kahun.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.kahun.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "gap": {"reason": "site_unusable", "note": "Domain parked at GoDaddy"}},
{"name": "Kaltura", "domain": "kaltura.com", "crawl": true, "careers_url": "https://corp.kaltura.com", "ats": "smartrecruiters", "slug": "kalturainc", "source": "guess", "status": "ok"},
{"name": "KELA", "domain": "kelacyber.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "kela/2A.004", "source": "override", "status": "ok"},
{"name": "Keysight", "domain": "keysight.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"israel": "https://jobs.keysight.com/location-israel", "global": "https://careers.keysight.com/"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "KLA", "domain": "kla.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "kla/wd1/Search", "source": "override", "status": "ok", "israel_only": true},
{"name": "Komodor", "domain": "komodor.com", "crawl": true, "careers_url": "https://komodor.com/careers/", "ats": "browser", "slug": "https://komodor.com/careers/", "source": "override", "status": "ok"},
{"name": "Kornit Digital", "domain": "kornit.com", "segment": "deeptech", "crawl": false, "careers_url": "https://careers.kornit.com/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://careers.kornit.com/all-positions/"}, "gap": {"reason": "needs_reader", "note": "Own theme on /all-positions/ with /cmcareer/ links, not the plugin's"}},
{"name": "Kyndryl", "domain": "kyndryl.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "kyndryl/wd5/KyndrylProfessionalCareers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.kyndryl.com/il/en/careers", "global": "https://www.kyndryl.com/us/en/careers"}},
{"name": "lakeFS", "domain": "lakefs.io", "segment": "cyber_infra", "crawl": false, "careers_url": "https://lakefs.io/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://lakefs.io/careers/"}, "gap": {"reason": "bot_protection", "note": "Access denied (403)"}},
{"name": "Landa", "domain": "landanano.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.landa-corporation.com/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Lasso Security", "domain": "lasso.security", "crawl": true, "careers_url": "https://www.lasso.security/careers", "ats": "browser", "slug": "https://www.lasso.security/careers", "source": "override", "status": "ok"},
{"name": "Legit Security", "domain": "legitsecurity.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "legitsecurity.com/37.004", "source": "override", "status": "ok"},
{"name": "Lemonade", "domain": "lemonade.com", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "Lemonade", "source": "override", "status": "ok"},
{"name": "Lightbits Labs", "domain": "lightbitslabs.com", "crawl": true, "careers_url": "https://www.lightbitslabs.com/careers/", "ats": "browser", "slug": "https://www.lightbitslabs.com/careers/", "source": "override", "status": "ok"},
{"name": "Lightricks", "domain": "lightricks.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "lightricks", "source": "override", "status": "ok"},
{"name": "Lightrun", "domain": "lightrun.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "lightrun", "source": "override", "status": "ok"},
{"name": "Line 5", "domain": "line5.ai", "segment": "deeptech", "crawl": false, "careers_url": "https://line5.ai/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://line5.ai/Hiring"}, "gap": {"reason": "site_unusable", "note": "Needs a login"}},
{"name": "LinearB", "domain": "linearb.io", "crawl": true, "careers_url": "", "ats": "jazzhr", "slug": "linearb", "source": "override", "status": "ok"},
{"name": "Lumigo", "domain": "lumigo.io", "segment": "cyber_infra", "crawl": false, "careers_url": "https://lumigo.io/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://lumigo.io/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Lumus", "domain": "lumusvision.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "lumus/62.00F", "source": "override", "status": "ok"},
{"name": "Lusha", "domain": "lusha.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "lusha/73.00B", "source": "override", "status": "ok", "israel_only": true},
{"name": "Madgicx", "domain": "madgicx.com", "segment": "biz_software", "crawl": false, "careers_url": "https://madgicx.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://madgicx.com/careers"}, "gap": {"reason": "no_openings", "note": "No open roles; CVs go through a form"}},
{"name": "Magic Software", "domain": "magicsoftware.com", "segment": "biz_software", "crawl": false, "careers_url": "https://www.magicsoftware.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://www.magicsoftware.com/about-us/careers/job-openings-in-israel/", "global": "https://www.magicsoftware.com/careers/"}, "gap": {"reason": "no_feed_found", "note": "Its Comeet account is Matrix IFS's, not its own"}},
{"name": "Marvell", "domain": "marvell.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "marvell/wd1/MarvellCareers", "source": "override", "status": "ok", "israel_only": true},
{"name": "Mastercard", "domain": "mastercard.com", "crawl": true, "careers_url": "", "ats": "phenom", "slug": "careers.mastercard.com/us/en", "source": "override", "status": "ok", "israel_only": true},
{"name": "Medtronic", "domain": "medtronic.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "medtronic/wd1/MedtronicCareers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.medtronic.com/il-he/our-company/careers.html", "global": "https://www.medtronic.com/en-us/our-company/careers.html"}},
{"name": "Melio", "domain": "melio.com", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "melio-payments", "source": "override", "status": "ok"},
{"name": "Mend", "domain": "mend.io", "crawl": true, "careers_url": "https://www.mend.io/careers", "ats": "ashby", "slug": "mend-io", "source": "page", "status": "ok"},
{"name": "Mesh Payments", "domain": "meshpayments.com", "crawl": true, "careers_url": "https://www.meshpayments.com/careers", "ats": "browser", "slug": "https://www.meshpayments.com/careers", "source": "override", "status": "ok"},
{"name": "Meta", "domain": "meta.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.metacareers.com/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://www.metacareers.com/tel-avivjobsearch/", "global": "https://www.metacareers.com/"}, "gap": {"reason": "no_feed_found", "note": "No feed found"}},
{"name": "Microsoft", "domain": "microsoft.com", "crawl": true, "careers_url": "https://apply.careers.microsoft.com/careers?location=Israel", "ats": "browser", "slug": "https://apply.careers.microsoft.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Mitiga", "domain": "mitiga.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "mitiga/26.00B", "source": "override", "status": "ok", "israel_only": true},
{"name": "Mobileye", "domain": "mobileye.com", "crawl": true, "careers_url": "", "ats": "lever", "slug": "eu:mobileye", "source": "override", "status": "ok"},
{"name": "monday.com", "domain": "monday.com", "crawl": true, "careers_url": "https://monday.com/careers", "ats": "browser", "slug": "https://monday.com/careers", "source": "override", "status": "ok"},
{"name": "MongoDB", "domain": "mongodb.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "mongodb", "source": "override", "status": "ok", "israel_only": true},
{"name": "Moon Active", "domain": "moonactive.com", "crawl": true, "careers_url": "https://www.moonactive.com/careers/", "ats": "ashby", "slug": "moonactive", "source": "guess", "status": "ok"},
{"name": "Motorola Solutions", "domain": "motorolasolutions.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "motorolasolutions/wd5/Careers", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://motorolasolutions.wd5.myworkdayjobs.com/Careers"}},
{"name": "Nano Dimension", "domain": "nano-di.com", "crawl": true, "careers_url": "https://www.nano-di.com/careers", "ats": "comeet", "slug": "nanodimension/62.003", "source": "override", "status": "ok", "israel_only": true},
{"name": "Nanox", "domain": "nanox.vision", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "nanox/43.00F", "source": "override", "status": "ok"},
{"name": "Navan", "domain": "navan.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "tripactions", "source": "override", "status": "ok"},
{"name": "Nayax", "domain": "nayax.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "nayax/13.009", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.comeet.com/jobs/nayax/13.009", "global": "https://www.nayax.com/careers/"}},
{"name": "NeuReality", "domain": "neureality.ai", "crawl": true, "careers_url": "", "ats": "comeet_page", "slug": "https://www.neureality.ai/careers/", "source": "override", "status": "ok", "israel_only": true},
{"name": "Nexar", "domain": "nexar.com", "crawl": true, "careers_url": "", "ats": "smartrecruiters", "slug": "nexarinc", "source": "override", "status": "ok"},
{"name": "Next Insurance", "domain": "nextinsurance.com", "crawl": true, "careers_url": "", "ats": "greenhouse_url", "slug": "https://www.nextinsurance.com/nextjs-api/careers/", "source": "override", "status": "ok"},
{"name": "Nexxen", "domain": "nexxen.com", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "nexxen", "source": "override", "status": "ok"},
{"name": "NICE", "domain": "nice.com", "crawl": true, "careers_url": "https://www.nice.com/careers", "ats": "greenhouse", "slug": "nice", "source": "guess", "status": "ok"},
{"name": "Nokia", "domain": "nokia.com", "crawl": true, "careers_url": "", "ats": "oraclehcm", "slug": "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com/CX_1", "source": "override", "status": "ok", "israel_only": true},
{"name": "Nova", "domain": "novami.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "nova/A5.007", "source": "override", "status": "ok"},
{"name": "Nucleai", "domain": "nucleai.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.nucleai.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://nucleai.ai/careers/"}, "gap": {"reason": "site_unusable", "note": "Domain parked at GoDaddy"}},
{"name": "Nuvei", "domain": "nuvei.com", "crawl": true, "careers_url": "", "ats": "workable", "slug": "nuvei", "source": "override", "status": "ok", "verify": false},
{"name": "Nvidia", "domain": "nvidia.com", "crawl": true, "careers_url": "https://jobs.nvidia.com/careers?location=Israel", "ats": "browser", "slug": "https://jobs.nvidia.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Okta", "domain": "okta.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "okta", "source": "override", "status": "ok", "israel_only": true},
{"name": "Oligo Security", "domain": "oligo.security", "crawl": true, "careers_url": "https://www.oligo.security/company/careers", "ats": "browser", "slug": "https://www.oligo.security/company/careers", "source": "override", "status": "ok"},
{"name": "Optibus", "domain": "optibus.com", "crawl": true, "careers_url": "https://optibus.com/company/careers/", "ats": "comeet_page", "slug": "https://optibus.com/company/careers/jobs/", "source": "override", "status": "ok", "israel_only": true},
{"name": "Optimove", "domain": "optimove.com", "crawl": true, "careers_url": "https://www.optimove.com/careers", "ats": "greenhouse", "slug": "optimove", "source": "guess", "status": "ok"},
{"name": "Oracle", "domain": "oracle.com", "crawl": true, "careers_url": "", "ats": "oraclehcm", "slug": "eeho.fa.us2.oraclecloud.com/jobsearch", "source": "override", "status": "ok", "israel_only": true},
{"name": "Orca AI", "domain": "orca-ai.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "Orca-AI/86.003", "source": "override", "status": "ok"},
{"name": "Orca Security", "domain": "orca.security", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "orcasecurity", "source": "guess", "status": "ok"},
{"name": "Ormat", "domain": "ormat.com", "crawl": true, "careers_url": "https://careers.ormat.com/search/?q=&locationsearch=Israel", "ats": "browser", "slug": "https://careers.ormat.com/search/?q=&locationsearch=Israel", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://careers.ormat.com/OrmatGlobal"}},
{"name": "Outbrain", "domain": "outbrain.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "teads1", "source": "override", "status": "ok"},
{"name": "Overwolf", "domain": "overwolf.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "overwolf/B1.001", "source": "override", "status": "ok"},
{"name": "OX Security", "domain": "ox.security", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "ox_security/F8.006", "source": "override", "status": "ok"},
{"name": "Pagaya", "domain": "pagaya.com", "crawl": true, "careers_url": "https://www.pagaya.com/careers", "ats": "greenhouse", "slug": "pagayais", "source": "page", "status": "ok"},
{"name": "Palo Alto Networks", "domain": "paloaltonetworks.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "paloaltonetworks/wd5/panwexternalcareers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://jobs.paloaltonetworks.com/en/israel", "global": "https://jobs.paloaltonetworks.com/"}},
{"name": "Panaya", "domain": "panaya.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "panaya/61.00C", "source": "override", "status": "ok"},
{"name": "Pango", "domain": "pango.co.il", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "pango/59.002", "source": "override", "status": "ok"},
{"name": "Panorays", "domain": "panorays.com", "crawl": true, "careers_url": "https://panorays.com/careers/", "ats": "browser", "slug": "https://panorays.com/careers/", "source": "override", "status": "ok"},
{"name": "Papaya Gaming", "domain": "papayagaming.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "papayagaming/46.00B", "source": "override", "status": "ok", "israel_only": true},
{"name": "Papaya Global", "domain": "papayaglobal.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "papayaglobal/16.005", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.comeet.com/jobs/papayaglobal/16.005", "global": "https://www.papayaglobal.com/careers/"}},
{"name": "Pay-Me", "domain": "paymeservice.com", "segment": "biz_software", "crawl": false, "careers_url": "https://payme.io/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://payme.io/careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Payoneer", "domain": "payoneer.com", "crawl": true, "careers_url": "https://careers.payoneer.com/", "ats": "greenhouse", "slug": "payoneer", "source": "guess", "status": "ok"},
{"name": "PayPal", "domain": "paypal.com", "crawl": true, "careers_url": "https://paypal.eightfold.ai/careers?location=Israel", "ats": "browser", "slug": "https://paypal.eightfold.ai/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Pecan AI", "domain": "pecan.ai", "crawl": true, "careers_url": "https://www.pecan.ai/careers/", "ats": "browser", "slug": "https://www.pecan.ai/careers/", "source": "override", "status": "ok"},
{"name": "Pendo", "domain": "pendo.io", "crawl": true, "careers_url": "https://www.pendo.io/careers", "ats": "greenhouse", "slug": "pendo", "source": "page", "status": "ok"},
{"name": "Pentera", "domain": "pentera.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "pentera/C5.00D", "source": "override", "status": "ok"},
{"name": "Percepto", "domain": "percepto.co", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "percepto/44.000", "source": "override", "status": "ok"},
{"name": "Perion", "domain": "perion.com", "crawl": true, "careers_url": "https://perion.com/careers/", "ats": "browser", "slug": "https://perion.com/careers/", "source": "override", "status": "ok"},
{"name": "Philips", "domain": "philips.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "philips/wd3/jobs-and-careers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.careers.philips.com/il/en/search-results", "global": "https://www.careers.philips.com/global/en"}},
{"name": "Pillar Security", "domain": "pillar.security", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.pillar.security/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.pillar.security/careers"}, "gap": {"reason": "needs_reader", "note": "Jobs on its own site, no ATS behind them"}},
{"name": "Placer.ai", "domain": "placer.ai", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "placerlabs", "source": "override", "status": "ok"},
{"name": "Playstudios", "domain": "playstudios.com", "segment": "biz_software", "crawl": false, "careers_url": "https://www.playstudios.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://www.playstudios.com/tel-aviv/", "global": "https://www.playstudios.com/careers/"}, "gap": {"reason": "needs_reader", "note": "Dayforce portal; its jobs have no location"}},
{"name": "Playtika", "domain": "playtika.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "playtikaltd", "source": "override", "status": "ok"},
{"name": "Pliops", "domain": "pliops.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.pliops.com/careers", "ats": "comeet", "slug": "pliops/05.004", "source": "page", "status": "ats_failed", "links": {"global": "https://pliops.com/careers/"}, "gap": {"reason": "no_openings", "note": "The Comeet board is deactivated; a Workable account lists no job"}},
{"name": "Port", "domain": "getport.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "port/59.004", "source": "override", "status": "ok"},
{"name": "Priority Software", "domain": "priority-software.com", "crawl": true, "careers_url": "https://www.priority-software.com/careers/", "ats": "browser", "slug": "https://www.priority-software.com/careers/", "source": "override", "status": "ok"},
{"name": "Proofpoint", "domain": "proofpoint.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "proofpoint/wd5/proofpointcareers", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://www.proofpoint.com/us/company/careers"}},
{"name": "Q Factor", "domain": "q-factor.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": "Real domain q-factor.com (a one-page site); it has no careers page, only a careers@ email address"}},
{"name": "Qedma", "domain": "qedma.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "qedma/7A.006", "source": "override", "status": "ok"},
{"name": "Qodo", "domain": "qodo.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "qodo/B8.00B", "source": "override", "status": "ok"},
{"name": "Qualcomm", "domain": "qualcomm.com", "crawl": true, "careers_url": "https://careers.qualcomm.com/careers?location=Israel", "ats": "browser", "slug": "https://careers.qualcomm.com/careers?location=Israel", "source": "override", "status": "ok", "israel_only": true},
{"name": "Quantum Art", "domain": "quantum-art.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.quantum-art.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.quantum-art.tech/careers"}, "gap": {"reason": "bot_protection", "note": "Access denied (403); the domain was also reported parked"}},
{"name": "Quantum Machines", "domain": "quantum-machines.co", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "quantummachines/D6.000", "source": "override", "status": "ok", "israel_only": true},
{"name": "Radware", "domain": "radware.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://www.radware.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.radware.com/careers/"}, "gap": {"reason": "site_unusable", "note": "The page is empty"}},
{"name": "Rapid7", "domain": "rapid7.com", "segment": "multinational", "crawl": false, "careers_url": "https://careers.rapid7.com/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://careers.rapid7.com/"}, "gap": {"reason": "needs_reader", "note": "Own jobs platform (/jobs/<slug>-<uuid>); its location filter is ignored, so the page is a worldwide list"}},
{"name": "Rapyd", "domain": "rapyd.com", "crawl": true, "careers_url": "https://www.rapyd.net/company/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.rapyd.net/company/careers/"}},
{"name": "Reco", "domain": "reco.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "reco/3A.00D", "source": "override", "status": "ok"},
{"name": "Redefine Meat", "domain": "redefinemeat.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.redefinemeat.com/company/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.comeet.com/jobs/redefinemeat/28.004"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Redis", "domain": "redis.io", "crawl": true, "careers_url": "https://redis.io/company/careers/", "ats": "ashby", "slug": "redis", "source": "guess", "status": "ok"},
{"name": "REE Automotive", "domain": "ree.auto", "crawl": true, "careers_url": "", "ats": "comeet_page", "slug": "https://ree.auto/careers/", "source": "override", "status": "ok", "israel_only": true},
{"name": "Remilk", "domain": "remilk.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.remilk.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.remilk.com/careers"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Retym", "domain": "retym.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "retym/C6.003", "source": "override", "status": "ok"},
{"name": "ReWalk", "domain": "rewalk.com", "segment": "deeptech", "crawl": false, "careers_url": "https://golifeward.com/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://golifeward.com/company/careers/"}, "gap": {"reason": "no_openings", "note": "The list is US jobs, none in Israel"}},
{"name": "Riskified", "domain": "riskified.com", "crawl": true, "careers_url": "https://www.riskified.com/careers/", "ats": "greenhouse", "slug": "riskified", "source": "guess", "status": "ok"},
{"name": "SafeBreach", "domain": "safebreach.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "safebreach", "source": "override", "status": "ok"},
{"name": "Salesforce", "domain": "salesforce.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "salesforce/wd12/External_Career_Site", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.salesforce.com/company/careers/locations/emea/israel/", "global": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"}},
{"name": "Salt Security", "domain": "salt.security", "crawl": true, "careers_url": "https://salt.security/careers", "ats": "greenhouse", "slug": "saltsecurity", "source": "guess", "status": "ok"},
{"name": "Samsung", "domain": "samsung.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "sec/wd3/Samsung_Careers", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://www.samsung.com/il/about-us/careers/", "global": "https://research.samsung.com/careers"}},
{"name": "SAP", "domain": "sap.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"israel": "https://careers.sap.com/viewalljobs/750/", "global": "https://jobs.sap.com/en/"}, "gap": {"reason": "no_careers_url", "note": "Blocks plain requests (403)"}},
{"name": "Sapiens", "domain": "sapiens.com", "crawl": true, "careers_url": "https://sapiens.careers.hibob.com/jobs", "ats": "browser", "slug": "https://sapiens.careers.hibob.com/jobs", "source": "override", "status": "ok"},
{"name": "Satixfy", "domain": "satixfy.com", "segment": "deeptech", "crawl": false, "careers_url": "https://satixfy.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://mda.space/careers"}, "gap": {"reason": "site_unusable", "note": "The page is now an MDA Space page"}},
{"name": "SeeTrue", "domain": "seetrue.ai", "segment": "deeptech", "crawl": false, "careers_url": "https://seetrue.ai/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://seetrue.ai/careers/"}, "gap": {"reason": "needs_reader", "note": "Roles are listed in the HTML of its own page; the Dueto board behind them is closed to robots (api.dueto.io robots.txt)"}},
{"name": "Semperis", "domain": "semperis.com", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "semperis", "source": "override", "status": "ok"},
{"name": "SentinelOne", "domain": "sentinelone.com", "crawl": true, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.sentinelone.com/jobs/"}},
{"name": "Sentra", "domain": "sentra.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "sentra/87.00B", "source": "override", "status": "ok"},
{"name": "Sentry Israel", "domain": "sentry.io", "crawl": true, "careers_url": "https://www.sentry.io/careers", "ats": "ashby", "slug": "sentry", "source": "page", "status": "ok"},
{"name": "ServiceNow", "domain": "servicenow.com", "crawl": true, "careers_url": "", "ats": "smartrecruiters", "slug": "servicenow", "source": "override", "status": "ok", "israel_only": true},
{"name": "Siemens", "domain": "siemens.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.siemens.com/en-us/company/jobs/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.siemens.com/en-us/company/jobs/"}, "gap": {"reason": "site_unusable", "note": "No DNS answer for jobs.siemens.com"}},
{"name": "Sight Diagnostics", "domain": "sightdx.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Siklu", "domain": "siklu.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.ceragon.com/about-ceragon/careers"}, "gap": {"reason": "no_careers_url", "note": "Now part of Ceragon; the Comeet board on ceragon.com is Ceragon's, not Siklu's"}},
{"name": "Silicom", "domain": "silicom.co.il", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.silicom.dk/careers/"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Silverfort", "domain": "silverfort.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "silverfort/54.007", "source": "override", "status": "ok"},
{"name": "Similarweb", "domain": "similarweb.com", "crawl": true, "careers_url": "https://www.similarweb.com/corp/careers-prague/", "ats": "greenhouse", "slug": "similarweb", "source": "guess", "status": "ok"},
{"name": "Singular", "domain": "singular.net", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "singular", "source": "override", "status": "ok"},
{"name": "Sisense", "domain": "sisense.com", "crawl": true, "careers_url": "https://www.sisense.com/careers", "ats": "ashby", "slug": "sisense", "source": "page", "status": "ok"},
{"name": "Smart Shooter", "domain": "smart-shooter.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "smart-shooter/0B.00A", "source": "override", "status": "ok"},
{"name": "Snowflake", "domain": "snowflake.com", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "snowflake", "source": "override", "status": "ok", "israel_only": true},
{"name": "Snyk", "domain": "snyk.io", "crawl": true, "careers_url": "https://snyk.io/careers/", "ats": "ashby", "slug": "snyk", "source": "guess", "status": "ok"},
{"name": "SolarEdge", "domain": "solaredge.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://corporate.solaredge.com/en/careers/open-positions"}, "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Speedata", "domain": "speedata.io", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.speedata.io/careers-1"}, "gap": {"reason": "no_careers_url", "note": "Jobs are on Viola Group's Getro board (API robots.txt disallows); its own page is a Wix site with no feed"}},
{"name": "Splitit", "domain": "splitit.com", "crawl": true, "careers_url": "", "ats": "bamboohr", "slug": "splitit", "source": "override", "status": "ok", "israel_only": true},
{"name": "SQream", "domain": "sqream.com", "segment": "cyber_infra", "crawl": false, "careers_url": "https://sqream.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://sqream.com/careers/"}, "gap": {"reason": "needs_reader", "note": "Comeet plugin page: its one job card names no place"}},
{"name": "Stampli", "domain": "stampli.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "stampli/F6.007", "source": "override", "status": "ok"},
{"name": "Stigg", "domain": "stigg.io", "crawl": true, "careers_url": "", "ats": "ashby", "slug": "stigg", "source": "override", "status": "ok"},
{"name": "StoreDot", "domain": "store-dot.com", "segment": "deeptech", "crawl": false, "careers_url": "https://store-dot.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://store-dot.com/careers.html"}, "gap": {"reason": "needs_reader", "note": "A list or CV form with no job link"}},
{"name": "Stratasys", "domain": "stratasys.com", "crawl": true, "careers_url": "https://careers.stratasys.com/search/?q=&locationsearch=Israel", "ats": "browser", "slug": "https://careers.stratasys.com/search/?q=&locationsearch=Israel", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://careers.stratasys.com/"}},
{"name": "Stream Security", "domain": "stream.security", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "streamsecurity/39.008", "source": "override", "status": "ok"},
{"name": "Sunbit", "domain": "sunbit.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "sunbit/37.001", "source": "override", "status": "ok"},
{"name": "SundaySky", "domain": "sundaysky.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "sundaysky/71.000", "source": "override", "status": "ok"},
{"name": "Sweet Security", "domain": "sweet.security", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "sweetsecurity", "source": "override", "status": "ok"},
{"name": "Swimm", "domain": "swimm.io", "segment": "cyber_infra", "crawl": false, "careers_url": "https://swimm.io/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://swimm.io/careers/"}, "gap": {"reason": "needs_reader", "note": "Only an email address"}},
{"name": "Synopsys", "domain": "synopsys.com", "segment": "multinational", "crawl": false, "careers_url": "https://careers.synopsys.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.synopsys.com/careers/global/europe-and-middle-east/israel.html"}, "gap": {"reason": "needs_reader", "note": "Avature board, no adapter yet"}},
{"name": "Syte", "domain": "syte.ai", "segment": "biz_software", "crawl": false, "careers_url": "https://www.syte.ai/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.syte.ai/careers/"}, "gap": {"reason": "needs_reader", "note": "The job list is drawn by a script, with no job link"}},
{"name": "Tabit", "domain": "tabit.cloud", "segment": "biz_software", "crawl": false, "careers_url": "https://www.tabit.cloud/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://tabit.cloud/il/tabit-careers/"}, "gap": {"reason": "no_feed_found", "note": "No ATS link, feed or job link found"}},
{"name": "Taboola", "domain": "taboola.com", "crawl": true, "careers_url": "https://www.taboola.com/careers", "ats": "greenhouse", "slug": "taboola", "source": "guess", "status": "ok"},
{"name": "Taranis", "domain": "taranis.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "taranis/34.003", "source": "override", "status": "ok"},
{"name": "Teramount", "domain": "teramount.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": "Acquired by Molex in May 2026; teramount.com redirects to a Molex page; its one job is on Grove Ventures' Getro board, whose API robots.txt disallows"}},
{"name": "Tevel Aerobotics", "domain": "tevel-tech.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Tipalti", "domain": "tipalti.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "tipaltisolutions", "source": "override", "status": "ok"},
{"name": "Tonkean", "domain": "tonkean.com", "crawl": true, "careers_url": "", "ats": "lever", "slug": "tonkean", "source": "override", "status": "ok"},
{"name": "Torii", "domain": "toriihq.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "toriihq", "source": "override", "status": "ok"},
{"name": "Torq", "domain": "torq.io", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "torq", "source": "override", "status": "ok"},
{"name": "Tower Semiconductor", "domain": "towersemi.com", "crawl": true, "careers_url": "https://careers.towersemi.com/search-result/?q=&category=&location=", "ats": "browser", "slug": "https://careers.towersemi.com/search-result/?q=&category=&location=", "source": "override", "status": "ok"},
{"name": "Transmit Security", "domain": "transmitsecurity.com", "crawl": true, "careers_url": "https://transmitsecurity.com/about/careers", "ats": "greenhouse", "slug": "transmitsecurity", "source": "guess", "status": "ok"},
{"name": "Trax", "domain": "traxretail.com", "crawl": true, "careers_url": "https://traxretail.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://traxretail.com/company/careers/"}},
{"name": "Trigo", "domain": "trigoretail.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "trigo/A6.005", "source": "override", "status": "ok"},
{"name": "Tufin", "domain": "tufin.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "tufin/A1.00B", "source": "override", "status": "ok"},
{"name": "Tyto Care", "domain": "tytocare.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.tytocare.com/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.tytocare.com/careers/"}, "gap": {"reason": "needs_reader", "note": "A list or CV form with no job link"}},
{"name": "Uber", "domain": "uber.com", "segment": "multinational", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "links": {"global": "https://www.uber.com/us/en/careers/"}, "gap": {"reason": "no_careers_url", "note": "Blocks plain requests (403)"}},
{"name": "Ultima Genomics", "domain": "ultimagenomics.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "ultimagenomics", "source": "override", "status": "ok"},
{"name": "Unframe", "domain": "unframe.ai", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "unframe", "source": "override", "status": "ok"},
{"name": "Unity", "domain": "unity.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "unitytech/wd1/Unity", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://unity.com/careers/positions"}},
{"name": "Upsolver", "domain": "upsolver.com", "segment": "cyber_infra", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Upwind", "domain": "upwind.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "upwind/49.004", "source": "override", "status": "ok"},
{"name": "UVeye", "domain": "uveye.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "uveye/92.00F", "source": "override", "status": "ok"},
{"name": "Valens Semiconductor", "domain": "valens.com", "crawl": true, "careers_url": "https://www.valens.com/positions/", "ats": "browser", "slug": "https://www.valens.com/positions/", "source": "override", "status": "ok"},
{"name": "Varonis", "domain": "varonis.com", "crawl": true, "careers_url": "https://www.varonis.com/company/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://jobs.jobvite.com/varonis"}},
{"name": "VAST Data", "domain": "vastdata.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "vastdata/43.001", "source": "override", "status": "ok"},
{"name": "Vayyar", "domain": "vayyar.com", "crawl": true, "careers_url": "", "ats": "workable", "slug": "vayyar", "source": "override", "status": "ok", "verify": false},
{"name": "Verbit", "domain": "verbit.ai", "crawl": true, "careers_url": "https://verbit.ai/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://verbit.ai/careers/"}},
{"name": "Verifone", "domain": "verifone.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "verifone", "source": "override", "status": "ok"},
{"name": "Verint", "domain": "verint.com", "crawl": true, "careers_url": "https://www.verint.com/careers", "ats": "ashby", "slug": "27cfd344-670f-4dcd-8b13-0e18005b0d65", "source": "page", "status": "ok"},
{"name": "Via", "domain": "ridewithvia.com", "crawl": true, "careers_url": "https://ridewithvia.com/careers/jobs", "ats": "greenhouse", "slug": "via", "source": "guess", "status": "ok"},
{"name": "Vimeo", "domain": "vimeo.com", "crawl": true, "careers_url": "https://vimeo.com/careers", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://vimeo.com/jobs"}},
{"name": "Visa", "domain": "visa.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "visa/wd5/Visa", "source": "override", "status": "ok", "israel_only": true, "links": {"global": "https://corporate.visa.com/en/careers.html"}},
{"name": "Voyantis", "domain": "voyantis.ai", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "voyantis/86.00B", "source": "override", "status": "ok"},
{"name": "Walkme", "domain": "walkme.com", "crawl": true, "careers_url": "https://www.walkme.com/careers/", "ats": "lever", "slug": "walkme", "source": "guess", "status": "ok"},
{"name": "Walmart Global Tech", "domain": "walmart.com", "segment": "multinational", "crawl": false, "careers_url": "https://careers.walmart.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://careers.walmart.com/us/en/home/careers-areas/technology"}, "gap": {"reason": "site_unusable", "note": "Timeout"}},
{"name": "Walnut", "domain": "walnut.io", "segment": "biz_software", "crawl": false, "careers_url": "https://www.walnut.io/jobs/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.walnut.io/jobs/"}, "gap": {"reason": "needs_reader", "note": "Jobs on its own site, no ATS behind them"}},
{"name": "Watergen", "domain": "watergen.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "Weebit Nano", "domain": "weebit-nano.com", "segment": "deeptech", "crawl": false, "careers_url": "https://www.weebit-nano.com/company/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://www.weebit-nano.com/company/careers/"}, "gap": {"reason": "bot_protection", "note": "A challenge page with no links"}},
{"name": "Weka", "domain": "weka.io", "crawl": true, "careers_url": "https://www.weka.io/careers", "ats": "browser", "slug": "https://www.weka.io/careers", "source": "override", "status": "ok"},
{"name": "Western Digital", "domain": "westerndigital.com", "segment": "multinational", "crawl": false, "careers_url": "https://www.westerndigital.com/careers", "ats": "smartrecruiters", "slug": "WesternDigital", "source": "page", "status": "ok", "links": {"global": "https://careers.smartrecruiters.com/westerndigital"}, "gap": {"reason": "no_openings", "note": "The SmartRecruiters board has no job in Israel today"}},
{"name": "Wiliot", "domain": "wiliot.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "wiliot/F6.003", "source": "override", "status": "ok"},
{"name": "Windward", "domain": "windward.ai", "crawl": true, "careers_url": "https://windward.ai/company/careers/", "ats": "ashby", "slug": "windward", "source": "guess", "status": "ok"},
{"name": "Wix", "domain": "wix.com", "crawl": true, "careers_url": "https://careers.wix.com", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"israel": "https://careers.wix.com/location/tel-aviv", "global": "https://careers.wix.com/"}},
{"name": "Wiz", "domain": "wiz.io", "crawl": true, "careers_url": "https://www.wiz.io/careers", "ats": "greenhouse", "slug": "wizinc", "source": "guess", "status": "ok"},
{"name": "Wolt", "domain": "wolt.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "wolt", "source": "override", "status": "ok"},
{"name": "Workday", "domain": "workday.com", "crawl": true, "careers_url": "", "ats": "workday", "slug": "workday/wd5/Workday", "source": "override", "status": "ok", "israel_only": true, "links": {"israel": "https://workday.wd5.myworkdayjobs.com/Workday/?q=Israel", "global": "https://workday.wd5.myworkdayjobs.com/Workday"}},
{"name": "Workiz", "domain": "workiz.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "workiz/F6.006", "source": "override", "status": "ok"},
{"name": "WSC Sports", "domain": "wsc-sports.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "wsc-sports/93.007", "source": "override", "status": "ok"},
{"name": "X-Globe", "domain": "x-globe.com", "segment": "deeptech", "crawl": false, "careers_url": "", "ats": "", "slug": "", "source": "", "status": "not_found", "gap": {"reason": "no_careers_url", "note": ""}},
{"name": "XM Cyber", "domain": "xmcyber.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "xmcyber/15.005", "source": "override", "status": "ok", "israel_only": true},
{"name": "Xtend", "domain": "xtend.me", "segment": "deeptech", "crawl": false, "careers_url": "https://xtend.me/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://xtend.me/careers/"}, "gap": {"reason": "bot_protection", "note": "A challenge page with no links"}},
{"name": "Yotpo", "domain": "yotpo.com", "crawl": true, "careers_url": "https://www.yotpo.com/careers/", "ats": "greenhouse", "slug": "yotpo", "source": "guess", "status": "ok"},
{"name": "Zencity", "domain": "zencity.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "zencity/56.00B", "source": "override", "status": "ok"},
{"name": "Zenity", "domain": "zenity.io", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "zenity/19.000", "source": "override", "status": "ok"},
{"name": "Zero Networks", "domain": "zeronetworks.com", "crawl": true, "careers_url": "", "ats": "comeet", "slug": "zeronetworks/39.00F", "source": "override", "status": "ok"},
{"name": "Zesty", "domain": "zesty.co", "crawl": true, "careers_url": "https://zesty.co/careers/", "ats": "", "slug": "", "source": "", "status": "careers_page_only", "links": {"global": "https://zesty.co/careers/"}},
{"name": "Zoom", "domain": "zoom.us", "crawl": true, "careers_url": "", "ats": "workday", "slug": "zoom/wd5/Zoom", "source": "override", "status": "ok", "israel_only": true},
{"name": "Zoominfo", "domain": "zoominfo.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "zoominfo", "source": "override", "status": "ok"},
{"name": "Zscaler", "domain": "zscaler.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "zscaler", "source": "override", "status": "ok", "israel_only": true},
{"name": "Zynga", "domain": "zynga.com", "crawl": true, "careers_url": "", "ats": "greenhouse", "slug": "zyngacareers", "source": "override", "status": "ok"}
]
$companies$::jsonb) as r (
  name text, domain text, segment text, crawl boolean, careers_url text, ats text, slug text, source text,
  status text, israel_only boolean, verify boolean, links jsonb, gap jsonb
);

create function public.companies_before_write() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  if new.excluded is not null then
    perform pg_advisory_xact_lock(hashtext('companies_excluded_limit'));
    if (select count(*) from public.companies where excluded is not null and id is distinct from new.id) >= 200 then
      raise exception 'at most 200 excluded companies (companies_excluded_limit)'
        using errcode = 'check_violation', constraint = 'companies_excluded_limit';
    end if;
  end if;
  return new;
end;
$$;

create function public.companies_log_change() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if public.is_admin() then
    insert into public.admin_log (action, target) values (
      case tg_op when 'INSERT' then 'company added' when 'UPDATE' then 'company changed' else 'company removed' end,
      case tg_op when 'DELETE' then old.name else new.name end
    );
  end if;
  return null;
end;
$$;

revoke all on function public.companies_before_write() from public, anon, authenticated;
revoke all on function public.companies_log_change() from public, anon, authenticated;

create trigger companies_before_write before insert or update on public.companies
  for each row execute function public.companies_before_write();
create trigger companies_log_change after insert or update or delete on public.companies
  for each row execute function public.companies_log_change();
