// What the platform did with a finished migration's contract terms, invoices and plant
// telemetry, in the words both pages use: the Migration page's chip on one run and the
// Vendors page's highlight on the company's latest. The platform reads a finished run
// itself (svc-operations-intelligence workbook_extras_runner); these only report it.

const n = (v) => (typeof v === 'number' ? v.toLocaleString('en-GB') : String(v || 0));
const plural = (k, one, many) => n(k) + ' ' + (k === 1 ? one : many);

// The counts line of a finished read.
export function extrasCounts(summary) {
  const s = summary || {};
  const bits = [];
  if (s.contracts) bits.push(plural(s.contracts, 'contract', 'contracts') + ' read into draft terms');
  if (s.invoices) {
    bits.push(plural(s.invoices, 'invoice', 'invoices') + ' verified'
      + (s.lines_held ? ', ' + plural(s.lines_held, 'line', 'lines') + ' held for your decision' : ''));
  }
  const tel = s.telemetry || {};
  const t = [tel.chiller_readings ? plural(tel.chiller_readings, 'chiller reading', 'chiller readings') : '',
    tel.degree_days ? plural(tel.degree_days, 'month', 'months') + ' of degree days' : '',
    tel.bms_samples ? plural(tel.bms_samples, 'BMS sample', 'BMS samples') : ''].filter(Boolean);
  if (t.length) bits.push('telemetry: ' + t.join(', '));
  if (s.skipped) bits.push(n(s.skipped) + ' skipped' + (s.skip_reasons && s.skip_reasons.length ? ' (' + s.skip_reasons.join(', ') + ')' : ''));
  return bits.join(' · ');
}

// One run's status as a chip: { show, tone, label, detail, action } - tone is ok | warn | risk
// | muted; action is the button's words, or '' for none. `st` is the status row the service
// answers ({status, summary, error, attempts}), { loading } while it is being read, or
// { error } when the read itself failed.
export function extrasStatus(st) {
  if (!st) return { show: false, tone: 'muted', label: '', detail: '', action: '' };
  if (st.loading) return { show: true, tone: 'warn', label: 'Checking', detail: "Asking the platform what it did with this run's contract terms, invoices and telemetry…", action: '' };
  if (st.error && !st.status) return { show: true, tone: 'risk', label: 'Not known', detail: 'The status could not be read — ' + st.error, action: 'Read now' };
  const counts = extrasCounts(st.summary);
  switch (st.status) {
    case 'waiting':
      return { show: true, tone: 'warn', label: 'Waiting', detail: 'The platform reads the contract terms, invoices and telemetry within a minute of the run finishing.', action: 'Read now' };
    case 'running':
      return { show: true, tone: 'warn', label: 'Reading', detail: 'Reading the contract terms, invoices and plant telemetry the workbook carried…', action: '' };
    case 'done':
      return { show: true, tone: 'ok', label: 'Done', detail: counts || 'Read.', action: 'Read again' };
    case 'none':
      return { show: true, tone: 'muted', label: 'Nothing to read', detail: (st.summary && st.summary.reason) || 'The workbook carried no contract terms, invoices or telemetry.', action: '' };
    case 'failed':
      return { show: true, tone: 'risk', label: 'Failed', detail: (st.error || 'The read failed and gave no reason.') + (st.attempts ? ' · attempt ' + st.attempts + ' of 3 — the platform retries on its own' : ''), action: 'Try again' };
    case 'predates':
      return { show: true, tone: 'muted', label: 'Not read', detail: 'This run finished before the platform read runs itself.', action: 'Read now' };
    default:
      return { show: false, tone: 'muted', label: '', detail: '', action: '' };
  }
}

// The Vendors page highlight: the latest read and what is left for a person to do.
// { show, tone, title, detail, next } from GET /workbook-extras/latest.
export function vendorsExtrasBanner(latest) {
  const run = latest && latest.run;
  if (!run) return { show: false, tone: 'muted', title: '', detail: '', next: '' };
  const st = extrasStatus(run);
  const file = run.source_filename ? ' · ' + run.source_filename : '';
  const drafts = Number(latest.drafts_awaiting) || 0;
  const next = run.status === 'done' && drafts
    ? plural(drafts, 'contract waits', 'contracts wait') + ' for a named person to confirm — confirm each on its vendor, then Rebuild scorecards.'
    : run.status === 'done' && latest.confirmed ? plural(latest.confirmed, 'contract', 'contracts') + ' confirmed. Rebuild scorecards to score against them.'
      : '';
  return { show: true, tone: drafts && run.status === 'done' ? 'warn' : st.tone, title: 'Latest migration: ' + st.label + file, detail: st.detail, next };
}
