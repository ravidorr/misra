// Entry point of the `unsubscribe` Edge Function (Deno). All the logic is in handler.ts; this file only reads the secrets
// from the function's environment and starts the server, so it is the one file the vitest suite does not run.
import { handleUnsubscribe, loadConfig } from './handler.ts';

declare const Deno: {
  env: { get(name: string): string | undefined };
  serve(handler: (request: Request) => Promise<Response>): unknown;
};

const loaded = loadConfig((name) => Deno.env.get(name));
if ('missing' in loaded) {
  // Names only, never values.
  console.error('unsubscribe: missing or malformed secrets:', loaded.missing.join(', '));
}

Deno.serve((request) =>
  handleUnsubscribe(request, {
    config: 'config' in loaded ? loaded.config : undefined,
    fetch,
    now: () => new Date(),
  }),
);
