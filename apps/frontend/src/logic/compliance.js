// compliance — compliance console model and certificate actions.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { TAG, MK } from './constants.js';
import { relDays, countryMeta } from './complianceLive.js';

export const complianceMethods = {
  // ── Compliance console ──────────────────────────────────────────────
  ccToggle(key, val) {
    const CC = this.ccData();
    this.setState((p) => {
      const a = p[key];
      const next = a.indexOf(val) > -1 ? a.filter((v) => v !== val) : a.concat([val]);
      const out = { [key]: next };
      // prune downstream selections that fall out of the new scope
      // Regions and buildings are read off the certificate rows, so a selection is
      // pruned when no in-scope certificate carries it any more.
      const reg = (c) => String(c.state || "").trim();
      if (key === "ccCountries") {
        const rows = CC.certs.filter((c) => !next.length || next.indexOf(c.cc) > -1);
        const live = rows.map(reg).filter(Boolean);
        out.ccStates = p.ccStates.filter((x) => live.indexOf(x) > -1);
        const holders = rows.filter((c) => c.kind === "building" && (!out.ccStates.length || out.ccStates.indexOf(reg(c)) > -1)).map((c) => c.holder);
        out.ccBuildings = p.ccBuildings.filter((x) => holders.indexOf(x) > -1);
      }
      if (key === "ccStates") {
        const holders = CC.certs.filter((c) => c.kind === "building" && next.indexOf(reg(c)) > -1).map((c) => c.holder);
        out.ccBuildings = next.length ? p.ccBuildings.filter((x) => holders.indexOf(x) > -1) : p.ccBuildings;
      }
      return out;
    });
  },

  ccModel() {
    const s = this.state;
    const CC = this.ccData();
    // Live rows carry their own gap list; the seed derives one from req - on.
    const gapsOf = (b) => Array.isArray(b.gaps) ? b.gaps : CC.gapTypes.slice(0, Math.max(0, b.req - b.on));
    const gapLine = (b) => { const g = gapsOf(b); return g.length ? "missing: " + g[0] + (g.length > 1 ? " +" + (g.length - 1) + " more" : "") : ""; };
    // Scope, built the way the plenum console builds it: the certificate rows are the
    // thing filtered, and the region list is derived from those rows rather than from a
    // building's recorded state.
    //
    //  - regions come from the certificates in the selected countries, so the same region
    //    name can never appear twice (you have already narrowed to one country);
    //  - a certificate with no region recorded contributes no region option, instead of a
    //    "region not recorded" row nothing can usefully be filtered by;
    //  - nothing is listed until a country is picked, which is what the column's own
    //    "Pick a country first." empty state already promised.
    const regionOf = (c) => String(c.state || "").trim();
    const inCC = (c) => !s.ccCountries.length || s.ccCountries.indexOf(c.cc) > -1;
    const inRegion = (c) => !s.ccStates.length || s.ccStates.indexOf(regionOf(c)) > -1;

    const regionMap = {};
    if (s.ccCountries.length) {
      CC.certs.filter(inCC).forEach((c) => {
        const n = regionOf(c);
        if (!n) return;
        const e = regionMap[n] || (regionMap[n] = { name: n, country: c.cc, certs: 0, lapsed: 0 });
        e.certs += 1;
        if (c.days < 0) e.lapsed += 1;
      });
    }
    const liveStates = Object.keys(regionMap).map((k) => regionMap[k])
      .sort((a, b) => b.certs - a.certs || a.name.localeCompare(b.name));

    // Buildings in scope are the ones the in-scope certificates actually name. With no
    // country or region picked the whole register is in scope, so every building shows —
    // including any that coverage knows about but holds no certificate yet.
    const ccScoped = CC.certs.filter((c) => inCC(c) && inRegion(c));
    const named = {};
    ccScoped.filter((c) => c.kind === "building").forEach((c) => { named[c.holder] = true; });
    const scoped = (!s.ccCountries.length && !s.ccStates.length)
      ? CC.buildings.slice()
      : CC.buildings.filter((b) => named[b.name]);

    const bs = s.ccBuildings.length ? scoped.filter((b) => s.ccBuildings.indexOf(b.name) > -1) : scoped;
    const names = bs.map((b) => b.name);
    // A vendor named on a building certificate is scoped by that building; one that is not
    // (portfolio-wide accreditation) is scoped by its country, so live vendors never vanish.
    let vs = CC.vendors.filter((v) => v.serves.length ? v.serves.some((x) => names.indexOf(x) > -1) : (!v.cc || !s.ccCountries.length || s.ccCountries.indexOf(v.cc) > -1));
    let certs = ccScoped.filter((c) => c.kind === "building" ? names.indexOf(c.holder) > -1 : vs.some((v) => v.name === c.holder));

    // The metric card always reads the whole scope; only the register and the
    // runway narrow when a tile is active, so the card can never contradict itself.
    const scopeCerts = certs.slice();
    const FILTERS = {
      lapsed: (c) => c.days < 0,
      auth: (c) => /suspect/.test(c.auth),
      d30: (c) => c.days >= 0 && c.days <= 30,
      d90: (c) => c.days > 30 && c.days <= 90,
      bcert: (c) => c.kind === "building",
      vcert: (c) => c.kind === "vendor",
      crt: () => true
    };
    if (FILTERS[s.ccTile] && s.ccTile !== "crt") certs = certs.filter(FILTERS[s.ccTile]);

    const chips = [];
    s.ccCountries.forEach((code) => {
      const m = CC.countries.find((c) => c.code === code);
      chips.push({ label: m ? m.flag + " " + m.name : code, drop: () => this.ccToggle("ccCountries", code) });
    });
    if (s.ccStates.length <= 2) s.ccStates.forEach((x) => chips.push({ label: x, drop: () => this.ccToggle("ccStates", x) }));
    else chips.push({ label: s.ccStates[0] + " +" + (s.ccStates.length - 1) + " regions", drop: () => this.setState({ ccStates: [] }) });
    if (s.ccBuildings.length <= 2) s.ccBuildings.forEach((x) => chips.push({ label: x, drop: () => this.ccToggle("ccBuildings", x) }));
    else chips.push({ label: s.ccBuildings[0] + " +" + (s.ccBuildings.length - 1) + " buildings", drop: () => this.setState({ ccBuildings: [] }) });
    if (!chips.length) chips.push({ label: "Whole portfolio", drop: () => {} });

    const opt = (label, on, count, bad, pick) => ({
      label: label, count: String(count), bad: String(bad || ""), badShow: bad ? "inline" : "none",
      border: on ? "var(--color-accent)" : "transparent", bg: on ? "var(--color-accent-900)" : "transparent",
      fg: on ? "var(--color-accent)" : "var(--color-neutral-300)",
      tickBorder: on ? "var(--color-accent)" : "var(--color-divider)",
      tickBg: on ? "var(--color-accent)" : "var(--color-surface)",
      tickFg: on ? "var(--accent-ink)" : "transparent",
      pick: pick
    });

    const cols = [
      { title: "Countries", hint: "Each loads its regulation pack.", empty: "", emptyShow: "none",
        items: CC.countries.map((c) => opt(c.flag + "  " + c.name, s.ccCountries.indexOf(c.code) > -1, c.certs, c.lapsed, () => this.ccToggle("ccCountries", c.code))) },
      { title: "States and regions", hint: "Within the countries above.",
        empty: s.ccCountries.length ? "No region matches." : "Pick a country first.",
        emptyShow: liveStates.length ? "none" : "block",
        items: liveStates.map((st) => opt(countryMeta(st.country).flag + "  " + st.name, s.ccStates.indexOf(st.name) > -1, st.certs, st.lapsed, () => this.ccToggle("ccStates", st.name))) },
      { title: "Buildings", hint: "Within the regions above.",
        empty: "Pick a region first.", emptyShow: scoped.length ? "none" : "block",
        items: scoped.map((b) => opt(b.name, s.ccBuildings.indexOf(b.name) > -1, b.cov + "%", b.high, () => this.ccToggle("ccBuildings", b.name))) }
    ];

    // One canonical metric set. Both the Needs-you card and the portfolio-state
    // grid read from it, so the two can never disagree. The Needs-you toggle
    // changes which side the fix comes from, never the arithmetic.
    const onSum = bs.reduce((a, b) => a + b.on, 0);
    const reqSum = bs.reduce((a, b) => a + b.req, 0);
    const METRICS = [
      { id: "lapsed", value: scopeCerts.filter((c) => c.days < 0).length, label: "Lapsed or expired", color: "var(--st-risk)", mark: "✕✕",
        bHint: "statutory exposure live now", vHint: "vendor barred from regulated work",
        bAct: () => this.orch("Assign contractor", "Compliance"), vAct: () => this.orch("Change contractor", "Compliance") },
      { id: "auth", value: scopeCerts.filter((c) => /suspect/.test(c.auth)).length, label: "Authenticity failed", color: "var(--st-risk)", mark: "⌾",
        bHint: "forensics flagged the document", vHint: "vendor-supplied document in doubt",
        bAct: () => this.orch("Review flagged certificates", "Compliance"), vAct: () => this.orch("Request evidence from vendor", "Vendor performance") },
      { id: "d30", value: scopeCerts.filter((c) => c.days >= 0 && c.days <= 30).length, label: "High risk · < 30d", color: "var(--st-risk)", mark: "✕",
        bHint: "book before the window closes", vHint: "lapse would block work in scope",
        bAct: () => this.orch("Book inspections", "Compliance"), vAct: () => this.orch("Request evidence from vendor", "Vendor performance") },
      { id: "d90", value: scopeCerts.filter((c) => c.days > 30 && c.days <= 90).length, label: "Medium risk · < 90d", color: "var(--st-warn)", mark: "!",
        bHint: "watch list", vHint: "renewal due inside the quarter",
        bAct: () => this.orch("Book inspections", "Compliance"), vAct: () => this.orch("Request evidence from vendor", "Vendor performance") },
      { id: "cov", value: reqSum ? Math.round((onSum / reqSum) * 100) + "%" : "—", label: "Portfolio coverage", color: reqSum && (onSum / reqSum) >= 0.6 ? "var(--st-ok)" : "var(--st-warn)",
        bHint: onSum + " of " + reqSum + " pack types on file", vHint: "vendor accreditation completeness", plain: true,
        bAct: () => this.orch("Upload missing certificates", "Compliance"), vAct: () => this.setState({ ccPivot: "vendors" }) },
      { id: "bld", value: bs.length, label: "Buildings", color: "var(--color-neutral-300)",
        bHint: "in scope", vHint: bs.filter((b) => CC.vendors.some((v) => v.block === "Blocked" && v.serves.indexOf(b.name) > -1)).length + " exposed by a vendor", plain: true,
        bAct: () => this.setState({ ccPivot: "buildings" }), vAct: () => this.orch("Reassign work orders", "Compliance") },
      { id: "crt", value: scopeCerts.length, label: "Certificates", color: "var(--color-neutral-300)",
        bHint: "building + vendor, in scope", vHint: "building + vendor, in scope", plain: true,
        bAct: () => this.setState({ ccTile: null }), vAct: () => this.setState({ ccTile: null }) },
      { id: "bcert", value: scopeCerts.filter((c) => c.kind === "building").length, label: "Building certificates", color: "var(--color-neutral-300)",
        bHint: "held against the asset", vHint: "held against the asset",
        bAct: () => this.setState({ ccPivot: "buildings" }), vAct: () => this.setState({ ccPivot: "buildings" }) },
      { id: "vcert", value: scopeCerts.filter((c) => c.kind === "vendor").length, label: "Vendor certificates", color: "var(--color-neutral-300)",
        bHint: "accreditations of those servicing it", vHint: "accreditations on file", 
        bAct: () => this.setState({ ccPivot: "vendors" }), vAct: () => this.setState({ ccPivot: "vendors" }) }
    ];

    const tiles = METRICS.map((m) => ({
      value: String(m.value), label: m.label, hint: s.nyView === "vendor" ? m.vHint : m.bHint, color: m.color,
      mark: m.mark || "", markShow: m.mark ? "inline" : "none",
      bg: s.ccTile === m.id ? "var(--color-accent-900)" : "var(--color-surface)",
      edge: s.ccTile === m.id ? "var(--color-accent)" : "var(--color-divider)",
      pick: () => this.setState((p) => ({ ccTile: p.ccTile === m.id ? null : m.id, ccQueue: p.ccQueue === m.id ? null : m.id, ccQueueOpenId: null }))
    }));

    const used = {};
    const pins = certs.map((c) => {
      const bk = Math.round(c.pos / 3);
      used[bk] = (used[bk] || 0) + 1;
      const stack = (used[bk] - 1) % 3;
      return {
        pos: c.pos + "%", top: stack === 0 ? "27px" : stack === 1 ? "40px" : "14px",
        size: c.sev === "warn" ? "11px" : "9px",
        bg: c.sev === "risk" ? "var(--st-risk)" : c.sev === "warn" ? "var(--color-surface)" : "var(--st-ok)",
        border: c.sev === "warn" ? "2.5px solid var(--st-warn)" : "0",
        tip: c.nm + " — " + c.holder + " · " + c.exp + " · " + relDays(c.days, true) + " · " + c.risk,
        click: () => this.setState({ ccPivot: c.kind === "vendor" ? "vendors" : "buildings", ccFocus: { kind: c.kind, name: c.holder }, ccTab: 0 })
      };
    });

    // Runway counts, taken from the same rows the pins are drawn from so a dot and a
    // number can never disagree. `sev` is what decides a pin's shape, so it decides the
    // legend count too; the band is a time window, so it counts by days instead.
    const pinCounts = {
      risk: certs.filter((c) => c.sev === "risk").length,
      warn: certs.filter((c) => c.sev === "warn").length,
      ok: certs.filter((c) => c.sev !== "risk" && c.sev !== "warn").length,
      lapsed: certs.filter((c) => isFinite(c.days) && c.days < 0).length,
      band: certs.filter((c) => isFinite(c.days) && c.days >= 0 && c.days <= 90).length,
      total: certs.length
    };

    const covColor = (p) => p >= 60 ? "var(--st-ok)" : p >= 30 ? "var(--st-warn)" : "var(--st-risk)";
    const tg = (label, k) => Object.assign({ label: label }, { bg: TAG[k].bg, fg: TAG[k].fg });

    let rows = [];
    if (s.ccPivot === "buildings") {
      rows = bs.map((b) => {
        const tags = [];
        if (b.blocked) tags.push(tg(b.blocked + " blocked", "risk"));
        if (b.high) tags.push(tg(b.high + " high", "risk"));
        else if (b.med) tags.push(tg(b.med + " med", "warn"));
        else tags.push(tg("clear", "ok"));
        const active = s.ccFocus.kind === "building" && s.ccFocus.name === b.name;
        return {
          name: b.name,
          // The count of obligations with no document behind them sits next to the count
          // of obligations. Coverage already says how many pack types are on file; this
          // says how many of those are backed by something anyone can open.
          meta: b.state + " · " + b.use + " · " + b.certs + " certificates"
                + (b.noDoc ? " · " + b.noDoc + " with no document" : ""),
          gap: gapLine(b),
          gapShow: gapsOf(b).length ? "block" : "none",
          cov: b.cov + "%", frac: b.on + "/" + b.req, covColor: covColor(b.cov),
          edge: b.high || b.blocked ? "var(--st-risk)" : b.med ? "var(--st-warn)" : "var(--st-ok)",
          rowBg: active ? "var(--color-accent-900)" : "transparent",
          tags: tags,
          click: () => this.setState({ ccFocus: { kind: "building", name: b.name }, ccTab: 0 })
        };
      });
    } else if (s.ccPivot === "vendors") {
      rows = vs.map((v) => {
        const tags = [tg(v.worst, v.sev)];
        if (v.block === "Blocked") tags.push(tg("blocked", "risk"));
        const active = s.ccFocus.kind === "vendor" && s.ccFocus.name === v.name;
        return {
          name: v.name,
          meta: v.spec + " · " + v.serves.filter((x) => names.indexOf(x) > -1).length
                + " of " + v.serves.length + " served in scope"
                + (v.noDoc ? " · " + v.noDoc + " accreditation"
                   + (v.noDoc === 1 ? "" : "s") + " with no document" : ""),
          gap: v.gaps.length ? "pending: " + v.gaps[0] + (v.gaps.length > 1 ? " +" + (v.gaps.length - 1) + " more" : "") : "",
          gapShow: v.gaps.length ? "block" : "none",
          cov: v.cov + "%", frac: v.on + "/" + v.req, covColor: covColor(v.cov),
          edge: v.sev === "risk" ? "var(--st-risk)" : v.sev === "warn" ? "var(--st-warn)" : "var(--st-ok)",
          rowBg: active ? "var(--color-accent-900)" : "transparent",
          tags: tags,
          click: () => this.setState({ ccFocus: { kind: "vendor", name: v.name }, ccTab: 0 })
        };
      });
    }

    const mxRows = bs.filter((b) => CC.mx[b.name]).map((b) => ({
      name: b.name, meta: b.state + " · " + b.cov + "% coverage",
      click: () => this.setState({ ccPivot: "buildings", ccFocus: { kind: "building", name: b.name }, ccTab: 0 }),
      cells: CC.mx[b.name].map((st, i) => Object.assign({ glyph: CC.mxGlyph[st], tip: CC.mxTypes[i] + " — " + CC.mxLabel[st] }, MK[st]))
    }));

    const isB = s.ccFocus.kind === "building";
    let focus = isB ? CC.buildings.find((b) => b.name === s.ccFocus.name) : CC.vendors.find((v) => v.name === s.ccFocus.name);
    if (!focus) focus = isB ? bs[0] : vs[0];
    let crumb = "", facts = [], tabs = [], certRows = [], vendorRows = [], gapRows = [], servedRows = [];

    if (focus) {
      const fname = focus.name;
      if (isB) {
        const own = CC.certs.filter((c) => c.kind === "building" && c.holder === fname);
        const fvs = CC.vendors.filter((v) => v.serves.indexOf(fname) > -1);
        const ctry = CC.countries.find((c) => CC.states.some((st) => st.name === focus.state && st.country === c.code));
        crumb = (ctry ? ctry.flag + " " + ctry.name + " › " : "") + focus.state + " › building";
        facts = [
          { label: "Coverage", value: focus.cov + "%", color: covColor(focus.cov) },
          { label: "Pack types on file", value: focus.on + " / " + focus.req, color: "var(--color-text)" },
          { label: "Certificates", value: String(focus.certs), color: "var(--color-text)" },
          { label: "Vendors serving", value: String(fvs.length), color: "var(--color-text)" }
        ];
        tabs = [["Building vault", own.length], ["Vendor impact", fvs.length], ["Not on record", gapsOf(focus).length]];
        certRows = own;
        vendorRows = fvs;
        gapRows = gapsOf(focus).slice(0, 10).map((g) => ({
          label: g, action: "Upload", act: () => this.orch("Upload " + g, fname)
        }));
      } else {
        const mine = CC.certs.filter((c) => c.holder === fname);
        crumb = "vendor › " + focus.spec + " › " + focus.serves.length + " buildings served";
        facts = [
          { label: "Coverage", value: focus.cov + "%", color: covColor(focus.cov) },
          { label: "Required on file", value: focus.on + " / " + focus.req, color: "var(--color-text)" },
          { label: "Accreditations", value: String(focus.certs), color: "var(--color-text)" },
          { label: "Status", value: focus.block, color: focus.block === "Blocked" ? "var(--st-risk)" : "var(--st-ok)" }
        ];
        tabs = [["Accreditations", mine.length], ["Buildings served", focus.serves.length], ["Pending types", focus.gaps.length]];
        certRows = mine;
        vendorRows = [];
        servedRows = focus.serves.map((nm) => {
          const b = CC.buildings.find((x) => x.name === nm) || { state: "—", cov: 0, on: 0, req: 0, certs: 0 };
          return {
            name: nm, state: b.state, cov: b.cov + "%", covColor: covColor(b.cov), certs: String(b.certs),
            open: () => this.setState({ ccPivot: "buildings", ccFocus: { kind: "building", name: nm }, ccTab: 0 })
          };
        });
        gapRows = focus.gaps.map((g) => ({ label: g, action: "Request", act: () => this.orch("Request " + g, fname) }));
      }
    }

    // Needs you — the same scope read two ways. Building view counts the
    // building's own record; vendor view counts what the servicing vendors
    // are doing to it, which is invisible in a certificate-only reading.
    const isVendorView = s.nyView === "vendor";
    const needs = METRICS.map((m) => ({
      value: String(m.value), label: m.label, color: m.color,
      hint: isVendorView ? m.vHint : m.bHint,
      act: () => this.setState({ ccQueue: m.id, ccQueueOpenId: null })
    }));

    const qMeta = {
      lapsed: { title: "Lapsed or expired", hint: "Statutory exposure is live. Each one needs a booking, a different contractor, or a formal extension." },
      auth: { title: "Authenticity failed", hint: "Forensics flagged the document itself. A certificate can be current and forged — this is a decision, not a filing task." },
      d30: { title: "High risk · expiring within 30 days", hint: "Inside the booking window. The alert ladder has already drafted where it can." },
      d90: { title: "Medium risk · expiring within 90 days", hint: "Watch list. Consolidating visits across a building saves call-out charges." },
      bcert: { title: "Building certificates", hint: "Held against the asset itself." },
      vcert: { title: "Vendor certificates", hint: "Accreditations of the vendors servicing these buildings." },
      crt: { title: "All certificates in scope", hint: "Building certificates and vendor accreditations together — the full register for the current selection." },
      cov: { title: "Portfolio coverage — what is missing", hint: "Coverage is required pack types on file. These are the types required here with nothing filed, which is what holds the percentage down.", mode: "gaps" },
      bld: { title: "Buildings in scope", hint: "Each building's own pack coverage, and whether a vendor servicing it is blocked.", mode: "buildings" }
    };
    const qm = qMeta[s.ccQueue] || null;
    const qMode = qm && qm.mode ? qm.mode : "certs";
    const qCerts = qm && qMode === "certs" ? scopeCerts.filter(FILTERS[s.ccQueue]) : [];
    const queueItems = qCerts.map((c, i) => {
      const id = c.nm + "|" + c.holder;
      const open = s.ccQueueOpenId === id;
      const subject = c.nm + " — " + c.holder;
      const vendorName = c.kind === "vendor" ? c.holder : (CC.vendors.find((v) => v.serves.indexOf(c.holder) > -1) || {}).name || "the responsible vendor";
      return {
        nm: c.nm, holder: c.holder,
        kind: c.kind === "vendor" ? "vendor accreditation" : "building certificate",
        // "no document on file" sits in the same line as the expiry and the verification
        // state, because it is the same kind of fact about the record: an obligation whose
        // evidence nobody can open is not evidenced, however current the date looks.
        meta: c.exp + " · " + relDays(c.days, true) + " · " + c.ver
              + (c.doc ? "" : " · no document on file"),
        risk: c.risk, riskBg: TAG[c.sev].bg, riskFg: TAG[c.sev].fg,
        auth: c.auth, authBg: TAG[c.authSev].bg, authFg: TAG[c.authSev].fg,
        caret: open ? "ph-caret-up" : "ph-caret-down",
        actShow: open ? "flex" : "none",
        toggle: () => this.setState((p) => ({ ccQueueOpenId: p.ccQueueOpenId === id ? null : id })),
        actions: this.ccCertActions(c).map((l, j) => ({
          label: l,
          bg: j === 0 ? "var(--color-accent)" : "transparent",
          fg: j === 0 ? "var(--accent-ink)" : "var(--color-neutral-300)",
          border: j === 0 ? "var(--color-accent)" : "var(--color-divider)",
          run: () => { this.setState({ ccQueue: null }); this.ccRunCertAction(l, c, subject, vendorName); }
        }))
      };
    });

    return {
      needs: needs,
      queueOpenCC: !!qm,
      qModeCerts: qMode === "certs", qModeBuildings: qMode === "buildings", qModeGaps: qMode === "gaps",
      qBuildingRows: qMode !== "buildings" ? [] : bs.map((b) => {
        const blockers = CC.vendors.filter((v) => v.block === "Blocked" && v.serves.indexOf(b.name) > -1);
        return {
          name: b.name, meta: b.state + " · " + b.use + " · " + b.certs + " certificates on file",
          cov: b.cov + "%", frac: b.on + "/" + b.req, covColor: covColor(b.cov),
          note: blockers.length ? "blocked vendor: " + blockers.map((v) => v.name).join(", ") : (b.high ? b.high + " lapsed or expiring" : "no live exposure"),
          noteColor: blockers.length || b.high ? "var(--st-risk)" : "var(--st-ok)",
          actLabel: blockers.length ? "Change contractor" : (b.high ? "Approve booking" : "Monitor"),
          act: () => this.runAction(blockers.length ? "Change contractor" : (b.high ? "Approve booking" : "Monitor"), b.name, (blockers[0] || {}).name),
          open: () => this.setState({ ccQueue: null, ccPivot: "buildings", ccFocus: { kind: "building", name: b.name }, ccTab: 0 })
        };
      }),
      qGapRows: qMode !== "gaps" ? [] : bs.reduce((acc, b) => {
        const gl = gapsOf(b); const n = gl.length;
        gl.slice(0, 3).forEach((t) => acc.push({
          building: b.name, label: t,
          more: n > 3 ? "+" + (n - 3) + " more here" : "",
          moreShow: n > 3 ? "inline" : "none",
          act: () => this.runAction("Upload " + t, t + " — " + b.name)
        }));
        return acc;
      }, []),
      ccScopeNote: bs.length + (bs.length === 1 ? " building" : " buildings") + " in scope",
      qTitle: qm ? qm.title : "",
      qHint: qm ? qm.hint : "",
      qCount: qMode === "buildings" ? bs.length + (bs.length === 1 ? " building" : " buildings")
        : qMode === "gaps" ? bs.reduce((a, b) => a + gapsOf(b).length, 0) + " missing types"
        : qCerts.length + (qCerts.length === 1 ? " certificate" : " certificates"),
      qItems: queueItems,
      qEmpty: qCerts.length ? "none" : "block",
      closeCCQueue: () => this.setState({ ccQueue: null, ccQueueOpenId: null, ccTile: null }),
      nCountries: s.ccCountries.length || CC.countries.length,
      live: !!CC.live, mxTypes: CC.mxTypes, mxShort: CC.mxShort, mxLabel: CC.mxLabel, mxGlyph: CC.mxGlyph,
      bs: bs, vs: vs, certs: certs, chips: chips, cols: cols, tiles: tiles, pins: pins, pinCounts: pinCounts, rows: rows, mxRows: mxRows,
      focus: focus, crumb: crumb,
      facts: facts,
      tabs: tabs.map((t, i) => ({
        label: t[0], n: String(t[1]),
        edge: s.ccTab === i ? "var(--color-accent)" : "transparent",
        fg: s.ccTab === i ? "var(--color-accent)" : "var(--color-neutral-500)",
        pick: () => this.setState({ ccTab: i })
      })),
      certRows: certRows.map((c) => {
        const acts = this.ccCertActions(c);
        const subject = c.nm + " — " + c.holder;
        const ven = c.kind === "vendor" ? c.holder : (CC.vendors.find((v) => v.serves.indexOf(c.holder) > -1) || {}).name || "the responsible vendor";
        const urgent = c.days <= 30 || /suspect/.test(c.auth);
        // Once an evidence request has been approved for this certificate the action
        // reports that instead of offering the same request again — the row is the only
        // place that state is visible.
        const sent = !!(s.ccRequested || {})[c.id];
        return {
          nm: c.nm, exp: c.exp, rel: relDays(c.days, false),
          risk: c.risk, riskBg: TAG[c.sev].bg, riskFg: TAG[c.sev].fg,
          auth: c.auth, authBg: TAG[c.authSev].bg, authFg: TAG[c.authSev].fg,
          ver: c.doc ? c.ver : c.ver + " · no document",
          actLabel: sent ? "Sent for approval" : acts[0],
          actBorder: sent ? "var(--st-ok)" : (urgent ? "var(--color-accent)" : "var(--color-divider)"),
          actFg: sent ? "var(--st-ok)" : (urgent ? "var(--color-accent)" : "var(--color-neutral-400)"),
          act: sent
            ? () => this.flash("Evidence request for " + c.nm + " is queued for approval — open it from the approvals card.")
            : () => this.ccRunCertAction(acts[0], c, subject, ven)
        };
      }),
      vendorRows: vendorRows.map((v) => ({
        name: v.name, worst: v.worst, bg: TAG[v.sev].bg, fg: TAG[v.sev].fg,
        serves: v.serves.join(", "), cov: v.cov + "%", frac: v.on + "/" + v.req, covColor: covColor(v.cov),
        gaps: v.gaps.length ? v.gaps.join(" · ") : "—",
        block: v.block, blockBg: v.block === "Blocked" ? TAG.risk.bg : TAG.none.bg, blockFg: v.block === "Blocked" ? TAG.risk.fg : TAG.none.fg
      })),
      gapRows: gapRows, servedRows: servedRows
    };
  },


  // Actions offered for a certificate, by what is wrong with it.
  certActions(c) {
    if (/suspect/.test(c.auth)) return ["Request evidence from vendor", "Escalate", "Acknowledge"];
    if (c.days < 0) return c.kind === "vendor" ? ["Change contractor", "Request evidence from vendor", "Extend deadline"] : ["Approve booking", "Change contractor", "Extend deadline"];
    if (c.days <= 30) return ["Approve booking", "Change priority", "Acknowledge"];
    return ["Approve booking", "Monitor"];
  },

  certDetail(c) {
    const link = { "Gas Safety (CP12)": "Gas Safe Register", EICR: "NICEIC portal", LOLER: "LEIA register", "Legionella (L8)": "LCA register", "F-Gas": "REFCOM register" }[c.type] || "issuing register";
    return {
      module: "Compliance", icon: "ph-shield-check", tone: c.tone,
      title: c.type + " — " + c.asset + ", " + c.building,
      meta: c.status + " · expiry " + c.expiry + " · responsible vendor " + c.vendor,
      body: (c.days < 0
        ? "This obligation lapsed " + Math.abs(c.days) + " days ago. Statutory exposure is live and the insurance position on this building is compromised until a satisfactory certificate is ingested."
        : "This obligation expires in " + c.days + " days. The alert ladder has already surfaced a contractor and, inside the 30-day window, drafted a booking request for your approval.") +
        " Inspector accreditation is verifiable against the " + link + " from the certificate record.",
      fields: [
        { l: "Certificate type", v: c.type },
        { l: "Asset", v: c.asset },
        { l: "Building", v: c.building },
        { l: "Expiry date", v: c.expiry },
        { l: "Verification register", v: link },
        { l: "Responsible vendor", v: c.vendor, editable: true },
        { l: "Booking window", v: c.days < 0 ? "Immediate — escalated" : "Draft, awaiting approval", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Nightly cron 02:00 → intent: compliance-engine, scope: " + c.building },
        { a: "Planner", t: "Fetch ComplianceCertificate → compute alert ladder → fetch approved contractors for this AssetType → draft booking" },
        { a: "Worker", t: "Record read from Hoist Graph. Status computed as " + c.status + ". Vendor accreditation cross-checked against " + link + "." },
        { a: "Quality", t: c.days < 0 ? "Fired — Lapsed is an irreversible compliance consequence. Status validated against the issuing register before flagging." : "Fired on the drafted booking. Contractor accreditation currency confirmed." }
      ],
      refinement: "Other obligations at " + c.building + " fall due within 90 days. Consolidate the inspection visits to reduce call-out charges?",
      actions: c.days < 0 ? ["Escalate now", "Assign contractor", "Acknowledge exposure"] : ["Approve booking", "Change contractor", "Extend deadline"]
    };
  }
};
