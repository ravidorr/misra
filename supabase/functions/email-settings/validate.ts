// Input checks of the `email-settings` Edge Function (PLAN.md step 6.2). Pure and free of any Deno or Node API, so the
// repository's test stack (vitest) runs it as it is. Nothing here trusts the caller: the action is one of two words,
// the address must look like an address and cannot carry a header, the frequency is one of two words, and the filters
// snapshot has exactly the four keys the dashboard copies (regions, levels, role types and keywords), each cut to a
// sane size and cleaned of control characters. The function never learns which region or level names exist; the
// digest sender (step 6.3) matches the names against the jobs and ignores one it does not know.

export const LIMITS = { address: 254, name: 80, names: 30, keywords: 200 } as const;
export const FREQUENCIES = ['daily', 'weekly'] as const;
export type Frequency = (typeof FREQUENCIES)[number];

/** What the dashboard's "Use these filters for my email" copies, as it is stored in `subscriptions.filters`. */
export interface Filters {
  regions: string[];
  levels: string[];
  role_types: string[];
  keywords: string;
}

export type FieldName = 'address' | 'frequency' | 'filters';
export type FieldErrors = Partial<Record<FieldName, string>>;

export interface TurnOn {
  action: 'turn_on';
  /** The address as typed (trimmed), or undefined for "my Google email". */
  address: string | undefined;
  frequency: Frequency;
  filters: Filters;
}

export type Validation = { ok: true; request: TurnOn | { action: 'turn_off' } } | { ok: false; fields: FieldErrors };

// Control characters (C0 and C1), the line and paragraph separators and the bidirectional controls.
const UNSAFE = /[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/gu;
// The same set without the `g` flag, for `test`: a global regex keeps its position between calls.
const HAS_UNSAFE = /[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/u;
// The same shape the table accepts (supabase/migrations/20261117100000_subscriptions_and_emailed.sql).
const ADDRESS = /^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$/u;
const FILTER_KEYS = ['regions', 'levels', 'role_types', 'keywords'] as const;

const oneLine = (value: string): string => value.normalize('NFC').replace(UNSAFE, ' ').replace(/\s+/gu, ' ').trim();

/** The address the user chose to type, or undefined for none (an empty field means "my own"); or the reason it is not one. */
export function checkAddress(raw: unknown): { address: string | undefined } | { error: string } {
  if (raw === undefined || raw === null) return { address: undefined };
  if (typeof raw !== 'string') return { error: 'Enter an email address.' };
  const text = raw.trim();
  if (text === '') return { address: undefined };
  if (text.length > LIMITS.address) return { error: `The address can be at most ${String(LIMITS.address)} characters.` };
  if (HAS_UNSAFE.test(text) || !ADDRESS.test(text)) return { error: 'Enter one email address, like name@example.com.' };
  return { address: text };
}

function cleanNames(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const names = new Set<string>();
  for (const item of raw as unknown[]) {
    if (typeof item !== 'string') return undefined;
    const name = oneLine(item);
    if (name === '' || name.length > LIMITS.name) return undefined;
    names.add(name);
  }
  return names.size <= LIMITS.names ? [...names] : undefined;
}

/** The snapshot with exactly the four keys, cleaned; or undefined when it is anything else. */
export function checkFilters(raw: unknown): Filters | undefined {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return undefined;
  const given = raw as Record<string, unknown>;
  if (Object.keys(given).some((key) => !(FILTER_KEYS as readonly string[]).includes(key))) return undefined;
  const regions = cleanNames(given.regions ?? []);
  const levels = cleanNames(given.levels ?? []);
  const roleTypes = cleanNames(given.role_types ?? []);
  const keywords = given.keywords ?? '';
  if (!regions || !levels || !roleTypes || typeof keywords !== 'string') return undefined;
  const words = oneLine(keywords);
  if (words.length > LIMITS.keywords) return undefined;
  return { regions, levels, role_types: roleTypes, keywords: words };
}

export function validateRequest(body: Record<string, unknown>): Validation | 'unknown_action' {
  if (body.action === 'turn_off') return { ok: true, request: { action: 'turn_off' } };
  if (body.action !== 'turn_on') return 'unknown_action';
  const fields: FieldErrors = {};
  const address = checkAddress(body.address);
  if ('error' in address) fields.address = address.error;
  const frequency = FREQUENCIES.find((candidate) => candidate === body.frequency);
  if (frequency === undefined) fields.frequency = 'Choose daily or weekly.';
  const filters = checkFilters(body.filters);
  if (filters === undefined) fields.filters = 'The filters could not be read. Use the filters button again.';
  if ('error' in address || frequency === undefined || filters === undefined) return { ok: false, fields };
  return { ok: true, request: { action: 'turn_on', address: address.address, frequency, filters } };
}
