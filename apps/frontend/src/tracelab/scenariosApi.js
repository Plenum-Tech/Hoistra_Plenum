// scenariosApi — generating a synthetic batch for one role space. Three routes, named once, all
// through api.js (so the bearer, the refresh-once retry and the superadmin's View-as company are
// the app's own). Generation calls no model and writes nothing: it is deterministic from
// (space, seed), so the same batch can be produced again to re-run a failure exactly.
import { tl, BASE, PREFIX } from './api.js';
import { accessToken, getActingOrg } from '../api/client.js';

export const scenariosApi = {
  spaces: () => tl.get('/scenarios/spaces', {}, { timeoutMs: 15000 }),
  generate: (body) => tl.post('/scenarios/generate', body || {}, {}, { timeoutMs: 30000 }),
  // Each question is a real chat turn, so this waits minutes rather than seconds.
  run: (body) => tl.post('/scenarios/run', body || {}, {}, { timeoutMs: 600000 }),

  // The CSV comes back as a file, which apiFetch does not do: it parses JSON. So this one
  // request is made directly, with the same bearer and the same acting company the rest use.
  async downloadCsv(body) {
    const org = getActingOrg();
    const url = BASE + PREFIX + '/scenarios/template.csv' + (org ? '?organization_id=' + encodeURIComponent(org) : '');
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (accessToken() || '') },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) throw new Error('The batch could not be downloaded (' + res.status + ').');
    const blob = await res.blob();
    const name = 'scenarios-' + (body.space || 'batch') + '-' + (body.seed || 0) + '.csv';
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(href);
    return name;
  }
};
