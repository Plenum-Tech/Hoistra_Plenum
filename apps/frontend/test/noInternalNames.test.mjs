// No screen names how Hoistra is built (7 Oct 2026).
//
// Every page used to say where its figures came from in the platform's own terms - "Live ·
// svc-operations-intelligence", "Reading plenum_cafm.sites…", "(GET /api/reports: …)", "Saves
// to svc-udr", "the UDR pass", "RAG / alias" - and customers read all of it. This walks the
// source with its comments stripped (esbuild) and fails on any line that still carries one of
// those names, so the next one is caught here rather than on a customer's screen.
//
// What is allowed is listed, line by line, in ALLOWED: requests and SQL the code sends, and
// values compared in logic, which never reach the screen. Super-admin-only surfaces (the Super
// Admin console, Hoist Traces, Skill lab, Platform cost) exist to show internals and are not
// scanned. Text from outside the codebase - backend error bodies, agent answers - is scrubbed
// at the point it is shown (logic/publicText.js, test/publicText.test.mjs).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { transformSync } from 'esbuild';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SRC = join(ROOT, 'src');

const FORBIDDEN = [
  { name: 'service name', re: /\bsvc-[a-z]/i },
  { name: 'database schema', re: /plenum_cafm/ },
  { name: 'raw route', re: /\b(?:GET|POST|PUT|PATCH|DELETE) \/(?:api|backend)\b/ },
  { name: 'UDR', re: /\bUDR\b/ },
  { name: 'RAG', re: /\bRAG\b/ },
  { name: 'document search service', re: /doc[-_]rag/ },
  { name: 'agent service', re: /deep-?agents/ },
  { name: 'skill document', re: /SKILL\.md|skills\/[\w-]+\// },
  { name: 'worker internals', re: /\barq\b|WorkerSettings/ }
];

// Super-admin-only surfaces, and the scrubber's own patterns.
const SKIP = new Set([
  'src/logic/publicText.js',
  'src/logic/superAdmin.js', 'src/logic/superAdminLive.js', 'src/logic/platformCost.js',
  'src/logic/tracesPage.js', 'src/logic/skillLabPage.js',
  'src/screens/Traces.jsx', 'src/screens/SkillLab.jsx',
  'src/components/shell/SuperAdminOverlay.jsx', 'src/components/shell/PlatformCostPage.jsx', 'src/components/shell/PlatformCostPanel.jsx'
]);

// [file, a substring of the offending line]: never shown, so never a leak.
const ALLOWED = [
  // Gateway mounts the client calls.
  ['src/api/client.js', '"/backend/deep-agents"'],
  ['src/api/client.js', '"/backend/doc-rag"'],
  ['src/cafm/features/ai/doc-rag-api.ts', '/doc-rag'],
  ['src/cafm/features/ai/udr-runs-api.ts', '/deep-agents'],
  // react-query cache keys.
  ['src/cafm/features/ai/doc-rag-api.ts', 'queryKey:["doc-rag"'],
  // The schema a request names, and the SQL the read-only data route runs.
  ['src/cafm/features/ai/doc-rag-api.ts', 'PLENUM_CMMS_SCHEMA="plenum_cafm"'],
  ['src/logic/buildingsCrud.js', 'FROM plenum_cafm.sites'],
  ['src/logic/energyLive.js', 'FROM plenum_cafm.equipment'],
  // The engine a trace names, matched to give the answer its domain label.
  ['src/logic/chat.js', 'engine.includes("doc_rag")']
];

const LOADER = { '.js': 'jsx', '.jsx': 'jsx', '.ts': 'ts', '.tsx': 'tsx', '.mjs': 'js' };

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (LOADER[extname(p)]) out.push(p);
  }
  return out;
}

// Comments out, everything else in: minified whitespace is the one esbuild mode that drops
// every comment (unminified output keeps those inside object literals and JSX).
function stripComments(file) {
  const code = readFileSync(file, 'utf8');
  return transformSync(code, { loader: LOADER[extname(file)], legalComments: 'none', minifyWhitespace: true, jsx: 'preserve', format: 'esm' }).code;
}

test('no shipped source outside the super-admin surfaces names an internal service, table, route or document', () => {
  const found = [];
  for (const file of walk(SRC, [])) {
    const rel = relative(ROOT, file).split('\\').join('/');
    if (SKIP.has(rel)) continue;
    const code = stripComments(file);
    for (const f of FORBIDDEN) {
      const re = new RegExp(f.re.source, f.re.flags.includes('g') ? f.re.flags : f.re.flags + 'g');
      let m;
      while ((m = re.exec(code))) {
        const around = code.slice(Math.max(0, m.index - 70), m.index + 70);
        // An import path names a module, not anything on screen.
        if (/(?:import|from)\s*["'][^"']*$/.test(code.slice(Math.max(0, m.index - 80), m.index))) continue;
        if (ALLOWED.some(([af, sub]) => af === rel && around.includes(sub))) continue;
        found.push(rel + ' [' + f.name + '] …' + around.replace(/\s+/g, ' ') + '…');
      }
    }
  }
  assert.deepEqual(found, [], 'internal names in shipped source:\n' + found.join('\n'));
});
