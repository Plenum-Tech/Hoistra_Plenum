// Scenarios — the scenarios and the specification they were written from (11 Nov 2026, task plan B2).
//
// A scenario is tagged with the sections of the specification it tests (B2.1) and carries the expected outcome of
// each turn (B2.2): the plan, how a failure must be handled, the answer format. It is versioned with the
// specification (B2.3): when a section it cites changes, the scenario shows as out of date until a person stores or
// approves it again.
//
// Curators can read the specification file in again, generate a batch that covers every section (no model is called;
// the same seed gives the same scenarios), import a file written elsewhere, download the scenarios to fill in
// record-specific values, and approve the ones whose expected outcome they have checked.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { isCurator, plural } from './logic/format.js';
import { TYPE_OPTIONS, scenarioRow } from './logic/sessions.js';
import './runs.css';
import './sessions.css';

const STATUS_OPTIONS = [['', 'Any status'], ['draft', 'Draft'], ['approved', 'Approved'], ['retired', 'Retired']];

// Which specification is current, how many scenarios it has left out of date, and how many cite sections it does not
// have (a change to the specification can never flag those).
export function SpecLine({ spec }) {
  const s = spec || {};
  if (!s.spec_version) return <p className="lab-small">{'No specification has been read in yet.'}</p>;
  const stale = Number(s.stale_scenarios) || 0;
  const uncovered = Number(s.uncovered_scenarios) || 0;
  return (
    <p className="lab-small">
      {'Specification ' + s.spec_version + ' · ' + plural((s.sections || []).length, 'section', 'sections') + ' · '
        + (stale ? plural(stale, 'scenario', 'scenarios') + ' out of date' : 'every scenario is up to date')
        + (uncovered ? ' · ' + plural(uncovered, 'scenario cites', 'scenarios cite') + ' sections the specification does not have ('
          + (s.uncovered_sections || []).join(', ') + '), so a change cannot flag them' : '')}
    </p>
  );
}

// The scenarios as a table. `rows` null while reading; `selected` the ids ticked for approval.
export function ScenariosTable({ rows, selected, onToggle }) {
  if (rows == null) return <p className="lab-small" role="status">{'Reading the scenarios…'}</p>;
  if (!rows.length) {
    return (
      <div className="lab-empty lab-empty-big">
        <h2>{'No scenarios yet.'}</h2>
        <p className="lab-small">{'Generate a batch for every section of the specification, or import a scenario file.'}</p>
      </div>
    );
  }
  const ticked = new Set(selected || []);
  return (
    <div className="lab-table-wrap lab-table-full">
      <table className="lab-table">
        <thead>
          <tr><th scope="col"><span className="lab-visually-hidden">{'Select'}</span></th><th scope="col">{'Scenario'}</th><th scope="col">{'Type'}</th>
            <th scope="col">{'Tests these sections'}</th><th scope="col">{'Turns'}</th><th scope="col">{'Version'}</th>
            <th scope="col">{'Status'}</th></tr>
        </thead>
        <tbody>
          {rows.map((raw) => {
            const r = scenarioRow(raw);
            return (
              <tr key={r.id}>
                <td><input type="checkbox" aria-label={'Select ' + r.id} checked={ticked.has(r.id)} onChange={() => onToggle(r.id)} /></td>
                <td>
                  <strong>{r.id}</strong>
                  {r.opening ? <div className="lab-small">{r.opening}</div> : null}
                  {r.stale ? <div className="lab-small lab-ses-stale">{r.staleText}</div> : null}
                  {r.expectedFlagged ? <div className="lab-small lab-ses-stale">{'Expected outcome flagged as wrong on ' + plural(r.expectedFlagged, 'turn', 'turns') + ': needs correcting'}</div> : null}
                </td>
                <td>{r.type}</td>
                <td><div className="lab-chips">{r.sections.map((s) => <span key={s} className="lab-chip">{s}</span>)}</div></td>
                <td>{r.turns}</td>
                <td>{r.version}</td>
                <td><span className={'lab-chip' + (r.status === 'Approved' ? ' lab-chip-ok' : '')}>{r.status}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function save(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function Scenarios({ me }) {
  const [filters, setFilters] = React.useState({ type: '', status: '', stale: false });
  const [rows, setRows] = React.useState(null);
  const [spec, setSpec] = React.useState(null);
  const [selected, setSelected] = React.useState([]);
  const [gen, setGen] = React.useState({ count: '250', seed: '0' });
  const [act, setAct] = React.useState({ busy: '', said: '', error: '', lines: [] });
  const file = React.useRef(null);
  const ask = React.useRef(0);
  const curator = isCurator(me);

  const load = React.useCallback(() => {
    const mine = (ask.current += 1);
    setRows(null);
    const query = {};
    if (filters.type) query.type = filters.type;
    if (filters.status) query.status = filters.status;
    if (filters.stale) query.stale = 'true';
    labApi.evalScenarios(query)
      .then((out) => { if (mine === ask.current) setRows((out && out.scenarios) || []); })
      .catch((e) => { if (mine === ask.current) { setRows([]); setAct((old) => ({ ...old, error: errText(e) })); } });
    labApi.evalSpec().then((out) => { if (mine === ask.current) setSpec(out || null); }, () => {});
  }, [filters]);
  React.useEffect(() => { load(); }, [load]);

  const run = (name, work, done) => {
    setAct({ busy: name, said: '', error: '', lines: [] });
    work()
      .then((out) => { setAct({ busy: '', said: done(out || {}), error: '', lines: [] }); setSelected([]); load(); })
      .catch((e) => {
        const errors = e && e.body && e.body.detail && Array.isArray(e.body.detail.errors) ? e.body.detail.errors : [];
        setAct({ busy: '', said: '', error: errText(e), lines: errors.slice(0, 20).map((x) => 'Line ' + x.line + ': ' + x.error) });
      });
  };
  const count = Number(gen.count);
  const seed = Number(gen.seed);
  const genOk = Number.isInteger(count) && count >= 1 && count <= 2000 && Number.isInteger(seed) && seed >= 0;
  const toggle = (id) => setSelected((old) => (old.includes(id) ? old.filter((x) => x !== id) : old.concat([id])));

  return (
    <div className="lab-ses-list">
      <SpecLine spec={spec} />
      {curator ? (
        <section className="lab-ses-sync" aria-label="Scenario actions">
          <div className="lab-row">
            <label className="lab-field lab-ses-num"><span>{'How many'}</span>
              <input type="number" min="1" max="2000" value={gen.count} onChange={(e) => setGen({ ...gen, count: e.target.value })} /></label>
            <label className="lab-field lab-ses-num"><span>{'Seed'}</span>
              <input type="number" min="0" value={gen.seed} onChange={(e) => setGen({ ...gen, seed: e.target.value })} /></label>
            <button type="button" className="lab-btn" disabled={!!act.busy || !genOk}
              onClick={() => run('generate', () => labApi.evalGenerateScenarios({ count, seed }),
                (o) => 'Generated ' + plural(o.generated, 'scenario', 'scenarios') + ': ' + (Number(o.added) || 0) + ' new, '
                  + (Number(o.new_versions) || 0) + ' updated, ' + (Number(o.unchanged) || 0) + ' unchanged'
                  + (o.dropped ? '. ' + plural(o.dropped, 'question was', 'questions were') + ' left out.' : '.'))}>
              {act.busy === 'generate' ? 'Generating…' : 'Generate scenarios'}</button>
          </div>
          <div className="lab-row">
            <input ref={file} type="file" accept=".jsonl,.json,application/json" className="lab-visually-hidden" tabIndex={-1} aria-hidden="true"
              onChange={(e) => {
                const f = e.target.files && e.target.files[0];
                e.target.value = '';
                if (f) run('import', () => labApi.evalImportScenarios(f, false),
                  (o) => 'Imported ' + plural(o.scenarios, 'scenario', 'scenarios') + ': ' + (Number(o.added) || 0) + ' new, '
                    + (Number(o.new_versions) || 0) + ' updated, ' + (Number(o.unchanged) || 0) + ' unchanged.');
              }} />
            <button type="button" className="lab-btn lab-btn-quiet" disabled={!!act.busy} onClick={() => file.current && file.current.click()}>
              {act.busy === 'import' ? 'Importing…' : 'Import a scenario file'}</button>
            <button type="button" className="lab-btn lab-btn-quiet" disabled={!!act.busy}
              onClick={() => run('export', () => labApi.evalExportScenarios().then((o) => { if (o && o.blob) save(o.blob, 'scenarios.jsonl'); return o; }),
                () => 'Downloaded scenarios.jsonl. Fill in the expected answers, then import it again.')}>
              {'Download scenarios'}</button>
            <button type="button" className="lab-btn lab-btn-quiet" disabled={!!act.busy || !selected.length}
              onClick={() => run('approve', () => labApi.evalApproveScenarios(selected),
                (o) => 'Approved ' + plural(o.approved, 'scenario', 'scenarios') + (o.already ? '; ' + o.already + ' already approved.' : '.'))}>
              {'Approve selected' + (selected.length ? ' (' + selected.length + ')' : '')}</button>
            <button type="button" className="lab-btn lab-btn-quiet" disabled={!!act.busy}
              onClick={() => run('spec', () => labApi.evalReloadSpec(),
                (o) => (o.new ? 'Read specification ' + o.spec_version + ': ' + plural((o.changed || []).length, 'section', 'sections') + ' changed.'
                  : 'The specification has not changed.'))}>
              {act.busy === 'spec' ? 'Reading…' : 'Read the specification again'}</button>
          </div>
          {act.said ? <p className="lab-small" role="status">{act.said}</p> : null}
          {act.error ? <div role="alert" className="lab-error">{act.error}</div> : null}
          {act.lines.length ? <ul className="lab-ses-lines">{act.lines.map((l) => <li key={l} className="lab-ses-line lab-ses-line-bad">{l}</li>)}</ul> : null}
        </section>
      ) : null}
      <div className="lab-row lab-ses-filters" role="group" aria-label="Filter the scenarios">
        <label className="lab-field lab-ses-filter"><span>{'Scenario type'}</span>
          <select value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value })}>
            {TYPE_OPTIONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <label className="lab-field lab-ses-filter"><span>{'Status'}</span>
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
            {STATUS_OPTIONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <label className="lab-ses-check">
          <input type="checkbox" checked={filters.stale} onChange={(e) => setFilters({ ...filters, stale: e.target.checked })} />
          <span>{'Out of date only'}</span></label>
      </div>
      <ScenariosTable rows={rows} selected={selected} onToggle={toggle} />
    </div>
  );
}
