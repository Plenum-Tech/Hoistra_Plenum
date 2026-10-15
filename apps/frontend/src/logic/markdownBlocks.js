// markdownBlocks — the block structure of an orchestrator answer, as plain data.
//
// Shared by Markdown.jsx (which renders the blocks) and answerCards.js (which arranges them as
// the dashboard). Pure and dependency-free so it can be tested under node. Blocks:
//   { t: 'h', level, body } · { t: 'p', body } · { t: 'list', ordered, start, items[], indents[] }
//   { t: 'table', head[], rows[][] } · { t: 'code', body } · { t: 'quote', body } · { t: 'rule' }

export const splitRow = (line) =>
  line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

export const isDivider = (line) => /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes('-');

const LIST_ITEM = /^\s*([-*+]|\d+[.)])\s+/;

export function parseBlocks(text) {
  const src = String(text == null ? '' : text).replace(/\r\n/g, '\n');
  const lines = src.split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code
    if (/^\s*```/.test(line)) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i += 1; }
      i += 1;
      blocks.push({ t: 'code', body: body.join('\n') });
      continue;
    }

    // pipe table — a header row plus a --- divider
    if (line.includes('|') && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        if (!isDivider(lines[i])) rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ t: 'table', head: head, rows: rows });
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { blocks.push({ t: 'rule' }); i += 1; continue; }

    const h = /^\s*(#{1,6})\s+(.*)$/.exec(line);
    if (h) { blocks.push({ t: 'h', level: h[1].length, body: h[2] }); i += 1; continue; }

    if (/^\s*>\s?/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { body.push(lines[i].replace(/^\s*>\s?/, '')); i += 1; }
      blocks.push({ t: 'quote', body: body.join(' ') });
      continue;
    }

    // lists — bullet or numbered. `indents` keeps each item's leading whitespace (nesting) for
    // the dashboard; the plain renderer ignores it.
    if (LIST_ITEM.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const start = ordered ? parseInt(/^\s*(\d+)/.exec(line)[1], 10) : 1;
      const items = [];
      const indents = [];
      while (i < lines.length && LIST_ITEM.test(lines[i])) {
        indents.push((/^(\s*)/.exec(lines[i])[1] || '').replace(/\t/g, '    ').length);
        items.push(lines[i].replace(LIST_ITEM, ''));
        i += 1;
      }
      blocks.push({ t: 'list', ordered: ordered, start: start, items: items, indents: indents });
      continue;
    }

    if (!line.trim()) { i += 1; continue; }

    // paragraph — consume until a blank line or the start of another block
    const para = [];
    while (
      i < lines.length && lines[i].trim() &&
      !/^\s*(#{1,6})\s+/.test(lines[i]) &&
      !LIST_ITEM.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isDivider(lines[i + 1]))
    ) { para.push(lines[i]); i += 1; }
    if (para.length) blocks.push({ t: 'p', body: para.join('\n') });
    else i += 1;
  }
  return blocks;
}
