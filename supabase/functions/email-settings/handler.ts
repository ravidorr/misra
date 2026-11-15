// The logic of the `email-settings` Edge Function (PLAN.md step 6.2; PRD FR-8 AC 1 to 3): a signed-in user turns the
// personalised email on, or off. Turning it on stores the address, frequency and filters snapshot, makes the two tokens,
// and sends ONE plain-text confirmation email with the link `email.html#token=<token>`; nothing else is ever sent from
// here, and nothing is sent to the user's digest until they confirm (step 6.1's `confirm-email`). Turning it off does
// exactly what the unsubscribe link does (the SQL function `unsubscribe_subscription`, reached through
// `unsubscribe_user`), so there is one clean-up, not two.
//
// POST JSON, from the dashboard's origin, with the user's access token as the bearer token (the gateway checks it, since
// `verify_jwt = true`, and this function asks Supabase Auth who owns it):
//   { action: 'turn_on', address?: string | null, frequency: 'daily' | 'weekly', filters: {regions, levels, role_types, keywords} }
//   { action: 'turn_off' }
// Answers: 200 { ok: true, state: 'waiting' | 'on' } (on: it was running already for that address and nothing was changed
// or sent) or 200 { ok: true, status: 'unsubscribed' | 'already_unsubscribed' | 'not_subscribed' }; every failure is
// { ok: false, error: { code, message } } with a status that says whether anything was sent. The state the page shows is
// read from the user's own row (row-level security lets them read it without the token hashes), never from this answer.
//
// Who can mail whom. The user id comes only from Auth's answer about the token. The address defaults to the Auth
// account's email (a Google account's email is verified); a different address is accepted only when the user typed it
// as a chosen input, and it then gets one email that does nothing unless its owner presses the link. That is the abuse
// this function limits, in the order the checks run: the whole signup switch (`DIGEST_SIGNUP_ENABLED`, a function secret,
// off by default, so the build-time switch in build_dashboard.py cannot be bypassed by calling the API), then at most
// USER_DAILY_LIMIT confirmation emails per user per day, ADDRESS_DAILY_LIMIT per address per day and DAILY_CAP in all,
// counted in the database against keyed hashes that change every day (the user id and the address themselves are never
// stored in a counter). A place is taken before the email is sent and given back only when Resend definitely refused it
// (a 4xx); a timeout or a 5xx may have sent it, so the place stays used. If the database cannot be reached nothing is
// sent (fail closed).
//
// Secrets and logs. The token exists only in the emailed link and in the one request to Resend; the database holds its
// SHA-256. No log line and no answer holds a token, a hash, an address or a user id: only function names and statuses.

import { bearerToken, identify } from '../_shared/identity.ts';
import { ALLOWED_ORIGIN, MAX_BODY_BYTES, PAGE_URL, callDatabase, describe, fail as failShared, loadConfig as loadBase, newToken, readLimited, reply as replyShared } from '../_shared/subscription.ts';
import type { Config as BaseConfig, Deps as BaseDeps } from '../_shared/subscription.ts';
import { validateRequest } from './validate.ts';
import type { FieldErrors, TurnOn } from './validate.ts';

export { ALLOWED_ORIGIN } from '../_shared/subscription.ts';
export type { Fetch } from '../_shared/subscription.ts';

// Resend's free plan is 100 emails a day and 3,000 a month, shared with the other project (easy-theory). `report-site`
// may use 20 a day. These emails may use 30 a day, so together they never pass 50 of the 100, and the digests of step 6.3
// (which have their own budget there) keep room. Three per user and per address a day let a person who mistyped or lost
// an email try again, and cap what a hostile account can do to one inbox.
export const USER_DAILY_LIMIT = 3;
export const ADDRESS_DAILY_LIMIT = 3;
export const DAILY_CAP = 30;
/** How long the confirmation link works. */
export const CONFIRM_TTL_DAYS = 7;
export const DEFAULT_FROM = 'Misra <digest@send.avidor.org>';
/** Replies must reach a real mailbox: the sending address cannot receive mail. */
export const DEFAULT_REPLY_TO = 'raanan@avidor.org';
export const SUBJECT = 'Confirm your email for Misra job alerts';
const RESEND_URL = 'https://api.resend.com/emails';
const REQUEST_TIMEOUT_MS = 10_000;
const MAIL_SECRETS_MIN_LENGTH = 16;
const ADDRESS = /^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;
const FROM = /^[^\r\n<>]*<[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+>$|^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;

/** What sending needs, kept apart so that turning the email off works even when the mail secrets are not set. */
export interface MailConfig {
  resendApiKey: string;
  from: string;
  replyTo: string;
  hashSecret: string;
}

export interface Config extends BaseConfig {
  /** `DIGEST_SIGNUP_ENABLED` is exactly "true". Anything else, or nothing, keeps signing up closed. */
  signupEnabled: boolean;
  mail: MailConfig | undefined;
}

export interface Deps {
  config: Config | undefined;
  fetch: BaseDeps['fetch'];
  now: () => Date;
}

/** The secrets. Returns what is missing or malformed by name, never by value; a missing mail secret only disables sending. */
export function loadConfig(get: (name: string) => string | undefined): { config: Config; mailMissing: string[] } | { missing: string[] } {
  const base = loadBase(get);
  if ('missing' in base) return base;
  const read = (name: string): string => (get(name) ?? '').trim();
  const resendApiKey = read('RESEND_API_KEY');
  const from = read('DIGEST_FROM_EMAIL') || DEFAULT_FROM;
  const replyTo = read('DIGEST_REPLY_TO_EMAIL') || DEFAULT_REPLY_TO;
  const hashSecret = read('IP_HASH_SECRET');
  const mailMissing: string[] = [];
  if (!resendApiKey) mailMissing.push('RESEND_API_KEY');
  if (!FROM.test(from)) mailMissing.push('DIGEST_FROM_EMAIL');
  if (!ADDRESS.test(replyTo)) mailMissing.push('DIGEST_REPLY_TO_EMAIL');
  if (hashSecret.length < MAIL_SECRETS_MIN_LENGTH) mailMissing.push('IP_HASH_SECRET');
  const mail = mailMissing.length > 0 ? undefined : { resendApiKey, from, replyTo, hashSecret };
  return { config: { ...base.config, signupEnabled: read('DIGEST_SIGNUP_ENABLED') === 'true', mail }, mailMissing };
}

// ---- Answers ---------------------------------------------------------------------------------------------------------

// The shared answers allow only `content-type, apikey` (the confirm and unsubscribe functions take no token); this
// function is called with the user's bearer token, so the browser's preflight must be told it may send `authorization`.
function withAuthHeader(origin: string | null, extra: Record<string, string>): Record<string, string> {
  return origin === ALLOWED_ORIGIN
    ? { 'access-control-allow-headers': 'content-type, authorization, apikey, x-client-info', ...extra }
    : extra;
}

function reply(origin: string | null, status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return replyShared(origin, status, body, withAuthHeader(origin, extra));
}

function fail(origin: string | null, status: number, code: string, message: string, headers: Record<string, string> = {}): Response {
  return failShared(origin, status, code, message, withAuthHeader(origin, headers));
}

// ---- Keys of the counters --------------------------------------------------------------------------------------------

async function hmac(key: BufferSource, message: string): Promise<ArrayBuffer> {
  const cryptoKey = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(message));
}

/** A keyed hash of a thing and the day. The salt of the day comes from the secret, so yesterday's hashes match nothing,
 *  and `kind` keeps a user id and an address from ever sharing a key. */
export async function hashKey(secret: string, day: string, kind: 'user' | 'address', value: string): Promise<string> {
  const salt = await hmac(new TextEncoder().encode(secret), day);
  const hash = new Uint8Array(await hmac(salt, `${kind}:${value}`));
  return Array.from(hash, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

// ---- The email -------------------------------------------------------------------------------------------------------

export function renderEmail(frequency: TurnOn['frequency'], link: string): { subject: string; text: string } {
  return {
    subject: SUBJECT,
    text: [
      'Hello,',
      '',
      `Someone entered this email address on Misra (${PAGE_URL.replace(/email\.html$/u, '')}) to get a ${frequency} list of new job postings that match the filters they chose.`,
      '',
      'Nothing is sent until you confirm. To confirm, open this link and press the Confirm button:',
      '',
      link,
      '',
      `The link works for ${String(CONFIRM_TTL_DAYS)} days.`,
      '',
      'If this was not you, ignore this email. Nothing is sent to this address unless it is confirmed.',
      '',
      '--',
      'Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project. Contact: raanan@avidor.org',
      '',
    ].join('\n'),
  };
}

/** Resend did not accept the email. `refused` is a 4xx: the email was definitely not sent, so the place can be given back. */
class SendFailure extends Error {
  constructor(
    message: string,
    readonly refused: boolean,
  ) {
    super(message);
  }
}

async function sendConfirmation(deps: Deps, mail: MailConfig, to: string, content: { subject: string; text: string }): Promise<void> {
  const response = await deps.fetch(RESEND_URL, {
    method: 'POST',
    headers: { authorization: `Bearer ${mail.resendApiKey}`, 'content-type': 'application/json' },
    body: JSON.stringify({ from: mail.from, to: [to], reply_to: mail.replyTo, subject: content.subject, text: content.text }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    // Only the status: an answer body can echo the request, and the request holds the address and the link.
    await response.body?.cancel().catch(() => undefined);
    const message = `Resend answered ${String(response.status)}`;
    // A 5xx or a timeout may have queued the email, so only a 4xx counts as "not sent".
    throw new SendFailure(message, response.status < 500);
  }
  await response.body?.cancel().catch(() => undefined);
}

// ---- The request -----------------------------------------------------------------------------------------------------

function secondsUntilTomorrow(now: Date): string {
  const tomorrow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return String(Math.max(1, Math.ceil((tomorrow - now.getTime()) / 1000)));
}

/** The JSON object of the request, or why there is none. */
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

function invalid(origin: string | null, fields: FieldErrors): Response {
  return reply(origin, 400, {
    ok: false,
    error: { code: 'invalid_input', message: 'Some details need another look.', fields },
  });
}

const RESERVATIONS = ['ok', 'user_limit', 'address_limit', 'daily_cap'] as const;
const STARTS = ['started', 'already_on'] as const;
const OFFS = ['unsubscribed', 'already_unsubscribed', 'unknown'] as const;

interface Who {
  id: string;
  email: string | undefined;
}

async function turnOn(origin: string | null, deps: Deps, config: Config, who: Who, request: TurnOn): Promise<Response> {
  const { mail } = config;
  if (!config.signupEnabled) {
    return fail(origin, 403, 'signup_closed', 'The email is not open yet, so nothing was turned on.');
  }
  if (!mail) {
    console.error('email-settings: the mail secrets are missing or malformed (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The email service is not set up yet.');
  }
  const address = request.address ?? who.email;
  if (address === undefined || !ADDRESS.test(address)) {
    return invalid(origin, { address: 'Enter the email address to send to.' });
  }

  const now = deps.now();
  const day = now.toISOString().slice(0, 10);
  const userKey = await hashKey(mail.hashSecret, day, 'user', who.id);
  const addressKey = await hashKey(mail.hashSecret, day, 'address', address.toLowerCase());
  const retryAfter = { 'retry-after': secondsUntilTomorrow(now) };
  const reservation = await callDatabase(
    deps,
    config,
    'confirm_email_reserve',
    {
      p_day: day,
      p_user_hash: userKey,
      p_address_hash: addressKey,
      p_user_limit: USER_DAILY_LIMIT,
      p_address_limit: ADDRESS_DAILY_LIMIT,
      p_daily_cap: DAILY_CAP,
    },
    RESERVATIONS,
  );
  if (reservation === 'user_limit') {
    return fail(origin, 429, 'rate_limited', `You have asked for ${String(USER_DAILY_LIMIT)} confirmation emails today, the most allowed. Please try again tomorrow.`, retryAfter);
  }
  if (reservation === 'address_limit') {
    return fail(origin, 429, 'address_limited', 'That address has already been sent as many confirmation emails as are allowed today. Please try again tomorrow.', retryAfter);
  }
  if (reservation === 'daily_cap') {
    return fail(origin, 503, 'daily_cap_reached', 'Too many confirmation emails were sent today. Please try again tomorrow.', retryAfter);
  }

  /** Gives the place back. Losing a place only means a limit is reached a little earlier today, so a failure is logged and goes on. */
  const giveBack = async (): Promise<void> => {
    try {
      await callDatabase(deps, config, 'confirm_email_release', { p_day: day, p_user_hash: userKey, p_address_hash: addressKey }, ['ok']);
    } catch (error) {
      console.error('email-settings: could not give the place back', describe(error));
    }
  };

  let started: (typeof STARTS)[number];
  let confirm: { token: string; hash: string };
  try {
    confirm = await newToken();
    const unsubscribe = await newToken();
    started = await callDatabase(
      deps,
      config,
      'subscribe_start',
      {
        p_user_id: who.id,
        p_address: address,
        p_frequency: request.frequency,
        p_filters: request.filters,
        p_confirm_hash: confirm.hash,
        p_confirm_expires: new Date(now.getTime() + CONFIRM_TTL_DAYS * 24 * 60 * 60 * 1000).toISOString(),
        p_unsubscribe_hash: unsubscribe.hash,
        p_now: now.toISOString(),
      },
      STARTS,
    );
  } catch (error) {
    await giveBack(); // nothing was stored and nothing was sent
    throw error;
  }
  if (started === 'already_on') {
    await giveBack(); // nothing changed and nothing is sent
    return reply(origin, 200, { ok: true, state: 'on' });
  }

  try {
    await sendConfirmation(deps, mail, address, renderEmail(request.frequency, `${PAGE_URL}#token=${confirm.token}`));
  } catch (error) {
    console.error('email-settings: sending failed', error instanceof SendFailure ? error.message : describe(error));
    if (error instanceof SendFailure && error.refused) {
      await giveBack();
      return fail(origin, 502, 'send_failed', 'The confirmation email could not be sent. Please try again later.');
    }
    // The outcome is unknown (timeout, network error, 5xx): the place stays used, and the answer says so.
    return fail(origin, 502, 'send_unconfirmed', 'We could not confirm that the email was sent. Wait a few minutes, check your inbox, and ask again only if it did not come.');
  }
  return reply(origin, 200, { ok: true, state: 'waiting' });
}

async function turnOff(origin: string | null, deps: Deps, config: Config, who: Who): Promise<Response> {
  const answer = await callDatabase(deps, config, 'unsubscribe_user', { p_user_id: who.id, p_now: deps.now().toISOString() }, OFFS);
  return reply(origin, 200, { ok: true, status: answer === 'unknown' ? 'not_subscribed' : answer });
}

export async function handleEmailSettings(request: Request, deps: Deps): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin !== null && origin !== ALLOWED_ORIGIN) {
    return fail(null, 403, 'origin_not_allowed', 'This address may not call this function.');
  }
  if (request.method === 'OPTIONS') return reply(origin, 204, null);
  if (request.method !== 'POST') {
    return fail(origin, 405, 'method_not_allowed', 'Use POST.', { allow: 'POST, OPTIONS' });
  }
  if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return fail(origin, 415, 'unsupported_media_type', 'Send the request as application/json.');
  }
  const { config } = deps;
  if (!config) {
    console.error('email-settings: the secrets are missing or malformed (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }
  const token = bearerToken(request.headers);
  if (!token) return fail(origin, 401, 'not_signed_in', 'Sign in to change your email settings.');

  try {
    const body = await readJson(request);
    if (body === 'too_large') {
      return fail(origin, 413, 'payload_too_large', `The request can be at most ${String(MAX_BODY_BYTES)} bytes.`);
    }
    if (body === 'not_an_object') return fail(origin, 400, 'invalid_request', 'Send a JSON object with an action.');
    const who = await identify(deps, config, token, 'email-settings');
    if (who === 'down') {
      return fail(origin, 503, 'service_unavailable', 'The sign-in service did not answer, so nothing was changed. Please try again.');
    }
    if (who === 'refused') {
      return fail(origin, 401, 'not_signed_in', 'Your sign-in was not accepted, so nothing was changed. Sign in again and retry.');
    }
    const validation = validateRequest(body);
    if (validation === 'unknown_action') return fail(origin, 400, 'invalid_request', 'The action must be turn_on or turn_off.');
    if (!validation.ok) return invalid(origin, validation.fields);
    return validation.request.action === 'turn_off'
      ? await turnOff(origin, deps, config, who)
      : await turnOn(origin, deps, config, who, validation.request);
  } catch (error) {
    console.error('email-settings: failed', describe(error));
    return fail(origin, 500, 'server_error', 'Something went wrong, so please check the state shown and try again.');
  }
}
