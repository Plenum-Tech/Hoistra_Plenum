// navSections — the sidebar's Reports list (Buildings, Compliance, Vendors, Energy,
// Assets, Maintenance). Admin view shows none of them: only the admin console
// (Integrations, Users & access, Audit trail — navAdmin, tested elsewhere) belongs there.
// They stay available in User view, same as always.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

test('Admin view shows no report sections at all — not even Buildings', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin' });
  assert.deepEqual(c.renderVals().navSections, []);
});

test('User view keeps the full report list, Buildings included', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'user' });
  const labels = c.renderVals().navSections.map((n) => n.label);
  assert.deepEqual(labels, ['Buildings', 'Compliance', 'Vendors', 'Energy', 'Assets', 'Maintenance']);
});
