// vendorHoist — Hoist vendors, in the User view of an admin (12 Oct 2026).
//
// From the Vendors page an admin — or a superadmin — opens Hoist vendors: the vendors the
// platform has seen named on certificates that are not in the vendor master, the vendor master
// itself with each vendor's eligibility, and "Hoist a new vendor". Hoisting runs in the
// orchestrator dock as a conversation, one question at a time, each answer echoed back
// (prototype: hoistra-frontend-changes/03-vendor-hoisting, 11 Oct 2026).
//
// Who sees it: an account that can administer, while in User view. The admin view does not
// carry it, and a plain user is never offered it — the page and the conversation both refuse.
//
// What it writes (saving switched on by Hussain, 12 Oct 2026), and only when Hoist vendor is
// pressed:
//   - a detected vendor through its certificate: POST /api/compliance/certificates/{id}/create-vendor
//     creates the vendor (or matches one of that name in the certificate's company) and links the
//     certificate; the vendor's other certificates are then linked with POST …/{id}/link. Only
//     vendor_name is sent — the route writes any other profile field straight into vendors
//     columns that not every database has, and the same insert is the one ingest already makes.
//   - a new vendor through the connector: POST /backend/connector/api/v1/plenum/vendors, with the
//     company it belongs to (a superadmin's company in view, otherwise the account's).
//   - an accounts email or phone as a vendor contact: POST …/plenum/vendor-contacts.
// Trade, registration, contract, rate, accreditations and buildings have no column in the vendor
// master. They are kept with the hoist in this browser and shown in the vendor trail, and every
// confirmation says which were not stored. A refused save keeps the record under Hoisted vendors,
// marked Not saved, with a Save to try again. Neither create route checks the caller's role:
// keeping a plain user out is this page's doing, and the backend should enforce it too.
//
// Where "detected" comes from: the certificate register (GET /api/compliance/certificates),
// whose rows say whether the vendor named on them is on record. Nothing else in the backend
// keeps vendors found in documents; the prototype's invoices, work orders and email have no
// live source, so this list does not claim them. Until the register answers, nothing is
// detected — never from the sample register.
//
// The shapers are pure and tested in test/vendorHoist.test.mjs; the methods are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { canAdmin, accountCanIngest } from './auth.js';
import { complianceApi } from '../api/compliance.js';
import { connectorApi } from '../api/connector.js';

export const VH_STORE_KEY = 'hoistra.vendorHoist.v1';

export const VH_FIELDS = [
  ['name', 'Legal name'], ['trade', 'Trade / scope'], ['email', 'Accounts email'], ['phone', 'Phone'],
  ['reg', 'Company registration'], ['contract', 'Contract'], ['rate', 'Rate'],
  ['accreds', 'Accreditations'], ['areas', 'Buildings served']
];

// The answers the vendor master has no field for, as a confirmation names them.
// Buildings are not here: the buildings question assigns the vendor to them (vendorBuildings.js).
const NOT_STORED = [['trade', 'trade'], ['reg', 'registration'], ['contract', 'contract'], ['rate', 'rate'],
  ['accreds', 'accreditations']];
const notStoredOf = (f) => NOT_STORED.filter(([k]) => String((f || {})[k] || '').trim()).map(([, l]) => l);
const reasonOf = (e) => String((e && e.message) || 'the vendor service could not be reached').replace(/[.\s]+$/, '');
function agoText(verb, at) {
  const m = Math.round((Date.now() - (at || Date.now())) / 60000);
  return verb + ' ' + (m < 1 ? 'just now' : m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago');
}
// What a saved hoist tells the person, in one paragraph.
function savedText(rec, res) {
  const out = [rec.name + ' is in the vendor master' + (res.created ? '.' : ' — it matched a vendor of that name already there, and the certificate is linked to it.')];
  const n = (rec.certIds || []).length;
  if (n > 1) out.push(res.linked + ' of ' + n + ' certificates are linked to it' + (res.linkFailed ? ' — ' + res.linkFailed + ' could not be linked' : '') + '.');
  if (res.contact === 'saved') out.push('The accounts contact is saved.');
  if (res.contact === 'failed') out.push('The accounts contact could not be saved: ' + res.contactError + '.');
  if (res.assigned && res.assigned.length) out.push('Assigned to ' + listOf(res.assigned) + '.');
  if (res.assignFailed && res.assignFailed.length) out.push('Not assigned to ' + listOf(res.assignFailed) + ': ' + res.assignError + '.');
  const kept = notStoredOf(rec.fields);
  if (kept.length) out.push('Not stored (no field for them yet): ' + kept.join(', ') + ' — kept with this hoist in your browser and in the vendor trail.');
  return out.join(' ');
}

const norm = (s) => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();
const listOf = (xs) => xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1];
const uniq = (list) => list.filter((x, i) => x && list.indexOf(x) === i);
const split = (s) => String(s || '').split(/\s*·\s*/).filter(Boolean);

// The vendor master as the compliance register holds it: a vendor with a record (an id),
// with its eligibility for work read off its accreditations. `gone` is the vendor ids deleted
// from this tab (logic/vendorDelete.js), which a register read before the delete still holds.
export function masterVendors(vendors, gone) {
  const out = gone || [];
  return (vendors || []).filter((v) => v && v.id && v.name && out.indexOf(String(v.id)) < 0).map((v) => {
    const status = v.block === 'Blocked' || v.worst === 'Blocked' || v.worst === 'Lapsed'
      ? { label: 'Not eligible', tone: 'risk' }
      : v.worst === '<30d' || v.worst === '<90d' ? { label: 'Expiring', tone: 'warn' }
      : v.worst === 'OK' ? { label: 'Eligible', tone: 'ok' }
      : { label: 'No certificates', tone: 'none' };
    return { key: norm(v.name), id: v.id, name: v.name, trade: v.spec && v.spec !== '—' ? v.spec : '', status: status };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

// Vendors named on certificates that are not linked to a vendor on record and whose name the
// master does not hold, one entry per name however it was spelled.
export function detectedVendors(certs, master) {
  const known = {};
  (master || []).forEach((v) => { known[v.key] = true; });
  const by = {};
  const order = [];
  (certs || []).forEach((c) => {
    // A retired vendor's certificates are its history: never a vendor to hoist again.
    // A row that carries a vendor id is linked to one already — read as not on record only when
    // the server could not say — and is never re-pointed from here.
    if (!c || !c.vendor || c.vendorOnRecord !== false || c.vendorDeleted || c.vendorId) return;
    const key = norm(c.vendor);
    if (!key || known[key]) return;
    if (!by[key]) { by[key] = { key: key, name: String(c.vendor).trim().replace(/\s+/g, ' '), certs: [] }; order.push(key); }
    by[key].certs.push(c);
  });
  return order.map((k) => {
    const d = by[k];
    // A draft is a certificate nobody has confirmed yet, so the line says so: the vendor was
    // named on it, but it is not evidence until the PM confirms or rejects it.
    const shown = d.certs.slice(0, 3).map((c) => [c.nm, c.kind === 'building' ? (c.building || 'no building named') : 'vendor accreditation', c.exp ? 'expires ' + c.exp : null, c.draft ? 'draft, awaiting confirmation' : null].filter(Boolean).join(' · '));
    if (d.certs.length > 3) shown.push('+' + (d.certs.length - 3) + ' more certificates');
    return {
      key: d.key, name: d.name, certs: d.certs,
      trade: (d.certs.find((c) => c.trade) || {}).trade || null,
      buildings: uniq(d.certs.filter((c) => c.kind === 'building').map((c) => c.building)),
      certNames: uniq(d.certs.map((c) => c.nm)),
      sources: shown
    };
  }).sort((a, b) => b.certs.length - a.certs.length || a.name.localeCompare(b.name));
}

// The per-browser record of what was hidden and what was prepared, per company.
export function loadVhStore() {
  try {
    const raw = window.localStorage.getItem(VH_STORE_KEY);
    const d = raw ? JSON.parse(raw) : {};
    return d && typeof d === 'object' ? d : {};
  } catch (e) { return {}; }
}
// Who did it, as the vendor audit trail names a person (logic/vendorAudit.js vendorRow).
function actorOf(s) {
  const a = s.account || {};
  return { name: a.full_name || a.email || null, email: a.email || null, role: a.role || null };
}
// The hoisting entries of the vendor audit trail: this browser's own record of each hoist, saved
// or not, newest first, capped; the trail says so on every row. (The vendor itself is saved.)
const TRAIL_MAX = 200;
function withEvent(cur, s, ev) {
  const id = 'vh-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
  const entry = Object.assign({ id: id, at: new Date().toISOString(), source: 'browser', actor: actorOf(s) }, ev);
  return Object.assign({}, cur, { events: [entry].concat(cur.events || []).slice(0, TRAIL_MAX) });
}

function saveVhStore(store) {
  try { window.localStorage.setItem(VH_STORE_KEY, JSON.stringify(store)); } catch (e) { /* private window: kept for this tab */ }
}

// The six questions. The replies never say a register, contract or accreditation was or will be
// checked: registration, contract and accreditations are not stored or checked anywhere yet, so
// each reply says it is kept in this browser only (pre-push review, 12 Oct 2026).
function steps(F, canUpload) {
  const name = (F.f.name || '').trim() || 'the vendor';
  const list = [
    { keys: ['name', 'trade'], need: ['name'],
      q: F.from ? 'I found this vendor on your certificates. Is this the right legal name, and what do they do for you?' : "Let's hoist a new vendor. What is their legal name, and what do they do for you?" },
    { keys: ['email', 'phone'], skip: true, q: 'Where do they invoice from, and how do we reach them?' },
    { keys: ['reg'], skip: true, q: 'What is ' + name + "'s company registration number?",
      reply: (f) => f.reg ? 'Noted: ' + f.reg + ' — kept in this browser only — not stored or checked.' : 'No registration given.' },
    { keys: ['contract', 'rate'], skip: true, q: 'Is there a contract with them? Give me the reference and the rate, or skip to use the platform defaults.',
      reply: (f) => f.contract ? 'Noted: ' + f.contract + ' — kept in this browser only — not stored or checked. Upload the contract itself for its terms to be read.' : 'No contract given.' },
    { keys: ['accreds'], skip: true, q: "Which accreditations do they hold? These decide which work they're eligible for.",
      reply: (f) => {
        const n = (f.accreds || '').split(/\s*·\s*|,\s*/).filter(Boolean).length;
        return n ? 'Noted — kept in this browser only — not stored or checked. Upload ' + (n === 1 ? 'the certificate' : 'the certificates') + ' for ' + (n === 1 ? 'it' : 'them') + ' to count.' : 'Nothing listed.';
      } },
    { keys: ['areas'], skip: true, chips: true, q: 'Which buildings will they work in?' }
  ];
  // Their certificates, attached here and uploaded once the vendor is saved (Hussain, 12 Oct
  // 2026). Asked only of an account that may add data: the upload is the same as Ingest
  // documents, which refuses anyone else.
  if (canUpload) {
    list.push({ keys: [], skip: true, files: true,
      q: 'Do you have ' + name + "'s certificates — accreditations, registrations, insurance? Attach them: once " + name + ' is saved they are read, checked and filed against it.' });
  }
  return list;
}

// A file's key (name and size), what its "for" choice is stored under.
const fileKey = (f) => f.name + ':' + (f.size || 0);
// The files attached to the certificates question, as the conversation shows them: a file for
// a building says which.
const fileLine = (files, targets) => files.length + ' attached · ' + files.map((f) => {
  const b = (targets || {})[fileKey(f)];
  return b ? f.name + ' → ' + b : f.name;
}).join(', ');

const FIELD_LABEL = {};
VH_FIELDS.forEach((k) => { FIELD_LABEL[k[0]] = k[1]; });
const PH = { name: 'e.g. Thames Valley Drainage Ltd', trade: 'e.g. Drainage, CCTV and jetting', email: 'accounts@…', phone: '+44 …', reg: 'e.g. 10933271', contract: 'Reference, or leave blank', rate: 'e.g. £160 / visit', accreds: 'e.g. Gas Safe · SafeContractor' };

export const vendorHoistMethods = {
  // An account that can administer, in User view. Never the admin view, never a plain user.
  vhCan(s) { return !!s.signedIn && canAdmin(s.account) && s.role !== 'admin'; },

  // The company's own buildings, from the live buildings register, for the last question.
  vhBuildingNames() { return uniq((this.bldData() || []).map((b) => b && b.name)).sort((a, b) => a.localeCompare(b)); },

  // The company a new vendor is filed under: the one a superadmin is viewing, else the account's.
  vhCompany(s) { return s.viewOrgId || (s.account && s.account.organization_id) || null; },

  // Writes one hoist. Returns what happened; never throws.
  async vhSave(rec, company) {
    const name = rec.name;
    const f = rec.fields || {};
    let vendorId = null, created = true, linked = 0, linkFailed = 0;
    try {
      if ((rec.certIds || []).length) {
        const r = await complianceApi.createVendorForCertificate(rec.certIds[0], { vendor_name: name });
        if (!r || r.ok === false || !r.vendor_id) return { ok: false, error: (r && r.error) || 'the vendor service did not return a vendor' };
        vendorId = String(r.vendor_id);
        created = r.created !== false;
        linked = 1;
        // The others go through create-vendor too: it finds the vendor just made by name and
        // links the certificate, with or without a source document (/link refuses one without).
        for (const id of rec.certIds.slice(1)) {
          try {
            const l = await complianceApi.createVendorForCertificate(id, { vendor_name: name });
            if (!l || l.ok === false || String(l.vendor_id) !== vendorId) linkFailed += 1; else linked += 1;
          } catch (e) { linkFailed += 1; }
        }
      } else {
        if (!company) return { ok: false, error: 'there is no company to file the vendor under' };
        // The connector keeps no unique vendor name, so the list is read again first: a name
        // already there (or saved by a try whose answer was lost) is not created a second time.
        if (typeof this.vmLoad === 'function') await this.vmLoad();
        const have = norm(name);
        const known = (this.state.vmRows || []).concat(masterVendors(this.state.ccLive && this.state.ccLive.vendors))
          .some((v) => v && norm(v.name) === have);
        if (known) return { ok: false, error: name + ' is already in your vendor list — open it on the Vendors page' };
        const r = await connectorApi.createVendor({ organization_id: company, vendor_name: name });
        if (!r || !r.id) return { ok: false, error: 'the vendor service did not return a vendor' };
        vendorId = String(r.id);
      }
    } catch (e) { return { ok: false, error: reasonOf(e) }; }
    let contact = 'none', contactError = '';
    const email = String(f.email || '').trim(), phone = String(f.phone || '').trim();
    if (email || phone) {
      try {
        await connectorApi.createVendorContact({ vendor_id: vendorId, name: name.slice(0, 255), email: email || null, phone: phone ? phone.slice(0, 50) : null, designation: 'Accounts' });
        contact = 'saved';
      } catch (e) { contact = 'failed'; contactError = reasonOf(e); }
    }
    // The buildings question: the vendor is assigned to each building chosen (by the id the
    // buildings register holds — the same one an upload is filed against).
    let assigned = [], assignFailed = [], assignError = '';
    const areas = split(f.areas);
    if (areas.length) {
      const ids = areas.map((nm) => (typeof this.iBuildingIdFor === 'function' ? this.iBuildingIdFor(nm) : null));
      const known = areas.filter((nm, i) => ids[i]);
      const unknown = areas.filter((nm, i) => !ids[i]);
      if (known.length) {
        const a = await this.vbAssign(vendorId, ids.filter(Boolean), 'hoist');
        if (a.ok) assigned = known;
        else { assignFailed = known; assignError = a.error; }
      }
      if (unknown.length) {
        assignFailed = assignFailed.concat(unknown);
        assignError = assignError || 'those buildings are not in the buildings register';
      }
    }
    return { ok: true, vendorId: vendorId, created: created, linked: linked, linkFailed: linkFailed, contact: contact, contactError: contactError,
      assigned: assigned, assignFailed: assignFailed, assignError: assignError };
  },

  // Saves a hoist and records it — in Hoisted vendors and in the vendor trail — either way.
  async vhCommit(org, rec, s) {
    const res = await this.vhSave(rec, this.vhCompany(s));
    const entry = Object.assign({}, rec, res.ok
      ? { saved: true, vendorId: res.vendorId, created: res.created, savedAt: Date.now(), contact: res.contact, linked: res.linked, linkFailed: res.linkFailed, error: null }
      : { saved: false, error: res.error });
    const base = { vendor: { name: rec.name }, trade: rec.trade || '', fields: Object.assign({}, rec.fields || {}), certificates: (rec.certIds || []).length };
    this.vhWrite(org, (cur) => withEvent(
      Object.assign({}, cur, { prepared: [entry].concat(cur.prepared.filter((x) => x.key !== rec.key)) }), s,
      res.ok
        ? Object.assign(base, { kind: 'hoist_saved', vendorId: res.vendorId, created: res.created, linked: res.linked, linkFailed: res.linkFailed, contact: res.contact, contactError: res.contactError })
        : Object.assign(base, { kind: 'hoist_failed', error: res.error })));
    // The certificate now names a vendor on record: the register re-reads so it leaves Detected,
    // and the vendor list re-reads so a vendor with no certificate is listed too.
    if (res.ok) this.vhReread();
    return res;
  },

  // The register and the vendor list, read again once any read already in flight has landed —
  // a read that started before the save would otherwise answer without the new vendor, and the
  // loaders skip a second call while one is running (pre-push review, 12 Oct 2026).
  vhReread() {
    const after = (flag, run) => {
      let tries = 0;
      const go = () => {
        if (!this.state.signedIn) return;
        if (this[flag] && tries++ < 50) { setTimeout(go, 120); return; }
        if (typeof run === 'function') run.call(this);
      };
      go();
    };
    after('_ccLoading', this.ccLoad);
    after('_vmLoading', this.vmLoad);
    // The Vendors page's own read: its directory row and Accreditations tab read the vendor's
    // certificates from it, and stayed "no accreditation on record" until a reload (12 Oct 2026).
    after('_vpLoading', this.vpLoad);
  },

  // The certificates attached in the hoist, once the vendor is saved: the save's summary stays
  // at the top of the panel, the files go up exactly as Ingest documents sends them (read,
  // checked, filed; a held one asks its question below), and the register is read again to say
  // how many are now on record for the vendor. A vendor's own certificates name no building.
  async vhUploadAfterSave(rec, res, files, targets, epoch) {
    const name = rec.name;
    const tg = targets || {};
    this._vhFiles = [];
    this.setState({
      vhSaving: false, flow: null, vhFlow: null, flowDone: '', sessionId: null,
      ccChat: [{ role: 'bot', text: savedText(rec, res) }]
    });
    // One upload per group: the vendor's own certificates with no building, then each building's
    // certificates filed against that building (the chat's filing building, cleared after).
    const groups = [];
    files.forEach((f) => {
      const b = tg[fileKey(f)] || '';
      let g = groups.find((x) => x.building === b);
      if (!g) { g = { building: b, files: [] }; groups.push(g); }
      g.files.push(f);
    });
    groups.sort((a, b) => (a.building ? 1 : 0) - (b.building ? 1 : 0));
    // Whatever happens to one upload, the chat is never left filing against a building, and the
    // panel says which files did not go (pre-push review, 12 Oct 2026).
    const failed = [];
    try {
      const tries = this._vhBusyTries === undefined ? 600 : this._vhBusyTries;
      for (const g of groups) {
        // Signed out, or another company in view: nothing more goes up.
        if ((this._vhEpoch || 0) !== (epoch === undefined ? (this._vhEpoch || 0) : epoch) || !this.state.signedIn) return;
        // One turn at a time: a reply still running would refuse this group's files.
        for (let i = 0; i < tries && this.state.ccBusy; i += 1) await new Promise((r) => setTimeout(r, 200));
        if (this.state.ccBusy) { failed.push(g); continue; }
        const id = g.building && typeof this.iBuildingIdFor === 'function' ? this.iBuildingIdFor(g.building) : null;
        this.setState({ ccFiles: g.files, cbBuildingId: id, cbBuildingName: id ? g.building : '', declFor: id ? g.building : '', declForId: id });
        const n = g.files.length === 1 ? 'this certificate' : 'these ' + g.files.length + ' certificates';
        const before = (this.state.ccChat || []).length;
        try {
          await this.ccAsk(g.building
            ? 'Ingest ' + n + ' for ' + g.building + '. They are certificates for the building, and they name ' + name + ' as the contractor.'
            : 'Ingest ' + n + ' for the vendor ' + name + '. They are ' + name + "'s own certificates — accreditations, registrations or insurance — not a building's.");
          // ccAsk reports in the transcript rather than throwing: a refusal leaves the files in
          // the tray, an error ends on an error turn.
          const after = (this.state.ccChat || []).slice(before);
          const last = after[after.length - 1];
          if ((this.state.ccFiles || []).length || !after.length || (last && last.error)) {
            failed.push(g);
            this.setState({ ccFiles: [] });
          }
        } catch (e) {
          failed.push(g);
          this.setState({ ccBusy: false, ccFiles: [] });
        }
      }
    } finally {
      this.setState({ cbBuildingId: null, cbBuildingName: '', declFor: '', declForId: null });
    }
    if (failed.length) {
      const names = failed.reduce((q, g) => q.concat(g.files.map((f) => f.name)), []);
      this.setState((p) => ({ ccChat: (p.ccChat || []).concat([{ role: 'bot',
        text: (names.length === 1 ? names[0] + ' could not be uploaded' : names.join(', ') + ' could not be uploaded')
          + '. ' + name + ' is saved; upload ' + (names.length === 1 ? 'it' : 'them') + ' again with Ingest, against the building or with no building for the vendor\'s own.' }]) }));
    }
    await this.vhCountOnRecord(name, res.vendorId);
  },

  // Reads the register again (once any read in flight has landed) and says in the panel how many
  // certificates are on record for the vendor — matched by its id, or by its name for one the
  // register has not tied to an id yet.
  async vhCountOnRecord(name, vendorId) {
    for (let i = 0; i < 50 && this._ccLoading; i += 1) await new Promise((r) => setTimeout(r, 120));
    if (typeof this.ccLoad === 'function') await this.ccLoad();
    if (typeof this.vmLoad === 'function') this.vmLoad();
    this.vhReread();
    const key = norm(name), vid = vendorId ? String(vendorId) : '';
    const mine = ((this.state.ccLive && this.state.ccLive.certs) || [])
      .filter((c) => (vid && c.vendorId && String(c.vendorId) === vid) || norm(c.kind === 'vendor' ? c.holder : c.vendor) === key);
    const n = mine.length;
    const text = n
      ? (n === 1 ? '1 certificate is' : n + ' certificates are') + ' now on record for ' + name + ': ' + uniq(mine.map((c) => c.nm)).slice(0, 6).join(', ') + '. They show on its Accreditations tab and in Compliance.'
      : 'No certificate is on record for ' + name + ' yet. If one above was held, answer its question; a certificate that names a different contractor is listed under Hoist vendors.';
    this.setState((p) => ({ ccChat: (p.ccChat || []).concat([{ role: 'bot', text: text }]) }));
  },

  // Save, from a row in Hoisted vendors: a record that was refused, or prepared before saving.
  async vhSaveRow(org, rec, s) {
    // One save at a time, from the row or the dock: the two used separate guards and could both
    // post the same vendor.
    if (this.state.vhSavingKey || this.state.vhSaving) return;
    this.setState({ vhSavingKey: rec.key, vhSaving: true });
    const res = await this.vhCommit(org, rec, s);
    this.setState({ vhSavingKey: null, vhSaving: false });
    this.flash(res.ok ? savedText(rec, res) : 'Not saved — ' + res.error + '.');
  },

  vhOrg(s) { return s.viewOrgId || (s.account && s.account.organization_id) || 'own'; },

  vhStore() { return this.state.vhLocal || (this.state.vhLocal = loadVhStore()); },

  // This company's hoisting entries for the vendor audit trail (logic/vendorAudit.js).
  vhTrailEvents(s) { return ((this.vhStore()[this.vhOrg(s)] || {}).events || []).slice(); },

  // Read from storage, not this tab's copy, so another tab's hoists are not written over.
  vhWrite(org, change) {
    const store = Object.assign({}, loadVhStore());
    const cur = Object.assign({ dismissed: [], prepared: [], events: [] }, store[org] || {});
    store[org] = change(cur);
    saveVhStore(store);
    this.setState({ vhLocal: store });
  },

  vhOpen() {
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    this.setState({ view: 'vh', navOpen: true, detail: null, queueOpen: false });
  },

  // Opens the conversation, prefilled from the certificates when the vendor was detected.
  vhStart(d) {
    this._vhFiles = [];
    const f = { name: '', trade: '', email: '', phone: '', reg: '', contract: '', rate: '', accreds: '', areas: '' };
    if (d) { f.name = d.name; f.trade = d.trade || d.certNames.join(' · '); f.areas = d.buildings.join(' · '); }
    const prefilled = d ? Object.keys(f).filter((k) => f[k]) : [];
    this.orchWith('Hoist a vendor', d ? d.name + ' · named on ' + d.certs.length + (d.certs.length === 1 ? ' certificate' : ' certificates') : 'New vendor', 'vhoist', {
      vhFlow: { from: d ? d.key : null, certIds: d ? d.certs.map((c) => c.id) : [], step: 0, f: f, prefilled: prefilled, files: [] },
      vhAreaQ: ''
    });
  },

  vhVals(s) {
    const can = this.vhCan(s);
    const reg = s.ccLive || null;
    const org = this.vhOrg(s);
    const mine = Object.assign({ dismissed: [], prepared: [] }, this.vhStore()[org] || {});
    // The vendor list: what the compliance register says about each vendor, plus every vendor in
    // the list itself that the register does not mention — one hoisted with no certificate yet
    // (logic/vendorMaster.js). The register's row wins, since it carries eligibility.
    const gone = s.vdlGone || [];
    const fromReg = reg ? masterVendors(reg.vendors, gone) : [];
    const regKeys = {};
    fromReg.forEach((v) => { regKeys[v.key] = true; });
    const master = fromReg.concat((s.vmRows || [])
      .filter((v) => !regKeys[norm(v.name)] && gone.indexOf(String(v.id)) < 0)
      .map((v) => ({ key: norm(v.name), id: v.id, name: v.name, trade: '', status: { label: 'No certificates', tone: 'none' } })))
      .filter((v, i, arr) => arr.findIndex((x) => x.key === v.key) === i)
      .sort((a, b) => a.name.localeCompare(b.name));
    const taken = {};
    mine.prepared.forEach((p) => { if (!p.deleted) taken[p.key] = true; });
    const all = reg ? detectedVendors(reg.certs, master) : [];
    const hidden = s.vhShowHidden ? [] : mine.dismissed;
    const det = all.filter((d) => !taken[d.key] && hidden.indexOf(d.key) < 0);
    const out = Object.assign({
      vhShow: can && s.view === 'vp',
      vhDetN: det.length,
      vhOpen: () => this.vhOpen(),
      isVH: can && !!s.signedIn && s.view === 'vh'
    }, this.vhChat(s, can));
    // A stored Hoist vendors view, opened by an account that may not use it, is the Vendors page.
    if (!can && s.view === 'vh') out.isVP = !!s.signedIn;
    if (!out.isVH) return out;

    const tone = (k) => ({ ok: ['var(--st-ok)', 'var(--st-ok-bg)'], warn: ['var(--st-warn)', 'var(--st-warn-bg)'], risk: ['var(--st-risk)', 'var(--st-risk-bg)'] })[k] || ['var(--color-neutral-400)', 'var(--color-neutral-900)'];
    // A hoist that is saved shows here until the vendor master lists it itself; one that is not
    // saved stays, with Save and Discard.
    const inMaster = {};
    const idInMaster = {};
    master.forEach((v) => { inMaster[v.key] = true; if (v.id) idInMaster[String(v.id)] = true; });
    // A hoist whose vendor was deleted since (logic/vendorDelete.js vdlMarkHoist) is in no
    // master and never will be again: it is not shown.
    // By the vendor id it was saved under too: a detected vendor renamed in the conversation
    // keeps its detected key, which the master never holds.
    const local = mine.prepared.filter((p) => !p.deleted && !(p.saved && (inMaster[p.key] || (p.vendorId && idInMaster[String(p.vendorId)]))));
    const saving = s.vhSavingKey || null;
    const prepared = local.map((p) => p.saved
      ? {
          key: p.key, name: p.name, trade: p.trade || '', status: 'In vendor master', fg: tone('ok')[0], bg: tone('ok')[1],
          when: agoText('hoisted', p.savedAt), whenFg: 'var(--color-accent)', prepared: false, saved: true, local: true, canSave: false,
          title: 'Saved to the vendor master' + (p.vendorId ? ' · vendor id ' + p.vendorId : '') + (notStoredOf(p.fields).length ? '. Not stored (no field yet): ' + notStoredOf(p.fields).join(', ') + '.' : '.'),
          save: null, discard: null
        }
      : {
          key: p.key, name: p.name, trade: p.trade || '', status: 'Not saved', fg: tone('warn')[0], bg: tone('warn')[1],
          when: saving === p.key ? 'saving…' : p.error ? 'save refused' : agoText('prepared', p.at), whenFg: p.error ? 'var(--st-risk)' : 'var(--color-accent)',
          prepared: true, saved: false, local: true, canSave: !saving,
          title: p.error ? 'Not saved — ' + p.error + '.' : 'Prepared before saving was switched on. Save writes it to the vendor master.',
          save: () => this.vhSaveRow(org, p, s),
          discard: () => this.vhWrite(org, (cur) => withEvent(
            Object.assign({}, cur, { prepared: cur.prepared.filter((x) => x.key !== p.key) }), s,
            { kind: 'hoist_discarded', vendor: { name: p.name }, trade: p.trade || '' }))
        });
    const unsavedN = prepared.filter((p) => !p.saved).length;
    const limit = s.vhAll ? master.length : 30;
    const bscope = s.account && s.account.selected_building_id && (s.account.buildings || []).find((b) => b.id === s.account.selected_building_id);
    return Object.assign(out, {
      vhScope: [s.viewOrgName || (s.account && s.account.organization_name) || 'Your company', bscope ? bscope.name : 'every building'].join(' · '),
      vhNewForm: () => this.vhStart(null),
      vhDet: det.map((d) => ({
        key: d.key, name: d.name,
        count: d.certs.length + (d.certs.length === 1 ? ' certificate' : ' certificates'),
        meta: [d.trade, d.buildings.length > 2 ? d.buildings.length + ' buildings' : d.buildings.join(' · ')].filter(Boolean).join(' · ') || 'Vendor accreditation',
        sources: d.sources,
        hoist: () => this.vhStart(d),
        dismiss: () => {
          this.vhWrite(org, (cur) => withEvent(Object.assign({}, cur, { dismissed: uniq(cur.dismissed.concat([d.key])) }), s,
            { kind: 'hoist_dismissed', vendor: { name: d.name }, trade: d.trade || '', certificates: d.certs.length }));
          this.flash(d.name + ' is hidden from detected vendors in this browser.');
        }
      })),
      vhDetNote: !reg
        ? (s.ccLoading ? 'Reading the certificate register…' : 'The certificate register has not answered yet, so nothing is detected.')
        : 'No vendors waiting. A vendor named on a certificate but missing from the vendor master appears here.',
      vhHiddenN: s.vhShowHidden ? 0 : all.filter((d) => mine.dismissed.indexOf(d.key) > -1 && !taken[d.key]).length,
      vhShowHidden: () => this.setState({ vhShowHidden: true }),
      vhHoisted: prepared.concat(master.slice(0, limit).map((v) => ({
        key: v.key, name: v.name, trade: v.trade, status: v.status.label, fg: tone(v.status.tone)[0], bg: tone(v.status.tone)[1],
        when: 'in vendor master', whenFg: 'var(--color-neutral-500)', prepared: false, saved: false, local: false, canSave: false,
        title: '', save: null, discard: null
      }))),
      vhHoistedN: master.length + prepared.filter((p) => p.saved).length,
      vhPreparedN: unsavedN,
      vhSaveLine: "Hoisting writes the vendor to the vendor master. Trade, registration, contract, rate, accreditations and buildings have no field there yet, so they're kept with the hoist in this browser.",
      vhHoistedNote: !reg && !master.length ? (s.ccLoading ? 'Reading the vendor master…' : 'The vendor master has not answered yet.') : (master.length ? '' : 'No vendors in the vendor master yet.'),
      // Said when the vendor list itself could not be read: a vendor with no certificate yet is
      // then missing from this list, and the page should not look complete.
      vhListNote: s.vmError && !s.vmRows ? 'Vendors with no certificate yet are not listed: the vendor list could not be read — ' + s.vmError + '.' : '',
      vhMore: !s.vhAll && master.length > limit ? 'Show all ' + master.length : '',
      vhShowAll: () => this.setState({ vhAll: true }),
      vhBack: () => this.setState({ view: 'vp', navOpen: true, detail: null })
    });
  },

  // The conversation in the dock. Only for an account that may hoist: a forced flow renders
  // nothing for anyone else.
  vhChat(s, can) {
    const F = s.vhFlow;
    if (s.flow !== 'vhoist' || !F || !can) return { fVhoist: false };
    const canUpload = accountCanIngest(s);
    const STEPS = steps(F, canUpload);
    const step = Math.min(F.step, STEPS.length);
    // The File objects live on the controller (state holds their names): a File is not data to
    // keep, and a reload rightly drops a half-done hoist's attachments.
    const files = canUpload ? (this._vhFiles || []) : [];
    const cur = STEPS[step] || null;
    const missing = cur && cur.need ? cur.need.some((k) => !String(F.f[k] || '').trim()) : false;
    const patchF = (fn) => this.setState((p) => (p.vhFlow ? { vhFlow: Object.assign({}, p.vhFlow, fn(p.vhFlow)) } : {}));
    const setF = (k) => (e) => { const v = e.target.value; patchF((fl) => ({ f: Object.assign({}, fl.f, { [k]: v }), tried: false })); };
    // Nothing moves while Hoist vendor is saving: an edit made then would be ignored by the save
    // already under way (second review).
    const go = (n) => { if (this.state.vhSaving) return; patchF(() => ({ step: Math.max(0, Math.min(STEPS.length, n)), tried: false })); };
    const picked = split(F.f.areas);
    const q = String(s.vhAreaQ || '').trim().toLowerCase();
    const names = this.vhBuildingNames();
    const match = names.filter((n) => !q || n.toLowerCase().indexOf(q) > -1);
    const chips = uniq(picked.filter((n) => names.indexOf(n) > -1).concat(match.slice(0, 14)));
    const org = this.vhOrg(s);
    // Hoist vendor: the one write, and only here. A refusal keeps the card open with the reason,
    // and the record under Hoisted vendors, marked Not saved.
    const finish = async () => {
      const f = F.f, name = String(f.name || '').trim();
      if (!name || this.state.vhSaving || this.state.vhSavingKey) return;
      const rec = { key: F.from || norm(name), name: name, trade: f.trade || '', at: Date.now(), fields: Object.assign({}, f), certIds: F.certIds || [] };
      // The company and sign-in this hoist belongs to: a switch while it saves must not send its
      // certificates into the next company (second review, 12 Oct 2026).
      const epoch = this._vhEpoch || 0;
      this.setState({ vhSaving: true });
      patchF(() => ({ error: '' }));
      const res = await this.vhCommit(org, rec, s);
      if ((this._vhEpoch || 0) !== epoch || !this.state.signedIn || this.vhOrg(this.state) !== org) {
        this.setState({ vhSaving: false });
        if (res.ok && files.length && this.state.signedIn) this.flash(name + ' was saved, but its certificates were not uploaded: the company changed while it saved.');
        return;
      }
      // A file's building only counts while that building is still one it can be for.
      const allowed = picked.length ? picked : names;
      const targets = {};
      Object.keys(F.fileTargets || {}).forEach((k) => { if (allowed.indexOf(F.fileTargets[k]) > -1) targets[k] = F.fileTargets[k]; });
      if (res.ok && files.length) return this.vhUploadAfterSave(rec, res, files.slice(), targets, epoch);
      if (res.ok) return this.setState({ vhSaving: false, flow: null, vhFlow: null, flowDone: savedText(rec, res) });
      this.setState({ vhSaving: false });
      patchF(() => ({ error: 'Not saved — ' + res.error + '. The record is kept under Hoisted vendors; press Hoist vendor to try again.' }));
    };
    const nCert = (F.certIds || []).length;
    const kept = notStoredOf(F.f);
    const hasEmail = !!String(F.f.email || '').trim(), hasPhone = !!String(F.f.phone || '').trim();
    const contactGiven = hasEmail || hasPhone;
    const contactWhat = hasEmail && hasPhone ? 'email and phone are' : hasEmail ? 'email is' : 'phone is';
    return {
      fVhoist: true,
      orchStatus: 'In progress · nothing is written until you press Hoist vendor',
      vhcDone: STEPS.slice(0, step).map((st) => ({
        q: st.q,
        a: st.files ? (files.length ? fileLine(files, F.fileTargets) : 'None attached')
          : st.keys.map((k) => String(F.f[k] || '').trim()).filter(Boolean).join(' · ') || 'Skipped',
        reply: st.reply ? st.reply(F.f) : ''
      })),
      vhcAsking: !!cur, vhcConfirm: !cur,
      vhcQ: cur ? cur.q : '',
      vhcStep: cur ? 'Question ' + (step + 1) + ' of ' + STEPS.length : 'Ready to hoist',
      vhcKey: 'vh-' + step,
      vhcFields: cur ? cur.keys.filter((k) => !(cur.chips && k === 'areas' && names.length)).map((k) => ({
        key: k, label: FIELD_LABEL[k], value: F.f[k] || '', set: setF(k), ph: PH[k] || '',
        auto: F.prefilled.indexOf(k) > -1 && !!F.f[k],
        tag: F.prefilled.indexOf(k) > -1 && F.f[k] ? 'from certificates' : ''
      })) : [],
      vhcChipsShow: !!(cur && cur.chips && names.length),
      vhcAreaQ: s.vhAreaQ || '',
      vhcAreaSet: (e) => this.setState({ vhAreaQ: e.target.value }),
      vhcAreaNote: cur && cur.chips && names.length
        ? (match.length > 14 ? 'Showing 14 of ' + match.length + ' — type to narrow' : match.length ? '' : 'No building matches that.')
        : '',
      vhcPicked: picked.length ? picked.length + (picked.length === 1 ? ' building' : ' buildings') + ' chosen' : '',
      vhcChips: cur && cur.chips ? chips.map((nm) => ({
        label: nm, on: picked.indexOf(nm) > -1,
        toggle: () => patchF((fl) => {
          const now = split(fl.f.areas);
          const next = now.indexOf(nm) > -1 ? now.filter((x) => x !== nm) : now.concat([nm]);
          return { f: Object.assign({}, fl.f, { areas: next.join(' · ') }) };
        })
      })) : [],
      vhcSkipShow: !!(cur && cur.skip),
      vhcNote: F.tried && missing ? 'The legal name is needed to key the record.' : '',
      vhcNext: () => { if (missing) return patchF(() => ({ tried: true })); go(step + 1); },
      vhcSkip: () => go(step + 1),
      vhcBack: () => go(step - 1),
      vhcBackShow: step > 0,
      vhcSummary: VH_FIELDS.map((k) => ({ l: k[1], v: String(F.f[k[0]] || '').trim() || '—', empty: !String(F.f[k[0]] || '').trim() }))
        .concat(canUpload ? [{ l: 'Certificates', v: files.length ? fileLine(files, F.fileTargets) : '—', empty: !files.length }] : []),
      vhcFilesShow: !!(cur && cur.files),
      vhcFileList: files.map((f, i) => ({
        name: f.name, size: f.size ? (f.size < 1048576 ? Math.max(1, Math.round(f.size / 1024)) + ' KB' : (f.size / 1048576).toFixed(1) + ' MB') : '',
        // What the file is: the vendor's own certificate, or one for a building it serves — the
        // buildings chosen at the buildings question, or every building when none was.
        target: (picked.length ? picked : names).indexOf((F.fileTargets || {})[fileKey(f)]) > -1 ? F.fileTargets[fileKey(f)] : '',
        targets: [{ value: '', label: "Vendor's own certificate" }].concat((picked.length ? picked : names).map((nm) => ({ value: nm, label: 'For ' + nm }))),
        setTarget: (e) => { if (this.state.vhSaving) return; const v = e && e.target ? e.target.value : e; patchF((fl) => ({ fileTargets: Object.assign({}, fl.fileTargets || {}, { [fileKey(f)]: v || '' }) })); },
        remove: () => { if (this.state.vhSaving) return; this._vhFiles = (this._vhFiles || []).filter((_, k) => k !== i); patchF(() => ({ files: this._vhFiles.map((x) => x.name) })); }
      })),
      vhcAddFiles: (e) => {
        if (this.state.vhSaving) return;
        const picked = Array.from((e && e.target && e.target.files) || []);
        if (e && e.target) e.target.value = '';
        if (!picked.length) return;
        const have = (this._vhFiles || []).map((x) => x.name + ':' + x.size);
        this._vhFiles = (this._vhFiles || []).concat(picked.filter((x) => have.indexOf(x.name + ':' + x.size) < 0)).slice(0, 20);
        patchF(() => ({ files: this._vhFiles.map((x) => x.name) }));
      },
      vhcName: String(F.f.name || '').trim() || 'the vendor',
      vhcSaveNote: 'Hoist vendor writes ' + (String(F.f.name || '').trim() || 'the vendor') + ' to the vendor master'
        + (nCert ? ', linked to its ' + (nCert === 1 ? 'certificate' : nCert + ' certificates') : '') + '.'
        + (contactGiven ? ' The accounts ' + contactWhat + ' saved as its contact.' : '')
        + (kept.length ? ' ' + kept.join(', ').replace(/^./, (c) => c.toUpperCase()) + (kept.length === 1 ? ' has' : ' have') + ' no field there yet — kept with this hoist in your browser.' : '')
        + (picked.length ? ' It is assigned to ' + listOf(picked) + '.' : '')
        + (files.length ? ' Then the attached ' + (files.length === 1 ? 'certificate is' : files.length + ' certificates are') + ' read and filed against '
          + (Object.values(F.fileTargets || {}).some(Boolean) ? 'the building each is for, the rest against ' : '')
          + (String(F.f.name || '').trim() || 'the vendor') + ', here in this panel.' : ''),
      vhcError: F.error || '',
      vhcSaving: !!s.vhSaving,
      vhcFinishLabel: s.vhSaving ? 'Saving…' : 'Hoist vendor',
      vhcFinish: finish
    };
  }
};
