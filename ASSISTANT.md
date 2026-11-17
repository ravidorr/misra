# Connect your assistant

Connect your own AI assistant, such as Claude or ChatGPT, and it can search the roles on Misra and read one posting in full, for example to compare roles or draft a cover letter.

This is optional: the dashboard and the job alerts work without it. It is free and read-only, with no account and no sign-in. Your assistant sends Misra only the search words and the posting it asks for. Misra does not keep them and does not log your address (only a daily, non-reversible hash is used to count rate limits; see the privacy notice). Your CV stays in your assistant.

Select this address and copy it. You paste it in the steps below. Before you connect, read the [terms](TERMS.md) and the [privacy notice](PRIVACY.md).

```text
https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/mcp
```

## Claude

In claude.ai or the desktop app, on Free (one custom connector), Pro or Max:

1. Open **Customize**, then **Connectors**.
2. Click **+ Add**, then **Add custom connector**.
3. Enter the name `Misra`, paste the address and click **Continue**.
4. Under **Authentication**, choose **No sign in**, then click **Add**.

On Team and Enterprise, an owner adds it first in **Organization settings**, **Connectors** (**Add**, **Custom**, **Web**). Each member then opens **Customize**, **Connectors**, finds Misra and clicks **Connect**. The mobile apps use it once it is added on the web.

## ChatGPT

On chatgpt.com. It depends on your plan, and may need **Developer mode** under **Settings**, **Security and login**. If you do not see these options, your account does not have them.

1. Open <https://chatgpt.com/plugins> and click the plus button.
2. Choose **Add custom MCP server**.
3. Enter the name `Misra` and paste the address as **Server URL**.
4. Set authentication to **No authentication**.
5. Read the warning, click **I understand and want to continue**, then **Create as a plugin**.
6. Install it. In a chat, type `@` and pick Misra.

## Other assistants

Any assistant that can add a "remote MCP server" or "custom connector" takes the same address, with no sign-in. If it asks for a type, choose "streamable HTTP". For developers, in Claude Code:

```sh
claude mcp add --transport http misra https://dncodqeqpoqwcxdctzco.supabase.co/functions/v1/mcp
```

Menu names change. If yours differ, look for "connectors" or "MCP" in the settings.

## Try it

- "Find junior product roles in Tel Aviv on Misra."
- "Read the first one in full and tell me what it asks for."
- "Draft a short cover letter for it. Here is my CV: ..."

## What your assistant can see

Each role has the company, title, place, level, role type, region, the start of the posting and a link to the employer's page.

### What your assistant can see—and the rules for using it

- **Public job data only:** Your assistant can search open listings and read the full description of one job at a time (up to 20,000 characters, for open roles or roles closed less than 30 days). It cannot see your Misra account, email address, stars, "Not for me" choices, or saved filters.
- **Your data stays in your assistant:** Connecting Misra requires no sign-in and sends nothing personal to us—your resume and prompts stay inside your own AI assistant. To enforce daily limits (100 searches and 15 full-job reads per day), we store only a daily, non-reversible hash of your network address, which is deleted during the next daily cleanup.
- **Personal job search only:** All job postings and descriptions belong to the respective employers and are provided "as is". You may use this connection only for your own personal job search—bulk copying, republishing, or training models on the postings is not allowed.
- **Check before you apply:** Misra is a free, unofficial project and is not affiliated with any employer. Your AI assistant—not Misra—is responsible for anything it writes (such as cover letters or summaries). Always verify details on the employer's own career page before applying.
- By connecting your assistant, you agree to the [Terms](https://ravidorr.github.io/misra/terms.html) and [Privacy Notice](https://ravidorr.github.io/misra/privacy.html).

## Limits

There is a daily limit for each connection, and one for everyone on reading full postings. When a limit is reached, your assistant tells you. It resets the next day (UTC).

## If it does not work

- Check that you pasted the whole address. On a phone it wraps over several lines; paste it as it is, with no spaces added.
- A work or school account may need an administrator to allow it.
- Ask your assistant to "use the Misra connector".

Still stuck? Write to <raanan@avidor.org>.

See the [privacy notice](PRIVACY.md) and the [terms](TERMS.md).
