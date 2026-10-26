// LabelsView — one run's labels (22 Oct 2026 feedback, after Cartwheel's "Structured labeling"): the run's questions as
// rows and the failure modes as columns. In each cell the caller gives their own value with a two-way toggle, negative
// or positive (positive: the failure is in that question's answer), and clicking the pressed side again clears it.
// Under the toggle everyone else's newest values are counted, with who said what and when on hover, and a split cell
// says "Disagree", so disagreement stays in view. A click shows at once and is put back, with the reason, if the
// service refuses it. Failure modes are shared by every company: the platform team and the consultant add them (from
// the coding agent's taxonomy), rename or redefine one, or retire a column; everyone else labels.
import React from 'react';
import { labApi } from './labApi.js';
import { ROLE_TEXT, errText } from './logic/labelling.js';
import './labels.css';

const VALUES = ['negative', 'positive'];
const ROLE = Object.assign({}, ROLE_TEXT, { consultant: 'Consultant' });

export function when(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// The service marks the caller's own cell (`mine`); a value set here before the service answered carries the same mark.
const isMine = (e, meId) => !!e && (e.mine !== undefined ? e.mine === true : !!meId && e.author_id === meId);
const shortId = (id) => { const s = String(id || ''); return s.length > 12 ? s.slice(0, 10) + '…' : s; };
const whoOf = (e) => (e.author_email || (e.source === 'coding_agent' ? 'Coding agent' : 'Someone'))
  + (e.author_role ? ' (' + (ROLE[e.author_role] || e.author_role) + ')' : '');
const atOf = (e) => (when(e.at) ? ', annotated at ' + when(e.at) : '');

// One cell as shown: the caller's value, everyone else's counted, whether people disagree, and who said what and when.
export function cellView(entries, meId) {
  const list = (entries || []).filter((e) => e && (e.value === 'positive' || e.value === 'negative'));
  const mine = list.find((e) => isMine(e, meId)) || null;
  const others = list.filter((e) => e !== mine).sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
  const pos = others.filter((e) => e.value === 'positive').length;
  return {
    mine: mine ? mine.value : null, mineAt: mine ? mine.at || null : null, pos, neg: others.length - pos, others: others.length,
    disagree: new Set(list.map((e) => e.value)).size > 1,
    who: (mine ? ['You: ' + mine.value + atOf(mine)] : []).concat(others.map((e) => whoOf(e) + ': ' + e.value + atOf(e))).join('\n')
  };
}

// The matrix with the caller's value in one cell replaced (entry) or cleared (null), as a copy: everything else is kept.
export function withMine(data, qid, modeId, meId, entry) {
  if (!data) return data;
  const cells = Object.assign({}, data.cells);
  const row = Object.assign({}, cells[qid]);
  const rest = (row[modeId] || []).filter((e) => !isMine(e, meId));
  row[modeId] = entry ? [entry].concat(rest) : rest;
  cells[qid] = row;
  return Object.assign({}, data, { cells });
}

function Empty({ title, children }) {
  return (
    <div className="lab-empty lab-empty-big">
      <h2>{title}</h2>
      <p className="lab-small">{children}</p>
    </div>
  );
}

// Add a failure mode, or change one's name or definition. onSubmit resolves once the matrix shows the change; a refusal
// stays in the form with the service's reason.
export function ModeForm({ mode, onSubmit, onCancel }) {
  const editing = !!mode;
  const [name, setName] = React.useState(editing ? mode.name || '' : '');
  const [definition, setDefinition] = React.useState(editing ? mode.definition || '' : '');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const id = editing ? 'lab-lv-mode-' + mode.lineage_id : 'lab-lv-mode-new';
  const submit = async (e) => {
    e.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true);
    setError('');
    try {
      await onSubmit({ name: name.trim(), definition: definition.trim() || null });
    } catch (err) {
      setError(errText(err));
      setBusy(false);
    }
  };
  return (
    <form className="lab-lv-panel" onSubmit={submit} aria-labelledby={id}>
      <h2 id={id}>{editing ? 'Edit failure mode' : 'Add failure mode'}</h2>
      <p className="lab-small">{editing
        ? 'The new name and definition show in every run. Values people already gave stay.'
        : 'A new column in every run’s labels. Use the name and definition from the coding agent’s taxonomy.'}</p>
      <label className="lab-lv-field"><span>{'Name'}</span>
        <input className="lab-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. wrong_company_scope" />
      </label>
      <label className="lab-lv-field"><span>{'Definition'}</span>
        <textarea className="lab-note" value={definition} onChange={(e) => setDefinition(e.target.value)} maxLength={4000} rows={3}
          placeholder="When is this failure in an answer, and when is it not?" />
      </label>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      <div className="lab-row">
        <button type="submit" className="lab-btn lab-btn-sm" disabled={busy || !name.trim()}>
          {busy ? 'Saving…' : (editing ? 'Save changes' : 'Add failure mode')}
        </button>
        {onCancel ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onCancel} disabled={busy}>{'Cancel'}</button> : null}
      </div>
    </form>
  );
}

// A column head: the failure mode's name, its definition on hover, and for the platform team and the consultant Edit
// and Retire (asked once, in place).
function ModeHead({ mode, canEdit, asking, busy, onEdit, onAsk, onRetire }) {
  return (
    <th scope="col" className="lab-lv-mode">
      <span className={'lab-lv-mode-name' + (mode.definition ? ' lab-lv-has-def' : '')} title={mode.definition || undefined}>{mode.name}</span>
      {canEdit && asking ? (
        <span className="lab-lv-mode-tools" role="group" aria-label={'Retire ' + mode.name + '?'}>
          <span className="lab-small">{'Retire in every run?'}</span>
          <button type="button" className="lab-link lab-danger" onClick={() => onRetire(mode)} disabled={busy}>{'Retire'}</button>
          <button type="button" className="lab-link" onClick={() => onAsk(null)} disabled={busy}>{'Keep'}</button>
        </span>
      ) : null}
      {canEdit && !asking ? (
        <span className="lab-lv-mode-tools">
          <button type="button" className="lab-link" onClick={() => onEdit(mode)} disabled={busy} aria-label={'Edit ' + mode.name}>{'Edit'}</button>
          <button type="button" className="lab-link lab-danger" onClick={() => onAsk(mode.lineage_id)} disabled={busy}
            aria-label={'Retire ' + mode.name}>{'Retire'}</button>
        </span>
      ) : null}
    </th>
  );
}

// One cell: the caller's two-way toggle, then everyone else's values counted (who and when on hover).
function Cell({ q, mode, c, busy, onCell }) {
  const tint = c.mine === 'positive' ? ' lab-lv-pos' : (c.mine === 'negative' ? ' lab-lv-neg' : '');
  return (
    <td className={'lab-lv-cell' + tint}>
      <div className="lab-lv-toggle" role="group" aria-label={'Your label for ' + q.code + ', ' + mode.name}>
        {VALUES.map((v) => {
          const on = c.mine === v;
          return (
            <button key={v} type="button" className={'lab-lv-opt lab-lv-opt-' + v + (on ? ' lab-on' : '')} aria-pressed={on ? 'true' : 'false'}
              disabled={!!busy} title={on ? 'Click again to clear' : undefined} onClick={() => onCell && onCell(q, mode, on ? null : v)}>{v}</button>
          );
        })}
      </div>
      {c.others ? (
        <div className="lab-lv-others" title={c.who}>
          {c.disagree ? <span className="lab-lv-disagree">{'Disagree'}</span> : null}
          {c.pos ? <span className="lab-lv-count lab-lv-count-positive">{c.pos + ' positive'}</span> : null}
          {c.neg ? <span className="lab-lv-count lab-lv-count-negative">{c.neg + ' negative'}</span> : null}
          <span className="lab-visually-hidden">{c.who}</span>
        </div>
      ) : null}
    </td>
  );
}

// The page for one run, from what the service returned (GET /lab/runs/{code}/labels). Nothing here reads the service:
// LabelsView does, and hands the clicks back through onCell and the onAddMode / onEditMode / onRetireMode promises.
export function LabelsMatrix({ run, data, me, loading, error, saving, onCell, onOpenQuestion, onAddMode, onEditMode, onRetireMode,
  onRetry }) {
  const [form, setForm] = React.useState(null);        // null, { add: true }, or { mode } while one column is edited
  const [asking, setAsking] = React.useState(null);    // the column whose retirement is being confirmed
  const [modeBusy, setModeBusy] = React.useState(false);
  const [modeError, setModeError] = React.useState('');
  const canEdit = !!(me && me.all_companies);

  if (!run) {
    return (
      <div className="lab-page-body lab-page-full">
        <div className="lab-page-head"><h1>{'Labels'}</h1></div>
        <Empty title="Pick a run">{'Choose a run to label its questions against the failure modes.'}</Empty>
      </div>
    );
  }
  const r = (data && data.run) || { code: String(run).toUpperCase() };
  const modes = (data && data.modes) || [];
  const questions = (data && data.questions) || [];
  const head = (
    <div className="lab-page-head lab-head-row">
      <div>
        <h1>{'Labels'}</h1>
        <p className="lab-small lab-big">{'Failure modes for ' + r.code + (r.title ? ' · ' + r.title : '')
          + '. Positive means the failure is in the question’s answer; negative means it is not.'}</p>
      </div>
      {canEdit && modes.length && !form ? (
        <button type="button" className="lab-btn lab-btn-sm" onClick={() => { setAsking(null); setForm({ add: true }); }}>{'Add failure mode'}</button>
      ) : null}
    </div>
  );
  if (!data) {
    return (
      <div className="lab-page-body lab-page-full">
        {head}
        {error ? <div role="alert" className="lab-error">{error}</div> : <p className="lab-small">{'Reading…'}</p>}
        {error && onRetry ? <div className="lab-row"><button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onRetry}>{'Try again'}</button></div> : null}
      </div>
    );
  }

  const cells = data.cells || {};
  const meId = data.me || (me && me.user_id) || null;
  const views = questions.map((q) => modes.map((m) => cellView((cells[q.id] || {})[m.lineage_id], meId)));
  const mineN = views.reduce((n, row) => n + row.filter((c) => c.mine).length, 0);
  const adding = canEdit && (form ? !!form.add : !modes.length);
  const editing = canEdit && form && form.mode ? form.mode : null;
  const submitMode = async (body) => {
    if (editing) await onEditMode(editing, body);
    else await onAddMode(body);
    setForm(null);
  };
  const retire = async (mode) => {
    setModeBusy(true);
    setModeError('');
    try {
      await onRetireMode(mode);
      setAsking(null);
    } catch (e) {
      setModeError('Could not retire ' + mode.name + ': ' + errText(e));
    } finally {
      setModeBusy(false);
    }
  };

  return (
    <div className="lab-page-body lab-page-full lab-lv-page">
      {head}
      <div className="lab-lv-bar">
        <span>{data.last_updated ? 'Last updated ' + when(data.last_updated) : 'No cells labelled yet'}</span>
        {questions.length && modes.length ? <span>{'You have labelled ' + mineN + ' of ' + questions.length * modes.length + ' cells'}</span> : null}
        {loading ? <span>{'Reading…'}</span> : null}
      </div>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {modeError ? <div role="alert" className="lab-error">{modeError}</div> : null}
      {!modes.length ? (
        <Empty title="No failure modes yet">{canEdit
          ? 'Failure modes come from the coding agent’s taxonomy. Add the first one here: it becomes a column in every run, and each reviewer marks every question positive or negative for it.'
          : 'Failure modes come from the coding agent’s taxonomy. The platform team or the consultant adds them here; then you mark each question positive or negative for each one.'}</Empty>
      ) : null}
      {adding ? <ModeForm key="add" onSubmit={submitMode} onCancel={modes.length ? () => setForm(null) : null} /> : null}
      {editing ? <ModeForm key={'edit-' + editing.lineage_id} mode={editing} onSubmit={submitMode} onCancel={() => setForm(null)} /> : null}
      {modes.length && !questions.length ? (
        <Empty title="This run has no questions yet">{'Questions appear here once they are imported, synced from the chat or typed in.'}</Empty>
      ) : null}
      {modes.length && questions.length ? (
        <div className="lab-lv-scroll" role="region" aria-label={'Labels for ' + r.code} tabIndex={0}>
          <table className="lab-lv-matrix">
            <thead>
              <tr>
                <th scope="col" className="lab-lv-corner">{'Question'}</th>
                {modes.map((m) => (
                  <ModeHead key={m.lineage_id} mode={m} canEdit={canEdit} asking={asking === m.lineage_id} busy={modeBusy} onAsk={setAsking}
                    onRetire={retire} onEdit={(x) => { setAsking(null); setModeError(''); setForm({ mode: x }); }} />
                ))}
              </tr>
            </thead>
            <tbody>
              {questions.map((q, i) => {
                const answer = (q.turn_ids || [])[0];
                return (
                  <tr key={q.id}>
                    <th scope="row" className="lab-lv-q">
                      <div className="lab-lv-qhead">
                        {onOpenQuestion
                          ? <button type="button" className="lab-lv-code" onClick={() => onOpenQuestion(q.code)} aria-label={'Open question ' + q.code}>{q.code}</button>
                          : <span className="lab-lv-code">{q.code}</span>}
                        {answer ? <span className="lab-lv-answer" title={answer}>{'Answer ' + shortId(answer)}</span>
                          : <span className="lab-lv-answer">{'No answer yet'}</span>}
                      </div>
                      <div className="lab-lv-qtext" title={q.question || undefined}>{q.question || '—'}</div>
                    </th>
                    {modes.map((m, j) => (
                      <Cell key={m.lineage_id} q={q} mode={m} c={views[i][j]} busy={saving && saving[q.id + '|' + m.lineage_id]} onCell={onCell} />
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

// Reads the run's labels, sets the caller's cells (shown at once, put back with the reason if refused) and, for the
// platform team and the consultant, adds, edits and retires failure modes, reading the matrix again after each.
export default function LabelsView({ me, run, onOpenQuestion }) {
  const [res, setRes] = React.useState({ data: null, loading: !!run, error: '' });
  const [saving, setSaving] = React.useState({});
  const [cellError, setCellError] = React.useState('');
  const ask = React.useRef(0);
  const inflight = React.useRef({});

  // A quiet read keeps what is on screen (and any error) until the answer comes; another run starts empty.
  const load = React.useCallback(async (quiet) => {
    const mine = (ask.current += 1);
    if (!run) { setRes({ data: null, loading: false, error: '' }); return; }
    const wanted = String(run).toUpperCase();
    setRes((p) => ({ data: p.data && p.data.run && String(p.data.run.code).toUpperCase() === wanted ? p.data : null,
      loading: true, error: quiet ? p.error : '' }));
    try {
      const out = await labApi.runLabels(run);
      if (mine === ask.current) setRes({ data: out || null, loading: false, error: '' });
    } catch (e) {
      if (mine === ask.current) setRes((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, [run]);
  React.useEffect(() => { inflight.current = {}; setSaving({}); setCellError(''); load(false); }, [load]);

  // One cell at a time per cell: the click shows at once; the service's time replaces ours, a refusal puts it back.
  const onCell = async (q, mode, value) => {
    const data = res.data;
    const key = q.id + '|' + mode.lineage_id;
    if (!data || inflight.current[key]) return;
    inflight.current[key] = true;
    const code = data.run && data.run.code;
    const meId = data.me || (me && me.user_id) || null;
    const before = (((data.cells || {})[q.id] || {})[mode.lineage_id] || []).find((e) => isMine(e, meId)) || null;
    const next = value ? { value, author_id: meId, author_email: (me && me.email) || null, author_role: (me && me.role) || null,
      source: 'manual', at: new Date().toISOString(), mine: true } : null;
    const put = (entry, extra) => setRes((p) => (p.data && p.data.run && p.data.run.code === code
      ? Object.assign({}, p, { data: Object.assign(withMine(p.data, q.id, mode.lineage_id, meId, entry), extra || {}) }) : p));
    put(next);
    setSaving((p) => Object.assign({}, p, { [key]: true }));
    setCellError('');
    try {
      const out = await labApi.setCell(q.code, mode.lineage_id, value);
      const at = out && out.cell && out.cell.at;
      if (at) put(next ? Object.assign({}, next, { at }) : null, { last_updated: at });
    } catch (e) {
      put(before);
      setCellError('Could not save ' + q.code + ' · ' + mode.name + ': ' + errText(e));
    } finally {
      delete inflight.current[key];
      setSaving((p) => { const n = Object.assign({}, p); delete n[key]; return n; });
    }
  };

  const onAddMode = async (body) => { await labApi.addMode(body); await load(true); };
  const onEditMode = async (mode, body) => { await labApi.editMode(mode.lineage_id, body); await load(true); };
  const onRetireMode = async (mode) => { await labApi.retireMode(mode.lineage_id); await load(true); };

  return (
    <LabelsMatrix run={run} data={res.data} me={me} loading={res.loading} error={cellError || res.error} saving={saving} onCell={onCell}
      onOpenQuestion={onOpenQuestion} onAddMode={onAddMode} onEditMode={onEditMode} onRetireMode={onRetireMode} onRetry={() => load(false)} />
  );
}
