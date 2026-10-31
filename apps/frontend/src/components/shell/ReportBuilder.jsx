// ReportBuilder — "New report", as a panel on the right (7 Oct 2026). It used to be a form
// squeezed into the navigator that offered the newest five conversations by title alone, so
// four sessions all called "What needs my approval today?" could not be told apart and every
// older one could not be reported on at all. Now: every session, searchable and filterable,
// each with where and when it was asked; the question the report will re-ask, shown and
// editable; how often it refreshes; and its name. Opened from the navigator's "+" next to
// Reports, or from "Make a report" on a session's ⋯ menu with that session already chosen.
//
// Rendered into <body> through a portal, for the reason CorrectionDrawer.jsx gives: an
// animated ancestor would otherwise become the containing block for position:fixed.
import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const KICK = { fontSize: '10.5px', letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--color-neutral-500)' };
const FIELD = { width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: '12.5px', padding: '8px 10px', borderRadius: '7px', border: '1px solid var(--color-divider)', background: 'var(--color-surface)', color: 'var(--color-text)' };

export default function ReportBuilder({ vals }) {
  const show = !!vals.reportMenu;
  const searchRef = useRef(null);
  const closeRef = useRef(vals.cancelReport);
  closeRef.current = vals.cancelReport;
  useEffect(() => {
    if (!show) return undefined;
    const t = setTimeout(() => { if (searchRef.current) searchRef.current.focus({ preventScroll: true }); }, 0);
    const onKey = (e) => { if (e.key === 'Escape' && closeRef.current) closeRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => { clearTimeout(t); document.removeEventListener('keydown', onKey); };
  }, [show]);
  if (!show || typeof document === 'undefined') return null;

  const sources = vals.reportSources || [];
  return createPortal(
    <>
      <div className="rb-scrim" onClick={vals.cancelReport} aria-hidden="true"></div>
      <div role="dialog" aria-modal="true" aria-labelledby="rb-title" className="rb">
        <div className="rb-head">
          <div style={{ minWidth: '0' }}>
            <h3 id="rb-title" style={{ margin: '0', fontSize: '17px', fontWeight: 500 }}>{'New report'}</h3>
            <div style={{ fontSize: '12px', color: 'var(--color-neutral-500)', marginTop: '4px', lineHeight: '1.45' }}>
              {'A report re-asks one question on a schedule and keeps every answer. Build it from any session.'}
            </div>
          </div>
          <button type="button" className="rb-close" onClick={vals.cancelReport} aria-label="Close">
            <i className="ph ph-x" aria-hidden="true"></i>
          </button>
        </div>

        <div className="rb-body">
          <section>
            <div style={KICK}>{'1 · Pick a session'}</div>
            <div className="rb-search">
              <i className="ph ph-magnifying-glass" aria-hidden="true"></i>
              <input ref={searchRef} value={vals.reportQuery} onChange={vals.setReportQuery} placeholder="Search your sessions…" aria-label="Search sessions" />
            </div>
            <div className="rb-filters" role="tablist" aria-label="Which sessions">
              {(vals.reportFilters || []).map((f) => (
                <button key={f.key} type="button" role="tab" aria-selected={f.on} className={'rb-filter' + (f.on ? ' is-on' : '')} onClick={f.pick}>
                  {f.label}<span className="rb-filter-n">{f.count}</span>
                </button>
              ))}
            </div>
            <div className="rb-list" role="radiogroup" aria-label="Sessions">
              {vals.reportSourcesEmpty ? (
                <div className="rb-empty">{'No sessions yet — ask something from the home bar, then make a report of it.'}</div>
              ) : vals.reportNoMatch ? (
                <div className="rb-empty">{'No session matches that search.'}</div>
              ) : sources.map((r) => (
                <button key={r.id} type="button" role="radio" aria-checked={r.on} className={'rb-source' + (r.on ? ' is-on' : '')} onClick={r.pick}>
                  <span className="rb-radio" aria-hidden="true"></span>
                  <span className="rb-source-icon"><i className={`ph ${r.icon}`} aria-hidden="true"></i></span>
                  <span style={{ minWidth: '0', flex: '1' }}>
                    <span className="rb-source-title">{r.label}</span>
                    <span className="rb-source-meta">
                      {r.meta}
                      {r.hasReport ? <span className="rb-has">{'has a report'}</span> : null}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section className={vals.reportPicked ? '' : 'rb-muted'} aria-disabled={!vals.reportPicked}>
            <label htmlFor="rb-question" style={KICK}>{'2 · The question it will re-ask'}</label>
            <textarea id="rb-question" rows={3} value={vals.reportQuestion} onChange={vals.setReportQuestion} disabled={!vals.reportPicked}
              placeholder="Pick a session above — its question appears here"
              style={{ ...FIELD, marginTop: '7px', resize: 'vertical', lineHeight: '1.45' }} />
            {vals.reportPickedTask ? (
              <div className="rb-note">{'This is a task, so the report asks where it stands now. Reword it to ask exactly what you want to track.'}</div>
            ) : vals.reportPicked ? (
              <div className="rb-note">{'The question the conversation started with. Reword it if you want the report to ask it differently.'}</div>
            ) : null}
          </section>

          <section className={vals.reportPicked ? '' : 'rb-muted'}>
            <div style={KICK}>{'3 · How often it refreshes'}</div>
            <div className="rb-cadences" role="radiogroup" aria-label="Refresh">
              {(vals.reportCadences || []).map((c, i) => {
                const on = c.tick === 'ph-radio-button';
                return (
                  <button key={i} type="button" role="radio" aria-checked={on} className={'rb-cadence' + (on ? ' is-on' : '')} onClick={c.pick}>{c.label}</button>
                );
              })}
            </div>
            {vals.reportDaysShow === 'flex' ? (
              <div className="rb-days">
                <div style={{ display: 'flex', gap: '4px' }}>
                  {(vals.reportDays || []).map((d, i) => (
                    <button key={i} type="button" onClick={d.pick} title={d.title} aria-pressed={d.bg !== 'transparent'}
                      style={{ flex: '1', fontFamily: 'inherit', fontSize: '11.5px', padding: '6px 0', borderRadius: '6px', border: `1px solid ${d.edge}`, background: d.bg, color: d.fg, cursor: 'pointer' }}>
                      {d.label}
                    </button>
                  ))}
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--color-neutral-500)' }}>
                  {'at'}
                  <input value={vals.reportTime} onChange={vals.setReportTime} placeholder="14:00" maxLength="5" style={{ ...FIELD, width: '90px' }} />
                  {'24-hour'}
                </label>
              </div>
            ) : null}
          </section>

          <section className={vals.reportPicked ? '' : 'rb-muted'}>
            <label htmlFor="rb-name" style={KICK}>{'4 · Name'}</label>
            <input id="rb-name" value={vals.reportName} onChange={vals.setReportName} placeholder={vals.reportNamePh} disabled={!vals.reportPicked} style={{ ...FIELD, marginTop: '7px' }} />
          </section>
        </div>

        <div className="rb-foot">
          <div style={{ fontSize: '11px', color: 'var(--color-neutral-500)', lineHeight: '1.45', flex: '1', minWidth: '0' }}>
            {vals.reportCadenceNote}
          </div>
          <button type="button" className="rb-btn" onClick={vals.cancelReport}>{'Cancel'}</button>
          <button type="button" className="rb-btn is-primary" onClick={vals.createReport} disabled={!vals.reportCanCreate}>{'Create report'}</button>
        </div>
      </div>
    </>,
    document.body
  );
}
