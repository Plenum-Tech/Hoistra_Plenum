// VendorDeleteDialog — the Delete vendor dialog's markup (12 Oct 2026). The component is bundled
// with esbuild and rendered to markup with react-dom/server, as test/gateFinalPlan.test.mjs does;
// the portal is rendered in place (react-dom/server has no portals). What is held here is the
// dialog's contract with assistive technology and with the hand on the mouse: it is a modal
// dialog named by its title and described by what the delete would do, Delete vendor is off
// until it may be pressed and says it is working while it does, and the result is announced.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = path.join(here, '../src/components/shell/VendorDeleteDialog.jsx');
const outFile = path.join(here, '.vendor-delete-dialog.bundle.mjs');

// react-dom's createPortal, rendered in place.
const portalInPlace = {
  name: 'portal-in-place',
  setup(b) {
    b.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom-shim', namespace: 'shim' }));
    b.onLoad({ filter: /.*/, namespace: 'shim' }, () => ({ contents: 'export const createPortal = (node) => node;', loader: 'js' }));
  }
};
const res = await build({
  entryPoints: [entry], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom/*'], plugins: [portalInPlace], logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));
globalThis.document = globalThis.document || { body: {} };
const { default: Dialog, vdlKey, vdlFocusAfter } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

const noop = () => {};
const BASE = {
  vdlShow: true, vdlSaid: '', vdlOpener: () => null,
  vdlTitle: 'Delete Acme Ltd?', vdlSub: 'ACME · Mechanical', vdlReading: '',
  vdlMode: 'This vendor has history, so it will be retired: hidden everywhere, its past records kept and marked (deleted).',
  vdlScope: 'Applies across every building in the company, including ones you do not manage.', vdlPermanent: 'This cannot be undone.',
  vdlGroups: [{ key: 'unassigned', title: 'Unassigned', note: 'These stop pointing at this vendor. Closed work keeps its history.',
    rows: [{ key: 'assets', label: 'Assets', n: '7', items: ['Boiler 1 · B-301', 'Boiler 2 · B-302'], more: 'and 5 more' }] }],
  vdlConfirmWith: 'Acme Ltd', vdlTyped: '', vdlNameOk: false, vdlReasonText: '', vdlReasonLeft: '500 characters left',
  vdlCanDelete: false, vdlBusy: false, vdlCanCancel: true, vdlDeleteLabel: 'Delete vendor', vdlDeleteShow: true, vdlCancelLabel: 'Cancel',
  vdlError: '', vdlRetryShow: false, vdlChanged: '', vdlTypeShow: true,
  vdlSetTyped: noop, vdlSetReason: noop, vdlRetry: noop, vdlClose: noop, vdlConfirm: noop
};
const render = (over) => renderToStaticMarkup(React.createElement(Dialog, { vals: Object.assign({}, BASE, over || {}) }));
const tag = (html, re) => (html.match(re) || [''])[0];
const deleteButton = (html) => tag(html, /<button[^>]*>(?:(?!<\/button>).)*(Delete vendor|Deleting…)<\/span><\/button>/);

test('closed, only the status line is there — empty until there is a result to announce', () => {
  assert.equal(render({ vdlShow: false }), '<div role="status" aria-live="polite" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0"></div>');
  assert.match(render({ vdlShow: false, vdlSaid: 'Acme Ltd deleted' }), /role="status"[^>]*>Acme Ltd deleted<\/div>/);
});

test('a modal dialog, named by its title and described by what the delete does', () => {
  const html = render();
  assert.match(html, /role="dialog" aria-modal="true" aria-labelledby="vdl-title" aria-describedby="vdl-desc"/);
  assert.match(html, /<h2 id="vdl-title"[^>]*>Delete Acme Ltd\?<\/h2>/);
  assert.match(html, /id="vdl-desc".*This vendor has history, so it will be retired/);
  assert.match(html, /Applies across every building in the company, including ones you do not manage\. This cannot be undone\./);
});

test('a long vendor name wraps instead of running off a phone screen', () => {
  assert.match(tag(render(), /<h2 id="vdl-title"[^>]*>/), /overflow-wrap:anywhere/);
});

test('every group is listed with its rows, its names and how many more', () => {
  const html = render();
  assert.match(html, /<section aria-label="Unassigned"/);
  assert.match(html, />Assets<\/span><span[^>]*>7<\/span>/);
  assert.match(html, /Boiler 1 · B-301, Boiler 2 · B-302 and 5 more/);
});

test('the name to type is shown, and its field is labelled', () => {
  const html = render();
  assert.match(html, /<label for="vdl-confirm"[^>]*>Type <b[^>]*>Acme Ltd<\/b> to confirm<\/label>/);
  assert.match(html, /<input id="vdl-confirm"/);
  assert.match(html, /<label for="vdl-reason"[^>]*>Why it is being deleted \(optional\)<\/label>/);
});

test('Delete vendor is off until it may be pressed', () => {
  assert.match(deleteButton(render()), /disabled=""/);
  assert.doesNotMatch(deleteButton(render({ vdlCanDelete: true, vdlNameOk: true })), /disabled=""/);
});

test('while it works, Delete vendor says so and nothing on the dialog can be pressed or typed', () => {
  const html = render({ vdlBusy: true, vdlCanCancel: false, vdlDeleteLabel: 'Deleting…', vdlTyped: 'Acme Ltd', vdlNameOk: true });
  assert.match(html, /aria-busy="true"/);
  assert.match(deleteButton(html), /disabled=""/);
  assert.match(deleteButton(html), /Deleting…/);
  assert.match(tag(html, /<button[^>]*>Cancel<\/button>/), /disabled=""/);
  assert.match(tag(html, /<input id="vdl-confirm"[^>]*>/), /disabled=""/);
});

test('before the plan answers it says it is reading, and offers no field to type in', () => {
  const html = render({ vdlReading: 'Reading what this would change…', vdlMode: '', vdlScope: '', vdlGroups: [], vdlTypeShow: false });
  assert.match(html, /Reading what this would change…/);
  assert.doesNotMatch(html, /vdl-confirm/);
});

test('an error is announced, with Try again when it can be tried again', () => {
  const html = render({ vdlError: 'Nothing was deleted — the change could not be recorded in the audit trail. Try again.', vdlRetryShow: true });
  assert.match(html, /<div role="alert"[^>]*><span[^>]*>Nothing was deleted — the change could not be recorded in the audit trail\. Try again\.<\/span><button[^>]*>Try again<\/button>/);
});

test('a vendor already gone offers Close and no Delete', () => {
  const html = render({ vdlError: 'This vendor is already gone.', vdlDeleteShow: false, vdlCancelLabel: 'Close', vdlTypeShow: false, vdlGroups: [], vdlMode: '', vdlScope: '' });
  assert.match(html, />Close<\/button>/);
  assert.equal(deleteButton(html), '');
});

// ── review fixes (12 Oct 2026) ────────────────────────────────────────────────

test('the error is read in the text colour on the risk tint — the risk colour marks it, it does not carry it', () => {
  const html = render({ vdlError: 'Nothing was deleted — the change could not be recorded in the audit trail. Try again.' });
  const box = tag(html, /<div role="alert"[^>]*>/);
  assert.match(box, /background:var\(--st-risk-bg\)/);
  assert.match(box, /color:var\(--color-text\)/);
  assert.doesNotMatch(box, /[;"]color:var\(--st-risk\)/);
  assert.match(box, /border-left:3px solid var\(--st-risk\)/);
});

test('a plan that moved is announced', () => {
  const html = render({ vdlChanged: 'What this would change moved since you opened it. Check it again and type the name to confirm.' });
  assert.match(html, /<div role="status"[^>]*>(?:(?!<\/div>).)*What this would change moved/);
});

test('the dialog can take focus itself, and so can its error', () => {
  const html = render({ vdlError: 'x' });
  assert.match(tag(html, /<div[^>]*role="dialog"[^>]*>/), /tabindex="-1"/);
  assert.match(tag(html, /<div role="alert"[^>]*>/), /tabindex="-1"/);
});

test('on a phone the box fits the visible screen, and the footer buttons are big enough to tap', () => {
  const html = render();
  assert.match(tag(html, /<div[^>]*role="dialog"[^>]*>/), /class="vdl-box"/);
  assert.doesNotMatch(tag(html, /<div[^>]*role="dialog"[^>]*>/), /max-height/, 'max-height is in the stylesheet, with its fallback');
  assert.match(tag(html, /<button[^>]*>Cancel<\/button>/), /class="[^"]*vdl-btn/);
  assert.match(deleteButton(html), /class="[^"]*vdl-btn/);
  const css = readFileSync(path.join(here, '../src/styles/hover.css'), 'utf8');
  assert.match(css, /\.vdl-box \{ max-height:calc\(100vh - 32px\); max-height:calc\(100dvh - 32px\); \}/);
  assert.match(css, /@media \(max-width: 640px\) \{[^\n]*\.vdl-btn \{ min-height:44px; \} \}/);
});

// The keys while it is open: Esc closes it unless it is deleting; Cmd/Ctrl+K — the command
// palette — does nothing, so the palette cannot open over it and take Esc and Tab from it.
const ev = (key, over) => {
  const e = Object.assign({ key: key, metaKey: false, ctrlKey: false, shiftKey: false, prevented: 0, stopped: 0 }, over || {});
  e.preventDefault = () => { e.prevented++; }; e.stopPropagation = () => { e.stopped++; };
  return e;
};
test('Cmd+K and Ctrl+K are swallowed while it is open', () => {
  for (const mod of [{ metaKey: true }, { ctrlKey: true }]) {
    for (const key of ['k', 'K']) {
      const e = ev(key, mod);
      let closed = 0;
      assert.equal(vdlKey(e, { busy: false, close: () => { closed++; } }), 'swallowed');
      assert.equal(e.prevented, 1); assert.equal(e.stopped, 1); assert.equal(closed, 0);
    }
  }
  const plain = ev('k');
  assert.equal(vdlKey(plain, { busy: false, close: noop }), null, 'typing a k in the name field is untouched');
  assert.equal(plain.prevented, 0);
});
test('Esc closes it, except while it is deleting', () => {
  let closed = 0;
  vdlKey(ev('Escape'), { busy: false, close: () => { closed++; } });
  vdlKey(ev('Escape'), { busy: true, close: () => { closed++; } });
  assert.equal(closed, 1);
});

// Where focus goes: the controls are disabled while it deletes, so focus moves to the dialog
// first instead of falling to the page; when it is back, to the name field if it was cleared,
// otherwise to the error.
test('focus moves to the dialog while it deletes, and back to what needs the admin after', () => {
  const ready = { vdlBusy: false, vdlTyped: 'Acme Ltd', vdlError: '', vdlTypeShow: true };
  const working = Object.assign({}, ready, { vdlBusy: true });
  assert.equal(vdlFocusAfter(ready, working), 'box');
  assert.equal(vdlFocusAfter(working, Object.assign({}, ready, { vdlTyped: '' })), 'confirm', 'plan changed: type the name again');
  assert.equal(vdlFocusAfter(working, Object.assign({}, ready, { vdlError: 'Something else is changing this vendor right now.' })), 'error');
  assert.equal(vdlFocusAfter(working, Object.assign({}, ready, { vdlTypeShow: false, vdlTyped: '', vdlError: 'gone' })), 'error', 'no field to type in');
  assert.equal(vdlFocusAfter(ready, Object.assign({}, ready, { vdlTyped: 'Acme' })), null, 'typing moves nothing');
});

// The Vendor audit trail's rows (screens/VendorAuditTrail.jsx) are a fixed 800px grid. At 390px
// the card clipped them, so an expanded deleted row's How / Removed / Deleted by lines could not
// be read; the card scrolls sideways instead (12 Oct 2026).
test('the Vendor audit trail\'s rows scroll sideways inside their card rather than being cut off', async () => {
  const out = path.join(here, '.vendor-audit-trail.bundle.mjs');
  const r = await build({ entryPoints: [path.join(here, '../src/screens/VendorAuditTrail.jsx')], bundle: true, write: false, format: 'esm',
    platform: 'node', jsx: 'automatic', external: ['react', 'react/*', 'react-dom/*'], logLevel: 'silent' });
  writeFileSync(out, r.outputFiles[0].text);
  try {
    const { default: Trail } = await import(pathToFileURL(out).href);
    const html = renderToStaticMarkup(React.createElement(Trail, { vals: { vaGroups: [{ label: 'Today', count: '1', rows: [
      { id: 'e1', open: true, toggle: noop, clock: '09:12', who: 'Ada', role: 'Admin', vendor: 'Acme Ltd (deleted)', change: 'Deleted',
        building: 'All buildings', badge: 'Deleted', bg: 'x', fg: 'y', panelShow: 'grid', fields: [{ l: 'How', v: 'Retired — hidden everywhere' }] }] }] } }));
    const cardTag = tag(html, /<div style="border-radius:11px;background:var\(--color-surface\);box-shadow:var\(--shadow-sm\)[^>]*>/);
    assert.ok(cardTag, 'the rows card');
    assert.match(cardTag, /overflow-x:auto/);
    assert.doesNotMatch(cardTag, /overflow:hidden/);
  } finally { rmSync(out, { force: true }); }
});
