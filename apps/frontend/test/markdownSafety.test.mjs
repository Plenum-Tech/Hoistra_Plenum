// markdownSafety — Markdown.jsx renders LLM-produced answers as real React elements
// (never dangerouslySetInnerHTML), but a link's *target* still went into `href` unfiltered.
// React does not block a javascript: href; it renders it, and a click executes it in this
// app's own origin — which proxies /backend/* to services that, per buildingsCrud.js's own
// comment, "do not authorise these routes". The text comes from an LLM reasoning over
// ingested documents (vendor PDFs, certificates, uploads), so the content chain starts
// outside the organisation: one poisoned document is one click from a write.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isSafeHref } from '../src/components/shell/markdownSafety.js';

test('an http(s) link is safe', () => {
  assert.equal(isSafeHref('https://example.com/cert.pdf'), true);
  assert.equal(isSafeHref('http://example.com'), true);
});

test('a mailto link is safe', () => {
  assert.equal(isSafeHref('mailto:someone@example.com'), true);
});

test('a same-origin relative path or anchor is safe', () => {
  assert.equal(isSafeHref('/documents/123'), true);
  assert.equal(isSafeHref('#section-2'), true);
});

test('a javascript: URL is refused', () => {
  assert.equal(isSafeHref("javascript:fetch('/backend/ops-intelligence/api/energy/buildings',{method:'POST'})"), false);
});

test('a data: URL is refused', () => {
  assert.equal(isSafeHref('data:text/html,<script>alert(1)</script>'), false);
});

test('surrounding whitespace does not defeat the check', () => {
  assert.equal(isSafeHref('  javascript:alert(1)'), false);
  assert.equal(isSafeHref('\n\tjavascript:alert(1)'), false);
});

test('case does not defeat the check', () => {
  assert.equal(isSafeHref('JaVaScRiPt:alert(1)'), false);
});

test('an unknown, empty or null href is refused, never guessed safe', () => {
  assert.equal(isSafeHref(''), false);
  assert.equal(isSafeHref(null), false);
  assert.equal(isSafeHref(undefined), false);
  assert.equal(isSafeHref('vbscript:msgbox(1)'), false);
  assert.equal(isSafeHref('file:///etc/passwd'), false);
});
