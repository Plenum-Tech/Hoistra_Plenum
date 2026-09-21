// The controller: one state object, methods split by domain and mixed in.
import { Controller } from './Controller.js';
import { setActingOrg } from '../api/client.js';
import { coreMethods } from './core.js';
import { complianceMethods } from './compliance.js';
import { vendorsMethods } from './vendors.js';
import { energyMethods } from './energy.js';
import { assetsConditionMethods } from './assetsCondition.js';
import { maintenanceMethods } from './maintenance.js';
import { integrationsMethods } from './integrations.js';
import { renderValsMethods } from './renderVals.js';
import { complianceLiveMethods } from './complianceLive.js';
import { homeLiveMethods } from './homeLive.js';
import { vendorsLiveMethods } from './vendorsLive.js';
import { vendorsWriteMethods } from './vendorsWrite.js';
import { buildingsLiveMethods } from './buildingsLive.js';
import { energyLiveMethods } from './energyLive.js';
import { assetsLiveMethods } from './assetsLive.js';
import { maintenanceLiveMethods } from './maintenanceLive.js';
import { graphLiveMethods } from './graphLive.js';
import { chatMethods } from './chat.js';
import { loadSession, saveSession } from './session.js';
import { loadSessions, saveSessions, sessionsMethods } from './sessions.js';
import { spacesMethods } from './spacesLive.js';
import { FALLBACK_PRESETS, reportsMethods } from './reports.js';
import { loadHidden } from './reportCards.js';
import { buildingsCrudMethods } from './buildingsCrud.js';
import { buildingsGraphMethods } from './buildingsGraph.js';
import { AUTH_DEFAULTS, authMethods, canAdmin } from './auth.js';
import { usersMethods } from './users.js';
import { auditMethods } from './auditTrail.js';
import { ingestionMethods } from './ingestion.js';
import { superAdminMethods } from './superAdmin.js';
import { usersLiveMethods } from './usersLive.js';
import { auditLiveMethods } from './auditLive.js';
import { ingestionLiveMethods } from './ingestionLive.js';
import { superAdminLiveMethods } from './superAdminLive.js';
import { AX_USERS, SA_COMPANIES, AU_SEED } from '../data/hoistra-access.js';

export class HoistraLogic extends Controller {
  state = {
    ...AUTH_DEFAULTS,
    view: "home", module: null, answerKey: null, askedQuery: "",
    query: "", queueOpen: false, paletteOpen: false, detail: null,
    chainOpen: true, toast: "", filter: "All", navOpen: false,
    // Custom reports (logic/reports.js): server-owned now (svc-operations-intelligence's
    // /api/reports) — reports[] holds each report with its cards and latest runs, as the
    // backend shaped them; reportKey is the card id on the page. The new-card menu's fields
    // (reportMenu..reportTime) stay local until Create is pressed. reportSelected is the
    // multi-select set the grid's bulk delete acts on.
    reportMenu: false, reportName: "", reportSrcId: null, reportCad: 1, reportKey: null, reportRunIdx: 0,
    reportDays: [1, 4], reportTime: "14:00",
    // reportsOwner is the account reports[] was read for — see rpLoad. Reports are personal,
    // and one tab sees more than one account, so the rows carry who they belong to.
    reports: [], reportsOwner: null, reportsLoading: false, reportsError: "", reportsLoadedAt: null,
    reportPresets: FALLBACK_PRESETS, reportSelected: [], rpArmed: null,
    // Which cards INSIDE a report the reader has put in the tray, and whether the tray is
    // open (logic/reportCards.js). A view preference, per account, per report card.
    reportHidden: loadHidden(), reportTrayOpen: false, reportOpenBlocks: [],
    // Sessions (logic/sessions.js): every conversation and task, the active thread's id, and
    // the Sessions page's search and space filter.
    sessions: [], sessionId: null, sessionsQuery: "", sessionsFilter: null,
    // Spaces (logic/spacesLive.js): saved spaces from svc-udr (null = not loaded), the inline
    // new-space field, the space page's key and its rename field.
    spaces: null, spLoading: false, spError: "", spNew: false, spNewName: "", spBusy: false,
    spaceKey: null, spRenaming: false, spRenameText: "", spaceAsk: "",
    signedIn: false, email: "", openObj: 0, frame: 0,
    orchOpen: false, orchTask: null, orchDone: 0, orchQuery: "",
    // Dock chat transcript (compliance orchestrator) and its drag-resized width.
    // orchW null = use the flow's default width; the drag only ever widens it.
    ccChat: [], ccBusy: false, orchW: null, ccStepsOpen: {},
    // Certificate ids whose evidence request has been approved into the queue.
    ccRequested: {}, emCertId: null,
    // Documents/photos staged to ride along with the next chat question.
    ccFiles: [],
    // Partial answer while a turn streams: steps and zones as they arrive.
    // NOT ccLive — that name already holds the live certificate register (ccData()).
    ccStream: null,
    // Which turn the trace rail is showing. null = follow the conversation (the live run
    // while one is going, otherwise the newest answered turn).
    ccTraceIdx: null,
    // Seconds a running turn has been going, bumped once a second so the rail's clock
    // keeps moving between stream events rather than freezing on the last one.
    ccTick: 0,
    // Inline question editing in the transcript.
    ccEditIdx: null, ccEditText: "",
    // Per-offer in-flight flags, keyed cert_id:kind.
    ccOfferBusy: {},
    cronsGone: [], cronPulse: -1,
    flow: null, flowDone: "", fSubject: "", fVendor: "", fSpec: "", fLabel: "", fiVals: {}, bkLocked: true,
    // No building is guessed for a general "Ingest documents" open — the person picks one.
    role: "user", acctOpen: false, declStep: 0, declFor: "",
    // The building scope picker in the TopBar (logic/auth.js): whether its menu is open and
    // what has been typed into its search. A company can hold hundreds of buildings, so the
    // list is searched rather than scrolled; neither field is persisted, since a reload
    // should reopen on the current scope, not on a half-typed search.
    bldOpen: false, bldQuery: "",
    pq: "", asPct: 10, asWeeks: 3, asOpenB: [], asOpenS: [], iotOpen: null, asLocations: [], asAnoms: [], asReadings: [], asSections: [], asVar: null, asIntel: {}, inspQ: "", inspDraft: "", eScope: [], enMatrixOpen: false, enRatingCc: "UK", enRulesOpen: false, enPosByCc: {}, enOpenB: "Bishopsgate Tower", inv: null, invStage: 0, invSrcDone: 0, intTab: 0, intQ: "", intOpen: [], intCat: null, intModal: null, intName: "", intUrl: "", intKeyShown: false, intExtra: [],
    bkDate: "2026-09-16", bkWindow: "08:00–12:00",
    nv: { name: "", email: "", id: "", phone: "" }, nvSpec: "Lifts — LOLER",
    emTo: "", emSubject: "", emBody: "", emKicker: "", emKind: "",
    vendors: [],
    cFreq: "Daily", cDate: "2026-09-02", cTime: "02:00",
    ccPanel: false, ccCountries: ["UK"], ccStates: [], ccBuildings: [], ccTile: null,
    gBuilding: "Bishopsgate Tower", gChild: "asset", gTable: "building", docOpen: "Bishopsgate Tower",
    // Documents section search and pagination — independent of the buildings table's own
    // (bldQuery/bldPage), since paging one list shouldn't move the other.
    docQuery: "", docPage: 0,
    ugText: "", ugParsed: false,
    vpVendor: "v1", vpTab: 0,
    // Contract terms panel, write side (logic/vendorsWrite.js). `vpConfirmArmed` is the
    // confirmation step in front of Confirm — it exists so the count of platform defaults
    // is read before those defaults become agreed values, not after. `vpEditField` is the
    // one term open for editing, and `vpEditValue` its draft; only one row edits at a time
    // because each save is its own PATCH.
    vpConfirmArmed: false, vpEditField: "", vpEditValue: "",
    ccPivot: "buildings", ccTab: 0, ccFocus: { kind: "building", name: "Bishopsgate Tower" }, nyView: "building",
    ccQueue: null, ccQueueOpenId: null, currency: "GBP", freq: "30 min", channels: ["In-platform", "Email"],
    // Compliance register from svc-operations-intelligence (null = seed data shown).
    ccLive: null, ccLoading: false, ccError: "", ccLoadedAt: null, ccLastScan: null,
    // Scan runs on the page, not in the chat: these drive the status pill / LAST RUN.
    ccScanning: false, ccScanMsg: "",
    // Home page reads from svc-operations-intelligence (null = seed tiles shown). The raw
    // responses are kept; homeModel() shapes them once per load.
    homeRaw: null, homeLoading: false, homeError: "", homeLoadedAt: null,
    // Vendors page reads from svc-operations-intelligence (null = seed shown). Raw responses
    // are kept; vpModel() shapes them once per load.
    vpRaw: null, vpLoading: false, vpError: "", vpLoadedAt: null,
    // Buildings table from svc-operations-intelligence (null = nothing loaded; the table is
    // DB-only and shows an empty state until the endpoint answers).
    bldLive: null, bldLoading: false, bldError: "", bldLoadedAt: null, bldMeta: null,
    // Buildings table search and pagination (buildingsLive.js) — client-side, over whatever
    // the register already loaded. bldPage is 0-indexed; a new search always lands on page 1.
    bldQuery: "", bldPage: 0,
    // Energy module's buildings list search and pagination (energy.js) — same shape as the
    // Buildings table's above, over the buildings already in scope.
    enBldQuery: "", enBldPage: 0,
    // Assets page's live asset register from svc-work-order-management (assetsLive.js) —
    // null = not loaded, the condition-scan section below it stays seed-only regardless.
    asLive: null, asLiveWos: null, asLiveLoading: false, asLiveError: "", asLiveLoadedAt: null,
    // Assets page's real buildings→assets tree (asLiveGroups in assetsLive.js): which
    // building groups are expanded, and the per-building cost-drivers cache (fetched lazily
    // the first time a group opens, keyed by building_id).
    asLiveOpenB: [], asLiveCost: {},
    // The whole Maintenance page, from svc-work-order-management's /api/maintenance routes
    // (maintenanceLive.js). mxRaw null = nothing has answered, and the page is empty and
    // says so — there is no seed behind it. mxGroup is the "Group by" control, which is a
    // server parameter (group_by) rather than a client-side regroup, so changing it re-reads.
    // mxAnswer is the Ask bar's last answer, with the endpoint each figure came from.
    mxRaw: null, mxLiveLoading: false, mxLiveError: "", mxLiveLoadedAt: null,
    mxGroup: "State", mxOpenG: null,
    mxAnswer: null, mxAsked: "", mxAskBusy: false, mxAskError: "",
    // The chat page's connection line: idle | checking | connected | unreachable, and the
    // size of the orchestrator's tool catalogue when it answered.
    chatLink: "idle", chatTools: null, chatLinkError: "",
    // Users & access (logic/users.js) — every user is invited and allocated to buildings
    // in local state; the building is the access boundary for viewing and ingestion alike.
    users: AX_USERS.map((u) => ({ ...u, buildings: u.buildings.slice() })), usOpen: null, usInviteOpen: false,
    usName: "", usEmail: "", usBlds: [], usIngest: false, usArmed: null,
    // The live read behind them (logic/usersLive.js): the /api/admin/users summary object
    // (drives the header tiles) and the company's canonical live buildings list
    // [{id, name, building_code}] every admin domain resolves names against — usersLive
    // owns axBldsLive; auditLive and ingestionLive only read it.
    usSummary: null, axBldsLive: null, usLiveLoading: false, usLiveError: "", usLiveLoadedAt: null,
    // Ingestion audit trail (logic/auditTrail.js) — every flagged ingestion, clarification,
    // override and approval. The ingestion validation agent (logic/ingestion.js) prepends to it.
    audit: AU_SEED.slice(), auFilter: "All", auOpen: null,
    // The audit trail's filter bar. `auRange` opens on Today — the trail is read to answer
    // "what happened today", and All is one click away. auTotal is the server's own count,
    // filled by auLiveLoad; auNow exists so a test can pin the clock the ranges read.
    auQuery: "", auBuilding: "", auPerson: "", auRange: "Today", auTotal: 0, auNow: null,
    // The person picker: a standing scope (everyone / admins / users), one name when one is
    // picked, and the popover's own open state and type-ahead.
    auPeopleScope: "", auPeopleOpen: false, auPeopleQuery: "", auPersonId: "",
    // The building picker, the same shape: the popover's open state and its type-ahead. The
    // building in force is auBuilding above — this pair is only how it gets chosen.
    auBldOpen: false, auBldQuery: "",
    // The register's own tallies, filled by auLiveLoad — counting the fetched page instead
    // would describe 200 rows beside a line that said 919.
    auByOutcome: {}, auActors: [],
    // The live trail behind it (logic/auditLive.js): {count, by_outcome} of the last
    // successful read — the shaped rows themselves replace `audit` in place.
    auLiveRaw: null, auLiveLoading: false, auLiveError: "", auLiveLoadedAt: null,
    // The ingestion validation agent (logic/ingestion.js) — runs for every ingestion, any
    // role, any document type, before anything is recorded as ingested.
    ingOn: false, ingPhase: "setup", ingB: "Riverside Court", ingDoc: 0, ingChecked: 0, ingSwitched: false, ingReason: "", ingOutcome: null,
    // The live conversation behind it (logic/ingestionLive.js): the raw case from the last
    // validate/clarify/reassign/decide response, the picked file (null = the selected
    // sample doc), the building selected at run start, the minted upload session id, and
    // the in-flight / offline-demo / error-bubble slices.
    ingCase: null, ingFile: null, ingB0: "", ingSessionId: "", ingBusy: false,
    ingOffline: false, ingLiveErr: "", ingLiveRetriable: false,
    // Super Admin console (logic/superAdmin.js) — platform-operator overlay, deliberately
    // separate from any single company's own admin app: onboards companies, does not operate them.
    saOn: false, saSel: "c1", saNew: false, saName: "", saCc: "UK", saEmail: "",
    saCompanies: SA_COMPANIES.map((c) => ({ ...c })),
    // Set only while a superadmin is viewing/acting as a company other than their own
    // (superAdmin.js's viewAsCompany/exitViewAsCompany) — null the rest of the time,
    // including for every non-superadmin account.
    viewOrgId: null, viewOrgName: null,
    // The live console behind it (logic/superAdminLive.js): raw {companies, credits}
    // responses (month_total/billing_note ride here) and the usage cards keyed by
    // organization_id — loaded when the overlay opens (auth.js's menu item), never at mount.
    saLiveRaw: null, saCardsLive: {}, saLiveCardError: "",
    saLiveLoading: false, saLiveError: "", saLiveLoadedAt: null
  };

  constructor() {
    super();
    // Class fields are initialised before this runs, so the stored slice is merged over
    // the defaults here. A reload therefore resumes the page it was on instead of
    // dropping back to the sign-in gate.
    Object.assign(this.state, loadSession());
    // Admin view is only ever offered to an account whose real role allows it; a stored
    // mode from before the role model, or from another account, is reset.
    if (this.state.role === "admin" && !canAdmin(this.state.account)) this.state.role = "user";
    // Same check for the Super Admin console: a stored saOn from a different, since-
    // switched-to account must not open it for whoever is actually signed in now.
    if (this.state.saOn && !(this.state.account && this.state.account.role === "superadmin")) this.state.saOn = false;
    if (this.state.viewOrgId && !(this.state.account && this.state.account.role === "superadmin")) {
      this.state.viewOrgId = null; this.state.viewOrgName = null;
    }
    // getActingOrg() (api/client.js) is a plain in-memory variable, not itself persisted —
    // every API call reads it directly, so a restored viewOrgId must be primed back into
    // it here, or every report keeps reading the account's own company regardless of what
    // state.viewOrgId now says.
    setActingOrg(this.state.viewOrgId || null);
    // The session list has its own store. The active session's transcript comes back from
    // its record, so the conversation page resumes as it was. Reports are server-owned now
    // (logic/reports.js's rpLoad, kicked off from componentDidMount) — nothing to restore
    // here; a reload that lands on "report" just waits for that load like any other page.
    this.state.sessions = loadSessions();
    const active = this.state.sessionId ? this.state.sessions.find((x) => x.id === this.state.sessionId) : null;
    if (active && active.kind === "chat") {
      // The transcript comes back into state either way, so the chat page still shows it
      // and the dock continues the same thread the moment it is opened — but a reload never
      // opens the dock itself; only asking something, or the orchestrator icon, does that.
      this.state.ccChat = active.turns || [];
    } else {
      this.state.sessionId = null;
    }
  }

  // One choke point for state, so one place to persist from: the reload slice on every
  // change, the session record whenever the transcript moves, and the two stores whenever
  // their lists change.
  setState(patch, cb) {
    const prev = this.state;
    super.setState(patch, cb);
    if (prev.ccChat !== this.state.ccChat) this.sessionSync();
    if (prev.sessions !== this.state.sessions) saveSessions(this.state.sessions);
    // Only a signed-in tab owns the stored session. A tab sitting on the gate writes nothing —
    // its animation ticks would otherwise erase the refresh token another tab is signed in with —
    // and the key is removed exactly once, on the way out. `prev` lets saveSession tell,
    // on that way out, whether the SHARED slot still belongs to this tab's own account.
    if (this.state.signedIn || prev.signedIn) saveSession(this.state, prev);
  }
}

Object.assign(HoistraLogic.prototype, coreMethods, complianceMethods, vendorsMethods, energyMethods, assetsConditionMethods, maintenanceMethods, integrationsMethods, complianceLiveMethods, homeLiveMethods, vendorsLiveMethods, vendorsWriteMethods, buildingsLiveMethods, energyLiveMethods, assetsLiveMethods, maintenanceLiveMethods, buildingsCrudMethods, buildingsGraphMethods, graphLiveMethods, chatMethods, sessionsMethods, spacesMethods, reportsMethods, authMethods, usersMethods, usersLiveMethods, auditMethods, auditLiveMethods, ingestionMethods, ingestionLiveMethods, superAdminMethods, superAdminLiveMethods, renderValsMethods);
