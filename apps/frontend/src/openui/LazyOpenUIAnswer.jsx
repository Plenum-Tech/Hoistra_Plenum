// LazyOpenUIAnswer — an OpenUI answer whose code loads on first use.
//
// Until the chunk arrives (or if it cannot be fetched at all, as in a tab left open across a
// deploy) the bubble shows the answer's Markdown, which the server always sends beside the
// program. The answer is never lost to a loading problem.
import React, { lazy, Suspense } from 'react';
import AnswerCards from '../components/shell/AnswerCards.jsx';
import { preloadOpenUI } from './host.js';

function MarkdownFallback({ fallbackText }) {
  return fallbackText ? <AnswerCards text={fallbackText} /> : null;
}

const OpenUIAnswer = lazy(() => preloadOpenUI().catch(() => ({ default: MarkdownFallback })));

export default function LazyOpenUIAnswer(props) {
  return (
    <Suspense fallback={<MarkdownFallback fallbackText={props.fallbackText} />}>
      <OpenUIAnswer {...props} />
    </Suspense>
  );
}
