// useFollowBottom — the "near the bottom" rule the transcript follow uses.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nearBottom } from '../src/components/shell/useFollowBottom.js';

test('at the very bottom counts as near', () => {
  assert.equal(nearBottom(900, 600, 1500), true);
});

test('within the threshold still counts as near, beyond it does not', () => {
  assert.equal(nearBottom(800, 600, 1500), true);    // 100px short
  assert.equal(nearBottom(700, 600, 1500), false);   // 200px short — the reader has scrolled up
  assert.equal(nearBottom(700, 600, 1500, 250), true);
});

test('content shorter than the viewport is always near the bottom', () => {
  assert.equal(nearBottom(0, 800, 500), true);
});

import { easeStep } from '../src/components/shell/useFollowBottom.js';

test('each frame of the glide covers a fifth of the remaining distance', () => {
  assert.equal(easeStep(0, 1000), 200);
  assert.equal(easeStep(200, 1000), 360);
  assert.equal(easeStep(0, 1000, 0.5), 500);
});

test('the glide never stalls: at least a pixel a frame, and a snap for the last pixel', () => {
  assert.equal(easeStep(996, 1000), 997);   // 0.8px step becomes 1px
  assert.equal(easeStep(999.5, 1000), 1000);
  assert.equal(easeStep(1000, 1000), 1000);
  assert.equal(easeStep(1004, 1000), 1003);  // and it works upward when content shrinks
});
