// Conversations — the list (left) and one conversation (right): its chips, the answers in it, notes about the
// record, the asker's own rating, the comments, and the open answer's timeline with the reviewer's margin notes
// beside it and the verdict bar under it (Phase 4: the annotation layer is views/VerdictBar.jsx and MarginNotes.jsx).
import React from 'react';
import Timeline from './Timeline.jsx';
import VerdictBar, { ConversationComments } from './VerdictBar.jsx';
import MarginNotes, { UnplacedNotes } from './MarginNotes.jsx';
import { TurnLabels, SuggestionList, suggestionMargin } from './Suggestions.jsx';
import ExpectedOutcome from './ExpectedOutcome.jsx';
import FidelityCaveats from './FidelityCaveats.jsx';
import { isGoldenSession } from '../logic/golden.js';
import * as act from '../actions.js';
import { convView, isOpen, listView } from '../logic/views.js';
import { noticesView, turnChips } from '../logic/annotate.js';

export default function Conversations({ s, now }) {
  const list = listView(s, now);
  const c = convView(s, now);
  const v = c.view;
  const opened = (id, dflt) => isOpen(s, id, dflt);
  const timelineRef = React.useRef(null);
  const [missing, setMissing] = React.useState([]);
  const notices = noticesView(s);
  return (
    <div className="tl-split">
      <section className="tl-list" aria-label="Conversations">
        <div className="tl-list-head"><span>{list.totalLabel || ' '}</span><span className="tl-meta">{list.pageLabel}</span></div>
        {list.loading ? <div className="tl-meta">{'Reading conversations…'}</div> : null}
        {list.error ? <div className="tl-bad" role="alert">{"Couldn't read the conversations: " + list.error}</div> : null}
        {list.empty ? <div className="tl-meta">{'No answers in this period.'}</div> : null}
        <ul className="tl-rows-list">
          {list.rows.map((r) => (
            <li key={r.key}>
              <button type="button" className={'tl-row' + (r.on ? ' tl-row-on' : '')} aria-current={r.on ? 'true' : undefined} onClick={() => act.openConversation(r.key)}>
                <span className="tl-row-q">{r.question}</span>
                <span className="tl-meta">{r.answers + ' · ' + r.who + ' · ' + r.when + (r.failed ? ' · failed' : '')}</span>
                {r.chip ? <span className="tl-chip">{r.chip}</span> : null}
              </button>
            </li>
          ))}
        </ul>
        <div className="tl-pager">
          <button type="button" className="tl-link" onClick={() => act.pageConversations(-1)} disabled={!list.canPrev}>{'‹ Previous'}</button>
          <button type="button" className="tl-link" onClick={() => act.pageConversations(1)} disabled={!list.canNext}>{'Next ›'}</button>
        </div>
      </section>
      <section className="tl-conv" aria-label="Conversation">
        {c.error ? <div className="tl-bad" role="alert">{"Couldn't read this conversation: " + c.error}</div> : null}
        {c.loading ? <div className="tl-meta">{'Reading the conversation…'}</div> : null}
        {!c.open && !c.error ? <div className="tl-meta">{'Pick a conversation on the left.'}</div> : null}
        {v ? (
          <>
            <h3 className="tl-title">{v.title}</h3>
            <div className="tl-chips">{v.chips.concat(turnChips(s, s.turnId)).map((chip, i) => <span key={i} className="tl-chip">{chip}</span>)}</div>
            <div className="tl-meta">{v.when + ' · ' + v.who + ' · ' + v.cost + ' · ' + v.latency}</div>
            {v.answers.length > 1 ? (
              <div className="tl-answers" role="group" aria-label="Answers in this conversation">
                {v.answers.map((a) => (
                  <button key={a.id} type="button" className={'tl-chip-btn' + (a.on ? ' tl-on' : '')} aria-pressed={a.on ? 'true' : 'false'} title={a.question} onClick={() => act.openTurn(a.id)}>
                    {a.label + (a.failed ? ' · failed' : '') + turnChips(s, a.id).map((chip) => ' · ' + chip).join('')}
                  </button>
                ))}
              </div>
            ) : null}
            {v.truncatedNote ? <div className="tl-meta">{v.truncatedNote}</div> : null}
            {v.notes.map((n) => <div key={n} className="tl-note">{n}</div>)}
            {v.rating ? <div className="tl-meta">{v.rating.text + (v.rating.comment ? ' — “' + v.rating.comment + '”' : '')}</div> : null}
            {isGoldenSession(s.conv.data && s.conv.data.session_id) ? <FidelityCaveats /> : null}
            <ExpectedOutcome s={s} />
            <ConversationComments s={s} />
            <TurnLabels s={s} />
            <div className="tl-annot" data-tl-timeline="" ref={timelineRef}>
              <div className="tl-annot-main"><Timeline cards={v.cards} isOpen={opened} setOpen={act.setOpen} s={s} /></div>
              <MarginNotes s={s} rootRef={timelineRef} onMissing={setMissing} extra={suggestionMargin(s)} />
            </div>
            <UnplacedNotes s={s} also={missing} />
            <SuggestionList s={s} also={missing} />
            {notices.selectionError ? <div role="status" className="tl-warn">{notices.selectionError}</div> : null}
            {notices.error ? <div role="alert" className="tl-bad">{"Couldn't read the reviews: " + notices.error}</div> : null}
            <VerdictBar s={s} />
          </>
        ) : null}
      </section>
    </div>
  );
}
