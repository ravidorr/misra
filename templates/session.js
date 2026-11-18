/* The saved sign-in session, shared by auth.js (the dashboard) and admin.js (the admin page): one storage key and one
   storage wrapper, so both pages on the origin read and write the same session and neither can save more of it than the
   other. A module of its own, with no page ids and no side effects. */
export const STORAGE_KEY = 'misra-auth';

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
export const storage = {
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
