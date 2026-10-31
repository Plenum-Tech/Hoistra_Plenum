// home — the lab's landing page as a view model. summarize() turns the replies Trace Lab already serves (golden
// sets, golden runs, progress, unfiled notes, failure modes, judges, monitoring batches) into one summary; homeView()
// turns that into one card per stage of the loop and the single next step. Unknown stays null and reads "—".

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
const DASH = '—';
const LIVE = new Set(['candidate', 'confirmed', 'frozen']);

export function summarize(r) {
  const x = r || {};
  const sets = x.sets;
  const run = x.runs && Array.isArray(x.runs.runs) ? x.runs.runs.find((y) => y && y.pass_rate) || null : null;
  const oc = x.progress && x.progress.open_coding;
  const modes = x.modes && Array.isArray(x.modes.modes) ? x.modes.modes : null;
  const judges = x.judges && Array.isArray(x.judges.judges) ? x.judges.judges : null;
  return {
    hasGolden: sets ? !!sets.active : null,
    goldenName: sets && sets.active ? sets.active.name || null : null,
    lastRun: run ? { passes: num(run.pass_rate.passes), graded: num(run.pass_rate.graded) } : null,
    reviewed: oc ? num(oc.reviewed) : null,
    wrong: oc ? num(oc.wrong) : null,
    missing: oc ? num(oc.missing) : null,
    openBatches: x.progress ? num(x.progress.open_batches) : null,
    unfiled: x.notes && Array.isArray(x.notes.notes) ? x.notes.notes.length : null,
    modes: modes ? { live: modes.filter((m) => LIVE.has(m.status)).length, confirmed: modes.filter((m) => m.status === 'confirmed' || m.status === 'frozen').length } : null,
    judges: judges ? judges.length : null,
    frozen: judges ? judges.filter((j) => j.frozen_at || j.status === 'frozen').length : null,
    batches: x.batches && Array.isArray(x.batches.batches) ? x.batches.batches.length : null
  };
}

const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

function nextStep(s) {
  if (s.hasGolden === null && s.missing === null) return { text: 'Reading where things stand…', tab: null, button: null };
  if (s.hasGolden === false) return { text: 'Upload a golden set: the questions with the answers you know are right.', tab: 'golden', button: 'Open the golden set' };
  if (s.missing) return { text: plural(s.missing, 'answer is', 'answers are') + ' waiting for your verdict.', tab: 'map', button: 'Start reviewing' };
  if (s.unfiled) return { text: plural(s.unfiled, 'note on a wrong answer is', 'notes on wrong answers are') + ' not filed under a failure mode yet.', tab: 'progress', button: 'File the notes' };
  if (s.modes && !s.modes.live) return { text: 'Group the wrong answers by cause: propose the first failure mode.', tab: 'progress', button: 'Open progress' };
  if (s.modes && s.modes.confirmed && !s.frozen) return { text: 'Build a judge for a confirmed failure mode, so it can be checked automatically.', tab: 'judges', button: 'Open judges' };
  if (s.frozen && !s.batches) return { text: 'A judge is frozen: start watching this company week by week.', tab: 'watch', button: 'Open watch' };
  return { text: 'Draw the next review batch to keep the picture current.', tab: 'map', button: 'Open the map' };
}

export function homeView(s) {
  const run = s.lastRun;
  return {
    next: nextStep(s),
    stages: [
      { name: 'Check', tab: 'golden', big: run ? run.passes + ' / ' + run.graded + ' right' : DASH,
        line: s.hasGolden ? 'Last golden run on ' + (s.goldenName || 'the active set') + '.' : s.hasGolden === false ? 'No golden set yet.' : 'Golden set and runs.' },
      { name: 'Explain', tab: 'progress', big: s.reviewed === null ? DASH : s.reviewed + ' reviewed',
        line: s.wrong === null ? 'Answers people have judged.' : plural(s.wrong, 'marked wrong', 'marked wrong') + (s.missing ? ', ' + s.missing + ' waiting' : '') + '.' },
      { name: 'Group', tab: 'progress', big: s.modes ? plural(s.modes.live, 'failure mode', 'failure modes') : DASH,
        line: s.modes ? s.modes.confirmed + ' confirmed' + (s.unfiled ? ', ' + s.unfiled + ' notes unfiled' : '') + '.' : 'Wrong answers grouped by cause.' },
      { name: 'Fix', tab: 'judges', big: s.judges === null ? DASH : plural(s.judges, 'judge', 'judges'),
        line: s.frozen === null ? 'Automatic checks for each failure mode.' : s.frozen + ' frozen and trusted.' },
      { name: 'Watch', tab: 'watch', big: s.batches === null ? DASH : plural(s.batches, 'weekly check', 'weekly checks'),
        line: 'How often each failure happens, week by week.' }
    ]
  };
}
