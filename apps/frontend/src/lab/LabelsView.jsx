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

// A coding agent's value (3 Nov 2026): a suggestion a person accepts by setting the same value, never a person's label.
const isSuggestion = (e) => !!e && (e.suggestion === true || e.source === 'coding_agent');

// One cell as shown: the caller's value, everyone else's counted, whether people disagree, and who said what and when.
// A coding agent's suggestion is kept apart (`suggested`): it is never counted among people's values.
export function cellView(entries, meId) {
  const all = (entries || []).filter((e) => e && (e.value === 'positive' || e.value === 'negative'));
  const sug = all.find(isSuggestion) || null;
  const list = all.filter((e) => !isSuggestion(e));
  const mine = list.find((e) => isMine(e, meId)) || null;
  const others = list.filter((e) => e !== mine).sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
  const pos = others.filter((e) => e.value === 'positive').length;
  const out = {
    mine: mine ? mine.value : null, mineAt: mine ? mine.at || null : null, pos, neg: others.length - pos, others: others.length,
    disagree: new Set(list.map((e) => e.value)).size > 1,
    who: (mine ? ['You: ' + mine.value + atOf(mine)] : []).concat(others.map((e) => whoOf(e) + ': ' + e.value + atOf(e))).join('\n')
  };
  if (sug) Object.assign(out, { suggested: sug.value, suggestedBy: whoOf(sug) + atOf(sug) });
  return out;
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
      <div className="lab-lv-toggle" role="group" aria-label={'Your label for ' + rowTag(q) + ', ' + mode.name}>
        {VALUES.map((v) => {
          const on = c.mine === v;
          return (
            <button key={v} type="button" className={'lab-lv-opt lab-lv-opt-' + v + (on ? ' lab-on' : '')} aria-pressed={on ? 'true' : 'false'}
              disabled={!!busy} title={on ? 'Click again to clear' : undefined} onClick={() => onCell && onCell(q, mode, on ? null : v)}>{v}</button>
          );
        })}
      </div>
      {c.suggested ? (
        <div className="lab-lv-suggest" title={c.suggestedBy}>
          <span>{'Suggested by agent: ' + c.suggested}</span>
          {c.mine !== c.suggested ? (
            <button type="button" className="lab-link" disabled={!!busy} aria-label={'Accept the suggestion ' + c.suggested + ' for ' + rowTag(q) + ', ' + mode.name}
              onClick={() => onCell && onCell(q, mode, c.suggested)}>{'Accept'}</button>
          ) : null}
        </div>
      ) : null}
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

// What is labelled (14 Nov 2026, Hussain: "make the labels also one only, no runs and sessions different"): the run
// picked in the top bar, a Codex batch or a person's live day alike. A run's rows are the turns it opens on the Runs page
// (runs.label_questions), and every label is in the one store that Progress, the Loop and the coding agent read. The
// Sessions | Runs switch and its own list of sessions runs (13 Nov 2026) are gone.

// Saves the caller's value for one cell on its question (PUT /lab/q/{code}/cells) and returns the service's time.
export async function saveCell(api, q, lineage, value) {
  const out = await api.setCell(q.code, lineage, value);
  return (out && out.cell && out.cell.at) || null;
}

// A failed save says so only while its grid is the one in view.
export function saveErrorShown(shownRun, savedRun) {
  return String(shownRun || '').toUpperCase() === String(savedRun || '').toUpperCase();
}

// The grid on view: a run's code, or ALL for every run.
export const gridKey = (run) => (run ? String(run).toUpperCase() : 'ALL');

// What a row is called (14 Nov 2026, Hussain: "I need to see the selected run only"). A Codex batch's row is a turn of
// one of its sessions: it is named by its scenario (and its turn when the session has more than one), never by the
// code of the run its question was filed in before 13 Nov (R51-1 on R57). Any other row keeps its code (R53-4).
const SCENARIO_OF = /^scn__.+?__(.+)__\d+$/;
export function rowTag(q) {
  const x = q || {};
  if (!x.session_id) return x.code || '';
  const m = SCENARIO_OF.exec(String(x.session_id));
  const name = x.scenario_id || (m ? m[1] : 'Session');
  return Number(x.session_turns) > 1 && x.turn_no ? name + ' · turn ' + x.turn_no : name;
}

// Opening a row: a Codex row opens its conversation inside the run on view, on its turn; any other its question.
export function openRow(q, runCode, { onOpenQuestion, onOpenTurn } = {}) {
  if (q && q.session_id && onOpenTurn) onOpenTurn(runCode, q.session_id, q.turn_id || null);
  else if (q && onOpenQuestion) onOpenQuestion(q.code);
}

// The grid's runs and their rows: one run (GET /lab/runs/{code}/labels) or every run (GET /lab/labels).
export function gridGroups(data) {
  if (!data) return [];
  if (Array.isArray(data.runs)) return data.runs.filter((g) => g && g.run && (g.questions || []).length);
  return data.run ? [{ run: data.run, questions: data.questions || [] }] : [];
}

// The page for one run or for All runs, from what the service returned. Nothing here reads the service: LabelsView
// does, and hands the clicks back through onCell and the onAddMode / onEditMode / onRetireMode promises.
export function LabelsMatrix({ run, all, data, me, loading, error, saving, onCell, onOpenQuestion, onOpenTurn, onAddMode, onEditMode,
  onRetireMode, onRetry, waiting, onOpenLoop, picker, empty }) {
  const [form, setForm] = React.useState(null);        // null, { add: true }, or { mode } while one column is edited
  const [asking, setAsking] = React.useState(null);    // the column whose retirement is being confirmed
  const [modeBusy, setModeBusy] = React.useState(false);
  const [modeError, setModeError] = React.useState('');
  const canEdit = !!(me && me.all_companies);

  if (!run && !all) {
    return (
      <div className="lab-page-body lab-page-full">
        <div className="lab-page-head"><div><h1>{'Labels'}</h1>{picker || null}</div></div>
        {empty === undefined ? <Empty title="Pick a run">{'Choose a run to label its questions against the failure modes.'}</Empty> : empty || null}
      </div>
    );
  }
  const r = all ? { code: 'All runs' } : ((data && data.run) || { code: String(run).toUpperCase() });
  const modes = (data && data.modes) || [];
  const groups = gridGroups(data);
  const questions = groups.reduce((acc, g) => acc.concat(g.questions), []);
  const head = (
    <div className="lab-page-head lab-head-row">
      <div>
        <h1>{'Labels'}</h1>
        <p className="lab-small lab-big">{(all ? 'Failure modes for every run, latest asked first'
          : 'Failure modes for ' + r.code + (r.title ? ' · ' + r.title : ''))
          + '. Positive means the failure is in the question’s answer; negative means it is not.'}</p>
        {picker || null}
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
  const views = {};
  questions.forEach((q) => { views[q.id] = modes.map((m) => cellView((cells[q.id] || {})[m.lineage_id], meId)); });
  const mineN = Object.values(views).reduce((n, row) => n + row.filter((c) => c.mine).length, 0);
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
      {waiting ? (
        <div className="lab-lv-waiting" role="status">
          <span>{waiting + (waiting === 1 ? ' proposal waits' : ' proposals wait') + ' for a person’s decision (failure modes, the specification or expected answers).'}</span>
          {onOpenLoop ? <button type="button" className="lab-link" onClick={onOpenLoop}>{'Open the loop'}</button> : null}
        </div>
      ) : null}
      <div className="lab-lv-bar">
        <span>{data.last_updated ? 'Last updated ' + when(data.last_updated) : 'No cells labelled yet'}</span>
        {questions.length && modes.length ? <span>{'You have labelled ' + mineN + ' of ' + questions.length * modes.length + ' cells'}</span> : null}
        {loading ? <span>{'Reading…'}</span> : null}
        {data.more ? <span>{'Showing the first ' + questions.length.toLocaleString('en-GB') + ' of '
          + (Number(data.total) || 0).toLocaleString('en-GB') + ' questions'}</span> : null}
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
      {modes.length && !questions.length ? (all
        ? <Empty title="No run has questions to label yet">{'Questions appear here once Codex sessions or chat questions are synced, or a run is typed in or imported.'}</Empty>
        : <Empty title="This run has no questions yet">{'Questions appear here once they are imported, synced from the chat or typed in.'}</Empty>
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
              {groups.map((g) => [
                all ? (
                  <tr key={'g-' + g.run.code} className="lab-lv-group">
                    <th scope="colgroup" colSpan={1 + modes.length}>
                      <span className="lab-lv-group-label">
                        <span className="lab-lv-group-name">{g.run.code + (g.run.title ? ' · ' + g.run.title : '')}</span>
                        <span className="lab-lv-group-count">{g.questions.length + (g.questions.length === 1 ? ' question' : ' questions')}</span>
                      </span>
                    </th>
                  </tr>
                ) : null,
              ].concat(g.questions.map((q) => {
                const answer = q.turn_id || (q.turn_ids || [])[0];
                const tag = rowTag(q);
                const canOpen = q.session_id ? !!onOpenTurn : !!onOpenQuestion;
                return (
                  <tr key={q.id}>
                    <th scope="row" className="lab-lv-q">
                      <div className="lab-lv-qhead">
                        {canOpen
                          ? <button type="button" className="lab-lv-code" onClick={() => openRow(q, g.run.code, { onOpenQuestion, onOpenTurn })}
                              aria-label={q.session_id ? 'Open ' + tag + ' in ' + g.run.code : 'Open question ' + q.code}>{tag}</button>
                          : <span className="lab-lv-code">{tag}</span>}
                        {answer ? <span className="lab-lv-answer" title={answer}>{'Answer ' + shortId(answer)}</span>
                          : <span className="lab-lv-answer">{'No answer yet'}</span>}
                      </div>
                      <div className="lab-lv-qtext" title={q.question || undefined}>{q.question || '—'}</div>
                    </th>
                    {modes.map((m, j) => (
                      <Cell key={m.lineage_id} q={q} mode={m} c={views[q.id][j]} busy={saving && saving[q.id + '|' + m.lineage_id]} onCell={onCell} />
                    ))}
                  </tr>
                );
              })))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

// Reads the run's labels, sets the caller's cells (shown at once, put back with the reason if refused) and, for the
// platform team and the consultant, adds, edits and retires failure modes, reading the matrix again after each.
export default function LabelsView({ me, run, onOpenQuestion, onOpenTurn, onOpenLoop }) {
  // The run picked in the top bar; with none (All runs), every run (GET /lab/labels).
  const grid = gridKey(run);
  const [res, setRes] = React.useState({ key: grid, data: null, loading: true, error: '' });
  const [waiting, setWaiting] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    labApi.proposals().then((p) => { if (live) setWaiting(((p && p.modes) || []).length + ((p && p.spec) || []).length + ((p && p.expected) || []).length); })
      .catch(() => { if (live) setWaiting(0); });
    return () => { live = false; };
  }, [run]);
  const [saving, setSaving] = React.useState({});
  const [cellError, setCellError] = React.useState('');
  const ask = React.useRef(0);
  const inflight = React.useRef({});
  const shown = React.useRef(grid);
  shown.current = grid;

  // A quiet read keeps what is on screen (and any error) until the answer comes; another grid starts empty.
  const load = React.useCallback(async (quiet) => {
    const mine = (ask.current += 1);
    setRes((p) => ({ key: grid, data: p.key === grid ? p.data : null, loading: true, error: quiet ? p.error : '' }));
    try {
      const out = run ? await labApi.runLabels(run) : await labApi.allLabels();
      if (mine === ask.current) setRes({ key: grid, data: out || null, loading: false, error: '' });
    } catch (e) {
      if (mine === ask.current) setRes((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, [run, grid]);
  React.useEffect(() => { inflight.current = {}; setSaving({}); setCellError(''); load(false); }, [load]);

  // One cell at a time per cell: the click shows at once; the service's time replaces ours, a refusal puts it back.
  const onCell = async (q, mode, value) => {
    const data = res.data;
    const key = q.id + '|' + mode.lineage_id;
    if (!data || inflight.current[key]) return;
    inflight.current[key] = true;
    const code = res.key;
    const meId = data.me || (me && me.user_id) || null;
    const before = (((data.cells || {})[q.id] || {})[mode.lineage_id] || []).find((e) => isMine(e, meId)) || null;
    const next = value ? { value, author_id: meId, author_email: (me && me.email) || null, author_role: (me && me.role) || null,
      source: 'manual', at: new Date().toISOString(), mine: true } : null;
    const put = (entry, extra) => setRes((p) => (p.data && p.key === code
      ? Object.assign({}, p, { data: Object.assign(withMine(p.data, q.id, mode.lineage_id, meId, entry), extra || {}) }) : p));
    put(next);
    setSaving((p) => Object.assign({}, p, { [key]: true }));
    setCellError('');
    try {
      const at = await saveCell(labApi, q, mode.lineage_id, value);
      if (at) put(next ? Object.assign({}, next, { at }) : null, { last_updated: at });
    } catch (e) {
      put(before);
      if (saveErrorShown(shown.current, code)) setCellError('Could not save ' + rowTag(q) + ' · ' + mode.name + ': ' + errText(e));
    } finally {
      delete inflight.current[key];
      setSaving((p) => { const n = Object.assign({}, p); delete n[key]; return n; });
    }
  };

  const onAddMode = async (body) => { await labApi.addMode(body); await load(true); };
  const onEditMode = async (mode, body) => { await labApi.editMode(mode.lineage_id, body); await load(true); };
  const onRetireMode = async (mode) => { await labApi.retireMode(mode.lineage_id); await load(true); };

  return (
    <LabelsMatrix run={run} all={!run} data={res.key === grid ? res.data : null} me={me} loading={res.loading}
      error={cellError || res.error} saving={saving} onCell={onCell} onOpenQuestion={onOpenQuestion} onOpenTurn={onOpenTurn}
      onAddMode={onAddMode} onEditMode={onEditMode} onRetireMode={onRetireMode} onRetry={() => load(false)}
      waiting={waiting} onOpenLoop={onOpenLoop} />
  );
}
