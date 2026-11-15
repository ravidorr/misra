// A fake of the database side of the subscription functions, shared by the tests of `confirm-email` and `unsubscribe`.
// It answers the two PostgREST calls exactly as `confirm_subscription` and `unsubscribe_subscription` do in
// supabase/migrations/20261117100000_subscriptions_and_emailed.sql (supabase/tests/subscriptions.sql tests the SQL itself),
// over a few in-memory rows, and records every request so a test can check what crossed the wire.
import { vi } from 'vitest';
import type { Mock } from 'vitest';

import { hashToken, newToken } from '../../supabase/functions/_shared/subscription.ts';
import type { Config, Deps, Fetch } from '../../supabase/functions/_shared/subscription.ts';

export const CONFIG: Config = { supabaseUrl: 'https://project.supabase.test', serviceRoleKey: 'service-role-test-key' };
export const NOW = new Date('2030-01-02T09:00:00.000Z');

export interface Row {
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

export type Fault = 'down' | 'status-500' | 'garbage' | 'unknown-word' | undefined;

export interface World {
  deps: Deps;
  rows: Row[];
  requests: { url: string; method: string; headers: Record<string, string>; body: string }[];
  fakeFetch: Mock<Fetch>;
  /** Every secret-ish string a test must never see in a log or an answer. */
  secrets: string[];
}

/** A subscription as the function that turns the email on (step 6.2) will write it: only the hashes are stored. */
export async function subscriber(overrides: Partial<Row> = {}): Promise<Subscriber> {
  const confirm = await newToken();
  const unsubscribe = await newToken();
  return {
    confirmToken: confirm.token,
    unsubscribeToken: unsubscribe.token,
    row: {
      address: 'ann@example.test',
      confirmHash: confirm.hash,
      confirmExpires: '2030-01-08T09:00:00.000Z',
      unsubscribeHash: unsubscribe.hash,
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
    if (fault === 'down') return Promise.reject(new Error('connection refused'));
    if (fault === 'status-500') return Promise.resolve(new Response('{"message":"secret detail"}', { status: 500 }));
    if (fault === 'garbage') return Promise.resolve(new Response('<html>', { status: 200 }));
    if (fault === 'unknown-word') return Promise.resolve(Response.json('exploded'));
    const args = JSON.parse(body) as { p_hash: string; p_now: string };
    const name = url.slice(`${CONFIG.supabaseUrl}/rest/v1/rpc/`.length);
    return Promise.resolve(Response.json(name === 'confirm_subscription' ? confirm(rows, args) : unsubscribe(rows, args)));
  });
  const secrets = subscribers.flatMap((s) => [s.confirmToken, s.unsubscribeToken, s.row.confirmHash ?? '', s.row.unsubscribeHash, s.row.address]);
  return { deps: { config: CONFIG, fetch: fakeFetch, now: () => now }, rows, requests, fakeFetch, secrets };
}

function confirm(rows: Row[], args: { p_hash: string; p_now: string }): string {
  const row = rows.find((candidate) => candidate.confirmHash === args.p_hash);
  if (!row) return 'unknown';
  if (row.unsubscribedAt !== null) return 'unsubscribed';
  if (row.confirmedAt !== null) return 'already_confirmed';
  if (row.confirmExpires === null || Date.parse(row.confirmExpires) <= Date.parse(args.p_now)) return 'expired';
  row.confirmedAt = args.p_now;
  return 'confirmed';
}

function unsubscribe(rows: Row[], args: { p_hash: string; p_now: string }): string {
  const row = rows.find((candidate) => candidate.unsubscribeHash === args.p_hash);
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
