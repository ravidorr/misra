/* Dashboard script. Loaded as a deferred module by docs/index.html; the roles come from jobs.json. */
const $ = (id) => document.getElementById(id);
const LS_D = 'il-jobs-discarded-v1';
const LS_F = 'il-jobs-filters-v1';
const LS_T = 'misra-theme';
const CATS = { pm: 'product', intern: 'intern', junior: 'junior', other: 'other' };
const KIND = { pm: 'Product', intern: 'Intern', junior: 'Junior', other: 'Other' };
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

async function loadData() {
  try {
    const response = await fetch('jobs.json');
    if (!response.ok) throw new Error(`jobs.json: HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    $('summary').textContent = 'Could not load the roles. Reload the page to try again.';
    throw error;
  }
}

const DATA = await loadData();
const JOBS = DATA.jobs;
let page = 0;
let ready = false;
let announceTimer;
const gone = lsGet(LS_D, {});
const defaults = () => ({
  q: '',
  company: '',
  loc: 'il',
  sort: 'new',
  cats: [],
  onlyNew: false,
  showGone: false,
  hideStale: false,
  size: 10,
});
let F = { ...defaults(), ...lsGet(LS_F, {}) };

const key = (j) => `${j.c}|${j.i}`;

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
$('upd').textContent = DATA.generated;
$('upd').setAttribute('datetime', DATA.generated.replace(' UTC', 'Z').replace(' ', 'T'));

function select(f) {
  const terms = f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const out = JOBS.filter((j) => {
    if (gone[key(j)] && !f.showGone) return false;
    if (f.loc === 'il' && !j.il) return false;
    if (f.company && j.c !== f.company) return false;
    if (f.cats.length > 0 && !f.cats.includes(j.k)) return false;
    if (f.onlyNew && !fresh(j)) return false;
    if (f.hideStale && j.s) return false;
    if (terms.length > 0) {
      const hay = `${j.c} ${j.t} ${j.m} ${j.l}`.toLowerCase();
      return terms.every((term) => hay.includes(term));
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
  $('q').value = F.q;
  $('company').value = F.company;
  $('loc').value = F.loc;
  $('sort').value = F.sort;
  $('hideStale').checked = F.hideStale;
  $('showGone').checked = F.showGone;
  $('other').checked = F.cats.length === 1 && F.cats[0] === 'other';
  $('onlyNew').setAttribute('aria-pressed', String(F.onlyNew));
  for (const chip of document.querySelectorAll('.chip[data-cat]')) {
    const cat = chip.dataset.cat;
    chip.setAttribute('aria-pressed', String(cat === 'all' ? F.cats.length === 0 : F.cats.includes(cat)));
  }
}

function renderStories() {
  const counts = {};
  for (const j of JOBS) {
    if (j.il && fresh(j) && !gone[key(j)]) counts[j.c] = (counts[j.c] ?? 0) + 1;
  }
  const names = Object.keys(counts)
    .sort((a, b) => counts[b] - counts[a] || a.localeCompare(b))
    .slice(0, 15);
  $('hiring').hidden = names.length === 0;
  $('stories').replaceChildren(
    ...names.map((company) => {
      const n = counts[company];
      const button = h(
        'button',
        {
          type: 'button',
          class: 'story story-new',
          'data-company': company,
          'aria-pressed': String(F.company === company),
        },
        [
          h('span', { class: 'story-badge', 'aria-hidden': 'true', text: company.charAt(0).toUpperCase() }),
          h('span', { class: 'story-name', text: company }),
          h('span', { class: 'sr-only', text: `, ${n} new ${n === 1 ? 'role' : 'roles'}` }),
        ],
      );
      return h('li', {}, [button]);
    }),
  );
}

const NEW_TAB = ' (opens in a new tab)';

function cardEl(j) {
  const k = key(j);
  const discarded = Boolean(gone[k]);
  const isNew = fresh(j);
  const what = `${j.t} at ${j.c}`;
  const url = safeUrl(j.u);
  const art = h('article', {
    class: 'card',
    'data-job-id': k,
    'data-company': j.c,
    'data-category': CATS[j.k],
    'data-new': String(isNew),
    'data-stale': String(j.s),
    'data-discarded': String(discarded),
  });
  if (j.s) {
    art.append(
      h('span', {
        class: 'sticker sticker-warn card-flag',
        title: 'This company could not be crawled in the latest run. The listing may be out of date.',
        text: 'OLD?',
      }),
    );
  } else if (isNew) {
    art.append(h('span', { class: 'sticker card-flag', text: 'NEW' }));
  }
  art.append(
    h('p', { class: 'card-company', text: j.c }),
    h('h3', { class: 'card-title' }, [
      h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, [
        j.t,
        h('span', { class: 'sr-only', text: NEW_TAB }),
      ]),
    ]),
    h('p', { class: 'card-tags' }, [
      j.l && h('span', { class: 'pill', text: j.l }),
      j.k !== 'other' && h('span', { class: 'pill pill-soft', text: KIND[j.k] }),
      j.m && h('span', { class: 'pill', text: j.m }),
      j.f && h('time', { class: 'card-seen', datetime: j.f, text: ago(j.f) }),
    ]),
    h('p', { class: 'card-actions' }, [
      h('a', { class: 'btn btn-primary', href: url, target: '_blank', rel: 'noopener noreferrer' }, [
        'Apply ',
        h('span', { 'aria-hidden': 'true', text: '↗' }),
        h('span', { class: 'sr-only', text: ` to ${what}${NEW_TAB}` }),
      ]),
      h('button', {
        type: 'button',
        class: 'btn btn-link',
        'data-act': 'toggle',
        'data-k': k,
        'aria-label': `${discarded ? 'Restore' : 'Not for me'}: ${what}`,
        text: discarded ? 'Restore' : 'Not for me',
      }),
    ]),
  );
  return h('li', {}, [art]);
}

function goneCount() {
  const known = new Set(JOBS.map(key));
  return Object.keys(gone).filter((k) => known.has(k)).length;
}

/* The visible summary changes on every keystroke; screen readers hear it once typing pauses. */
function announce(text) {
  clearTimeout(announceTimer);
  if (!ready) return;
  announceTimer = setTimeout(() => {
    $('status').textContent = text;
  }, ANNOUNCE_DELAY_MS);
}

function render(focusIdx) {
  const res = select(F);
  const goneN = goneCount();
  const israel = JOBS.filter((j) => j.il).length;
  const inScope = F.loc === 'il' ? israel : JOBS.length;
  $('goneN').textContent = goneN;
  $('showGone').disabled = goneN === 0;
  const newN = JOBS.filter((j) => j.il && fresh(j) && !gone[key(j)]).length;
  $('heroTop').textContent = newN ? `${newN} new` : `${israel} open`;
  $('heroBottom').textContent = newN ? 'roles this week.' : 'roles in Israel.';
  const summary = `Showing ${res.length} of ${inScope} open roles ${F.loc === 'il' ? 'in Israel' : 'in all locations'}. ${goneN} discarded.`;
  $('summary').textContent = summary;
  announce(summary);
  renderStories();
  const list = $('list');
  if (res.length === 0) {
    list.replaceChildren(h('li', { class: 'empty', text: 'No roles match these filters.' }));
    $('pager').hidden = true;
    return;
  }
  const size = SIZES.includes(Number(F.size)) ? Number(F.size) : 10;
  const pages = Math.ceil(res.length / size);
  page = Math.min(page, pages - 1);
  list.replaceChildren(...res.slice(page * size, (page + 1) * size).map(cardEl));
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
  lsSet(LS_F, F);
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
$('loc').addEventListener('change', (e) => {
  F.loc = e.target.value;
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
$('other').addEventListener('change', (e) => {
  F.cats = e.target.checked ? ['other'] : [];
  update();
});
$('onlyNew').addEventListener('click', () => {
  F.onlyNew = !F.onlyNew;
  update();
});
for (const chip of document.querySelectorAll('.chip[data-cat]')) {
  chip.addEventListener('click', () => {
    const cat = chip.dataset.cat;
    if (cat === 'all') F.cats = [];
    else if (F.cats.includes(cat)) F.cats = F.cats.filter((c) => c !== cat);
    else F.cats = [...F.cats.filter((c) => c !== 'other'), cat];
    update();
  });
}
$('stories').addEventListener('click', (e) => {
  const button = e.target.closest('.story');
  if (!button) return;
  F.company = F.company === button.dataset.company ? '' : button.dataset.company;
  update();
});
$('reset').addEventListener('click', () => {
  F = defaults();
  update();
});
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

function setGone(k, on) {
  if (on) gone[k] = TODAY;
  else Reflect.deleteProperty(gone, k);
  lsSet(LS_D, gone);
}

$('list').addEventListener('click', (e) => {
  const button = e.target.closest('[data-act="toggle"]');
  if (!button) return;
  const index = [...$('list').querySelectorAll('[data-act]')].indexOf(button);
  setGone(button.dataset.k, !gone[button.dataset.k]);
  render(index);
});

$('exp').addEventListener('click', () => {
  const link = h('a', { download: 'discarded-jobs.json' });
  link.href = URL.createObjectURL(new Blob([JSON.stringify(gone, null, 1)], { type: 'application/json' }));
  link.click();
  URL.revokeObjectURL(link.href);
});
$('imp').addEventListener('click', () => {
  $('file').click();
});
$('file').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const imported = JSON.parse(reader.result);
      if (imported && typeof imported === 'object') {
        Object.assign(gone, imported);
        lsSet(LS_D, gone);
        render();
      }
    } catch {
      alert('Not a valid discards file');
    }
  };
  reader.readAsText(file);
});

const gaps = DATA.gaps ?? { blocked: [], untracked: [] };
if (gaps.blocked.length > 0 || gaps.untracked.length > 0) {
  const el = $('gaps');
  $('gaps-title').textContent =
    `Coverage gaps: ${gaps.blocked.length} failing in the latest run, ${gaps.untracked.length} not tracked yet`;
  for (const x of gaps.blocked) {
    el.append(
      h('p', {}, [
        h('b', { text: x.c }),
        `: ${x.why}. Its roles are possibly old (last good crawl ${x.last}).`,
      ]),
    );
  }
  if (gaps.untracked.length > 0) {
    el.append(
      h('p', {}, [h('b', { text: 'Not tracked (no usable job feed found):' }), ` ${gaps.untracked.join(', ')}`]),
    );
  }
  el.hidden = false;
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
    category: CATS[j.k],
    isNew: fresh(j),
    possiblyOld: j.s,
    discarded: Boolean(gone[key(j)]),
  };
}
const reply = (obj, isError = false) => ({ content: [{ type: 'text', text: JSON.stringify(obj) }], isError });
const find = (id) => JOBS.find((j) => key(j) === id);

function search(a) {
  const f = defaults();
  f.q = a.query ?? '';
  f.company = a.company ?? '';
  f.onlyNew = Boolean(a.newOnly);
  f.showGone = Boolean(a.includeDiscarded);
  f.loc = a.allLocations ? 'all' : 'il';
  f.sort = { newest: 'new', company: 'co', title: 'title' }[a.sort] ?? 'new';
  if (a.category) f.cats = Object.keys(CATS).filter((k) => CATS[k] === a.category);
  const res = select(f);
  const n = Math.max(1, Math.min(100, Math.floor(a.limit ?? 20)));
  return { total: res.length, returned: Math.min(n, res.length), jobs: res.slice(0, n).map(describe) };
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
        category: { type: 'string', enum: ['product', 'intern', 'junior', 'other'] },
        company: { type: 'string', description: 'Exact company name.' },
        newOnly: { type: 'boolean', description: 'Only roles first seen in the last 7 days.' },
        includeDiscarded: { type: 'boolean' },
        allLocations: { type: 'boolean', description: 'Include roles outside Israel.' },
        sort: { type: 'string', enum: ['newest', 'company', 'title'] },
        limit: { type: 'integer', minimum: 1, maximum: 100, description: 'Default 20.' },
      },
    },
    annotations: { readOnlyHint: true },
    execute: (a) => Promise.resolve(reply(search(a ?? {}))),
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
    description: 'Mark a role as not interesting. Saved in this browser and hidden from the feed.',
    inputSchema: idSchema,
    execute: (a) => {
      const j = find((a ?? {}).id);
      if (!j) return unknownJob();
      setGone(key(j), true);
      render();
      return Promise.resolve(reply(describe(j)));
    },
  },
  {
    name: 'restore_job',
    description: 'Bring a discarded role back into the feed.',
    inputSchema: idSchema,
    execute: (a) => {
      const j = find((a ?? {}).id);
      if (!j) return unknownJob();
      setGone(key(j), false);
      render();
      return Promise.resolve(reply(describe(j)));
    },
  },
  {
    name: 'list_coverage_gaps',
    description: 'List companies that could not be crawled or are not tracked, so their roles may be missing or old.',
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

sync();
render();
ready = true;
