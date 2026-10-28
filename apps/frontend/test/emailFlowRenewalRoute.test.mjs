// emailFlowRenewalRoute — what the renewal-email route now says, and the offer button's states
// (28 Oct 2026).
//
// POST /api/compliance/certificates/renewal-email now also answers `reused` (it handed back the
// Approvals item already pending for that certificate instead of queueing another) and
// `to_is_fallback` (no vendor contact was found, so `to` is the default PM address). The draft
// says both: the note does not claim a new Approvals item that was not made, and the email card
// says when the address is not the vendor's. The offer button shows what it is doing beside its
// label ("Drafting…", "Open below", "Failed — press to retry") without changing the label.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { complianceApi } = await import('../src/api/compliance.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');

// The two components, bundled as Vite would and rendered to static markup.
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, '.email-flow-renewal-route.bundle.mjs');
const res = await build({
  stdin: { contents: "export { default as EmailComposer } from '../src/components/shell/EmailComposer.jsx';\nexport { default as ComplianceAnswer } from '../src/components/shell/ComplianceAnswer.jsx';", resolveDir: here, loader: 'js' },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', '@openuidev/lang-core', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent',
  plugins: [{ name: 'lazy-to-real', setup(b) { b.onResolve({ filter: /LazyOpenUIAnswer\.jsx$/ }, () => ({ path: path.join(here, '../src/openui/OpenUIAnswer.jsx') })); } }]
});
writeFileSync(out, res.outputFiles[0].text);
after(() => rmSync(out, { force: true }));
const { EmailComposer, ComplianceAnswer } = await import(pathToFileURL(out).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

const real = { sent: opsApi.sentEmails, send: opsApi.sendEmail, renewal: complianceApi.draftRenewalEmail };
let c = null;
let posts = [];
let reply = null;
beforeEach(() => {
  posts = [];
  reply = (id) => ({ ok: true, certificate_id: id, queue_item_id: 'qi-' + id, reused: false, to_is_fallback: false,
    email_draft: { to: 'renewals@' + id + '.example', subject: 'Renewal required — ' + id, body: 'Please renew ' + id + '.' } });
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  complianceApi.draftRenewalEmail = async (id) => { posts.push(id); return reply(id); };
});
afterEach(() => {
  Object.assign(opsApi, { sentEmails: real.sent, sendEmail: real.send });
  complianceApi.draftRenewalEmail = real.renewal;
  if (c) { clearInterval(c._orchTick); clearInterval(c._ccTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

const REG = shapeLiveCompliance({ certificates: [
  { id: 'c-fgas-1', certificate_type_code: 'FGAS', certificate_type_name: 'F-Gas Certificate', cert_scope: 'Building', building_name: 'Bishopsgate Tower',
    vendor_name: 'Apex Mechanical', days_to_expiry: 1, expiry_date: '2026-10-29', country_code: 'UK', certificate_number: 'FG-101' },
  { id: 'c-fgas-2', certificate_type_code: 'FGAS', certificate_type_name: 'F-Gas Certificate', cert_scope: 'Building', building_name: 'Bishopsgate Tower',
    vendor_name: 'Apex Mechanical', days_to_expiry: -3, expiry_date: '2026-10-25', country_code: 'UK', certificate_number: 'FG-102' }
] });
const OFFERS = [
  { cert_id: 'c-fgas-1', cert_name: 'FGAS', owner: 'Bishopsgate Tower', kind: 'renewal_email', label: 'Draft renewal email', reason: 'Expires tomorrow' },
  { cert_id: 'c-fgas-2', cert_name: 'FGAS', owner: 'Bishopsgate Tower', kind: 'renewal_email', label: 'Draft renewal email', reason: 'Lapsed' }
];
const RICH = { narrative: 'Two F-Gas certificates at Bishopsgate Tower need renewing.', sections: [], groups: [], kpis: [], actions: [], insights: [],
  certificates: [], pending: [], offers: OFFERS, validation: null, steps: [], cost: null };
const CHAT = [{ role: 'you', text: 'Which certificates need renewing?' }, { role: 'bot', text: RICH.narrative, rich: RICH }];
const boot = (state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, view: 'chat', orchOpen: false, ccLive: REG, ccChat: CHAT.slice(),
    account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates', role: 'admin', can_ingest: true,
      buildings: [{ id: 'bld-1', name: 'Bishopsgate Tower' }] } }, state));
  return c;
};
const offer = (id) => c.renderVals().orchChat[1].rich.offers.find((o) => o.cert_id === id);
const lastNote = () => c.state.ccChat[c.state.ccChat.length - 1].text;
const noop = () => {};

test('a pending Approvals item handed back (reused) is said as such, and the send is tied to it', async () => {
  boot({});
  reply = (id) => ({ ok: true, queue_item_id: 'qi-old-' + id, reused: true, to_is_fallback: false, email_draft: { to: 'r@v.example', subject: 'S', body: 'B' } });
  await offer('c-fgas-1').run();
  assert.equal(lastNote(), 'Renewal email drafted for F-Gas Certificate — Bishopsgate Tower — it was already on the Approvals card, so nothing new was added. Check the address, then press Approve & send.');
  assert.doesNotMatch(lastNote(), /added to the Approvals card/);
  assert.equal(c.state.emQueueItemId, 'qi-old-c-fgas-1');
  assert.equal(c.renderVals().em.queued, true);
});

test('a draft addressed to the default PM address says so in the card and the note; others do not', async () => {
  boot({});
  reply = (id) => ({ ok: true, queue_item_id: 'qi-' + id, reused: false, to_is_fallback: id === 'c-fgas-1',
    email_draft: { to: id === 'c-fgas-1' ? 'pm@northbridge.example' : 'renewals@apex.example', subject: 'S', body: 'B' } });
  await offer('c-fgas-1').run();
  assert.equal(c.renderVals().em.toFallback, true);
  assert.equal(lastNote(), 'Renewal email drafted for F-Gas Certificate — Bishopsgate Tower and added to the Approvals card. No vendor contact was found, so it is addressed to the default PM address. Check the address, then press Approve & send.');
  // Reopened from the remembered draft: still said.
  c.renderVals().fCancel();
  assert.equal(c.renderVals().em.toFallback, false, 'nothing open, nothing said');
  await offer('c-fgas-1').run();
  assert.equal(c.renderVals().em.toFallback, true);
  assert.deepEqual(posts, ['c-fgas-1']);
  // Another certificate whose vendor was found.
  await offer('c-fgas-2').run();
  assert.equal(c.renderVals().em.toFallback, false);
  // Another kind of email for the same certificate never inherits it.
  await offer('c-fgas-1').run();
  c.setState({ emKind: 'evidence' });
  assert.equal(c.renderVals().em.toFallback, false);
});

test('the email card draws the default-address line only when it is so', () => {
  const EM = { kicker: 'Draft renewal email', to: 'pm@northbridge.example', subject: 'S', body: 'B', prevShow: 'none', setTo: noop, setSubject: noop, setBody: noop,
    packShow: 'none', sendBusy: false, sample: false, send: noop, queued: true, canCc: false, cc: '', setCc: noop };
  const draw = (em) => renderToStaticMarkup(React.createElement(EmailComposer, { em: Object.assign({}, EM, em), onCancel: noop, variant: 'page' }));
  assert.match(draw({ toFallback: true }), /<div class="emc-line is-warn" role="note"><i class="ph ph-warning" aria-hidden="true"><\/i><span>No vendor contact was found, so this is addressed to the default PM address\. Change To if it should go to the vendor\.<\/span><\/div>/);
  assert.doesNotMatch(draw({}), /default PM address/);
});

test('the offer says what it is doing beside its label: Drafting…, then where its draft is open', async () => {
  boot({});
  let release;
  complianceApi.draftRenewalEmail = (id) => { posts.push(id); return new Promise((r) => { release = () => r(reply(id)); }); };
  const running = offer('c-fgas-1').run();
  assert.equal(offer('c-fgas-1').status, 'Drafting…');
  assert.equal(offer('c-fgas-1').label, 'Draft renewal email');
  assert.match(offer('c-fgas-1').aria, /\(drafting…\)$/);
  release();
  await running;
  assert.equal(offer('c-fgas-1').status, 'Open below', 'the chat page draws it under the answer');
  assert.equal(offer('c-fgas-2').status, '');
  // In the dock the draft is at the top of the dock, above the answer.
  c.setState({ view: 'cc', orchOpen: true });
  assert.equal(offer('c-fgas-1').status, 'Open above');
  assert.match(offer('c-fgas-1').aria, /\(draft open above\)$/);
  c.renderVals().fCancel();
  assert.equal(offer('c-fgas-1').state, 'done');
  assert.equal(offer('c-fgas-1').status, '');
});

test('a draft that failed marks its button, and pressing it again retries and clears the mark', async () => {
  boot({});
  complianceApi.draftRenewalEmail = async (id) => { posts.push(id); return { ok: false, error: 'service unavailable' }; };
  await offer('c-fgas-1').run();
  assert.equal(offer('c-fgas-1').state, 'error');
  assert.equal(offer('c-fgas-1').status, 'Failed — press to retry');
  assert.match(offer('c-fgas-1').aria, /^Draft renewal email — F-Gas Certificate, Bishopsgate Tower \(failed — press to retry\)$/);
  assert.equal(typeof offer('c-fgas-1').run, 'function');
  assert.match(lastNote(), /^Could not complete “Draft renewal email” for F-Gas Certificate — Bishopsgate Tower: service unavailable$/);
  assert.notEqual(c.state.flow, 'email', 'no card opens for a draft that was not made');
  complianceApi.draftRenewalEmail = async (id) => { posts.push(id); return reply(id); };
  await offer('c-fgas-1').run();
  assert.deepEqual(posts, ['c-fgas-1', 'c-fgas-1']);
  assert.equal(offer('c-fgas-1').state, 'open');
  c.renderVals().fCancel();
  assert.equal(offer('c-fgas-1').state, 'done');
});

test('the button draws the status as UI beside the label, and the failure with a retry icon', () => {
  const answer = (offers) => renderToStaticMarkup(React.createElement(ComplianceAnswer, { rich: Object.assign({}, RICH, { offers }), open: false, onToggle: noop, fallbackText: '' }));
  const base = { cert_id: 'c-1', kind: 'renewal_email', label: 'Draft renewal email', forName: 'F-Gas · FG-101', aria: 'Draft renewal email — F-Gas', run: noop };
  const busy = answer([Object.assign({}, base, { state: 'busy', status: 'Drafting…', run: undefined })]);
  assert.match(busy, /<span class="ans-offer-label">Draft renewal email<span class="ans-offer-state" aria-hidden="true">Drafting…<\/span><\/span>/);
  assert.match(busy, /class="ans-offer is-busy" disabled="" aria-busy="true"/);
  const bad = answer([Object.assign({}, base, { state: 'error', status: 'Failed — press to retry' })]);
  assert.match(bad, /<button type="button" class="ans-offer is-error" aria-label="Draft renewal email — F-Gas"><i class="ph ph-arrow-clockwise" aria-hidden="true"><\/i>/);
  assert.match(bad, /<span class="ans-offer-state" aria-hidden="true">Failed — press to retry<\/span>/);
  assert.doesNotMatch(answer([base]), /ans-offer-state/);
});

// Review fixes (28 Oct 2026).
test('the renewal draft carries the viewed company, like every other compliance call', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../src/api/compliance.js', import.meta.url), 'utf8');
  assert.match(src, /renewal-email', \{\s*method: 'POST', query: withOrg\(\), body: \{ certificate_id: certificateId \}/);
});

test('a sent renewal is no longer a remembered draft: the next press asks the service again', async () => {
  boot({});
  await offer('c-fgas-1').run();
  assert.equal(posts.length, 1);
  assert.ok(c.state.ccOfferDrafts['c-fgas-1:renewal_email']);
  await c.renderVals().em.send();
  for (let i = 0; i < 20 && c.state.emSending; i++) await new Promise((r) => setTimeout(r, 5));
  assert.equal((c.state.ccOfferDrafts || {})['c-fgas-1:renewal_email'], undefined);
  await offer('c-fgas-1').run();
  assert.equal(posts.length, 2);
});

test('no draft opens while an email is still sending (its landing would close the new one)', async () => {
  boot({ emSending: true });
  await offer('c-fgas-1').run();
  assert.equal(posts.length, 0);
  assert.notEqual(c.state.flow, 'email');
  c.emOpenDraft({ to: 'a@b.example', subject: 'S', body: 'B' });
  assert.notEqual(c.state.flow, 'email');
});
