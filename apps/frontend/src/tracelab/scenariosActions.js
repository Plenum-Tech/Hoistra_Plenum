// scenariosActions — the Scenarios tab: generate a synthetic batch for one role space, look at
// it, and download it in the golden-set shape for a person to add expected answers to.
//
// State lives in the module store under `scenarios`. A reply issued before a company switch (the
// store's seq, bumped by resetForCompany) is dropped, like every other Trace Lab action.
//
// Nothing here writes: generation is deterministic from (space, seed) and touches no database, so
// pressing Generate twice is free and the same seed gives the same batch back.
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { scenariosApi } from './scenariosApi.js';

const get = () => tlStore.get();
const S = () => tlStore.get().scenarios;
const setS = (patch) => tlStore.set((s) => ({
  scenarios: Object.assign({}, s.scenarios, typeof patch === 'function' ? patch(s.scenarios) : patch)
}));
const live = (seq) => seq === get().seq;
const msg = (e) => (e && e.message) || String(e);

export function setForm(patch) {
  // The batch on screen belongs to the settings that made it. Changing any of them clears it
  // rather than leaving a batch above controls that no longer describe it.
  setS((s) => Object.assign({}, s, patch, { batch: null, error: '', downloaded: '' }));
}

export async function loadSpaces() {
  if (S().spaces.loading) return;
  const seq = get().seq;
  setS((s) => ({ spaces: Object.assign({}, s.spaces, { loading: true, error: '' }) }));
  try {
    const out = await scenariosApi.spaces();
    if (!live(seq)) return;
    setS({ spaces: { rows: (out && out.spaces) || [], loading: false, error: '' } });
  } catch (e) {
    if (!live(seq) || isStaleScope(e)) return;
    setS({ spaces: { rows: [], loading: false, error: msg(e) } });
  }
}

export async function generate() {
  const s = S();
  if (s.generating) return;
  const seq = get().seq;
  setS({ generating: true, error: '', batch: null, downloaded: '' });
  try {
    const out = await scenariosApi.generate({
      space: s.space, count: Number(s.count) || 50, seed: Number(s.seed) || 0,
      challenge_ratio: Number(s.challenge) || 0
    });
    if (!live(seq)) return;
    setS({ generating: false, batch: out });
  } catch (e) {
    if (!live(seq) || isStaleScope(e)) return;
    setS({ generating: false, error: msg(e) });
  }
}

export async function runQuestions() {
  const s = S();
  if (s.running) return;
  const seq = get().seq;
  setS({ running: true, runError: '', run: null });
  try {
    const out = await scenariosApi.run({
      space: s.space, count: Number(s.runCount) || 2, seed: Number(s.seed) || 0,
      challenge_ratio: Number(s.challenge) || 0
    });
    if (!live(seq)) return;
    setS({ running: false, run: out });
  } catch (e) {
    if (!live(seq) || isStaleScope(e)) return;
    // The service refuses rather than asking a question from a process that could write
    // something; its problems are worth showing, not just the sentence.
    const problems = (e && e.body && e.body.detail && e.body.detail.problems) || [];
    setS({ running: false, runError: msg(e) + (problems.length ? ' — ' + problems.join('; ') : '') });
  }
}

export async function download() {
  const s = S();
  if (s.downloading || !s.batch) return;
  setS({ downloading: true, error: '', downloaded: '' });
  try {
    const name = await scenariosApi.downloadCsv({
      space: s.space, count: Number(s.count) || 50, seed: Number(s.seed) || 0,
      challenge_ratio: Number(s.challenge) || 0
    });
    setS({ downloading: false, downloaded: name });
  } catch (e) {
    setS({ downloading: false, error: msg(e) });
  }
}
