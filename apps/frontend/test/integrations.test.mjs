// Integrations (admin): the page keeps its whole structure — tiles, Last sync, the Connected
// table, three tabs, the connect dialog — but nothing on it is invented. It used to draw six
// "connected" sources (Yardi, SAP, Maximo…), a 61-product catalogue, sync times, row counts, a
// bearer token and API limits from a seed file, none of it read from anywhere. Where there is no
// data it now says 0 or "—"; the source types are the connector service's own plugins.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async () => ({ ok: false, status: 404, statusText: '404', text: async () => '{}' });

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { SOURCE_TYPES } = await import('../src/logic/integrations.js');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGINS = path.resolve(HERE, '../../backend/cafm-connector-service-final/cafm-connector-service/src/cafm_connector/connectors/plugins');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', view: 'integ', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
});
const vals = () => c.intVals(c.state);
const names = (v) => (v.intCats || []).flatMap((g) => g.items.map((i) => i.name));

test('nothing the seed file invented is on the page', () => {
  const page = JSON.stringify(vals(), (k, v) => (typeof v === 'function' ? undefined : v));
  for (const fake of ['Yardi', 'S/4HANA', 'Maximo', 'Planon', 'Reuters', 'R. Achebe', '02:31', 'hst_live',
    'api.hoistra.com', 'portfolio data lake', 'Auth expired', '15-minute poll', 'service_charge_line', '15 min', '10k', '99.9%']) {
    assert.ok(!page.includes(fake), 'still shows ' + fake);
  }
});

test('the source types are exactly the connectors the connector service implements', () => {
  const plugins = fs.readdirSync(PLUGINS, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith('_')).map((d) => d.name.replace(/_source$/, '')).sort();
  assert.deepEqual(SOURCE_TYPES.map((t) => t.id).sort(), plugins);
  c.setState({ intTab: 1 });
  assert.equal(names(vals()).length, plugins.length);
});

test('the four tiles stay, at zero where nothing is connected', () => {
  const tiles = vals().intTiles;
  assert.deepEqual(tiles.map((t) => t.label), ['Connected', 'Tables fed', 'Need attention', 'Available']);
  assert.deepEqual(tiles.map((t) => t.value), [0, 0, 0, SOURCE_TYPES.length]);
});

test('all three tabs stay, counted from what is there', () => {
  assert.deepEqual(vals().intTabs.map((t) => [t.label, t.n]),
    [['Connected', 0], ['Available sources', SOURCE_TYPES.length], ['Custom API', 0]]);
});

test('the Connected table keeps its header and says it has no rows', () => {
  const v = vals();
  assert.equal(v.intRows.length, 0);
  assert.equal(v.intConnSummary, '0 of 0 shown · 0 tables fed');
  assert.match(v.intConnEmpty, /no sources? (are )?connected/i);
  assert.equal(v.intLastSync, '—');
});

test('the navigator counts connections instead of claiming a sync cadence', () => {
  assert.equal(vals().navAdmin[0].label, 'Integrations');
  assert.equal(vals().navAdmin[0].badge, '0');
});

test('the Custom API tab keeps its panels and fills them with nothing invented', () => {
  const v = vals();
  assert.equal(v.intBaseUrl, '—');
  assert.equal(v.intKey, '—');
  assert.deepEqual(v.intLimits.map((l) => l.value), ['—', '—', '—']);
  assert.equal(v.intEndpoints.length, 0);
  assert.equal(v.intMapping.length, 0);
});

test('search and the group chips narrow the source types', () => {
  c.setState({ intTab: 1, intQ: 'sql' });
  assert.deepEqual(names(vals()).sort(), ['MySQL', 'PostgreSQL', 'SQL Server']);
  c.setState({ intQ: '', intCat: 'files' });
  assert.deepEqual(names(vals()).sort(), ['CSV', 'Excel', 'JSON', 'Parquet', 'XML']);
  c.setState({ intCat: null, intQ: 'nothing like this' });
  assert.equal(vals().intNoneShow, 'block');
});

test('Connect opens the dialog, and confirming adds no pretend connection', () => {
  c.setState({ intTab: 1 });
  const pg = vals().intCats.flatMap((g) => g.items).find((i) => i.name === 'PostgreSQL');
  assert.equal(pg.btn, 'Connect');
  const asked = [];
  c.orch = (task) => { asked.push(task); };
  pg.click();
  assert.equal(vals().intModalOn, true);
  assert.equal(vals().intModal.name, 'PostgreSQL');
  vals().intConfirm();
  assert.equal(vals().intModalOn, false);
  assert.equal(vals().intRows.length, 0, 'a connection appears only when one exists');
  assert.equal(vals().intTiles[0].value, 0);
  assert.equal(asked.length, 1, 'the request goes to the orchestrator');
});
