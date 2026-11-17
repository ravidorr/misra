// The Edge Function is TypeScript for Deno, and `npm run typecheck` only runs mypy. This test type-checks the function
// and its tests with the compiler against test/tsconfig.json, which extends supabase/functions/tsconfig.json (strict), so a type error fails `npm run test` and CI.
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

describe('TypeScript of the Edge Functions and their tests', () => {
  it('compiles without a diagnostic', () => {
    const root = fileURLToPath(new URL('.', import.meta.url));
    const configPath = ts.findConfigFile(root, ts.sys.fileExists, 'tsconfig.json');
    expect(configPath).toBe(`${root}tsconfig.json`);
    const read = ts.readConfigFile(configPath, ts.sys.readFile);
    const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, root);
    expect(parsed.errors.map((d) => ts.flattenDiagnosticMessageText(d.messageText, '\n'))).toEqual([]);
    const names = [
      'handler.ts',
      'validate.ts',
      'email-html.ts',
      'index.ts',
      'subscription.ts',
      'identity.ts',
      'visitor.ts',
      'tools.ts',
      'report-site.test.ts',
      'delete-account.test.ts',
      'confirm-email.test.ts',
      'unsubscribe.test.ts',
      'email-settings.test.ts',
      'email-html.test.ts',
      'mcp.test.ts',
    ];
    for (const name of names) {
      expect(parsed.fileNames.some((file) => file.endsWith(`/${name}`)), name).toBe(true);
    }
    const program = ts.createProgram(parsed.fileNames, parsed.options);
    const messages = ts.getPreEmitDiagnostics(program).map((d) => {
      const where = d.file ? `${d.file.fileName}: ` : '';
      return where + ts.flattenDiagnosticMessageText(d.messageText, '\n');
    });
    expect(messages).toEqual([]);
  }, 60_000);
});
