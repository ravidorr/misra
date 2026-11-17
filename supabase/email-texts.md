# Email texts

The exact texts of the two emails, from the lawyer's review of 2026-11-15 and the editorial round of 2026-11-16. Step 6.2 (the confirmation email) and step 6.3
(the digest) copy them line for line, line breaks included. `{{...}}` marks a value the code fills in. Both emails are
sent as plain text with an HTML version that carries exactly these words, only laid out
(`tests/test_email_html.py` and `test/email-html.test.ts` check it), are sent from `Misra <digest@send.avidor.org>`, and set `Reply-To: raanan@avidor.org`, because the sending
address cannot receive mail and the lawyer said people may reply "Remove". The postal address in the footers is the maintainer's work address, added because Resend's acceptable-use policy asks bulk email to carry a valid address and company name (the lawyer's texts had only the email address; he should approve the addition). `tests/test_legal_wording.py` pins this file.

## Confirmation email

Subject: `Confirm your Misra job alerts`

```text
Confirm your Misra job alerts

Someone (hopefully you) turned on {{schedule}} job alerts for {{user_email}} on Misra.

Open this link and press "Confirm my email" to start receiving new matching roles:
{{confirm_url}}
The link works for 7 days.
If you get more than one of these emails, use the newest one: each new email stops the link in the older ones from working.

No emails will be sent until you press "Confirm my email" on that page. Once confirmed, your first email will include only roles that appear after confirmation.

---
Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel.
If you did not ask for this email, ignore it, and nothing will be sent to you.
Privacy: https://ravidorr.github.io/misra/privacy.html | Terms: https://ravidorr.github.io/misra/terms.html
```

## Digest subject and list

The lawyer's texts cover the footer and the headers; the subject and the list of roles are the project's own plain wording, set by
step 6.3 and pinned by `tests/test_send_digests.py`. `{{roles}}` is `role` for one and `roles` for more. The subject of a weekly
digest starts with `Misra weekly:`. A role with no place leaves out the brackets and the place, and one with no link leaves out the link line.
The list is followed by a blank line and the footer. It holds job listings only, never anything about crawl problems.

Subject (daily): `Misra: {{count}} new matching {{roles}}`

Subject (weekly): `Misra weekly: {{count}} new matching {{roles}}`

```text
{{count}} new {{roles}} matching your filters:

{{company}} - {{title}} ({{location}})
{{url}}
```

## Digest footer

This footer ends every digest email, after the list of roles.

```text
---
You are receiving this {{schedule}} email because you confirmed job alerts on Misra (https://ravidorr.github.io/misra/).
Misra is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org), Ditsa 8, Herzliya 4627825, Israel. Job listings are collected automatically from public career pages and are provided as-is, without guarantee that they are accurate, complete or still open.

Unsubscribe in one click (no sign-in needed):
{{unsubscribe_url}}

To change your filters, or to delete your account and all stored data, sign in at https://ravidorr.github.io/misra/
Privacy: https://ravidorr.github.io/misra/privacy.html | Terms: https://ravidorr.github.io/misra/terms.html
```

## Headers of every digest email

Passed in the `headers` field of the Resend payload, so that Gmail and Yahoo show their own Unsubscribe button (RFC 8058).
`{{token}}` is the subscriber's stateless unsubscribe token, `<subscription id>.<HMAC-SHA256(UNSUBSCRIBE_SECRET, id)>`, which
`send_digests.py` makes for every email and the `unsubscribe` Edge Function verifies by recomputing the MAC (`supabase/README.md`). The function
reads the token from `?t=`; the same URL is the `{{unsubscribe_url}}` of the footer.

```text
List-Unsubscribe: <https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/unsubscribe?t={{token}}>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
```
