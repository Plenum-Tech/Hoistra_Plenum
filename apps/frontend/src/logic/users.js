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

  // Suspending (the status pill) and deleting (the trash icon) are both consequential —
  // they end someone's access — so a click arms a 4s confirm window instead of firing
  // immediately; a second click on the SAME control within that window is the confirm.
  // Reactivating a suspended row is the one direction that needs none of this: it is
  // safe and reversible, so it fires on the first click, same as the ingest toggle.
  usArm(key) {
    clearTimeout(this._usArmTimer);
    this.setState({ usArmed: key });
    this._usArmTimer = setTimeout(() => this.setState((p) => (p.usArmed === key ? { usArmed: null } : {})), 4000);
  },
  usDisarm() {
    clearTimeout(this._usArmTimer);
    this.setState({ usArmed: null });
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
    // GET /api/admin/buildings has genuinely answered, the seed names only until then.
    // axBldsLive is null before that first successful read and a real (possibly EMPTY)
    // array once it lands — checking .length instead of this collapsed the two: a company
    // with zero buildings on file read as "not loaded yet" and showed the seed's eight
    // demo names as things to allocate a user to, none of which exist on this account.
    const bldNames = Array.isArray(s.axBldsLive) ? s.axBldsLive.map((b) => b.name) : AX_BUILDINGS;
    // The server itself refuses to suspend or deactivate the caller's own row (PATCH and
    // DELETE both 400 on self) — matched here so the control never fires a doomed request
    // and reads as broken; it is simply not offered on your own row.
    const meEmail = ((s.account && s.account.email) || "").toLowerCase();
    // Nothing read yet and nothing to show: the table is empty because the answer has not
    // arrived, not because the company has no people.
    const unread = !live && !s.usLiveError && !s.users.length;

    return {
      isUsers: s.signedIn && s.view === "users",
      // "0 users" is a finding, and it is not one anybody has established while the read
      // that would establish it is still running. An unread table counts nothing, so the
      // tiles say so rather than reporting a company with no staff on it.
      usTiles: [
        { value: unread ? "…" : String(total), label: "Users", hint: "no seat limit", color: "var(--color-accent)" },
        { value: unread ? "…" : String(withIngest), label: "Can ingest data", hint: "granted per user", color: "var(--st-ok)" },
        { value: unread ? "…" : String(pend), label: "Pending invites", hint: "awaiting activation", color: "var(--st-warn)" },
        { value: "Building", label: "Access boundary", hint: "every query filtered by it", color: "var(--color-neutral-300)" }
      ],
      // The live/seed source pill next to the table, with the loader's manual retry.
      usLiveSourceLabel: s.usLiveLoading ? "Reading /api/admin/users…"
        // Samples fill only a table that was never read; after a successful read the rows
        // on screen are the company's own, and the label says only that the refresh failed.
        : s.usLiveError ? "Unreachable — " + s.usLiveError + (s.usLiveLoadedAt ? "" : " · showing sample data")
        : live ? "Live · svc-operations-intelligence"
        : unread ? "Not read yet"
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
        // A building is not required: the server itself allows building_ids: [] (a real,
        // named state — "allocated to nothing" — not an error), for exactly this case —
        // a brand-new company with nothing hoisted yet still needs to invite its first
        // admin. usLiveInvite()/the offline demo row below both already carry an empty
        // list through correctly; only this validation used to block it.
        if (!this.state.usName.trim() || !this.state.usEmail.trim()) return this.flash("Name and email are required.");
        // Live page → the real invitation (usersLive.js appends the server's row);
        // until then the local demo row keeps the flow working offline.
        if (this.state.usLiveLoadedAt && typeof this.usLiveInvite === "function") return this.usLiveInvite();
        const u = { id: "u" + this.state.users.length + "-" + this.state.usEmail, name: this.state.usName, email: this.state.usEmail, title: "Invited user", buildings: this.state.usBlds.slice(), ingest: this.state.usIngest, status: "Invited", queries: 0, ingests: 0, last: "—" };
        this.setState((p) => ({ users: p.users.concat(u), usInviteOpen: false, usName: "", usEmail: "", usBlds: [], usIngest: false }));
        this.flash(u.buildings.length
          ? "Invitation sent to " + u.email + " — they activate the account and set a password from the email."
          : "Invitation sent to " + u.email + " — no buildings allocated, so they'll see no data until some are.");
      },
      axUsers: s.users.map((u) => {
        const isMe = !!meEmail && (u.email || "").toLowerCase() === meEmail;
        const suspKey = "susp:" + u.id, delKey = "del:" + u.id;
        const suspArmed = s.usArmed === suspKey, delArmed = s.usArmed === delKey;
        return {
          name: u.name, email: u.email, title: u.title, isMe,
          // An admin row (live) carries buildings [] BY DESIGN — all_buildings rides along
          // so it does not render as "0 buildings" against the person who administers them.
          nB: u.allB ? "All buildings" : u.buildings.length + (u.buildings.length === 1 ? " building" : " buildings"),
          blds: u.allB && !u.buildings.length ? "Administers every building in the company" : u.buildings.join(", "),
          usage: u.queries + " queries · " + u.ingests + " ingests", last: u.last,
          // Active is the only ok pill — Invited and Suspended (live rows) share the warn
          // pair, except an armed confirm, which reads as risk (about to change) either way.
          status: suspArmed ? "Confirm?" : u.status,
          stFg: suspArmed ? "var(--st-risk)" : u.status === "Active" ? "var(--st-ok)" : "var(--st-warn)",
          stBg: suspArmed ? "var(--st-risk-bg)" : u.status === "Active" ? "var(--st-ok-bg)" : "var(--st-warn-bg)",
          stTitle: isMe ? "You cannot change your own status"
            : u.status === "Suspended" ? "Reactivate " + u.name
            : suspArmed ? "Click again to confirm suspending " + u.name
            : "Suspend " + u.name,
          stClick: (e) => {
            if (e && e.stopPropagation) e.stopPropagation();
            if (isMe) return this.flash("You cannot change your own status.");
            if (u.status === "Suspended") {
              // Reactivating is safe and reversible — no confirm, same as the ingest toggle.
              if (u.live && typeof this.usLiveSetStatus === "function") return this.usLiveSetStatus(u, "active");
              this.usSetU(u.id, (x) => ({ ...x, status: "Active" }));
              return this.flash(u.name + " is active again.");
            }
            if (!suspArmed) return this.usArm(suspKey);
            this.usDisarm();
            if (u.live && typeof this.usLiveSetStatus === "function") return this.usLiveSetStatus(u, "suspended");
            this.usSetU(u.id, (x) => ({ ...x, status: "Suspended" }));
            this.flash(u.name + " is suspended — nothing is deleted.");
          },
          delShow: !isMe,
          delArmed,
          delTitle: delArmed ? "Click again to confirm — suspends, does not delete" : "Suspend " + u.name + "'s access",
          delClick: (e) => {
            if (e && e.stopPropagation) e.stopPropagation();
            if (isMe) return this.flash("You cannot remove your own access.");
            if (!delArmed) return this.usArm(delKey);
            this.usDisarm();
            if (u.live && typeof this.usLiveDelete === "function") return this.usLiveDelete(u);
            this.usSetU(u.id, (x) => ({ ...x, status: "Suspended" }));
            this.flash(u.name + "'s access is suspended — their record and history stay on the account, nothing is deleted.");
          },
          ingLabel: u.ingest ? "Yes" : "No", ingFg: u.ingest ? "var(--st-ok)" : "var(--color-neutral-500)",
          tgBg: u.ingest ? "var(--color-accent)" : "var(--color-divider)", tgLeft: u.ingest ? "17px" : "3px",
          toggleIngest: (e) => {
            if (e && e.stopPropagation) e.stopPropagation();
            // An admin's ingest right is not a grant — the server forces it true across
            // the company, so a PATCH would "succeed" and silently revert on the next read.
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
        };
      })
    };
  }
};
