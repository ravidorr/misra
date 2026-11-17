// What the `confirm-email` and `unsubscribe` Edge Functions share (PLAN.md step 6.1; PRD FR-8 AC 7): the secrets, the
// token format and its hash, the call into the database, and the JSON answers. Plain TypeScript with web standards only
// (`fetch`, `Request`, `Response`, Web Crypto); the secrets, `fetch` and the clock come in through `Deps`, so the vitest
// suite runs this file as it is. The `index.ts` of each function is the only code that knows Deno.
//
// Confirmation tokens. A token is 32 random bytes, base64url (43 characters). The database stores only its SHA-256
// (64 hex characters), so a leaked database confirms nobody; the raw token appears only in the emailed link. The
// function looks a token up by that hash inside the database function (`confirm_subscription`): an attacker cannot
// choose a hash, so the index lookup leaks nothing through timing, and guessing a token is a 1 in 2^256 chance. Because of that there is no lock-out or rate
// limit here, and unknown, malformed and wrong tokens all get the same answer, so the functions cannot be used to find
// out who is subscribed.
//
// The unsubscribe link is the exception to "a random token, stored as a hash": it is stateless (PLAN.md step 6.3). The
// digest sender must put a working link in every email, and the database holds no raw token, so the token is
// `<subscription id>.<HMAC-SHA256(UNSUBSCRIBE_SECRET, id)>` (the id is `subscriptions.user_id`; the MAC is 64 hex
// characters), rebuilt by the sender (send_digests.py) and verified here by recomputing the MAC. Nothing about it is
// stored, an attacker who does not hold the secret cannot make a link for anyone else, and the MAC is compared in
// constant time (`crypto.subtle.verify`). One token per subscription for its whole life: an old email's link still
// unsubscribes the current subscription, which is the safe direction.
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
 * A fresh token and its hash. Step 6.2's function that turns the email on uses this for the confirmation token: it
 * stores `hash`, puts `token` in the email, and keeps no other copy. (It also makes one for the `NOT NULL` unsubscribe
 * hash column, whose value nothing reads any more: the unsubscribe link is the stateless token below.)
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

// ---- The stateless unsubscribe token -------------------------------------------------------------------------------

const UNSUBSCRIBE_TOKEN = /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([0-9a-f]{64})$/u;
/** The shortest `UNSUBSCRIBE_SECRET` accepted (`openssl rand -hex 32` gives 64 characters). */
export const UNSUBSCRIBE_SECRET_MIN_LENGTH = 32;

async function macKey(secret: string, usage: 'sign' | 'verify'): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, [usage]);
}

const toHex = (bytes: Uint8Array): string => Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');

/** The unsubscribe token of a subscription: `<id>.<hex HMAC-SHA256 of the id under the secret>`. */
export async function makeUnsubscribeToken(secret: string, id: string): Promise<string> {
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', await macKey(secret, 'sign'), new TextEncoder().encode(id)));
  return `${id}.${toHex(mac)}`;
}

/**
 * The subscription id a token names, when the MAC is right; undefined for anything else (wrong shape, wrong MAC, a
 * confirmation token, a token made under another secret). `crypto.subtle.verify` compares in constant time.
 */
export async function verifyUnsubscribeToken(secret: string, token: unknown): Promise<string | undefined> {
  const match = typeof token === 'string' ? UNSUBSCRIBE_TOKEN.exec(token) : null;
  if (!match) return undefined;
  const [, id = '', mac = ''] = match;
  const bytes = Uint8Array.from(mac.match(/../gu) ?? [], (pair) => Number.parseInt(pair, 16));
  const valid = await crypto.subtle.verify('HMAC', await macKey(secret, 'verify'), bytes, new TextEncoder().encode(id));
  return valid ? id : undefined;
}

// ---- The database --------------------------------------------------------------------------------------------------

/** A failure this module raises itself, whose message is safe to log (a function name and a status, nothing else). */
export class DatabaseError extends Error {}

/**
 * Calls a Postgres function through PostgREST as the service role and returns its answer as JSON. Any failure throws
 * with the status only, never with a body.
 */
export async function callRpc(deps: Deps, config: Config, name: string, args: Record<string, unknown>): Promise<unknown> {
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
  return response.json().catch(() => {
    throw new DatabaseError(`${name} gave an answer that is not JSON`);
  });
}

/**
 * Calls a Postgres function that returns text (PostgREST sends it as a bare JSON string) and checks that the answer is
 * one of the `allowed` words.
 */
export async function callDatabase<Word extends string>(
  deps: Deps,
  config: Config,
  name: string,
  args: Record<string, unknown>,
  allowed: readonly Word[],
): Promise<Word> {
  const answer = await callRpc(deps, config, name, args);
  const word = allowed.find((candidate) => candidate === answer);
  if (word === undefined) throw new DatabaseError(`${name} gave an answer this function does not know`);
  return word;
}

export type Schedule = 'daily' | 'weekly';

/**
 * The schedule (`frequency`) of the subscription whose confirmation token has this hash, read with the service role right
 * after a confirm so the page can say "daily" or "weekly". Best effort by design: it never throws and answers undefined
 * when the row is not found (for instance the user unsubscribed in between, which clears the hash), the call fails, or the
 * value is not one we know. The confirmation has already happened; the page then says "daily or weekly" instead of
 * guessing. The hash is the same one-way value the database function received, and nothing is logged.
 */
export async function readSchedule(deps: Deps, config: Config, hash: string): Promise<Schedule | undefined> {
  try {
    const response = await deps.fetch(
      `${config.supabaseUrl}/rest/v1/subscriptions?confirm_token_hash=eq.${hash}&select=frequency&limit=1`,
      {
        method: 'GET',
        headers: { apikey: config.serviceRoleKey, authorization: `Bearer ${config.serviceRoleKey}`, accept: 'application/json' },
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      await response.body?.cancel().catch(() => undefined);
      return undefined;
    }
    const rows: unknown = await response.json().catch(() => undefined);
    const frequency = Array.isArray(rows) ? (rows[0] as { frequency?: unknown } | undefined)?.frequency : undefined;
    return frequency === 'daily' || frequency === 'weekly' ? frequency : undefined;
  } catch {
    return undefined;
  }
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

/** A bare answer with no body, for HEAD: the caller learns the link is alive and nothing is read or changed. */
export function emptyOk(): Response {
  return new Response(null, { status: 200, headers: BASE_HEADERS });
}

/** A plain-text answer (RFC 8058 asks for no particular body; the mailbox provider only reads the status). */
export function plainText(status: number, text: string): Response {
  return new Response(text, { status, headers: { ...BASE_HEADERS, 'content-type': 'text/plain; charset=utf-8' } });
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
