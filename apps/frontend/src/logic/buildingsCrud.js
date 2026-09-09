// buildingsCrud — hoisting a building, editing one field by field, and removing one.
//
// One form serves create (POST) and edit (PATCH): opening it for a row prefills every field
// from the table and switches the submit to send only what changed. Three rules govern both,
// none obvious from a text box:
//
//   Floor area is SQUARE METRES. The database column is square feet and the service
//   converts on write, so a number typed in square feet is accepted, stored, and quietly
//   wrong in every place area is used — including as the denominator of EUI. The input says
//   m² in the label, in the placeholder and in the hint, because this is the one field where
//   being wrong looks exactly like being right.
//
//   On edit, an omitted field is left alone; an emptied optional field is a deliberate clear.
//   So the diff against the prefilled original decides what to send, and a field blanked back
//   to nothing is sent as "" rather than dropped. `expected_updated_at` (read off the row) goes
//   with it, so an edit started against a stale row is refused instead of overwriting someone.
//
//   Deleting a building never deletes what it held. A certificate or an invoice is the
//   record of something that actually happened. The confirmation calls DELETE without
//   `confirm` first — which changes nothing and reports what is attached — and shows that
//   count, so the decision is "1 asset and 1 document will be unlinked and kept" rather
//   than "are you sure?".
//
// Errors come back keyed by field on both routes, so each one renders against its own input
// instead of a banner the reader has to match back to a box by guessing.
import { energyApi } from '../api/energy.js';

// What the form offers. `Mall` is deliberately here: a facilities manager calls it a mall,
// and the service maps it to the Retail enum member and says so in the response. `Laboratory`
// is a real member of the database enum the form had been missing.
export const USE_TYPES = [
  'Commercial', 'Retail', 'Mall', 'Residential', 'Mixed',
  'Hospital', 'Hotel', 'Industrial', 'Logistics', 'Education', 'Laboratory', 'Leisure', 'Other'
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
// The three steps of a hoist, as the card labels them.
const STEP_NAMES = ['building record', 'schema written', 'documents'];
// Fields PATCH will actually touch — the same set PatchBuildingRequest declares. Anything
// else is never sent, so a stray key from the form can't come back "Not editable here."
const PATCHABLE_KEYS = [
  'site_name', 'country_code', 'state', 'city', 'postcode',
  'use_type', 'use_mix', 'floors', 'gfa_sqm',
  'metering_granularity', 'metering_route', 'building_code', 'site_id'
];

const num = (v) => { const n = Number(String(v).replace(/,/g, '').trim()); return Number.isFinite(n) ? n : null; };
const trim = (v) => String(v === undefined || v === null ? '' : v).trim();

// A row from buildingsLive.js → the form's fields, so opening Edit shows the building as the
// table already has it rather than a blank sheet.
function formFromRow(row) {
  const r = row || {};
  return {
    site_name: r.name && r.name !== 'Unnamed site' ? r.name : '',
    country_code: r.cc && r.cc !== '—' ? r.cc : 'UK',
    state: r.state && r.state !== '—' ? r.state : '',
    city: r.city || '',
    postcode: r.postcode || '',
    use_type: r.siteTypeRaw || r.use || 'Commercial',
    floors: typeof r.floors === 'number' ? String(r.floors) : '',
    gfa_sqm: typeof r.areaM2 === 'number' ? String(Math.round(r.areaM2)) : '',
    metering_granularity: r.gran || 'building-level',
    metering_route: r.route && !/^No meter|route not stated/.test(r.route) ? r.route : '',
    building_code: r.code || '',
    site_id: r.siteId || ''
  };
}
function mixFromRow(row) {
  const raw = row && row.useMixRaw;
  if (Array.isArray(raw) && raw.length) return raw.map((m) => ({ use: m.use, pct: m.pct }));
  return BLANK_MIX.map((m) => Object.assign({}, m));
}

export const buildingsCrudMethods = {

  // ── the hoist form ─────────────────────────────────────────────────────────
  //
  // The form is a card in the orchestrator dock, not a modal: hoisting is an instruction to
  // the platform, so it opens the dock, records the task as a session and plays the chain like
  // every other action. Create runs as three steps — the record (the one write), the schema it
  // landed in, then documents — and the card shows while `flow` is 'declare', so closing the
  // dock or starting another flow puts it away with everything else.

  bcOpenForm() {
    // A new task, a fresh panel: whatever conversation was already in the dock is cleared,
    // not left showing underneath the form — it is already saved under Recent tasks.
    this.ccChatReset();
    this.orchWith('Hoist building', this.ctxLabel(), 'declare', {
      bcMode: 'create', bcTarget: null, bcStep: 0, bcResult: null,
      bcOpen: true, bcForm: Object.assign({}, BLANK), bcMix: BLANK_MIX.map((m) => Object.assign({}, m)),
      bcErrors: {}, bcWarnings: [], bcSaving: false, bcTopError: ''
    });
  },

  // Opening Edit on a row. `bcTarget` carries the building_id the PATCH addresses and the
  // row's updated_at, read off the row so it can be sent back as expected_updated_at.
  bcOpenEdit(row) {
    if (!row || !row.buildingId) return this.flash('This row has no building_id — it predates the building graph and cannot be edited here yet.');
    // Kept outside state: they are the diff baseline, not something the form or a re-render
    // needs to read, and they must survive exactly as read even if `row` is later replaced.
    this._bcOriginal = formFromRow(row);
    this._bcOriginalRow = row;
    this.ccChatReset();
    // One step, not three: an edit is a single PATCH and the record already has its schema.
    this.orchWith('Edit building', row.name || this.ctxLabel(), 'declare', {
      bcMode: 'edit', bcTarget: { buildingId: row.buildingId, updatedAt: row.updatedAt || null, name: row.name },
      bcStep: 0, bcResult: null,
      bcOpen: true, bcForm: Object.assign({}, this._bcOriginal), bcMix: mixFromRow(row),
      bcErrors: {}, bcWarnings: [], bcSaving: false, bcTopError: ''
    });
  },

  bcCloseForm() { this.setState({ bcOpen: false, flow: null, bcErrors: {}, bcTopError: '' }); },

  // Step 2 → 3. The record is already written by now, so there is no way back from here.
  bcNext() { this.setState({ bcStep: 2 }); },

  // Step 3's two exits. Ingest hands the new building to the dock's ingest flow; later leaves
  // the keyed-as line in the dock so the outcome is still on screen after the card goes.
  bcIngestNow() {
    const r = this.state.bcResult || {};
    this.setState({ flow: 'ingest', flowDone: '', declFor: r.name || 'the new building', bcOpen: false });
  },

  bcLater() {
    const r = this.state.bcResult || {};
    this.setState({
      flow: null, bcOpen: false,
      flowDone: (r.name || 'The building') + ' is hoisted and keyed as ' + (r.code || '—') + '. No documents ingested — its Hoist Score stays at 0% until they arrive. Run Ingest documents whenever you are ready.'
    });
  },

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

  // The create body: every field the form offers, in the shape POST expects.
  bcCreateBody() {
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
    return body;
  },

  // The edit body: only what changed from the prefilled original, in PATCH's vocabulary. An
  // optional field blanked back to nothing is sent as "" (a clear); one that was already
  // empty and stays empty is omitted (nothing changed). use_mix travels with use_type
  // whenever either moved, since a mix belongs to the use it splits.
  bcPatchBody(original) {
    const f = this.state.bcForm || {};
    const o = original || {};
    const body = {};
    ['site_name', 'state', 'city', 'postcode', 'metering_route', 'building_code', 'site_id'].forEach((k) => {
      const now = trim(f[k]), was = trim(o[k]);
      if (now !== was) body[k] = now;
    });
    if (f.country_code !== o.country_code) body.country_code = f.country_code;
    if (num(f.floors) !== num(o.floors)) body.floors = num(f.floors);
    if (f.metering_granularity !== o.metering_granularity) body.metering_granularity = f.metering_granularity;
    const gfaNow = String(f.gfa_sqm || '').trim() ? num(f.gfa_sqm) : '';
    const gfaWas = String(o.gfa_sqm || '').trim() ? num(o.gfa_sqm) : '';
    if (gfaNow !== gfaWas) body.gfa_sqm = gfaNow;
    const mixNow = (this.state.bcMix || []).map((m) => ({ use: m.use, pct: num(m.pct) }));
    const mixWas = mixFromRow(this._bcOriginalRow).map((m) => ({ use: m.use, pct: num(m.pct) }));
    if (f.use_type !== o.use_type || JSON.stringify(mixNow) !== JSON.stringify(mixWas)) {
      body.use_type = f.use_type;
      body.use_mix = mixNow;
    }
    return body;
  },

  // Resolves true once the write has succeeded, false whenever nothing was sent or the
  // service refused it — a caller (and a test) can tell "saved" from "still open" without
  // reading state back out.
  async bcSubmit() {
    if (this.state.bcSaving) return false;
    const edit = this.state.bcMode === 'edit' && this.state.bcTarget;
    const f = this.state.bcForm || {};
    const name = f.site_name || (edit && edit.name) || 'building';
    const body = edit ? this.bcPatchBody(this._bcOriginal) : this.bcCreateBody();
    if (edit && !Object.keys(body).length) {
      this.setState({ bcTopError: 'Nothing to change — every field is as it was.' });
      return false;
    }
    if (edit && this.state.bcTarget.updatedAt) body.expected_updated_at = this.state.bcTarget.updatedAt;

    this.setState({ bcSaving: true, bcErrors: {}, bcTopError: '', bcWarnings: [] });
    try {
      const res = edit
        ? await energyApi.patchBuilding(this.state.bcTarget.buildingId, body)
        : await energyApi.createBuilding(body);
      // An edit is done here. A create is a third of the way: the card moves on to show the
      // schema the record landed in, with the code the service allocated, then to documents.
      this.setState(edit
        ? { bcSaving: false, bcOpen: false, flow: null, bcWarnings: res.warnings || [] }
        : { bcSaving: false, bcStep: 1, bcWarnings: res.warnings || [],
            bcResult: { name: name, code: res.building_code || '—', storedAs: res.stored_as || null, warnings: res.warnings || [] } });
      this.flash(edit
        ? 'Saved ' + name + ((res.changed || []).length ? ' — ' + res.changed.join(', ') + ' changed' : '') + (res.relocated ? ' · relocated to its new market’s regulation pack' : '')
        : 'Hoisted ' + name + ' as ' + (res.building_code || '—'));
      await this.bldLoad();
      // Warnings are not failures — "stored as Retail", "no regulation pack for this
      // market". They belong after the success, not instead of it.
      if ((res.warnings || []).length) this.flash(res.warnings[0]);
      return true;
    } catch (e) {
      const errBody = (e && e.body) || {};
      const errs = errBody.errors || {};
      const patch = { bcSaving: false, bcErrors: errs };
      if (edit && e && e.status === 409 && errBody.current_updated_at) {
        patch.bcTarget = Object.assign({}, this.state.bcTarget, { updatedAt: errBody.current_updated_at });
        patch.bcTopError = 'This building changed since you opened it. Reload and re-apply your edit — the row has been re-read.';
        this.bldLoad();
      } else {
        patch.bcTopError = Object.keys(errs).length ? '' : ((e && e.message) || String(e));
        if (e && e.status === 409 && !edit) this.flash('That building code is already in use — nothing was overwritten.');
      }
      this.setState(patch);
      return false;
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

    const edit = s.bcMode === 'edit';
    const step = s.bcStep || 0;
    const r = s.bcResult || {};
    return {
      // Both roles, so a facilities manager is not blocked from the register they keep.
      bcCanHoist: !!s.signedIn && HOIST_ROLES.has(s.role),
      bcCanRemove: !!s.signedIn && HOIST_ROLES.has(s.role),
      // The card is a dock flow: it shows while the dock is on it, and goes with the dock.
      bcOpen: !!s.bcOpen && s.flow === 'declare',
      bcMode: s.bcMode || 'create',
      bcTitle: edit ? 'Edit building' : 'Hoist a building',
      bcStep: step,
      bcStep1: step === 0, bcStep2: step === 1, bcStep3: step === 2,
      bcStepLabel: edit ? 'Only what you change is sent' : 'Step ' + (step + 1) + ' of 3 · ' + STEP_NAMES[step],
      bcIntro: edit
        ? 'Only the fields you change are sent. Leaving one alone leaves it alone; clearing an optional one clears it on the record.'
        : 'This record becomes the primary key. Every document ingested afterwards is stamped with it, so nothing sits in the graph unattached to an asset.',
      bcForm: f,
      bcErr: (k) => err[k] || '',
      bcErrShow: (k) => (err[k] ? 'block' : 'none'),
      bcTopError: s.bcTopError || '',
      bcTopErrorShow: s.bcTopError ? 'block' : 'none',
      bcSaving: !!s.bcSaving,
      bcSubmitLabel: edit ? (s.bcSaving ? 'Saving…' : 'Save changes') : (s.bcSaving ? 'Writing…' : 'Write the record'),
      bcSet: (k) => (e) => this.bcSet(k, e && e.target ? e.target.value : e),
      bcClose: () => this.bcCloseForm(),
      bcSubmit: () => this.bcSubmit(),
      bcOpenEdit: (row) => this.bcOpenEdit(row),

      // Steps 2 and 3 — what the write produced, and where to go from it.
      bcResultName: r.name || '',
      bcResultCode: r.code || '',
      bcResultWarnings: r.warnings || [],
      bcNext: () => this.bcNext(),
      bcIngestNow: () => this.bcIngestNow(),
      bcLater: () => this.bcLater(),

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
