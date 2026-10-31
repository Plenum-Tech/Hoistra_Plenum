// Content — the answer's words: the headline, prose, a called-out finding, one record's fields.
import React, { memo, useContext, useMemo } from 'react';
import { MarkdownBlocks } from '../../components/shell/Markdown.jsx';
import { inline } from '../../components/shell/markdownInline.jsx';
import { parseBlocks } from '../../logic/markdownBlocks.js';
import { arr, cell, childProps, CODE, marked, refs, sameData } from '../shared.jsx';
import { AskArrow, findingQuestion, InBrief, rowClick, useAsk } from '../ask.jsx';

export const Headline = memo(function Headline({ props }) {
  return <div className="oui-headline">{marked(props.text, 'hl')}</div>;
}, sameData);

// A paragraph that opens on bold is one finding ("**Fire Risk Assessment — Lapsed**, expired…"):
// the lead is what it is about.
const LEAD = /^\s*(\*\*|__)([\s\S]+?)\1/;
const isLead = (b) => !!b && b.t === 'p' && LEAD.test(b.body);

// Prose, through the same Markdown renderer as every other answer (lists, links, document
// links, the internal-name scrub). Record codes in it are drawn as tags (shared.jsx refs).
//
// Each finding paragraph is an issue the reader can act on (28 Oct 2026): a click on its card asks
// what to do about its lead, and its arrow is the same question for a keyboard. The arrow comes
// after the words: in the card's corner on the page, at the end of the text in the dock, where a
// corner would take a column from every line of a 190px answer. Those paragraphs are drawn here exactly as
// MarkdownBlocks draws a paragraph (as Finding.jsx does), and the blocks between them by
// MarkdownBlocks itself, with the same 7px between every block. The brief's paragraphs are a
// summary, not a list of issues, and stay as they were (InBrief), and so does Text outside an
// answer.
export const Text = memo(function Text({ props }) {
  const md = String(props.markdown == null ? '' : props.markdown);
  const blocks = useMemo(() => parseBlocks(md), [md]);
  const brief = useContext(InBrief);
  const { ready, streaming, ask } = useAsk();
  if (brief || !ready || !blocks.some(isLead)) return <div className="oui-text"><MarkdownBlocks blocks={blocks} mark={refs} /></div>;
  const parts = [];
  let run = [];
  const flush = (k) => {
    if (run.length) parts.push(<MarkdownBlocks key={'b' + k} blocks={run} mark={refs} />);
    run = [];
  };
  blocks.forEach((b, k) => {
    if (!isLead(b)) { run.push(b); return; }
    flush(k);
    const q = findingQuestion(LEAD.exec(b.body)[2], '');
    parts.push(
      <div key={'p' + k} className="md-p" data-lead="" style={{ lineHeight: '1.5', whiteSpace: 'pre-wrap' }}
        onClick={q ? rowClick(() => { if (!streaming) ask(q); }) : undefined}>
        {refs(inline(b.body, 'p' + k))}
        {q ? <AskArrow question={q} className="oui-card-go" /> : null}
      </div>
    );
  });
  flush(blocks.length);
  return <div className="oui-text"><div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>{parts}</div></div>;
}, sameData);

const CALLOUT_ICON = { risk: 'ph-warning-circle', warn: 'ph-warning', ok: 'ph-check-circle', info: 'ph-info' };

export const Callout = memo(function Callout({ props }) {
  const tone = CALLOUT_ICON[props.tone] ? props.tone : 'info';
  return (
    <div className={'oui-callout oui-tone-' + tone} role="note">
      <i className={'ph ' + CALLOUT_ICON[tone]} aria-hidden="true"></i>
      <div className="oui-callout-text">
        {props.title ? <div className="oui-callout-title">{inline(props.title, 'ct')}</div> : null}
        <div>{marked(props.text, 'cx')}</div>
      </div>
    </div>
  );
}, sameData);

// One record: its title, then label / value pairs. Fields are drawn here rather than one by one,
// so the labels line up in a single column.
export const Details = memo(function Details({ props }) {
  const fields = arr(props.fields).map(childProps).filter(Boolean);
  return (
    <div className="oui-card">
      <div className="oui-details-head">
        <span className="oui-card-title">{inline(props.title, 'dt')}</span>
        {props.subtitle ? <span className="oui-card-sub">{inline(props.subtitle, 'dsub')}</span> : null}
      </div>
      {fields.length ? (
        <div className="oui-fields">
          {fields.map((f, i) => (
            <React.Fragment key={i}>
              <span className="oui-field-key">{inline(f.label, 'fk' + i)}</span>
              <span className={CODE.test(String(f.value)) ? 'oui-field-value oui-code' : 'oui-field-value'}>{refs(cell(f.value, 'fv' + i), 'fr' + i)}</span>
            </React.Fragment>
          ))}
        </div>
      ) : null}
    </div>
  );
}, sameData);

// Drawn by Details.
export function Field() { return null; }
