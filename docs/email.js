/* The page the emails of the digest link to (PLAN.md step 6.1).
   Confirmation: the link is email.html#token=<token>. A GET never confirms: the page shows a button, and only the click
   POSTs the token to the confirm-email function. The token is read from the fragment (no server ever sees it), removed
   from the address bar at once, and sent in the request body.
   Unsubscribe: the unsubscribe function has already done its work and redirects here with a word in the fragment. */
const FUNCTION_URL = 'https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/confirm-email';
const TOKEN = /^[A-Za-z0-9_-]{43}$/u;
const TIMEOUT_MS = 15000;

const heading = document.getElementById('heading');
const message = document.getElementById('message');
const status = document.getElementById('status');
const actions = document.getElementById('actions');
const button = document.getElementById('confirm');
const back = document.getElementById('back');

const WORDS = {
  unsubscribed: ['You are unsubscribed', 'No more emails will be sent. Your saved email filters were deleted.'],
  'already-unsubscribed': ['You are unsubscribed', 'This was done already. No more emails will be sent.'],
  'link-not-valid': ['This link does not work', 'It is not valid, or it is no longer active. If you still get emails, use the unsubscribe link in the newest one.'],
  'try-again': ['Something went wrong', 'We could not finish this. Please open the link in your email again in a moment.'],
};
const CONFIRM_ERRORS = {
  link_not_valid: ['This link does not work', 'It is not valid, or it is no longer active. Turn the email on again from the dashboard to get a new one.'],
  link_expired: ['This link has expired', 'Turn the email on again from the dashboard to get a new link.'],
  unsubscribed: ['You have unsubscribed', 'Turn the email on again from the dashboard if you want it back.'],
};

function show([title, text], { done = true } = {}) {
  heading.textContent = title;
  message.textContent = text;
  status.textContent = `${title}. ${text}`;
  actions.hidden = true;
  back.hidden = !done;
}

function readFragment() {
  const raw = location.hash.slice(1);
  if (raw) history.replaceState(null, '', location.pathname + location.search);
  return raw;
}

async function confirm(token) {
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(FUNCTION_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ token }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const answer = await response.json().catch(() => ({}));
    if (answer.ok === true) {
      show(['Your email is confirmed', 'The digest will start with the next new roles that match your filters. Every email has a link to unsubscribe.']);
    } else if (CONFIRM_ERRORS[answer.error?.code]) {
      show(CONFIRM_ERRORS[answer.error.code]);
    } else {
      throw new Error('server');
    }
  } catch (error) {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    const text =
      error instanceof Error && error.message === 'server'
        ? 'Something went wrong on our side. Please press Confirm again in a moment.'
        : 'We could not reach the service. Check your connection and press Confirm again.';
    message.textContent = text;
    status.textContent = text;
    console.error('confirm failed:', error instanceof Error ? error.name : 'unknown'); // never the token or the answer
  }
}

const fragment = readFragment();
const token = fragment.startsWith('token=') ? fragment.slice('token='.length) : '';
if (TOKEN.test(token)) {
  show(['Confirm your email', 'Press the button to start the Misra digest by email. Nothing is sent until you do.'], { done: false });
  actions.hidden = false;
  button.addEventListener('click', () => {
    void confirm(token);
  });
} else if (Object.hasOwn(WORDS, fragment)) {
  show(WORDS[fragment]);
} else {
  show(['This link is incomplete', 'Open the link from your email again.']);
}
