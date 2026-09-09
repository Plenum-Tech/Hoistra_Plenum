// buildingsCrud — hoisting a building, and removing one.
//
// Two rules this form exists to honour, both of which come from the API and neither of
// which is obvious from a text box:
//
//   Floor area is SQUARE METRES. The database column is square feet and the service
//   converts on write, so a number typed in square feet is accepted, stored, and quietly
//   wrong in every place area is used — including as the denominator of EUI. The input says
//   m² in the label, in the placeholder and in the hint, because this is the one field where
//   being wrong looks exactly like being right.
//
//   Deleting a building never deletes what it held. A certificate or an invoice is the
//   record of something that actually happened. The confirmation calls DELETE without
//   `confirm` first — which changes nothing and reports what is attached — and shows that
//   count, so the decision is "1 asset and 1 document will be unlinked and kept" rather
//   than "are you sure?".
//
// Errors come back keyed by field, so each one renders against its own input instead of a
// banner that makes the reader hunt for which box was wrong.
import { energyApi } from '../api/energy.js';

// What the form offers. `Mall` is deliberately here: a facilities manager calls it a mall,
// and the service maps it to the Retail enum member and says so in the response.
export const USE_TYPES = [
  'Commercial', 'Retail', 'Mall', 'Residential', 'Mixed',
  'Hospital', 'Hotel', 'Industrial', 'Logistics', 'Education', 'Leisure', 'Other'
];

export const COUNTRIES = [
  { code: 'UK', name: 'United Kingdom', standard: 'CIBSE TM46', standing: 'guidance' },
  { code: 'US', name: 'United States', standard: 'Energy Star · ASHRAE 100', standing: 'enacted' },
  { code: 'AE', name: 'United Arab Emirates', standard: 'Rolling portfolio benchmark', standing: 'no operational standard' },
  { code: 'SG', name: 'Singapore', standard: 'BCA Benchmarking Report', standing: 'submission mandatory' }
];

export const GRANULARITIES = [
  { value: 'none', label: 'No meter', hint: 'No reading arrives for this building yet.' },
  { value: 'building-level', label: 'Building-level', hint: 'Attribution to a plant item is inferred, not measured.' },
  { value: 'sub-metered', label: 'Sub-metered', hint: 'Consumption is measured per circuit or plant item.' }
];

// Who may change the register. Two roles exist in this app — `admin`, and `user`, which is
// the everyday facilities-manager view — and both hoist and remove buildings: adding a
// property you have taken on, and removing one entered by mistake, are the work, not an
// administrative exception to it.
//
// This is an affordance, not a permission. The service does not authorise these routes, so
// hiding the button never stopped anyone who could reach the API; what the gate decides is
// whose screen is uncluttered. When a real role model arrives, this set is the one line to
// change — and the check belongs on the service at the same time.
// Every platform role, because all three may hoist a building — a facilities manager
// is the person who actually does it. Listed rather than left implicit: the backend
// now issues 'superadmin' as well (svc-operations-intelligence/engines/auth/roles.py),
// and a set that omits it would deny the platform owner the one action every lesser
// role is allowed, which reads as a broken screen rather than as a permission.
export const HOIST_ROLES = new Set(['superadmin', 'admin', 'user']);

const BLANK = {
  site_name: '', country_code: 'UK', state: '', city: '', postcode: '',
  use_type: 'Commercial', floors: '', gfa_sqm: '',
  metering_granularity: 'building-level', metering_route: '',
  building_code: '', site_id: ''
};
const BLANK_MIX = [{ use: 'office', pct: 100 }];

const num = (v) => { const n = Number(String(v).replace(/,/g, '').trim()); return Number.isFinite(n) ? n : null; };

export const buildingsCrudMethods = {

  // ── the hoist form ─────────────────────────────────────────────────────────

  bcOpenForm() {
    this.setState({
      bcOpen: true, bcForm: Object.assign({}, BLANK), bcMix: BLANK_MIX.map((m) => Object.assign({}, m)),
      bcErrors: {}, bcWarnings: [], bcSaving: false, bcTopError: ''
    });
  },

  bcCloseForm() { this.setState({ bcOpen: false, bcErrors: {}, bcTopError: '' }); },

  bcSet(field, value) {
    this.setState((p) => ({
      bcForm: Object.assign({}, p.bcForm, { [field]: value }),
      // Clearing the error as they type is the whole point of keying errors by field.
      bcErrors: Object.assign({}, p.bcErrors, { [field]: undefined })
    }));
  },

  bcMixSet(i, key, value) {
    this.setState((p) => {
      const mix = (p.bcMix || []).map((m, j) => (j === i ? Object.assign({}, m, { [key]: value }) : m));
      return { bcMix: mix, bcErrors: Object.assign({}, p.bcErrors, { use_mix: undefined }) };
    });
  },

  bcMixAdd() {
    this.setState((p) => ({ bcMix: (p.bcMix || []).concat([{ use: '', pct: '' }]) }));
  },

  bcMixRemove(i) {
    this.setState((p) => {
      const mix = (p.bcMix || []).filter((_, j) => j !== i);
      return { bcMix: mix.length ? mix : BLANK_MIX.map((m) => Object.assign({}, m)) };
    });
  },

  bcMixTotal() {
    return (this.state.bcMix || []).reduce((t, m) => t + (num(m.pct) || 0), 0);
  },

  async bcSubmit() {
    if (this.state.bcSaving) return;
    const f = this.state.bcForm || {};
    const body = {
      site_name: f.site_name, country_code: f.country_code, state: f.state,
      use_type: f.use_type,
      use_mix: (this.state.bcMix || []).map((m) => ({ use: m.use, pct: num(m.pct) })),
      floors: num(f.floors),
      metering_granularity: f.metering_granularity,
      source: 'hoistra-ui'
    };
    // Square METRES. The service converts; sending feet here is the one mistake that is
    // accepted and then wrong everywhere.
    if (String(f.gfa_sqm).trim()) body.gfa_sqm = num(f.gfa_sqm);
    ['city', 'postcode', 'metering_route', 'building_code', 'site_id'].forEach((k) => {
      if (String(f[k] || '').trim()) body[k] = String(f[k]).trim();
    });

    this.setState({ bcSaving: true, bcErrors: {}, bcTopError: '', bcWarnings: [] });
    try {
      const res = await energyApi.createBuilding(body);
      this.setState({ bcSaving: false, bcOpen: false, bcWarnings: res.warnings || [] });
      this.flash('Hoisted ' + (f.site_name || 'building') + ' as ' + (res.building_code || '—'));
      await this.bldLoad();
      // Warnings are not failures — "stored as Retail", "no regulation pack for this
      // market". They belong after the success, not instead of it.
      if ((res.warnings || []).length) this.flash(res.warnings[0]);
    } catch (e) {
      const body = (e && e.body) || {};
      const errs = body.errors || {};
      this.setState({
        bcSaving: false,
        bcErrors: errs,
        bcTopError: Object.keys(errs).length ? '' : ((e && e.message) || String(e))
      });
      if (e && e.status === 409) {
        this.flash('That building code is already in use — nothing was overwritten.');
      }
    }
  },

  // ── removing one ───────────────────────────────────────────────────────────

  // Step one is a DELETE with no `confirm`. It changes nothing and reports what the
  // building holds, which is what the dialog needs in order to say something useful.
  async bcAskDelete(row) {
    const id = (row && (row.buildingId || row.id)) || '';
    if (!id) return;
    this.setState({ bcDel: { id: id, name: (row && row.name) || id, loading: true }, bcDelError: '' });
    try {
      const plan = await energyApi.deleteBuilding(id, { confirm: false });
      this.setState({ bcDel: { id: id, name: plan.name || (row && row.name) || id, code: plan.building_code, attached: plan.attached || {}, total: plan.attached_total || 0, loading: false } });
    } catch (e) {
      this.setState({ bcDel: null, bcDelError: (e && e.message) || String(e) });
      this.flash('Could not check what that building holds — ' + ((e && e.message) || e));
    }
  },

  bcCancelDelete() { this.setState({ bcDel: null, bcDelError: '' }); },

  async bcConfirmDelete() {
    const d = this.state.bcDel;
    if (!d || d.loading || d.working) return;
    this.setState({ bcDel: Object.assign({}, d, { working: true }) });
    try {
      const res = await energyApi.deleteBuilding(d.id, { confirm: true, detach: true });
      this.setState({ bcDel: null });
      this.flash(res.message || ('Deleted ' + d.name));
      await this.bldLoad();
    } catch (e) {
      this.setState({ bcDel: Object.assign({}, d, { working: false }) });
      this.flash('Delete failed — ' + ((e && e.message) || e));
    }
  },

  // ── view model ─────────────────────────────────────────────────────────────

  bcVals() {
    const s = this.state;
    const f = s.bcForm || BLANK;
    const mix = s.bcMix || [];
    const total = this.bcMixTotal();
    const country = COUNTRIES.find((c) => c.code === f.country_code) || COUNTRIES[0];
    const gran = GRANULARITIES.find((g) => g.value === f.metering_granularity) || GRANULARITIES[1];
    const err = s.bcErrors || {};
    const d = s.bcDel;

    const attachedText = d && d.total
      ? Object.entries(d.attached || {})
          .map(([t, n]) => n + ' ' + (n === 1 ? t.replace(/s$/, '') : t).replace(/_/g, ' '))
          .join(' and ')
      : '';

    return {
      // Both roles, so a facilities manager is not blocked from the register they keep.
      bcCanHoist: !!s.signedIn && HOIST_ROLES.has(s.role),
      bcCanRemove: !!s.signedIn && HOIST_ROLES.has(s.role),
      bcOpen: !!s.bcOpen,
      bcShow: s.bcOpen ? 'flex' : 'none',
      bcForm: f,
      bcErr: (k) => err[k] || '',
      bcErrShow: (k) => (err[k] ? 'block' : 'none'),
      bcTopError: s.bcTopError || '',
      bcTopErrorShow: s.bcTopError ? 'block' : 'none',
      bcSaving: !!s.bcSaving,
      bcSubmitLabel: s.bcSaving ? 'Hoisting…' : 'Hoist building',
      bcSet: (k) => (e) => this.bcSet(k, e && e.target ? e.target.value : e),
      bcClose: () => this.bcCloseForm(),
      bcSubmit: () => this.bcSubmit(),

      bcUseTypes: USE_TYPES,
      bcCountries: COUNTRIES,
      bcGranularities: GRANULARITIES,

      // The standard the building will be read against, shown while they pick the market —
      // it is a consequence of the country and nobody would guess it from a dropdown.
      bcStandardNote: country.standard + ' · ' + country.standing,
      bcGranularityNote: gran.hint,
      // Mall is a real answer that stores as something else. Saying so before they submit
      // is better than a warning after.
      bcUseNote: f.use_type === 'Mall' ? 'Stored as Retail — the register has no Mall category.' : '',
      bcUseNoteShow: f.use_type === 'Mall' ? 'block' : 'none',

      bcMix: mix.map((m, i) => ({
        key: 'mix' + i, use: m.use, pct: m.pct,
        setUse: (e) => this.bcMixSet(i, 'use', e.target.value),
        setPct: (e) => this.bcMixSet(i, 'pct', e.target.value),
        remove: () => this.bcMixRemove(i),
        removeShow: mix.length > 1 ? 'inline' : 'none'
      })),
      bcMixAdd: () => this.bcMixAdd(),
      bcMixTotal: Math.round(total * 100) / 100,
      bcMixTotalOk: Math.abs(total - 100) <= 0.5,
      bcMixTotalColor: Math.abs(total - 100) <= 0.5 ? 'var(--st-ok)' : 'var(--st-warn)',
      bcMixTotalLabel: (Math.round(total * 100) / 100) + '% of 100',

      // ── delete ──
      bcDelShow: d ? 'flex' : 'none',
      bcDelName: d ? d.name : '',
      bcDelCode: d && d.code ? d.code : '',
      bcDelLoading: !!(d && d.loading),
      bcDelWorking: !!(d && d.working),
      bcDelBusyLabel: d && d.working ? 'Deleting…' : 'Delete building',
      // The whole reason for the dry run: name what happens to what it holds.
      bcDelAttachedShow: d && d.total ? 'block' : 'none',
      bcDelAttachedText: attachedText
        ? attachedText + ' will be unlinked and kept — nothing attached to this building is deleted.'
        : '',
      bcDelEmptyShow: d && !d.loading && !d.total ? 'block' : 'none',
      bcDelCancel: () => this.bcCancelDelete(),
      bcDelConfirm: () => this.bcConfirmDelete(),
      bcAskDelete: (row) => this.bcAskDelete(row)
    };
  }
};
