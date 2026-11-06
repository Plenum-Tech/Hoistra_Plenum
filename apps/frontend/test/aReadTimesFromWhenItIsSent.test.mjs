// A read's timer starts when the read is sent, not when it is asked for (2 Nov 2026).
//
// The Vendors page in Ops Brain showed "—" on Pending tasks, Pending critical, Invoice lines
// held, Terms on default and Contracts while the gateway logged every one of those reads as a
// 200 in 4-8 s. A page mount asks for forty-odd reads at once; a browser sends six at a time to
// one host over HTTP/1.1, and the rest wait in the browser. apiFetch's 20 s timer was already
// running while they waited, so a read that sat behind slow ones (the compliance summary takes
// 14-16 s, wo-scores up to 25 s) was cut off moments after it was finally sent - and the page
// read a timeout as "not read". Reads now queue in apiFetch, at most five in flight, and each
// read's timer starts when its turn comes. Writes and the orchestrator's long POSTs never queue.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local', href: 'http://test.local/', search: '' },
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};

let inFlight = 0, peak = 0, sent = [];
// A server that answers each request after `ms`, honouring an abort the way fetch does.
function serve(ms) {
  globalThis.fetch = (url, init) => new Promise((resolve, reject) => {
    inFlight += 1; peak = Math.max(peak, inFlight); sent.push((init && init.method) || 'GET');
    const t = setTimeout(() => {
      inFlight -= 1;
      resolve({ ok: true, status: 200, headers: { get: () => null }, text: async () => '{"ok":true}' });
    }, ms);
    if (init && init.signal) init.signal.addEventListener('abort', () => {
      clearTimeout(t); inFlight -= 1;
      const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
    });
  });
}
const reset = () => { inFlight = 0; peak = 0; sent = []; };

const { apiFetch, MAX_READS, enableReadQueue } = await import('../src/api/client.js');
// main.jsx switches it on in the browser.
enableReadQueue(true);

test('no more than MAX_READS reads are in flight at once', async () => {
  reset(); serve(20);
  const out = await Promise.all(Array.from({ length: 12 }, (_, i) => apiFetch('/backend/x', '/r' + i, { auth: false })));
  assert.equal(out.length, 12);
  assert.equal(MAX_READS, 5);
  assert.equal(peak, MAX_READS, 'the rest waited their turn');
});

test('a read that waited its turn is not timed out for the wait', async () => {
  reset(); serve(40);
  // Twelve reads, 40 ms each, five at a time: the last is sent about 80 ms after it was asked
  // for, past its 60 ms timer. Timed from the ask, it would have been cut off.
  const res = await Promise.allSettled(Array.from({ length: 12 }, (_, i) => apiFetch('/backend/x', '/r' + i, { auth: false, timeoutMs: 60 })));
  assert.deepEqual(res.map((r) => r.status), Array(12).fill('fulfilled'), JSON.stringify(res.filter((r) => r.status === 'rejected').map((r) => String(r.reason))));
});

test('a read that is slow once sent still times out', async () => {
  reset(); serve(200);
  await assert.rejects(apiFetch('/backend/x', '/slow', { auth: false, timeoutMs: 30 }), /timed out/);
});

test('writes do not queue behind reads', async () => {
  reset(); serve(30);
  const reads = Array.from({ length: 8 }, (_, i) => apiFetch('/backend/x', '/r' + i, { auth: false }));
  const write = apiFetch('/backend/x', '/w', { auth: false, method: 'POST', body: {} });
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(sent.includes('POST'), 'the write went out while reads were still queued');
  assert.equal(sent.filter((m) => m === 'GET').length, MAX_READS);
  await Promise.all(reads.concat([write]));
});

test('a read cancelled while it waits leaves the queue and is reported as cancelled', async () => {
  reset(); serve(30);
  const first = Array.from({ length: MAX_READS }, (_, i) => apiFetch('/backend/x', '/r' + i, { auth: false }));
  const ctrl = new AbortController();
  const waiting = apiFetch('/backend/x', '/never', { auth: false, signal: ctrl.signal });
  ctrl.abort();
  await assert.rejects(waiting, (e) => e.cancelled === true);
  await Promise.all(first);
  assert.equal(sent.length, MAX_READS, 'the cancelled read was never sent');
  // The slots all came back: the next read goes straight out.
  reset(); serve(5);
  await apiFetch('/backend/x', '/after', { auth: false });
  assert.equal(sent.length, 1);
});

test('a fetch that never settles still gives its slot back when its timer fires', async () => {
  reset();
  globalThis.fetch = () => new Promise(() => { inFlight += 1; });   // ignores the abort
  // Left pending for good: only their slots matter here.
  Array.from({ length: MAX_READS }, (_, i) => apiFetch('/backend/x', '/stuck' + i, { auth: false, timeoutMs: 20 }).catch(() => {}));
  serve(5);
  const next = await apiFetch('/backend/x', '/next', { auth: false, timeoutMs: 200 });
  assert.deepEqual(next, { ok: true });
});

// Pre-push review (3 Nov 2026): a read still waiting for its turn when the company changes (a
// view-as switch, a sign-out) was sent anyway, under the old company and token, and only thrown
// away on the way back - the new company's reads queued behind every one of them.
test('a read still queued when the company changes is never sent', async () => {
  const { setActingOrg, isStaleScope } = await import('../src/api/client.js');
  reset(); serve(30);
  const first = Array.from({ length: MAX_READS }, (_, i) => apiFetch('/backend/x', '/r' + i));
  await new Promise((r) => setTimeout(r, 5));          // the first five are out
  const waiting = apiFetch('/backend/x', '/old-company');
  await new Promise((r) => setTimeout(r, 1));          // and this one is waiting its turn
  setActingOrg('org-2');
  await assert.rejects(waiting, (e) => isStaleScope(e));
  await Promise.allSettled(first);
  assert.equal(sent.length, MAX_READS, 'the queued read of the old company was not sent');
  setActingOrg(null);
});
