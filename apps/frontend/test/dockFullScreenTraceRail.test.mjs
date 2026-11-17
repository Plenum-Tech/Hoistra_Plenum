// The full-screen dock shows the run's trace in a rail on the right (Hussain, 17 Nov 2026: "when
// i enlarge the side chat show me the trace span in the right side only when i enhance it").
// Beside the page the dock has no rail: it is too narrow for two columns.
//
// Also two dock bugs from the same screenshots:
//   · the header read "Complete · logged to the Activity Log" while the answer was still running,
//     because a question has no task steps and the step count alone called it done;
//   · the routing note read "lifecycle data.. Answering" — a reason with its own full stop,
//     joined to a sentence that adds one.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470 };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { tidyNote } = await import('../src/logic/complianceLive.js');

let c;
beforeEach(() => {
  globalThis.window.innerWidth = 1470;
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'buildings', role: 'admin', navOpen: true, orchW: null });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearInterval(c._ccTick); };

const CHAT = [
  { role: 'you', text: 'Which buildings are out of compliance today?' },
  { role: 'bot', text: 'Two are.', domain: 'Compliance', trace: [{ kind: 'tool', tool: 'list_certificates', ranMs: 400 }] }
];

test('beside the page the dock has no trace rail', () => {
  c.setState({ orchOpen: true, ccChat: CHAT });
  const v = c.renderVals();
  assert.equal(v.orchFull, false);
  assert.equal(v.orchRail, false);
  assert.equal(v.orchPad, '22px 28px 24px');
  cleanup();
});

test('full screen with a conversation, the rail shows and the row is centred with room for it', () => {
  c.setState({ orchOpen: true, ccChat: CHAT });
  c.renderVals().orchFullToggle();
  const v = c.renderVals();
  assert.equal(v.orchFull, true);
  assert.equal(v.orchRail, true);
  assert.equal(v.orchTraceShow, true, 'the rail has the turn\'s trace to show');
  assert.equal(v.orchTraceRows.length, 1);
  assert.match(v.orchPad, /- 1310px\) \/ 2\)/, 'centred as column + gap + rail, not as the 880px column alone');
  cleanup();
});

test('full screen while the first answer is still running, the rail is already there', () => {
  c.setState({ orchOpen: true, ccChat: [CHAT[0]], ccBusy: true, ccStream: { steps: [], zones: {}, reasoning: 'Domain routing — x', trace: [], answer: '' } });
  c.renderVals().orchFullToggle();
  const v = c.renderVals();
  assert.equal(v.orchRail, true);
  assert.equal(v.orchTraceLive, true);
  cleanup();
});

test('full screen with no conversation (a form flow, or nothing asked yet) has no rail', () => {
  c.setState({ orchOpen: true, ccChat: [] });
  c.renderVals().orchFullToggle();
  const v = c.renderVals();
  assert.equal(v.orchFull, true);
  assert.equal(v.orchRail, false);
  assert.match(v.orchPad, /- 880px\) \/ 2\)/);
  cleanup();
});

test('going back beside the page takes the rail away', () => {
  c.setState({ orchOpen: true, ccChat: CHAT });
  c.renderVals().orchFullToggle();
  c.renderVals().orchFullToggle();
  assert.equal(c.renderVals().orchRail, false);
  cleanup();
});

test('orchTraceFollow(null) clears a pinned turn — the dock rail\'s "Follow latest"', () => {
  c.setState({ orchOpen: true, ccChat: CHAT, ccTraceIdx: 1 });
  c.renderVals().orchTraceFollow(null);
  assert.equal(c.state.ccTraceIdx, null);
  cleanup();
});

test('an answer still streaming is "Answering", never "Complete"', () => {
  c.setState({ orchOpen: true, orchTask: { id: 't1', steps: [] }, orchDone: 0, ccChat: [CHAT[0]], ccBusy: true, ccTick: 12 });
  let v = c.renderVals();
  assert.doesNotMatch(v.orchStatus, /Complete/);
  assert.match(v.orchStatus, /^Answering · 12 s/);
  assert.equal(v.orchLiveDot, 'block', 'the live dot says it is running too');

  c.setState({ ccBusy: false, ccChat: CHAT });
  v = c.renderVals();
  assert.match(v.orchStatus, /^Complete/, 'once it lands it is complete');
  assert.equal(v.orchLiveDot, 'none');
  cleanup();
});

test('a note joined from two sentences loses the doubled full stop, and an ellipsis is kept', () => {
  assert.equal(
    tidyNote("Read the question → compliance engine — owned by the certificate lifecycle data.. Answering with its tools only."),
    "Read the question → compliance engine — owned by the certificate lifecycle data. Answering with its tools only."
  );
  assert.equal(tidyNote('Thinking...'), 'Thinking...');
  assert.equal(tidyNote('Wait… then.'), 'Wait… then.');
  assert.equal(tidyNote(null), '');
});
