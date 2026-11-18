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
const MESSAGES = {
  expired: 'Your sign-in expired.',
  loading: 'Loading…',
  failed: 'Could not load this area.',
  retry: 'Try again',
};
/* The page title of each area, and what loads it. A loader resolves to true when it showed something; none asks the
   database for anything yet, so each area is empty until its step lands. */
const AREAS = {
  crawl: { title: 'Crawl', load: async () => false },
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
      await client.auth.refreshSession();
      return await check(false);
    }
    if (error) throw error;
    return data === true ? 'ready' : 'not-admin';
  } catch (error) {
    console.error(error);
    return 'check-failed';
  }
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
  const result = await check(true);
  if (result !== 'expired') {
    setState(result);
    return;
  }
  storage.removeItem(STORAGE_KEY); /* a session the service refuses is no use to the dashboard either */
  setState('signed-out');
  $('adminExpired').textContent = MESSAGES.expired; /* after the line is shown, so a screen reader announces it */
}

await start();
