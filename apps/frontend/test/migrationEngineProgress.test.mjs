// migrationEngineProgress — the one line under the step title while a Go-engine step runs. The
// status poll carries engine_progress ({engine, step, stage, table, done, total, rate_per_s, at},
// written by the service at most every half second); progressLine says where the step is in
// words, and nothing once the report is more than 30 s old (the step finished or the worker went).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { progressLine } from '../src/cafm/hoistra-wizard-steps.js';

const NOW = Date.parse('2026-10-01T15:00:10Z');
const at = '2026-10-01T15:00:00'; // the service writes UTC with no zone (datetime.utcnow().isoformat())
const p = (over) => ({ engine: 'go', at, done: 0, total: 0, table: null, stage: null, ...over });

test('the write says which table and how many rows', () => {
  assert.equal(progressLine(p({ step: 'write', stage: 'insert', table: 'meter_readings', done: 120000, total: 277412 }), NOW),
    'Writing meter_readings — 120,000 of 277,412 rows');
  assert.equal(progressLine(p({ step: 'write', stage: 'resolve', table: 'assets', done: 50, total: 1200 }), NOW),
    'Matching references in assets — 50 of 1,200 rows');
  assert.equal(progressLine(p({ step: 'write', stage: 'insert', table: 'assets' }), NOW), 'Writing assets');
  assert.equal(progressLine(p({ step: 'write_plan', table: 'assets' }), NOW), 'Checking what the write will do — assets');
});

test('the other Go steps say what they are reading or making', () => {
  assert.equal(progressLine(p({ step: 'parse', stage: 'parse', table: 'Assets', done: 3, total: 16 }), NOW),
    'Reading the workbook — Assets');
  assert.equal(progressLine(p({ step: 'combine', table: 'b101.xlsx', done: 1, total: 2 }), NOW),
    'Combining the uploads — b101.xlsx');
  assert.equal(progressLine(p({ step: 'preprocess', table: 'Work_Orders', done: 5, total: 17 }), NOW),
    'Cleaning Work_Orders — 5 of 17 tables');
  assert.equal(progressLine(p({ step: 'outputs', table: 'output.xlsx', done: 3, total: 6 }), NOW),
    'Writing the output files — output.xlsx');
  assert.equal(progressLine(p({ step: 'udr', stage: 'test1', done: 3, total: 7 }), NOW), 'Checking relationships — 3 of 7');
});

test('a report older than 30 s, or none, says nothing', () => {
  assert.equal(progressLine(p({ step: 'write', at: '2026-10-01T14:59:39', table: 'x', done: 1, total: 2 }), NOW), null);
  assert.equal(progressLine(p({ step: 'write', at: '2026-10-01T14:59:41', table: 'x', done: 1, total: 2 }), NOW),
    'Writing x — 1 of 2 rows');
  assert.equal(progressLine(null, NOW), null);
  assert.equal(progressLine(undefined, NOW), null);
  assert.equal(progressLine(p({ step: 'write', at: 'not a time' }), NOW), null);
  assert.equal(progressLine(p({ step: 'mystery', table: 'x' }), NOW), null);
});

test('a time with a zone or fractional seconds is read as written', () => {
  assert.equal(progressLine(p({ step: 'udr', done: 1, total: 7, at: '2026-10-01T15:00:05.250000' }), NOW),
    'Checking relationships — 1 of 7');
  assert.equal(progressLine(p({ step: 'udr', done: 1, total: 7, at: '2026-10-01T16:00:05+01:00' }), NOW),
    'Checking relationships — 1 of 7');
});
