// No emoji and no em dash in any source file, comments included. ESLint also checks the strings of the JS and TS
// files (eslint-plugin-no-emoji, eslint-plugin-no-em-dash); this covers the rest: Markdown, Python, HTML, CSS, YAML, SQL
// and the comments of the scripts.
//
// Run from the repository root: node scripts/lint-text.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Files whose wording is the lawyer's and may keep an em dash (the emoji rule still applies to them). The list is
// empty: on 2026-11-17 he agreed to commas, colons and parentheses in ASSISTANT.md, and no other file of his has one.
// Add a file here only when he asks for a word to stay as it is.
export const LAWYER_FILES = [];

const TEXT_FILES = ['*.md', '*.py', '*.html', '*.css', '*.yml', '*.sql', '*.js', '*.mjs', '*.ts'];

// docs/ is generated, vendor/ is third-party, data/ holds crawled text.
const SKIPPED = /^(docs|vendor|data)\//;

// \p{Emoji_Presentation} is the real emoji (a check mark or an arrow is not); U+FE0F turns a symbol into one.
const EMOJI = /[\p{Emoji_Presentation}\uFE0F]/u;
const EM_DASH = String.fromCodePoint(0x2014); // not a string literal: ESLint's no-em-dash would flag it

/** The problems of one file, as "file:line: message" strings. */
export function findProblems(file, text) {
  const problems = [];
  text.split('\n').forEach((line, index) => {
    const at = `${file}:${index + 1}`;
    if (EMOJI.test(line)) problems.push(`${at}: emoji`);
    if (line.includes(EM_DASH) && !LAWYER_FILES.includes(file)) problems.push(`${at}: em dash`);
  });
  return problems;
}

function main() {
  const files = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', ...TEXT_FILES], {
    encoding: 'utf8',
  })
    .split('\n')
    .filter((file) => file && !SKIPPED.test(file));
  const problems = files.flatMap((file) => findProblems(file, readFileSync(file, 'utf8')));
  if (problems.length > 0) {
    console.error(`${problems.join('\n')}\nlint-text: remove the emoji and use a hyphen or a comma instead of an em dash.`);
    process.exit(1);
  }
  console.log(`lint-text: ${files.length} files, no emoji, no em dash.`);
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
