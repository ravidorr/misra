import js from '@eslint/js';
import noEmDash from 'eslint-plugin-no-em-dash';
import noEmoji from 'eslint-plugin-no-emoji';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import { LAWYER_FILES } from './scripts/lint-text.mjs';

export default tseslint.config(
  // docs/ is generated from templates/ (linted there); vendor/ holds unmodified third-party files.
  { ignores: ['.venv/**', 'dist/**', 'coverage/**', 'node_modules/**', '.lighthouseci/**', 'docs/**', 'vendor/**'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      eqeqeq: ['error', 'always'],
      'no-console': ['error', { allow: ['warn', 'error'] }],
      'no-var': 'error',
      'prefer-const': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/explicit-function-return-type': ['error', { allowExpressions: true }],
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  {
    // No emoji and no em dash in strings (Markdown and Python: scripts/lint-text.mjs).
    plugins: { 'no-emoji': noEmoji, 'no-em-dash': noEmDash },
    rules: { 'no-emoji/no-emoji': 'error', 'no-em-dash/no-em-dash': 'error' },
  },
  ...(LAWYER_FILES.length > 0
    ? [
        {
          // The lawyer's approved wording is not ours to change (see LAWYER_FILES); the emoji rule still applies.
          files: LAWYER_FILES.filter((file) => file.endsWith('.js')),
          rules: { 'no-em-dash/no-em-dash': 'off' },
        },
      ]
    : []),
  {
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: { globals: globals.node },
    // Plain JS/MJS tooling scripts have no type annotations; return types apply to TypeScript only.
    rules: { '@typescript-eslint/explicit-function-return-type': 'off' },
  },
  {
    // The dashboard scripts run in the browser, not in Node.
    files: ['templates/**/*.js'],
    languageOptions: { globals: globals.browser },
  },
  {
    // CLI scripts print to stdout.
    files: ['scripts/**'],
    rules: { 'no-console': 'off' },
  },
  {
    // Plain JS/MJS config and script files are not part of the TS project.
    files: ['**/*.{js,mjs,cjs}'],
    ...tseslint.configs.disableTypeChecked,
  },
);
