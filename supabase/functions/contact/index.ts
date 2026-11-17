// Entry point of the `contact` Edge Function (Deno). All the logic is in handler.ts; this file only reads the secrets from
// the function's environment and starts the server, so it is the one file the vitest suite does not run. The endpoint is
// off (it answers 404) unless CONTACT_FORM_ENABLED is "true"; see handler.ts.
import { handleContact, loadConfig } from './handler.ts';

declare const Deno: {
  env: { get(name: string): string | undefined };
  serve(handler: (request: Request) => Promise<Response>): unknown;
};

const loaded = loadConfig((name) => Deno.env.get(name));
if (loaded.enabled && 'missing' in loaded) {
  // Names only, never values.
  console.error('contact: missing or malformed secrets:', loaded.missing.join(', '));
}

if (!loaded.enabled && (Deno.env.get('CONTACT_FORM_ENABLED') ?? '') !== '') {
  console.warn('contact: CONTACT_FORM_ENABLED is set but is not exactly "true", so the endpoint stays off');
}

Deno.serve((request) =>
  handleContact(request, {
    enabled: loaded.enabled,
    config: 'config' in loaded ? loaded.config : undefined,
    fetch,
    now: () => new Date(),
  }),
);
