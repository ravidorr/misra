// A fake of the database side of the subscription functions, shared by the tests of `confirm-email` and `unsubscribe`.
// It answers the two PostgREST calls exactly as `confirm_subscription` and `unsubscribe_user` do in
// supabase/migrations/20261117100000_subscriptions_and_emailed.sql and 20261118100000_digest_signup_and_limits.sql
// (supabase/tests/subscriptions.sql and digest_signup.sql test the SQL itself),
// over a few in-memory rows, and records every request so a test can check what crossed the wire.
import { vi } from 'vitest';
import type { Mock } from 'vitest';

import { hashToken, makeUnsubscribeToken, newToken } from '../../supabase/functions/_shared/subscription.ts';
import type { Fetch } from '../../supabase/functions/_shared/subscription.ts';
import type { Config, Deps } from '../../supabase/functions/unsubscribe/handler.ts';

export const UNSUBSCRIBE_SECRET = 'unsubscribe-test-secret-0123456789';
export const CONFIG: Config = {
  supabaseUrl: 'https://project.supabase.test',
  serviceRoleKey: 'service-role-test-key',
  unsubscribeSecret: UNSUBSCRIBE_SECRET,
};
export const NOW = new Date('2030-01-02T09:00:00.000Z');

export interface Row {
  /** The subscription id (`subscriptions.user_id`). */
  id: string;
  address: string;
  confirmHash: string | null;
  confirmExpires: string | null;
  unsubscribeHash: string;
  confirmedAt: string | null;
  unsubscribedAt: string | null;
  filters: Record<string, unknown>;
  frequency: 'daily' | 'weekly';
  emailed: number;
}

export interface Subscriber {
  row: Row;
  confirmToken: string;
  unsubscribeToken: string;
}

export type Fault = 'down' | 'status-500' | 'garbage' | 'unknown-word' | 'schedule-down' | 'schedule-500' | 'schedule-garbage' | 'schedule-odd' | undefined;

export interface World {
  deps: Deps;
  rows: Row[];
  requests: { url: string; method: string; headers: Record<string, string>; body: string }[];
  fakeFetch: Mock<Fetch>;
  /** Every secret-ish string a test must never see in a log or an answer. */
  secrets: string[];
}

/**
 * A subscription as the function that turns the email on (step 6.2) writes it: only the confirmation hash is stored, and
 * the unsubscribe token is the stateless `<id>.<HMAC>` the digest sender makes (the hash column holds a value nobody reads).
 */
export async function subscriber(overrides: Partial<Row> = {}): Promise<Subscriber> {
  const confirm = await newToken();
  const placeholder = await newToken();
  const id = overrides.id ?? crypto.randomUUID();
  return {
    confirmToken: confirm.token,
    unsubscribeToken: await makeUnsubscribeToken(UNSUBSCRIBE_SECRET, id),
    row: {
      id,
      address: 'ann@example.test',
      confirmHash: confirm.hash,
      confirmExpires: '2030-01-08T09:00:00.000Z',
      unsubscribeHash: placeholder.hash,
      confirmedAt: null,
      unsubscribedAt: null,
      filters: { levels: ['Intern'] },
      frequency: 'weekly',
      emailed: 2,
      ...overrides,
    },
  };
}

export function world(subscribers: Subscriber[], fault?: Fault, now: Date = NOW): World {
  const rows = subscribers.map((s) => s.row);
  const requests: World['requests'] = [];
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const headers = init.headers as Record<string, string>;
    const body = typeof init.body === 'string' ? init.body : '';
    requests.push({ url, method: String(init.method), headers, body });
    if (init.method === 'GET') return Promise.resolve(schedule(rows, url, fault));
    if (fault === 'down') return Promise.reject(new Error('connection refused'));
    if (fault === 'status-500') return Promise.resolve(new Response('{"message":"secret detail"}', { status: 500 }));
    if (fault === 'garbage') return Promise.resolve(new Response('<html>', { status: 200 }));
    if (fault === 'unknown-word') return Promise.resolve(Response.json('exploded'));
    const args = JSON.parse(body) as { p_hash?: string; p_user_id?: string; p_now: string };
    const name = url.slice(`${CONFIG.supabaseUrl}/rest/v1/rpc/`.length);
    return Promise.resolve(Response.json(name === 'confirm_subscription' ? confirm(rows, args) : unsubscribe(rows, args)));
  });
  const secrets = subscribers.flatMap((s) => [s.confirmToken, s.unsubscribeToken, s.row.confirmHash ?? '', s.row.unsubscribeHash, s.row.address, s.row.id]);
  return { deps: { config: CONFIG, fetch: fakeFetch, now: () => now }, rows, requests, fakeFetch, secrets };
}

/** The PostgREST read of `frequency` by confirmation token hash (what `readSchedule` sends). */
function schedule(rows: Row[], url: string, fault: Fault): Response | Promise<Response> {
  if (fault === 'schedule-down') return Promise.reject(new Error('connection refused'));
  if (fault === 'schedule-500') return new Response('{"message":"secret detail"}', { status: 500 });
  if (fault === 'schedule-garbage') return new Response('<html>', { status: 200 });
  if (fault === 'schedule-odd') return Response.json([{ frequency: 'hourly' }]);
  const hash = new URL(url).searchParams.get('confirm_token_hash')?.replace(/^eq\./u, '');
  const row = rows.find((candidate) => candidate.confirmHash === hash);
  return Response.json(row ? [{ frequency: row.frequency }] : []);
}

function confirm(rows: Row[], args: { p_hash?: string; p_now: string }): string {
  const row = rows.find((candidate) => candidate.confirmHash === args.p_hash);
  if (!row) return 'unknown';
  if (row.unsubscribedAt !== null) return 'unsubscribed';
  if (row.confirmedAt !== null) return 'already_confirmed';
  if (row.confirmExpires === null || Date.parse(row.confirmExpires) <= Date.parse(args.p_now)) return 'expired';
  row.confirmedAt = args.p_now;
  return 'confirmed';
}

function unsubscribe(rows: Row[], args: { p_user_id?: string; p_now: string }): string {
  const row = rows.find((candidate) => candidate.id === args.p_user_id);
  if (!row) return 'unknown';
  if (row.unsubscribedAt !== null) {
    row.emailed = 0;
    return 'already_unsubscribed';
  }
  row.unsubscribedAt = args.p_now;
  row.filters = {};
  row.frequency = 'daily';
  row.confirmHash = null;
  row.confirmExpires = null;
  row.emailed = 0;
  return 'unsubscribed';
}

/** The hash the database holds for a token (the same function the Edge Functions use). */
export const hashOf = hashToken;

/** What was logged through console.error and console.warn, as one string. */
export function logged(...spies: Mock[]): string {
  return spies.flatMap((spy) => spy.mock.calls.map((call: unknown[]) => call.map(String).join(' '))).join('\n');
}
