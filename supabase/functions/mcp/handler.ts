// The logic of the `mcp` Edge Function (PLAN.md step 8.1; PRD FR-6): a public, read-only MCP server (Model Context
// Protocol, Streamable HTTP, JSON answers, no sessions) that lets anyone's AI assistant search the jobs on the Misra
// dashboard and read one job at a time. No sign-in, no user data: it cannot see anyone's stars, discards or preferences.
//
// IT IS OFF UNTIL THE LAWYER HAS REVIEWED IT (his request of 2026-11-17). Every request is answered 404, with no sign
// that anything is here, unless the function secret ASSISTANT_ENDPOINT_ENABLED is exactly "true" (the same pattern as
// DIGEST_SIGNUP_ENABLED). Nobody sets it before he confirms. See supabase/README.md, "Assistant endpoint".
//
// Everything that touches the outside world comes in through `Deps` (the secrets, `fetch`, the clock), so the vitest suite
// runs this file as it is against a fake database. `index.ts` is the only file that knows Deno.
//
// What it returns, and what keeps the texts safe:
//   * `search_jobs`: the dashboard's own search (the SQL function `search_jobs`, which uses `search_job_keys` for the full
//     text), at most 50 roles per call, with the snippet and never the full text.
//   * `get_job`: the one job named by company and job_id, with the full text, only when it is open or closed for less than
//     30 days. Never more than one job per call, never a list.
//   * Rate limits, counted in the database against keyed hashes of the tool, the caller's network and the day (the address
//     itself is never stored or logged): SEARCH_DAILY_LIMIT and JOB_DAILY_LIMIT calls per caller per day, and
//     JOB_DAILY_CAP `get_job` calls per day for everyone together, so many callers cannot read the texts in bulk. If the
//     database cannot be reached nothing is answered (fail closed).
//   * Nothing personal is logged or kept: no address, no query, no job. A log line holds a function name and a status.

import { DatabaseError, callDatabase, callRpc, describe, readLimited } from '../_shared/subscription.ts';
import type { Config as DatabaseConfig, Fetch } from '../_shared/subscription.ts';
import { hashVisitor, visitorNetwork } from '../_shared/visitor.ts';
import { TOOLS, parseJob, parseSearch } from './tools.ts';

export const SEARCH_DAILY_LIMIT = 200;
export const JOB_DAILY_LIMIT = 30;
export const JOB_DAILY_CAP = 1000;
export const MAX_BODY_BYTES = 16_384;
/** A closed job's text is deleted 30 days after it closes (`jobs_text_trim`); it is not offered after that either. */
const CLOSED_DAYS = 30;
const REQUEST_TIMEOUT_MS = 10_000;
const SERVER_INFO = { name: 'misra-jobs', title: 'Misra job search', version: '1.0.0' };
/** Newest first. A client that asks for one of these gets it; for anything else the server answers with the newest. */
export const PROTOCOL_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'] as const;
const INSTRUCTIONS =
  'Misra lists open entry-level, student and product roles in Israel, read daily from company career pages. ' +
  'Use search_jobs to find roles and get_job to read one posting in full. The data is public and read-only. ' +
  'Always link the employer page (url) when you present a role, and say that the posting belongs to the employer.';
/** What a search result holds: the public columns the dashboard reads, never the text. */
const SEARCH_COLUMNS = ['company', 'job_id', 'title', 'team', 'location', 'url', 'first_seen', 'level', 'role_types', 'regions', 'locations', 'snippet'] as const;
const JOB_COLUMNS =
  'company,job_id,title,team,location,url,posted,first_seen,last_seen,status,level,role_types,regions,locations,snippet,description,details';

export interface Config extends DatabaseConfig {
  ipHashSecret: string;
}

export interface Deps {
  /** `ASSISTANT_ENDPOINT_ENABLED` is exactly "true". Anything else answers 404 to everything. */
  enabled: boolean;
  config: Config | undefined;
  fetch: Fetch;
  now: () => Date;
}

/** The function's secrets. Returns what is missing or malformed by name, never by value. */
export function loadConfig(
  get: (name: string) => string | undefined,
): { enabled: boolean; config: Config } | { enabled: boolean; missing: string[] } {
  const read = (name: string): string => (get(name) ?? '').trim();
  const enabled = read('ASSISTANT_ENDPOINT_ENABLED') === 'true';
  const supabaseUrl = read('SUPABASE_URL').replace(/\/+$/u, '');
  const serviceRoleKey = read('SUPABASE_SERVICE_ROLE_KEY');
  const ipHashSecret = read('IP_HASH_SECRET');
  const missing: string[] = [];
  if (!/^https?:\/\//u.test(supabaseUrl)) missing.push('SUPABASE_URL');
  if (!serviceRoleKey) missing.push('SUPABASE_SERVICE_ROLE_KEY');
  if (ipHashSecret.length < 16) missing.push('IP_HASH_SECRET');
  return missing.length > 0 ? { enabled, missing } : { enabled, config: { supabaseUrl, serviceRoleKey, ipHashSecret } };
}

// ---- Answers ---------------------------------------------------------------------------------------------------------

// The data is public and read-only and nothing here uses cookies or a token, so any web page may call it.
const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'POST, OPTIONS',
  'access-control-allow-headers': 'content-type, accept, mcp-protocol-version, mcp-session-id, authorization, apikey, x-client-info',
  'access-control-max-age': '86400',
};

function answer(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(status === 202 || status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      ...(status === 202 || status === 204 ? {} : { 'content-type': 'application/json; charset=utf-8' }),
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      ...CORS,
      ...extra,
    },
  });
}

type Id = string | number | null;
const rpcResult = (id: Id, result: unknown): Response => answer(200, { jsonrpc: '2.0', id, result });
const rpcError = (id: Id, code: number, message: string, status = 200): Response =>
  answer(status, { jsonrpc: '2.0', id, error: { code, message } });

/** A tool's answer: text for the assistant. An error is a result the assistant reads, not a protocol error. */
const toolResult = (id: Id, data: unknown): Response =>
  rpcResult(id, { content: [{ type: 'text', text: JSON.stringify(data) }] });
const toolError = (id: Id, message: string): Response =>
  rpcResult(id, { content: [{ type: 'text', text: message }], isError: true });

// ---- The database ----------------------------------------------------------------------------------------------------

/** Takes one place in a counter of today; false when it is used up. */
async function reserve(deps: Deps, config: Config, day: string, key: string, limit: number): Promise<boolean> {
  const args = { p_day: day, p_ip_hash: key, p_limit: limit };
  return (await callDatabase(deps, config, 'assistant_reserve', args, ['ok', 'limit'])) === 'ok';
}

/** The one job with this key, when it is open or closed for less than 30 days; undefined otherwise. */
async function readJob(deps: Deps, config: Config, company: string, jobId: string): Promise<Record<string, unknown> | undefined> {
  const since = new Date(deps.now().getTime() - CLOSED_DAYS * 86_400_000).toISOString().slice(0, 10);
  const query = new URLSearchParams({
    select: JOB_COLUMNS,
    company: `eq.${company}`,
    job_id: `eq.${jobId}`,
    or: `(status.eq.open,last_seen.gte.${since})`,
    limit: '1',
  });
  const response = await deps.fetch(`${config.supabaseUrl}/rest/v1/jobs?${query.toString()}`, {
    method: 'GET',
    headers: { apikey: config.serviceRoleKey, authorization: `Bearer ${config.serviceRoleKey}`, accept: 'application/json' },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    throw new DatabaseError(`jobs answered ${String(response.status)}`);
  }
  const rows: unknown = await response.json().catch(() => {
    throw new DatabaseError('jobs gave an answer that is not JSON');
  });
  if (!Array.isArray(rows)) throw new DatabaseError('jobs gave an answer that is not a list');
  return rows[0] as Record<string, unknown> | undefined;
}

// ---- The tools -------------------------------------------------------------------------------------------------------

const LIMITED =
  'This assistant connection has reached its limit for today. It starts again tomorrow (UTC). Tell the person, and send them to the Misra dashboard for more.';
const LIMITED_ALL =
  'The job texts have been read too many times today, by everyone together. Try again tomorrow (UTC), and send the person to the Misra dashboard for now.';
const UNAVAILABLE = 'The job data could not be reached right now. Try again later.';

async function callTool(deps: Deps, config: Config, id: Id, name: string, args: unknown, network: string): Promise<Response> {
  const day = deps.now().toISOString().slice(0, 10);
  const caller = async (tool: string, limit: number): Promise<boolean> =>
    reserve(deps, config, day, await hashVisitor(config.ipHashSecret, day, `${tool}|${network}`), limit);
  try {
    if (name === 'search_jobs') {
      const parsed = parseSearch(args);
      if (!parsed.ok) return toolError(id, parsed.message);
      const { query, levels, roleTypes, regions, limit, offset, warnings } = parsed.value;
      if (!(await caller('search_jobs', SEARCH_DAILY_LIMIT))) return toolError(id, LIMITED);
      const found = (await callRpc(deps, config, 'search_jobs', {
        p_query: query,
        p_levels: levels,
        p_role_types: roleTypes,
        p_regions: regions,
        p_limit: limit,
        p_offset: offset,
      })) as { total?: unknown; jobs?: unknown } | null;
      if (typeof found?.total !== 'number' || !Array.isArray(found.jobs)) throw new DatabaseError('search_jobs gave an unknown answer');
      // Defence in depth: only the public columns of a role ever leave, whatever the database function returns.
      const jobs = found.jobs.filter(isRecord).map((job) => Object.fromEntries(SEARCH_COLUMNS.map((column) => [column, job[column] ?? null])));
      return toolResult(id, {
        total: found.total,
        returned: jobs.length,
        offset,
        jobs,
        ...(warnings.length > 0 ? { warnings } : {}),
      });
    }
    // get_job: the per-caller limit first, so one caller cannot use up the cap of everyone.
    const parsed = parseJob(args);
    if (!parsed.ok) return toolError(id, parsed.message);
    if (!(await caller('get_job', JOB_DAILY_LIMIT))) return toolError(id, LIMITED);
    if (!(await reserve(deps, config, day, 'get_job:all', JOB_DAILY_CAP))) return toolError(id, LIMITED_ALL);
    const job = await readJob(deps, config, parsed.value.company, parsed.value.jobId);
    if (!job) return toolError(id, 'No open job, or job closed in the last 30 days, has this company and job_id. Search again with search_jobs.');
    return toolResult(id, job);
  } catch (error) {
    console.error(`mcp: ${name} failed`, describe(error));
    return toolError(id, UNAVAILABLE);
  }
}

// ---- The request -----------------------------------------------------------------------------------------------------

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function negotiate(requested: unknown): string {
  return PROTOCOL_VERSIONS.find((version) => version === requested) ?? PROTOCOL_VERSIONS[0];
}

export async function handleMcp(request: Request, deps: Deps): Promise<Response> {
  try {
    return await route(request, deps);
  } catch (error) {
    console.error('mcp: failed', describe(error)); // e.g. a client that hangs up in the middle of its message
    return rpcError(null, -32603, 'Something went wrong. Try again later.', 500);
  }
}

async function route(request: Request, deps: Deps): Promise<Response> {
  if (!deps.enabled) return new Response('{"error":"not_found"}', { status: 404, headers: { 'content-type': 'application/json' } });
  if (request.method === 'OPTIONS') return answer(204, null);
  if (request.method !== 'POST') return answer(405, { error: 'method_not_allowed' }, { allow: 'POST, OPTIONS' });
  if (!(request.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json')) {
    return rpcError(null, -32600, 'Send the message as application/json.', 415);
  }
  const { config } = deps;
  if (!config) {
    console.error('mcp: the secrets are missing or malformed (see the log at start-up for their names)');
    return rpcError(null, -32603, 'The service is not set up yet.', 500);
  }

  const text = (Number(request.headers.get('content-length') ?? '0') > MAX_BODY_BYTES) ? undefined : await readLimited(request, MAX_BODY_BYTES);
  if (text === undefined) return rpcError(null, -32600, `The message can be at most ${String(MAX_BODY_BYTES)} bytes.`, 413);
  let message: unknown;
  try {
    message = JSON.parse(text);
  } catch {
    return rpcError(null, -32700, 'Parse error.', 400);
  }
  if (!isRecord(message) || message.jsonrpc !== '2.0') return rpcError(null, -32600, 'Send one JSON-RPC 2.0 message.', 400);

  // A notification (no id) or a response from the client needs no answer.
  if (!('id' in message) || typeof message.method !== 'string') return answer(202, null);
  const id = message.id;
  if (typeof id !== 'string' && typeof id !== 'number') return rpcError(null, -32600, 'The id must be a string or a number.', 400);

  switch (message.method) {
    case 'initialize': {
      const params = isRecord(message.params) ? message.params : {};
      return rpcResult(id, {
        protocolVersion: negotiate(params.protocolVersion),
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
        instructions: INSTRUCTIONS,
      });
    }
    case 'ping':
      return rpcResult(id, {});
    case 'tools/list':
      return rpcResult(id, { tools: TOOLS });
    case 'tools/call': {
      const params = isRecord(message.params) ? message.params : {};
      const name = params.name;
      if (typeof name !== 'string' || !TOOLS.some((tool) => tool.name === name)) return rpcError(id, -32602, 'Unknown tool.');
      return callTool(deps, config, id, name, params.arguments ?? {}, visitorNetwork(request.headers, 'mcp'));
    }
    default:
      return rpcError(id, -32601, 'Method not found.');
  }
}
