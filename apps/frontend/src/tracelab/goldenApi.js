// goldenApi — every golden-set and golden-run request Trace Lab's page makes, in one place, so the service's
// golden routes are named once. Every call but one goes through api.js (tl.get / tl.post, so through the app's
// apiFetch: the bearer, the refresh-once retry, the stale-company check, and organization_id only for a
// superadmin's View as). The one exception is pauseOnClose(): a page that is closing cannot wait for apiFetch,
// so it sends one keepalive request with the access token (api/client.js accessToken, reused by import) and the
// company the run was started under.
import { tl, BASE, PREFIX } from './api.js';
import { accessToken } from '../api/client.js';

const enc = encodeURIComponent;
const run = (id, tail) => '/golden-runs/' + enc(id) + (tail || '');

export const TEMPLATE_PATHS = Object.freeze({
  plain: '/golden-sets/template.csv',
  compliance: '/golden-sets/templates/compliance.csv'
});

function uploadForm(file, o) {
  const form = new FormData();
  form.append('file', file, (file && file.name) || 'golden-set.xlsx');
  if (o.sheet) form.append('sheet', String(o.sheet));
  if (o.name) form.append('name', String(o.name));
  if (o.note) form.append('note', String(o.note));
  return form;
}

export const goldenApi = {
  sets: () => tl.get('/golden-sets', {}, { timeoutMs: 15000 }),
  upload: (file, opts) => {
    const o = opts || {};
    return tl.post('/golden-sets', undefined, o.dryRun ? { dry_run: 'true' } : {}, { form: uploadForm(file, o), timeoutMs: 60000 });
  },
  cases: (setId, query) => tl.get('/golden-sets/' + enc(setId) + '/cases', query || {}, { timeoutMs: 15000 }),
  activate: (setId) => tl.post('/golden-sets/' + enc(setId) + '/activate', {}, {}, { timeoutMs: 15000 }),
  retire: (setId) => tl.post('/golden-sets/' + enc(setId) + '/retire', {}, {}, { timeoutMs: 15000 }),
  template: (kind) => tl.get(TEMPLATE_PATHS[kind] || TEMPLATE_PATHS.plain, {}, { raw: true, timeoutMs: 15000 }),
  estimate: (body) => tl.post('/golden-runs/estimate', body || {}, {}, { timeoutMs: 30000 }),
  start: (body) => tl.post('/golden-runs', body || {}, {}, { timeoutMs: 45000 }),
  runs: (query) => tl.get('/golden-runs', query || {}, { timeoutMs: 20000 }),
  run: (id) => tl.get(run(id), {}, { timeoutMs: 20000 }),
  step: (id) => tl.post(run(id, '/step'), {}, {}, { timeoutMs: 20000 }),
  pause: (id) => tl.post(run(id, '/pause'), {}, {}, { timeoutMs: 15000 }),
  resume: (id, body) => tl.post(run(id, '/resume'), body || {}, {}, { timeoutMs: 15000 }),
  cancel: (id) => tl.post(run(id, '/cancel'), {}, {}, { timeoutMs: 15000 }),
  regrade: (id, body) => tl.post(run(id, '/regrade'), body || { only: 'pending' }, {}, { timeoutMs: 20000 }),
  compare: (base, head) => tl.get('/golden-runs/compare', { base, head }, { timeoutMs: 30000 }),
  expected: (turnId) => tl.get('/golden/turns/' + enc(turnId), {}, { timeoutMs: 15000 }),
  reveal: (turnId) => tl.post('/golden/turns/' + enc(turnId) + '/reveal', {}, {}, { timeoutMs: 15000 })
};

// The page is closing (pagehide) or leaving Trace Lab while it steps a run: ask the service to pause that run, in a
// request the browser finishes after the page is gone. `org` is the company the run was started under (a
// superadmin's View as), captured when the loop started, so a company switch cannot send it to the wrong company.
// Never throws; returns whether a request was sent.
export function pauseOnClose(runId, org) {
  try {
    if (!runId || typeof fetch !== 'function' || typeof window === 'undefined') return false;
    const url = new URL(BASE + PREFIX + run(runId, '/pause'), window.location.origin);
    if (org) url.searchParams.set('organization_id', String(org));
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
    const token = accessToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    const sent = fetch(url.toString(), { method: 'POST', keepalive: true, headers, body: JSON.stringify({ reason: 'page_closed' }) });
    if (sent && typeof sent.catch === 'function') sent.catch(() => {});
    return true;
  } catch (e) {
    return false;
  }
}
