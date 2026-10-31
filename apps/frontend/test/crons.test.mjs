// crons — scheduling a job from the chat (logic/crons.js): what counts as a request, what the
// card is ticked with, and the cadence the service is sent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isScheduleRequest, guessJobs, guessCadence, guessTime, refreshFor, summaryLine } from '../src/logic/crons.js';

test('a scheduling request is told from a question that mentions time', () => {
  assert.ok(isScheduleRequest('Run an energy anomaly scan every hour'));
  assert.ok(isScheduleRequest('schedule the compliance expiry check daily at 7am'));
  assert.ok(isScheduleRequest('create a cron job for meter gaps'));
  assert.ok(isScheduleRequest('send me a compliance summary every Monday at 9'));
  assert.ok(!isScheduleRequest('what is our daily consumption at Bishopsgate?'));
  assert.ok(!isScheduleRequest('show the PPM schedule for October'));
  assert.ok(!isScheduleRequest("what's scheduled for tomorrow"));
  assert.ok(!isScheduleRequest('In Bishopsgate Tower what is status of Lift Asset-4471?'));
});

test('the card is ticked with the jobs the sentence names', () => {
  assert.deepEqual(guessJobs('run the energy anomaly scan and the chiller check every hour'), ['energy_anomaly_scan', 'energy_chiller_scan']);
  assert.deepEqual(guessJobs('schedule the compliance expiry scan nightly'), ['compliance_expiry_scan']);
  assert.deepEqual(guessJobs('create a cron'), []);
});

test('the cadence and time are read from the sentence', () => {
  assert.equal(guessTime('daily at 7am'), '07:00');
  assert.equal(guessTime('at 19:30'), '19:30');
  assert.equal(guessTime('at 2 pm'), '14:00');
  assert.equal(guessTime('nightly'), '02:00');
  assert.deepEqual(guessCadence('every hour'), { freq: '1h' });
  assert.deepEqual(guessCadence('every 30 minutes'), { freq: '30m' });
  assert.deepEqual(guessCadence('daily at 7am'), { freq: 'daily', time: '07:00' });
  assert.deepEqual(guessCadence('every Monday and Thursday at 9'), { freq: 'days', time: '09:00', days: [1, 4] });
  assert.deepEqual(guessCadence('on weekdays at 6:30'), { freq: 'days', time: '06:30', days: [1, 2, 3, 4, 5] });
  assert.deepEqual(guessCadence('create a cron'), { freq: '1h' });
});

test('the service is sent its own refresh shape, or the card says what is missing', () => {
  assert.deepEqual(refreshFor({ freq: '6h' }), { refresh: { every_minutes: 360 } });
  assert.deepEqual(refreshFor({ freq: 'daily', time: '7:05' }), { refresh: { daily_at: '07:05' } });
  assert.deepEqual(refreshFor({ freq: 'days', time: '09:00', days: [4, 1] }), { refresh: { days: [1, 4], time: '09:00' } });
  assert.equal(refreshFor({ freq: 'days', time: '09:00', days: [] }).error, 'Pick at least one day.');
  assert.equal(refreshFor({ freq: 'daily', time: '25:00' }).error, 'Choose a time, HH:MM.');
});

test('a run is one line of its figures', () => {
  assert.equal(summaryLine({ ok: true, meters_scanned: 58, alerts_created: 3 }), '58 meters scanned · 3 alerts created');
  assert.equal(summaryLine(null), '');
});

test('"send me … every Monday at 9" is a question job, emailed, on Mondays at 09:00', async () => {
  const { promptFrom } = await import('../src/logic/crons.js');
  const q = 'Send me a compliance summary every Monday at 9';
  assert.ok(isScheduleRequest(q));
  assert.deepEqual(guessCadence(q), { freq: 'days', time: '09:00', days: [1] });
  assert.equal(promptFrom(q), 'Give me a compliance summary.');
  assert.equal(promptFrom('which certificates lapsed this week, daily at 8'), 'Which certificates lapsed this week?');
  assert.equal(summaryLine({ tools: 3, emailed_to: 'a@b.c', email_status: 'sent' }), 'emailed to a@b.c');
  assert.equal(summaryLine({ tools: 3, emailed_to: 'a@b.c', email_status: 'failed' }), 'email to a@b.c failed');
  assert.equal(summaryLine({ tools: 3 }), 'answered');
});
