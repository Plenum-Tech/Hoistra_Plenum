// publicText — the UI says what happened, never what it runs on.
//
// Text that reaches the screen from somewhere this codebase did not write it - a backend's
// error body, an orchestrator answer - arrives carrying the platform's own internals: service
// names (svc-operations-intelligence), the database schema (plenum_cafm.sites), gateway paths
// (/backend/doc-rag), raw routes (GET /api/...), source files (engines/auth/access.py) and the
// agents' skill documents (skills/compliance/SKILL.md). None of it helps a customer act, and the
// skill documents are the product's own instructions. Such text passes through scrubInternal()
// before it is shown; copy written here is written without them in the first place
// (test/noInternalNames.test.mjs holds that line).

// Order matters: the longer, more specific shapes are replaced before the bare names inside
// them, so "plenum_cafm.sites" reads "sites" rather than "the database.sites".
//
// Every rule names the platform's own shapes and nothing wider (8 Oct 2026 review): a
// case-blind "svc-anything" turned the part number GEN-SVC-001 into "GEN-the platform" and an
// address svc-alerts@acme.co.uk into "the platform@acme.co.uk". Path rules start only at a path
// boundary with bounded segments - unanchored, a 40,000-character run took 0.8-2.8 s to scan,
// on every render of a streaming answer.
//
// No lookbehind (8 Oct 2026): Safari before 16.4 cannot parse it, and api/client.js imports this
// file, so one such pattern left the whole app blank there. A rule that must not start inside a
// longer word captures the character before it as $1 and puts it back.
const SERVICES = 'ai-schema-mapper(?:-ui)?|deepagents|ingestion|operations-intelligence|query|udr|work-order-management|doc-rag';
const GUIDANCE = "the assistant's guidance";
const RULES = [
  // The agents' own instruction documents.
  [/(^|[^\w/.-])(?:\/?skills\/)?(?:[\w-]{1,80}\/){0,8}SKILL\.md\b/gi, '$1' + GUIDANCE],
  [/(^|[^\w/.-])\/?skills\/[\w./-]{1,300}?\.md\b/gi, '$1' + GUIDANCE],
  [/(^|[\s(`'"])[\w-]+\.md\b/g, '$1' + GUIDANCE],
  // Source files.
  [/(^|[^\w/.-])(?:[\w-]{1,80}\/){0,8}[\w-]{1,80}\.py\b/g, '$1the platform'],
  // Gateway paths and raw routes.
  [/\b(?:GET|POST|PUT|PATCH|DELETE) \/[^\s,;:)]*/g, 'the request'],
  [/(^|[\s(])\/backend\/[^\s,;:)]*/g, '$1the platform'],
  [/(^|[\s(])\/api\/[^\s,;:)]*/g, '$1the request'],
  // Service names: the platform's own (any case, with a -worker/-ui suffix), never part of a
  // longer reference or an address. Named whole first: the bare rules below once left
  // "svc-ai-the data importer-worker" and "SVC-building data" (8 Oct 2026).
  [new RegExp('(^|[^\\w@.-])svc-(?:' + SERVICES + ')(?:-(?:worker|ui|app|api))?(?![\\w@-])', 'gi'), '$1the platform'],
  [/(^|[^\w@.-])ai-schema-mapper(?:-(?:ui|worker))?(?![\w@-])/gi, '$1the data importer'],
  [/\b(?:ops|operations)-intelligence\b/gi, 'the platform'],
  [/\bdoc[-_]rag\b/gi, 'document search'],
  [/\bdeep[-_]?agents\b/gi, 'the assistant'],
  [/\bschema[-_ ]mapper\b/gi, 'the data importer'],
  // The database schema: a table keeps its plain name, the schema alone is "the database".
  [/\bplenum_cafm\.([a-z_]+)/gi, (_, t) => t.replace(/_/g, ' '), 'table'],
  [/\bplenum_cafm\b/gi, 'the database'],
  [/\bUDR\b/g, 'building data']
];

// A link is passed through whole: it is how the reader gets to the thing. Except a link to one
// of the platform's own hosts - a container name, localhost, a private address - which no reader
// can open and which names the service (http://svc-udr:8006/..., 8 Oct 2026).
const URL_RE = /\bhttps?:\/\/[^\s<>"'`)\]]+/gi;
const INTERNAL_HOST = /^(?:[\w-]+|svc-[\w.-]+|127(?:\.\d+){3}|10(?:\.\d+){3}|192\.168(?:\.\d+){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2}|0\.0\.0\.0)$/i;
const internalLink = (url) => INTERNAL_HOST.test(url.replace(/^https?:\/\//i, '').split(/[/:?#]/)[0]);

function scrubPlain(s, code) {
  let out = s;
  for (const [re, to, kind] of RULES) {
    // In code the schema goes and the identifier stays as written, so SQL stays SQL.
    out = out.replace(re, code && kind === 'table' ? (_, t) => t : to);
  }
  return out;
}

// `opts.code`: the text is code (a code block or span) - identifiers are kept usable.
export function scrubInternal(text, opts) {
  if (text === null || text === undefined) return text;
  const s = String(text);
  const code = !!(opts && opts.code);
  let out = '';
  let last = 0;
  for (const m of s.matchAll(URL_RE)) {
    out += scrubPlain(s.slice(last, m.index), code) + (internalLink(m[0]) ? 'the platform' : m[0]);
    last = m.index + m[0].length;
  }
  out += scrubPlain(s.slice(last), code);
  // A replacement can leave "the platform (the platform)" where the original named a service
  // and its path; say it once.
  return out.replace(/\b(the platform|the request) \(\1\)/g, '$1').replace(/\b(the platform) \1\b/g, '$1');
}
