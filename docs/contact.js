/* The form of the Contact page (templates/contact.html). A visitor, signed in or not, writes to the maintainer; the
   `contact` Edge Function (supabase/functions/contact/) emails the message. The page is built with this script only
   while CONTACT_FORM_ENABLED in build_dashboard.py is on, and the function
   answers 404 until its own secret is on.

   The call carries no key and no token: the function is public (verify_jwt = false) and checks the page's origin itself.
   Nothing here is a secret and nothing is stored in the browser. The checks below mirror the function's limits so a
   person gets a helpful message before sending, but the function stays the authority: the page says "sent" only for a
   200 with `{ ok: true }`. There is deliberately no WebMCP tool for this form: a message is an email to the maintainer,
   so only a person who has read the form may send it. The form starts hidden and is shown here, so without
   JavaScript the visitor sees the note that points to GitHub, not a form that cannot send. */
const $ = (id) => document.getElementById(id);
const ENDPOINT = 'https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/contact';
const TIMEOUT_MS = 35000; /* longer than the function's own worst case: 10 s for its database plus 10 s for the email service */
const LIMITS = { name: 100, email: 254, message: 2000 };
const FIELDS = ['name', 'email', 'message'];
const loadedAt = performance.now();
const NOT_FOUND = 404; /* the function is deployed but its switch is off, or it is not deployed */
const MESSAGES = {
  unreachable: 'We could not reach the service, so your message was most likely not sent. Check your connection and try again.',
  slow: "The service took too long to answer, so we cannot tell whether your message arrived. Wait a minute, then try again. If the first one did arrive, we get the message twice and the repeat counts toward today's limit.",
  unclear: 'We could not confirm that your message was sent. Try again in a few minutes.',
  invalid: 'Some details were not accepted, so nothing was sent. Check the fields below and try again.',
  request: 'Your message could not be read, so nothing was sent. Reload the page and try again.',
  origin: 'Messages can only be sent from the live page, so nothing was sent. Open the page from its normal address and try again.',
  large: 'The message is too large, so nothing was sent. Shorten the text and try again.',
  limited: 'You have sent as many messages as are allowed today, so this one was not sent. Try again tomorrow.',
  paused: 'Messages are paused until tomorrow: we have received as many as we can read today, so this one was not sent. Try again tomorrow.',
  refused: 'We could not confirm that your message was passed on to us. Try again later, or open an issue on GitHub (see below).',
  server: 'Something went wrong on our side, so nothing was sent. Try again later.',
  fast: 'Your message came too soon, so it was not sent. Wait a few seconds and try again.',
  closed: 'The contact form is not available right now, so your message was not sent. Open an issue on GitHub (see below).',
};
const BY_CODE = {
  invalid_input: 'invalid',
  too_fast: 'fast',
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
/* The same checks as supabase/functions/contact/validate.ts, with the same words, on the same cleaned text. Each
   returns a message, or '' when the value is fine. The function stays the authority. */
const UNSAFE = /(?![\t\n])[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/gu;
const ADDRESS = /^[^\s<>@,;"'()[\]\\:]+@[^\s<>@,;"'()[\]\\:]+\.[^\s<>@,;"'()[\]\\:]+$/u;
const clean = (value) => value.normalize('NFC').replace(/\r\n?/g, '\n').replace(UNSAFE, '');
const CHECKS = {
  name: (value) =>
    clean(value).replace(/\s+/gu, ' ').trim().length > LIMITS.name ? `The name can be at most ${LIMITS.name} characters.` : '',
  email: (value) => {
    const text = value.trim();
    if (text.length > LIMITS.email) return `The address can be at most ${LIMITS.email} characters.`;
    return text && !ADDRESS.test(text) ? 'Enter one email address, like name@example.com.' : '';
  },
  message: (value) => {
    const text = clean(value).replace(/[^\S\n]+\n/gu, '\n').replace(/\n{3,}/gu, '\n\n').trim();
    if (!text) return 'Enter your message.';
    return text.length > LIMITS.message ? `The message can be at most ${LIMITS.message} characters.` : '';
  },
};

const form = $('contactForm');
if (form) {
  const send = $('contactSend');
  const problem = $('contactError');
  const done = $('contactDone');
  const trap = $('contactWebsite');
  const inputs = Object.fromEntries(FIELDS.map((name) => [name, $(`contact${name[0].toUpperCase()}${name.slice(1)}`)]));
  const SEND_TEXT = send.textContent;
  let sending = false;

  function setSending(on) {
    sending = on;
    send.setAttribute('aria-disabled', String(on)); /* not "disabled": focus stays */
    send.setAttribute('aria-busy', String(on));
    send.textContent = on ? 'Sending…' : SEND_TEXT;
  }

  function fieldError(name, message) {
    const box = $(`contact${name[0].toUpperCase()}${name.slice(1)}Error`);
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

  /* Asks the function. Resolves with { sent: true }, or with the words to show and the fields it faulted. */
  async function request(body) {
    let response;
    try {
      response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      return { message: error?.name === 'TimeoutError' ? MESSAGES.slow : MESSAGES.unreachable };
    }
    const answer = await response.json().catch(() => null);
    if (response.ok && answer?.ok === true) return { sent: true };
    if (response.ok) return { message: MESSAGES.unclear }; /* a 200 that is not the function's answer: never "sent" */
    /* Only the function's own code says what happened: a status alone can come from the platform in front of it, and then
       we cannot say whether the message was sent. */
    const key = BY_CODE[answer?.error?.code] ?? (response.status === NOT_FOUND ? 'closed' : 'unclear');
    return { message: MESSAGES[key], fields: key === 'invalid' ? answer?.error?.fields : null };
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
      invalid.focus();
      return;
    }
    setSending(true);
    let outcome;
    try {
      /* `website` is the honeypot (people never see it; a bot that fills it is told "sent" and ignored); `elapsed` is the
         time on the page, which the function uses to drop a submit no person could have made. */
      outcome = await request({
        name: inputs.name.value,
        email: inputs.email.value,
        message: inputs.message.value,
        website: trap.value,
        elapsed: Math.round(performance.now() - loadedAt),
      });
    } catch (error) {
      console.error(error);
      outcome = { message: MESSAGES.unclear };
    }
    setSending(false);
    if (outcome.sent) {
      form.hidden = true;
      done.hidden = false;
      $('contactDoneTitle').focus();
      return;
    }
    send.textContent = 'Try again';
    problem.textContent = outcome.message;
    problem.hidden = false;
    (showServerFields(outcome.fields) ?? problem).focus();
  });

  $('contactFallback').hidden = true;
  form.hidden = false;
}
