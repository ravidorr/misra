import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';


import { SendFailed, SendRefused, sendMail } from '../supabase/functions/_shared/resend.ts';
import type { Mail } from '../supabase/functions/_shared/resend.ts';

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;
const MAIL = { from: 'Misra <a@b.test>', to: 'c@d.test', subject: 's', text: 't' };
const send = (response: Response, mail: Mail = MAIL): { fetcher: Mock<Fetcher>; done: Promise<void> } => {
  const fetcher = vi.fn<Fetcher>(() => Promise.resolve(response));
  return { fetcher, done: sendMail(fetcher, 'key', mail) };
};

describe('sendMail', () => {
  it('posts one message to Resend with the key, and Reply-To only when given', async () => {
    const first = send(Response.json({ id: '1' }));
    await first.done;
    const [url, init] = first.fetcher.mock.calls[0] ?? [];
    expect(url).toBe('https://api.resend.com/emails');
    expect((init?.headers as Record<string, string>).authorization).toBe('Bearer key');
    expect(JSON.parse(init?.body as string)).toEqual({ from: MAIL.from, to: ['c@d.test'], subject: 's', text: 't' });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    const second = send(Response.json({ id: '2' }), { ...MAIL, replyTo: 'v@w.test' });
    await second.done;
    expect(JSON.parse(second.fetcher.mock.calls[0]?.[1].body as string)).toMatchObject({ reply_to: 'v@w.test' });
  });

  it('throws SendRefused for a 4xx and SendFailed (not SendRefused) for a 5xx', async () => {
    await expect(send(new Response('{}', { status: 422 })).done).rejects.toBeInstanceOf(SendRefused);
    const failed = send(new Response(null, { status: 503 })).done;
    await expect(failed).rejects.toBeInstanceOf(SendFailed);
    await expect(failed).rejects.not.toBeInstanceOf(SendRefused);
    await expect(send(new Response('{}', { status: 422 })).done).rejects.toBeInstanceOf(SendFailed);
  });

  it('puts the status and a short error name in the message and never the rest of the answer', async () => {
    const echo = '{"name":"validation_error","message":"The reply_to dana@example.test is not valid"}';
    await expect(send(new Response(echo, { status: 422 })).done).rejects.toThrow(/^Resend answered 422 validation_error$/u);
    await expect(send(new Response('{"name":"Bad Name!"}', { status: 400 })).done).rejects.toThrow(/^Resend answered 400$/u);
    await expect(send(new Response('not json', { status: 500 })).done).rejects.toThrow(/^Resend answered 500$/u);
  });
});
