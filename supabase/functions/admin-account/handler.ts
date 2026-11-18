// The logic of the `admin-account` Edge Function (PLAN_2.md step 5.2): the maintainer, signed in on the admin page, types
// the address from a support email and either ends that person's job alerts (what the unsubscribe link does) or deletes
// their account (what "Delete my account" does). Everything that touches the outside world comes in through `Deps`, so the
// vitest suite runs this file as it is; `index.ts` is the only file that knows Deno.
//
// Who may call it. The first thing the function does (after it has a token) is ask the database `public.is_admin()` WITH THE CALLER'S TOKEN (the
// public key as `apikey`, the caller's token as `Authorization`), never with the service-role key: the answer is about the
// caller. Everything else (a missing or malformed token, a token that is not an admin's, a method other than POST, an
// origin other than the dashboard's) is answered 404 with no body, the same for all of them, so the function does not say
// that it exists. The one other answer before the check is the CORS preflight (204; it carries no token and nothing in it
// depends on who asks), and a check that could not be made is 503 (not an oracle: it is the same for an admin and for
// anyone else). Then it asks Supabase Auth whether the session is still live (`identify`, as delete-account does), so a signed-out or
// revoked session is a 404 too. Only after both checks does the service-role key do any work.
//
// What the request is (POST, JSON, at most 2 KB): { "address": "<the address>", "action": "end_alerts" | "delete_account" }.
// What the answers are, for the admin only (every body is JSON; each has `ok`):
//
//   200 { ok: true, result: "done", message: "Done." }                          the action was done
//   200 { ok: true, result: "no_account", message: "No account uses that address." }   nothing matched, nothing was done
//   400 { ok: false, error: { code: "bad_request", message } }                  the body is not what is described above
//   404 (no body)                                                               not an admin, or not a POST, or no token (a
//                                                                               401 of the check, an expired session, is a 404 too)
//   500 { ok: false, error: { code: "server_error", message } }                 a missing secret, or a database call failed
//   409 { ok: false, error: { code: "ambiguous", message } }                   more than one account has that sign-in email (delete only); nothing was done
//   502 { ok: false, error: { code: "delete_failed" | "delete_unconfirmed", message } }   the Admin API refused, or did not answer
//   503 { ok: false, error: { code: "service_unavailable", message } }         the admin check could not be made; nothing was done
//
// An account "uses" an address when it is the email the person signs in with, or, for `end_alerts` only, the address the
// digest goes to (`public.admin_account_ids`). `delete_account` goes by the sign-in email only, so a digest address typed by
// someone else cannot delete another person's account. A person with no subscription, or who had already turned the email
// off, still gets "Done.": the goal (no alerts) is reached.
//
// The log. One `admin_log` row per action, `action` = "alerts ended" or "account deleted", `target` empty, and the time:
// no address, no hash of it, no user id. It is written BEFORE the action, so an action is never done without a record
// (if the log cannot be written, nothing is done and the answer is 500). So a row means "attempted": a failed action still has its row. When no account matches, nothing is written.
//
// What it never does: log the address (nothing is logged but function names and status codes), return a row or an id,
// or email the person.

import { bearerToken, identify } from '../_shared/identity.ts';
import { deleteAuthUser, isUserId } from '../_shared/delete-user.ts';
import {
  ALLOWED_ORIGIN,
  DatabaseError,
  MAX_BODY_BYTES,
  callDatabase,
  callRpc,
  describe,
  loadConfig as loadBase,
  readLimited,
} from '../_shared/subscription.ts';
import type { Config as BaseConfig, Deps as BaseDeps } from '../_shared/subscription.ts';

export { ALLOWED_ORIGIN } from '../_shared/subscription.ts';
export type { Fetch } from '../_shared/subscription.ts';

const REQUEST_TIMEOUT_MS = 5000;
const ADDRESS = /^[^\s<>@,"]+@[^\s<>@,"]+[.][^\s<>@,"]+$/u;
const NAME = 'admin-account';

export interface Config extends BaseConfig {
  /** `SUPABASE_ANON_KEY`: the public key, sent as `apikey` with the caller's token to ask `is_admin()`. */
  anonKey: string;
}

export interface Deps extends BaseDeps {
  config: Config | undefined;
}

/** The secrets (Supabase supplies all three to every function). Returns what is missing by name, never by value. */
export function loadConfig(get: (name: string) => string | undefined): { config: Config } | { missing: string[] } {
  const base = loadBase(get);
  const anonKey = (get('SUPABASE_ANON_KEY') ?? '').trim();
  if ('missing' in base) return { missing: anonKey ? base.missing : [...base.missing, 'SUPABASE_ANON_KEY'] };
  return anonKey ? { config: { ...base.config, anonKey } } : { missing: ['SUPABASE_ANON_KEY'] };
}

type Action = 'end_alerts' | 'delete_account';

const LOG_TEXT: Record<Action, string> = { end_alerts: 'alerts ended', delete_account: 'account deleted' };

function headers(origin: string | null, extra: Record<string, string> = {}): Record<string, string> {
  const cors: Record<string, string> =
    origin === ALLOWED_ORIGIN
      ? {
          'access-control-allow-origin': ALLOWED_ORIGIN,
          'access-control-allow-methods': 'POST, OPTIONS',
          'access-control-allow-headers': 'content-type, authorization, apikey, x-client-info',
          'access-control-max-age': '86400',
        }
      : {};
  return { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', vary: 'Origin', ...cors, ...extra };
}

/** The one answer to everything that is not an admin's POST: no body, no detail. */
function notFound(origin: string | null): Response {
  return new Response(null, { status: 404, headers: headers(origin) });
}

function json(origin: string | null, status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: headers(origin, { 'content-type': 'application/json; charset=utf-8' }),
  });
}

function fail(origin: string | null, status: number, code: string, message: string): Response {
  return json(origin, status, { ok: false, error: { code, message } });
}

/** True or false when the database answered about the caller; 'down' when it could not be asked. */
async function isAdmin(deps: Deps, config: Config, token: string): Promise<boolean | 'down'> {
  let response: Response;
  try {
    response = await deps.fetch(`${config.supabaseUrl}/rest/v1/rpc/is_admin`, {
      method: 'POST',
      headers: { apikey: config.anonKey, authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: '{}',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    console.error(`${NAME}: the admin check could not be reached`);
    return 'down';
  }
  if (response.status >= 500 || response.status === 408 || response.status === 429) {
    await response.body?.cancel().catch(() => undefined);
    console.error(`${NAME}: the admin check answered ${String(response.status)}`);
    return 'down';
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined); // a token the database does not accept: not an admin
    // Logged as a status code so a key the gateway does not accept (every admin call would be a 404) shows in the logs.
    console.error(`${NAME}: the admin check refused the request with ${String(response.status)}`);
    return false;
  }
  const answer: unknown = await response.json().catch(() => null);
  if (answer !== true && answer !== false) console.error(`${NAME}: the admin check answered something that is not true or false`);
  return answer === true;
}

/** The request body as an address and an action, or undefined when it is anything else. */
async function readRequest(request: Request): Promise<{ address: string; action: Action } | undefined> {
  if (Number(request.headers.get('content-length') ?? '0') > MAX_BODY_BYTES) return undefined;
  const text = await readLimited(request, MAX_BODY_BYTES);
  if (text === undefined) return undefined;
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return undefined;
  const { address, action } = body as { address?: unknown; action?: unknown };
  if (action !== 'end_alerts' && action !== 'delete_account') return undefined;
  const trimmed = typeof address === 'string' ? address.trim() : '';
  if (trimmed.length < 3 || trimmed.length > 254 || !ADDRESS.test(trimmed)) return undefined;
  return { address: trimmed, action };
}

/** The ids of the accounts that use the address (see the header). Throws a DatabaseError, never with the address in it. */
async function findAccounts(deps: Deps, config: Config, address: string, action: Action): Promise<string[]> {
  const rows = await callRpc(deps, config, 'admin_account_ids', {
    p_address: address,
    p_login_only: action === 'delete_account',
  });
  if (!Array.isArray(rows) || !rows.every(isUserId)) {
    throw new DatabaseError('admin_account_ids gave an answer this function does not know');
  }
  return rows;
}

/** One `admin_log` row: the action and (by the column default) the time. Nothing about the person. */
async function writeLog(deps: Deps, config: Config, action: Action): Promise<void> {
  const response = await deps.fetch(`${config.supabaseUrl}/rest/v1/admin_log`, {
    method: 'POST',
    headers: {
      apikey: config.serviceRoleKey,
      authorization: `Bearer ${config.serviceRoleKey}`,
      'content-type': 'application/json',
      prefer: 'return=minimal',
    },
    body: JSON.stringify({ action: LOG_TEXT[action] }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  await response.body?.cancel().catch(() => undefined);
  if (!response.ok) throw new DatabaseError(`admin_log answered ${String(response.status)}`);
}

export async function handleAdminAccount(request: Request, deps: Deps): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin !== null && origin !== ALLOWED_ORIGIN) return notFound(null);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: headers(origin) });
  if (request.method !== 'POST') return notFound(origin);
  const token = bearerToken(request.headers);
  if (!token) return notFound(origin);
  const { config } = deps;
  if (!config) {
    console.error(`${NAME}: the secrets are missing (see the log at start-up for their names)`);
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }
  const admin = await isAdmin(deps, config, token);
  if (admin === 'down') return fail(origin, 503, 'service_unavailable', 'The admin check did not answer. Nothing was done.');
  if (!admin) return notFound(origin);
  // PostgREST checks the token's signature and expiry only. Ask Auth too, so a signed-out or revoked session is refused
  // as well (the same check as delete-account), before anything is looked up, logged or done.
  const session = await identify(deps, config, token, NAME);
  if (session === 'down') return fail(origin, 503, 'service_unavailable', 'The admin check did not answer. Nothing was done.');
  if (session === 'refused') return notFound(origin);

  try {
    const asked = await readRequest(request);
    if (!asked) return fail(origin, 400, 'bad_request', 'Send an address and one action: end_alerts or delete_account.');
    const ids = await findAccounts(deps, config, asked.address, asked.action);
    if (ids.length === 0) return json(origin, 200, { ok: true, result: 'no_account', message: 'No account uses that address.' });
    if (asked.action === 'delete_account' && ids.length > 1) {
      // Never delete more than one account on one request: the maintainer looks at that case by hand.
      return fail(origin, 409, 'ambiguous', 'More than one account uses that address. Nothing was done.');
    }
    await writeLog(deps, config, asked.action);
    for (const id of ids) {
      if (asked.action === 'end_alerts') {
        await callDatabase(deps, config, 'unsubscribe_user', { p_user_id: id, p_now: deps.now().toISOString() }, [
          'unsubscribed',
          'already_unsubscribed',
          'unknown',
        ] as const);
        continue;
      }
      const result = await deleteAuthUser(deps, config, id, NAME);
      if (result === 'unconfirmed') {
        return fail(origin, 502, 'delete_unconfirmed', 'The deletion could not be confirmed. Try again.');
      }
      if (result === 'failed') return fail(origin, 502, 'delete_failed', 'The account was not deleted. Try again later.');
    }
    return json(origin, 200, { ok: true, result: 'done', message: 'Done.' });
  } catch (error) {
    console.error(`${NAME}: failed`, describe(error));
    return fail(origin, 500, 'server_error', 'Something went wrong. Try again.');
  }
}
