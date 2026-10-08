// A Support question is answered by Support, not taken for the action it describes (8 Oct 2026).
//
// "How do I schedule a report every Monday?" asked in Support opened the schedule-a-job card, and
// "Can I run an energy scan every hour?" did the same: the dock's own shortcuts read the words,
// not the session. In Support the reader is asking how; the guide answers.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

function boot(support) {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, role: 'admin', view: 'home', account: { email: 'pm@portfolio.com', role: 'admin' } });
  const took = [];
  c.cronOpenFromChat = () => took.push('schedule card');
  c.bcOpenForm = () => took.push('hoist form');
  if (support) c.openSupport();
  return { c, took };
}
const stop = (c) => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._ccRetry); clearTimeout(c._homeRetry); };

for (const q of ['How do I schedule a report every Monday?', 'Can I run an energy scan every hour?']) {
  test('in Support, "' + q + '" is asked, not opened as a schedule', async () => {
    const { c, took } = boot(true);
    await c.ccAsk(q).catch(() => {});
    assert.deepEqual(took, []);
    assert.ok(c.state.ccChat.some((m) => m.role === 'you' && m.text === q), 'the question is in the conversation');
    stop(c);
  });
}

test('outside Support the same sentence still opens the schedule card', async () => {
  const { c, took } = boot(false);
  await c.ccAsk('Run an energy anomaly scan every hour').catch(() => {});
  assert.deepEqual(took, ['schedule card']);
  stop(c);
});
