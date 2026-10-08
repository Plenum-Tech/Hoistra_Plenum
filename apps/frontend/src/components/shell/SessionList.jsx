// SessionList — sessions grouped by day, as the Sessions page and a space page list them.
//
// A row is one conversation with the orchestrator, or one orchestrator task: its first question,
// the engine that answered, the page it was asked from, how many questions it holds, and the
// space it is in. Clicking reopens it. Any row can be filed in a space (7 Oct 2026): drag it —
// or the rows ticked — onto a space (the Spaces panel beside the list, or a space in the
// navigator), or use its ⋯ menu; the bar that appears over a selection does the same for many.
// `groups` and the handlers come from the view model; selection, the open menu and the drag
// are this component's own, because they mean nothing outside the screen.
import React, { useEffect, useRef, useState } from 'react';

// The drag carries session ids under its own type, so a drop target can tell a session from a
// file or a piece of text dragged in from elsewhere.
export const SESSION_MIME = 'application/x-hoistra-sessions';
export function dragHasSessions(e) {
  const types = e && e.dataTransfer && e.dataTransfer.types;
  return !!types && Array.prototype.indexOf.call(types, SESSION_MIME) > -1;
}
export function readDroppedSessions(e) {
  try {
    const ids = JSON.parse(e.dataTransfer.getData(SESSION_MIME) || '[]');
    return Array.isArray(ids) ? ids.filter((x) => typeof x === 'string' && x) : [];
  } catch (err) { return []; }
}
// A small "3 sessions" chip under the pointer instead of a ghost of one row, so a multi-row drag
// says what is being moved.
export function startSessionDrag(e, ids, label) {
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData(SESSION_MIME, JSON.stringify(ids));
  e.dataTransfer.setData('text/plain', label || (ids.length + ' sessions'));
  if (typeof document === 'undefined' || !e.dataTransfer.setDragImage) return;
  const chip = document.createElement('div');
  chip.className = 'ss-drag-chip';
  chip.textContent = ids.length === 1 ? (label || '1 session') : ids.length + ' sessions';
  document.body.appendChild(chip);
  e.dataTransfer.setDragImage(chip, 14, 14);
  setTimeout(() => chip.remove(), 0);
}

const BARE = { font: 'inherit', background: 'transparent', border: 'none', padding: '0', margin: '0', cursor: 'pointer', color: 'inherit' };

export default function SessionList({ groups, empty, emptyText, spaces, onFile, onDelete }) {
  const [sel, setSel] = useState(() => new Set());
  const [menu, setMenu] = useState(null);
  const [dragging, setDragging] = useState(null);
  // Deleting several at once takes two presses, like a report's delete: one click could hide a
  // thousand sessions on the server and nothing brings them back. Disarms after a few seconds.
  const [armed, setArmed] = useState(false);
  const armTimer = useRef(null);
  useEffect(() => () => clearTimeout(armTimer.current), []);
  const menuRef = useRef(null);
  const canFile = typeof onFile === 'function' && (spaces || []).length > 0;
  const allIds = (groups || []).reduce((a, g) => a.concat(g.rows.map((r) => r.id)), []);
  // A ticked row that is no longer listed (searched away, deleted) is not selected any more.
  const picked = allIds.filter((id) => sel.has(id));

  useEffect(() => {
    if (!menu) return undefined;
    const close = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(null); };
    const esc = (e) => { if (e.key === 'Escape') setMenu(null); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [menu]);

  if (empty) {
    return (
      <div style={{ marginTop: '18px', padding: '28px', borderRadius: '12px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-sm)', fontSize: '13px', lineHeight: '1.55', color: 'var(--color-neutral-400)', maxWidth: '62ch' }}>
        {emptyText}
      </div>
    );
  }

  const toggle = (id) => setSel((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const clear = () => { setSel(new Set()); setArmed(false); clearTimeout(armTimer.current); };
  const file = (ids, key) => { if (canFile) onFile(ids, key); setMenu(null); clear(); };
  const remove = (ids) => { if (onDelete) onDelete(ids); setMenu(null); clear(); };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '18px', marginTop: '14px' }}>
      {picked.length ? (
        <div className="ss-bulk" role="toolbar" aria-label="Selected sessions">
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', cursor: 'pointer' }}>
            <input type="checkbox" className="ss-check" checked={picked.length === allIds.length}
              onChange={() => (picked.length === allIds.length ? clear() : setSel(new Set(allIds)))} aria-label="Select every session shown" />
            <span style={{ fontWeight: 500 }}>{picked.length + ' selected'}</span>
          </label>
          {canFile ? (
            <select className="ss-select" value="" onChange={(e) => file(picked, e.target.value === '__none' ? null : e.target.value)} aria-label="Add the selected sessions to a space">
              <option value="" disabled>{'Add to space…'}</option>
              {spaces.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
              <option value="__none">{'Remove from its space'}</option>
            </select>
          ) : null}
          {onDelete ? (
            <button type="button" className="ss-bulk-btn is-risk"
              onClick={() => {
                if (picked.length > 1 && !armed) {
                  setArmed(true);
                  clearTimeout(armTimer.current);
                  armTimer.current = setTimeout(() => setArmed(false), 4000);
                  return;
                }
                remove(picked);
              }}>
              <i className="ph ph-trash" aria-hidden="true"></i>{armed ? 'Delete ' + picked.length + ' — press again' : 'Delete'}
            </button>
          ) : null}
          <span className="ss-bulk-hint">{canFile ? 'or drag them onto a space' : ''}</span>
          <button type="button" className="ss-bulk-btn" onClick={clear} style={{ marginLeft: 'auto' }}>{'Clear'}</button>
        </div>
      ) : null}

      {(groups || []).map((g) => (
        <div key={g.day}>
          <div style={{ fontSize: '10.5px', letterSpacing: '0.11em', textTransform: 'uppercase', color: 'var(--color-neutral-500)', padding: '0 2px 8px' }}>
            {g.day}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {g.rows.map((r) => {
              const on = sel.has(r.id);
              const dragIds = on ? picked : [r.id];
              return (
                <div key={r.id} className={'ss-row' + (on ? ' is-picked' : '') + (r.active ? ' is-active' : '') + (dragging && dragging.indexOf(r.id) > -1 ? ' is-dragging' : '')}
                  draggable={canFile}
                  onDragStart={(e) => { startSessionDrag(e, dragIds, r.title); setDragging(dragIds); }}
                  onDragEnd={() => setDragging(null)}
                  onClick={r.open} title={canFile ? 'Open — or drag onto a space to add it there' : undefined}>
                  {canFile ? <i className="ph ph-dots-six-vertical ss-grip" aria-hidden="true"></i> : <span></span>}
                  <input type="checkbox" className="ss-check" checked={on} onClick={(e) => e.stopPropagation()} onChange={() => toggle(r.id)} aria-label={'Select ' + r.title} />
                  <div className="ss-icon"><i className={`ph ${r.icon}`}></i></div>
                  <div style={{ minWidth: '0' }}>
                    <div style={{ fontSize: '13px', lineHeight: '1.4', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.title}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '3px', minWidth: '0' }}>
                      <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {[r.domain, r.page ? 'from ' + r.page : '', r.turns].filter(Boolean).join(' · ')}
                      </span>
                      {r.spaceName ? (
                        <span className="ss-space-chip"><i className="ph ph-folder-simple" aria-hidden="true"></i>{r.spaceName}</span>
                      ) : null}
                      {/* A support request: its reference and where it stands (logic/support.js). */}
                      {r.ref ? <span className="ss-ref">{r.ref}</span> : null}
                      {r.status ? (
                        <span className={'ss-status is-' + r.status}>
                          <i className={`ph ${r.status === 'resolved' ? 'ph-check' : 'ph-circle'}`} aria-hidden="true"></i>
                          {r.status === 'resolved' ? 'Resolved' : 'Open'}
                        </span>
                      ) : null}
                      {r.emailed ? (
                        <span className="ss-space-chip"><i className="ph ph-envelope-simple" aria-hidden="true"></i>{'Emailed to Plenum'}</span>
                      ) : null}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: '0', position: 'relative' }}>
                    <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>{r.when}</span>
                    <button type="button" className="ss-more" aria-haspopup="menu" aria-expanded={menu === r.id} aria-label={'Options for ' + r.title}
                      onClick={(e) => { e.stopPropagation(); setMenu(menu === r.id ? null : r.id); }}>
                      <i className="ph ph-dots-three" aria-hidden="true"></i>
                    </button>
                    {menu === r.id ? (
                      <div ref={menuRef} className="ss-menu" role="menu" onClick={(e) => e.stopPropagation()}>
                        <button type="button" role="menuitem" className="ss-menu-item" onClick={() => { setMenu(null); r.open(); }}>
                          <i className="ph ph-arrow-square-out" aria-hidden="true"></i>{'Open'}
                        </button>
                        {r.makeReport ? (
                          <button type="button" role="menuitem" className="ss-menu-item" onClick={() => { setMenu(null); r.makeReport(); }}>
                            <i className="ph ph-chart-bar" aria-hidden="true"></i>{'Make a report'}
                          </button>
                        ) : null}
                        {canFile ? (
                          <>
                            <div className="ss-menu-head">{'Add to space'}</div>
                            {spaces.map((t) => (
                              <button key={t.key} type="button" role="menuitemradio" aria-checked={r.spaceKey === t.key} className="ss-menu-item" onClick={() => file([r.id], t.key)}>
                                <i className={`ph ${t.icon}`} aria-hidden="true"></i>
                                <span style={{ flex: '1' }}>{t.name}</span>
                                {r.spaceKey === t.key ? <i className="ph ph-check" aria-hidden="true" style={{ color: 'var(--color-accent)' }}></i> : null}
                              </button>
                            ))}
                            {r.spaceKey ? (
                              <button type="button" role="menuitem" className="ss-menu-item" onClick={() => file([r.id], null)}>
                                <i className="ph ph-folder-minus" aria-hidden="true"></i>{'Remove from ' + (r.spaceName || 'its space')}
                              </button>
                            ) : null}
                          </>
                        ) : null}
                        {onDelete ? (
                          <>
                            <div className="ss-menu-rule"></div>
                            <button type="button" role="menuitem" className="ss-menu-item is-risk" onClick={() => remove([r.id])}>
                              <i className="ph ph-trash" aria-hidden="true"></i>{'Delete'}
                            </button>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// The spaces a session can be dropped on — beside the list on the Sessions page. Each is a
// target (drag sessions onto it) and a filter (pick it to list only what is in it).
export function SpaceTargets({ spaces, unfiledCount, onFile, newSpace }) {
  const [over, setOver] = useState(undefined);
  const drop = (key) => (e) => {
    if (!dragHasSessions(e)) return;
    e.preventDefault();
    setOver(undefined);
    const ids = readDroppedSessions(e);
    if (ids.length && onFile) onFile(ids, key);
  };
  const hover = (key) => (e) => { if (!dragHasSessions(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; if (over !== key) setOver(key); };
  return (
    <aside className="ss-spaces" aria-label="Spaces">
      <div className="ss-spaces-head">{'Spaces'}</div>
      <div className="ss-spaces-hint">{'Drag sessions onto a space to add them. Pick one to list what is in it.'}</div>
      <div className="ss-targets">
        {(spaces || []).map((t) => (
          <button key={String(t.key)} type="button" aria-pressed={t.on}
            className={'ss-target' + (t.on ? ' is-on' : '') + (!t.all && !t.noDrop && over === t.key ? ' is-over' : '')}
            onClick={t.pick}
            onDragOver={t.all || t.noDrop ? undefined : hover(t.key)}
            onDragLeave={t.all || t.noDrop ? undefined : () => setOver(undefined)}
            onDrop={t.all || t.noDrop ? undefined : drop(t.key)}>
            <i className={`ph ${t.icon}`} aria-hidden="true"></i>
            <span className="ss-target-name">{t.name}</span>
            <span className="ss-target-n">{t.count}</span>
          </button>
        ))}
        <div className={'ss-target is-unfile' + (over === null ? ' is-over' : '')}
          onDragOver={hover(null)} onDragLeave={() => setOver(undefined)} onDrop={drop(null)}>
          <i className="ph ph-folder-minus" aria-hidden="true"></i>
          <span className="ss-target-name">{'No space'}</span>
          <span className="ss-target-n">{unfiledCount}</span>
        </div>
      </div>
      {newSpace ? (
        newSpace.open ? (
          <div className="ss-new">
            <input className="ss-new-input" autoFocus value={newSpace.name} onChange={newSpace.set} onKeyDown={newSpace.key} placeholder="Name the space" aria-label="New space name" />
            <div style={{ display: 'flex', gap: '6px' }}>
              <button type="button" className="ss-bulk-btn is-primary" disabled={!newSpace.canCreate || !String(newSpace.name || '').trim()} onClick={newSpace.create}>{newSpace.busy ? 'Saving…' : 'Create'}</button>
              <button type="button" className="ss-bulk-btn" onClick={newSpace.cancel}>{'Cancel'}</button>
            </div>
          </div>
        ) : (
          <button type="button" className="ss-add" onClick={newSpace.toggle} disabled={!newSpace.canCreate} title={newSpace.note || 'Create a saved space'}>
            <i className="ph ph-plus" aria-hidden="true"></i>{'New space'}
          </button>
        )
      ) : null}
      {newSpace && newSpace.note ? <div className="ss-spaces-hint">{newSpace.note}</div> : null}
    </aside>
  );
}
