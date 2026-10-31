// actions — where a click in an OpenUI answer goes.
//
// The answer's buttons fire OpenUI events; the model wrote their targets, so nothing is opened on
// trust. A follow-up question is asked in this conversation. A page of the app opens in the app.
// A web page opens in a new tab only when it is plainly http(s). Everything else does nothing.

// The app's pages a model may name, and the module each one opens.
const PAGES = {
  compliance: 'compliance', energy: 'energy', vendors: 'vendors', assets: 'assets',
  maintenance: 'ops', 'work-orders': 'ops', ops: 'ops'
};

// A path inside one of those pages opens the page: "/work-orders/WO-1184" opens the work orders
// (27 Oct 2026: it was ignored, and its button did nothing).
export function pageOf(url) {
  const m = /^\/([a-z-]+)(?:\/\S*)?$/i.exec(String(url == null ? '' : url).trim().replace(/[?#].*$/, ''));
  return m ? PAGES[m[1].toLowerCase()] || null : null;
}

const WEB = /^https?:\/\/[^\s/?#]+/i;

// Whether a click on @OpenUrl(url) would open anything here.
export const canOpen = (url) => !!pageOf(url) || WEB.test(String(url == null ? '' : url).trim());

// A @ToAssistant context, said in words after the question: "Show details (wo: WO-1184)". The
// prompt asks for the whole question in the message; a model that still passes context must not
// have it dropped.
function contextText(ctx) {
  if (ctx === null || ctx === undefined || ctx === '') return '';
  if (typeof ctx !== 'object') return String(ctx);
  if (Array.isArray(ctx)) return ctx.map(contextText).filter(Boolean).join(', ');
  return Object.entries(ctx).map(([k, v]) => k + ': ' + contextText(v)).join(', ');
}

// host: { ask(question), open({ page } | { url }) }. Returns what was done, for the tests.
export function routeAction(event, host) {
  if (!event || typeof event !== 'object' || !host) return 'ignored';
  if (event.type === 'continue_conversation') {
    const q = String(event.humanFriendlyMessage == null ? '' : event.humanFriendlyMessage).trim();
    if (!q) return 'ignored';
    const ctx = contextText(event.params && event.params.context).trim();
    host.ask(ctx ? q + ' (' + ctx + ')' : q);
    return 'ask';
  }
  if (event.type === 'open_url') {
    const url = String((event.params && event.params.url) || '').trim();
    const page = pageOf(url);
    if (page) { host.open({ page }); return 'open'; }
    if (WEB.test(url)) { host.open({ url }); return 'open'; }
  }
  return 'ignored';
}
