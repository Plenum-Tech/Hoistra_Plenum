// Finding — one issue of several the answer lists, each about one asset, certificate, vendor or
// work order: its lead, the record code when the answer wrote one, and the rest of what it said.
//
// (28 Oct 2026: answers that list issues as bold-lead paragraphs read as one grey slab, with no
// way to tell the lapsed certificate from the one due next month. The model may now lay each
// issue out as a Finding.) Consecutive Findings stack as a list through CSS (openui.css), so
// there is no container component. The words are the answer's own, drawn in the order it wrote
// them: title, code, text.
import React, { memo, useMemo } from 'react';
import { MarkdownBlocks } from '../../components/shell/Markdown.jsx';
import { inline } from '../../components/shell/markdownInline.jsx';
import { parseBlocks } from '../../logic/markdownBlocks.js';
import { scrubInternal } from '../../logic/publicText.js';
import { refs, sameData } from '../shared.jsx';
import { AskArrow, CodeTag, findingQuestion, rowClick, useAsk } from '../ask.jsx';

// The words that back a tone. The tone the model gives is only shown when the finding's own
// words say the same thing, so a tick never claims more than the sentence beside it does, and
// no tone is ever made up from the words alone. (28 Oct 2026: "LOLER statutory inspection past
// date." drew a lapsed lift amber, because only "past its/the due/expiry/inspection date" was read;
// the kind of date is now optional, so "past date" and "past its date" count too.)
const RISK_WORDS = /\b(?:lapsed|expired|overdue|blocked|failed|failing|breach(?:ed)?|past (?:its |the )?(?:(?:due|expiry|inspection) )?date|unaccredited|no current|not certified|invalid|critical)\b/;
const WARN_WORDS = /\b(?:expires|expiring|due|tomorrow|within \d+ days?|close to|at risk|poor|slipping|degraded|pending)\b/;
const OK_WORDS = /\b(?:valid|in date|compliant|healthy|met|on track|passed|resolved|clear)\b/g;
// "not met", "non-compliant", "never passed": the good word is there, the news is not.
const NEGATED = /(?:\bnot\s+|\bnever\s+|\bnon-?\s*)$/;

const said = (v) => String(v == null ? '' : v);
function saysOk(words) {
  for (const m of words.matchAll(OK_WORDS)) if (!NEGATED.test(words.slice(0, m.index))) return true;
  return false;
}

// The tone a finding is drawn in: the model's tone, held to its words.
//   risk  shows risk when a risk word is there, else warn when a warn word is, else neutral;
//   warn  shows warn when a warn or a risk word is there, else neutral;
//   ok    shows ok when an ok word is there (not negated) and no risk word is, else neutral;
//   anything else is neutral.
export function findingTone(tone, title, text, code) {
  const words = [said(title), said(text), said(code)].join(' ').toLowerCase();
  const risk = RISK_WORDS.test(words);
  const warn = WARN_WORDS.test(words);
  if (tone === 'risk') return risk ? 'risk' : warn ? 'warn' : 'neutral';
  if (tone === 'warn') return warn || risk ? 'warn' : 'neutral';
  if (tone === 'ok') return saysOk(words) && !risk ? 'ok' : 'neutral';
  return 'neutral';
}

// Props may arrive one at a time while the answer streams: a finding with only its title so far
// draws the title, and nothing is drawn for a part that has not arrived. The text is drawn as
// Text draws it: the same Markdown renderer, with record codes in it set as tags (shared.jsx refs).
//
// (28 Oct 2026: the title sat on a line of its own, so the text's first line opened on the
// answer's own punctuation: ", expired 2026-06-29…" under "Fire Risk Assessment — Lapsed".) The
// title now runs in to the text, as one sentence: the head is an inline span, the text's first
// paragraph is drawn here as MarkdownBlocks draws a paragraph (a direct child of the text, so the
// stylesheet can set it inline beside the head), and the rest of the text follows it as before.
// A space goes between head and text only when the text does not open on punctuation, and one
// between the title and its code; no word is added. Codes in the title are tags, as in the text.
const LEAD = /^\s*(\*\*|__)/;
const OPENS_ON_PUNCTUATION = /^[,.;:)]/;
export const Finding = memo(function Finding({ props }) {
  const title = said(props.title);
  const md = said(props.text);
  const code = said(props.code).trim();
  const blocks = useMemo(() => parseBlocks(md), [md]);
  const tone = findingTone(props.tone, props.title, props.text, props.code);
  const hasHead = Boolean(title.trim() || code);
  const hasText = Boolean(md.trim());
  const first = blocks[0] && blocks[0].t === 'p' ? blocks[0] : null;
  const rest = first ? blocks.slice(1) : blocks;
  // A finding is an issue to act on: a click on the card asks what to do about it, and the arrow
  // in its corner is the same question for a keyboard (28 Oct 2026). A code in its words keeps its
  // own ask, and so does the record's code beside the title (ask.jsx CodeTag).
  const { ready, streaming, ask } = useAsk();
  const question = ready ? findingQuestion(title, code) : '';
  return (
    <article className="oui-finding" data-tone={tone} onClick={question ? rowClick(() => { if (!streaming) ask(question); }) : undefined}>
      {hasHead ? (
        <span className="oui-finding-head">
          <span className="oui-finding-title">{refs(inline(title, 'ft'), 'ft')}</span>
          {code && title.trim() ? ' ' : null}
          {code ? <CodeTag code={scrubInternal(code, { code: true })} /> : null}
        </span>
      ) : null}
      {hasHead && hasText && !OPENS_ON_PUNCTUATION.test(md.trim()) ? ' ' : null}
      {hasText ? (
        <div className="oui-finding-text">
          {first ? (
            <div className="md-p" data-lead={LEAD.test(first.body) ? '' : undefined} style={{ lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
              {refs(inline(first.body, 'p0'))}
            </div>
          ) : null}
          {rest.length ? (first ? <div style={{ marginTop: '7px' }}><MarkdownBlocks blocks={rest} mark={refs} /></div> : <MarkdownBlocks blocks={rest} mark={refs} />) : null}
        </div>
      ) : null}
      {question ? <AskArrow question={question} className="oui-card-go" /> : null}
    </article>
  );
}, sameData);
