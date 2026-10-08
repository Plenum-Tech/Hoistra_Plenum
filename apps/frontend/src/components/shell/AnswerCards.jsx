// AnswerCards — any structured answer, laid out like the compliance dashboard.
//
// logic/answerCards.js arranges the answer's own text into an Overall line and sections; this
// draws them with the dashboard's pieces (cardStyles.js): a section label, KPI tiles, entity cards
// with their fields, priority-action cards, and a card for whatever else the section holds (tables,
// lists, paragraphs, drawn by the plain Markdown renderer). Same order and look as
// ComplianceAnswer, and never a card inside a card. An answer with no structure is plain Markdown.
import React from 'react';
import Markdown, { MarkdownBlocks } from './Markdown.jsx';
import { inline } from './markdownInline.jsx';
import { answerCards, toneOf } from '../../logic/answerCards.js';
import { sev, LABEL, CARD } from './cardStyles.js';

const TEXT = { fontSize: '11.5px', lineHeight: '1.55', textWrap: 'pretty' };

// A figure's colour: a count of something bad is red unless it is zero; anything else is plain.
function kpiTone(k) {
  const zero = /^[£$€]?\s?0(?:[.,]0+)?\s?%?$/.test(k.value);
  const t = toneOf(k.label);
  return zero && (t === 'critical' || t === 'warning') ? 'ok' : t;
}

function Kpis({ kpis }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(128px,1fr))', gap: '7px' }}>
      {kpis.map((k, i) => {
        const c = sev(kpiTone(k));
        return (
          <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}` }}>
            <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '19px', lineHeight: '1.1', color: c.fg, fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>{k.value}</div>
            <div style={{ fontSize: '10.5px', lineHeight: '1.35', marginTop: '3px' }}>{inline(k.label, 'kl' + i)}</div>
            {k.sub ? <div style={{ fontSize: '10px', color: 'var(--color-neutral-500)', marginTop: '2px', lineHeight: '1.35' }}>{inline(k.sub, 'ks' + i)}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

function Entities({ entities, tone, keyBase }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: '7px' }}>
      {entities.map((e, i) => {
        const c = sev(toneOf(e.fields.map((f) => f.value).join(' ')) !== 'neutral' ? toneOf(e.fields.map((f) => f.value).join(' ')) : tone);
        return (
          <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}`, minWidth: '0' }}>
            <div style={{ fontSize: '12px', lineHeight: '1.35', fontWeight: '600', overflowWrap: 'anywhere' }}>{inline(e.title, keyBase + 'et' + i)}</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(84px,max-content) minmax(0,1fr)', columnGap: '10px', rowGap: '4px', marginTop: '7px', fontSize: '10.5px', lineHeight: '1.45' }}>
              {e.fields.map((f, j) => (f.key
                ? (
                  <React.Fragment key={j}>
                    <span style={{ color: 'var(--color-neutral-500)', overflowWrap: 'anywhere' }}>{inline(f.key, keyBase + 'ek' + i + '-' + j)}</span>
                    <span style={{ color: 'var(--color-neutral-200)', minWidth: '0', overflowWrap: 'anywhere' }}>{inline(f.value, keyBase + 'ef' + i + '-' + j)}</span>
                  </React.Fragment>
                )
                : <span key={j} style={{ gridColumn: '1 / -1', color: 'var(--color-neutral-300)', overflowWrap: 'anywhere' }}>{inline(f.value, keyBase + 'ef' + i + '-' + j)}</span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Actions({ actions, keyBase }) {
  const c = sev('info');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
      {actions.map((a, i) => (
        <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}`, padding: '9px 11px' }}>
          <div style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: c.fg }}>{'Action'}</div>
          <div style={{ fontSize: '11.5px', lineHeight: '1.45', marginTop: '4px', overflowWrap: 'anywhere' }}>{inline(a.text, keyBase + 'a' + i)}</div>
          {a.subs.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '6px' }}>
              {a.subs.map((x, j) => (
                <div key={j} style={{ display: 'grid', gridTemplateColumns: '7px minmax(0,1fr)', gap: '6px', fontSize: '10.5px', color: 'var(--color-neutral-300)', lineHeight: '1.45' }}>
                  <span>{'•'}</span><span style={{ overflowWrap: 'anywhere' }}>{inline(x, keyBase + 'as' + i + '-' + j)}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// Consecutive plain blocks share one card; tiles, entity cards and actions sit between them.
function Parts({ parts, tone, keyBase }) {
  const runs = [];
  for (const p of parts) {
    const last = runs[runs.length - 1];
    if (p.t === 'block' && last && last.t === 'blocks') last.blocks.push(p.block);
    else runs.push(p.t === 'block' ? { t: 'blocks', blocks: [p.block] } : p);
  }
  const c = sev(tone);
  return runs.map((r, i) => {
    const k = keyBase + '-' + i;
    if (r.t === 'kpis') return <Kpis key={k} kpis={r.kpis} />;
    if (r.t === 'entities') return <Entities key={k} entities={r.entities} tone={tone} keyBase={k} />;
    if (r.t === 'actions') return <Actions key={k} actions={r.actions} keyBase={k} />;
    return (
      <div key={k} style={{ ...CARD, borderLeft: `3px solid ${c.fg}`, ...TEXT, minWidth: '0' }}>
        <MarkdownBlocks blocks={r.blocks} />
      </div>
    );
  });
}

function Section({ s, i }) {
  const key = 's' + i;
  const label = s.title || s.kicker ? (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px', flexWrap: 'wrap' }}>
      {s.kicker ? <span style={LABEL}>{inline(s.kicker, key + 'k')}{' ·'}</span> : null}
      {s.title ? <span style={{ ...LABEL, color: 'var(--color-neutral-400)' }}>{inline(s.title, key + 't')}</span> : null}
    </div>
  ) : null;
  if (s.kind === 'summary') {
    return (
      <div>
        {label}
        <div style={{ ...TEXT, marginTop: '5px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {s.parts.map((p, j) => <div key={j} style={{ whiteSpace: 'pre-wrap' }}>{inline(p.block.body, key + 'p' + j)}</div>)}
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', minWidth: '0' }}>
      {label}
      <Parts parts={s.parts} tone={s.kind === 'actions' ? 'info' : s.tone} keyBase={key} />
    </div>
  );
}

export default function AnswerCards({ text }) {
  const m = answerCards(text);
  if (!m) return <Markdown text={text} />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '11px', minWidth: '0' }}>
      {m.overall.length ? (
        <div>
          <div style={LABEL}>{'Overall'}</div>
          <div style={{ ...TEXT, marginTop: '5px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {m.overall.map((p, i) => <div key={i} style={{ whiteSpace: 'pre-wrap' }}>{inline(p, 'o' + i)}</div>)}
          </div>
        </div>
      ) : null}
      {m.sections.map((s, i) => <Section key={i} s={s} i={i} />)}
    </div>
  );
}
