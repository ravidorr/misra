// The logic of the `report-site` Edge Function (PLAN.md step 5.1; PRD FR-7): a visitor of the dashboard reports a career
// site that is missing from the crawl, and the maintainer gets one plain-text email through Resend.
//
// Everything that touches the outside world comes in through `Deps` (the secrets, `fetch`, the clock), so the vitest
// suite runs this file as it is with a fake Resend and a fake database. `index.ts` is the only file that knows Deno.
//
// What keeps the maintainer's inbox safe, in the order the checks run:
//   1. only the dashboard's origin may call it from a browser (CORS), and the body must be small JSON;
//   2. a honeypot field a person never sees: a filled one is answered with a fake success and nothing happens;
//   3. every field is validated (see validate.ts) before anything is counted or sent;
//   4. at most IP_DAILY_LIMIT reports per visitor per day, counted against a keyed hash of the visitor's address that
//      changes every day (the address itself is never stored or logged);
//   5. at most DAILY_CAP emails per day in all, whoever sends them. A place is taken in the database before the email is
//      sent and given back when sending fails. If the database cannot be reached nothing is sent (fail closed).
// The email is plain text with no field in a header except the company name (or, without one, the link) as one line of
// the subject, so a visitor cannot inject markup or a header.

import { validateReport } from './validate.ts';
import type { Report } from './validate.ts';

export const ALLOWED_ORIGIN = 'https://ravidorr.github.io';
export const IP_DAILY_LIMIT = 3;
export const DAILY_CAP = 20;
export const MAX_BODY_BYTES = 8192;
export const DEFAULT_FROM = 'Misra reports <reports@send.avidor.org>';
const RESEND_URL = 'https://api.resend.com/emails';
const REQUEST_TIMEOUT_MS = 10_000;
const ADDRESS = /^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;
const FROM = /^[^\r\n<>]*<[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+>$|^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;

export interface Config {
  resendApiKey: string;
  reportTo: string;
  from: string;
  ipHashSecret: string;
  supabaseUrl: string;
  serviceRoleKey: string;
}

/** The part of `fetch` this function uses; the global `fetch` fits it. */
export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export interface Deps {
  config: Config | undefined;
  fetch: Fetch;
  now: () => Date;
}

/** The function's secrets. Returns what is missing or malformed by name, never by value. */
export function loadConfig(get: (name: string) => string | undefined): { config: Config } | { missing: string[] } {
  const read = (name: string): string => (get(name) ?? '').trim();
  const values = {
    RESEND_API_KEY: read('RESEND_API_KEY'),
    REPORT_TO_EMAIL: read('REPORT_TO_EMAIL'),
    REPORT_FROM_EMAIL: read('REPORT_FROM_EMAIL') || DEFAULT_FROM,
    IP_HASH_SECRET: read('IP_HASH_SECRET'),
    SUPABASE_URL: read('SUPABASE_URL').replace(/\/+$/u, ''),
    SUPABASE_SERVICE_ROLE_KEY: read('SUPABASE_SERVICE_ROLE_KEY'),
  };
  const problems = new Set(
    Object.entries(values)
      .filter(([, value]) => !value)
      .map(([name]) => name),
  );
  if (!ADDRESS.test(values.REPORT_TO_EMAIL)) problems.add('REPORT_TO_EMAIL');
  if (!FROM.test(values.REPORT_FROM_EMAIL)) problems.add('REPORT_FROM_EMAIL');
  if (!/^https?:\/\//u.test(values.SUPABASE_URL)) problems.add('SUPABASE_URL');
  if (values.IP_HASH_SECRET.length < 16) problems.add('IP_HASH_SECRET');
  if (problems.size > 0) return { missing: [...problems] };
  return {
    config: {
      resendApiKey: values.RESEND_API_KEY,
      reportTo: values.REPORT_TO_EMAIL,
      from: values.REPORT_FROM_EMAIL,
      ipHashSecret: values.IP_HASH_SECRET,
      supabaseUrl: values.SUPABASE_URL,
      serviceRoleKey: values.SUPABASE_SERVICE_ROLE_KEY,
    },
  };
}

// ---- Responses ---------------------------------------------------------------------------------------------------

type ErrorCode =
  | 'origin_not_allowed'
  | 'method_not_allowed'
  | 'unsupported_media_type'
  | 'payload_too_large'
  | 'invalid_request'
  | 'invalid_input'
  | 'rate_limited'
  | 'daily_cap_reached'
  | 'send_failed'
  | 'server_error';

function corsHeaders(origin: string | null): Record<string, string> {
  if (origin !== ALLOWED_ORIGIN) return {};
  return {
    'access-control-allow-origin': ALLOWED_ORIGIN,
    'access-control-allow-methods': 'POST, OPTIONS',
    'access-control-allow-headers': 'content-type, authorization, apikey, x-client-info',
    'access-control-max-age': '86400',
  };
}

function reply(origin: string | null, status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      ...(status === 204 ? {} : { 'content-type': 'application/json; charset=utf-8' }),
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      vary: 'Origin',
      ...corsHeaders(origin),
      ...extra,
    },
  });
}

function fail(
  origin: string | null,
  status: number,
  code: ErrorCode,
  message: string,
  extra: Record<string, unknown> = {},
  headers: Record<string, string> = {},
): Response {
  return reply(origin, status, { ok: false, error: { code, message, ...extra } }, headers);
}

// ---- The visitor ---------------------------------------------------------------------------------------------------

/** An IPv4 address as it is, an IPv6 address as its /64 network (one home or one host gets one bucket, not 2^64). */
export function normalizeAddress(raw: string): string | undefined {
  const text = raw.trim().replace(/^\[|\]$/gu, '').replace(/%.*$/u, '').toLowerCase();
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/u.exec(text);
  const candidate = mapped?.[1] ?? text;
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/u.test(candidate)) {
    return candidate.split('.').every((part) => Number(part) <= 255) ? candidate : undefined;
  }
  if (!/^[0-9a-f:]+$/u.test(candidate) || !candidate.includes(':')) return undefined;
  const halves = candidate.split('::');
  if (halves.length > 2) return undefined;
  const head = halves[0] ? halves[0].split(':') : [];
  const tail = halves[1] ? halves[1].split(':') : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 1 ? missing !== 0 : missing < 1) return undefined;
  const groups = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill('0'), ...tail];
  if (groups.some((group) => group.length < 1 || group.length > 4)) return undefined;
  return `${groups
    .slice(0, 4)
    .map((group) => group.padStart(4, '0'))
    .join(':')}::/64`;
}

/**
 * The visitor's network as the platform reports it. `cf-connecting-ip` is set by Cloudflare in front of Supabase and
 * replaces whatever the caller sent. `x-forwarded-for` is the fallback, and only its LAST entry counts: each proxy
 * appends the address it saw, so the first entries are whatever the caller wrote. If neither gives an address every such
 * request shares one bucket, which makes the limit stricter, never looser.
 */
export function visitorNetwork(headers: Headers): string {
  const cloudflare = normalizeAddress(headers.get('cf-connecting-ip') ?? '');
  if (cloudflare) return cloudflare;
  const entries = (headers.get('x-forwarded-for') ?? '').split(',');
  const forwarded = normalizeAddress(entries[entries.length - 1] ?? '');
  if (forwarded) return forwarded;
  console.warn('report-site: no usable visitor address, using the shared bucket');
  return 'unknown';
}

async function hmac(key: BufferSource, message: string): Promise<ArrayBuffer> {
  const cryptoKey = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(message));
}

/** A keyed hash of the visitor and the day. The salt of the day comes from the secret, so yesterday's hashes match nothing. */
export async function hashVisitor(secret: string, day: string, network: string): Promise<string> {
  const salt = await hmac(new TextEncoder().encode(secret), day);
  const hash = new Uint8Array(await hmac(salt, network));
  return Array.from(hash, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

// ---- The database and Resend -------------------------------------------------------------------------------------

type Reservation = 'ok' | 'ip_limit' | 'daily_cap';

/**
 * Calls a Postgres function through PostgREST and returns the response once its status is a success. The caller decides
 * whether there is a body to read: PostgREST answers a function that `returns void` with no body (204, and an empty 200
 * in older versions), so a void function's answer is judged by its status alone.
 */
async function callDatabase(deps: Deps, config: Config, name: string, args: Record<string, unknown>): Promise<Response> {
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
    throw new Error(`${name} answered ${String(response.status)}`);
  }
  return response;
}

async function reserve(deps: Deps, config: Config, day: string, ipHash: string): Promise<Reservation> {
  const response = await callDatabase(deps, config, 'report_site_reserve', {
    p_day: day,
    p_ip_hash: ipHash,
    p_ip_limit: IP_DAILY_LIMIT,
    p_daily_cap: DAILY_CAP,
  });
  // The function returns text, which PostgREST sends as a bare JSON string: "ok".
  const answer: unknown = await response.json();
  if (answer === 'ok' || answer === 'ip_limit' || answer === 'daily_cap') return answer;
  throw new Error('report_site_reserve gave an answer this function does not know');
}

async function release(deps: Deps, config: Config, day: string, ipHash: string): Promise<void> {
  try {
    // The function returns void: PostgREST answers with no body, so only the status counts and nothing is parsed.
    const response = await callDatabase(deps, config, 'report_site_release', { p_day: day, p_ip_hash: ipHash });
    await response.body?.cancel().catch(() => undefined);
  } catch (error) {
    // Losing a place only means the cap is reached a little earlier today.
    console.error(
      `report-site: could not give the place back for ${day}`,
      error instanceof Error ? error.message : 'unknown',
    );
  }
}

export function renderEmail(report: Report, now: Date): { subject: string; text: string } {
  return {
    subject: `Site report: ${report.company || report.link}`.slice(0, 150),
    text: [
      'A visitor of the Misra dashboard reports a career site that is missing from the crawl.',
      '',
      ...(report.company ? [`Company: ${report.company}`] : []),
      `Careers page: ${report.link}`,
      '',
      ...(report.why ? ['Why it matters:', report.why, ''] : []),
      `Received: ${now.toISOString()}`,
      '',
    ].join('\n'),
  };
}

/** Resend answered with a refusal (4xx): the email was definitely not sent, so the place can be given back. */
class SendRefused extends Error {}

async function sendEmail(deps: Deps, config: Config, report: Report): Promise<void> {
  const { subject, text } = renderEmail(report, deps.now());
  const response = await deps.fetch(RESEND_URL, {
    method: 'POST',
    headers: { authorization: `Bearer ${config.resendApiKey}`, 'content-type': 'application/json' },
    body: JSON.stringify({ from: config.from, to: [config.reportTo], subject, text }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    const detail = (await response.text().catch(() => '')).slice(0, 200);
    const message = `Resend answered ${String(response.status)} ${detail}`;
    // A 5xx or a timeout may have queued the email, so only a 4xx counts as "not sent".
    throw response.status < 500 ? new SendRefused(message) : new Error(message);
  }
}

// ---- The request ---------------------------------------------------------------------------------------------------

function secondsUntilTomorrow(now: Date): string {
  const tomorrow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return String(Math.max(1, Math.ceil((tomorrow - now.getTime()) / 1000)));
}

/** The body as text, read chunk by chunk and abandoned as soon as it passes the limit; undefined when it is too large. */
async function readLimited(request: Request): Promise<string | undefined> {
  if (!request.body) return '';
  const reader = request.body.getReader();
  const bytes = new Uint8Array(MAX_BODY_BYTES);
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (length + value.byteLength > MAX_BODY_BYTES) {
      await reader.cancel();
      return undefined;
    }
    bytes.set(value, length);
    length += value.byteLength;
  }
  return new TextDecoder().decode(bytes.subarray(0, length));
}

/** The JSON object of the request, or why there is none. The size is checked before and after reading the body. */
async function readJson(request: Request): Promise<Record<string, unknown> | 'too_large' | 'not_an_object'> {
  if (Number(request.headers.get('content-length') ?? '0') > MAX_BODY_BYTES) return 'too_large';
  const text = await readLimited(request);
  if (text === undefined) return 'too_large';
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return 'not_an_object';
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return 'not_an_object';
  return parsed as Record<string, unknown>;
}

/** The honeypot field is hidden from people, so anything in it (an empty string, null or false is nothing) is a bot. */
function tripsHoneypot(body: Record<string, unknown>): boolean {
  const value = body.website;
  if (typeof value === 'string') return value.trim() !== '';
  return value !== undefined && value !== null && value !== false;
}

export async function handleReport(request: Request, deps: Deps): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin !== null && origin !== ALLOWED_ORIGIN) {
    return fail(null, 403, 'origin_not_allowed', 'This address may not call this function.');
  }
  if (request.method === 'OPTIONS') return reply(origin, 204, null);
  if (request.method !== 'POST') {
    return fail(origin, 405, 'method_not_allowed', 'Use POST.', {}, { allow: 'POST, OPTIONS' });
  }
  if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return fail(origin, 415, 'unsupported_media_type', 'Send the report as application/json.');
  }
  const { config } = deps;
  if (!config) {
    console.error('report-site: the secrets are missing or malformed (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }

  try {
    const body = await readJson(request);
    if (body === 'too_large') {
      return fail(origin, 413, 'payload_too_large', `The report can be at most ${String(MAX_BODY_BYTES)} bytes.`);
    }
    if (body === 'not_an_object') {
      return fail(origin, 400, 'invalid_request', 'Send a JSON object with company, link and why.');
    }
    if (tripsHoneypot(body)) return reply(origin, 200, { ok: true });

    const validation = validateReport(body);
    if (!validation.ok) {
      return fail(origin, 400, 'invalid_input', 'Some fields need another look.', { fields: validation.fields });
    }

    const now = deps.now();
    const day = now.toISOString().slice(0, 10);
    const ipHash = await hashVisitor(config.ipHashSecret, day, visitorNetwork(request.headers));
    const reservation = await reserve(deps, config, day, ipHash);
    if (reservation === 'ip_limit') {
      return fail(
        origin,
        429,
        'rate_limited',
        'You have sent the most reports allowed for today. Try again tomorrow.',
        {},
        { 'retry-after': secondsUntilTomorrow(now) },
      );
    }
    if (reservation === 'daily_cap') {
      return fail(
        origin,
        503,
        'daily_cap_reached',
        'Too many reports came in today. Try again tomorrow.',
        {},
        { 'retry-after': secondsUntilTomorrow(now) },
      );
    }

    try {
      await sendEmail(deps, config, validation.report);
    } catch (error) {
      console.error('report-site: sending failed', error instanceof Error ? error.message : 'unknown');
      // Keep the place when the outcome is unknown (timeout, network error, 5xx): the cap is the stricter side.
      if (error instanceof SendRefused) await release(deps, config, day, ipHash);
      return fail(origin, 502, 'send_failed', 'The report could not be sent. Try again later.');
    }
    return reply(origin, 200, { ok: true });
  } catch (error) {
    console.error('report-site: failed', error instanceof Error ? error.message : 'unknown');
    return fail(origin, 500, 'server_error', 'Something went wrong. Try again later.');
  }
}
