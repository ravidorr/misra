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
/* The page title of each area, and what loads it. A loader resolves to true when it showed something. Only the Crawl
   area asks the database for anything yet; the others are empty until their steps land. */
const AREAS = {
  crawl: { title: 'Crawl', load: loadCrawl },
  companies: { title: 'Companies', load: async () => false },
  alerts: { title: 'Alerts', load: async () => false },
  log: { title: 'Log', load: async () => false },
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

/* "2026-11-18T07:12:00" (the log's own stamp, UTC) as "18 Nov" in the viewer's language, with the year when it is not
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

async function loadCrawl(box) {
  let rows = await crawlRows();
  if (rows === null) {
    /* A 401: run the admin check again, as the page does, which renews the token or ends the session. */
    const result = await verdict();
    if (result !== 'ready') {
      settle(result);
      return true; /* the page is no longer ready: nothing to say in this area */
    }
    rows = await crawlRows();
    if (rows === null) throw new Error('admin_crawl_health was refused twice');
  }
  if (rows.length === 0) return false;
  box.replaceChildren(crawlView(rows));
  return true;
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

await start();
