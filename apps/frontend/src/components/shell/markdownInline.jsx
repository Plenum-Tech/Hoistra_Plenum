// markdownInline — the inline marks of an orchestrator answer (bold, italic, code, links) as React
// elements. Its own module so Markdown.jsx and AnswerCards.jsx export only components: a module
// that mixes a helper with components cannot be hot-swapped, and on 5 Oct that left an open chat
// tab on the old renderer after an update.
import React from 'react';
import { isSafeHref } from './markdownSafety.js';
import { documentIdFromHref, openDocument } from '../../api/docRag.js';
import { scrubInternal } from '../../logic/publicText.js';

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

export function inline(text, keyBase) {
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
    // The answer's prose can carry the platform's own names - a service, a table, a skill
    // document the agent read - so every run of text is scrubbed (logic/publicText.js). A
    // link's target is not: it is how a document link opens.
    if (!best) { out.push(scrubInternal(rest)); break; }

    if (best.at > 0) out.push(scrubInternal(rest.slice(0, best.at)));
    const k = keyBase + '-' + n++;
    const g = best.m;

    if (best.kind === 'code') {
      out.push(
        <code key={k} style={{ fontFamily: 'ui-monospace,monospace', fontSize: '0.92em', background: 'var(--color-bg)', borderRadius: '4px', padding: '1px 4px' }}>{scrubInternal(g[1], { code: true })}</code>
      );
    } else if (best.kind === 'strong') {
      // Inline marks inside bold still render: "**[View Certificate](https://…)**" is a link,
      // not the literal brackets and URL it showed before (5 Oct 2026).
      out.push(<strong key={k} style={{ fontWeight: '600' }}>{inline(g[1], k)}</strong>);
    } else if (best.kind === 'em') {
      out.push(<em key={k}>{scrubInternal(g[1])}</em>);
    } else if (best.kind === 'link') {
      // The target came from the orchestrator's answer, not from a trusted author — see
      // markdownSafety.js. Anything other than a confirmed safe scheme renders as its own
      // link text rather than a clickable href.
      // A document link - doc:<id> or a /download URL, in an answer old or new - opens through a
      // signed link (api/docRag.js); the bare URL alone is refused by the server.
      const docId = documentIdFromHref(g[2]);
      out.push(
        docId
          ? <a key={k} href="#" onClick={(e) => { e.preventDefault(); openDocument(docId); }} style={{ color: 'var(--color-accent)' }}>{scrubInternal(g[1])}</a>
          : isSafeHref(g[2])
            ? <a key={k} href={g[2]} target="_blank" rel="noreferrer noopener" style={{ color: 'var(--color-accent)' }}>{scrubInternal(g[1])}</a>
            : scrubInternal(g[1])
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
