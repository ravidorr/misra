import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MockInstance } from 'vitest';

import {
  ADDRESS_DAILY_LIMIT,
  ALLOWED_ORIGIN,
  CONFIRM_TTL_DAYS,
  DAILY_CAP,
  DEFAULT_FROM,
  DEFAULT_REPLY_TO,
  SUBJECT,
  USER_DAILY_LIMIT,
  handleEmailSettings,
  hashKey,
  loadConfig,
  renderEmail,
} from '../supabase/functions/email-settings/handler.ts';
import { checkAddress, checkFilters, validateRequest } from '../supabase/functions/email-settings/validate.ts';
import { PAGE_URL, hashToken } from '../supabase/functions/_shared/subscription.ts';
import { ANN, BASE, BOB, CAROL, FILTERS, HASH_SECRET, NOW, ORIGIN, RESEND_KEY, SERVICE_KEY, post, tokenIn, turnOn, world } from './support/fake-email-settings.ts';
import type { World } from './support/fake-email-settings.ts';

interface Answer {
  ok: boolean;
  state?: string;
  status?: string;
  error?: { code: string; message: string; fields?: Record<string, string> };
}

let errors: MockInstance<typeof console.error>;
let warnings: MockInstance<typeof console.warn>;
beforeEach(() => {
  errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  warnings = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
});

async function call(w: World, ...args: Parameters<typeof post>): Promise<{ response: Response; answer: Answer; text: string }> {
  const response = await handleEmailSettings(post(...args), w.deps);
  const text = await response.text();
  return { response, answer: JSON.parse(text || 'null') as Answer, text };
}

/** Everything a log line or an answer must never hold. */
function secretsOf(w: World): string[] {
  const hashes = [...w.rows.values()].flatMap((r) => [r.confirmHash ?? '', r.unsubscribeHash]);
  const tokens = w.emails.map((e) => tokenIn(e.text));
  return [ANN.id, ANN.email, BOB.email, ANN.token, RESEND_KEY, SERVICE_KEY, HASH_SECRET, ...hashes, ...tokens].filter(Boolean);
}

function logged(): string {
  return [errors, warnings].flatMap((spy) => spy.mock.calls.map((c: unknown[]) => c.map(String).join(' '))).join('\n');
}

describe('turning the email on', () => {
  it('stores the choices, sends one confirmation to the account email and says only that it is waiting', async () => {
    const w = world();
    const { response, answer, text } = await call(w, ANN, turnOn({ frequency: 'weekly' }));
    expect(response.status).toBe(200);
    expect(answer).toEqual({ ok: true, state: 'waiting' });
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('access-control-allow-origin')).toBe(ORIGIN);
    expect(response.headers.get('access-control-allow-headers')).toContain('authorization');

    const row = w.rows.get(ANN.id);
    expect(row).toMatchObject({ address: ANN.email, frequency: 'weekly', filters: FILTERS, confirmedAt: null, unsubscribedAt: null });
    expect(row?.givenAt).toBe(NOW.toISOString());
    expect(row?.confirmExpires).toBe(new Date(NOW.getTime() + CONFIRM_TTL_DAYS * 86_400_000).toISOString());

    expect(w.emails).toHaveLength(1);
    const [email] = w.emails;
    expect(email).toMatchObject({ to: [ANN.email], from: DEFAULT_FROM, reply_to: DEFAULT_REPLY_TO, subject: SUBJECT });
    const token = tokenIn(email?.text ?? '');
    expect(email?.text).toContain(`${PAGE_URL}#token=${token}`);
    // the database holds the hash of the token and of nothing else, and the token is nowhere else
    expect(row?.confirmHash).toBe(await hashToken(token));
    expect(row?.unsubscribeHash).toMatch(/^[0-9a-f]{64}$/u);
    expect(row?.unsubscribeHash).not.toBe(row?.confirmHash);
    expect(JSON.stringify(w.requests.filter((r) => !r.url.includes('resend')))).not.toContain(token);
    expect(text).not.toContain(token);
    expect(logged()).toBe('');
  });

  it('writes the confirmation email exactly as the lawyer worded it', () => {
    const link = `${PAGE_URL}#token=abc`;
    const { subject, text } = renderEmail('daily', 'ann@example.test', link);
    expect(subject).toBe('Confirm your Misra job alerts');
    expect(text).toBe(
      [
        'Confirm your Misra job alerts',
        '',
        'Someone (hopefully you) turned on daily job alerts for ann@example.test on Misra.',
        '',
        'Click this link to confirm and start receiving new matching roles:',
        'https://ravidorr.github.io/misra/email.html#token=abc',
        '',
        'No emails will be sent unless you click the link above. Once confirmed, your first email will include only roles that appear after confirmation.',
        '',
        '---',
        'Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel.',
        'If you did not ask for this email, ignore it, and nothing will be sent to you.',
        'Privacy: https://ravidorr.github.io/misra/privacy.html | Terms: https://ravidorr.github.io/misra/terms.html',
        '',
      ].join('\n'),
    );
    expect(renderEmail('weekly', 'x@example.test', 'l').text).toContain('turned on weekly job alerts for x@example.test');
    expect(text).not.toContain('<');
  });

  it('never writes the token to any log line, whatever fails', async () => {
    for (const fault of ['ok', '400', '500', 'network'] as const) {
      const w = world();
      w.mail.fault = fault;
      await call(w, ANN, turnOn());
      const token = tokenIn(w.requests.find((r) => r.url.includes('resend'))?.body ?? ''); /* sent, or refused: the request holds it either way */
      expect(token.length).toBeGreaterThan(20);
      const all = logged();
      expect(all).not.toContain(token);
      expect(all).not.toContain(await hashToken(token));
      errors.mockClear();
    }
  });

  it('sends to a different address only when the user typed it, and keeps the account email out of it', async () => {
    const w = world();
    const { answer } = await call(w, ANN, turnOn({ address: '  Other.Person@Example.test ' }));
    expect(answer.state).toBe('waiting');
    expect(w.emails[0]?.to).toEqual(['Other.Person@Example.test']);
    expect(w.rows.get(ANN.id)?.address).toBe('Other.Person@Example.test');
    expect(w.emails[0]?.text).not.toContain(ANN.email);
  });

  it('uses the account email for an empty or missing address field', async () => {
    for (const address of [undefined, null, '', '   ']) {
      const w = world();
      await call(w, ANN, turnOn({ address }));
      expect(w.emails[0]?.to).toEqual([ANN.email]);
    }
  });

  it('takes the user only from the token: a body that names someone else changes nothing for them', async () => {
    const w = world();
    await call(w, ANN, turnOn({ user_id: BOB.id, userId: BOB.id, email: BOB.email, p_user_id: BOB.id }));
    expect([...w.rows.keys()]).toEqual([ANN.id]);
    expect(w.emails[0]?.to).toEqual([ANN.email]);
    const start = w.requests.find((r) => r.url.endsWith('/subscribe_start'));
    expect(JSON.parse(start?.body ?? '{}')).toMatchObject({ p_user_id: ANN.id });
  });

  it('asks the database as the service role and Resend with the key, over fixed addresses', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const rpc = w.requests.filter((r) => r.url.startsWith(`${BASE}/rest/v1/rpc/`));
    expect(rpc.map((r) => r.url.split('/').pop())).toEqual(['confirm_email_reserve', 'subscribe_start']);
    for (const request of rpc) expect(request.headers.authorization).toBe(`Bearer ${SERVICE_KEY}`);
    const reserve = JSON.parse(rpc[0]?.body ?? '{}') as Record<string, unknown>;
    expect(reserve).toMatchObject({ p_day: '2030-01-02', p_user_limit: USER_DAILY_LIMIT, p_address_limit: ADDRESS_DAILY_LIMIT, p_daily_cap: DAILY_CAP });
    // the counters hold keyed hashes: never the user id or the address
    expect(JSON.stringify(reserve)).not.toContain(ANN.id);
    expect(JSON.stringify(reserve)).not.toContain(ANN.email);
    const resend = w.requests.find((r) => r.url === 'https://api.resend.com/emails');
    expect(resend?.headers.authorization).toBe(`Bearer ${RESEND_KEY}`);
  });

  it('restarts a subscription that was unsubscribed: a new consent that must be confirmed again', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const row = w.rows.get(ANN.id);
    if (row) {
      row.confirmedAt = '2030-01-02T09:30:00.000Z';
    }
    await call(w, ANN, { action: 'turn_off' });
    expect(w.rows.get(ANN.id)?.unsubscribedAt).not.toBeNull();
    const { answer } = await call(w, ANN, turnOn({ frequency: 'weekly' }));
    expect(answer.state).toBe('waiting');
    expect(w.rows.get(ANN.id)).toMatchObject({ unsubscribedAt: null, confirmedAt: null, frequency: 'weekly', filters: FILTERS });
    expect(w.emails).toHaveLength(2);
    expect(tokenIn(w.emails[1]?.text ?? '')).not.toBe(tokenIn(w.emails[0]?.text ?? ''));
  });

  it('asks again for a new link while waiting, with fresh tokens', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const first = w.rows.get(ANN.id)?.confirmHash;
    await call(w, ANN, turnOn());
    expect(w.rows.get(ANN.id)?.confirmHash).not.toBe(first);
    expect(w.emails).toHaveLength(2);
  });

  it('leaves a running email alone when it is asked for the same address again, and uses no place', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const row = w.rows.get(ANN.id);
    if (row) row.confirmedAt = '2030-01-02T09:30:00.000Z';
    const before = JSON.stringify(row);
    const { answer } = await call(w, ANN, turnOn({ frequency: 'weekly', address: ANN.email.toUpperCase() }));
    expect(answer).toEqual({ ok: true, state: 'on' });
    expect(JSON.stringify(w.rows.get(ANN.id))).toBe(before);
    expect(w.emails).toHaveLength(1);
    expect([...w.limits.values()].reduce((a, b) => a + b, 0)).toBe(2); // one user place and one address place, from the first call
    expect(w.cap.value).toBe(1);
  });

  it('needs a new confirmation for a new address, even while running', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const row = w.rows.get(ANN.id);
    if (row) row.confirmedAt = '2030-01-02T09:30:00.000Z';
    const { answer } = await call(w, ANN, turnOn({ address: 'new@example.test' }));
    expect(answer.state).toBe('waiting');
    expect(w.rows.get(ANN.id)).toMatchObject({ address: 'new@example.test', confirmedAt: null });
    expect(w.emails.at(-1)?.to).toEqual(['new@example.test']);
  });
});

describe('the signup switch (DIGEST_SIGNUP_ENABLED)', () => {
  it('refuses to create anything while it is off: no row, no email, no place used', async () => {
    const w = world({ config: { signupEnabled: false } });
    const { response, answer } = await call(w, ANN, turnOn());
    expect(response.status).toBe(403);
    expect(answer.error?.code).toBe('signup_closed');
    expect(w.rows.size).toBe(0);
    expect(w.emails).toHaveLength(0);
    expect(w.limits.size).toBe(0);
    expect(w.requests.map((r) => r.url)).toEqual([`${BASE}/auth/v1/user`]);
  });

  it('still lets a user turn it off', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    w.deps.config = { ...w.deps.config, ...{ signupEnabled: false } } as typeof w.deps.config;
    const { answer } = await call(w, ANN, { action: 'turn_off' });
    expect(answer).toEqual({ ok: true, status: 'unsubscribed' });
  });

  it.each([undefined, '', 'TRUE', 'yes', '1', 'false'])('is on only for exactly "true" (%s)', (value) => {
    const loaded = loadConfig((name) => ({ ...ENV, DIGEST_SIGNUP_ENABLED: value })[name]);
    expect('config' in loaded && loaded.config.signupEnabled).toBe(false);
  });

  it('is on for "true"', () => {
    const loaded = loadConfig((name) => ({ ...ENV, DIGEST_SIGNUP_ENABLED: ' true ' })[name]);
    expect('config' in loaded && loaded.config.signupEnabled).toBe(true);
  });
});

describe('abuse limits', () => {
  it('stops a user after three confirmation emails a day, and a refused request uses nothing', async () => {
    const w = world();
    const answers = [];
    for (let i = 0; i < 5; i += 1) answers.push(await call(w, ANN, turnOn()));
    expect(answers.map((a) => a.response.status)).toEqual([200, 200, 200, 429, 429]);
    expect(answers[3]?.answer.error?.code).toBe('rate_limited');
    expect(answers[3]?.answer.error?.message).toContain('3 confirmation emails today');
    expect(answers[3]?.response.headers.get('retry-after')).toBe(String(15 * 3600));
    expect(w.emails).toHaveLength(USER_DAILY_LIMIT);
    expect(w.cap.value).toBe(USER_DAILY_LIMIT);
  });

  it('stops mail to one address after three a day, from any number of users, whatever its case', async () => {
    const w = world();
    const target = 'victim@example.test';
    const results = [
      await call(w, ANN, turnOn({ address: target })),
      await call(w, BOB, turnOn({ address: target.toUpperCase() })),
      await call(w, CAROL, turnOn({ address: target })),
      await call(w, CAROL, turnOn({ address: ` ${target}` })),
    ];
    expect(results.map((r) => r.response.status)).toEqual([200, 200, 200, 429]);
    expect(results[3]?.answer.error?.code).toBe('address_limited');
    expect(w.emails).toHaveLength(ADDRESS_DAILY_LIMIT);
    // the refused request gave back what it had taken from the user
    expect([...w.limits.entries()].filter(([key]) => key.endsWith(ANN.id)).length).toBe(0);
    const carolPlaces = w.limits.size;
    expect(carolPlaces).toBe(1 + 3); // three users' places and the address's
  });

  it('keeps a global daily cap on all of these emails and says when to come back', async () => {
    const w = world();
    w.cap.value = DAILY_CAP;
    const { response, answer } = await call(w, ANN, turnOn());
    expect(response.status).toBe(503);
    expect(answer.error?.code).toBe('daily_cap_reached');
    expect(response.headers.get('retry-after')).toBe(String(15 * 3600));
    expect(w.emails).toHaveLength(0);
    expect(w.rows.size).toBe(0);
    expect(w.limits.size === 0 || [...w.limits.values()].every((n) => n === 0)).toBe(true); // a refusal uses no place
  });

  it('counts a new day from zero', async () => {
    const w = world();
    for (let i = 0; i < 3; i += 1) await call(w, ANN, turnOn());
    const tomorrow = world({ now: new Date('2030-01-03T00:00:01.000Z') });
    for (const [key, value] of w.limits) tomorrow.limits.set(key, value);
    tomorrow.cap.value = w.cap.value;
    expect((await call(tomorrow, ANN, turnOn())).response.status).toBe(200);
  });

  it('gives a place back only when Resend definitely refused the email', async () => {
    for (const fault of ['400', '422', '429'] as const) {
      const w = world();
      w.mail.fault = fault;
      const { response, answer, text } = await call(w, ANN, turnOn());
      expect(response.status).toBe(502);
      expect(answer.error?.code).toBe('send_failed');
      expect([...w.limits.values()].every((n) => n === 0)).toBe(true);
      expect(w.cap.value).toBe(0);
      expect(text).not.toContain(ANN.email);
      expect(logged()).toContain(`Resend answered ${fault}`);
      expect(logged()).not.toContain('secret detail');
      expect(logged()).not.toContain(ANN.email);
      // so the user can ask again as often as the limit allows
      w.mail.fault = 'ok';
      expect((await call(w, ANN, turnOn())).response.status).toBe(200);
      errors.mockClear();
    }
  });

  it('keeps the place when the outcome is unknown (a 5xx, or no answer), and says so honestly', async () => {
    for (const fault of ['500', '503', 'network'] as const) {
      const w = world();
      w.mail.fault = fault;
      const { response, answer } = await call(w, ANN, turnOn());
      expect(response.status).toBe(502);
      expect(answer.error?.code).toBe('send_unconfirmed');
      expect(answer.error?.message).toContain('could not confirm that the email was sent');
      expect(w.cap.value).toBe(1);
      expect([...w.limits.values()].reduce((a, b) => a + b, 0)).toBe(2);
    }
  });

  it('fails closed when the counters cannot be reached: nothing is stored or sent', async () => {
    const w = world();
    w.fault.current = 'reserve-down';
    const { response, answer } = await call(w, ANN, turnOn());
    expect(response.status).toBe(500);
    expect(answer.error?.code).toBe('server_error');
    expect(w.emails).toHaveLength(0);
    expect(w.rows.size).toBe(0);
    expect(logged()).toBe('email-settings: failed confirm_email_reserve answered 500');
  });

  it('gives the place back when the subscription could not be stored, and sends nothing', async () => {
    const w = world();
    w.fault.current = 'start-down';
    const { response } = await call(w, ANN, turnOn());
    expect(response.status).toBe(500);
    expect(w.emails).toHaveLength(0);
    expect(w.cap.value).toBe(0);
    expect([...w.limits.values()].every((n) => n === 0)).toBe(true);
  });

  it('survives a failed give-back, which only makes a limit reached a little earlier', async () => {
    const w = world();
    w.mail.fault = '422';
    w.fault.current = 'release-down';
    const { answer } = await call(w, ANN, turnOn());
    expect(answer.error?.code).toBe('send_failed');
    expect(logged()).toContain('could not give the place back confirm_email_release answered 500');
  });
});

describe('turning the email off', () => {
  it('does the one-click unsubscribe for the signed-in user: the SQL function does the clean-up', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    const row = w.rows.get(ANN.id);
    if (row) {
      row.confirmedAt = '2030-01-02T09:30:00.000Z';
      row.emailed = 4;
    }
    const { response, answer } = await call(w, ANN, { action: 'turn_off' });
    expect(response.status).toBe(200);
    expect(answer).toEqual({ ok: true, status: 'unsubscribed' });
    expect(w.rows.get(ANN.id)).toMatchObject({
      address: ANN.email,
      givenAt: NOW.toISOString(),
      confirmedAt: '2030-01-02T09:30:00.000Z', // the record is kept
      unsubscribedAt: NOW.toISOString(),
      filters: {},
      frequency: 'daily',
      confirmHash: null,
      emailed: 0,
    });
    const off = w.requests.filter((r) => r.url.endsWith('/unsubscribe_user'));
    expect(off.map((r) => JSON.parse(r.body) as unknown)).toEqual([{ p_user_id: ANN.id, p_now: NOW.toISOString() }]);
    expect(w.emails).toHaveLength(1); // turning off sends nothing
  });

  it('is not an error the second time, nor for a user with no subscription, and affects nobody else', async () => {
    const w = world();
    await call(w, ANN, turnOn());
    await call(w, BOB, turnOn());
    await call(w, ANN, { action: 'turn_off' });
    expect((await call(w, ANN, { action: 'turn_off' })).answer).toEqual({ ok: true, status: 'already_unsubscribed' });
    expect((await call(w, CAROL, { action: 'turn_off' })).answer).toEqual({ ok: true, status: 'not_subscribed' });
    expect(w.rows.get(BOB.id)?.unsubscribedAt).toBeNull();
  });

  it('works when the mail secrets are missing', async () => {
    const w = world({ config: { mail: undefined } });
    expect((await call(w, ANN, { action: 'turn_off' })).response.status).toBe(200);
  });

  it('says it could not when the database cannot be reached', async () => {
    const w = world();
    w.fault.current = 'off-down';
    const { response, answer } = await call(w, ANN, { action: 'turn_off' });
    expect(response.status).toBe(500);
    expect(answer.ok).toBe(false);
  });
});

describe('who may call it, and how', () => {
  it('refuses a request without a token, with one Auth does not accept, or when Auth cannot be asked', async () => {
    const w = world();
    expect((await call(w, null, turnOn())).response.status).toBe(401);
    expect((await call(w, { token: 'not-a-known-token' }, turnOn())).response.status).toBe(401);
    expect((await call(w, { token: 'bad token with spaces' }, turnOn())).response.status).toBe(401);
    for (const fault of ['auth-down', 'auth-500', 'auth-garbage'] as const) {
      w.fault.current = fault;
      const { response, answer } = await call(w, ANN, turnOn());
      expect(response.status).toBe(503);
      expect(answer.error?.code).toBe('service_unavailable');
    }
    expect(w.rows.size).toBe(0);
    expect(w.emails).toHaveLength(0);
    expect(w.limits.size).toBe(0);
  });

  it('refuses an account with no id Auth vouches for', async () => {
    const w = world();
    w.fakeFetch.mockImplementationOnce(() => Promise.resolve(Response.json({ id: 'not-a-uuid', role: 'authenticated' })));
    expect((await call(w, ANN, turnOn())).response.status).toBe(401);
    w.fakeFetch.mockImplementationOnce(() => Promise.resolve(Response.json({ id: ANN.id, role: 'anon' })));
    expect((await call(w, ANN, turnOn())).response.status).toBe(401);
  });

  it('needs an address when the account has none and none was typed', async () => {
    const w = world();
    w.fakeFetch.mockImplementationOnce(() => Promise.resolve(Response.json({ id: ANN.id, role: 'authenticated', email: ' ' })));
    const { response, answer } = await call(w, ANN, turnOn());
    expect(response.status).toBe(400);
    expect(answer.error?.fields?.address).toBe('Enter the email address to send to.');
    expect(w.emails).toHaveLength(0);
  });

  it('answers the dashboard origin and nobody else', async () => {
    const w = world();
    const wrong = await handleEmailSettings(post(ANN, turnOn(), { headers: { origin: 'https://evil.test', 'content-type': 'application/json', authorization: `Bearer ${ANN.token}` } }), w.deps);
    expect(wrong.status).toBe(403);
    expect(wrong.headers.get('access-control-allow-origin')).toBeNull();
    const preflight = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'OPTIONS', headers: { origin: ORIGIN } }), w.deps);
    expect(preflight.status).toBe(204);
    expect(preflight.headers.get('access-control-allow-origin')).toBe(ALLOWED_ORIGIN);
    expect(preflight.headers.get('access-control-allow-methods')).toBe('POST, OPTIONS');
    const get = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'GET', headers: { origin: ORIGIN } }), w.deps);
    expect(get.status).toBe(405);
    expect(get.headers.get('allow')).toBe('POST, OPTIONS');
    expect(w.requests).toEqual([]);
  });

  it('refuses a body that is not small JSON', async () => {
    const w = world();
    const headers = { origin: ORIGIN, authorization: `Bearer ${ANN.token}` };
    const plain = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'POST', headers: { ...headers, 'content-type': 'text/plain' }, body: '{}' }), w.deps);
    expect(plain.status).toBe(415);
    const json = { ...headers, 'content-type': 'application/json' };
    const big = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'POST', headers: json, body: JSON.stringify({ x: 'y'.repeat(3000) }) }), w.deps);
    expect(big.status).toBe(413);
    const lying = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'POST', headers: { ...json, 'content-length': '99999' }, body: '{}' }), w.deps);
    expect(lying.status).toBe(413);
    for (const body of ['not json', '[]', 'null', '"x"']) {
      const bad = await handleEmailSettings(new Request(`${BASE}/f`, { method: 'POST', headers: json, body }), w.deps);
      expect(bad.status).toBe(400);
    }
    expect((await call(w, ANN, { action: 'delete_everything' })).answer.error?.code).toBe('invalid_request');
    expect((await call(w, ANN, {})).answer.error?.code).toBe('invalid_request');
    expect(w.emails).toHaveLength(0);
  });

  it('says plainly when it is not set up', async () => {
    const w = world();
    w.deps.config = undefined;
    const { response } = await call(w, ANN, turnOn());
    expect(response.status).toBe(500);
    expect(errors.mock.calls.length).toBe(1);
  });

  it('says plainly when only the mail secrets are missing, and sends nothing', async () => {
    const w = world({ config: { mail: undefined } });
    const { response, answer } = await call(w, ANN, turnOn());
    expect(response.status).toBe(500);
    expect(answer.error?.message).toBe('The email service is not set up yet.');
    expect(w.rows.size).toBe(0);
    expect(w.limits.size).toBe(0);
  });

  it('rejects input the function cannot use, field by field', async () => {
    const w = world();
    const { response, answer } = await call(w, ANN, { action: 'turn_on', address: 'not an address', frequency: 'hourly', filters: 'all' });
    expect(response.status).toBe(400);
    expect(Object.keys(answer.error?.fields ?? {}).sort()).toEqual(['address', 'filters', 'frequency']);
    expect(w.requests.map((r) => r.url)).toEqual([`${BASE}/auth/v1/user`]);
  });

  it('never puts a secret in a log line or an answer, whatever fails', async () => {
    for (const setup of [(w: World) => (w.mail.fault = '422'), (w: World) => (w.mail.fault = '500'), (w: World) => (w.fault.current = 'start-down'), (w: World) => (w.fault.current = 'reserve-down')]) {
      const w = world();
      setup(w);
      const { text } = await call(w, ANN, turnOn());
      for (const secret of secretsOf(w)) {
        expect(text).not.toContain(secret);
        expect(logged()).not.toContain(secret);
      }
    }
  });
});

describe('the pieces', () => {
  it('hashes the user, the address and the day into different keys that do not show the value', async () => {
    const a = await hashKey('s'.repeat(16), '2030-01-02', 'user', 'x');
    expect(a).toMatch(/^[0-9a-f]{64}$/u);
    expect(a).not.toBe(await hashKey('s'.repeat(16), '2030-01-02', 'address', 'x'));
    expect(a).not.toBe(await hashKey('s'.repeat(16), '2030-01-03', 'user', 'x'));
    expect(a).not.toBe(await hashKey('t'.repeat(16), '2030-01-02', 'user', 'x'));
    expect(a).toBe(await hashKey('s'.repeat(16), '2030-01-02', 'user', 'x'));
  });

  it('reads the secrets and names what is missing, never a value', () => {
    expect(loadConfig(() => undefined)).toEqual({ missing: ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'] });
    const loaded = loadConfig((name) => ENV[name]);
    expect('config' in loaded && loaded.mailMissing).toEqual([]);
    expect('config' in loaded && loaded.config.mail).toEqual({ resendApiKey: RESEND_KEY, from: DEFAULT_FROM, replyTo: DEFAULT_REPLY_TO, hashSecret: HASH_SECRET });
    const custom = loadConfig((name) => ({ ...ENV, DIGEST_FROM_EMAIL: 'a@b.test', DIGEST_REPLY_TO_EMAIL: 'r@b.test' })[name]);
    expect('config' in custom && custom.config.mail?.from).toBe('a@b.test');
    expect('config' in custom && custom.config.mail?.replyTo).toBe('r@b.test');
    const broken = loadConfig((name) => ({ ...ENV, RESEND_API_KEY: '', IP_HASH_SECRET: 'short', DIGEST_FROM_EMAIL: 'x y', DIGEST_REPLY_TO_EMAIL: 'nope' })[name]);
    expect('config' in broken && broken.config.mail).toBeUndefined();
    expect('config' in broken && broken.mailMissing).toEqual(['RESEND_API_KEY', 'DIGEST_FROM_EMAIL', 'DIGEST_REPLY_TO_EMAIL', 'IP_HASH_SECRET']);
  });

  it('checks the address like the table does', () => {
    expect(checkAddress(undefined)).toEqual({ address: undefined });
    expect(checkAddress('a@b.co')).toEqual({ address: 'a@b.co' });
    for (const bad of [5, 'a@b', 'a b@c.de', 'a@@b.co', 'a,b@c.de', '<a@b.co>', 'a@b.co\nBcc: x@y.zz', `${'a'.repeat(250)}@b.co`]) {
      expect(checkAddress(bad), String(bad)).toHaveProperty('error');
    }
  });

  it('keeps exactly the four filters, cleaned and cut to size', () => {
    expect(checkFilters({})).toEqual({ regions: [], levels: [], role_types: [], keywords: '' });
    expect(checkFilters({ regions: [' Tel  Aviv ', 'Tel Aviv'], levels: ['Intern'], role_types: [], keywords: '  growth\n marketing ' })).toEqual({
      regions: ['Tel Aviv'],
      levels: ['Intern'],
      role_types: [],
      keywords: 'growth marketing',
    });
    for (const bad of [null, [], 'x', { company: 'x' }, { regions: 'x' }, { regions: [1] }, { regions: [''] }, { regions: ['x'.repeat(81)] }, { levels: Array.from({ length: 31 }, (_, i) => `n${String(i)}`) }, { keywords: 5 }, { keywords: 'k'.repeat(201) }]) {
      expect(checkFilters(bad), JSON.stringify(bad)).toBeUndefined();
    }
  });

  it('judges every address on its own, however many came before in the same process', () => {
    const late = `${'a'.repeat(30)}\u202e@example.test`;
    const early = 'a\u202eb@example.test';
    for (let i = 0; i < 3; i += 1) {
      expect(checkAddress(late)).toHaveProperty('error');
      expect(checkAddress(early)).toHaveProperty('error');
    }
    expect(checkAddress('a b@example.test')).toHaveProperty('error');
  });

  it('validates a request', () => {
    expect(validateRequest({ action: 'turn_off', frequency: 'bad' })).toEqual({ ok: true, request: { action: 'turn_off' } });
    expect(validateRequest({ action: 'nope' })).toBe('unknown_action');
    expect(validateRequest({ action: 'turn_on', frequency: 'weekly', filters: FILTERS, address: 'a@b.co' })).toEqual({
      ok: true,
      request: { action: 'turn_on', address: 'a@b.co', frequency: 'weekly', filters: FILTERS },
    });
  });
});

const ENV: Record<string, string | undefined> = {
  SUPABASE_URL: BASE,
  SUPABASE_SERVICE_ROLE_KEY: SERVICE_KEY,
  RESEND_API_KEY: RESEND_KEY,
  IP_HASH_SECRET: HASH_SECRET,
};
