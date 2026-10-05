// correctionDrawerPinned — the Teach the agent drawer stays the window's height, with its
// actions in view.
//
// Chat.jsx mounts CorrectionDrawer inside .chat-grid, which runs the fadeUp entrance with
// fill-mode "both". An animation on transform that stays in effect makes the grid the
// containing block for position:fixed descendants, so the drawer's top:0/bottom:0 spanned the
// whole conversation rather than the viewport. Seen 5 Oct 2026: Re-run the steps, Re-answer
// freely and Save as teaching sat at the bottom of the chat, below a long stretch of empty
// panel, and only scrolling the page reached them. Rendering through a portal into <body>
// takes the drawer out of every transformed ancestor.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
const drawer = src('components/shell/CorrectionDrawer.jsx');
const chat = src('screens/Chat.jsx');
const base = src('styles/base.css');

test('the chat grid it mounts under still animates a transform (the reason for the portal)', () => {
  assert.match(base, /@keyframes fadeUp\s*\{[^}]*transform/);
  assert.match(chat, /className="chat-grid"[^>]*fadeUp[^>]*both/);
  assert.match(chat, /<CorrectionDrawer vals=\{vals\} \/>/);
});

test('the drawer renders through a portal into document.body', () => {
  assert.match(drawer, /import \{ createPortal \} from 'react-dom'/);
  assert.match(drawer, /return createPortal\(/);
  assert.match(drawer, /,\s*document\.body\s*\);\s*\}\s*$/);
});

test('the actions sit in a pinned footer after the scrolling body', () => {
  const body = drawer.indexOf('overflowY: "auto"');
  const footer = drawer.indexOf('flexShrink: "0", borderTop');
  const rerun = drawer.indexOf('"Re-run the steps"');
  assert.ok(body > -1 && footer > body, 'the footer follows the scrolling body');
  assert.ok(rerun > footer, 'Re-run the steps lives in the footer');
  assert.ok(drawer.indexOf('"Save as teaching"') > footer);
  assert.ok(drawer.indexOf('"Re-answer freely"') > footer);
});

test('Escape closes, and the key effect does not re-run on every render', () => {
  assert.match(drawer, /e\.key === "Escape"/);
  assert.match(drawer, /\}, \[show\]\);/);
});
