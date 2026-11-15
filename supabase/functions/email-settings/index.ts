// Entry point of the `email-settings` Edge Function (Deno). All the logic is in handler.ts; this file only reads the secrets
// from the function's environment and starts the server, so it is the one file the vitest suite does not run.
import { handleEmailSettings, loadConfig } from './handler.ts';

declare const Deno: {
  env: { get(name: string): string | undefined };
  serve(handler: (request: Request) => Promise<Response>): unknown;
};

const loaded = loadConfig((name) => Deno.env.get(name));
if ('missing' in loaded) {
  // Names only, never values.
  console.error('email-settings: missing or malformed secrets:', loaded.missing.join(', '));
} else if (loaded.mailMissing.length > 0) {
  console.error('email-settings: turning the email on is disabled, missing or malformed secrets:', loaded.mailMissing.join(', '));
}

Deno.serve((request) =>
  handleEmailSettings(request, {
    config: 'config' in loaded ? loaded.config : undefined,
    fetch,
    now: () => new Date(),
  }),
);
