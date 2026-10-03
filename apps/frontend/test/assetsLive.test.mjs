// healthBand — plenum_cafm.assets.health_score (integer, 0-100 check constraint) mapped to
// the Threat / Watch / In-control language the Assets page speaks. An asset that has never
// been scored is "unscored", never silently folded into "in control": no score is not
// evidence of health.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { healthBand } from '../src/logic/assetsLive.js';

test('healthBand: below 40 is a threat, 40-69 is a watch, 70+ is in control', () => {
  assert.equal(healthBand(0).cond, 'threat');
  assert.equal(healthBand(39).cond, 'threat');
  assert.equal(healthBand(40).cond, 'watch');
  assert.equal(healthBand(69).cond, 'watch');
  assert.equal(healthBand(70).cond, 'ok');
  assert.equal(healthBand(100).cond, 'ok');
});

test('healthBand: a missing score is unscored, not in control', () => {
  assert.equal(healthBand(null).cond, 'unscored');
  assert.equal(healthBand(undefined).cond, 'unscored');
  assert.equal(healthBand(null).label, 'Not scored');
  assert.equal(healthBand(null).tone, 'dormant');
});
