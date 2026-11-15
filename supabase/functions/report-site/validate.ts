// Input checks of the `report-site` Edge Function. Pure and free of any Deno or Node API, so the repository's test stack
// (vitest) runs it as it is. Nothing here trusts the caller: every field must be a string of a known length, the link
// must be an http(s) address, and the text is normalised so it cannot carry a line break into an email header.

export const LIMITS = { company: 100, link: 500, why: 1000 } as const;

export interface Report {
  company: string;
  link: string;
  why: string;
}

export type FieldName = keyof Report;
export type FieldErrors = Partial<Record<FieldName, string>>;
export type Validation = { ok: true; report: Report } | { ok: false; fields: FieldErrors };

// Control characters (C0 and C1, except the tab and line feed a long text may hold), the line and paragraph separators,
// and the bidirectional controls that make text read differently from what it is.
const UNSAFE = /(?![\t\n])[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/gu;
const LINK_FORBIDDEN = /[\s\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/u;

function asText(value: unknown): string | undefined {
  return typeof value === 'string' ? value.normalize('NFC').replace(/\r\n?/g, '\n').replace(UNSAFE, '') : undefined;
}

/** The company as one line: any run of white space, line breaks included, becomes one space. */
function cleanCompany(value: unknown): string | undefined {
  return asText(value)?.replace(/\s+/gu, ' ').trim();
}

/** The reason as plain text: line breaks kept, trailing spaces and runs of blank lines trimmed. */
function cleanWhy(value: unknown): string | undefined {
  return asText(value)
    ?.replace(/[^\S\n]+\n/gu, '\n')
    .replace(/\n{3,}/gu, '\n\n')
    .trim();
}

/** An http(s) address with a host name and no sign-in details. Returns the normalised address, or the reason. */
export function checkLink(raw: unknown): { link: string } | { error: string } {
  const text = typeof raw === 'string' ? raw.trim() : '';
  if (!text) return { error: 'Enter the link to the careers page.' };
  if (text.length > LIMITS.link) return { error: `The link can be at most ${String(LIMITS.link)} characters.` };
  if (LINK_FORBIDDEN.test(text)) return { error: 'The link cannot contain spaces or line breaks.' };
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return { error: 'Enter a full link that starts with https:// or http://.' };
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    return { error: 'The link must start with https:// or http://.' };
  }
  if (url.username || url.password) return { error: 'The link cannot contain a user name or password.' };
  if (!url.hostname.includes('.') && !url.hostname.includes(':')) {
    return { error: 'Enter a link to a public website.' };
  }
  return { link: url.href };
}

export function validateReport(input: Record<string, unknown>): Validation {
  const fields: FieldErrors = {};

  const company = cleanCompany(input.company);
  if (!company) fields.company = 'Enter the company name.';
  else if (company.length > LIMITS.company) {
    fields.company = `The name can be at most ${String(LIMITS.company)} characters.`;
  }

  const link = checkLink(input.link);
  if ('error' in link) fields.link = link.error;

  const why = cleanWhy(input.why);
  if (!why) fields.why = 'Tell us why this company matters.';
  else if (why.length < 3) fields.why = 'Write a few words on why this company matters.';
  else if (why.length > LIMITS.why) fields.why = `The reason can be at most ${String(LIMITS.why)} characters.`;

  if (Object.keys(fields).length > 0 || !company || 'error' in link || !why) return { ok: false, fields };
  return { ok: true, report: { company, link: link.link, why } };
}
