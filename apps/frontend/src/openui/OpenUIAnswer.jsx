// OpenUIAnswer — one orchestrator answer written in OpenUI Lang, drawn with the Hoistra library.
//
// Memoised on its strings alone. The chat re-renders on every keystroke and, while an answer
// streams, on every painted frame; a finished answer must not re-parse or re-draw for any of
// that. Clicks reach the chat through OpenUIHostContext, read from a ref, so the handler OpenUI
// sees never changes either.
//
// A program that draws nothing (every block dropped for a wrong type or an unknown name) shows
// the answer it came with (fallbackText, the full Markdown answer) when there is one, and its own
// words otherwise. While it streams, a partial program is left to fill in. (27 Oct 2026: a stored
// view that drew nothing showed only its string literals, every figure lost, though the full
// answer was in hand.)
import React, { memo, useCallback, useContext, useMemo, useRef } from 'react';
import { Renderer } from '@openuidev/react-lang';
import AnswerCards from '../components/shell/AnswerCards.jsx';
import Markdown from '../components/shell/Markdown.jsx';
import { library } from './library.jsx';
import { rendersSomething } from './parse.js';
import { plainTextOf, splitProgram } from './detect.js';
import { routeAction } from './actions.js';
import { OpenUIHostContext } from './host.js';

const DEV = (() => { try { return !!import.meta.env.DEV; } catch (e) { return false; } })();
// OpenUI prints every dropped component to the console unless told otherwise; in development the
// list is useful, in production it is noise in a customer's console.
const reportErrors = DEV
  ? (errors) => { if (errors && errors.length) console.debug('[openui]', errors); }
  : () => {};

function OpenUIAnswer({ text, tail, streaming, fallbackText }) {
  const host = useContext(OpenUIHostContext);
  const hostRef = useRef(host);
  hostRef.current = host;
  const onAction = useCallback((event) => { routeAction(event, hostRef.current); }, []);
  // Words the model wrote before its program are the reader's too: drawn above it, as text.
  const { lead, program } = useMemo(() => splitProgram(text), [text]);
  const empty = useMemo(() => !streaming && !rendersSomething(program), [program, streaming]);
  // The full answer already holds the lead and the note under it: drawn alone, said once.
  if (empty && fallbackText) return <div className="oui-host"><AnswerCards text={fallbackText} /></div>;
  return (
    <div className={streaming ? 'oui-host is-live' : 'oui-host'}>
      {lead ? <div className="oui-lead"><Markdown text={lead} /></div> : null}
      {empty
        ? <AnswerCards text={plainTextOf(program)} />
        : <Renderer response={program} library={library} isStreaming={!!streaming} onAction={onAction}
            onError={reportErrors} publishObservability={false} />}
      {tail ? (
        <div className="oui-tail" role="note"><i className="ph ph-warning" aria-hidden="true"></i><Markdown text={tail} /></div>
      ) : null}
    </div>
  );
}

export default memo(OpenUIAnswer, (a, b) => a.text === b.text && a.tail === b.tail && !!a.streaming === !!b.streaming
  && a.fallbackText === b.fallbackText);
