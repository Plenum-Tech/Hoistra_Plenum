// GoldenSet — the Golden set tab: the company's questions with known right answers, as versions. Upload an Excel
// or CSV (checked first, saved only on request, all rows or none), download the blank template or the compliance
// question templates, activate or retire a version, and read its cases. Expected answers stay hidden row by row
// until the reader asks for one, so the tab never primes a reviewer.
import React from 'react';
import * as gact from '../goldenActions.js';
import { goldenSetView } from '../logic/golden.js';

function Upload({ v }) {
  const [file, setFile] = React.useState(null);
  const [sheet, setSheet] = React.useState('');
  const [name, setName] = React.useState('');
  const [note, setNote] = React.useState('');
  const up = v.upload;
  const opts = (dryRun) => ({ sheet, name, note, dryRun });
  const checked = !!file && !!up.report && !up.report.saved && up.dryRun && up.fileName === file.name && !up.error;
  return (
    <section className="tl-card" aria-label="Upload a golden set">
      <h3 className="tl-title">{'Upload a new version'}</h3>
      <p className="tl-meta">{'An .xlsx or .csv with one question per row and its expected answer. Every row is checked; nothing is saved until every row is valid.'}</p>
      <div className="tl-form">
        <label className="tl-field"><span>{'File'}</span>
          <input type="file" accept=".xlsx,.csv" onChange={(e) => setFile((e.target.files && e.target.files[0]) || null)} />
        </label>
        <label className="tl-field"><span>{'Sheet'}</span>
          <input type="text" value={sheet} placeholder="First sheet with the columns" maxLength={100} onChange={(e) => setSheet(e.target.value)} />
        </label>
        <label className="tl-field"><span>{'Name'}</span>
          <input type="text" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="tl-field"><span>{'Note'}</span>
          <input type="text" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <div className="tl-actions">
        <button type="button" className="tl-btn" disabled={!file || up.busy} onClick={() => gact.upload(file, opts(true))}>{up.busy && up.dryRun ? 'Checking…' : 'Check the file'}</button>
        <button type="button" className="tl-btn tl-btn-primary" disabled={!checked || up.busy} onClick={() => gact.upload(file, opts(false))}>{up.busy && !up.dryRun ? 'Saving…' : 'Save as a new version'}</button>
        {file && !checked && !up.busy ? <span className="tl-meta">{'Check the file before saving it.'}</span> : null}
      </div>
      {up.error ? <div className="tl-bad" role="alert">{up.error}</div> : null}
      {up.errors.length ? <ul className="tl-issues" aria-label="Rows to fix">{up.errors.map((e, i) => <li key={i}>{e}</li>)}</ul> : null}
      {up.report ? (
        <div className="tl-report" role="status">
          {up.report.lines.map((l) => <div key={l}>{l}</div>)}
          {up.report.preview.length ? (
            <ol className="tl-steps">{up.report.preview.map((p) => <li key={p.key}>{p.question + ' · ' + p.evaluation}</li>)}</ol>
          ) : null}
        </div>
      ) : null}
      {up.warnings.length ? <ul className="tl-issues tl-meta" aria-label="Warnings">{up.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
    </section>
  );
}

function Templates() {
  return (
    <section className="tl-card" aria-label="Templates">
      <h3 className="tl-title">{'Templates'}</h3>
      <div className="tl-actions">
        <button type="button" className="tl-btn" onClick={() => gact.download('plain')}>{'Download the blank template'}</button>
        <button type="button" className="tl-btn" onClick={() => gact.download('compliance')}>{'Download the compliance question templates (161 questions)'}</button>
      </div>
      <p className="tl-meta">{'The compliance templates are questions only. Replace every {placeholder} with a name from your own records, write the expected answer from your records, and delete the rows you will not use before uploading.'}</p>
    </section>
  );
}

function Versions({ v }) {
  if (!v.versions.length) return null;
  return (
    <section className="tl-card" aria-label="Versions">
      <h3 className="tl-title">{'Versions'}</h3>
      <table className="tl-table">
        <thead><tr>{['Version', 'Name', 'Status', 'Cases', 'Uploaded', 'By', 'File', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {v.versions.map((x) => (
            <tr key={x.id} className={x.on ? 'tl-row-on' : ''}>
              <td><button type="button" className="tl-link" aria-pressed={x.on ? 'true' : 'false'} onClick={() => gact.pickSet(x.id)}>{x.label}</button></td>
              <td>{x.name || '—'}</td><td>{x.status}</td><td>{x.cases}</td><td>{x.when || '—'}</td><td>{x.by}</td><td>{x.file}</td>
              <td>
                {x.canActivate ? <button type="button" className="tl-link" disabled={x.busy} onClick={() => gact.activateSet(x.id)}>{'Make active'}</button> : null}
                {x.canRetire ? <button type="button" className="tl-link" disabled={x.busy} onClick={() => gact.retireSet(x.id)}>{'Retire'}</button> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Cases({ c }) {
  return (
    <section className="tl-card" aria-label="Cases">
      <div className="tl-list-head"><h3 className="tl-title">{'Cases'}</h3><span className="tl-meta">{c.totalLabel + ' · ' + c.pageLabel}</span></div>
      <div className="tl-filters">
        {c.flags.map((f) => (
          <button key={f.key || 'all'} type="button" className={'tl-chip-btn' + (f.on ? ' tl-on' : '')} aria-pressed={f.on ? 'true' : 'false'}
            onClick={() => gact.setCasesFlag(f.key)}>{f.label}</button>
        ))}
        <form className="tl-search" onSubmit={(e) => { e.preventDefault(); gact.searchCases(); }}>
          <input aria-label="Search the questions" placeholder="Search the questions…" value={c.draft} onChange={(e) => gact.setCasesDraft(e.target.value)} />
          <button type="submit" className="tl-link">{'Search'}</button>
        </form>
      </div>
      {c.loading ? <div className="tl-meta">{'Reading the cases…'}</div> : null}
      {c.error ? <div className="tl-bad" role="alert">{"Couldn't read the cases: " + c.error}</div> : null}
      {!c.loading && !c.error && !c.rows.length ? <div className="tl-meta">{'No cases match.'}</div> : null}
      {c.rows.length ? (
        <table className="tl-table tl-cases">
          <thead><tr>{['ID', 'Question', 'Follow-ups', 'Evaluation', 'Verified on', 'Requirements', 'Expected answer'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {c.rows.map((r) => (
              <tr key={r.id}>
                <td>{r.key}</td>
                <td className="tl-wrap">{r.question}{r.writeIntent ? <span className="tl-chip">{'Asks for an action'}</span> : null}</td>
                <td>{r.followups || '—'}</td>
                <td>{r.evaluation}</td>
                <td>{r.verified}{r.stale ? <span className="tl-chip">{'Stale'}</span> : null}</td>
                <td>{r.requirements}</td>
                <td className="tl-wrap">
                  {r.shown ? (
                    <>
                      <div>{r.expected}</div>
                      {r.values && r.values.length ? <div className="tl-meta">{'Values: ' + r.values.join('; ')}</div> : null}
                    </>
                  ) : null}
                  <button type="button" className="tl-link" aria-expanded={r.shown ? 'true' : 'false'} onClick={() => gact.toggleExpected(r.id)}>
                    {r.shown ? 'Hide' : 'Show expected answer'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="tl-pager">
        <button type="button" className="tl-link" onClick={() => gact.pageCases(-1)} disabled={!c.canPrev}>{'‹ Previous'}</button>
        <button type="button" className="tl-link" onClick={() => gact.pageCases(1)} disabled={!c.canNext}>{'Next ›'}</button>
      </div>
    </section>
  );
}

export default function GoldenSet({ s, now }) {
  const v = goldenSetView(s, now);
  if (v.notice) return <div className="tl-notice" role="status">{v.notice}</div>;
  return (
    <div className="tl-golden">
      {v.loading ? <div className="tl-meta">{'Reading the golden set…'}</div> : null}
      {v.error ? <div className="tl-bad" role="alert">{"Couldn't read the golden set: " + v.error}</div> : null}
      {v.actionError ? <div className="tl-bad" role="alert">{v.actionError}</div> : null}
      {v.active ? (
        <section className="tl-card" aria-label="Active version">
          <h3 className="tl-title">{'Version ' + v.active.version + ' is active' + (v.active.name ? ' · ' + v.active.name : '')}</h3>
          <div className="tl-meta">{v.active.cases + ' cases · ' + v.active.stale + ' stale · ' + v.active.unverified + ' not verified · uploaded ' + (v.active.when || '—') + ' by ' + v.active.by + ' from ' + v.active.file}</div>
        </section>
      ) : null}
      {v.empty ? <div className="tl-notice">{'No golden set yet. Download a template, fill in the expected answers from your own records, and upload it.'}</div> : null}
      <Upload v={v} />
      <Templates />
      <Versions v={v} />
      {v.versions.length ? <Cases c={v.cases} /> : null}
    </div>
  );
}
