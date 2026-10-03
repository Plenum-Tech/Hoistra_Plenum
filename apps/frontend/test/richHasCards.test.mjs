// richHasCards — whether a compliance `rich` payload has any card content of its own.
//
// compliance_response does not fire on every turn: a question can route through
// compliance_pipeline alone, or through a plain skill with no structured payload at all.
// extractComplianceAnswer() still returns a `rich` object in that case (the pipeline's steps
// are enough to build one), with every card field empty — and ComplianceAnswer must fall back
// to the markdown answer then, or the reply renders as an empty card with the real answer
// thrown away. This fixture is the exact tool_calls array a live orchestrator call returned
// for "Which buildings put me at risk this month?" (captured 09 Sep 2026): no compliance_response,
// only three lookups and compliance_pipeline.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractComplianceAnswer, richHasCards } from '../src/logic/complianceLive.js';

const LIVE_TOOL_CALLS_NO_RESPONSE = [
  { tool: 'list_building_certificates', output: { certificates: [] } },
  { tool: 'list_vendor_accreditations', output: { certificates: [] } },
  { tool: 'get_compliance_saved_space_summary', output: { ok: true, building_certificates: {}, vendor_certificates: {} } },
  { tool: 'compliance_pipeline', output: { steps: [
    { stage: 'plan', label: 'Planned the question', detail: 'buildings with certificates expiring this month — building' },
    { stage: 'data', label: 'Read the certificate register', queries: [{ matched_rows: 1 }] },
    { stage: 'analyse', label: 'Compliance analyst reasoning' }
  ] } }
];

test('a turn that only ran the pipeline (no compliance_response) still builds a rich object, from its steps alone', () => {
  const rich = extractComplianceAnswer(LIVE_TOOL_CALLS_NO_RESPONSE);
  assert.ok(rich, 'rich is not null — the pipeline alone is enough to build one');
  assert.equal(rich.narrative, '');
  assert.equal(rich.groups.length, 0);
  assert.equal(rich.kpis.length, 0);
  assert.equal(rich.steps.length, 3);
});

test('richHasCards is false for that payload — nothing but the step list — so the caller must fall back to the plain answer', () => {
  const rich = extractComplianceAnswer(LIVE_TOOL_CALLS_NO_RESPONSE);
  assert.equal(richHasCards(rich), false);
});

test('richHasCards is true the moment any one card field is populated', () => {
  assert.equal(richHasCards({ narrative: 'Two buildings are at risk.' }), true);
  assert.equal(richHasCards({ narrative: '', groups: [{ label: 'x' }] }), true);
  assert.equal(richHasCards({ narrative: '', kpis: [{ label: 'x', count: 1 }] }), true);
  assert.equal(richHasCards({ narrative: '', offers: [{ kind: 'confirm_draft' }] }), true);
});

test('richHasCards is false for null, and for a payload with only steps', () => {
  assert.equal(richHasCards(null), false);
  assert.equal(richHasCards({ narrative: '', sections: [], groups: [], kpis: [], actions: [], insights: [], certificates: [], pending: [], offers: [], steps: [{ stage: 'plan', label: 'x' }] }), false);
});

test('a turn that DOES get compliance_response has cards, and no fallback is needed', () => {
  const withResponse = LIVE_TOOL_CALLS_NO_RESPONSE.concat([
    { tool: 'compliance_response', output: {
      narrative: 'Two buildings carry lapsed life-safety certificates.',
      groups: [{ label: 'Building — Fire', items: [] }], kpis: [{ label: 'At risk', count: 2 }],
      actions: [], insights: [], certificates: [], pending: [], sections: [], offers: []
    } }
  ]);
  const rich = extractComplianceAnswer(withResponse);
  assert.match(rich.narrative, /lapsed life-safety/);
  assert.equal(richHasCards(rich), true);
});
