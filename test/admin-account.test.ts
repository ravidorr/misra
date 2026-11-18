import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import { ALLOWED_ORIGIN, handleAdminAccount, loadConfig } from '../supabase/functions/admin-account/handler.ts';
import { deleteAuthUser } from '../supabase/functions/_shared/delete-user.ts';
import type { Config, Deps, Fetch } from '../supabase/functions/admin-account/handler.ts';

const FUNCTION_URL = 'https://project.supabase.test/functions/v1/admin-account';
const SERVICE_KEY = 'service-role-test-key';
const ANON_KEY = 'anon-test-key';
const config: Config = { supabaseUrl: 'https://project.supabase.test', serviceRoleKey: SERVICE_KEY, anonKey: ANON_KEY };
const NOW = new Date('2030-03-04T05:06:07.000Z');

const ADMIN_TOKEN = 'admin.token.value';
const USER_TOKEN = 'user.token.value';
const ANN_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const BOB_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const ANN_ADDRESS = 'ann.private@example.test';
const BOB_LOGIN = 'bob.private@example.test';
const BOB_DIGEST = 'bob.digest@example.test';
const TWIN = 'twin@example.test';

type Fault = 'none' | 'admin-down' | 'admin-5xx' | 'admin-garbage' | 'ids-fail' | 'ids-garbage' | 'log-fail' | 'unsubscribe-fail' | 'session-revoked' | 'session-down' | 'session-5xx' | 'delete-down' | 'delete-5xx' | 'delete-4xx';

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string;
}

interface World {
  deps: Deps;
  calls: Call[];
  log: Record<string, unknown>[];
  unsubscribed: string[];
  deleted: string[];
}

/** The database and Auth as the function sees them: two accounts, an admin check by token, and what the function writes. */
function world(fault: Fault = 'none'): World {
  const calls: Call[] = [];
  const log: Record<string, unknown>[] = [];
  const unsubscribed: string[] = [];
  const deleted: string[] = [];
  const logins = new Map([
    [ANN_ADDRESS, [ANN_ID]],
    [BOB_LOGIN, [BOB_ID]],
  ]);
  const digests = new Map([[BOB_DIGEST, [BOB_ID]]]);
  logins.set(TWIN, [ANN_ID, BOB_ID]);
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const headers = init.headers as Record<string, string>;
    const body = typeof init.body === 'string' ? init.body : '';
    calls.push({ url, method: String(init.method), headers, body });
    const rest = url.replace(config.supabaseUrl, '');
    const asService = headers.authorization === `Bearer ${SERVICE_KEY}`;
    if (rest === '/rest/v1/rpc/is_admin') {
      if (fault === 'admin-down') return Promise.reject(new Error('connection refused'));
      if (fault === 'admin-5xx') return Promise.resolve(new Response('no', { status: 502 }));
      if (fault === 'admin-garbage') return Promise.resolve(new Response('not json', { status: 200 }));
      const token = (headers.authorization ?? '').replace('Bearer ', '');
      if (token !== ADMIN_TOKEN && token !== USER_TOKEN) return Promise.resolve(new Response('{}', { status: 401 }));
      return Promise.resolve(Response.json(token === ADMIN_TOKEN));
    }
    if (rest === '/auth/v1/user') {
      if (fault === 'session-down') return Promise.reject(new Error('connection refused'));
      if (fault === 'session-5xx') return Promise.resolve(new Response('no', { status: 502 }));
      if (fault === 'session-revoked') return Promise.resolve(Response.json({ error_code: 'session_not_found' }, { status: 403 }));
      const token = (headers.authorization ?? '').replace('Bearer ', '');
      return Promise.resolve(token === ADMIN_TOKEN ? Response.json({ id: ANN_ID, role: 'authenticated' }) : new Response('{}', { status: 401 }));
    }
    if (!asService) return Promise.resolve(new Response('{}', { status: 401 }));
    if (rest === '/rest/v1/rpc/admin_account_ids') {
      if (fault === 'ids-fail') return Promise.resolve(new Response('boom', { status: 500 }));
      if (fault === 'ids-garbage') return Promise.resolve(Response.json(['../admin']));
      const args = JSON.parse(body) as { p_address: string; p_login_only: boolean };
      const found = new Set([...(logins.get(args.p_address) ?? []), ...(args.p_login_only ? [] : (digests.get(args.p_address) ?? []))]);
      return Promise.resolve(Response.json([...found]));
    }
    if (rest === '/rest/v1/rpc/unsubscribe_user') {
      if (fault === 'unsubscribe-fail') return Promise.resolve(new Response('boom', { status: 500 }));
      unsubscribed.push((JSON.parse(body) as { p_user_id: string }).p_user_id);
      return Promise.resolve(Response.json('unsubscribed'));
    }
    if (rest === '/rest/v1/admin_log') {
      if (fault === 'log-fail') return Promise.resolve(new Response('boom', { status: 500 }));
      log.push(JSON.parse(body) as Record<string, unknown>);
      return Promise.resolve(new Response(null, { status: 201 }));
    }
    const target = /^\/auth\/v1\/admin\/users\/(.+)$/u.exec(rest);
    if (target && init.method === 'DELETE') {
      if (fault === 'delete-down') return Promise.reject(new Error('timeout'));
      if (fault === 'delete-5xx') return Promise.resolve(new Response('no', { status: 502 }));
      if (fault === 'delete-4xx') return Promise.resolve(new Response('no', { status: 403 }));
      deleted.push(target[1] ?? '');
      return Promise.resolve(new Response('{}', { status: 200 }));
    }
    return Promise.resolve(new Response('not found', { status: 404 }));
  });
  return { deps: { config, fetch: fakeFetch, now: () => NOW }, calls, log, unsubscribed, deleted };
}

function ask(body: unknown, token: string | null = ADMIN_TOKEN, init: RequestInit = {}): Request {
  return new Request(FUNCTION_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: typeof body === 'string' ? body : JSON.stringify(body),
    ...init,
  });
}

interface Answer {
  ok: boolean;
  result?: string;
  message?: string;
  error?: { code: string; message: string };
}

async function answer(response: Response): Promise<Answer> {
  return (await response.json()) as Answer;
}

let spies: Record<'log' | 'info' | 'warn' | 'error' | 'debug', MockInstance>;
beforeEach(() => {
  spies = {
    log: vi.spyOn(console, 'log').mockImplementation(() => undefined),
    info: vi.spyOn(console, 'info').mockImplementation(() => undefined),
    warn: vi.spyOn(console, 'warn').mockImplementation(() => undefined),
    error: vi.spyOn(console, 'error').mockImplementation(() => undefined),
    debug: vi.spyOn(console, 'debug').mockImplementation(() => undefined),
  };
});
afterEach(() => {
  vi.restoreAllMocks();
});

function everythingLogged(): string {
  return Object.values(spies)
    .flatMap((spy) => spy.mock.calls)
    .map((args) => args.map(String).join(' '))
    .join('\n');
}

describe('who may call it: everyone else gets a bare 404', () => {
  it('answers 404 with no body to a signed-in user who is not an admin, and does nothing', async () => {
    const w = world();
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }, USER_TOKEN), w.deps);
    expect(response.status).toBe(404);
    expect(await response.text()).toBe('');
    expect(w.deleted).toEqual([]);
    expect(w.log).toEqual([]);
    expect(w.calls.map((c) => c.url)).toEqual([`${config.supabaseUrl}/rest/v1/rpc/is_admin`]); // the check, nothing after it
  });

  it.each([
    ['a missing token', {}],
    ['a malformed token', { authorization: 'Bearer not a token!' }],
    ['a token the database does not accept', { authorization: 'Bearer forged.token.value' }],
  ])('answers 404 for %s', async (_name, extra) => {
    const w = world();
    const request = ask({ address: ANN_ADDRESS, action: 'end_alerts' }, null, { headers: { 'content-type': 'application/json', ...extra } });
    const response = await handleAdminAccount(request, w.deps);
    expect(response.status).toBe(404);
    expect(await response.text()).toBe('');
    expect(w.unsubscribed).toEqual([]);
    expect(w.log).toEqual([]);
    expect(w.calls.length).toBe(_name === 'a token the database does not accept' ? 1 : 0); // a bad token never reaches the database
  });

  it('answers 404 to anything but a POST, and to another origin, with no database call', async () => {
    const w = world();
    for (const method of ['GET', 'PUT', 'DELETE', 'HEAD']) {
      const response = await handleAdminAccount(new Request(FUNCTION_URL, { method, headers: { authorization: `Bearer ${ADMIN_TOKEN}` } }), w.deps);
      expect(response.status, method).toBe(404);
    }
    const other = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }, ADMIN_TOKEN, { headers: { origin: 'https://evil.example', authorization: `Bearer ${ADMIN_TOKEN}` } }), w.deps);
    expect(other.status).toBe(404);
    expect(w.calls).toEqual([]);
  });

  it('asks is_admin with the caller token and the public key, never the service-role key', async () => {
    const w = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), w.deps);
    const check = w.calls[0];
    expect(check?.url).toBe(`${config.supabaseUrl}/rest/v1/rpc/is_admin`);
    expect(check?.headers).toMatchObject({ apikey: ANON_KEY, authorization: `Bearer ${ADMIN_TOKEN}` });
    expect(JSON.stringify(check)).not.toContain(SERVICE_KEY);
  });

  it('answers the CORS preflight for the dashboard origin only, with no check and no body', async () => {
    const w = world();
    const ok = await handleAdminAccount(new Request(FUNCTION_URL, { method: 'OPTIONS', headers: { origin: ALLOWED_ORIGIN } }), w.deps);
    expect(ok.status).toBe(204);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ok.headers.get('access-control-allow-headers')).toContain('authorization');
    const bad = await handleAdminAccount(new Request(FUNCTION_URL, { method: 'OPTIONS', headers: { origin: 'https://evil.example' } }), w.deps);
    expect(bad.status).toBe(404);
    expect(bad.headers.get('access-control-allow-origin')).toBeNull();
    expect(w.calls).toEqual([]);
  });

  it('says 503, not 404, when the admin check cannot be made, and does nothing', async () => {
    for (const fault of ['admin-down', 'admin-5xx'] as const) {
      const w = world(fault);
      const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
      expect(response.status, fault).toBe(503);
      expect((await answer(response)).error?.code).toBe('service_unavailable');
      expect(w.deleted).toEqual([]);
      expect(w.log).toEqual([]);
    }
  });

  it('answers a bare 404 to a revoked or expired session, and looks up, logs and deletes nothing', async () => {
    for (const action of ['end_alerts', 'delete_account'] as const) {
      const w = world('session-revoked');
      const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action }), w.deps);
      expect(response.status).toBe(404);
      expect(await response.text()).toBe('');
      expect(w.calls.map((c) => c.url.replace(config.supabaseUrl, ''))).toEqual(['/rest/v1/rpc/is_admin', '/auth/v1/user']);
      expect(w.log).toEqual([]);
      expect(w.unsubscribed).toEqual([]);
      expect(w.deleted).toEqual([]);
    }
  });

  it('says 503 when the session check cannot be made, and does nothing', async () => {
    for (const fault of ['session-down', 'session-5xx'] as const) {
      const w = world(fault);
      const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
      expect(response.status, fault).toBe(503);
      expect(w.calls.map((c) => c.url.replace(config.supabaseUrl, ''))).toEqual(['/rest/v1/rpc/is_admin', '/auth/v1/user']);
      expect(w.deleted).toEqual([]);
    }
  });

  it('does not ask Auth for a caller who is not an admin', async () => {
    const w = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }, USER_TOKEN), w.deps);
    expect(w.calls.map((c) => c.url)).toEqual([`${config.supabaseUrl}/rest/v1/rpc/is_admin`]);
  });

  it('treats an answer of the check that is not `true` as not an admin', async () => {
    const w = world('admin-garbage');
    expect((await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps)).status).toBe(404);
    expect(w.deleted).toEqual([]);
  });

  it('answers a request with no token 404 even when the secrets are missing', async () => {
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }, null), { config: undefined, fetch: world().deps.fetch, now: () => NOW });
    expect(response.status).toBe(404);
    expect(await response.text()).toBe('');
  });

  it('logs the status when the check refuses the request, so a key the gateway does not accept shows in the logs', async () => {
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }, 'forged.token.value'), world().deps);
    expect(everythingLogged()).toContain('the admin check refused the request with 401');
  });

  it('answers 500 when the secrets are missing, and names no secret in the answer', async () => {
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), { config: undefined, fetch: world().deps.fetch, now: () => NOW });
    expect(response.status).toBe(500);
    expect((await answer(response)).error?.code).toBe('server_error');
  });
});

describe('the two answers for the admin', () => {
  it('says "Done." when it ended the alerts of the account that uses the address', async () => {
    const w = world();
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), w.deps);
    expect(response.status).toBe(200);
    expect(await answer(response)).toEqual({ ok: true, result: 'done', message: 'Done.' });
    expect(w.unsubscribed).toEqual([ANN_ID]);
    expect(w.deleted).toEqual([]);
    const call = w.calls.find((c) => c.url.endsWith('/rpc/unsubscribe_user'));
    expect(JSON.parse(call?.body ?? '{}')).toEqual({ p_user_id: ANN_ID, p_now: NOW.toISOString() });
  });

  it('says "Done." when it deleted the account, through the Admin API, as a hard delete', async () => {
    const w = world();
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
    expect(response.status).toBe(200);
    expect(await answer(response)).toEqual({ ok: true, result: 'done', message: 'Done.' });
    expect(w.deleted).toEqual([ANN_ID]);
    expect(w.unsubscribed).toEqual([]);
    const call = w.calls.find((c) => c.method === 'DELETE');
    expect(call?.url).toBe(`${config.supabaseUrl}/auth/v1/admin/users/${ANN_ID}`);
    expect(JSON.parse(call?.body ?? '{}')).toEqual({ should_soft_delete: false });
  });

  it('says "No account uses that address." when none matches, and writes and changes nothing', async () => {
    for (const action of ['end_alerts', 'delete_account'] as const) {
      const w = world();
      const response = await handleAdminAccount(ask({ address: 'nobody@example.test', action }), w.deps);
      expect(response.status).toBe(200);
      expect(await answer(response)).toEqual({ ok: true, result: 'no_account', message: 'No account uses that address.' });
      expect(w.log).toEqual([]);
      expect(w.unsubscribed).toEqual([]);
      expect(w.deleted).toEqual([]);
    }
  });

  it('finds an account by its digest address to end alerts, but never to delete it', async () => {
    const w = world();
    expect((await answer(await handleAdminAccount(ask({ address: BOB_DIGEST, action: 'end_alerts' }), w.deps))).result).toBe('done');
    expect(w.unsubscribed).toEqual([BOB_ID]);
    const w2 = world();
    expect((await answer(await handleAdminAccount(ask({ address: BOB_DIGEST, action: 'delete_account' }), w2.deps))).result).toBe('no_account');
    expect(w2.deleted).toEqual([]);
    const lookup = w2.calls.find((c) => c.url.endsWith('/rpc/admin_account_ids'));
    expect(JSON.parse(lookup?.body ?? '{}')).toEqual({ p_address: BOB_DIGEST, p_login_only: true });
    expect((await answer(await handleAdminAccount(ask({ address: BOB_LOGIN, action: 'delete_account' }), w2.deps))).result).toBe('done');
    expect(w2.deleted).toEqual([BOB_ID]);
  });

  it('ends the alerts of every account that matches, but refuses to delete more than one account', async () => {
    const w = world();
    expect((await answer(await handleAdminAccount(ask({ address: TWIN, action: 'end_alerts' }), w.deps))).result).toBe('done');
    expect(w.unsubscribed).toEqual([ANN_ID, BOB_ID]);
    const d = world();
    const response = await handleAdminAccount(ask({ address: TWIN, action: 'delete_account' }), d.deps);
    expect(response.status).toBe(409);
    expect((await answer(response)).error?.code).toBe('ambiguous');
    expect(d.deleted).toEqual([]);
    expect(d.log).toEqual([]);
  });

  it('makes exactly these calls, in this order, and no others (no email is sent)', async () => {
    const steps = (w: World): string[] => w.calls.map((c) => `${c.method} ${c.url.replace(config.supabaseUrl, '')}`);
    const a = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), a.deps);
    expect(steps(a)).toEqual(['POST /rest/v1/rpc/is_admin', 'GET /auth/v1/user', 'POST /rest/v1/rpc/admin_account_ids', 'POST /rest/v1/admin_log', 'POST /rest/v1/rpc/unsubscribe_user']);
    const d = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), d.deps);
    expect(steps(d)).toEqual(['POST /rest/v1/rpc/is_admin', 'GET /auth/v1/user', 'POST /rest/v1/rpc/admin_account_ids', 'POST /rest/v1/admin_log', `DELETE /auth/v1/admin/users/${ANN_ID}`]);
  });

  it('trims the address it is given', async () => {
    const w = world();
    expect((await answer(await handleAdminAccount(ask({ address: `  ${ANN_ADDRESS} `, action: 'end_alerts' }), w.deps))).result).toBe('done');
  });
});

describe('the log holds the action and the time, and nothing about the person', () => {
  it.each([
    ['end_alerts', 'alerts ended'],
    ['delete_account', 'account deleted'],
  ] as const)('writes one row for %s', async (action, text) => {
    const w = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action }), w.deps);
    expect(w.log).toEqual([{ action: text }]); // the time is the column default; there is no target, hash or id
    const write = w.calls.find((c) => c.url.endsWith('/rest/v1/admin_log'));
    expect(write?.body).not.toContain(ANN_ADDRESS);
    expect(write?.body).not.toContain(ANN_ID);
    expect(write?.headers.authorization).toBe(`Bearer ${SERVICE_KEY}`);
  });

  it('writes the log before it acts, so a failed log means nothing was done', async () => {
    const w = world('log-fail');
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
    expect(response.status).toBe(500);
    expect(w.deleted).toEqual([]);
    expect(w.calls.some((c) => c.method === 'DELETE')).toBe(false); // the log failed first, so the Admin API was never called
  });

  it('sends the address only to the database lookup (a request body, never a URL), and nowhere else', async () => {
    const w = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
    for (const call of w.calls) {
      expect(call.url, call.url).not.toContain('ann.private');
      expect(call.url, call.url).not.toContain(encodeURIComponent(ANN_ADDRESS));
      if (!call.url.endsWith('/rpc/admin_account_ids')) expect(call.body, call.url).not.toContain(ANN_ADDRESS);
    }
  });
});

describe('what goes wrong', () => {
  it('answers 400 to a body that is not an address and one known action, before it looks anything up', async () => {
    const bad: unknown[] = [
      '',
      'not json',
      '[]',
      {},
      { address: ANN_ADDRESS },
      { action: 'end_alerts' },
      { address: ANN_ADDRESS, action: 'list_accounts' },
      { address: 'not-an-address', action: 'end_alerts' },
      { address: 'a@b', action: 'end_alerts' },
      { address: 'two@example.test, three@example.test', action: 'end_alerts' },
      { address: ['ann@example.test'], action: 'end_alerts' },
      { address: `${'a'.repeat(250)}@example.test`, action: 'end_alerts' },
      { address: ANN_ADDRESS, action: 'end_alerts', padding: 'x'.repeat(3000) },
    ];
    for (const body of bad) {
      const w = world();
      const response = await handleAdminAccount(ask(body), w.deps);
      expect(response.status, JSON.stringify(body).slice(0, 60)).toBe(400);
      expect((await answer(response)).error?.code).toBe('bad_request');
      expect(w.calls.map((c) => c.url.replace(config.supabaseUrl, ''))).toEqual(['/rest/v1/rpc/is_admin', '/auth/v1/user']);
    }
  });

  it.each(['ids-fail', 'ids-garbage', 'unsubscribe-fail'] as const)('answers 500 when the database fails (%s)', async (fault) => {
    const w = world(fault);
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), w.deps);
    expect(response.status).toBe(500);
    expect((await answer(response)).error?.code).toBe('server_error');
    expect(w.deleted).toEqual([]);
  });

  it.each([
    ['delete-down', 'delete_unconfirmed'],
    ['delete-5xx', 'delete_unconfirmed'],
    ['delete-4xx', 'delete_failed'],
  ] as const)('answers 502 %s as %s and never says "Done."', async (fault, code) => {
    const w = world(fault);
    const response = await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'delete_account' }), w.deps);
    expect(response.status).toBe(502);
    const body = await answer(response);
    expect(body.error?.code).toBe(code);
    expect(body.message).toBeUndefined();
  });

  it('never logs the address, a token, a key or a user id, whatever fails', async () => {
    const faults: Fault[] = ['none', 'admin-down', 'admin-5xx', 'session-revoked', 'session-down', 'ids-fail', 'ids-garbage', 'log-fail', 'unsubscribe-fail', 'delete-down', 'delete-5xx', 'delete-4xx'];
    for (const fault of faults) {
      for (const action of ['end_alerts', 'delete_account'] as const) {
        await handleAdminAccount(ask({ address: ANN_ADDRESS, action }), world(fault).deps);
        await handleAdminAccount(ask({ address: ANN_ADDRESS, action }, USER_TOKEN), world(fault).deps);
      }
    }
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'bad' }), world().deps);
    const logged = everythingLogged();
    expect(logged.length).toBeGreaterThan(0); // the failures were logged, as status codes
    for (const secret of [ANN_ADDRESS, 'ann.private', ANN_ID, ADMIN_TOKEN, USER_TOKEN, SERVICE_KEY, ANON_KEY]) {
      expect(logged).not.toContain(secret);
    }
  });

  it('does not log at all on the paths that go well', async () => {
    const w = world();
    await handleAdminAccount(ask({ address: ANN_ADDRESS, action: 'end_alerts' }), w.deps);
    await handleAdminAccount(ask({ address: BOB_LOGIN, action: 'delete_account' }), w.deps);
    await handleAdminAccount(ask({ address: 'nobody@example.test', action: 'end_alerts' }), w.deps);
    expect(everythingLogged()).toBe('');
  });

  it('never returns a row, an id or the address in any answer', async () => {
    const answers: string[] = [];
    for (const action of ['end_alerts', 'delete_account'] as const) {
      for (const address of [ANN_ADDRESS, 'nobody@example.test']) {
        answers.push(await (await handleAdminAccount(ask({ address, action }), world().deps)).text());
      }
    }
    for (const text of answers) {
      expect(text).not.toContain(ANN_ID);
      expect(text).not.toContain('ann.private');
    }
  });
});

describe('loadConfig', () => {
  const all: Record<string, string> = { SUPABASE_URL: 'https://p.supabase.test/', SUPABASE_SERVICE_ROLE_KEY: 'k', SUPABASE_ANON_KEY: 'a' };

  it('reads the three secrets', () => {
    expect(loadConfig((name) => all[name])).toEqual({ config: { supabaseUrl: 'https://p.supabase.test', serviceRoleKey: 'k', anonKey: 'a' } });
  });

  it('names what is missing, never a value', () => {
    expect(loadConfig(() => undefined)).toEqual({ missing: ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_ANON_KEY'] });
    expect(loadConfig((name) => (name === 'SUPABASE_ANON_KEY' ? undefined : all[name]))).toEqual({ missing: ['SUPABASE_ANON_KEY'] });
  });
});

describe('the shared deletion (also used by delete-account)', () => {
  it('refuses an id that is not a UUID before it makes a request', async () => {
    const w = world();
    expect(await deleteAuthUser(w.deps, config, '../admin', 'test')).toBe('failed');
    expect(w.calls).toEqual([]);
  });

  it('counts a 404 (already gone) as deleted, and tells a 4xx from a 5xx', async () => {
    const answer = (status: number): Deps['fetch'] => () => Promise.resolve(new Response(null, { status }));
    expect(await deleteAuthUser({ fetch: answer(404) }, config, ANN_ID, 'test')).toBe('deleted');
    expect(await deleteAuthUser({ fetch: answer(200) }, config, ANN_ID, 'test')).toBe('deleted');
    expect(await deleteAuthUser({ fetch: answer(403) }, config, ANN_ID, 'test')).toBe('failed');
    expect(await deleteAuthUser({ fetch: answer(503) }, config, ANN_ID, 'test')).toBe('unconfirmed');
  });
});
