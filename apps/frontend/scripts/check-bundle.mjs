// Fail the build rather than ship a bundle that points at a developer's machine.
//
// On 23 Sep 2026 a production build run on a workstation shipped with every service base URL
// set to http://127.0.0.1:<port>. Vite reads .env.local in EVERY mode, not just development,
// and that file held the local-dev overrides. Nothing warned: the build succeeded, the bundle
// deployed, and the app half-worked — only :8009 is published to the host, so the page loaded,
// signed in and drew its cards while every upload failed with "Failed to fetch" against an
// unreachable :8008. The symptom pointed at the service, which was healthy the whole time.
//
// The overrides now live in .env.development.local, which Vite reads in development only, so
// the mistake should not recur. This is the check that says so out loud if it ever does —
// including from a CI runner or a container build, where nobody is watching the output.
//
// Scanning the built output rather than the env files is deliberate: the bundle is the artefact
// that ships, and it is true regardless of which of Vite's six env files a value came from, or
// whether it was hardcoded in a source file instead.
import fs from 'node:fs';
import path from 'node:path';

const DIST = path.resolve(process.argv[2] || 'dist');

// A base URL naming a loopback host or an unqualified port. `localhost` on its own is not
// enough to match — "localhost" appears in prose and in comments about CORS — so the pattern
// requires the scheme and a port, which is what an actual base URL looks like.
const OFFENDERS = [
  { re: /https?:\/\/127\.0\.0\.1:\d+/g, what: 'a loopback base URL' },
  { re: /https?:\/\/localhost:\d+/g, what: 'a localhost base URL' },
  { re: /https?:\/\/0\.0\.0\.0:\d+/g, what: 'a wildcard-address base URL' },
];

/** Every file under dir, recursively. */
function walk(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

if (!fs.existsSync(DIST)) {
  console.error(`check-bundle: no build at ${DIST}`);
  process.exit(1);
}

const TEXT = /\.(js|mjs|cjs|css|html|json|map)$/i;
const findings = [];

for (const file of walk(DIST).filter((f) => TEXT.test(f))) {
  const src = fs.readFileSync(file, 'utf8');
  for (const { re, what } of OFFENDERS) {
    const hits = [...new Set(src.match(re) || [])];
    if (hits.length) findings.push({ file: path.relative(DIST, file), what, hits });
  }
}

if (!findings.length) {
  console.log('check-bundle: no developer-machine URLs in the build');
  process.exit(0);
}

console.error('\ncheck-bundle: this build points at a developer machine and must not ship.\n');
for (const f of findings) {
  console.error(`  ${f.file}  — ${f.what}`);
  for (const h of f.hits) console.error(`      ${h}`);
}
console.error(`
Almost always this means local overrides reached a production build. Vite reads .env.local in
every mode; put developer-only values in .env.development.local, which it reads only under
\`npm run dev\`. Check, in this order:

  1. apps/frontend/.env.local        — must hold no VITE_ base URLs
  2. VITE_* set in the shell         — these beat every .env file
  3. a URL hardcoded in src/

What should ship is what apps/frontend/.env holds: same-origin gateway paths (/backend/...),
which resolve wherever the app is served from.
`);
process.exit(1);
