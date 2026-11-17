// A failed answer says what happened in plain words and offers Retry (Hussain, 17 Nov 2026, from
// the UI audit: "Could not answer: 404 Not Found" gave no cause, no retry and no next step).
// The server's own message stays, as a small detail line under the sentence.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { answerErrorText } = await import('../src/logic/complianceLive.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

test('each kind of failure gets its own plain sentence, never an HTTP code', () => {
  const cases = [
    [{ status: 404 }, '404 Not Found', /isn't available right now/],
    [{ status: 503 }, 'HTTP 503', /can't be reached right now/],
    [{}, 'TypeError: Failed to fetch', /can't be reached right now/],
    [{ status: 401 }, 'HTTP 401', /signed out|sign in again/i],
    [{ status: 403 }, 'HTTP 403', /access/i],
    [{ status: 429 }, 'HTTP 429', /busy/i],
    [{ status: 500 }, 'HTTP 500 Internal Server Error', /couldn't answer this time/],
    [{}, 'The request timed out after 180s', /didn't answer in time/]
  ];
  for (const [err, msg, re] of cases) {
    const out = answerErrorText(err, msg);
    assert.match(out.text, re, msg);
    assert.doesNotMatch(out.text, /\b(404|401|403|429|500|503)\b/, 'no status code in the sentence: ' + msg);
    assert.match(out.text, /Nothing was changed|may still be finishing/, 'says whether anything happened: ' + msg);
  }
});

test('the server message is kept as the detail line', () => {
  const out = answerErrorText({ status: 404 }, '404 Not Found');
  assert.equal(out.detail, '404 Not Found');
});

test('Retry is offered for every failure except a timeout, which may still be running', () => {
  assert.equal(answerErrorText({ status: 404 }, '404 Not Found').retry, true);
  assert.equal(answerErrorText({ status: 503 }, 'HTTP 503').retry, true);
  assert.equal(answerErrorText({}, 'The request timed out after 180s').retry, false);
});

test('an error bubble carries its detail and a Retry that asks the same question again', () => {
  const c = new HoistraLogic({});
  const asked = [];
  c.askScoped = (q) => { asked.push(q); };
  c.setState({ signedIn: true, view: 'buildings', orchOpen: true, ccChat: [
    { role: 'you', text: 'Which buildings are out of compliance today?' },
    { role: 'bot', error: true, text: "The assistant isn't available right now. Nothing was changed.", detail: '404 Not Found', retryQ: 'Which buildings are out of compliance today?' }
  ] });
  const m = c.renderVals().orchChat[1];
  assert.equal(m.errorDetail, '404 Not Found');
  assert.equal(m.retryShow, true);
  m.retry();
  assert.deepEqual(asked, ['Which buildings are out of compliance today?']);
  assert.equal(m.fg, 'var(--color-text)', 'error text is readable ink, not risk-orange on pink (2.75:1)');
  clearInterval(c._orchTick); clearTimeout(c._tt);
});

test('no Retry while another answer is running', () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, view: 'buildings', orchOpen: true, ccBusy: true, ccChat: [
    { role: 'bot', error: true, text: 'x', detail: 'y', retryQ: 'q' }
  ] });
  assert.equal(c.renderVals().orchChat[0].retryShow, false);
  clearInterval(c._orchTick); clearTimeout(c._tt);
});

test('the graph page in an instance that does not offer it falls back Home, never a blank page', () => {
  const c = new HoistraLogic({});
  c.setState({ signedIn: true, view: 'hg', instance: null });
  const v = c.renderVals();
  assert.equal(v.isHG, false);
  assert.equal(v.isHome, true);
  clearInterval(c._orchTick); clearTimeout(c._tt);
});
