// Input checks of the `contact` Edge Function. Pure and free of any Deno or Node API, so vitest runs it as it is. Nothing
// here trusts the caller: only the message is required, every text has a length cap, the email (when given) must be one
// plain address, and the texts are normalised so none can carry a line break into an email header. The same checks, with
// the same words, run in templates/contact.js before sending; this file stays the authority.

export const LIMITS = { name: 100, email: 254, message: 2000 } as const;

export interface Contact {
  name: string;
  email: string;
  message: string;
}

export type FieldName = keyof Contact;
export type FieldErrors = Partial<Record<FieldName, string>>;
export type Validation = { ok: true; contact: Contact } | { ok: false; fields: FieldErrors };

// Control characters (C0 and C1, except the tab and line feed a message may hold), the line and paragraph separators, and
// the bidirectional controls that make text read differently from what it is.
const UNSAFE = /(?![\t\n])[\p{Cc}\p{Zl}\p{Zp}\p{Bidi_Control}]/gu;
// One address and nothing else: no space, comma, semicolon, angle bracket or quote, so it cannot add a recipient or a name.
const ADDRESS = /^[^\s<>@,;"'()[\]\\:]+@[^\s<>@,;"'()[\]\\:]+\.[^\s<>@,;"'()[\]\\:]+$/u;

function asText(value: unknown): string {
  return typeof value === 'string' ? value.normalize('NFC').replace(/\r\n?/g, '\n').replace(UNSAFE, '') : '';
}

export function validateContact(input: Record<string, unknown>): Validation {
  const fields: FieldErrors = {};

  const name = asText(input.name).replace(/\s+/gu, ' ').trim();
  if (name.length > LIMITS.name) fields.name = `The name can be at most ${String(LIMITS.name)} characters.`;

  const rawEmail = typeof input.email === 'string' ? input.email.trim() : '';
  if (rawEmail.length > LIMITS.email) fields.email = `The address can be at most ${String(LIMITS.email)} characters.`;
  else if (rawEmail && !ADDRESS.test(rawEmail)) fields.email = 'Enter one email address, like name@example.com.';

  const message = asText(input.message)
    .replace(/[^\S\n]+\n/gu, '\n')
    .replace(/\n{3,}/gu, '\n\n')
    .trim();
  if (!message) fields.message = 'Enter your message.';
  else if (message.length > LIMITS.message) {
    fields.message = `The message can be at most ${String(LIMITS.message)} characters.`;
  }

  if (Object.keys(fields).length > 0) return { ok: false, fields };
  return { ok: true, contact: { name, email: rawEmail, message } };
}
