// miniMarkdown — the Trace tab's Formatted view (9 Nov 2026): an answer's markdown read into plain data blocks the page
// draws as React elements, never as HTML, so a model's text can't put markup on the page.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { parseMarkdown } = await import('../logic/miniMarkdown.js');
const t = (text) => ({ t: 'text', text });

test('headings, bold, lists and a pipe table', () => {
  const md = '## Summary\n**13 open** maintenance work orders are past *their* SLA due time.\n\n'
    + '- first `code` item\n* second\n\n1. one\n2) two\n\n| WO | ISSUE |\n|---|:---:|\n| WO-1 | Fire **alarm** |\n| WO-2 | Boiler |\n'
    + 'After the table.';
  assert.deepEqual(parseMarkdown(md), [
    { t: 'h', level: 2, inline: [t('Summary')] },
    { t: 'p', inline: [{ t: 'b', text: '13 open' }, t(' maintenance work orders are past '), { t: 'i', text: 'their' },
      t(' SLA due time.')] },
    { t: 'ul', items: [[t('first '), { t: 'code', text: 'code' }, t(' item')], [t('second')]] },
    { t: 'ol', items: [[t('one')], [t('two')]] },
    { t: 'table', head: [[t('WO')], [t('ISSUE')]],
      rows: [[[t('WO-1')], [t('Fire '), { t: 'b', text: 'alarm' }]], [[t('WO-2')], [t('Boiler')]]] },
    { t: 'p', inline: [t('After the table.')] },
  ]);
});

test('html in markdown stays text', () => {
  assert.deepEqual(parseMarkdown('<script>alert(1)</script> <b>hi</b>'),
    [{ t: 'p', inline: [t('<script>alert(1)</script> <b>hi</b>')] }]);
  assert.deepEqual(parseMarkdown(''), []);
  assert.deepEqual(parseMarkdown(null), []);
});

test('a fenced block is code', () => {
  assert.deepEqual(parseMarkdown('Before\n```sql\nSELECT 1\n  FROM x\n```\nAfter'),
    [{ t: 'p', inline: [t('Before')] }, { t: 'code', text: 'SELECT 1\n  FROM x' }, { t: 'p', inline: [t('After')] }]);
  assert.deepEqual(parseMarkdown('```\nnever closed'), [{ t: 'code', text: 'never closed' }]);
});
