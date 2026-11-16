// The HTML version of the confirmation email: the words of `renderEmail`'s text, in the same order, only laid out (table
// layout, inline styles, system fonts, no image, no script, no remote stylesheet). The one link is a button: a link with a
// background and padding, because email clients have no <button>, inside a table cell that also carries the colour and
// padding for Outlook's Word engine. Its label is the link text, the address itself, as the approved text has it. The
// colours are the fixed values of `_shared/email-colors.json`, which a test keeps equal to design-system/tokens.css.
import colors from '../_shared/email-colors.json' with { type: 'json' };

const { light, dark } = colors;
const FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const DARK_CSS =
  ':root{color-scheme:light dark}@media (prefers-color-scheme:dark){' +
  `.bg{background-color:${dark.bg}!important}` +
  `.panel{background-color:${dark.panel}!important;border-color:${dark.line}!important}` +
  `.ink{color:${dark.ink}!important}.muted{color:${dark.muted}!important}` +
  `.accent{color:${dark.accent}!important}.rule{border-color:${dark.line}!important}}`;
const URL_PATTERN = /https?:\/\/[^\s<>"()]*[^\s<>"().,;:]/gu;

export function escapeHtml(text: string): string {
  return text.replace(/&/gu, '&amp;').replace(/</gu, '&lt;').replace(/>/gu, '&gt;').replace(/"/gu, '&quot;').replace(/'/gu, '&#39;');
}

function anchor(url: string, text: string): string {
  if (!/^https?:\/\//u.test(url)) return escapeHtml(text);
  return `<a class="accent" dir="ltr" href="${escapeHtml(url)}" style="color:${light.accent};text-decoration:underline">${escapeHtml(text)}</a>`;
}

/** The text with each web address a link whose text is that address, so no word changes. */
function linkify(text: string): string {
  let out = '';
  let last = 0;
  for (const found of text.matchAll(URL_PATTERN)) {
    out += escapeHtml(text.slice(last, found.index)) + anchor(found[0], found[0]);
    last = found.index + found[0].length;
  }
  return out + escapeHtml(text.slice(last));
}

function button(url: string): string {
  if (!/^https?:\/\//u.test(url)) return `<p style="margin:0 0 16px 0">${escapeHtml(url)}</p>`;
  const label = escapeHtml(url);
  return (
    `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px 0"><tr>` +
    `<td bgcolor="${light.solid}" style="background-color:${light.solid};border-radius:14px;mso-padding-alt:14px 20px">` +
    `<a dir="ltr" href="${escapeHtml(url)}" style="display:block;padding:14px 20px;font-weight:700;line-height:22px;` +
    `color:${light['on-solid']};text-decoration:none;word-break:break-all;overflow-wrap:anywhere">${label}</a></td></tr></table>`
  );
}

/** `text` is the plain-text email and `link` the one address that becomes the button. */
export function renderHtml(subject: string, text: string, link: string): string {
  const lines = text.split('\n').filter((line) => line !== '');
  const [title = '', ...rest] = lines;
  const rule = lines.indexOf('---');
  let body = `<h1 style="margin:0 0 16px 0;font-size:24px;line-height:1.25">${escapeHtml(title)}</h1>`;
  for (const [index, line] of rest.entries()) {
    const position = index + 1;
    if (line === '---') {
      body += `<div class="rule" style="border-top:2px solid ${light.line};height:0;line-height:0;font-size:0;margin:16px 0">&nbsp;</div>`;
    } else if (line === link) {
      body += button(line);
    } else if (position > rule) {
      body += `<p class="muted" style="margin:0 0 16px 0;font-size:14px;color:${light.muted}">${linkify(line)}</p>`;
    } else {
      body += `<p style="margin:0 0 16px 0">${escapeHtml(line)}</p>`;
    }
  }
  return (
    '<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">' +
    `<title>${escapeHtml(subject)}</title><style>${DARK_CSS}</style></head>` +
    `<body class="bg" style="margin:0;padding:0;background-color:${light.bg};-webkit-text-size-adjust:100%">` +
    `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg" style="background-color:${light.bg}"><tr><td align="center" style="padding:24px 16px">` +
    `<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="panel" style="width:100%;max-width:600px;background-color:${light.panel};border:2px solid ${light.line};border-radius:20px">` +
    `<tr><td aria-hidden="true" height="8" bgcolor="${light.lime}" style="height:8px;line-height:8px;font-size:8px;background-color:${light.lime};border-radius:18px 18px 0 0">&nbsp;</td></tr>` +
    `<tr><td class="ink" style="padding:24px 24px 8px 24px;font-family:${FONT};font-size:16px;line-height:1.5;color:${light.ink}">${body}</td></tr></table>` +
    '</td></tr></table></body></html>'
  );
}
