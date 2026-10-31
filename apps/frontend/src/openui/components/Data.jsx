// Data — headline figures and tables.
import React, { memo, useState } from 'react';
import { inline } from '../../components/shell/markdownInline.jsx';
import { scrubInternal } from '../../logic/publicText.js';
import { arr, cell, CODE, fmt, refs, sameData, toneOf } from '../shared.jsx';
import { aboutQuestion, AskArrow, barQuestion, PlainCodes, rowClick, useAsk } from '../ask.jsx';

export function Metrics({ props, renderNode }) {
  return <div className="oui-metrics">{renderNode(props.items)}</div>;
}

// A figure on the dashboard's tile: value in tabular mono, coloured by tone only when the tone
// means something (risk, warn, ok); the stripe on its left carries the same tone. The figure is
// sized to its tile by its longest unbroken run (--oui-run, openui.css), so "£12,480,300" shrinks
// to fit rather than breaking mid-number in the dock (27 Oct 2026).
//
// A tile asks about its figure (28 Oct 2026, "more clickable option"): a click anywhere on it asks
// about the label and the figure as shown, as a bar does, and the arrow in its corner is the same
// question for a keyboard. The tile stays a box, not a button: it holds blocks of its own. The
// arrow shows when the tile is pointed at or focused, and always on a touch screen (openui.css).
export const Metric = memo(function Metric({ props }) {
  const tone = toneOf(props.value, props.tone, props.label);
  const text = scrubInternal(fmt(props.value));
  // A short figure with its unit ("241.0 kWh/m²") is sized as one run, so it stays on one line.
  const run = text.length <= 16 ? Math.max(1, text.length) : Math.max(1, ...text.split(/\s+/).map((t) => t.length));
  const { ready, streaming, ask } = useAsk();
  const q = ready ? barQuestion(props.label, text) : '';
  return (
    <div className={'oui-metric oui-tone-' + tone} onClick={q ? rowClick(() => { if (!streaming) ask(q); }) : undefined}>
      <div className="oui-metric-value" style={{ '--oui-run': run }}>{text}</div>
      <div className="oui-metric-label">{inline(props.label, 'ml')}</div>
      {props.note ? <div className="oui-metric-note">{inline(props.note, 'mn')}</div> : null}
      {q ? <AskArrow question={q} className="oui-metric-go" /> : null}
    </div>
  );
}, sameData);

const LIMIT = 8;
// A figure with its unit: 9, "£1,200", "91%", "6 days", "241.0 kWh/m²".
const NUMERIC = /^[-+]?[£$€]?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|pts?|days?|d|hrs?|h|mins?|kWh(?:\/m²)?|kW|MWh|m²|°C)?$/i;
const EMPTY = (v) => v === null || v === undefined || v === '' || v === '—' || v === '-';

// A cell read without the bold the answer put round it: a column of "**£114,716**" is still a
// column of figures, so it right-aligns (28 Oct 2026). The cell is still drawn bold.
const bare = (v) => String(v).trim().replace(/^(\*\*|__)(.+)\1$/, '$2');

// What each column holds, read off its cells: numbers right-align, record codes are drawn as tags.
function columnKinds(columns, rows) {
  return columns.map((_, j) => {
    const vals = rows.map((r) => r[j]).filter((v) => !EMPTY(v));
    if (!vals.length) return 'text';
    if (vals.every((v) => typeof v === 'number' || NUMERIC.test(bare(v)))) return 'num';
    if (vals.every((v) => CODE.test(bare(v)))) return 'code';
    return 'text';
  });
}
const KIND_CLASS = { num: 'oui-num', code: 'oui-code', text: undefined };

// Records. Eight rows show; the rest are a click away rather than a scroll through the chat.
// Wider than its box (the dock), it scrolls inside the box and never widens the page.
// A row longer than the headings keeps its extra cells under blank headings, and a table with no
// headings at all is drawn without a heading row: no cell is ever dropped (27 Oct 2026).
export const Table = memo(function Table({ props }) {
  const [all, setAll] = useState(false);
  const heads = arr(props.columns);
  const rows = arr(props.rows).filter(Array.isArray);
  const width = rows.reduce((w, r) => Math.max(w, r.length), heads.length);
  const columns = Array.from({ length: width }, (_, j) => String(heads[j] == null ? '' : heads[j]));
  const headed = columns.some((c) => c.trim());
  const kinds = columnKinds(columns, rows);
  const shown = all ? rows : rows.slice(0, LIMIT);
  const more = rows.length > LIMIT;
  const { ready, streaming, ask } = useAsk();
  // A row is about the item in its first column (a vendor, an asset, a work order), so a click on
  // it asks about that item; its arrow, in a last column of its own that stays in view while the
  // table scrolls, is the same question for a keyboard (28 Oct 2026). Not when the first column
  // is figures: "Tell me more about 230" asks nothing. The item's own code is part of its name
  // there, so it is drawn plain (PlainCodes), and a code elsewhere on the row keeps its own ask.
  const askable = ready && kinds[0] !== 'num' && rows.some((r) => !EMPTY(r[0]));
  const questionOf = (r) => (EMPTY(r[0]) ? '' : aboutQuestion(typeof r[0] === 'number' ? fmt(r[0]) : r[0]));
  if (!width) return null;
  return (
    <div className="oui-table">
      <div className="oui-scroll">
        <table>
          {headed ? (
            <thead>
              <tr>
                {columns.map((c, j) => <th key={j} className={kinds[j] === 'num' ? 'oui-num' : undefined}>{inline(c, 'th' + j)}</th>)}
                {askable ? <td className="oui-go-col" aria-hidden="true"></td> : null}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {shown.map((r, i) => {
              const q = askable ? questionOf(r) : '';
              return (
                <tr key={i} className="oui-row" onClick={q ? rowClick(() => { if (!streaming) ask(q); }) : undefined}>
                  {columns.map((_, j) => (
                    <td key={j} className={KIND_CLASS[kinds[j]]}>
                      {kinds[j] === 'num' ? cell(r[j], 'c' + i + '-' + j)
                        : j === 0 && askable ? <PlainCodes.Provider value={true}>{refs(cell(r[j], 'c' + i + '-' + j), 'r' + i + '-' + j + '-')}</PlainCodes.Provider>
                          : refs(cell(r[j], 'c' + i + '-' + j), 'r' + i + '-' + j + '-')}
                    </td>
                  ))}
                  {askable ? <td className="oui-go-col">{q ? <AskArrow question={q} /> : null}</td> : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {more || props.caption ? (
        <div className="oui-table-foot">
          <span>{props.caption ? inline(props.caption, 'cap') : null}</span>
          {more ? (
            <button type="button" className="oui-link" onClick={() => setAll(!all)}>
              {all ? 'Show fewer' : 'Show all ' + rows.length + ' rows'}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}, sameData);
