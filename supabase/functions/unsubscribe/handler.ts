// The logic of the `unsubscribe` Edge Function (PLAN.md step 6.1; PRD FR-8 AC 7): one click, no sign-in, token-based.
// The unsubscribe link of every digest email is `<function url>?t=<token>`, and the same URL is the `List-Unsubscribe`
// header of the email (step 6.3), so a person's click and the mail client's own button both work.
//
// What each method does (a table, kept in step with supabase/README.md):
//
//   method   | does                                                                   | touches the database
//   ---------|------------------------------------------------------------------------|---------------------
//   GET      | the user's click: unsubscribes at once, then 303 to the static page    | yes
//            | (templates/email.html) with a word in the fragment (`#unsubscribed`,   |
//            | `#already-unsubscribed`, `#link-not-valid`, `#try-again`), because a   |
//            | function cannot render a page. The token is not in the Location.       |
//   POST     | RFC 8058 one-click (Gmail, Apple Mail): the provider POSTs             | yes
//            | `List-Unsubscribe=One-Click` to the header URL; the body is ignored,   |
//            | the token in the URL is the credential. 200 text/plain `Unsubscribed.` |
//            | (also when already unsubscribed); 404 JSON `link_not_valid`; 500 JSON  |
//            | `server_error` (the provider may retry).                               |
//   HEAD     | 200, no body: a scanner sees a healthy link and nothing changes        | never
//   OPTIONS  | 405 (no CORS: a browser is sent here by a plain link)                  | never
//   others   | 405                                                                    | never
//
// There is no CORS and no Origin check: a mailbox provider has no Origin, and the token is the only credential.
// Accepted trade-off: a link scanner (corporate or university, Outlook or Gmail previewers) that fetches the link with GET
// unsubscribes that person. GET and POST are one click with no second button, as the lawyer and RFC 8058 expect, so a scanner
// cannot be told from the person; it is the safe direction (the user turns the email on again). Confirming, in contrast,
// can never happen from a GET (see confirm-email).
//
// The token is in the URL, so the platform's request log holds it (README: "What is logged"). It can only unsubscribe.
// Nothing here logs a token, a hash, an address or a user id: only status codes.

import { callDatabase, describe, emptyOk, fail, hashToken, isToken, plainText, redirectToPage } from '../_shared/subscription.ts';
import type { Deps } from '../_shared/subscription.ts';

export { loadConfig } from '../_shared/subscription.ts';
export type { Config, Deps, Fetch } from '../_shared/subscription.ts';

const ANSWERS = ['unsubscribed', 'already_unsubscribed', 'unknown'] as const;

const NOT_VALID = 'This link is not valid, or it is no longer active.';

export async function handleUnsubscribe(request: Request, deps: Deps): Promise<Response> {
  if (request.method === 'HEAD') return emptyOk(); // nothing is read or changed: no secrets, URL or database
  const isGet = request.method === 'GET';
  if (!isGet && request.method !== 'POST') {
    return fail(null, 405, 'method_not_allowed', 'Use the link in the email.', { allow: 'GET, HEAD, POST' });
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
    return answer === 'unknown' ? fail(null, 404, 'link_not_valid', NOT_VALID) : plainText(200, 'Unsubscribed.');
  } catch (error) {
    console.error('unsubscribe: failed', describe(error));
    return isGet ? redirectToPage('try-again') : fail(null, 500, 'server_error', 'Something went wrong. Please try again.');
  }
}
