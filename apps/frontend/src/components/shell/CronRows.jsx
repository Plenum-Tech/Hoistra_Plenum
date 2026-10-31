// CronRows — the Hoist Crons list on Home (logic/crons.js cronRows).
//
// Each check is its name (and building), how it is doing, and one plain sentence under it:
// what it found, when it ran, when it runs next - or why it is not running. The day-by-day
// strip is on the job's own page.
//
// The list takes whatever height the band row gives it (the neighbouring columns decide it), and
// fades at its foot only while there is more below - a half-shown row with no fade read as a
// rendering fault.
import React, { useEffect, useRef, useState } from 'react';

const TEXT = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

export default function CronRows({ rows }) {
  const ref = useRef(null);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const check = () => setMore(el.scrollHeight - el.clientHeight - el.scrollTop > 2);
    check();
    el.addEventListener('scroll', check, { passive: true });
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(check) : null;
    if (ro) ro.observe(el);
    return () => { el.removeEventListener('scroll', check); if (ro) ro.disconnect(); };
    // Re-checked when the rows' text changes too: a longer sentence can push a row below the fold.
  }, [rows.map((r) => r.id + r.line).join('|')]);

  const fade = more ? 'linear-gradient(to bottom, #000 calc(100% - 22px), transparent)' : 'none';
  return (
    <div ref={ref} style={{ flex: '1 1 0', minHeight: '132px', overflowY: 'auto', marginTop: '8px', marginRight: '-8px', paddingRight: '8px',
      display: 'flex', flexDirection: 'column', gap: '2px', maskImage: fade, WebkitMaskImage: fade }}>
      {rows.map((j) => {
        const Row = j.open ? 'button' : 'div';
        return (
          <Row key={j.id} type={j.open ? 'button' : undefined} className={j.open ? 'hv2' : undefined} onClick={j.open || undefined} title={j.title}
            style={{ display: 'grid', gridTemplateColumns: '6px minmax(0,1fr) auto', columnGap: '8px', alignItems: 'baseline', width: 'calc(100% + 8px)',
              boxSizing: 'border-box', margin: '0 0 0 -8px', padding: '6px 8px', borderRadius: '7px', border: 'none', background: 'transparent',
              textAlign: 'left', fontFamily: 'inherit', color: 'inherit', cursor: j.open ? 'pointer' : 'default' }}>
            <span aria-hidden="true" style={{ width: '6px', height: '6px', borderRadius: '50%', background: j.tone, alignSelf: 'center' }}></span>
            <span style={{ ...TEXT, fontSize: '11.5px', lineHeight: '1.3', color: 'var(--color-text)' }}>
              {j.name}
              {j.where ? <span style={{ color: 'var(--color-neutral-500)' }}>{' · ' + j.where}</span> : null}
            </span>
            <span style={{ fontSize: '9.5px', lineHeight: '1.3', color: j.tone, whiteSpace: 'nowrap', justifySelf: 'end' }}>{j.health}</span>
            {/* The one line that says it all (logic/crons.js oneLiner): what it found, when it ran,
                when it runs next - wrapped onto a second line rather than cut off. */}
            <span style={{ gridColumn: '2 / 4', fontSize: '10.5px', lineHeight: '1.4', marginTop: '2px', color: j.failed ? 'var(--st-risk)' : 'var(--color-neutral-400)',
              display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
              {j.line}
            </span>
          </Row>
        );
      })}
    </div>
  );
}
