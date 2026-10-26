// Suggestions — AI suggestions on the open answer in Conversations (logic/suggest.js taxonomySuggestVals). The quoted
// evidence gets a dashed highlight in the focus colour (the annotation layer draws it from the suggestion marks), each
// suggestion gets a dashed card in the margin beside it with "AI suggestion", Accept and Dismiss, and the answer's
// failure modes show as chips: solid when a person decided, dashed while only the AI has. Suggestions whose quote is
// not on screen (no quote, or a highlight the page does not draw, such as one in a closed panel) are listed under the
// timeline. Nothing here counts until a person decides it.
import React from 'react';
import * as a from '../taxonomyActions.js';
import { taxonomySuggestVals } from '../logic/suggest.js';

const suggestOf = (s) => taxonomySuggestVals(s, a).tlTaxSuggest;
const TIMELINE = '[data-tl-timeline]';

// A card and its highlight outline each other while either is hovered.
function outline(root, id, on) {
  if (!root) return;
  root.querySelectorAll('[data-tl-hl~="' + id + '"], [data-tl-note="' + id + '"]').forEach((el) => el.classList.toggle('tl-hover', on));
}
const rootOf = (e, root) => root || (e.currentTarget && e.currentTarget.closest ? e.currentTarget.closest(TIMELINE) : null);

export default function SuggestionCard({ card, root, style, more, expand }) {
  return (
    <div id={'tl-note-' + card.markId} data-tl-note={card.markId} className="tl-sug-card" style={style} role="note" aria-label={'AI suggestion: ' + card.modeName}
      onMouseEnter={(e) => outline(rootOf(e, root), card.markId, true)} onMouseLeave={(e) => outline(rootOf(e, root), card.markId, false)}>
      <div className="tl-kick tl-kick-focus">{card.kicker}</div>
      <div className="tl-sug-mode">{card.modeName}</div>
      <div className="tl-sug-line">{card.suggestion}</div>
      {card.evidence ? <div className="tl-sug-line">{card.evidence}</div> : null}
      {card.quote ? <div className="tl-meta">{'“' + card.quote + '”'}</div> : null}
      <div className="tl-actions tl-actions-tight">
        <button type="button" className="tl-btn tl-btn-small" onClick={card.accept} disabled={card.busy}>{card.acceptLabel}</button>
        <button type="button" className="tl-btn tl-btn-small" onClick={card.other} disabled={card.busy}>{card.otherLabel}</button>
      </div>
      {card.otherHint ? <div className="tl-meta">{card.otherHint}</div> : null}
      {card.error ? <div role="alert" className="tl-bad">{card.error}</div> : null}
      {more && more.length && expand ? (
        <button type="button" onClick={expand} className="tl-link tl-more">
          {'+' + more.length + (more.length === 1 ? ' more note here' : ' more notes here')}
        </button>
      ) : null}
    </div>
  );
}

// Items for the margin column (MarginNotes' `extra`): placed beside their dashed highlight like a note.
export function suggestionMargin(s) {
  const v = suggestOf(s);
  if (!v || v.hidden) return [];
  return v.cards.map((card) => ({
    id: card.markId, height: card.height,
    render: (p, root, expand) => (
      <SuggestionCard key={p.id} card={card} root={root} more={p.more} expand={expand}
        style={{ position: 'absolute', top: p.top + 'px', left: '0', right: '0', minHeight: p.height + 'px', boxSizing: 'border-box' }} />
    )
  }));
}

// The answer's failure modes as chips, read when the open answer changes and again once it has been judged.
export function TurnLabels({ s }) {
  const v = suggestOf(s);
  const turnId = v ? v.turnId : null;
  const judged = v ? v.judged : false;
  const ensure = v ? v.ensure : null;
  React.useEffect(() => { if (ensure) ensure(); }, [turnId, judged, s.seq]);
  if (!v) return null;
  return (
    <section aria-label="Failure modes on this answer" className="tl-turn-labels">
      <span className="tl-kick">{'Failure modes'}</span>
      {v.loading ? <span className="tl-meta">{'Reading…'}</span> : null}
      {v.hidden ? <span className="tl-meta">{v.hiddenText}</span> : null}
      {v.chips.map((ch) => <span key={ch.key} className={'tl-chip tl-chip-' + ch.kind} title={ch.title}>{ch.text}</span>)}
      {!v.loading && !v.hidden && !v.error && !v.chips.length ? <span className="tl-meta">{v.emptyText}</span> : null}
      {v.error ? <span role="alert" className="tl-bad">{v.error}<button type="button" className="tl-btn tl-btn-small tl-gap-left" onClick={v.reload}>{'Retry'}</button></span> : null}
    </section>
  );
}

// Suggestions whose quoted text is not on screen: no quote, a quote that is not in the answer's text, and (`also`, the
// ids the margin column reports as having no highlight on the page) a quote inside a closed panel or a cut-short result.
export function SuggestionList({ s, also }) {
  const v = suggestOf(s);
  if (!v || v.hidden) return null;
  const off = new Set(also || []);
  const list = v.unplaced.concat(v.cards.filter((card) => off.has(card.markId)));
  if (!list.length) return null;
  return (
    <section aria-label="AI suggestions on this answer" className="tl-unplaced">
      <div className="tl-verdict-label">{'AI suggestions on this answer'}</div>
      <div className="tl-meta">{'Nothing here counts until a person decides it.'}</div>
      {list.map((card) => <SuggestionCard key={card.id} card={card} />)}
    </section>
  );
}
