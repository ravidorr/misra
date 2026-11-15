// The logic of the `confirm-email` Edge Function (PLAN.md step 6.1; PRD FR-8 AC 7): the user presses Confirm on the page
// the confirmation email links to, and the page POSTs the token here. Only then does `consent_confirmed_at` get a time,
// and only a confirmed, not unsubscribed subscription is ever sent a digest.
//
// A GET never confirms anything. Mail scanners and link previewers fetch every link in a message, and a confirmation
// that a GET could complete would be given by a robot, not by the user, and would spoil the consent log. So the link
// in the email opens a static page (templates/email.html) that holds a button, and the button makes this POST. The
// token travels in the URL fragment (never sent to any server) and then in the JSON body, so it is in no request log.
//
// Answers are JSON (Supabase serves a function's HTML as plain text, so a function cannot render a page):
//   200 {ok, status: 'confirmed' | 'already_confirmed'}  a replay is not an error
//   404 link_not_valid   unknown, malformed, or the link of an unsubscribed subscription (its confirmation token is gone)
//   409 unsubscribed     the user unsubscribed; this link will never turn sending back on
//   410 link_expired     the link is older than its expiry; the user turns the email on again for a new one
// CORS answers the dashboard's origin only. Only status codes are logged.

import { ALLOWED_ORIGIN, MAX_BODY_BYTES, callDatabase, describe, fail, hashToken, isToken, readLimited, reply } from '../_shared/subscription.ts';
import type { Deps } from '../_shared/subscription.ts';

export { ALLOWED_ORIGIN, loadConfig } from '../_shared/subscription.ts';
export type { Config, Deps, Fetch } from '../_shared/subscription.ts';

const ANSWERS = ['confirmed', 'already_confirmed', 'expired', 'unsubscribed', 'unknown'] as const;

const NOT_VALID = 'This link is not valid, or it is no longer active.';

/** The token of the request body, or undefined when there is no JSON object with a string `token`. */
async function readToken(request: Request): Promise<{ token: unknown } | 'too_large' | 'not_an_object'> {
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
  return { token: (parsed as Record<string, unknown>).token };
}

export async function handleConfirm(request: Request, deps: Deps): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin !== null && origin !== ALLOWED_ORIGIN) {
    return fail(null, 403, 'origin_not_allowed', 'This address may not call this function.');
  }
  if (request.method === 'OPTIONS') return reply(origin, 204, null);
  if (request.method !== 'POST') {
    return fail(origin, 405, 'method_not_allowed', 'Open the link from your email and press Confirm.', {
      allow: 'POST, OPTIONS',
    });
  }
  if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return fail(origin, 415, 'unsupported_media_type', 'Send the token as application/json.');
  }
  const { config } = deps;
  if (!config) {
    console.error('confirm-email: the secrets are missing or malformed (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }

  try {
    const body = await readToken(request);
    if (body === 'too_large') {
      return fail(origin, 413, 'payload_too_large', `The request can be at most ${String(MAX_BODY_BYTES)} bytes.`);
    }
    if (body === 'not_an_object') return fail(origin, 400, 'invalid_request', 'Send a JSON object with a token.');
    if (!isToken(body.token)) return fail(origin, 404, 'link_not_valid', NOT_VALID);

    const answer = await callDatabase(
      deps,
      config,
      'confirm_subscription',
      { p_hash: await hashToken(body.token), p_now: deps.now().toISOString() },
      ANSWERS,
    );
    switch (answer) {
      case 'confirmed':
      case 'already_confirmed':
        return reply(origin, 200, { ok: true, status: answer });
      case 'unsubscribed':
        return fail(origin, 409, 'unsubscribed', 'You have unsubscribed. Turn the email on again to get a new link.');
      case 'expired':
        return fail(origin, 410, 'link_expired', 'This link has expired. Turn the email on again to get a new one.');
      case 'unknown':
        return fail(origin, 404, 'link_not_valid', NOT_VALID);
    }
  } catch (error) {
    console.error('confirm-email: failed', describe(error));
    return fail(origin, 500, 'server_error', 'Something went wrong. Please try again.');
  }
}
