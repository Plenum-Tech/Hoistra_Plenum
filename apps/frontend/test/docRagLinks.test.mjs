// A document link in a chat answer - old or new - is recognised so the chat opens it through a
// signed link (api/docRag.js) instead of the bare /download URL the server now refuses.
import test from 'node:test';
import assert from 'node:assert/strict';
import { documentIdFromHref } from '../src/api/docRag.js';

const ID = '3f2b9a10-1c2d-4e5f-8a9b-0c1d2e3f4a5b';

test('an old absolute download URL in chat history', () => {
  assert.equal(documentIdFromHref('https://hoistra.example/backend/deep-agents/api/documents/' + ID + '/download'), ID);
});

test('a relative download URL, with or without a stale signature', () => {
  assert.equal(documentIdFromHref('/backend/deep-agents/api/documents/' + ID + '/download'), ID);
  assert.equal(documentIdFromHref('/api/documents/' + ID + '/download?exp=1&u=x&sig=abc'), ID);
});

test('a doc: reference', () => {
  assert.equal(documentIdFromHref('doc:' + ID), ID);
});

test('anything else is an ordinary link', () => {
  assert.equal(documentIdFromHref('https://www.gov.uk/energy-certificate/1234'), null);
  assert.equal(documentIdFromHref('/api/documents/not-an-id/download'), null);
  assert.equal(documentIdFromHref('/api/documents/' + ID + '/index'), null);
  assert.equal(documentIdFromHref(''), null);
});
