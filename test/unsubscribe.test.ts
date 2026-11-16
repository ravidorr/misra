import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { handleUnsubscribe, loadConfig } from '../supabase/functions/unsubscribe/handler.ts';
import { PAGE_URL, makeUnsubscribeToken, newToken, verifyUnsubscribeToken } from '../supabase/functions/_shared/subscription.ts';
import type { Fetch } from '../supabase/functions/_shared/subscription.ts';
import { CONFIG, NOW, UNSUBSCRIBE_SECRET, hashOf, logged, subscriber, world } from './support/fake-subscriptions.ts';
import type { Fault } from './support/fake-subscriptions.ts';

const FUNCTION_URL = 'https://project.supabase.test/functions/v1/unsubscribe';
const ONE_CLICK = 'List-Unsubscribe=One-Click';

function get(token: string | null): Request {
  return new Request(token === null ? FUNCTION_URL : `${FUNCTION_URL}?t=${token}`, { method: 'GET' });
}

/** What a mailbox provider sends for RFC 8058: a POST to the header URL, form-encoded, with no Origin. */
function oneClick(token: string | null, init: RequestInit = {}): Request {
  return new Request(token === null ? FUNCTION_URL : `${FUNCTION_URL}?t=${token}`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: ONE_CLICK,
    ...init,
  });
}

interface Answer {
  ok: boolean;
  status?: string;
  error?: { code: string; message: string };
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

describe('a click on the link in the email (GET)', () => {
  it('unsubscribes at once and sends the browser to the page, with no token in the address', async () => {
    const ann = await subscriber({ confirmedAt: '2030-01-01T10:00:00.000Z' });
    const w = world([ann]);
    const response = await handleUnsubscribe(get(ann.unsubscribeToken), w.deps);
    expect(response.status).toBe(303);
    expect(response.headers.get('location')).toBe(`${PAGE_URL}#unsubscribed`);
    expect(response.headers.get('location')).not.toContain(ann.unsubscribeToken);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
    expect(ann.row.unsubscribedAt).toBe(NOW.toISOString());
    expect(ann.row.filters).toEqual({});
    expect(ann.row.frequency).toBe('daily');
    expect(ann.row.emailed).toBe(0);
    expect(ann.row.confirmedAt).toBe('2030-01-01T10:00:00.000Z'); // the audit record stays
    const [request] = w.requests;
    expect(request?.url).toBe(`${CONFIG.supabaseUrl}/rest/v1/rpc/unsubscribe_user`);
    expect(JSON.parse(request?.body ?? '{}')).toEqual({ p_user_id: ann.row.id, p_now: NOW.toISOString() });
    expect(JSON.stringify(request)).not.toContain(ann.unsubscribeToken); // only the verified id goes to the database
  });

  it('works for a subscription that was never confirmed', async () => {
    const ann = await subscriber();
    const response = await handleUnsubscribe(get(ann.unsubscribeToken), world([ann]).deps);
    expect(response.headers.get('location')).toBe(`${PAGE_URL}#unsubscribed`);
    expect(ann.row.unsubscribedAt).not.toBeNull();
  });

  it('is not an error the second time, and keeps the first time', async () => {
    const ann = await subscriber();
    await handleUnsubscribe(get(ann.unsubscribeToken), world([ann]).deps);
    const later = world([ann], undefined, new Date('2030-02-01T00:00:00.000Z'));
    const replay = await handleUnsubscribe(get(ann.unsubscribeToken), later.deps);
    expect(replay.status).toBe(303);
    expect(replay.headers.get('location')).toBe(`${PAGE_URL}#already-unsubscribed`);
    expect(ann.row.unsubscribedAt).toBe(NOW.toISOString());
  });

  it('says the link is not valid for an unknown token, for the confirmation token and for no token, and changes nothing', async () => {
    const ann = await subscriber();
    const unknown = await newToken();
    const w = world([ann]);
    for (const token of [unknown.token, ann.confirmToken]) {
      const response = await handleUnsubscribe(get(token), w.deps);
      expect(response.status).toBe(303);
      expect(response.headers.get('location')).toBe(`${PAGE_URL}#unsubscribe-link-not-valid`);
    }
    expect(ann.row.unsubscribedAt).toBeNull();
    const calls = w.fakeFetch.mock.calls.length;
    for (const token of [null, '', 'short', `${'a'.repeat(42)}!`, 'a'.repeat(100)]) {
      const response = await handleUnsubscribe(get(token), w.deps);
      expect(response.headers.get('location')).toBe(`${PAGE_URL}#unsubscribe-link-not-valid`);
    }
    expect(w.fakeFetch.mock.calls.length).toBe(calls); // a malformed token never reaches the database
  });

  it('sends the browser to "try again" when the database or the secrets fail, and unsubscribes nobody', async () => {
    const ann = await subscriber();
    for (const fault of ['down', 'status-500', 'garbage', 'unknown-word'] as const) {
      const response = await handleUnsubscribe(get(ann.unsubscribeToken), world([ann], fault).deps);
      expect(response.status, fault).toBe(303);
      expect(response.headers.get('location')).toBe(`${PAGE_URL}#try-again`);
    }
    const noSecrets = await handleUnsubscribe(get(ann.unsubscribeToken), { config: undefined, fetch: vi.fn<Fetch>(), now: () => NOW });
    expect(noSecrets.headers.get('location')).toBe(`${PAGE_URL}#try-again`);
    expect(ann.row.unsubscribedAt).toBeNull();
    expect(errors).toHaveBeenCalled();
  });
});

describe('RFC 8058 one-click (POST to the header URL)', () => {
  it('unsubscribes from a form POST with the token in the URL, with no Origin and no sign-in', async () => {
    const ann = await subscriber();
    const response = await handleUnsubscribe(oneClick(ann.unsubscribeToken), world([ann]).deps);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('text/plain; charset=utf-8');
    expect(await response.text()).toBe('Unsubscribed.');
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
    expect(ann.row.unsubscribedAt).toBe(NOW.toISOString());
    expect(ann.row.filters).toEqual({});
  });

  it('does not depend on the body or its type, nor refuse a foreign Origin: the token is the credential', async () => {
    for (const init of [
      { body: ONE_CLICK },
      { body: '', headers: {} },
      { body: new FormData(), headers: undefined },
      { body: ONE_CLICK, headers: { origin: 'https://mail.example', 'content-type': 'text/plain' } },
    ]) {
      const ann = await subscriber();
      const response = await handleUnsubscribe(oneClick(ann.unsubscribeToken, init as RequestInit), world([ann]).deps);
      expect(response.status).toBe(200);
      expect(ann.row.unsubscribedAt).not.toBeNull();
    }
    const noBody = await subscriber();
    const bare = new Request(`${FUNCTION_URL}?t=${noBody.unsubscribeToken}`, { method: 'POST' });
    expect((await handleUnsubscribe(bare, world([noBody]).deps)).status).toBe(200);
  });

  it('answers a repeat with 200 and the same plain text', async () => {
    const ann = await subscriber();
    await handleUnsubscribe(oneClick(ann.unsubscribeToken), world([ann]).deps);
    const replay = await handleUnsubscribe(oneClick(ann.unsubscribeToken), world([ann]).deps);
    expect(replay.status).toBe(200);
    expect(replay.headers.get('content-type')).toBe('text/plain; charset=utf-8');
    expect(await replay.text()).toBe('Unsubscribed.');
  });

  it('answers 404 for an unknown or missing token and 500 when the service fails (so the provider may retry)', async () => {
    const ann = await subscriber();
    const unknown = await newToken();
    for (const token of [unknown.token, ann.confirmToken, null, 'junk']) {
      const response = await handleUnsubscribe(oneClick(token), world([ann]).deps);
      expect(response.status).toBe(404);
      expect(((await response.json()) as Answer).error?.code).toBe('link_not_valid');
    }
    for (const fault of ['down', 'status-500', 'garbage'] as const) {
      const response = await handleUnsubscribe(oneClick(ann.unsubscribeToken), world([ann], fault).deps);
      expect(response.status).toBe(500);
      expect(((await response.json()) as Answer).error?.code).toBe('server_error');
    }
    const noSecrets = await handleUnsubscribe(oneClick(ann.unsubscribeToken), { config: undefined, fetch: vi.fn<Fetch>(), now: () => NOW });
    expect(noSecrets.status).toBe(500);
    expect(ann.row.unsubscribedAt).toBeNull();
  });
});

describe('the stateless token (<subscription id>.<HMAC-SHA256(UNSUBSCRIBE_SECRET, id)>)', () => {
  const ID = '11111111-1111-4111-8111-111111111111';
  // The same vector is pinned in tests/test_send_digests.py: the Python sender and this function must agree.
  const VECTOR = `${ID}.3ee0be7f8d9d03d8e80efd19d45c303cf23278727854c5d5a88f1694bcbc0f88`;

  it('is made and checked the same way the Python sender makes it', async () => {
    expect(await makeUnsubscribeToken(UNSUBSCRIBE_SECRET, ID)).toBe(VECTOR);
    expect(await verifyUnsubscribeToken(UNSUBSCRIBE_SECRET, VECTOR)).toBe(ID);
  });

  it('is refused when the MAC, the id, the secret or the shape is wrong, before the database is asked', async () => {
    const ann = await subscriber();
    const [id = '', mac = ''] = ann.unsubscribeToken.split('.');
    const flipped = `${mac.startsWith('0') ? '1' : '0'}${mac.slice(1)}`;
    const other = await subscriber();
    const bad = [
      `${id}.${flipped}`, // one character of the MAC changed
      `${other.row.id}.${mac}`, // somebody else's id with this MAC
      await makeUnsubscribeToken('another-secret-0123456789abcdefghij', id), // made under another secret
      ann.unsubscribeToken.toUpperCase(),
      `${id}.${mac.slice(1)}`,
      `${id}.${mac}0`,
      `${id}.`,
      `.${mac}`,
      id,
      mac,
      `${id}:${mac}`,
      ann.confirmToken,
    ];
    for (const token of bad) {
      expect(await verifyUnsubscribeToken(UNSUBSCRIBE_SECRET, token), token).toBeUndefined();
    }
    expect(await verifyUnsubscribeToken(UNSUBSCRIBE_SECRET, undefined)).toBeUndefined();
    const w = world([ann]);
    for (const token of bad) {
      expect((await handleUnsubscribe(get(token), w.deps)).headers.get('location'), token).toBe(`${PAGE_URL}#unsubscribe-link-not-valid`);
      expect((await handleUnsubscribe(oneClick(token), w.deps)).status, token).toBe(404);
    }
    expect(w.fakeFetch).not.toHaveBeenCalled();
    expect(ann.row.unsubscribedAt).toBeNull();
  });

  it('with a right MAC for an id that has no subscription (a deleted account) is answered as a link that is not valid', async () => {
    const ann = await subscriber();
    const ghost = await makeUnsubscribeToken(UNSUBSCRIBE_SECRET, crypto.randomUUID());
    const w = world([ann]);
    expect((await handleUnsubscribe(get(ghost), w.deps)).headers.get('location')).toBe(`${PAGE_URL}#unsubscribe-link-not-valid`);
    expect((await handleUnsubscribe(oneClick(ghost), w.deps)).status).toBe(404);
    expect(w.fakeFetch).toHaveBeenCalledTimes(2);
    expect(ann.row.unsubscribedAt).toBeNull();
  });

  it('keeps working for the same subscription after it was unsubscribed and started again', async () => {
    const ann = await subscriber();
    await handleUnsubscribe(get(ann.unsubscribeToken), world([ann]).deps);
    ann.row.unsubscribedAt = null; // a new consent (subscribe_start) keeps the same id
    const again = await handleUnsubscribe(get(ann.unsubscribeToken), world([ann]).deps);
    expect(again.headers.get('location')).toBe(`${PAGE_URL}#unsubscribed`);
  });
});

describe('loadConfig', () => {
  const ENV: Record<string, string> = {
    SUPABASE_URL: 'https://p.test/',
    SUPABASE_SERVICE_ROLE_KEY: 'k',
    UNSUBSCRIBE_SECRET: UNSUBSCRIBE_SECRET,
  };

  it('reads the secret and keeps the other two as before', () => {
    expect(loadConfig((name) => ENV[name])).toEqual({
      config: { supabaseUrl: 'https://p.test', serviceRoleKey: 'k', unsubscribeSecret: UNSUBSCRIBE_SECRET },
    });
    expect(loadConfig((name) => ({ ...ENV, UNSUBSCRIBE_SECRET: ` ${UNSUBSCRIBE_SECRET} ` })[name])).toMatchObject({
      config: { unsubscribeSecret: UNSUBSCRIBE_SECRET },
    });
  });

  it('names a missing or too short secret, by name only', () => {
    expect(loadConfig((name) => ({ ...ENV, UNSUBSCRIBE_SECRET: undefined })[name])).toEqual({ missing: ['UNSUBSCRIBE_SECRET'] });
    expect(loadConfig((name) => ({ ...ENV, UNSUBSCRIBE_SECRET: 'short' })[name])).toEqual({ missing: ['UNSUBSCRIBE_SECRET'] });
    expect(loadConfig(() => undefined)).toEqual({ missing: ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'UNSUBSCRIBE_SECRET'] });
    expect(loadConfig((name) => ({ ...ENV, SUPABASE_URL: undefined })[name])).toEqual({ missing: ['SUPABASE_URL'] });
  });
});

describe('what never unsubscribes anyone', () => {
  it('a HEAD answers 200 with no body and never touches the database (what a link scanner sends)', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    const request = new Request(`${FUNCTION_URL}?t=${ann.unsubscribeToken}`, { method: 'HEAD' });
    const response = await handleUnsubscribe(request, w.deps);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe('');
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
    const noSecrets = await handleUnsubscribe(request, { config: undefined, fetch: w.fakeFetch, now: () => NOW });
    expect(noSecrets.status).toBe(200);
    const noToken = await handleUnsubscribe(new Request(FUNCTION_URL, { method: 'HEAD' }), w.deps);
    expect(noToken.status).toBe(200);
    expect(w.fakeFetch).not.toHaveBeenCalled();
    expect(ann.row.unsubscribedAt).toBeNull();
  });

  it('an OPTIONS or another method, which link scanners send', async () => {
    const ann = await subscriber();
    const w = world([ann]);
    for (const method of ['OPTIONS', 'PUT', 'DELETE', 'PATCH']) {
      const response = await handleUnsubscribe(new Request(`${FUNCTION_URL}?t=${ann.unsubscribeToken}`, { method }), w.deps);
      expect(response.status, method).toBe(405);
      expect(response.headers.get('allow')).toBe('GET, HEAD, POST');
      expect(response.headers.get('access-control-allow-origin')).toBeNull();
    }
    expect(w.fakeFetch).not.toHaveBeenCalled();
    expect(ann.row.unsubscribedAt).toBeNull();
  });

  it('one person\'s token, however many times, touches no other subscription', async () => {
    const ann = await subscriber();
    const bob = await subscriber({ address: 'bob@example.test' });
    const w = world([ann, bob]);
    await handleUnsubscribe(get(ann.unsubscribeToken), w.deps);
    await handleUnsubscribe(oneClick(ann.unsubscribeToken), w.deps);
    expect(bob.row.unsubscribedAt).toBeNull();
    expect(bob.row.filters).toEqual({ levels: ['Intern'] });
    expect(bob.row.emailed).toBe(2);
  });
});

describe('what is written down', () => {
  it('never contains a token, a hash, an address, the key or a detail from the database', async () => {
    const ann = await subscriber();
    const responses: string[] = [];
    const faults: Fault[] = [undefined, 'down', 'status-500', 'garbage', 'unknown-word'];
    for (const fault of faults) {
      const w = world([ann], fault);
      for (const request of [get(ann.unsubscribeToken), oneClick(ann.unsubscribeToken), get(ann.confirmToken), oneClick('junk')]) {
        const response = await handleUnsubscribe(request, w.deps);
        responses.push(`${response.headers.get('location') ?? ''} ${await response.text()}`);
      }
    }
    const log = logged(errors as never, warnings as never);
    const secrets = [ann.confirmToken, ann.unsubscribeToken, ann.row.unsubscribeHash, await hashOf(ann.confirmToken), ann.row.address, CONFIG.serviceRoleKey, 'secret detail'];
    for (const secret of secrets) {
      expect(log).not.toContain(secret);
      expect(responses.join('\n')).not.toContain(secret);
    }
    expect(log).toContain('unsubscribe: failed');
  });
});
