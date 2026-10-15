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

export function MarkdownBlocks({ blocks }) {
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
          const Tag = b.ordered ? 'ol' : 'ul';
          return (
            <Tag key={k} start={b.ordered && b.start > 1 ? b.start : undefined} style={{ margin: '0', paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
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
