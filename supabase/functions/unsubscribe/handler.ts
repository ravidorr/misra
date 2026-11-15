// The logic of the `unsubscribe` Edge Function (PLAN.md step 6.1; PRD FR-8 AC 7): one click, no sign-in, token-based.
// The unsubscribe link of every digest email is `<function url>?t=<token>`, and the same URL is the `List-Unsubscribe`
// header of the email (step 6.3), so both of these work:
//
//   GET   the user's click. It unsubscribes at once and answers 303 to the static page (templates/email.html) with a
//         word in the fragment (`#unsubscribed`, `#already-unsubscribed`, `#link-not-valid`, `#try-again`), because
//         a function cannot render a page. The token is not in the Location.
//   POST  RFC 8058 one-click: the mailbox provider POSTs `List-Unsubscribe=One-Click` (form-encoded) to the header URL.
//         The body is ignored: the token in the URL is the credential. The answer is JSON,
//         200 {ok, status: 'unsubscribed' | 'already_unsubscribed'} (a repeat is not an error), 404 link_not_valid,
//         500 server_error (the provider may retry).
//
// HEAD and OPTIONS never act (link scanners send them): 405. There is no CORS and no Origin check: the browser is sent
// here by a plain link, a mailbox provider has no Origin, and the token is the only credential. A GET can be fired by a
// link scanner that follows every link in a message. That unsubscribes someone who did not ask to, which is the safe
// direction and is accepted (the user turns the email on again), unlike confirming, which a GET never does.
//
// The token is in the URL, so the platform's request log holds it (README: "What is logged"). It can only unsubscribe.
// Nothing here logs a token, a hash, an address or a user id: only status codes.

import { callDatabase, describe, fail, hashToken, isToken, redirectToPage, reply } from '../_shared/subscription.ts';
import type { Deps } from '../_shared/subscription.ts';

export { loadConfig } from '../_shared/subscription.ts';
export type { Config, Deps, Fetch } from '../_shared/subscription.ts';

const ANSWERS = ['unsubscribed', 'already_unsubscribed', 'unknown'] as const;

const NOT_VALID = 'This link is not valid, or it is no longer active.';

export async function handleUnsubscribe(request: Request, deps: Deps): Promise<Response> {
  const isGet = request.method === 'GET';
  if (!isGet && request.method !== 'POST') {
    return fail(null, 405, 'method_not_allowed', 'Use the link in the email.', { allow: 'GET, POST' });
  }
  const { config } = deps;
  if (!config) {
    console.error('unsubscribe: the secrets are missing or malformed (see the log at start-up for their names)');
    return isGet ? redirectToPage('try-again') : fail(null, 500, 'server_error', 'The service is not set up yet.');
  }

  try {
    const token = new URL(request.url).searchParams.get('t');
    if (!isToken(token)) {
      return isGet ? redirectToPage('link-not-valid') : fail(null, 404, 'link_not_valid', NOT_VALID);
    }
    const answer = await callDatabase(
      deps,
      config,
      'unsubscribe_subscription',
      { p_hash: await hashToken(token), p_now: deps.now().toISOString() },
      ANSWERS,
    );
    if (isGet) {
      return redirectToPage(
        { unsubscribed: 'unsubscribed', already_unsubscribed: 'already-unsubscribed', unknown: 'link-not-valid' }[
          answer
        ],
      );
    }
    return answer === 'unknown'
      ? fail(null, 404, 'link_not_valid', NOT_VALID)
      : reply(null, 200, { ok: true, status: answer });
  } catch (error) {
    console.error('unsubscribe: failed', describe(error));
    return isGet ? redirectToPage('try-again') : fail(null, 500, 'server_error', 'Something went wrong. Please try again.');
  }
}
