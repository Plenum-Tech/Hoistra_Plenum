// store — Trace Lab's own state. One small external store read through useSyncExternalStore; nothing here
// touches the app controller (HoistraLogic), its renderVals keys or its session slice. Selectors passed to
// useStore must return a value already in the state (a slice), never a new object, or React re-renders forever.
import { useSyncExternalStore } from 'react';

export const FILTER_DEFAULTS = Object.freeze({ range: '7d', channel: '', status: '', q: '' });

export function createStore(initial) {
  let state = typeof initial === 'function' ? initial() : initial;
  const subs = new Set();
  return {
    get() { return state; },
    set(patch) {
      const p = typeof patch === 'function' ? patch(state) : patch;
      if (!p) return state;
      state = Object.assign({}, state, p);
      subs.forEach((fn) => fn());
      return state;
    },
    subscribe(fn) {
      subs.add(fn);
      return () => { subs.delete(fn); };
    }
  };
}

export function useStore(store, selector) {
  const pick = selector || ((s) => s);
  const read = () => pick(store.get());
  return useSyncExternalStore(store.subscribe, read, read);
}

// Phase 3: what the Golden set tab, the golden runs, the step loop and the expected-outcome panel read, at its
// default (goldenActions.js writes it). A fresh object every call; resetForCompany() resets it with the rest.
export function goldenInitial() {
  return {
    sets: { data: null, loading: false, error: '', reason: '', loadedAt: null },
    setId: null,             // the version the Golden set tab shows
    cases: { data: null, loading: false, error: '', q: '', flag: '', page: 0 },
    casesDraft: '',
    shown: {},               // case id -> true: that row's expected answer is shown
    upload: null,            // { busy, dryRun, fileName, error, errors, warnings, report }
    setBusy: '',             // 'activate:<id>' or 'retire:<id>' while it runs
    setError: '',
    runs: { rows: null, loading: false, error: '', reason: '', loadedAt: null },
    dialog: null,            // the start dialog
    detail: { runId: null, data: null, loading: false, error: '', loadedAt: null },
    action: { busy: '', error: '' },
    resumeCap: '',
    own: {},                 // run id -> true: this page started or resumed it, so this page steps it
    pick: [],                // the (at most two) runs ticked for a comparison
    compare: null,           // { base, head, data, loading, error }
    loop: { runId: null, busy: false, error: null, note: '', lastRunId: null, run: null },
    expected: {}             // golden answer's turn id -> { loading, error, data, revealing, hidden }
  };
}

// Every key the Trace Lab page reads, at its default. A fresh object every call.
export function initialState() {
  return {
    seq: 0,                  // bumped by resetForCompany(); a reply issued under an older seq is dropped
    tab: 'conversations',
    run: 'live',
    filters: Object.assign({}, FILTER_DEFAULTS),
    qDraft: '',
    convKey: null,
    turnId: null,
    convs: { rows: null, total: null, cursors: [null], page: 0, loading: false, error: '', reason: '', loadedAt: null, watermark: null },
    facets: null,
    conv: { data: null, loading: false, error: '', reason: '' },
    runs: { rows: null, goldenAvailable: false, loading: false, error: '', reason: '', loadedAt: null },
    calls: { rows: null, aggregate: null, retentionDays: null, cursors: [null], page: 0, loading: false, error: '', reason: '', loadedAt: null },
    call: null,              // the model call open in the side panel: { spanId, loading, error, data, turnId, conversationKey }
    open: {},                // card and panel id -> true/false; a refresh never touches it
    poll: { updatedAt: null, newTurns: 0, error: '' },
    golden: goldenInitial()
  };
}

export const tlStore = createStore(initialState);

// A company switch, a sign-out or leaving the page: every company-scoped value back to its default, the tab
// kept, and the sequence bumped so any reply still in flight is disowned.
export function resetForCompany() {
  tlStore.set((s) => Object.assign(initialState(), { seq: (s.seq || 0) + 1, tab: s.tab }));
}
