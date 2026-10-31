// dataReset — clearing this company's Compliance, Contracts, Assets, Energy and Maintenance
// page data, from Users & access (admin only).
//
// The service decides what goes (svc-operations-intelligence /api/admin/data-reset): only this
// company's rows, only the chosen pages, in one transaction. This panel's job is to make the
// deletion impossible to do by accident and easy to check first:
//
//   - nothing is chosen until the admin ticks a page; ticking one fetches the preview, so the
//     row counts on screen are always the ones for what is ticked;
//   - kept pages that depend on a cleared one are shown before anything happens — the links
//     that will be emptied, and anything that BLOCKS the reset, with the page to add;
//   - the delete button stays off until the company name is typed exactly, nothing blocks,
//     and there is something to delete. The server checks the name again;
//   - the scope is every building or one of them. One building is confirmed with ITS name
//     (never the company's), what serves every building is listed as kept from the count
//     itself, and a preview read for another scope or other pages never arms the button.
//     A company switch or a sign-out puts the panel back to nothing chosen.
import { adminApi } from '../api/admin.js';
import { isStaleScope } from '../api/client.js';

export const DR_AREAS = [
  { key: 'compliance', label: 'Compliance', icon: 'ph-shield-check', what: 'Certificates, risk snapshots, compliance scans' },
  { key: 'contracts', label: 'Contracts', icon: 'ph-handshake', what: 'Vendors, contracts, SLA terms, invoices, scorecards' },
  { key: 'assets', label: 'Assets', icon: 'ph-cube', what: 'Assets, asset readings and bands, building sections' },
  { key: 'energy', label: 'Energy', icon: 'ph-lightning', what: 'Meters, meter readings, anomalies, EUI snapshots' },
  { key: 'maintenance', label: 'Maintenance', icon: 'ph-wrench', what: 'Work orders, PPM visits, plans, inspections, parts, technicians' }
];
const LABEL = Object.fromEntries(DR_AREAS.map((a) => [a.key, a.label]));

export const DR_DEFAULTS = {
  drPicked: {}, drPlan: null, drLoading: false, drError: '', drBlocked: [], drConfirm: '',
  drBusy: false, drDone: null, drOpenArea: '', drDisabled: '', drBuilding: ''
};

const NUM = (n) => (typeof n === 'number' ? n.toLocaleString('en-GB') : '—');
const pickedKeys = (picked) => DR_AREAS.map((a) => a.key).filter((k) => picked && picked[k]);
// The building a plan was counted for: '' for every building.
const planScope = (plan) => (plan && plan.building && plan.building.id ? String(plan.building.id) : '');
// A building as the panel names it: its name and code, or whichever of them it has.
const bLabel = (b) => (!b ? '' : b.name && b.building_code ? b.name + ' (' + b.building_code + ')'
  : String(b.name || b.building_code || 'this building'));
// The plan was counted for exactly these pages.
const sameAreas = (plan, keys) => !!plan
  && (plan.areas || []).map((a) => a.area).sort().join(',') === keys.slice().sort().join(',');

export const dataResetMethods = {

  // Back to nothing chosen, disowning any preview still on the wire. On a company switch
  // (resetLiveData) and on sign-out: a building picked for one company must never sit,
  // armed, under the next one or the next person's session.
  drReset() {
    this.setState(this.drResetPatch());
  },

  // The same, as a patch for a caller that must change state in ONE write: sign-out, where an
  // extra write while still signed in would store this tab's session over the shared slot.
  drResetPatch() {
    this._drSeq = (this._drSeq || 0) + 1;
    return Object.assign({}, DR_DEFAULTS, { drPicked: {} });
  },

  drToggle(key) {
    if (this.state.drBusy) return;
    const picked = Object.assign({}, this.state.drPicked);
    if (picked[key]) delete picked[key]; else picked[key] = true;
    this.setState({ drPicked: picked, drConfirm: '', drDone: null });
    this.drPreview(picked);
  },

  drAddArea(key) {
    if (!this.state.drPicked[key]) this.drToggle(key);
  },

  // '' is every building. A new scope is a new reset: the typed name goes, the counts are read again.
  drSetBuilding(id) {
    if (this.state.drBusy) return;
    const building = String(id || '');
    if (building === this.state.drBuilding) return;
    this.setState({ drBuilding: building, drConfirm: '', drDone: null, drOpenArea: '' });
    this.drPreview(this.state.drPicked, building);
  },

  async drPreview(picked, building) {
    const keys = pickedKeys(picked || this.state.drPicked);
    const scope = building === undefined ? this.state.drBuilding : building;
    const seq = (this._drSeq = (this._drSeq || 0) + 1);
    if (!keys.length) {
      this.setState({ drPlan: null, drLoading: false, drError: '', drBlocked: [] });
      return;
    }
    this.setState({ drLoading: true, drError: '' });
    try {
      const plan = await adminApi.dataResetPreview(keys, scope || undefined);
      if (seq !== this._drSeq) return; // a newer tick has asked since
      this.setState({ drPlan: plan, drLoading: false, drBlocked: plan.blocked || [], drDisabled: '' });
    } catch (e) {
      if (seq !== this._drSeq) return;
      // Issued for the company before a switch: the switch has its own reads to make.
      if (isStaleScope(e)) { this.setState({ drLoading: false }); return; }
      const reason = e && e.reason;
      this.setState({
        drLoading: false, drPlan: null, drBlocked: [],
        drDisabled: reason === 'reset_disabled' ? ((e && e.message) || 'Data reset is switched off here.') : '',
        drError: reason === 'reset_disabled' ? '' : ((e && e.message) || String(e))
      });
    }
  },

  async drApply() {
    const s = this.state;
    const keys = pickedKeys(s.drPicked);
    const plan = s.drPlan;
    if (s.drBusy || s.drLoading || !plan || !keys.length) return;
    // Counted for another scope or other pages: the counts on screen are not this delete's.
    if (planScope(plan) !== s.drBuilding || !sameAreas(plan, keys)) return;
    if (!plan.confirm_with || String(s.drConfirm).trim() !== plan.confirm_with) return;
    this.setState({ drBusy: true, drError: '' });
    try {
      const out = await adminApi.dataReset(keys, String(s.drConfirm).trim(), s.drBuilding || undefined);
      this.setState({ drBusy: false, drDone: out, drConfirm: '' });
      this.flash('Cleared ' + NUM(out.row_total) + ' rows from ' + keys.map((k) => LABEL[k]).join(', ')
        + (plan.building ? ' on ' + bLabel(plan.building) : ''));
      // Read the counts again: they should now be zero, and seeing that is the check.
      this.drPreview();
    } catch (e) {
      // The company changed under the request. The delete was sent; its answer belongs to
      // the previous company's panel, which the switch has already cleared.
      if (isStaleScope(e)) {
        this.setState({ drBusy: false });
        this.flash('The reset started before the company switch was sent; its result was not read here.');
        return;
      }
      const d = e && e.body && e.body.detail;
      const msg = (e && e.message) || String(e);
      if (e && (e.reason === 'confirm_mismatch' || e.reason === 'building_not_found')) {
        // The plan that armed the button is wrong about the name or the building: read again,
        // and the name is typed again before another try.
        this.setState({ drBusy: false, drPlan: null, drConfirm: '' });
        await this.drPreview();
        this.setState({ drError: msg });
        return;
      }
      this.setState({
        drBusy: false,
        drBlocked: (d && d.blocked) || s.drBlocked,
        drError: msg
      });
    }
  },

  drVals() {
    const s = this.state;
    const a = s.account || {};
    const isAdmin = a.role === 'admin' || a.role === 'superadmin';
    const plan = s.drPlan;
    const keys = pickedKeys(s.drPicked);
    const blocked = s.drBlocked || [];
    const total = plan ? plan.row_total || 0 : 0;
    // One building is confirmed by what the service names it, never by the company's name.
    const name = !plan ? '' : planScope(plan) ? plan.confirm_with || ''
      : plan.confirm_with || plan.organization_name || '';
    const typed = String(s.drConfirm || '').trim();
    const nameOk = !!name && typed === name;
    const scope = s.drBuilding || '';
    const blds = Array.isArray(s.axBldsLive) ? s.axBldsLive : [];
    // The chosen building is no longer in this company's list (re-read, or another company).
    const missing = !!scope && Array.isArray(s.axBldsLive) && !blds.some((b) => String(b.id) === scope);
    const inScope = !!plan && planScope(plan) === scope && sameAreas(plan, keys);
    const canApply = inScope && !missing && keys.length > 0 && !blocked.length && total > 0 && nameOk
      && !s.drBusy && !s.drLoading;
    const areaRows = plan ? (plan.areas || []) : [];
    const bName = plan && plan.building ? bLabel(plan.building) : '';

    return {
      drShow: !!s.signedIn && isAdmin,
      drScopeShow: blds.length > 0,
      drScope: scope,
      drScopeOptions: [{ id: '', label: 'All buildings' }].concat(blds.map((b) => ({
        id: String(b.id),
        label: b.name ? b.name + (b.building_code ? ' · ' + b.building_code : '') : String(b.building_code || b.id)
      })), missing ? [{ id: scope, label: 'Not in this company\'s list — choose again' }] : []),
      drSetScope: (e) => this.drSetBuilding(e && e.target ? e.target.value : e),
      // What is kept is read from the count (drCompanyWide), never promised ahead of it.
      drScopeNote: missing ? 'That building is not in this company\'s list — choose again.'
        : scope ? 'Only this building\'s rows. Records not tied to one building are kept, and listed once counted.'
          : 'Every building this company holds.',
      drScopeStale: plan && !inScope && !s.drLoading && keys.length
        ? 'These counts are for a different choice — tick a page or choose the scope again to read them.'
        : '',
      drBusy: !!s.drBusy,
      drAreas: DR_AREAS.map((ar) => {
        const on = !!(s.drPicked || {})[ar.key];
        const row = areaRows.find((r) => r.area === ar.key);
        return {
          key: ar.key, label: ar.label, icon: ar.icon, what: ar.what, on: on,
          count: on && row ? NUM(row.rows) + ' rows' : '',
          edge: on ? 'var(--st-risk)' : 'var(--color-divider)',
          bg: on ? 'var(--st-risk-bg)' : 'var(--color-bg)',
          fg: on ? 'var(--color-text)' : 'var(--color-neutral-400)',
          tick: on ? 'ph-check-square' : 'ph-square',
          pick: () => this.drToggle(ar.key)
        };
      }),
      drNothingPicked: keys.length === 0,
      drLoading: !!s.drLoading,
      drDisabled: s.drDisabled || '',
      drError: s.drError || '',
      drHasPlan: !!plan && keys.length > 0,
      drSummary: plan
        ? NUM(total) + ' rows would be deleted ' + (bName ? 'from ' + bName : 'for ' + (plan.organization_name || 'this company'))
          + ' across ' + keys.map((k) => LABEL[k]).join(', ') + '.'
        : '',
      drBuildings: !plan ? '' : bName ? bName + ' only'
        : plan.buildings + ' building' + (plan.buildings === 1 ? '' : 's') + ' in scope',
      drCompanyWide: (plan && plan.company_wide || []).map((t) => String(t.table).replace(/_/g, ' ')).join(', '),
      drAreaRows: areaRows.map((r) => ({
        label: r.label, rows: NUM(r.rows), open: s.drOpenArea === r.area,
        toggle: () => this.setState({ drOpenArea: s.drOpenArea === r.area ? '' : r.area }),
        tables: (r.tables || []).map((t) => ({ table: t.table, rows: NUM(t.rows) }))
      })),
      drLinks: (plan && plan.links_cleared || []).map((l) => ({
        what: l.table + '.' + l.column, rows: NUM(l.rows), why: l.because
      })),
      drBlocked: blocked.map((b) => ({
        what: NUM(b.rows) + ' ' + b.table.replace(/_/g, ' '),
        why: b.because,
        needs: b.needs_area ? LABEL[b.needs_area] || b.needs_area : '',
        add: b.needs_area ? () => this.drAddArea(b.needs_area) : null
      })),
      drKept: bName
        ? 'Kept: every other building\'s data, users, the company, buildings, sites, floors, uploaded documents, and approval items about anything kept.'
        : 'Kept: users, the company, buildings, sites, floors, uploaded documents, and approval items raised by anything else.',
      drConfirmName: name,
      drConfirm: s.drConfirm || '',
      drSetConfirm: (e) => this.setState({ drConfirm: e && e.target ? e.target.value : String(e || '') }),
      drNameOk: nameOk,
      drCanApply: canApply,
      drApplyLabel: s.drBusy ? 'Deleting…' : 'Delete ' + NUM(total) + ' rows' + (bName ? ' from ' + bName : ''),
      drApply: () => this.drApply(),
      drDone: s.drDone
        ? 'Deleted ' + NUM(s.drDone.row_total) + ' rows'
          + (s.drDone.building ? ' from ' + bLabel(s.drDone.building) : '')
          + '. The counts above are read again from the database.'
        : ''
    };
  }
};
