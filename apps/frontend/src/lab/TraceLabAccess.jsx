// TraceLabAccess — the one Trace Lab control on Hoistra's Users & access screen (approved attachment, 20 Oct 2026): in an
// opened user row, whether this person may sign in to Trace Lab and with which Trace Lab role. It reads and writes Trace
// Lab's own records only (labApi, /api/trace-lab/lab/members), never Hoistra's user table. Company admins always have
// access, so their rows say so instead of offering the choice. Inline styles, as the rest of that screen.
import React from 'react';
import { labApi } from './labApi.js';
import { ACCESS_CHOICES, accessValue, errText } from './logic/labelling.js';

const LABEL = { fontSize: '10.5px', letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--color-neutral-500)' };
const NOTE = { fontSize: '11.5px', color: 'var(--color-neutral-500)' };
const SELECT = { fontSize: '12.5px', padding: '6px 9px', borderRadius: '7px', border: '1px solid var(--color-divider)',
  background: 'var(--color-surface)', color: 'var(--color-text)', fontFamily: 'var(--font-body)' };

export function AccessView({ admin, member, loading, saving, error, onChange, inputId }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '6px' }} onClick={(e) => e.stopPropagation()}>
      <div style={LABEL}>{'Trace Lab access'}</div>
      {admin ? <div style={NOTE}>{'Company admins always have Trace Lab access.'}</div> : (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px' }}>
          <label htmlFor={inputId} style={{ position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{'Trace Lab access'}</label>
          <select id={inputId} style={SELECT} value={accessValue(member)} disabled={loading || saving} onChange={(e) => onChange(e.target.value)}>
            {ACCESS_CHOICES.map((c) => <option key={c.key} value={c.key}>{c.key ? 'Access as ' + c.text.toLowerCase() : c.text}</option>)}
          </select>
          <span style={NOTE}>{loading ? 'Reading…' : saving ? 'Saving…'
            : member && member.status === 'active' ? 'Can sign in to Trace Lab and label answers.' : 'Cannot sign in to Trace Lab.'}</span>
        </div>
      )}
      {error ? <div role="alert" style={{ fontSize: '11.5px', color: 'var(--st-risk)' }}>{error}</div> : null}
    </div>
  );
}

export default function TraceLabAccess({ user }) {
  const [member, setMember] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState('');
  const admin = !!user && (user.role === 'admin' || user.role === 'superadmin');

  React.useEffect(() => {
    if (!user || admin) { setLoading(false); return undefined; }
    let alive = true;
    labApi.member(user.id).then((out) => { if (alive) { setMember((out && out.member) || null); setLoading(false); } })
      .catch((e) => { if (alive) { setError('Trace Lab could not be reached: ' + errText(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [user && user.id, admin]);

  const onChange = async (value) => {
    const on = !!value;
    if (!on && (!member || member.status !== 'active')) return;
    setSaving(true); setError('');
    try {
      const out = await labApi.decide(user.id, { role: value || (member && member.role) || 'user', status: on ? 'active' : 'removed',
        base: (member && member.id) || null });
      setMember((out && out.member) || null);
    } catch (e) {
      setError(e && e.status === 409 ? 'Changed meanwhile in another window. Reopen this person to see the latest.' : errText(e));
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;
  return <AccessView admin={admin} member={member} loading={loading} saving={saving} error={error} onChange={onChange}
    inputId={'tl-access-' + user.id} />;
}
