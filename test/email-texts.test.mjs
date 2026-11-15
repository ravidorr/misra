import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { PAGE_URL, renderEmail } from '../supabase/functions/email-settings/handler.ts';

// A plain .mjs file: the typecheck of the functions has no Node types, and this test only needs the file system.
describe('the confirmation email', () => {
  it('matches supabase/email-texts.md line for line', () => {
    const file = readFileSync(new URL('../supabase/email-texts.md', import.meta.url), 'utf8');
    const block = /## Confirmation email[\s\S]*?```text\n([\s\S]*?)```/u.exec(file)?.[1] ?? '';
    const expected = block
      .replace('{{schedule}}', 'weekly')
      .replace('{{user_email}}', 'ann@example.test')
      .replace('{{confirm_url}}', `${PAGE_URL}#token=abc`);
    expect(expected).not.toBe('');
    expect(renderEmail('weekly', 'ann@example.test', `${PAGE_URL}#token=abc`).text).toBe(expected);
  });
});
