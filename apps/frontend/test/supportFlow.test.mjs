// Support, end to end through the controller: pressing Support opens the orchestrator in a
// support session; the first question makes it a request (a session marked open, filed in the
// Support space); the user closes it when it is sorted; the next message after that is a new
// request, not a follow-up; asking for a person opens the email to Plenum instead of asking
// the orchestrator. The real methods are bound to a small store — a copy would keep passing
// after the real one changed.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const { sessionsMethods } = await import('../src/logic/sessions.js');
const { supportMethods, supportRef } = await import('../src/logic/support.js');
const { complianceLiveMethods } = await import('../src/logic/complianceLive.js');

const ME = 'pm@portfolio.com';

function harness(state) {
  const ctx = {
    state: Object.assign({
      sessions: [], account: { email: ME, full_name: 'Dana Reyes', role: 'admin', organization_name: 'Planum Technologies' },
      view: 'cc', ccChat: [], sessionId: null, ccBusy: false, flow: null, flowDone: ''
    }, state || {}),
    flashes: [],
    opened: 0,
    setState(patch, cb) {
      const next = typeof patch === 'function' ? patch(this.state) : patch;
      this.state = Object.assign({}, this.state, next);
      if (cb) cb();
    },
    flash(m) { this.flashes.push(m); },
    ctxLabel() { return { cc: 'Compliance', chat: 'Orchestrator', home: 'Home', space: 'Support' }[this.state.view] || 'Home'; },
    openChat() { this.opened += 1; this.setState({ view: 'chat', orchOpen: false, queueOpen: false, detail: null }); },
    mgDetach() {},
    emCheckHistory() { this.historyChecked = true; },
    asked: [],
    askScoped(q) { this.asked.push(q); }
  };
  Object.keys(sessionsMethods).forEach((k) => { ctx[k] = sessionsMethods[k].bind(ctx); });
  Object.keys(supportMethods).forEach((k) => { ctx[k] = supportMethods[k].bind(ctx); });
  ctx.chatContext = complianceLiveMethods.chatContext.bind(ctx);
  return ctx;
}
const current = (ctx) => ctx.state.sessions.find((r) => r.id === ctx.state.sessionId) || null;

test('Support opens the orchestrator page in a support session, leaving the last conversation behind', () => {
  const ctx = harness({ ccChat: [{ role: 'you', text: 'Old question' }], sessionId: 'old-thread' });
  ctx.openSupport();
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.opened, 1);
  assert.equal(ctx.state.sessionId, null, 'a follow-up to the old thread is not a support request');
  assert.deepEqual(ctx.state.ccChat, []);
  assert.equal(ctx.state.supportPending, true);
  assert.equal(ctx.state.supFrom, 'Compliance', 'it remembers where it was opened from');
  assert.equal(ctx.supIsOn(), true);
});

test('the first question makes the request: open, from the page Support was pressed on', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('How do I upload an EICR?');
  const rec = current(ctx);
  assert.equal(rec.id, id);
  assert.equal(rec.support.status, 'open');
  assert.equal(rec.support.from, 'Compliance');
  assert.equal(rec.page, 'Compliance');
  assert.equal(ctx.state.supportPending, false, 'consumed by the request it made');
  assert.equal(ctx.supIsOn(), true);
});

test('an ordinary question is never stamped as support', () => {
  const ctx = harness({ view: 'chat' });
  ctx.sessionEnsure('Which buildings put me at risk?');
  assert.equal(current(ctx).support, null);
  assert.equal(ctx.supIsOn(), false);
});

test('Support pressed and then left for another page does not turn the next question into a request', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ view: 'home' });
  ctx.sessionEnsure('Which vendors are blocked right now?');
  assert.equal(current(ctx).support, null);
  assert.equal(ctx.state.supportPending, false);
});

test('while a request is open, the orchestrator is told it is support and given the guide', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.sessionEnsure('How do I invite a colleague?');
  const c = ctx.chatContext();
  assert.match(c, /support/i);
  assert.ok(c.indexOf(supportRef(ctx.state.sessionId)) > -1);
  assert.match(c, /Buildings & access/);
  assert.match(c, /Planum Technologies/);
});

test('marking it sorted resolves it; the next message starts a new request', () => {
  const ctx = harness();
  ctx.openSupport();
  const first = ctx.sessionEnsure('How do I upload an EICR?');
  ctx.setState({ ccChat: [{ role: 'you', text: 'How do I upload an EICR?' }, { role: 'bot', text: 'Attach it…' }] });
  ctx.supResolve();
  const rec = ctx.state.sessions.find((r) => r.id === first);
  assert.equal(rec.support.status, 'resolved');
  assert.ok(rec.support.resolvedAt > 0);
  // The resolved line above the composer says what happens next; no toast repeats it.
  assert.equal(ctx.flashes.length, 0);

  const handled = ctx.supIntercept('And how do I add a building?');
  assert.equal(handled, false, 'the question is still asked — in a new request');
  assert.equal(ctx.state.sessionId, null);
  assert.deepEqual(ctx.state.ccChat, []);
  assert.equal(ctx.state.supportPending, true);
  const second = ctx.sessionEnsure('And how do I add a building?');
  assert.notEqual(second, first);
  assert.equal(current(ctx).support.status, 'open');
  assert.equal(ctx.state.sessions.find((r) => r.id === first).support.status, 'resolved', 'the closed one stays closed');
});

test('a resolved request can be reopened, and then a message continues it', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('SSO login loop for one user');
  ctx.supResolve();
  ctx.supReopen();
  assert.equal(current(ctx).support.status, 'open');
  assert.equal(current(ctx).support.resolvedAt, null);
  assert.equal(ctx.supIntercept('It happened again today'), false);
  assert.equal(ctx.state.sessionId, id, 'an open request takes follow-ups');
});

test('"I need support" typed anywhere opens Support instead of being asked', () => {
  const ctx = harness({ view: 'home' });
  assert.equal(ctx.supIntercept('I need support'), true);
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.state.supportPending, true);
  assert.equal(ctx.state.supFrom, 'Home');
});

test('a support question typed into a new conversation is asked as a support request', () => {
  const ctx = harness({ view: 'home' });
  assert.equal(ctx.supIntercept('I need help uploading an EICR'), false);
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.supIsOn(), true);
  ctx.sessionEnsure('I need help uploading an EICR');
  assert.equal(current(ctx).support.status, 'open');
});

test('"I need support" with a request still open goes back to it, not into it as a message', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('How do I upload an EICR?');
  ctx.setState({ ccChat: [{ role: 'you', text: 'How do I upload an EICR?' }, { role: 'bot', text: 'Attach it…' }], view: 'home' });
  assert.equal(ctx.supIntercept('I need support'), true);
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.state.sessionId, id);
  assert.equal(ctx.state.ccChat.length, 2, 'nothing was asked');
});

test('a portfolio question from another page is not added to the support request left open', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('How do I upload an EICR?');
  ctx.setState({ ccChat: [{ role: 'you', text: 'How do I upload an EICR?' }, { role: 'bot', text: 'Attach it…' }], view: 'home' });
  assert.equal(ctx.supIntercept('Which vendors are blocked right now?'), false);
  assert.equal(ctx.state.sessionId, null, 'a new conversation, not a follow-up in the ticket');
  assert.deepEqual(ctx.state.ccChat, []);
  assert.equal(ctx.supIsOn(), false);
  ctx.sessionEnsure('Which vendors are blocked right now?');
  assert.equal(current(ctx).support, null);
  assert.equal(ctx.state.sessions.find((r) => r.id === id).support.status, 'open', 'the request is left as it was');
});

test('a support-sounding question in the middle of a conversation is just a question', () => {
  const ctx = harness({ view: 'chat', ccChat: [{ role: 'you', text: 'Which vendors are blocked?' }, { role: 'bot', text: 'Two.' }], sessionId: 's1',
    sessions: [{ id: 's1', kind: 'chat', title: 'Which vendors are blocked?', owner: ME, support: null }] });
  assert.equal(ctx.supIntercept('I need help reading this'), false);
  assert.equal(ctx.state.sessionId, 's1');
  assert.equal(ctx.state.supportPending, undefined);
});

test('asking for a person at Plenum drafts the email from the conversation', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.sessionEnsure('Why was my EICR rejected?');
  ctx.setState({ ccChat: [{ role: 'you', text: 'Why was my EICR rejected?' }, { role: 'bot', text: 'The asset IDs on it are not on the register.' }] });
  assert.equal(ctx.supIntercept('Can I talk to someone at Plenum?'), true);
  const s = ctx.state;
  assert.equal(s.flow, 'email');
  assert.equal(s.emKind, 'support');
  assert.equal(s.emTo, 'support@hoistra.com');
  assert.equal(s.emCc, ME);
  assert.match(s.emSubject, new RegExp(supportRef(s.sessionId)));
  assert.match(s.emBody, /Why was my EICR rejected\?/);
  assert.equal(s.emSupportId, s.sessionId);
  assert.equal(s.fSubject, '', 'no other flow\'s subject leaks into the confirmation');
  assert.equal(ctx.historyChecked, true);
  const last = s.ccChat[s.ccChat.length - 1];
  assert.equal(last.role, 'bot');
  assert.equal(last.isNote, true);
  assert.match(last.text, /email to the Plenum team/i);
  assert.equal(s.ccChat[s.ccChat.length - 2].text, 'Can I talk to someone at Plenum?');
});

test('"email Plenum" typed on another page opens the conversation with the draft in it', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.sessionEnsure('How do I upload an EICR?');
  ctx.setState({ view: 'home' });
  assert.equal(ctx.supIntercept('please email the Plenum team'), true);
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.state.flow, 'email');
});

test('a question of your own, typed in the help index, is asked under the topic on screen', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ supTopic: 'integrations', supCustom: '  Can Hoistra pull work orders from Maximo every night?  ' });
  ctx.supAskCustom();
  assert.deepEqual(ctx.asked, ['Can Hoistra pull work orders from Maximo every night?']);
  assert.equal(ctx.state.supCustom, '', 'the field is cleared once asked');
  assert.equal(ctx.state.supAskedTopic, 'integrations');
  ctx.sessionEnsure('Can Hoistra pull work orders from Maximo every night?');
  assert.equal(current(ctx).support.topic, 'integrations');
  assert.match(ctx.chatContext(), /help topic Integrations/);
});

test('an empty question of your own asks nothing', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ supCustom: '   ' });
  ctx.supAskCustom();
  assert.deepEqual(ctx.asked, []);
});

test('a listed question is asked under its topic too', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ supTopic: 'access' });
  ctx.supAskFromIndex('How do I invite a colleague?');
  assert.deepEqual(ctx.asked, ['How do I invite a colleague?']);
  assert.equal(ctx.state.supAskedTopic, 'access');
});

test('a question typed in the composer is not tied to whichever topic happened to be open', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ supTopic: 'billing' });
  ctx.sessionEnsure('My EICR was rejected');
  assert.ok(!current(ctx).support.topic);
  assert.doesNotMatch(ctx.chatContext(), /help topic/);
});

test('a new request forgets the topic the last one started from', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.setState({ supTopic: 'access' });
  ctx.supAskFromIndex('How do I invite a colleague?');
  ctx.openSupport({ fresh: true });
  assert.equal(ctx.state.supAskedTopic, '');
});

test('Email Plenum before any question still makes the request it belongs to', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.supContact();
  assert.equal(ctx.state.flow, 'email');
  assert.equal(ctx.state.emKind, 'support');
  assert.equal(ctx.state.emSupportId, null, 'nothing asked yet: no request to stamp');
});

test('a sent email is recorded on its request', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('Why was my EICR rejected?');
  ctx.supMarkEmailed(id);
  assert.ok(ctx.state.sessions.find((r) => r.id === id).support.emailedAt > 0);
  ctx.supMarkEmailed('no-such-id');
});

test('New query and reopening another session leave support mode', () => {
  const ctx = harness();
  ctx.openSupport();
  ctx.newQuery();
  assert.equal(ctx.state.supportPending, false);
  ctx.openSupport();
  ctx.setState({ sessions: [{ id: 'c1', kind: 'chat', title: 'q', owner: ME, turns: [{ role: 'you', text: 'q' }], support: null }] });
  ctx.openSession('c1');
  assert.equal(ctx.state.supportPending, false);
  assert.equal(ctx.supIsOn(), false);
});

test('pressing Support on an open request that is on screen stays on it', () => {
  const ctx = harness();
  ctx.openSupport();
  const id = ctx.sessionEnsure('How do I upload an EICR?');
  ctx.setState({ ccChat: [{ role: 'you', text: 'How do I upload an EICR?' }] });
  ctx.openSupport();
  assert.equal(ctx.state.sessionId, id);
  assert.equal(ctx.state.ccChat.length, 1);
  ctx.openSupport({ fresh: true });
  assert.equal(ctx.state.sessionId, null, 'New request starts a new one');
});
