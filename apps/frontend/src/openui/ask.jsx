// ask — the controls that turn a piece of an answer into the next question.
//
// (28 Oct 2026, Hussain: "more clickable option and interactive". A step without a button of its
// own drew no arrow, and record codes, table rows, bars and findings were plain text: nothing in
// a finished answer led anywhere.) Every one of them is now a real <button> that asks a question
// built from the answer's own words, in this conversation, through OpenUI's own action runner
// (useTriggerAction, routed by OpenUIAnswer to the chat's ask). The question is said in full in
// the control's name and tooltip, so the reader knows what a click will ask before making it.
//
// None of these adds a word to the answer: an arrow is an icon with a name, a code keeps its own
// characters, and a row or a bar is clicked where it already is. Each is disabled while its
// answer is still being written: a half-streamed row asks about half a name.
import React, { createContext, useContext } from 'react';
import { useIsStreaming, useTriggerAction } from '@openuidev/react-lang';
import { scrubInternal } from '../logic/publicText.js';

// OpenUI's hooks throw outside a <Renderer>. A component drawn on its own (a test, a preview)
// gets no action and draws its words as plain text, exactly as before. Each hook is always
// called, so the order of hooks never changes between renders of one component.
export function useAsk() {
  let trigger = null;
  let streaming = false;
  try { trigger = useTriggerAction(); } catch (e) { trigger = null; }
  try { streaming = !!useIsStreaming(); } catch (e) { streaming = false; }
  const ready = typeof trigger === 'function';
  return {
    ready,
    streaming,
    trigger,
    ask: (question) => {
      const q = String(question == null ? '' : question).trim();
      if (ready && !streaming && q) trigger(q);
    }
  };
}

// Inside a control that is itself the action (a table row's label, a bar's label), a record code
// is part of that label and is drawn as a plain tag: a button never sits inside another one.
export const PlainCodes = createContext(false);

// The answer's lead (Layout.jsx Answer) is a summary, not a list of issues: its bold-lead
// paragraphs are neither cards nor actions.
export const InBrief = createContext(false);

// Words as a question can carry them: links reduced to their text, emphasis marks dropped,
// internal names scrubbed, spaces collapsed.
export function plainOf(v) {
  const s = String(v == null ? '' : v)
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\*\*|__|`/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return scrubInternal(s);
}
// A lead without the punctuation the sentence carried on with ("Lapsed:" → "Lapsed").
const bareLead = (s) => plainOf(s).replace(/[\s,.;:—–-]+$/, '');

// The questions. Each is built from the answer's own words only.
export const stepQuestion = (title) => {
  const t = bareLead(title);
  return t ? 'Walk me through this step: ' + t : '';
};
export const aboutQuestion = (name) => {
  const n = bareLead(name);
  return n ? 'Tell me more about ' + n : '';
};
export const codeQuestion = (code) => {
  const c = plainOf(code);
  return c ? 'Tell me about ' + c : '';
};
// A bar: the item and the figure the chart shows for it ("Tell me more about AHU-3: £74,092").
// The chart's title is left to the conversation, which already holds it: said again in every
// bar's name, a section titled by its one chart read the title once per bar.
export function barQuestion(label, value) {
  const n = bareLead(label);
  if (!n) return '';
  const v = plainOf(value);
  return 'Tell me more about ' + n + (v && v !== '—' ? ': ' + v : '');
}
export function findingQuestion(title, code) {
  const t = bareLead(title);
  const c = plainOf(code);
  if (!t && !c) return '';
  return 'What should I do about ' + (t && c ? t + ' (' + c + ')' : t || c) + '?';
}

// A click on a row or a card (anywhere but a control inside it) asks the row's question, as its
// arrow does. Selecting text in the row is not a click on it: the reader may be copying a figure.
export function rowClick(run) {
  return (e) => {
    const t = e && e.target;
    if (t && typeof t.closest === 'function' && t.closest('button, a, summary, input, textarea, select, label')) return;
    try {
      const sel = typeof window !== 'undefined' && window.getSelection ? String(window.getSelection()) : '';
      if (sel.trim()) return;
    } catch (err) { /* no selection to read */ }
    run();
  };
}

// The arrow: a round icon button whose name is the question it asks. Quiet at rest; it takes the
// accent when its row is pointed at or it has focus (openui.css .oui-ask).
export function AskArrow({ question, className }) {
  const { ready, streaming, ask } = useAsk();
  const q = String(question == null ? '' : question).trim();
  if (!ready || !q) return null;
  return (
    <button type="button" className={className ? 'oui-ask ' + className : 'oui-ask'} disabled={streaming}
      aria-label={q} title={q} onClick={() => ask(q)}>
      <i className="ph ph-arrow-right" aria-hidden="true"></i>
    </button>
  );
}

// A record code in the answer ("FRA-B-301-2025", "WO-1184"): a tag that asks about the record.
// Its characters are the answer's own, unchanged. Outside an answer, or inside a control that is
// already the action, it is the plain tag it always was.
export function CodeTag({ code }) {
  const plain = useContext(PlainCodes);
  const { ready, streaming, ask } = useAsk();
  const q = codeQuestion(code);
  if (plain || !ready || !q) return <span className="oui-ref">{code}</span>;
  const name = 'Ask about ' + plainOf(code);
  return (
    <button type="button" className="oui-ref oui-code-ask" disabled={streaming} aria-label={name} title={name}
      onClick={() => ask(q)}>{code}</button>
  );
}
