import { describe, expect, it } from 'vitest';

import { renderEmail } from '../supabase/functions/email-settings/handler.ts';
import { htmlText } from './support/html-text.ts';
import { PAGE_URL } from '../supabase/functions/_shared/subscription.ts';

const LINK = `${PAGE_URL}#token=${'a'.repeat(43)}`;

function plain(text: string): string {
  return text
    .split('\n')
    .filter((line) => line !== '---')
    .join(' ')
    .split(/\s+/u)
    .filter((word) => word !== '')
    .join(' ');
}

describe('the confirmation email as HTML', () => {
  it('carries exactly the words and links of the plain text, in the same order', () => {
    for (const to of ['ann@example.test', "o'neil&co@example.test"]) {
      const { text, html } = renderEmail('weekly', to, LINK);
      expect(htmlText(html)).toBe(plain(text));
      const links = [...html.matchAll(/href="([^"]*)"/gu)].map((match) => match[1]);
      expect(links).toContain(LINK);
      for (const link of links) expect(text).toContain(link?.replace(/&amp;/gu, '&'));
    }
  });

  it('has one primary button, a link with a background, a padded cell for Outlook and the address as its label', () => {
    const { html } = renderEmail('daily', 'ann@example.test', LINK);
    expect(html).not.toContain('<button');
    expect(html).toContain('mso-padding-alt');
    expect(html).toContain(`<a dir="ltr" href="${LINK}" style=`); /* the link is the button... */
    expect(html).toContain(`>${LINK}</a></td></tr></table>`); /* ...and its label is the address */
    expect(html).toContain('lang="en" dir="ltr"');
    expect(html).toContain('role="presentation"');
    expect(html).toContain('prefers-color-scheme:dark');
  });

  it('has no script, image, external stylesheet or background image, and is small', () => {
    const { html } = renderEmail('daily', 'ann@example.test', LINK);
    for (const forbidden of ['<script', '<img', '<link', '<iframe', 'src=', 'url(', '@import']) {
      expect(html).not.toContain(forbidden);
    }
    expect(new TextEncoder().encode(html).length).toBeLessThan(10_000);
  });

  it('escapes the address, and a link that is not a web address stays text', () => {
    const { html } = renderEmail('daily', '<script>x</script>"@example.test', 'javascript:alert(1)');
    expect(html).not.toContain('<script');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('href="javascript:');
  });
});
