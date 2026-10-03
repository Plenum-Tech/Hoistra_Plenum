// assetsVendor — the vendor drawer on the Assets page.
//
// Which vendor an asset is assigned to, and — for an admin — moving it to another of the
// company's vendors (Hussain, 28 Sep 2026: a drawer with a Change control; direct, confirmed,
// admins only). The vendor is not a label: it decides who receives the asset's work-order,
// inspection and records emails, which are sent for real, and whose scorecard its work counts
// towards. So the drawer says what moves before the change, the change waits for a
// confirmation, and a refusal (blocked, another company's, a legacy id) is shown where it
// happened. Everything shown is what GET /api/energy/assets/{id}/vendor returned; the write is
// PATCH on the same path, which records the change in audit_logs in the same transaction.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { energyApi } from '../api/energy.js';

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dateText(iso) {
  const d = iso ? new Date(String(iso).slice(0, 10) + "T00:00:00") : null;
  return d && !isNaN(d) ? d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear() : null;
}

function monthText(iso) {
  const m = /^(\d{4})-(\d{2})/.exec(String(iso || ""));
  return m ? MONTHS[Number(m[2]) - 1] + " " + m[1] : "";
}

function errorOf(e, lead) {
  const detail = e && e.body && e.body.detail;
  if (detail && typeof detail === "object" && detail.error) return String(detail.error);
  return lead + ((e && e.message) || "no response") + ".";
}

/** What the drawer renders, from the state. Pure: every line is what the read returned. */
export function vendorDrawerModel(s, handlers) {
  const h = handlers || {};
  const asset = s.vdAsset || {};
  const view = s.vd || null;
  const v = view && view.vendor;
  const choices = (view && view.choices) || [];
  const picked = choices.find((c) => c.id === s.vdPick) || null;
  const canChange = !!(view && view.can_change);
  const canAsk = canChange && !!picked && picked.assignable && !picked.current && !s.vdSaving;
  const contacts = (view && view.contacts) || {};
  const last = view && view.last_change;
  // The read lists only vendors of the current vendor's trade (Hussain, 29 Sep 2026); the
  // drawer says which trade, and says so too when there was no trade to filter by.
  const tf = (view && view.trade_filter) || null;
  const others = choices.filter((c) => !c.current).length;
  const tradeHint = !tf ? ""
    : tf.applied
      ? others
        ? "Only " + tf.trade + " vendors are listed — the same trade as " + (v ? v.name : "the current vendor") + "."
          + (tf.hidden ? " " + tf.hidden + " of the company's vendors are of another trade." : "")
        : "No other of the company's vendors is " + tf.trade + ", so there is no vendor to move this asset to."
      : v ? v.name + " has no trade on record, so every one of the company's vendors is listed."
        : "The asset has no vendor yet, so every one of the company's vendors is listed.";
  return {
    title: (asset.name || "Asset") + " · vendor",
    meta: [asset.code, asset.building].filter(Boolean).join(" · "),
    loading: !!s.vdLoading,
    loadingShow: s.vdLoading ? "block" : "none",
    bodyShow: view ? "block" : "none",
    vendorName: v ? v.name : "No vendor on the asset record",
    vendorLine: v ? [v.code, v.trade, v.accreditation].filter(Boolean).join(" · ") : "",
    blockText: !v ? "" : String(v.block_state || "Clear").toLowerCase() === "blocked"
      ? "Blocked" + (v.block_reason ? " — " + v.block_reason : "") : "Clear — not blocked",
    blockColor: v && String(v.block_state || "").toLowerCase() === "blocked" ? "var(--st-risk)" : "var(--st-ok)",
    contactLine: !v ? "" : contacts.email ? contacts.email
      : (contacts.candidates || []).length
        ? "Several contacts on record and none marked primary: " + contacts.candidates.join(", ")
        : "No address on the vendor record",
    phone: (v && v.phone) || "",
    scoreLine: !v ? "" : view.score ? view.score.score + "/100 · " + monthText(view.score.month) : "Not scored yet",
    lastChange: last
      ? "Changed from " + (last.from || "no vendor") + " to " + (last.to || "—") + (last.by ? " by " + last.by : "")
        + (dateText(last.at) ? " on " + dateText(last.at) : "") + (last.note ? " — " + last.note : "")
      : "No change recorded",
    openWorkOrders: typeof asset.openWorkOrders === "number" ? String(asset.openWorkOrders) : "—",
    unreadableNote: view && (view.unreadable || []).length ? "Could not be read just now: " + view.unreadable.join(", ") + "." : "",
    canChange: canChange,
    changeShow: canChange ? "block" : "none",
    readOnlyNote: view && !canChange ? "Only an admin can change the vendor an asset is assigned to." : "",
    tradeHint: tradeHint,
    pickPrompt: tf && tf.applied ? "Choose another " + tf.trade + " vendor…" : "Choose another of the company's vendors…",
    options: choices.map((c) => ({
      value: c.id,
      label: c.name + (c.trade ? " · " + c.trade : "") + (c.current ? " (current)" : c.why ? " — " + c.why : ""),
      disabled: !c.assignable || !!c.current
    })),
    pick: s.vdPick || "",
    pickChange: (e) => h.pick && h.pick(e && e.target ? e.target.value : ""),
    note: s.vdNote || "",
    noteChange: (e) => h.note && h.note(e && e.target ? e.target.value : ""),
    askShow: canAsk && !s.vdConfirm ? "inline-flex" : "none",
    ask: () => h.ask && h.ask(),
    confirmShow: s.vdConfirm && picked ? "block" : "none",
    confirmText: picked
      ? "Move " + (asset.name || "this asset") + " from " + (v ? v.name : "no vendor") + " to " + picked.name
        + "? Future work-order, inspection and records emails go to " + picked.name + "’s contact. "
        + "Past work orders and scores stay with " + (v ? v.name : "no vendor") + "."
      : "",
    confirm: () => h.confirm && h.confirm(),
    cancel: () => h.cancel && h.cancel(),
    saving: !!s.vdSaving,
    confirmLabel: s.vdSaving ? "Changing…" : "Confirm change",
    doneText: s.vdDone || "",
    doneShow: s.vdDone ? "block" : "none",
    errorText: s.vdError || "",
    errorShow: s.vdError ? "block" : "none",
    close: () => h.close && h.close()
  };
}

export const assetsVendorMethods = {
  // Opened from the vendor on an asset row, or from the asset drawer's Vendor action. `x` is
  // the row's own record; the drawer re-reads the vendor rather than trusting the row.
  async asVendorOpen(x) {
    const a = x.a || {};
    const token = (this._vdToken = (this._vdToken || 0) + 1);
    this.setState({
      vdOpen: true, vd: null, vdLoading: true, vdError: "", vdDone: "", vdPick: "", vdNote: "",
      vdConfirm: false, vdSaving: false, detail: null,
      vdAsset: { id: a.asset_id, name: a.asset_name, code: a.asset_code || null,
                 building: x.b ? x.b.name : null, openWorkOrders: typeof x.nWo === "number" ? x.nWo : null }
    });
    return this.asVendorRead(token, a.asset_id);
  },

  async asVendorRead(token, assetId) {
    let res = null, err = null;
    try { res = await energyApi.assetVendor(assetId); } catch (e) { err = e; }
    if (token !== this._vdToken || !this.state.vdOpen) return;
    if (err || !res || res.ok === false) {
      return this.setState({ vdLoading: false, vdError: errorOf(err, "The vendor could not be read — ") });
    }
    this.setState({ vd: res, vdLoading: false });
  },

  asVendorClose() {
    this._vdToken = (this._vdToken || 0) + 1;
    this.setState({ vdOpen: false, vd: null, vdLoading: false, vdConfirm: false, vdSaving: false, vdError: "", vdDone: "" });
  },

  asVendorPick(id) { this.setState({ vdPick: id, vdConfirm: false, vdError: "", vdDone: "" }); },
  asVendorNote(text) { this.setState({ vdNote: String(text || "").slice(0, 500) }); },
  asVendorAsk() { this.setState({ vdConfirm: true, vdError: "" }); },
  asVendorCancel() { this.setState({ vdConfirm: false }); },

  async asVendorConfirm() {
    const s = this.state;
    const asset = s.vdAsset || {};
    const picked = ((s.vd && s.vd.choices) || []).find((c) => c.id === s.vdPick);
    if (!picked || !picked.assignable || picked.current || s.vdSaving) return;
    const token = this._vdToken;
    this.setState({ vdSaving: true, vdError: "" });
    let res = null, err = null;
    try { res = await energyApi.changeAssetVendor(asset.id, picked.id, s.vdNote || null); } catch (e) { err = e; }
    if (token !== this._vdToken || !this.state.vdOpen) return;
    if (err || !res || res.ok === false) {
      return this.setState({ vdSaving: false, vdConfirm: false,
        vdError: errorOf(err || { body: { detail: res } }, "Not changed — ") });
    }
    const name = (res.vendor && res.vendor.name) || picked.name;
    // The row reads the vendor from the condition read and the per-asset read: the one is
    // patched in place, the other dropped so its next open re-reads the record.
    const bands = (this.state.asCondBands || []).map((b) => (b && String(b.asset_id) === String(asset.id)
      ? Object.assign({}, b, { vendor: name, vendor_id: picked.id }) : b));
    const intel = Object.assign({}, this.state.asIntel || {});
    delete intel[asset.id];
    // An investigation open on this asset drafts its Raise work order to the vendor it was
    // opened with: point it at the new one and drop the old contact (re-review, 29 Sep 2026).
    const inv = this.state.inv;
    if (inv && String(inv.assetId) === String(asset.id)) {
      this.setState({ inv: Object.assign({}, inv, { sub: name, vendorEmail: null, vendorEmailCandidates: [] }) });
    }
    // A details read already in flight answers for the old vendor: disown it.
    this._asIntelGen = this._asIntelGen || {};
    this._asIntelGen[asset.id] = (this._asIntelGen[asset.id] || 0) + 1;
    if (this._asIntelP) delete this._asIntelP[asset.id];
    this.setState({
      vdSaving: false, vdConfirm: false, vdPick: "", vdNote: "", asCondBands: bands, asIntel: intel,
      vdDone: res.changed === false ? (asset.name || "The asset") + " is already assigned to " + name + "."
        : (asset.name || "The asset") + " is now assigned to " + name + ". The change is in the audit log."
    });
    return this.asVendorRead(token, asset.id);
  },

  vendorDrawerVals() {
    return vendorDrawerModel(this.state, {
      pick: (id) => this.asVendorPick(id), note: (t) => this.asVendorNote(t), ask: () => this.asVendorAsk(),
      confirm: () => this.asVendorConfirm(), cancel: () => this.asVendorCancel(), close: () => this.asVendorClose()
    });
  }
};
