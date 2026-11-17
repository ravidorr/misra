// The logic of the `contact` Edge Function (TODO.md "Dashboard and site review", the Contact page): a visitor of the Contact
// page writes to the maintainer, and the maintainer gets one plain-text email through Resend. Nothing is stored: not the
// message, not the name, not the address. The visitor is never emailed (no copy, no acknowledgement), so it cannot be
// used to send mail to someone else.
//
// IT IS OFF UNTIL THE LAWYER HAS AGREED. Every request is answered 404 unless the function secret CONTACT_FORM_ENABLED is
// exactly "true" (the same pattern as ASSISTANT_ENDPOINT_ENABLED). The page that calls it is behind the build constant
// CONTACT_FORM_ENABLED in build_dashboard.py. Nobody turns either on before he says so. See supabase/README.md.
//
// Everything that touches the outside world comes in through `Deps` (the secrets, `fetch`, the clock), so the vitest suite
// runs this file as it is. `index.ts` is the only file that knows Deno.
//
// What keeps the maintainer's inbox and the sending domain safe, in the order the checks run:
//   1. the switch; then only the site's origin may call it from a browser (CORS), and the body must be small JSON;
//   2. a honeypot field (a filled one is answered with a fake success and nothing happens) and a minimum time to fill the
//      form (answered with an honest error, because autofill can make a person that fast). The time is sent by the page,
//      so it only stops bots that do not know it (a filter, not a guarantee);
//   3. every field is validated (validate.ts) before anything is counted or sent;
//   4. at most CALLER_DAILY_LIMIT messages per visitor per day, then at most DAILY_CAP in all, counted in the database with
//      assistant_reserve against a keyed hash of the visitor's network and the day (the address is never stored or logged).
//      If the database cannot be reached nothing is sent (fail closed);
//   5. the email has a fixed subject and a fixed From address. The visitor's words are in the plain-text body, and the
//      visitor's address (when given and valid) is only the Reply-To, so nothing can inject a header or a recipient.
// Nothing personal is logged: a log line holds a function name and a status.

import { SendFailed, sendMail } from '../_shared/resend.ts';
import { ALLOWED_ORIGIN, callDatabase, describe, readLimited, reply } from '../_shared/subscription.ts';
import type { Config as DatabaseConfig, Fetch } from '../_shared/subscription.ts';
import { hashVisitor, visitorNetwork } from '../_shared/visitor.ts';
import { validateContact } from './validate.ts';
import type { Contact } from './validate.ts';

export const CALLER_DAILY_LIMIT = 3;
// The Resend plan allows 100 emails a day for everything (report-site 20, job alerts and the digest the rest): see supabase/README.md.
export const DAILY_CAP = 10;
export const MAX_BODY_BYTES = 8192;
/** No person fills in a message in less than this; a faster submit is a bot. */
export const MIN_FILL_MS = 3000;
export const DEFAULT_FROM = 'Misra contact <digest@send.avidor.org>';
export const SUBJECT = 'Misra contact form';
const ADDRESS = /^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;
const FROM = /^[^\r\n<>]*<[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+>$|^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;

export interface Config extends DatabaseConfig {
  resendApiKey: string;
  contactTo: string;
  from: string;
  ipHashSecret: string;
}

export interface Deps {
  /** `CONTACT_FORM_ENABLED` is exactly "true". Anything else answers 404 to everything. */
  enabled: boolean;
  config: Config | undefined;
  fetch: Fetch;
  now: () => Date;
}

/** The function's secrets. Returns what is missing or malformed by name, never by value. */
export function loadConfig(
  get: (name: string) => string | undefined,
): { enabled: boolean; config: Config } | { enabled: boolean; missing: string[] } {
  const read = (name: string): string => (get(name) ?? '').trim();
  const enabled = read('CONTACT_FORM_ENABLED') === 'true';
  const values = {
    RESEND_API_KEY: read('RESEND_API_KEY'),
    CONTACT_TO: read('CONTACT_TO'),
    CONTACT_FROM_EMAIL: read('CONTACT_FROM_EMAIL') || DEFAULT_FROM,
    IP_HASH_SECRET: read('IP_HASH_SECRET'),
    SUPABASE_URL: read('SUPABASE_URL').replace(/\/+$/u, ''),
    SUPABASE_SERVICE_ROLE_KEY: read('SUPABASE_SERVICE_ROLE_KEY'),
  };
  const problems = new Set(
    Object.entries(values)
      .filter(([, value]) => !value)
      .map(([name]) => name),
  );
  if (!ADDRESS.test(values.CONTACT_TO)) problems.add('CONTACT_TO');
  if (!FROM.test(values.CONTACT_FROM_EMAIL)) problems.add('CONTACT_FROM_EMAIL');
  if (!/^https?:\/\//u.test(values.SUPABASE_URL)) problems.add('SUPABASE_URL');
  if (values.IP_HASH_SECRET.length < 16) problems.add('IP_HASH_SECRET');
  if (problems.size > 0) return { enabled, missing: [...problems] };
  return {
    enabled,
    config: {
      resendApiKey: values.RESEND_API_KEY,
      contactTo: values.CONTACT_TO,
      from: values.CONTACT_FROM_EMAIL,
      ipHashSecret: values.IP_HASH_SECRET,
      supabaseUrl: values.SUPABASE_URL,
      serviceRoleKey: values.SUPABASE_SERVICE_ROLE_KEY,
    },
  };
}

// ---- Answers ---------------------------------------------------------------------------------------------------------

type ErrorCode =
  | 'origin_not_allowed'
  | 'method_not_allowed'
  | 'unsupported_media_type'
  | 'payload_too_large'
  | 'invalid_request'
  | 'invalid_input'
  | 'too_fast'
  | 'rate_limited'
  | 'daily_cap_reached'
  | 'send_failed'
  | 'server_error';

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

// ---- The counters ----------------------------------------------------------------------------------------------------

// The counters are the ones of the assistant endpoint (assistant_reserve and assistant_release, table assistant_ip_limits):
// any key works, the key of this form starts with "contact", and report_site_cleanup already deletes the rows of earlier days.
async function reserve(deps: Deps, config: Config, day: string, key: string, limit: number): Promise<boolean> {
  return (await callDatabase(deps, config, 'assistant_reserve', { p_day: day, p_ip_hash: key, p_limit: limit }, ['ok', 'limit'])) === 'ok';
}

/** Gives one place back. Best effort: a failure here only means the limit is reached a little earlier today. */
async function release(deps: Deps, config: Config, day: string, key: string): Promise<void> {
  try {
    await callDatabase(deps, config, 'assistant_release', { p_day: day, p_ip_hash: key }, ['ok']);
  } catch (error) {
    console.error('contact: release failed', describe(error));
  }
}

export function renderEmail(contact: Contact, now: Date): { subject: string; text: string } {
  return {
    subject: SUBJECT,
    text: [
      'A visitor wrote to you through the Misra Contact page.',
      '',
      `Name: ${contact.name || '(not given)'}`,
      `Email: ${contact.email || '(not given, so you cannot reply)'}`,
      '',
      'Message:',
      contact.message,
      '',
      `Received: ${now.toISOString()}`,
      '',
    ].join('\n'),
  };
}

// ---- The request -----------------------------------------------------------------------------------------------------

function secondsUntilTomorrow(now: Date): string {
  const tomorrow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return String(Math.max(1, Math.ceil((tomorrow - now.getTime()) / 1000)));
}

/** The JSON object of the request, or why there is none. The size is checked before and after reading the body. */
async function readJson(request: Request): Promise<Record<string, unknown> | 'too_large' | 'not_an_object'> {
  if (Number(request.headers.get('content-length') ?? '0') > MAX_BODY_BYTES) return 'too_large';
  const text = await readLimited(request, MAX_BODY_BYTES);
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

/** The hidden field has something in it (an empty string, null or false is nothing): no person sees it, so it is a bot. */
function filledTrap(body: Record<string, unknown>): boolean {
  const trap = body.website;
  return typeof trap === 'string' ? trap.trim() !== '' : trap !== undefined && trap !== null && trap !== false;
}

/** Sent faster than a person can write. A missing or non-numeric time counts as too fast: the page always sends it. */
function tooFast(body: Record<string, unknown>): boolean {
  const elapsed = body.elapsed;
  return typeof elapsed !== 'number' || !Number.isFinite(elapsed) || elapsed < MIN_FILL_MS;
}

export async function handleContact(request: Request, deps: Deps): Promise<Response> {
  if (!deps.enabled) {
    // Nothing is done. The site's own page still gets to read the 404 (CORS headers for its origin only, and its preflight
    // is answered), so it can say "not available" and not "could not reach the service".
    const origin = request.headers.get('origin');
    const allowed = origin === ALLOWED_ORIGIN ? origin : null;
    return request.method === 'OPTIONS' && allowed ? reply(allowed, 204, null) : reply(allowed, 404, { error: 'not_found' });
  }
  let origin: string | null = null;
  try {
    origin = request.headers.get('origin');
    return await route(request, deps, origin);
  } catch (error) {
    console.error('contact: failed', describe(error));
    // With the CORS headers of the site, so the page shows the real answer and not "could not reach the service".
    return fail(origin === ALLOWED_ORIGIN ? origin : null, 500, 'server_error', 'Something went wrong. Try again later.');
  }
}

async function route(request: Request, deps: Deps, origin: string | null): Promise<Response> {
  if (origin !== null && origin !== ALLOWED_ORIGIN) {
    return fail(null, 403, 'origin_not_allowed', 'This address may not call this function.');
  }
  if (request.method === 'OPTIONS') return reply(origin, 204, null);
  if (request.method !== 'POST') {
    return fail(origin, 405, 'method_not_allowed', 'Use POST.', {}, { allow: 'POST, OPTIONS' });
  }
  if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return fail(origin, 415, 'unsupported_media_type', 'Send the message as application/json.');
  }
  const { config } = deps;
  if (!config) {
    console.error('contact: the secrets are missing or malformed (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }

  const body = await readJson(request);
  if (body === 'too_large') {
    return fail(origin, 413, 'payload_too_large', `The message can be at most ${String(MAX_BODY_BYTES)} bytes.`);
  }
  if (body === 'not_an_object') {
    return fail(origin, 400, 'invalid_request', 'Send a JSON object with name, email and message.');
  }
  if (filledTrap(body)) return reply(origin, 200, { ok: true }); // a fake success: a bot learns nothing
  // A real error, not a fake success: autofill or a restored page can make a person this fast, and they must not think it was sent.
  if (tooFast(body)) return fail(origin, 400, 'too_fast', 'The message came too soon. Wait a few seconds and try again.');

  const validation = validateContact(body);
  if (!validation.ok) {
    return fail(origin, 400, 'invalid_input', 'Some fields need another look.', { fields: validation.fields });
  }

  const now = deps.now();
  const day = now.toISOString().slice(0, 10);
  const caller = await hashVisitor(config.ipHashSecret, day, `contact|${visitorNetwork(request.headers, 'contact')}`);
  const all = 'contact:all';
  const tomorrow = { 'retry-after': secondsUntilTomorrow(now) };
  // The places taken from the counters by this request, given back when nothing was sent.
  const held: string[] = [];
  const refund = async (): Promise<void> => {
    for (const key of held) await release(deps, config, day, key);
  };

  try {
    // The caller's own limit first, so one caller cannot use up the cap of everyone.
    if (!(await reserve(deps, config, day, caller, CALLER_DAILY_LIMIT))) {
      return fail(origin, 429, 'rate_limited', 'You have sent the most messages allowed for today. Try again tomorrow.', {}, tomorrow);
    }
    held.push(caller);
    if (!(await reserve(deps, config, day, all, DAILY_CAP))) {
      await refund();
      return fail(origin, 503, 'daily_cap_reached', 'Too many messages came in today. Try again tomorrow.', {}, tomorrow);
    }
    held.push(all);
  } catch (error) {
    console.error('contact: counters failed', describe(error));
    await refund();
    return fail(origin, 500, 'server_error', 'Something went wrong. Try again later.');
  }

  try {
    const { subject, text } = renderEmail(validation.contact, now);
    await sendMail(deps.fetch, config.resendApiKey, {
      from: config.from,
      to: config.contactTo,
      subject,
      text,
      ...(validation.contact.email ? { replyTo: validation.contact.email } : {}),
    });
  } catch (error) {
    console.error('contact: sending failed', error instanceof SendFailed ? error.message : describe(error));
    // The places are never given back after a send was tried, even when Resend refused it: a visitor who can make Resend
    // refuse (a Reply-To it dislikes) would otherwise send unlimited requests that cost an API call each.
    return fail(origin, 502, 'send_failed', 'The message could not be sent. Try again later.');
  }
  return reply(origin, 200, { ok: true });
}
