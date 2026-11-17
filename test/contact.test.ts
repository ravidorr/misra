import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import {
  CALLER_DAILY_LIMIT,
  DAILY_CAP,
  DEFAULT_FROM,
  MAX_BODY_BYTES,
  MIN_FILL_MS,
  SUBJECT,
  handleContact,
  loadConfig,
  renderEmail,
} from '../supabase/functions/contact/handler.ts';
import type { Config, Deps } from '../supabase/functions/contact/handler.ts';
import { LIMITS, validateContact } from '../supabase/functions/contact/validate.ts';
import type { Fetch } from '../supabase/functions/_shared/subscription.ts';

const ORIGIN = 'https://ravidorr.github.io';
const config: Config = {
  resendApiKey: 're_test_key',
  contactTo: 'maintainer@example.test',
  from: DEFAULT_FROM,
  ipHashSecret: 'a-secret-of-at-least-16-chars',
  supabaseUrl: 'https://project.supabase.test',
  serviceRoleKey: 'service-role-test-key',
};
const GOOD = { name: 'Dana', email: 'dana@example.test', message: 'Hello there', elapsed: 20_000, website: '' };

interface World {
  deps: Deps;
  emails: Record<string, unknown>[];
  counters: Map<string, number>;
  logs: string[];
  fakeFetch: Mock<Fetch>;
}

function fakeWorld(options: { resend?: number | 'network'; database?: 'down' | 'garbage' | 'release-down'; enabled?: boolean } = {}): World {
  const counters = new Map<string, number>();
  const emails: World['emails'] = [];
  const logs: string[] = [];
  for (const level of ['error', 'warn'] as const) {
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => void logs.push(args.map(String).join(' ')));
  }
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const body = JSON.parse(typeof init.body === 'string' ? init.body : '{}') as Record<string, unknown>;
    if (url === 'https://api.resend.com/emails') {
      if (options.resend === 'network') return Promise.reject(new Error('socket hang up'));
      if (options.resend) return Promise.resolve(new Response('{"echo":"dana@example.test"}', { status: options.resend }));
      emails.push(body);
      return Promise.resolve(Response.json({ id: 'e1' }));
    }
    if (options.database === 'down') return Promise.reject(new Error('connection refused'));
    if (url.endsWith('/rpc/assistant_reserve')) {
      if (options.database === 'garbage') return Promise.resolve(Response.json('surprise'));
      const key = String(body.p_ip_hash);
      const used = counters.get(key) ?? 0;
      if (used >= Number(body.p_limit)) return Promise.resolve(Response.json('limit'));
      counters.set(key, used + 1);
      return Promise.resolve(Response.json('ok'));
    }
    if (url.endsWith('/rpc/assistant_release')) {
      if (options.database === 'release-down') return Promise.resolve(new Response('no', { status: 500 }));
      const key = String(body.p_ip_hash);
      counters.set(key, Math.max(0, (counters.get(key) ?? 0) - 1));
      return Promise.resolve(Response.json('ok'));
    }
    return Promise.resolve(new Response('?', { status: 404 }));
  });
  return {
    deps: { enabled: options.enabled ?? true, config, fetch: fakeFetch, now: () => new Date('2026-11-17T10:00:00Z') },
    emails,
    counters,
    logs,
    fakeFetch,
  };
}

function post(body: unknown, headers: Record<string, string> = {}, ip = '203.0.113.7'): Request {
  return new Request('https://project.supabase.test/functions/v1/contact', {
    method: 'POST',
    headers: { origin: ORIGIN, 'content-type': 'application/json', 'cf-connecting-ip': ip, ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

interface Answer {
  ok?: boolean;
  error: { code: string; message: string; fields?: Record<string, string> };
}
const json = async (response: Response): Promise<Answer> => (await response.json()) as Answer;

describe('the switch', () => {
  it('answers 404 to everything while off, before any other check', async () => {
    const world = fakeWorld({ enabled: false });
    for (const request of [post(GOOD), new Request('https://x.test/', { method: 'OPTIONS' }), post(GOOD, { origin: 'https://evil.test' })]) {
      expect((await handleContact(request, world.deps)).status).toBe(404);
    }
    expect(world.fakeFetch).not.toHaveBeenCalled();
  });

  it('is on only for exactly "true"', () => {
    const env = (value: string) => (name: string): string | undefined =>
      ({ CONTACT_FORM_ENABLED: value, RESEND_API_KEY: 'k', CONTACT_TO: 'a@b.test', IP_HASH_SECRET: 'x'.repeat(16), SUPABASE_URL: 'https://p.test/', SUPABASE_SERVICE_ROLE_KEY: 's' })[name];
    expect(loadConfig(env('true')).enabled).toBe(true);
    for (const value of ['', 'TRUE', '1', 'yes', 'true ']) {
      expect(loadConfig(env(value)).enabled).toBe(value === 'true ');
    }
  });
});

describe('loadConfig', () => {
  const full: Record<string, string> = {
    CONTACT_FORM_ENABLED: 'true',
    RESEND_API_KEY: 'k',
    CONTACT_TO: 'a@b.test',
    IP_HASH_SECRET: 'x'.repeat(16),
    SUPABASE_URL: 'https://p.test/',
    SUPABASE_SERVICE_ROLE_KEY: 's',
  };
  it('reads a full set and defaults only the From address', () => {
    const loaded = loadConfig((name) => full[name]);
    expect('config' in loaded && loaded.config).toMatchObject({ contactTo: 'a@b.test', from: DEFAULT_FROM, supabaseUrl: 'https://p.test' });
  });
  it('has no default destination and names what is wrong, never a value', () => {
    for (const [name, bad] of [['CONTACT_TO', ''], ['CONTACT_TO', 'not an address'], ['CONTACT_FROM_EMAIL', 'a\nb'], ['IP_HASH_SECRET', 'short'], ['SUPABASE_URL', 'ftp://x'], ['RESEND_API_KEY', '']] as const) {
      const loaded = loadConfig((key) => ({ ...full, [name]: bad })[key]);
      expect('missing' in loaded && loaded.missing).toEqual([name]);
    }
  });
});

describe('validateContact', () => {
  it('accepts the message alone', () => {
    expect(validateContact({ message: '  hi  ' })).toEqual({ ok: true, contact: { name: '', email: '', message: 'hi' } });
  });
  it('checks each field with its own words', () => {
    const result = validateContact({ name: 'n'.repeat(101), email: 'a'.repeat(LIMITS.email) + '@b.test', message: ' ' });
    expect(result).toEqual({
      ok: false,
      fields: {
        name: 'The name can be at most 100 characters.',
        email: 'The address can be at most 254 characters.',
        message: 'Enter your message.',
      },
    });
    expect(validateContact({ message: 'x'.repeat(2001) })).toMatchObject({ fields: { message: 'The message can be at most 2000 characters.' } });
  });
  it.each(['a@b', 'a b@c.test', 'a@b.test, c@d.test', 'A <a@b.test>', '"a"@b.test', 'a@b.test\r\nBcc: x@y.test', 'a@b@c.test', 'a;b@c.test'])('refuses the email %j', (email) => {
    expect(validateContact({ email, message: 'hi' })).toMatchObject({ ok: false, fields: { email: 'Enter one email address, like name@example.com.' } });
  });
  it('strips control and bidirectional characters, and keeps the name on one line', () => {
    const result = validateContact({ name: 'Da\r\nna‮', message: 'a\u0000b\r\n\r\n\r\n\r\nc  \nd' });
    expect(result).toEqual({ ok: true, contact: { name: 'Da na', email: '', message: 'ab\n\nc\nd' } });
  });
  it('treats values that are not text as empty', () => {
    expect(validateContact({ name: 5, email: {}, message: ['x'] })).toMatchObject({ ok: false, fields: { message: 'Enter your message.' } });
  });
});

describe('the request', () => {
  it('sends one plain-text email with a fixed subject, a fixed From and the visitor only as Reply-To', async () => {
    const world = fakeWorld();
    const response = await handleContact(post(GOOD), world.deps);
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ ok: true });
    expect(response.headers.get('access-control-allow-origin')).toBe(ORIGIN);
    expect(world.emails).toEqual([
      { from: DEFAULT_FROM, to: ['maintainer@example.test'], subject: SUBJECT, text: expect.stringContaining('Hello there') as string, reply_to: 'dana@example.test' },
    ]);
    expect(Object.keys(world.emails[0] ?? {}).sort()).toEqual(['from', 'reply_to', 'subject', 'text', 'to']);
  });

  it('sends no Reply-To when no email is given, and never emails anyone but the maintainer', async () => {
    const world = fakeWorld();
    await handleContact(post({ ...GOOD, email: '' }), world.deps);
    expect(world.emails[0]).not.toHaveProperty('reply_to');
    expect(world.emails[0]?.to).toEqual(['maintainer@example.test']);
    expect(renderEmail({ name: '', email: '', message: 'm' }, new Date(0)).text).toContain('(not given, so you cannot reply)');
  });

  it('cannot be made to put the visitor in a header', async () => {
    const world = fakeWorld();
    const response = await handleContact(post({ ...GOOD, name: 'x\r\nBcc: spam@example.test', email: 'a@b.test\r\nBcc: spam@example.test' }), world.deps);
    expect(response.status).toBe(400);
    expect(world.emails).toEqual([]);
  });

  it('answers a bot (honeypot, too fast, no time at all) with a fake success and does nothing', async () => {
    const world = fakeWorld();
    for (const extra of [{ website: 'http://spam.test' }, { website: true }, { elapsed: MIN_FILL_MS - 1 }, { elapsed: undefined }, { elapsed: '99999' }, { elapsed: Number.NaN }]) {
      const response = await handleContact(post({ ...GOOD, ...extra }), world.deps);
      expect(response.status).toBe(200);
      expect(await json(response)).toEqual({ ok: true });
    }
    expect(world.emails).toEqual([]);
    expect(world.counters.size).toBe(0);
  });

  it('does not treat an empty or false honeypot as a bot', async () => {
    const world = fakeWorld();
    for (const website of [undefined, null, false, '  ']) await handleContact(post({ ...GOOD, website }), world.deps);
    expect(world.emails).toHaveLength(3);
  });

  it('limits each visitor, then everyone, and gives the visitor place back when the cap refuses', async () => {
    const world = fakeWorld();
    for (let n = 0; n < CALLER_DAILY_LIMIT; n += 1) expect((await handleContact(post(GOOD), world.deps)).status).toBe(200);
    const limited = await handleContact(post(GOOD), world.deps);
    expect(limited.status).toBe(429);
    expect(limited.headers.get('retry-after')).toBe('50400');
    expect((await json(limited)).error.code).toBe('rate_limited');
    // other visitors until the cap
    for (let n = 0; (world.counters.get('contact:all') ?? 0) < DAILY_CAP; n += 1) {
      await handleContact(post(GOOD, {}, `198.51.100.${String(n + 1)}`), world.deps);
    }
    const before = [...world.counters.entries()].filter(([k]) => k !== 'contact:all').length;
    const capped = await handleContact(post(GOOD, {}, '192.0.2.99'), world.deps);
    expect(capped.status).toBe(503);
    expect((await json(capped)).error.code).toBe('daily_cap_reached');
    expect([...world.counters.entries()].filter(([k, v]) => k !== 'contact:all' && v > 0).length).toBe(before);
    expect(world.emails.length).toBe(DAILY_CAP);
  });

  it('keeps the places when the outcome is unknown and gives them back when Resend refuses', async () => {
    const unknown = fakeWorld({ resend: 503 });
    expect((await handleContact(post(GOOD), unknown.deps)).status).toBe(502);
    expect(unknown.counters.get('contact:all')).toBe(1);
    const network = fakeWorld({ resend: 'network' });
    expect((await handleContact(post(GOOD), network.deps)).status).toBe(502);
    expect(network.counters.get('contact:all')).toBe(1);
    const refused = fakeWorld({ resend: 422 });
    const response = await handleContact(post(GOOD), refused.deps);
    expect(response.status).toBe(502);
    expect((await json(response)).error.code).toBe('send_failed');
    expect(refused.counters.get('contact:all')).toBe(0);
    expect([...refused.counters.values()].every((v) => v === 0)).toBe(true);
  });

  it('fails closed when the database is down or answers nonsense, and survives a failed release', async () => {
    for (const database of ['down', 'garbage'] as const) {
      const world = fakeWorld({ database });
      const response = await handleContact(post(GOOD), world.deps);
      expect(response.status).toBe(500);
      expect(world.emails).toEqual([]);
    }
    const world = fakeWorld({ database: 'release-down', resend: 422 });
    expect((await handleContact(post(GOOD), world.deps)).status).toBe(502);
  });

  it('never logs the message, the name, the address or the network', async () => {
    for (const options of [{ resend: 422 }, { resend: 503 }, { database: 'down' }, { resend: 'network' }] as const) {
      const world = fakeWorld(options);
      await handleContact(post({ ...GOOD, message: 'secret-words' }), world.deps);
      const text = world.logs.join('\n');
      for (const secret of ['secret-words', 'Dana', 'dana@example.test', '203.0.113.7', 'echo']) expect(text).not.toContain(secret);
      expect(text).not.toBe('');
    }
    vi.restoreAllMocks();
  });
});

describe('the protocol', () => {
  const world = (): World => fakeWorld();
  it('answers the preflight for the site only, and refuses other origins', async () => {
    const w = world();
    const ok = await handleContact(new Request('https://x.test/', { method: 'OPTIONS', headers: { origin: ORIGIN } }), w.deps);
    expect(ok.status).toBe(204);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ORIGIN);
    const other = await handleContact(post(GOOD, { origin: 'https://evil.test' }), w.deps);
    expect(other.status).toBe(403);
    expect(other.headers.get('access-control-allow-origin')).toBeNull();
    expect(w.emails).toEqual([]);
  });
  it('allows a call with no Origin header (not a browser) but only through the same checks', async () => {
    const w = world();
    const request = new Request('https://x.test/', { method: 'POST', headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.9' }, body: JSON.stringify(GOOD) });
    expect((await handleContact(request, w.deps)).status).toBe(200);
  });
  it('refuses other methods, other media types, a missing config, big and broken bodies', async () => {
    const w = world();
    const get = await handleContact(new Request('https://x.test/', { method: 'GET', headers: { origin: ORIGIN } }), w.deps);
    expect(get.status).toBe(405);
    expect(get.headers.get('allow')).toBe('POST, OPTIONS');
    expect((await handleContact(post('x', { 'content-type': 'text/plain' }), w.deps)).status).toBe(415);
    expect((await handleContact(post(GOOD), { ...w.deps, config: undefined })).status).toBe(500);
    const big = await handleContact(post({ ...GOOD, message: 'x'.repeat(MAX_BODY_BYTES) }), w.deps);
    expect(big.status).toBe(413);
    const declared = await handleContact(post(GOOD, { 'content-length': String(MAX_BODY_BYTES + 1) }), w.deps);
    expect(declared.status).toBe(413);
    expect((await handleContact(post('{nope'), w.deps)).status).toBe(400);
    expect((await handleContact(post('[1]'), w.deps)).status).toBe(400);
    expect((await handleContact(post('null'), w.deps)).status).toBe(400);
    expect(w.emails).toEqual([]);
  });
  it('returns the fields it faulted', async () => {
    const response = await handleContact(post({ ...GOOD, message: '' }), world().deps);
    expect(response.status).toBe(400);
    expect((await json(response)).error).toMatchObject({ code: 'invalid_input', fields: { message: 'Enter your message.' } });
  });
  it('turns an unexpected failure into a plain 500', async () => {
    const w = world();
    const request = post(GOOD);
    vi.spyOn(request, 'headers', 'get').mockImplementation(() => {
      throw new Error('boom');
    });
    expect((await handleContact(request, w.deps)).status).toBe(500);
  });
});
