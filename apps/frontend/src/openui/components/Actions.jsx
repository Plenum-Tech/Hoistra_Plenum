// Actions — what to do next, and the buttons that do it or ask for it.
//
// A click goes through OpenUI's own action runner (useTriggerAction), which hands the chat a
// `continue_conversation` or `open_url` event; OpenUIAnswer decides what those mean here. Every
// control is disabled while its answer is still being written: a half-streamed button can carry
// a half-streamed question.
import React, { memo, useContext, useEffect, useRef, useState } from 'react';
import { useIsStreaming, useTriggerAction } from '@openuidev/react-lang';
import { inline } from '../../components/shell/markdownInline.jsx';
import { scrubInternal } from '../../logic/publicText.js';
import { arr, childProps, marked, sameData } from '../shared.jsx';
import { canOpen } from '../actions.js';
import { OpenUIHostContext } from '../host.js';
import { rowClick, stepQuestion, useAsk } from '../ask.jsx';

const steps = (action) => arr(action && action.steps).filter(Boolean);
const opensPage = (action) => steps(action).some((s) => s.type === 'open_url' && canOpen(s.url));
// An action whose every step opens somewhere the app cannot go would do nothing at all. The
// button asks its own label instead, as a button without an action does (27 Oct 2026: "Open
// work order WO-1184" to a path the app has no page for was an enabled button that did nothing).
const goesNowhere = (action) => steps(action).length > 0 && steps(action).every((s) => s.type === 'open_url' && !canOpen(s.url));
const actionOf = (action) => (goesNowhere(action) ? undefined : action);

export function Button({ props }) {
  const trigger = useTriggerAction();
  const streaming = useIsStreaming();
  const label = String(props.label == null ? '' : props.label);
  return (
    <button type="button" className={props.variant === 'primary' ? 'oui-btn is-primary' : 'oui-btn'} disabled={streaming}
      onClick={() => trigger(label, undefined, actionOf(props.action))}>
      {scrubInternal(label)}
      {opensPage(props.action) ? <i className="ph ph-arrow-up-right" aria-hidden="true"></i>
        : goesNowhere(props.action) ? <i className="ph ph-arrow-right" aria-hidden="true"></i> : null}
    </button>
  );
}

export function Buttons({ props, renderNode }) {
  return <div className="oui-buttons">{renderNode(props.items)}</div>;
}

const plainWords = (v) => String(v == null ? '' : v).replace(/\*\*|__/g, '').replace(/[^\w]+/g, ' ').trim().toLowerCase();
// A button label that says nothing its step does not: every one of its words is in the title.
const echoes = (label, title) => {
  const said = new Set(plainWords(title).split(' '));
  const words = plainWords(label).split(' ').filter(Boolean);
  return words.length > 0 && words.every((w) => said.has(w));
};

// A step's button that only repeats the step ("Reassess the FRA now" beside the step "Reassess the
// FRA now"): the same action as a compact arrow, its label kept as the name a pointer or a screen
// reader gets, so the row does not say it twice.
function EchoButton({ node }) {
  const trigger = useTriggerAction();
  const streaming = useIsStreaming();
  const p = childProps(node) || {};
  const label = String(p.label == null ? '' : p.label);
  return (
    <button type="button" className="oui-btn oui-btn-icon" disabled={streaming} aria-label={scrubInternal(label)} title={scrubInternal(label)}
      onClick={() => trigger(label, undefined, actionOf(p.action))}>
      <i className={opensPage(p.action) ? 'ph ph-arrow-up-right' : 'ph ph-arrow-right'} aria-hidden="true"></i>
    </button>
  );
}

// A step the model gave no button still leads somewhere (28 Oct 2026, Hussain: "i need the what
// to do arrow"; a step without an action drew nothing, so most "What to do" lists were dead
// text). Its arrow asks how to carry the step out, in the step's own words; the question is the
// arrow's name and tooltip, so the reader sees what it will ask. The same compact arrow as an
// echoed button, so every row of the list reads alike.
function StepArrow({ question }) {
  const { ready, streaming, ask } = useAsk();
  if (!ready || !question) return null;
  return (
    <button type="button" className="oui-btn oui-btn-icon" disabled={streaming} aria-label={question} title={question}
      onClick={() => ask(question)}>
      <i className="ph ph-arrow-right" aria-hidden="true"></i>
    </button>
  );
}

// One step. A row whose only control is its arrow (an echoed button, or the step's own question)
// is the arrow's: a click anywhere on it does what the arrow does, except on a link or a code in
// its words, which keep their own. A row with a labelled button keeps the button as its one
// control (28 Oct 2026).
function StepRow({ s, i, renderNode }) {
  const { ready, streaming, trigger, ask } = useAsk();
  const node = s.action;
  const p = childProps(node) || {};
  const echo = !!node && echoes(p.label, s.title);
  const question = node ? '' : stepQuestion(s.title);
  const arrow = echo || (!node && ready && !!question);
  const run = () => {
    if (!ready || streaming) return;
    if (echo) trigger(String(p.label == null ? '' : p.label), undefined, actionOf(p.action));
    else ask(question);
  };
  return (
    <li className={arrow ? 'oui-step is-ask' : 'oui-step'} onClick={arrow ? rowClick(run) : undefined}>
      <span className="oui-step-n" aria-hidden="true">{i + 1}</span>
      <span className="oui-step-text">
        <span className="oui-step-title">{marked(s.title, 'sx' + i)}</span>
        {s.detail ? <span className="oui-step-detail">{inline(s.detail, 'sd' + i)}</span> : null}
      </span>
      {node ? <span className="oui-step-action">{echo ? <EchoButton node={node} /> : renderNode(node)}</span>
        : arrow ? <span className="oui-step-action"><StepArrow question={question} /></span> : null}
    </li>
  );
}

// Numbered, the way a list of things to do is read (the order is the priority); each step's
// button sits on its row, and a step with none gets the arrow that asks how to do it.
export function Steps({ props, renderNode }) {
  const steps = arr(props.items).map(childProps).filter(Boolean);
  return (
    <ol className="oui-steps">
      {steps.map((s, i) => <StepRow key={i} s={s} i={i} renderNode={renderNode} />)}
    </ol>
  );
}

// Drawn by Steps.
export function Step() { return null; }

// The next questions, one click to ask.
export function FollowUps({ props }) {
  const trigger = useTriggerAction();
  const streaming = useIsStreaming();
  const qs = arr(props.questions).filter((q) => typeof q === 'string' && q.trim());
  return (
    <div className="oui-followups">
      {qs.map((q, i) => (
        <button key={i} type="button" className="oui-chip" disabled={streaming} onClick={() => trigger(q)}>
          <i className="ph ph-arrow-bend-down-right" aria-hidden="true"></i>
          <span>{scrubInternal(q)}</span>
        </button>
      ))}
    </div>
  );
}

// The email addresses in a recipient line, and nothing else: a name, a role or a placeholder
// ("Pennard Fire Services (contract manager)", "[Your facilities manager]") put in a mailto
// opens the mail client with a recipient it cannot send to (27 Oct 2026).
const ADDRESS = /[^\s@<>,;:()[\]"']+@[^\s@<>,;:()[\]"']+\.[^\s@<>,;:()[\]"'.]+/g;
const addresses = (s) => String(s || '').match(ADDRESS) || [];
// A recipient line as shown: its addresses exactly as written (a scrub must never rewrite an
// address), the words around them scrubbed like any other text.
function shownRecipients(s) {
  let out = '';
  let last = 0;
  for (const m of s.matchAll(ADDRESS)) {
    out += scrubInternal(s.slice(last, m.index)) + m[0];
    last = m.index + m[0].length;
  }
  return out + scrubInternal(s.slice(last));
}

// An email the user sends themselves: Copy puts the whole draft on the clipboard, and Open in
// email starts it in their mail client with every field filled in. Both send exactly the words
// the card shows, and neither works while the draft is still being written: a half-streamed
// body is not a draft (27 Oct 2026).
//
// Inside the chat, Edit in Hoistra opens the same draft in the app's own email card (host.email),
// where it can be edited and is sent only by its Approve & send, so the send is logged like every
// other email the app sends; Copy and a mail client leave no record (28 Oct 2026). It opens the
// card and nothing more: it sends nothing. Drawn anywhere without that card (a report, a test),
// the host has no email and the control is not drawn.
export const EmailDraft = memo(function EmailDraft({ props }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(0);
  const streaming = useIsStreaming();
  const host = useContext(OpenUIHostContext);
  const canEdit = !!host && typeof host.email === 'function';
  useEffect(() => () => clearTimeout(timer.current), []);
  const subject = scrubInternal(String(props.subject == null ? '' : props.subject));
  const to = shownRecipients(String(props.to == null ? '' : props.to));
  const cc = props.cc ? shownRecipients(String(props.cc)) : '';
  const body = scrubInternal(String(props.body == null ? '' : props.body));
  const full = 'Subject: ' + subject + '\nTo: ' + to + (cc ? '\nCc: ' + cc : '') + '\n\n' + body;
  const ccs = addresses(cc);
  const query = ['subject=' + encodeURIComponent(subject), ccs.length ? 'cc=' + ccs.map(encodeURIComponent).join(',') : '', 'body=' + encodeURIComponent(body)].filter(Boolean).join('&');
  const mailto = 'mailto:' + addresses(to).map(encodeURIComponent).join(',') + '?' + query;
  // Where the clipboard API is missing or refused (a page served over plain http, a browser that
  // asks first), the draft is copied the older way, from a hidden text box, so Copy never does
  // nothing (28 Oct 2026).
  const copy = () => {
    const done = () => { setCopied(true); clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 1600); };
    const fallback = () => {
      try {
        const box = document.createElement('textarea');
        box.value = full;
        box.setAttribute('readonly', '');
        box.style.position = 'fixed';
        box.style.opacity = '0';
        document.body.appendChild(box);
        box.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(box);
        if (ok) done();
      } catch (e) { /* nothing more to try */ }
    };
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(full).then(done, fallback);
    else if (typeof document !== 'undefined') fallback();
  };
  return (
    <div className="oui-card oui-email">
      <div className="oui-label"><i className="ph ph-envelope-simple" aria-hidden="true"></i>{' Email draft'}</div>
      <div className="oui-email-subject">{subject}</div>
      <div className="oui-email-meta">
        <span>To</span><span>{to}</span>
        {cc ? <><span>Cc</span><span>{cc}</span></> : null}
      </div>
      <div className="oui-email-body">{body}</div>
      <div className="oui-email-actions">
        {canEdit ? (
          <button type="button" className="oui-btn" disabled={streaming}
            title="Open this draft in Hoistra's email card to edit, then approve to send"
            onClick={() => { if (!streaming) host.email({ to, cc, subject, body }); }}>
            <i className="ph ph-pencil-simple" aria-hidden="true"></i>{'Edit in Hoistra'}
          </button>
        ) : null}
        <button type="button" className="oui-btn" disabled={streaming} onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
        <a className="oui-btn is-primary" href={streaming ? undefined : mailto} aria-disabled={streaming || undefined}>{'Open in email'}<i className="ph ph-arrow-up-right" aria-hidden="true"></i></a>
      </div>
    </div>
  );
}, sameData);
