/* The "Daily email" settings (PLAN.md step 6.2, PRD FR-8 AC 1 to 3), for signed-in users only. A native dialog opened by the
   "Daily email" button next to "Sign out". It is a separate module on purpose, like report.js: the roles never wait for
   it, and a failure here cannot stop them. The build leaves it out of the page entirely while the signup switch is off
   (DIGEST_SIGNUP_ENABLED in build_dashboard.py).

   What the page does and what the service decides. The settings live in the user's own `subscriptions` row, which the
   user can read (never the token hashes) and, for `frequency` and `filters` only, update through row-level security.
   Everything that mails a person goes through the `email-settings` Edge Function: turning the email on (address,
   frequency, filters; it sends the confirmation email and enforces the limits) and off (the same clean-up as the
   unsubscribe link). The page says "sent" or "off" only for the function's `ok: true`, and after every attempt it reads
   the row again, so what it shows is what the database holds, never what the page hoped for.

   There is deliberately no WebMCP tool here: turning the email on emails a person, so only a person who read this
   dialog may do it. Agents can read the state from the data-digest hooks (`data-digest-state` on the button and on the
   dialog's state line, `data-digest-frequency`); they carry no address. */
const $ = (id) => document.getElementById(id);
const ENDPOINT = 'https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/email-settings';
const ANON_KEY = 'sb_publishable_9gvh4YdsfErlFsgJfmNexQ_yHF-XwnA';
const TIMEOUT_MS = 30000; /* longer than the function's own worst case: 5 s for Auth, 10 s for the database, 10 s for the email service */
const STATUS = { badRequest: 400, unauthorized: 401, forbidden: 403, limited: 429, unavailable: 503 };
const USER_LIMIT = 3; /* the function's per-user limit a day (supabase/functions/email-settings/handler.ts) */
const MAX_ADDRESS = 254;
const ADDRESS = /^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;
const COLUMNS = 'address,frequency,filters,consent_confirmed_at,unsubscribed_at';
const FILTER_LABELS = [
  ['regions', 'Regions'],
  ['levels', 'Levels'],
  ['role_types', 'Role types'],
];

const MESSAGES = {
  unreachable: 'We could not get an answer from the service, so we cannot be sure what happened. Check your connection and try again. The settings above are read again from your account.',
  slow: 'The service took too long to answer, so we cannot tell whether it went through. The settings above are read again from your account.',
  invalid: 'Some details were not accepted, so nothing was changed. Check the fields below and try again.',
  request: 'The request could not be read, so nothing was changed. Reload the page and try again.',
  origin: 'This can only be done from the live page, so nothing was changed. Open the page from its normal address and try again.',
  signIn: 'Your sign-in was not accepted, so nothing was changed. Sign out, sign in again, and try again.',
  signInDown: 'The sign-in service did not answer, so nothing was changed. Please try again in a minute.',
  limited: `You have already asked for ${USER_LIMIT} confirmation emails today, the most allowed, so nothing was sent. Please try again tomorrow.`,
  addressLimited: 'That address has already been sent as many confirmation emails as are allowed today, so nothing was sent. Please try again tomorrow, or use another address.',
  paused: 'We have sent as many confirmation emails as we can today, so nothing was sent. Please try again tomorrow.',
  closed: 'The email is not open yet, so nothing was turned on.',
  sendFailed: 'The confirmation email could not be sent, so nothing is waiting for you in your inbox, and the email stays paused until you confirm. Press "Send the confirmation again" later.',
  sendUnclear: 'We could not confirm that the email was sent. Wait a few minutes and check your inbox, and your spam folder, before asking again: each request uses one of the confirmation emails allowed today.',
  server: 'Something went wrong on our side. Check the settings above and try again.',
  unclear: 'We could not confirm what happened. The settings above are read again from your account; check them and try again.',
  loadFailed: 'We could not read your email settings. Nothing was changed.',
  saveFailed: 'We could not save that, so the email keeps its earlier settings. Please try again.',
  signedOut: 'You are signed out, so nothing was changed. Sign in and try again.',
  noFilters: 'The roles are still loading, so the filters could not be read. Try again in a moment.',
};

const BY_CODE = {
  invalid_input: 'invalid',
  invalid_request: 'request',
  origin_not_allowed: 'origin',
  method_not_allowed: 'server',
  unsupported_media_type: 'server',
  payload_too_large: 'request',
  not_signed_in: 'signIn',
  service_unavailable: 'signInDown',
  signup_closed: 'closed',
  rate_limited: 'limited',
  address_limited: 'addressLimited',
  daily_cap_reached: 'paused',
  send_failed: 'sendFailed',
  send_unconfirmed: 'sendUnclear',
  server_error: 'server',
};
const BY_STATUS = {
  [STATUS.badRequest]: 'invalid',
  [STATUS.unauthorized]: 'signIn',
  [STATUS.forbidden]: 'origin',
  [STATUS.limited]: 'limited',
  [STATUS.unavailable]: 'paused',
};

const open = $('digestOpen');
const dialog = $('digestDialog');
if (open && dialog) {
  const form = $('digestForm');
  const stateLine = $('digestState');
  const note = $('digestNote');
  const problem = $('digestError');
  const retryRow = $('digestRetryRow');
  const address = $('digestAddress');
  const addressError = $('digestAddressError');
  const filtersList = $('digestFilters');
  const useFilters = $('digestUseFilters');
  const send = $('digestSend');
  const off = $('digestOff');
  const frequencies = [...form.querySelectorAll('input[name="digestFrequency"]')];

  /* `row` is the account's subscription row as last read (null: none, or not read yet), `phase` is loading, ready or failed,
     and `draft` is what "Turn the email on" will send while there is no running subscription (off or unsubscribed). */
  const view = { row: null, phase: 'loading', draft: { frequency: 'daily', filters: emptyFilters() }, busy: false, user: null };

  function emptyFilters() {
    return { regions: [], levels: [], role_types: [], keywords: '' };
  }

  const account = () => globalThis.misraAccount ?? null;

  function stateOf(row) {
    if (view.phase !== 'ready') return view.phase === 'failed' ? 'unknown' : 'loading';
    if (!row) return 'off';
    if (row.unsubscribed_at) return 'unsubscribed';
    return row.consent_confirmed_at ? 'on' : 'waiting';
  }

  const running = () => ['waiting', 'on'].includes(stateOf(view.row));
  const shownFilters = () => (running() ? view.row.filters : view.draft.filters);
  const shownFrequency = () => (running() ? view.row.frequency : view.draft.frequency);

  function describeFilters(filters) {
    const items = FILTER_LABELS.filter(([key]) => Array.isArray(filters?.[key]) && filters[key].length > 0).map(
      ([key, label]) => `${label}: ${filters[key].join(', ')}`,
    );
    if (typeof filters?.keywords === 'string' && filters.keywords) items.push(`Keywords: ${filters.keywords}`);
    return items.length > 0 ? items : ['Every new role (no filters chosen).'];
  }

  function renderFilters() {
    filtersList.replaceChildren(
      ...describeFilters(shownFilters()).map((text) => {
        const item = document.createElement('li');
        item.textContent = text;
        return item;
      }),
    );
  }

  const every = (frequency) => (frequency === 'weekly' ? 'once a week, as a summary' : 'every day there is something new');

  /* The words of each state. They name the address because the user may have typed another one. */
  function stateText(state) {
    const row = view.row;
    switch (state) {
      case 'loading':
        return 'Checking your email settings…';
      case 'unknown':
        return 'We could not read your email settings.';
      case 'on':
        return `The email is on. New roles that match your filters go to ${row.address}, ${every(row.frequency)}.`;
      case 'waiting':
        return `Waiting for you to confirm ${row.address}. Open the link in the confirmation email and press Confirm; until then nothing is sent. The link works for 7 days.`;
      case 'unsubscribed':
        return 'You unsubscribed, so nothing is sent. You can turn the email on again; you will confirm your address again first.';
      default:
        return 'The email is off. Nothing is sent to you.';
    }
  }

  function setBusy(on) {
    view.busy = on;
    for (const button of [send, off, useFilters, $('digestClose'), $('digestRetry')]) {
      button.setAttribute('aria-disabled', String(on)); /* not "disabled": focus stays where it is */
    }
    send.setAttribute('aria-busy', String(on));
    dialog.dataset.busy = String(on);
  }

  function fieldError(text) {
    addressError.textContent = text;
    addressError.hidden = !text;
    if (text) address.setAttribute('aria-invalid', 'true');
    else address.removeAttribute('aria-invalid');
  }

  function showProblem(text) {
    problem.textContent = text;
    problem.hidden = !text;
  }

  function showNote(text) {
    note.textContent = text;
    note.hidden = !text;
  }

  /* Draws the dialog, the button hooks and the form from `view`. `keepAddress` leaves what the user typed. */
  function render({ keepAddress = false } = {}) {
    const state = stateOf(view.row);
    open.dataset.digestState = state;
    dialog.dataset.state = state;
    stateLine.dataset.digestState = state;
    stateLine.textContent = stateText(state);
    const frequency = shownFrequency();
    open.dataset.digestFrequency = running() ? frequency : '';
    const ready = view.phase === 'ready';
    form.hidden = !ready;
    retryRow.hidden = view.phase !== 'failed';
    if (!ready) return;
    if (!keepAddress) address.value = view.row && state !== 'unsubscribed' ? view.row.address : (account()?.email ?? view.row?.address ?? '');
    for (const radio of frequencies) radio.checked = radio.value === frequency;
    renderFilters();
    $('digestConsentTo').textContent = address.value.trim() || account()?.email || 'your address';
    const typed = address.value.trim().toLowerCase();
    const changed = state === 'on' && typed !== '' && typed !== view.row.address.toLowerCase();
    send.hidden = state === 'on' && !changed;
    off.hidden = !running();
    send.textContent = { waiting: 'Send the confirmation again', on: 'Change the address and confirm it' }[state] ?? 'Turn the email on';
    $('digestUseHint').textContent = running()
      ? 'Copies the regions, levels, role types and keywords chosen on the page right now and saves them for the email. Changing the filters on the page later, or searching, does not change the email until you press this again.'
      : 'Copies the regions, levels, role types and keywords chosen on the page right now. Press it before you turn the email on if you want only roles like the ones you see. Changing the filters on the page later does not change the email until you press this again.';
  }

  /* Reads the user's own row (row-level security gives them theirs and no token hash). Returns true when it could. */
  async function refresh() {
    const a = account();
    if (!a) return false;
    view.user = a.userId;
    try {
      const { data, error } = await a.client.from('subscriptions').select(COLUMNS).eq('user_id', a.userId).retry(false);
      if (error) throw error;
      if (account()?.userId !== a.userId) return false; /* signed out or another account meanwhile (a renewed token is the same user) */
      view.row = data.length > 0 ? data[0] : null;
      view.phase = 'ready';
      return true;
    } catch (error) {
      console.error(error);
      if (account()?.userId === a.userId) view.phase = 'failed';
      return false;
    }
  }

  async function load({ keepAddress = false } = {}) {
    view.phase = 'loading';
    render({ keepAddress });
    const ok = await refresh();
    if (account()) {
      showProblem(ok ? '' : MESSAGES.loadFailed);
      render({ keepAddress });
    }
    return ok;
  }

  /* Asks the function. Resolves with { ok: true, body }, or with the words to show (`message`) and the fields it faulted. */
  async function request(body) {
    const a = account();
    const { data, error: tokenError } = a ? await a.client.auth.getSession() : { data: {}, error: null }; /* renews an expired token first */
    if (tokenError) return { message: MESSAGES.unreachable, reread: false }; /* the token could not be renewed: nothing was sent */
    const token = data.session?.access_token;
    if (!token) return { message: MESSAGES.signedOut, reread: false };
    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, apikey: ANON_KEY, 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      /* A timeout, a reset connection or a gateway error without CORS headers may all come after the function did its work,
         so the account is read again whatever the reason. */
      return { message: error?.name === 'TimeoutError' ? MESSAGES.slow : MESSAGES.unreachable };
    }
    const answer = await response.json().catch(() => null);
    if (response.ok && answer?.ok === true) return { ok: true, body: answer };
    if (response.ok) return { message: MESSAGES.unclear }; /* a 200 that is not the function's answer: never "done" */
    const fromFunction = typeof answer?.error?.code === 'string'; /* a gateway's own error says nothing about what the function did */
    const key = BY_CODE[answer?.error?.code] ?? (fromFunction ? BY_STATUS[response.status] : undefined) ?? 'unclear';
    return { message: MESSAGES[key], fields: key === 'invalid' ? answer?.error?.fields : null };
  }

  function checkAddress() {
    const text = address.value.trim();
    if (text === '') return ''; /* the account's own email */
    if (text.length > MAX_ADDRESS) return `The address can be at most ${MAX_ADDRESS} characters.`;
    if (!ADDRESS.test(text)) return 'Enter one email address, like name@example.com.';
    return '';
  }

  /* One action that talks to the function: runs it, then reads the account again and says what the database holds. */
  async function act(body, { success }) {
    if (view.busy) return;
    showProblem('');
    showNote('');
    setBusy(true);
    let outcome;
    try {
      outcome = await request(body);
    } catch (error) {
      console.error(error);
      outcome = { message: MESSAGES.unclear };
    }
    /* Whatever the answer, the account is read again (the function may have stored the settings before a later step failed). */
    const reread = outcome.reread === false ? true : await refresh();
    setBusy(false);
    if (!account()) return;
    if (outcome.ok) showNote(success(outcome.body));
    else {
      showProblem(outcome.message);
      if (outcome.fields?.address) fieldError(outcome.fields.address);
    }
    if (!reread) showProblem(`${problem.textContent ? `${problem.textContent} ` : ''}${MESSAGES.loadFailed}`);
    render({ keepAddress: !outcome.ok });
    if (!dialog.open) dialog.showModal(); /* a second Esc can close it while the request runs: the answer must still be seen */
    if (!problem.hidden) problem.focus();
    else if (document.activeElement === document.body) note.focus(); /* the button that was pressed may have gone away */
  }

  function turnOn() {
    fieldError('');
    const message = checkAddress();
    if (message) {
      fieldError(message);
      address.focus();
      return Promise.resolve();
    }
    const state = stateOf(view.row);
    const typed = address.value.trim();
    const own = account()?.email ?? '';
    const isOwn = typed === '' || typed.toLowerCase() === own.toLowerCase();
    const sentTo = isOwn ? own || 'your address' : typed;
    return act(
      {
        action: 'turn_on',
        address: typed === '' || typed.toLowerCase() === own.toLowerCase() ? null : typed,
        frequency: running() ? view.row.frequency : view.draft.frequency,
        filters: shownFilters(),
      },
      {
        success: (answer) =>
          answer.state === 'on'
            ? 'The email was already on for this address, so nothing was changed and nothing was sent.'
            : `Check ${sentTo} for a confirmation link. Your ${running() ? view.row.frequency : view.draft.frequency} digest will start only after you click it.${state === 'on' ? ' The email stays paused until then.' : ''}`,
      },
    );
  }

  function turnOff() {
    return act(
      { action: 'turn_off' },
      {
        success: (answer) =>
          answer.status === 'not_subscribed'
            ? 'The email was not on, so nothing was changed.'
            : 'The email is off. Nothing more will be sent, and your saved filters were deleted. We keep the address and the times you turned it on, confirmed and off as a record of your consent.',
      },
    );
  }

  /* The user changes `frequency` or `filters` of a running subscription: the account's own row, saved straight to it. */
  async function saveRow(patch) {
    const a = account();
    if (!a || view.busy) return;
    showProblem('');
    showNote('');
    setBusy(true);
    let saved = false;
    try {
      const { data, error } = await a.client.from('subscriptions').update(patch).eq('user_id', a.userId).select('user_id').retry(false);
      if (error) throw error;
      saved = data.length === 1;
    } catch (error) {
      console.error(error);
    }
    const reread = await refresh();
    setBusy(false);
    if (!account()) return;
    if (saved) showNote('Saved. The next email uses this.');
    else showProblem(MESSAGES.saveFailed);
    if (!reread) showProblem(`${problem.textContent ? `${problem.textContent} ` : ''}${MESSAGES.loadFailed}`);
    render({ keepAddress: true });
    if (!problem.hidden) problem.focus();
  }

  function copyFilters() {
    if (view.busy) return;
    const read = globalThis.misraEmailFilters;
    if (typeof read !== 'function') {
      showProblem(MESSAGES.noFilters);
      return;
    }
    showProblem('');
    showNote('');
    const filters = read(); /* a copy taken now: the email never follows the page's filters by itself */
    if (running()) {
      saveRow({ filters });
      return;
    }
    view.draft.filters = filters;
    render({ keepAddress: true });
    showNote('Copied. Press "Turn the email on" to use them.');
  }

  open.addEventListener('click', async () => {
    showProblem('');
    showNote('');
    fieldError('');
    dialog.showModal();
    $('digestTitle').focus();
    await load();
  });

  $('digestClose').addEventListener('click', () => {
    if (!view.busy) dialog.close();
  });
  $('digestRetry').addEventListener('click', () => {
    if (!view.busy) load();
  });
  dialog.addEventListener('cancel', (event) => {
    if (view.busy) event.preventDefault(); /* Esc does not abandon a request in flight */
  });
  dialog.addEventListener('close', () => {
    if (!view.busy) {
      showProblem('');
      showNote('');
      fieldError('');
    }
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!view.busy) turnOn();
  });
  off.addEventListener('click', () => {
    if (!view.busy) turnOff();
  });
  useFilters.addEventListener('click', copyFilters);
  address.addEventListener('input', () => {
    if (address.hasAttribute('aria-invalid')) fieldError(checkAddress());
    render({ keepAddress: true }); /* the "Change the address" button appears once the address differs */
  });
  for (const radio of frequencies) {
    radio.addEventListener('change', () => {
      if (!radio.checked || view.busy) {
        render({ keepAddress: true }); /* a change made while a request runs is not saved: show what the account holds */
        return;
      }
      if (running()) saveRow({ frequency: radio.value });
      else view.draft.frequency = radio.value;
    });
  }

  /* The account comes, changes or goes. Nothing of one user's settings is kept for the next: the draft and the row are
     dropped, and a dialog left open for a signed-out page is closed. */
  document.addEventListener('misra:account', () => {
    setTimeout(async () => {
      const a = account();
      if (a?.userId === view.user && a) return; /* the same user again (a renewed token) */
      view.row = null;
      view.draft = { frequency: 'daily', filters: emptyFilters() };
      view.user = a?.userId ?? null;
      if (!a) {
        view.phase = 'loading';
        if (dialog.open && !view.busy) dialog.close();
        render();
        return;
      }
      await load();
    }, 0);
  });
  if (account()) load();
}
