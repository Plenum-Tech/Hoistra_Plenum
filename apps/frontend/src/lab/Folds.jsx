// Folds - a long list of values, or a long sentence, behind one line (19 Nov 2026, Hussain: "make this collapsable the
// 14 cert or anything like this"). Used by the expected answer (TurnExpected) and both sides of the query editor's result
// (QueryEditor). Native <details>, so it opens and closes with no state of its own; what is folded is decided in
// logic/recordsValues.js (foldFacts, foldSentence).
import React from 'react';
import { foldFacts, foldSentence } from './logic/recordsValues.js';

// The sentence whole when it is short; else its start, "Show all", and the whole of it once opened.
export function Sentence({ text }) {
  const short = foldSentence(text);
  if (!short) return text ? <p className="lab-ses-pre">{text}</p> : null;
  return (
    <details className="lab-xp-more">
      <summary>
        <span className="lab-ses-pre lab-xp-short">{short}</span>
        <span className="lab-xp-toggle lab-xp-closed">{'Show all'}</span>
        <span className="lab-xp-toggle lab-xp-opened">{'Show less'}</span>
      </summary>
      <p className="lab-ses-pre">{text}</p>
    </details>
  );
}

// The values of one query, each drawn by `chip(fact)`: the single values in view, each long list behind its own line.
export function Values({ facts, chip }) {
  const { shown, folds } = foldFacts(facts);
  return (
    <>
      {shown.length || !folds.length ? <div className="lab-xp-chips">{shown.map(chip)}</div> : null}
      {folds.map((fd) => (
        <details key={fd.kind} className="lab-xp-fold">
          <summary className="lab-xp-fold-h">{fd.words}</summary>
          <div className="lab-xp-chips">{fd.facts.map(chip)}</div>
        </details>
      ))}
    </>
  );
}
