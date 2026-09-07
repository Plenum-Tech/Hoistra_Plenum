// The controller: one state object, methods split by domain and mixed in.
import { Controller } from './Controller.js';
import { SESSIONS } from './constants.js';
import { coreMethods } from './core.js';
import { complianceMethods } from './compliance.js';
import { vendorsMethods } from './vendors.js';
import { energyMethods } from './energy.js';
import { integrationsMethods } from './integrations.js';
import { renderValsMethods } from './renderVals.js';
import { complianceLiveMethods } from './complianceLive.js';

export class HoistraLogic extends Controller {
  state = {
    view: "home", module: null, answerKey: null, askedQuery: "",
    query: "", queueOpen: false, paletteOpen: false, detail: null,
    chainOpen: true, toast: "", filter: "All", navOpen: false,
    reportMenu: false, reportName: "", reportSrc: 0, reportCad: 1, reportKey: "assetrisk",
    reportDays: [1, 4], reportTime: "14:00",
    reports: [{ key: "assetrisk", name: "Risky Buildings", badge: "30 min", cad: "Refresh every 30 minutes", src: "Which buildings are carrying asset risk?", ready: true }],
    signedIn: false, email: "", openObj: 0, frame: 0,
    orchOpen: false, orchTask: null, orchDone: 0, orchQuery: "",
    cronsGone: [], cronPulse: -1,
    flow: null, flowDone: "", fSubject: "", fVendor: "", fSpec: "", fLabel: "", fiVals: {}, bkLocked: true,
    role: "user", acctOpen: false, declStep: 0, declFor: "Bishopsgate Tower",
    pq: "", eScope: [], enMatrixOpen: false, enRatingCc: "UK", enRulesOpen: false, enOpenB: "Bishopsgate Tower", inv: null, invStage: 0, invSrcDone: 0, intTab: 0, intQ: "", intOpen: [], intCat: null, intModal: null, intName: "", intUrl: "", intKeyShown: false, intExtra: [],
    decl: { name: "", cc: "UK", state: "Greater London", use: "Commercial", floors: "", area: "", mixC: "", mixR: "", mixL: "", mixM: "", mixH: "", mixT: "" }, bkDate: "2026-09-16", bkWindow: "08:00–12:00",
    nv: { name: "", email: "", id: "", phone: "" }, nvSpec: "Lifts — LOLER",
    emTo: "", emSubject: "", emBody: "", emKicker: "", emKind: "",
    vendors: [],
    cFreq: "Daily", cDate: "2026-09-02", cTime: "02:00",
    sessions: SESSIONS.slice(),
    ccPanel: false, ccCountries: ["UK"], ccStates: [], ccBuildings: [], ccTile: null,
    gBuilding: "Bishopsgate Tower", gChild: "asset", gTable: "building", docOpen: "Bishopsgate Tower",
    ugText: "", ugParsed: false,
    vpVendor: "v1", vpTab: 0,
    ccPivot: "buildings", ccTab: 0, ccFocus: { kind: "building", name: "Bishopsgate Tower" }, nyView: "building",
    ccQueue: null, ccQueueOpenId: null, currency: "GBP", freq: "30 min", channels: ["In-platform", "Email"],
    // Compliance register from svc-operations-intelligence (null = seed data shown).
    ccLive: null, ccLoading: false, ccError: "", ccLoadedAt: null, ccLastScan: null
  };
}

Object.assign(HoistraLogic.prototype, coreMethods, complianceMethods, vendorsMethods, energyMethods, integrationsMethods, complianceLiveMethods, renderValsMethods);
