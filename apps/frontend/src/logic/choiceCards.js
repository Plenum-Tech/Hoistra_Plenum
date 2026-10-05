// choiceCards — option cards a reply offers, rendered under it in the chat and the dock.
//
// The first reply that offers them is "I want to migrate my data" (svc-deepagents
// agents/migration_chooser.py): three cards, CSV / Excel data migration, PDF / document migration
// and direct database migration, each a button. The reply's text still says the same thing for
// anything that reads text; when the cards are present the chat shows its first line and the cards
// in place of the text.
//
// A card's `action` decides what a click does:
//   attach  open the file picker for those types and send what was picked. A spreadsheet sent
//           with no text starts a migration; a document is ingested (orchSubmitNow).
//   ask     send the card's text as the next question.

import { accountCanIngest } from './auth.js';

// Only what the chat can draw: a card with no title, or an action it does not know, is left out.
export function validChoices(choices) {
  return (Array.isArray(choices) ? choices : []).filter((c) =>
    c && typeof c === 'object' && String(c.title || '').trim() && c.action
    && ((c.action.kind === 'attach' && String(c.action.accept || '').trim())
      || (c.action.kind === 'ask' && String(c.action.text || '').trim())));
}

// The view of each card; `pick(c)` runs it.
export function choiceCards(choices, pick) {
  return validChoices(choices).map((c, i) => ({
    key: String(c.id || i),
    n: c.n || i + 1,
    icon: String(c.icon || 'ph-arrow-right'),
    title: String(c.title),
    detail: String(c.detail || ''),
    cta: String(c.cta || (c.action.kind === 'attach' ? 'Choose files' : c.action.text)),
    pick: () => pick(c)
  }));
}

// The line above the cards: the reply's first paragraph, without markdown emphasis.
export function choiceIntro(text) {
  const first = String(text || '').split(/\n\s*\n/)[0] || '';
  return first.replace(/\*\*|__/g, '').trim();
}

export const choiceMethods = {
  // A card was clicked.
  orchChoice(c) {
    const a = (c && c.action) || {};
    if (this.state.ccBusy) return this.flash('Still answering — stop it first, or wait for it to finish.');
    if (a.kind === 'ask') return this.askScoped(String(a.text || ''));
    if (a.kind !== 'attach' || typeof document === 'undefined') return undefined;
    // A picker of its own, filtered to the card's types: the composer's picker takes every type.
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = String(a.accept || '');
    input.style.display = 'none';
    input.onchange = () => {
      const files = Array.from(input.files || []);
      input.remove();
      if (!files.length) return;
      if (!accountCanIngest(this.state)) {
        this.flash('Your account cannot add data. Ask an administrator to grant upload access.');
        return;
      }
      // The staged files are the instruction: a spreadsheet starts its migration, a document is
      // ingested. Sent from the update's callback, once the tray holds them (same cap as ccAddFiles).
      this.setState((p) => ({ ccFiles: (p.ccFiles || []).concat(files).slice(0, 10) }), () => this.orchSubmitNow());
    };
    document.body.appendChild(input);
    input.click();
    return undefined;
  }
};
