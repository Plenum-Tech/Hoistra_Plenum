// fromCompliance — the compliance pipeline's structured answer, written as an OpenUI program.
//
// The compliance route answers with JSON cards (compliance_response, logic/complianceLive.js),
// never with text the model wrote as OpenUI. This writes those cards as the components every other
// answer is drawn with: the narrative as headline and prose, KPIs as figures, days overdue as bars,
// priority actions as numbered steps, owner groups and insights as findings, and the certificates
// in scope as a table the reader opens.
//
// Presentation only: every value is the payload's own. What carries a button that runs something
// here (pending items, offers) is not written: ComplianceAnswer keeps drawing those itself, because
// an OpenUI button can only ask a question or open a page.
import { lit, markdownChildren, programWriter } from './fromMarkdown.js';

const arr = (v) => (Array.isArray(v) ? v : []);
const str = (v) => String(v == null ? '' : v).trim();
const sevKey = (s) => str(s).toLowerCase();
const METRIC_TONE = { critical: 'risk', warning: 'warn', ok: 'ok' };
const CALLOUT_TONE = { critical: 'risk', warning: 'warn', ok: 'ok' };
const INSIGHT = {
  correlation: ['info', 'Correlation detected'], risk: ['risk', 'Risk'], anomaly: ['warn', 'Anomaly']
};
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

function kpis(w, rich) {
  const items = arr(rich.kpis).filter((k) => k && str(k.label));
  const out = [];
  for (let i = 0; i < items.length; i += 6) {
    const ids = items.slice(i, i + 6).map((k) => {
      // "other" is the builders' placeholder unit (planner._kpi), not a unit worth printing.
      const note = [str(k.sublabel), k.unit && k.unit !== 'other' ? str(k.unit) : ''].filter(Boolean).join(' · ');
      const tone = METRIC_TONE[sevKey(k.severity)];
      const value = typeof k.count === 'number' && Number.isFinite(k.count) ? String(k.count) : lit(str(k.count));
      return w.add('Metric(' + lit(k.label) + ', ' + value + (note || tone ? ', ' + (note ? lit(note) : 'null') : '') + (tone ? ', ' + lit(tone) : '') + ')');
    });
    out.push(w.add('Metrics([' + ids.join(', ') + '])'));
  }
  return out;
}

// Eight bars a scope at most (complianceLive.overdueBars); `more` says how many were left out,
// and the reader is told where they are rather than the chart ending as if it were complete.
function overdue(w, rich) {
  const o = rich.overdue || {};
  const more = o.more || {};
  return [['Days overdue — buildings', arr(o.buildings), more.buildings], ['Days overdue — vendors', arr(o.vendors), more.vendors]]
    .filter(([, rows]) => rows.length)
    .flatMap(([title, rows, left]) => [
      w.add('BarChart(' + lit(title) + ', [' + rows.map((r) => lit(r.label)).join(', ') + '], ['
        + rows.map((r) => (typeof r.days === 'number' && Number.isFinite(r.days) ? r.days : 'null')).join(', ') + '], "days")'),
      ...(left > 0 ? [w.add('Text(' + lit('+' + left + ' more in Certificates in scope') + ')')] : [])
    ]);
}

function actions(w, rich) {
  const items = arr(rich.actions).filter((a) => a && str(a.title));
  if (!items.length) return [];
  const steps = items.map((a) => {
    const certs = arr(a.cert_ids).length;
    const detail = [
      [cap(sevKey(a.severity)), cap(str(a.scope))].filter(Boolean).join(' · '),
      certs ? certs + ' cert' + (certs === 1 ? '' : 's') : '',
      arr(a.tags).map(str).filter(Boolean).join(', ')
    ].filter(Boolean).join(' · ');
    return w.add('Step(' + lit(a.title) + (detail ? ', ' + lit(detail) : '') + ')');
  });
  return [w.add('Section("Priority actions", [' + w.add('Steps([' + steps.join(', ') + '])') + '])')];
}

function groups(w, rich) {
  return arr(rich.groups).filter((g) => g && (str(g.owner) || str(g.headline))).map((g) => {
    const inner = [];
    const tone = CALLOUT_TONE[sevKey(g.severity)] || 'info';
    if (str(g.headline)) inner.push(w.add('Callout(' + lit(tone) + ', ' + lit(g.headline) + ')'));
    const points = arr(g.points).map(str).filter(Boolean);
    if (points.length) inner.push(w.add('Text(' + lit(points.map((p) => '- ' + p).join('\n')) + ')'));
    const title = [str(g.owner), str(g.scope) ? cap(str(g.scope)) : ''].filter(Boolean).join(' · ');
    return w.add('Section(' + lit(title || 'Owner') + ', [' + inner.join(', ') + '])');
  });
}

function insights(w, rich) {
  return arr(rich.insights).filter((x) => x && str(x.text)).map((x) => {
    const [tone, title] = INSIGHT[sevKey(x.type)] || ['info', 'Insight'];
    return w.add('Callout(' + lit(tone) + ', ' + lit(x.text) + ', ' + lit(title) + ')');
  });
}

// The certificates the answer rests on: supporting detail, so a closed section whose one line
// says how they stand.
function certificates(w, rich) {
  const certs = arr(rich.certificates).filter(Boolean);
  if (!certs.length) return [];
  const withReason = certs.some((c) => str(c.reason));
  const cols = ['Certificate', 'Held by', 'Scope', 'Status', ...(withReason ? ['Note'] : [])];
  const rows = certs.map((c) => '[' + [c.name, c.company, c.scope, c.status, ...(withReason ? [c.reason] : [])].map((v) => lit(str(v))).join(', ') + ']');
  const counts = {};
  certs.forEach((c) => { const s = str(c.status).toLowerCase(); if (s) counts[s] = (counts[s] || 0) + 1; });
  const summary = Object.keys(counts).map((s) => counts[s] + ' ' + s).join(', ');
  const table = w.add('Table([' + cols.map(lit).join(', ') + '], [' + rows.join(', ') + '])');
  return [w.add('Section(' + lit('Certificates in scope · ' + certs.length) + ', [' + table + ']' + (summary ? ', ' + lit(summary) : ', null') + ', true)')];
}

// The text drawn above the cards. The card builders (svc-deepagents planner._lead) set the
// narrative to the answer's first long paragraph with its Markdown marks stripped, so an answer
// with a table, a caveat and steps showed one sentence; a bullet-only answer showed none; a
// table-first answer showed its pipes as the headline (27 Oct 2026). When the narrative is that
// (empty, or the answer's opening or one of its paragraphs once marks and spacing are set
// aside), the whole answer is drawn instead. A narrative the compliance engine wrote itself is
// its own and is kept. So is the narrative of an answer whose text is the owner groups written
// out (orchestrator._compliance_plain_text): those groups are drawn as their own cards, and
// drawing the text as well said each of them twice (28 Oct 2026).
const bare = (s) => String(s || '').replace(/[*_`#>|\s]/g, '');
export function narrativeIsLead(rich, answer) {
  const said = bare(answer);
  if (!said || !rich) return false;
  if (arr(rich.groups).some((g) => g && bare(g.headline) && said.includes(bare(g.headline)))) return false;
  const n = bare(rich.narrative);
  return !n || said.startsWith(n) || String(answer).split(/\n\s*\n/).some((p) => bare(p).startsWith(n));
}
function narrativeOf(rich, answer) {
  return narrativeIsLead(rich, answer) ? String(answer) : rich.narrative || '';
}

export function programFromCompliance(rich, answer = '') {
  if (!rich) return '';
  const w = programWriter();
  const kids = [
    ...markdownChildren(w, narrativeOf(rich, answer)),
    ...kpis(w, rich),
    ...overdue(w, rich),
    ...actions(w, rich),
    ...groups(w, rich),
    ...insights(w, rich),
    ...certificates(w, rich)
  ];
  return w.done(kids);
}
