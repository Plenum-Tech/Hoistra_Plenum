// Every figure card on Energy, Assets and Maintenance opens what it counts (23 Nov 2026, Hussain: "I want to make all
// the cards clickable"): a filter of the page's own list and a scroll to it, Maintenance's statutory-only view, its
// inspection question, or (Energy's ratings and duties) a question to the orchestrator. Placeholders open nothing.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cardTarget, withCardLinks, withRatingLinks, ratingQuestion, opens, openTarget, VALUE_AT_RISK, UNCONVERTED_Q }
  from '../src/logic/cardLinks.js';

function app() {
  const calls = [];
  return {
    calls, state: {},
    setState(p) { calls.push(['setState', p]); },
    asCondSetFilter(f) { calls.push(['asCondSetFilter', f]); },
    mxSetFilter(f) { calls.push(['mxSetFilter', f]); },
    mxInspAsk(q) { calls.push(['mxInspAsk', q]); },
    askNew(q) { calls.push(['askNew', q]); }
  };
}

test('each Assets card opens the asset list on its own band', () => {
  assert.deepEqual(cardTarget('assets', { l: 'Threat' }), { filter: 'Threat', section: 'mod-list' });
  assert.deepEqual(cardTarget('assets', { l: 'Watch' }), { filter: 'Watch', section: 'mod-list' });
  assert.deepEqual(cardTarget('assets', { l: 'In control' }), { filter: 'In control', section: 'mod-list' });
  assert.deepEqual(cardTarget('assets', { l: 'Est. asset value at risk' }), { filter: VALUE_AT_RISK, section: 'mod-list' });
});

test('each Energy card opens the building list on what it counts', () => {
  assert.equal(cardTarget('energy', { l: 'Buildings in scope' }).filter, 'All');
  assert.equal(cardTarget('energy', { l: 'EUI, area weighted' }).filter, 'Over benchmark');
  assert.equal(cardTarget('energy', { l: 'Cost above benchmark / year' }).filter, 'Over benchmark');
  assert.equal(cardTarget('energy', { l: 'Anomaly cost / year' }).filter, 'With anomalies');
});

test('each Maintenance card opens its own part of the page', () => {
  assert.deepEqual(cardTarget('ops', { k: 'decisions_owed' }), { filter: 'All', statutory: false, section: 'mx-decisions' });
  assert.deepEqual(cardTarget('ops', { k: 'statutory' }), { filter: 'All', statutory: true, section: 'mx-decisions' });
  assert.deepEqual(cardTarget('ops', { k: 'recommendations_unconverted' }), { section: 'mx-insp', inspect: UNCONVERTED_Q });
  assert.deepEqual(cardTarget('ops', { k: 'ppm_to_plan' }), { section: 'mx-ppm' });
  assert.equal(cardTarget('ops', { k: 'something_else' }), null);
});

test('a card the mapping does not know, or a placeholder, stays a plain figure', () => {
  const cards = withCardLinks('assets', [{ l: 'Threat', v: '6' }, { l: 'Unknown', v: '1' }], app());
  assert.equal(typeof cards[0].click, 'function');
  assert.equal(cards[1].click, undefined);
  assert.equal(opens({ l: 'Ratings', v: '…' }), false);
  assert.equal(withRatingLinks([{ l: 'Ratings', v: '—', s: 'could not reach' }], app())[0].click, undefined);
});

test('opening an Assets card sets the chip and asks the condition engine for the band, as the chip does', () => {
  const a = app();
  withCardLinks('assets', [{ l: 'Watch', v: '9' }], a)[0].click();
  assert.deepEqual(a.calls, [['setState', { filter: 'Watch' }], ['asCondSetFilter', 'Watch']]);
});

test('opening an Energy card sets the chip only', () => {
  const a = app();
  openTarget('energy', cardTarget('energy', { l: 'Anomaly cost / year' }), a);
  assert.deepEqual(a.calls, [['setState', { filter: 'With anomalies' }]]);
});

test('Maintenance: statutory sets its view, the decisions card clears it, the recommendations card asks the panel', () => {
  let a = app();
  openTarget('ops', cardTarget('ops', { k: 'statutory' }), a);
  assert.deepEqual(a.calls, [['setState', { mxStatutory: true }], ['mxSetFilter', 'All']]);
  a = app();
  openTarget('ops', cardTarget('ops', { k: 'decisions_owed' }), a);
  assert.deepEqual(a.calls, [['setState', { mxStatutory: false }], ['mxSetFilter', 'All']]);
  a = app();
  openTarget('ops', cardTarget('ops', { k: 'recommendations_unconverted' }), a);
  assert.deepEqual(a.calls, [['mxInspAsk', UNCONVERTED_Q]]);
});

test('a ratings card asks which buildings it counts, in its own words', () => {
  const card = { l: 'MEES — proposed 2030', v: '2', s: 'below EPC B · over 1,000 m²' };
  assert.equal(ratingQuestion(card),
    'Which of my buildings are counted in "MEES — proposed 2030" (2 — below EPC B · over 1,000 m²), and what should I do about each of them?');
  const a = app();
  withRatingLinks([card], a)[0].click();
  assert.deepEqual(a.calls, [['askNew', ratingQuestion(card)]]);
  assert.equal(ratingQuestion({ l: 'EPCs on file', v: '—', s: '' }), 'Which of my buildings are counted in "EPCs on file", and what should I do about each of them?');
});
