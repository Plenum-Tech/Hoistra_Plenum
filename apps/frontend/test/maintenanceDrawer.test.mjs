// The decision drawer is the shared DetailDrawer, which reads icon / module / meta / title /
// body / chain[].a / chain[].t. A drawer built in any other shape opens with a broken glyph
// and an empty chain — the row looks clickable, and clicking it tells you nothing.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'module', module: 'ops', filter: 'All',
    // No seed decisions remain, so the grid only has rows when the backend answered.
    account: { id: 'u1', role: 'user', building_ids: ['b1'] },
    mxRaw: {
      decisions: { ok: true, count: 1, by_state: { 'Awaiting approval': 1 }, decisions: [
        { work_order: 'WO-1', state: 'Awaiting approval', source: 'Maintenance',
          trigger: 'Approval outstanding', detail: 'Leak in the roof plant.', asset: 'Chiller-1',
          building: 'Town Hall', building_id: 'b1', vendor: 'Acme', estimated_cost: 1200,
          priority: 'urgent', due: null }
      ] }
    }
  });
});

test('opening a decision fills the fields DetailDrawer actually reads', () => {
  const vals = c.renderVals();
  const row = (vals.mxDecisions || [])[0];
  assert.ok(row && typeof row.open === 'function', 'there is a decision row to open');
  row.open();
  const d = c.state.detail;
  assert.ok(d, 'a drawer opened');
  assert.ok(d.icon, 'icon — without it the glyph renders broken');
  assert.ok(d.module, 'module — the drawer kicker');
  assert.ok(d.title, 'title');
  assert.equal(typeof d.meta, 'string', 'meta is the sub-line the drawer reads, not `sub`');
  assert.ok(Array.isArray(d.chain) && d.chain.length, 'a chain to show');
  d.chain.forEach((c2) => {
    assert.equal(typeof c2.a, 'string', 'chain rows are {a, t} — {k, v} renders blank');
    assert.equal(typeof c2.t, 'string');
  });
});
