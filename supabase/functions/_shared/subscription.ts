// What the `confirm-email` and `unsubscribe` Edge Functions share (PLAN.md step 6.1; PRD FR-8 AC 7): the secrets, the
// token format and its hash, the call into the database, and the JSON answers. Plain TypeScript with web standards only
// (`fetch`, `Request`, `Response`, Web Crypto); the secrets, `fetch` and the clock come in through `Deps`, so the vitest
// suite runs this file as it is. The `index.ts` of each function is the only code that knows Deno.
//
// Tokens. A token is 32 random bytes, base64url (43 characters), one for each purpose (confirm, unsubscribe). The
// database stores only its SHA-256 (64 hex characters), so a leaked database confirms and unsubscribes nobody; the raw
// token appears only in the emailed link. The functions look a token up by that hash inside the database function
// (`confirm_subscription`, `unsubscribe_subscription`): an attacker cannot choose a hash, so the index lookup leaks
// nothing through timing, and guessing a token is a 1 in 2^256 chance. Because of that there is no lock-out or rate
// limit here, and unknown, malformed and wrong tokens all get the same answer, so the functions cannot be used to find
// out who is subscribed.
//
// Nothing is ever logged but status codes and function names: no token, hash, address or user id.

export const ALLOWED_ORIGIN = 'https://ravidorr.github.io';
/** The static page a confirmation link opens and an unsubscribe ends on (templates/email.html, built into docs/). */
export const PAGE_URL = 'https://ravidorr.github.io/misra/email.html';
export const MAX_BODY_BYTES = 2048;
const REQUEST_TIMEOUT_MS = 8000;
const TOKEN = /^[A-Za-z0-9_-]{43}$/u;

export interface Config {
  supabaseUrl: string;
  serviceRoleKey: string;
}

/** The part of `fetch` these functions use; the global `fetch` fits it. */
export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export interface Deps {
  config: Config | undefined;
  fetch: Fetch;
  now: () => Date;
}

/** The functions' secrets (Supabase supplies both to every function). Returns what is missing by name, never by value. */
export function loadConfig(get: (name: string) => string | undefined): { config: Config } | { missing: string[] } {
  const read = (name: string): string => (get(name) ?? '').trim();
  const supabaseUrl = read('SUPABASE_URL').replace(/\/+$/u, '');
  const serviceRoleKey = read('SUPABASE_SERVICE_ROLE_KEY');
  const missing: string[] = [];
  if (!/^https?:\/\//u.test(supabaseUrl)) missing.push('SUPABASE_URL');
  if (!serviceRoleKey) missing.push('SUPABASE_SERVICE_ROLE_KEY');
  return missing.length > 0 ? { missing } : { config: { supabaseUrl, serviceRoleKey } };
}

// ---- Tokens --------------------------------------------------------------------------------------------------------

/** Is this the shape of a token we issue? Anything else is answered as an unknown link, without a database call. */
export function isToken(value: unknown): value is string {
  return typeof value === 'string' && TOKEN.test(value);
}

/** SHA-256 of a token as 64 lower-case hex characters: the only form in which a token reaches the database. */
export async function hashToken(token: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token)));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * A fresh token and its hash. Step 6.2's function that turns the email on uses this for both the confirmation and the
 * unsubscribe token: it stores `hash`, puts `token` in the email, and keeps no other copy.
 */
export async function newToken(
  random: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array<ArrayBuffer> = (bytes) => crypto.getRandomValues(bytes),
): Promise<{ token: string; hash: string }> {
  const bytes = random(new Uint8Array(32));
  const token = btoa(String.fromCharCode(...bytes))
    .replace(/\+/gu, '-')
    .replace(/\//gu, '_')
    .replace(/=+$/u, '');
  return { token, hash: await hashToken(token) };
}

// ---- The database --------------------------------------------------------------------------------------------------

/** A failure this module raises itself, whose message is safe to log (a function name and a status, nothing else). */
export class DatabaseError extends Error {}

/**
 * Calls a Postgres function through PostgREST as the service role and returns its answer, which must be one of the
 * `allowed` words (the functions return text, which PostgREST sends as a bare JSON string). Any other outcome throws
 * with the status only, never with a body.
 */
export async function callDatabase<Word extends string>(
  deps: Deps,
  config: Config,
  name: string,
  args: Record<string, unknown>,
  allowed: readonly Word[],
): Promise<Word> {
  const response = await deps.fetch(`${config.supabaseUrl}/rest/v1/rpc/${name}`, {
    method: 'POST',
    headers: {
      apikey: config.serviceRoleKey,
      authorization: `Bearer ${config.serviceRoleKey}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(args),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    throw new DatabaseError(`${name} answered ${String(response.status)}`);
  }
  const answer: unknown = await response.json().catch(() => {
    throw new DatabaseError(`${name} gave an answer that is not JSON`);
  });
  const word = allowed.find((candidate) => candidate === answer);
  if (word === undefined) throw new DatabaseError(`${name} gave an answer this function does not know`);
  return word;
}

/** What may be said about a failure: the message of our own DatabaseError, else only the kind of error. */
export function describe(error: unknown): string {
  if (error instanceof DatabaseError) return error.message;
  return error instanceof Error ? error.name : 'unknown';
}

// ---- Answers -------------------------------------------------------------------------------------------------------

const BASE_HEADERS = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' };

export function corsHeaders(origin: string | null): Record<string, string> {
  if (origin !== ALLOWED_ORIGIN) return {};
  return {
    'access-control-allow-origin': ALLOWED_ORIGIN,
    'access-control-allow-methods': 'POST, OPTIONS',
    'access-control-allow-headers': 'content-type, apikey, x-client-info',
    'access-control-max-age': '86400',
  };
}

export function reply(
  origin: string | null,
  status: number,
  body: unknown,
  extra: Record<string, string> = {},
): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      ...(status === 204 ? {} : { 'content-type': 'application/json; charset=utf-8' }),
      ...BASE_HEADERS,
      vary: 'Origin',
      ...corsHeaders(origin),
      ...extra,
    },
  });
}

export function fail(
  origin: string | null,
  status: number,
  code: string,
  message: string,
  headers: Record<string, string> = {},
): Response {
  return reply(origin, status, { ok: false, error: { code, message } }, headers);
}

/** A redirect that carries no data but a word in the fragment, which no server ever sees. */
export function redirectToPage(word: string): Response {
  return new Response(null, { status: 303, headers: { ...BASE_HEADERS, location: `${PAGE_URL}#${word}` } });
}

/** The body as text, read chunk by chunk and abandoned as soon as it passes the limit; undefined when it is too large. */
export async function readLimited(request: Request, limit: number): Promise<string | undefined> {
  if (!request.body) return '';
  const reader = request.body.getReader();
  const bytes = new Uint8Array(limit);
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (length + value.byteLength > limit) {
      await reader.cancel();
      return undefined;
    }
    bytes.set(value, length);
    length += value.byteLength;
  }
  return new TextDecoder().decode(bytes.subarray(0, length));
}
