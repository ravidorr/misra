// No emoji and no em dash in any source file, comments included. ESLint also checks the strings of the JS and TS
// files (eslint-plugin-no-emoji, eslint-plugin-no-em-dash); this covers the rest: Markdown, Python, HTML, CSS, YAML, SQL
// and the comments of the scripts.
//
// Run from the repository root: node scripts/lint-text.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// The lawyer approved the wording of these files word for word and asked for no changes (AGENTS.md), so the
// em dash rule skips them (the emoji rule does not). Only ASSISTANT.md has em dashes today; TODO.md asks him.
export const LAWYER_FILES = [
  'PRIVACY.md',
  'TERMS.md',
  'SECURITY.md',
  'ASSISTANT.md',
  'supabase/email-texts.md',
  'templates/email.js',
];

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
