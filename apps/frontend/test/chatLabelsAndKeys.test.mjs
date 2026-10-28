// chatLabelsAndKeys — what an answer is filed and badged as, and the keys and focus that send it.
//
// Finding 23: every engine answer carries the pipeline panel (`compliance_pipeline`), so a reply
// with cards was badged COMPLIANCE, given the shield and filed in the Compliance space — a vendor
// SLA answer read "Compliance" above a trace saying contract_performance. The tools behind the
// reply decide; Compliance only for a real compliance turn (or a card payload with no tool names).
// Saved records labelled the old way are repaired when the store is read.
//
// Finding 51: Enter that confirms an IME candidate (isComposing, keyCode 229) is not a send.
// Finding 49: the dock's composer takes focus as it opens, so the first key typed is not lost.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { syncTurns, makeSession, sessionIcon, spaceKeyOf, sessionDomain, loadSessions, SESSIONS_KEY } = await import('../src/logic/sessions.js');
const { isSendEnter } = await import('../src/logic/renderVals.js');

const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
const memStorage = () => {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; } };
};

// What the vendor engine's reply is built from (orchestrator.py: its tools, the pipeline panel,
// the card payload, the engine wrapper), as the message keeps them (callNames).
const VENDOR = ['get_vendor_scorecards', 'compliance_pipeline', 'compliance_response', 'phase2_engine:contract_performance'];
const ENERGY = ['get_building_cost_drivers', 'compliance_pipeline', 'phase2_engine:energy_intelligence'];
const WO = ['work_order_blockers', 'compliance_pipeline', 'compliance_response', 'phase2_engine:wo_engine'];
const PLANNED = ['answer_from_records', 'planner', 'compliance_pipeline', 'compliance_response'];
const RICH = { narrative: 'n', sections: [], groups: [], kpis: [], actions: [], insights: [], certificates: [], pending: [], offers: [], validation: null, steps: [], cost: null };

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'chat' });
});
const rowOf = (m) => {
  c.setState({ ccChat: [{ role: 'you', text: 'q' }, m] });
  return c.renderVals().orchChat[1];
};

test('a vendor answer with cards is badged Vendors, not Compliance', () => {
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: VENDOR }).domain, 'Vendors');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: ENERGY }).domain, 'Energy');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: WO }).domain, 'Work orders');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: PLANNED }).domain, 'Orchestrator');
});

test('a real compliance turn, and a card payload with no tool names, stay Compliance', () => {
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: ['compliance_pipeline', 'compliance_response'] }).domain, 'Compliance');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: ['list_certificates', 'compliance_pipeline', 'compliance_response'] }).domain, 'Compliance');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH, calls: [] }).domain, 'Compliance');
  assert.equal(rowOf({ role: 'bot', text: 'a', rich: RICH }).domain, 'Compliance');
  assert.equal(rowOf({ role: 'bot', text: 'a', error: true, rich: RICH, calls: VENDOR }).domain, 'Orchestrator');
});

test('a vendor session gets the vendors icon and is filed in the Vendors space', () => {
  const rec = syncTurns(makeSession({ id: 'v', title: 'q', page: 'Home', at: 1 }), [{ role: 'you', text: 'q' }, { role: 'bot', text: 'a', rich: RICH, calls: VENDOR }], 2);
  assert.equal(rec.domain, 'Vendors');
  assert.equal(sessionIcon(rec), 'ph-chart-line-up');
  assert.equal(spaceKeyOf(rec.domain), 'vendors');
  const energy = syncTurns(makeSession({ id: 'e', title: 'q', page: 'Home', at: 1 }), [{ role: 'you', text: 'q' }, { role: 'bot', text: 'a', rich: RICH, calls: ENERGY }], 2);
  assert.equal(energy.domain, 'Energy');
  assert.equal(spaceKeyOf(energy.domain), 'energy');
});

test('a structured answer with no tool names is still Compliance (the stored-turn case)', () => {
  const rec = syncTurns(makeSession({ id: 'x', title: 'q', page: 'Compliance', at: 1 }), [{ role: 'you', text: 'q' }, { role: 'bot', text: '', rich: RICH, calls: [] }], 2);
  assert.equal(sessionDomain(rec), 'Compliance');
});

test('a record saved with the old wrong label is read back under the engine its tools name', () => {
  const st = memStorage();
  const turns = [{ role: 'you', text: 'Which vendors are slipping?' }, { role: 'bot', text: 'a', rich: RICH, calls: VENDOR }];
  st.setItem(SESSIONS_KEY, JSON.stringify([
    { id: 'old-vendor', title: 'Which vendors are slipping?', page: 'Home', at: 5, turns: turns, calls: VENDOR, domain: 'Compliance' },
    // Shed: no transcript here, but the tools seen across it are kept.
    { id: 'old-shed', title: 'Energy?', page: 'Home', at: 4, turns: [], calls: ENERGY, domain: 'Compliance', remote: true },
    // Nothing to read the engine off: the stored label stands.
    { id: 'bare', title: 'Bare', page: 'Home', at: 3, turns: [], calls: [], domain: 'Compliance' }
  ]));
  const list = loadSessions(st);
  const by = (id) => list.find((r) => r.id === id);
  assert.equal(by('old-vendor').domain, 'Vendors');
  assert.equal(sessionIcon(by('old-vendor')), 'ph-chart-line-up');
  assert.equal(by('old-shed').domain, 'Energy');
  assert.equal(by('bare').domain, 'Compliance');
});

// ── Enter during IME composition (finding 51) ───────────────────────────────

const IME = { key: 'Enter', isComposing: true, keyCode: 229, preventDefault() {} };
const SAFARI_IME = { key: 'Enter', isComposing: false, keyCode: 229, preventDefault() {} };
const REACT_IME = { key: 'Enter', keyCode: 13, nativeEvent: { isComposing: true }, preventDefault() {} };
const ENTER = { key: 'Enter', isComposing: false, keyCode: 13, preventDefault() {} };

test('isSendEnter: only a plain Enter sends', () => {
  assert.equal(isSendEnter(ENTER), true);
  assert.equal(isSendEnter(IME), false);
  assert.equal(isSendEnter(SAFARI_IME), false);
  assert.equal(isSendEnter(REACT_IME), false);
  assert.equal(isSendEnter({ key: 'a' }), false);
  assert.equal(isSendEnter(null), false);
});

test('the composers do not send the pre-edit text when Enter confirms an IME candidate', () => {
  const sent = [];
  c.askScoped = (q) => { sent.push(q); };
  c.setState({ orchQuery: 'べんだー', pq: 'べんだー', query: 'べんだー' });
  const v = c.renderVals();
  for (const e of [IME, SAFARI_IME, REACT_IME]) { v.orchKey(e); v.pqKey(e); v.onKey(e); }
  assert.deepEqual(sent, []);
  assert.equal(c.state.orchQuery, 'べんだー', 'the composer keeps the text the IME is committing');
  c.renderVals().orchKey(ENTER);
  assert.deepEqual(sent, ['べんだー']);
});

test('the edit box and the thumbs-down reason ignore an IME Enter too', () => {
  let ran = 0;
  c.ccEditRun = () => { ran += 1; };
  c.crWhySend = () => { ran += 1; };
  c.setState({ ccChat: [{ role: 'you', text: 'q' }] });
  const v = c.renderVals();
  v.ccEditKey(IME);
  v.ccEditKey(SAFARI_IME);
  v.orchChat[0].whyKey(IME);
  assert.equal(ran, 0);
  v.ccEditKey(ENTER);
  assert.equal(ran, 1);
});

test('every Enter-to-send handler in the chat slice goes through isSendEnter', () => {
  const s = src('logic/renderVals.js');
  assert.match(s, /orchKey: \(e\) => \{ if \(isSendEnter\(e\)\) this\.orchSubmitNow\(\); \}/);
  assert.equal((s.match(/askKey: \(ev\) => \{ if \(isSendEnter\(ev\)\) this\.spaceAskRun\(\); \}/g) || []).length, 2);
  assert.ok(!/askKey: \(ev\) => \{ if \(ev\.key === "Enter"\)/.test(s));
});

// ── focus as the dock opens (finding 49) ────────────────────────────────────

test('the dock composer takes focus as it mounts, so the first key typed is kept', () => {
  assert.match(src('components/shell/OrchestratorDock.jsx'), /<input id="orch-composer"[^>]*\sautoFocus[\s/]/);
  // The chat page's composer already did.
  assert.match(src('screens/Chat.jsx'), /id="chat-composer"[^>]*autoFocus/);
});

test('ccOpenChat focuses a mounted composer at once, not 80 ms later', () => {
  const focused = [];
  const el = { focus: () => focused.push('chat-composer') };
  globalThis.document = { getElementById: (id) => (id === 'chat-composer' ? el : null) };
  try {
    c.setState({ view: 'chat' });
    c.ccOpenChat();
    assert.deepEqual(focused.slice(0, 1), ['chat-composer'], 'focused in the same task as the click');
  } finally {
    delete globalThis.document;
  }
});

test('support\'s composer focus is immediate too', () => {
  const s = src('logic/support.js');
  assert.ok(!/setTimeout\(\(\) => \{ const el = document\.getElementById\('chat-composer'\); if \(el\) el\.focus\(\); \}, 80\)/.test(s));
});
