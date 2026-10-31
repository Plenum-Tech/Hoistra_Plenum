// vendorDelete — Delete vendor, from the selected vendor's panel on the Vendors page (Hussain,
// 12 Oct 2026).
//
// Hussain's decisions, fixed: a vendor with no history is REMOVED (its row goes); a vendor with
// history is RETIRED — gone from every live list, picker, scan, score and email, its past records
// kept and reading "<name> (deleted)". Assets and open work orders pointing at it are unassigned;
// closed work keeps its history. There is no restore. Admin only, for the company in view, and
// recorded in the Vendor audit trail.
//
// The service decides what happens (svc-operations-intelligence engines/admin/vendor_delete.py,
// GET and POST /api/admin/vendors/{id}/delete), in one transaction. This dialog's job is the one
// the data reset panel has (logic/dataReset.js): make the delete impossible to do by accident and
// easy to check first.
//   - Opening it reads the plan — counted in a transaction the service rolls back — and nothing
//     is armed until that answers. Before it does, the dialog says it is reading; it never shows
//     a sample of what might change.
//   - The plan is shown as the service gave it: the mode, and every group it touches with its
//     count, the assets and open work orders by name.
//   - Delete vendor stays off until the vendor's name is typed exactly (runs of spaces aside, as
//     the service reads it). The service checks the name again, and checks that the mode the
//     admin was shown is still the mode — a plan that moved comes back with the new one, and the
//     name is typed again.
//   - A reply that lands after the dialog closed, the company changed or the person signed out
//     is dropped.
// After a delete the vendor is hidden at once (vdlGone) — before any read comes back — so a read
// that left before the delete cannot paint it back. Then every register that lists vendors is
// read again, each after the read it already has in flight: every loader's re-entry guard drops
// a call made mid-flight, and that read was issued before the delete.
//
// The methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { adminApi } from '../api/admin.js';
import { isStaleScope } from '../api/client.js';
import { canAdmin } from './auth.js';

// vdl: the open dialog, or null. vdlGone: the ids deleted from this tab, for the company in view
// (a switch forgets them). vdlSaid: the last result, for the page's screen-reader announcement —
// the toast that says it too is not announced. vdlNoDelete: the ids the service said are not on
// the company's vendor record (12 Oct 2026) — still listed by older scorecards or certificates,
// so not hidden, but not offered Delete vendor again in this tab, which would only say so again.
export const VDL_DEFAULTS = { vdl: null, vdlGone: [], vdlSaid: '', vdlNoDelete: [] };

const NAME_MAX = 300;
const REASON_MAX = 500;
// Names shown per group row; the rest are counted.
const SHOWN = 5;
// How often a re-read checks whether the read in flight has landed, and for how long.
const REREAD_MS = 120;
const REREAD_TRIES = 500;

const NUM = (n) => (typeof n === 'number' && isFinite(n) ? n.toLocaleString('en-GB') : '—');
// The service compares the typed name NFC-normalised with every run of whitespace collapsed to
// one space; the button is armed by the same rule, so what arms it is what the service accepts.
const norm = (t) => String(t || '').normalize('NFC').trim().replace(/\s+/g, ' ');
const nameKey = (s) => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();

const MODE = {
  remove: 'This vendor has no history, so it will be removed completely.',
  retire: 'This vendor has history, so it will be retired: hidden everywhere, its past records kept and marked (deleted).'
};
// The plan's groups, in the order the dialog reads them, each with a line saying what happens.
const GROUPS = [
  ['unassigned', 'Unassigned', 'These stop pointing at this vendor. Closed work keeps its history.'],
  ['closed', 'Pending decisions closed', 'Closed without a decision, so they leave the queue.'],
  ['revoked', 'Share links switched off', 'Links already sent stop working.'],
  ['ended', 'Document links ended', 'The documents stay; only their link to this vendor ends.'],
  ['removed', 'Removed with it', 'Deleted with the vendor.'],
  ['archived', 'Accreditations archived', 'Kept on record, no longer counted as current.'],
  ['kept', 'History kept', 'Stays on record, naming the vendor as deleted.'],
  ['other', 'Other records kept', 'Stays on record, naming the vendor as deleted.']
];

const ERR = {
  gone: 'This vendor is already gone.',
  // A 404 vendor_not_found: no vendors row of this company has the id — a legacy row with no
  // company, or one removed outside Hoistra — though the directory still lists it from older
  // scorecards or certificates. Not "already gone": it is still listed (12 Oct 2026).
  notHere: 'This vendor is not on your company\'s vendor record, so it cannot be deleted here. Nothing was deleted.',
  planUnread: 'What this would change could not be read. Nothing has been deleted — try again.',
  noAnswer: 'No answer came back, so it is not known whether the vendor was deleted. Try again — if it was, you will be told it is already gone.',
  // Another transaction holds the vendor — possibly this admin's own earlier delete, still
  // committing after its answer was lost — so nothing is claimed about whether it happened.
  busy: 'Something else is changing this vendor right now — it may be an earlier delete still finishing. Wait a moment and press Delete vendor again.',
  // The retry of a delete whose answer was lost finds no such vendor: a removal commits even when
  // its answer never arrives (pre-push review, 12 Oct 2026).
  likelyDone: 'This vendor is no longer on your vendor record — the earlier delete most likely went through. Check the Vendor audit trail to confirm.',
  notRecorded: 'Nothing was deleted — the change could not be recorded in the audit trail. Try again.',
  refused: 'Nothing was changed — the delete could not be completed. Try again, and contact support if it keeps happening.',
  mismatch: 'The name did not match the one on record. It has been read again — type it exactly as shown.',
  forbidden: 'Only an admin can delete a vendor. Nothing was deleted.',
  // A 404 with no reason is a service that does not have the delete yet, not a missing vendor.
  unavailable: 'Deleting a vendor is not available here yet. Nothing was deleted.',
  badId: 'This vendor cannot be deleted from here. Nothing was deleted — contact support and name the vendor.',
  other: 'The delete did not go through. Try again — if it was deleted after all, you will be told it is already gone.'
};

// An asset reads "name · code", a work order "code · title".
function itemText(it) {
  if (!it || typeof it !== 'object') return String(it || '');
  const parts = 'title' in it ? [it.code, it.title] : [it.name, it.code];
  return parts.filter(Boolean).join(' · ') || String(it.id || '');
}

// The plan's non-empty groups, each row with its count and, where the service listed them, the
// first names and how many more.
export function vdlGroups(plan) {
  const g = (plan && plan.groups) || {};
  return GROUPS.map(([key, title, note]) => {
    let src = (Array.isArray(g[key]) ? g[key] : []).filter((r) => r && Number(r.rows) > 0);
    // The service labels every reference it cannot name "Other records", one per table and
    // column: one row, counted together, as the trail's own line reads (12 Oct 2026).
    if (key === 'other' && src.length > 1) {
      src = [{ key: 'other', label: 'Other records', rows: src.reduce((t, r) => t + Number(r.rows), 0),
        items: [].concat.apply([], src.map((r) => (Array.isArray(r.items) ? r.items : []))) }];
    }
    const rows = src.map((r) => {
      const n = Number(r.rows);
      const items = (Array.isArray(r.items) ? r.items : []).map(itemText).filter(Boolean);
      const shown = items.slice(0, SHOWN);
      const rest = Math.max(n, items.length) - shown.length;
      return { key: String(r.key || r.label || ''), label: String(r.label || 'Records'), n: NUM(n), items: shown,
        more: shown.length && rest > 0 ? 'and ' + NUM(rest) + ' more' : '' };
    });
    return rows.length ? { key: key, title: title, note: note, rows: rows } : null;
  }).filter(Boolean);
}

// What moved between the plan the admin read and the one the service holds now.
function changedText(before, next) {
  const tail = ' Check it again and type the name to confirm.';
  if (before && next && before.mode !== next.mode) {
    return 'What this would change moved since you opened it: '
      + (next.mode === 'remove' ? 'it no longer has history, so it will be removed completely.' : 'it now has history, so it will be retired instead.')
      + tail;
  }
  return 'What this would change moved since you opened it.' + tail;
}

const nameOk = (d) => !!(d && d.plan && d.plan.confirm_with) && norm(d.typed) === norm(d.plan.confirm_with);

export const vendorDeleteMethods = {
  // Who is offered it: a signed-in account whose REAL role can administer (canAdmin reads
  // account.role, never the Admin/User view lens), in User view. The Vendors page is a User-view
  // report, and this is Hoist vendors' rule (logic/vendorHoist.js vhCan): an admin in User view,
  // or a superadmin viewing as a company — whose account.role stays superadmin while the view
  // lens moves. The admin view does not carry it. This only decides what the page offers: the
  // service is the gate (admin role, the company in scope) and refuses anyone else.
  vdlCan(s) { return !!s.signedIn && canAdmin(s.account) && s.role !== 'admin'; },

  // The same, for one vendor: not one the service said this company's record does not hold.
  vdlCanFor(s, id) { return this.vdlCan(s) && (s.vdlNoDelete || []).indexOf(String(id)) < 0; },

  // The person and the company a request is made for. A reply for any other is dropped.
  vdlScope(s) {
    const a = s.account || {};
    return [a.id || '', s.viewOrgId || a.organization_id || ''].join('|');
  },

  vdlLive(seq, scope) {
    const s = this.state;
    return seq === this._vdlSeq && !!s.vdl && !!s.signedIn && this.vdlScope(s) === scope;
  },

  vdlPatch(p) { this.setState((st) => (st.vdl ? { vdl: Object.assign({}, st.vdl, p) } : {})); },

  // Back to nothing open, disowning anything on the wire and any re-read still waiting. On a
  // company switch (resetLiveData) and the next sign-in: the ids deleted belong to one company.
  vdlReset() { this.setState(this.vdlResetPatch()); },

  // The same, as a patch for a caller that must change state in ONE write (sign-out, as
  // dataReset.js drResetPatch is used there).
  vdlResetPatch() {
    this._vdlSeq = (this._vdlSeq || 0) + 1;
    this._vdlEpoch = (this._vdlEpoch || 0) + 1;
    this._vdlOpener = null;
    return Object.assign({}, VDL_DEFAULTS, { vdlGone: [], vdlNoDelete: [] });
  },

  // `opener` is the control that opened it; focus goes back there when it closes.
  vdlOpen(vendorId, name, opener) {
    const s = this.state;
    if (!vendorId || !this.vdlCanFor(s, vendorId) || (s.vdl && s.vdl.phase === 'working')) return;
    this._vdlOpener = opener || null;
    this.setState({
      vdl: { id: String(vendorId), name: String(name || ''), phase: 'reading', plan: null, typed: '', reason: '', error: '', retry: '', changed: '' },
      vdlSaid: ''
    });
    return this.vdlPreview();
  },

  vdlClose() {
    const d = this.state.vdl;
    if (!d || d.phase === 'working') return;
    this._vdlSeq = (this._vdlSeq || 0) + 1;
    this.setState({ vdl: null });
  },

  vdlType(v) {
    const d = this.state.vdl;
    if (!d || d.phase === 'working') return;
    const t = v && v.target ? v.target.value : v;
    this.vdlPatch({ typed: String(t || '').slice(0, NAME_MAX) });
  },

  vdlReason(v) {
    const d = this.state.vdl;
    if (!d || d.phase === 'working') return;
    const t = v && v.target ? v.target.value : v;
    this.vdlPatch({ reason: String(t || '').slice(0, REASON_MAX) });
  },

  // Reads the plan. `keep` is what to say beside the fresh plan (why it was read again).
  async vdlPreview(keep) {
    const d = this.state.vdl;
    if (!d) return;
    const seq = (this._vdlSeq = (this._vdlSeq || 0) + 1);
    const scope = this.vdlScope(this.state);
    this.vdlPatch({ phase: 'reading', error: '', retry: '' });
    let plan = null, err = null;
    try { plan = await adminApi.vendorDeletePlan(d.id); } catch (e) { err = e; }
    // Issued for the company before a switch, or the dialog closed: the answer is nobody's now.
    if (err && isStaleScope(err)) return;
    if (!this.vdlLive(seq, scope)) return;
    if (err) return this.vdlFailed(err, 'plan');
    if (plan && plan.already) return this.vdlGoneNow(d.id);
    if (!plan || plan.ok === false || !plan.mode) return this.vdlFailed(null, 'plan');
    this.vdlPatch({ phase: 'ready', plan: plan, error: (keep && keep.error) || '', changed: (keep && keep.changed) || '', retry: '' });
  },

  async vdlConfirm() {
    const d = this.state.vdl;
    if (!d || d.phase !== 'ready' || !d.plan || !this.vdlCan(this.state) || !nameOk(d)) return;
    const seq = (this._vdlSeq = (this._vdlSeq || 0) + 1);
    const scope = this.vdlScope(this.state);
    const reason = String(d.reason || '').trim();
    // Exactly what the admin saw and typed: the name, the mode, and the reason or none.
    const body = { confirm: norm(d.typed), expect: d.plan.mode, reason: reason || null };
    this.vdlPatch({ phase: 'working', error: '', retry: '', changed: '' });
    let out = null, err = null;
    try { out = await adminApi.deleteVendor(d.id, body); } catch (e) { err = e; }
    // The company changed under the request (or the person signed out). The delete was sent; its
    // answer belongs to the dialog the switch already closed.
    if (err && isStaleScope(err)) {
      if (this.state.signedIn) this.flash('The delete was sent before the company changed; its result was not read here.');
      return;
    }
    if (!this.vdlLive(seq, scope)) return;
    if (err) return this.vdlFailed(err, 'delete');
    if (!out || out.ok === false) return this.vdlFailed(null, 'delete');
    this.vdlDone(d, out);
  },

  // Try again: the read or the delete, whichever did not answer.
  vdlRetry() {
    const d = this.state.vdl;
    if (!d || d.phase === 'working') return;
    if (d.retry === 'plan') return this.vdlPreview();
    if (d.retry === 'delete') return this.vdlConfirm();
  },

  vdlFailed(e, step) {
    const d = this.state.vdl;
    if (!d) return;
    const reason = (e && e.reason) || '';
    const status = e && e.status;
    const detail = (e && e.body && e.body.detail) || {};
    if (reason === 'vendor_not_found') {
      // A delete that was sent with no answer, now not found: that delete worked.
      if (step === 'delete' && d.sentUnanswered) {
        this.vdlGoneNow(d.id);
        return this.vdlPatch({ error: ERR.likelyDone });
      }
      return this.vdlNotHere(d.id);
    }
    if (reason === 'forbidden' || status === 403) return this.vdlPatch({ phase: 'error', error: ERR.forbidden, retry: '' });
    if (status === 404) return this.vdlPatch({ phase: 'error', error: ERR.unavailable, retry: '' });
    if (reason === 'bad_vendor_id') return this.vdlPatch({ phase: 'error', error: ERR.badId, retry: '' });
    if (step === 'plan') return this.vdlPatch({ phase: 'error', error: ERR.planUnread, retry: 'plan' });
    if (reason === 'plan_changed') {
      if (detail.plan && detail.plan.mode) {
        return this.vdlPatch({ phase: 'ready', plan: detail.plan, typed: '', changed: changedText(d.plan, detail.plan), error: '', retry: '' });
      }
      this.vdlPatch({ typed: '' });
      return this.vdlPreview({ changed: changedText(null, null) });
    }
    if (reason === 'confirm_mismatch') {
      // The plan that armed the button is wrong about the name: read it again, and the name is
      // typed again before another try.
      this.vdlPatch({ typed: '' });
      return this.vdlPreview({ error: ERR.mismatch });
    }
    if (reason === 'vendor_busy') return this.vdlPatch({ phase: 'ready', error: ERR.busy, retry: '' });
    if (reason === 'not_recorded') return this.vdlPatch({ phase: 'ready', error: ERR.notRecorded, retry: '' });
    if (reason === 'delete_refused') return this.vdlPatch({ phase: 'ready', error: ERR.refused, retry: '' });
    // No answer — the network, a timeout, or a gateway that gave up — says nothing about whether
    // the delete happened, so the dialog does not claim either. Sending it again is safe: a
    // vendor already deleted answers as such.
    if (!status || status >= 502) return this.vdlPatch({ phase: 'ready', error: ERR.noAnswer, retry: 'delete', sentUnanswered: true });
    return this.vdlPatch({ phase: 'ready', error: ERR.other, retry: 'delete' });
  },

  // The vendor was deleted already: it is hidden like any other delete, and the pages read again.
  vdlGoneNow(id) {
    this.vdlPatch({ phase: 'gone', plan: null, typed: '', error: ERR.gone, retry: '', changed: '' });
    this.setState((st) => ({ vdlGone: st.vdlGone.indexOf(id) > -1 ? st.vdlGone : st.vdlGone.concat([id]) }));
    this.vdlReread();
  },

  // Not on the company's vendor record: nothing to delete here. It is not hidden — the reads
  // decide whether it is listed — but Delete vendor is not offered for it again in this tab.
  vdlNotHere(id) {
    this.vdlPatch({ phase: 'gone', plan: null, typed: '', error: ERR.notHere, retry: '', changed: '' });
    this.setState((st) => {
      const no = st.vdlNoDelete || [];
      return { vdlNoDelete: no.indexOf(id) > -1 ? no : no.concat([id]) };
    });
    this.vdlReread();
  },

  vdlDone(d, out) {
    const s = this.state;
    const v = (out && out.vendor) || (d.plan && d.plan.vendor) || {};
    const name = v.name || d.name || 'The vendor';
    const mode = (out && out.mode) || (d.plan && d.plan.mode);
    const said = out && out.already ? name + ' was already deleted.'
      : mode === 'remove' ? name + ' deleted' : name + ' deleted — its history is kept, marked (deleted)';
    // The directory as it stood, to pick the vendor after this one when this one was on screen.
    const list = this.vpModel().vendors || [];
    const at = list.findIndex((x) => x.id === d.id);
    const selected = (list.find((x) => x.id === s.vpVendor) || list[0] || {}).id;
    const patch = {
      vdl: null, vdlSaid: said,
      vdlGone: s.vdlGone.indexOf(d.id) > -1 ? s.vdlGone : s.vdlGone.concat([d.id]),
      // Asset reads cached on the Assets page name the vendor the assets have just lost.
      asIntel: {},
      // The trail has a new entry; it reads again on its next visit.
      vaEvents: null
    };
    if (selected === d.id) {
      const next = list[at + 1] || list[at - 1] || null;
      Object.assign(patch, { vpVendor: next ? next.id : s.vpVendor, vpTab: 0, vpConfirmArmed: false, vpEditField: '', vpEditValue: '' });
    }
    if (s.vpEv && s.vpEv.vendor === d.id) patch.vpEv = null;
    // Home's vendor count and the Compliance lists read the register in hand until it is read
    // again; it drops the vendor now.
    if (s.ccLive && Array.isArray(s.ccLive.vendors)) {
      patch.ccLive = Object.assign({}, s.ccLive, { vendors: s.ccLive.vendors.filter((x) => !x || x.id !== d.id) });
    }
    this.setState(patch);
    this.flash(said);
    this.vdlMarkHoist(d.id, name);
    this.vdlReread();
  },

  // A hoist saved from this browser keeps its own record (logic/vendorHoist.js), shown as "In
  // vendor master" until the master lists it. A deleted vendor never will again, so the record
  // is marked and is not shown.
  vdlMarkHoist(id, name) {
    if (typeof this.vhWrite !== 'function' || typeof this.vhStore !== 'function') return;
    const org = this.vhOrg(this.state);
    const key = nameKey(name);
    const hit = (p) => p && (p.vendorId ? String(p.vendorId) === id : (p.saved && p.key === key));
    const mine = (this.vhStore()[org] || {}).prepared || [];
    if (!mine.some(hit)) return;
    this.vhWrite(org, (cur) => Object.assign({}, cur, {
      prepared: cur.prepared.map((p) => (hit(p) ? Object.assign({}, p, { deleted: true, deletedAt: Date.now() }) : p))
    }));
  },

  // Every register that lists the vendor, read again — each once the read it has in flight has
  // landed. A company switch or sign-in (vdlReset) stops any still waiting.
  vdlReread() {
    const epoch = this._vdlEpoch || 0;
    const after = (flag, run) => {
      let tries = 0;
      const go = () => {
        if ((this._vdlEpoch || 0) !== epoch || !this.state.signedIn) return;
        if (this[flag] && tries++ < REREAD_TRIES) { setTimeout(go, REREAD_MS); return; }
        run();
      };
      go();
    };
    after('_vpLoading', () => this.vpLoad());
    after('_vmLoading', () => { if (typeof this.vmLoad === 'function') this.vmLoad(); });
    // Its building assignments end with it (vendorBuildings.js).
    after('_vbLoading', () => { if (typeof this.vbLoad === 'function') this.vbLoad(); });
    after('_ccLoading', () => this.ccLoad());
    after('_homeLoading', () => this.homeLoad());
    after('_asCondLoading', () => this.asCondLoad());
    // Maintenance's open work orders name the vendor they have just lost (12 Oct 2026).
    after('_mxLiveLoading', () => { if (typeof this.mxLiveLoad === 'function') this.mxLiveLoad(); });
  },

  vdlVals(s) {
    const d = s.vdl;
    const show = !!d && !!s.signedIn && this.vdlCan(s);
    const out = {
      vdlShow: show, vdlSaid: s.vdlSaid || '',
      // The control that opened the dialog, for focus to return to.
      vdlOpener: () => this._vdlOpener || null,
      vdlTitle: '', vdlSub: '', vdlReading: '', vdlMode: '', vdlScope: '', vdlPermanent: '', vdlGroups: [],
      vdlConfirmWith: '', vdlTyped: '', vdlNameOk: false, vdlReasonText: '', vdlReasonLeft: '',
      vdlCanDelete: false, vdlBusy: false, vdlCanCancel: true, vdlDeleteLabel: 'Delete vendor',
      vdlDeleteShow: false, vdlCancelLabel: 'Cancel',
      vdlError: '', vdlRetryShow: false, vdlChanged: '', vdlTypeShow: false,
      vdlSetTyped: (e) => this.vdlType(e), vdlSetReason: (e) => this.vdlReason(e),
      vdlRetry: () => this.vdlRetry(), vdlClose: () => this.vdlClose(), vdlConfirm: () => this.vdlConfirm()
    };
    if (!show) return out;
    const plan = d.plan;
    const name = (plan && plan.vendor && plan.vendor.name) || d.name || 'this vendor';
    const working = d.phase === 'working';
    const ok = nameOk(d);
    return Object.assign(out, {
      vdlTitle: 'Delete ' + name + '?',
      vdlSub: plan && plan.vendor ? [plan.vendor.code, plan.vendor.trade].filter(Boolean).join(' · ') : '',
      vdlReading: d.phase === 'reading' ? 'Reading what this would change…' : '',
      vdlMode: plan ? MODE[plan.mode] || '' : '',
      // The delete is company-wide; an admin allocated to some buildings must not read "your
      // buildings" as only theirs (12 Oct 2026).
      vdlScope: plan ? 'Applies across every building in the company, including ones you do not manage.' : '',
      vdlPermanent: plan ? 'This cannot be undone.' : '',
      vdlGroups: plan ? vdlGroups(plan) : [],
      vdlConfirmWith: plan ? plan.confirm_with || '' : '',
      vdlTyped: d.typed || '', vdlNameOk: ok,
      vdlReasonText: d.reason || '',
      vdlReasonLeft: (REASON_MAX - (d.reason || '').length) + ' characters left',
      vdlCanDelete: d.phase === 'ready' && !!plan && ok,
      vdlBusy: working, vdlCanCancel: !working,
      vdlDeleteLabel: working ? 'Deleting…' : 'Delete vendor',
      // Gone, or refused for good: there is nothing to delete, so there is no Delete — only Close.
      vdlDeleteShow: d.phase !== 'gone' && d.phase !== 'error',
      vdlCancelLabel: d.phase === 'gone' || d.phase === 'error' ? 'Close' : 'Cancel',
      vdlError: d.error || '', vdlRetryShow: !!d.retry && !working, vdlChanged: d.changed || '',
      vdlTypeShow: !!plan && (d.phase === 'ready' || working)
    });
  }
};
