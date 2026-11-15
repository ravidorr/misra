// Entry point of the `confirm-email` Edge Function (Deno). All the logic is in handler.ts; this file only reads the secrets
// from the function's environment and starts the server, so it is the one file the vitest suite does not run.
import { handleConfirm, loadConfig } from './handler.ts';

declare const Deno: {
  env: { get(name: string): string | undefined };
  serve(handler: (request: Request) => Promise<Response>): unknown;
};

const loaded = loadConfig((name) => Deno.env.get(name));
if ('missing' in loaded) {
  // Names only, never values.
  console.error('confirm-email: missing or malformed secrets:', loaded.missing.join(', '));
}

Deno.serve((request) =>
  handleConfirm(request, {
    config: 'config' in loaded ? loaded.config : undefined,
    fetch,
    now: () => new Date(),
  }),
);
