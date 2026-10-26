// ModelCalls — every model call in the period: the aggregate line, one row per call, and the call's card in a
// side panel (Escape or × closes it). A field the trace did not record reads "unavailable".
import React from 'react';
import { ModelCallCard } from './Timeline.jsx';
import * as act from '../actions.js';
import { callPanelView, callsView, isOpen } from '../logic/views.js';

export default function ModelCalls({ s, now }) {
  const v = callsView(s, now);
  const panel = callPanelView(s);
  const opened = (id, dflt) => isOpen(s, id, dflt);
  return (
    <>
      <section className="tl-card" aria-label="Model calls">
        <div className="tl-list-head"><span>{v.header || ' '}</span></div>
        {v.note ? <div className="tl-meta">{v.note}</div> : null}
        {v.loading ? <div className="tl-meta">{'Reading model calls…'}</div> : null}
        {v.error ? <div className="tl-bad" role="alert">{"Couldn't read the model calls: " + v.error}</div> : null}
        {v.empty ? <div className="tl-meta">{'No model calls in this period.'}</div> : null}
        <table className="tl-table">
          <thead><tr>{['Time', 'Model', 'Purpose', 'Latency', 'Tokens', 'Cost', 'Status', 'Question'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {v.rows.map((r) => (
              <tr key={r.id} className={r.on ? 'tl-row-on' : ''}>
                <td><button type="button" className="tl-link" onClick={() => act.openCall(r.id)} aria-label={'Open the model call at ' + r.when}>{r.when}</button></td>
                <td>{r.model}</td><td>{r.purpose}</td><td>{r.latency}</td><td>{r.tokens}</td><td>{r.cost}</td>
                <td>{r.ok ? 'ok' : 'failed'}</td><td className="tl-ellipsis" title={r.question}>{r.question}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="tl-pager">
          <button type="button" className="tl-link" onClick={() => act.pageCalls(-1)} disabled={!v.canPrev}>{'‹ Previous'}</button>
          <button type="button" className="tl-link" onClick={() => act.pageCalls(1)} disabled={!v.canNext}>{'Next ›'}</button>
        </div>
      </section>
      {panel ? (
        <div className="tl-side" role="dialog" aria-modal="true" aria-label="Model call" onKeyDown={(e) => { if (e.key === 'Escape') act.closeCall(); }}>
          <button type="button" className="tl-close" aria-label="Close" onClick={act.closeCall}>{'×'}</button>
          {panel.loading ? <div className="tl-meta">{'Reading the model call…'}</div> : null}
          {panel.error ? <div className="tl-bad" role="alert">{"Couldn't read the model call: " + panel.error}</div> : null}
          {panel.card ? <ModelCallCard card={panel.card} isOpen={opened} setOpen={act.setOpen} /> : null}
          {panel.conversationKey ? (
            <button type="button" className="tl-link" onClick={() => act.openInConversations(panel.conversationKey, panel.turnId)}>{'Open in Conversations'}</button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
