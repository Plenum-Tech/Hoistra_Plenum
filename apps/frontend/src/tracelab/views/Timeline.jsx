// Timeline — one answer's cards (logic/timeline.js toCards) as a list of articles, each labelled with its role
// and time. The question and the answer are read in full; every step between them is one line that opens in place,
// so a long answer no longer means a long page. ModelCallCard is also the model-call side panel's card. Every panel starts closed, and what is open
// is the store's (isOpen / setOpen), so the 45-second refresh never closes it. Given the page state (`s`), a body
// the service sent text for is drawn as that text, selectable and highlighted (MarginNotes.jsx AnnotatedText);
// without `s` (the model-call panel) or without the text a card shows its own body, as before.
import React from 'react';
import RawPanel from './RawPanel.jsx';
import { AnnotatedText } from './MarginNotes.jsx';
import { blockView } from '../logic/annotate.js';

function Rows({ rows }) {
  if (!rows || !rows.length) return null;
  const cols = Object.keys(rows[0] || {});
  const cell = (v) => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
  return (
    <div className="tl-rows">
      <table>
        <thead><tr>{cols.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} title={cell(r[c])}>{cell(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Panel({ panel, open, onToggle, annotate }) {
  return (
    <details className="tl-panel" open={!!open} onToggle={(e) => { if (e.currentTarget.open !== !!open) onToggle(e.currentTarget.open); }}>
      <summary>{panel.title}</summary>
      {open ? (
        <>
          {panel.note ? <div className="tl-meta">{panel.note}</div> : null}
          {panel.rows ? (
            <table className="tl-kv"><tbody>{panel.rows.map(([k, v]) => <tr key={k}><th scope="row">{k}</th><td>{v}</td></tr>)}</tbody></table>
          ) : (
            <AnnotatedText s={annotate && annotate.s} anchor={annotate && annotate.anchor} className="tl-mono"><pre className="tl-pre">{panel.body}</pre></AnnotatedText>
          )}
        </>
      ) : null}
    </details>
  );
}

// One model call: what it was for, "model · latency · tokens · cost" (each "unavailable" when the trace did not
// record it), the record's note on private reasoning, and Request, Response, Usage and Raw metadata, closed.
export function ModelCallCard({ card, isOpen, setOpen, s, bare }) {
  const [request, response, usage, raw] = card.panels;
  return (
    <>
      {bare ? null : <div className="tl-card-head">
        <span className="tl-role">{card.role}</span><span>{card.purpose}</span>
        <span className="tl-meta">{card.model + ' · ' + card.latency + ' · ' + card.tokens + ' · ' + card.cost}</span>
        {card.ok ? null : <span className="tl-bad">{'Failed' + (card.error ? ': ' + card.error : '')}</span>}
      </div>}
      {card.cotNote ? <div className="tl-meta">{card.cotNote}</div> : null}
      {[request, response, usage].map((p) => (
        <Panel key={p.id} panel={p} open={isOpen(p.id, false)} onToggle={(o) => setOpen(p.id, o)}
          annotate={p === response && !card.withheld ? { s, anchor: card.anchor } : null} />
      ))}
      <RawPanel title={raw.title} data={raw.raw} open={isOpen(raw.id, false)} onToggle={(o) => setOpen(raw.id, o)} />
    </>
  );
}

// The body of an opened step row: a tool call and its result, or a database query with the rows it answered. The
// row above already names the step, so no head repeats here. The tool's own name, its arguments as recorded and
// the SQL sit in the raw panel.
function ToolCard({ card, isOpen, setOpen, s }) {
  const raw = isOpen(card.id + ':raw', false);
  if (card.kind === 'db') {
    // The service's full text (review mode) can run to thousands of lines, so the compact table comes first and the
    // text waits behind "Show all". A card that already holds a note or a draft quote starts open.
    const block = s ? blockView(s, card.anchor) : null;
    const hasText = !!(block && block.text) && card.rowCount !== 0;
    const all = hasText && isOpen(card.id + ':all', block.items.length > 0);
    return (
      <>
        {failedNote(card)}
        {all ? <AnnotatedText s={s} anchor={card.anchor} className="tl-mono"><Rows rows={card.rows} /></AnnotatedText> : (
          <>
            {card.rows && card.rows.length ? <Rows rows={card.rows} /> : <div className="tl-meta">{card.rowCount === 0 ? 'No rows came back.' : 'No rows recorded.'}</div>}
            {card.moreRows ? <div className="tl-meta">{card.moreRows + ' more rows not shown'}</div> : null}
          </>
        )}
        {hasText ? (
          <button type="button" className="tl-link" aria-expanded={all ? 'true' : 'false'} onClick={() => setOpen(card.id + ':all', !all)}>
            {all ? 'Show less' : 'Show all as text' + (card.rowCount ? ' (' + rowsText(card) + ')' : '')}
          </button>
        ) : null}
        <RawPanel title="Technical detail" data={card.raw} open={raw} onToggle={(o) => setOpen(card.id + ':raw', o)} />
      </>
    );
  }
  const body = card.result || { text: '', full: '', long: false, chars: 0 };
  const all = isOpen(card.id + ':all', false);
  return (
    <>
      {failedNote(card)}
      {body.full ? (
        <>
          <AnnotatedText s={s} anchor={all || !body.long ? card.anchor : null} className="tl-mono">
            <pre className="tl-pre">{all || !body.long ? body.full : body.text}</pre>
          </AnnotatedText>
          {body.long ? (
            <button type="button" className="tl-link" aria-expanded={all ? 'true' : 'false'} onClick={() => setOpen(card.id + ':all', !all)}>
              {all ? 'Show less' : 'Show all (' + body.chars.toLocaleString('en-GB') + ' characters)'}
            </button>
          ) : null}
        </>
      ) : <div className="tl-meta">{'No result recorded.'}</div>}
      <RawPanel title="Technical detail" data={card.raw} open={raw} onToggle={(o) => setOpen(card.id + ':raw', o)} />
    </>
  );
}

const failedNote = (card) => (card.ok === false && card.error ? <div className="tl-bad">{card.error}</div> : null);
const rowsText = (card) => (card.rowCount === null || card.rowCount === undefined ? 'rows not recorded'
  : card.rowCount + (card.rowCount === 1 ? ' row' : ' rows'));
const charsText = (body) => (body && body.chars ? body.chars.toLocaleString('en-GB') + ' characters' : '');

// What a step row says after its name: the facts a reviewer scans for without opening it.
function stepFacts(card) {
  if (card.kind === 'db') return rowsText(card);
  if (card.kind === 'tool') return card.latency || '';
  if (card.kind === 'model') return [card.model, card.latency, card.tokens, card.cost].filter(Boolean).join(' · ');
  if (card.kind === 'plan') return card.steps.length ? card.steps.length + (card.steps.length === 1 ? ' step' : ' steps') : '';
  return charsText(card.body);
}
const stepTitle = (card) => (card.kind === 'model' ? card.purpose : card.kind === 'plan' ? (card.rejected ? 'Rejected: ' + card.rejected : '') : card.title || '');

// The body of an opened step row for a plan or any other recorded step.
export function StepBody({ card, isOpen, setOpen, s }) {
  const id = card.id;
  if (card.kind === 'tool' || card.kind === 'db') return <ToolCard card={card} isOpen={isOpen} setOpen={setOpen} s={s} />;
  if (card.kind === 'model') return <ModelCallCard card={card} isOpen={isOpen} setOpen={setOpen} s={s} bare />;
  if (card.kind === 'plan') {
    return (
      <>
        {card.steps.length ? <ol className="tl-steps">{card.steps.map((x) => <li key={x.n}>{x.text}</li>)}</ol> : <div className="tl-meta">{'No steps recorded.'}</div>}
        <RawPanel title="Technical detail" data={card.raw} open={isOpen(id + ':raw', false)} onToggle={(o) => setOpen(id + ':raw', o)} />
      </>
    );
  }
  return (
    <>
      {failedNote(card)}
      <AnnotatedText s={s} anchor={card.anchor} className="tl-mono"><pre className="tl-pre">{card.body ? card.body.full : ''}</pre></AnnotatedText>
      <RawPanel title="Technical detail" data={card.raw} open={isOpen(id + ':raw', false)} onToggle={(o) => setOpen(id + ':raw', o)} />
    </>
  );
}

const isStep = (card) => card.kind !== 'user' && card.kind !== 'answer' && card.kind !== 'cot';
const stepKey = (card) => card.id + ':step';

// A step starts closed unless it holds a reviewer's note or draft quote, so every highlight stays on screen.
function stepOpen(card, isOpen, s) {
  const block = s && card.anchor ? blockView(s, card.anchor) : null;
  return isOpen(stepKey(card), !!(block && block.items.length));
}

// One step as a single line that opens in place: what it was, its name, and the facts worth scanning.
function StepRow({ card, isOpen, setOpen, s }) {
  const open = stepOpen(card, isOpen, s);
  const title = stepTitle(card);
  const facts = stepFacts(card);
  return (
    <>
      <button type="button" className="tl-step-row" aria-expanded={open ? 'true' : 'false'} onClick={() => setOpen(stepKey(card), !open)}>
        <span className="tl-step-chev" aria-hidden="true" />
        <span className="tl-step-kind">{card.role}</span>
        {title ? <span className="tl-step-title" title={title}>{title}</span> : <span className="tl-step-title" />}
        {facts ? <span className="tl-step-facts">{facts}</span> : null}
        {card.ok === false ? <span className="tl-bad">{'Failed'}</span> : null}
      </button>
      {open ? <div className="tl-step-body"><StepBody card={card} isOpen={isOpen} setOpen={setOpen} s={s} /></div> : null}
    </>
  );
}

// The question and the answer: the two cards a reviewer reads in full.
function Said({ card, s }) {
  return (
    <>
      <div className="tl-card-head"><span className="tl-role">{card.role}</span></div>
      <AnnotatedText s={s} anchor={card.anchor} className="tl-text"><div className="tl-text">{card.text || '—'}</div></AnnotatedText>
      {card.error ? <div className="tl-bad">{card.error}</div> : null}
    </>
  );
}

export default function Timeline({ cards, isOpen, setOpen, s }) {
  if (!cards || !cards.length) return <div className="tl-meta">{'Nothing was recorded for this answer.'}</div>;
  // The record's note on model reasoning follows every group of model calls; once is enough to read it.
  const firstCot = cards.find((c) => c.kind === 'cot');
  const shown = cards.filter((c) => c.kind !== 'cot' || c === firstCot);
  const steps = shown.filter(isStep);
  const allOpen = steps.length > 0 && steps.every((c) => stepOpen(c, isOpen, s));
  return (
    <>
      {steps.length ? (
        <div className="tl-steps-bar">
          <span className="tl-meta">{steps.length + (steps.length === 1 ? ' step' : ' steps') + ' between the question and the answer'}</span>
          <button type="button" className="tl-link" onClick={() => steps.forEach((c) => setOpen(stepKey(c), !allOpen))}>{allOpen ? 'Close every step' : 'Open every step'}</button>
        </div>
      ) : null}
      <ol className="tl-timeline" aria-label="How this answer was produced">
        {shown.map((card) => (
          <li key={card.id} className={'tl-item tl-' + card.kind + (isStep(card) ? ' tl-step' : '')} style={{ marginLeft: Math.min(card.depth || 0, 4) * 14 + 'px' }}>
            <article aria-label={card.role + (card.when ? ' · ' + card.when : '')}>
              {card.kind === 'cot' ? <p className="tl-cot">{card.text}</p>
                : isStep(card) ? <StepRow card={card} isOpen={isOpen} setOpen={setOpen} s={s} /> : <Said card={card} s={s} />}
            </article>
          </li>
        ))}
      </ol>
    </>
  );
}
