import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { ALLOWED_ORIGIN, handleConfirm, loadConfig } from '../supabase/functions/confirm-email/handler.ts';
import { handleUnsubscribe } from '../supabase/functions/unsubscribe/handler.ts';
import { hashToken, isToken, newToken } from '../supabase/functions/_shared/subscription.ts';
import type { Fetch } from '../supabase/functions/_shared/subscription.ts';
import { CONFIG, NOW, hashOf, logged, subscriber, world } from './support/fake-subscriptions.ts';
import type { Fault } from './support/fake-subscriptions.ts';

const URL_OF_FUNCTION = 'https://project.supabase.test/functions/v1/confirm-email';

function post(body: unknown, origin: string | null = ALLOWED_ORIGIN, init: RequestInit = {}): Request {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (origin) headers.origin = origin;
  return new Request(URL_OF_FUNCTION, {
    method: 'POST',
    headers,
    body: typeof body === 'string' ? body : JSON.stringify(body),
    ...init,
  });
}

interface Answer {
  ok: boolean;
  status?: string;
  schedule?: string;
  error?: { code: string; message: string };
}

async function answer(response: Response): Promise<Answer> {
  return (await response.json()) as Answer;
}

let errors: MockInstance<typeof console.error>;
let warnings: MockInstance<typeof console.warn>;
beforeEach(() => {
  errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  warnings = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('tokens', () => {
  it('are 256 random bits, base64url, each different, and hashed with SHA-256', async () => {
    const one = await newToken();
    const two = await newToken();
    expect(isToken(one.token)).toBe(true);
    expect(one.token).toMatch(/^[A-Za-z0-9_-]{43}$/u);
    expect(one.token).not.toBe(two.token);
    expect(one.hash).toMatch(/^[0-9a-f]{64}$/u);
    expect(one.hash).toBe(await hashToken(one.token));
    expect(one.hash).not.toContain(one.token);
    // the hash is the SHA-256 of the token's text: a known vector
    expect(await hashToken('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });

  it('are made from the bytes it is given, padding and the unsafe characters removed', async () => {
    const { token } = await newToken((bytes) => bytes.fill(0xfb));
    expect(token).toBe('-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_s');
    expect(token).toHaveLength(43);
  });

  it('are only accepted in the shape this service issues', () => {
    expect(isToken('a'.repeat(43))).toBe(true);
    for (const wrong of ['', 'a'.repeat(42), 'a'.repeat(44), `${'a'.repeat(42)}=`, `${'a'.repeat(42)}+`, `${'a'.repeat(42)} `, 42, null, undefined, ['a'.repeat(43)]]) {
      expect(isToken(wrong)).toBe(false);
    }
  });
});

describe('loadConfig', () => {
  it('reads both secrets and trims the address', () => {
    expect(loadConfig((n) => ({ SUPABASE_URL: ' https://p.test// ', SUPABASE_SERVICE_ROLE_KEY: ' k ' })[n])).toEqual({
      config: { supabaseUrl: 'https://p.test', serviceRoleKey: 'k' },
    });
  });
  it('names what is missing, never a value', () => {
    expect(loadConfig(() => undefined)).toEqual({ missing: ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'] });
    expect(loadConfig((n) => (n === 'SUPABASE_URL' ? 'ftp://x' : 'k'))).toEqual({ missing: ['SUPABASE_URL'] });
  });
});

describe('confirming', () => {
  it('confirms once, from a POST with the token in the body, and the database only ever sees its hash', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    const response = await handleConfirm(post({ token: ann.confirmToken }), w.deps);
    expect(response.status).toBe(200);
    expect(await answer(response)).toEqual({ ok: true, status: 'confirmed', schedule: 'weekly' });
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ann.row.confirmedAt).toBe(NOW.toISOString());
    expect(w.requests).toHaveLength(2);
    const [request, read] = w.requests;
    expect(read?.method).toBe('GET'); // the schedule, read after the confirm
    expect(read?.url).toBe(`${CONFIG.supabaseUrl}/rest/v1/subscriptions?confirm_token_hash=eq.${await hashOf(ann.confirmToken)}&select=frequency&limit=1`);
    expect(JSON.stringify(read)).not.toContain(ann.confirmToken);
    expect(request?.url).toBe(`${CONFIG.supabaseUrl}/rest/v1/rpc/confirm_subscription`);
    expect(request?.headers.authorization).toBe(`Bearer ${CONFIG.serviceRoleKey}`);
    expect(JSON.parse(request?.body ?? '{}')).toEqual({ p_hash: await hashOf(ann.confirmToken), p_now: NOW.toISOString() });
    expect(JSON.stringify(request)).not.toContain(ann.confirmToken);
  });

  it('answers a replay as "already confirmed" and does not move the first time', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    await handleConfirm(post({ token: ann.confirmToken }), w.deps);
    const later = world([ann], undefined, new Date('2030-01-03T09:00:00.000Z'));
    const replay = await handleConfirm(post({ token: ann.confirmToken }), later.deps);
    expect(replay.status).toBe(200);
    expect(await answer(replay)).toEqual({ ok: true, status: 'already_confirmed', schedule: 'weekly' });
    expect(ann.row.confirmedAt).toBe(NOW.toISOString());
  });

  it('tells the schedule the subscription has, daily or weekly', async () => {
    for (const frequency of ['daily', 'weekly'] as const) {
      const ann = await subscriber({ frequency });
      const response = await handleConfirm(post({ token: ann.confirmToken }), world([ann]).deps);
      expect((await answer(response)).schedule).toBe(frequency);
    }
  });

  it('still confirms, and only leaves the schedule out, when the schedule cannot be read', async () => {
    for (const fault of ['schedule-down', 'schedule-500', 'schedule-garbage', 'schedule-odd'] as const) {
      const ann = await subscriber();
      const response = await handleConfirm(post({ token: ann.confirmToken }), world([ann], fault).deps);
      expect(response.status, fault).toBe(200);
      expect(await response.json(), fault).toEqual({ ok: true, status: 'confirmed' });
      expect(ann.row.confirmedAt, fault).toBe(NOW.toISOString());
    }
  });

  it('leaves the schedule out when the row is gone by the time it is read (an unsubscribe in between)', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    const deps = {
      ...w.deps,
      fetch: async (url: string, init: RequestInit) => {
        const result = await w.fakeFetch(url, init);
        if (init.method !== 'GET') ann.row.confirmHash = null; // the user unsubscribes right after the confirm
        return result;
      },
    };
    const response = await handleConfirm(post({ token: ann.confirmToken }), deps);
    expect(await response.json()).toEqual({ ok: true, status: 'confirmed' });
  });

  it('refuses an expired link with a clear answer and confirms nothing', async () => {
    const ann = await subscriber({ confirmExpires: '2030-01-02T08:00:00.000Z' });
    const response = await handleConfirm(post({ token: ann.confirmToken }), world([ann]).deps);
    expect(response.status).toBe(410);
    expect((await answer(response)).error?.code).toBe('link_expired');
    expect(ann.row.confirmedAt).toBeNull();
  });

  it('never confirms a subscription that was unsubscribed, and its old link is simply not valid', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    // the user unsubscribes first: the confirmation token is cleared
    expect((await handleUnsubscribe(new Request(`https://x.test/functions/v1/unsubscribe?t=${ann.unsubscribeToken}`), w.deps)).status).toBe(303);
    const response = await handleConfirm(post({ token: ann.confirmToken }), w.deps);
    expect(response.status).toBe(404);
    expect((await answer(response)).error?.code).toBe('link_not_valid');
    expect(ann.row.confirmedAt).toBeNull();
    // and with a token still on file, an unsubscribed row says so
    const bob = await subscriber({ unsubscribedAt: '2030-01-01T00:00:00.000Z', filters: {} });
    const refused = await handleConfirm(post({ token: bob.confirmToken }), world([bob]).deps);
    expect(refused.status).toBe(409);
    expect((await answer(refused)).error?.code).toBe('unsubscribed');
    expect(bob.row.confirmedAt).toBeNull();
  });

  it('answers an unknown token like a malformed one, so nobody can find out who is subscribed', async () => {
    const ann = await subscriber();
    const unknown = await newToken();
    const w = world([ann]);
    const response = await handleConfirm(post({ token: unknown.token }), w.deps);
    expect(response.status).toBe(404);
    expect((await answer(response)).error?.code).toBe('link_not_valid');
    expect(ann.row.confirmedAt).toBeNull();
    // the unsubscribe token of the same person is a different credential and does not confirm
    const wrongKind = await handleConfirm(post({ token: ann.unsubscribeToken }), w.deps);
    expect(wrongKind.status).toBe(404);
    expect(ann.row.confirmedAt).toBeNull();
  });

  it.each([
    ['no token', {}],
    ['a short token', { token: 'abc' }],
    ['a long token', { token: 'a'.repeat(300) }],
    ['a token with other characters', { token: `${'a'.repeat(42)}!` }],
    ['a number', { token: 42 }],
    ['a list', { token: ['a'.repeat(43)] }],
    ['null', { token: null }],
  ])('answers %s as a link that is not valid, without asking the database', async (_name, body) => {
    const w = world([]);
    const response = await handleConfirm(post(body), w.deps);
    expect(response.status).toBe(404);
    expect((await answer(response)).error?.code).toBe('link_not_valid');
    expect(w.fakeFetch).not.toHaveBeenCalled();
  });

  it('answers a HEAD with 200 and no body, and never touches the database (what a link scanner sends)', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    const request = new Request(`${URL_OF_FUNCTION}?token=${ann.confirmToken}`, { method: 'HEAD' });
    const response = await handleConfirm(request, w.deps);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe('');
    expect(response.headers.get('cache-control')).toBe('no-store');
    const noSecrets = await handleConfirm(request, { config: undefined, fetch: w.fakeFetch, now: () => NOW });
    expect(noSecrets.status).toBe(200);
    expect(w.fakeFetch).not.toHaveBeenCalled();
    expect(ann.row.confirmedAt).toBeNull();
  });

  it('never confirms from a GET or any other method, so a mail scanner cannot give consent', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    for (const method of ['GET', 'PUT', 'DELETE', 'PATCH']) {
      const request = new Request(`${URL_OF_FUNCTION}?token=${ann.confirmToken}&t=${ann.confirmToken}`, {
        method,
        headers: { origin: ALLOWED_ORIGIN },
      });
      const response = await handleConfirm(request, w.deps);
      expect(response.status, method).toBe(405);
      expect(response.headers.get('allow')).toBe('POST, OPTIONS');
      expect((await answer(response)).error?.code).toBe('method_not_allowed');
    }
    expect(w.fakeFetch).not.toHaveBeenCalled();
    expect(ann.row.confirmedAt).toBeNull();
  });
});

describe('the request', () => {
  it('answers a preflight from the dashboard only, with CORS for that origin only', async () => {
    const w = world([]);
    const ok = await handleConfirm(new Request(URL_OF_FUNCTION, { method: 'OPTIONS', headers: { origin: ALLOWED_ORIGIN } }), w.deps);
    expect(ok.status).toBe(204);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ok.headers.get('access-control-allow-methods')).toBe('POST, OPTIONS');
    expect(ok.headers.get('access-control-allow-headers')).toContain('content-type');
    expect(ok.headers.get('access-control-allow-headers')).not.toContain('authorization');
    const bad = await handleConfirm(new Request(URL_OF_FUNCTION, { method: 'OPTIONS', headers: { origin: 'https://evil.test' } }), w.deps);
    expect(bad.status).toBe(403);
    expect(bad.headers.get('access-control-allow-origin')).toBeNull();
    expect(w.fakeFetch).not.toHaveBeenCalled();
  });

  it('refuses another origin, even with a valid token, and gives it no CORS header', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    const response = await handleConfirm(post({ token: ann.confirmToken }, 'https://evil.test'), w.deps);
    expect(response.status).toBe(403);
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
    expect((await answer(response)).error?.code).toBe('origin_not_allowed');
    expect(ann.row.confirmedAt).toBeNull();
  });

  it('allows a caller without an Origin header (curl) but gives it no CORS header', async () => {
    const ann = await subscriber();
    const response = await handleConfirm(post({ token: ann.confirmToken }, null), world([ann]).deps);
    expect(response.status).toBe(200);
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
  });

  it('wants JSON, a small body and an object', async () => {
    const w = world([]);
    const text = await handleConfirm(post('token=abc', ALLOWED_ORIGIN, { headers: { 'content-type': 'text/plain', origin: ALLOWED_ORIGIN } }), w.deps);
    expect(text.status).toBe(415);
    expect((await answer(text)).error?.code).toBe('unsupported_media_type');
    const declared = await handleConfirm(post({ token: 'a'.repeat(300) }, ALLOWED_ORIGIN, { headers: { 'content-type': 'application/json', 'content-length': '999999', origin: ALLOWED_ORIGIN } }), w.deps);
    expect(declared.status).toBe(413);
    const streamed = await handleConfirm(post(JSON.stringify({ token: 'a'.repeat(5000) })), w.deps);
    expect(streamed.status).toBe(413);
    expect((await answer(streamed)).error?.code).toBe('payload_too_large');
    for (const body of ['not json', '[]', 'null', '"x"', '']) {
      const response = await handleConfirm(post(body), w.deps);
      expect(response.status, body).toBe(400);
      expect((await answer(response)).error?.code).toBe('invalid_request');
    }
    expect(w.fakeFetch).not.toHaveBeenCalled();
  });

  it('accepts a request with no body stream at all as an empty one', async () => {
    const response = await handleConfirm(
      new Request(URL_OF_FUNCTION, { method: 'POST', headers: { 'content-type': 'application/json' } }),
      world([]).deps,
    );
    expect(response.status).toBe(400);
  });
});

describe('when something is wrong with the service', () => {
  it('says so when the secrets are missing', async () => {
    const response = await handleConfirm(post({ token: 'a'.repeat(43) }), { config: undefined, fetch: vi.fn<Fetch>(), now: () => NOW });
    expect(response.status).toBe(500);
    expect((await answer(response)).error?.code).toBe('server_error');
    expect(errors).toHaveBeenCalled();
  });

  it.each<[string, Fault]>([
    ['the database is unreachable', 'down'],
    ['the database answers 500', 'status-500'],
    ['the database gives something that is not an answer', 'garbage'],
    ['the database gives a word this function does not know', 'unknown-word'],
  ])('answers 500 and confirms nothing when %s', async (_name, fault) => {
    const ann = await subscriber();
    const w = world([ann], fault);
    const response = await handleConfirm(post({ token: ann.confirmToken }), w.deps);
    expect(response.status).toBe(500);
    expect((await answer(response)).error?.code).toBe('server_error');
    expect(ann.row.confirmedAt).toBeNull();
  });
});

describe('what is written down', () => {
  it('never contains a token, a hash, an address, the key or a detail from the database', async () => {
    const ann = await subscriber();
    const all: Fault[] = [undefined, 'down', 'status-500', 'garbage', 'unknown-word'];
    const responses: string[] = [];
    for (const fault of all) {
      const w = world([ann], fault);
      for (const body of [{ token: ann.confirmToken }, { token: ann.unsubscribeToken }, { token: 'x' }, 'broken']) {
        responses.push(await (await handleConfirm(post(body), w.deps)).text());
      }
    }
    const log = logged(errors as never, warnings as never);
    const secrets = [ann.confirmToken, ann.unsubscribeToken, ann.row.unsubscribeHash, await hashOf(ann.confirmToken), ann.row.address, CONFIG.serviceRoleKey, 'secret detail'];
    for (const secret of secrets) {
      expect(log).not.toContain(secret);
      expect(responses.join('\n')).not.toContain(secret);
    }
    expect(log).toContain('confirm-email: failed');
  });
});
