// Access roles (12 Nov 2026). An admin may give a user a role and tick the pages that user may
// open (Users & access). From then on only those pages, and only their data, reach them.
//
// The server enforces it: every service refuses data owned by a page the caller does not hold
// (svc-operations-intelligence engines/auth/pages.py and page_map.py; the same gate in the
// work-order, connector, udr and chat services). Nothing here is the boundary. This is the
// client's half, whose job is that what is not held is ABSENT rather than broken: no entry in
// the navigator, no card on Home, no request sent for it, no error where its data would be.
//
//   normal user   role 'user', no access role   → every page (pages null), picks an instance
//   role user     role 'user', an access role   → only the ticked pages, no instance picker
//   admin, superadmin                            → every page, whatever the record says
//
// A role is a label. It ticks nothing by itself.

export const ACCESS_ROLES = [
  { key: 'portfolio_management', label: 'Portfolio Management' },
  { key: 'property_manager', label: 'Property Manager' },
  { key: 'general_user', label: 'General User' },
  { key: 'compliance_officer', label: 'Compliance Officer' }
];

// The pages that can be ticked, named as the navigator names them. Where the space of the
// same page has another name, it is the hint.
export const PAGE_OPTIONS = [
  { key: 'buildings', label: 'Buildings' },
  { key: 'compliance', label: 'Compliance' },
  { key: 'energy', label: 'Energy' },
  { key: 'vendors', label: 'Vendors', hint: 'Vendor performance' },
  { key: 'ops', label: 'Maintenance', hint: 'Vendor operations' },
  { key: 'assets', label: 'Assets' }
];
export const PAGE_KEYS = PAGE_OPTIONS.map((p) => p.key);

// Ticks as they are stored and sent: known pages, once each, in page order.
export function cleanPages(list) {
  const have = Array.isArray(list) ? list : [];
  return PAGE_KEYS.filter((k) => have.indexOf(k) > -1);
}

export function togglePage(list, key) {
  const have = cleanPages(list);
  return cleanPages(have.indexOf(key) > -1 ? have.filter((k) => k !== key) : have.concat([key]));
}

// A role user: a plain user an admin gave an access role. An admin is never one.
// Pages left on an account with no role - a record somebody edited by hand - still
// restrict, as they do on the server: a restriction is never lost to a missing label.
export function isRoleUser(account) {
  return !!(account && account.role === 'user' && (account.access_role || Array.isArray(account.pages)));
}

// The pages this account holds: null for every page, a list for a role user. Reading fails
// closed: a role with nothing readable behind it holds nothing; it is never read as every page.
export function pagesOf(account) {
  if (!isRoleUser(account)) return null;
  return cleanPages(account.pages);
}

// Whether the account's pages have been read yet. A plain user's are not known between the
// sign-in answer and GET /api/auth/me unless that answer carried them (`pages_known`).
export function pagesKnown(account) {
  if (!account || account.role !== 'user') return true;
  return account.pages_known === true || account.pages !== undefined;
}

export function holdsPage(account, page) {
  const mine = pagesOf(account);
  return mine === null || mine.indexOf(page) > -1;
}
export function holdsAny(account, pages) {
  const mine = pagesOf(account);
  return mine === null || (pages || []).some((p) => mine.indexOf(p) > -1);
}
export function holdsAll(account, pages) {
  const mine = pagesOf(account);
  return mine === null || (pages || []).every((p) => mine.indexOf(p) > -1);
}

// The reports a role user sees, in the words logic/instances.js uses: their pages, and the
// Compliance Graph, which has no tick of its own and comes with Compliance. null for an
// account that is not a role user, so the instance rules decide as before.
export function roleReports(account) {
  const mine = pagesOf(account);
  if (mine === null) return null;
  const out = [];
  mine.forEach((p) => { out.push(p); if (p === 'compliance') out.push('graph'); });
  return out;
}

// The name the top bar shows: the server's own label first, then this build's, and for pages
// with no role (a record edited by hand) a plain description - never "null" or a raw key.
export function roleLabel(account) {
  if (!isRoleUser(account)) return null;
  if (typeof account.access_role_label === 'string' && account.access_role_label.trim()) return account.access_role_label.trim();
  const known = ACCESS_ROLES.find((r) => r.key === account.access_role);
  if (known) return known.label;
  // A role this build has not heard of is shown by its key; pages with no role at all are
  // described, never printed as "null".
  return account.access_role ? String(account.access_role) : 'Limited access';
}

// A conversation a role user may still see in this browser's list: none from before their
// access was last narrowed (access_narrowed_at), the same cut the server applies to its
// thread list - the transcript kept here may hold answers from a page since taken away.
export function sessionVisible(account, rec) {
  if (!isRoleUser(account) || !rec) return true;
  const cut = account.access_narrowed_at ? Date.parse(account.access_narrowed_at) : NaN;
  if (isNaN(cut)) return true;
  const at = Number(rec.createdAt) || Number(rec.at) || 0;
  return at > cut;
}

// ── the view a page lives on ──────────────────────────────────────────────────────────
const VIEW_PAGE = { cc: 'compliance', hg: 'compliance', vp: 'vendors', vh: 'vendors', buildings: 'buildings', insp: 'ops' };
const MODULE_PAGE = { compliance: 'compliance', energy: 'energy', assets: 'assets', ops: 'ops', vendors: 'vendors' };

// The page that owns a view, or null for one that belongs to everyone (Home, chat, sessions,
// reports, support).
export function viewPage(view, module) {
  return (view === 'module' ? MODULE_PAGE[module] : VIEW_PAGE[view]) || null;
}

// A role user is never on a view whose page they do not hold, nor on the instance picker:
// either is Home for them (HoistraLogic's update hook moves them there).
// The built-in spaces (spacesLive.js BUILTIN_SPACES) are keyed by the page they belong to;
// Support and saved spaces belong to everyone.
const SPACE_PAGE = { compliance: 'compliance', energy: 'energy', vendors: 'vendors', ops: 'ops' };
export function viewOffLimits(account, view, module, spaceKey) {
  if (!isRoleUser(account)) return false;
  if (view === 'instances') return true;
  const page = view === 'space' ? (SPACE_PAGE[spaceKey] || null) : viewPage(view, module);
  return !!page && !holdsPage(account, page);
}

// ── loaders ───────────────────────────────────────────────────────────────────────────
// Each page's register loader, and the pages whose holder may run it. A loader with two pages
// serves a fact that belongs to both (which vendor serves which building). The loaders that
// read for several pages at once - Home, the buildings names, the Pending queue - are not
// here: their requests are held back one by one, below.
export const LOADER_PAGES = {
  ccLoad: ['compliance'],
  vpLoad: ['vendors'],
  vmLoad: ['vendors'],
  vbLoad: ['vendors', 'compliance'],
  energyLoad: ['energy'],
  enPositionLoad: ['energy'],
  asLiveLoad: ['assets'],
  asCondLoad: ['assets'],
  mxLiveLoad: ['ops']
};

// Wrap each of those loaders, on the controller's prototype, so it does not run for an
// account that holds none of its pages - whoever calls it: the mount, a page opening, a
// Retry, a timer. An account with every page runs every loader exactly as before. A skipped
// loader answers a resolved promise, so a caller that waits on it is not left waiting.
export function guardLoaders(proto) {
  Object.keys(LOADER_PAGES).forEach((name) => {
    const inner = proto[name];
    if (typeof inner !== 'function' || inner.__pageGuard) return;
    const pages = LOADER_PAGES[name];
    const guarded = function () {
      const account = this && this.state ? this.state.account : null;
      if (!holdsAny(account, pages)) return Promise.resolve();
      return inner.apply(this, arguments);
    };
    guarded.__pageGuard = true;
    proto[name] = guarded;
  });
  return proto;
}

// ── requests that are never sent ──────────────────────────────────────────────────────
// A loader that reads for several pages at once would otherwise ask for a page the account
// does not hold and be refused. These are the paths that are certainly one page's own, so
// api/client.js can answer the refusal itself without sending anything. It is deliberately
// narrower than the server's table (engines/auth/page_map.py): a path that is missing here
// is still sent and still refused there; a path wrongly listed here would hide data its
// holder is entitled to. So only what is unmistakable is listed, and what the server opens
// to everyone or narrows by itself (building names, the Hoist Score, the Pending queue, the
// value card, uploads) is left alone.
const OPS_RULES = [
  [/^\/api\/compliance\/(vendor-buildings|vendors\/[^/]+\/buildings)(\/|$)/, ['vendors', 'compliance']],
  // A vendor's Blocked state stays wherever the vendor shows, so the Vendors page reads this
  // too; without Compliance the server sends the state and no certificate detail.
  [/^\/api\/compliance\/coverage\/vendors$/, ['vendors', 'compliance']],
  [/^\/api\/compliance\/(documents\/|forensics|adversary|extract|ingest-batch|table-match|vendor-registration-check|passport\/share|approvals\/one-click)/, null],
  [/^\/api\/compliance\//, ['compliance']],
  [/^\/api\/contract-performance\/(migration\/|contracts\/(extract|ingest)$|invoices\/extract-verify|asset-criticality)/, null],
  [/^\/api\/contract-performance\//, ['vendors']],
  [/^\/api\/energy\/(anomalies|meters|market-profiles|ratings|tariffs|gaps|benchmarks|consumption|detection|chillers|tm46)(\/|$)/, ['energy']],
  // The question bar answers the Energy page's questions and the Assets page's.
  [/^\/api\/energy\/ask(\/|$)/, ['energy', 'assets']],
  [/^\/api\/energy\/(condition\/|assets\/)/, ['assets']],
  [/^\/api\/energy\/(graph\/|buildings\/[^/]+\/graph$)/, ['buildings']],
  // Which plant drives a building's spend is the asset register's, wherever it is shown.
  [/^\/api\/energy\/buildings\/[^/]+\/cost-drivers$/, ['assets']]
];
const WO_RULES = [
  [/^\/api\/(maintenance|work-orders|journeys|ppm)(\/|$)/, ['ops']],
  [/^\/api\/(assets|asset-categories)(\/|$)/, ['assets']]
];
const CON_RULES = [
  [/^\/api\/v1\/plenum\/(vendors|vendor-contacts|vendor-contracts|sla-policies)(\/|$)/, ['vendors']],
  [/^\/api\/v1\/plenum\/(assets|asset-categories|asset-documents|asset-readings|spare-parts)(\/|$)/, ['assets']],
  [/^\/api\/v1\/plenum\/(work-orders|work-order-[a-z]+|maintenance-[a-z]+|technicians|technician-skills)(\/|$)/, ['ops']]
];

// The pages that own a request, or null when it is not one page's own. `bases` is the
// services' base URLs (api/client.js BASES, by any names): ops, wo and con are read.
export function pagesForRequest(base, path, bases) {
  const b = bases || {};
  const rules = base === b.ops ? OPS_RULES : base === b.wo ? WO_RULES : base === b.con ? CON_RULES : null;
  if (!rules) return null;
  const p = String(path || '').split('?')[0];
  for (let i = 0; i < rules.length; i += 1) if (rules[i][0].test(p)) return rules[i][1];
  return null;
}

// Whether this account may send this request: true unless it is one page's own and the
// account holds none of its pages.
export function requestHeld(account, base, path, bases) {
  if (!isRoleUser(account)) return true;
  const need = pagesForRequest(base, path, bases);
  return !need || holdsAny(account, need);
}

// ── Home, for a role user ─────────────────────────────────────────────────────────────
// Home is the full platform's Home with everything an unticked page feeds left out. The
// server has already narrowed the value card, the P&L heads, the Hoist Score bars and the
// Pending queue; what is left to do here is to leave out a tile that now holds nothing, and
// never to let a sample figure stand in for a read that has not answered - for anyone else
// that is a figure about no real estate, for a role user it would also be a figure about a
// page they do not hold.

// The Hoist Score's bars (logic/homeLive.js BARS) and the page each one's records belong to.
const BAR_PAGE = { contracts: 'vendors', assets: 'assets', meters: 'energy', certificates: 'compliance' };

// Home's stock questions that are about one page's records. A question not listed here - the
// person's own saved cards, "What needs my approval today?" - is everyone's.
const PINNED_PAGES = {
  'Which buildings put me at risk this month?': ['compliance'],
  'Which vendors are blocked right now?': ['vendors', 'compliance']
};

// Country codes as the hero line prints them (renderVals COUNTRY_SHORT).
const COUNTRY_NAME = { GB: 'UK', UK: 'UK', US: 'US', SG: 'Singapore', AE: 'UAE' };
const isLive = (note) => /^Live\b/.test(String(note || ''));

// The Home values a role user sees in place of the full platform's, or null for an account
// with every page. `vals` is renderVals' own; `hero` is homeLive's hero counts.
export function roleHome(account, vals, hero) {
  if (!isRoleUser(account)) return null;
  const v = vals || {};
  const h = hero || {};
  const bars = (v.hoistBars || []).filter((b) => b && b.note !== 'seed' && holdsPage(account, BAR_PAGE[b.key]));
  const pvRows = isLive(v.pvNote) ? (v.pvRows || []) : [];
  const pnlTop = isLive(v.pnlNote) ? (v.pnlTop || []) : [];
  const stats = [];
  if (v.heroLive) {
    const n = Number(h.buildings) || 0;
    stats.push(n + (n === 1 ? ' building hoisted' : ' buildings hoisted'));
    if (holdsPage(account, 'compliance')) {
      const c = Number(h.certificates) || 0;
      stats.push(c + (c === 1 ? ' certificate on record' : ' certificates on record'));
    }
    const countries = Array.isArray(h.countries) ? h.countries : [];
    if (countries.length) stats.push(countries.map((c) => COUNTRY_NAME[c] || c).join(', '));
    else if (holdsPage(account, 'vendors')) stats.push((Number(h.vendors) || 0) + ' vendors');
  }
  // A tile that is not drawn carries nothing either: a sample total left in the page is
  // still a figure about a page this person does not hold.
  const valueLive = isLive(v.pvNote);
  const pnlLive = isLive(v.pnlNote);
  const scoreLive = !(v.hoistBars || []).some((b) => b && b.note === 'seed');
  return {
    hoistBars: bars,
    hoistScore: scoreLive ? v.hoistScore : { value: '—', band: '', gap: '', note: '' },
    hoistScoreNote: scoreLive ? v.hoistScoreNote : '',
    pvRows: pvRows,
    pvTotal: valueLive ? v.pvTotal : '—',
    pvNote: valueLive ? v.pvNote : '',
    pnlTop: pnlTop,
    pnlSaved: pnlLive ? v.pnlSaved : '—',
    pnlNote: pnlLive ? v.pnlNote : '',
    heroStats: stats,
    pinned: (v.pinned || []).filter((p) => { const need = p && PINNED_PAGES[p.label]; return !need || holdsAny(account, need); }),
    homeTiles: { score: bars.length > 0, pnl: pnlTop.length > 0, value: pvRows.length > 0 }
  };
}

// ── buttons that write into another page ──────────────────────────────────────────────
// A button on a ticked page can create or change a record in another page's register. It
// needs EVERY page listed: nobody creates a record in a register they cannot see. The server
// refuses the call as well; this is so the button is not offered, because a refusal is never
// shown as an error and a button that silently does nothing is a defect. An action that is
// not listed is not offered to a role user: a new one must be given its pages here first.
const ACTION_PAGES = {
  // Assets: both raise work for the maintenance register (and email its vendor).
  assetWorkOrder: ['assets', 'ops'],
  assetInspection: ['assets', 'ops'],
  // The asset's own investigation, from its own sources.
  assetInvestigate: ['assets'],
  // Compliance: writes the vendor master.
  certificateCreateVendor: ['compliance', 'vendors']
};

export function canAct(account, action) {
  if (!isRoleUser(account)) return true;
  const need = ACTION_PAGES[action];
  return !!need && holdsAll(account, need);
}

// The asset drawer's lines. Its certificates, and its vendor's accreditations, are the
// compliance register's: without that page the two lines are left out rather than shown as
// unreadable. The same array for an account that holds it.
const COMPLIANCE_LINES = ['certificates', 'vendor accreditation'];
export function assetChain(account, chain) {
  if (holdsPage(account, 'compliance')) return chain;
  return (chain || []).filter((c) => !c || COMPLIANCE_LINES.indexOf(c.a) < 0);
}
