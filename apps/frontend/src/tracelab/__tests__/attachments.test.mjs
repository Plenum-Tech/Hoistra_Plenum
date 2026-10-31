// Attachments A4 and A5, pinned (Golden Rule G0: each approved edit to an existing file is additive, inert while
// its flag is off, and held by a test). A4: both frontend Dockerfiles take VITE_TRACE_LAB as a build argument,
// default 0, in the frontend build stage only. A5: the gateway proxies /backend/trace-lab/ through a variable
// upstream (so the gateway starts and only this route answers 502 when the trace-lab container is absent), and no
// compose file makes anything depend on trace-lab.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { FRONTEND } from './esbuild-helper.mjs';

const REPO = resolve(FRONTEND, '..', '..');
const read = (...p) => readFileSync(join(...p), 'utf8');
// Exactly addendum A4: two lines, no comment.
const A4_LINES = 'ARG VITE_TRACE_LAB=0\n'
  + 'ENV VITE_TRACE_LAB=$VITE_TRACE_LAB\n';
const A4_ANCHOR = '    VITE_ORGANIZATION_ID=$VITE_ORGANIZATION_ID\n' + A4_LINES + 'RUN npm run build\n';
const count = (s, sub) => s.split(sub).length - 1;

test('A4: the frontend Dockerfile takes VITE_TRACE_LAB, default 0, right before its build', () => {
  const src = read(FRONTEND, 'Dockerfile');
  assert.equal(count(src, A4_ANCHOR), 1);
  assert.equal(count(src, 'VITE_TRACE_LAB'), 3);
});

test('A4: the all-in-one Dockerfile takes it in the frontend stage, and nowhere else', () => {
  const src = read(REPO, 'deploy', 'allinone', 'Dockerfile');
  const start = src.indexOf('FROM node:20-alpine AS frontend');
  assert.ok(start > -1);
  const end = src.indexOf('\nFROM ', start + 1);
  const stage = src.slice(start, end);
  assert.equal(count(stage, A4_ANCHOR), 1);
  assert.equal(count(src, 'VITE_TRACE_LAB'), 3, 'only the frontend stage names it');
});

// Exactly addendum A5, no comment.
const A5_BLOCK = '    location /backend/trace-lab/ {\n'
  + '        set $trace_lab http://trace-lab:8010;\n'
  + '        rewrite ^/backend/trace-lab/(.*)$ /$1 break;\n'
  + '        proxy_pass $trace_lab;\n'
  + '    }\n';

test('A5: the gateway proxies /backend/trace-lab/ through a variable upstream, before location /', () => {
  const src = read(FRONTEND, 'infra', 'single-url', 'nginx.conf');
  assert.equal(count(src, A5_BLOCK), 1);
  assert.ok(src.indexOf(A5_BLOCK) < src.indexOf('    location / {\n'));
  assert.ok(src.includes('resolver 127.0.0.11'), 'the variable upstream is resolved per request by this resolver');
  assert.equal(/proxy_pass\s+https?:\/\/trace-lab/.test(src), false, 'a static trace-lab upstream would stop the gateway starting without it');
  const at = src.indexOf(A5_BLOCK);
  const elsewhere = (src.slice(0, at) + src.slice(at + A5_BLOCK.length)).split('\n').filter((l) => l.includes('trace-lab'));
  assert.deepEqual(elsewhere, [], 'trace-lab appears only in its own location block');
  assert.ok(src.includes('    }\n\n' + A5_BLOCK + '\n    location / {\n'), 'the block sits between the ops-intelligence block and location /, one blank line each side');
});

test('A5: no existing compose file mentions trace-lab, so nothing depends on it', () => {
  for (const f of ['docker-compose.single-url.local.yml', 'docker-compose.testenv.yml', 'docker-compose.azure-safe.yml']) {
    const p = join(REPO, f);
    if (!existsSync(p)) continue;
    assert.equal(read(p).includes('trace-lab'), false, f);
  }
});
