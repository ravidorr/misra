import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import {
  DETAIL_KEYS,
  JOB_DAILY_CAP,
  JOB_DAILY_LIMIT,
  MAX_BODY_BYTES,
  PROTOCOL_VERSIONS,
  SEARCH_DAILY_LIMIT,
  candidateDetails,
  handleMcp,
  loadConfig,
} from '../supabase/functions/mcp/handler.ts';
import type { Config, Deps } from '../supabase/functions/mcp/handler.ts';
import { TOOLS, parseJob, parseSearch } from '../supabase/functions/mcp/tools.ts';
import type { Fetch } from '../supabase/functions/_shared/subscription.ts';

const SECRET = 'a-secret-of-at-least-16-chars';
const config: Config = { supabaseUrl: 'https://project.supabase.test', serviceRoleKey: 'service-role-test-key', ipHashSecret: SECRET };
const NOW = new Date('2026-11-17T10:00:00Z');

/** A job as the table holds it, with the full text. */
const JOB = {
  company: 'Acme',
  job_id: 'j1',
  title: 'Junior Rust Developer',
  team: 'Platform',
  location: 'Tel Aviv',
  url: 'https://acme.example/jobs/j1',
  posted: '2026-11-01',
  first_seen: '2026-11-10',
  last_seen: '2026-11-16',
  status: 'open',
  level: 'Junior',
  role_types: ['R&D'],
  regions: ['Tel Aviv area'],
  locations: [],
  snippet: 'We build rockets',
  description: 'We build rockets with Rust. The full posting, a long text.',
  details: { employment_type: 'Full time', departments: ['R&D'], is_remote: false, text_sha256: 'abc', requisition_id: 'R-77', recruiter: 'x@example.com', raw: '<script>x()</script>', offices: [1] },
};
const SAFE_DETAILS = { employment_type: 'Full time', departments: ['R&D'], is_remote: false };
const PUBLIC = { company: 'Acme', job_id: 'j1', title: 'Junior Rust Developer', team: null, location: 'Tel Aviv', url: null, first_seen: '2026-11-10', level: 'Junior', role_types: ['R&D'], regions: ['Tel Aviv area'], locations: [], snippet: 'We build rockets' };
// A database function that wrongly returned the text: the handler must still not pass it on.
const FOUND = { total: 1, jobs: [{ ...PUBLIC, description: 'THE FULL TEXT', details: { x: 1 } }, 'junk'] };

interface World {
  deps: Deps;
  calls: { url: string; headers: Record<string, string>; body: Record<string, unknown> | undefined }[];
  counters: Map<string, number>;
  fakeFetch: Mock<Fetch>;
}

/** A fake of the database: the counter function (same rules as the migration), the search and the jobs table. */
function fakeWorld(options: { database?: 'down' | 'garbage' | 'search-garbage' | 'jobs-error' | 'release-down'; jobs?: unknown[]; enabled?: boolean } = {}): World {
  const counters = new Map<string, number>();
  const calls: World['calls'] = [];
  const fakeFetch = vi.fn<Fetch>((url, init) => {
    const body = typeof init.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : undefined;
    calls.push({ url, headers: init.headers as Record<string, string>, body });
    if (options.database === 'down') return Promise.reject(new Error('connection refused'));
    if (url.endsWith('/rpc/assistant_reserve')) {
      if (options.database === 'garbage') return Promise.resolve(Response.json('surprise'));
      const key = `${String(body?.p_day)}|${String(body?.p_ip_hash)}`;
      const used = counters.get(key) ?? 0;
      if (used >= Number(body?.p_limit)) return Promise.resolve(Response.json('limit'));
      counters.set(key, used + 1);
      return Promise.resolve(Response.json('ok'));
    }
    if (url.endsWith('/rpc/assistant_release')) {
      if (options.database === 'release-down') return Promise.reject(new Error('down'));
      const key = `${String(body?.p_day)}|${String(body?.p_ip_hash)}`;
      counters.set(key, Math.max((counters.get(key) ?? 0) - 1, 0));
      return Promise.resolve(Response.json('ok'));
    }
    if (url.endsWith('/rpc/search_jobs')) {
      return Promise.resolve(options.database === 'search-garbage' ? Response.json({ nope: true }) : Response.json(FOUND));
    }
    if (url.includes('/rest/v1/jobs?')) {
      return Promise.resolve(options.database === 'jobs-error' ? new Response('no', { status: 500 }) : Response.json(options.jobs ?? [JOB]));
    }
    return Promise.resolve(new Response('unexpected', { status: 404 }));
  });
  return {
    deps: { enabled: options.enabled ?? true, config, fetch: fakeFetch, now: () => NOW },
    calls,
    counters,
    fakeFetch,
  };
}

function post(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request('https://project.supabase.test/functions/v1/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

const rpc = (method: string, params?: unknown, id: number | string = 1): Record<string, unknown> => ({
  jsonrpc: '2.0',
  id,
  method,
  ...(params === undefined ? {} : { params }),
});

interface Answer {
  status: number;
  json: Record<string, unknown>;
  headers: Headers;
}
async function ask(world: World, body: unknown, headers: Record<string, string> = {}): Promise<Answer> {
  const response = await handleMcp(post(body, headers), world.deps);
  return { status: response.status, json: (await response.json()) as Record<string, unknown>, headers: response.headers };
}

const call = (name: string, args: unknown): Record<string, unknown> => rpc('tools/call', { name, arguments: args });
interface ToolAnswer {
  isError: boolean;
  text: string;
  data: Record<string, unknown> | undefined;
}
async function tool(world: World, name: string, args: unknown, headers: Record<string, string> = {}): Promise<ToolAnswer> {
  const { status, json } = await ask(world, call(name, args), headers);
  expect(status).toBe(200);
  const result = json.result as { content: { type: string; text: string }[]; isError?: boolean };
  expect(result.content).toHaveLength(1);
  const text = result.content[0]?.text ?? '';
  let data: Record<string, unknown> | undefined;
  try {
    data = JSON.parse(text) as Record<string, unknown>;
  } catch {
    data = undefined;
  }
  return { isError: result.isError === true, text, data };
}

const silence = (): void => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
};

describe('the switch', () => {
  it('answers 404 to everything, with no sign of an endpoint, unless the secret is "true"', async () => {
    const world = fakeWorld({ enabled: false });
    for (const request of [
      post(rpc('initialize', { protocolVersion: '2025-06-18' })),
      new Request('https://project.supabase.test/functions/v1/mcp', { method: 'GET' }),
      new Request('https://project.supabase.test/functions/v1/mcp', { method: 'OPTIONS', headers: { origin: 'https://x.example' } }),
      post('not json'),
    ]) {
      const response = await handleMcp(request, world.deps);
      expect(response.status).toBe(404);
      expect(response.headers.get('access-control-allow-origin')).toBeNull();
      expect(await response.text()).toBe('{"error":"not_found"}');
    }
    expect(world.fakeFetch).not.toHaveBeenCalled();
  });

  it('is on only when ASSISTANT_ENDPOINT_ENABLED is exactly "true"', () => {
    const env = (extra: Record<string, string>) => (name: string): string | undefined =>
      ({ SUPABASE_URL: 'https://p.supabase.test/', SUPABASE_SERVICE_ROLE_KEY: 'k', IP_HASH_SECRET: SECRET, ...extra })[name];
    expect(loadConfig(env({ ASSISTANT_ENDPOINT_ENABLED: 'true' })).enabled).toBe(true);
    expect(loadConfig(env({ ASSISTANT_ENDPOINT_ENABLED: ' true ' })).enabled).toBe(true);
    for (const value of ['', 'TRUE', '1', 'yes', 'false', 'on']) {
      expect(loadConfig(env({ ASSISTANT_ENDPOINT_ENABLED: value })).enabled, value).toBe(false);
    }
    expect(loadConfig(env({})).enabled).toBe(false);
  });

  it('names the secrets that are missing or malformed, never a value, and strips the slash of the URL', () => {
    const none = loadConfig(() => undefined);
    expect(none).toEqual({ enabled: false, missing: ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'IP_HASH_SECRET'] });
    const short = loadConfig((name) => ({ SUPABASE_URL: 'ftp://x', SUPABASE_SERVICE_ROLE_KEY: 'k', IP_HASH_SECRET: 'short' })[name]);
    expect(short).toMatchObject({ missing: ['SUPABASE_URL', 'IP_HASH_SECRET'] });
    const good = loadConfig((name) => ({ SUPABASE_URL: 'https://p.supabase.test///', SUPABASE_SERVICE_ROLE_KEY: 'k', IP_HASH_SECRET: SECRET })[name]);
    expect(good).toEqual({ enabled: false, config: { supabaseUrl: 'https://p.supabase.test', serviceRoleKey: 'k', ipHashSecret: SECRET } });
  });
});

describe('the protocol (MCP over HTTP, JSON-RPC)', () => {
  it('initialize: gives the version the client asked for when it is one we speak, else the newest, with the tools capability', async () => {
    const world = fakeWorld();
    for (const version of PROTOCOL_VERSIONS) {
      const { status, json } = await ask(world, rpc('initialize', { protocolVersion: version, capabilities: {}, clientInfo: { name: 't', version: '1' } }));
      expect(status).toBe(200);
      expect(json).toMatchObject({
        jsonrpc: '2.0',
        id: 1,
        result: { protocolVersion: version, capabilities: { tools: {} }, serverInfo: { name: 'misra-jobs' } },
      });
    }
    const newer = await ask(world, rpc('initialize', { protocolVersion: '2099-01-01' }));
    expect((newer.json.result as { protocolVersion: string }).protocolVersion).toBe(PROTOCOL_VERSIONS[0]);
    const bare = await ask(world, rpc('initialize'));
    expect((bare.json.result as { protocolVersion: string }).protocolVersion).toBe(PROTOCOL_VERSIONS[0]);
    expect((newer.json.result as { instructions: string }).instructions).toContain('get_job');
    expect(world.fakeFetch).not.toHaveBeenCalled(); // the handshake never touches the database
  });

  it('answers a notification and a client response with 202 and no body, and ping with an empty result', async () => {
    const world = fakeWorld();
    for (const message of [
      { jsonrpc: '2.0', method: 'notifications/initialized' },
      { jsonrpc: '2.0', id: 4, result: {} },
    ]) {
      const response = await handleMcp(post(message), world.deps);
      expect(response.status).toBe(202);
      expect(await response.text()).toBe('');
    }
    expect((await ask(world, rpc('ping', undefined, 'abc'))).json).toEqual({ jsonrpc: '2.0', id: 'abc', result: {} });
  });

  it('tools/list: two read-only tools with schemas, and no tool that writes, sends or reads anyone\'s data', async () => {
    const { json } = await ask(fakeWorld(), rpc('tools/list'));
    const tools = (json.result as { tools: { name: string; description: string; inputSchema: { properties: Record<string, unknown>; required?: string[] }; annotations: Record<string, boolean> }[] }).tools;
    expect(tools.map((t) => t.name)).toEqual(['search_jobs', 'get_job']);
    for (const t of tools) {
      expect(t.annotations).toMatchObject({ readOnlyHint: true, destructiveHint: false, openWorldHint: false });
      expect(t.description.length).toBeGreaterThan(40);
    }
    expect(Object.keys(tools[0]?.inputSchema.properties ?? {})).toEqual(['query', 'levels', 'roleTypes', 'regions', 'limit', 'offset']);
    expect(tools[1]?.inputSchema.required).toEqual(['company', 'job_id']);
    expect(JSON.stringify(tools)).not.toMatch(/\bstars?\b|starred|discard|prefer|email|subscribe/iu);
  });

  it('refuses what it cannot read with the right JSON-RPC error', async () => {
    silence();
    const world = fakeWorld();
    expect(await ask(world, rpc('resources/list'))).toMatchObject({ status: 200, json: { id: 1, error: { code: -32601 } } });
    expect(await ask(world, call('delete_everything', {}))).toMatchObject({ status: 200, json: { error: { code: -32602 } } });
    expect(await ask(world, rpc('tools/call', { arguments: {} }))).toMatchObject({ json: { error: { code: -32602 } } });
    expect(await ask(world, rpc('tools/call'))).toMatchObject({ json: { error: { code: -32602 } } });
    expect(await ask(world, '{"jsonrpc":')).toMatchObject({ status: 400, json: { id: null, error: { code: -32700 } } });
    for (const bad of ['[]', '"x"', '{"jsonrpc":"1.0","id":1,"method":"ping"}', '[{"jsonrpc":"2.0","id":1,"method":"ping"}]']) {
      expect(await ask(world, bad), bad).toMatchObject({ status: 400, json: { error: { code: -32600 } } });
    }
    expect(await ask(world, { jsonrpc: '2.0', id: null, method: 'ping' })).toMatchObject({ status: 400 });
    expect(await ask(world, { jsonrpc: '2.0', id: {}, method: 'ping' })).toMatchObject({ status: 400 });
    expect(world.fakeFetch).not.toHaveBeenCalled();
  });

  it('allows POST and OPTIONS only, from any origin, and wants JSON and a small body', async () => {
    const world = fakeWorld();
    const options = await handleMcp(new Request('https://x.test/mcp', { method: 'OPTIONS', headers: { origin: 'https://claude.ai' } }), world.deps);
    expect(options.status).toBe(204);
    expect(options.headers.get('access-control-allow-origin')).toBe('*');
    expect(options.headers.get('access-control-allow-headers')).toContain('mcp-protocol-version');
    for (const method of ['GET', 'DELETE', 'PUT']) {
      const response = await handleMcp(new Request('https://x.test/mcp', { method }), world.deps);
      expect(response.status, method).toBe(405);
      expect(response.headers.get('allow')).toBe('POST, OPTIONS');
    }
    const wrongType = await handleMcp(new Request('https://x.test/mcp', { method: 'POST', headers: { 'content-type': 'text/plain' }, body: '{}' }), world.deps);
    expect(wrongType.status).toBe(415);
    const big = JSON.stringify(rpc('ping', { pad: 'x'.repeat(MAX_BODY_BYTES) }));
    expect((await handleMcp(post(big), world.deps)).status).toBe(413);
    expect((await handleMcp(post(big, { 'content-length': '5' }), world.deps)).status).toBe(413); // a lying header does not help
    expect((await handleMcp(post(big, { 'content-length': String(big.length) }), world.deps)).status).toBe(413);
    const ok = await handleMcp(post(rpc('ping')), world.deps);
    expect(ok.headers.get('access-control-allow-origin')).toBe('*');
    expect(ok.headers.get('cache-control')).toBe('no-store');
  });

  it('says the service is not set up when a secret is missing', async () => {
    silence();
    const world = fakeWorld();
    const response = await handleMcp(post(rpc('ping')), { ...world.deps, config: undefined });
    expect(response.status).toBe(500);
    expect(((await response.json()) as { error: { code: number } }).error.code).toBe(-32603);
  });
});

describe('search_jobs', () => {
  it('runs the dashboard\'s search function with the arguments and gives the roles, never a text', async () => {
    const world = fakeWorld();
    const answer = await tool(world, 'search_jobs', { query: ' rust  ', levels: ['Junior'], roleTypes: 'R&D', regions: ['Tel Aviv area'], limit: 5, offset: 10 });
    expect(answer.isError).toBe(false);
    expect(answer.data).toEqual({ total: 1, returned: 1, offset: 10, jobs: [PUBLIC] });
    const search = world.calls.find((c) => c.url.endsWith('/rpc/search_jobs'));
    expect(search?.body).toEqual({ p_query: 'rust', p_levels: ['Junior'], p_role_types: ['R&D'], p_regions: ['Tel Aviv area'], p_limit: 5, p_offset: 10 });
    expect(answer.text).not.toContain('THE FULL TEXT');
    expect(answer.text).not.toContain('description');
    expect(world.calls.every((c) => !c.url.includes('/rest/v1/jobs?'))).toBe(true); // never reads the table itself
  });

  it('defaults to 20 roles from the start, clamps the page, and warns about a value no role has', async () => {
    const world = fakeWorld();
    const bare = await tool(world, 'search_jobs', {});
    expect(bare.data).not.toHaveProperty('warnings');
    expect(world.calls.find((c) => c.url.endsWith('/rpc/search_jobs'))?.body).toMatchObject({ p_query: '', p_levels: [], p_limit: 20, p_offset: 0 });
    const short = await tool(world, 'search_jobs', { query: 'qa rust', regions: ['Remote'] });
    expect(short.data?.warnings).toEqual([expect.stringContaining('"qa" has fewer than 3')]); // Remote is a region
    const big = await tool(world, 'search_jobs', { limit: 9999, offset: -4, levels: ['junior'], regions: ['Mars'] });
    const last = world.calls.filter((c) => c.url.endsWith('/rpc/search_jobs')).at(-1);
    expect(last?.body).toMatchObject({ p_limit: 50, p_offset: 0 });
    expect(big.data?.warnings).toEqual([
      expect.stringContaining('levels: no role has "junior"'),
      expect.stringContaining('regions: no role has "Mars"'),
    ]);
  });

  it('turns a bad argument into a message the assistant reads, without counting it or asking the database', async () => {
    const world = fakeWorld();
    for (const args of [{ query: 5 }, { levels: [1] }, { levels: 'x'.repeat(400) }, { regions: Array(21).fill('South') }, { limit: 'ten' }, { offset: 1.5 }, 'text', [], { query: 'q'.repeat(201) }]) {
      const answer = await tool(world, 'search_jobs', args);
      expect(answer.isError, JSON.stringify(args)).toBe(true);
      expect(answer.text.length).toBeGreaterThan(10);
    }
    expect(world.fakeFetch).not.toHaveBeenCalled();
  });

  it('is limited per caller per day, and another caller has its own limit', async () => {
    const world = fakeWorld();
    for (let i = 0; i < SEARCH_DAILY_LIMIT; i += 1) {
      expect((await tool(world, 'search_jobs', {}, { 'cf-connecting-ip': '203.0.113.7' })).isError).toBe(false);
    }
    const refused = await tool(world, 'search_jobs', {}, { 'cf-connecting-ip': '203.0.113.7' });
    expect(refused.isError).toBe(true);
    expect(refused.text).toContain('limit for today');
    expect((await tool(world, 'search_jobs', {}, { 'cf-connecting-ip': '203.0.113.8' })).isError).toBe(false);
  });

  it('fails closed when the database cannot be reached or answers something unknown', async () => {
    silence();
    for (const database of ['down', 'garbage', 'search-garbage'] as const) {
      const world = fakeWorld({ database });
      const answer = await tool(world, 'search_jobs', { query: 'rust' });
      expect(answer.isError, database).toBe(true);
      expect(answer.text).toContain('could not be reached');
      expect(answer.text).not.toContain('refused');
    }
  });
});

describe('get_job', () => {
  it('gives the one job with the full text, read by its exact key with the service role', async () => {
    const world = fakeWorld();
    const answer = await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
    expect(answer.isError).toBe(false);
    expect(answer.data).toEqual({ ...JOB, details: SAFE_DETAILS }); // only the candidate-facing details, no ATS metadata, hash, HTML or ids
    expect(answer.text).not.toMatch(/text_sha256|requisition|recruiter|<script/u);
    expect(answer.data?.description).toContain('full posting');
    const read = world.calls.find((c) => c.url.includes('/rest/v1/jobs?'));
    const query = new URL(read?.url ?? '').searchParams;
    expect(query.get('company')).toBe('eq.Acme');
    expect(query.get('job_id')).toBe('eq.j1');
    expect(query.get('limit')).toBe('1');
    expect(query.get('or')).toBe('(status.eq.open,last_seen.gte.2026-10-18)'); // open, or closed in the last 30 days
    expect(query.get('select')).toContain('description');
    expect(read?.headers.authorization).toBe('Bearer service-role-test-key');
  });

  it('is one job per call: a database that gave more is cut to the first, and a missing key or a list is refused', async () => {
    const world = fakeWorld({ jobs: [JOB, { ...JOB, job_id: 'j2' }] });
    const answer = await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
    expect(Array.isArray(answer.data)).toBe(false);
    expect(answer.data?.job_id).toBe('j1');
    for (const args of [{}, { company: 'Acme' }, { job_id: 'j1' }, { company: '  ', job_id: 'j1' }, { company: ['Acme', 'Beta'], job_id: 'j1' }, { company: 'Acme', job_id: ['j1', 'j2'] }, { company: 'Acme', job_id: 7 }, 'Acme|j1', { company: 'A'.repeat(301), job_id: 'j1' }]) {
      const refused = await tool(fakeWorld(), 'get_job', args);
      expect(refused.isError, JSON.stringify(args)).toBe(true);
    }
  });

  it('says so when there is no such open or recently closed job, and sends the key as data, not as filter syntax', async () => {
    const world = fakeWorld({ jobs: [] });
    const answer = await tool(world, 'get_job', { company: 'Acme,status.eq.closed', job_id: '*&select=*' });
    expect(answer.isError).toBe(true);
    expect(answer.text).toContain('No open job');
    const query = new URL(world.calls.find((c) => c.url.includes('/rest/v1/jobs?'))?.url ?? '').searchParams;
    expect(query.get('company')).toBe('eq.Acme,status.eq.closed'); // one value of one parameter
    expect(query.get('job_id')).toBe('eq.*&select=*');
    expect(query.get('select')).toContain('description');
    expect([...query.keys()].sort()).toEqual(['company', 'job_id', 'limit', 'or', 'select']);
  });

  it('is limited per caller, and for everyone together, so the texts cannot be read in bulk', async () => {
    const world = fakeWorld();
    const ask1 = (ip: string): Promise<ToolAnswer> => tool(world, 'get_job', { company: 'Acme', job_id: 'j1' }, { 'cf-connecting-ip': ip });
    for (let i = 0; i < JOB_DAILY_LIMIT; i += 1) expect((await ask1('203.0.113.7')).isError).toBe(false);
    const before = world.calls.filter((c) => c.url.includes('/rest/v1/jobs?')).length;
    expect(before).toBe(JOB_DAILY_LIMIT);
    const allBefore = world.counters.get('2026-11-17|get_job:all');
    expect((await ask1('203.0.113.7')).isError).toBe(true);
    expect(world.counters.get('2026-11-17|get_job:all')).toBe(allBefore); // a caller over their own limit does not use the cap of everyone
    expect(world.calls.filter((c) => c.url.includes('/rest/v1/jobs?')).length).toBe(before); // a refused call reads nothing
    // another caller still reads, until the cap of everyone is used up
    world.counters.set(`2026-11-17|get_job:all`, JOB_DAILY_CAP);
    const capped = await ask1('203.0.113.9');
    expect(capped.isError).toBe(true);
    expect(capped.text).toContain('by everyone together');
    // the cap is for get_job only: the search still works
    expect((await tool(world, 'search_jobs', {}, { 'cf-connecting-ip': '203.0.113.9' })).isError).toBe(false);
  });

  it('gives the places back when a call fails or serves nothing, so only served calls count', async () => {
    silence();
    const left = (world: World): number[] => [...world.counters.values()];
    const none = fakeWorld({ jobs: [] });
    await tool(none, 'get_job', { company: 'Acme', job_id: 'nope' });
    expect(left(none).sort()).toEqual([1, 1]); // a miss counts: guessing keys is limited like reading
    const failing = fakeWorld({ database: 'jobs-error' });
    expect((await tool(failing, 'get_job', { company: 'Acme', job_id: 'j1' })).isError).toBe(true);
    expect(left(failing).every((n) => n === 0)).toBe(true);
    const searching = fakeWorld({ database: 'search-garbage' });
    await tool(searching, 'search_jobs', {});
    expect(left(searching).every((n) => n === 0)).toBe(true);
    const served = fakeWorld();
    await tool(served, 'get_job', { company: 'Acme', job_id: 'j1' });
    expect(left(served).sort()).toEqual([1, 1]); // a served call takes one place of the caller and one of the cap
    // over the cap of everyone: the caller's own place is given back too
    const capped = fakeWorld();
    capped.counters.set('2026-11-17|get_job:all', JOB_DAILY_CAP);
    await tool(capped, 'get_job', { company: 'Acme', job_id: 'j1' });
    expect([...capped.counters.entries()].filter(([k]) => !k.endsWith('get_job:all')).every(([, n]) => n === 0)).toBe(true);
  });

  it('does not fail the answer when giving a place back fails', async () => {
    silence();
    const world = fakeWorld({ database: 'release-down', jobs: [JOB] });
    world.counters.set('2026-11-17|get_job:all', JOB_DAILY_CAP);
    const answer = await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
    expect(answer.isError).toBe(true);
    expect(answer.text).toContain('by everyone together');
  });

  it('does not read anything when the counter cannot be reached (fail closed)', async () => {
    silence();
    for (const database of ['down', 'garbage'] as const) {
      const world = fakeWorld({ database });
      const answer = await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
      expect(answer.isError).toBe(true);
      expect(world.calls.every((c) => !c.url.includes('/rest/v1/jobs?'))).toBe(true);
    }
  });

  it('fails closed when the jobs table answers with an error or with something that is not a list', async () => {
    silence();
    for (const reply of [() => new Response('no', { status: 500 }), () => new Response('<html>', { status: 200 }), () => Response.json({ error: 'x' })]) {
      const world = fakeWorld();
      world.fakeFetch.mockImplementation((url) =>
        Promise.resolve(url.includes('/rest/v1/jobs?') ? reply() : Response.json('ok')),
      );
      const answer = await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
      expect(answer.isError).toBe(true);
      expect(answer.text).toContain('could not be reached');
    }
  });
});

describe('the details allowlist', () => {
  it('passes every listed key as text, a flag or a list of text, and nothing else', () => {
    const all = Object.fromEntries(DETAIL_KEYS.map((key, i) => [key, i % 3 === 0 ? 'text' : i % 3 === 1 ? true : ['a', 'b']]));
    expect(candidateDetails({ ...all, text_sha256: 'x', other: 'y', employment_type: 5, offices: [1, 'a'], function: { a: 1 } })).toEqual(
      Object.fromEntries(Object.entries(all).filter(([key]) => !['employment_type', 'offices', 'function'].includes(key))),
    );
    for (const bad of [null, undefined, 'text', ['a'], 7]) expect(candidateDetails(bad)).toEqual({});
  });
});

describe('what is kept', () => {
  it('gives the database only hashes that change every day, and logs no address, query or job', async () => {
    const world = fakeWorld();
    const logs = [vi.spyOn(console, 'error').mockImplementation(() => undefined), vi.spyOn(console, 'warn').mockImplementation(() => undefined), vi.spyOn(console, 'log').mockImplementation(() => undefined)];
    await tool(world, 'search_jobs', { query: 'secret-query' }, { 'cf-connecting-ip': '203.0.113.7' });
    await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' }, { 'cf-connecting-ip': '203.0.113.7' });
    await tool(world, 'get_job', { company: 'Nobody', job_id: 'x' }, { 'x-forwarded-for': 'garbage' });
    const keys = [...world.counters.keys()];
    expect(keys.some((k) => /^2026-11-17\|[0-9a-f]{64}$/u.test(k))).toBe(true);
    expect(keys.filter((k) => !/\|[0-9a-f]{64}$/u.test(k))).toEqual(['2026-11-17|get_job:all']); // the cap of everyone is the one key of nobody
    expect(keys.join(' ')).not.toContain('203.0.113.7');
    const counted = world.calls.filter((c) => c.url.endsWith('/rpc/assistant_reserve')).map((c) => JSON.stringify(c.body)).join(' ');
    expect(counted).not.toContain('203.0.113.7');
    expect(counted).not.toContain('secret-query');
    const logged = logs.flatMap((spy) => spy.mock.calls).join(' ');
    expect(logged).not.toMatch(/203\.0\.113|secret-query|Acme|Nobody/u);
    // the search and the get_job of one caller are counted apart: two callers' get_job, one search, and the cap
    expect(new Set(keys).size).toBe(4);
  });

  it('sends the service-role key to the database only', async () => {
    const world = fakeWorld();
    await tool(world, 'get_job', { company: 'Acme', job_id: 'j1' });
    for (const { url, headers } of world.calls) {
      expect(url.startsWith('https://project.supabase.test/')).toBe(true);
      expect(headers.authorization).toBe('Bearer service-role-test-key');
    }
  });
});

describe('the tool arguments', () => {
  it('reads a search and a job', () => {
    expect(parseSearch(undefined)).toEqual({ ok: false, message: 'The arguments must be an object.' });
    expect(parseSearch({ roleTypes: ['Other'] })).toMatchObject({ ok: true, value: { roleTypes: ['Other'], warnings: [] } });
    expect(parseJob(null)).toEqual({ ok: false, message: 'The arguments must be an object.' });
    expect(parseJob({ company: ' Acme ', job_id: ' j1 ' })).toEqual({ ok: true, value: { company: 'Acme', jobId: 'j1' } });
  });

  it('has exactly the two tools', () => {
    expect(TOOLS.map((t) => t.name)).toEqual(['search_jobs', 'get_job']);
  });
});
