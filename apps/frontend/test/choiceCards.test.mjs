// choiceCards — "I want to migrate my data" answers with three option cards, each a button.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { choiceCards, choiceIntro, validChoices, choiceMethods } from '../src/logic/choiceCards.js';

const CHOICES = [
  { id: 'csv_excel', n: 1, icon: 'ph-file-xls', title: 'CSV / Excel data migration', detail: 'Exports…', cta: 'Choose CSV or Excel files', action: { kind: 'attach', accept: '.csv,.xls,.xlsx' } },
  { id: 'documents', n: 2, icon: 'ph-file-pdf', title: 'PDF / document migration', detail: 'Certificates…', cta: 'Choose PDF or Word files', action: { kind: 'attach', accept: '.pdf,.doc,.docx' } },
  { id: 'database', n: 3, icon: 'ph-database', title: 'Direct database migration', detail: 'Connect…', cta: 'Connect Fiix', action: { kind: 'ask', text: 'connect Fiix' } }
];

test('the three options become three numbered cards in order', () => {
  const picked = [];
  const cards = choiceCards(CHOICES, (c) => picked.push(c.id));
  assert.deepEqual(cards.map((c) => [c.n, c.title]), [
    [1, 'CSV / Excel data migration'], [2, 'PDF / document migration'], [3, 'Direct database migration']]);
  cards[2].pick();
  assert.deepEqual(picked, ['database']);
});

test('a card the chat cannot run is left out, and no choices means no cards', () => {
  assert.equal(validChoices([{ title: 'x', action: { kind: 'navigate' } }, { title: '', action: { kind: 'ask', text: 'a' } }]).length, 0);
  assert.deepEqual(choiceCards(undefined, () => {}), []);
});

test('the line above the cards is the reply\'s first paragraph without emphasis', () => {
  assert.equal(choiceIntro('There are **three** ways in.\n\n**1. CSV**…'), 'There are three ways in.');
});

test('the database card sends its text as the next question', () => {
  const asked = [];
  const ctrl = { state: {}, askScoped: (q) => asked.push(q), flash: () => {} };
  choiceMethods.orchChoice.call(ctrl, CHOICES[2]);
  assert.deepEqual(asked, ['connect Fiix']);
});

test('a card clicked while an answer is running waits', () => {
  const said = [];
  const ctrl = { state: { ccBusy: true }, askScoped: () => assert.fail('should not ask'), flash: (m) => said.push(m) };
  choiceMethods.orchChoice.call(ctrl, CHOICES[2]);
  assert.match(said[0], /Still answering/);
});
