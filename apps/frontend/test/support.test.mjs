// support — Support is a conversation with the orchestrator, not a page (call with Aasim,
// 7 Oct 2026): pressing Support opens the orchestrator in a support session, the help topics
// are prompts it can be asked, a session ends when the user says it is sorted, and the
// Plenum team is reached by an email drafted from the conversation. Pure functions only;
// the controller flow is in supportFlow.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const {
  SUPPORT_TOPICS, SUPPORT_EMAIL, SUPPORT_SPACE_KEY, supportRef, isBareSupportAsk, isSupportAsk, wantsPlenum,
  supportStatus, supportContext, supportDraft, shapeSupportSpace
} = await import('../src/logic/support.js');
const { makeSession, loadSessions, saveSessions, shapeSessionList, sessionIcon } = await import('../src/logic/sessions.js');
const { shapeSpaces } = await import('../src/logic/spacesLive.js');

const ME = 'me@example.com';
const NOW = new Date(2026, 9, 7, 14, 0, 0).getTime();
const memStorage = () => {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; } };
};
const ticket = (id, status, extra) => Object.assign(makeSession({ id: id, title: 'How do I upload an EICR?', page: 'Compliance', at: NOW, owner: ME }),
  { support: { status: status, openedAt: NOW - 60000, resolvedAt: status === 'resolved' ? NOW : null, from: 'Compliance' } }, extra || {});

test('the six help topics from the old Support page are prompts, each with questions to ask', () => {
  assert.deepEqual(SUPPORT_TOPICS.map((t) => t.name),
    ['Ingesting documents', 'Buildings & access', 'Audit trail', 'Credits & billing', 'Security & data', 'Integrations']);
  SUPPORT_TOPICS.forEach((t) => {
    assert.ok(t.key && t.icon && t.blurb, t.name + ' needs a key, an icon and a line');
    assert.ok(t.prompts.length >= 3, t.name + ' offers at least three questions');
    t.prompts.forEach((p) => assert.ok(/\?$/.test(p), '"' + p + '" reads as a question'));
  });
  assert.equal(new Set(SUPPORT_TOPICS.map((t) => t.key)).size, SUPPORT_TOPICS.length);
});

test('the support address is the one the Support page showed', () => {
  assert.equal(SUPPORT_EMAIL, 'support@hoistra.com');
  assert.equal(SUPPORT_SPACE_KEY, 'support');
});

test('a support reference is read from the thread id, the same every time', () => {
  const id = '3f9a1c7e-55aa-4b1e-9c0d-1234567890ab';
  assert.equal(supportRef(id), 'SUP-3F9A1C');
  assert.equal(supportRef(id), supportRef(id));
  assert.equal(supportRef('hs-lq2v9x-ab12cd34'), 'SUP-HSLQ2V');
  assert.equal(supportRef(''), '');
  assert.equal(supportRef(null), '');
});

test('"I need support" on its own opens support; a real question goes with it', () => {
  ['I need support', 'support', 'Help', 'i need help', 'I want support please', 'get help', 'Need support!']
    .forEach((q) => assert.equal(isBareSupportAsk(q), true, q));
  ['I need help uploading an EICR', 'Which buildings need support?', 'How do I get help with a scan?', 'helpdesk tickets this month', '']
    .forEach((q) => assert.equal(isBareSupportAsk(q), false, q));
});

test('a support request is recognised whether or not it carries a question', () => {
  ['I need support', 'I need help uploading an EICR', 'I need support with SSO', 'Can I raise a support ticket?', 'contact Plenum support', 'open a ticket']
    .forEach((q) => assert.equal(isSupportAsk(q), true, q));
  ['Which buildings need support?', 'Show work orders with help desk category', 'Which vendors are blocked right now?', 'helpdesk tickets this month']
    .forEach((q) => assert.equal(isSupportAsk(q), false, q));
});

test('asking for a person at Plenum is a request for the email, not a question', () => {
  ['Can I talk to someone at Plenum?', 'email support', 'Contact Plenum', 'I want to speak to a human', 'please email the Plenum team', 'escalate this to support']
    .forEach((q) => assert.equal(wantsPlenum(q), true, q));
  ['Email the vendor about the lapsed certificate', 'Who is the contact for Apex Lifts?', 'How do I invite someone?', 'support hours for lifts']
    .forEach((q) => assert.equal(wantsPlenum(q), false, q));
});

test('a support record carries its status through the browser store', () => {
  const st = memStorage();
  saveSessions([ticket('t1', 'resolved'), makeSession({ id: 'c1', title: 'Which buildings put me at risk?', at: NOW, owner: ME })], st);
  const back = loadSessions(st);
  const t = back.find((r) => r.id === 't1');
  assert.deepEqual(t.support, { status: 'resolved', openedAt: NOW - 60000, resolvedAt: NOW, from: 'Compliance' });
  assert.equal(back.find((r) => r.id === 'c1').support, null);
  assert.equal(supportStatus(t), 'resolved');
  assert.equal(supportStatus(back.find((r) => r.id === 'c1')), null);
});

test('a malformed support stamp is dropped, not trusted', () => {
  const st = memStorage();
  st.setItem('hoistra.sessions.v1', JSON.stringify([
    { id: 'x', title: 'q', at: NOW, owner: ME, support: { status: 'deleted-by-hand' } },
    { id: 'y', title: 'q', at: NOW, owner: ME, support: 'open' }
  ]));
  const back = loadSessions(st);
  assert.deepEqual(back.find((r) => r.id === 'x').support, { status: 'open', openedAt: null, resolvedAt: null, from: '' });
  assert.equal(back.find((r) => r.id === 'y').support, null);
});

test('the Support space lists support sessions only, with their status; engine spaces leave them out', () => {
  const sessions = [
    ticket('t1', 'open'),
    ticket('t2', 'resolved', { domain: 'Compliance' }),
    Object.assign(makeSession({ id: 'c1', title: 'Lapsed certificates?', at: NOW, owner: ME }), { domain: 'Compliance' })
  ];
  const sup = shapeSessionList(sessions, { space: 'support', owner: ME, nowMs: NOW });
  const rows = sup.flatMap((g) => g.rows);
  assert.deepEqual(rows.map((r) => r.id).sort(), ['t1', 't2']);
  assert.equal(rows.find((r) => r.id === 't1').status, 'open');
  assert.equal(rows.find((r) => r.id === 't2').status, 'resolved');
  assert.equal(rows.find((r) => r.id === 't1').icon, 'ph-lifebuoy');
  assert.equal(rows.find((r) => r.id === 't1').domain, 'Support');
  assert.equal(rows.find((r) => r.id === 't1').ref, 'SUP-T1');
  const comp = shapeSessionList(sessions, { space: 'compliance', owner: ME, nowMs: NOW }).flatMap((g) => g.rows);
  assert.deepEqual(comp.map((r) => r.id), ['c1'], 'a support ticket that touched compliance data is still a support ticket');
  const all = shapeSessionList(sessions, { owner: ME, nowMs: NOW }).flatMap((g) => g.rows);
  assert.equal(all.length, 3, 'the full history still has every session');
  assert.equal(all.find((r) => r.id === 'c1').status, null);
  assert.equal(sessionIcon(ticket('t9', 'open')), 'ph-lifebuoy');
});

test('the Support space can be narrowed to open or resolved requests', () => {
  const sessions = [ticket('t1', 'open'), ticket('t2', 'resolved'), ticket('t3', 'open')];
  const open = shapeSessionList(sessions, { space: 'support', status: 'open', owner: ME, nowMs: NOW }).flatMap((g) => g.rows);
  assert.deepEqual(open.map((r) => r.id).sort(), ['t1', 't3']);
  const done = shapeSessionList(sessions, { space: 'support', status: 'resolved', owner: ME, nowMs: NOW }).flatMap((g) => g.rows);
  assert.deepEqual(done.map((r) => r.id), ['t2']);
});

test('the Support space counts only the asker\'s own requests', () => {
  const mine = [ticket('t1', 'open'), ticket('t2', 'resolved'), ticket('t3', 'resolved')];
  const theirs = Object.assign(ticket('o1', 'open'), { owner: 'them@example.com' });
  const scoped = Object.assign(ticket('v1', 'open'), { viewOrgId: 'org-techcorp' });
  const m = shapeSupportSpace(mine.concat([theirs, scoped]), { owner: ME, viewOrgId: null });
  assert.equal(m.open, 1);
  assert.equal(m.resolved, 2);
  assert.equal(m.total, 3);
  assert.equal(m.badge, '1 open');
  assert.deepEqual(m.kpis.map((k) => [k.label, k.value]), [['Open', 1], ['Resolved', 2], ['All requests', 3]]);
  assert.equal(shapeSupportSpace([], { owner: ME }).badge, 'None open');
  assert.equal(shapeSupportSpace(mine, { owner: null }).total, 0, 'no account, no requests');
});

test('shapeSpaces has the Support space beside the four engines, not among them', () => {
  const m = shapeSpaces({ home: null, vendors: { live: false, vendors: [] }, saved: [], owner: ME,
    sessions: [ticket('t1', 'open'), ticket('t2', 'resolved', { domain: 'Compliance' })] });
  assert.deepEqual(m.builtin.map((b) => b.key), ['compliance', 'energy', 'vendors', 'ops']);
  assert.equal(m.support.key, 'support');
  assert.equal(m.support.name, 'Support');
  assert.equal(m.support.icon, 'ph-lifebuoy');
  assert.equal(m.support.badge, '1 open');
  assert.equal(m.byKey.support, m.support);
  assert.equal(m.byKey.compliance.sessions, 0, 'a ticket is not counted as a compliance conversation');
});

test('the orchestrator is told it is a support session and given the guide', () => {
  const ctx = supportContext({ ref: 'SUP-3F9A1C', from: 'Compliance', company: 'Planum Technologies' });
  assert.match(ctx, /support/i);
  assert.match(ctx, /SUP-3F9A1C/);
  assert.match(ctx, /Compliance/);
  assert.match(ctx, /Planum Technologies/);
  SUPPORT_TOPICS.forEach((t) => assert.ok(ctx.indexOf(t.name) > -1, 'the guide covers ' + t.name));
  assert.match(ctx, /Email Plenum/, 'it names the button that reaches a person');
  assert.match(ctx, /do not guess|never guess/i);
  // Customer-facing: no service names, tables or routes (no-internal-names rule).
  assert.doesNotMatch(ctx, /svc-|plenum_cafm|\/api\/|deepagents|UDR\b|RAG\b/);
});

test('a question asked from a help topic tells the orchestrator which topic it was', () => {
  const ctx = supportContext({ ref: 'SUP-1', topic: 'Integrations' });
  assert.match(ctx, /help topic Integrations/);
  assert.doesNotMatch(supportContext({ ref: 'SUP-1' }), /help topic/);
});

test('the topic a request started from is kept on its record', () => {
  const st = memStorage();
  saveSessions([ticket('t1', 'open', { support: { status: 'open', openedAt: NOW, from: 'Home', topic: 'integrations' } })], st);
  assert.equal(loadSessions(st).find((r) => r.id === 't1').support.topic, 'integrations');
});

test('the guide stands on its own when nothing is known about where support was opened', () => {
  const ctx = supportContext({});
  assert.match(ctx, /support/i);
  assert.doesNotMatch(ctx, /undefined|null/);
});

test('the email to Plenum carries the conversation, the reference and who is asking', () => {
  const rec = ticket('3f9a1c7e-55aa', 'open');
  const turns = [
    { role: 'you', text: 'How do I upload an EICR?' },
    { role: 'bot', text: 'Attach it with the paperclip and choose the building.' },
    { role: 'you', text: 'It says the asset IDs are not recognised.' },
    { role: 'bot', text: 'x'.repeat(2000) }
  ];
  const d = supportDraft({ rec: rec, turns: turns, account: { email: 'Pm@Portfolio.com', full_name: 'Dana Reyes' }, company: 'Planum Technologies', from: 'Compliance' });
  assert.equal(d.to, 'support@hoistra.com');
  assert.equal(d.cc, 'pm@portfolio.com', 'a copy goes to the asker, so the reply reaches them');
  assert.match(d.subject, /SUP-3F9A1C/);
  assert.match(d.subject, /How do I upload an EICR\?/);
  assert.match(d.body, /How do I upload an EICR\?/);
  assert.match(d.body, /It says the asset IDs are not recognised\./);
  assert.match(d.body, /Dana Reyes/);
  assert.match(d.body, /pm@portfolio\.com/);
  assert.match(d.body, /Planum Technologies/);
  assert.match(d.body, /SUP-3F9A1C/);
  assert.match(d.body, /Compliance/);
  assert.ok(d.body.length < 2600, 'a long answer is cut, not pasted whole');
  assert.ok(d.body.indexOf('x'.repeat(700)) < 0);
});

test('the answer in the email reads as text: its steps on their own lines, no markdown markers', () => {
  const turns = [
    { role: 'you', text: 'How do I invite a colleague?' },
    { role: 'bot', text: '1. Switch to **Admin view** from the account menu.\n2. Open `Users & access` and press __Invite user__.\n\n## Then\n- Press **Send invitation**.' }
  ];
  const d = supportDraft({ rec: ticket('abc123', 'open'), turns: turns, account: { email: 'a@b.c' } });
  assert.match(d.body, /1\. Switch to Admin view from the account menu\.\n2\. Open Users & access and press Invite user\./);
  assert.match(d.body, /\nThen\n- Press Send invitation\./);
  assert.doesNotMatch(d.body, /\*\*|__|`|##/);
});

test('an email drafted before any question still says who is asking and leaves room to explain', () => {
  const d = supportDraft({ rec: null, turns: [], account: { email: 'pm@portfolio.com' }, company: '', from: 'Home' });
  assert.equal(d.to, 'support@hoistra.com');
  assert.match(d.subject, /Hoistra support/);
  assert.match(d.body, /pm@portfolio\.com/);
  assert.doesNotMatch(d.body, /undefined|null/);
});

test('a long first question is shortened in the subject line only', () => {
  const q = 'Why does the compliance console show a lapsed certificate for a building that we sold last year and removed from the register?';
  const d = supportDraft({ rec: ticket('abcdef12', 'open', { title: q }), turns: [{ role: 'you', text: q }], account: { email: 'a@b.c' } });
  assert.ok(d.subject.length <= 100, d.subject);
  assert.match(d.subject, /…$/);
  assert.ok(d.body.indexOf(q) > -1);
});
