// A request that already went out is drafted again as a reminder, not a second copy.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { baseSubject, historyNote, reminderDraft } from '../src/logic/emailHistory.js';

const hist = { count: 1, reminders: 0, first_sent_at: '2026-09-30T08:00:00+00:00', last_sent_at: '2026-09-30T08:00:00+00:00',
  last_to: 'bala.r@plenum-tech.com',
  items: [{ to: 'bala.r@plenum-tech.com', subject: 'Work order request — LCP — Basement · Bishopsgate Tower', sent_at: '2026-09-30T08:00:00+00:00', reminder: false }] };
const draft = { kicker: 'Work order request · draft', to: '', subject: 'Work order request — LCP — Basement · Bishopsgate Tower',
  body: 'Hello Northgate Electrical team,\n\nPlease raise a predictive work order on LCP.\n\nRegards,\nHoistra' };

test('a reminder names the request it follows and keeps the original', () => {
  const r = reminderDraft(draft, hist);
  assert.equal(r.subject, 'Reminder: Work order request — LCP — Basement · Bishopsgate Tower');
  assert.equal(r.kicker, 'Work order request · reminder');
  assert.equal(r.to, 'bala.r@plenum-tech.com');                         // the address used last time
  assert.match(r.body, /^Hello Northgate Electrical team,\n\nA reminder of our request of 30 Sept? 2026/);
  assert.match(r.body, /— Original request —\nPlease raise a predictive work order on LCP\./);
});

test('a draft with its own address keeps it, and a reminder of a reminder is not "Reminder: Reminder:"', () => {
  assert.equal(reminderDraft(Object.assign({}, draft, { to: 'ops@vendor.co.uk' }), hist).to, 'ops@vendor.co.uk');
  assert.equal(baseSubject('Reminder: Reminder:  X'), 'X');
  assert.equal(reminderDraft(Object.assign({}, draft, { subject: 'Reminder: ' + draft.subject }), hist).subject, 'Reminder: ' + draft.subject);
});

test('the note says when, to whom, and how many reminders since', () => {
  assert.match(historyNote(hist), /^You sent this request on 30 Sept? 2026, \d\d:\d\d to bala\.r@plenum-tech\.com\.$/);
  const h2 = Object.assign({}, hist, { reminders: 1, last_sent_at: '2026-10-02T08:00:00+00:00' });
  assert.match(historyNote(h2), /1 reminder since, the last on 02 Oct 2026\.$/);
  assert.equal(historyNote({ count: 0 }), '');
});
