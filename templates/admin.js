/* The admin page (PLAN_2.md step 1.2). It has no sign-in of its own: the sign-in is on the dashboard, and the session it
   saves is shared on the origin (the same storage key and wrapper, see session.js). This script builds its own client
   from the vendored supabase.js, asks the database `is_admin()` and shows one of five states in `data-state` of the main
   element: checking (the default, everything hidden), signed-out, not-admin, check-failed and ready. The page has no
   power of its own: what an admin may do is decided by row-level security and the database, never by this script, and a
   page that is not ready asks for nothing more. It never starts a redirect. */
import { STORAGE_KEY, storage } from './session.js';

const $ = (id) => document.getElementById(id);
const SUPABASE_URL = '__SUPABASE_URL__';
const SUPABASE_ANON_KEY = '__SUPABASE_ANON_KEY__';
const HTTP_UNAUTHORIZED = 401;
const CHECK_TIMEOUT_MS = 15000; /* the library retries a failing renewal for minutes: the page does not wait that long */
const HTTP_CLIENT_ERROR = 400;
const HTTP_SERVER_ERROR = 500;
const MESSAGES = {
  expired: 'Your sign-in expired.',
  loading: 'Loading…',
  failed: 'Could not load this area.',
  retry: 'Try again',
};
/* The page title of each area, and what loads it. A loader resolves to true when it showed something. */
const AREAS = {
  crawl: { title: 'Crawl', load: loadCrawl },
  companies: { title: 'Companies', load: loadCompanies },
  alerts: { title: 'Alerts', load: loadAlerts },
  log: { title: 'Log', load: loadLog },
};
const FIRST_AREA = 'crawl';
const PAGE_TITLE = 'Admin';

const main = $('main');
const opened = new Set(); /* the areas that have started loading */
let client = null;

function setState(state) {
  main.dataset.state = state;
  for (const part of main.querySelectorAll('[data-when]')) part.hidden = !part.dataset.when.split(' ').includes(state);
  if (state === 'ready') showArea(areaName(), false);
  else document.title = PAGE_TITLE;
  if (state !== 'ready') clearAddresses(); /* an address typed is not kept once the page stops being ready */
}

/* A word of the area's own: its `notice` or its plain text, in the area's box. */
function paint(box, state, text, retry) {
  const section = box.parentElement;
  box.replaceChildren();
  if (state === 'loading') box.setAttribute('role', 'status');
  else box.removeAttribute('role');
  box.className = state === 'error' ? 'admin-state notice' : 'admin-state';
  if (state === 'error') box.setAttribute('role', 'alert');
  const line = document.createElement('p');
  line.textContent = text;
  box.append(line);
  if (state === 'error') {
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'btn btn-pill';
    again.textContent = MESSAGES.retry;
    again.addEventListener('click', async () => {
      await retry();
      $(`${section.id}Title`).focus(); /* the pressed button is gone: the focus goes to the area's heading */
    });
    box.append(again);
  }
}

/* loading -> empty or ready, or error with "Try again". */
async function loadArea(name) {
  const section = $(name);
  let box = section.querySelector('.admin-state');
  if (!box) {
    box = document.createElement('div');
    box.className = 'admin-state';
    section.append(box);
  }
  section.dataset.state = 'loading';
  paint(box, 'loading', MESSAGES.loading);
  try {
    const filled = await AREAS[name].load(box);
    box.removeAttribute('role'); /* the loading line was a status; what replaced it is not */
    section.dataset.state = filled ? 'ready' : 'empty';
    if (!filled) paint(box, 'empty', section.dataset.empty);
  } catch (error) {
    console.error(error);
    section.dataset.state = 'error';
    paint(box, 'error', MESSAGES.failed, () => loadArea(name));
  }
}

function isArea(name) {
  return Object.hasOwn(AREAS, name);
}

function areaName() {
  const name = location.hash.slice(1);
  return isArea(name) ? name : FIRST_AREA;
}

/* Shows one area and hides the rest. A change the visitor made moves the focus to the area's heading, so a keyboard or
   screen reader user lands where the content starts; the first show on load leaves it where it is. */
function showArea(name, moveFocus) {
  for (const key of Object.keys(AREAS)) $(key).hidden = key !== name;
  for (const link of main.querySelectorAll('.admin-nav a')) {
    if (link.hash === `#${name}`) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  document.title = `${AREAS[name].title} - ${PAGE_TITLE}`;
  if (moveFocus) $(`${name}Title`).focus();
  if (!opened.has(name)) {
    opened.add(name);
    loadArea(name);
  }
}

/* The service answered and said no (a 4xx): the session is over. No answer, or a 5xx, says nothing about it. */
function refused(error) {
  return error.status >= HTTP_CLIENT_ERROR && error.status < HTTP_SERVER_ERROR;
}

/* Whether this account is an admin. Resolves with the state to show: 'ready', 'not-admin', 'check-failed', or
   'signed-out' (with the reason in `expired` when the service refused the session). */
async function check(again) {
  const saved = storage.getItem(STORAGE_KEY) !== null;
  let session;
  try {
    const { data, error } = await client.auth.getSession(); /* renews an expired token first */
    if (error) throw error; /* offline or the service is down: not the same as signed out */
    session = data.session;
  } catch (error) {
    console.error(error);
    return 'check-failed';
  }
  /* A saved session the library dropped because the service refused to renew it expired; none saved was never there. */
  if (!session) return saved ? 'expired' : 'signed-out';
  try {
    const { data, error, status } = await client.rpc('is_admin');
    if (status === HTTP_UNAUTHORIZED) {
      /* The token was refused: renew it once and ask again, then give up and say so. */
      if (!again) return 'expired';
      const { error: renewal } = await client.auth.refreshSession();
      if (renewal) return refused(renewal) ? 'expired' : 'check-failed'; /* a failed renewal is not a refused one */
      return await check(false);
    }
    if (error) throw error;
    return data === true ? 'ready' : 'not-admin';
  } catch (error) {
    console.error(error);
    return 'check-failed';
  }
}

/* ---- Crawl: one card per company, worst first (PLAN_2.md step 2.2) ---- */

const STATUS_TEXT = { failing: 'Failing', held: 'Held', robots: 'Robots.txt', ok: 'OK' };
const RANK = { failing: 0, held: 1, robots: 2, ok: 3 };
/* The views, one pressed at a time. A company has one status, so the counts never add one twice. */
const VIEWS = [
  { id: 'attention', label: 'Needs attention', has: (status) => status !== 'ok' },
  { id: 'failing', label: 'Failing', has: (status) => status === 'failing' },
  { id: 'robots', label: 'Robots.txt', has: (status) => status === 'robots' },
  { id: 'held', label: 'Held', has: (status) => status === 'held' },
  { id: 'all', label: 'All', has: () => true },
];
const NOTE_LOG =
  'The crawl log may read short until about 20 Nov 2026, after the history moved to Supabase. Until then, streaks can read low and older days can be missing.';
const NOTE_FETCHED = '"Fetched" is the number of jobs a crawl read that day. A failed crawl fetches 0.';
const NONE = '-';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* "2026-11-18T07:12:00" (the log's own stamp; only the date is shown) as "18 Nov" in the viewer's language, with the year when it is not
   this year. Anything else is shown as it is, as the dashboard does. */
function day(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(stamp ?? '');
  if (!m) return stamp ?? NONE;
  const when = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  const year = when.getUTCFullYear() === new Date().getUTCFullYear() ? undefined : 'numeric';
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year, timeZone: 'UTC' }).format(when);
}

/* The rows of the function, each with its one status, worst first: failing by the longest streak, then held, then
   skipped for robots.txt, then the rest, each by name. */
function ranked(rows) {
  const withStatus = rows.map((row) => {
    let status = 'ok';
    if (row.held) status = 'held';
    else if (row.robots_skipped) status = 'robots';
    else if (row.failing) status = 'failing';
    return { row, status };
  });
  return withStatus.sort(
    (a, b) =>
      RANK[a.status] - RANK[b.status] ||
      (a.status === 'failing' ? b.row.streak - a.row.streak : 0) ||
      a.row.company.localeCompare(b.row.company),
  );
}

function crawlCard({ row, status }) {
  const card = el('li', 'gap-card crawl-card');
  card.dataset.company = row.company;
  card.dataset.status = status;
  const head = el('div', 'crawl-head');
  const name = el('h3', 'crawl-name', row.company);
  name.dir = 'auto';
  head.append(name, el('span', status === 'ok' ? 'pill pill-soft' : 'pill pill-warn', STATUS_TEXT[status]));
  const facts = [
    ['Last good crawl', row.last_good ? day(row.last_good) : 'None in the log'],
    ['Last result', `${day(row.last_crawl)}, ${row.last_ok ? 'OK' : 'failed'}`],
    ...(row.last_note ? [['Note', row.last_note]] : []),
    ['Failed days in a row', String(row.streak)],
    ['Open jobs now', String(row.open_jobs)],
    ['Fetched about 30 days ago', row.fetched_30d_ago === null ? NONE : String(row.fetched_30d_ago)],
  ];
  const list = el('dl', 'crawl-facts');
  for (const [term, value] of facts) {
    const group = el('div', 'crawl-fact');
    group.append(el('dt', '', term), el('dd', '', value));
    list.append(group);
  }
  card.append(head, list);
  return card;
}

/* The page of the area: headline, notes, the view chips, a status line and the list of the pressed view. Only that
   view is in the DOM, and the chips stay put, so the focus stays on the chip that was pressed. */
function crawlView(rows) {
  const companies = ranked(rows);
  const counts = Object.fromEntries(VIEWS.map((view) => [view.id, companies.filter((c) => view.has(c.status)).length]));
  const needs = counts.attention;
  const fine = companies.length - needs;
  const root = el('div', 'crawl');
  root.append(
    el('p', 'crawl-headline', `${needs === 0 ? 'None need' : plural(needs, 'needs', 'need')} attention, ${fine} fine.`),
    el('p', 'crawl-note', NOTE_FETCHED),
    el('p', 'crawl-note', NOTE_LOG),
  );
  const chips = el('div', 'chip-group');
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'Show');
  const buttons = new Map();
  for (const view of VIEWS) {
    const chip = el('button', 'chip', `${view.label} (${counts[view.id]})`);
    chip.type = 'button';
    chip.dataset.view = view.id;
    chip.addEventListener('click', () => show(view));
    buttons.set(view.id, chip);
    chips.append(chip);
  }
  const status = el('p', 'crawl-count');
  status.setAttribute('role', 'status');
  const none = el('p', 'crawl-none', 'No companies in this view.');
  const list = el('ul', 'crawl-list');
  function show(view) {
    const shown = companies.filter((c) => view.has(c.status));
    for (const [id, chip] of buttons) chip.setAttribute('aria-pressed', String(id === view.id));
    list.replaceChildren(...shown.map(crawlCard));
    list.hidden = shown.length === 0;
    none.hidden = shown.length > 0;
    status.textContent = `Showing ${shown.length} of ${plural(companies.length, 'company', 'companies')}`;
  }
  root.append(chips, status, none, list);
  show(VIEWS[0]);
  return root;
}

/* One call, one row per company, counted in the database (PostgREST stops at 1000 rows). Resolves with the rows, or
   with null when the token was refused. A null answer is no rows. */
async function crawlRows() {
  const { data, error, status } = await client.rpc('admin_crawl_health');
  if (status === HTTP_UNAUTHORIZED) return null;
  if (error) throw error;
  if (data !== null && !Array.isArray(data)) throw new TypeError('admin_crawl_health did not return a list');
  return data ?? [];
}

/* The rows of one call that may be refused with a 401 (`fetchRows` resolves with null then): run the admin check again,
   as the page does, which renews the token or ends the session, and ask once more. Resolves with the rows, or with
   undefined when the page is no longer ready and has said so. */
async function renewing(fetchRows, what) {
  let rows = await fetchRows();
  if (rows === null) {
    const result = await verdict();
    if (result !== 'ready') {
      settle(result);
      return undefined;
    }
    rows = await fetchRows();
    if (rows === null) throw new Error(`${what} was refused twice`);
  }
  return rows;
}

async function loadCrawl(box) {
  const rows = await renewing(crawlRows, 'admin_crawl_health');
  if (rows === undefined) return true; /* the page is no longer ready: nothing to say in this area */
  if (rows.length === 0) return false;
  box.removeAttribute('role'); /* the loading line was a status; the rows that replace it are not */
  box.replaceChildren(crawlView(rows));
  return true;
}

/* ---- Companies: the list and its editor (PLAN_2.md step 3.3) ----
   Every write goes through the signed-in client: row-level security and the check constraints of the table decide what
   is allowed, never this script. The page only turns a refusal into one plain sentence (CONSTRAINTS), and never shows
   the text of the database's error, which can quote a row. */

const COMPANY_COLUMNS =
  'id,name,domain,segment,crawl,careers_url,ats,slug,source,status,israel_only,verify,links,gap,excluded,updated_at';
const DISCOVER_URL = 'https://github.com/ravidorr/misra/actions/workflows/discover-companies.yml';
const JOB_ROWS_MAX = 1000;
const MAX_PURGE_COMPANIES = 3; /* common.MAX_PURGE_COMPANIES: the daily run stops when more companies wait to be purged */
/* The keys of REASONS in build_dashboard.py, with the words the visitor reads; tests/test_admin_companies.py keeps them equal. */
const GAP_REASONS = {
  crawl_failing: 'Failing in the latest crawl',
  bot_protection: 'Blocks automated access',
  no_careers_url: 'No career page found',
  site_unusable: 'Site down, parked or unreachable',
  no_openings: 'No open roles in Israel, or the board is closed',
  needs_reader: 'Roles are listed, but we cannot read them yet',
  robots_disallowed: 'robots.txt does not allow it',
  no_feed_found: 'No job feed found yet',
};
const SEGMENTS = {
  deeptech: 'Deep tech',
  multinational: 'Multinational',
  biz_software: 'Business software',
  cyber_infra: 'Cyber and infrastructure',
};
/* The job feeds a pin may name: the keys of adapters.ADAPTERS plus `browser` (run.py routes it to the browser), as the
   check `companies_ats_known` of the migration lists them; tests/test_admin_companies.py keeps all three equal. */
const PIN_ATS = [
  'greenhouse_url', 'greenhouse', 'lever', 'ashby', 'workable', 'smartrecruiters', 'comeet', 'workday', 'jazzhr',
  'bamboohr', 'oraclehcm', 'amazon', 'phenom', 'comeet_page', 'avature', 'eightfold', 'browser',
];
/* The form of the slug, only where CONTRIBUTING.md ("Adding a company") or the adapter's own text documents it. */
const PIN_HINTS = {
  comeet: '<name>/<uid>, from comeet.com/jobs/<name>/<uid>.',
  comeet_page: 'The address of the career page, starting with https://. Its job links look like /co/<place>/<uid>/<title>.',
  workday: 'tenant/wdN/site, from <tenant>.<wdN>.myworkdayjobs.com/<site>. Israel only.',
  jazzhr: 'The <slug> of <slug>.applytojob.com.',
  bamboohr: 'The <slug> of <slug>.bamboohr.com.',
  oraclehcm: 'host/site, from the public requisition search. Israel only.',
  amazon: 'ISR.',
  phenom: 'host/country/lang, from the search widget, filtered to Israel.',
  avature: 'host/portal?field=value, the Country facet of Israel, for example synopsys.avature.net/careers?2001=21374.',
  eightfold: 'host?domain=domain, the board and its domain, for example careers.micron.com?domain=micron.com. Israel only.',
  browser: 'The address of the career page, starting with https://, for example an Eightfold ...careers?location=Israel address.',
};
const PIN_ADDRESS = ['browser', 'comeet_page']; /* the slug of these is a career page address */
const PIN_SLUG_MAX = 300;
const PIN_SLUG_CHARS = /^[A-Za-z0-9._~:/?&=#%+@-]+$/;
const PIN_WORDS = {
  pair: ['pinAts', 'Choose the job feed and write its slug, or leave both empty.'],
  slug: ['pinSlug', 'The slug may hold only letters, digits and . _ ~ : / ? & = # % + @ -, with no space.'],
  long: ['pinSlug', 'The slug is too long.'],
  address: ['pinSlug', 'For this feed the slug is the career page address, starting with https://.'],
};
const COMPANY_STATES = { crawled: 'Crawled', card: 'On the card', pending: 'Not crawled yet', excluded: 'Excluded' };
const COMPANY_VIEWS = [{ id: 'all', label: 'All' }, ...Object.entries(COMPANY_STATES).map(([id, label]) => ({ id, label }))];
const EXCLUDED_BLANKS = {
  domain: null,
  segment: null,
  careers_url: null,
  ats: null,
  slug: null,
  source: null,
  status: null,
  israel_only: null,
  verify: null,
  links: null,
  gap: null,
};
const ERROR_GENERIC = 'Could not save. Try again, or reload the page.';
const ERROR_EXPIRED = 'Your sign-in expired. Reload the page, and sign in again on the dashboard if asked.';
const ERROR_DENIED = 'This account may not change the company list.';
const ERROR_CHANGED = 'This company was changed somewhere else. Reload the page and try again.';
/* One plain sentence for each check constraint and unique index of the `companies` table (supabase/migrations/
   20261130100000_companies.sql), and the field it names ('' when it names none). A test keeps the keys equal to the
   migration's, so a new check cannot ship without its sentence. Anything not here gets ERROR_GENERIC. */
const CONSTRAINTS = {
  companies_name_unique: ['name', 'A company with this name already exists.'],
  companies_domain_unique: ['domain', 'A company with this domain already exists.'],
  companies_name_min_length: ['name', 'The name needs at least 2 characters, with no space at either end.'],
  companies_domain_required: ['domain', 'A domain is required.'],
  companies_exactly_one_status: ['', 'A company is crawled, on the coverage card or excluded, and only one of the three.'],
  companies_gap_reason: ['gapReason', 'Choose why it is not crawled.'],
  companies_careers_https: ['', 'The career page address must start with https://.'],
  companies_links_https: ['', 'A career page address must start with https://.'],
  companies_ats_known: ['pinAts', 'The job feed is not one the crawler knows.'],
  companies_excluded_keys: ['', 'An exclusion holds a reason and a date, and nothing else.'],
  companies_excluded_reason: ['reason', 'Write the reason.'],
  companies_excluded_date: ['date', 'Enter the date as year-month-day, for example 2026-11-18.'],
  companies_excluded_shape: ['', 'An excluded company keeps only its name, the reason and the date.'],
  companies_excluded_limit: ['', 'The list already holds 200 excluded companies, the most it can.'],
};
/* The inputs each dialog can mark, by the field names of CONSTRAINTS. */
const FIELD_IDS = {
  editor: { name: 'cName', domain: 'cDomain', gapReason: 'cReason', pinAts: 'cPinAts', pinSlug: 'cPinSlug' },
  confirm: { name: 'xName', reason: 'xReason', date: 'xDate' },
};

/* The sentence for a refused write: { field, text }. `status` is the HTTP status of the answer. */
function explain(error, status) {
  const message = String(error?.message ?? '');
  const known = Object.keys(CONSTRAINTS).find((name) => message.includes(name));
  if (known) {
    const [field, text] = CONSTRAINTS[known];
    return { field, text };
  }
  if (status === HTTP_UNAUTHORIZED || error?.code === 'PGRST301') return { field: '', text: ERROR_EXPIRED };
  if (error?.code === '42501') return { field: '', text: ERROR_DENIED };
  return { field: '', text: ERROR_GENERIC };
}

/* Runs one query that selects the written rows back. Resolves with { rows } or { problem: { field, text } }. */
async function write(query) {
  let answer;
  try {
    answer = await query;
  } catch {
    return { problem: { field: '', text: ERROR_GENERIC } }; /* the request itself failed */
  }
  const { data, error, status } = answer;
  if (error) return { problem: explain(error, status) };
  if (!data?.length) return { problem: { field: '', text: ERROR_CHANGED } }; /* the row moved or is gone */
  return { rows: data };
}

function stateOf(row) {
  if (row.excluded) return 'excluded';
  if (row.gap) return 'card';
  return row.status === 'ok' ? 'crawled' : 'pending';
}

/* A fetched count of the jobs of one company, or null when it could not be counted. Only the key columns are read. */
async function countJobs(name, openOnly) {
  let query = client.from('jobs').select('job_id', { count: 'exact', head: true }).eq('company', name);
  if (openOnly) query = query.eq('status', 'open');
  const { count, error } = await query;
  return error || typeof count !== 'number' ? null : count;
}

/* How many excluded companies still have jobs that the daily run has not purged yet, or null (also when the answer is
   full at a thousand rows, which may hide some). */
async function waitingToPurge(excluded) {
  if (excluded.length === 0) return 0;
  const { data, error } = await client.from('jobs').select('company').in('company', excluded).limit(JOB_ROWS_MAX);
  if (error || data.length >= JOB_ROWS_MAX) return null; /* a full answer may hide more companies: say nothing */
  return new Set(data.map((job) => job.company)).size;
}

/* The list on screen. `render` and `say` are set when the area is built, and nothing opens a dialog before that. */
const board = { rows: [], view: 'all', query: '', render: null, say: null };
let focusAfter = null; /* where the focus goes when a dialog closes: { id } of a row, or { node } */

function focusBack() {
  const target = focusAfter?.id === undefined ? focusAfter?.node : document.querySelector(`.company-edit[data-id="${focusAfter.id}"]`);
  (target?.isConnected ? target : $('companiesTitle')).focus();
}

function companyFacts(row, state) {
  if (state === 'excluded') return [['Reason', row.excluded.reason], ['Added', row.excluded.date_added]];
  const facts = [['Domain', row.domain], ['Segment', SEGMENTS[row.segment] ?? row.segment ?? NONE]];
  if (state === 'card') {
    facts.push(['Reason', GAP_REASONS[row.gap.reason] ?? row.gap.reason]);
    if (row.gap.note) facts.push(['Note', row.gap.note]);
  } else {
    facts.push(['Job feed', row.ats || NONE]);
  }
  if (state === 'pending') facts.push(['Discovery status', row.status || 'Not run yet']);
  if (row.israel_only) facts.push(['Jobs kept', 'Israel only']);
  return facts;
}

function careerLink(row) {
  const href = row.links?.israel ?? row.links?.global ?? row.careers_url;
  if (typeof href !== 'string' || !href.startsWith('https://')) return null;
  const link = el('a', 'admin-link', 'Career page');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.append(el('span', 'sr-only', ' (opens in a new tab)'));
  return link;
}

function companyCard(row) {
  const state = stateOf(row);
  const card = el('li', 'gap-card crawl-card company-card');
  card.dataset.company = row.name;
  card.dataset.state = state;
  const head = el('div', 'crawl-head');
  const name = el('h3', 'crawl-name', row.name);
  name.dir = 'auto';
  head.append(name, el('span', state === 'crawled' ? 'pill pill-soft' : 'pill pill-warn', COMPANY_STATES[state]));
  const list = el('dl', 'crawl-facts');
  for (const [term, value] of companyFacts(row, state)) {
    const group = el('div', 'crawl-fact');
    const description = el('dd', '', value);
    description.dir = 'auto';
    group.append(el('dt', '', term), description);
    list.append(group);
  }
  const edit = el('button', 'btn btn-pill company-edit', 'Edit');
  edit.type = 'button';
  edit.dataset.id = String(row.id);
  edit.append(el('span', 'sr-only', ` ${row.name}`));
  edit.addEventListener('click', () => openEditor(row, { id: row.id }));
  const actions = el('div', 'company-actions');
  const link = careerLink(row);
  if (link) actions.append(link);
  actions.append(edit);
  card.append(head, list, actions);
  return card;
}

/* The page of the area: the counts, what takes effect when, the two buttons that add a record, the search, the chips,
   a status line and the list. The chips and the search stay put while the list is redrawn under them. */
function companiesView() {
  const root = el('div', 'crawl companies');
  const headline = el('p', 'crawl-headline');
  const note = el('p', 'crawl-note', 'A company you add is crawled only after the discover-companies workflow has run. Every other change takes effect at the next daily run.');
  const workflow = el('a', 'btn btn-pill', 'Open the workflow');
  workflow.href = DISCOVER_URL;
  workflow.target = '_blank';
  workflow.rel = 'noopener';
  workflow.append(el('span', 'sr-only', ' (opens in a new tab)'));
  const add = el('button', 'btn btn-pill btn-primary', 'Add a company');
  add.type = 'button';
  add.addEventListener('click', () => openEditor(null, { node: add }));
  const exclude = el('button', 'btn btn-pill', 'Exclude a company');
  exclude.type = 'button';
  exclude.addEventListener('click', () => openExclude(null, { node: exclude }));
  const buttons = el('div', 'company-toolbar');
  buttons.append(add, exclude, workflow);
  const label = el('label', 'sr-only', 'Search companies');
  label.htmlFor = 'companySearch';
  const search = el('input', 'field');
  search.id = 'companySearch';
  search.type = 'search';
  search.placeholder = 'Search by name or domain';
  search.autocomplete = 'off';
  search.addEventListener('input', () => {
    board.query = search.value;
    board.render();
  });
  const chips = el('div', 'chip-group');
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'Show');
  const chipFor = new Map();
  for (const view of COMPANY_VIEWS) {
    const chip = el('button', 'chip');
    chip.type = 'button';
    chip.dataset.view = view.id;
    chip.addEventListener('click', () => {
      board.view = view.id;
      board.render();
    });
    chipFor.set(view.id, chip);
    chips.append(chip);
  }
  const count = el('p', 'crawl-count');
  count.setAttribute('role', 'status');
  const saved = el('p', 'company-saved');
  saved.setAttribute('role', 'status');
  const lines = el('div', 'company-lines');
  lines.append(count, saved);
  const none = el('p', 'crawl-none', 'No companies match.');
  const list = el('ul', 'crawl-list');
  board.say = (...parts) => {
    saved.replaceChildren(...parts);
  };
  board.render = () => {
    const words = board.query.trim().toLowerCase();
    const everyone = [...board.rows].sort((a, b) => a.name.localeCompare(b.name));
    const shown = everyone.filter(
      (row) =>
        (board.view === 'all' || stateOf(row) === board.view) &&
        (!words || row.name.toLowerCase().includes(words) || (row.domain ?? '').toLowerCase().includes(words)),
    );
    for (const view of COMPANY_VIEWS) {
      const n = everyone.filter((row) => view.id === 'all' || stateOf(row) === view.id).length;
      const chip = chipFor.get(view.id);
      chip.textContent = `${view.label} (${n})`;
      chip.setAttribute('aria-pressed', String(view.id === board.view));
    }
    const crawled = everyone.filter((row) => stateOf(row) === 'crawled').length;
    const excluded = everyone.filter((row) => row.excluded).length;
    headline.textContent = `${plural(everyone.length, 'company', 'companies')}: ${crawled} crawled, ${excluded} excluded.`;
    list.replaceChildren(...shown.map(companyCard));
    list.hidden = shown.length === 0;
    none.hidden = shown.length > 0;
    count.textContent = `Showing ${shown.length} of ${everyone.length}`;
  };
  root.append(headline, note, buttons, label, search, chips, lines, none, list);
  board.view = 'all';
  board.query = '';
  board.render();
  return root;
}

async function companyRows() {
  /* One answer holds up to 1000 rows; the list is the few hundred companies and at most 200 exclusions. */
  const { data, error, status } = await client.from('companies').select(COMPANY_COLUMNS).order('name').range(0, 999);
  if (status === HTTP_UNAUTHORIZED) return null;
  if (error) throw error;
  return data ?? [];
}

async function loadCompanies(box) {
  const rows = await renewing(companyRows, 'the company list');
  if (rows === undefined) return true;
  if (rows.length === 0) return false;
  board.rows = rows;
  box.removeAttribute('role');
  box.replaceChildren(companiesView());
  return true;
}

/* ---- Alerts: counts of the job alerts, never a list of accounts (PLAN_2.md step 5.1) ----
   One call to `admin_alert_counts()`, which counts in the database and returns kind, day and n: the subscriptions by
   state and the distinct users emailed on each of the last 7 UTC days. The answer holds no address and no user id. */

const SUBSCRIPTION_KINDS = [
  ['confirmed', 'Confirmed'],
  ['pending', 'Waiting for confirmation'],
  ['off', 'Turned off'],
];
const NOTE_EMAILED =
  'People who were emailed that day (UTC), one count each however many jobs they got. Someone who turned the email off or deleted the account is no longer counted.';

async function alertRows() {
  const { data, error, status } = await client.rpc('admin_alert_counts');
  if (status === HTTP_UNAUTHORIZED) return null;
  if (error) throw error;
  if (!Array.isArray(data)) throw new TypeError('admin_alert_counts did not return a list');
  return data;
}

function factList(entries) {
  const list = el('dl', 'crawl-facts');
  for (const [label, value] of entries) {
    const group = el('div', 'crawl-fact');
    group.append(el('dt', '', label), el('dd', '', value));
    list.append(group);
  }
  return list;
}

function alertCard(title, entries, note) {
  const card = el('li', 'gap-card crawl-card');
  card.append(el('h3', 'crawl-name', title), factList(entries));
  if (note) card.append(el('p', 'crawl-note', note));
  return card;
}

function alertsView(rows) {
  const count = (kind) => rows.find((row) => row.kind === kind && row.day === null)?.n ?? 0;
  const days = rows.filter((row) => row.kind === 'emailed').sort((a, b) => (a.day < b.day ? 1 : -1));
  const root = el('div', 'crawl alerts');
  root.append(
    el('p', 'crawl-headline', `${count('confirmed')} confirmed, ${count('pending')} waiting, ${count('off')} off.`),
    el('ul', 'crawl-list'),
  );
  root.lastChild.append(
    alertCard('Job alerts', SUBSCRIPTION_KINDS.map(([kind, label]) => [label, String(count(kind))])),
    alertCard(
      'Emailed in the last 7 days',
      days.map((row) => [day(row.day), plural(row.n, 'person', 'people')]),
      NOTE_EMAILED,
    ),
  );
  return root;
}

async function loadAlerts(box) {
  const rows = await renewing(alertRows, 'admin_alert_counts');
  if (rows === undefined) return true;
  if (rows.length === 0) return false;
  box.removeAttribute('role');
  box.replaceChildren(alertsView(rows));
  return true;
}

/* ---- Alerts: the two forms (PLAN_2.md step 5.3) ----
   One address field each: end a person's job alerts, or delete their account. There is no list or search of accounts, and
   the page never keeps what was typed. The address goes in the body of one request to the `admin-account` function, never
   in a URL, in storage, in the console, in the Log or in the text of an answer: the answers are fixed sentences of this
   page. The field is emptied after every answer that cannot be retried by pressing the button again (and when the page
   stops being ready). Deleting asks first, in a dialog that shows the typed address and nothing else does. */

const ACCOUNT_URL = `${SUPABASE_URL}/functions/v1/admin-account`;
const ACCOUNT_TIMEOUT_MS = 20000;
const HTTP_OK = 200;
const HTTP_BAD_REQUEST = 400;
const HTTP_UNAVAILABLE = 503;
const HTTP_TOO_MANY = 429;
const HTTP_NOT_FOUND = 404;
const HTTP_CONFLICT = 409;
const DONE_TEXT = { done: 'Done.', no_account: 'No account uses that address.' };
const ACCOUNT_WORDS = {
  conflict: 'More than one account uses that address. Nothing was done.',
  nothing: 'Nothing was done. Try again.',
  maybe: 'It may not have finished. Try again.',
  address: 'Enter the full email address.',
};
/* The two forms: what each is called in the ids, and what it asks the function to do. */
const ACCOUNT_FORMS = { end: { action: 'end_alerts' }, delete: { action: 'delete_account' } };
const accountDialog = $('accountConfirm');
const account = { busy: false };

/* The access token: the session is renewed first when it expired, or by force once the function said no (a 404 is also what
   an expired session looks like). Resolves with null when there is no session. */
class TokenError extends Error {}

async function accountToken(renew) {
  let result;
  let timer;
  const silence = new Promise((_resolve, reject) => {
    timer = setTimeout(reject, CHECK_TIMEOUT_MS, new TokenError('timeout')); /* the library's own requests have no deadline */
  });
  try {
    result = await Promise.race([renew ? client.auth.refreshSession() : client.auth.getSession(), silence]);
  } catch {
    throw new TokenError('session'); /* the request is never sent after this: postAccount stops at the throw */
  } finally {
    clearTimeout(timer);
  }
  const { data, error } = result;
  if (error) throw new TokenError('session');
  return data.session?.access_token ?? null;
}

/* One request. Resolves with { status, body }; a missing session counts as a refused one. Throws when nothing answers. */
async function postAccount(address, action, renew) {
  const token = await accountToken(renew);
  if (!token) return { status: HTTP_NOT_FOUND, body: null };
  const response = await fetch(ACCOUNT_URL, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, apikey: SUPABASE_ANON_KEY, 'content-type': 'application/json' },
    body: JSON.stringify({ address, action }),
    signal: AbortSignal.timeout(ACCOUNT_TIMEOUT_MS),
  });
  const body = await response.json().catch(() => null);
  return { status: response.status, body };
}

/* What an answer means for the page: { kind: 'status' | 'problem', text, keep, done }. `keep` leaves the address in the
   field, for an answer where pressing the button again (or fixing the address) is the way on. */
function accountOutcome({ status, body }) {
  if (status === HTTP_OK && body?.ok === true && Object.hasOwn(DONE_TEXT, body.result)) {
    return { kind: 'status', text: DONE_TEXT[body.result], keep: false, done: body.result === 'done', logged: body.result === 'done' };
  }
  if (status === HTTP_CONFLICT) return { kind: 'problem', text: ACCOUNT_WORDS.conflict, keep: false, done: false, logged: false };
  if (status === HTTP_BAD_REQUEST) return { kind: 'problem', text: ACCOUNT_WORDS.address, keep: true, done: false, logged: false };
  /* Only a 503 (the admin check could not be made) and a 429 are known to have done nothing: a 500 can come after the
     log row and part of the work, a 502 after a deletion that was asked for, and a gateway timeout after all of it. */
  const none = status === HTTP_UNAVAILABLE || status === HTTP_TOO_MANY;
  return { kind: 'problem', text: none ? ACCOUNT_WORDS.nothing : ACCOUNT_WORDS.maybe, keep: true, done: false, logged: !none };
}

/* Asks the function; a 404 gets one renewed session and one more try. Resolves with an outcome, or null when the page
   is no longer ready (it has said so). */
async function sendAccount(address, action) {
  let answer;
  try {
    answer = await postAccount(address, action, false);
    if (answer.status === HTTP_NOT_FOUND) answer = await postAccount(address, action, true);
  } catch (error) {
    /* Log the kind of failure only: the error holds nothing worth keeping and nothing here may log what was typed. */
    console.error(`admin-account: ${error?.name ?? 'failed'}`);
    if (error instanceof TokenError) answer = { status: HTTP_NOT_FOUND, body: null }; /* nothing was sent: the page's check decides */
    else return { kind: 'problem', text: ACCOUNT_WORDS.maybe, keep: true, done: false, logged: true };
  }
  if (answer.status !== HTTP_NOT_FOUND) return accountOutcome(answer);
  const result = await verdict(); /* the page's own check: a session that is over, or an account that is not an admin */
  if (result !== 'ready') {
    settle(result);
    return null;
  }
  return { kind: 'problem', text: ACCOUNT_WORDS.nothing, keep: true, done: false, logged: false };
}

/* Closes the question and forgets the address it showed, at once: the `close` event comes later, and not for a close made here. */
function closeAccountDialog() {
  $('accountAddress').textContent = '';
  if (accountDialog.open) accountDialog.close();
}

function clearAddresses() {
  for (const name of Object.keys(ACCOUNT_FORMS)) {
    $(`${name}Address`).value = '';
    fieldError(`${name}Address`, '');
    sayAccount(name, null);
  }
  closeAccountDialog();
}

/* One answer in the form's own lines: a status (the two answers) or a problem, never both. */
function sayAccount(name, outcome) {
  const status = $(`${name}Status`);
  const problem = $(`${name}Problem`);
  status.textContent = outcome?.kind === 'status' ? outcome.text : '';
  problem.textContent = outcome?.kind === 'problem' ? outcome.text : '';
  problem.hidden = outcome?.kind !== 'problem';
}

function setAccountBusy(button, busy) {
  account.busy = busy;
  button.setAttribute('aria-busy', String(busy));
  if (busy) button.setAttribute('aria-disabled', 'true');
  else button.removeAttribute('aria-disabled');
}

async function runAccount(name) {
  if (account.busy) return;
  const input = $(`${name}Address`);
  const address = input.value.trim();
  const button = $(name === 'delete' ? 'accountGo' : 'endGo');
  sayAccount(name, null);
  setAccountBusy(button, true);
  let outcome;
  try {
    outcome = await sendAccount(address, ACCOUNT_FORMS[name].action);
  } finally {
    setAccountBusy(button, false);
  }
  closeAccountDialog();
  if (!outcome) {
    clearAddresses(); /* the page left its ready state */
    return;
  }
  if (!outcome.keep) input.value = '';
  sayAccount(name, outcome);
  if (outcome.logged) opened.delete('log'); /* the function may have logged the action: the Log area reads it again when next shown */
  if (outcome.done) loadArea('alerts'); /* the counts moved */
}

/* The address of a form when it is one the function could take, else the field says so and keeps the focus. */
function checkedAddress(name) {
  const input = $(`${name}Address`);
  const ok = input.value.trim() !== '' && input.validity.valid;
  fieldError(`${name}Address`, ok ? '' : ACCOUNT_WORDS.address);
  if (!ok) input.focus();
  return ok;
}

function wireAccountForms() {
  for (const name of Object.keys(ACCOUNT_FORMS)) {
    $(`${name}Address`).addEventListener('input', () => {
      fieldError(`${name}Address`, '');
      sayAccount(name, null);
    });
  }
  $('endForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!account.busy && checkedAddress('end')) runAccount('end');
  });
  $('deleteForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if (account.busy || !checkedAddress('delete')) return;
    sayAccount('delete', null);
    $('accountAddress').textContent = $('deleteAddress').value.trim(); /* text, never HTML */
    accountDialog.showModal();
    $('accountCancel').focus(); /* the safe choice first */
  });
  $('accountGo').addEventListener('click', () => runAccount('delete'));
  for (const id of ['accountClose', 'accountCancel']) $(id).addEventListener('click', () => !account.busy && closeAccountDialog());
  accountDialog.addEventListener('cancel', (event) => account.busy && event.preventDefault()); /* Esc does not abandon a request */
  accountDialog.addEventListener('close', () => {
    $('accountAddress').textContent = ''; /* the address is shown only while the question is open */
  });
}

/* ---- Log: what the admin changed, newest first (PLAN_2.md step 1.3) ----
   Read with the admin's own session (row-level security lets only a listed admin read it). The target is a company
   name or a table name, never an address. PostgREST answers 1000 rows at most; the page asks for the last 50. */

const LOG_LIMIT = 50;
const LOG_ACTIONS = {
  'company added': 'Company added',
  'company changed': 'Company changed',
  'company removed': 'Company removed',
};

async function logRows() {
  const { data, error, status } = await client
    .from('admin_log')
    .select('id,ts,action,target')
    .order('ts', { ascending: false })
    .order('id', { ascending: false })
    .limit(LOG_LIMIT);
  if (status === HTTP_UNAUTHORIZED) return null;
  if (error) throw error;
  if (!Array.isArray(data)) throw new TypeError('the admin log did not return a list');
  return data;
}

/* An action the page has no sentence for (a later step adds some) is shown as it is, with a capital letter. */
function logAction(action) {
  return LOG_ACTIONS[action] ?? `${action.charAt(0).toUpperCase()}${action.slice(1)}`;
}

function logRow(row) {
  const item = el('li', 'gap-card log-row');
  const when = el('time', 'log-time');
  const moment = new Date(row.ts);
  if (Number.isNaN(moment.getTime())) {
    when.textContent = String(row.ts);
  } else {
    when.dateTime = moment.toISOString();
    when.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(moment);
  }
  const what = el('span', 'log-what');
  what.append(el('strong', '', logAction(String(row.action))));
  if (row.target) {
    const target = el('span', 'log-target', row.target);
    target.dir = 'auto';
    what.append(': ', target);
  }
  item.append(when, what);
  return item;
}

async function loadLog(box) {
  const rows = await renewing(logRows, 'the admin log');
  if (rows === undefined) return true;
  if (rows.length === 0) return false;
  const root = el('div', 'crawl log');
  const line = rows.length >= LOG_LIMIT ? `Showing the last ${LOG_LIMIT} changes.` : plural(rows.length, 'change', 'changes') + '.';
  const list = el('ul', 'log-list');
  list.append(...rows.map(logRow));
  root.append(el('p', 'crawl-count', line), list);
  box.removeAttribute('role');
  box.replaceChildren(root);
  return true;
}

/* ---- the dialogs ---- */

const editorDialog = $('companyEditor');
const confirmDialog = $('companyConfirm');
const editor = { mode: 'add', row: null, busy: false, values: null };
const asking = { kind: '', row: null, busy: false, token: 0, waiting: null };

function setBusy(state, button, busy) {
  state.busy = busy;
  if (busy) button.setAttribute('aria-disabled', 'true');
  else button.removeAttribute('aria-disabled');
}

function fieldError(id, text) {
  const input = $(id);
  const message = $(`${id}Error`);
  message.textContent = text ?? '';
  message.hidden = !text;
  if (text) input.setAttribute('aria-invalid', 'true');
  else input.removeAttribute('aria-invalid');
}

function notice(id, text) {
  const node = $(id);
  node.textContent = text ?? '';
  node.hidden = !text;
}

/* Shows a problem in one dialog: at the field it names there, else in the dialog's notice. The focus goes to the field. */
function showProblem(scope, problem) {
  for (const field of Object.values(FIELD_IDS[scope])) fieldError(field, '');
  let id = FIELD_IDS[scope][problem.field];
  if (id && $(id).closest('[hidden]')) id = undefined; /* a field the admin cannot see cannot carry the sentence */
  notice(scope === 'editor' ? 'editorNotice' : 'confirmNotice', id ? '' : problem.text);
  if (id) {
    fieldError(id, problem.text);
    $(id).focus();
  }
}

function clearProblems(scope) {
  showProblem(scope, { field: '', text: '' });
}

function option(value, text) {
  const node = el('option', '', text);
  node.value = value;
  return node;
}

function fillSelect(select, entries, firstText) {
  select.replaceChildren(option('', firstText), ...entries.map(([value, text]) => option(value, text)));
}

function syncEditor() {
  const crawl = $('cCrawl').checked;
  $('cardFields').hidden = crawl;
  $('timingNote').hidden = !(crawl && (editor.mode !== 'edit' || !editor.row.crawl));
  $('segmentGroup').hidden = unlocking(); /* a crawled record has no segment: the write clears it */
  $('pinGroup').hidden = !(crawl && editor.mode === 'edit');
  $('pinFields').hidden = $('cUnpin').checked;
  showPinHint();
}

/* A company that is on the coverage card and is being set to crawl. */
function unlocking() {
  return editor.mode === 'edit' && !editor.row.crawl && $('cCrawl').checked;
}

function showPinHint() {
  const ats = $('cPinAts').value;
  $('cPinHint').textContent = PIN_HINTS[ats] ?? (ats ? '' : 'Choose the feed first.');
}

function openEditor(row, back) {
  focusAfter = back;
  editor.row = row;
  editor.mode = row === null ? 'add' : row.excluded ? 'include' : 'edit';
  const { mode } = editor;
  $('editorTitle').textContent = { add: 'Add a company', include: `Include ${row?.name} again`, edit: `Edit ${row?.name}` }[mode];
  $('cName').value = row?.name ?? '';
  $('cName').readOnly = mode !== 'add';
  $('cNameHint').hidden = mode === 'add';
  $('cDomain').value = row?.domain ?? '';
  $('cCrawl').checked = row?.excluded ? true : (row?.crawl ?? true);
  $('cCard').checked = !$('cCrawl').checked;
  fillSelect($('cReason'), Object.entries(GAP_REASONS), 'Choose a reason');
  $('cReason').value = row?.gap?.reason ?? '';
  $('cNote').value = row?.gap?.note ?? '';
  const segments = Object.entries(SEGMENTS);
  if (row?.segment && !(row.segment in SEGMENTS)) segments.push([row.segment, row.segment]); /* keep a value this page does not know */
  fillSelect($('cSegment'), segments, 'None');
  $('cSegment').value = row?.segment ?? '';
  $('cIsrael').checked = row?.israel_only === true;
  $('cVerify').checked = row?.verify === false;
  $('verifyRow').hidden = !(mode === 'edit' && (row.source === 'override' || row.verify === false)); /* discover.py reads it for a pinned feed only */
  $('cLinkIsrael').value = row?.links?.israel ?? '';
  $('cLinkGlobal').value = row?.links?.global ?? '';
  $('requeueRow').hidden = mode !== 'edit' || !row.crawl;
  $('cRequeue').checked = false;
  const pinned = mode === 'edit' && row.source === 'override';
  const feeds = PIN_ATS.map((ats) => [ats, ats]);
  if (pinned && row.ats && !PIN_ATS.includes(row.ats)) feeds.push([row.ats, row.ats]); /* keep a value this page does not know */
  fillSelect($('cPinAts'), feeds, 'No feed pinned');
  $('cPinAts').value = pinned ? (row.ats ?? '') : '';
  $('cPinSlug').value = pinned ? (row.slug ?? '') : '';
  $('unpinRow').hidden = !pinned;
  $('cUnpin').checked = false;
  const facts = $('discovered');
  facts.replaceChildren();
  const shown =
    mode === 'edit'
      ? [['Career page', row.careers_url], ['Job feed', row.ats], ['Feed slug', row.slug], ['Found by', row.source], ['Discovery status', row.status]]
      : mode === 'include'
        ? [['Excluded on', row.excluded.date_added], ['Reason', row.excluded.reason]]
        : [];
  for (const [term, value] of shown) {
    const group = el('div', 'crawl-fact');
    group.append(el('dt', '', term), el('dd', '', value || NONE));
    facts.append(group);
  }
  facts.hidden = shown.length === 0;
  $('editorMore').hidden = mode !== 'edit';
  $('editorSave').textContent = { add: 'Add company', include: 'Include again', edit: 'Save' }[mode];
  clearProblems('editor');
  syncEditor();
  editorDialog.showModal();
  (mode === 'add' ? $('cName') : $('editorTitle')).focus();
}

/* What the pin part of the editor asks for: { fields } to add to the record (null when nothing changes), or { problem }.
   A pin is the feed by hand: source override, the feed, its slug, and an empty status, so the discover-companies
   workflow checks it. The page never writes any other status. */
function pinChange() {
  if ($('cUnpin').checked) return { fields: { source: '', ats: '', slug: '', status: '' } };
  const ats = $('cPinAts').value;
  const slug = $('cPinSlug').value.trim();
  if (!ats && !slug) {
    if (editor.row.source !== 'override') return { fields: null };
    return { problem: { field: 'pinAts', text: 'To take the pin away, tick Remove the pin.' } }; /* an empty pair must not keep a pin silently */
  }
  let words = null;
  if (!ats || !slug) words = PIN_WORDS.pair;
  else if (slug.length > PIN_SLUG_MAX) words = PIN_WORDS.long;
  else if (!PIN_SLUG_CHARS.test(slug)) words = PIN_WORDS.slug;
  else if (PIN_ADDRESS.includes(ats) && !slug.startsWith('https://')) words = PIN_WORDS.address;
  if (words) return { problem: { field: words[0], text: words[1] } };
  const { row } = editor;
  if (row.source === 'override' && row.ats === ats && row.slug === slug) return { fields: null }; /* already pinned so */
  return { fields: { source: 'override', ats, slug, status: '' } };
}

/* The record the form describes, and the fields of it that stay as the database holds them. */
function readEditor() {
  const text = (id) => $(id).value.trim();
  const crawl = $('cCrawl').checked;
  const links = {};
  if (text('cLinkIsrael')) links.israel = text('cLinkIsrael');
  if (text('cLinkGlobal')) links.global = text('cLinkGlobal');
  const record = {
    domain: text('cDomain'),
    segment: text('cSegment') || null,
    crawl,
    israel_only: $('cIsrael').checked ? true : null,
    verify: $('verifyRow').hidden ? (editor.row?.verify ?? null) : ($('cVerify').checked ? false : null),
    links: Object.keys(links).length > 0 ? links : null,
    gap: crawl ? null : { reason: $('cReason').value, note: text('cNote') },
  };
  const { mode, row } = editor;
  /* A company that starts crawling (new, included again or moved back from the card) has no job feed yet: an empty
     status puts it in the queue of the discover-companies workflow. */
  if (mode === 'edit') {
    if (crawl && ($('cRequeue').checked || !row.crawl)) record.status = '';
    if (unlocking()) record.segment = null;
    return record;
  }
  const fresh = { ...record, careers_url: '', ats: '', slug: '', source: '', status: '' };
  return mode === 'add' ? { ...fresh, name: text('cName') } : { ...fresh, excluded: null };
}

async function saveEditor(event) {
  event.preventDefault();
  if (editor.busy) return;
  const record = readEditor();
  const { mode, row } = editor;
  if (mode === 'edit' && record.crawl) {
    const pin = pinChange();
    if (pin.problem) {
      showProblem('editor', pin.problem);
      return;
    }
    Object.assign(record, pin.fields);
  }
  if ((mode === 'edit' && row.crawl && !record.crawl) || mode === 'include') {
    editor.values = record;
    openConfirm(mode === 'include' ? 'include' : 'leave', row);
    return;
  }
  await persistEditor(record, 'editor');
}

/* Writes the editor's record (add, include again or edit) and shows what happened in `scope`. */
async function persistEditor(record, scope) {
  const { mode, row } = editor;
  const table = client.from('companies');
  const query =
    mode === 'add'
      ? table.insert(record).select(COMPANY_COLUMNS)
      : table.update(record).eq('id', row.id).eq('updated_at', row.updated_at).select(COMPANY_COLUMNS);
  setBusy(editor, $('editorSave'), true);
  const { rows, problem } = await write(query).finally(() => setBusy(editor, $('editorSave'), false));
  if (problem) {
    if (scope === 'confirm' && !FIELD_IDS.editor[problem.field]) showProblem('confirm', problem);
    else {
      if (scope === 'confirm') confirmDialog.close();
      showProblem('editor', problem);
    }
    return;
  }
  const [saved] = rows;
  settleRow(saved, row);
  if (mode === 'add') {
    board.view = 'all';
    board.query = '';
    $('companySearch').value = '';
    focusAfter = { id: saved.id };
  }
  const queued = record.status === '' && saved.crawl; /* new, included again, moved back from the card, or sent back to discovery */
  finish(queued ? 'Saved. It is crawled only after the discover-companies workflow has run. ' : 'Saved. Takes effect at the next daily run.');
}

function settleRow(saved, previous) {
  const at = previous ? board.rows.findIndex((row) => row.id === previous.id) : -1;
  if (at === -1) board.rows.push(saved);
  else board.rows[at] = saved;
}

function finish(...words) {
  opened.delete('log'); /* the database logged this change: the Log area reads it again when it is next shown */
  confirmDialog.close();
  editorDialog.close();
  board.render();
  const parts = words.map((word) => document.createTextNode(word));
  if (words.some((word) => word.includes('discover-companies'))) {
    const link = el('a', 'admin-link', 'Open the workflow');
    link.href = DISCOVER_URL;
    link.target = '_blank';
    link.rel = 'noopener';
    link.append(el('span', 'sr-only', ' (opens in a new tab)'));
    parts.push(link);
  }
  board.say(...parts);
  focusBack();
}

const DIGITS = 2;
function today() {
  const now = new Date();
  const part = (n) => String(n).padStart(DIGITS, '0');
  return `${now.getFullYear()}-${part(now.getMonth() + 1)}-${part(now.getDate())}`;
}

/* One confirmation dialog for the three actions that need one: leaving the crawl ('leave'), removing a company from
   the list ('remove') and excluding one ('exclude', of a listed company, or of a name that is not in the list when
   `row` is null). It counts the company's jobs and shows the number; a count that fails says so and the action stays. */
function openConfirm(kind, row) {
  asking.kind = kind;
  asking.row = row;
  asking.waiting = null;
  const name = row?.name;
  const copy = {
    leave: {
      title: `Take ${name} off the crawl?`,
      text: 'It moves to the coverage card and nothing crawls it any more. At the next daily run its open jobs are closed. The rows stay as history.',
      go: 'Move to the card',
    },
    include: {
      title: `Include ${name} again?`,
      text: 'This erases the exclusion: its reason and date are gone, and the company is crawled again. Do this only when the company has asked to be included again.',
      go: 'Include again',
    },
    remove: {
      title: `Remove ${name} from the list?`,
      text: 'The company is deleted from the list. After that nothing crawls it and no run closes its open jobs: they stay in the history. To close them first, move the company to the coverage card.',
      go: 'Remove',
    },
    exclude: {
      title: row ? `Exclude ${name}?` : 'Exclude a company',
      text: 'The record keeps only the name, the reason and the date. At the next daily run every job of the company, open or closed, and its crawl log rows are deleted, and it is never crawled again. This page never deletes the record, and including the company again is a separate step that asks first.',
      go: 'Exclude',
    },
  }[kind];
  $('confirmTitle').textContent = copy.title;
  $('confirmText').replaceChildren(el('p', '', copy.text));
  $('confirmGo').textContent = copy.go;
  $('confirmFields').hidden = kind !== 'exclude';
  $('xNameGroup').hidden = row !== null;
  $('xName').value = row?.name ?? '';
  $('xReason').value = '';
  $('xDate').value = today();
  clearProblems('confirm');
  confirmDialog.showModal();
  $('confirmCancel').focus();
  recount();
}

async function recount() {
  const { kind, row } = asking;
  const name = row?.name ?? $('xName').value.trim();
  const line = $('confirmCount');
  const token = ++asking.token;
  if (!name || kind === 'include') {
    line.textContent = '';
    return;
  }
  line.textContent = 'Counting jobs…';
  let n = null;
  let others = null;
  try {
    [n, others] = await Promise.all([
      countJobs(name, kind !== 'exclude'),
      kind === 'exclude' ? waitingToPurge(board.rows.filter((r) => r.excluded && r.name !== name).map((r) => r.name)) : 0,
    ]);
  } catch {
    /* counted nothing: n stays null */
  }
  if (token !== asking.token) return; /* a newer count is on its way */
  if (n === null) {
    line.textContent = 'Could not count the jobs. You can still continue.';
    return;
  }
  const lines = {
    include: '',
    leave: `${plural(n, 'open job', 'open jobs')} will be closed at the next daily run.`,
    remove: `${plural(n, 'open job', 'open jobs')} stay${n === 1 ? 's' : ''} in the history.`,
    exclude: `${plural(n, 'job', 'jobs')} will be deleted at the next daily run.`,
  };
  let text = lines[kind];
  if (kind === 'exclude' && others !== null) {
    const waiting = others + (n > 0 ? 1 : 0);
    text += ` ${plural(waiting, 'company waits', 'companies wait')} to be purged at that run.`;
    if (waiting > MAX_PURGE_COMPANIES) text += ` The run stops when more than ${MAX_PURGE_COMPANIES} wait: exclude the rest on another day.`;
  }
  line.textContent = text;
}

async function runConfirmed(event) {
  event.preventDefault();
  if (asking.busy) return;
  const { kind, row } = asking;
  if (kind === 'leave' || kind === 'include') {
    setBusy(asking, $('confirmGo'), true);
    await persistEditor(editor.values, 'confirm').finally(() => setBusy(asking, $('confirmGo'), false));
    return;
  }
  const table = client.from('companies');
  let query;
  if (kind === 'remove') {
    query = table.delete().eq('id', row.id).eq('updated_at', row.updated_at).select('id');
  } else {
    const excluded = { reason: $('xReason').value.trim(), date_added: $('xDate').value };
    query = row
      ? table.update({ ...EXCLUDED_BLANKS, crawl: false, excluded }).eq('id', row.id).eq('updated_at', row.updated_at).select(COMPANY_COLUMNS)
      : table.insert({ name: $('xName').value.trim(), crawl: false, excluded }).select(COMPANY_COLUMNS);
  }
  setBusy(asking, $('confirmGo'), true);
  const { rows, problem } = await write(query).finally(() => setBusy(asking, $('confirmGo'), false));
  if (problem) {
    showProblem('confirm', problem);
    return;
  }
  if (kind === 'remove') board.rows = board.rows.filter((r) => r.id !== row.id);
  else settleRow(rows[0], row);
  const done = { remove: 'Removed from the list.', exclude: 'Excluded. Its jobs are deleted at the next daily run.' }[kind];
  finish(done);
}

function openExclude(row, back) {
  focusAfter = back;
  openConfirm('exclude', row);
}

/* Wires the dialogs once. A dialog that is busy ignores Esc and its buttons, as the delete dialog of the dashboard does. */
function wireCompanyDialogs() {
  $('editorForm').addEventListener('submit', saveEditor);
  $('confirmForm').addEventListener('submit', runConfirmed);
  for (const radio of document.querySelectorAll('input[name="cState"]')) radio.addEventListener('change', syncEditor);
  $('xName').addEventListener('change', recount);
  $('cPinAts').addEventListener('change', showPinHint);
  $('cUnpin').addEventListener('change', syncEditor);
  const closes = [
    [editorDialog, editor, ['editorClose', 'editorCancel']],
    [confirmDialog, asking, ['confirmClose', 'confirmCancel']],
  ];
  for (const [dialog, state, ids] of closes) {
    for (const id of ids) $(id).addEventListener('click', () => !state.busy && dialog.close());
    dialog.addEventListener('cancel', (event) => state.busy && event.preventDefault());
    dialog.addEventListener('close', () => {
      if (dialog === confirmDialog) asking.token += 1; /* a count still on its way writes nothing */
      if (!editorDialog.open && !confirmDialog.open) focusBack();
    });
  }
  $('editorExclude').addEventListener('click', () => {
    if (editor.busy) return;
    const { row } = editor;
    editorDialog.close();
    openExclude(row, { id: row.id });
  });
  $('editorRemove').addEventListener('click', () => {
    if (editor.busy) return;
    const { row } = editor;
    editorDialog.close();
    openConfirm('remove', row);
  });
}

/* The answer of the check, or 'check-failed' when it takes longer than the page waits. */
async function verdict() {
  const silence = new Promise((resolve) => {
    setTimeout(resolve, CHECK_TIMEOUT_MS, 'check-failed');
  });
  return Promise.race([check(true), silence]);
}

/* Shows what the check said. A session the service refuses is cleared, no use to the dashboard either. */
function settle(result) {
  if (result !== 'expired') {
    setState(result);
    return;
  }
  storage.removeItem(STORAGE_KEY);
  setState('signed-out');
  $('adminExpired').textContent = MESSAGES.expired; /* after the line is shown, so a screen reader announces it */
}

async function start() {
  if (typeof globalThis.supabase?.createClient !== 'function') {
    setState('check-failed');
    return;
  }
  client = globalThis.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { flowType: 'pkce', storage, storageKey: STORAGE_KEY, detectSessionInUrl: false },
  });
  /* Signing out here or on the dashboard (another tab) ends the admin view; nothing else the library reports matters. */
  client.auth.onAuthStateChange((event) => {
    if (event === 'SIGNED_OUT') setState('signed-out');
  });
  $('adminSignOut').addEventListener('click', async () => {
    try {
      const { error } = await client.auth.signOut();
      if (error) throw error;
    } catch (error) {
      console.error(error);
      /* The library gives up before clearing the session when the token cannot be renewed: clear it here, so the page
         never says "signed out" while the saved session could sign the browser in. */
      storage.removeItem(STORAGE_KEY);
    }
    setState('signed-out');
  });
  globalThis.addEventListener('hashchange', () => {
    /* Only a link to an area changes the area: the skip link ("#main") leaves the page where it is. */
    if (main.dataset.state === 'ready' && isArea(location.hash.slice(1))) showArea(areaName(), true);
  });
  settle(await verdict());
}

wireCompanyDialogs();
wireAccountForms();
await start();
