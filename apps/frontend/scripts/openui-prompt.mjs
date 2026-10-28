// openui-prompt — writes svc-deepagents' "Answer format" prompt from the chat's component spec.
//
//   node scripts/openui-prompt.mjs           write src/agents/openui_prompt.md
//   node scripts/openui-prompt.mjs --check   exit 1 when the committed file is out of date
//
// The prompt is generated from src/openui/spec.js with @openuidev/lang-core (no React needed), so
// the model is taught exactly the components the chat can draw. Re-run it after any change to
// the spec; test/openuiSpec.test.mjs fails until you do.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as core from '@openuidev/lang-core';
import { z } from 'zod';
import { makeLibrary, promptOptions } from '../src/openui/spec.js';

const target = fileURLToPath(new URL(
  '../../backend/cafm-connector-service-final/svc-deepagents/src/agents/openui_prompt.md', import.meta.url));
const text = makeLibrary(core, z).prompt(promptOptions) + '\n';

if (process.argv.includes('--check')) {
  let current = '';
  try { current = readFileSync(target, 'utf8'); } catch { /* missing counts as out of date */ }
  if (current !== text) {
    console.error('openui_prompt.md is out of date: run node scripts/openui-prompt.mjs');
    process.exit(1);
  }
  console.log('openui_prompt.md is up to date');
} else {
  writeFileSync(target, text);
  console.log('wrote ' + target + ' (' + text.length + ' chars)');
}
