/* "Add a company" (PLAN.md step 5.2, PRD FR-7). A visitor, signed in or not, tells the maintainer about a company whose
   careers page is missing from the crawl. The form lives in a native dialog opened from the coverage gap card; its button
   asks the `report-site` Edge Function (supabase/functions/report-site/), which emails the report. It is a separate module
   on purpose: it needs no account and no other script, and a failure here can never stop the roles from loading.

   The call carries no key and no token: the function is public (verify_jwt = false) and checks the page's origin itself.
   Nothing here is a secret, and nothing is stored in the browser. The checks below mirror the function's limits so a
   person gets a helpful message before sending, but the function stays the authority and its answer is what counts: the
   page says "sent" only for a 200 with `{ ok: true }`.

   There is deliberately no WebMCP tool for this form: a report is an email to the maintainer, so only a person who has
   read the form may send it. Agents can find the action and the form through the data-report hooks. */
const $ = (id) => document.getElementById(id);
const ENDPOINT = 'https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/report-site';
const TIMEOUT_MS = 35000; /* longer than the function's own worst case: 10 s for its database plus 10 s for the email service */
const LIMITS = { company: 100, link: 500, why: 1000 };
const STATUS = { badRequest: 400, forbidden: 403, tooLarge: 413, limited: 429, unavailable: 503, failed: 502 };
const FIELDS = ['company', 'link', 'why'];
const MESSAGES = {
  unreachable: 'We could not reach the service, so your report was most likely not sent. Check your connection and try again.',
  slow: "The service took too long to answer, so we cannot tell whether your report arrived. Wait a minute, then try again. If the first one did arrive, we get the report twice and the repeat counts toward today's limit.",
  unclear: 'We could not confirm that your report was sent. Try again in a few minutes.',
  invalid: 'Some details were not accepted, so nothing was sent. Check the fields below and try again.',
  request: 'Your report could not be read, so nothing was sent. Reload the page and try again.',
  origin: 'Reports can only be sent from the live page, so nothing was sent. Open the page from its normal address and try again.',
  large: 'The report is too large, so nothing was sent. Shorten the text and try again.',
  limited: 'You have sent as many reports as are allowed today, so this one was not sent. Try again tomorrow.',
  paused: 'Reports are paused until tomorrow: we have received as many as we can read today, so this one was not sent. Try again tomorrow.',
  refused: 'We could not confirm that your report was passed on to us. Try again in a few minutes.',
  server: 'Something went wrong on our side, so nothing was sent. Try again later.',
};

/* What the visitor sees for each code the function can answer with (and, failing a code, for the status). */
const BY_CODE = {
  invalid_input: 'invalid',
  invalid_request: 'request',
  origin_not_allowed: 'origin',
  method_not_allowed: 'server',
  payload_too_large: 'large',
  unsupported_media_type: 'server',
  rate_limited: 'limited',
  daily_cap_reached: 'paused',
  send_failed: 'refused',
  server_error: 'server',
};
const BY_STATUS = {
  [STATUS.badRequest]: 'invalid',
  [STATUS.forbidden]: 'origin',
  [STATUS.tooLarge]: 'large',
  [STATUS.limited]: 'limited',
  [STATUS.unavailable]: 'paused',
  [STATUS.failed]: 'refused',
};

/* The same checks as supabase/functions/report-site/validate.ts, with the same words, on the same cleaned text (control,
   line-separator and bidirectional characters removed first). Each returns a message, or '' when the value is fine. The
   function stays the authority. */
const UNSAFE = /(?![\t\n])[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/gu;
const clean = (value) => value.normalize('NFC').replace(/\r\n?/g, '\n').replace(UNSAFE, '');
function checkCompany(value) {
  const text = clean(value).replace(/\s+/gu, ' ').trim();
  if (text.length > LIMITS.company) return `The name can be at most ${LIMITS.company} characters.`;
  return '';
}

function checkLink(value) {
  const text = value.trim();
  if (!text) return 'Enter the link to the career page.';
  if (text.length > LIMITS.link) return `The link can be at most ${LIMITS.link} characters.`;
  if (/[\s\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/u.test(text)) return 'The link cannot contain spaces or line breaks.';
  let url;
  try {
    url = new URL(text);
  } catch {
    return 'Enter a full link that starts with https:// or http://.';
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return 'The link must start with https:// or http://.';
  if (url.username || url.password) return 'The link cannot contain a user name or password.';
  if (!url.hostname.includes('.') && !url.hostname.includes(':')) return 'Enter a link to a public website.';
  return '';
}

function checkWhy(value) {
  const text = clean(value).replace(/[^\S\n]+\n/gu, '\n').replace(/\n{3,}/gu, '\n\n').trim();
  if (text.length > LIMITS.why) return `The reason can be at most ${LIMITS.why} characters.`;
  return '';
}

const CHECKS = { company: checkCompany, link: checkLink, why: checkWhy };

const open = $('reportOpen');
const dialog = $('reportDialog');
if (open && dialog) {
  const form = $('reportForm');
  const send = $('reportSend');
  const cancel = $('reportCancel');
  const problem = $('reportError');
  const done = $('reportDone');
  const inputs = Object.fromEntries(FIELDS.map((name) => [name, $(`report${name[0].toUpperCase()}${name.slice(1)}`)]));
  const trap = $('reportWebsite');
  const SEND_TEXT = send.textContent;
  let sending = false;

  function setState(state) {
    dialog.dataset.state = state;
  }

  function setSending(on) {
    sending = on;
    for (const button of [send, cancel, $('reportClose')]) button.setAttribute('aria-disabled', String(on)); /* not "disabled": focus stays */
    send.setAttribute('aria-busy', String(on));
    send.textContent = on ? 'Sending…' : SEND_TEXT;
    setState(on ? 'sending' : 'idle');
  }

  function fieldError(name, message) {
    const box = $(`report${name[0].toUpperCase()}${name.slice(1)}Error`);
    box.textContent = message;
    box.hidden = !message;
    if (message) inputs[name].setAttribute('aria-invalid', 'true');
    else inputs[name].removeAttribute('aria-invalid');
  }

  /* Shows what is wrong and returns the first field that needs another look (or null). */
  function validate(only = FIELDS) {
    let first = null;
    for (const name of only) {
      const message = CHECKS[name](inputs[name].value);
      fieldError(name, message);
      if (message && !first) first = inputs[name];
    }
    return first;
  }

  function showProblem(text) {
    problem.textContent = text;
    problem.hidden = false;
  }

  function reset() {
    form.reset();
    for (const name of FIELDS) fieldError(name, '');
    problem.hidden = true;
    problem.textContent = '';
    form.hidden = false;
    done.hidden = true;
    $('reportTitle').hidden = false;
    setSending(false);
  }

  /* Asks the function. Resolves with { sent: true }, or with the words to show and the fields it faulted. */
  async function request(report) {
    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(report),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      return { message: error?.name === 'TimeoutError' ? MESSAGES.slow : MESSAGES.unreachable };
    }
    const body = await response.json().catch(() => null);
    if (response.ok && body?.ok === true) return { sent: true };
    if (response.ok) return { message: MESSAGES.unclear }; /* a 200 that is not the function's answer: never "sent" */
    const key = BY_CODE[body?.error?.code] ?? BY_STATUS[response.status] ?? 'unclear';
    return { message: MESSAGES[key], fields: key === 'invalid' ? body?.error?.fields : null };
  }

  /* The function's own words for a field it faulted, shown beside that field. */
  function showServerFields(fields) {
    let first = null;
    for (const name of FIELDS) {
      const message = typeof fields?.[name] === 'string' ? fields[name] : '';
      if (!message) continue;
      fieldError(name, message);
      first ??= inputs[name];
    }
    return first;
  }

  function finish() {
    form.hidden = true;
    $('reportTitle').hidden = true;
    done.hidden = false;
    setState('sent');
    $('reportDoneTitle').focus();
  }

  open.hidden = false;
  open.addEventListener('click', () => {
    if (sending) {
      if (!dialog.open) dialog.showModal(); /* a request is still running: do not reset it or start another */
      return;
    }
    reset();
    dialog.showModal();
    inputs.company.focus();
  });

  cancel.addEventListener('click', () => {
    if (!sending) dialog.close();
  });
  $('reportClose').addEventListener('click', () => {
    if (!sending) dialog.close();
  });
  dialog.addEventListener('cancel', (event) => {
    if (sending) event.preventDefault(); /* Esc does not abandon a request in flight */
  });
  dialog.addEventListener('close', () => {
    if (!sending) reset(); /* a closed form keeps nothing: not a draft, not a report that was sent */
  });

  for (const name of FIELDS) {
    inputs[name].addEventListener('input', () => {
      if (inputs[name].hasAttribute('aria-invalid')) validate([name]); /* clears the message as soon as the value is fine */
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (sending) return;
    problem.hidden = true;
    const invalid = validate();
    if (invalid) {
      send.textContent = SEND_TEXT;
      invalid.focus();
      return;
    }
    setSending(true);
    let outcome;
    try {
      /* `website` is the honeypot: people never see it and it goes empty; a bot that fills it is told "sent" and ignored. */
      outcome = await request({
        company: inputs.company.value,
        link: inputs.link.value,
        why: inputs.why.value,
        website: trap.value,
      });
    } catch (error) {
      console.error(error);
      outcome = { message: MESSAGES.unclear };
    }
    setSending(false);
    if (!dialog.open) dialog.showModal(); /* a second Esc can close it while the request runs: the answer must still be seen */
    if (outcome.sent) {
      finish();
      return;
    }
    send.textContent = 'Try again';
    showProblem(outcome.message);
    const faulted = showServerFields(outcome.fields);
    (faulted ?? problem).focus();
  });
}
