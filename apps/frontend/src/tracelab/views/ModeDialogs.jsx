// ModeDialogs — the dialogs that change failure modes (logic/taxonomy.js tlTaxDialog): new, edit, rename, merge, split,
// retire and freeze (the platform team's), Propose a failure mode (a company reviewer's, from their own notes) and
// Reject a company's proposal with a reason (the platform team's), File under (any reviewer, for their own company's
// notes) and setting a note aside (the platform team's). Dialogs that group notes carry "Two notes share a mode only if the same fix clears
// both."; merge, split and the boundary field also carry the fix test the server sends. Agent and level choices come
// from the server, so no internal name is written here. Each dialog sits on an overlay (Escape closes), portalled to
// the page body, and drawn in place where there is no body.
import React from 'react';
import { createPortal } from 'react-dom';
import * as a from '../taxonomyActions.js';
import { taxonomyModesVals } from '../logic/taxonomy.js';

function Options({ list }) {
  return list.map((o) => <option key={o.value || 'none'} value={o.value}>{o.label}</option>);
}

function DraftFields({ draft, set, levels, agents, evaluators, nameEditable, frozen, fixTest, fixTestText, nearestOptions }) {
  return (
    <div className="tl-fields">
      {nameEditable ? (
        <label className="tl-field-row">{'Name (lower-case words joined by underscores)'}
          <input value={draft.name} onChange={set('name')} placeholder="counts_cancelled_jobs_as_open" maxLength={64} />
        </label>
      ) : null}
      <label className="tl-field-row">{'Definition — one sentence another reviewer can apply'}
        <textarea className="tl-textarea" value={draft.definition} onChange={set('definition')} placeholder="Present when …" maxLength={1000} disabled={frozen} />
      </label>
      <label className="tl-field-row">{'Boundary — how it differs from the nearest failure mode'}
        <textarea className="tl-textarea" value={draft.boundary} onChange={set('boundary')} maxLength={1000} disabled={frozen} />
      </label>
      {fixTest ? <div className="tl-guidance"><strong>{fixTest}</strong>{' ' + fixTestText}</div> : null}
      {nearestOptions ? (
        <label className="tl-field-row">{'Nearest failure mode'}
          <select value={draft.nearest} onChange={set('nearest_mode_id')}><Options list={nearestOptions} /></select>
        </label>
      ) : null}
      <div className="tl-fields-row">
        <label className="tl-field-row">{'Who decides it'}
          <select value={draft.level} onChange={set('level')} disabled={frozen}><Options list={[{ value: '', label: 'Choose…' }].concat(levels)} /></select>
        </label>
        <label className="tl-field-row">{'Where the failure lives'}
          <select value={draft.agent} onChange={set('agent')} disabled={frozen}><Options list={[{ value: '', label: 'Choose…' }].concat(agents)} /></select>
        </label>
        {evaluators ? (
          <label className="tl-field-row">{'How it will be checked'}
            <select value={draft.evaluator} onChange={set('evaluator_type')} disabled={frozen}><Options list={evaluators} /></select>
          </label>
        ) : null}
      </div>
    </div>
  );
}

function FromNotes({ from }) {
  if (!from) return null;
  return (
    <div>
      <div className="tl-kick">{'From ' + from.count + (from.count === 1 ? ' reviewer note' : ' reviewer notes')}</div>
      {from.notes.map((n) => <div key={n.id} className="tl-meta">{'✎ ' + n.note + ' — ' + n.who}</div>)}
    </div>
  );
}

// A company reviewer's draft: the same fields as a new failure mode, minus how it will be checked and the requirements
// it cites, which the platform team sets when it accepts.
function Propose({ d }) {
  return (
    <div className="tl-fields">
      <FromNotes from={d.fromNotes} />
      <DraftFields draft={d.draft} set={d.set} levels={d.levels} agents={d.agents} nameEditable fixTest={d.fixTest} fixTestText={d.fixTestText}
        nearestOptions={d.nearestOptions} />
      <div className="tl-meta">{d.note}</div>
    </div>
  );
}

function CreateOrEdit({ d }) {
  return (
    <div className="tl-fields">
      <FromNotes from={d.fromNotes} />
      {d.frozenText ? <div className="tl-guidance" role="note">{d.frozenText}</div> : null}
      <DraftFields draft={d.draft} set={d.set} levels={d.levels} agents={d.agents} evaluators={d.evaluators} nameEditable={d.nameEditable}
        frozen={d.frozen} fixTest={d.fixTest} fixTestText={d.fixTestText} nearestOptions={d.nearestOptions} />
      <fieldset className="tl-fieldset">
        <legend className="tl-kick">{'Requirements it cites'}</legend>
        {!d.requirements.length ? <div className="tl-meta">{'No active requirements yet.'}</div> : d.requirements.map((r) => (
          <label key={r.value} className="tl-check-row">
            <input type="checkbox" checked={r.on} onChange={r.toggle} />{r.label}
          </label>
        ))}
      </fieldset>
      {d.kind === 'edit' ? (
        <>
          <label className="tl-field-row">{'Why there are fewer than three close answers (only when the reviewed answers do not allow three)'}
            <textarea className="tl-textarea" value={d.waiver} onChange={d.set('close_negatives_waiver')} maxLength={500} />
          </label>
          <label className="tl-check-row">
            <input type="checkbox" checked={d.rescope} onChange={d.toggleRescope} disabled={d.frozen} />{d.rescopeText}
          </label>
        </>
      ) : null}
    </div>
  );
}

function Merge({ d }) {
  return (
    <div className="tl-fields">
      <fieldset className="tl-fieldset">
        <legend className="tl-kick">{'Failure modes to merge'}</legend>
        {d.sources.map((x) => (
          <label key={x.id} className="tl-check-row">
            <input type="checkbox" checked={x.on} onChange={x.toggle} />{x.name}
          </label>
        ))}
      </fieldset>
      <div role="radiogroup" aria-label="Merge into" className="tl-radios">
        <label><input type="radio" name="tl-merge-target" checked={d.target === 'existing'} onChange={() => d.setTarget('existing')} />{' An existing failure mode'}</label>
        <label><input type="radio" name="tl-merge-target" checked={d.target === 'new'} onChange={() => d.setTarget('new')} />{' A new failure mode'}</label>
      </div>
      {d.target === 'existing' ? (
        <label className="tl-field-row">{'Merge into'}
          <select value={d.into} onChange={d.set('into')}><Options list={d.intoOptions} /></select>
        </label>
      ) : (
        <DraftFields draft={d.newDraft} set={d.newDraft.set} levels={d.levels} agents={d.agents} nameEditable />
      )}
      <div className="tl-meta">{d.note}</div>
    </div>
  );
}

function Split({ d }) {
  return (
    <div className="tl-fields">
      {d.children.map((ch) => (
        <fieldset key={ch.index} className="tl-fieldset tl-fieldset-box">
          <legend className="tl-kick">{ch.label}</legend>
          <DraftFields draft={ch} set={ch.set} levels={d.levels} agents={d.agents} nameEditable />
          {ch.remove ? <button type="button" className="tl-btn tl-gap-top" onClick={ch.remove}>{'Remove this one'}</button> : null}
        </fieldset>
      ))}
      {d.addChild ? <button type="button" className="tl-btn tl-fit" onClick={d.addChild}>{'Add another'}</button> : null}
      <div>
        <div className="tl-kick">{'Where each note goes'}</div>
        {!d.notes.length ? <div className="tl-meta">{'No notes you can see.'}</div> : d.notes.map((n) => (
          <label key={n.id} className="tl-field-row tl-gap-top">
            {'✎ ' + n.note + ' — ' + n.who + (n.origin ? '' : ' (filed later)')}
            <select value={n.child} onChange={n.setChild}><Options list={d.childOptions} /></select>
          </label>
        ))}
      </div>
      <div className="tl-meta">{d.note}</div>
    </div>
  );
}

function FileUnder({ d }) {
  return (
    <div className="tl-fields" role="radiogroup" aria-label="Failure mode">
      {!d.fileModes.length ? <div className="tl-meta">{'No failure modes yet.'}</div> : d.fileModes.map((m) => (
        <label key={m.id} className="tl-pick-row">
          <span><input type="radio" name="tl-file-mode" checked={m.on} onChange={m.pick} />{' ' + m.name}</span>
          {m.definition ? <span className="tl-meta">{m.definition}</span> : null}
        </label>
      ))}
      {d.conflict ? (
        <div role="alert" className="tl-guidance">
          {d.conflict}
          <div className="tl-gap-top"><button type="button" className="tl-btn" onClick={d.fileAnyway} disabled={d.busy}>{'File anyway'}</button></div>
        </div>
      ) : null}
    </div>
  );
}

function Body({ d }) {
  if (d.kind === 'create' || d.kind === 'edit') return <CreateOrEdit d={d} />;
  if (d.kind === 'propose') return <Propose d={d} />;
  if (d.kind === 'reject') {
    return (
      <div className="tl-fields">
        <label className="tl-field-row">{'Why it is rejected'}<textarea className="tl-textarea" value={d.reason} onChange={d.set('reason')} maxLength={500} /></label>
        <div className="tl-meta">{d.note}</div>
      </div>
    );
  }
  if (d.kind === 'rename') {
    return (
      <div className="tl-fields">
        <label className="tl-field-row">{'New name'}<input value={d.name} onChange={d.set('name')} maxLength={64} /></label>
        <div className="tl-meta">{d.note}</div>
      </div>
    );
  }
  if (d.kind === 'merge') return <Merge d={d} />;
  if (d.kind === 'split') return <Split d={d} />;
  if (d.kind === 'retire') {
    return (
      <div className="tl-fields">
        <label className="tl-field-row">{'Why it is retired'}<textarea className="tl-textarea" value={d.reason} onChange={d.set('reason')} maxLength={500} /></label>
        <div className="tl-meta">{d.note}</div>
      </div>
    );
  }
  if (d.kind === 'freeze') return <div className="tl-guidance" role="note">{d.note}</div>;
  if (d.kind === 'file') return <FileUnder d={d} />;
  if (d.kind === 'dispose') {
    return (
      <div className="tl-fields">
        <div role="radiogroup" aria-label="Why it is set aside" className="tl-fields tl-fields-tight">
          {d.outcomes.map((o) => (
            <label key={o.value} className="tl-check-row"><input type="radio" name="tl-dispose" checked={o.on} onChange={o.pick} />{' ' + o.label}</label>
          ))}
        </div>
        <label className="tl-field-row">{'Note for the record'}<textarea className="tl-textarea" value={d.noteText} onChange={d.set('note')} maxLength={1000} /></label>
      </div>
    );
  }
  return null;
}

export default function ModeDialogs({ s }) {
  const d = taxonomyModesVals(s, a).tlTaxDialog;
  const closeRef = React.useRef(d ? d.close : null);
  closeRef.current = d ? d.close : null;
  const open = !!d;
  React.useEffect(() => {
    if (!open || typeof document === 'undefined' || !document.addEventListener) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && closeRef.current) closeRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
  if (!d) return null;
  const node = (
    <div className="tl-overlay">
      <div className="tl-modal" role="dialog" aria-modal="true" aria-label={d.title}>
        <div className="tl-head-row">
          <h3 className="tl-title tl-grow">{d.title}</h3>
          <button type="button" className="tl-btn" onClick={d.close} aria-label="Close">{'Close'}</button>
        </div>
        {d.showGuidance ? <div className="tl-guidance">{d.guidance}</div> : null}
        {d.showFixTest && d.fixTest && (d.kind === 'merge' || d.kind === 'split') ? <div className="tl-guidance"><strong>{d.fixTest}</strong>{' ' + d.fixTestText}</div> : null}
        <Body d={d} />
        {d.problems.length ? <ul className="tl-issues">{d.problems.map((p) => <li key={p} className="tl-meta">{p}</li>)}</ul> : null}
        {d.missing.length ? <div role="alert" className="tl-bad">{'Missing: ' + d.missing.join('; ') + '.'}</div> : null}
        {d.error ? <div role="alert" className="tl-bad tl-inline">{d.error}{d.retry ? <button type="button" className="tl-btn" onClick={d.retry}>{'Retry'}</button> : null}</div> : null}
        <div className="tl-actions tl-actions-end">
          <button type="button" className="tl-btn" onClick={d.close}>{'Cancel'}</button>
          <button type="button" className={'tl-btn' + (d.canSubmit ? ' tl-btn-primary' : '')} onClick={d.submit} disabled={!d.canSubmit}>{d.busy ? 'Saving…' : d.submitLabel}</button>
        </div>
      </div>
    </div>
  );
  const target = typeof document !== 'undefined' && document && document.body ? document.body : null;
  return target ? createPortal(node, target) : node;
}
