// AnyAnswer — an answer written in Markdown, drawn with the OpenUI components.
//
// Every route that does not write OpenUI itself (engine sub-agents, the planner's synthesis, the
// compliance summary, chats saved before OpenUI) answers in Markdown; fromMarkdown.js writes that
// text as a program, so it is drawn by the same components as the answers the model writes as
// OpenUI. Until the OpenUI chunk loads, and for text with nothing to draw, it is the card layout.
//
// VITE_OPENUI_ALL_ANSWERS=0 switches this off: Markdown answers are the card layout again.
import React, { memo, useMemo } from 'react';
import AnswerCards from '../components/shell/AnswerCards.jsx';
import LazyOpenUIAnswer from './LazyOpenUIAnswer.jsx';
import { programFromMarkdown } from './fromMarkdown.js';

export const ALL_ANSWERS = (() => {
  try { return String(import.meta.env.VITE_OPENUI_ALL_ANSWERS || '1') !== '0'; } catch (e) { return true; }
})();

function AnyAnswer({ text, streaming }) {
  const program = useMemo(() => (ALL_ANSWERS ? programFromMarkdown(text) : ''), [text]);
  if (!program) return <AnswerCards text={text} />;
  return <LazyOpenUIAnswer text={program} fallbackText={text} streaming={streaming} />;
}

// The whole app re-renders on every keystroke; an answer whose text has not changed is not
// converted or drawn again.
export default memo(AnyAnswer, (a, b) => a.text === b.text && !!a.streaming === !!b.streaming);
