// usersLive — Users & access read from svc-operations-intelligence's /api/admin.
//
// Replace-in-place: when the read answers, the seed rows in s.users are REPLACED with
// live rows shaped to exactly the seed's shape, so every downstream consumer (tiles,
// the navigator badge, the allocation panel) keeps working unchanged. The server's
// summary object lands in s.usSummary and drives the header tiles; the company's
// buildings land in s.axBldsLive = [{id, name, building_code}] — the canonical
// name↔id list every admin domain resolves against. Until the endpoint answers, the
// seed stays (vendors-style fallback) and the failure is surfaced in usLiveError.
//
//   Users + summary    GET  /api/admin/users
//   Buildings (chips)  GET  /api/admin/buildings
//   Invite             POST /api/admin/users/invite            (usLiveInvite, from usSend)
//   Can-ingest         PATCH /api/admin/users/{id} {can_ingest}
//   Allocation         PATCH /api/admin/users/{id} {building_ids}  — FULL replacement
//
// Mutations are optimistic: the row flips first, the PATCH follows, a failure reverts
// and flashes the ApiError message. Only rows the server shaped (live: true) reach the
// API — pure-seed rows keep their local-only behaviour so the demo still works offline.
// Clicks are latest-wins AND sends are serialised per row and kind (the server applies
// full-replacement PATCHes in arrival order, so dispatch order must be click order):
// every allocation click sends the complete list it produced, a stale settle only
// updates the acknowledgement ledger, and only the newest failure reverts — to the last
// value the server acknowledged, never a half-applied one.
//
// shapeLiveUser() is a pure function; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { adminApi } from '../api/admin.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;
// An invite is answered optimistically; the re-read shortly after swaps the appended
// row for the server's own (invited_at, usage counters, canonical building names).
const REFRESH_AFTER_WRITE_MS = 1500;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad2 = (n) => String(n).padStart(2, "0");
const numOf = (v) => (typeof v === "number" && isFinite(v) ? v : null);

// usage.last_active → the seed's vocabulary: "just now", "12 min ago", "1 hour ago"
// (same day), "Yesterday", a weekday inside the week ("Tue"), then "28 Aug 14:47".
export function lastActiveLabel(iso, now) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const n = new Date(now === undefined ? Date.now() : now);
  const s = Math.max(0, Math.floor((n - d) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return m + " min ago";
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, n)) { const h = Math.floor(m / 60); return h + (h === 1 ? " hour ago" : " hours ago"); }
  const y = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1);
  if (same(d, y)) return "Yesterday";
  if (n - d < 6 * 24 * 3600 * 1000) return DAYS[d.getDay()];
  return pad2(d.getDate()) + " " + MONTHS[d.getMonth()] + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
}

// The API's lowercase status → the seed's pill vocabulary. Suspended rides the same
// warn pill Invited does (users.js colours everything that is not Active as warn).
const STATUS = { active: "Active", invited: "Invited", suspended: "Suspended" };
export function statusLabel(raw) {
  const s = String(raw || "").toLowerCase();
  return STATUS[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : "Active");
}

// A GET /api/admin/users row → exactly the seed shape users.js renders, plus live: true
// so mutations know the id is the server's. Building names come off the row itself
// ({id, name} pairs); `bldNameById` (from GET /api/admin/buildings) fills any entry
// that arrives id-only. An admin's buildings are [] by design — admins are not allocated.
export function shapeLiveUser(u, bldNameById, now) {
  const usage = u.usage || {};
  return {
    id: String(u.id),
    name: u.full_name || u.email || "—",
    email: u.email || "",
    title: u.job_title || "",
    buildings: (u.buildings || []).map((b) => b.name || (bldNameById && bldNameById[String(b.id)]) || null).filter(Boolean),
    // The server sends buildings [] for an admin WITH all_buildings true and
    // building_count = the whole company, precisely so the table does not render
    // "0 buildings" against the person who administers all of them — carry both through.
    allB: !!u.all_buildings,
    buildingCount: numOf(u.building_count) !== null ? u.building_count : (u.buildings || []).length,
    ingest: !!u.can_ingest,
    status: statusLabel(u.status),
    queries: numOf(usage.queries) !== null ? usage.queries : 0,
    ingests: numOf(usage.ingests) !== null ? usage.ingests : 0,
    last: lastActiveLabel(usage.last_active, now),
    live: true
  };
}

// ── controller methods ──────────────────────────────────────────────────
export const usersLiveMethods = {
  // A building's name as the chips show it → the id the API keys by: the live admin
  // list first, then the buildings register (bldIdFor), then null — an unresolved name
  // is dropped rather than guessed, because the endpoint rejects an unknown id.
  usBldIdOf(name) {
    const want = String(name || "").trim().toLowerCase();
    const hit = (this.state.axBldsLive || []).find((b) => String(b.name || "").trim().toLowerCase() === want);
    if (hit && hit.id) return String(hit.id);
    return (typeof this.bldIdFor === "function" && this.bldIdFor(name)) || null;
  },

  // One in-flight ledger per row and mutation kind: seq is the newest click, done the
  // newest settled one, ack the newest call the server accepted, base the value that
  // acknowledgement carried — the one a newest-failure reverts to — and chain the
  // serialised dispatch queue, so one PATCH is in flight per row and kind at a time.
  usPendOf(id, kind) {
    const m = this._usPend || (this._usPend = {});
    const k = id + ":" + kind;
    return m[k] || (m[k] = { seq: 0, done: 0, ack: 0, base: null, chain: null });
  },

  // The two reads are independent: the buildings list is worth keeping even when the
  // users read failed, and vice versa. The page is live once the users read answers.
  async usLiveLoad(opts) {
    if (this._usLiveLoading) return;
    this._usLiveLoading = true;
    clearTimeout(this._usLiveRetry);
    this.setState({ usLiveLoading: true });
    const [users, blds] = await Promise.allSettled([adminApi.listUsers(), adminApi.listBuildings()]);
    this._usLiveLoading = false;
    const patch = { usLiveLoading: false };
    // apiFetch returns null for an empty 200 — guard before reading .buildings/.users.
    if (blds.status === "fulfilled" && blds.value && Array.isArray(blds.value.buildings)) patch.axBldsLive = blds.value.buildings;
    if (users.status === "fulfilled" && users.value && Array.isArray(users.value.users)) {
      const byId = {};
      (patch.axBldsLive || this.state.axBldsLive || []).forEach((b) => { byId[String(b.id)] = b.name; });
      patch.users = users.value.users.map((u) => shapeLiveUser(u, byId));
      patch.usSummary = users.value.summary || null;
      patch.usLiveError = "";
      patch.usLiveLoadedAt = new Date().toISOString();
      this._usLiveAttempts = 0;
      this.setState(patch);
      // The audit trail resolves suggested-building ids against axBldsLive — fill in any
      // rows it shaped before the register landed (the mount fires both loads at once).
      if (patch.axBldsLive && typeof this.auLiveReshape === "function") this.auLiveReshape();
      // A buildings read failing ALONE must not strand the chips — and the alloc/invite
      // name→id round-trips behind them — on the seed names for the whole session.
      // Retry on the shared timer; the concurrency guard makes the re-run cheap.
      if (blds.status === "rejected" && !this.state.axBldsLive && !(blds.reason && blds.reason.status === 403)) {
        this._usBldsAttempts = (this._usBldsAttempts || 0) + 1;
        if (this._usBldsAttempts < RETRY_MAX) this._usLiveRetry = setTimeout(() => this.usLiveLoad(), RETRY_MS);
      } else if (blds.status === "fulfilled") this._usBldsAttempts = 0;
      if (opts && opts.announce) this.flash("Users refreshed — " + patch.users.length + " accounts.");
      return;
    }
    const err = users.status === "rejected" ? users.reason : null;
    const msg = (err && err.message) || "no rows in the reply";
    this._usLiveAttempts = (this._usLiveAttempts || 0) + 1;
    patch.usLiveError = msg;
    this.setState(patch);
    if (patch.axBldsLive && typeof this.auLiveReshape === "function") this.auLiveReshape();
    // A 403 is the caller's role, not the backend's health — retrying cannot change it.
    if (!(err && err.status === 403) && this._usLiveAttempts < RETRY_MAX) this._usLiveRetry = setTimeout(() => this.usLiveLoad(), RETRY_MS);
    if (opts && opts.announce) this.flash("Users & access backend unreachable — " + msg);
  },
  usLiveRetryNow() { this._usLiveAttempts = 0; return this.usLiveLoad({ announce: true }); },

  // The live invite behind usSend() — the form is already validated there. The row is
  // appended under the SERVER's user_id, then the trail re-reads shortly after so the
  // optimistic row is swapped for the server's own.
  async usLiveInvite() {
    if (this._usInviteBusy) return;
    this._usInviteBusy = true;
    const s = this.state;
    const names = s.usBlds.slice();
    const body = {
      full_name: s.usName.trim(),
      email: s.usEmail.trim(),
      building_ids: names.map((n) => this.usBldIdOf(n)).filter(Boolean),
      can_ingest: !!s.usIngest
    };
    // The API accepts building_ids: [] — a picked name that cannot resolve to a live id
    // would be silently dropped, inviting a user who "will see no data" while the row
    // shows buildings the server never allocated. Refuse the lossy invite instead.
    if (body.building_ids.length !== names.length) {
      this._usInviteBusy = false;
      return this.flash("Invitation not sent — the picked buildings cannot be resolved to live ids yet. Retry once the buildings list has loaded.");
    }
    try {
      const r = await adminApi.inviteUser(body);
      const u = {
        id: String(r.user_id), name: body.full_name, email: r.email || body.email, title: "Invited user",
        // Only server-confirmed buildings — never the picked names the server may not hold.
        buildings: (r.buildings || []).map((b) => b.name),
        ingest: !!r.can_ingest, status: "Invited", queries: 0, ingests: 0, last: "—", live: true
      };
      this.setState((p) => ({ users: p.users.concat(u), usInviteOpen: false, usName: "", usEmail: "", usBlds: [], usIngest: false }));
      // accept_url only rides back when the email was not delivered (dry-run/undelivered)
      // — then the link is the only way in, so the admin is told to share it.
      this.flash(r.accept_url
        ? "Invitation created for " + u.email + " — the email was not delivered. Share the activation link: " + r.accept_url
        : "Invitation sent to " + u.email + " — they activate the account and set a password from the email.");
      clearTimeout(this._usLiveRefresh);
      this._usLiveRefresh = setTimeout(() => this.usLiveLoad(), REFRESH_AFTER_WRITE_MS);
    } catch (e) {
      this.flash("Invitation failed — " + ((e && e.message) || String(e)));
    } finally {
      this._usInviteBusy = false;
    }
  },

  // Optimistic flip; the newest settled call decides, a stale one is ignored, and a
  // newest failure reverts to the last value the server ACKNOWLEDGED — a blind re-flip
  // can disagree with the server after a burst where an intermediate call succeeded.
  usLiveToggleIngest(u) {
    const row = this.state.users.find((x) => x.id === u.id) || u;
    const next = !row.ingest;
    const pend = this.usPendOf(u.id, "ingest");
    if (pend.seq === pend.done || pend.base === null) pend.base = row.ingest;
    this.usSetU(u.id, (x) => ({ ...x, ingest: next }));
    this.flash(u.name + (next ? " can now ingest data into their assigned buildings." : " can no longer ingest data."));
    const seq = ++pend.seq;
    const send = () => adminApi.patchUser(u.id, { can_ingest: next }).then(() => {
      if (seq > pend.ack) { pend.ack = seq; pend.base = next; }
      if (seq === pend.seq) pend.done = seq;
    }).catch((e) => {
      if (seq !== pend.seq) return;
      pend.done = seq;
      this.usSetU(u.id, (x) => ({ ...x, ingest: pend.base }));
      this.flash("Could not change ingest for " + u.name + " — " + ((e && e.message) || String(e)));
    });
    // Serialised: the server applies PATCHes in ARRIVAL order, so two in flight could
    // land older-last and silently hold the stale value while the UI shows the new one.
    return (pend.chain = (pend.chain || Promise.resolve()).then(send, send));
  },

  // Allocation chips toggle instantly with no save button, so clicks can outrun the
  // network. building_ids is a FULL replacement, so every click sends the complete
  // list its toggle produced; only the newest call's result is applied — a stale
  // success or failure changes nothing, and the newest failure reverts to the last
  // list the server acknowledged (the state before the burst began).
  usLiveAllocToggle(u, name) {
    const row = this.state.users.find((x) => x.id === u.id) || u;
    const pend = this.usPendOf(u.id, "alloc");
    if (pend.seq === pend.done || !pend.base) pend.base = row.buildings.slice();
    const next = row.buildings.includes(name) ? row.buildings.filter((x) => x !== name) : row.buildings.concat(name);
    this.usSetU(u.id, (x) => ({ ...x, buildings: next }));
    const ids = next.map((n) => this.usBldIdOf(n)).filter(Boolean);
    // FULL replacement cuts both ways: an allocated name that cannot resolve to a live
    // id would be silently DROPPED from the list, and the PATCH would permanently wipe
    // that real allocation on one chip click. Abort and revert instead of sending less.
    if (ids.length !== next.length) {
      const back = pend.base.slice();
      this.usSetU(u.id, (x) => ({ ...x, buildings: back }));
      return this.flash("Allocation not saved for " + u.name + " — some buildings cannot be resolved to live ids yet.");
    }
    const seq = ++pend.seq;
    const send = () => adminApi.patchUser(u.id, { building_ids: ids }).then(() => {
      // Any success is an acknowledgement — record the newest one even while a newer
      // click is in flight, so a later failure reverts to what the server now holds.
      if (seq > pend.ack) { pend.ack = seq; pend.base = next.slice(); }
      if (seq === pend.seq) pend.done = seq;
    }).catch((e) => {
      if (seq !== pend.seq) return;
      pend.done = seq;
      const back = pend.base.slice();
      this.usSetU(u.id, (x) => ({ ...x, buildings: back }));
      this.flash("Allocation not saved for " + u.name + " — " + ((e && e.message) || String(e)));
    });
    // Serialised: the server applies full-replacement lists in ARRIVAL order, so a stale
    // list landing last would silently clobber the newer one there while the ledger acks
    // the newest here. One PATCH in flight per row makes dispatch order arrival order.
    return (pend.chain = (pend.chain || Promise.resolve()).then(send, send));
  }
};
