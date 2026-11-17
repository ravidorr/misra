/* Dashboard script. Loaded as a deferred module by docs/index.html; the roles come from Supabase. */
const $ = (id) => document.getElementById(id);
const LS_D = 'il-jobs-discarded-v1';
const LS_S = 'il-jobs-starred-v1';
const LS_Q = 'misra-pending-choices-v1';
const LS_F = 'il-jobs-filters-v3';
const LS_PF = 'misra-pending-filters-v1';
const LS_I = 'misra-imported-v1';
const LS_T = 'misra-theme';
const OTHER = 'Other';
const REMOTE_REGION = 'Remote';
const REMOTE_NOT_SAID = 'Remote, country not given';
const MODES = ['auto', 'light', 'dark'];
const SIZES = [10, 25, 50, 100];
const ANNOUNCE_DELAY_MS = 600;

function lsGet(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function lsSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Storage is blocked: the choice lasts until the page closes. */
  }
}

/* Theme: auto follows the system; light and dark are forced with data-theme. */
function themeLabel(mode) {
  const button = $('theme');
  button.setAttribute('aria-label', `Theme: ${mode}`);
  button.title = `Theme: ${mode}`;
}

function currentMode() {
  return document.documentElement.getAttribute('data-theme') ?? 'auto';
}

function setTheme(mode) {
  globalThis.misraTheme(mode);
  try {
    if (mode === 'auto') localStorage.removeItem(LS_T);
    else localStorage.setItem(LS_T, mode);
  } catch {
    /* Storage is blocked: the theme lasts until the page closes. */
  }
  themeLabel(mode);
}

themeLabel(currentMode());
$('theme').addEventListener('click', () => {
  setTheme(MODES[(MODES.indexOf(currentMode()) + 1) % MODES.length]);
});

/* The backend: Supabase over PostgREST, read with the public anon key (row-level security lets it read open jobs only).
   build_dashboard.py injects both values, the same ones it puts in the page's connect-src. */
const SUPABASE_URL = '__SUPABASE_URL__';
const SUPABASE_ANON_KEY = '__SUPABASE_ANON_KEY__';
const FETCH_TIMEOUT_MS = 20000;
const REST_PAGE = 1000; /* PostgREST answers at most 1,000 rows at a time */
const JOB_COLUMNS = 'company,job_id,title,team,location,url,first_seen,level,role_types,regions,locations,snippet,snippet_cut';

async function fetchJson(url, headers = {}) {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`${new URL(url, location.href).pathname}: HTTP ${response.status}`);
  return { body: await response.json(), range: response.headers.get('Content-Range') };
}

/* All open jobs, REST_PAGE rows per request, in a stable order. The first answer carries the total in Content-Range
   ("0-999/6773"); the rest are fetched together. A count that does not match the total is an error, never a short list. */
async function fetchJobs() {
  const headers = { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` };
  const url = (offset) =>
    `${SUPABASE_URL}/rest/v1/jobs?select=${JOB_COLUMNS}&status=eq.open&order=company,job_id&limit=${REST_PAGE}&offset=${offset}`;
  const first = await fetchJson(url(0), { ...headers, Prefer: 'count=exact' });
  const total = Number(first.range?.split('/')[1]);
  if (!Number.isInteger(total)) throw new Error('jobs: no total in Content-Range');
  if (total === 0) throw new Error('jobs: the backend returned no open roles'); /* a policy or sync fault, not an empty market */
  const offsets = Array.from({ length: Math.ceil(total / REST_PAGE) - 1 }, (_, n) => (n + 1) * REST_PAGE);
  const rest = await Promise.all(offsets.map((offset) => fetchJson(url(offset), headers)));
  const rows = [first, ...rest].flatMap((answer) => answer.body);
  if (rows.length !== total) throw new Error(`jobs: got ${rows.length} of ${total}`);
  return rows;
}

/* A remote role whose location names no country or city ("Remote"): the posting does not say whether it is open to
   people in Israel, so it is not Remote by default (FR-3). "Remote, US" names a country and never matches. */
const REMOTE_WORDS = /remote|work from home|wfh|fully|flexible|distributed|virtual/gi;
function remoteNotSaid(location) {
  return /remote|work from home|wfh/i.test(location) && location.replace(REMOTE_WORDS, '').replace(/[^\p{L}]/gu, '') === '';
}

/* A row of the jobs table as the page uses it: c company, i id, t title, m team, l location as written, u url,
   f first seen, lv level, rt role types, rg regions, lo places in Israel without a region, il in Israel, ru remote
   with no country given, n not on the company's first day of listings, s company's crawl is failing, sn the snippet:
   the start of the posting as written, up to 255 characters ('' when the job has no text), sc whether
   that snippet is shorter than the full text (the stored flag snippet_cut; false when it is null). */
function toJob(row, failing, firstDay) {
  /* A row without the classification columns is a backend fault (sync lag, a missing migration), not a role that is
     "Not specified" and "Other": fail loudly instead of showing every role as unclassified. */
  const listed = [row.role_types, row.regions, row.locations].every(Array.isArray);
  if (!listed || typeof row.level !== 'string') throw new Error(`jobs: ${row.company}|${row.job_id} has no level, role types, regions or locations`);
  const f = (row.first_seen ?? '').slice(0, 10);
  const l = (row.location ?? '').trim();
  const rg = row.regions;
  const lo = row.locations;
  const il = rg.length + lo.length > 0;
  return {
    c: row.company,
    i: row.job_id,
    t: (row.title ?? '').trim(),
    m: (row.team ?? '').trim(),
    l,
    u: row.url,
    f,
    lv: row.level,
    rt: row.role_types.length > 0 ? row.role_types : [OTHER],
    rg,
    lo,
    il,
    ru: !il && remoteNotSaid(l),
    n: f !== '' && f > (firstDay[row.company] ?? f),
    s: failing.has(row.company),
    sn: typeof row.snippet === 'string' ? row.snippet.trim() : '',
    sc: row.snippet_cut === true,
  };
}

function showLoadError(error) {
  console.error(error);
  $('summary').textContent = 'Could not load the roles.';
  const retry = h('button', { type: 'button', class: 'btn btn-pill', id: 'retry', text: 'Try again' });
  retry.addEventListener('click', () => location.reload());
  $('summary').after(
    h('section', { class: 'notice load-error', id: 'load-error', role: 'alert' }, [
      h('h2', { text: 'The roles could not be loaded' }),
      h('p', {
        text: 'The service that holds the roles did not give a usable answer. Check your connection and try again. If it keeps failing, the service may be down.',
      }),
      retry,
    ]),
  );
}

async function loadData() {
  try {
    const meta = JSON.parse($('page-data').textContent);
    const rows = await fetchJobs();
    if (![meta.levels, meta.roleTypes, meta.regions].every(Array.isArray)) throw new Error('page data: no filter names');
    const failing = new Set(meta.gaps.blocked.map((gap) => gap.c));
    return {
      jobs: rows.map((row) => toJob(row, failing, meta.firstDay)),
      gaps: meta.gaps,
      generated: meta.generated,
      names: { levels: meta.levels, roleTypes: meta.roleTypes, regions: meta.regions },
    };
  } catch (error) {
    showLoadError(error);
    throw error;
  }
}

const DATA = await loadData();
const JOBS = DATA.jobs;
let page = 0;
let ready = false;
let announceTimer;
let announcedQuery = ''; /* the search the live region last spoke for */
let undoTimer; /* "Reset" can be undone for a few seconds (see hideUndo) */
let undoFilters = null;
let view = 'roles';
/* Signed out, the choices live in this browser: `gone` maps a job key to the day it was discarded, `starred` maps it to
   what the page needs to show it later (company, id, title, location, link). Signed in, they live in the account (see
   "Stars and discards" below) and these two are left alone. */
const gone = lsGet(LS_D, {});
const starred = Object.fromEntries(
  Object.entries(lsGet(LS_S, {})).filter(([, v]) => typeof v?.c === 'string' && typeof v.i === 'string'),
);

/* The values each filter offers. The build supplies the names in display order (classify.py and regions.py); a value
   that only the data has (a role type added later) is appended, so a new type needs no change here. */
const withExtras = (names, found) => [...new Set([...names, ...found])];
const LEVELS = withExtras(DATA.names.levels, JOBS.map((j) => j.lv));
const ROLE_TYPES = withExtras(DATA.names.roleTypes, JOBS.flatMap((j) => j.rt));
const REGIONS = withExtras(DATA.names.regions, JOBS.flatMap((j) => j.rg));
/* "Abroad" is a Region chip of the page only: the backend has no such region (a job outside Israel has none), so it is
   never in `DATA.names`, in the digest filters or in the assistant endpoint. */
const ABROAD = 'Abroad';
const CHOICES = { levels: LEVELS, types: ROLE_TYPES, regions: [...REGIONS, ABROAD] };

const defaults = () => ({
  q: '',
  company: '',
  sort: 'new',
  levels: [],
  types: [],
  regions: [],
  remote: false,
  onlyNew: false,
  showGone: false,
  hideStale: false,
  size: 10,
});

/* A filters object (saved in this browser or read from the account), made safe: every field the right type, minus values
   the data no longer has (a stale choice would hide jobs with no way to see why). */
function cleanFilters(saved) {
  const base = defaults();
  /* Only the keys of today's filters are kept: the removed "Location" and "Places" (an older save) are dropped here, so
     they are never written back. A saved "All locations" shows the Israel default, and a saved place no longer narrows the
     list (the chosen regions, if any, still do): no saved value can hide every role. */
  const given = saved && typeof saved === 'object' ? saved : {};
  const f = { ...base, ...Object.fromEntries(Object.keys(base).filter((name) => name in given).map((name) => [name, given[name]])) };
  for (const [name, known] of Object.entries(CHOICES)) {
    f[name] = Array.isArray(f[name]) ? f[name].filter((v) => known.includes(v)) : [];
  }
  for (const name of ['q', 'company', 'sort']) if (typeof f[name] !== 'string') f[name] = base[name];
  for (const name of ['remote', 'onlyNew', 'showGone', 'hideStale']) f[name] = f[name] === true;
  if (!['new', 'co', 'title'].includes(f.sort)) f.sort = base.sort;
  if (!SIZES.includes(Number(f.size))) f.size = base.size;
  if (f.company !== '' && !JOBS.some((j) => j.c === f.company)) f.company = '';
  return f;
}
/* The filters this browser keeps for itself (signed out; signed in they are left alone, like the browser's stars). */
const savedFilters = () => cleanFilters(lsGet(LS_F, {}));
let F = savedFilters();

function h(tag, attrs = {}, kids = []) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === 'text') el.textContent = value;
    else el.setAttribute(name, value);
  }
  for (const kid of kids) {
    if (kid) el.append(kid);
  }
  return el;
}

function safeUrl(url) {
  try {
    const parsed = new URL(url, location.href);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : '#';
  } catch {
    return '#';
  }
}

/* ---- Stars and discards ----
   A job is starred, discarded or neither: one choice, so it is never both. Signed out, the choice is saved in this
   browser (`gone`, `starred`). Signed in (auth.js publishes the account as `globalThis.misraAccount` and fires
   `misra:account`), it is saved in the user's `user_jobs` rows, and every other device that signs in reads it from there.

   The account keeps two maps. `server` is what the service last confirmed. `pending` is what the user did that the
   service has not confirmed yet; it is also saved in this browser (LS_Q, by user id), so closing the tab or losing the
   network never loses a choice. What the page shows is `pending` laid over `server`. A save that fails because the
   network or the service is down stays pending and is tried again (on a timer, when the browser comes back online, or
   from "Try again"); a save the database refuses (the row limit) is dropped and the page says so, so the page never
   shows a choice as saved when it is not. */
const MAX_TEXT = 500;
const MAX_URL = 2000;
const LIMIT_CODE = '54000'; /* the migration's per-user row limit: program_limit_exceeded */
const RETRY_MS = [2000, 5000, 15000, 60000];
const MAX_CHOICES = 2000; /* the per-user row limit of the migration */
const IMPORT_CHUNK = 200; /* rows per request when a browser's choices are brought over */
const FILTER_SAVE_MS = 800; /* a search changes with every key: save once typing pauses */
const MAX_QUERY = 200;
const MAX_FILTERS_BYTES = 15000; /* the table accepts 16384 */
const HTTP_UNAUTHORIZED = 401;
const HTTP_TIMEOUT = 408;
const HTTP_TOO_MANY = 429;
const HTTP_SERVER_ERROR = 500;
const SYNC_MESSAGES = {
  limit: 'Your account has reached the limit of stars and discards it can keep, so the last choice was not saved. Restore a discarded role or remove a star, then try again.',
  refused: 'The service did not accept the last star or discard, so it was not saved.',
  load: 'Your saved stars and discards could not be loaded, so only the changes made on this device show.',
  filtersLoad: 'Your saved filters could not be loaded, so this device uses its own filters for now.',
  filtersLater: 'Your filter change is kept on this device and saved to your account when the service answers.',
  filtersRefused: 'The service did not accept your filters, so they were not saved to your account.',
  filtersRestored: 'Your filters were restored from your account.',
};

const fates = new WeakMap(); /* what became of each change sent: 'saved', 'limit' or 'refused' (none yet: still pending) */
let account = null; /* the signed-in user's choices, or null */
let leftBehind = 0; /* changes still pending when the user signed out */

const key = (j) => `${j.c}|${j.i}`;
const JOB_BY_KEY = new Map(JOBS.map((j) => [key(j), j]));

/* The link as the database accepts it: http or https, not too long, else none. */
function storableUrl(url) {
  const safe = safeUrl(url);
  return safe !== '#' && safe.length <= MAX_URL ? safe : null;
}

/* What is kept of a job so a star outlives the job: company, id, title, location and link. */
const snapshotOf = (item) => ({
  c: item.c,
  i: item.i,
  t: (item.t ?? '').slice(0, MAX_TEXT),
  l: (item.l ?? '').slice(0, MAX_TEXT),
  u: item.u ? storableUrl(item.u) : null,
});

const entryOfRow = (row) => ({ c: row.company, i: row.job_id, state: row.state, t: row.title ?? '', l: row.location ?? '', u: row.url });
const validEntry = (e) =>
  typeof e?.c === 'string' && typeof e.i === 'string' && (e.state === 'star' || e.state === 'discard' || e.state === null);

function choiceOf(k) {
  if (account) return (account.pending.has(k) ? account.pending.get(k) : account.server.get(k))?.state ?? null;
  if (gone[k]) return 'discard';
  return starred[k] ? 'star' : null;
}
const isDiscarded = (k) => choiceOf(k) === 'discard';
const isStarred = (k) => choiceOf(k) === 'star';

/* Every starred job, with the open ones as jobs and the closed ones as what was kept (`closed: true`). */
function starredItems() {
  const entries = account
    ? [...new Set([...account.server.keys(), ...account.pending.keys()])]
        .filter(isStarred)
        .map((k) => account.pending.get(k) ?? account.server.get(k))
    : Object.entries(starred)
        .filter(([k]) => !gone[k])
        .map(([, entry]) => entry);
  return entries
    .map((entry) => JOB_BY_KEY.get(key(entry)) ?? { ...entry, t: entry.t ?? '', l: entry.l ?? '', closed: true })
    .sort((a, b) => a.c.localeCompare(b.c) || a.t.localeCompare(b.t));
}
const discardedCount = () => JOBS.filter((j) => isDiscarded(key(j))).length;

function loadPending(userId) {
  const saved = lsGet(LS_Q, {})[userId];
  const entries = Object.entries(saved && typeof saved === 'object' ? saved : {});
  return new Map(entries.filter(([, entry]) => validEntry(entry)));
}

/* Saves one change (or, with op null, forgets one) in this browser. It reads what is stored first and touches only that
   job, and forgets a change only if it is the very one the service confirmed (each has an id), so another tab of the
   same account keeps the changes it has not sent yet, also newer ones to the same job. */
function savePendingBatch(a, sets, forgets) {
  const all = lsGet(LS_Q, {});
  const mine = all[a.userId] && typeof all[a.userId] === 'object' ? all[a.userId] : {};
  for (const [k, op] of sets) mine[k] = op;
  for (const [k, acknowledged] of forgets) {
    if (mine[k]?.id === acknowledged?.id) Reflect.deleteProperty(mine, k); /* not a newer change of another tab */
  }
  if (Object.keys(mine).length > 0) all[a.userId] = mine;
  else Reflect.deleteProperty(all, a.userId);
  lsSet(LS_Q, all);
}
const savePending = (a, k, op, acknowledged) => savePendingBatch(a, op ? [[k, op]] : [], op ? [] : [[k, acknowledged]]);

/* A failure worth trying again: no answer, a service error, an expired token (it renews), a timeout or a rate limit.
   Anything else (the row limit, a refused row) will not change by waiting. */
function isTransient(error, status) {
  if (error.code === LIMIT_CODE) return false;
  return !status || status >= HTTP_SERVER_ERROR || [HTTP_UNAUTHORIZED, HTTP_TIMEOUT, HTTP_TOO_MANY].includes(status);
}

/* The row for one pending change. Discards keep no title, location or link: only a star needs them later. */
function rowOf(a, op) {
  const row = { user_id: a.userId, company: op.c, job_id: op.i, state: op.state, title: null, location: null, url: null };
  if (op.state === 'star') Object.assign(row, { title: op.t || null, location: op.l || null, url: op.u });
  return row;
}

/* One request for one pending change, or, for choices brought over from this browser (`keep`), one request for a chunk of
   them that never replaces a row the account already has: a choice made on another device meanwhile wins. The library's
   own retries are off (here and in loadServer): the page keeps the change and tries again itself, and says so at once,
   instead of looking stuck for several seconds. */
async function send(a, batch) {
  const table = a.client.from('user_jobs');
  const onConflict = 'user_id,company,job_id';
  try {
    const op = batch[0][1];
    if (op.keep) {
      return await table.upsert(batch.map(([, o]) => rowOf(a, o)), { onConflict, ignoreDuplicates: true }).retry(false);
    }
    if (op.state === null) {
      return await table.delete().eq('user_id', a.userId).eq('company', op.c).eq('job_id', op.i).retry(false);
    }
    return await table.upsert(rowOf(a, op), { onConflict }).retry(false);
  } catch (error) {
    console.error(batch[0][0], error);
    return { error: { code: '', message: String(error) }, status: 0 };
  }
}

/* The next change to send: one, or the run of brought-over choices at the head of the queue (a chunk). */
function nextBatch(a) {
  const entries = a.pending.entries();
  const first = entries.next().value;
  const batch = [first];
  if (first[1].keep) {
    for (const entry of entries) {
      if (!entry[1].keep || batch.length >= IMPORT_CHUNK) break;
      batch.push(entry);
    }
  }
  return batch;
}

/* Sends the pending changes oldest first (a chunk of brought-over choices at a time) until none is left or one cannot be
   sent now. */
async function drain(a) {
  let brought = false;
  while (a.pending.size > 0) {
    const batch = nextBatch(a);
    const { error, status } = await send(a, batch);
    if (account !== a) return false; /* signed out meanwhile: what is left stays saved in this browser */
    if (error) {
      console.error(batch[0][0], error);
      if (isTransient(error, status)) {
        a.failure = 'later';
        scheduleRetry(a);
        return false;
      }
      /* The notice stays until the user acts again: a later change that is saved must not wipe it. */
      a.failure = error.code === LIMIT_CODE ? 'limit' : 'refused';
    }
    const forgets = [];
    for (const [k, op] of batch) {
      fates.set(op, error ? a.failure : 'saved');
      if (!error) {
        a.retries = 0;
        if (op.keep) brought = true; /* the service may have kept a row it already had: the list is read again below */
        else {
          if (op.state === null) a.server.delete(k);
          else a.server.set(k, op);
          a.touched.add(k);
        }
      }
      if (a.pending.get(k) === op) {
        a.pending.delete(k); /* a newer change to the same job stays */
        forgets.push([k, op]);
      }
    }
    savePendingBatch(a, [], forgets);
    if (error) render(); /* the refused choice is gone from the view too */
  }
  if (a.failure === 'later') a.failure = null;
  if (brought) await loadServer(a);
  return true;
}

function flush() {
  const a = account;
  if (!a) return Promise.resolve(false);
  clearTimeout(a.retryTimer);
  a.flushing ??= drain(a).finally(() => {
    a.flushing = null;
    renderSyncNote();
  });
  return a.flushing;
}

function scheduleRetry(a) {
  a.retryTimer = setTimeout(flush, RETRY_MS[Math.min(a.retries, RETRY_MS.length - 1)]);
  a.retries += 1;
}

/* The account's rows from the service, all pages. The count must match, so a short list is an error, not "no stars". */
async function loadServer(a) {
  a.touched = new Set();
  a.choicesState = 'loading';
  try {
    const rows = [];
    for (;;) {
      const { data, count, error } = await a.client
        .from('user_jobs')
        .select('company,job_id,state,title,location,url', { count: 'exact' })
        .order('company')
        .order('job_id')
        .range(rows.length, rows.length + REST_PAGE - 1)
        .retry(false);
      if (error) throw error;
      if (!Number.isInteger(count)) throw new Error('user_jobs: no total in Content-Range');
      rows.push(...data);
      if (rows.length >= count) break;
      if (data.length === 0) throw new Error(`user_jobs: got ${rows.length} of ${count}`);
    }
    if (account !== a) return;
    const fresh = new Map(rows.map((row) => [`${row.company}|${row.job_id}`, entryOfRow(row)]));
    for (const k of a.touched) {
      /* confirmed while this list was on its way: the list may predate it */
      if (a.server.has(k)) fresh.set(k, a.server.get(k));
      else fresh.delete(k);
    }
    a.server = fresh;
    a.loadFailed = false;
    a.choicesState = 'loaded';
    $('list').dataset.choices = 'loaded';
  } catch (error) {
    console.error(error);
    if (account !== a) return;
    a.loadFailed = true;
    a.choicesState = 'failed';
    $('list').dataset.choices = 'failed';
  }
  render();
  renderSyncNote();
  settle(a);
}

/* The one way a choice changes: item is a job, or what was kept of a closed starred one; state is 'star', 'discard' or
   null (neither). Resolves with `saved` (true: kept in this browser or in the account) and, when not, `failure`:
   'limit' or 'refused' (the database refused it, it is undone) or 'later' (still pending, tried again). */
function setChoice(item, state) {
  const k = key(item);
  if (!account) {
    Reflect.deleteProperty(gone, k);
    Reflect.deleteProperty(starred, k);
    if (state === 'discard') gone[k] = TODAY;
    if (state === 'star') starred[k] = snapshotOf(item);
    lsSet(LS_D, gone);
    lsSet(LS_S, starred);
    return Promise.resolve({ saved: true, failure: null });
  }
  const a = account;
  if (a.failure === 'limit' || a.failure === 'refused') a.failure = null;
  const op = { ...snapshotOf(item), state, id: `${Date.now()}-${Math.random()}` };
  a.pending.set(k, op);
  savePending(a, k, op);
  return flush().then(() => {
    const fate = fates.get(op);
    return { saved: fate === 'saved', failure: fate === 'saved' ? null : (fate ?? 'later') };
  });
}

function attach(next) {
  const a = {
    client: next.client,
    userId: next.userId,
    server: new Map(),
    pending: loadPending(next.userId),
    touched: new Set(),
    failure: null,
    loadFailed: false,
    choicesState: 'loading',
    flushing: null,
    retryTimer: 0,
    retries: 0,
    decided: Boolean(lsGet(LS_I, {})[next.userId]),
    offer: null,
    result: '',
    prefs: {
      state: 'loading',
      remote: null,
      pending: loadPendingFilters(next.userId),
      paused: false,
      restored: false,
      failure: null,
      flushing: null,
      timer: 0,
      retries: 0,
    },
  };
  account = a;
  leftBehind = 0;
  $('list').dataset.choices = 'loading';
  if (a.prefs.pending) F = cleanFilters(a.prefs.pending); /* the last change made here, not yet saved */
  renderAccount();
  sync();
  render();
  loadServer(a);
  loadPrefs(a);
  flush();
}

function detach() {
  clearTimeout(account.retryTimer);
  clearTimeout(account.prefs.timer);
  leftBehind = account.pending.size + (account.prefs.pending ? 1 : 0);
  account = null;
  F = savedFilters(); /* the browser's own filters were never touched */
  page = 0;
  $('list').dataset.choices = 'browser';
  renderAccount();
  sync();
  render();
}

/* auth.js publishes the signed-in user and the client (or null) and says so; the token renews without this changing. */
function onAccount() {
  const next = globalThis.misraAccount ?? null;
  if (!next) {
    if (account) detach();
  } else if (account?.userId === next.userId) {
    account.client = next.client;
  } else {
    if (account) detach();
    attach(next);
  }
}

/* The account was deleted (auth.js, after the service confirmed it): nothing of it may stay behind or be sent again. Its
   queued changes, its unsent filters and its first-sign-in answer are dropped from memory and from this browser (only this
   user's entries: another account's are not touched), and the page goes back to the signed-out state with nothing to
   save "next time". The browser's own signed-out stars, discards and filters are not the account's and stay. */
document.addEventListener('misra:account-deleted', (event) => {
  const userId = event.detail?.userId ?? account?.userId;
  if (account && account.userId === userId) {
    account.pending.clear();
    account.prefs.pending = null;
    detach();
    leftBehind = 0;
    renderSyncNote();
  }
  if (typeof userId !== 'string') return;
  for (const name of [LS_Q, LS_PF, LS_I]) {
    const all = lsGet(name, {});
    if (userId in all) {
      Reflect.deleteProperty(all, userId);
      lsSet(name, all);
    }
  }
});

function retryNow() {
  if (account.loadFailed) loadServer(account);
  if (account.prefs.state === 'failed') loadPrefs(account);
  else if (account.prefs.pending && canPushFilters(account)) pushFilters(account);
  flush();
}

/* The line under the summary about saving: nothing when all is well. It is a status region, so it is read out. */
function renderSyncNote() {
  for (const card of $('list').querySelectorAll('article[data-job-id]')) {
    card.dataset.pending = String(Boolean(account?.pending.has(card.dataset.jobId)));
  }
  const box = $('syncNote');
  const pending = account?.pending.size ?? 0;
  const prefs = account?.prefs;
  const notes = [];
  let retry = false;
  if (!account && leftBehind > 0) {
    notes.push(`${leftBehind} ${leftBehind === 1 ? 'change' : 'changes'} made while you were signed in could not be saved to your account. They stay on this device and are saved the next time you sign in.`);
  }
  if (account?.failure === 'limit' || account?.failure === 'refused') {
    notes.push(SYNC_MESSAGES[account.failure]);
  } else if (pending > 0 && account.failure === 'later') {
    notes.push(`${pending} ${pending === 1 ? 'change is' : 'changes are'} not saved to your account yet. ${pending === 1 ? 'It is' : 'They are'} kept on this device and saved when the service answers.`);
    retry = true;
  } else if (account?.loadFailed) {
    notes.push(SYNC_MESSAGES.load);
    retry = true;
  }
  if (prefs?.failure === 'refused') notes.push(SYNC_MESSAGES.filtersRefused);
  else if (prefs?.failure === 'later') {
    notes.push(SYNC_MESSAGES.filtersLater);
    retry = true;
  }
  if (prefs?.state === 'failed') {
    notes.push(SYNC_MESSAGES.filtersLoad);
    retry = true;
  } else if (prefs?.restored) notes.push(SYNC_MESSAGES.filtersRestored);
  const note = notes.map((text) => h('p', { class: 'notice', text }));
  if (retry) {
    const button = h('button', { type: 'button', class: 'btn btn-pill', id: 'syncRetry', text: 'Try again' });
    button.addEventListener('click', retryNow);
    note.push(button);
  }
  box.replaceChildren(...note);
  $('list').dataset.filters = filtersState();
  renderImportResult();
}

/* What depends on who is signed in: where choices are saved. */
function renderAccount() {
  $('list').dataset.choices ||= 'browser'; /* where the choices live: browser, or the account while loading, loaded, failed */
  $('importBox').hidden = true;
  renderSyncNote();
}

/* ---- Filters that follow the user ----
   Signed out, the filters are saved in this browser (LS_F). Signed in they are the account's: they are read from the
   user's `user_prefs` row, saved there when the user changes them, and the browser's own are left alone (they come
   back at sign-out, like the browser's own stars and discards).

   The rule when the two differ, in one line: the account's filters win when the page loads, except for a change made on
   this device that the account has not confirmed yet (it is kept in this browser, by user id, until the service takes
   it), which is saved over them; after that the last save wins, for the whole set. A change is never dropped silently:
   a failed save is kept and tried again, and the page says so. If the account's filters cannot be loaded, nothing is
   saved over them by itself; only an explicit change made now is sent. The first sign-in can bring this browser's
   filters over (see "Bringing this browser's choices over"). Only `filters` is written, never `email_settings`. */
const PREF_KEYS = Object.keys(defaults());
const asSet = (value) => JSON.stringify(Array.isArray(value) ? [...value].sort() : value); /* the order of chosen values means nothing */
const filtersEqual = (a, b) => PREF_KEYS.every((name) => asSet(a[name]) === asSet(b[name]));
const isDefaultFilters = (f) => filtersEqual(f, defaults());

/* What is sent to the account: the clean filters, with the search words cut to a sane length. */
const syncable = (f) => {
  const clean = cleanFilters(f);
  return { ...clean, q: clean.q.slice(0, MAX_QUERY) };
};
const fitsPrefs = (f) => new TextEncoder().encode(JSON.stringify(f)).length <= MAX_FILTERS_BYTES;

function loadPendingFilters(userId) {
  const saved = lsGet(LS_PF, {})[userId];
  return saved && typeof saved === 'object' ? cleanFilters(saved) : null;
}

/* Keeps the pending filters in this browser. With none pending, forgets the stored ones only if they are the very ones
   just confirmed or dropped (`settled`): another tab of the account may have stored a newer change. */
function savePendingFilters(a, settled) {
  const all = lsGet(LS_PF, {});
  if (a.prefs.pending) all[a.userId] = a.prefs.pending;
  else if (JSON.stringify(all[a.userId]) === JSON.stringify(settled)) Reflect.deleteProperty(all, a.userId);
  lsSet(LS_PF, all);
}

const canPushFilters = (a) => (a.prefs.state === 'ready' || a.prefs.state === 'failed') && !a.prefs.paused;

/* 'browser' signed out; else loading, paused (an offer is waiting), synced, saving, later (kept, tried again), refused or
   failed (the account's filters could not be loaded). */
function filtersState() {
  const p = account?.prefs;
  if (!p) return 'browser';
  if (p.failure === 'refused') return 'refused';
  if (p.failure === 'later') return 'later';
  if (p.state === 'failed') return 'failed';
  if (p.state === 'loading' || account.choicesState === 'loading') return 'loading';
  if (p.paused) return 'paused';
  return p.pending ? 'saving' : 'synced';
}

function setFilters(next) {
  if (filtersEqual(F, next)) return false;
  F = next;
  page = 0;
  sync();
  render();
  return true;
}

async function loadPrefs(a) {
  const p = a.prefs;
  p.state = 'loading';
  try {
    const { data, error } = await a.client.from('user_prefs').select('filters').eq('user_id', a.userId).retry(false);
    if (error) throw error;
    if (account !== a) return;
    p.remote = data.length > 0 ? cleanFilters(data[0].filters) : null;
    p.state = 'ready';
  } catch (error) {
    console.error(error);
    if (account !== a) return;
    p.state = 'failed';
  }
  renderSyncNote();
  settle(a);
}

async function sendFilters(a, filters) {
  try {
    return await a.client.from('user_prefs').upsert({ user_id: a.userId, filters }, { onConflict: 'user_id' }).retry(false);
  } catch (error) {
    console.error(error);
    return { error: { code: '', message: String(error) }, status: 0 };
  }
}

/* Sends the pending filters; a change made while one is on its way goes next. */
async function pushLoop(a) {
  const p = a.prefs;
  while (p.pending) {
    const sending = p.pending;
    if (!fitsPrefs(sending)) {
      p.failure = 'refused';
      p.pending = null;
      savePendingFilters(a, sending);
      return;
    }
    const { error, status } = await sendFilters(a, sending);
    if (account !== a) return;
    if (error) {
      console.error(error);
      if (isTransient(error, status)) {
        p.failure = 'later';
        clearTimeout(p.timer);
        p.timer = setTimeout(() => pushFilters(a), RETRY_MS[Math.min(p.retries, RETRY_MS.length - 1)]);
        p.retries += 1;
        return;
      }
      p.failure = 'refused';
      if (p.pending === sending) p.pending = null;
      savePendingFilters(a, sending);
      continue; /* a newer change made meanwhile is still sent */
    }
    p.failure = null;
    p.retries = 0;
    p.remote = sending;
    p.state = 'ready';
    if (p.pending === sending) p.pending = null;
    savePendingFilters(a, sending);
  }
}

function pushFilters(a) {
  clearTimeout(a.prefs.timer);
  a.prefs.flushing ??= pushLoop(a).finally(() => {
    a.prefs.flushing = null;
    renderSyncNote();
  });
  return a.prefs.flushing;
}

/* The user changed a filter while signed in: keep it (also in this browser) and save it once typing pauses. */
function queueFilters(a) {
  const p = a.prefs;
  p.pending = syncable(F);
  p.restored = false;
  if (p.failure === 'refused') p.failure = null;
  savePendingFilters(a);
  if (canPushFilters(a)) {
    clearTimeout(p.timer);
    p.timer = setTimeout(() => pushFilters(a), FILTER_SAVE_MS);
  }
  renderSyncNote();
}

/* Both of the account's lists have answered: offer this browser's choices once, else apply the account's filters. */
function settle(a) {
  if (account !== a || a.choicesState === 'loading' || a.prefs.state === 'loading') return;
  if (!a.decided && a.choicesState === 'loaded' && a.prefs.state === 'ready') {
    const offer = importOffer(a);
    if (offer.discards.length + offer.stars.length > 0 || offer.filters) {
      a.offer = offer;
      a.prefs.paused = true;
      showOffer(a);
      return;
    }
  }
  a.offer = null;
  $('importBox').hidden = true;
  applyAccountFilters(a);
}

function applyAccountFilters(a) {
  const p = a.prefs;
  p.paused = false;
  if (p.state === 'ready' && !p.pending && setFilters(p.remote ?? defaults())) p.restored = true;
  if (p.pending && canPushFilters(a)) pushFilters(a);
  renderSyncNote();
}

/* ---- Bringing this browser's choices over ----
   The first time a user signs in on a browser that holds discards, stars or filters the account does not have, the page
   offers to copy them (copy, not move: the browser's own stay as they are for when the user is signed out). Choices the
   account already has are never touched, in either state, so a star or a discard made in the account is never replaced
   and nothing is duplicated; they are sent so the service ignores a row it already has even if another device made it
   meanwhile. Only open roles are brought over (a discard of a closed role means nothing). Stars are included, though the
   PRD names discards only: a star made signed out lives on this device alone and would otherwise be stranded. Filters:
   if the account has none, the browser's are saved; if it has some, bringing them replaces those, and the offer says so.
   The offer is answered once per account and browser (LS_I); it waits until both lists are loaded, so a failed load
   never leads to an import that cannot tell what is already there. */
function importOffer(a) {
  const mine = (k) => choiceOf(k) !== null;
  const browser = savedFilters();
  const remote = a.prefs.remote ?? defaults();
  let filters = null;
  if (!isDefaultFilters(browser) && !filtersEqual(browser, remote)) filters = isDefaultFilters(remote) ? 'new' : 'replace';
  return {
    discards: Object.keys(gone).filter((k) => JOB_BY_KEY.has(k) && !mine(k)),
    stars: Object.entries(starred)
      .filter(([k]) => !gone[k] && !mine(k))
      .map(([, entry]) => entry),
    filters,
    browser,
  };
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function showOffer(a) {
  const { discards, stars, filters } = a.offer;
  const box = $('importBox');
  box.dataset.discards = String(discards.length);
  box.dataset.stars = String(stars.length);
  box.dataset.filters = filters ?? 'none';
  const choices = discards.length + stars.length > 0;
  $('importChoicesRow').hidden = !choices;
  $('importChoices').checked = true;
  $('importChoicesText').textContent = `${plural(discards.length, 'discard', 'discards')} and ${plural(stars.length, 'star', 'stars')}`;
  $('importFiltersRow').hidden = !filters;
  $('importFilters').checked = true;
  $('importFiltersText').textContent =
    filters === 'replace' ? 'Your filters (they replace the filters saved in your account)' : 'Your filters (your account has none saved yet)';
  box.hidden = false;
  $('status').textContent = 'This browser has choices that are not in your account yet. You can bring them over.';
  renderSyncNote();
}

function renderImportResult() {
  const box = $('importResult');
  const text = account?.result ?? '';
  if (text === '') {
    box.replaceChildren();
    return;
  }
  const close = h('button', { type: 'button', class: 'btn btn-pill', id: 'importClose', text: 'Close' });
  close.addEventListener('click', () => {
    account.result = '';
    renderImportResult();
    $('list-h').focus();
  });
  box.replaceChildren(h('p', { class: 'notice', text }), close);
}

/* Sends choices as changes that never replace a row the account has, and resolves with what became of them. The browser
   keeps them until the service confirms (a reload or an outage loses nothing). */
async function bringOver(a, wanted) {
  const owned = [...new Set([...a.server.keys(), ...a.pending.keys()])].filter((k) => choiceOf(k) !== null).length;
  /* Anything the account holds or is about to change (also a pending removal) is left as it is, whatever the user did
     since the list was made. */
  const open = wanted.filter(({ item }) => !a.server.has(key(item)) && !a.pending.has(key(item)));
  const ops = open
    .slice(0, Math.max(0, MAX_CHOICES - owned))
    .map(({ item, state }) => ({ ...snapshotOf(item), state, keep: true, id: `${Date.now()}-${Math.random()}` }));
  for (const op of ops) a.pending.set(key(op), op);
  savePendingBatch(a, ops.map((op) => [key(op), op]), []);
  render();
  await flush();
  const kept = ops.filter((op) => a.pending.get(key(op)) === op).length;
  const saved = ops.filter((op) => fates.get(op) === 'saved').length;
  return { saved, kept, refused: ops.length - saved - kept, left: open.length - ops.length };
}

/* What became of a bringing over, in words. */
function describeBringOver({ saved, kept, refused, left }, from) {
  const parts = [];
  if (saved > 0) parts.push(`Brought over ${plural(saved, 'choice', 'choices')} from ${from}.`);
  if (kept > 0) parts.push(`${plural(kept, 'choice', 'choices')} from ${from} ${kept === 1 ? 'is' : 'are'} kept on this device and saved when the service answers.`);
  if (refused > 0) parts.push(`${plural(refused, 'choice was', 'choices were')} not accepted by the service.`);
  if (left > 0) parts.push(`${plural(left, 'choice was', 'choices were')} left out: an account keeps at most ${MAX_CHOICES.toLocaleString('en-US')}.`);
  return parts.join(' ');
}

const wantedOf = (offer) => [
  ...offer.discards.map((k) => ({ item: JOB_BY_KEY.get(k), state: 'discard' })),
  ...offer.stars.map((item) => ({ item, state: 'star' })),
];

/* The user answered the offer. It is not made again on this browser for this account (LS_I). */
async function answerOffer(a, bring) {
  const offer = a.offer;
  a.decided = true;
  a.offer = null;
  const all = lsGet(LS_I, {});
  all[a.userId] = TODAY;
  lsSet(LS_I, all);
  $('importBox').hidden = true;
  const parts = [];
  if (bring.filters && offer.filters) {
    a.prefs.pending ??= syncable(offer.browser); /* a change made while the offer waited is the newest */
    savePendingFilters(a);
    setFilters(cleanFilters(a.prefs.pending));
    parts.push('Your filters are saved to your account.');
  }
  applyAccountFilters(a);
  $('list-h').focus();
  if (bring.choices) parts.unshift(describeBringOver(await bringOver(a, wantedOf(offer)), 'this browser'));
  if (account === a) a.result = parts.filter(Boolean).join(' ');
  renderSyncNote();
}

function failedAnswer(error) {
  console.error(error);
  if (account) {
    account.result = 'The choices could not be brought over. Anything not saved yet stays on this device.';
    renderSyncNote();
  }
}

$('importYes').addEventListener('click', () => {
  if (account?.offer) answerOffer(account, { choices: $('importChoices').checked, filters: $('importFilters').checked }).catch(failedAnswer);
});
$('importNo').addEventListener('click', () => {
  if (account?.offer) answerOffer(account, { choices: false, filters: false }).catch(failedAnswer);
});

const TODAY = DATA.generated.slice(0, 10);
const dayNum = (date) => Math.floor(Date.parse(`${date}T00:00:00Z`) / 86400000);
const age = (first) => (first ? dayNum(TODAY) - dayNum(first) : null);
const fresh = (j) => {
  const days = age(j.f);
  return Boolean(j.n) && days !== null && days <= 7;
};

function ago(first) {
  const days = age(first);
  if (days === null) return '';
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

const companies = [...new Set(JOBS.map((j) => j.c))].sort((a, b) => a.localeCompare(b));
for (const company of companies) $('company').append(h('option', { value: company, text: company }));
/* "2026-11-15 20:28 UTC" as "15 Nov, 20:28 UTC" (the year too when it is not this year); anything else is shown as it is. */
function friendlyTime(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC$/.exec(stamp);
  if (!m) return stamp;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const year = Number(m[1]) === new Date().getUTCFullYear() ? '' : ` ${m[1]}`;
  return `${Number(m[3])} ${months[Number(m[2]) - 1]}${year}, ${m[4]}:${m[5]} UTC`;
}
$('upd').textContent = friendlyTime(DATA.generated);
$('upd').setAttribute('datetime', DATA.generated.replace(' UTC', 'Z').replace(' ', 'T'));

/* A job is in scope when it is in Israel, or the Region chips include Abroad, or it is a remote role that does not say
   where and the user chose to include those. */
const inScope = (j, f) => j.il || f.regions.includes(ABROAD) || (f.remote && j.ru);

/* The regions a job counts under: its own (Abroad for a job outside Israel that names a place; a remote role that does not
   say where is not "abroad"), plus Remote for a remote role that does not
   say when the user chose to include those. */
const regionsOf = (j, f) => [...(j.il ? j.rg : j.ru ? [] : [ABROAD]), ...(f.remote && j.ru ? [REMOTE_REGION] : [])];

/* The Region chips: a job matches when it is in any chosen one. Nothing chosen means every role in scope (Israel). */
function placeMatches(j, f) {
  if (f.regions.length === 0) return true;
  const regions = regionsOf(j, f);
  return f.regions.some((r) => regions.includes(r));
}

/* Within one filter any chosen value matches; the filters combine (a job fits the levels and the role types). A filter
   with nothing chosen means every value, and a value nobody has matches nothing. */
const levelMatches = (j, f) => f.levels.length === 0 || f.levels.includes(j.lv);
const typeMatches = (j, f) => f.types.length === 0 || f.types.some((t) => j.rt.includes(t));

/* Words in the full text of a posting (PLAN.md step 7.3). The text is never sent to the page: a database function
   (search_job_keys) answers with the keys of the open roles whose text has the words, and a role matches the search when
   its title, company, team or location has every word, or when its key is in that answer. The answers are kept by query
   (the words, one space apart), so only the answer for the query being shown counts; a search that fails or is slow
   leaves the match on title, company, team and location alone. */
/* The function answers nothing without a run of 3 letters or digits (Postgres [[:alnum:]]). Marks (\p{M}) count too: glibc
   counts the points of pointed Hebrew and Arabic as letters, so a pointed word must still be sent. This may be wider than
   the function (it also takes Latin accents and signs like ½), and a query it lets through that the function refuses
   gets an empty answer and no note; the other way round the search would be held back wrongly. */
const hasSearchWord = (query) => /[\p{L}\p{M}\p{N}]{3}/u.test(query);
const SEARCH_DELAY_MS = 400; /* the box changes with every key: ask once typing pauses */
const SEARCH_KEPT = 30; /* answers kept */
const SEARCH_CAP = 3000; /* the most keys the function returns: a full answer may be missing some */
const SEARCH_NOTES = {
  failed: "Could not search job descriptions. Showing matches in titles, companies, teams and locations only.",
  capped: 'Too many description matches, so some roles may be missing. Add a word to narrow the search.',
  short: 'Type a word of 3 or more letters or digits to search job descriptions too.',
};
const textKeys = new Map();
const textCapped = new Set(); /* the queries whose answer hit the cap */
const searchFailed = new Set(); /* the queries whose last ask failed, each until it is answered: asks overlap, so one slot would lose a failure */
let searchTimer;
let shortFor = ''; /* the too-short query whose pause has passed: its note shows then, so the line under the heading does not jump while a word is typed */
const searchQuery = (q) => q.trim().toLowerCase().split(/\s+/).filter(Boolean).join(' ');

async function fetchTextKeys(query) {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/rpc/search_job_keys`, {
    method: 'POST',
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ p_query: query }),
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`search_job_keys: HTTP ${response.status}`);
  const pairs = await response.json();
  if (!Array.isArray(pairs)) throw new Error('search_job_keys: not a list');
  return { keys: new Set(pairs.map(([company, id]) => `${company}|${id}`)), capped: pairs.length >= SEARCH_CAP };
}

/* The sentence about the description search for the query in the box, or nothing when all is well. It goes on the
   summary line, which is a visible line and is read out by the status region. Nothing of the query is logged. */
function searchNote() {
  const query = searchQuery(F.q);
  if (query === '') return '';
  if (!hasSearchWord(query)) return shortFor === query ? SEARCH_NOTES.short : '';
  if (searchFailed.has(query)) return SEARCH_NOTES.failed;
  return textCapped.has(query) ? SEARCH_NOTES.capped : '';
}

/* Ask for the keys of the query in the box once, a moment after the last key; show the list again when they come, if
   the box still says the same. */
function searchText() {
  clearTimeout(searchTimer);
  const query = searchQuery(F.q);
  if (shortFor !== query) shortFor = '';
  if (query !== '' && !hasSearchWord(query)) {
    searchTimer = setTimeout(() => {
      shortFor = query;
      if (ready && view === 'roles' && searchQuery(F.q) === query) render();
    }, SEARCH_DELAY_MS);
    return;
  }
  if (!hasSearchWord(query) || textKeys.has(query)) return;
  searchTimer = setTimeout(async () => {
    try {
      const { keys, capped } = await fetchTextKeys(query);
      if (textKeys.size >= SEARCH_KEPT) {
        textKeys.clear();
        textCapped.clear();
      }
      textKeys.set(query, keys);
      if (capped) textCapped.add(query);
      searchFailed.delete(query);
    } catch {
      if (!textKeys.has(query)) searchFailed.add(query); /* an older ask of the same words may fail after a newer one answered; the title match stays and says so; a later change asks again */
    }
    if (ready && view === 'roles' && searchQuery(F.q) === query) render();
  }, SEARCH_DELAY_MS);
}

function select(f) {
  const terms = f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = textKeys.get(searchQuery(f.q));
  const out = JOBS.filter((j) => {
    if (isDiscarded(key(j)) && !f.showGone) return false;
    if (!inScope(j, f)) return false;
    if (f.company && j.c !== f.company) return false;
    if (!levelMatches(j, f) || !typeMatches(j, f) || !placeMatches(j, f)) return false;
    if (f.onlyNew && !fresh(j)) return false;
    if (f.hideStale && j.s) return false;
    if (terms.length > 0) {
      const hay = `${j.c} ${j.t} ${j.m} ${j.l}`.toLowerCase();
      return terms.every((term) => hay.includes(term)) || Boolean(found?.has(key(j)));
    }
    return true;
  });
  out.sort((a, b) => {
    if (f.sort === 'co') return a.c.localeCompare(b.c) || a.t.localeCompare(b.t);
    if (f.sort === 'title') return a.t.localeCompare(b.t);
    return (b.f > a.f ? 1 : b.f < a.f ? -1 : 0) || a.c.localeCompare(b.c);
  });
  return out;
}

function sync() {
  hideUndo(); /* every change to F, from the reader or from the account, ends the offer of an undo (reset sets it after) */
  $('q').value = F.q;
  searchText();
  $('company').value = F.company;
  $('sort').value = F.sort;
  $('size').value = SIZES.includes(Number(F.size)) ? Number(F.size) : defaults().size;
  $('hideStale').checked = F.hideStale;
  $('showGone').checked = F.showGone;
  $('onlyNew').setAttribute('aria-pressed', String(F.onlyNew));
  $('remote').setAttribute('aria-pressed', String(F.remote));
  for (const chip of document.querySelectorAll('.chip[data-group]')) {
    const chosen = F[chip.dataset.group];
    chip.setAttribute('aria-pressed', String(chip.dataset.value === '' ? chosen.length === 0 : chosen.includes(chip.dataset.value)));
  }
  const active = activeFilterCount(F);
  $('filtersOpen').textContent = active > 0 ? `Filters (${active})` : 'Filters';
}

/* How many filters differ from the defaults, a group of values (levels, role types, regions) counting once.
   The search words are not a filter: the search box shows them. */
function activeFilterCount(f) {
  const base = defaults();
  const groups = ['levels', 'types', 'regions'].filter((name) => f[name].length > 0);
  const settings = ['company', 'sort'].filter((name) => f[name] !== base[name]);
  const switches = ['remote', 'onlyNew', 'showGone', 'hideStale'].filter((name) => f[name]);
  return groups.length + settings.length + switches.length;
}

/* One chip per value of a filter, after an "All" chip that clears it. */
function buildChips(group, names) {
  $(group).replaceChildren(
    ...['', ...names].map((value) =>
      h('button', {
        type: 'button',
        class: 'chip',
        'data-group': group,
        'data-value': value,
        'aria-pressed': 'false',
        text: value === '' ? 'All' : value,
      }),
    ),
  );
}
for (const group of ['levels', 'types', 'regions']) buildChips(group, CHOICES[group]);

/* The new roles (this week, in scope, not discarded) that the filters let through,
   Company aside: one count per company. The row and the headline both read it, so they never disagree, and a tap on a
   circle (which sets Company) shows at least that many new roles. */
function newRoles() {
  const counts = {};
  const jobs = select({ ...F, company: '', showGone: false, onlyNew: true });
  for (const j of jobs) counts[j.c] = (counts[j.c] ?? 0) + 1;
  return { counts, total: jobs.length };
}

/* One circle per company with new roles, 15 at most, most first; the chosen company always stays, so it can be turned off. */
function renderStories(counts) {
  const names = Object.keys(counts)
    .sort((a, b) => counts[b] - counts[a] || a.localeCompare(b))
    .slice(0, 15);
  if (F.company && !names.includes(F.company)) names.push(F.company);
  const focused = document.activeElement?.closest('#stories .story')?.dataset.company; /* replaceChildren drops the focus */
  $('hiring').hidden = names.length === 0;
  $('stories').replaceChildren(
    ...names.map((company) => {
      const n = counts[company] ?? 0;
      const button = h(
        'button',
        {
          type: 'button',
          class: 'story story-new',
          'data-company': company,
          'data-count': String(n),
          'aria-pressed': String(F.company === company),
        },
        [
          h('span', { class: 'story-badge', 'aria-hidden': 'true', text: company.charAt(0).toUpperCase() }),
          h('span', { class: 'story-name', text: company }),
          h('span', { class: 'story-count', 'aria-hidden': 'true', text: `${n} new` }),
          h('span', { class: 'sr-only', text: n === 0 ? ', no new roles' : `, ${n} new ${n === 1 ? 'role' : 'roles'}` }),
        ],
      );
      return h('li', {}, [button]);
    }),
  );
  layoutStories();
  if (focused !== undefined) {
    /* the circle can be gone (a chosen company without new roles, turned off): the focus goes to the list heading, never to the page */
    const same = [...$('stories').children].find((li) => li.firstChild.dataset.company === focused);
    (same?.firstChild ?? $('list-h')).focus();
  }
}

/* The headline: the new roles that match the filters, "95 new this week." With a filter on it says "12 new matches."
   and, when none is new, how many open roles match ("40 open matches."). Without a filter and without new roles it is
   the open count, "in Israel" unless the Abroad chip is on. */
function renderHero(newN, filteredCount) {
  const filtered = F.q.trim() !== '' || activeFilterCount({ ...F, company: '', sort: 'new', onlyNew: false, showGone: false }) > 0;
  const many = (n) => (n === 1 ? 'match.' : 'matches.');
  let top;
  let bottom;
  if (view === 'starred') [top, bottom] = ['Starred', 'roles.'];
  else if (newN > 0) [top, bottom] = [`${newN} new`, filtered ? many(newN) : 'this week.'];
  else if (filtered) [top, bottom] = filteredCount > 0 ? [`${filteredCount} open`, many(filteredCount)] : ['No', 'matches.'];
  else [top, bottom] = [`${filteredCount} open`, F.regions.includes(ABROAD) ? 'roles.' : 'in Israel.'];
  $('heroTop').textContent = top;
  $('heroBottom').textContent = bottom;
}

/* "New this week" is one line of circles and a button ("Show all 15" / "Show fewer") at its end; the others wait in a
   wrapping area below until the button opens it. The choice lasts for the page session only. Hidden circles carry the
   `hidden` attribute, so they are out of the Tab order and away from screen readers, and nothing scrolls sideways. */
let hiringOpen = false;
let hiringWidth = 0;
function layoutStories() {
  const items = [...$('stories').children];
  const toggle = $('hiringToggle');
  const focused = document.activeElement === toggle; /* measuring hides the button for a moment, and that drops the focus */
  for (const item of items) item.hidden = false;
  toggle.hidden = true;
  const overflowing = () => {
    const top = items[0]?.offsetTop ?? 0;
    return items.filter((item) => item.offsetTop > top);
  };
  if (overflowing().length === 0) {
    $('stories').dataset.expanded = 'false';
    return; /* everything fits on one line: no button */
  }
  toggle.hidden = false; /* the button takes room from the line */
  const rest = overflowing();
  toggle.textContent = hiringOpen ? 'Show fewer' : `Show all ${items.length}`;
  toggle.setAttribute('aria-expanded', String(hiringOpen));
  $('stories').dataset.expanded = String(hiringOpen);
  if (!hiringOpen) for (const item of rest) item.hidden = true;
  if (focused) toggle.focus();
}
$('hiringToggle').addEventListener('click', () => {
  hiringOpen = !hiringOpen;
  layoutStories();
});
new ResizeObserver(() => {
  const width = $('hiring').clientWidth;
  if (width === hiringWidth) return;
  hiringWidth = width;
  layoutStories();
}).observe($('hiring'));

const NEW_TAB = ' (opens in a new tab)';
const MAX_LOCATION = 80;
/* Separator of the list-valued data-* hooks; places can contain commas, never a bar. */
const LIST_SEP = '|';

/* A tag of a card, with a hidden name so a screen reader hears "Level: Intern", not just "Intern". */
const tag = (name, value, cls) =>
  h('span', { class: cls }, [h('span', { class: 'sr-only', text: `${name}: ` }), value]);

/* The star toggle: its name stays "Star: ..." (the visible word is part of it) and aria-pressed carries the state; the
   glyph also changes from an outline to a filled star and the button is tinted, so it is never colour alone. */
function starButton(k, what, on) {
  return h(
    'button',
    {
      type: 'button',
      class: 'btn btn-pill btn-star',
      'data-act': 'star',
      'data-k': k,
      'aria-pressed': String(on),
      'aria-label': `Star: ${what}`,
    },
    [h('span', { 'aria-hidden': 'true', text: on ? '★' : '☆' }), 'Star'],
  );
}

/* A starred job that has closed: what was kept of it (title, company, location, link), marked as no longer listed. There
   is no Apply, since the role is gone; the saved link is the posting as it was. */
function closedCardEl(e) {
  const k = key(e);
  const what = `${e.t || 'Untitled role'} at ${e.c}`;
  const url = e.u ? safeUrl(e.u) : null;
  const title = e.t || 'Untitled role';
  const art = h('article', {
    class: 'card',
    'data-job-id': k,
    'data-company': e.c,
    'data-listed': 'false',
    'data-starred': 'true',
    'data-discarded': 'false',
    'data-pending': String(Boolean(account?.pending.has(k))),
  });
  art.append(
    h('span', { class: 'sticker sticker-warn card-flag', text: 'No longer listed' }),
    h('p', { class: 'card-company', text: e.c }),
    h('h3', { class: 'card-title' }, [
      url && url !== '#'
        ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, [title, h('span', { class: 'sr-only', text: NEW_TAB })])
        : title,
    ]),
    h('p', { class: 'card-tags' }, [e.l && h('span', { class: 'pill', text: e.l.slice(0, MAX_LOCATION) })]),
    h('p', { class: 'card-actions' }, [starButton(k, what, true)]),
  );
  return h('li', {}, [art]);
}

/* The place, level, role types and team of a role, and when it was first seen: on the card. */
const tagsEl = (j) =>
  h('p', { class: 'card-tags' }, [
    j.l && h('span', { class: 'pill', text: j.l.slice(0, MAX_LOCATION) }),
    ...j.rg.filter((region) => region !== j.l).map((region) => tag('Region', region, 'pill')),
    ...j.lo.filter((place) => place !== j.l).map((place) => tag('Place', place, 'pill')),
    j.ru && tag('Region', REMOTE_NOT_SAID, 'pill'),
    tag('Level', j.lv, 'pill pill-soft'),
    ...j.rt.map((type) => tag('Role type', type, 'pill pill-soft')),
    j.m && h('span', { class: 'pill', text: j.m }),
    j.f && h('time', { class: 'card-seen', datetime: j.f, text: ago(j.f) }),
  ]);

/* The ellipsis after a snippet that is shorter than the full text: exactly when the stored flag `snippet_cut` is true (the
   sync sets it, see descriptions.snippet_cut). A row without the flag (null) shows nothing. Hidden from screen readers. */
const cutMark = (j) => j.sc && h('span', { 'aria-hidden': 'true', text: '…' });

function cardEl(j) {
  const k = key(j);
  const discarded = isDiscarded(k);
  const starredNow = isStarred(k);
  const isNew = fresh(j);
  const what = `${j.t} at ${j.c}`;
  const url = safeUrl(j.u);
  const art = h('article', {
    class: 'card',
    'data-job-id': k,
    'data-company': j.c,
    'data-level': j.lv,
    'data-role-types': j.rt.join(LIST_SEP),
    'data-regions': j.rg.join(LIST_SEP),
    'data-locations': j.lo.join(LIST_SEP),
    'data-remote-country-not-given': String(j.ru),
    'data-new': String(isNew),
    'data-stale': String(j.s),
    'data-discarded': String(discarded),
    'data-starred': String(starredNow),
    'data-pending': String(Boolean(account?.pending.has(k))),
  });
  if (j.s) {
    art.append(
      h('span', {
        class: 'sticker sticker-warn card-flag',
        title: 'The latest crawl of this company failed. The role may be out of date.',
        text: 'Old?',
      }),
    );
  } else if (isNew) {
    art.append(h('span', { class: 'sticker card-flag', text: 'New' }));
  }
  art.append(
    h('p', { class: 'card-company', text: j.c }),
    h('h3', { class: 'card-title' }, [
      h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, [
        j.t,
        h('span', { class: 'sr-only', text: NEW_TAB }),
      ]),
    ]),
    /* Third-party text, so text and never markup; dir="auto" lets a Hebrew posting run right to left. */
    j.sn && h('p', { class: 'card-snippet', dir: 'auto' }, [j.sn, cutMark(j)]),
    /* The lawyer's link under every snippet: the posting in full is on the employer's page. */
    j.sn &&
      h('p', { class: 'card-more' }, [
        h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, [
          'Read full description & apply on ',
          j.c,
          ' ',
          h('span', { 'aria-hidden': 'true', text: '↗' }),
          h('span', { class: 'sr-only', text: NEW_TAB }),
        ]),
      ]),
    tagsEl(j),
    h('p', { class: 'card-actions' }, [
      h('a', { class: 'btn btn-primary', href: url, target: '_blank', rel: 'noopener noreferrer' }, [
        'Apply ',
        h('span', { 'aria-hidden': 'true', text: '↗' }),
        h('span', { class: 'sr-only', text: ` to ${what}${NEW_TAB}` }),
      ]),
      starButton(k, what, starredNow),
      h('button', {
        type: 'button',
        class: 'btn btn-pill',
        'data-act': 'toggle',
        'data-k': k,
        'aria-label': `${discarded ? 'Restore' : 'Not for me'}: ${what}`,
        text: discarded ? 'Restore' : 'Not for me',
      }),
    ]),
  );
  return h('li', {}, [art]);
}

/* The visible summary changes on every keystroke; screen readers hear it once typing pauses, and only when the words
   have changed or the search has (a rewrite of the same text for the same search is read again). */
function announce(text) {
  clearTimeout(announceTimer);
  if (!ready) return;
  announceTimer = setTimeout(() => {
    const query = searchQuery(F.q);
    if ($('status').textContent !== text || query !== announcedQuery) $('status').textContent = text;
    announcedQuery = query;
  }, ANNOUNCE_DELAY_MS);
}

/* A search for words that the description search has not answered yet: the count shown is the title match only, and the
   answer draws the list and the line again, so the line is announced then and not before. */
const searchPending = () => {
  const query = searchQuery(F.q);
  return hasSearchWord(query) && !textKeys.has(query) && !searchFailed.has(query);
};

/* The starred list is a view of its own: the filters do not apply to it (a closed role has no level, region or role
   type), so they are put away while it shows. */
function showView() {
  const roles = view === 'roles';
  for (const id of ['moreFilters', 'searchBar', 'filtersOpen']) $(id).hidden = !roles;
  $('starredView').setAttribute('aria-pressed', String(!roles));
  $('starredView').textContent = `Starred (${starredItems().length})`;
}

function renderStarred() {
  const items = starredItems();
  const closedN = items.filter((item) => item.closed).length;
  const summary = `${items.length} starred ${items.length === 1 ? 'role' : 'roles'}${closedN > 0 ? `, ${closedN} no longer listed` : ''}. The filters do not apply to this list.`;
  $('summary').textContent = summary;
  announce(summary);
  $('hiring').hidden = true;
  return items;
}

/* Draw the list. replaceChildren drops the focus, and a late search answer draws the list while a reader is on one of
   its controls: put the focus back on the same control of the same role, or on the list heading when that role is not
   on the page any more (the heading shows itself while it holds the focus, so the reader sees where they are). A caller that places the focus itself (a star or a discard) passes false. A modal dialog that is
   open holds the focus, so it never finds it in the list. */
function drawList(nodes, keepFocus) {
  const list = $('list');
  const held = list.contains(document.activeElement) ? document.activeElement : null;
  const card = held?.closest('article');
  const jobId = card?.dataset.jobId;
  const spot = card ? [...card.querySelectorAll('a[href], button')].indexOf(held) : -1;
  list.replaceChildren(...nodes);
  if (!held || !keepFocus) return;
  const again = [...list.querySelectorAll('article')].find((art) => art.dataset.jobId === jobId);
  const control = again?.querySelectorAll('a[href], button')[spot];
  if (control) control.focus({ preventScroll: true }); /* a late answer must not scroll the page */
  else $('list-h').focus(); /* it shows a focus ring (.list-heading) and may be out of view: let the browser scroll to it */
}

function render(focusIdx) {
  showView();
  const starredView = view === 'starred';
  const res = starredView ? renderStarred() : select(F);
  const goneN = discardedCount();
  const scope = JOBS.filter((j) => inScope(j, F)).length;
  $('goneN').textContent = goneN;
  $('showGone').disabled = goneN === 0;
  $('staleN').textContent = select({ ...F, hideStale: false }).filter((j) => j.s).length; /* what ticking the box would hide */
  const { counts, total } = newRoles();
  renderHero(total, select({ ...F, company: '', showGone: false, onlyNew: false }).length);
  if (!starredView) {
    const note = searchNote();
    const summary = `Showing ${res.length} of ${scope} open roles${F.regions.includes(ABROAD) ? '' : ' in Israel'}. ${goneN} discarded.${note && ` ${note}`}`;
    $('summary').textContent = summary;
    if (searchPending()) clearTimeout(announceTimer);
    else announce(summary);
    $('filtersDone').textContent = `Show ${res.length} ${res.length === 1 ? 'role' : 'roles'}`;
    renderStories(counts);
  }
  const list = $('list');
  if (res.length === 0) {
    drawList([
      h('li', {
        class: 'empty',
        text: starredView ? 'No starred roles yet. Use Star on a role to keep it here.' : 'No roles match. Change the search or the filters.',
      }),
    ], focusIdx === undefined);
    if (focusIdx !== undefined) $('list-h').focus(); /* the last role was starred or discarded: the focus stays on the page's list */
    $('pager').hidden = true;
    return;
  }
  const size = SIZES.includes(Number(F.size)) ? Number(F.size) : 10;
  const pages = Math.ceil(res.length / size);
  page = Math.min(page, pages - 1);
  drawList(res.slice(page * size, (page + 1) * size).map((item) => (item.closed ? closedCardEl(item) : cardEl(item))), focusIdx === undefined);
  $('pager').hidden = res.length <= 10;
  $('size').value = size;
  $('prev').disabled = page === 0;
  $('next').disabled = page >= pages - 1;
  $('pageInfo').textContent = `Page ${page + 1} of ${pages}`;
  if (focusIdx !== undefined) {
    const buttons = list.querySelectorAll('[data-act]');
    buttons[Math.min(focusIdx, buttons.length - 1)]?.focus();
  }
}

function update() {
  page = 0;
  if (account) queueFilters(account); /* the account's filters; the browser's own are left alone while signed in */
  else lsSet(LS_F, F);
  sync();
  render();
}

$('q').addEventListener('input', (e) => {
  F.q = e.target.value;
  update();
});
$('company').addEventListener('change', (e) => {
  F.company = e.target.value;
  update();
});
$('sort').addEventListener('change', (e) => {
  F.sort = e.target.value;
  update();
});
$('hideStale').addEventListener('change', (e) => {
  F.hideStale = e.target.checked;
  update();
});
$('showGone').addEventListener('change', (e) => {
  F.showGone = e.target.checked;
  update();
});
$('onlyNew').addEventListener('click', () => {
  F.onlyNew = !F.onlyNew;
  update();
});
$('remote').addEventListener('click', () => {
  F.remote = !F.remote;
  update();
});

/* Add the value to a filter, or take it out; the "All" chip (empty value) clears the filter. */
function toggleChoice(group, value) {
  if (value === '') F[group] = [];
  else if (F[group].includes(value)) F[group] = F[group].filter((v) => v !== value);
  else F[group] = [...F[group], value];
  update();
}
for (const chip of document.querySelectorAll('.chip[data-group]')) {
  chip.addEventListener('click', () => {
    toggleChoice(chip.dataset.group, chip.dataset.value);
  });
}
$('stories').addEventListener('click', (e) => {
  const button = e.target.closest('.story');
  if (!button) return;
  F.company = F.company === button.dataset.company ? '' : button.dataset.company;
  update();
});
/* "Reset" can be undone for a few seconds: the filters as they were are kept in memory only, with no history
   and no dialog. Any other change to the filters ends the offer (update() hides it), so it never restores stale state. */
const UNDO_MS = 6000;
function hideUndo() {
  clearTimeout(undoTimer);
  undoFilters = null;
  const note = $('resetNote');
  if (note.contains(document.activeElement)) $('reset').focus(); /* the focus never falls to the page when it goes */
  note.replaceChildren();
}
$('reset').addEventListener('click', () => {
  const before = F;
  F = { ...defaults(), q: F.q }; /* the search words are not a filter: the search box keeps them */
  update();
  undoFilters = before;
  $('resetNote').replaceChildren(h('span', { text: 'Filters reset.' }), h('button', { type: 'button', class: 'btn btn-pill', id: 'undoReset', text: 'Undo' }));
  undoTimer = setTimeout(hideUndo, UNDO_MS);
});
$('resetNote').addEventListener('click', (e) => {
  if (!e.target.closest('#undoReset')) return;
  F = { ...undoFilters, q: F.q }; /* the search words typed meanwhile stay */
  update();
});
/* The filters are a panel in the page on a wide screen and a bottom sheet on a phone. It is one native <dialog>: open
   as a panel (show) on a wide screen, and opened modally (showModal) from "Filters" on a narrow one, where the browser
   traps focus, closes it on Esc and hides the page from screen readers. The media query is the one in sheet.css. */
const sheet = $('moreFilters');
const compact = matchMedia('(width < 561px), (height < 500px)');

function openFilters() {
  sheet.close(); /* the markup opens it as a panel, and showModal() refuses a dialog that is already open */
  sheet.showModal();
  $('sheetTitle').focus();
}
function closeFilters() {
  sheet.close();
}
sheet.addEventListener('close', () => {
  if (sheet.matches(':modal')) return; /* the close() that openFilters made, reported after showModal */
  if (compact.matches) $('filtersOpen').focus();
  else sheet.show();
});
compact.addEventListener('change', () => {
  if (compact.matches) return;
  if (sheet.matches(':modal')) sheet.close(); /* its close event shows the panel */
  else if (!sheet.open) sheet.show();
});
sheet.addEventListener('click', (e) => {
  if (e.target === sheet) closeFilters(); /* the backdrop */
});
$('filtersOpen').addEventListener('click', openFilters);
$('filtersClose').addEventListener('click', closeFilters);
$('filtersDone').addEventListener('click', closeFilters);
$('size').addEventListener('change', (e) => {
  F.size = Number(e.target.value);
  update();
});

/* After paging, focus moves to the list heading so keyboard and screen-reader users land on the new page. */
function go(step) {
  page += step;
  render();
  const heading = $('list-h');
  heading.focus();
  heading.scrollIntoView();
}
$('prev').addEventListener('click', () => {
  go(-1);
});
$('next').addEventListener('click', () => {
  go(1);
});

/* A job as the page holds it, or, for a starred job that has closed, what was kept of it. */
const itemFor = (k) => JOB_BY_KEY.get(k) ?? starredItems().find((item) => key(item) === k);

/* Changes a choice and shows it at once; resolves as setChoice does. */
function choose(item, state) {
  const saving = setChoice(item, state);
  render();
  return saving;
}

$('list').addEventListener('click', (e) => {
  const button = e.target.closest('[data-act]');
  if (!button) return;
  const k = button.dataset.k;
  const item = itemFor(k);
  const index = [...$('list').querySelectorAll('[data-act]')].indexOf(button);
  if (button.dataset.act === 'star') setChoice(item, isStarred(k) ? null : 'star');
  else setChoice(item, isDiscarded(k) ? null : 'discard');
  render(index);
});

$('starredView').addEventListener('click', () => {
  view = view === 'starred' ? 'roles' : 'starred';
  page = 0;
  render();
});

const gaps = DATA.gaps ?? { blocked: [], untracked: [], total: 0, groups: [] };
{
  /* The card is always there: "Add a company" (report.js) sits under the list and stays in view while the list is
     closed, even when no company is known to be missing. The reader gets two lists, not the internal reasons: the
     companies we choose not to include (their robots.txt does not allow it) first, then the ones whose roles we
     cannot find or read. Each company keeps its reason in data-reason, and the reasons stay in the page data. */
  const parts = [
    {
      part: 'skipped',
      title: 'We choose not to include these companies',
      note: 'Their policy does not allow automated access, so we respect it.',
      sites: [],
    },
    {
      part: 'cannot-read',
      title: 'We miss roles at these companies',
      note: 'We cannot find or read their open roles in Israel.',
      sites: [],
    },
  ];
  /* What a company's link opens (`to`, from the build): said in two or three words under the name. */
  const LINK_KIND = { israel: 'Israel careers', careers: 'Careers page', site: 'Website' };
  for (const group of gaps.groups) {
    const part = parts[group.reason === 'robots_disallowed' ? 0 : 1];
    for (const site of group.sites) part.sites.push({ ...site, reason: group.reason });
  }
  if (gaps.total > 0) {
    $('gaps-title').textContent = `Coverage gaps: ${gaps.total} ${gaps.total === 1 ? 'company' : 'companies'} whose roles may be missing or old`;
    for (const part of parts.filter((p) => p.sites.length > 0)) {
      part.sites.sort((a, b) => a.c.localeCompare(b.c, 'en', { sensitivity: 'base' }));
      const heading = `gap-${part.part}-h`;
      const list = h('ul', { class: 'gap-list', 'aria-labelledby': heading });
      for (const site of part.sites) {
        /* The name is a link to the careers page (or the company's own site): https only, so nothing else is ever linked. */
        const name = site.url?.startsWith('https://')
          ? h('a', { class: 'gap-link', href: site.url, target: '_blank', rel: 'noopener noreferrer' }, [
              h('span', { class: 'gap-name-text', text: site.c }),
              ...(LINK_KIND[site.to] ? [h('span', { class: 'gap-to', text: LINK_KIND[site.to] })] : []),
              h('span', { class: 'sr-only', text: NEW_TAB }),
            ])
          : site.c;
        list.append(
          h('li', { class: 'gap-card', 'data-company': site.c, 'data-reason': site.reason }, [
            h('b', { class: 'gap-name' }, [name]),
          ]),
        );
      }
      $('gapParts').append(
        h('section', { class: 'gap-part', 'data-part': part.part, 'data-count': String(part.sites.length) }, [
          h('h3', { class: 'gap-part-title', id: heading }, [
            h('span', { text: part.title }),
            h('span', { class: 'gap-count', text: String(part.sites.length) }),
          ]),
          h('p', { class: 'gap-part-note', text: part.note }),
          list,
        ]),
      );
    }
  } else {
    $('gaps').hidden = true;
  }
  $('gapsCard').hidden = false;
}

/* WebMCP: expose the same data and actions to browser agents (navigator.modelContext). */
function describe(j) {
  return {
    id: key(j),
    company: j.c,
    title: j.t,
    team: j.m,
    location: j.l,
    url: j.u,
    firstSeen: j.f,
    level: j.lv,
    roleTypes: j.rt,
    regions: j.rg,
    locations: j.lo,
    inIsrael: j.il,
    remoteCountryNotGiven: j.ru,
    isNew: fresh(j),
    possiblyOld: j.s,
    discarded: isDiscarded(key(j)),
    starred: isStarred(key(j)),
    snippet: j.sn || null,
  };
}

/* A starred job for an assistant: an open role as describe() gives it, a closed one as what was kept of it. */
const describeStarred = (item) =>
  item.closed
    ? { id: key(item), company: item.c, title: item.t, location: item.l, url: item.u, listed: false, starred: true }
    : { ...describe(item), listed: true };
const reply = (obj, isError = false) => ({ content: [{ type: 'text', text: JSON.stringify(obj) }], isError });
const find = (id) => JOB_BY_KEY.get(id);

/* A tool argument that is a list of strings; one string is a list of one. */
const asList = (value) => (Array.isArray(value) ? value : [value]).filter((v) => typeof v === 'string');

/* What an assistant got wrong, so an empty answer is never silent: a value no role has matches nothing (as it does
   for the page), and the old category argument is gone. See list_filters for the accepted values. */
function argumentWarnings(a) {
  const warnings = [];
  if ('category' in a) warnings.push('category was replaced by levels and roleTypes and is ignored');
  for (const old of ['allLocations', 'locations']) {
    if (old in a) warnings.push(`${old} was replaced by the "${ABROAD}" value of regions and is ignored`);
  }
  for (const [arg, known] of [['levels', LEVELS], ['roleTypes', ROLE_TYPES], ['regions', CHOICES.regions]]) {
    const given = a[arg] === undefined || a[arg] === null ? [] : [a[arg]].flat();
    for (const value of given) {
      if (typeof value !== 'string') warnings.push(`${arg}: ${JSON.stringify(value)} is not a string and is ignored`);
      else if (!known.includes(value)) warnings.push(`${arg}: no role has "${value}" (values are case-sensitive; see list_filters)`);
    }
  }
  return warnings;
}

function search(a) {
  const f = defaults();
  f.q = a.query ?? '';
  f.company = a.company ?? '';
  f.onlyNew = Boolean(a.newOnly);
  f.showGone = Boolean(a.includeDiscarded);
  f.sort = { newest: 'new', company: 'co', title: 'title' }[a.sort] ?? 'new';
  f.levels = asList(a.levels);
  f.types = asList(a.roleTypes);
  f.regions = asList(a.regions);
  f.remote = Boolean(a.includeRemoteNotSaid);
  const warnings = argumentWarnings(a);
  const res = select(f);
  const n = Math.max(1, Math.min(100, Math.floor(a.limit ?? 20)));
  const answer = { total: res.length, returned: Math.min(n, res.length), jobs: res.slice(0, n).map(describe) };
  return warnings.length > 0 ? { ...answer, warnings } : answer;
}

const idSchema = {
  type: 'object',
  properties: { id: { type: 'string', description: 'Job id as returned by search_jobs (company|jobId).' } },
  required: ['id'],
};
const unknownJob = () => Promise.resolve(reply({ error: 'Unknown job id' }, true));

const TOOLS = [
  {
    name: 'search_jobs',
    description: 'Search open entry-level, student and product roles in Israel. All filters are optional.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Words to match in title, company, team or location.' },
        levels: {
          type: 'array',
          items: { type: 'string', enum: LEVELS },
          description:
            'Levels to include (any of them). Intern: intern, internship and student titles. Junior: junior, jr, graduate, entry and trainee titles. Not specified: every other title. Empty or missing means every level.',
        },
        roleTypes: {
          type: 'array',
          items: { type: 'string', enum: ROLE_TYPES },
          description:
            'Role types (fields) to include (any of them). A title can have several. Empty or missing means every role type. A role is returned when it fits both the levels and the role types: Product with no level gives Product at any seniority; Product with Intern and Junior gives only entry-level Product roles.',
        },
        regions: {
          type: 'array',
          items: { type: 'string', enum: CHOICES.regions },
          description:
            `Regions of Israel to include (any of them), and "${ABROAD}" for roles outside Israel. Empty or missing means every region of Israel.`,
        },
        includeRemoteNotSaid: {
          type: 'boolean',
          description:
            'Also include remote roles whose location names no country (for example "Remote"), counted under the Remote region. They are left out by default because they may not be open to people in Israel.',
        },
        company: { type: 'string', description: 'Exact company name.' },
        newOnly: { type: 'boolean', description: 'Only roles first seen in the last 7 days.' },
        includeDiscarded: { type: 'boolean' },
        sort: { type: 'string', enum: ['newest', 'company', 'title'] },
        limit: { type: 'integer', minimum: 1, maximum: 100, description: 'Default 20.' },
      },
    },
    annotations: { readOnlyHint: true },
    execute: (a) => Promise.resolve(reply(search(a ?? {}))),
  },
  {
    name: 'list_filters',
    description:
      'List the values the search_jobs filters accept, with the number of open roles in Israel for each: levels, roleTypes and regions ("Abroad" counts the roles outside Israel).',
    inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true },
    execute: () => {
      const counts = (names, of) =>
        names.map((name) => ({ name, roles: JOBS.filter((j) => (name === ABROAD ? !j.il && !j.ru : j.il && of(j).includes(name))).length }));
      return Promise.resolve(
        reply({
          levels: counts(LEVELS, (j) => [j.lv]),
          roleTypes: counts(ROLE_TYPES, (j) => j.rt),
          regions: counts(CHOICES.regions, (j) => j.rg),
        }),
      );
    },
  },
  {
    name: 'list_new_jobs',
    description: 'List roles first seen in the last 7 days, newest first.',
    inputSchema: { type: 'object', properties: { limit: { type: 'integer', minimum: 1, maximum: 100 } } },
    annotations: { readOnlyHint: true },
    execute: (a) => Promise.resolve(reply(search({ newOnly: true, limit: (a ?? {}).limit }))),
  },
  {
    name: 'get_job',
    description: 'Get one role by id.',
    inputSchema: idSchema,
    annotations: { readOnlyHint: true },
    execute: (a) => {
      const j = find((a ?? {}).id);
      return j ? Promise.resolve(reply(describe(j))) : unknownJob();
    },
  },
  {
    name: 'discard_job',
    description:
      'Mark a role as not interesting and hide it from the feed. A starred role loses its star (a role is starred or discarded, never both). Saved in this browser, or in the account when the user is signed in; "saved" is false, with a "failure" of "later" (kept on this device, tried again) or "limit" or "refused" (not saved and undone), when it was not saved to the account.',
    inputSchema: idSchema,
    execute: async (a) => {
      const j = find((a ?? {}).id);
      if (!j) return unknownJob();
      const result = await choose(j, 'discard');
      return reply({ ...describe(j), ...result });
    },
  },
  {
    name: 'restore_job',
    description: 'Bring a discarded role back into the feed. A role that is not discarded is left as it is.',
    inputSchema: idSchema,
    execute: async (a) => {
      const j = find((a ?? {}).id);
      if (!j) return unknownJob();
      const result = isDiscarded(key(j)) ? await choose(j, null) : { saved: true, failure: null };
      return reply({ ...describe(j), ...result });
    },
  },
  {
    name: 'star_job',
    description:
      'Star a role the user is interested in. A discarded role loses its discard (a role is starred or discarded, never both). Saved in this browser, or in the account when the user is signed in; "saved" is false, with a "failure" of "later" (kept on this device, tried again) or "limit" or "refused" (not saved and undone), when it was not saved to the account.',
    inputSchema: idSchema,
    execute: async (a) => {
      const j = find((a ?? {}).id);
      if (!j) return unknownJob();
      const result = await choose(j, 'star');
      return reply({ ...describe(j), ...result });
    },
  },
  {
    name: 'unstar_job',
    description: 'Remove the star from a role. A role that is not starred is left as it is. Works for a closed starred role too (see list_starred_jobs).',
    inputSchema: idSchema,
    execute: async (a) => {
      const id = (a ?? {}).id;
      const item = typeof id === 'string' ? itemFor(id) : undefined;
      if (!item) return unknownJob();
      const result = isStarred(id) ? await choose(item, null) : { saved: true, failure: null };
      return reply({ ...describeStarred(item), starred: isStarred(id), ...result });
    },
  },
  {
    name: 'list_starred_jobs',
    description:
      'List the roles the user starred, with `listed` false for a starred role that has closed (it keeps its title, company, location and link). Filters do not apply.',
    inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true },
    execute: () => {
      const items = starredItems();
      return Promise.resolve(reply({ total: items.length, jobs: items.map(describeStarred) }));
    },
  },
  {
    name: 'get_sync_status',
    description:
      'Say where the user\'s stars, discards and filters are saved (this browser, or the account when signed in), whether the filters are in step with the account, how many changes still wait to be saved, and what the first-sign-in offer would bring over from this browser.',
    inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true },
    execute: () => {
      const offer = account?.offer;
      return Promise.resolve(
        reply({
          signedIn: Boolean(account),
          choices: account ? account.choicesState : 'browser',
          filters: filtersState(),
          pendingChanges: (account?.pending.size ?? 0) + (account?.prefs.pending ? 1 : 0),
          filtersInUse: F,
          offer: offer ? { discards: offer.discards.length, stars: offer.stars.length, filters: offer.filters } : null,
        }),
      );
    },
  },
  {
    name: 'import_browser_choices',
    description:
      'Answer the offer made at the first sign-in: copy this browser\'s discards and stars (and, when `filters` is true, its filters, which replace any saved in the account) into the account. Choices the account already has are never changed. Only works while an offer is waiting (see get_sync_status); the user asked for it by calling this.',
    inputSchema: { type: 'object', properties: { filters: { type: 'boolean', description: 'Also bring the browser\'s filters.' } } },
    execute: async (a) => {
      if (!account?.offer) return reply({ error: 'There is nothing to bring over: no offer is waiting.' }, true);
      await answerOffer(account, { choices: true, filters: Boolean((a ?? {}).filters) });
      return reply({ saved: account?.failure === null, failure: account?.failure ?? null, result: account?.result ?? '' });
    },
  },
  {
    name: 'list_coverage_gaps',
    description:
      'List the companies we do not fully crawl, grouped by reason (groups, each with a count and its companies), so their roles may be missing or old.',
    inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true },
    execute: () => Promise.resolve(reply(gaps)),
  },
];

const modelContext = navigator.modelContext;
if (modelContext) {
  try {
    if (typeof modelContext.registerTool === 'function') {
      for (const tool of TOOLS) modelContext.registerTool(tool);
    } else if (typeof modelContext.provideContext === 'function') {
      modelContext.provideContext({ tools: TOOLS });
    }
  } catch (error) {
    console.warn('WebMCP registration failed', error);
  }
}

/* The four filters the daily email copies (regions, levels, role types and keywords, never the rest). digest.js asks for
   them when the user presses "Use the page filters now"; it is a read-only copy taken at that moment, so later
   changes to the page's filters, such as a quick search, never reach the email by themselves. */
globalThis.misraEmailFilters = () => ({
  regions: F.regions.filter((region) => region !== ABROAD), /* the email carries roles in Israel only */
  levels: [...F.levels],
  role_types: [...F.types],
  keywords: F.q.trim().slice(0, MAX_QUERY),
});

sync();
renderAccount();
render();
ready = true;

/* The account may already be known (auth.js is fast) or may come later; either way the page follows it. The handler
   waits a tick: it runs inside the sign-in library's own callback, which must not be waited on. */
document.addEventListener('misra:account', () => {
  setTimeout(onAccount, 0);
});
globalThis.addEventListener('online', () => {
  if (account) retryNow();
});
onAccount();
