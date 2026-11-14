import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  // docs/ is generated from templates/ (linted there).
  { ignores: ['.venv/**', 'dist/**', 'coverage/**', 'node_modules/**', '.lighthouseci/**', 'docs/**'] },
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
