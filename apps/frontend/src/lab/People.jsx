// People — the company admin's list of everyone whose Trace Lab access was ever turned on (in Hoistra, under Users &
// access), with their Trace Lab role (domain expert, tester, admin, user) and status. Nobody is added by signing in: a
// person without access sees "You do not have access". Each change is kept as a new version with who made it.
// The platform team also sees platform-wide access (22 Oct 2026): consultants, who see every company's runs, annotate
// and label but cannot manage people, given or taken away by their Hoistra email.
import React from 'react';
import { labApi } from './labApi.js';
import { ROLE_CHOICES, STATUS_CHOICES, errText, isStale, memberRow } from './logic/labelling.js';
import './labels.css';

export function PeopleTable({ rows, edits, busy, errors, onEdit, onSave }) {
  if (!rows.length) return <p className="lab-small">{'Nobody has Trace Lab access yet. Turn it on for people in Hoistra, under Users & access.'}</p>;
  return (
    <div className="lab-table-wrap">
      <table className="lab-table">
        <thead><tr>{['Person', 'Trace Lab role', 'Status', 'Since', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((m) => {
            const e = edits[m.userId] || {};
            const role = e.role || m.role;
            const status = e.status || m.status;
            const changed = role !== m.role || status !== m.status;
            return (
              <tr key={m.userId} className={m.status === 'pending' ? 'lab-row-pending' : ''}>
                <td>{m.email}</td>
                <td>
                  <label className="lab-visually-hidden" htmlFor={'role-' + m.userId}>{'Trace Lab role of ' + m.email}</label>
                  <select id={'role-' + m.userId} value={role} onChange={(ev) => onEdit(m.userId, { role: ev.target.value })} disabled={!!busy[m.userId]}>
                    {ROLE_CHOICES.map((r) => <option key={r.key} value={r.key}>{r.text}</option>)}
                  </select>
                </td>
                <td>
                  <label className="lab-visually-hidden" htmlFor={'status-' + m.userId}>{'Status of ' + m.email}</label>
                  <select id={'status-' + m.userId} value={status} onChange={(ev) => onEdit(m.userId, { status: ev.target.value })} disabled={!!busy[m.userId]}>
                    {STATUS_CHOICES.map((s) => <option key={s.key} value={s.key}>{s.text}</option>)}
                  </select>
                </td>
                <td className="lab-small">{m.since + (m.by ? ' · by ' + m.by : '')}</td>
                <td>
                  <button type="button" className="lab-btn lab-btn-sm" onClick={() => onSave(m)} disabled={!changed || !!busy[m.userId]}>
                    {busy[m.userId] ? 'Saving…' : 'Save'}
                  </button>
                  {errors[m.userId] ? <div role="alert" className="lab-error">{errors[m.userId]}</div> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Platform-wide access, for the platform team only: the consultants (status, since, by whom), Remove (asked once), and
// a form that gives a Hoistra account the consultant role. onGive resolves true when access was given.
export function PlatformAccess({ rows, error, busy, giving, notice, onGive, onRemove }) {
  const [email, setEmail] = React.useState('');
  const [confirming, setConfirming] = React.useState(null);
  const give = async (e) => {
    e.preventDefault();
    const v = email.trim();
    if (!v || giving) return;
    if (await onGive(v)) setEmail('');
  };
  const list = rows || null;
  return (
    <section className="lab-lv-section" aria-labelledby="lab-lv-platform">
      <h2 id="lab-lv-platform">{'Platform-wide access'}</h2>
      <p className="lab-small lab-big">{'A consultant sees every company’s runs, can annotate and label, but cannot manage people. Create their Hoistra account first.'}</p>
      {notice ? <p className="lab-small" role="status">{notice}</p> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {list === null && !error ? <p className="lab-small">{'Reading…'}</p> : null}
      {list && !list.length ? <p className="lab-small">{'No consultants yet.'}</p> : null}
      {list && list.length ? (
        <div className="lab-table-wrap">
          <table className="lab-table">
            <thead><tr>{['Person', 'Role', 'Status', 'Since', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
            <tbody>
              {list.map((m) => {
                const r = memberRow(m);
                const active = m.status === 'active';
                const working = !!(busy && busy[m.user_id]);
                return (
                  <tr key={m.user_id || m.id}>
                    <td>{r.email}</td>
                    <td>{'Consultant'}</td>
                    <td><span className={'lab-tag' + (active ? ' lab-lv-tag-ok' : '')}>{active ? 'Active' : 'Removed'}</span></td>
                    <td className="lab-small">{r.since + (r.by ? ' · by ' + r.by : '')}</td>
                    <td className="lab-nowrap">
                      {active && confirming === m.user_id ? (
                        <span className="lab-confirm" role="group" aria-label={'Remove ' + r.email + '?'}>
                          <span className="lab-small">{'Remove access?'}</span>
                          <button type="button" className="lab-link lab-danger" disabled={working} onClick={() => { setConfirming(null); onRemove(m); }}>{'Yes'}</button>
                          <button type="button" className="lab-link" disabled={working} onClick={() => setConfirming(null)}>{'No'}</button>
                        </span>
                      ) : null}
                      {active && confirming !== m.user_id ? (
                        <button type="button" className="lab-link lab-danger" disabled={working || !m.email} onClick={() => setConfirming(m.user_id)}>
                          {working ? 'Removing…' : 'Remove'}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <form className="lab-lv-give" onSubmit={give} aria-labelledby="lab-lv-give-title">
        <h3 id="lab-lv-give-title">{'Give consultant access'}</h3>
        <div className="lab-lv-give-row">
          <label className="lab-lv-field lab-lv-grow"><span>{'Hoistra email'}</span>
            <input type="email" className="lab-input" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com"
              autoComplete="off" maxLength={320} />
          </label>
          <button type="submit" className="lab-btn lab-btn-sm" disabled={!!giving || !email.trim()}>{giving ? 'Giving access…' : 'Give access'}</button>
        </div>
      </form>
    </section>
  );
}

function PlatformSection() {
  const [rows, setRows] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState({});
  const [giving, setGiving] = React.useState(false);
  const [notice, setNotice] = React.useState('');

  const load = async () => {
    try {
      const out = await labApi.platformMembers();
      setRows((out && out.members) || []);
    } catch (e) {
      setError(errText(e));
    }
  };
  React.useEffect(() => { load(); }, []);

  const onGive = async (email) => {
    setGiving(true); setError(''); setNotice('');
    try {
      const out = await labApi.decidePlatformMember({ email, status: 'active' });
      setNotice(((out && out.member && out.member.email) || email) + ' now has consultant access.');
      await load();
      return true;
    } catch (e) {
      setError(errText(e));
      return false;
    } finally {
      setGiving(false);
    }
  };
  const onRemove = async (m) => {
    setBusy((p) => Object.assign({}, p, { [m.user_id]: true }));
    setError(''); setNotice('');
    try {
      await labApi.decidePlatformMember({ email: m.email, status: 'removed' });
      setNotice(m.email + ' no longer has platform-wide access.');
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy((p) => { const n = Object.assign({}, p); delete n[m.user_id]; return n; });
    }
  };
  return <PlatformAccess rows={rows} error={error} busy={busy} giving={giving} notice={notice} onGive={onGive} onRemove={onRemove} />;
}

export default function People({ me } = {}) {
  const platform = !!me && me.role === 'platform';
  const [rows, setRows] = React.useState(null);
  const [error, setError] = React.useState('');
  const [edits, setEdits] = React.useState({});
  const [busy, setBusy] = React.useState({});
  const [errors, setErrors] = React.useState({});

  const load = async () => {
    setError('');
    try {
      const out = await labApi.members();
      setRows(((out && out.members) || []).map(memberRow));
    } catch (e) {
      setError(errText(e));
    }
  };
  React.useEffect(() => { load(); }, []);

  const onEdit = (uid, patch) => setEdits((p) => Object.assign({}, p, { [uid]: Object.assign({}, p[uid], patch) }));
  const onSave = async (m) => {
    const e = edits[m.userId] || {};
    setBusy((p) => Object.assign({}, p, { [m.userId]: true }));
    setErrors((p) => Object.assign({}, p, { [m.userId]: '' }));
    try {
      const out = await labApi.decide(m.userId, { role: e.role || m.role, status: e.status || m.status, base: m.id });
      const row = memberRow(out.member);
      setRows((p) => p.map((x) => (x.userId === m.userId ? row : x)));
      setEdits((p) => { const n = Object.assign({}, p); delete n[m.userId]; return n; });
    } catch (err) {
      setErrors((p) => Object.assign({}, p, { [m.userId]: isStale(err) ? 'Someone else changed this person meanwhile. Reloaded.' : errText(err) }));
      if (isStale(err)) load();
    } finally {
      setBusy((p) => Object.assign({}, p, { [m.userId]: false }));
    }
  };

  const pending = (rows || []).filter((m) => m.status === 'pending').length;
  return (
    <div className="lab-page-body">
      <div className="lab-page-head">
        <h1>{'People'}</h1>
        <p className="lab-small lab-big">{'Turn Trace Lab on for a person in Hoistra, under Users & access: pick their Trace Lab role there. Everyone who has access is listed here, and you can change their role or turn their access off.'}</p>
      </div>
      {pending ? <div className="lab-next" role="status"><span className="lab-next-text">{pending + (pending === 1 ? ' person is' : ' people are') + ' waiting for access.'}</span></div> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {rows === null && !error ? <p className="lab-small">{'Reading…'}</p> : null}
      {rows ? <PeopleTable rows={rows} edits={edits} busy={busy} errors={errors} onEdit={onEdit} onSave={onSave} /> : null}
      {platform ? <PlatformSection /> : null}
    </div>
  );
}
