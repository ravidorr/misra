// Run html-validate on every project HTML file; succeed when there are none (e.g. libraries, CLIs).
import { execFileSync, spawnSync } from 'node:child_process';

const files = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '*.html'], {
  encoding: 'utf8',
})
  .split('\n')
  // Google Search Console ownership files hold one line of text, not HTML.
  .filter((file) => file && !/(^|\/)google[0-9a-f]+\.html$/.test(file));

if (files.length === 0) {
  console.log('lint-html: no HTML files, nothing to validate.');
  process.exit(0);
}

const result = spawnSync('npx', ['html-validate', ...files], { stdio: 'inherit' });
process.exit(result.status ?? 1);
