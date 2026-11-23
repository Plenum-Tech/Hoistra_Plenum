// cardLinks — every figure card on the Energy, Assets and Maintenance pages opens what it counts (23 Nov 2026,
// Hussain: "I want to make all the cards clickable").
//
// A card opens the list on its own page that it summarises, with that list's own filter set, and scrolls to it -
// the in-place drill-down Compliance and Vendors already have, built only from controls the page already has (the
// filter chips, Maintenance's sections and its inspection questions). A card whose figure has no list behind it -
// Energy's ratings and duties - asks the orchestrator which buildings it counts, the way the page's own Investigate
// does. Nothing here writes: a click is a filter, a scroll or a question.

// What a card opens: { filter } a chip of its page's list, { statutory } Maintenance's statutory-only view,
// { section } the element id scrolled to, { ask } a question for the page's own asker. null: it opens nothing.
export function cardTarget(modKey, card) {
  if (!card) return null;
  const l = String(card.l || '');
  if (modKey === 'assets') {
    const band = { 'Threat': 'Threat', 'Watch': 'Watch', 'In control': 'In control',
      'Est. asset value at risk': VALUE_AT_RISK }[l];
    return band ? { filter: band, section: 'mod-list' } : null;
  }
  if (modKey === 'energy') {
    const f = { 'Buildings in scope': 'All', 'EUI, area weighted': 'Over benchmark',
      'Cost above benchmark / year': 'Over benchmark', 'Anomaly cost / year': 'With anomalies' }[l];
    return f ? { filter: f, section: 'mod-list' } : null;
  }
  if (modKey === 'ops') {
    switch (card.k) {
      case 'decisions_owed': return { filter: 'All', statutory: false, section: 'mx-decisions' };
      case 'statutory': return { filter: 'All', statutory: true, section: 'mx-decisions' };
      // The same question as the inspection panel's own "recommendations never converted to orders" card.
      case 'recommendations_unconverted': return { section: 'mx-insp', inspect: UNCONVERTED_Q };
      case 'ppm_to_plan': return { section: 'mx-ppm' };
      default: return null;
    }
  }
  return null;
}

// The Assets chip the "Est. asset value at risk" card opens: the assets that figure is summed from.
export const VALUE_AT_RISK = 'Value at risk';
export const UNCONVERTED_Q = 'Which recommendations were never converted to orders?';

// The question a ratings-and-duties card asks: which buildings it counts, in its own words, and what to do.
export function ratingQuestion(card) {
  const what = String(card.l || '').trim();
  const figure = [card.v, card.s].filter((x) => x && x !== '—').join(' — ');
  return 'Which of my buildings are counted in "' + what + '"' + (figure ? ' (' + figure + ')' : '')
    + ', and what should I do about each of them?';
}

// Whether a card has anything to open yet: a placeholder ("…", the loading or failed ratings tile) opens nothing.
export function opens(card) {
  return !!card && card.v !== '…' && String(card.l || '') !== 'Ratings';
}

// Scroll the element `id` into view once the page has drawn the change a click made (the filter, the view).
export function scrollToSection(id, win) {
  const w = win || (typeof window !== 'undefined' ? window : null);
  if (!w || !id) return;
  const go = () => {
    const el = w.document && w.document.getElementById(id);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };
  if (typeof w.requestAnimationFrame === 'function') w.requestAnimationFrame(() => w.setTimeout(go, 30));
  else w.setTimeout(go, 30);
}

// The cards with their `click`: `app` is the page's logic (setState, asCondSetFilter, mxSetFilter, mxInspAsk, askNew).
export function withCardLinks(modKey, cards, app) {
  return (cards || []).map((c) => {
    const target = opens(c) ? cardTarget(modKey, c) : null;
    if (!target) return c;
    return Object.assign({}, c, {
      clickTitle: target.inspect ? 'Ask: ' + target.inspect : 'Show what ' + c.l + ' counts',
      click: () => openTarget(modKey, target, app)
    });
  });
}

export function withRatingLinks(cards, app) {
  return (cards || []).map((c) => (!opens(c) ? c : Object.assign({}, c, {
    clickTitle: 'Ask which buildings ' + c.l + ' counts',
    click: () => app.askNew(ratingQuestion(c))
  })));
}

export function openTarget(modKey, target, app) {
  if (modKey === 'ops') {
    if (typeof target.statutory === 'boolean') app.setState({ mxStatutory: target.statutory });
    if (target.filter) app.mxSetFilter(target.filter);
    if (target.inspect) app.mxInspAsk(target.inspect);
  } else if (target.filter) {
    // The same as clicking the chip (renderVals modFilters): Assets asks its condition engine for a band.
    app.setState({ filter: target.filter });
    if (modKey === 'assets' && app.asCondSetFilter) app.asCondSetFilter(target.filter);
  }
  scrollToSection(target.section);
}
