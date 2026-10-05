// cardStyles — the dashboard's visual language, shared by the compliance dashboard
// (ComplianceAnswer.jsx) and the card layout every other answer gets (AnswerCards.jsx), so the
// two read as one system.
export const SEV = {
  critical: { fg: 'var(--st-risk)', bg: 'var(--st-risk-bg)' },
  warning: { fg: 'var(--st-warn)', bg: 'var(--st-warn-bg)' },
  info: { fg: 'var(--color-accent)', bg: 'var(--color-accent-900)' },
  ok: { fg: 'var(--st-ok)', bg: 'var(--st-ok-bg)' }
};
export const sev = (s) => SEV[String(s || '').toLowerCase()] || { fg: 'var(--color-neutral-400)', bg: 'var(--color-bg)' };

export const LABEL = {
  fontSize: '9.5px', letterSpacing: '0.11em', textTransform: 'uppercase',
  color: 'var(--color-neutral-500)'
};
export const CARD = {
  border: '1px solid var(--color-divider)', borderRadius: '9px',
  background: 'var(--color-bg)', padding: '10px 11px'
};
