// openuiSafety — the guard rails that come with OpenUI (@openuidev/react-lang), the generative-UI
// renderer the orchestrator's answers are drawn with.
//
// Two of its behaviours have to be switched off by the host, and nothing in our own code would
// notice if the switch went missing:
// - In development, react-lang's entry mounts a devtools panel by importing JavaScript from
//   cdn.jsdelivr.net at a floating `@0` version and running it with the app's React. Vite's dev
//   server is development, so every dev tab would run whatever that tag points at. The
//   maintainers' switch is a global flag set before the app's first import.
// - @openuidev/lang-core's postinstall script reports each install to the vendor (a hash of the
//   git remote, versions, CI/Docker flags). OPENUI_TELEMETRY_DISABLED=1 silences it; the image
//   builds run `npm ci`, so the flag belongs in the Dockerfiles before that line.
// Versions are pinned exactly: the renderer's parser decides what an answer looks like, and a
// floating range would let a minor release change every answer without a change here.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const pkg = JSON.parse(read('../package.json'));
const html = read('../index.html');

test('react-lang is pinned to exactly 1.0.1 and zod to an exact 4.x', () => {
  assert.equal(pkg.dependencies['@openuidev/react-lang'], '1.0.1');
  assert.match(pkg.dependencies.zod || '', /^4\.\d+\.\d+$/);
});

test('index.html switches the devtools auto-mount off before the app script loads', () => {
  const flag = html.indexOf('Symbol.for("openui.devtools.autoMount")] = true');
  const app = html.indexOf('type="module"');
  assert.ok(flag > -1, 'the devtools flag is missing from index.html');
  assert.ok(app > -1, 'the app module script is missing from index.html');
  assert.ok(flag < app, 'the flag must run before the app module script');
});

for (const file of ['../Dockerfile', '../../../deploy/allinone/Dockerfile']) {
  test(`${file.replace(/^(\.\.\/)+/, '')} silences install telemetry before its first npm`, () => {
    const src = read(file);
    const flag = src.search(/OPENUI_TELEMETRY_DISABLED=1/);
    const npm = src.search(/\bnpm (ci|install)\b/);
    assert.ok(flag > -1, 'OPENUI_TELEMETRY_DISABLED=1 is not set');
    assert.ok(npm > -1, 'no npm install line found');
    assert.ok(flag < npm, 'the flag must come before the first npm install');
  });
}
