// answerCards — every structured answer is arranged as the dashboard, from its own text.
//
// 5 Oct 2026: a general-loop answer ("all of them" -> dashboard stats, PPM contracts, inspections)
// rendered as headings, "Label: value" bullets and numbered lists restarting at 1, under a chat
// whose other answers were KPI tiles and cards. The fixtures below are the answers from that day's
// screenshots. The rule that matters most is the last test: nothing in the answer is dropped.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerCards, keyValue, kpiOf, entitiesOf, titleOf, cleanTitle, toneOf } from '../src/logic/answerCards.js';
import { parseBlocks } from '../src/logic/markdownBlocks.js';

export const OVERVIEW = `Here's a comprehensive overview of everything currently at risk across various registers:

### 1. Compliance and Certificates
- **Pending Approvals:** 500 items in the approvals queue.
- **Compliance Status:** Currently, there are no specific compliance issues reported, but the overall compliance score is low, indicating potential risks.

### 2. Planned Preventive Maintenance (PPM) Contracts
- **Total Contracts:** 13
- **Completed Visits:** 196
- **Planned Visits:** 125
- **Completion Percentage:** 156.8%
- **Missed Visits:** 2
- **Late Visits:** 2
- **Contracts Behind Plan:** 6
- **Contracts to Plan:** 7

**Contracts Behind Plan:**
| Contract Name | Vendor | Completion % | Missed Visits | Late Visits |
|---|---|---|---|---|
| Mechanical PPM · Bishopsgate | Apex Mechanical | 68.8% | 2 | 2 |
| Fire alarm maintenance · Bishopsgate | Kestamere Fire Protection | 75.0% | 0 | 0 |
| Water hygiene monitoring · Bishopsgate | Aquilon Water Hygiene | 25.0% | 0 | 0 |

### 3. Asset Condition and Inspection Findings
- **Total Assets Inspected:** 61
- **Unconverted Recommendations:** 16 (8 flagged by energy)
- **Assets Graded Poor:** 2 (Condition Score 4)
  - **CHILLER-101:** Installation Date: 2009-03-15, Criticality: High
  - **Fan Coil Units — Level 20:** Installation Date: 2016-05-20, Criticality: Medium

### 4. Energy Anomalies
- **Open Anomalies:** 23
- **Corroborated Anomalies:** 0
- **Recent Anomalies Detected:**
  - **AHU-3:** Non-occurrence spike detected with a cost of £27,432.15.
  - **Boiler 1:** Multiple anomalies flagged, including simultaneous heating and cooling.

### 5. Ingestion Status
- **Current Ingestion Job:** Not found, indicating potential issues with data ingestion processes.

### Summary
The overall risk landscape shows several areas of concern, particularly with compliance, PPM contracts, and energy anomalies. Immediate attention may be required to address the missed visits and ensure compliance with maintenance schedules.

If you need further details on any specific area or wish to take action on any of these risks, please let me know!`;

export const CERTS = `### Assets at Risk
- **Total Assets at Risk:** 6
- **Total Value at Risk:** £230,160.70 (for those with available costs)

These assets require immediate attention due to their classification as threats.

### Compliance Certificates at Risk
#### Lapsed Certificates
1. **Display Energy Certificate (DEC)**
2. **Building**: Manchester Town Hall
3. **Certificate Number**: 9920-1010-0626-0890-2091
4. **Expiry Date**: 30 September 2013
5. **Status**: Lapsed
6. **Insurance Risk Flag**: Yes
7. **Email Sent to PM**: Yes (dry run)
8. **[View Certificate](https://find-energy-certificate.service.gov.uk/energy-certificate/9920-1010-0626-0890-2091)**

1. **Fire Risk Assessment (FRA)**
2. **Building**: Bishopsgate Tower
3. **Certificate Number**: FRA-B-301-2025
4. **Expiry Date**: 29 June 2026
5. **Status**: Lapsed
6. **Vendor**: Pennard Fire Services (Blocked due to lapsed accreditation)

### Summary of Compliance Certificates
- **Total Lapsed Certificates:** 2

### Next Steps
- Review the lapsed certificates for renewal actions.
- Prioritize the expiring soon certificates to ensure compliance.`;

export const VENDORS = `There are currently **9 blocked vendors**. Here are the details:

### Blocked Vendors
| Vendor | Certificate Type | Status |
|---|---|---|
| Ostley Power Services | NICEIC Approved Contractor Certificate | Lapsed |
| ProudCastle Solutions Ltd | BAFE SP203-1 Registration Certificate | Lapsed |

### What to do next
You should initiate the renewal process for the blocked vendors' accreditations to ensure compliance and avoid operational disruptions.`;

export const DECISIONS = `You have 5 maintenance decisions awaiting your approval today. Here are the details:

1. **WO-B-301-4527**
2. **Asset:** AHU-3
3. **Building:** Bishopsgate Tower
4. **Vendor:** Apex Mechanical
5. **Priority:** P2
6. **Estimated Cost:** £1,800
7. **Due Date:** 28th September 2026
8. **Detail:** AHU-3 supply fan — bearing degradation signature: motor current 19% above 30-day baseline for 72 hours.
9. **Statutory Requirement:** Yes (Certificate: CHAS_SSIP-APXM-04, Expires: 25th October 2026)

1. **WO-B-301-4533**
2. **Asset:** Lift Asset-4471
3. **Building:** Bishopsgate Tower
4. **Vendor:** Apex Lifts
5. **Priority:** P3
6. **Estimated Cost:** £520
7. **Due Date:** 3rd October 2026
8. **Detail:** LOLER thorough examination for Lift Asset-4471 before expiry.
9. **Statutory Requirement:** Yes (Certificate: LOLER-B-301-4471, Expires: 18th October 2026)`;

export const RISK_ALL = `Here's a comprehensive overview of everything currently at risk across compliance, contracts, and energy/asset risk:

### 1. Compliance Overview
- **Total Certificates:** 70
- **At Risk:** 0 certificates are currently flagged as non-compliant or lapsed.

### 2. Contracts Overview
- **Total Contracts:** 13
- **Contracts Behind Plan:** 6
- **Missed Visits:** 2
- **Late Visits:** 2
- **Completion Percentage:** 156.8% (indicating some contracts are ahead of schedule)

**Contracts Behind Plan:**
| Contract Name | Vendor | Completion % | Missed Visits | Late Visits |
|---|---|---|---|---|
| Mechanical PPM · Bishopsgate | Apex Mechanical | 68.8% | 2 | 2 |
| Lifts · Bishopsgate | Apex Lifts | 75.0% | 0 | 0 |

### 3. Asset Risk Overview
- **Total Assets Inspected:** 61
- **Assets Graded Poor:** 2 assets graded as poor (condition score of 4 out of 5).

**Poorly Graded Assets:**
| Asset Name | Asset Code | Condition Score | Installation Date | Building |
|---|---|---|---|---|
| CHILLER-101 | B-301-CHILLER-101 | 4 | 2009-03-15 | Bishopsgate Tower |

### 4. Energy Anomalies
- **Open Anomalies:** 23
- **Corroborated Anomalies:** 0 (none of the open anomalies have been corroborated by earlier findings).

**Notable Energy Anomalies:**
| Anomaly ID | Asset Name | Anomaly Type | Cost (GBP) |
|---|---|---|---|
| d33ea901-8249-423e-9e88-4e3b2ca85d3b | AHU-3 | Non-occurrence Spike | 27,432.15 |

### Summary
- **Compliance:** No current risks.
- **Contracts:** 6 contracts are behind plan, with a few missed and late visits.`;

export const CLARIFY = `Before I read anything, I want to be sure what you are asking: Ambiguous which 'late' is meant — work orders, PPM, SLAs or certificates — needs clarification.

Tell me which you mean - or name the register (parts stock, assets, work orders, certificates, contracts, meters, documents) - and I will answer from it. Say "all of them" and I will answer each reading.`;

test('prose answers get the layout too: the Overall line, as the dashboard narrative', () => {
  const c = answerCards(CLARIFY);
  assert.equal(c.overall.length, 2);
  assert.deepEqual(c.sections, []);
  assert.deepEqual(answerCards('Three boilers are overdue.'), { overall: ['Three boilers are overdue.'], sections: [] });
  const h = answerCards('## Vendor ranking by first-time fix\nNo vendor scorecards are currently available.');
  assert.deepEqual(h.sections.map((x) => [x.title, x.parts[0].block.t]), [['Vendor ranking by first-time fix', 'p']]);
  assert.equal(answerCards(''), null);
  assert.equal(answerCards('   \n  '), null);
});

test('the overview answer: an Overall line, one card per heading, figures as tiles', () => {
  const m = answerCards(OVERVIEW);
  assert.ok(m);
  assert.deepEqual(m.overall, ["Here's a comprehensive overview of everything currently at risk across various registers:"]);
  const titles = m.sections.map((s) => s.title);
  assert.deepEqual(titles, ['1. Compliance and Certificates', '2. Planned Preventive Maintenance (PPM) Contracts', 'Contracts Behind Plan',
    '3. Asset Condition and Inspection Findings', '4. Energy Anomalies', '5. Ingestion Status', 'Summary']);
  const ppm = m.sections[1];
  const tiles = ppm.parts.find((p) => p.t === 'kpis').kpis;
  assert.equal(tiles.length, 8);
  assert.deepEqual(tiles[3], { value: '156.8%', label: 'Completion Percentage', sub: '' });
  // the table stays a table, in its own card
  assert.equal(m.sections[2].parts[0].block.t, 'table');
  assert.equal(m.sections[2].tone, 'critical');
  // the nested asset lines are kept beside the tiles, not turned into tiles
  const cond = m.sections[3];
  assert.deepEqual(cond.parts.find((p) => p.t === 'kpis').kpis.map((k) => k.value), ['61', '16', '2']);
  assert.deepEqual(cond.parts.find((p) => p.t === 'kpis').kpis[1].sub, '(8 flagged by energy)');
  assert.equal(cond.parts.find((p) => p.t === 'block').block.items.length, 2);
  // two figures out of five still make tiles; the rest stays a list
  const energy = m.sections[4];
  assert.deepEqual(energy.parts.find((p) => p.t === 'kpis').kpis.map((k) => k.value), ['23', '0']);
  assert.equal(m.sections[6].kind, 'summary');
});

test('the restarting numbered lists become one card per certificate with its fields', () => {
  const m = answerCards(CERTS);
  const lapsed = m.sections.find((s) => s.title === 'Lapsed Certificates');
  assert.equal(lapsed.kicker, 'Compliance Certificates at Risk');
  const ents = lapsed.parts.filter((p) => p.t === 'entities').flatMap((p) => p.entities);
  assert.deepEqual(ents.map((e) => e.title), ['Display Energy Certificate (DEC)', 'Fire Risk Assessment (FRA)']);
  assert.deepEqual(ents[0].fields[0], { key: 'Building', value: 'Manchester Town Hall' });
  assert.equal(ents[0].fields.find((f) => f.key === 'Certificate Number').value, '9920-1010-0626-0890-2091');
  assert.ok(ents[0].fields.some((f) => f.key === '' && f.value.includes('[View Certificate](https://find-energy-certificate')));
  const risk = m.sections.find((s) => s.title === 'Assets at Risk');
  assert.deepEqual(risk.parts[0].kpis[1], { value: '£230,160.70', label: 'Total Value at Risk', sub: '(for those with available costs)' });
  const next = m.sections.find((s) => s.title === 'Next Steps');
  assert.equal(next.kind, 'actions');
  assert.equal(next.parts[0].actions.length, 2);
});

test('a short answer with a table gets its card, and a "what to do next" paragraph becomes an action', () => {
  const m = answerCards(VENDORS);
  assert.deepEqual(m.overall, ['There are currently **9 blocked vendors**. Here are the details:']);
  assert.equal(m.sections[0].parts[0].block.t, 'table');
  assert.equal(m.sections[1].kind, 'actions');
  assert.match(m.sections[1].parts[0].actions[0].text, /initiate the renewal process/);
});

test('decisions written as restarting numbered lists become one card per work order', () => {
  const m = answerCards(DECISIONS);
  assert.deepEqual(m.overall, ['You have 5 maintenance decisions awaiting your approval today. Here are the details:']);
  const ents = m.sections.flatMap((s) => s.parts).filter((p) => p.t === 'entities').flatMap((p) => p.entities);
  assert.deepEqual(ents.map((e) => e.title), ['WO-B-301-4527', 'WO-B-301-4533']);
  assert.deepEqual(ents[0].fields.map((f) => f.key), ['Asset', 'Building', 'Vendor', 'Priority', 'Estimated Cost', 'Due Date', 'Detail', 'Statutory Requirement']);
  assert.equal(ents[1].fields[4].value, '£520');
});

test('the "all of them" answer is cards whichever heading style the model wrote', () => {
  for (const text of [RISK_ALL, RISK_ALL.replace(/^### (.*)$/gm, '**$1**')]) {
    const m = answerCards(text);
    assert.ok(m);
    assert.deepEqual(m.sections.map((s) => s.title), ['1. Compliance Overview', '2. Contracts Overview', 'Contracts Behind Plan', '3. Asset Risk Overview',
      'Poorly Graded Assets', '4. Energy Anomalies', 'Notable Energy Anomalies', 'Summary']);
    const contracts = m.sections[1].parts.find((p) => p.t === 'kpis').kpis;
    assert.deepEqual(contracts.map((k) => k.value), ['13', '6', '2', '2', '156.8%']);
    assert.equal(contracts[4].sub, '(indicating some contracts are ahead of schedule)');
    assert.deepEqual(m.sections[0].parts.find((p) => p.t === 'kpis').kpis.map((k) => k.value), ['70', '0']);
    assert.equal(m.sections[4].parts[0].block.t, 'table');
  }
});

test('figures are figures; dates, ids and prose are not', () => {
  assert.deepEqual(kpiOf(keyValue('**Missed Visits:** 2')), { value: '2', label: 'Missed Visits', sub: '' });
  assert.deepEqual(kpiOf(keyValue('**Pending Approvals:** 500 items in the approvals queue.')), { value: '500', label: 'Pending Approvals', sub: 'items in the approvals queue.' });
  assert.equal(kpiOf(keyValue('**Certificate Number**: 9920-1010-0626-0890-2091')), null);
  assert.equal(kpiOf(keyValue('**Installation Date:** 2009-03-15')), null);
  assert.equal(kpiOf(keyValue('**Asset:** B-301-CHILLER-101')), null);
  assert.equal(kpiOf(keyValue('**Status:** Lapsed')), null);
  assert.equal(titleOf('**Fire Risk Assessment (FRA)**'), 'Fire Risk Assessment (FRA)');
  assert.equal(titleOf('**Building**: Bishopsgate Tower'), null);
  assert.equal(titleOf('**[View Certificate](https://example.com/c/1)**'), null);
  assert.equal(entitiesOf(['**Total:** 3', '**Late:** 1']), null);
  assert.equal(cleanTitle('2. **Planned Preventive Maintenance (PPM) Contracts**:'), '2. Planned Preventive Maintenance (PPM) Contracts');
  assert.equal(toneOf('Contracts Behind Plan'), 'critical');
  assert.equal(toneOf('Ingestion Status'), 'neutral');
});

// The cards show the backend's answer as written. Read back in source order, the card model must
// give exactly the answer's words, numbers included, in the same order: nothing dropped, added or
// moved. (On screen a tile shows its figure above its label; that is layout, not the text.)
export const tokens = (s) => String(s).replace(/\[([^\]]*)\]\(([^)]*)\)/g, '$1 $2').match(/[\p{L}\p{N}]+/gu) || [];
const blockText = (b) => b.t === 'table' ? [...b.head, ...b.rows.flat()].join(' ')
  : b.t === 'list' ? b.items.join(' ') : String(b.body || '');
export function modelText(m) {
  const out = [...m.overall];
  for (const s of m.sections) {
    out.push(s.kicker, s.title);
    for (const p of s.parts) {
      if (p.t === 'kpis') p.kpis.forEach((k) => out.push(k.label, k.value, k.sub));
      else if (p.t === 'entities') p.entities.forEach((e) => { out.push(e.title); e.fields.forEach((f) => out.push(f.key, f.value)); });
      else if (p.t === 'actions') p.actions.forEach((a) => out.push(a.text, ...a.subs));
      else out.push(blockText(p.block));
    }
  }
  return out.filter(Boolean).join(' ');
}
for (const [name, text] of Object.entries({ OVERVIEW, CERTS, VENDORS, DECISIONS, CLARIFY, RISK_ALL, RISK_ALL_BOLD: RISK_ALL.replace(/^### (.*)$/gm, '**$1**') })) {
  test(`the ${name} answer is shown word for word, in order`, () => {
    // list markers ("1.", "-") are markdown syntax, like "###" and "|"; the answer's words are the rest
    const source = text.split('\n').map((l) => l.replace(/^\s*([-*+]|\d+[.)])\s+/, '')).join('\n');
    assert.deepEqual(tokens(modelText(answerCards(text))), tokens(source));
  });
}

// Shapes the frontend review found (5 Oct 2026): each must be shown word for word, in order.
const PROBES = {
  boldOnly: '**Done. The work order has been raised.**',
  headingOnly: '## Nothing found',
  closingQuestion: '## Stats\n- Total: 61\n- Overdue: 5\n\n**Would you like me to raise work orders for these?**',
  trailingHeadings: '## A\n- Total: 61\n- Overdue: 5\n## Part two\n## More',
  threeHeadings: '# Portfolio\n## Compliance\n### Certificates\n- Total: 61\n- Overdue: 5',
  inlineMarks: '### Certificates at *Manchester* see [docs](https://x.com)\n- Total: 61\n- Overdue: 5',
  linkKey: '1. **Gas Safe record**\n2. **[Certificate](https://x.com/c):** valid\n3. **Expiry:** 2026-11-01',
  transactions: '## Recent Transactions\n- Invoice 123: £500\n- Invoice 124: £700',
  nestedSteps: '## Next steps\n1. Renew the DEC\n   - Book the assessor by Friday\n2. Chase Apex',
  codeAndQuote: '## Notes\n> Checked against the register.\n\n```\nSELECT 1\n```\n\n---\n\nDone.',
};
for (const [name, text] of Object.entries(PROBES)) {
  test(`probe ${name} is shown word for word, in order`, () => {
    const source = text.split('\n').map((l) => l.replace(/^\s*([-*+]|\d+[.)])\s+/, '')).join('\n');
    const m = answerCards(text);
    assert.ok(m);
    assert.deepEqual(tokens(modelText(m)), tokens(source));
  });
}

test('the probes land where they belong', () => {
  assert.deepEqual(answerCards(PROBES.boldOnly).overall, ['**Done. The work order has been raised.**']);
  assert.deepEqual(answerCards(PROBES.headingOnly).overall, ['Nothing found']);
  const q = answerCards(PROBES.closingQuestion);
  assert.equal(q.sections.length, 1);
  assert.equal(q.sections[0].parts[q.sections[0].parts.length - 1].block.body, '**Would you like me to raise work orders for these?**');
  assert.equal(answerCards(PROBES.threeHeadings).sections[0].kicker, 'Portfolio · Compliance');
  const t = answerCards(PROBES.transactions).sections[0];
  assert.equal(t.kind, 'card');
  assert.ok(!t.parts.some((p) => p.t === 'actions'));
  assert.equal(answerCards('## High priority sites\n- B-301: 4 issues\n- B-302: 2 issues').sections[0].kind, 'card');
  assert.equal(answerCards('## 3. Recommended actions\n- Renew the DEC').sections[0].kind, 'actions');
  for (const t of ['Next actions by vendor', 'Highest-priority actions', 'Corrective action plan', 'Recommendations', 'What to do next'])
    assert.equal(answerCards(`## ${t}\n- Renew the DEC`).sections[0].kind, 'actions', t);
  for (const t of ['Recent Transactions', 'Interactions this week', 'High priority sites', 'Statutory decisions to prioritise'])
    assert.equal(answerCards(`## ${t}\n- Renew the DEC`).sections[0].kind, 'card', t);
  const steps = answerCards(PROBES.nestedSteps).sections[0].parts[0].actions;
  assert.deepEqual(steps, [{ text: 'Renew the DEC', subs: ['Book the assessor by Friday'] }, { text: 'Chase Apex', subs: [] }]);
});

test('a split numbered list keeps its numbers', () => {
  const m = answerCards('### Items\n1. Note one\n2. **Total:** 5\n3. **Late:** 2\n4. Note two');
  const parts = m.sections[0].parts;
  assert.deepEqual(parts.map((p) => p.t), ['block', 'kpis', 'block']);
  assert.equal(parts[0].block.start, 1);
  assert.equal(parts[2].block.start, 4);
});

test('list items keep their nesting depth for the dashboard; the plain renderer is unchanged', () => {
  const b = parseBlocks('- a\n  - b\n- c').find((x) => x.t === 'list');
  assert.deepEqual(b.items, ['a', 'b', 'c']);
  assert.deepEqual(b.indents, [0, 2, 0]);
});
