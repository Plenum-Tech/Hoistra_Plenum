// supportVals — what the conversation page shows while it is a support session (logic/support.js):
// the request's reference and status, the help topics as questions to ask, "Did this sort it
// out?" under the latest answer, the resolved line, and the email to Plenum. Spread into
// renderVals(); `c` is the controller. The email fields themselves are renderVals' `em` — the
// same draft and the same send the dock uses.
import { SUPPORT_TOPICS, supportRef, supportStatus } from './support.js';
import { ago } from './sessions.js';

export function supportVals(c) {
  const s = c.state;
  const rec = c.supCurrent();
  const status = supportStatus(rec);
  const on = c.supIsOn() && s.view === 'chat';
  const chat = s.ccChat || [];
  const last = chat[chat.length - 1];
  const answered = chat.some((m) => m && m.role !== 'you' && !m.isNote);
  const draftOpen = s.flow === 'email' && s.emKind === 'support';
  const sup = (rec && rec.support) || {};
  const from = sup.from || s.supFrom || '';
  const topic = SUPPORT_TOPICS.find((t) => t.key === s.supTopic) || SUPPORT_TOPICS[0];
  const resolveShow = on && status === 'open' && !s.ccBusy && answered && !!last && last.role !== 'you' && !draftOpen;
  return {
    supOn: on,
    supRef: rec && status ? supportRef(rec.id) : '',
    // 'new' until the first question makes the request.
    supStatus: status || 'new',
    supStatusLabel: status === 'resolved' ? 'Resolved' : status === 'open' ? 'Open' : 'New request',
    supMeta: [
      from ? 'Opened from ' + from : '',
      status ? ago(sup.openedAt || rec.createdAt) : '',
      sup.emailedAt ? 'emailed to Plenum ' + ago(sup.emailedAt) : ''
    ].filter(Boolean).join(' · '),
    supTopics: SUPPORT_TOPICS.map((t) => ({
      key: t.key, name: t.name, icon: t.icon, on: t.key === topic.key,
      pick: () => c.setState({ supTopic: t.key })
    })),
    supTopic: {
      key: topic.key, name: topic.name, icon: topic.icon, blurb: topic.blurb,
      prompts: topic.prompts.map((p) => ({ text: p, ask: () => c.supAskFromIndex(p) }))
    },
    // A question of your own, from the index: asked under the topic on screen.
    supCustom: s.supCustom || '',
    supCustomPh: 'Ask your own question about ' + topic.name.replace(/&/g, 'and').toLowerCase() + '…',
    supCustomCanAsk: !!String(s.supCustom || '').trim() && !s.ccBusy,
    setSupCustom: (e) => c.setState({ supCustom: e.target.value }),
    // Enter asks; Shift+Enter is a new line.
    supCustomKey: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); c.supAskCustom(); } },
    supCustomAsk: () => c.supAskCustom(),
    // The topics are the opening of a request; once it has a question they make way for it.
    supShowTopics: on && !chat.length && !s.ccBusy,
    supResolveShow: resolveShow,
    supNotYetShow: resolveShow && s.supNotYet === chat.length,
    supResolve: () => c.supResolve(),
    supNotYet: () => c.supNotYet(),
    supReopen: () => c.supReopen(),
    supContact: () => c.supContact(),
    supNew: () => c.openSupport({ fresh: true }),
    supOpenSpace: () => c.openSpace('support'),
    supResolved: on && status === 'resolved',
    supResolvedText: 'Resolved ' + ago(sup.resolvedAt || Date.now()) + '. Your next message starts a new support request.',
    supDraftOpen: on && draftOpen,
    supDraftCc: draftOpen && s.emCc ? s.emCc : '',
    // The top bar's Support, lit while a support session is on screen.
    openSupportTop: () => c.openSupport(),
    supTopActive: on,
    supPlaceholder: status === 'resolved'
      ? 'Ask something new — it starts a new support request'
      : chat.length ? 'Tell me more, or ask a follow-up…' : 'Ask how something works, or describe what went wrong…'
  };
}
