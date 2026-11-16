import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { renderEmail } from '../supabase/functions/email-settings/handler.ts';
import { PAGE_URL } from '../supabase/functions/_shared/subscription.ts';

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

  it('has every line of the approved text in its HTML version, word for word', () => {
    const file = readFileSync(new URL('../supabase/email-texts.md', import.meta.url), 'utf8');
    const block = /## Confirmation email[\s\S]*?```text\n([\s\S]*?)```/u.exec(file)?.[1] ?? '';
    const lines = block.split('\n').filter((line) => line !== '' && line !== '---');
    expect(lines.length).toBeGreaterThan(5);
    const { html } = renderEmail('weekly', 'ann@example.test', `${PAGE_URL}#token=abc`);
    const seen = html
      .replace(/<head>[\s\S]*?<\/head>/u, '')
      .replace(/<\/?(?:p|td|tr|table|div|h1|body|html)\b[^>]*>/gu, ' ')
      .replace(/<[^>]+>/gu, '')
      .replace(/&quot;/gu, '"')
      .replace(/&amp;/gu, '&')
      .split(/\s+/u)
      .join(' ');
    for (const line of lines) {
      const filled = line.replace('{{schedule}}', 'weekly').replace('{{user_email}}', 'ann@example.test').replace('{{confirm_url}}', `${PAGE_URL}#token=abc`);
      expect(seen, line).toContain(filled);
    }
  });
});
