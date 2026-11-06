// NextStep — the "what to do next" pop-up of the correction loop (Bala, 6 Nov 2026).
//
// The loop is ask -> annotate -> the agent proposes -> approve -> expected answer -> the agent drafts the fix -> approve for
// testing -> test the fix -> make live -> commit. A person new to it should not have to know that order: after each step
// this card says what just happened and what to do next, with a button that does it. It never acts by itself, and the
// steps themselves are unchanged - the agent only proposes and a person decides each one.
import React from 'react';

const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

// The pop-up after `event`. `act` names what its button does on the page that shows it (null: nothing more to do).
export function nextStepFor(event, data) {
  const d = data || {};
  switch (event) {
    case 'note_saved':
      return { title: 'Note saved', text: 'Next: let the agent read your note and propose the failure mode it shows.',
        actionLabel: 'Go to Loop', act: 'open_loop' };
    case 'proposed': {
      const modes = (d.proposed_modes || []).length;
      if (modes) {
        return { title: 'The agent proposed ' + plural(modes, 'failure mode', 'failure modes'),
          text: 'Read it under Waiting for a decision and approve it: its rule is added to SPEC.md and its labels are accepted in the same click.',
          actionLabel: 'Show it', act: 'show_waiting' };
      }
      if (d.labels) {
        return { title: 'The agent matched your note to a failure mode you already have',
          text: 'Accept the suggested label under Waiting for a decision.', actionLabel: 'Show it', act: 'show_waiting' };
      }
      return { title: 'Nothing new to propose', text: d.message || 'Write a note on a question of this run first.', act: null };
    }
    case 'mode_approved':
      return { title: d.spec ? 'Rule ' + d.spec.code + ' added to SPEC.md' : 'Failure mode confirmed',
        text: [plural(d.labels_accepted || 0, 'label', 'labels') + ' accepted',
          d.published === 'testing' ? 'SPEC.md is under test' : null,
          'Next: write the expected answer for its question, from the company’s records'].filter(Boolean).join('. ') + '.',
        actionLabel: 'Add expected answer', act: 'expected' };
    case 'label_accepted':
      return d.hasRule
        ? { title: 'Label accepted', text: 'Next: write the expected answer for the question, from the company’s records.',
          actionLabel: 'Add expected answer', act: 'expected' }
        : { title: 'Label accepted', text: 'Next: add this failure mode’s rule to SPEC.md.', actionLabel: 'Add to SPEC.md', act: 'add_rule' };
    case 'rule_added':
      return { title: 'Rule ' + (d.code || '') + ' added to SPEC.md',
        text: 'It is under test. Next: write the expected answer for its question, from the company’s records.',
        actionLabel: 'Add expected answer', act: 'expected' };
    case 'expected_saved':
      return { title: 'Expected answer saved', text: 'Next: let the agent draft the change to the instructions that fixes it.',
        actionLabel: 'Agent: draft fix', act: 'draft_fix' };
    case 'fix_drafted':
      return { title: 'The agent drafted a change' + (d.path ? ' to ' + d.path : ''),
        text: 'Read it under Instruction changes and approve it for testing. People using Hoistra keep the current text until you make it live.',
        actionLabel: 'Show it', act: 'show_changes' };
    case 'change_approved':
      return { title: 'The change is under test',
        text: 'Next: test the fix. The questions are asked again with the change and compared with their expected answers.',
        actionLabel: 'Test the fix', act: 'test_fix' };
    case 'tested': {
      if (d.no_answer) {
        return { title: 'No answer came back', text: 'A question ran out of time or failed, so nothing was judged. Ask again.',
          actionLabel: 'Test again', act: 'test_fix' };
      }
      if (d.fixed && !d.still_failing && !d.regressed) {
        return { title: 'Fixed', text: 'Every question now carries its expected values. Next: make the change live.',
          actionLabel: 'Show the change', act: 'show_changes' };
      }
      return { title: 'Not fixed yet',
        text: 'Read Compare: check the expected answer, or reject the change and let the agent draft another.',
        actionLabel: 'Show Compare', act: 'show_compare' };
    }
    case 'made_live':
      return { title: 'Live', text: 'Every agent reads it from its next answer, with no rebuild. Ask the question in Hoistra again to see it, then download the files for the commit.',
        actionLabel: 'Download for commit', act: 'export' };
    default:
      return null;
  }
}

// The card. `onAct(act)` runs the button's step; Later (or OK when there is nothing more to do) closes it.
export function NextStep({ step, busy, onAct, onClose }) {
  if (!step) return null;
  return (
    <div className="lab-next" role="dialog" aria-modal="false" aria-labelledby="lab-next-title">
      <div className="lab-next-kicker">{'Next step'}</div>
      <h2 id="lab-next-title" className="lab-next-title">{step.title}</h2>
      {step.text ? <p className="lab-next-text">{step.text}</p> : null}
      <div className="lab-row">
        {step.act ? (
          <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy}
            onClick={() => { if (onClose) onClose(); if (onAct) onAct(step.act); }}>{step.actionLabel || 'Do it'}</button>
        ) : null}
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onClose}>{step.act ? 'Later' : 'OK'}</button>
      </div>
    </div>
  );
}

export default NextStep;
