import { energyApi } from '../api/energy.js';

// homeLive — the home page, read from the register instead of typed into the bundle.
//
// Everything on this screen was a constant: a Hoist Score of 78, four coverage bars at
// 92/84/71/65, a portfolio P&L of £390k saved, and a hero line saying 24 buildings and
// 1.84m ft². None of it moved when the database did, which is the worst kind of dashboard
// — it looks like measurement and behaves like wallpaper.
//
// Three of those four now come off the buildings the register already loaded, so they
// cost no extra request:
//
//   the hero        how many buildings, their floor area, the markets they sit in
//   Hoist Score     the mean record completeness — which is what the old copy said the
//                   number meant ("ingestion coverage across the Hoist Graph")
//   the four bars   real coverage per branch: of the buildings on record, how many have
//                   a contract, an asset register, a meter, a certificate
//
// The fourth has no source at all and is treated accordingly. See PNL below.

// Which branch of graph_counts each bar measures, and what it is called on screen. The
// order is the order they are drawn.
const BARS = [
  { key: 'contracts', label: 'Contracts and framework agreements', short: 'Contracts' },
  { key: 'assets', label: 'Asset registers', short: 'Assets' },
  { key: 'meters', label: 'Meter consent — MPAN / MPRN', short: 'Meter consent' },
  { key: 'certificates', label: 'Certificates and evidence', short: 'Certificates' }
];

// Where the score stops being "watch it" and starts being "let it run". The band names
// are the product's, not arithmetic — they are what the number is FOR.
const BANDS = [
  { at: 85, name: 'Delegated autonomy' },
  { at: 60, name: 'Supervised autonomy' },
  { at: 1, name: 'Observation only' }
];

const pct = (n, d) => (d > 0 ? Math.round((n / d) * 100) : 0);
const tone = (p) => (p >= 80 ? 'var(--st-ok)' : p >= 50 ? 'var(--st-warn)' : 'var(--st-risk)');

export const homeLiveMethods = {

  // ── the approvals rail ─────────────────────────────────────────────────────
  //
  // One request, on first paint. Everything else on this screen comes from buildings the
  // register has already fetched.
  async homeLoad() {
    if (this._homeLoading) return;
    this._homeLoading = true;
    try {
      const res = await energyApi.approvals({ limit: 12 });
      if (res && res.ok) this.setState({ homeApprovals: res.items || [] });
    } catch (e) {
      // A rail that cannot load is not a reason to lose the page. It renders its own
      // "could not read" line rather than an empty list that reads as "nothing pending".
      this.setState({ homeApprovalsError: (e && e.message) || String(e) });
    } finally {
      this._homeLoading = false;
    }
  },

  // ── the figures ────────────────────────────────────────────────────────────

  homeVals() {
    const s = this.state;
    const rows = this.bldData();
    const live = !!s.bldLive;
    const n = rows.length;

    // Mean record completeness. The endpoint computes it per building against the fields
    // the graph expects, so this is the same measure the Buildings table shows in its
    // tooltip — not a second definition of "how complete are we".
    const scored = rows.filter((b) => typeof b.completeness === 'number');
    const score = scored.length
      ? Math.round(scored.reduce((t, b) => t + b.completeness, 0) / scored.length)
      : null;

    const bars = BARS.map((bar) => {
      // Three states, the same three the Buildings page uses. The rollup reports a branch
      // per building only where it has rows, so a missing key is ambiguous on its own: it
      // means none, or it means nobody counted. If ANY building carries a figure the
      // query ran, and a building absent from it has none — so every building counts and
      // coverage is real. If no building carries one, nothing here can tell zero from
      // uncounted, and the bar says "—" rather than reporting 0% coverage that might
      // really be 100% uncounted.
      const measured = this.glBranchCounted(bar.key);
      const have = measured
        ? rows.filter((b) => ((b.counts || {})[bar.key] || 0) > 0).length : 0;
      const p = measured ? pct(have, n) : 0;
      return {
        label: measured
          ? `${bar.label} — ${have} of ${n} ${n === 1 ? 'building' : 'buildings'}`
          : `${bar.label} — not counted on any building yet`,
        short: bar.short,
        val: measured ? p + '%' : '—',
        pct: p + '%',
        color: measured ? tone(p) : 'var(--color-neutral-700)'
      };
    });

    const weakest = bars
      .filter((b) => b.val !== '—')
      .sort((a, b) => parseInt(a.val, 10) - parseInt(b.val, 10))[0];
    const band = score === null ? '—'
      : (BANDS.find((x) => score >= x.at) || { name: 'Nothing on record' }).name;

    // Floor area and markets, from the rows themselves.
    const areaM2 = rows.reduce((t, b) => t + (typeof b.areaM2 === 'number' ? b.areaM2 : 0), 0);
    const areaFt2 = areaM2 * 10.7639;
    const markets = [...new Set(rows.map((b) => b.cc).filter((c) => c && c !== '—'))].sort();

    return {
      homeIsLive: live,

      // ── hero ────────────────────────────────────────────────────────────────
      heroCount: live ? String(n) : '—',
      heroCountLabel: n === 1 ? 'building hoisted' : 'buildings hoisted',
      heroArea: !live || !areaFt2 ? '—'
        : areaFt2 >= 1e6 ? (Math.round(areaFt2 / 1e5) / 10) + 'm ft²'
        : Math.round(areaFt2 / 1000) + 'k ft²',
      heroMarkets: markets.length ? markets.join(', ') : (live ? 'no market on record' : '—'),
      heroShowArea: live && areaFt2 ? 'inline' : 'none',

      // ── Hoist Score ─────────────────────────────────────────────────────────
      hoistScore: {
        value: score === null ? '—' : String(score),
        band: live ? band : 'register unreachable',
        // The gap names the real weakest branch. The old copy hard-coded "Meter consent
        // lowest at 71%", which stayed on screen whatever the data said.
        gap: !live ? 'The register could not be read, so nothing here is measured.'
          : !n ? 'No buildings on the register yet — there is nothing to score.'
          : weakest ? weakest.short + ' is lowest at ' + weakest.val
            + ' — the gap to delegated autonomy'
          : 'No branch has been counted yet, so coverage is unknown rather than low.',
        note: 'Mean record completeness across the buildings on the register. At 85 the '
          + 'agents move from supervised to delegated dispatch on L3 assets.'
      },
      hoistBars: bars,

      // ── portfolio P&L ───────────────────────────────────────────────────────
      //
      // NOT WIRED, because there is nothing to wire it to. A budget-versus-actual needs a
      // budget, and this platform has no table holding one: plenum_cafm has misc_costs
      // (per work order), cost_variance_alerts and claude_budget_config (AI spend), and
      // nothing that says what any head was budgeted. "£390k saved to date" against four
      // invented heads is the single most quotable number on the screen and the least
      // supported, and a figure like that being wrong is worse than it being absent.
      //
      // So it says what it is. When a budget source arrives, this is the only block that
      // needs to change.
      pnlSaved: '—',
      pnlNote: 'No budget on record. Savings need a budget to be measured against, and '
        + 'nothing in the database holds one yet.',
      pnlNoteShow: 'block',
      pnlTop: [],

      // ── approvals rail ──────────────────────────────────────────────────────
      queuePreview: (s.homeApprovals || []).slice(0, 3).map((it) => ({
        title: it.title || it.summary || 'Approval',
        meta: [it.source_feature, it.status].filter(Boolean).join(' · '),
        money: it.value_gbp ? '£' + Number(it.value_gbp).toLocaleString('en-GB') : '',
        icon: 'ph-check-square',
        color: 'var(--color-accent)', bg: 'var(--color-accent-900)',
        click: () => this.flash((it.title || 'This item') + ' — from plenum_cafm.approvals_queue_items.')
      })),
      queueEmpty: s.homeApprovalsError
        ? 'The approvals rail could not be read: ' + s.homeApprovalsError
        : (s.homeApprovals || []).length ? ''
        : 'Nothing waiting for approval.',
      queueEmptyShow: (s.homeApprovals || []).length ? 'none' : 'block'
    };
  }
};
