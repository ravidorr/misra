/* Optional Google sign-in. Loaded as a module after supabase.js (the vendored supabase-js, which defines the global
   `supabase`). It is separate from dashboard.js on purpose: the dashboard works exactly as before without it, signed out,
   and a failure here (a blocked script, a broken backend) can never stop the roles from loading. There is no password
   anywhere: Google does the sign-in and the browser comes back with a one-time code (PKCE).

   What the sign-in provider sends besides the email (a name, a photo) is never shown by this page, and the session it
   saves has that stripped from the user (see `scrubbed`). The signed access token is saved as issued, and the page
   cannot edit it: it carries the same details as its `user_metadata` claim unless the project's access token hook (the
   migration's `custom_access_token_hook`, switched on in the Supabase dashboard) empties the claim. The tables keep
   the email only (supabase/migrations). */
const $ = (id) => document.getElementById(id);
const SUPABASE_URL = '__SUPABASE_URL__';
const SUPABASE_ANON_KEY = '__SUPABASE_ANON_KEY__';
const STORAGE_KEY = 'misra-auth';
const DELETE_TIMEOUT_MS = 15000;
const HTTP_UNAUTHORIZED = 401;
const MESSAGES = {
  start: 'Could not start the Google sign-in. Check your connection and try again. The roles work without signing in.',
  finish: 'The sign-in did not finish. Try again. The roles work without signing in.',
  cancelled: 'The sign-in was cancelled. You can try again, or keep using the roles without an account.',
  deleteLater: 'We could not confirm that your account was deleted. Please try again.',
  deleteRefused: 'Your account was not deleted. Please try again.',
  deleteSignIn: 'Your sign-in was not accepted, so nothing was deleted by this request. Sign out and sign in again, then check whether your account still exists.',
  deleted: 'Your account and everything saved in it were deleted. You are signed out and the roles work as before, without an account.',
  out: 'You are signed out here, but the service could not end the session on its side. It ends by itself when it expires.',
};

/* The session as the page keeps it: the user reduced to id, email and a few account fields, and without Google's own
   access and refresh tokens (the page never uses them, and they could fetch the name and photo). */
function scrubbed(value) {
  let session;
  try {
    session = JSON.parse(value);
  } catch {
    return value; /* not a session (the PKCE code verifier is a plain string) */
  }
  if (typeof session?.user !== 'object' || session.user === null) return value;
  const kept = { ...session };
  for (const name of ['provider_token', 'provider_refresh_token']) Reflect.deleteProperty(kept, name); /* Google's own */
  const { id, aud, role, email, created_at: createdAt } = session.user;
  return JSON.stringify({ ...kept, user: { id, aud, role, email, created_at: createdAt, app_metadata: {}, user_metadata: {} } });
}

/* The per-user notes dashboard.js keeps in this browser (queued changes, unsent filters, the first-sign-in answer), by user id.
   Deleting an account clears this user's entries here too, so it does not depend on dashboard.js having loaded its data. */
function forgetUser(userId) {
  if (typeof userId !== 'string') return;
  for (const name of ['misra-pending-choices-v1', 'misra-pending-filters-v1', 'misra-imported-v1']) {
    try {
      const all = JSON.parse(localStorage.getItem(name) ?? '{}');
      if (typeof all === 'object' && all !== null && userId in all) {
        Reflect.deleteProperty(all, userId);
        localStorage.setItem(name, JSON.stringify(all));
      }
    } catch {
      /* Storage is blocked or the note is not JSON: nothing of this user's can be read back from it. */
    }
  }
}

/* localStorage, or memory for this page view when the browser blocks it or its quota is full (then a sign-in cannot survive the
   redirect from Google: the code that comes back has no matching start, and the page says the sign-in did not finish). */
const memory = new Map();
const storage = {
  getItem(key) {
    if (memory.has(key)) return memory.get(key); /* a write that failed is newer than what the storage still holds */
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key, value) {
    const kept = scrubbed(value);
    try {
      localStorage.setItem(key, kept);
      memory.delete(key);
    } catch {
      memory.set(key, kept);
    }
  },
  removeItem(key) {
    memory.delete(key);
    try {
      localStorage.removeItem(key);
    } catch {
      /* Storage is blocked: nothing was saved. */
    }
  },
};

let deleting = false; /* a deletion request is in flight */

function say(text) {
  const box = $('authMessage');
  box.replaceChildren();
  if (!text) return;
  const note = document.createElement('p');
  note.className = 'notice';
  note.textContent = text;
  box.append(note);
}

/* Google or Supabase sends the browser back with ?error=... (or #error=...) when the sign-in fails or is cancelled. The
   words come from the address bar, so the page shows its own text and never theirs. */
function returnedError() {
  const found = new URLSearchParams(location.search.slice(1));
  const hash = new URLSearchParams(location.hash.slice(1));
  const code = found.get('error') ?? hash.get('error');
  if (code === null) return null;
  const cleaned = new URL(location.href);
  for (const name of ['error', 'error_code', 'error_description']) cleaned.searchParams.delete(name);
  cleaned.hash = '';
  history.replaceState(null, '', cleaned);
  return code === 'access_denied' ? MESSAGES.cancelled : MESSAGES.finish;
}

/* Tells dashboard.js who is signed in, to save stars and discards in the account: the user's id and the client (which
   renews the token). The page script may not have started yet, so the account is also kept on `globalThis`. */
function publish(client, session) {
  globalThis.misraAccount = session?.user?.id ? { client, userId: session.user.id } : null;
  document.dispatchEvent(new CustomEvent('misra:account'));
}

let signedInEmail = ''; /* the address of the signed-in user, for the delete dialog; empty when there is none */

function show(session) {
  const email = session?.user?.email;
  signedInEmail = typeof email === 'string' ? email : '';
  $('account').classList.toggle('is-signed-in', Boolean(session));
  $('signIn').hidden = Boolean(session);
  $('signInNote').hidden = Boolean(session);
  $('accountUser').hidden = !session;
  $('accountEmail').textContent = session ? (email ?? 'Signed in') : '';
}

if (typeof globalThis.supabase?.createClient === 'function') {
  const failure = returnedError();
  const client = globalThis.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { flowType: 'pkce', storage, storageKey: STORAGE_KEY, detectSessionInUrl: true },
  });
  /* Fires once with the saved session (after a code from Google is exchanged), then on every sign-in and sign-out. */
  client.auth.onAuthStateChange((_event, session) => {
    show(session);
    publish(client, session);
  });
  $('account').hidden = false;
  $('signInNote').hidden = false; /* the notice shows whenever the button does; show() hides both once signed in */
  say(failure);

  $('signIn').addEventListener('click', async () => {
    say('');
    try {
      const { error } = await client.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: location.origin + location.pathname },
      });
      if (error) throw error;
    } catch (error) {
      console.error(error);
      say(MESSAGES.start);
    }
  });

  $('signOut').addEventListener('click', async () => {
    say('');
    try {
      const { error } = await client.auth.signOut();
      if (error) throw error;
    } catch (error) {
      console.error(error);
      /* The library gives up before clearing the session when the expired token cannot be renewed (offline, service
         down): clear it here, so the page never says "signed out" while the saved session could sign the browser in. */
      storage.removeItem(STORAGE_KEY);
      show(null);
      publish(client, null);
      say(MESSAGES.out);
    }
  });

  /* Delete my account (PLAN.md step 4.4, FR-1 AC 4). The button opens a confirmation dialog; the dialog's button asks the
     `delete-account` Edge Function, which checks this user's own token and removes the account and every row of it. Only a
     reply of "ok" counts: any other outcome leaves the account and says so, with a retry. */
  const dialog = $('deleteDialog');
  const confirm = $('deleteConfirm');
  const cancel = $('deleteCancel');
  const problem = $('deleteError');
  const CONFIRM_TEXT = confirm.textContent;
  let deletingUser = null; /* the id of the user whose token the request carried */

  function setDeleting(on) {
    deleting = on;
    for (const button of [confirm, cancel]) button.setAttribute('aria-disabled', String(on)); /* not "disabled": focus stays */
    confirm.setAttribute('aria-busy', String(on));
    if (on) confirm.textContent = 'Deleting…';
  }

  function failDelete(text) {
    setDeleting(false);
    confirm.textContent = 'Try again';
    problem.textContent = text;
    problem.hidden = false;
    if (!dialog.open) dialog.showModal(); /* a second Esc can close it while the request runs: the answer must still be seen */
  }

  /* Asks the function. Resolves with 'deleted', or with the words to show when it did not (and what the user can do). */
  async function requestDeletion() {
    const { data } = await client.auth.getSession(); /* renews an expired token first */
    const token = data.session?.access_token;
    if (!token) return MESSAGES.deleteSignIn;
    deletingUser = data.session.user?.id ?? null;
    let response;
    try {
      response = await fetch(`${SUPABASE_URL}/functions/v1/delete-account`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, apikey: SUPABASE_ANON_KEY },
        signal: AbortSignal.timeout(DELETE_TIMEOUT_MS),
      });
    } catch {
      return MESSAGES.deleteLater; /* no answer: the deletion may or may not have happened */
    }
    const body = await response.json().catch(() => null);
    if (response.ok && body?.ok === true) return 'deleted';
    if (response.status === HTTP_UNAUTHORIZED) return MESSAGES.deleteSignIn;
    return body?.error?.code === 'delete_failed' ? MESSAGES.deleteRefused : MESSAGES.deleteLater;
  }

  /* The account is gone: forget it here. dashboard.js drops this user's queues and stored notes first (the event is
     handled synchronously), then the session is cleared without asking the service (it no longer knows the user). */
  async function finishDeletion() {
    globalThis.misraAccount = null; /* so no auth event in between can attach the deleted account again */
    forgetUser(deletingUser);
    document.dispatchEvent(new CustomEvent('misra:account-deleted', { detail: { userId: deletingUser } }));
    try {
      await client.auth.signOut({ scope: 'local' });
    } catch (error) {
      console.error(error);
    }
    storage.removeItem(STORAGE_KEY);
    storage.removeItem(`${STORAGE_KEY}-code-verifier`);
    deleting = false;
    show(null);
    publish(client, null);
    dialog.close();
    say(MESSAGES.deleted);
    $('signIn').focus();
  }

  $('deleteAccount').addEventListener('click', () => {
    $('deleteEmail').textContent = signedInEmail; /* text, never HTML */
    $('deleteEmailPart').hidden = !signedInEmail;
    setDeleting(false);
    confirm.textContent = CONFIRM_TEXT;
    problem.hidden = true;
    problem.textContent = '';
    dialog.showModal();
    cancel.focus(); /* the safe choice first */
  });
  cancel.addEventListener('click', () => {
    if (!deleting) dialog.close();
  });
  dialog.addEventListener('cancel', (event) => {
    if (deleting) event.preventDefault(); /* Esc does not abandon a request in flight */
  });
  confirm.addEventListener('click', async () => {
    if (deleting) return;
    problem.hidden = true;
    setDeleting(true);
    try {
      const outcome = await requestDeletion();
      if (outcome === 'deleted') await finishDeletion();
      else failDelete(outcome);
    } catch (error) {
      console.error(error);
      failDelete(MESSAGES.deleteLater);
    }
  });

  /* The sign-in is over once the library has read the address: it exchanged the code, or it had nothing to exchange. A
     code that is still in the address then belongs to a sign-in this browser never started (a copied or stale link). */
  const { error } = await client.auth.initialize();
  const { data } = await client.auth.getSession();
  const unused = !data.session && new URLSearchParams(location.search).has('code');
  if (unused) {
    const cleaned = new URL(location.href);
    cleaned.searchParams.delete('code');
    history.replaceState(null, '', cleaned);
  }
  if (error) console.error(error);
  if (error || unused) say(MESSAGES.finish);
}
