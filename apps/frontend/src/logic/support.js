// support — Support is a conversation with the orchestrator, not a page.
//
// Agreed on the 7 Oct 2026 call (Aasim, Hussain): Hoistra is AI-native, so pressing Support
// opens the orchestrator in a support session instead of a help-centre page. The old page's six
// topics become questions the orchestrator can be asked; the session ends when the user says it
// is sorted; after that, a new message starts a new request; every request is listed, open or
// resolved, in the Support space; and reaching the Plenum team is an email drafted from the
// conversation, sent from Hoistra's own mail service.
//
// A support session is an ordinary conversation record (logic/sessions.js) carrying `support`:
//   { status: 'open' | 'resolved', openedAt, resolvedAt, from, emailedAt? }
// It lives on the record in this browser, like a session's space does — the server's thread
// list has no field for it, so the status does not follow the user to another device.
//
// The orchestrator answers from the guide below, sent as the page context (chatContext()), so
// support needs no new agent on the server. Questions about the user's own records still go
// to the engines through the same orchestrator.
//
// Pure functions are tested in test/support.test.mjs; the methods at the bottom are mixed into
// HoistraLogic.prototype (`this` is the controller) and covered in test/supportFlow.test.mjs.
import { scrubInternal } from './publicText.js';

const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};

// Where "Email Plenum" writes to: the address the prototype Support page showed (confirmed by
// Hussain, 7 Oct 2026). A deployment can point it elsewhere without a code change.
export const SUPPORT_EMAIL = String(env.VITE_SUPPORT_EMAIL || '').trim() || 'support@hoistra.com';

// The Support space's key. Not one of the four engine spaces: those are filed by which engine
// answered, a request is filed by being a request.
export const SUPPORT_SPACE_KEY = 'support';

// The six topics of the old help centre, as prompts. Each question is asked as written.
export const SUPPORT_TOPICS = [
  {
    key: 'ingest', name: 'Ingesting documents', icon: 'ph-upload-simple',
    blurb: 'Uploading, the file types Hoistra reads, and what to do when a document is held.',
    prompts: [
      'How do I upload a certificate for a building?',
      'Which file types can I upload?',
      'How does importing a spreadsheet from my CAFM work?',
      'Why was my document held instead of filed?'
    ]
  },
  {
    key: 'access', name: 'Buildings & access', icon: 'ph-buildings',
    blurb: 'Adding buildings, inviting people, and what admins can do that users cannot.',
    prompts: [
      'How do I add a building?',
      'How do I invite a colleague?',
      'How do I limit someone to certain buildings?',
      'What can an admin do that a user cannot?'
    ]
  },
  {
    key: 'audit', name: 'Audit trail', icon: 'ph-list-magnifying-glass',
    blurb: 'Who did what and when, and reading the vendor history.',
    prompts: [
      'Where do I see who changed what?',
      'How do I see when a vendor was blocked or reassigned?',
      'Can I export the audit trail?'
    ]
  },
  {
    key: 'billing', name: 'Credits & billing', icon: 'ph-coins',
    blurb: 'How usage is counted, and who to ask about plans and invoices.',
    prompts: [
      'How are credits used?',
      'Where can I see our usage?',
      'How do I change our plan or get an invoice?'
    ]
  },
  {
    key: 'security', name: 'Security & data', icon: 'ph-shield-check',
    blurb: 'Signing in, passwords, sessions, and taking your data out.',
    prompts: [
      'How do I change my password?',
      'How do I sign out of every device?',
      'Does Hoistra support single sign-on?',
      'How do I export everything we hold in Hoistra?'
    ]
  },
  {
    key: 'integrations', name: 'Integrations', icon: 'ph-plugs-connected',
    blurb: 'Connecting a CAFM or CMMS, and bringing its data in.',
    prompts: [
      'Which systems can Hoistra connect to?',
      'How do I bring in data from my CAFM?',
      'Is there an API or webhooks?'
    ]
  }
];

// What the orchestrator answers from. Every line is true of the app as it stands; anything not
// here the orchestrator is told to say it does not know, and to offer the email.
const GUIDE = {
  ingest: [
    'Documents can be attached in several places: the paperclip beside the orchestrator message box, Ingest on the Home bar, Ingest documents on the Hoist Graph (Buildings page), Ingest a document on a building card, Upload on the Compliance page, and Ingest documents now right after hoisting a building.',
    'Hoistra reads PDF, Word (.doc, .docx), images, CSV and Excel (.csv, .xls, .xlsx). Up to 10 files go in one send.',
    'PDF, Word and images are read and indexed so they can be searched and asked about.',
    'When files are attached, a Filing against… picker asks which building they are for. Filed against a building, a document is checked against that building and recorded in the audit trail. Sent with no building, it is indexed and searchable but not checked, not filed and not in the audit trail.',
    'A document that does not match its building is held, not filed, and Hoistra says why in the conversation. Type the reason and it goes back to the check; reply "yes" or "file it anyway" to file it, or "no" or "cancel" to reject it; Not now leaves it held. One held document is answered at a time.',
    'CSV and Excel files are a migration, not an upload: send them with nothing typed (or press Migrate it) and the migration opens in the conversation. It runs in 10 steps; at each review step it stops, marked Awaiting your review, until you confirm how keys, tables, columns, fields and the hierarchy were read. Nothing is written until the last step, Confirm & write, which takes two clicks. A migration can be cancelled before then.',
    'Every upload control is hidden for an account whose Can ingest data is switched off. An administrator switches it on in Users & access. Administrators can always ingest.'
  ],
  access: [
    'Adding a building is called hoisting it: Hoist a building on the Buildings page, or say "hoist a building" to the orchestrator. Only administrators can. The form asks for the building name, country, region or state, primary use, floors, floor area in square metres, use mix, metering, a building code and, optionally, a site; Write the record saves it, and then documents can be ingested straight away or later.',
    'People are managed in Users & access (administrators, in Admin view). Invite user asks for the full name, work email, the buildings they can see (Allocate to buildings) and whether they can ingest data, then Send invitation. The invitee finishes with Activate account from the email.',
    'On each person in Users & access an administrator can change their buildings, switch Can ingest data on or off, see their usage, and suspend or reactivate them. Removing someone suspends them; nothing is deleted.',
    'Roles are user and admin. The invitation has no role choice and there is no control in the app to change a role — the user should email Plenum for that.',
    'Administrators switch between Admin view and User view in the account menu (the initial in the top right). Admin view holds Integrations, Users & access, Hoist Crons, Chat memory, Hoist Traces, Skill lab and Audit trail; User view holds Buildings, Compliance, Vendors, Energy, Assets and Maintenance.',
    'An account with more than one building has a building picker in the top bar to narrow everything to one building.'
  ],
  audit: [
    'Audit trail is in Admin view. It has two tabs: Ingestion audit trail and Vendor audit trail.',
    'Ingestion audit trail lists every document decision. It can be searched by person, document or building, narrowed by building or person, by Today, 7 days, 14 days or All, and by outcome: Accepted, Reassigned, Overridden or Rejected.',
    'Vendor audit trail lists each time a vendor was reassigned, blocked or cleared, searchable and narrowed by the same date ranges. It records from late September 2026, so earlier blocks are not in it.',
    'Neither tab has an export. For an audit extract the user should email Plenum.'
  ],
  billing: [
    'Hoistra has no billing, plans or invoices page in the app. Plans, credits, invoices and plan changes are handled by the Plenum team — the user should press Email Plenum and say what they need.',
    'Administrators can see each person\'s usage (questions asked and ingests run) in Users & access.',
    'Never quote prices, credit amounts or plan names.'
  ],
  security: [
    'Sign-in is with an email address and a password. A 6-digit code is emailed to verify the email address and to reset a forgotten password. Single sign-on is not available yet.',
    'Change password is in the account menu (the initial in the top right); changing it signs out every session.',
    'Sign out everywhere, in the same menu, ends every signed-in session on every device. Sign out ends only this one. Signing out in one tab signs out the others in that browser.',
    'Administrators in Admin view have Export in the top bar: it first shows what the file holds and what is deliberately left out, then Download the zip gives one CSV per table and a manifest. Passwords and credentials are never in it.',
    'Reset page data, in Users & access (administrators), clears one area — compliance, contracts, assets, energy or maintenance — for one building or all of them, after typing the name to confirm. It cannot be undone.',
    'Data residency, retention periods and the data processing agreement are not in the app — the user should email Plenum for them. Never state where data is stored.'
  ],
  integrations: [
    'Integrations is in Admin view. It lists the kinds of source Hoistra can read: CSV, Excel, JSON, XML, Parquet, PostgreSQL, MySQL, SQL Server, MongoDB, OData, REST API and SOAP. No live connection is set up from the page today; Connect on a source opens the request in the orchestrator conversation.',
    'Data from a CAFM or CMMS comes in as a spreadsheet export through the migration: attach the CSV or Excel in the orchestrator and confirm each step before anything is written.',
    'There are no API keys and no webhooks in the app (the Custom API tab says no ingest token has been issued). For API access the user should email Plenum.'
  ]
};

// "SUP-3F9A1C": read from the thread id, so the same request always has the same reference
// and nothing has to be issued or stored to have one.
export function supportRef(id) {
  const raw = String(id || '').replace(/[^a-z0-9]/gi, '').slice(0, 6).toUpperCase();
  return raw ? 'SUP-' + raw : '';
}

const norm = (q) => String(q || '').trim().toLowerCase().replace(/\s+/g, ' ');

// "I need support", "help" — a request to open Support, with no question in it yet.
const BARE = /^(?:please\s+)?(?:(?:i\s+)?(?:need|want|would like)\s+|(?:can\s+i\s+)?get\s+)?(?:some\s+)?(?:help|support)(?:\s+please)?[\s.!?]*$/;
export function isBareSupportAsk(q) { return BARE.test(norm(q)); }

// A message asking for support, with or without a question: "I need help uploading an EICR",
// "raise a support ticket". Only the user's own need counts — "which buildings need support"
// is a question about buildings.
const ASKS = [
  /^(?:please\s+)?(?:i\s+)?(?:need|want|would like)\s+(?:some\s+)?(?:help|support)\b/,
  /\b(?:raise|open|log|create|submit)\s+(?:a\s+)?(?:support\s+)?(?:ticket|request)\b/,
  /\bcontact\s+(?:plenum|hoistra)?\s*support\b/
];
export function isSupportAsk(q) {
  const t = norm(q);
  if (!t) return false;
  return isBareSupportAsk(t) || ASKS.some((re) => re.test(t));
}

// Asking for a person: answered with the email to Plenum, not by the orchestrator.
const PERSON = /\b(?:plenum|support team|support|a human|human|a person|someone|somebody|the team)\b/;
const REACH = /\b(?:talk|speak|chat)\s+(?:to|with)\b|\b(?:contact|email|e-mail|mail|reach|escalate)\b/;
export function wantsPlenum(q) {
  const t = norm(q);
  if (!t) return false;
  if (!REACH.test(t)) return false;
  // "email the vendor", "contact for Apex Lifts" — someone else is being reached.
  if (/\b(?:vendor|contractor|supplier|landlord|tenant|engineer)\b/.test(t)) return false;
  return PERSON.test(t) && !/\bsupport hours\b/.test(t);
}

export function supportStatus(rec) {
  return rec && rec.support && (rec.support.status === 'open' || rec.support.status === 'resolved') ? rec.support.status : null;
}

// The record's `support`, as read back from storage: anything but an object is dropped, an
// unknown status reads as open (a request nobody closed is open).
export function readSupport(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
  const num = (x) => (typeof x === 'number' && isFinite(x) ? x : null);
  const out = {
    status: v.status === 'resolved' ? 'resolved' : 'open',
    openedAt: num(v.openedAt),
    resolvedAt: v.status === 'resolved' ? num(v.resolvedAt) : null,
    from: typeof v.from === 'string' ? v.from : ''
  };
  if (num(v.emailedAt)) out.emailedAt = v.emailedAt;
  // The help topic its first question was asked from, when it was asked from the index.
  if (typeof v.topic === 'string' && v.topic) out.topic = v.topic;
  return out;
}

// The Support space's figures, for the asker in the company they are looking at.
export function shapeSupportSpace(sessions, opts) {
  const o = opts || {};
  const owner = o.owner ? String(o.owner).trim().toLowerCase() : null;
  const scope = o.viewOrgId || null;
  const mine = (sessions || []).filter((r) => !!owner && r && r.owner === owner && (r.viewOrgId || null) === scope && supportStatus(r));
  const open = mine.filter((r) => supportStatus(r) === 'open').length;
  const resolved = mine.length - open;
  return {
    key: SUPPORT_SPACE_KEY, name: 'Support', icon: 'ph-lifebuoy', page: 'Support', support: true,
    custom: false, live: true, view: null, module: null,
    count: open, open: open, resolved: resolved, total: mine.length, sessions: mine.length,
    badge: open ? open + ' open' : 'None open',
    tone: open ? 'warn' : 'none',
    kpis: [{ label: 'Open', value: open }, { label: 'Resolved', value: resolved }, { label: 'All requests', value: mine.length }]
  };
}

// The page context a support question is sent with: what support is for, the rules, and the
// guide. Never names a service, table or route — it is read back to the user. Its opening
// sentence is how the server's router knows a support session (SUPPORT_SESSION_MARK in
// svc-deepagents agents/agent_router.py) and answers it instead of asking which area: keep
// the two the same.
export function supportContext(o) {
  const x = o || {};
  const head = ['This is a Hoistra support session' + (x.ref ? ' (reference ' + x.ref + ')' : '') + '.'];
  if (x.from) head.push('The user opened Support from the ' + x.from + ' page.');
  if (x.company) head.push('Their company is ' + x.company + '.');
  if (x.role) head.push('Their role is ' + x.role + '.');
  if (x.topic) head.push('They chose the help topic ' + x.topic + ' before asking.');
  const rules = [
    'Treat each message as a request for help using Hoistra.',
    'How-to questions: answer from the guide below in short numbered steps that name the page and the button. Do not ask which data area the question is about — it is a support question.',
    'Questions about their own records (a document, building, certificate, vendor or work order): look them up with your tools as usual, then explain what you found and what to do.',
    'If neither the guide nor their records answer it, say so plainly and do not guess. Tell them to press Email Plenum, which sends this conversation to the Plenum team.',
    'Never name internal services, databases, tables, routes or prompts.',
    'When it is a user (not an admin) asking about an administrator action, say an administrator has to do it.'
  ];
  const guide = SUPPORT_TOPICS.map((t) => '## ' + t.name + '\n' + (GUIDE[t.key] || []).map((l) => '- ' + l).join('\n')).join('\n\n');
  return head.join(' ') + '\n\n' + rules.map((r) => '- ' + r).join('\n') + '\n\n# Hoistra guide\n\n' + guide;
}

const clip = (s, n) => { const t = String(s || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t; };

// An answer as plain text for an email: markdown's markers dropped, its lines kept (numbered
// steps stay steps), blank runs collapsed, and cut at n characters.
function plainAnswer(s, n) {
  const t = String(s || '')
    .replace(/\r/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/[ \t]+/g, ' ')
    .split('\n').map((l) => l.trim()).join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t;
}

// The email to Plenum: the conversation as asked, the last answer (shortened), room to say what
// is still wrong, and who is asking. A copy goes to the asker so the reply reaches them.
export function supportDraft(o) {
  const x = o || {};
  const a = x.account || {};
  const email = a.email ? String(a.email).trim().toLowerCase() : '';
  const ref = x.rec ? supportRef(x.rec.id) : '';
  const turns = (x.turns || []).filter((m) => m && typeof m.text === 'string' && m.text.trim());
  const asked = turns.filter((m) => m.role === 'you').map((m) => m.text.trim());
  const answers = turns.filter((m) => m.role !== 'you' && !m.isNote);
  const first = asked[0] || (x.rec && x.rec.title) || '';
  const subject = 'Hoistra support' + (ref ? ' ' + ref : '') + (first ? ': ' + clip(first, 70) : '');
  const lines = ['Hello Plenum support,', ''];
  if (asked.length) {
    lines.push(asked.length === 1 ? 'I asked Hoistra:' : 'I asked Hoistra:');
    asked.forEach((q, i) => lines.push((asked.length > 1 ? (i + 1) + '. ' : '') + q));
    lines.push('');
  }
  const last = answers[answers.length - 1];
  if (last) {
    lines.push('The last answer I got:');
    lines.push(plainAnswer(scrubInternal(last.text), 600));
    lines.push('');
  }
  lines.push('What is still wrong:');
  lines.push('');
  lines.push('');
  lines.push('—');
  if (ref) lines.push('Reference: ' + ref);
  const who = [a.full_name ? String(a.full_name).trim() : '', email ? '<' + email + '>' : ''].filter(Boolean).join(' ');
  if (who) lines.push('From: ' + who);
  if (x.company) lines.push('Company: ' + x.company);
  if (x.from) lines.push('Opened from: ' + x.from);
  lines.push('Sent from Hoistra');
  return { to: SUPPORT_EMAIL, cc: email, subject: clip(subject, 100), body: lines.join('\n') };
}

// ── controller ───────────────────────────────────────────────────────────────
export const supportMethods = {
  supCurrent() {
    const s = this.state;
    if (!s.sessionId) return null;
    return (s.sessions || []).find((r) => r.id === s.sessionId) || null;
  },

  // Support is on for the conversation on screen: a request already made, or Support pressed
  // and the first question not asked yet. A pending request only counts on the conversation
  // page — pressed and then left for another page, the next question there is not support.
  supIsOn() {
    const rec = this.supCurrent();
    if (rec) return !!supportStatus(rec);
    return !!this.state.supportPending && this.state.view === 'chat';
  },

  // Support pressed (account menu, top bar, the Support space, "I need support"): the
  // orchestrator page in a support session. An open request already on screen stays; `fresh`
  // (New request) always starts another.
  openSupport(opts) {
    const o = opts || {};
    const s = this.state;
    const rec = this.supCurrent();
    if (!o.fresh && s.view === 'chat' && (supportStatus(rec) === 'open' || (!rec && s.supportPending))) {
      if (o.topic) this.setState({ supTopic: o.topic });
      return this.supFocus();
    }
    if (this._ccAbort) this._ccAbort.abort();
    clearInterval(this._orchTick);
    if (typeof this.mgDetach === 'function') this.mgDetach();
    // Where it was pressed: the page under it, or — pressed from a conversation — the page that
    // conversation was asked from.
    const from = s.view === 'chat' ? ((rec && rec.page) || s.supFrom || 'Orchestrator') : this.ctxLabel();
    this.setState({
      sessionId: null, ccChat: [], ccBusy: false, ccStream: null, ccTraceIdx: null, ccStepsOpen: {}, ccEditIdx: null, ccEditText: '',
      flow: null, flowDone: '', orchOpen: false, orchTask: null, orchDone: 0, acctOpen: false, queueOpen: false, paletteOpen: false, detail: null,
      cbBuildingId: null, cbBuildingName: '', cbPickerOpen: false, ccCaseId: null, ccCaseDoc: '', ccCaseQuestion: '',
      supportPending: true, supFrom: from, supTopic: o.topic || s.supTopic || SUPPORT_TOPICS[0].key, supNotYet: null,
      supAskedTopic: '', supCustom: ''
    });
    this.openChat();
    this.supFocus();
  },

  // A question from the help index — listed, or typed in its own field — is asked under the
  // topic on screen, so the orchestrator knows what area it is about. One typed in the composer
  // is not tied to whichever topic happened to be open.
  supAskFromIndex(q) {
    const text = String(q || '').trim();
    if (!text) return;
    this.setState({ supAskedTopic: this.state.supTopic || '' });
    this.askScoped(text);
  },

  supAskCustom() {
    const text = String(this.state.supCustom || '').trim();
    if (!text) return;
    this.setState({ supCustom: '' });
    this.supAskFromIndex(text);
  },

  supFocus() {
    if (typeof document === 'undefined') return;
    setTimeout(() => { const el = document.getElementById('chat-composer'); if (el) el.focus(); }, 80);
  },

  // Before a message is asked (ccAsk): the cases support answers itself. Returns true when the
  // message has been dealt with and must not go to the orchestrator.
  supIntercept(q) {
    const s = this.state;
    const rec = this.supCurrent();
    // "I need support" with a request still open: back to it — nothing is asked.
    if (isBareSupportAsk(q) && supportStatus(rec) === 'open') {
      if (s.view !== 'chat') this.openChat();
      this.supFocus();
      return true;
    }
    // The Home bar and the page docks continue the conversation last on screen. A request left
    // for another page does not take that page's questions: they start a conversation of their
    // own (a support question is still a support question, below).
    if (supportStatus(rec) && s.view !== 'chat' && !isSupportAsk(q) && !wantsPlenum(q)) {
      this.setState({ sessionId: null, ccChat: [], ccTraceIdx: null, ccStepsOpen: {}, flow: null, flowDone: '', supNotYet: null });
      return false;
    }
    const on = this.supIsOn();
    // Closed: the next message is a new request, not a follow-up to the closed one.
    if (supportStatus(rec) === 'resolved') {
      this.setState({ sessionId: null, ccChat: [], ccTraceIdx: null, ccStepsOpen: {}, flow: null, flowDone: '', supportPending: true, supNotYet: null });
      if (wantsPlenum(q)) return this.supAskPlenum(q);
      return false;
    }
    if (on && wantsPlenum(q)) return this.supAskPlenum(q);
    if (on) return false;
    // "I need support" from anywhere: open it, and wait for the question.
    if (isBareSupportAsk(q)) { this.openSupport(); return true; }
    // A support question as the first message of a conversation is asked as a request. In the
    // middle of a conversation it is just a question.
    if (isSupportAsk(q) && !(s.ccChat || []).length) {
      this.openSupport();
      if (wantsPlenum(q)) return this.supAskPlenum(q);
      return false;
    }
    return false;
  },

  // "Can I talk to someone at Plenum?": the question stays in the conversation, the answer is
  // the email, ready to check and send.
  supAskPlenum(q) {
    // The draft is shown on the conversation page; asked from another page, go there.
    if (this.state.view !== 'chat') this.openChat();
    this.sessionEnsure(q);
    this.setState((p) => ({
      ccChat: (p.ccChat || []).concat([
        { role: 'you', text: q },
        { role: 'bot', isNote: true, text: 'Here is an email to the Plenum team with this conversation in it. Check it, say what is still wrong, and press Send.' }
      ])
    }));
    this.supContact();
    return true;
  },

  supResolve() {
    const rec = this.supCurrent();
    if (supportStatus(rec) !== 'open') return;
    const at = Date.now();
    this.supPatch(rec.id, { status: 'resolved', resolvedAt: at });
    // The resolved line above the composer is the confirmation (supportVals supResolvedText).
    this.setState({ supNotYet: null });
  },

  supReopen() {
    const rec = this.supCurrent();
    if (supportStatus(rec) !== 'resolved') return;
    this.supPatch(rec.id, { status: 'open', resolvedAt: null });
    this.flash('Reopened — this request takes your next message.');
    this.supFocus();
  },

  // "Not yet" under an answer: the answer it was said to stays marked, and the way on is shown.
  supNotYet() {
    this.setState({ supNotYet: (this.state.ccChat || []).length });
    this.supFocus();
  },

  supPatch(id, patch) {
    this.setState((p) => ({
      sessions: (p.sessions || []).map((r) => (r.id === id && r.support ? Object.assign({}, r, { support: Object.assign({}, r.support, patch) }) : r))
    }));
  },

  supMarkEmailed(id) {
    if (!id) return;
    this.supPatch(id, { emailedAt: Date.now() });
  },

  // Email Plenum: the dock's email draft (renderVals em.send), on the conversation page. The
  // send reports what actually happened — sent, recorded in dry run, or not sent.
  supContact() {
    const s = this.state;
    const rec = this.supCurrent();
    const a = s.account || {};
    const d = supportDraft({
      rec: supportStatus(rec) ? rec : null, turns: s.ccChat || [], account: a,
      company: (s.viewOrgId && s.viewOrgName) || a.organization_name || '',
      from: (rec && rec.support && rec.support.from) || s.supFrom || ''
    });
    this.setState({
      flow: 'email', flowDone: '', emKind: 'support', emKicker: 'Email to Plenum support',
      emTo: d.to, emCc: d.cc, emSubject: d.subject, emBody: d.body,
      emQueueItemId: null, emSample: false, emFromInv: false, emReminder: false, emPrev: null, emCertId: null,
      emSentLabel: 'Support request', emSentNote: d.cc ? 'A copy went to ' + d.cc + ', so the reply reaches you.' : 'It is recorded in the email log.',
      emSupportId: supportStatus(rec) ? rec.id : null, fSubject: ''
    });
    if (typeof this.emCheckHistory === 'function') this.emCheckHistory();
  },

  // The page context for a support question (chatContext()).
  supContextNow() {
    const s = this.state;
    const rec = this.supCurrent();
    const a = s.account || {};
    const topicKey = rec ? (rec.support && rec.support.topic) || '' : s.supAskedTopic || '';
    const topic = SUPPORT_TOPICS.find((t) => t.key === topicKey);
    return supportContext({
      topic: topic ? topic.name : '',
      ref: rec ? supportRef(rec.id) : '',
      from: (rec && rec.support && rec.support.from) || s.supFrom || '',
      company: (s.viewOrgId && s.viewOrgName) || a.organization_name || '',
      role: a.role === 'superadmin' ? 'platform administrator' : s.role === 'admin' || a.role === 'admin' ? 'admin' : a.role ? 'user' : ''
    });
  }
};
