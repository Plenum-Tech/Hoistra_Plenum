// The query editor (19 Nov 2026, Hussain and Bala: "the editor must cover every part of the query, not only the states, the
// building and the dates"). A live question's records queries, each as fields built from Hoistra's map of the records:
// kind of answer, what is read, its name or code, states, conditions, limits, grouping and linked records. A curator
// changes what is wrong, runs it (read-only, through the faithful re-run), sees what Hoistra's query found beside what
// their version finds - value by value, each opening its receipt - and uses it as the expected answer.
//
// `QueryEditor` is the stateful whole (what GET /lab/turns/{id}/expected/query returned goes in `query`; Run and Use go out
// through `onRun` and `onUse`); `QueryEditorView` is the editor itself, driven by props, so the page and the tests see
// the same markup. The words and rules are in logic/queryForm.js. Nobody types SQL: every name comes from the map.
import React from 'react';
import { Receipt, when } from './Receipt.jsx';
import { SOURCE_WORDS, PERSON_WORDS, explainOf, receiptOf } from './logic/recordsValues.js';
import {
  KIND_WORDS, addGroup, addInclude, addLimit, attributesOf, blankCondition, canUse, changeColumn, changeFocusConcept, changeNodeConcept, changeOp, columnWords, conceptWords,
  describePlan, formOf, formProblems, groupConcepts, isChanged, kindOf, linksOf, noQueryWords, opChoice, opsFor, periodWords, periodsOf, planOf,
  removeAt, removeGroup, rollingOf, setGroup, setNode, statesOf, stateHelp, tryRun
} from './logic/queryForm.js';

const list = (x) => (Array.isArray(x) ? x : []);
const words = (x) => String(x == null ? '' : x).replace(/_/g, ' ');
const lower = (s) => s.charAt(0).toLowerCase() + s.slice(1);

const LEAD = { hoistra: 'Hoistra looked for', answer_key: 'Trace Lab’s fixed query looks for', proposed: 'The proposed query looks for', person: 'Your saved version looks for' };
const FOUND = { hoistra: 'Hoistra’s query found', answer_key: 'Trace Lab’s fixed query found', proposed: 'The proposed query found', person: 'Your saved version found' };
const sourceWords = (q) => (q && (q.source_words || SOURCE_WORDS[q.source] || (q.source === 'person' ? PERSON_WORDS : ''))) || '';
const DATE_HINTS = ['today', 'today+30d', 'today-30d'];

// ── Fields ──────────────────────────────────────────────────────────────────────────────────────────────────────────
function Choice({ label, value, options, onChange, placeholder, always }) {
  const known = options.some((o) => o.id === value);
  return (
    <select className="lab-input" aria-label={label} value={value == null ? '' : value} onChange={(e) => onChange(e.target.value)}>
      {always || !value ? <option value="">{placeholder || 'Choose'}</option> : null}
      {!known && value ? <option value={value}>{words(value)}</option> : null}
      {options.map((o) => <option key={o.id} value={o.id}>{o.words}</option>)}
    </select>
  );
}

function ValueFields({ c, kind, map, label, onChange }) {
  const set = (value) => onChange(Object.assign({}, c, { value }));
  const type = kind === 'number' ? 'number' : 'text';
  const hint = kind === 'date' ? '2026-12-31 or today+30d' : '';
  if (c.op === 'is_null') return null;
  if (c.op === 'within') {
    const r = rollingOf(c.value);
    const current = r ? r.way : String(c.value == null ? '' : c.value);
    const periods = periodsOf(map);
    return (
      <>
        <select className="lab-input" aria-label={label + ': period'} value={current}
          onChange={(e) => { const v = e.target.value; set(v === 'next' || v === 'last' ? v + '_' + ((r && r.n) || '30') + '_days' : v); }}>
          <option value="next">{'the next'}</option>
          <option value="last">{'the last'}</option>
          {current && !r && !periods.some((p) => p.id === current) ? <option value={current}>{periodWords(current)}</option> : null}
          {periods.map((p) => <option key={p.id} value={p.id}>{p.words}</option>)}
        </select>
        {r ? (
          <>
            <input className="lab-input lab-xq-n" type="number" min="1" max="3660" aria-label={label + ': days'} value={r.n}
              onChange={(e) => set(r.way + '_' + e.target.value + '_days')} />
            <span>{'days'}</span>
          </>
        ) : null}
      </>
    );
  }
  if (c.op === 'between') {
    const v = Array.isArray(c.value) ? c.value : ['', ''];
    const at = (i, x) => set(i === 0 ? [x, v[1] == null ? '' : v[1]] : [v[0] == null ? '' : v[0], x]);
    return (
      <>
        <input className="lab-input" type={type} placeholder={hint} list={kind === 'date' ? 'lab-xe-dates' : undefined} aria-label={label + ': from'}
          value={v[0] == null ? '' : String(v[0])} onChange={(e) => at(0, e.target.value)} />
        <span>{'and'}</span>
        <input className="lab-input" type={type} placeholder={hint} list={kind === 'date' ? 'lab-xe-dates' : undefined} aria-label={label + ': to'}
          value={v[1] == null ? '' : String(v[1])} onChange={(e) => at(1, e.target.value)} />
      </>
    );
  }
  if (c.op === 'in' || c.op === 'not_in') {
    return (
      <input className="lab-input" placeholder="separate with commas" aria-label={label + ': value'}
        value={Array.isArray(c.value) ? c.value.join(', ') : String(c.value == null ? '' : c.value)} onChange={(e) => set(e.target.value)} />
    );
  }
  return (
    <input className="lab-input" type={type} placeholder={hint} list={kind === 'date' ? 'lab-xe-dates' : undefined} aria-label={label + ': value'}
      value={Array.isArray(c.value) ? '' : String(c.value == null ? '' : c.value)} onChange={(e) => set(e.target.value)} />
  );
}

function ConditionRow({ c, concept, map, label, onChange, onRemove }) {
  const kind = kindOf(map, concept, c.attribute);
  const attrs = attributesOf(map, concept);
  const columns = (c.attribute && !attrs.some((a) => a.name === c.attribute) ? attrs.concat([{ name: c.attribute, kind }]) : attrs)
    .map((a) => ({ id: a.name, words: columnWords(a.name, a.kind) }));
  return (
    <div className="lab-row lab-xe-cond">
      <Choice label={label + ': column'} value={c.attribute} options={columns} placeholder="Choose a column"
        onChange={(attribute) => onChange(attribute ? changeColumn(c, attribute, map, concept) : Object.assign({}, c, { attribute: '' }))} />
      <Choice label={label + ': how'} value={opChoice(c)} options={opsFor(map, kind, opChoice(c))} onChange={(op) => onChange(changeOp(c, op))} />
      <ValueFields c={c} kind={kind} map={map} label={label} onChange={onChange} />
      <button type="button" className="lab-link" aria-label={'Remove ' + lower(label)} onClick={onRemove}>{'Remove'}</button>
    </div>
  );
}

function StateBoxes({ node, map, prefix, onChange }) {
  const choices = statesOf(map, node.concept);
  const known = new Set(choices.map((c) => c.id));
  const all = choices.concat(list(node.states).filter((s) => !known.has(s)).map((id) => ({ id, description: '' })));
  const on = new Set(node.states);
  return (
    <fieldset className="lab-xq-group"><legend>{'States'}</legend>
      {all.length ? (
        <>
          <div className="lab-xq-states">
            {all.map((c) => (
              <label key={c.id} className="lab-xq-state">
                <input type="checkbox" aria-label={(prefix ? prefix + ': ' : '') + words(c.id)} checked={on.has(c.id)}
                  onChange={(e) => onChange(e.target.checked ? list(node.states).concat([c.id]) : list(node.states).filter((x) => x !== c.id))} />
                <span><strong>{words(c.id)}</strong>{stateHelp(c) ? <span className="lab-small">{' — ' + stateHelp(c)}</span> : null}</span>
              </label>
            ))}
          </div>
          <p className="lab-small lab-xe-hint">{'Tick none to include every state.'}</p>
        </>
      ) : <p className="lab-small">{'This kind of record has no states: use conditions.'}</p>}
    </fieldset>
  );
}

// What one part of the query reads: the kind of record, its name or code, its states and its conditions.
function NodeFields({ node, map, name, focus, concepts, onChange, onConcept, extra }) {
  const set = (change) => onChange(Object.assign({}, node, change));
  const cond = (i) => (focus ? 'Condition ' : name + ', condition ') + (i + 1);
  return (
    <>
      <div className="lab-row lab-xe-pair">
        <label className="lab-field"><span>{focus ? 'What' : 'Kind of record'}</span>
          <Choice label={focus ? 'What' : name + ': kind of record'} value={node.concept} options={concepts} placeholder="Choose" onChange={onConcept} />
        </label>
        <label className="lab-field lab-xq-building"><span>{'Name or code'}</span>
          <input className="lab-input" placeholder="none" aria-label={focus ? 'Name or code' : name + ': name or code'} value={node.match}
            onChange={(e) => set({ match: e.target.value })} />
        </label>
      </div>
      <StateBoxes node={node} map={map} prefix={focus ? '' : name} onChange={(states) => set({ states })} />
      <fieldset className="lab-xq-group"><legend>{'Conditions'}</legend>
        {node.conditions.map((c, i) => (
          <ConditionRow key={i} c={c} concept={node.concept} map={map} label={cond(i)}
            onChange={(nc) => set({ conditions: node.conditions.map((x, j) => (j === i ? nc : x)) })}
            onRemove={() => set({ conditions: node.conditions.filter((x, j) => j !== i) })} />
        ))}
        <button type="button" className="lab-link" onClick={() => set({ conditions: node.conditions.concat([blankCondition(map, node.concept)]) })}>{'Add a condition'}</button>
      </fieldset>
      {extra || null}
    </>
  );
}

const conceptChoices = (map, ids, current) => {
  const all = ids && ids.length ? ids : list(map && map.concepts).map((c) => c.name);
  const names = current && !all.includes(current) ? [current].concat(all) : all;
  return names.map((id) => ({ id, words: conceptWords(map, id) }));
};

// ── The values, side by side ────────────────────────────────────────────────────────────────────────────────────────
function Chip({ side, fact, which, openKey, onOpenKey }) {
  const key = which + ':' + fact.id;
  const text = fact.kind === 'missing' ? fact.label + ': ' + fact.value : (fact.label ? fact.label + ': ' : '') + String(fact.value);
  const shown = fact.kind === 'missing' ? <strong>{text}</strong> : <>{fact.label ? <span className="lab-xp-k">{fact.label}</span> : null}<strong>{String(fact.value)}</strong></>;
  if (!receiptOf(side, fact)) return <span className="lab-xp-chip lab-xp-static">{shown}<span className="lab-small lab-xp-nolink">{'no query to show'}</span></span>;
  return (
    <button type="button" aria-label={'See how ' + text + ' was found'} aria-expanded={openKey === key ? 'true' : 'false'}
      className={'lab-xp-chip' + (openKey === key ? ' lab-on' : '')} onClick={() => onOpenKey && onOpenKey(openKey === key ? null : key)}>
      {shown}
    </button>
  );
}

function Side({ title, side, which, openKey, onOpenKey }) {
  return (
    <section className="lab-xe-col" aria-label={title}>
      <span className="lab-xp-h">{title}</span>
      {when(side.read_at) ? <span className="lab-small">{'Read ' + when(side.read_at)}</span> : null}
      <p className="lab-ses-pre">{side.sentence}</p>
      {list(side.unresolved).length && side.found === false ? <p className="lab-small">{'The records do not hold ' + side.unresolved.join(', ') + '. Check the spelling.'}</p> : null}
      <div className="lab-xp-chips">
        {list(side.facts).map((f) => <Chip key={f.id} side={side} fact={f} which={which} openKey={openKey} onOpenKey={onOpenKey} />)}
      </div>
      {list(side.notes).length ? (
        <details className="lab-xp-block lab-xp-tech">
          <summary>{'How the query was read'}</summary>
          <ul className="lab-small lab-xp-notes">{side.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </details>
      ) : null}
    </section>
  );
}

function Result({ result, current, form, openKey, onOpenKey, copied, onCopy, saving, onUse }) {
  const open = /^(before|after):(.+)$/.exec(String(openKey || ''));
  const side = open ? result[open[1]] : null;
  const fact = side ? list(side.facts).find((f) => f.id === open[2]) : null;
  const receipt = fact ? receiptOf(side, fact) : null;
  const after = result.after || {};
  const usable = canUse(result, form);
  return (
    <div className="lab-xq-result lab-xe-result" role="status">
      <div className="lab-xe-cols">
        {result.before ? <Side title={FOUND[current.source] || FOUND.hoistra} side={result.before} which="before" openKey={openKey} onOpenKey={onOpenKey} /> : null}
        {result.after ? <Side title="Your version finds" side={result.after} which="after" openKey={openKey} onOpenKey={onOpenKey} /> : null}
      </div>
      {fact && receipt ? <Receipt fact={fact} receipt={receipt} readAt={side.read_at} explain={explainOf(fact)} copied={copied} onCopy={onCopy} onClose={() => onOpenKey && onOpenKey(null)} /> : null}
      {list(result.changes).length ? (
        <div className="lab-xp-block">
          <span className="lab-xp-h">{'What you changed'}</span>
          <ul className="lab-small lab-xq-changes">{result.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      ) : null}
      {result.after && after.found !== false && !usable ? <p className="lab-small">{'You changed the query after this run. Run it again to see what it finds.'}</p> : null}
      {result.after && after.found !== false ? (
        <div className="lab-row">
          <button type="button" className="lab-btn lab-btn-sm" disabled={!!saving || !usable} onClick={() => onUse && onUse()}>{saving ? 'Saving…' : 'Use as the expected answer'}</button>
        </div>
      ) : null}
    </div>
  );
}

// ── The editor ──────────────────────────────────────────────────────────────────────────────────────────────────────
export function QueryEditorView({ query, map, tab, onTab, forms, onForm, results, errors, busy, openKey, onOpenKey, copied, onCopy, saving, onRun, onUse, onHide }) {
  const q = query || {};
  const m = map || q.map || null;
  const queries = list(q.queries);
  const current = queries.find((x) => x.index === tab) || queries[0];
  if (!current) return null;
  const index = current.index;
  const form = (forms || {})[index] || formOf(current.plan);
  const result = (results || {})[index] || null;
  const error = (errors || {})[index] || null;
  const reading = busy !== null && busy !== undefined && busy !== false;
  const problems = formProblems(form);
  const changed = isChanged(current.plan, form);
  const set = (next) => onForm && onForm(index, next);
  const focusLinks = linksOf(m, form.focus.concept);
  const bySource = LEAD[current.source] || LEAD.hoistra;
  const sentence = (plan, lead) => describePlan(plan, m, lead);
  return (
    <div className="lab-xq lab-xe">
      <div className="lab-row lab-xq-top">
        <ol className="lab-xq-steps">
          <li>{'1. Check what the query looks for'}</li><li>{'2. Change what is wrong'}</li><li>{'3. Run it'}</li><li>{'4. Use it'}</li>
        </ol>
        {onHide ? <button type="button" className="lab-link lab-xq-hide" aria-expanded="true" onClick={onHide}>{'Hide'}</button> : null}
      </div>
      {queries.length > 1 ? (
        <div className="lab-xe-tabs" role="tablist" aria-label="The queries of this question">
          {queries.map((x, n) => (
            <button key={x.index} type="button" role="tab" aria-selected={x.index === index ? 'true' : 'false'} className={'lab-xe-tab' + (x.index === index ? ' lab-on' : '')}
              onClick={() => onTab && onTab(x.index)}>
              <strong>{'Query ' + (n + 1)}</strong><span className="lab-small">{sourceWords(x)}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div role={queries.length > 1 ? 'tabpanel' : undefined}>
        <div className="lab-row lab-xp-src">
          {sourceWords(current) ? <strong className={'lab-xp-source' + (current.source === 'proposed' ? ' lab-xp-proposed' : '')}>{sourceWords(current)}</strong> : null}
          {current.question ? <span className="lab-small">{'Question: ' + current.question}</span> : null}
        </div>
        <p className="lab-xp-plain"><strong>{sentence(current.plan, bySource)}</strong></p>
        {changed ? <p className="lab-xp-plain lab-xq-yours"><strong>{sentence(planOf(form), 'Your version looks for')}</strong></p> : null}
        <datalist id="lab-xe-dates">{DATE_HINTS.concat(periodsOf(m).map((p) => p.id).filter((id) => !DATE_HINTS.includes(id))).map((d) => <option key={d} value={d} />)}</datalist>
        <div className="lab-xe-grid">
          <div className="lab-xe-fields">
            <fieldset className="lab-xq-group"><legend>{'Kind of answer'}</legend>
              <Choice label="Kind of answer" value={form.kind || 'list'} options={Object.keys(KIND_WORDS).map((id) => ({ id, words: KIND_WORDS[id] }))}
                onChange={(kind) => set(Object.assign({}, form, { kind }))} />
            </fieldset>
            <NodeFields node={form.focus} map={m} name="What" focus concepts={conceptChoices(m, null, form.focus.concept)}
              onChange={(n) => set(Object.assign({}, form, { focus: n }))}
              onConcept={(c) => set(changeFocusConcept(form, c, m))} />
            <fieldset className="lab-xq-group"><legend>{'Limited to'}</legend>
              <p className="lab-small lab-xe-hint">{'Other records the answer must be linked to: a building, a vendor, an asset.'}</p>
              {form.limits.map((n, i) => (
                <div key={i} className="lab-xe-item">
                  <div className="lab-row lab-xe-item-head"><strong>{'Limited to ' + (i + 1)}</strong>
                    <button type="button" className="lab-link" aria-label={'Remove limit ' + (i + 1)} onClick={() => set(removeAt(form, 'limits', i, m))}>{'Remove'}</button></div>
                  <NodeFields node={n} map={m} name={'Limited to ' + (i + 1)} concepts={conceptChoices(m, focusLinks, n.concept)}
                    onChange={(nn) => set(setNode(form, 'limits', i, nn))} onConcept={(c) => set(changeNodeConcept(form, 'limits', i, c, m))} />
                </div>
              ))}
              <button type="button" className="lab-link" onClick={() => set(addLimit(form, m))}>{'Add a limit'}</button>
            </fieldset>
            <fieldset className="lab-xq-group"><legend>{'Grouped by'}</legend>
              <p className="lab-small lab-xe-hint">{'Counts the records for each value of a column of what is read, or of a record it is limited to.'}</p>
              {list(form.groupBy).map((g, i) => (
                <div key={i} className="lab-row lab-xe-cond">
                  <Choice label={'Grouped by ' + (i + 1) + ': kind of record'} value={g.concept} options={conceptChoices(m, groupConcepts(form), g.concept)}
                    onChange={(concept) => { const first = attributesOf(m, concept)[0]; set(setGroup(form, i, { concept, attribute: attributesOf(m, concept).some((a) => a.name === g.attribute) ? g.attribute : first ? first.name : '' })); }} />
                  <Choice label={'Grouped by ' + (i + 1) + ': column'} value={g.attribute} placeholder="Choose a column"
                    options={attributesOf(m, g.concept).map((a) => ({ id: a.name, words: columnWords(a.name, a.kind) }))} onChange={(attribute) => set(setGroup(form, i, { attribute }))} />
                  <button type="button" className="lab-link" aria-label={'Remove grouping ' + (i + 1)} onClick={() => set(removeGroup(form, i))}>{'Remove'}</button>
                </div>
              ))}
              <button type="button" className="lab-link" onClick={() => set(addGroup(form, m))}>{'Add a grouping'}</button>
            </fieldset>
            <fieldset className="lab-xq-group"><legend>{'Also show'}</legend>
              <p className="lab-small lab-xe-hint">{'Linked records to fetch for each result.'}</p>
              {form.alsoShow.map((n, i) => {
                const name = 'Also show ' + (i + 1);
                const attrs = attributesOf(m, n.concept);
                return (
                  <div key={i} className="lab-xe-item">
                    <div className="lab-row lab-xe-item-head"><strong>{name}</strong>
                      <button type="button" className="lab-link" aria-label={'Remove linked records ' + (i + 1)} onClick={() => set(removeAt(form, 'alsoShow', i))}>{'Remove'}</button></div>
                    <NodeFields node={n} map={m} name={name} concepts={conceptChoices(m, focusLinks, n.concept)}
                      onChange={(nn) => set(setNode(form, 'alsoShow', i, nn))} onConcept={(c) => set(changeNodeConcept(form, 'alsoShow', i, c, m))}
                      extra={(
                        <div className="lab-row lab-xe-pair">
                          <label className="lab-field"><span>{'Sort by'}</span>
                            <Choice label={name + ': sort by'} value={n.sort ? n.sort.attribute : ''} placeholder="Not sorted" always
                              options={attrs.map((a) => ({ id: a.name, words: columnWords(a.name, a.kind) }))}
                              onChange={(attribute) => set(setNode(form, 'alsoShow', i, { sort: attribute ? { attribute, dir: (n.sort && n.sort.dir) || 'asc' } : null }))} />
                          </label>
                          <label className="lab-field"><span>{'Order'}</span>
                            <Choice label={name + ': order'} value={n.sort ? n.sort.dir || 'asc' : 'asc'}
                              options={[{ id: 'asc', words: 'lowest or oldest first' }, { id: 'desc', words: 'highest or newest first' }]}
                              onChange={(dir) => set(setNode(form, 'alsoShow', i, { sort: n.sort ? Object.assign({}, n.sort, { dir }) : n.sort }))} />
                          </label>
                          <label className="lab-field"><span>{'How many'}</span>
                            <input className="lab-input lab-xq-n" type="number" min="1" max="200" aria-label={name + ': how many'}
                              value={n.limit == null ? '' : String(n.limit)} onChange={(e) => set(setNode(form, 'alsoShow', i, { limit: e.target.value }))} />
                          </label>
                        </div>
                      )} />
                  </div>
                );
              })}
              <button type="button" className="lab-link" onClick={() => set(addInclude(form, m))}>{'Add linked records'}</button>
            </fieldset>
            <div className="lab-row">
              <button type="button" className="lab-btn lab-btn-sm" disabled={reading || problems.length > 0} onClick={() => onRun && onRun(index)}>
                {reading && busy === index ? 'Reading…' : 'Run with my changes'}
              </button>
              <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={reading} onClick={() => set(formOf(current.plan))}>{'Start again'}</button>
            </div>
          </div>
          {error || problems.length ? (
            <aside className="lab-xe-side" role={error ? 'alert' : 'status'}>
              {error ? (
                <>
                  {error.reason === 'bad_query' ? <strong>{'This query cannot run'}</strong> : null}
                  <ul className="lab-xe-words">{list(error.words).map((w, i) => <li key={i}>{w}</li>)}</ul>
                </>
              ) : null}
              {problems.length ? (
                <>
                  <strong>{'Before you run it'}</strong>
                  <ul className="lab-xe-words">{problems.map((w, i) => <li key={i}>{w}</li>)}</ul>
                </>
              ) : null}
            </aside>
          ) : null}
        </div>
        {result ? <Result result={result} current={current} form={form} openKey={openKey} onOpenKey={onOpenKey} copied={copied} onCopy={onCopy} saving={saving} onUse={() => onUse && onUse(index)} /> : null}
      </div>
    </div>
  );
}

// ── The whole: what GET /lab/turns/{id}/expected/query returned, and what a person may do with it ───────────────────
function Note({ children }) { return <p className="lab-small lab-xe-noquery">{children}</p>; }

export default function QueryEditor({ query, map, error, onRun, onUse, onHide, saving, copied, onCopy }) {
  const [tab, setTab] = React.useState(null);
  const [forms, setForms] = React.useState({});
  const [results, setResults] = React.useState({});
  const [errors, setErrors] = React.useState({});
  const [busy, setBusy] = React.useState(null);
  const [openKey, setOpenKey] = React.useState(null);
  const hide = onHide ? <button type="button" className="lab-link lab-xq-hide" aria-expanded="true" onClick={onHide}>{'Hide'}</button> : null;
  const wrap = (body) => <div className="lab-xq lab-xe"><div className="lab-row lab-xq-top">{body}{hide}</div></div>;
  if (!query) {
    return error ? wrap(<div role="alert" className="lab-error">{error}</div>) : wrap(<p className="lab-small" role="status">{'Reading the query…'}</p>);
  }
  if (!query.ready) return wrap(<Note>{noQueryWords('no_map')}</Note>);
  const queries = list(query.queries);
  if (!queries.length) return wrap(<Note>{noQueryWords(query.none)}</Note>);
  const m = map || query.map || null;
  if (query.can_run === false) {
    return (
      <div className="lab-xq lab-xe">
        <div className="lab-row lab-xq-top"><p className="lab-small">{'Only curators and the platform team can change and run a query.'}</p>{hide}</div>
        {queries.map((x) => (
          <div key={x.index} className="lab-xp-query">
            <div className="lab-row lab-xp-src">
              {sourceWords(x) ? <strong className={'lab-xp-source' + (x.source === 'proposed' ? ' lab-xp-proposed' : '')}>{sourceWords(x)}</strong> : null}
              {x.question ? <span className="lab-small">{'Question: ' + x.question}</span> : null}
            </div>
            <p className="lab-xp-plain"><strong>{describePlan(x.plan, m, LEAD[x.source] || LEAD.hoistra)}</strong></p>
          </div>
        ))}
      </div>
    );
  }
  const run = async (index) => {
    const q = queries.find((x) => x.index === index);
    if (!q || !onRun) return;
    setBusy(index); setOpenKey(null);
    setErrors((e) => Object.assign({}, e, { [index]: null }));
    const out = await tryRun(onRun, index, forms[index] || formOf(q.plan));
    if (out.ok) setResults((r) => Object.assign({}, r, { [index]: out.result }));
    else setErrors((e) => Object.assign({}, e, { [index]: { reason: out.reason, words: out.words } }));
    setBusy(null);
  };
  return (
    <QueryEditorView query={query} map={m} tab={tab} onTab={(i) => { setTab(i); setOpenKey(null); }} forms={forms}
      onForm={(i, f) => setForms((p) => Object.assign({}, p, { [i]: f }))} results={results} errors={errors} busy={busy} openKey={openKey} onOpenKey={setOpenKey}
      copied={copied} onCopy={onCopy} saving={saving} onRun={run} onHide={onHide}
      onUse={(index) => { const r = results[index]; if (r && onUse) onUse({ index, plan: r.plan, result: r }); }} />
  );
}
