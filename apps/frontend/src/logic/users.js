// users — Users & access (admin). The store logic over s.users: the seed rows render
// until usersLive.js (usLiveLoad) replaces them with the server's own, shaped to the
// same fields. A live row carries live: true and the SERVER id — its mutations go to
// /api/admin (usLiveInvite / usLiveToggleIngest / usLiveAllocToggle, optimistic with
// revert-on-error); a pure-seed row keeps the local-only behaviour so the demo still
// works offline. Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { AX_BUILDINGS } from '../data/hoistra-access.js';

export const usersMethods = {
  usSetU(id, fn) {
    this.setState((p) => ({ users: p.users.map((u) => (u.id === id ? fn(u) : u)) }));
  },

  usersVals(s) {
    // The tiles prefer the server's summary (GET /api/admin/users, kept in s.usSummary)
    // only for figures the rows cannot derive. The mutable figures move on every
    // optimistic toggle/invite while the summary only moves on a re-read — once live
    // they count from the rows, so the tiles never contradict the toggles beneath them.
    const sum = s.usSummary || null;
    const nOf = (v, fb) => (typeof v === "number" ? v : fb);
    const live = !!s.usLiveLoadedAt;
    const total = nOf(sum && sum.users, s.users.length);
    const withIngest = live ? s.users.filter((u) => u.ingest).length : nOf(sum && sum.can_ingest, s.users.filter((u) => u.ingest).length);
    const pend = live ? s.users.filter((u) => u.status === "Invited").length : nOf(sum && sum.pending_invites, s.users.filter((u) => u.status === "Invited").length);
    // The allocation chips: the company's own buildings (usersLive's axBldsLive) once
    // GET /api/admin/buildings answered, the seed names until then.
    const bldNames = s.axBldsLive && s.axBldsLive.length ? s.axBldsLive.map((b) => b.name) : AX_BUILDINGS;

    return {
      isUsers: s.signedIn && s.view === "users",
      usTiles: [
        { value: String(total), label: "Users", hint: "no seat limit", color: "var(--color-accent)" },
        { value: String(withIngest), label: "Can ingest data", hint: "granted per user", color: "var(--st-ok)" },
        { value: String(pend), label: "Pending invites", hint: "awaiting activation", color: "var(--st-warn)" },
        { value: "Building", label: "Access boundary", hint: "every query filtered by it", color: "var(--color-neutral-300)" }
      ],
      // The live/seed source pill next to the table, with the loader's manual retry.
      usLiveSourceLabel: s.usLiveLoading ? "Reading /api/admin/users…"
        : s.usLiveError ? "Unreachable — " + s.usLiveError
        : live ? "Live · svc-operations-intelligence"
        : "Sample data — backend not read yet",
      usLiveSourceDot: s.usLiveError ? "var(--st-risk)" : live ? "var(--st-ok)" : "var(--color-neutral-600)",
      usLiveRetryShow: !s.usLiveLoading && (!live || !!s.usLiveError) && typeof this.usLiveRetryNow === "function" ? "inline" : "none",
      usLiveRetry: () => (typeof this.usLiveRetryNow === "function" ? this.usLiveRetryNow() : null),
      usInviteOpen: s.usInviteOpen,
      usToggleInvite: () => this.setState((p) => ({ usInviteOpen: !p.usInviteOpen })),
      usName: s.usName || "", usSetName: (e) => this.setState({ usName: e.target.value }),
      usEmail: s.usEmail || "", usSetEmail: (e) => this.setState({ usEmail: e.target.value }),
      usFormBlds: bldNames.map((b) => ({
        name: b, pick: () => this.setState((p) => ({ usBlds: p.usBlds.includes(b) ? p.usBlds.filter((x) => x !== b) : p.usBlds.concat(b) })),
        bg: s.usBlds.includes(b) ? "var(--color-accent-900)" : "var(--color-surface)",
        fg: s.usBlds.includes(b) ? "var(--color-accent)" : "var(--color-neutral-400)",
        edge: s.usBlds.includes(b) ? "var(--color-accent)" : "var(--color-divider)"
      })),
      usIngestOn: s.usIngest,
      usToggleIngestForm: () => this.setState((p) => ({ usIngest: !p.usIngest })),
      usFormTgBg: s.usIngest ? "var(--color-accent)" : "var(--color-divider)",
      usFormTgLeft: s.usIngest ? "17px" : "3px",
      usFormTgLabel: s.usIngest ? "Yes — can ingest into assigned buildings" : "No — read-only on assigned buildings",
      usSend: () => {
        if (!this.state.usName.trim() || !this.state.usEmail.trim() || !this.state.usBlds.length) return this.flash("Name, email and at least one building are required.");
        // Live page → the real invitation (usersLive.js appends the server's row);
        // until then the local demo row keeps the flow working offline.
        if (this.state.usLiveLoadedAt && typeof this.usLiveInvite === "function") return this.usLiveInvite();
        const u = { id: "u" + this.state.users.length + "-" + this.state.usEmail, name: this.state.usName, email: this.state.usEmail, title: "Invited user", buildings: this.state.usBlds.slice(), ingest: this.state.usIngest, status: "Invited", queries: 0, ingests: 0, last: "—" };
        this.setState((p) => ({ users: p.users.concat(u), usInviteOpen: false, usName: "", usEmail: "", usBlds: [], usIngest: false }));
        this.flash("Invitation sent to " + u.email + " — they activate the account and set a password from the email.");
      },
      axUsers: s.users.map((u) => ({
        name: u.name, email: u.email, title: u.title,
        // An admin row (live) carries buildings [] BY DESIGN — all_buildings rides along
        // so it does not render as "0 buildings" against the person who has all of them.
        nB: u.allB ? "All buildings" : u.buildings.length + (u.buildings.length === 1 ? " building" : " buildings"),
        blds: u.allB && !u.buildings.length ? "Administers every building in the company" : u.buildings.join(", "),
        usage: u.queries + " queries · " + u.ingests + " ingests", last: u.last,
        // Active is the only ok pill — Invited and Suspended (live rows) share the warn pair.
        status: u.status, stFg: u.status === "Active" ? "var(--st-ok)" : "var(--st-warn)", stBg: u.status === "Active" ? "var(--st-ok-bg)" : "var(--st-warn-bg)",
        ingLabel: u.ingest ? "Yes" : "No", ingFg: u.ingest ? "var(--st-ok)" : "var(--color-neutral-500)",
        tgBg: u.ingest ? "var(--color-accent)" : "var(--color-divider)", tgLeft: u.ingest ? "17px" : "3px",
        toggleIngest: (e) => {
          if (e && e.stopPropagation) e.stopPropagation();
          // An admin's ingest right is not a grant — the server forces it true across the
          // company, so a PATCH would "succeed" and silently revert on the next read.
          if (u.live && u.allB) return this.flash(u.name + " is an admin — admins can always ingest, across every building.");
          if (u.live && typeof this.usLiveToggleIngest === "function") return this.usLiveToggleIngest(u);
          this.usSetU(u.id, (x) => ({ ...x, ingest: !x.ingest }));
          this.flash(u.name + (u.ingest ? " can no longer ingest data." : " can now ingest data into their assigned buildings."));
        },
        open: s.usOpen === u.id, arrow: s.usOpen === u.id ? "▾" : "▸", panelShow: s.usOpen === u.id ? "flex" : "none",
        toggle: () => this.setState((p) => ({ usOpen: p.usOpen === u.id ? null : u.id })),
        alloc: bldNames.map((b) => ({
          name: b,
          // An admin is not allocated — a building_ids PATCH would silently revert too.
          pick: () => (u.live && u.allB
            ? this.flash(u.name + " is an admin — admins are not allocated; they see every building.")
            : u.live && typeof this.usLiveAllocToggle === "function"
              ? this.usLiveAllocToggle(u, b)
              : this.usSetU(u.id, (x) => ({ ...x, buildings: x.buildings.includes(b) ? x.buildings.filter((y) => y !== b) : x.buildings.concat(b) }))),
          bg: u.buildings.includes(b) ? "var(--color-accent-900)" : "transparent",
          fg: u.buildings.includes(b) ? "var(--color-accent)" : "var(--color-neutral-400)",
          edge: u.buildings.includes(b) ? "var(--color-accent)" : "var(--color-divider)",
          tick: u.buildings.includes(b) ? "ph-check-square" : "ph-square"
        }))
      }))
    };
  }
};
