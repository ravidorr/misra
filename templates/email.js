/* The page the emails of the digest link to (PLAN.md step 6.1).
   Confirmation: the link is email.html#token=<token>. A GET never confirms: the page shows a button, and only the click
   POSTs the token to the confirm-email function. The token is read from the fragment (no server ever sees it), removed
   from the address bar at once, and sent in the request body.
   Unsubscribe: the unsubscribe function has already done its work and redirects here with a word in the fragment. */
const FUNCTION_URL = '__SUPABASE_URL__/functions/v1/confirm-email';
const TOKEN = /^[A-Za-z0-9_-]{43}$/u;
const TIMEOUT_MS = 15000;

const heading = document.getElementById('heading');
const message = document.getElementById('message');
const status = document.getElementById('status');
const actions = document.getElementById('actions');
const button = document.getElementById('confirm');
const back = document.getElementById('back');

const NOT_VALID = [
  'This link is no longer valid',
  'It may have expired (confirmation links work for 7 days), been replaced by a newer link, or belong to job alerts that were turned off. To manage your job alerts or delete your account, sign in on the Misra dashboard.',
];
const UNSUBSCRIBED = [
  'You have been unsubscribed',
  'Your job alerts are stopped. We deleted the filters saved for them and the list of jobs we already emailed you. We keep your address and the times you turned the email on, confirmed it and unsubscribed, as a record of your consent. Your Misra account (stars, "Not for me" choices, and dashboard filters) is unchanged. To delete your entire account and all stored data, sign in on the dashboard and choose "Delete my account".',
];
const WORDS = {
  unsubscribed: UNSUBSCRIBED,
  'already-unsubscribed': UNSUBSCRIBED,
  'link-not-valid': NOT_VALID,
  'try-again': ['Something went wrong', 'We could not finish this. Open the link in your email again in a moment.'],
};
const CONFIRM_ERRORS = {
  link_not_valid: NOT_VALID,
  link_expired: NOT_VALID,
  unsubscribed: NOT_VALID,
};

/** The confirmed state. `schedule` is what the function read from the subscription; without it the text stays true. */
function confirmed(schedule) {
  const which = schedule === 'daily' || schedule === 'weekly' ? `a ${schedule} email` : 'a daily or weekly email, as you chose,';
  return [
    'Your job alerts are confirmed',
    `You now get ${which} when new roles match your saved filters. Your first email includes only roles Misra finds from now on, and you can unsubscribe in one click from any email.`,
  ];
}

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
      show(confirmed(answer.schedule));
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
        ? 'Something went wrong on our side. Press the button again in a moment.'
        : 'We could not reach the service. Check your connection and press the button again.';
    message.textContent = text;
    status.textContent = text;
    console.error('confirm failed:', error instanceof Error ? error.name : 'unknown'); // never the token or the answer
  }
}

const fragment = readFragment();
const token = fragment.startsWith('token=') ? fragment.slice('token='.length) : '';
if (TOKEN.test(token)) {
  show(['Confirm your email', 'Press the button to start your Misra job alerts. Nothing is sent until you do.'], { done: false });
  actions.hidden = false;
  button.addEventListener('click', () => {
    void confirm(token);
  });
} else if (Object.hasOwn(WORDS, fragment)) {
  show(WORDS[fragment]);
} else {
  show(['This link is incomplete', 'Open the link from your email again.']);
}
