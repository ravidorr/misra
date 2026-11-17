// The two tools of the assistant endpoint (PLAN.md step 8.1; PRD FR-6): what an assistant is told about them and how their
// arguments are read. No network and no database here, so the vitest suite runs this file as it is.
//
// The three lists below are the values of the dashboard's filters (`classify.py` and `regions.py`); a test compares them with those files, so a role type added there cannot be forgotten here.

export const LEVELS = ['Intern', 'Junior', 'Not specified'] as const;
export const ROLE_TYPES = [
  'Product',
  'R&D',
  'DevOps & SRE',
  'Data & AI',
  'Design',
  'IT',
  'Finance',
  'Sales & BD',
  'Customer success & support',
  'Other',
] as const;
export const REGIONS = ['Tel Aviv area', 'Sharon', 'Center', 'Jerusalem', 'Haifa and the north', 'South', 'Remote'] as const;

export const DEFAULT_LIMIT = 20;
export const MAX_LIMIT = 50;
const MAX_OFFSET = 10_000;
const MAX_TEXT = 300;
/** The SQL search cuts the query here, so a longer one is refused rather than cut in the middle of a word. */
const MAX_QUERY = 200;
const MAX_LIST = 20;

const list = (values: readonly string[]): string => values.join(', ');

export const TOOLS = [
  {
    name: 'search_jobs',
    title: 'Search jobs',
    description:
      'Search the open entry-level, student and product roles in Israel that Misra reads daily from company career pages. ' +
      'The results are the same as on the Misra dashboard. Every filter is optional; with none you get the newest roles. ' +
      'Each result has the company, title, location, a link to the employer, the level, role types, regions and the start ' +
      'of the posting (snippet). Use get_job with the company and job_id of a result for the whole posting.',
    inputSchema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description:
            'Words to look for. A role matches when its company, title, team or location has every word, or the full text of ' +
            'its posting has every word (words of three letters or more).',
        },
        levels: {
          type: 'array',
          items: { type: 'string', enum: [...LEVELS] },
          description:
            'Levels to include (any of them): Intern (intern, internship and student titles), Junior (junior, graduate, ' +
            'entry and trainee titles), Not specified (every other title). Empty or missing means every level.',
        },
        roleTypes: {
          type: 'array',
          items: { type: 'string', enum: [...ROLE_TYPES] },
          description: `Role types (fields) to include, any of them. A title can have several. Values: ${list(ROLE_TYPES)}. Empty or missing means every role type.`,
        },
        regions: {
          type: 'array',
          items: { type: 'string', enum: [...REGIONS] },
          description: `Regions of Israel to include, any of them. Values: ${list(REGIONS)}. Empty or missing means every region.`,
        },
        limit: {
          type: 'integer',
          minimum: 1,
          maximum: MAX_LIMIT,
          description: `How many roles to return, newest first. Default ${String(DEFAULT_LIMIT)}, at most ${String(MAX_LIMIT)}.`,
        },
        offset: { type: 'integer', minimum: 0, description: 'How many roles to skip, to read the next page. Default 0.' },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'get_job',
    title: 'Get one job',
    description:
      'Get one job with everything Misra collected: title, company, team, location, link to the employer, level, role ' +
      'types, regions, dates and the full text of the posting (description) and the details the site gave. Use it to ' +
      'read a role closely or to draft a cover letter. One job per call, by the company and job_id of a search_jobs ' +
      'result. Only open roles and roles closed in the last 30 days are available.',
    inputSchema: {
      type: 'object',
      properties: {
        company: { type: 'string', description: 'The company, exactly as in a search_jobs result.' },
        job_id: { type: 'string', description: 'The job_id, exactly as in a search_jobs result.' },
      },
      required: ['company', 'job_id'],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
] as const;

export interface SearchArguments {
  query: string;
  levels: string[];
  roleTypes: string[];
  regions: string[];
  limit: number;
  offset: number;
  /** Values no role has: told to the assistant, so an empty answer is never silent. */
  warnings: string[];
}

export interface JobArguments {
  company: string;
  jobId: string;
}

type Parsed<T> = { ok: true; value: T } | { ok: false; message: string };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function text(value: unknown, name: string, required: boolean, max = MAX_TEXT): Parsed<string> {
  if (value === undefined || value === null) {
    return required ? { ok: false, message: `${name} is required.` } : { ok: true, value: '' };
  }
  if (typeof value !== 'string') return { ok: false, message: `${name} must be a string.` };
  const trimmed = value.trim();
  if (required && trimmed === '') return { ok: false, message: `${name} is required.` };
  if (trimmed.length > max) return { ok: false, message: `${name} can be at most ${String(max)} characters.` };
  return { ok: true, value: trimmed };
}

/** A list of strings; one string is a list of one. */
function strings(value: unknown, name: string, known: readonly string[], warnings: string[]): Parsed<string[]> {
  if (value === undefined || value === null) return { ok: true, value: [] };
  const given: unknown[] = Array.isArray(value) ? (value as unknown[]) : [value];
  if (given.length > MAX_LIST) return { ok: false, message: `${name} can have at most ${String(MAX_LIST)} values.` };
  const out: string[] = [];
  for (const item of given) {
    if (typeof item !== 'string' || item.length > MAX_TEXT) return { ok: false, message: `${name} must be a list of strings.` };
    out.push(item);
    if (!known.includes(item)) warnings.push(`${name}: no role has "${item}" (values are case-sensitive: ${list(known)})`);
  }
  return { ok: true, value: out };
}

function whole(value: unknown, name: string, fallback: number, min: number, max: number): Parsed<number> {
  if (value === undefined || value === null) return { ok: true, value: fallback };
  if (typeof value !== 'number' || !Number.isInteger(value)) return { ok: false, message: `${name} must be a whole number.` };
  return { ok: true, value: Math.min(max, Math.max(min, value)) };
}

export function parseSearch(args: unknown): Parsed<SearchArguments> {
  if (!isRecord(args)) return { ok: false, message: 'The arguments must be an object.' };
  const warnings: string[] = [];
  const query = text(args.query, 'query', false, MAX_QUERY);
  const levels = strings(args.levels, 'levels', LEVELS, warnings);
  const roleTypes = strings(args.roleTypes, 'roleTypes', ROLE_TYPES, warnings);
  const regions = strings(args.regions, 'regions', REGIONS, warnings);
  const limit = whole(args.limit, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT);
  const offset = whole(args.offset, 'offset', 0, 0, MAX_OFFSET);
  if (!query.ok) return query;
  for (const word of query.value.split(/\s+/u)) {
    if (word !== '' && !/[\p{L}\p{N}]{3}/u.test(word)) {
      warnings.push(`query: "${word}" has fewer than 3 letters or digits, so the full text is not searched for it (the company, title, team and location are)`);
    }
  }
  if (!levels.ok) return levels;
  if (!roleTypes.ok) return roleTypes;
  if (!regions.ok) return regions;
  if (!limit.ok) return limit;
  if (!offset.ok) return offset;
  return {
    ok: true,
    value: {
      query: query.value,
      levels: levels.value,
      roleTypes: roleTypes.value,
      regions: regions.value,
      limit: limit.value,
      offset: offset.value,
      warnings,
    },
  };
}

export function parseJob(args: unknown): Parsed<JobArguments> {
  if (!isRecord(args)) return { ok: false, message: 'The arguments must be an object.' };
  const company = text(args.company, 'company', true);
  if (!company.ok) return company;
  const jobId = text(args.job_id, 'job_id', true);
  if (!jobId.ok) return jobId;
  return { ok: true, value: { company: company.value, jobId: jobId.value } };
}
