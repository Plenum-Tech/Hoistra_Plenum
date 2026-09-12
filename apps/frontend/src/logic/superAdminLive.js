// superAdminLive — the Super Admin console read from svc-operations-intelligence's
// /api/superadmin (api/superAdmin.js), superadmin role required.
//
// Replace-in-place: when the companies read answers, the seed rows in s.saCompanies are
// REPLACED with live rows shaped to exactly the seed's shape, so saVals' list, tiles and
// credit bars render either without knowing which. The list route is deliberately thin —
// the per-company tile numbers (buildings, UDR bytes, certificates, API requests, users)
// live on the usage card, GET /companies/{id}, fetched for the selected company on open
// and on every pick and merged into its row. A live row whose card has not answered yet
// carries null tile numbers (saVals shows "…" for those). Until the companies read
// answers, the seed stays (vendors-style fallback) and the failure is surfaced in
// saLiveError; the credits read rides along for fresher per-company figures but cannot
// build rows on its own.
//
//   Companies + credits  GET  /api/superadmin/companies · /api/superadmin/credits
//   Usage card           GET  /api/superadmin/companies/{id}          (saLiveLoadCompany)
//   Create               POST /api/superadmin/companies               (saLiveCreate, from saCreate)
//   Invite admin         POST /api/superadmin/companies/{id}/invite-admin  (saLiveInvite)
//
// The overlay opens from the account menu, so NOTHING here loads at app mount —
// saLiveLoad() is the on-open hook, fired when saOn flips true.
//
// The invite route needs an email and the console has no input for a re-send: it uses the
// admin_email already on the company record (the card's company object — the list route
// does not carry it). A company created without one gets an instruction, not an API call.
//
// shapeLiveCompany() and the formatters are pure; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { superAdminApi } from '../api/superAdmin.js';
import { lastActiveLabel } from './usersLive.js';

export { lastActiveLabel };

const RETRY_MS = 30000;
const RETRY_MAX = 6;
// A create is answered optimistically; the re-read shortly after swaps the appended row
// for the server's own (canonical ordering, server-side credits, the fresh usage card).
const REFRESH_AFTER_WRITE_MS = 1500;

// Canonical wire codes → the seed's display vocabulary. An unknown code passes through
// as itself (the API accepts any bare 2–3-letter uppercase code), null reads as "—".
const CC_DISPLAY = { UK: "UK", US: "US", AE: "UAE", SG: "Singapore" };
export function ccLabel(code) {
  const c = String(code || "").toUpperCase();
  return c ? CC_DISPLAY[c] || c : "—";
}

// The create form's chips ("UAE", "Singapore") → the API's canonical vocabulary
// (UK | US | AE | SG). The server resolves aliases anyway; the contract's codes are sent.
export const CC_API = { UK: "UK", US: "US", UAE: "AE", Singapore: "SG" };

// lowercase lifecycle on the wire → the seed's status vocabulary. Unknown future tokens
// capitalise rather than throw.
const LIFECYCLE = { created: "Created", onboarding: "Onboarding", active: "Active" };
export function lifecycleLabel(raw) {
  const s = String(raw || "").toLowerCase();
  return LIFECYCLE[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : "Created");
}

// udr_data_bytes (raw int) → the seed's "0 MB" / "90 MB" / "4.9 GB" style. MB below a
// gigabyte, one-decimal GB above it — the seed has no KB tier, so anything under a
// megabyte reads as 1 MB rather than inventing a finer unit.
export function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  const b = Number(n);
  if (!isFinite(b) || b < 0) return "—";
  if (b === 0) return "0 MB";
  const gb = b / 1e9;
  if (gb >= 1000) return (gb / 1000).toFixed(1).replace(/\.0$/, "") + " TB";
  if (gb >= 1) return (gb >= 100 ? String(Math.round(gb)) : gb.toFixed(1).replace(/\.0$/, "")) + " GB";
  return Math.max(1, Math.round(b / 1e6)) + " MB";
}

// api_requests_30d (raw int) → the seed's "0" / "9k" / "112k" / "1.21M" style.
export function fmtCount(n) {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (!isFinite(v) || v < 0) return "—";
  if (v < 1000) return String(Math.round(v));
  if (v < 999500) return Math.round(v / 1000) + "k";
  const m = v / 1e6;
  return (m >= 100 ? String(Math.round(m)) : m.toFixed(m >= 10 ? 1 : 2).replace(/\.?0+$/, "")) + "M";
}

const intOf = (v) => (typeof v === "number" && isFinite(v) ? v : 0);

// A GET /api/superadmin/companies row (+ its usage card, when loaded) → exactly the seed
// shape superAdmin.js renders, plus live: true so mutations know the id is the server's.
// The list route carries no tile numbers: without a card those are null (numbers) and "—"
// (pre-formatted strings), and saVals renders the nulls as "…" until the card lands.
// With a card, everything derives from the card — it is the fresher read.
export function shapeLiveCompany(c, card, now) {
  const row = c || {};
  const co = (card && card.company) || {};
  const lifecycle = String((card ? co.lifecycle : row.lifecycle) || "").toLowerCase();
  const creditsRaw = card ? card.credits_this_month : row.credits_this_month;
  return {
    id: String((card && co.id) || row.organization_id || ""),
    name: (card && co.name) || row.name || "—",
    cc: ccLabel(card ? co.country_code : row.country_code),
    status: lifecycleLabel(lifecycle),
    buildings: card ? intOf(card.buildings_created) : null,
    graphs: card ? intOf(card.hoist_graphs) : null,
    last: lastActiveLabel(card ? card.last_activity : row.last_activity, now),
    udr: card ? fmtBytes(card.udr_data_bytes) : "—",
    certs: card ? intOf(card.compliance_certificates) : null,
    certCc: card ? (card.certificate_countries || []).map(ccLabel).join(", ") || "—" : "—",
    api: card ? fmtCount(card.api_requests_30d) : "—",
    credits: Math.round(Number(creditsRaw) || 0),
    users: card ? intOf(card.users && card.users.total) : null,
    // The list route carries no admin_email; lifecycle is the proxy there — invite-admin
    // (and createCompany with an admin_email) is exactly what bumps created→onboarding.
    invited: card ? !!co.admin_email || intOf(card.pending_invitations) > 0 : lifecycle !== "created" && lifecycle !== "",
    live: true
  };
}

// ── controller methods ──────────────────────────────────────────────────
export const superAdminLiveMethods = {
  // The two reads are independent: the credits read cannot build rows on its own, but its
  // per-company figures are worth folding in when the list read also answered. The console
  // is live once the companies read answers.
  async saLiveLoad(opts) {
    if (this._saLiveLoading) return;
    this._saLiveLoading = true;
    clearTimeout(this._saLiveRetry);
    this.setState({ saLiveLoading: true });
    const [companies, credits] = await Promise.allSettled([superAdminApi.listCompanies(), superAdminApi.credits()]);
    this._saLiveLoading = false;
    // apiFetch returns null for an empty 200 — guard before reading .companies.
    const list = companies.status === "fulfilled" && companies.value && Array.isArray(companies.value.companies) ? companies.value.companies : null;
    if (list) {
      const now = new Date();
      const cards = this.state.saCardsLive || {};
      const crOf = {};
      if (credits.status === "fulfilled" && credits.value && Array.isArray(credits.value.companies)) {
        credits.value.companies.forEach((r) => { crOf[String(r.organization_id)] = r.credits_this_month; });
      }
      const rows = list.map((c) => {
        const id = String(c.organization_id);
        const row = shapeLiveCompany(c, cards[id] || null, now);
        if (!cards[id] && crOf[id] !== undefined) row.credits = Math.round(Number(crOf[id]) || 0);
        return row;
      });
      const patch = {
        saCompanies: rows,
        saLiveRaw: { companies: companies.value, credits: credits.status === "fulfilled" ? credits.value : null },
        saLiveLoading: false, saLiveError: "", saLiveLoadedAt: now.toISOString()
      };
      // The seed's saSel ("c1") matches no live org id — point the selection at the top
      // row (credits DESC, so the busiest company) so the header and tiles have a subject.
      if (!rows.some((r) => r.id === this.state.saSel)) patch.saSel = rows.length ? rows[0].id : null;
      this._saLiveAttempts = 0;
      this.setState(patch);
      // Fresh tile numbers for whichever company the overlay is showing.
      if (this.state.saSel) this.saLiveLoadCompany(this.state.saSel);
      if (opts && opts.announce) this.flash("Companies refreshed — " + rows.length + " on the platform.");
      return;
    }
    const err = companies.status === "rejected" ? companies.reason : null;
    const msg = (err && err.message) || "no companies in the reply";
    this._saLiveAttempts = (this._saLiveAttempts || 0) + 1;
    this.setState({ saLiveLoading: false, saLiveError: msg });
    // A 403 is the caller's role, not the backend's health — a timer will not change it;
    // saLiveRetryNow still works should the account be promoted mid-session.
    if (!(err && err.status === 403) && this._saLiveAttempts < RETRY_MAX) this._saLiveRetry = setTimeout(() => this.saLiveLoad(), RETRY_MS);
    if (opts && opts.announce) this.flash("Companies backend unreachable — " + msg);
  },
  saLiveRetryNow() { this._saLiveAttempts = 0; return this.saLiveLoad({ announce: true }); },

  // One company's usage card, merged into its row when it lands. Deduped per org: a call
  // while the same card is in flight returns the in-flight promise, so saLiveInvite can
  // await whatever read is already going. Resolves to the raw card, or null on failure —
  // failures land in saLiveCardError, never a thrown rejection.
  saLiveLoadCompany(orgId) {
    const id = String(orgId || "");
    if (!id) return Promise.resolve(null);
    const busy = this._saCardBusy || (this._saCardBusy = {});
    if (busy[id]) return busy[id];
    busy[id] = (async () => {
      try {
        const card = await superAdminApi.company(id);
        // An empty 200 is a malformed answer, not a card — keep the row's placeholders.
        if (!card || !card.company) throw new Error("empty response — no company on the card");
        this.setState((p) => ({
          saCardsLive: { ...(p.saCardsLive || {}), [id]: card },
          saLiveCardError: "",
          saCompanies: p.saCompanies.map((c) => (c.id === id && c.live ? shapeLiveCompany(null, card) : c))
        }));
        return card;
      } catch (e) {
        this.setState({ saLiveCardError: (e && e.message) || String(e) });
        return null;
      } finally {
        delete busy[id];
      }
    })();
    return busy[id];
  },

  // The live create behind saCreate() — the name is already validated there. The row is
  // appended under the SERVER's organization_id and selected, then the list re-reads
  // shortly after so the optimistic row is swapped for the server's own.
  async saLiveCreate() {
    if (this._saCreateBusy) return;
    this._saCreateBusy = true;
    const s = this.state;
    const email = (s.saEmail || "").trim();
    const body = {
      name: (s.saName || "").trim(),
      country_code: CC_API[s.saCc] || s.saCc,
      admin_email: email || undefined
    };
    try {
      const r = await superAdminApi.createCompany(body);
      const row = shapeLiveCompany({
        organization_id: r.organization_id, name: r.name || body.name,
        country_code: r.country_code, lifecycle: r.lifecycle,
        credits_this_month: 0, last_activity: null
      }, null);
      this.setState((p) => ({ saCompanies: p.saCompanies.concat(row), saSel: row.id, saNew: false, saName: "", saEmail: "" }));
      const inv = r.admin_invitation;
      // accept_url only rides back when the email was not delivered (dry-run/undelivered)
      // — then the link is the only way in, so the operator is told to share it.
      this.flash(!email ? "Company created. Invite its administrator when ready."
        : inv && inv.ok === false ? "Company created, but the admin invitation was refused — " + (inv.error || "unknown reason")
        : inv && inv.accept_url ? "Company created — the invitation email was not delivered. Share the activation link: " + inv.accept_url
        : "Company created — admin invitation sent to " + email);
      clearTimeout(this._saLiveRefresh);
      this._saLiveRefresh = setTimeout(() => this.saLiveLoad(), REFRESH_AFTER_WRITE_MS);
    } catch (e) {
      this.flash("Company not created — " + ((e && e.message) || String(e)));
    } finally {
      this._saCreateBusy = false;
    }
  },

  // The live invite behind saCo.invite(), for a live row only. Re-sends to the
  // admin_email on the company record; without one there is nothing to send to — the
  // create form is where an email enters the platform.
  async saLiveInvite(co) {
    if (this._saInviteBusy) return;
    this._saInviteBusy = true;
    try {
      let card = (this.state.saCardsLive || {})[co.id] || null;
      if (!card) card = await this.saLiveLoadCompany(co.id);
      // A card that could not be READ is not a company with no email on record — say
      // which happened, or the operator is told to recreate a company that may be fine.
      if (!card) {
        this.flash("Could not read the company record for " + co.name + " — " + (this.state.saLiveCardError || "backend unreachable") + ". Try again.");
        return;
      }
      const email = card.company && card.company.admin_email;
      if (!email) {
        this.flash("No administrator email on record for " + co.name + " — create the company with an administrator email to invite one.");
        return;
      }
      const r = await superAdminApi.inviteAdmin(co.id, { email });
      this.setState((p) => ({ saCompanies: p.saCompanies.map((x) => (x.id === co.id ? { ...x, invited: true, status: x.status === "Created" ? "Onboarding" : x.status } : x)) }));
      this.flash(r && r.accept_url
        ? "Admin invitation created for " + co.name + " — the email was not delivered. Share the activation link: " + r.accept_url
        : "Admin invitation sent for " + co.name + " — they activate, set a password and land in the company admin application.");
      // lifecycle and pending_invitations moved server-side — re-read the card.
      this.saLiveLoadCompany(co.id);
    } catch (e) {
      this.flash("Invitation not sent for " + co.name + " — " + ((e && e.message) || String(e)));
    } finally {
      this._saInviteBusy = false;
    }
  }
};
