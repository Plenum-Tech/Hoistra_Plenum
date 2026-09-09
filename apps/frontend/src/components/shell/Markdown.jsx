// Markdown — renders the orchestrator's answers.
//
// The sub-agents answer in markdown: bold, headings, bullet lists and pipe tables. Left
// as plain text those show as literal `**`, `###` and wrapped ASCII pipes, which is
// unreadable in a narrow dock.
//
// Deliberately small and dependency-free, and it builds React elements rather than using
// dangerouslySetInnerHTML — the text comes from an LLM, so it is never trusted as HTML.
// Supported: headings, **bold**, *italic*, `code`, links, bullet/numbered lists, pipe
// tables, fenced code, blockquotes, --- rules. Anything else falls through as text.
import React from 'react';
import { isSafeHref } from './markdownSafety.js';

// ── inline ────────────────────────────────────────────────────────────────────
// Ordered so the greedier tokens (**, ``) are tried before the single-char ones.
const INLINE = [
  { re: /`([^`]+)`/, kind: 'code' },
  { re: /\*\*([^*]+)\*\*/, kind: 'strong' },
  { re: /__([^_]+)__/, kind: 'strong' },
  { re: /\[([^\]]+)\]\(([^)\s]+)\)/, kind: 'link' },
  { re: /(?:^|[^*])\*([^*\n]+)\*/, kind: 'em' },
  { re: /(?:^|[\s(])_([^_\n]+)_/, kind: 'em' }
];

function inline(text, keyBase) {
  const out = [];
  let rest = String(text == null ? '' : text);
  let n = 0;

  while (rest) {
    // Find whichever token appears earliest.
    let best = null;
    for (const t of INLINE) {
      const m = t.re.exec(rest);
      if (!m) continue;
      // For the em patterns the match may include a leading boundary char; find the
      // real start of the marker so preceding text is not swallowed.
      const markerAt = t.kind === 'em' ? rest.indexOf('*' + m[1] + '*') >= 0
        ? rest.indexOf('*' + m[1] + '*')
        : rest.indexOf('_' + m[1] + '_')
        : m.index;
      if (!best || markerAt < best.at) best = { at: markerAt, m: m, kind: t.kind };
    }
    if (!best) { out.push(rest); break; }

    if (best.at > 0) out.push(rest.slice(0, best.at));
    const k = keyBase + '-' + n++;
    const g = best.m;

    if (best.kind === 'code') {
      out.push(
        <code key={k} style={{ fontFamily: 'ui-monospace,monospace', fontSize: '0.92em', background: 'var(--color-bg)', borderRadius: '4px', padding: '1px 4px' }}>{g[1]}</code>
      );
    } else if (best.kind === 'strong') {
      out.push(<strong key={k} style={{ fontWeight: '600' }}>{g[1]}</strong>);
    } else if (best.kind === 'em') {
      out.push(<em key={k}>{g[1]}</em>);
    } else if (best.kind === 'link') {
      // The target came from the orchestrator's answer, not from a trusted author — see
      // markdownSafety.js. Anything other than a confirmed safe scheme renders as its own
      // link text rather than a clickable href.
      out.push(
        isSafeHref(g[2])
          ? <a key={k} href={g[2]} target="_blank" rel="noreferrer noopener" style={{ color: 'var(--color-accent)' }}>{g[1]}</a>
          : g[1]
      );
    }

    // Advance past the consumed marker, not past the regex match (which may have
    // included a boundary character we already emitted).
    const consumed = best.kind === 'code' ? '`' + g[1] + '`'
      : best.kind === 'strong' ? (rest.slice(best.at, best.at + 2) === '**' ? '**' + g[1] + '**' : '__' + g[1] + '__')
      : best.kind === 'link' ? '[' + g[1] + '](' + g[2] + ')'
      : rest.slice(best.at, best.at + 1) + g[1] + rest.slice(best.at, best.at + 1);
    rest = rest.slice(best.at + consumed.length);
  }
  return out;
}

const splitRow = (line) =>
  line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

const isDivider = (line) => /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes('-');

// ── blocks ────────────────────────────────────────────────────────────────────
export default function Markdown({ text }) {
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

    // lists — bullet or numbered
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ''));
        i += 1;
      }
      blocks.push({ t: 'list', ordered: ordered, items: items });
      continue;
    }

    if (!line.trim()) { i += 1; continue; }

    // paragraph — consume until a blank line or the start of another block
    const para = [];
    while (
      i < lines.length && lines[i].trim() &&
      !/^\s*(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isDivider(lines[i + 1]))
    ) { para.push(lines[i]); i += 1; }
    if (para.length) blocks.push({ t: 'p', body: para.join('\n') });
    else i += 1;
  }

  const HS = { 1: '15px', 2: '13.5px', 3: '12.5px', 4: '12px', 5: '11.5px', 6: '11.5px' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
      {blocks.map((b, k) => {
        if (b.t === 'h') {
          return (
            <div key={k} style={{ fontSize: HS[b.level] || '12px', fontWeight: '600', lineHeight: '1.3', marginTop: k ? '3px' : '0' }}>
              {inline(b.body, 'h' + k)}
            </div>
          );
        }
        if (b.t === 'rule') return <div key={k} style={{ height: '1px', background: 'var(--color-divider)' }}></div>;
        if (b.t === 'code') {
          return (
            <pre key={k} style={{ margin: '0', overflowX: 'auto', background: 'var(--color-bg)', borderRadius: '6px', padding: '8px 9px', fontFamily: 'ui-monospace,monospace', fontSize: '10.5px', lineHeight: '1.5' }}>
              {b.body}
            </pre>
          );
        }
        if (b.t === 'quote') {
          return (
            <div key={k} style={{ borderLeft: '2px solid var(--color-accent)', paddingLeft: '9px', color: 'var(--color-neutral-300)' }}>
              {inline(b.body, 'q' + k)}
            </div>
          );
        }
        if (b.t === 'list') {
          const Tag = b.ordered ? 'ol' : 'ul';
          return (
            <Tag key={k} style={{ margin: '0', paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
              {b.items.map((it, j) => <li key={j} style={{ lineHeight: '1.5' }}>{inline(it, 'l' + k + '-' + j)}</li>)}
            </Tag>
          );
        }
        if (b.t === 'table') {
          // The dock is narrow, so the table scrolls inside its own box rather than
          // forcing the whole panel wide.
          return (
            <div key={k} style={{ overflowX: 'auto', border: '1px solid var(--color-divider)', borderRadius: '7px' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '10.5px' }}>
                <thead>
                  <tr>
                    {b.head.map((c, j) => (
                      <th key={j} style={{ textAlign: 'left', whiteSpace: 'nowrap', padding: '6px 9px', background: 'var(--color-bg)', borderBottom: '1px solid var(--color-divider)', fontWeight: '600', letterSpacing: '0.02em' }}>
                        {inline(c, 'th' + k + '-' + j)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {b.rows.map((r, j) => (
                    <tr key={j}>
                      {r.map((c, m) => (
                        <td key={m} style={{ padding: '6px 9px', borderBottom: j === b.rows.length - 1 ? 'none' : '1px solid var(--color-divider)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                          {inline(c, 'td' + k + '-' + j + '-' + m)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        return (
          <div key={k} style={{ lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
            {inline(b.body, 'p' + k)}
          </div>
        );
      })}
    </div>
  );
}
