// miniMarkdown — the Trace tab's Formatted view (9 Nov 2026). An answer's markdown (headings, paragraphs, bold,
// italic, inline code, bullet and numbered lists, pipe tables, fenced code) read into plain data blocks. It returns
// data, never HTML: the page draws the blocks as React elements, so text a model wrote can't become markup.
//
//   {t:'h', level, inline} | {t:'p', inline} | {t:'ul', items: inline[]} | {t:'ol', items: inline[]}
//   {t:'table', head: inline[], rows: inline[][]} | {t:'code', text}
//   inline = [{t:'text'|'b'|'i'|'code', text}]

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;

export function parseInline(text) {
  const out = [];
  for (const part of String(text || '').split(INLINE)) {
    if (!part) continue;
    if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) out.push({ t: 'code', text: part.slice(1, -1) });
    else if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) out.push({ t: 'b', text: part.slice(2, -2) });
    else if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) out.push({ t: 'i', text: part.slice(1, -1) });
    else out.push({ t: 'text', text: part });
  }
  return out;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const FENCE = /^\s*```/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const isRow = (line) => /^\s*\|/.test(line);
const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => parseInline(c.trim()));

export function parseMarkdown(text) {
  const lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const special = (k) => FENCE.test(lines[k]) || HEADING.test(lines[k]) || BULLET.test(lines[k])
    || NUMBERED.test(lines[k]) || (isRow(lines[k]) && k + 1 < lines.length && TABLE_RULE.test(lines[k + 1]));
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (FENCE.test(line)) {
      const body = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) { body.push(lines[i]); i += 1; }
      i += 1;
      out.push({ t: 'code', text: body.join('\n') });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) { out.push({ t: 'h', level: h[1].length, inline: parseInline(h[2].trim()) }); i += 1; continue; }
    if (isRow(line) && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && isRow(lines[i])) { rows.push(cells(lines[i])); i += 1; }
      out.push({ t: 'table', head, rows });
      continue;
    }
    const list = [[BULLET, 'ul'], [NUMBERED, 'ol']].find(([rx]) => rx.test(line));
    if (list) {
      const [rx, kind] = list;
      const items = [];
      while (i < lines.length && rx.test(lines[i])) { items.push(parseInline(rx.exec(lines[i])[1].trim())); i += 1; }
      out.push({ t: kind, items });
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !special(i)) { para.push(lines[i].trim()); i += 1; }
    if (para.length) out.push({ t: 'p', inline: parseInline(para.join(' ')) });
  }
  return out;
}
