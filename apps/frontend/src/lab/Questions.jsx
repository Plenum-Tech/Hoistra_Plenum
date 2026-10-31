// Questions — the labelling screen. Left: the company's answered questions, searchable, in their own scrolling column.
// Right: how the chosen answer was made, under a bar that stays on screen (the question, progress, open/close all). The
// question and the final answer are always open; every model call, tool and database step between them is one line that
// opens in place to show everything recorded (what it was sent and returned, its result and technical detail). Selecting
// any text inside a step opens a dialog to label that text Right / Wrong / Not sure with a note; "Label this whole step"
// does the same for the step. Under each step: everyone's labels with the selected text, who gave them, their Trace Lab
// role and when; the person's own can be edited or removed. A label another window changed meanwhile says so and reloads.
// A label may also say what should happen in the step and when ("only if"). "Label several steps together" puts a tick
// box on every step; the chosen steps get one label and note, and each its own "what should happen" and "only if".
import React from 'react';
import { labApi } from './labApi.js';
import { StepBody } from '../tracelab/views/Timeline.jsx';
import { EXPECTED_MAX, GROUP_MAX, GROUP_MIN, LABEL_CHOICES, NOTE_MAX, errText, groupsOf, isStale, labelsByStep, progressLine, questionRow,
  selectedIn, stepsOf, stepsText } from './logic/labelling.js';


export function QuestionList({ rows, selected, loading, error, q, onQ, onSearch, onOpen, more, onMore }) {
  return (
    <section className="lab-qlist" aria-label="Questions">
      <form className="lab-search" onSubmit={(e) => { e.preventDefault(); onSearch(); }}>
        <label className="lab-visually-hidden" htmlFor="lab-q">{'Search questions'}</label>
        <input id="lab-q" type="search" placeholder="Search questions" value={q} onChange={(e) => onQ(e.target.value)} />
        <button type="submit" className="lab-btn lab-btn-quiet lab-btn-sm">{'Search'}</button>
      </form>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {loading && !rows.length ? <p className="lab-small">{'Reading the questions…'}</p> : null}
      {!loading && !error && !rows.length ? <p className="lab-small">{'No questions in this period yet.'}</p> : null}
      <ul className="lab-qitems">
        {rows.map((r) => (
          <li key={r.key}>
            <button type="button" className={'lab-qitem' + (r.key === selected ? ' lab-on' : '')} aria-current={r.key === selected ? 'true' : undefined}
              onClick={() => onOpen(r.key)}>
              <span className="lab-qtext">{r.text}</span>
              <span className="lab-small">{[r.when, r.turns > 1 ? r.turns + ' questions' : '', r.asker].filter(Boolean).join(' · ')}</span>
            </button>
          </li>
        ))}
      </ul>
      {more ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onMore} disabled={loading}>{loading ? 'Reading…' : 'Show more'}</button> : null}
    </section>
  );
}

// The label dialog: what is being labelled (the step, and the selected text when there is one), the three choices, a note.
export function LabelDialog({ d, onPick, onNote, onField, onSave, onCancel }) {
  const boxRef = React.useRef(null);
  React.useEffect(() => {
    const first = boxRef.current && boxRef.current.querySelector('[role="radio"]');
    if (first) first.focus();
  }, []);
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); onCancel(); } };
  return (
    <div className="lab-dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={boxRef} className="lab-dialog" role="dialog" aria-modal="true" aria-labelledby="lab-dialog-title" onKeyDown={onKey}>
        <h2 id="lab-dialog-title">{d.edit ? 'Change your label' : (d.quote ? 'Label the selected text' : 'Label this step')}</h2>
        <p className="lab-small">{'Step ' + d.stepN + ' · ' + d.stepText}</p>
        {d.quote ? <blockquote className="lab-quote">{d.quote}</blockquote> : null}
        <div role="radiogroup" aria-label="Your label" className="lab-choices">
          {LABEL_CHOICES.map((c) => (
            <button key={c.key} type="button" role="radio" aria-checked={d.label === c.key ? 'true' : 'false'}
              className={'lab-choice lab-choice-' + c.key + (d.label === c.key ? ' lab-on' : '')} onClick={() => onPick(c.key)} disabled={d.saving}>
              {c.text}
            </button>
          ))}
        </div>
        <label className="lab-field"><span>{'Note'}</span>
          <textarea className="lab-note" rows={4} maxLength={NOTE_MAX} value={d.note} placeholder="What is right or wrong here, in your own words"
            onChange={(e) => onNote(e.target.value)} disabled={d.saving} />
        </label>
        <RuleFields value={d} onField={onField} disabled={d.saving} />
        {d.error ? <div role="alert" className="lab-error">{d.error}</div> : null}
        <div className="lab-row lab-dialog-actions">
          <button type="button" className="lab-btn" onClick={onSave} disabled={d.saving || !d.label}>{d.saving ? 'Saving…' : 'Save label'}</button>
          <button type="button" className="lab-btn lab-btn-quiet" onClick={onCancel} disabled={d.saving}>{'Cancel'}</button>
        </div>
      </div>
    </div>
  );
}

// What should happen in a step, and when: optional, on a single label and on each step of a group.
function RuleFields({ value, onField, disabled, idSuffix }) {
  return (
    <>
      <label className="lab-field"><span>{'What should happen in this step'}</span>
        <textarea className="lab-note lab-note-short" rows={2} maxLength={EXPECTED_MAX} value={value.expected || ''} disabled={disabled}
          placeholder="Optional: the right behaviour, e.g. count only this company's vendors" onChange={(e) => onField('expected', e.target.value)} />
      </label>
      <label className="lab-field"><span>{'Only if'}</span>
        <input className="lab-input" type="text" maxLength={EXPECTED_MAX} value={value.onlyIf || ''} disabled={disabled} name={'only-if' + (idSuffix || '')}
          placeholder="Optional: when it applies, e.g. the question names a vendor" onChange={(e) => onField('onlyIf', e.target.value)} />
      </label>
    </>
  );
}

// Several steps labelled together: one label and note for all of them, and for each step what should happen and when.
export function GroupDialog({ g, onPick, onNote, onItem, onSave, onCancel }) {
  const boxRef = React.useRef(null);
  React.useEffect(() => {
    const first = boxRef.current && boxRef.current.querySelector('[role="radio"]');
    if (first) first.focus();
  }, []);
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); onCancel(); } };
  const n = g.items.length;
  return (
    <div className="lab-dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={boxRef} className="lab-dialog lab-dialog-wide" role="dialog" aria-modal="true" aria-labelledby="lab-group-title" onKeyDown={onKey}>
        <h2 id="lab-group-title">{'Label ' + n + ' steps together'}</h2>
        <p className="lab-small">{stepsText(g.items.map((it) => it.n)) + ': one label and note for all of them, and for each step what should happen and when.'}</p>
        <div role="radiogroup" aria-label="Your label" className="lab-choices">
          {LABEL_CHOICES.map((c) => (
            <button key={c.key} type="button" role="radio" aria-checked={g.label === c.key ? 'true' : 'false'}
              className={'lab-choice lab-choice-' + c.key + (g.label === c.key ? ' lab-on' : '')} onClick={() => onPick(c.key)} disabled={g.saving}>
              {c.text}
            </button>
          ))}
        </div>
        <label className="lab-field"><span>{'Note, for all these steps'}</span>
          <textarea className="lab-note" rows={3} maxLength={NOTE_MAX} value={g.note} placeholder="The rule overall, in your own words"
            onChange={(e) => onNote(e.target.value)} disabled={g.saving} />
        </label>
        <ol className="lab-group-items">
          {g.items.map((it, i) => (
            <li key={it.key} className="lab-group-item">
              <div className="lab-group-step"><span className="lab-step-n" aria-hidden="true">{it.n}</span><span>{'Step ' + it.n + ' · ' + it.text}</span></div>
              <RuleFields value={it} onField={(k, v) => onItem(i, k, v)} disabled={g.saving} idSuffix={'-' + it.n} />
            </li>
          ))}
        </ol>
        {g.error ? <div role="alert" className="lab-error">{g.error}</div> : null}
        <div className="lab-row lab-dialog-actions">
          <button type="button" className="lab-btn" onClick={onSave} disabled={g.saving || !g.label}>{g.saving ? 'Saving…' : 'Save ' + n + ' labels'}</button>
          <button type="button" className="lab-btn lab-btn-quiet" onClick={onCancel} disabled={g.saving}>{'Cancel'}</button>
        </div>
      </div>
    </div>
  );
}

export function StepLabels({ items, busy, onEdit, onRemove, groups, onRemoveGroup }) {
  if (!items || !items.length) return null;
  return (
    <ul className="lab-others" aria-label="Labels on this step">
      {items.map((o) => (
        <li key={o.id} className={o.mine ? 'lab-mine' : ''}>
          <span className={'lab-tag lab-tag-' + o.label}>{o.labelText}</span>
          <span>{o.mine ? 'You' : o.who}</span>
          <span className="lab-small">{o.role + ' · ' + o.when + (o.edited ? ' · edited' : '')}</span>
          {o.mine ? (
            <span className="lab-row lab-mine-actions">
              <button type="button" className="lab-link" onClick={() => onEdit(o)} disabled={busy}>{'Edit'}</button>
              <button type="button" className="lab-link" onClick={() => onRemove(o)} disabled={busy}>{'Remove'}</button>
            </span>
          ) : null}
          {o.quote ? <blockquote className="lab-quote lab-quote-small">{o.quote}</blockquote> : null}
          {o.note ? <div className="lab-small lab-other-note">{o.note}</div> : null}
          {o.expected ? <div className="lab-small lab-other-note lab-rule"><strong>{'Should: '}</strong>{o.expected}</div> : null}
          {o.onlyIf ? <div className="lab-small lab-other-note lab-rule"><strong>{'Only if: '}</strong>{o.onlyIf}</div> : null}
          {o.group ? (
            <div className="lab-small lab-other-note lab-group-note">
              {'Labelled together: ' + (stepsText((groups || {})[o.group]) || 'several steps')}
              {o.mine && onRemoveGroup ? <button type="button" className="lab-link" onClick={() => onRemoveGroup(o)} disabled={busy}>{'Remove the group'}</button> : null}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

// One step's head. The question and the answer have nothing to open; every other step's name opens and closes it.
function StepHead({ st, open, onToggle, onWhole, picking, picked, onPick }) {
  const parts = (
    <>
      <span className="lab-step-n" aria-hidden="true">{st.n}</span>
      <span className="lab-step-kind">{st.kindText}</span>
      {st.isEnd || !st.title ? null : <span className="lab-step-title">{st.title}</span>}
      {st.facts ? <span className="lab-step-facts">{st.facts}</span> : null}
      {st.failed ? <span className="lab-bad">{'Failed'}</span> : null}
    </>
  );
  return (
    <div className="lab-step-head">
      {picking ? (
        <button type="button" role="checkbox" aria-checked={picked ? 'true' : 'false'} aria-label={'Choose step ' + st.n}
          className={'lab-pick' + (picked ? ' lab-on' : '')} onClick={() => onPick(st)}>{picked ? '✓' : ''}</button>
      ) : null}
      {st.isEnd ? <div className="lab-step-name">{parts}</div> : (
        <button type="button" className="lab-step-name lab-step-toggle" aria-expanded={open ? 'true' : 'false'} aria-controls={'lab-body-' + st.n}
          onClick={() => onToggle(st.key)}>
          <span className={'lab-chev' + (open ? ' lab-chev-open' : '')} aria-hidden="true" />
          {parts}
        </button>
      )}
      <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm lab-step-label" onClick={() => onWhole(st)}>{'Label this whole step'}</button>
    </div>
  );
}

// An answer as steps. `startOpen` opens every step on first paint (the tests read the full content that way); a person
// opens the steps they want. Inside an opened step everything recorded is shown, and each part can still be closed.
export function Flow({ conv, steps, byStep, busy, errors, onTurn, onSelect, onWhole, onEdit, onRemove, startOpen, focusStep,
  picking, picked, onPickMode, onPick, onGroup, groups, onRemoveGroup }) {
  const active = conv.active || {};
  const turns = conv.turns || [];
  const middle = steps.filter((st) => !st.isEnd);
  const [open, setOpen] = React.useState(() => (startOpen ? Object.fromEntries(middle.map((st) => [st.key, true]))
    : (focusStep ? { [focusStep]: true } : {})));
  // Arriving from "Open" on All labels: bring that step into view, opened, and mark it for a moment.
  const listRef = React.useRef(null);
  React.useEffect(() => {
    const el = focusStep && listRef.current ? Array.from(listRef.current.children).find((li) => li.dataset.key === focusStep) : null;
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, [focusStep]);
  const [parts, setParts] = React.useState({});
  // An opened model call shows what it was sent and what it returned; its usage table and raw metadata wait behind a click.
  const quiet = new Set(middle.filter((st) => st.kind === 'model').flatMap((st) => [st.key + ':usage', st.key + ':raw']));
  const isPartOpen = (k) => (k in parts ? parts[k] : !quiet.has(k));
  const setPartOpen = (k, o) => setParts((p) => Object.assign({}, p, { [k]: o }));
  const onToggle = (key) => setOpen((p) => Object.assign({}, p, { [key]: !p[key] }));
  const allOpen = middle.length > 0 && middle.every((st) => open[st.key]);
  const setAll = (o) => setOpen(o ? Object.fromEntries(middle.map((st) => [st.key, true])) : {});
  const question = steps.find((st) => st.kind === 'user');
  const chosen = Object.keys(picked || {}).filter((k) => picked[k]).length;
  return (
    <section className="lab-flow" aria-label="How this answer was made">
      <div className="lab-flow-bar">
        {question && question.text ? <div className="lab-flow-q" title={question.text}>{question.text}</div> : null}
        <div className="lab-row lab-flow-tools">
          <span className="lab-small">{progressLine(steps, byStep) + ' · select any text in an open step to label it'}</span>
          {middle.length ? (
            <button type="button" className="lab-link lab-push" onClick={() => setAll(!allOpen)}>
              {allOpen ? 'Close all steps' : 'Open all steps'}
            </button>
          ) : null}
        </div>
        {picking ? (
          <div className="lab-pickbar" role="status">
            <span>{chosen + ' of up to ' + GROUP_MAX + ' steps chosen · tick the steps that go together'}</span>
            <button type="button" className="lab-btn lab-btn-sm lab-push" onClick={onGroup} disabled={chosen < GROUP_MIN}>
              {chosen >= GROUP_MIN ? 'Label ' + chosen + ' steps together' : 'Choose at least ' + GROUP_MIN + ' steps'}
            </button>
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onPickMode(false)}>{'Cancel'}</button>
          </div>
        ) : (onPickMode ? (
          <div className="lab-row"><button type="button" className="lab-link" onClick={() => onPickMode(true)}>{'Label several steps together'}</button></div>
        ) : null)}
        {turns.length > 1 ? (
          <div className="lab-turns" role="group" aria-label="Questions in this conversation">
            {turns.map((t, i) => (
              <button key={t.turn_id} type="button" className={'lab-turn' + (t.turn_id === active.id ? ' lab-on' : '')}
                aria-pressed={t.turn_id === active.id ? 'true' : 'false'} onClick={() => onTurn(t.turn_id)}>{'Question ' + (i + 1)}</button>
            ))}
          </div>
        ) : null}
      </div>
      <ol className="lab-steps" ref={listRef}>
        {steps.map((st) => {
          const shown = st.isEnd || !!open[st.key];
          return (
            <li key={st.key} data-key={st.key} className={'lab-step lab-step-' + st.kind + (st.nested ? ' lab-step-nested' : '')
              + (shown ? '' : ' lab-step-shut') + (st.key === focusStep ? ' lab-step-focus' : '') + (picking && picked[st.key] ? ' lab-step-picked' : '')}>
              <StepHead st={st} open={shown} onToggle={onToggle} onWhole={onWhole} picking={picking} picked={!!(picked && picked[st.key])} onPick={onPick} />
              {shown ? (
                <div id={'lab-body-' + st.n} className="lab-step-content" data-step={st.key} onMouseUp={(e) => onSelect(st, e.currentTarget)}>
                  {st.isEnd ? <div className="lab-step-text">{st.text || '—'}</div>
                    : st.kind === 'agent' ? <div className="lab-step-text lab-agent-text">{st.text}</div>
                      : <div className="lab-step-body"><StepBody card={st.card} isOpen={isPartOpen} setOpen={setPartOpen} /></div>}
                </div>
              ) : null}
              {errors[st.key] ? <div role="alert" className="lab-error">{errors[st.key]}</div> : null}
              <StepLabels items={byStep[st.key]} busy={busy} onEdit={(o) => onEdit(st, o)} onRemove={(o) => onRemove(st, o)}
                groups={groups} onRemoveGroup={onRemoveGroup ? (o) => onRemoveGroup(st, o) : null} />
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function Questions({ focus }) {
  const [q, setQ] = React.useState('');
  const [list, setList] = React.useState({ rows: [], loading: true, error: '', next: null });
  const [sel, setSel] = React.useState(null);
  const [conv, setConv] = React.useState({ data: null, loading: false, error: '' });
  const [labels, setLabels] = React.useState({ rows: [], me: null, error: '' });
  const [dialog, setDialog] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [errors, setErrors] = React.useState({});
  const [picking, setPicking] = React.useState(false);
  const [picked, setPicked] = React.useState({});
  const [group, setGroup] = React.useState(null);
  const ask = React.useRef(0);
  const pane = React.useRef(null);

  const loadList = React.useCallback(async (cursor) => {
    setList((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      const out = await labApi.questions({ q: q.trim() || undefined, cursor: cursor || undefined, limit: 30 });
      const rows = ((out && out.conversations) || []).map(questionRow);
      setList((p) => ({ rows: cursor ? p.rows.concat(rows) : rows, loading: false, error: '', next: (out && out.next_cursor) || null }));
    } catch (e) {
      setList((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, [q]);
  React.useEffect(() => { loadList(null); }, []);
  React.useEffect(() => { if (focus && focus.key) open(focus.key, focus.turn); }, [focus]);

  const loadLabels = async (turnId) => {
    try {
      const out = await labApi.turnLabels(turnId);
      setLabels({ rows: (out && out.labels) || [], me: (out && out.me) || null, error: '' });
    } catch (e) {
      setLabels({ rows: [], me: null, error: errText(e) });
    }
  };

  const open = async (key, turn) => {
    const mine = (ask.current += 1);
    setSel(key); setDialog(null); setErrors({}); setPicking(false); setPicked({}); setGroup(null);
    if (pane.current) pane.current.scrollTop = 0;
    setConv({ data: null, loading: true, error: '' });
    setLabels({ rows: [], me: null, error: '' });
    try {
      const out = await labApi.question(key, turn);
      if (mine !== ask.current) return;
      const data = (out && out.conversation) || null;
      setConv({ data, loading: false, error: '' });
      if (data && data.active) await loadLabels(data.active.id);
    } catch (e) {
      if (mine === ask.current) setConv({ data: null, loading: false, error: errText(e) });
    }
  };

  const data = conv.data;
  const turnId = data && data.active ? data.active.id : null;
  const steps = React.useMemo(() => stepsOf(data && data.active), [data]);
  const byStep = labelsByStep(labels.rows, labels.me);
  const groups = groupsOf(labels.rows, steps);
  const stepText = (st) => [st.kindText, st.isEnd ? '' : st.title].filter(Boolean).join(' · ');

  const onSelect = (st, el) => {
    if (dialog || group || picking) return;
    const quote = selectedIn(el, typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null);
    if (!quote) return;
    setDialog({ stepKey: st.key, stepN: st.n, stepText: stepText(st), title: st.isEnd ? st.kindText : (st.title || st.kindText),
      quote, label: null, note: '', expected: '', onlyIf: '', saving: false, error: '' });
  };
  const onWhole = (st) => setDialog({ stepKey: st.key, stepN: st.n, stepText: stepText(st), title: st.isEnd ? st.kindText : (st.title || st.kindText),
    quote: '', label: null, note: '', expected: '', onlyIf: '', saving: false, error: '' });
  const onEdit = (st, o) => setDialog({ stepKey: st.key, stepN: st.n, stepText: stepText(st), title: null, quote: o.quote, label: o.label,
    note: o.note, expected: o.expected, onlyIf: o.onlyIf, edit: { lineage: o.lineage, base: o.id }, saving: false, error: '' });
  const rule = (v) => (v || '').trim() || null;

  const onSave = async () => {
    const d = dialog;
    if (!d || !d.label || !turnId) return;
    setDialog(Object.assign({}, d, { saving: true, error: '' }));
    try {
      const body = d.edit ? { label: d.label, note: d.note.trim() || null, lineage_id: d.edit.lineage, base: d.edit.base,
        expected: rule(d.expected), only_if: rule(d.onlyIf) }
        : { step_key: d.stepKey, label: d.label, note: d.note.trim() || null, step_title: d.title, quote: d.quote || null,
          expected: rule(d.expected), only_if: rule(d.onlyIf) };
      const out = await labApi.saveLabel(turnId, body);
      const saved = out && out.label;
      setLabels((p) => ({ rows: p.rows.filter((x) => !(saved && (x.lineage_id || x.id) === saved.lineage_id)).concat(saved ? [saved] : []),
        me: p.me, error: '' }));
      setDialog(null);
      if (typeof window !== 'undefined' && window.getSelection) window.getSelection().removeAllRanges();
    } catch (e) {
      setDialog(Object.assign({}, d, { saving: false, error: isStale(e) ? 'This label changed in another window. Close and try again.' : errText(e) }));
      if (isStale(e)) loadLabels(turnId);
    }
  };

  const onRemove = async (st, o) => {
    if (!turnId) return;
    setBusy(true); setErrors((p) => Object.assign({}, p, { [st.key]: '' }));
    try {
      await labApi.clearLabel(turnId, o.lineage, o.id);
      setLabels((p) => ({ rows: p.rows.filter((x) => (x.lineage_id || x.id) !== o.lineage), me: p.me, error: '' }));
    } catch (e) {
      setErrors((p) => Object.assign({}, p, { [st.key]: isStale(e) ? 'This label changed in another window. It has been reloaded.' : errText(e) }));
      if (isStale(e)) loadLabels(turnId);
    } finally {
      setBusy(false);
    }
  };

  const onPickMode = (on) => { setPicking(on); setPicked({}); };
  const onPick = (st) => setPicked((p) => {
    const n = Object.assign({}, p);
    if (n[st.key]) delete n[st.key];
    else if (Object.keys(n).length < GROUP_MAX) n[st.key] = true;
    return n;
  });
  const onGroup = () => setGroup({
    items: steps.filter((st) => picked[st.key]).map((st) => ({ key: st.key, n: st.n, text: stepText(st),
      title: st.isEnd ? st.kindText : (st.title || st.kindText), expected: '', onlyIf: '' })),
    label: null, note: '', saving: false, error: '' });
  const onSaveGroup = async () => {
    const g = group;
    if (!g || !g.label || !turnId) return;
    setGroup(Object.assign({}, g, { saving: true, error: '' }));
    try {
      const out = await labApi.saveGroup(turnId, { label: g.label, note: g.note.trim() || null,
        items: g.items.map((it) => ({ step_key: it.key, step_title: it.title, expected: rule(it.expected), only_if: rule(it.onlyIf) })) });
      const saved = (out && out.labels) || [];
      setLabels((p) => ({ rows: p.rows.concat(saved), me: p.me, error: '' }));
      setGroup(null); setPicking(false); setPicked({});
    } catch (e) {
      setGroup(Object.assign({}, g, { saving: false, error: errText(e) }));
    }
  };
  const onRemoveGroup = async (st, o) => {
    if (!turnId || !o.group) return;
    setBusy(true); setErrors((p) => Object.assign({}, p, { [st.key]: '' }));
    try {
      await labApi.clearGroup(turnId, o.group);
      setLabels((p) => ({ rows: p.rows.filter((x) => x.group_id !== o.group), me: p.me, error: '' }));
    } catch (e) {
      setErrors((p) => Object.assign({}, p, { [st.key]: isStale(e) ? 'These labels changed in another window. They have been reloaded.' : errText(e) }));
      if (isStale(e)) loadLabels(turnId);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lab-questions">
      <QuestionList rows={list.rows} selected={sel} loading={list.loading} error={list.error} q={q} onQ={setQ}
        onSearch={() => loadList(null)} onOpen={(key) => open(key)} more={!!list.next} onMore={() => loadList(list.next)} />
      <div className="lab-flow-wrap" ref={pane}>
        {!sel ? <div className="lab-empty"><h2>{'Pick a question'}</h2><p className="lab-small">{'Choose a question on the left to see how its answer was made. Open a step, then select any text in it to label it.'}</p></div> : null}
        {conv.loading ? <p className="lab-small">{'Reading the answer…'}</p> : null}
        {conv.error ? <div role="alert" className="lab-error">{conv.error}</div> : null}
        {labels.error ? <div role="alert" className="lab-error">{'Could not read the labels: ' + labels.error}</div> : null}
        {data ? <Flow key={turnId || 'none'} conv={data} steps={steps} byStep={byStep} busy={busy} errors={errors} onTurn={(t) => open(sel, t)}
          onSelect={onSelect} onWhole={onWhole} onEdit={onEdit} onRemove={onRemove}
          focusStep={focus && turnId === focus.turn ? focus.step : null} picking={picking} picked={picked} onPickMode={onPickMode}
          onPick={onPick} onGroup={onGroup} groups={groups} onRemoveGroup={onRemoveGroup} /> : null}
      </div>
      {dialog ? <LabelDialog d={dialog} onPick={(label) => setDialog(Object.assign({}, dialog, { label }))}
        onNote={(note) => setDialog(Object.assign({}, dialog, { note }))} onField={(k, v) => setDialog(Object.assign({}, dialog, { [k]: v }))}
        onSave={onSave} onCancel={() => setDialog(null)} /> : null}
      {group ? <GroupDialog g={group} onPick={(label) => setGroup(Object.assign({}, group, { label }))}
        onNote={(note) => setGroup(Object.assign({}, group, { note }))}
        onItem={(i, k, v) => setGroup(Object.assign({}, group, { items: group.items.map((it, j) => (j === i ? Object.assign({}, it, { [k]: v }) : it)) }))}
        onSave={onSaveGroup} onCancel={() => setGroup(null)} /> : null}
    </div>
  );
}
