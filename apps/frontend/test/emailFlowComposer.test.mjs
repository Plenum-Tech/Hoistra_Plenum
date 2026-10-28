// emailFlowComposer — the email card and the offer buttons as markup (28 Oct 2026).
//
// The dock's email card was <div onClick>s and unlabelled fields, and the compliance offers
// ("Draft renewal email", "Verify with the issuer") were <span onClick>s: none of it could be
// reached by keyboard or showed focus, and nothing said which of four identical "Draft renewal
// email" buttons was which. EmailComposer is the one card both the dock and the chat page draw;
// OfferButton is the one offer button ComplianceAnswer draws in its three places.
//
// Both are bundled with esbuild (as Vite would) and rendered to static markup.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' }, addEventListener: () => {}, removeEventListener: () => {} };

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
const lazyToReal = {
  name: 'lazy-to-real',
  setup(b) {
    b.onResolve({ filter: /LazyOpenUIAnswer\.jsx$/ }, () => ({ path: path.join(here, '../src/openui/OpenUIAnswer.jsx') }));
  }
};
const out = path.join(here, '.email-flow-composer.bundle.mjs');
const res = await build({
  stdin: { contents: "export { default as EmailComposer } from '../src/components/shell/EmailComposer.jsx';\nexport { default as ComplianceAnswer } from '../src/components/shell/ComplianceAnswer.jsx';", resolveDir: here, loader: 'js' },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', '@openuidev/lang-core', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent', plugins: [lazyToReal]
});
writeFileSync(out, res.outputFiles[0].text);
after(() => rmSync(out, { force: true }));
const { EmailComposer, ComplianceAnswer } = await import(pathToFileURL(out).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

const noop = () => {};
const EM = { kicker: 'Draft renewal email', to: 'renewals@vendor.example', subject: 'Renewal required — F-Gas', body: 'Please renew.',
  prevShow: 'none', prevNote: '', prevIsReminder: false, prevSwitchLabel: 'Make it a reminder', prevSwitch: noop,
  setTo: noop, setSubject: noop, setBody: noop, packShow: 'none', packName: '', packSize: '', openPack: noop,
  sendBusy: false, sample: false, send: noop, queued: false, canCc: false, cc: '', setCc: noop };
const card = (em, props) => renderToStaticMarkup(React.createElement(EmailComposer, Object.assign({ em: Object.assign({}, EM, em), onCancel: noop, variant: 'page' }, props)));

test('the card is real controls: labelled fields and <button>s, nothing clickable that is not one', () => {
  const h = card({});
  assert.match(h, /<section class="emc emc-page" id="emc-page" aria-labelledby="emc-page-title">/);
  assert.match(h, /<h3 class="emc-title" id="emc-page-title">Draft renewal email<\/h3>/);
  assert.match(h, /<label for="emc-page-to">To<\/label><input id="emc-page-to" type="text" inputMode="email"[^>]*value="renewals@vendor\.example"\/>/);
  assert.match(h, /<label for="emc-page-subject">Subject<\/label><input id="emc-page-subject"/);
  assert.match(h, /<label class="emc-sr" for="emc-page-body">Draft<\/label><textarea id="emc-page-body"/);
  assert.match(h, /<button type="button" class="emc-btn is-quiet">Cancel<\/button>/);
  assert.match(h, /<button type="button" class="emc-btn is-primary"><i class="ph ph-paper-plane-tilt" aria-hidden="true"><\/i>Approve &amp; send<\/button>/);
  assert.doesNotMatch(h, /<(div|span)[^>]*class="hv/);
  assert.doesNotMatch(h, /autofocus/i, 'the composer below keeps its autoFocus (chatLabelsAndKeys)');
  assert.doesNotMatch(src('components/shell/EmailComposer.jsx'), /<(div|span)\s[^>]*onClick=\{/);
});

test('the dock\'s card has ids of its own, so both can be on screen at once', () => {
  const h = card({}, { variant: 'dock' });
  assert.match(h, /id="emc-dock"/);
  assert.match(h, /<label for="emc-dock-to">To<\/label>/);
  assert.doesNotMatch(h, /emc-page/);
});

test('while it sends: Approve & send says so, is busy and takes no second click; Cancel waits', () => {
  const h = card({ sendBusy: true });
  assert.match(h, /<button type="button" class="emc-btn is-quiet" disabled="">Cancel<\/button>/);
  assert.match(h, /<button type="button" class="emc-btn is-primary" disabled="" aria-busy="true"><i class="ph ph-circle-notch emc-spin" aria-hidden="true"><\/i>Sending…<\/button>/);
});

test('the send keeps its other labels: a reminder, and a sample that is never sent', () => {
  assert.match(card({ prevIsReminder: true }), />Approve &amp; send reminder<\/button>/);
  const s = card({ sample: true });
  assert.match(s, /class="emc-btn is-primary is-sample"[^>]*>.*Sample — not sent<\/button>/);
  assert.match(s, /<span class="emc-hint">Sample — not sent<\/span>/);
  assert.match(card({}), /<span class="emc-hint">Nothing leaves Hoistra until you press Approve &amp; send<\/span>/);
});

test('what the send did is announced in the card, toned by what it was', () => {
  const h = card({}, { done: 'Not sent — mailbox refused.', doneTone: 'risk' });
  assert.match(h, /<div class="emc-status is-risk" role="status"><i class="ph ph-warning-circle" aria-hidden="true"><\/i><span>Not sent — mailbox refused\.<\/span><\/div>/);
  assert.doesNotMatch(card({}), /role="status"/);
});

test('an Approvals item, an earlier send, a Cc line and the evidence pack each show only when there', () => {
  const plain = card({});
  assert.doesNotMatch(plain, /Approvals card|emc-prev|emc-page-cc|emc-pack/);
  const full = card({ queued: true, prevShow: 'block', prevNote: 'You sent this request on 01 Oct 2026, 10:00.', prevIsReminder: true,
    prevSwitchLabel: 'Send as a new request instead', canCc: true, cc: 'ops@n.example', packShow: 'flex', packName: 'pack.pdf', packSize: '12 KB' });
  assert.match(full, /Linked to its item on the Approvals card — sending here records it there\./);
  assert.match(full, /<div class="emc-prev" role="note">.*You sent this request on 01 Oct 2026, 10:00\. <span class="emc-prev-sub">This draft is a reminder of it\.<\/span>.*<button type="button" class="emc-link">Send as a new request instead<\/button>/);
  assert.match(full, /<label for="emc-page-cc">Cc<\/label><input id="emc-page-cc"[^>]*value="ops@n\.example"/);
  assert.match(full, /<button type="button" class="emc-pack" title="Open the generated evidence pack">/);
});

// ── the offer buttons ────────────────────────────────────────────────────────
const RICH = { narrative: 'Two certificates need renewing.', sections: [], groups: [], kpis: [], actions: [], insights: [], certificates: [],
  pending: [{ cert_id: 'c-3', name: 'Gas Safety — Bishopsgate Tower', what_is_pending: 'Draft awaiting review' }], validation: null, steps: [], cost: null,
  offers: [
    { cert_id: 'c-1', kind: 'renewal_email', label: 'Draft renewal email', forName: 'F-Gas Certificate · FG-101', aria: 'Draft renewal email — F-Gas Certificate, Bishopsgate Tower', reason: 'Expires tomorrow', state: '', run: noop },
    { cert_id: 'c-2', kind: 'renewal_email', label: 'Draft renewal email', forName: 'F-Gas Certificate · FG-102', aria: 'Draft renewal email — F-Gas Certificate, Bishopsgate Tower', reason: 'Lapsed', state: 'busy' },
    { cert_id: 'c-3', kind: 'confirm_draft', label: 'Mark reviewed', forName: 'Gas Safety', aria: 'Mark reviewed — Gas Safety', state: 'done', run: noop }
  ] };
const answer = (rich) => renderToStaticMarkup(React.createElement(ComplianceAnswer, { rich: rich || RICH, open: false, onToggle: noop, fallbackText: '' }));

test('each offer is a button that says which certificate it is for, under the answer', () => {
  const h = answer();
  assert.match(h, /<div class="ans-offers" role="group" aria-label="Actions on these certificates">/);
  assert.match(h, /<button type="button" class="ans-offer" aria-label="Draft renewal email — F-Gas Certificate, Bishopsgate Tower" title="Expires tomorrow"><i class="ph ph-envelope-simple" aria-hidden="true"><\/i><span class="ans-offer-text"><span class="ans-offer-label">Draft renewal email<\/span><span class="ans-offer-for">F-Gas Certificate · FG-101<\/span><\/span><\/button>/);
  assert.match(h, /<span class="ans-offer-for">F-Gas Certificate · FG-102<\/span>/);
  assert.doesNotMatch(h, /class="hv13"/);
});

test('a busy offer is disabled and announced; one with nothing to run (a live answer) is disabled', () => {
  const h = answer();
  assert.match(h, /<button type="button" class="ans-offer is-busy" disabled="" aria-busy="true"[^>]*><i class="ph ph-circle-notch" aria-hidden="true"><\/i>/);
  const live = answer(Object.assign({}, RICH, { offers: RICH.offers.map((o) => Object.assign({}, o, { run: undefined, state: '' })) }));
  assert.equal((live.match(/<button type="button" class="ans-offer" disabled=""/g) || []).length, 3);
});

test('a pending card\'s offer does not repeat the certificate its title already names', () => {
  const h = answer();
  assert.match(h, /<button type="button" class="ans-offer is-done" aria-label="Mark reviewed — Gas Safety"><i class="ph ph-check" aria-hidden="true"><\/i><span class="ans-offer-text"><span class="ans-offer-label">Mark reviewed<\/span><\/span><\/button>/);
});

test('the dock and the chat page draw the one card; the dock\'s div controls are gone', () => {
  const dock = src('components/shell/OrchestratorDock.jsx');
  const chat = src('screens/Chat.jsx');
  assert.match(dock, /import EmailComposer from '\.\/EmailComposer\.jsx'/);
  assert.match(dock, /\{vals\.fEmail \? <EmailComposer em=\{vals\.em\} onCancel=\{vals\.fCancel\} variant="dock" \/> : null\}/);
  assert.doesNotMatch(dock, /onClick=\{vals\.em\.sendBusy \? undefined : vals\.em\.send\}/);
  assert.match(chat, /import EmailComposer from '\.\.\/components\/shell\/EmailComposer\.jsx'/);
  assert.match(chat, /<EmailComposer em=\{vals\.em\} onCancel=\{vals\.fCancel\} variant="page" done=\{vals\.fDoneText\} doneTone=\{vals\.fDoneTone\} \/>/);
  assert.match(chat, /\{vals\.emChatAt === m\.key \? draftHere : null\}/);
  assert.match(chat, /\{vals\.emChatAt === -1 \? draftHere : null\}/);
  // The offers' one button, in all three places.
  const ca = src('components/shell/ComplianceAnswer.jsx');
  assert.equal((ca.match(/<OfferButton /g) || []).length, 3);
  assert.doesNotMatch(ca, /className="hv13" onClick=\{o\.run\}/);
});
