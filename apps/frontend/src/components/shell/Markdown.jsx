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
import { parseBlocks } from '../../logic/markdownBlocks.js';
import { inline } from './markdownInline.jsx';
import { scrubInternal } from '../../logic/publicText.js';

// ── blocks ────────────────────────────────────────────────────────────────────
// The block parser lives in logic/markdownBlocks.js (shared with the dashboard layout).
export default function Markdown({ text }) {
  return <MarkdownBlocks blocks={parseBlocks(text)} />;
}

// mark: an optional pass over what inline() drew in paragraphs, list items and table cells (the
// OpenUI answer passes shared.jsx refs(), which draws record codes as tags). Every other caller
// passes none, and its words are drawn as before. A paragraph is a "md-p"; one that opens on bold
// (a finding: "**Fire Risk Assessment — Lapsed**, expired…") is marked data-lead, which only the
// OpenUI stylesheet draws as a card (28 Oct 2026).
const same = (x) => x;
const LEAD = /^\s*(\*\*|__)/;

export function MarkdownBlocks({ blocks, mark = same }) {
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
              {scrubInternal(b.body, { code: true })}
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
          // Items indented deeper than the list's own items sit under the item before them, and a
          // numbered item keeps the number the answer gave it (27 Oct 2026: two sub-bullets under
          // "2." turned the answer's "3." into 5).
          const Tag = b.ordered ? 'ol' : 'ul';
          const indents = b.indents || [];
          const marks = b.marks || [];
          const top = indents.length ? Math.min(...indents) : 0;
          const groups = [];
          b.items.forEach((it, j) => {
            if ((indents[j] || 0) > top && groups.length) groups[groups.length - 1].subs.push(j);
            else groups.push({ j: j, subs: [] });
          });
          const number = (j) => (/^\d+/.test(marks[j] || '') ? parseInt(marks[j], 10) : undefined);
          const style = { margin: '0', paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '3px' };
          return (
            <Tag key={k} start={b.ordered && b.start > 1 ? b.start : undefined} style={style}>
              {groups.map((g) => {
                const SubTag = g.subs.length && number(g.subs[0]) !== undefined ? 'ol' : 'ul';
                return (
                  <li key={g.j} value={b.ordered ? number(g.j) : undefined} style={{ lineHeight: '1.5' }}>
                    {mark(inline(b.items[g.j], 'l' + k + '-' + g.j))}
                    {g.subs.length ? (
                      <SubTag style={{ ...style, marginTop: '3px' }}>
                        {g.subs.map((j) => <li key={j} value={SubTag === 'ol' ? number(j) : undefined} style={{ lineHeight: '1.5' }}>{mark(inline(b.items[j], 'l' + k + '-' + j))}</li>)}
                      </SubTag>
                    ) : null}
                  </li>
                );
              })}
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
                          {mark(inline(c, 'td' + k + '-' + j + '-' + m))}
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
          <div key={k} className="md-p" data-lead={LEAD.test(b.body) ? '' : undefined} style={{ lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
            {mark(inline(b.body, 'p' + k))}
          </div>
        );
      })}
    </div>
  );
}
