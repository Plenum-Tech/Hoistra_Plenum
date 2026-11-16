// The answer and its steps exactly as Hoistra showed them (16 Nov 2026, Hussain: "make sure u show me the exact trace
// steps and exact same ans in the trace lab").
//
// The answer: a trace keeps only the answer's text. The cards (KPIs, priority actions, owner groups) and the OpenUI view
// Hoistra drew it with are kept on the chat's own row of the turn (plenum_cafm.chat_turns.tools, services/chat_threads.py
// _card_payloads), which GET /eval/traces/{turn_id}/as-shown reads, read-only. They are drawn here with Hoistra's own
// components, the way a reopened chat draws a stored turn (logic/sessions.js turnsFromThread, OrchestratorDock): cards
// with ComplianceAnswer, a view alone with LazyOpenUIAnswer, text alone with AnyAnswer. Nothing of Hoistra's is changed.
//
// The steps: the line under each step's name follows Hoistra's run trace (logic/corrections.js): rows for a records
// query, "One step: <engine>" for a plan, "decided: <tool>" for a model call that chose a tool, the opening of a model
// call's text, the ask of a step, and "Failed: <error>" for one that failed.
import React from 'react';
import ComplianceAnswer from '../components/shell/ComplianceAnswer.jsx';
import LazyOpenUIAnswer from '../openui/LazyOpenUIAnswer.jsx';
import AnyAnswer from '../openui/AnyAnswer.jsx';
import { extractComplianceAnswer } from '../logic/complianceLive.js';
import { extractOpenUIView } from '../logic/openuiView.js';

function parse(v) {
  if (v && typeof v === 'object') return v;
  if (typeof v !== 'string' || !v.trim()) return null;
  try { return JSON.parse(v); } catch (e) { return null; }
}

export function stepDetail(node) {
  const k = node || {};
  const inp = parse(k.input) || {};
  const o = parse(k.output) || {};
  if (k.ok === false && k.error) return 'Failed: ' + k.error;
  if (k.kind === 'plan') {
    if (o.rejected) return 'Rejected: ' + o.rejected;
    const plan = o.plan || {};
    const first = (Array.isArray(plan.steps) ? plan.steps : [])[0] || {};
    return plan.goal || (plan.mode === 'single' && first.target ? 'One step: ' + first.target : '');
  }
  if (k.kind === 'step') return typeof inp.ask === 'string' ? inp.ask.slice(0, 160) : '';
  const chose = Array.isArray(o.tool_calls) ? o.tool_calls.map((t) => t && t.name).filter(Boolean) : [];
  if (k.kind === 'llm' && chose.length) return 'decided: ' + chose.join(', ');
  if (k.kind === 'llm' && typeof o.content === 'string' && o.content) return o.content.slice(0, 120);
  if (k.kind === 'db') return o.row_count === null || o.row_count === undefined ? '' : o.row_count + (o.row_count === 1 ? ' row' : ' rows');
  return '';
}

// The stored row as the reopened chat reads it: { rich (the cards) | null, view { lang, tail } | null, text }.
export function answerParts(row) {
  const r = row || {};
  const payloads = (Array.isArray(r.tools) ? r.tools : []).filter((x) => x && typeof x === 'object' && typeof x.tool === 'string');
  return { rich: extractComplianceAnswer(payloads), view: extractOpenUIView(payloads), text: String(r.answer || '') };
}

// `shown` is GET /eval/traces/{turn_id}/as-shown. Nothing is drawn without the chat's row: the step keeps its own text.
export default function HoistraAnswer({ shown }) {
  const [open, setOpen] = React.useState(false);
  if (!shown || !shown.found) return null;
  const { rich, view, text } = answerParts(shown);
  return (
    <div className="lab-hoistra-answer" role="group" aria-label="The answer as Hoistra showed it">
      {rich
        ? <ComplianceAnswer rich={rich} open={open} onToggle={() => setOpen(!open)} fallbackText={text}
            view={view ? view.lang : undefined} viewTail={view ? view.tail : undefined} />
        : view ? <LazyOpenUIAnswer text={view.lang} tail={view.tail} fallbackText={text} /> : <AnyAnswer text={text} />}
    </div>
  );
}
