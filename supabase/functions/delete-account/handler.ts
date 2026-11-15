// The logic of the `delete-account` Edge Function (PLAN.md step 4.4; PRD FR-1 AC 4): a signed-in user deletes their own
// account in one step. The auth user is removed through the Admin API, and every row that points at it (`profiles`,
// `user_prefs`, `user_jobs`) goes with it because each foreign key is `on delete cascade`.
//
// Everything that touches the outside world comes in through `Deps` (the secrets, `fetch`), so the vitest suite runs this
// file as it is with a fake Auth service. `index.ts` is the only file that knows Deno.
//
// Who may be deleted: only the owner of the access token in the request. The id comes from Supabase Auth's answer about
// that token (`GET /auth/v1/user`, which checks the signature, the expiry and that the session still exists), never from
// the body, the query string or the token's own unchecked claims. Nothing in the request can name another user.
// Nothing is logged but status codes: no token, no email, no user id.

export const ALLOWED_ORIGIN = 'https://ravidorr.github.io';
const REQUEST_TIMEOUT_MS = 10_000;
const MAX_TOKEN_LENGTH = 4096;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;

export interface Config {
  supabaseUrl: string;
  serviceRoleKey: string;
}

/** The part of `fetch` this function uses; the global `fetch` fits it. */
export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export interface Deps {
  config: Config | undefined;
  fetch: Fetch;
}

/** The function's secrets (Supabase supplies both to every function). Returns what is missing by name, never by value. */
export function loadConfig(get: (name: string) => string | undefined): { config: Config } | { missing: string[] } {
  const read = (name: string): string => (get(name) ?? '').trim();
  const supabaseUrl = read('SUPABASE_URL').replace(/\/+$/u, '');
  const serviceRoleKey = read('SUPABASE_SERVICE_ROLE_KEY');
  const missing: string[] = [];
  if (!/^https?:\/\//u.test(supabaseUrl)) missing.push('SUPABASE_URL');
  if (!serviceRoleKey) missing.push('SUPABASE_SERVICE_ROLE_KEY');
  return missing.length > 0 ? { missing } : { config: { supabaseUrl, serviceRoleKey } };
}

type ErrorCode =
  | 'origin_not_allowed'
  | 'method_not_allowed'
  | 'not_signed_in'
  | 'delete_failed'
  | 'delete_unconfirmed'
  | 'service_unavailable'
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
  headers: Record<string, string> = {},
): Response {
  return reply(origin, status, { ok: false, error: { code, message } }, headers);
}

/** The bearer token of the request, or undefined when there is none or it cannot be a token. */
export function bearerToken(headers: Headers): string | undefined {
  const match = /^Bearer ([A-Za-z0-9._~+/=-]+)$/u.exec((headers.get('authorization') ?? '').trim());
  const token = match?.[1];
  return token && token.length <= MAX_TOKEN_LENGTH ? token : undefined;
}

/** Why the caller could not be identified: Auth refused the token (`refused`) or Auth could not be asked (`down`). */
type Identity = { id: string } | 'refused' | 'down';

/** Asks Supabase Auth who owns the token. Only a 200 with an authenticated user and a UUID id counts. */
async function identify(deps: Deps, config: Config, token: string): Promise<Identity> {
  let response: Response;
  try {
    response = await deps.fetch(`${config.supabaseUrl}/auth/v1/user`, {
      method: 'GET',
      headers: { apikey: config.serviceRoleKey, authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    console.error('delete-account: the user check could not be reached');
    return 'down';
  }
  if (response.status >= 500) {
    console.error(`delete-account: the user check answered ${String(response.status)}`);
    return 'down';
  }
  if (!response.ok) return 'refused';
  const user = (await response.json().catch(() => null)) as { id?: unknown; role?: unknown } | null;
  if (typeof user?.id !== 'string' || !UUID.test(user.id) || user.role !== 'authenticated') return 'refused';
  return { id: user.id };
}

export async function handleDelete(request: Request, deps: Deps): Promise<Response> {
  const origin = request.headers.get('origin');
  if (origin !== null && origin !== ALLOWED_ORIGIN) {
    return fail(null, 403, 'origin_not_allowed', 'This address may not call this function.');
  }
  if (request.method === 'OPTIONS') return reply(origin, 204, null);
  if (request.method !== 'POST') {
    return fail(origin, 405, 'method_not_allowed', 'Use POST.', { allow: 'POST, OPTIONS' });
  }
  const { config } = deps;
  if (!config) {
    console.error('delete-account: the secrets are missing (see the log at start-up for their names)');
    return fail(origin, 500, 'server_error', 'The service is not set up yet.');
  }
  const token = bearerToken(request.headers);
  if (!token) return fail(origin, 401, 'not_signed_in', 'Sign in to delete your account.');

  const who = await identify(deps, config, token);
  if (who === 'down') {
    return fail(origin, 503, 'service_unavailable', 'The sign-in service did not answer. Your account was not deleted.');
  }
  if (who === 'refused') {
    return fail(
      origin,
      401,
      'not_signed_in',
      'Your sign-in was not accepted, so nothing was deleted by this request. Sign in again to see whether your account still exists.',
    );
  }

  let response: Response;
  try {
    response = await deps.fetch(`${config.supabaseUrl}/auth/v1/admin/users/${who.id}`, {
      method: 'DELETE',
      headers: {
        apikey: config.serviceRoleKey,
        authorization: `Bearer ${config.serviceRoleKey}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ should_soft_delete: false }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    // A timeout or a dropped connection: the deletion may have happened.
    console.error('delete-account: the deletion did not answer');
    return fail(origin, 502, 'delete_unconfirmed', 'We could not confirm that your account was deleted. Please try again.');
  }
  // 404: the user was already gone (a second tab, or a repeat that crossed the first). The goal is reached.
  if (response.ok || response.status === 404) return reply(origin, 200, { ok: true });
  console.error(`delete-account: the deletion answered ${String(response.status)}`);
  if (response.status >= 500) {
    return fail(origin, 502, 'delete_unconfirmed', 'We could not confirm that your account was deleted. Please try again.');
  }
  return fail(origin, 502, 'delete_failed', 'Your account was not deleted. Please try again later.');
}
