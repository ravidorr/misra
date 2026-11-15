# Email texts

The exact texts of the two emails, from the lawyer's review of 2026-11-15. Step 6.2 (the confirmation email) and step 6.3
(the digest) copy them line for line, line breaks included. `{{...}}` marks a value the code fills in. Both emails are
plain text, are sent from `Misra <digest@send.avidor.org>`, and set `Reply-To: raanan@avidor.org`, because the sending
address cannot receive mail and the lawyer said people may reply "Remove". `tests/test_legal_wording.py` pins this file.

## Confirmation email

Subject: `Confirm your Misra job alerts`

```text
Confirm your Misra job alerts

Someone (hopefully you) turned on the {{schedule}} job digest for {{user_email}} on Misra.

Click this link to confirm and start receiving new matching roles:
{{confirm_url}}

No emails will be sent unless you click the link above. Once confirmed, your first digest will include only roles that appear after confirmation.

---
Misra (https://ravidorr.github.io/misra/) is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org).
If you did not ask for this email, ignore it, and nothing will be sent to you.
Privacy: https://github.com/ravidorr/misra/blob/main/PRIVACY.md | Terms: https://github.com/ravidorr/misra/blob/main/TERMS.md
```

## Digest footer

This footer ends every digest email, after the list of roles.

```text
---
You are receiving this {{schedule}} digest because you confirmed job alerts on Misra (https://ravidorr.github.io/misra/).
Misra is a free, non-commercial project run by Raanan Avidor (raanan@avidor.org). Job listings are collected automatically from public career pages and are provided as-is without guarantee that they are complete or still open.

Unsubscribe in one click (no sign-in needed):
{{unsubscribe_url}}

To change your filters or delete your account and all stored data immediately, sign in at https://ravidorr.github.io/misra/
Privacy: https://github.com/ravidorr/misra/blob/main/PRIVACY.md | Terms: https://github.com/ravidorr/misra/blob/main/TERMS.md
```

## Headers of every digest email

Passed in the `headers` field of the Resend payload, so that Gmail and Yahoo show their own Unsubscribe button (RFC 8058).
`{{raw_token}}` is the subscriber's unsubscribe token (see the design note in `PLAN.md` step 6.3).

```text
List-Unsubscribe: <https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/unsubscribe?token={{raw_token}}>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
```
