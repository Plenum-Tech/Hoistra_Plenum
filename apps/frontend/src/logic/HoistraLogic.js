// The controller: one state object, methods split by domain and mixed in.
import { Controller } from './Controller.js';
import { coreMethods } from './core.js';
import { complianceMethods } from './compliance.js';
import { vendorsMethods } from './vendors.js';
import { energyMethods } from './energy.js';
import { assetsMethods } from './assets.js';
import { maintenanceMethods } from './maintenance.js';
import { integrationsMethods } from './integrations.js';
import { renderValsMethods } from './renderVals.js';
import { complianceLiveMethods, DOCK_VIEWS } from './complianceLive.js';
import { homeLiveMethods } from './homeLive.js';
import { vendorsLiveMethods } from './vendorsLive.js';
import { buildingsLiveMethods } from './buildingsLive.js';
import { energyLiveMethods } from './energyLive.js';
import { assetsLiveMethods } from './assetsLive.js';
import { maintenanceLiveMethods } from './maintenanceLive.js';
import { graphLiveMethods } from './graphLive.js';
import { chatMethods } from './chat.js';
import { loadSession, saveSession } from './session.js';
import { loadSessions, saveSessions, sessionsMethods } from './sessions.js';
import { spacesMethods } from './spacesLive.js';
import { loadReports, saveReports, reportsMethods } from './reports.js';
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
    // Custom reports (logic/reports.js): the saved reports, the one on the page and which of
    // its refreshes is in view, and the new-report menu's fields.
    reportMenu: false, reportName: "", reportSrcId: null, reportCad: 1, reportKey: null, reportRunIdx: 0,
    reportDays: [1, 4], reportTime: "14:00",
    reports: [],
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
    pq: "", asPct: 10, asWeeks: 3, asOpenB: [], asOpenS: [], iotTick: 0, iotOpen: "AS-1042", inspQ: "", inspDraft: "", asScanB: [], asScanSec: [], asScanAllSec: true, asScanStage: 0, asScanDone: 0, asScanReport: null, asLastRun: "02:14 today", eScope: [], enMatrixOpen: false, enRatingCc: "UK", enRulesOpen: false, enPosByCc: {}, enOpenB: "Bishopsgate Tower", inv: null, invStage: 0, invSrcDone: 0, intTab: 0, intQ: "", intOpen: [], intCat: null, intModal: null, intName: "", intUrl: "", intKeyShown: false, intExtra: [],
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
    // Maintenance page's live decisions grid and KPI tiles from svc-work-order-management
    // (maintenanceLive.js) — null = not loaded, the seed decisions/cards render until it is.
    mxStatsLive: null, mxWosLive: null, mxLiveLoading: false, mxLiveError: "", mxLiveLoadedAt: null,
    // The chat page's connection line: idle | checking | connected | unreachable, and the
    // size of the orchestrator's tool catalogue when it answered.
    chatLink: "idle", chatTools: null, chatLinkError: "",
    // Users & access (logic/users.js) — every user is invited and allocated to buildings
    // in local state; the building is the access boundary for viewing and ingestion alike.
    users: AX_USERS.map((u) => ({ ...u, buildings: u.buildings.slice() })), usOpen: null, usInviteOpen: false,
    usName: "", usEmail: "", usBlds: [], usIngest: false,
    // The live read behind them (logic/usersLive.js): the /api/admin/users summary object
    // (drives the header tiles) and the company's canonical live buildings list
    // [{id, name, building_code}] every admin domain resolves names against — usersLive
    // owns axBldsLive; auditLive and ingestionLive only read it.
    usSummary: null, axBldsLive: null, usLiveLoading: false, usLiveError: "", usLiveLoadedAt: null,
    // Ingestion audit trail (logic/auditTrail.js) — every flagged ingestion, clarification,
    // override and approval. The ingestion validation agent (logic/ingestion.js) prepends to it.
    audit: AU_SEED.slice(), auFilter: "All", auOpen: null,
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
    // The session list and the saved reports have their own stores. The active session's
    // transcript comes back from its record, so the conversation page resumes as it was.
    this.state.sessions = loadSessions();
    this.state.reports = loadReports();
    const active = this.state.sessionId ? this.state.sessions.find((x) => x.id === this.state.sessionId) : null;
    if (active && active.kind === "chat") {
      this.state.ccChat = active.turns || [];
      // On a dock page the conversation lives in the dock, so a reload reopens it with the
      // transcript where it was; the chat page shows the same transcript as the page.
      if (this.state.ccChat.length && DOCK_VIEWS.indexOf(this.state.view) > -1) this.state.orchOpen = true;
    } else {
      this.state.sessionId = null;
    }
    if (this.state.view === "report" && !this.state.reports.some((r) => r.key === this.state.reportKey)) {
      this.state.view = "home"; this.state.reportKey = null;
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
    if (prev.reports !== this.state.reports) saveReports(this.state.reports);
    // Only a signed-in tab owns the stored session. A tab sitting on the gate writes nothing —
    // its animation ticks would otherwise erase the refresh token another tab is signed in with —
    // and the key is removed exactly once, on the way out.
    if (this.state.signedIn || prev.signedIn) saveSession(this.state);
  }
}

Object.assign(HoistraLogic.prototype, coreMethods, complianceMethods, vendorsMethods, energyMethods, assetsMethods, maintenanceMethods, integrationsMethods, complianceLiveMethods, homeLiveMethods, vendorsLiveMethods, buildingsLiveMethods, energyLiveMethods, assetsLiveMethods, maintenanceLiveMethods, buildingsCrudMethods, buildingsGraphMethods, graphLiveMethods, chatMethods, sessionsMethods, spacesMethods, reportsMethods, authMethods, usersMethods, usersLiveMethods, auditMethods, auditLiveMethods, ingestionMethods, ingestionLiveMethods, superAdminMethods, superAdminLiveMethods, renderValsMethods);
