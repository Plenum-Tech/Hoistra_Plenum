// Layout — the answer itself and its sections.
import React, { createContext, useContext, useState } from 'react';
import { inline } from '../../components/shell/markdownInline.jsx';
import { dataShown, soleChartTitled, startsOpen, withoutRepeats } from '../shared.jsx';
import { InBrief } from '../ask.jsx';

// The tables and charts the answer shows outside its closed sections.
const DataShown = createContext(null);

const isNode = (n, t) => !!n && typeof n === 'object' && n.typeName === t;

// The whole answer: its blocks top to bottom. The container is what the dock's narrow widths are
// measured against (container queries in openui.css), not the window.
// The answer's own lead is its brief (.oui-brief): the first block when it is a Headline or a
// Text, and the Text after a Headline with it (one paragraph split in two by the converter). Never
// more than that, and decided on the blocks' types alone, so a lead still streaming in never
// jumps out of the panel. The panel is drawn only when more follows (openui.css); a one-block
// answer stays plain text (28 Oct 2026).
export function Answer({ props, renderNode }) {
  const children = withoutRepeats(props.children);
  const list = Array.isArray(children) ? children : null;
  const lead = !list ? 0 : isNode(list[0], 'Headline') ? (isNode(list[1], 'Text') ? 2 : 1) : isNode(list[0], 'Text') ? 1 : 0;
  return (
    <DataShown.Provider value={dataShown(children)}>
      <div className="oui">
        {/* The brief is a summary: its bold-lead paragraphs are not finding cards to act on (InBrief). */}
        {lead ? <div className="oui-brief"><InBrief.Provider value={true}>{renderNode(list.slice(0, lead))}</InBrief.Provider></div> : null}
        {renderNode(lead ? list.slice(lead) : children)}
      </div>
    </DataShown.Provider>
  );
}

// A chapter's own number ("1. Risk to life — act first"): the answer's characters, set apart so
// it can hang in the margin. Never added where the answer wrote none.
const CHAPTER = /^(\d{1,2}[.)])\s+(?=\S)/;

// A titled group. Open, it reads as a chapter of a report: its title over its blocks, the
// answer's own number set in the margin (28 Oct 2026; it was a small caps label). Collapsed,
// it is one row (title and summary) the reader opens when they want the detail. It follows the
// model's `collapsed` until the reader clicks it, so a flag that streams in last still applies.
// A closed section holding the answer's only table or chart starts open: those figures are the
// answer, not detail to skip (27 Oct 2026, the vendors' SLA scores closed under "SLA delivery").
// So does one holding a caveat, and one whose table the only chart in view is drawn from
// (shared.jsx startsOpen).
export function Section({ props, renderNode }) {
  const [userOpen, setUserOpen] = useState(null);
  const shown = useContext(DataShown);
  const collapsible = props.collapsed === true;
  const children = withoutRepeats(props.children);
  if (!collapsible) {
    // A section holding only a chart of the same name: the chart's own title says it once.
    const titled = !props.summary && soleChartTitled(children, props.title);
    const t = String(props.title == null ? '' : props.title);
    const num = CHAPTER.exec(t);
    return (
      <section className="oui-section">
        {titled ? null : (
          <h3 className={num ? 'oui-section-title oui-chapter' : 'oui-section-title'}>
            {num ? [<span key="n" className="oui-chapter-n">{num[1]}</span>, ' ',
              <span key="t" className="oui-chapter-t">{inline(t.slice(num[0].length), 'st')}</span>]
              : inline(props.title, 'st')}
          </h3>
        )}
        {props.summary ? <div className="oui-section-summary">{inline(props.summary, 'ss')}</div> : null}
        <div className="oui-section-body">{renderNode(children)}</div>
      </section>
    );
  }
  const open = userOpen === null ? startsOpen(children, shown) : userOpen;
  return (
    <section className={'oui-disclosure' + (open ? ' is-open' : '')}>
      <button type="button" className="oui-disclosure-head" aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <i className="ph ph-caret-right oui-chev" aria-hidden="true"></i>
        <span className="oui-disclosure-text">
          <span className="oui-disclosure-title">{inline(props.title, 'dt')}</span>
          {props.summary ? <span className="oui-disclosure-summary">{inline(props.summary, 'ds')}</span> : null}
        </span>
      </button>
      {open ? <div className="oui-disclosure-body">{renderNode(children)}</div> : null}
    </section>
  );
}
