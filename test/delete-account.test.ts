import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import { ALLOWED_ORIGIN, bearerToken, handleDelete, loadConfig } from '../supabase/functions/delete-account/handler.ts';
import type { Config, Deps, Fetch } from '../supabase/functions/delete-account/handler.ts';

const config: Config = { supabaseUrl: 'https://project.supabase.test', serviceRoleKey: 'service-role-test-key' };
const ANN = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', email: 'ann@example.test', role: 'authenticated' };
const BOB_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const ANN_TOKEN = 'ann.token.value';
const BOB_TOKEN = 'bob.token.value';

type AuthMode = 'ok' | 'user-refused' | 'user-down' | 'user-5xx' | 'user-garbage' | 'user-no-role' | 'user-bad-id';
type DeleteMode = 'ok' | 'gone' | 'refused' | 'down' | 'server-error';

interface World {
  deps: Deps;
  users: Map<string, { id: string; email: string; role: string }>;
  deleted: string[];
  calls: { url: string; method: string; headers: Record<string, string>; body: string }[];
  fakeFetch: Mock<Fetch>;
}

/** A fake of Supabase Auth: `/user` answers for a token, the admin endpoint removes a user. */
function fakeWorld(options: { auth?: AuthMode; delete?: DeleteMode } = {}): World {
  const users = new Map([
    [ANN.id, ANN],
    [BOB_ID, { id: BOB_ID, email: 'bob@example.test', role: 'authenticated' }],
  ]);
  const tokens = new Map([
    [ANN_TOKEN, ANN.id],
    [BOB_TOKEN, BOB_ID],
  ]);
  const deleted: string[] = [];
  const calls: World['calls'] = [];
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const headers = init.headers as Record<string, string>;
    calls.push({ url, method: String(init.method), headers, body: typeof init.body === 'string' ? init.body : '' });
    if (url === `${config.supabaseUrl}/auth/v1/user`) {
      switch (options.auth) {
        case 'user-down':
          return Promise.reject(new Error('connection refused'));
        case 'user-5xx':
          return Promise.resolve(new Response('no', { status: 502 }));
        case 'user-refused':
          return Promise.resolve(new Response('{"error_code":"bad_jwt"}', { status: 401 }));
        case 'user-garbage':
          return Promise.resolve(new Response('not json', { status: 200 }));
        case 'user-no-role':
          return Promise.resolve(Response.json({ id: ANN.id, role: 'service_role' }));
        case 'user-bad-id':
          return Promise.resolve(Response.json({ id: '../admin', role: 'authenticated' }));
        default: {
          const id = tokens.get((headers.authorization ?? '').replace('Bearer ', ''));
          const user = id ? users.get(id) : undefined;
          return Promise.resolve(user ? Response.json(user) : new Response('{}', { status: 403 }));
        }
      }
    }
    const prefix = `${config.supabaseUrl}/auth/v1/admin/users/`;
    if (url.startsWith(prefix) && init.method === 'DELETE') {
      switch (options.delete) {
        case 'down':
          return Promise.reject(new Error('timeout'));
        case 'refused':
          return Promise.resolve(new Response('{}', { status: 422 }));
        case 'server-error':
          return Promise.resolve(new Response('{}', { status: 500 }));
        case 'gone':
          return Promise.resolve(new Response('{}', { status: 404 }));
        default: {
          const id = url.slice(prefix.length);
          deleted.push(id);
          users.delete(id);
          return Promise.resolve(Response.json({}));
        }
      }
    }
    return Promise.resolve(new Response('unexpected', { status: 404 }));
  });
  return { deps: { config, fetch: fakeFetch }, users, deleted, calls, fakeFetch };
}

function post(token: string | null, init: RequestInit = {}, origin: string | null = ALLOWED_ORIGIN): Request {
  const headers: Record<string, string> = {};
  if (origin) headers.origin = origin;
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request('https://project.supabase.test/functions/v1/delete-account', { method: 'POST', headers, ...init });
}

async function json(response: Response): Promise<{ ok: boolean; error?: { code: string; message: string } }> {
  return (await response.json()) as { ok: boolean; error?: { code: string; message: string } };
}

afterEach(() => {
  vi.restoreAllMocks();
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

describe('bearerToken', () => {
  it('accepts a bearer token and nothing else', () => {
    expect(bearerToken(new Headers({ authorization: 'Bearer abc.def-g_h' }))).toBe('abc.def-g_h');
    expect(bearerToken(new Headers())).toBeUndefined();
    expect(bearerToken(new Headers({ authorization: 'Basic abc' }))).toBeUndefined();
    expect(bearerToken(new Headers({ authorization: 'Bearer a b' }))).toBeUndefined();
    expect(bearerToken(new Headers({ authorization: `Bearer ${'a'.repeat(5000)}` }))).toBeUndefined();
  });
});

describe('the request', () => {
  it('answers a preflight from the dashboard only, with CORS for that origin only', async () => {
    const ok = await handleDelete(new Request('https://x.test/', { method: 'OPTIONS', headers: { origin: ALLOWED_ORIGIN } }), fakeWorld().deps);
    expect(ok.status).toBe(204);
    expect(ok.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(ok.headers.get('access-control-allow-methods')).toBe('POST, OPTIONS');
    expect(ok.headers.get('access-control-allow-headers')).toContain('authorization');
    const bad = await handleDelete(post(ANN_TOKEN, {}, 'https://evil.test'), fakeWorld().deps);
    expect(bad.status).toBe(403);
    expect(bad.headers.get('access-control-allow-origin')).toBeNull();
    expect((await json(bad)).error?.code).toBe('origin_not_allowed');
  });

  it('allows a caller without an Origin header (curl) but gives it no CORS header', async () => {
    const world = fakeWorld();
    const response = await handleDelete(post(ANN_TOKEN, {}, null), world.deps);
    expect(response.status).toBe(200);
    expect(response.headers.get('access-control-allow-origin')).toBeNull();
  });

  it.each(['GET', 'PUT', 'DELETE', 'PATCH'])('refuses %s', async (method) => {
    const world = fakeWorld();
    const response = await handleDelete(new Request('https://x.test/', { method, headers: { origin: ALLOWED_ORIGIN, authorization: `Bearer ${ANN_TOKEN}` } }), world.deps);
    expect(response.status).toBe(405);
    expect(response.headers.get('allow')).toBe('POST, OPTIONS');
    expect((await json(response)).error?.code).toBe('method_not_allowed');
    expect(world.fakeFetch).not.toHaveBeenCalled();
  });

  it('refuses a request with no usable token before asking anyone', async () => {
    for (const request of [post(null), new Request('https://x.test/', { method: 'POST', headers: { authorization: 'Bearer a b' } })]) {
      const world = fakeWorld();
      const response = await handleDelete(request, world.deps);
      expect(response.status).toBe(401);
      expect((await json(response)).error?.code).toBe('not_signed_in');
      expect(world.fakeFetch).not.toHaveBeenCalled();
    }
  });

  it('says so when the secrets are missing', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const response = await handleDelete(post(ANN_TOKEN), { config: undefined, fetch: vi.fn<Fetch>() });
    expect(response.status).toBe(500);
    expect((await json(response)).error?.code).toBe('server_error');
    expect(spy).toHaveBeenCalled();
  });
});

describe('deleting', () => {
  it('removes the owner of the token with a hard delete, and answers with no data', async () => {
    const world = fakeWorld();
    const response = await handleDelete(post(ANN_TOKEN), world.deps);
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ ok: true });
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(world.deleted).toEqual([ANN.id]);
    const [check, removal] = world.calls;
    expect(check).toMatchObject({ method: 'GET', url: `${config.supabaseUrl}/auth/v1/user` });
    expect(check?.headers.authorization).toBe(`Bearer ${ANN_TOKEN}`);
    expect(removal).toMatchObject({ method: 'DELETE', url: `${config.supabaseUrl}/auth/v1/admin/users/${ANN.id}` });
    expect(removal?.headers.authorization).toBe(`Bearer ${config.serviceRoleKey}`);
    expect(JSON.parse(removal?.body ?? '{}')).toEqual({ should_soft_delete: false });
  });

  it('can only delete the caller: a body, a query string or headers naming another user change nothing', async () => {
    const world = fakeWorld();
    const request = new Request(`https://x.test/functions/v1/delete-account?id=${BOB_ID}&user_id=${BOB_ID}`, {
      method: 'POST',
      headers: { origin: ALLOWED_ORIGIN, authorization: `Bearer ${ANN_TOKEN}`, 'x-user-id': BOB_ID },
      body: JSON.stringify({ id: BOB_ID, user_id: BOB_ID, userId: BOB_ID }),
    });
    expect((await handleDelete(request, world.deps)).status).toBe(200);
    expect(world.deleted).toEqual([ANN.id]);
    expect(world.users.has(BOB_ID)).toBe(true);
  });

  it('answers a replay after deletion with a clean 401 and calls no admin endpoint', async () => {
    const world = fakeWorld();
    expect((await handleDelete(post(ANN_TOKEN), world.deps)).status).toBe(200);
    world.calls.length = 0;
    const again = await handleDelete(post(ANN_TOKEN), world.deps);
    expect(again.status).toBe(401);
    expect((await json(again)).error?.code).toBe('not_signed_in');
    expect(world.calls.map((c) => c.method)).toEqual(['GET']);
    expect(world.deleted).toEqual([ANN.id]);
    expect(world.users.has(BOB_ID)).toBe(true);
  });

  it.each(['user-refused', 'user-garbage', 'user-no-role', 'user-bad-id'] as const)(
    'never calls the admin endpoint when the token check gives %s',
    async (auth) => {
      const world = fakeWorld({ auth });
      const response = await handleDelete(post(ANN_TOKEN), world.deps);
      expect(response.status).toBe(401);
      expect((await json(response)).error?.code).toBe('not_signed_in');
      expect(world.calls.every((c) => c.method === 'GET')).toBe(true);
      expect(world.deleted).toEqual([]);
    },
  );

  it.each(['user-down', 'user-5xx'] as const)('says the account was not deleted when the token check is unreachable (%s)', async (auth) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const world = fakeWorld({ auth });
    const response = await handleDelete(post(ANN_TOKEN), world.deps);
    expect(response.status).toBe(503);
    const body = await json(response);
    expect(body.error?.code).toBe('service_unavailable');
    expect(body.error?.message).toContain('was not deleted');
    expect(world.deleted).toEqual([]);
  });

  it('counts a user who is already gone (404) as done', async () => {
    const response = await handleDelete(post(ANN_TOKEN), fakeWorld({ delete: 'gone' }).deps);
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ ok: true });
  });

  it('reports a refusal as a definite failure', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const response = await handleDelete(post(ANN_TOKEN), fakeWorld({ delete: 'refused' }).deps);
    expect(response.status).toBe(502);
    expect((await json(response)).error?.code).toBe('delete_failed');
  });

  it.each(['down', 'server-error'] as const)('reports an unknown outcome as unconfirmed (%s)', async (mode) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const response = await handleDelete(post(ANN_TOKEN), fakeWorld({ delete: mode }).deps);
    expect(response.status).toBe(502);
    expect((await json(response)).error?.code).toBe('delete_unconfirmed');
  });

  it('treats a throw while deleting as an unknown outcome', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const throwing: Deps = {
      config,
      fetch: (url) =>
        url.endsWith('/auth/v1/user')
          ? Promise.resolve(Response.json({ id: ANN.id, role: 'authenticated' }))
          : (() => {
              throw new Error('boom');
            })(),
    };
    const response = await handleDelete(post(ANN_TOKEN), throwing);
    expect((await json(response)).error?.code).toBe('delete_unconfirmed');
  });

  it('logs no token, email or user id, on any path', async () => {
    const lines: string[] = [];
    for (const name of ['error', 'warn', 'log', 'info'] as const) {
      vi.spyOn(console, name).mockImplementation((...args: unknown[]) => {
        lines.push(args.map(String).join(' '));
      });
    }
    for (const options of [{}, { auth: 'user-refused' }, { auth: 'user-down' }, { delete: 'refused' }, { delete: 'down' }, { delete: 'server-error' }] as const) {
      await handleDelete(post(ANN_TOKEN), fakeWorld(options).deps);
    }
    await handleDelete(post(ANN_TOKEN), { config: undefined, fetch: vi.fn<Fetch>() });
    const text = lines.join('\n');
    for (const secret of [ANN_TOKEN, ANN.email, ANN.id, config.serviceRoleKey]) expect(text).not.toContain(secret);
  });
});
