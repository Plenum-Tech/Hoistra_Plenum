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
//     and there is something to delete. The server checks the name again.
import { adminApi } from '../api/admin.js';

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
  drBusy: false, drDone: null, drOpenArea: '', drDisabled: ''
};

const NUM = (n) => (typeof n === 'number' ? n.toLocaleString('en-GB') : '—');
const pickedKeys = (picked) => DR_AREAS.map((a) => a.key).filter((k) => picked && picked[k]);

export const dataResetMethods = {

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

  async drPreview(picked) {
    const keys = pickedKeys(picked || this.state.drPicked);
    const seq = (this._drSeq = (this._drSeq || 0) + 1);
    if (!keys.length) {
      this.setState({ drPlan: null, drLoading: false, drError: '', drBlocked: [] });
      return;
    }
    this.setState({ drLoading: true, drError: '' });
    try {
      const plan = await adminApi.dataResetPreview(keys);
      if (seq !== this._drSeq) return; // a newer tick has asked since
      this.setState({ drPlan: plan, drLoading: false, drBlocked: plan.blocked || [], drDisabled: '' });
    } catch (e) {
      if (seq !== this._drSeq) return;
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
    if (s.drBusy || !plan || !keys.length) return;
    if (String(s.drConfirm).trim() !== plan.confirm_with) return;
    this.setState({ drBusy: true, drError: '' });
    try {
      const out = await adminApi.dataReset(keys, String(s.drConfirm).trim());
      this.setState({ drBusy: false, drDone: out, drConfirm: '' });
      this.flash('Cleared ' + NUM(out.row_total) + ' rows from ' + keys.map((k) => LABEL[k]).join(', '));
      // Read the counts again: they should now be zero, and seeing that is the check.
      this.drPreview();
    } catch (e) {
      const d = e && e.body && e.body.detail;
      this.setState({
        drBusy: false,
        drBlocked: (d && d.blocked) || s.drBlocked,
        drError: (e && e.message) || String(e)
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
    const name = plan ? plan.confirm_with || plan.organization_name || '' : '';
    const typed = String(s.drConfirm || '').trim();
    const nameOk = !!name && typed === name;
    const canApply = !!plan && keys.length > 0 && !blocked.length && total > 0 && nameOk && !s.drBusy && !s.drLoading;
    const areaRows = plan ? (plan.areas || []) : [];

    return {
      drShow: !!s.signedIn && isAdmin,
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
        ? NUM(total) + ' rows would be deleted for ' + (plan.organization_name || 'this company')
          + ' across ' + keys.map((k) => LABEL[k]).join(', ') + '.'
        : '',
      drBuildings: plan ? plan.buildings + ' building' + (plan.buildings === 1 ? '' : 's') + ' in scope' : '',
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
      drKept: 'Kept: users, the company, buildings, sites, floors, uploaded documents, and approval items raised by anything else.',
      drConfirmName: name,
      drConfirm: s.drConfirm || '',
      drSetConfirm: (e) => this.setState({ drConfirm: e && e.target ? e.target.value : String(e || '') }),
      drNameOk: nameOk,
      drCanApply: canApply,
      drApplyLabel: s.drBusy ? 'Deleting…' : 'Delete ' + NUM(total) + ' rows',
      drApply: () => this.drApply(),
      drDone: s.drDone
        ? 'Deleted ' + NUM(s.drDone.row_total) + ' rows. The counts above are read again from the database.'
        : ''
    };
  }
};
