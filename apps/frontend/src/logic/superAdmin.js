// superAdmin — the Super Admin console: a full-screen overlay for onboarding
// companies onto the platform, separate from any single company's admin app.
// Live via superAdminLive.js (svc-operations-intelligence /api/superadmin): the seed
// rows in s.saCompanies are replaced in place once the companies read answers, and
// create/invite delegate to the API for live rows — the seed keeps its local-only
// behaviour so the demo still works offline. saLiveLoad() fires when the overlay
// opens (the account-menu item), never at app mount.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.

// A live list can be genuinely empty (a fresh platform) — the header still needs a
// subject, so an empty list renders this placeholder instead of throwing.
const NO_CO = { id: null, name: "No companies yet", cc: "—", status: "—", buildings: null, graphs: null, last: "—", udr: "—", certs: null, certCc: "—", api: "—", credits: 0, users: null, invited: false };

export const superAdminMethods = {
  saVals(s) {
    const co = s.saCompanies.find((c) => c.id === s.saSel) || s.saCompanies[0] || NO_CO;
    const maxCr = Math.max(...s.saCompanies.map((c) => c.credits || 0), 0);
    // A live row's tile numbers ride on the usage card; null = the card has not landed.
    const nn = (v) => (v === null || v === undefined ? "…" : String(v));

    return {
      saOn: s.saOn,
      saOpen: () => this.setState({ saOn: true, acctOpen: false }),
      saClose: () => this.setState({ saOn: false }),
      // Live once the companies read has answered; until then the seed is on show, and
      // the overlay says so next to the retry control.
      saLiveError: s.saLiveError
        ? (s.saLiveLoadedAt ? "Companies refresh failed — " + s.saLiveError : "Companies backend unreachable — " + s.saLiveError + ". Showing sample data.")
        : (s.saLiveCardError ? "Company usage card unavailable — " + s.saLiveCardError : ""),
      saLiveRetry: () => { if (typeof this.saLiveRetryNow === "function") this.saLiveRetryNow(); },
      saCompanies: s.saCompanies.map((c) => ({
        name: c.name, cc: c.cc, status: c.status, credits: (c.credits || 0).toLocaleString("en-GB") + " cr",
        dot: c.status === "Active" ? "var(--st-ok)" : c.status === "Onboarding" ? "var(--st-warn)" : "var(--color-neutral-500)",
        bg: s.saSel === c.id ? "var(--color-accent-900)" : "var(--color-surface)",
        edge: s.saSel === c.id ? "var(--color-accent)" : "transparent",
        pick: () => {
          this.setState({ saSel: c.id });
          // Fresh tile numbers on focus — the card is the only read that carries them.
          if (c.live && typeof this.saLiveLoadCompany === "function") this.saLiveLoadCompany(c.id);
        }
      })),
      saNewOpen: s.saNew,
      saToggleNew: () => this.setState((p) => ({ saNew: !p.saNew })),
      saName: s.saName || "", saSetName: (e) => this.setState({ saName: e.target.value }),
      saEmail: s.saEmail || "", saSetEmail: (e) => this.setState({ saEmail: e.target.value }),
      saCcs: ["UK", "US", "UAE", "Singapore"].map((cc) => ({
        label: cc, pick: () => this.setState({ saCc: cc }),
        bg: s.saCc === cc ? "var(--color-accent)" : "var(--color-surface)",
        fg: s.saCc === cc ? "var(--accent-ink)" : "var(--color-neutral-400)"
      })),
      saCreate: () => {
        if (!this.state.saName.trim()) return this.flash("Company name is required.");
        // Live once the platform has answered: the API creates the record (and invites
        // the administrator when an email was given). Offline, the demo keeps minting a
        // local row so the console still works without a backend.
        if (this.state.saLiveLoadedAt && typeof this.saLiveCreate === "function") return this.saLiveCreate();
        const email = this.state.saEmail.trim();
        const c = { id: "c" + this.state.saCompanies.length + "-" + this.state.saName, name: this.state.saName, cc: this.state.saCc, status: "Created", buildings: 0, graphs: 0, last: "—", udr: "0 MB", certs: 0, certCc: "—", api: "0", credits: 0, users: 0, invited: !!email };
        this.setState((p) => ({ saCompanies: p.saCompanies.concat(c), saSel: c.id, saNew: false, saName: "", saEmail: "" }));
        this.flash(c.invited ? "Company created — admin invitation sent to " + email : "Company created. Invite its administrator when ready.");
      },
      saCo: {
        name: co.name, cc: co.cc, status: co.status,
        dot: co.status === "Active" ? "var(--st-ok)" : co.status === "Onboarding" ? "var(--st-warn)" : "var(--color-neutral-500)",
        inviteLabel: co.invited ? "Re-send admin invitation" : "Invite company admin",
        invite: () => {
          if (!co.id) return this.flash("Create a company first.");
          // A live row re-sends to the admin_email on record; a company without one gets
          // an instruction rather than an API call (the create form is where it enters).
          if (co.live && typeof this.saLiveInvite === "function") return this.saLiveInvite(co);
          this.setState((p) => ({ saCompanies: p.saCompanies.map((x) => x.id === co.id ? { ...x, invited: true, status: x.status === "Created" ? "Onboarding" : x.status } : x) })); this.flash("Admin invitation sent for " + co.name + " — they activate, set a password and land in the company admin application.");
        }
      },
      saTiles: [
        { value: nn(co.buildings), label: "Buildings created", hint: "access boundary per user", color: "var(--color-accent)" },
        { value: nn(co.graphs), label: "Hoist graphs", hint: "one per hoisted building", color: "var(--color-accent)" },
        { value: co.last, label: "Last activity", hint: "most recent update", color: "var(--color-neutral-300)" },
        { value: co.udr, label: "UDR data", hint: "volume added", color: "var(--color-neutral-300)" },
        { value: nn(co.certs), label: "Compliance certificates", hint: co.certCc === "—" ? "none yet" : "countries: " + co.certCc, color: "var(--st-ok)" },
        { value: co.api, label: "API requests", hint: "30 days · platform usage", color: "var(--color-neutral-300)" },
        { value: (co.credits || 0).toLocaleString("en-GB"), label: "Credits consumed", hint: "this month", color: "var(--st-warn)" },
        { value: nn(co.users), label: "Users", hint: "no seat limit", color: "var(--color-neutral-300)" }
      ],
      saCredits: s.saCompanies.map((c) => ({
        name: c.name, v: (c.credits || 0).toLocaleString("en-GB"),
        bar: Math.max(2, Math.round(((c.credits || 0) / (maxCr || 1)) * 100)) + "%",
        fg: c.id === s.saSel ? "var(--color-accent)" : "var(--color-neutral-500)"
      }))
    };
  }
};
