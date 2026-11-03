// Instances — which instance the lab is working in, chosen the way a customer chooses one.
//
// An instance is not a label on a run. agents/spaces.py decides which engines may answer at all,
// so the same question asked in Ops Brain and in the Compliance Engine is two different
// measurements, and a batch generated without one fills with questions the instance refuses.
// That is worth a page rather than a dropdown: the choice is the first thing about a batch, and
// the cards say what each instance is for and which modules answer in it.
//
// The four come from logic/instances.js — the same definitions the Hoistra picker shows — rather
// than a copy here, so a card cannot describe an instance the product no longer has. The lab
// reads them; it never sets the app's active space (that is the chat's own, set per request).
import React from 'react';
import { INSTANCES, REPORT_LABEL } from '../logic/instances.js';

export const DEFAULT_INSTANCE = 'ops';
const KEY = 'lab.instance';

export function readInstance() {
  try {
    const v = window.sessionStorage.getItem(KEY);
    return INSTANCES.some((i) => i.key === v) ? v : DEFAULT_INSTANCE;
  } catch (e) {
    // Private windows and blocked site data throw rather than return null.
    return DEFAULT_INSTANCE;
  }
}

export function writeInstance(key) {
  try { window.sessionStorage.setItem(KEY, String(key)); } catch (e) { /* nothing to do */ }
}

export const instanceName = (key) => (INSTANCES.find((i) => i.key === key) || {}).name || key;

export default function Instances({ instance, onPick }) {
  return (
    <div className="lab-page-body lab-page-full">
      <div className="lab-page-head">
        <h1>{'Instance'}</h1>
        <p className="lab-small lab-big">
          {'Each instance answers from its own modules, so a question asked in one cannot always be asked in another. Everything the lab generates and runs is asked in the instance chosen here.'}
        </p>
      </div>

      <div className="lab-inst-grid">
        {INSTANCES.map((i) => {
          const on = i.key === instance;
          return (
            <button key={i.key} type="button" aria-pressed={on ? 'true' : 'false'}
              className={'lab-inst' + (on ? ' lab-on' : '')} onClick={() => onPick(i.key)}>
              <span className="lab-inst-head">
                <span className="lab-inst-n">{i.n}</span>
                <span className="lab-inst-name">{i.name}</span>
                {on ? <span className="lab-inst-on">{'working here'}</span> : null}
              </span>
              <span className="lab-inst-icp">{i.icp}</span>
              <span className="lab-inst-desc">{i.desc}</span>
              <span className="lab-inst-reports">
                {(i.reports || []).map((r) => REPORT_LABEL[r] || r).join(' · ')}
              </span>
            </button>
          );
        })}
      </div>

      <p className="lab-note">
        {'The chosen instance is kept for this browser tab. A run records it, so two runs of the same questions in different instances are never read as one measurement.'}
      </p>
    </div>
  );
}
