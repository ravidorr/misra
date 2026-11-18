// Deleting an auth user (PLAN.md step 4.4; PLAN_2.md step 5.2): the one place that calls the Admin API, shared by
// `delete-account` (a user deletes their own account) and `admin-account` (the maintainer deletes one from a support
// email). Each caller decides WHO may be deleted; this file only deletes the id it is given.
//
// The auth user is removed through the Admin API, and every row that points at it (`profiles`, `user_prefs`,
// `user_jobs`, `subscriptions`, `emailed`) goes with it because each foreign key is `on delete cascade`.
//
// The id is put in a URL, so it must be a UUID: anything else is refused before a request is made. Nothing is logged but
// a status code and the caller's name: no id, no token, no address.

import type { Config, Fetch } from './subscription.ts';

const REQUEST_TIMEOUT_MS = 5000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;

export function isUserId(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

/**
 * `deleted`: the account is gone (also when it was already gone). `unconfirmed`: a timeout, a dropped connection or a 5xx,
 * so it may or may not be gone. `failed`: the Admin API refused, the account is still there.
 */
export type DeleteResult = 'deleted' | 'unconfirmed' | 'failed';

export async function deleteAuthUser(
  deps: { fetch: Fetch },
  config: Config,
  id: string,
  name: string,
): Promise<DeleteResult> {
  if (!isUserId(id)) {
    console.error(`${name}: the deletion was refused, the id is not a UUID`);
    return 'failed';
  }
  let response: Response;
  try {
    response = await deps.fetch(`${config.supabaseUrl}/auth/v1/admin/users/${id}`, {
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
    console.error(`${name}: the deletion did not answer`);
    return 'unconfirmed';
  }
  // 404: the user was already gone (a second tab, or a repeat that crossed the first). The goal is reached.
  if (response.ok || response.status === 404) return 'deleted';
  console.error(`${name}: the deletion answered ${String(response.status)}`);
  return response.status >= 500 ? 'unconfirmed' : 'failed';
}
