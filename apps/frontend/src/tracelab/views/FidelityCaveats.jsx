// FidelityCaveats — what a golden run cannot measure the way live chat runs (addendum §2.5), shown on every golden
// run: in its detail, in the start dialog, and above every golden conversation.
import React from 'react';
import { FIDELITY_CAVEATS } from '../logic/golden.js';

export default function FidelityCaveats() {
  return (
    <div className="tl-caveats" role="note" aria-label="What a golden run cannot measure">
      <strong>{'What a golden run cannot measure'}</strong>
      <ul>{FIDELITY_CAVEATS.map((c) => <li key={c}>{c}</li>)}</ul>
    </div>
  );
}
