// Who is calling (PLAN.md step 6.2): the user a signed-in request belongs to, taken only from Supabase Auth's answer
// about the request's access token, never from the body, the query string or the token's own unchecked claims. The
// gateway already refuses a token with a bad signature (`verify_jwt = true` in supabase/config.toml); this asks Auth
// as well, which also checks that the session still exists and gives the user's current email, so the function never
// trusts an address the browser sends as "my own". Plain web standards, like the rest of `_shared`.
//
// Nothing here logs a token, an id or an address: only the status Auth answered with.

import type { Config, Deps } from './subscription.ts';

const MAX_TOKEN_LENGTH = 4096;
const REQUEST_TIMEOUT_MS = 5000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;

/** The bearer token of the request, or undefined when there is none or it cannot be a token. */
export function bearerToken(headers: Headers): string | undefined {
  const match = /^Bearer ([A-Za-z0-9._~+/=-]+)$/u.exec((headers.get('authorization') ?? '').trim());
  const token = match?.[1];
  return token && token.length <= MAX_TOKEN_LENGTH ? token : undefined;
}

/** The signed-in user (the email is what Auth holds for the account, and may be missing), or why there is none. */
export type Identity = { id: string; email: string | undefined } | 'refused' | 'down';

/** Asks Supabase Auth who owns the token. Only a 200 with an authenticated user and a UUID id counts. */
export async function identify(deps: Deps, config: Config, token: string, name: string): Promise<Identity> {
  let response: Response;
  try {
    response = await deps.fetch(`${config.supabaseUrl}/auth/v1/user`, {
      method: 'GET',
      headers: { apikey: config.serviceRoleKey, authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    console.error(`${name}: the user check could not be reached`);
    return 'down';
  }
  if (response.status >= 500 || response.status === 408 || response.status === 429) {
    await response.body?.cancel().catch(() => undefined);
    console.error(`${name}: the user check answered ${String(response.status)}`);
    return 'down';
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    console.error(`${name}: the user check answered ${String(response.status)}`); /* a wrong service key looks like this */
    return 'refused';
  }
  const user = (await response.json().catch(() => null)) as { id?: unknown; role?: unknown; email?: unknown } | null;
  if (user === null) {
    console.error(`${name}: the user check answered with something unreadable`);
    return 'down';
  }
  if (typeof user.id !== 'string' || !UUID.test(user.id) || user.role !== 'authenticated') {
    console.error(`${name}: the user check answered 200 but not with an authenticated user`);
    return 'refused';
  }
  return { id: user.id, email: typeof user.email === 'string' && user.email.trim() ? user.email.trim() : undefined };
}
