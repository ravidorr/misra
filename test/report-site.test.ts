import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import {
  ALLOWED_ORIGIN,
  DAILY_CAP,
  DEFAULT_FROM,
  IP_DAILY_LIMIT,
  MAX_BODY_BYTES,
  handleReport,
  hashVisitor,
  loadConfig,
  normalizeAddress,
  renderEmail,
  visitorNetwork,
} from '../supabase/functions/report-site/handler.ts';
import type { Config, Deps, Fetch } from '../supabase/functions/report-site/handler.ts';
import { LIMITS, checkLink, validateReport } from '../supabase/functions/report-site/validate.ts';

const SECRET = 'a-secret-of-at-least-16-chars';
const config: Config = {
  resendApiKey: 're_test_key',
  reportTo: 'maintainer@example.test',
  from: DEFAULT_FROM,
  ipHashSecret: SECRET,
  supabaseUrl: 'https://project.supabase.test',
  serviceRoleKey: 'service-role-test-key',
};
const GOOD = { company: 'Acme Robotics', link: 'https://acme.example/careers', why: 'They hire juniors in Haifa.' };

/** A fake of the two services: the Postgres functions of the migration (same rules) and Resend. */
interface World {
  deps: Deps;
  emails: { url: string; headers: Record<string, string>; body: Record<string, unknown> }[];
  cap: { sent: number };
  calls: string[];
  reports: Map<string, number>;
  fakeFetch: Mock<Fetch>;
}

/** Rejects with something that is not an Error, as a broken library might. */
function rejectWithText(reason: string): Promise<never> {
  // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- the point is a non-Error rejection
  return Promise.reject(reason);
}

const anyString = expect.any(String) as string;
const contains = (text: string): string => expect.stringContaining(text) as string;

function fakeWorld(options: { resend?: number; database?: 'down' | 'garbage' | 'release-down' } = {}): World {
  const reports = new Map<string, number>();
  const cap = { sent: 0 };
  const emails: World['emails'] = [];
  const calls: string[] = [];
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const body = JSON.parse(typeof init.body === 'string' ? init.body : '{}') as Record<string, unknown>;
    calls.push(url);
    if (url === 'https://api.resend.com/emails') {
      emails.push({ url, headers: init.headers as Record<string, string>, body });
      return Promise.resolve(
        options.resend && options.resend >= 400
          ? new Response('{"name":"validation_error"}', { status: options.resend })
          : Response.json({ id: 'email-1' }),
      );
    }
    if (options.database === 'down') return Promise.reject(new Error('connection refused'));
    if (url.endsWith('/rpc/report_site_reserve')) {
      if (options.database === 'garbage') return Promise.resolve(Response.json('surprise'));
      const key = `${String(body.p_day)}|${String(body.p_ip_hash)}`;
      const used = (reports.get(key) ?? 0) + 1;
      reports.set(key, used);
      if (used > Number(body.p_ip_limit)) return Promise.resolve(Response.json('ip_limit'));
      if (cap.sent >= Number(body.p_daily_cap)) {
        reports.set(key, used - 1);
        return Promise.resolve(Response.json('daily_cap'));
      }
      cap.sent += 1;
      return Promise.resolve(Response.json('ok'));
    }
    if (url.endsWith('/rpc/report_site_release')) {
      if (options.database === 'release-down') return Promise.resolve(new Response('no', { status: 500 }));
      cap.sent = Math.max(0, cap.sent - 1);
      const key = `${String(body.p_day)}|${String(body.p_ip_hash)}`;
      reports.set(key, Math.max(0, (reports.get(key) ?? 0) - 1));
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.resolve(new Response('unexpected', { status: 404 }));
  });
  const deps: Deps = { config, fetch: fakeFetch, now: () => new Date('2026-11-16T10:00:00Z') };
  return { deps, emails, cap, calls, reports, fakeFetch };
}

function post(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request('https://project.supabase.test/functions/v1/report-site', {
    method: 'POST',
    headers: { 'content-type': 'application/json', origin: ALLOWED_ORIGIN, ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

async function answer(response: Response): Promise<{ status: number; json: Record<string, unknown> }> {
  return { status: response.status, json: (await response.json()) as Record<string, unknown> };
}

function silence(): void {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
}

describe('a good report', () => {
  it('sends one plain-text email to the maintainer and says ok', async () => {
    const world = fakeWorld();
    const response = await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    expect(await answer(response)).toEqual({ status: 200, json: { ok: true } });
    expect(world.emails).toHaveLength(1);
    const [email] = world.emails;
    expect(email?.headers.authorization).toBe('Bearer re_test_key');
    expect(email?.body).toMatchObject({ from: DEFAULT_FROM, to: ['maintainer@example.test'], subject: 'Site report: Acme Robotics' });
    expect(email?.body).not.toHaveProperty('html');
    expect(String(email?.body.text)).toContain('Careers page: https://acme.example/careers');
    expect(String(email?.body.text)).toContain('They hire juniors in Haifa.');
  });

  it('never gives the database the visitor address, only a hash that differs from day to day', async () => {
    const world = fakeWorld();
    await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    const stored = [...world.reports.keys()].join(' ');
    expect(stored).not.toContain('203.0.113.7');
    expect(stored).toMatch(/^2026-11-16\|[0-9a-f]{64}$/u);
    const today = await hashVisitor(SECRET, '2026-11-16', '203.0.113.7');
    expect(await hashVisitor(SECRET, '2026-11-17', '203.0.113.7')).not.toBe(today);
    expect(await hashVisitor('another-secret-16-chars-', '2026-11-16', '203.0.113.7')).not.toBe(today);
    expect(await hashVisitor(SECRET, '2026-11-16', '203.0.113.8')).not.toBe(today);
  });

  it('sends the service-role key only to the database and the Resend key only to Resend', async () => {
    const world = fakeWorld();
    await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    for (const [url, init] of world.fakeFetch.mock.calls) {
      const sent = JSON.stringify(init.headers);
      if (new URL(url).hostname === 'api.resend.com') expect(sent).not.toContain('service-role-test-key');
      else expect(sent).not.toContain('re_test_key');
    }
  });
});

describe('the flood', () => {
  it('sends at most the cap in a day from many visitors, then answers daily_cap_reached', async () => {
    const world = fakeWorld();
    const outcomes: number[] = [];
    let last: Record<string, unknown> = {};
    for (let n = 0; n < DAILY_CAP + 15; n += 1) {
      const response = await handleReport(post(GOOD, { 'cf-connecting-ip': `198.51.100.${String(n)}` }), world.deps);
      outcomes.push(response.status);
      last = ((await response.json()) as Record<string, unknown>);
      if (response.status === 503) expect(response.headers.get('retry-after')).toBe('50400');
    }
    expect(world.emails).toHaveLength(DAILY_CAP);
    expect(outcomes.filter((status) => status === 200)).toHaveLength(DAILY_CAP);
    expect(outcomes.slice(DAILY_CAP).every((status) => status === 503)).toBe(true);
    expect(last).toMatchObject({ ok: false, error: { code: 'daily_cap_reached' } });
  });

  it('lets one visitor send only the daily limit and tells them so', async () => {
    const world = fakeWorld();
    const statuses: number[] = [];
    for (let n = 0; n < IP_DAILY_LIMIT + 2; n += 1) {
      const response = await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
      statuses.push(response.status);
      if (response.status === 429) {
        expect(response.headers.get('retry-after')).toBe('50400');
        expect(await response.json()).toMatchObject({ ok: false, error: { code: 'rate_limited' } });
      }
    }
    expect(statuses).toEqual([200, 200, 200, 429, 429]);
    expect(world.emails).toHaveLength(IP_DAILY_LIMIT);
    // another visitor is not affected
    expect((await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.8' }), world.deps)).status).toBe(200);
  });

  it('cannot be got around by writing x-forwarded-for, and a visitor over the limit does not use up the cap', async () => {
    const world = fakeWorld();
    for (let n = 0; n < 10; n += 1) {
      await handleReport(post(GOOD, { 'x-forwarded-for': `10.0.0.${String(n)}, 192.0.2.50` }), world.deps);
    }
    expect(world.emails).toHaveLength(IP_DAILY_LIMIT);
    expect(world.cap.sent).toBe(IP_DAILY_LIMIT);
  });

  it('starts again the next day', async () => {
    const world = fakeWorld();
    for (let n = 0; n < IP_DAILY_LIMIT; n += 1) await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    expect((await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps)).status).toBe(429);
    world.cap.sent = 0; // the database starts a new day with an empty cap row
    const tomorrow: Deps = { ...world.deps, now: () => new Date('2026-11-17T00:00:01Z') };
    expect((await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), tomorrow)).status).toBe(200);
  });

  it('gives the place back when Resend fails, so a failure does not eat the cap', async () => {
    silence();
    const world = fakeWorld({ resend: 422 });
    const result = await answer(await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps));
    expect(result).toMatchObject({ status: 502, json: { ok: false, error: { code: 'send_failed' } } });
    expect(world.cap.sent).toBe(0);
    expect([...world.reports.values()]).toEqual([0]); // the visitor's own report is given back too
  });

  it('keeps the place when the outcome is unknown (a 5xx or a timeout may have sent the email)', async () => {
    silence();
    const world = fakeWorld({ resend: 503 });
    const result = await answer(await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps));
    expect(result.status).toBe(502);
    expect(world.cap.sent).toBe(1);
    expect(world.calls.some((url) => url.endsWith('report_site_release'))).toBe(false);
  });

  it('still answers send_failed when giving the place back fails too', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const world = fakeWorld({ resend: 422, database: 'release-down' });
    const result = await answer(await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps));
    expect(result.status).toBe(502);
    expect(JSON.stringify(spy.mock.calls)).toContain('could not give the place back for 2026-11-16');
  });

  it('never releases a place for a refusal, and does not call Resend', async () => {
    for (const refusal of ['ip_limit', 'daily_cap']) {
      const world = fakeWorld();
      const calls: string[] = [];
      const deps: Deps = {
        ...world.deps,
        fetch: (url) => {
          calls.push(url);
          return Promise.resolve(Response.json(refusal));
        },
      };
      const response = await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), deps);
      expect(response.status).toBe(refusal === 'ip_limit' ? 429 : 503);
      expect(response.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
      expect(calls).toEqual(['https://project.supabase.test/rest/v1/rpc/report_site_reserve']);
    }
  });

  it('calls the database with the exact function, key and parameters', async () => {
    const world = fakeWorld();
    await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    const [url, init] = world.fakeFetch.mock.calls[0] ?? ['', {}];
    expect(url).toBe('https://project.supabase.test/rest/v1/rpc/report_site_reserve');
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ apikey: 'service-role-test-key', authorization: 'Bearer service-role-test-key' });
    const sent = JSON.parse(typeof init.body === 'string' ? init.body : '{}') as Record<string, unknown>;
    expect(Object.keys(sent).sort()).toEqual(['p_daily_cap', 'p_day', 'p_ip_hash', 'p_ip_limit']);
    expect(sent).toMatchObject({ p_day: '2026-11-16', p_ip_limit: 3, p_daily_cap: 20 });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it('sends only from, to, subject and text to Resend, whatever extra fields the caller adds', async () => {
    const world = fakeWorld();
    const extra = { ...GOOD, to: 'x@y.test', from: 'z@y.test', bcc: 'b@y.test', html: '<b>x</b>', headers: {}, reply_to: 'r@y.test' };
    await handleReport(post(extra, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    expect(Object.keys(world.emails[0]?.body ?? {}).sort()).toEqual(['from', 'subject', 'text', 'to']);
    expect(world.emails[0]?.body.to).toEqual(['maintainer@example.test']);
  });

  it('puts the CORS headers on error answers too, so the page can read them', async () => {
    const world = fakeWorld();
    const bad = await handleReport(post({ company: '' }), world.deps);
    const wrongType = await handleReport(post('{}', { 'content-type': 'text/plain' }), world.deps);
    const unconfigured = await handleReport(post(GOOD), { ...world.deps, config: undefined });
    for (const response of [bad, wrongType, unconfigured]) {
      expect(response.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
      expect(response.headers.get('cache-control')).toBe('no-store');
    }
  });
});

describe('bad requests', () => {
  it('answers a bad input with the fields to fix, and sends and counts nothing', async () => {
    const world = fakeWorld();
    const result = await answer(await handleReport(post({ company: '', link: 'ftp://x.example', why: '' }), world.deps));
    expect(result.status).toBe(400);
    expect(result.json).toMatchObject({
      ok: false,
      error: { code: 'invalid_input', fields: { company: anyString, link: anyString, why: anyString } },
    });
    expect(world.calls).toEqual([]);
  });

  it('treats a filled honeypot as success without sending or counting anything', async () => {
    const world = fakeWorld();
    for (const website of ['http://spam.example', 'x', 1, true, {}, ['a']]) {
      const result = await answer(await handleReport(post({ ...GOOD, website }), world.deps));
      expect(result).toEqual({ status: 200, json: { ok: true } });
    }
    expect(world.calls).toEqual([]);
    for (const website of ['', '  ', null, false, undefined]) {
      const result = await answer(await handleReport(post({ ...GOOD, website, link: 'nope' }), world.deps));
      expect(result.status).toBe(400); // an empty honeypot is just a normal request
    }
  });

  it('refuses a body that is not a JSON object, too large, or not JSON at all', async () => {
    const world = fakeWorld();
    for (const body of ['not json', '[1]', 'null', '"text"', '']) {
      const result = await answer(await handleReport(post(body), world.deps));
      expect(result).toMatchObject({ status: 400, json: { error: { code: 'invalid_request' } } });
    }
    const big = JSON.stringify({ ...GOOD, why: 'x'.repeat(MAX_BODY_BYTES) });
    expect((await handleReport(post(big), world.deps)).status).toBe(413);
    expect((await handleReport(post(big, { 'content-length': String(big.length) }), world.deps)).status).toBe(413);
    const wide = JSON.stringify({ ...GOOD, why: '€'.repeat(MAX_BODY_BYTES / 2) }); // few characters, too many bytes
    expect(wide.length).toBeLessThan(MAX_BODY_BYTES);
    expect((await handleReport(post(wide), world.deps)).status).toBe(413);
    expect(world.calls).toEqual([]);
  });

  it('stops reading a body that has no length once it is over the limit', async () => {
    const world = fakeWorld();
    let pulled = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled += 1;
        controller.enqueue(new Uint8Array(1024).fill(97));
        if (pulled > 1000) controller.close();
      },
    });
    const request = new Request('https://project.supabase.test/', {
      method: 'POST',
      headers: { 'content-type': 'application/json', origin: ALLOWED_ORIGIN },
      body: stream,
      duplex: 'half',
    } as RequestInit);
    expect((await handleReport(request, world.deps)).status).toBe(413);
    expect(pulled).toBeLessThan(20);
    expect(world.calls).toEqual([]);
  });

  it('wants application/json and POST', async () => {
    const world = fakeWorld();
    const plain = post(JSON.stringify(GOOD), { 'content-type': 'text/plain' });
    expect(await answer(await handleReport(plain, world.deps))).toMatchObject({ status: 415, json: { error: { code: 'unsupported_media_type' } } });
    const noType = new Request('https://project.supabase.test/', { method: 'POST', body: '{}', headers: { origin: ALLOWED_ORIGIN } });
    expect((await handleReport(noType, world.deps)).status).toBe(415);
    const get = new Request('https://project.supabase.test/', { method: 'GET' });
    const refused = await handleReport(get, world.deps);
    expect(refused.status).toBe(405);
    expect(refused.headers.get('allow')).toBe('POST, OPTIONS');
  });
});

describe('failures on our side', () => {
  it('sends nothing when the database cannot be reached or answers nonsense', async () => {
    silence();
    for (const database of ['down', 'garbage'] as const) {
      const world = fakeWorld({ database });
      const result = await answer(await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps));
      expect(result).toMatchObject({ status: 500, json: { ok: false, error: { code: 'server_error' } } });
      expect(world.emails).toEqual([]);
    }
  });

  it('answers server_error when it is not configured, without a stack trace or a secret in the answer', async () => {
    const world = fakeWorld();
    const response = await handleReport(post(GOOD), { ...world.deps, config: undefined });
    const text = await response.text();
    expect(response.status).toBe(500);
    expect(world.calls).toEqual([]);
    expect(text).toContain('server_error');
    expect(text).not.toMatch(/re_test_key|service-role|SECRET|RESEND/u);
  });

  it('does not put a secret in the logs or the answer when Resend fails', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const world = fakeWorld({ resend: 401 });
    const response = await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    expect(await response.text()).not.toMatch(/re_test_key|validation_error/u);
    expect(JSON.stringify(spy.mock.calls)).not.toMatch(/re_test_key|service-role-test-key|203\.0\.113\.7|Acme/u);
  });

  it('reports a thrown non-Error without crashing', async () => {
    silence();
    const world = fakeWorld();
    const deps: Deps = { ...world.deps, fetch: () => rejectWithText('plain string') };
    expect((await handleReport(post(GOOD), deps)).status).toBe(500);
    const resendOnly: Deps = {
      ...world.deps,
      fetch: (url, init) => (url.includes('resend') ? rejectWithText('plain string') : world.deps.fetch(url, init)),
    };
    expect((await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.9' }), resendOnly)).status).toBe(502);
  });
});

describe('CORS', () => {
  it('allows the GitHub Pages origin and no other', async () => {
    const world = fakeWorld();
    const ok = await handleReport(post(GOOD, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ok.headers.get('vary')).toBe('Origin');
    for (const origin of ['https://evil.example', 'http://ravidorr.github.io', 'https://ravidorr.github.io.evil.example', 'null', 'https://RAVIDORR.github.io']) {
      const refused = await handleReport(post(GOOD, { origin }), world.deps);
      expect(refused.status).toBe(403);
      expect(refused.headers.get('access-control-allow-origin')).toBeNull();
    }
    expect(world.emails).toHaveLength(1);
  });

  it('answers the preflight for the allowed origin only', async () => {
    const world = fakeWorld();
    const preflight = (origin?: string): Request =>
      new Request('https://project.supabase.test/', {
        method: 'OPTIONS',
        headers: origin ? { origin, 'access-control-request-method': 'POST' } : {},
      });
    const ok = await handleReport(preflight(ALLOWED_ORIGIN), world.deps);
    expect(ok.status).toBe(204);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ok.headers.get('access-control-allow-methods')).toBe('POST, OPTIONS');
    expect(ok.headers.get('access-control-allow-headers')).toContain('content-type');
    expect((await handleReport(preflight('https://evil.example'), world.deps)).status).toBe(403);
    const noOrigin = await handleReport(preflight(), world.deps);
    expect(noOrigin.status).toBe(204);
    expect(noOrigin.headers.get('access-control-allow-origin')).toBeNull();
    expect(world.calls).toEqual([]);
  });

  it('allows a call with no Origin (curl, a test) because the limits protect the inbox either way', async () => {
    const world = fakeWorld();
    const request = new Request('https://project.supabase.test/', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'cf-connecting-ip': '203.0.113.7' },
      body: JSON.stringify(GOOD),
    });
    const response = await handleReport(request, world.deps);
    expect(response.status).toBe(200);
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
  });
});

describe('email content', () => {
  it('has no way to inject a header or markup', async () => {
    const world = fakeWorld();
    const evil = {
      company: 'Evil\r\nBcc: victim@example.test\nSubject: pwned <b>x</b>',
      link: 'https://evil.example/a?b=<script>alert(1)</script>',
      why: '<img src=x onerror=alert(1)>\r\nBcc: victim@example.test',
    };
    await handleReport(post(evil, { 'cf-connecting-ip': '203.0.113.7' }), world.deps);
    const [email] = world.emails;
    const subject = String(email?.body.subject);
    expect(subject).not.toMatch(/[\r\n]/u);
    expect(email?.body).not.toHaveProperty('html');
    expect(email?.body).not.toHaveProperty('bcc');
    expect(email?.body).not.toHaveProperty('cc');
    expect(email?.body.to).toEqual(['maintainer@example.test']);
    expect(String(email?.body.text)).not.toContain('\r');
    expect(String(email?.body.text)).not.toContain('<script>'); // the address is percent-encoded
  });

  it('renders the report as text', () => {
    const { subject, text } = renderEmail({ company: 'A'.repeat(200), link: 'https://a.example/', why: 'w' }, new Date('2026-11-16T10:00:00Z'));
    expect(subject.length).toBeLessThanOrEqual(150);
    expect(text).toContain('Received: 2026-11-16T10:00:00.000Z');
  });
});

describe('the visitor network', () => {
  it('prefers the Cloudflare address, then the last forwarded entry, then one shared bucket', () => {
    const headers = (init: Record<string, string>): Headers => new Headers(init);
    expect(visitorNetwork(headers({ 'cf-connecting-ip': '203.0.113.7', 'x-forwarded-for': '1.1.1.1' }))).toBe('203.0.113.7');
    expect(visitorNetwork(headers({ 'cf-connecting-ip': 'garbage', 'x-forwarded-for': '1.1.1.1, 192.0.2.9' }))).toBe('192.0.2.9');
    expect(visitorNetwork(headers({ 'x-forwarded-for': '1.1.1.1, 192.0.2.9' }))).toBe('192.0.2.9');
    expect(visitorNetwork(headers({ 'x-forwarded-for': 'garbage' }))).toBe('unknown');
    expect(visitorNetwork(headers({}))).toBe('unknown');
  });

  it('normalises addresses: IPv6 to its /64, IPv4-mapped to IPv4, junk to nothing', () => {
    expect(normalizeAddress(' 203.0.113.7 ')).toBe('203.0.113.7');
    expect(normalizeAddress('::ffff:203.0.113.7')).toBe('203.0.113.7');
    expect(normalizeAddress('2001:db8:1:2:3:4:5:6')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeAddress('2001:DB8:1:2::9')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeAddress('2001:db8::1')).toBe('2001:0db8:0000:0000::/64');
    expect(normalizeAddress('::1')).toBe('0000:0000:0000:0000::/64');
    expect(normalizeAddress('[2001:db8:1:2::1]')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeAddress('fe80::1%eth0')).toBe('fe80:0000:0000:0000::/64');
    expect(normalizeAddress('2001:db8:1:2:aaaa::ffff')).toBe('2001:0db8:0001:0002::/64');
    for (const junk of ['', 'unknown', '999.1.1.1', '1.2.3', '1.2.3.4.5', '1::2::3', '1:2:3', '1:2:3:4:5:6:7:8:9', '12345::1', 'g::1', '::ffff:1.2.3', ':', '1:2:3:4:5:6:7::8']) {
      expect(normalizeAddress(junk), junk).toBeUndefined();
    }
  });
});

describe('loadConfig', () => {
  const env: Record<string, string> = {
    RESEND_API_KEY: 're_x',
    REPORT_TO_EMAIL: 'maintainer@example.test',
    IP_HASH_SECRET: SECRET,
    SUPABASE_URL: 'https://project.supabase.test/',
    SUPABASE_SERVICE_ROLE_KEY: 'key',
  };
  const load = (extra: Record<string, string | undefined> = {}): ReturnType<typeof loadConfig> => loadConfig((name) => ({ ...env, ...extra })[name]);

  it('reads the secrets, strips a trailing slash and defaults the sender', () => {
    expect(load()).toEqual({
      config: {
        resendApiKey: 're_x',
        reportTo: 'maintainer@example.test',
        from: DEFAULT_FROM,
        ipHashSecret: SECRET,
        supabaseUrl: 'https://project.supabase.test',
        serviceRoleKey: 'key',
      },
    });
    expect(load({ REPORT_FROM_EMAIL: 'reports@send.avidor.org' })).toMatchObject({ config: { from: 'reports@send.avidor.org' } });
  });

  it('names what is missing or malformed, never a value', () => {
    expect(load({ RESEND_API_KEY: undefined, IP_HASH_SECRET: ' ' })).toEqual({ missing: ['RESEND_API_KEY', 'IP_HASH_SECRET'] });
    expect(load({ REPORT_TO_EMAIL: 'a@b.test\nBcc: x@y.test' })).toEqual({ missing: ['REPORT_TO_EMAIL'] });
    expect(load({ REPORT_TO_EMAIL: 'a@b.test, c@d.test' })).toEqual({ missing: ['REPORT_TO_EMAIL'] });
    expect(load({ REPORT_FROM_EMAIL: 'Misra\r\nBcc: x@y.test <a@b.test>' })).toEqual({ missing: ['REPORT_FROM_EMAIL'] });
    expect(load({ SUPABASE_URL: 'project.supabase.test' })).toEqual({ missing: ['SUPABASE_URL'] });
    expect(load({ IP_HASH_SECRET: 'short' })).toEqual({ missing: ['IP_HASH_SECRET'] });
  });
});

describe('validateReport', () => {
  it('accepts a good report and normalises it', () => {
    expect(validateReport({ company: '  Acme \n Robotics  ', link: ' https://Acme.example/careers#jobs ', why: ' Hires juniors. \r\n\r\n\r\n\r\nSecond  \nline ' })).toEqual({
      ok: true,
      report: { company: 'Acme Robotics', link: 'https://acme.example/careers#jobs', why: 'Hires juniors.\n\nSecond\nline' },
    });
  });

  it('removes control and direction-override characters', () => {
    const result = validateReport({ company: 'Ac\u0000me\u202E', link: GOOD.link, why: 'a\u0007b\u2028c\u0085d' });
    expect(result).toMatchObject({ ok: true, report: { company: 'Acme', why: 'abcd' } });
  });

  it('refuses anything that is not a string and any length over the limit', () => {
    for (const value of [undefined, null, 1, true, [], {}, ['a']]) {
      const result = validateReport({ company: value, link: value, why: value });
      expect(result).toMatchObject({ ok: false, fields: { company: anyString, link: anyString, why: anyString } });
    }
    const long = validateReport({ company: 'c'.repeat(LIMITS.company + 1), link: 'https://a.example/' + 'x'.repeat(LIMITS.link), why: 'w'.repeat(LIMITS.why + 1) });
    expect(long).toMatchObject({ ok: false, fields: { company: contains('100'), link: contains('500'), why: contains('1000') } });
    expect(validateReport({ company: 'c'.repeat(LIMITS.company), link: GOOD.link, why: 'w'.repeat(LIMITS.why) }).ok).toBe(true);
    expect(validateReport({ company: ' \n ', link: GOOD.link, why: 'ab' })).toMatchObject({ ok: false, fields: { company: anyString, why: contains('few words') } });
  });
});

describe('checkLink', () => {
  it('accepts http and https addresses of a public host', () => {
    expect(checkLink('http://jobs.example.co.il/x?y=1')).toEqual({ link: 'http://jobs.example.co.il/x?y=1' });
    expect(checkLink('https://[2001:db8::1]/jobs')).toEqual({ link: 'https://[2001:db8::1]/jobs' });
    expect(checkLink('https://acme.example/a b')).toHaveProperty('error'); // a space is refused, not encoded
  });

  it('refuses everything else', () => {
    for (const bad of [
      '', '   ', 'acme.example/careers', 'javascript:alert(1)', 'data:text/html,<b>x</b>', 'ftp://acme.example', 'file:///etc/passwd', 'mailto:a@b.example',
      'https://user:pw@acme.example/', 'https://user@acme.example/', 'https://localhost/', 'https://intranet/', 'https://', 'https://acme.example/\njavascript:x', 'https://acme.example/\u0000', 'https://acme .example', 'not a url',
    ]) {
      expect(checkLink(bad), bad).toHaveProperty('error');
    }
    expect(checkLink(42)).toHaveProperty('error');
  });
});
