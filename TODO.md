# TODO

Open work, as of 2026-11-14. Each item says what triggers it, how to do it and when it is done. When one is finished,
delete it and update the counts in the others: the 85 and the four segment counts in the research item (a pin removes
one row from one segment), its "(28)" if the company had no careers URL, else its "(56 today)".
An item with a screenshot in `todo-images/` goes with it: delete the image file in the same PR.
Observations about live sites below come from checks made on 2026-11-14 and may have changed.

## Emails

- [ ] **Make the emails designed HTML documents.** Trigger: both emails people receive from Misra (the confirmation
  email and the job-alert digest) are plain text today. A designed email, in the look of the web app, is easier to read
  and to act on, and looks like the same product. Plain text stays as the fallback.
  - Where: the digest is built in `send_digests.py` (Resend payload, `"text"` today; add `"html"` so the message is
    multipart); the confirmation email in the Edge Function `supabase/functions/email-settings` (and its plain modules
    under `supabase/functions/`); the approved wording in `supabase/email-texts.md`, pinned by
    `tests/test_legal_wording.py` and `test/email-texts.test.mjs`; colours and type from `design-system/tokens.css`
    (emails cannot use CSS variables, so the tokens are copied as fixed values into one place and a test keeps them
    equal to `tokens.css`). The site-report email to the maintainer and the failure alerts (`send_alert.py`, Gmail)
    stay plain text: nobody else reads them.
  - How: consult before building, and record every answer in the PR. 1) Claude Designer plus the UI/UX experts
    (`design-super-agent`, `ui-design-review`, `ux-web-review`, `accessibility-expert`): a simple single-column layout
    (about 600px, fluid on a phone), the logo as text or a small inline image with alt text (many clients block
    images, so nothing may depend on one), the digest list (company, title, place, one clear "view role" link per job),
    the confirmation email with one primary button (email clients do not support the HTML `<button>` tag: a button is a link
    styled as one, with padding and a background colour, and it needs a version that still looks right in Outlook), light and dark (`prefers-color-scheme` where supported, and colours
    that stay readable where the client forces its own), 44px targets, contrast WCAG AA. Two or three layouts, one
    picked with reasons. 2) The `technical-writer` agent: wording stays EXACTLY as in `supabase/email-texts.md`
    (the lawyer approved it; the HTML only changes the layout, never the words, the footer, the postal address, the
    unsubscribe link or the explanation of why the person got it). No word is changed, added or removed.
    Ask the experts whether the full URL should be shown next to the button link, for copying and pasting
    (a common habit; it may no longer be needed). KISS: leave it out unless they show it helps, and it must not add
    or change any approved word.
    3) The `advisor`, before choosing the approach and before calling it done.
  - Build it for email clients, not browsers: table layout, inline styles (a build step may inline a small stylesheet),
    no JavaScript, no web fonts (system font stack), no background images, `role="presentation"` on layout tables,
    `lang` and `dir` set, preheader text, and keep the whole HTML well
    below Gmail's clipping size (about 100 KB; a digest with many jobs must stay under it, so cap or split the list as
    the digest already does). Keep the `List-Unsubscribe` and `List-Unsubscribe-Post` headers and the unsubscribe link in
    the footer exactly as now. A test that the plain text and the HTML carry the same words and links (strip the tags and
    compare), one that every approved sentence appears, one that the HTML has no script, no remote image and no
    external stylesheet, and a test of the size limit. Render checks: Playwright at 600px and 375px, light and dark;
    send test messages to the maintainer's own address only (never to a subscriber) and look at them in Gmail (web and
    phone) and one other client; list what was checked.
  - Status: built in the HTML emails PR (layout, tests, screenshots). Left: the maintainer looks at both emails in Gmail
    (web and phone) and one other client, then this item is removed.
  - Done when: both emails go out as multipart (plain text plus HTML) with the same approved words, the maintainer has
    looked at both in Gmail and one other client, the tests above pass, `npm run lint` and `npm run test:coverage` pass,
    and the PR shows screenshots. A change to `send_digests.py` needs a version bump and a changelog entry.

## Yearly security review

- [ ] **Do the yearly security review by 2027-11-15, then every year.** Trigger: the date. Where: the
  security note in `SECURITY.md` (it lists the checks: provider logs, who has access, two-factor sign-in, key
  rotation, Dependabot and CodeQL alerts, and re-reading `PRIVACY.md` and `TERMS.md` against the product).
  - Also each year: renew the GitHub token that cron-job.org uses to start the daily run (about a year after it was
    created; delete the old token in your GitHub settings under personal access tokens), and check that the
    `X-GitHub-Api-Version` header in the cron-job.org job is still supported (`2022-11-28` is deprecated and ends on
    2028-03-10; move it to the newest version before then and test the job once).
  - Done when: the checks are done and the date and result are written in the note, and this item is moved to the
    next year.

## Review the Node and Python pins

- [ ] Trigger: Node 27 becomes LTS (scheduled 2027-10-20) or Python 3.12 nears its end of life (October 2028), whichever
  comes first. Nothing to do before then.
  - State now (as of 2026-11-14, from the Node release schedule and the Python release page): Node 26 has been LTS since
    2026-10-28 and `.nvmrc` is on its latest release (26.13.1). Python has no LTS; 3.12 is the floor and the version CI
    and the daily job run.
  - Where the pins live, to change together:
    - Node: `.nvmrc`; `engines.node` in `package.json` (`>=26.13.1 <27`, so the cap must be raised too); the same
      `engines` value under `packages[""]` in `package-lock.json` (regenerate it with `npm install`; the other `engines`
      lines in the lock file belong to dependencies and are left alone); `packageManager` in `package.json` (npm 11.20.0
      today: check it still fits the new Node); the Node badge and "Node 26" in `README.md`. The `ci.yml` and
      `release.yml` workflows read `.nvmrc`, so they follow.
    - Python: `python-version` in `ci.yml`, `release.yml` and `daily.yml`; `requires-python`, ruff `target-version`
      and mypy `python_version` in `pyproject.toml`; "Python 3.12+" in `README.md`.
    - Playwright is pinned twice, `playwright==1.65.0` in `pyproject.toml` and in the `pip install` line of `daily.yml`,
      and the two must change together: Dependabot can bump `pyproject.toml` but not the workflow line, and `daily.yml`
      also installs `requests` unpinned. Check that the pinned version supports a new Python before moving it.
      `markdown-it-py` is pinned the same way, in `pyproject.toml`, `daily.yml` and `publish-docs.yml` (a test keeps
      the three equal).
  - Steps:
    1. Check the dates at the Node release schedule and the Python release page; take the newest LTS (Node) or the
       newest release that Playwright and the dev tools support (Python).
    2. Change the pins above in one PR. It touches `package.json` or `pyproject.toml`, so it needs a version bump and a
       `CHANGELOG.md` entry (see `AGENTS.md`); the version moves in `package.json`, `pyproject.toml` and the root entry
       of `package-lock.json`.
    3. Run `npm run lint`, `npm run typecheck` and `npm run test:coverage` on the new versions, with Chromium installed
       so the browser tests run (a machine on an older Node cannot `npm ci`: use the new Node).
    4. After the merge, run the daily workflow once by hand (Actions, "daily-job-digest", Run workflow) and check it is
       green.
  - Done when: the pins name the new versions and that manual run passed. Put the next review date here.

## Research the remaining unresolved companies

- [ ] `data/unresolved_candidates.csv` lists 83 researched companies with no usable job feed (as of 2026-11-14: 24
  multinational, 36 deeptech, 12 cyber_infra, 11 biz_software). Each needs
  its own research; there is no batch fix left. Every observation about a live site below is from the checks of
  2026-11-14, a crawl of the 105 rows that had a careers URL that day (56 today) and a scan of their rendered pages. The
  remaining work falls into these groups:
  - **No careers URL (27).** Status `not_found` in `data/new/*_companies.json`: Upsolver, Speedata,
    Teramount, X-Globe, Q Factor, Gilat, Siklu, Silicom, Sight Diagnostics, Brainsway, EarlySign, Diagnostic Robotics,
    Tevel Aerobotics, CytoReason, SolarEdge, Watergen, Stratasys, AIR, Intel, SAP, eBay,
    Autodesk, Akamai, Ford, Ericsson, Uber, Keysight. Find the real
    career page first, then continue with the groups below. The large multinationals will need a location-filtered
    address or their ATS (see the last group).
  - **A job board was found but there is no adapter.** Playstudios and Booking.com. Playstudios was listed as a Jobvite
    board, but on 2026-11-14 `jobs.jobvite.com/playstudios-inc` is an invalid board and the career page links to
    Dayforce (`jobs.dayforcehcm.com/en-US/playstudios/CANDIDATEPORTAL`). `browser_crawl.crawl_many` reads its 12 jobs with
    real titles and distinct links but an empty location for every one, so none could be recognised as Israel (the page
    shows 5 in Tel Aviv-Yafo), so it is not a browser pin. The portal has a JSON search (`POST
    /api/geo/playstudios/jobposting/search`, locations as `formattedAddress`); a Dayforce adapter would read it, but no
    other company in the lists is on Dayforce, so write one only when a second appears. Booking.com (iCIMS) is skipped: its iCIMS board answered
    403 to a plain request on 2026-11-14 (bot protection, not to be bypassed) and `careers.booking.com` is a custom site
    with no job feed; revisit it with the large multinationals. A new adapter is its own PR: a function in `adapters.py`,
    an entry in `ADAPTERS`, tests with the fakes in `tests/test_adapters.py`, a version bump and a changelog entry.
  - **A board was found but its address is wrong or dead.**
    - Pliops: on 2026-11-14 the Comeet page for `pliops/05.004` said "This Spark Hire Recruit account has been
      deactivated", and it still did on 2026-11-16, but `pliops.com/careers` still links to that page, so the company has published no
      new board. A Workable account `pliops` exists (`adapters.workable("pliops")` answers with no job) but nothing links
      to it, so it is not pinned: pin it, or the board the career page ends up linking to, once it lists a job.
    - Western Digital: the SmartRecruiters board (`WesternDigital`, 325 jobs) has no Israel job today, so there is
      nothing to show. It can be pinned with `israel_only` now that the matcher and the new-company rule are fixed, but it
      would fetch 325 jobs a day to store none.
    - Cloudflare: the Greenhouse board's locations are "Hybrid" or "Distributed", never a city, so an Israel job could
      not be recognised. The adapter would need to read the offices (`?content=true`) first.
  - **The page returned links, not jobs.** Checked on 2026-11-14: Ex Libris links to `careers.clarivate.com`, a
    worldwide Phenom board of its parent Clarivate (not Ex Libris only); Ormat's list is a ViziRecruiter board
    (`vizi.vizirecruiter.com/OrmatTechnologiesInc-4638`, one company so no adapter); Optibus is a Comeet plugin
    page, done on 2026-11-16 below; REE Automotive is in the next group.
  - **Large multinational boards that show a worldwide first page.** Google, Intuit, Apple, IBM
    and others returned jobs, but almost none in Israel (Apple's page had one), because the page is a worldwide search.
    They need a career address filtered to Israel (as `citi.eightfold.ai/careers?location=Israel` is) with `israel_only`,
    or their ATS. Check that the filter works before pinning.
    Findings on the 24 multinationals left after the pins of 2026-11-14 (so the next person does not repeat them):
    - Workday guesses that still do not answer (checked again on 2026-11-14 after the adapter fix): Intel
      (`intel/wd1/External`) and Autodesk (`autodesk/wd1/Ext`, `autodesk/wd1/uni`) are sent to Workday's maintenance
      page, which is also what a tenant that does not exist on that data centre gets, and Akamai
      (`akamai/wd1/akamaicareers`) answers 500. The tenant, data centre or site name is still unknown: find it from the
      company's own career page, not by guessing.
    - Bot protection (403 to a plain request, 2026-11-14; not to be bypassed): Intel (`jobs.intel.com`), Autodesk,
      Akamai, General Motors (`search-careers.gm.com`), Ford, SAP, Uber, Booking.com.
    - Other platforms with no adapter yet: AMD and Keysight (iCIMS: `careers-amd.icims.com`,
      `careers-keysight.icims.com`), Synopsys (Avature: `synopsys.avature.net`), Intuit (Radancy: the search takes a
      location only with coordinates and shows "Multiple Locations" for many jobs), Google (the results page embeds
      its data; the crawler read only navigation links on `?location=Israel`), Apple (`jobs.apple.com/en-il/search`
      is a script app; `/api/role/search` answers a redirect), IBM and Meta (no feed found).
    - Ericsson: `jobs.ericsson.com/careers?location=Israel` is an Eightfold board that shows "0 jobs" in Israel today,
      and the crawler reads its "Join Talent Network" link as a job, so it is not pinned; pin it once it lists one.
    - eBay (`careers.ebayinc.com`), Walmart Global Tech (`careers.walmart.com`, a timeout), Siemens
      (`jobs.siemens.com`, no DNS answer), Align Technology (`jobs.aligntech.com`) and Rapid7
      (`careers.rapid7.com/jobs/search`): no feed found in the page HTML; Align and Rapid7 were looked at again on
      2026-11-16 (third batch, below).
    - Done on 2026-11-14: new adapters `oraclehcm` (Oracle, JPMorgan Chase, Dell, American Express, Nokia), `amazon`
      and `phenom` (Cisco, GE HealthCare, Mastercard); Workday pins (Salesforce, Cadence, Edwards, Adobe, Zoom,
      Marvell, Medtronic, KLA, Kyndryl, Workday, then Broadcom and Philips once the adapter read a country value with
      its own id and the places of a location facet); HP on Eightfold. American Express and Nokia list no job in
      Israel today, and Adobe and Zoom none either.
  - **Pages that loaded and showed no job links.** 72 of the 105 pages crawled on 2026-11-14 returned no job, five of
    them with an empty title: the list is probably drawn by JavaScript from a JSON feed. Open the page, watch its network
    requests for the feed, then decide between an adapter and a browser pin.
    Findings of 2026-11-14 (feed-finding pass): most of these pages were not JavaScript apps but WordPress sites with the
    Comeet plugin, whose job links look like `/careers/co/<place>/<XX.XXX>/<title>/all`, which the crawler's link pattern
    does not match. The page source holds `"comeet_uid":"<uid>"` (and a token) when the plugin is configured with the
    company's account; the `comeet` adapter then reads the board with slug `<name>/<uid>`. Try the careers-api first, as
    `https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>&details=true`: its jobs carry the board
    name in `url_comeet_hosted_page` (any name works for a valid uid; a wrong uid gives no data). Done: Retym
    (`retym/C6.003`), Qedma (`qedma/7A.006`), Qodo (`qodo/B8.00B`), Trigo (`trigo/A6.005`), Cyolo (`cyolo/F5.005`; its page
    shows a `company-uid` in the job page source, not the career page), Allot (`allot/C4.009`, 24 jobs, one in Israel;
    the uid is also in a vacancy page), and three boards with no job today, pinned with `israel_only` so the first Israel job
    is reported as new: Foretellix (`Foretellix/84.007`), XM Cyber (`xmcyber/15.005`) and Dataloop (`dataloop/B5.00A`); Sunbit (`sunbit/37.001`, 40 jobs, 8 in Israel; the uid is in the
    source of a `?job=` page) and Firefly (`firefly/39.000`, `israel_only`: the board holds one "Future Opportunities" entry
    with no place), and DarioHealth (`Dario.Health/34.006`, 2 jobs, both in India today), and OX Security (`ox_security/F8.006`, 36 jobs,
    7 in Israel; the uid is in the career page source, the board name `ox_security` in the careers-api answer),
    and, on 2026-11-16, four Comeet plugin pages whose account answers 400, with the new `comeet_page` adapter (the slug
    is the page address; it reads the plugin's links `/co/<place>/<uid>/<title>` from the page HTML), all `israel_only`:
    REE Automotive (`https://ree.auto/careers/`, 2 jobs, 1 in Israel), Gauzy (`https://www.gauzy.com/careers/`, 9 jobs, none
    in Israel), Guesty (`https://www.guesty.com/careers/`, 35 jobs, 8 in Israel) and Apester (`https://apester.com/careers/`:
    "We don't have any open positions" today, so the first Israel job is reported as new).
    On 2026-11-16, Ceva (`ceva/76.005`, found in the job-page source of its own `/career/` site; the page names the
    account SNC but the board answered under the name `ceva`, not `SNC`; 25 jobs, 14 in Israel, `israel_only`).
    Converge Bio (`convergebio/CA.00D`: the real domain is `converge-bio.com`, not `convergebio.com`; its `/careers`
    page is a Framer page that loads the Comeet widget, with `company-uid` `CA.00D` in the source; the board answers
    under the name `convergebio` and lists no job today, `israel_only` so the first Israel job is reported as new).
    Optibus (`https://optibus.com/company/careers/jobs/` with `comeet_page`, `israel_only`: the careers page only links
    to this plugin page, which lists 6 jobs, 1 in Tel Aviv).
    Nano Dimension (`nanodimension/62.003`: the `comeet_uid` is in the source of `nano-di.com/job-openings`, a Comeet
    plugin page the `comeet_page` adapter cannot read; the board holds 4 jobs, none in Israel, `israel_only`).
    Not done, and why (checked on 2026-11-14, or 2026-11-16 where dated; do not repeat):
    - Comeet plugin pages that the `comeet_page` adapter does not read (checked on 2026-11-16): SQream
      (`sqream.com/careers/` lists 1 job, "Controller", whose card names no place, and its `/co/` link holds the
      department, so the Israel filter would drop it; pin it when its cards show a place) and Kornit
      (`careers.kornit.com/all-positions/` lists 11 jobs with its own theme and `/cmcareer/<id>/` links, not the
      plugin's, so it needs its own parser, for one company).
    - Comeet account valid but not the company's own board: Magic Software (19.00F is Matrix IFS, 23 jobs, none in
      Israel). NeuReality (89.002) answers no job through the `comeet` adapter (its hosted page is not served), but its
      career page is a Comeet plugin page that the `comeet_page` adapter reads: pinned on 2026-11-16, `israel_only`,
      "We don't have any open positions" today, so the first Israel job is reported as new.
    - No openings at all, no feed to pin: Mitiga ("We don't have any openings right now"), Applitools ("No openings
      available"), H2Pro and Madgicx (no open positions; Madgicx takes CVs through a ClickUp form).
    - Own-site pages the crawler's link pattern does not match, no ATS behind them: Pillar Security
      (`/open-position/<slug>`, 1 Tel Aviv job), Walnut (`/jobs/<slug>`), env0 (`/careers/<slug>`), Deep Instinct (PDF job
      descriptions, none in Israel), Firefly's own `/positions/` pages (Firefly itself is pinned above), Syte (job list
      by script, no job link), Tyto Care and StoreDot (list or CV form with no job link), Swimm (an email address).
    - Checked on 2026-11-16 (first batch, do not repeat): Stratasys is SuccessFactors (`careers.stratasys.com`, no
      adapter); Speedata's jobs are on Viola Group's Getro board and its own page is a Wix site with no feed; Siklu is
      now part of Ceragon, whose Comeet board is Ceragon's; CytoReason's old plugin page answers 404; Diagnostic
      Robotics' list shows "No items found"; Guesty's page is a Next.js app that shows no uid even on a job page. Guessing
      the Greenhouse, Lever, Ashby and Workable slugs of the 21 companies without a careers URL found only an unrelated
      company (`air` on Greenhouse). Western Digital's board is on SmartRecruiters, which robots.txt skips.
    - Checked on 2026-11-16 (second batch, do not repeat): no adapter for iCIMS (AMD, Keysight: robots.txt of
      `careers-amd.icims.com` and `careers-keysight.icims.com` is `Disallow: /`) or Avature (Synopsys alone: the
      robots.txt of `generalmotors.avature.net` disallows `/careers`) or SuccessFactors (Stratasys alone) or Pinpoint
      (Align Technology alone). Cloudflare's Greenhouse offices (`?content=true`) list no Israel or Tel Aviv office, so
      reading them would find no job. Pliops and Ericsson are unchanged. The homepages of Gilat (307 without a
      redirect target), Silicom (redirects to `silicom-usa.com`), Sight Diagnostics, Tevel, Watergen, AIR and X-Globe
      show no careers link and `/careers`, `/career`, `/jobs` answer 404; Upsolver answers 403 (bot protection);
      Brainsway's careers page is its LinkedIn page; Teramount, Q Factor and EarlySign do not answer (Converge
      Bio's real domain is `converge-bio.com`: done above).
      A scan of the careers pages and their job links for ATS markers found only Nano Dimension (done above).
    - Checked on 2026-11-16 (third batch, do not repeat): Converge Bio, Teramount, Q Factor and EarlySign were looked
      up under their real names. Converge Bio's domain is `converge-bio.com` (pinned above); Teramount's own domain
      does not answer and its jobs are on Grove Ventures' Getro board (`careers.grovevc.com/companies/teramount-2`, a
      venture fund's board, no adapter); the real domain of Q Factor and EarlySign could not be found (`q-factor.io`
      is another site). Slug guesses (Greenhouse, Lever, Ashby, Workable, three candidates each) for the other ~50 rows
      with a name and no known ATS found only an unrelated `air` account. Extra careers paths (`/company/careers`,
      `/join-us`, `/about/careers`, `/positions` and others) on X-Globe, Tevel, Sight, Watergen, AIR, Brainsway,
      Diagnostic Robotics, CytoReason and Gilat answered nothing. Rapid7's `careers.rapid7.com/jobs/search` is its own
      Rails and Turbo platform (`/jobs/<slug>-<uuid>`, server-drawn pages of 16): `?location=Israel` is ignored, so
      it returns a worldwide list and would need its own adapter. Align Technology's Pinpoint board
      has a `jobs.rss` feed, but Pinpoint is used by Align alone. The careers pages of the remaining own-site rows
      show no Comeet, Greenhouse, Lever, Ashby, Workable, BambooHR, Recruitee, Teamtailor or Personio marker besides
      those already listed.
    - Checked on 2026-11-16 (fourth batch, do not repeat): Getro boards (Grove Ventures, Viola Group; TLV Partners'
      board is `jobs.tlv.partners`) are not readable. The board pages (`careers.grovevc.com`) allow crawling in their
      robots.txt but hold no job list: the list is drawn by script from `POST
      api.getro.com/api/v2/collections/<network id>/search/jobs`, and the robots.txt of `api.getro.com` is `Disallow: /`
      for every agent, so no adapter was written. The boards' sitemaps list company pages and job pages with a
      JSON-LD `JobPosting`, but the list is incomplete (Teramount's one job is not in Grove's sitemap), so it is not a
      feed. Getro's own job link is the employer's page when it has one (`teramount.com/careers/?job=1`). Revisit only
      if Getro publishes a feed or lifts the API rule. Q Factor's real domain is `q-factor.com` (a one-page site, a
      neutral-atom quantum company that emerged from stealth in April 2026; its only job route is a `careers@` email
      address, so nothing to pin); the lists now carry the right domain. Teramount was acquired by Molex (completed
      on 2026-05-07; `teramount.com` redirects to a Molex product page), so its jobs would be Molex's worldwide board.
      EarlySign (Medial EarlySign): `earlysign.com` has no DNS answer, `earlysign.ai` is a parked page, and a Start-Up
      Nation Central listing (403 to us, seen only in a search summary) says it ceased operations in October 2025; not
      confirmed by the company.
    - Dead or unusable: Jelly Button Games, Kahun, Nucleai and Quantum Art (domain parked at GoDaddy), Satixfy (now an
      MDA Space page), Line 5 (needs a login), lakeFS and Quantum Art (403 or "Access Denied": bot protection), Radware
      and Camtek (an empty page), Elbit Systems (timeout on the news page; its career site `elbitsystemscareer.com` is a
      Hebrew custom site with no feed seen), ReWalk (its list on `golifeward.com/company/careers/` is US jobs, no Israel
      job), Lumigo, Aniview, AnyClip, Pay-Me, Evogene, Aleph Farms, Remilk, Redefine Meat, Landa, Tabit,
      Cognata: no ATS link, feed or job link in the HTML or the network
      requests; not investigated further.
  - **Bot protection.** Some pages answered 403, "Access Denied" or a browser check. Leave them and record it. Do not try
    to get around bot protection: a few pinned companies (Check Point, Papaya Global, Unity) are already blocked from the CI
    runner this way and show up as gaps on the dashboard and in the job log.
  - How to research a company:
    1. Find the real career page, then look for the job board it uses: an embedded or linked Greenhouse, Lever, Ashby,
       Workable, SmartRecruiters, Comeet or Workday board (`discover.ATS_LINK` has the patterns, and the page's HTML,
       links and iframes all need a look, since scripts hide them) or a JSON feed in its network requests.
       A Workable job link on a page is `apply.workable.com/j/<shortcode>`: it names no account (open it: it redirects
       to `apply.workable.com/<account>/j/<shortcode>`). `ATS_LINK` skips these links since 0.10.1.
    2. With an existing adapter, probe it from a shell (`adapters.ADAPTERS["greenhouse"]("slug")` and so on). Without
       one, the browser crawler is the fallback: `browser_crawl.crawl_many([url])` (set `PW_CHROMIUM` to a Chromium
       build, or run `playwright install chromium`) shows what it reads.
    3. Read the result before pinning. It needs real titles, distinct links and a location per job that the Israel matcher
       (`discover.ISRAEL`) can recognise, for the jobs that will appear and not only the ones there today. A list of
       navigation links, a worldwide first page or a partial list is not a feed.
    4. Pin with four edits (`israel_only` for a large multinational), one company or one source per PR so a bad pin is
       easy to drop. Edit the files by hand and append at the end: do not rerun `discover.py`, which re-probes every
       company and rewrites `data/companies.json`.
       - `data/overrides.json`: `"Visa": {"ats": "workday", "slug": "visa/wd5/Visa", "israel_only": true}`
       - `data/companies.json`: `{"name", "domain", "careers_url": "", "ats": "workday", "slug", "source": "override",
         "status": "ok", "n_jobs", "n_israel", "israel_only": true}`, with `n_jobs` and `n_israel` from the probe (an
         `israel_only` adapter returns Israel jobs only, so they are equal; the domain is in
         `data/unresolved_candidates.csv`)
       - `data/seed_companies.csv`: one `Name,domain` row
       - remove the row from `data/unresolved_candidates.csv`
       - both CSV files use CRLF line endings: keep them, and update the counts named at the top.
       A board with no Israel job today is fine to pin: the `israel_only` rule reports the first Israel job it ever
       lists. Code changes (an adapter, a parser fix) need a version bump and a changelog entry; data-only PRs do not.
       Probe a Workday slug with `.venv/bin/python -c "import adapters; print(adapters.workday('visa/wd5/Visa'))"`:
       `None` is a host that did not answer with a job search (Workday tenants go to a maintenance page for hours at a
       time, and answer again later: probe on more than one day before giving up), `[]` is a board with no Israel job.
    5. If a company cannot be done, say why. Set the `reason` (one of the keys of `build_dashboard.REASONS`) and a short `note` of
       the row in `data/unresolved_candidates.csv`: the coverage gap card reads them, and `tests/test_todo_counts.py` fails
       when the counts quoted here and the rows disagree.
  - Tools: `browser_crawl.crawl_many` over every row with a careers URL, and a scan of the rendered pages for ATS links,
    were the two passes behind this list; both were throwaway scripts and are not in the repo. Adding them as a dev tool
    next to `survey.py` and `survey2.py` would make a re-run easy (a new module must be registered as `AGENTS.md`
    describes).
  - Done when: `data/unresolved_candidates.csv` is empty, or every remaining row has a `reason` that says why it cannot be done.
