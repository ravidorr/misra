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
const MESSAGES = {
  start: 'Could not start the Google sign-in. Check your connection and try again. The roles work without signing in.',
  finish: 'The sign-in did not finish. Try again. The roles work without signing in.',
  cancelled: 'The sign-in was cancelled. You can try again, or keep using the roles without an account.',
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

function show(session) {
  const email = session?.user?.email;
  $('account').classList.toggle('is-signed-in', Boolean(session));
  $('signIn').hidden = Boolean(session);
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
  });
  $('account').hidden = false;
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
      say(MESSAGES.out);
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
