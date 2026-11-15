// A fake of everything `email-settings` talks to: Supabase Auth (who owns a token), the five database functions behind
// PostgREST (they answer exactly as the SQL of supabase/migrations/20261118100000_digest_signup_and_limits.sql does;
// supabase/tests/digest_signup.sql tests the SQL itself) and Resend. It records every request so a test can check what
// crossed the wire, and it can be told to fail in each place.
import { vi } from 'vitest';
import type { Mock } from 'vitest';

import type { Config, Deps } from '../../supabase/functions/email-settings/handler.ts';
import type { Fetch } from '../../supabase/functions/_shared/subscription.ts';

export const NOW = new Date('2030-01-02T09:00:00.000Z');
export const BASE = 'https://project.supabase.test';
export const SERVICE_KEY = 'service-role-test-key';
export const RESEND_KEY = 're_test_key_value';
export const HASH_SECRET = 'a-long-enough-hash-secret';
export const ORIGIN = 'https://ravidorr.github.io';
export const ANN = { token: 'ann-access-token', id: '11111111-1111-4111-8111-111111111111', email: 'ann@example.test' };
export const BOB = { token: 'bob-access-token', id: '22222222-2222-4222-8222-222222222222', email: 'bob@example.test' };
export const CAROL = { token: 'carol-access-token', id: '33333333-3333-4333-8333-333333333333', email: 'carol@example.test' };

export const FILTERS = { regions: ['Tel Aviv'], levels: ['Intern', 'Junior'], role_types: ['Product'], keywords: 'growth' };

export interface Row {
  address: string;
  frequency: string;
  filters: Record<string, unknown>;
  confirmHash: string | null;
  confirmExpires: string | null;
  unsubscribeHash: string;
  givenAt: string;
  confirmedAt: string | null;
  unsubscribedAt: string | null;
  emailed: number;
}

export interface Seen {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
}

export type Fault = 'reserve-down' | 'start-down' | 'release-down' | 'off-down' | 'auth-down' | 'auth-500' | 'auth-garbage' | undefined;
export type MailFault = 'ok' | '400' | '422' | '429' | '500' | '503' | 'network';

export interface World {
  deps: Deps;
  /** The subscriptions by user id. */
  rows: Map<string, Row>;
  /** The counters by key hash (`day:key`). */
  limits: Map<string, number>;
  cap: { value: number };
  requests: Seen[];
  emails: { to: string[]; from: string; reply_to: string; subject: string; text: string }[];
  mail: { fault: MailFault };
  fault: { current: Fault };
  fakeFetch: Mock<Fetch>;
}

export function config(overrides: Partial<Config> = {}): Config {
  return {
    supabaseUrl: BASE,
    serviceRoleKey: SERVICE_KEY,
    signupEnabled: true,
    mail: { resendApiKey: RESEND_KEY, from: 'Misra <digest@send.avidor.org>', replyTo: 'raanan@avidor.org', hashSecret: HASH_SECRET },
    ...overrides,
  };
}

export function world(options: { config?: Partial<Config>; now?: Date } = {}): World {
  const rows = new Map<string, Row>();
  const limits = new Map<string, number>();
  const cap = { value: 0 };
  const requests: Seen[] = [];
  const emails: World['emails'] = [];
  const mail: World['mail'] = { fault: 'ok' };
  const fault: World['fault'] = { current: undefined };
  const people = [ANN, BOB, CAROL];

  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const headers = init.headers as Record<string, string>;
    const body = typeof init.body === 'string' ? init.body : '';
    requests.push({ url, method: String(init.method), headers, body });

    if (url === `${BASE}/auth/v1/user`) {
      if (fault.current === 'auth-down') return Promise.reject(new Error('connection refused'));
      if (fault.current === 'auth-500') return Promise.resolve(new Response('{}', { status: 502 }));
      if (fault.current === 'auth-garbage') return Promise.resolve(new Response('<html>', { status: 200 }));
      const bearer = (headers.authorization ?? '').replace('Bearer ', '');
      const person = people.find((p) => p.token === bearer);
      if (!person) return Promise.resolve(Response.json({ msg: 'invalid JWT' }, { status: 401 }));
      return Promise.resolve(Response.json({ id: person.id, role: 'authenticated', email: person.email }));
    }

    if (url === 'https://api.resend.com/emails') {
      if (mail.fault === 'network') return Promise.reject(new Error('socket hang up'));
      if (mail.fault !== 'ok') {
        return Promise.resolve(new Response(`{"message":"secret detail ${body}"}`, { status: Number(mail.fault) }));
      }
      emails.push(JSON.parse(body) as World['emails'][number]);
      return Promise.resolve(Response.json({ id: 'email-id' }));
    }

    const name = url.slice(`${BASE}/rest/v1/rpc/`.length);
    const args = JSON.parse(body) as Record<string, unknown>;
    const down =
      (name === 'confirm_email_reserve' && fault.current === 'reserve-down') ||
      (name === 'subscribe_start' && fault.current === 'start-down') ||
      (name === 'confirm_email_release' && fault.current === 'release-down') ||
      (name === 'unsubscribe_user' && fault.current === 'off-down');
    if (down) return Promise.resolve(new Response('{"message":"secret detail"}', { status: 500 }));
    return Promise.resolve(Response.json(database(name, args, rows, limits, cap)));
  });

  const now = options.now ?? NOW;
  return {
    deps: { config: config(options.config), fetch: fakeFetch, now: () => now },
    rows,
    limits,
    cap,
    requests,
    emails,
    mail,
    fault,
    fakeFetch,
  };
}

/** The same decisions as the SQL functions (see the migration). */
function database(name: string, a: Record<string, unknown>, rows: Map<string, Row>, limits: Map<string, number>, cap: { value: number }): string {
  const day = String(a.p_day);
  const key = (hash: unknown): string => `${day}:${String(hash)}`;
  const take = (hash: unknown, limit: number): boolean => {
    const used = limits.get(key(hash)) ?? 0;
    if (limit < 1 || used >= limit) return false;
    limits.set(key(hash), used + 1);
    return true;
  };
  const back = (hash: unknown): void => {
    limits.set(key(hash), Math.max((limits.get(key(hash)) ?? 0) - 1, 0));
  };
  if (name === 'confirm_email_reserve') {
    if (!take(a.p_user_hash, Number(a.p_user_limit))) return 'user_limit';
    if (!take(a.p_address_hash, Number(a.p_address_limit))) {
      back(a.p_user_hash);
      return 'address_limit';
    }
    if (Number(a.p_daily_cap) < 1 || cap.value >= Number(a.p_daily_cap)) {
      back(a.p_user_hash);
      back(a.p_address_hash);
      return 'daily_cap';
    }
    cap.value += 1;
    return 'ok';
  }
  if (name === 'confirm_email_release') {
    back(a.p_user_hash);
    back(a.p_address_hash);
    cap.value = Math.max(cap.value - 1, 0);
    return 'ok';
  }
  const id = String(a.p_user_id);
  const row = rows.get(id);
  if (name === 'subscribe_start') {
    if (row?.unsubscribedAt === null && row.confirmedAt !== null && row.address.toLowerCase() === String(a.p_address).toLowerCase()) {
      return 'already_on';
    }
    rows.set(id, {
      address: String(a.p_address),
      frequency: String(a.p_frequency),
      filters: a.p_filters as Record<string, unknown>,
      confirmHash: String(a.p_confirm_hash),
      confirmExpires: String(a.p_confirm_expires),
      unsubscribeHash: String(a.p_unsubscribe_hash),
      givenAt: String(a.p_now),
      confirmedAt: null,
      unsubscribedAt: null,
      emailed: row?.emailed ?? 0,
    });
    return 'started';
  }
  // unsubscribe_user, which hands the row's hash to unsubscribe_subscription
  if (!row) return 'unknown';
  if (row.unsubscribedAt !== null) {
    row.emailed = 0;
    return 'already_unsubscribed';
  }
  row.unsubscribedAt = String(a.p_now);
  row.filters = {};
  row.frequency = 'daily';
  row.confirmHash = null;
  row.confirmExpires = null;
  row.emailed = 0;
  return 'unsubscribed';
}

export function post(person: { token: string } | null, body: unknown, init: RequestInit = {}): Request {
  return new Request(`${BASE}/functions/v1/email-settings`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      origin: ORIGIN,
      ...(person ? { authorization: `Bearer ${person.token}` } : {}),
    },
    body: JSON.stringify(body),
    ...init,
  });
}

export const turnOn = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  action: 'turn_on',
  frequency: 'daily',
  filters: FILTERS,
  ...extra,
});

/** The confirmation token in the emailed link (the only place it appears). */
export function tokenIn(text: string): string {
  const match = /#token=([A-Za-z0-9_-]{43})/u.exec(text);
  if (!match?.[1]) throw new Error('no token link in the email');
  return match[1];
}
