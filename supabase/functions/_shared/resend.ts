// How the functions that email the maintainer (`report-site`, `contact`) send one plain-text message through Resend. Web
// standards only, like the rest of `_shared`. What goes into the request is decided by the caller; nothing here logs.

const RESEND_URL = 'https://api.resend.com/emails';
const REQUEST_TIMEOUT_MS = 10_000;

/** Resend answered with an error (5xx): the email may have been queued. The message holds the status only, so it is safe to log. */
export class SendFailed extends Error {}

/** Resend answered with a refusal (4xx): the email was definitely not sent, so a place in a counter can be given back. */
export class SendRefused extends SendFailed {}

export interface Mail {
  from: string;
  to: string;
  subject: string;
  text: string;
  /** One address, already validated by the caller. */
  replyTo?: string;
}

/**
 * Sends the email. Throws when it was not accepted: `SendRefused` for a 4xx, `SendFailed` for a 5xx (the email may have been queued). The message holds the status only: Resend's own answer can echo what the visitor wrote.
 */
export async function sendMail(
  fetcher: (url: string, init: RequestInit) => Promise<Response>,
  apiKey: string,
  mail: Mail,
): Promise<void> {
  const response = await fetcher(RESEND_URL, {
    method: 'POST',
    headers: { authorization: `Bearer ${apiKey}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      from: mail.from,
      to: [mail.to],
      subject: mail.subject,
      text: mail.text,
      ...(mail.replyTo ? { reply_to: mail.replyTo } : {}),
    }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    const message = `Resend answered ${String(response.status)}`;
    throw response.status < 500 ? new SendRefused(message) : new SendFailed(message);
  }
}
