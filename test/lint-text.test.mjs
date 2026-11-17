import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

import { LAWYER_FILES, findProblems } from '../scripts/lint-text.mjs';

// Built from code points so that this file holds neither character.
const EM_DASH = String.fromCodePoint(0x2014);
const EMOJI = String.fromCodePoint(0x1f600);
const WARNING_SIGN = `${String.fromCodePoint(0x26a0)}${String.fromCodePoint(0xfe0f)}`;

describe('ESLint: no emoji and no em dash in strings', () => {
  const eslint = new ESLint();

  async function ruleIds(code, filePath) {
    const [result] = await eslint.lintText(code, { filePath });
    return result.messages.map((message) => message.ruleId);
  }

  it('fails on an emoji in a string', async () => {
    expect(await ruleIds(`export const a = 'hi ${EMOJI}';\n`, 'scripts/sample.mjs')).toEqual(['no-emoji/no-emoji']);
  });

  it('fails on an em dash in a string and in a template literal', async () => {
    const code = `export const a = 'a ${EM_DASH} b';\nexport const b = \`c ${EM_DASH} d\`;\n`;
    expect(await ruleIds(code, 'templates/sample.js')).toEqual(['no-em-dash/no-em-dash', 'no-em-dash/no-em-dash']);
  });

  it('accepts a hyphen, and fails on an em dash and an emoji even in templates/email.js', async () => {
    expect(await ruleIds("export const a = 'a - b';\n", 'scripts/sample.mjs')).toEqual([]);
    expect(await ruleIds(`export const a = 'a ${EM_DASH} b';\n`, 'templates/email.js')).toEqual(['no-em-dash/no-em-dash']);
    expect(await ruleIds(`export const a = '${EMOJI}';\n`, 'templates/email.js')).toEqual(['no-emoji/no-emoji']);
  });
});

describe('lint-text: Markdown and Python', () => {
  it('reports an emoji and an em dash with their line', () => {
    expect(findProblems('x.md', `fine\nbad ${EMOJI}\nalso ${WARNING_SIGN}\nand ${EM_DASH}\n`)).toEqual([
      'x.md:2: emoji',
      'x.md:3: emoji',
      'x.md:4: em dash',
    ]);
    expect(findProblems('x.py', `s = "a ${EM_DASH} b"\n`)).toEqual(['x.py:1: em dash']);
  });

  it('accepts plain text, a check mark, an arrow and a copyright sign', () => {
    const plain = `ok ${String.fromCodePoint(0x2714)} ${String.fromCodePoint(0x2192)} ${String.fromCodePoint(0xa9)} - –`;
    expect(findProblems('x.md', plain)).toEqual([]);
  });

  it('exempts no file: the lawyer files have no em dash since his answer of 2026-11-17', () => {
    expect(LAWYER_FILES).toEqual([]);
    for (const file of ['ASSISTANT.md', 'PRIVACY.md', 'TERMS.md', 'SECURITY.md', 'supabase/email-texts.md']) {
      expect(findProblems(file, `a ${EM_DASH} b\n`)).toEqual([`${file}:1: em dash`]);
      expect(findProblems(file, `${EMOJI}\n`)).toEqual([`${file}:1: emoji`]);
    }
  });
});
