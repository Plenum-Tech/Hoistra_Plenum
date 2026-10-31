// toastAboveOverlays — the toast is drawn above every full-screen overlay, so a message raised inside one (the Super
// Admin console's "Re-send admin invitation") is seen; and a long message (an activation link to copy) stays longer.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = fileURLToPath(new URL('../src/', import.meta.url));
const zOf = (text) => [...text.matchAll(/zIndex:\s*"?(\d+)"?/g)].map((m) => Number(m[1]));

function jsxFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === '__tests__' ? [] : jsxFiles(p);
    return p.endsWith('.jsx') ? [p] : [];
  });
}

test('the toast sits above every overlay in the app, the Super Admin console included', () => {
  const toast = Math.max(...zOf(readFileSync(join(SRC, 'components/shell/Toast.jsx'), 'utf8')));
  const console_ = Math.max(...zOf(readFileSync(join(SRC, 'components/shell/SuperAdminOverlay.jsx'), 'utf8')));
  assert.ok(toast > console_, `toast z-index ${toast} must be above the console's ${console_}`);
  const others = jsxFiles(SRC).filter((p) => !p.endsWith('Toast.jsx')).flatMap((p) => zOf(readFileSync(p, 'utf8')));
  assert.ok(toast > Math.max(...others), `toast z-index ${toast} must be above every other layer (${Math.max(...others)})`);
});

test('a short message shows briefly; a long one (an activation link) stays long enough to read', () => {
  const src = readFileSync(join(SRC, 'logic/core.js'), 'utf8');
  const flash = src.slice(src.indexOf('  flash(msg) {'), src.indexOf('  },', src.indexOf('  flash(msg) {')));
  assert.match(flash, /length > 120 \? 9000 : 2600/);
});
