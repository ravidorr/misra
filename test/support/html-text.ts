// The visible text of an email's HTML, read with a small tokenizer instead of regular expressions: a tag runs from `<` to
// the `>` that is not inside a quoted attribute value, the head is skipped, block tags are word breaks, inline tags
// vanish, and each entity is decoded once. The tests compare this text with the plain-text email.
const BLOCK = new Set(['p', 'td', 'tr', 'table', 'div', 'h1', 'body', 'html']);
const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' ' };

function tagEnd(html: string, start: number): number {
  let quote = '';
  for (let at = start + 1; at < html.length; at += 1) {
    const char = html.charAt(at);
    if (quote !== '') {
      if (char === quote) quote = '';
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (char === '>') {
      return at;
    }
  }
  return html.length;
}

export function htmlText(html: string): string {
  let out = '';
  let at = 0;
  while (at < html.length) {
    const char = html.charAt(at);
    if (char === '<') {
      const end = tagEnd(html, at);
      const tag = html.slice(at + 1, end);
      const name = /^\/?([a-z0-9]+)/iu.exec(tag)?.[1]?.toLowerCase() ?? '';
      if (name === 'head' && !tag.startsWith('/')) {
        const close = html.indexOf('</head>', end);
        at = close === -1 ? html.length : close + '</head>'.length;
        continue;
      }
      if (BLOCK.has(name)) out += ' ';
      at = end + 1;
    } else if (char === '&') {
      const entity = /^&(#?[a-z0-9]+);/iu.exec(html.slice(at, at + 8));
      const decoded = entity?.[1] === undefined ? undefined : ENTITIES[entity[1]];
      out += decoded ?? '&';
      at += decoded === undefined ? 1 : (entity?.[0].length ?? 1);
    } else {
      out += char;
      at += 1;
    }
  }
  return out.split(/\s+/u).filter((word) => word !== '').join(' ');
}
