// plainFailure — a failed request in plain words, for the pages that used to append the raw
// backend error to their sentence (Crons, Memories; Hussain, 17 Nov 2026, from the UI audit).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { plainFailure } = await import('../src/logic/plainErrors.js');

test('each kind of failure reads as a plain phrase with no status code', () => {
  const cases = [
    [{ status: 503, message: 'HTTP 503' }, /can't be reached right now/],
    [new TypeError('Failed to fetch'), /can't be reached right now/],
    [{ status: 404, message: '404 Not Found' }, /isn't available right now/],
    [{ status: 401, message: 'HTTP 401' }, /signed out/],
    [{ status: 403, message: 'HTTP 403 forbidden' }, /doesn't have access/],
    [{ status: 429, message: 'HTTP 429' }, /busy/],
    [{ message: 'The request timed out after 30s' }, /didn't answer in time/],
    [{ status: 500, message: 'Internal Server Error: psycopg.errors.UndefinedColumn' }, /went wrong on the server/]
  ];
  for (const [err, re] of cases) {
    const out = plainFailure(err);
    assert.match(out.text, re, JSON.stringify(err));
    assert.doesNotMatch(out.text, /\d{3}|psycopg|Error:/, 'no code or stack in the sentence');
  }
});

test('the raw message is kept as detail, and a plain string works too', () => {
  assert.equal(plainFailure({ status: 404, message: '404 Not Found' }).detail, '404 Not Found');
  assert.match(plainFailure('connect ECONNREFUSED 127.0.0.1:8000').text, /can't be reached/);
  assert.equal(plainFailure(null).text, 'something went wrong on the server');
});

test('a stored message alone still reads by its status code', () => {
  assert.match(plainFailure('404 Not Found').text, /isn't available/);
  assert.match(plainFailure('HTTP 503 Service Unavailable').text, /can't be reached/);
  assert.match(plainFailure('HTTP 401').text, /signed out/);
});

test('the reason phrase alone reads as its status ("Service Unavailable" is unreachable)', () => {
  assert.match(plainFailure('Service Unavailable').text, /can't be reached/);
  assert.match(plainFailure('Bad Gateway').text, /can't be reached/);
  assert.match(plainFailure('Not Found').text, /isn't available/);
  assert.match(plainFailure('Forbidden').text, /doesn't have access/);
});
