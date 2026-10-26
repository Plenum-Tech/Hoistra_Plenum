// VerdictBar — the reviewer's verdict on the open answer (logic/annotate.js reviewView): Right, or Wrong with a note
// on the first failure, Save and Save & next, the blind-review line, a 409 shown beside the draft, and Retry after
// a failed save. ConversationComments is the comment list under the conversation header. Both draw what the view
// models say and call annotateActions.js by name; the draft lives in the store, so a refresh never loses it.
import React from 'react';
import * as ann from '../annotateActions.js';
import { commentsView, reviewView } from '../logic/annotate.js';

const mark = (m) => (m ? m.mark + (m.note ? ' — ' + m.note : '') : '');

export default function VerdictBar({ s }) {
  const r = reviewView(s);
  const noteRef = React.useRef(null);
  const focus = !!(r && r.focusNote);
  const turnId = r ? r.turnId : null;
  React.useEffect(() => { if (focus && noteRef.current) noteRef.current.focus(); }, [focus, turnId]);
  if (!r) return null;
  const id = r.turnId;
  return (
    <section aria-label="Your verdict on this answer" className="tl-verdict">
      {r.blindCopy ? <div className="tl-meta">{r.blindCopy}</div> : null}
      {r.inBatch ? (
        <div className="tl-verdict-row">
          <span className="tl-meta">{'In a review batch: ' + r.inBatch.reason}</span>
          {r.mine && r.inBatch.batchId ? <button type="button" className="tl-btn" onClick={() => ann.reviewBatch(r.inBatch.batchId)} disabled={r.saving}>{'Next answer in this batch'}</button> : null}
        </div>
      ) : null}
      <div role="radiogroup" aria-label="Verdict" className="tl-verdict-row">
        <span className="tl-verdict-label">{'Verdict'}</span>
        <label className="tl-radio">
          <input type="radio" name={'tl-verdict-' + id} checked={r.verdict === 'right'} onChange={() => ann.pickVerdict(id, 'right')} disabled={r.saving} />{'✓ Right'}
        </label>
        <label className="tl-radio">
          <input type="radio" name={'tl-verdict-' + id} checked={r.verdict === 'wrong'} onChange={() => ann.pickVerdict(id, 'wrong')} disabled={r.saving} />{'✗ Wrong'}
        </label>
        {r.mine ? <span className="tl-meta">{'Saved: ' + mark(r.mine)}</span> : null}
        {r.canClear ? <button type="button" className="tl-btn tl-push" onClick={() => ann.deleteVerdict(id)} disabled={r.saving}>{'Clear my verdict'}</button> : null}
      </div>
      <div className="tl-meta">{'Keys: r Right, w Wrong'}</div>
      {r.canEdit ? (
        <div className="tl-actions">
          <button type="button" className="tl-btn" onClick={() => ann.pickVerdict(id, 'wrong')} disabled={r.saving}>{'Edit my note'}</button>
        </div>
      ) : null}
      {r.editing ? (
        <div className="tl-verdict-wrong">
          <div className="tl-meta">{r.quote ? 'First failure at: “' + r.quote + '”' : 'Select the text where it first went wrong, inside one step of the answer above.'}</div>
          <textarea ref={noteRef} aria-label="Note on the first failure" placeholder="What went wrong first? First failure only, in your own words."
            value={r.note} onChange={(e) => ann.setNote(id, e.target.value)} maxLength={2000} className="tl-textarea" />
          <div className="tl-actions">
            <button type="button" className="tl-btn tl-btn-primary" onClick={() => ann.saveNote(id)} disabled={r.saving}>{r.saving ? 'Saving…' : 'Save'}</button>
            <button type="button" className="tl-btn" onClick={() => ann.saveAndNext(id, 'wrong')} disabled={r.saving}>{'Save & next'}</button>
            <button type="button" className="tl-btn" onClick={() => ann.cancelDraft(id)} disabled={r.saving}>{'Cancel'}</button>
          </div>
        </div>
      ) : null}
      {r.error ? (
        <div role="alert" className="tl-bad tl-verdict-alert">{r.error}<button type="button" className="tl-btn" onClick={() => ann.retryVerdict(id)}>{'Retry'}</button></div>
      ) : null}
      {r.conflict ? (
        <div role="alert" className="tl-conflict">
          <div className="tl-conflict-head">{r.conflict.message}</div>
          <div>{r.conflict.theirs ? 'Now saved: ' + mark(r.conflict.theirs) : 'Your verdict was cleared in another tab.'}</div>
          <div>{'Yours: ' + (r.verdict === 'right' ? '✓ Right' : '✗ Wrong') + (r.note ? ' — ' + r.note : '')}</div>
          <div className="tl-actions">
            <button type="button" className="tl-btn tl-btn-primary" onClick={() => ann.resolveConflict(id, true)}>{'Keep mine'}</button>
            <button type="button" className="tl-btn" onClick={() => ann.resolveConflict(id, false)}>{'Use theirs'}</button>
          </div>
        </div>
      ) : null}
      {r.others.length ? (
        <div className="tl-others">
          <div className="tl-verdict-label">{'Other reviewers'}</div>
          {r.others.map((o) => (
            <div key={o.id} className="tl-meta">{o.who + ': ' + o.mark + (o.note ? ' — ' + o.note : '') + (o.quote ? ' (“' + o.quote + '”)' : '')}</div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function ConversationComments({ s }) {
  const cm = commentsView(s);
  return (
    <section aria-label="Comments on this conversation" className="tl-comments">
      <div className="tl-verdict-label">{'Comments'}</div>
      {cm.rows.length ? cm.rows.map((c) => (
        <div key={c.id} className="tl-comment">
          <span className="tl-comment-who">{c.who + ': '}</span><span className="tl-comment-note">{c.note}</span>
          {c.mine ? <button type="button" className="tl-link" aria-label="Remove my comment" onClick={() => ann.deleteComment(c.id, c.base)}>{'Remove'}</button> : null}
        </div>
      )) : <div className="tl-meta">{'No comments yet.'}</div>}
      {cm.hiddenCopy ? <div className="tl-meta">{cm.hiddenCopy}</div> : null}
      <form className="tl-commentform" onSubmit={(e) => { e.preventDefault(); return ann.addComment(); }}>
        <input type="text" aria-label="Add a comment" placeholder="Add a comment for other reviewers…" value={cm.draft} onChange={(e) => ann.setCommentDraft(e.target.value)} maxLength={2000} />
        <button type="submit" className="tl-btn" disabled={cm.busy}>{cm.busy ? 'Saving…' : 'Comment'}</button>
      </form>
      {cm.error ? <div role="alert" className="tl-bad">{cm.error}</div> : null}
    </section>
  );
}
