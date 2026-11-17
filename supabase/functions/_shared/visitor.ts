// How the functions that rate limit a visitor tell visitors apart without storing who they are (`report-site`, `mcp`): the
// visitor's network as the platform reports it, and a keyed hash of that network and the day. The address itself never
// reaches the database or a log; the hash changes every day, so yesterday's matches nothing today. Plain web standards
// only, like the rest of `_shared`.

/** An IPv4 address as it is, an IPv6 address as its /64 network (one home or one host gets one bucket, not 2^64). */
export function normalizeAddress(raw: string): string | undefined {
  const text = raw.trim().replace(/^\[|\]$/gu, '').replace(/%.*$/u, '').toLowerCase();
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/u.exec(text);
  const candidate = mapped?.[1] ?? text;
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/u.test(candidate)) {
    return candidate.split('.').every((part) => Number(part) <= 255) ? candidate : undefined;
  }
  if (!/^[0-9a-f:]+$/u.test(candidate) || !candidate.includes(':')) return undefined;
  const halves = candidate.split('::');
  if (halves.length > 2) return undefined;
  const head = halves[0] ? halves[0].split(':') : [];
  const tail = halves[1] ? halves[1].split(':') : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 1 ? missing !== 0 : missing < 1) return undefined;
  const groups = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill('0'), ...tail];
  if (groups.some((group) => group.length < 1 || group.length > 4)) return undefined;
  return `${groups
    .slice(0, 4)
    .map((group) => group.padStart(4, '0'))
    .join(':')}::/64`;
}

/**
 * The visitor's network as the platform reports it. `cf-connecting-ip` is set by Cloudflare in front of Supabase and
 * replaces whatever the caller sent. `x-forwarded-for` is the fallback, and only its LAST entry counts: each proxy
 * appends the address it saw, so the first entries are whatever the caller wrote. If neither gives an address every such
 * request shares one bucket, which makes the limit stricter, never looser. `caller` is the function's name, for the log.
 */
export function visitorNetwork(headers: Headers, caller: string): string {
  const cloudflare = normalizeAddress(headers.get('cf-connecting-ip') ?? '');
  if (cloudflare) return cloudflare;
  const entries = (headers.get('x-forwarded-for') ?? '').split(',');
  const forwarded = normalizeAddress(entries[entries.length - 1] ?? '');
  if (forwarded) return forwarded;
  console.warn(`${caller}: no usable visitor address, using the shared bucket`);
  return 'unknown';
}

async function hmac(key: BufferSource, message: string): Promise<ArrayBuffer> {
  const cryptoKey = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(message));
}

/** A keyed hash of the visitor and the day. The salt of the day comes from the secret, so yesterday's hashes match nothing. */
export async function hashVisitor(secret: string, day: string, network: string): Promise<string> {
  const salt = await hmac(new TextEncoder().encode(secret), day);
  const hash = new Uint8Array(await hmac(salt, network));
  return Array.from(hash, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
