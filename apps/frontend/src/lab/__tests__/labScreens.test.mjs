// labScreens — the lab's own screens rendered to markup: the sign-in in Hoistra's words with labelled fields, the
// code step, the not-allowed screen, the platform team's picker (Platform view first, the chosen place checked), the
// workspace menu grouped by the loop with Home first, and Home's next step and stage cards.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const L = await bundle('../lab/LabApp.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

test('sign in: Hoistra\'s account, labelled fields, a link back to Hoistra, and the error said aloud', () => {
  const out = html(L.SignIn, { onSubmit: () => {}, onCode: () => {}, error: 'That email or password is not right.' });
  assert.ok(out.includes('Sign in to Trace Lab'));
  assert.ok(out.includes('Use your Hoistra email and password. There is no separate Trace Lab account.'));
  assert.match(out, /<label class="lab-field"><span>Email<\/span><input type="email" autoComplete="username"/);
  assert.match(out, /type="password" autoComplete="current-password"/);
  assert.match(out, /role="alert"[^>]*>That email or password is not right\./);
  assert.ok(out.includes('Open Hoistra'));
  const code = html(L.SignIn, { code: true, email: 'a@x.com', onSubmit: () => {}, onCode: () => {} });
  assert.ok(code.includes('We sent a code to a@x.com'));
  assert.match(code, /autoComplete="one-time-code"/);
});

test('not allowed: who signed in, why, and the way out', () => {
  const out = html(L.Denied, { user: { email: 'maria@northbridge.example' }, onSignOut: () => {} });
  assert.ok(out.includes('Trace Lab is for company admins and the platform team'));
  assert.ok(out.includes('maria@northbridge.example'));
  assert.ok(out.includes('Open Hoistra') && out.includes('Sign out'));
});

test('the picker: Platform view first, then each company, the chosen one checked', () => {
  const out = html(L.Picker, { companies: [{ id: 'o1', name: 'Plenum Technologies' }, { id: 'o2', name: 'Northbridge Estates Ltd' }],
    chosen: { id: 'o2', name: 'Northbridge Estates Ltd' }, onChoose: () => {} });
  const names = [...out.matchAll(/<strong>([^<]*)<\/strong>/g)].map((m) => m[1]);
  assert.deepEqual(names, ['Platform view', 'Plenum Technologies', 'Northbridge Estates Ltd']);
  assert.match(out, /role="radiogroup" aria-label="Where to work"/);
  assert.equal((out.match(/aria-checked="true"/g) || []).length, 1);
  assert.match(out, /aria-checked="true"[^>]*><span><strong>Northbridge Estates Ltd/);
  assert.ok(out.includes('Open Northbridge Estates Ltd'));
  assert.ok(html(L.Picker, { companies: null, onChoose: () => {} }).includes('Reading the companies'));
});

test('the menu: Sessions, Runs, Synthetic, Labels, Progress, Loop, Notes and Learning, plus People for admins; a question code names its run', () => {
  // Sessions first (11 Nov 2026); Runs is back after it for the questions people ask by hand.
  const WORK = ['sessions', 'runs', 'synthetic', 'labels', 'progress', 'loop', 'notes', 'learning'];
  assert.deepEqual(L.navFor({ manages: false }).map((x) => x[0]), WORK);
  assert.deepEqual(L.navFor({ manages: true }).map((x) => x[0]), WORK.concat(['people']));
  assert.deepEqual(L.navFor(null).map((x) => x[1]),
    ['Sessions', 'Runs', 'Synthetic', 'Labels', 'Progress', 'Loop', 'Notes', 'Learning']);
  assert.equal(L.runOf('r3-12'), 'R3');
  assert.equal(L.runOf('R3'), null);
});

test('the top bar: every run the person may see, newest first, and Sync now', () => {
  const out = html(L.RunPicker, { runs: [{ code: 'R4', title: 'ann@a.test · 2026-10-21', n_questions: 3 },
    { code: 'R3', title: 'support batch', n_questions: 250 }], run: 'R3', onRun() {}, onSync() {}, syncing: false, synced: '2 new questions' });
  assert.ok(out.includes('All runs') && out.includes('R4 · ann@a.test · 2026-10-21 · 3 questions') && out.includes('R3 · support batch · 250 questions'));
  assert.match(out, /<option value="R3" selected="">/);
  assert.ok(out.includes('Sync now') && out.includes('2 new questions'));
});

test('no access: who signed in and that their company admin turns Trace Lab on in Hoistra; a turned-off person is told so', () => {
  const out = html(L.Waiting, { me: { email: 'tester@x.test', status: 'none' }, onSignOut: () => {}, onRetry: () => {} });
  assert.ok(out.includes('You do not have access to Trace Lab') && out.includes('tester@x.test') && out.includes('Users &amp; access'));
  assert.ok(out.includes('Check again') && out.includes('Sign out'));
  const gone = html(L.Waiting, { me: { email: 'x@y', status: 'removed' }, onSignOut: () => {}, onRetry: () => {} });
  assert.ok(gone.includes('turned off') && !gone.includes('Check again'));
});

test('the platform team picks a company only (no platform view) when labelling', () => {
  const out = html(L.Picker, { companies: [{ id: 'o1', name: 'Plenum Technologies' }], companiesOnly: true, onChoose: () => {} });
  const names = [...out.matchAll(/<strong>([^<]*)<\/strong>/g)].map((m) => m[1]);
  assert.deepEqual(names, ['Plenum Technologies']);
});

test('Synthetic is a page of its own, and the lab app renders it', () => {
  const src = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes("import Synthetic from './Synthetic.jsx'"));
  assert.ok(src.includes("current === 'synthetic' ? <Synthetic instance={instance} onPick={pickInstance} org={orgOfRun} />"),
    'the menu names a page nothing draws, so opening it shows nothing');
});

test('it asks the service rather than making questions up in the browser', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  for (const call of ['labApi.spaces(', 'labApi.generate(', 'labApi.runScenarios(']) {
    assert.ok(src.includes(call), 'the page does not call ' + call);
  }
  assert.ok(!/BY_ENGINE|const QUESTIONS/.test(src),
    'a question list in the browser is a second copy of the generator, free to drift from the '
    + 'one the service verifies against the real router');
});

test('the instance is sent with every call', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  const calls = src.split(String.fromCharCode(10)).filter((l) => l.includes('labApi.generate(') || l.includes('labApi.runScenarios('));
  assert.ok(calls.length >= 2);
  for (const c of calls) assert.ok(c.includes('space') || src.includes('{ space,'), c);
});

test('the lab api names the three scenario routes once', () => {
  const src = readFileSync(new URL('../labApi.js', import.meta.url), 'utf-8');
  for (const path of ['/scenarios/spaces', '/scenarios/generate', '/scenarios/run']) {
    // The whole quoted path: the eval store's /eval/scenarios/generate (11 Nov 2026) is another route, not a second naming.
    assert.equal(src.split("'" + path + "'").length - 1, 1, path + ' is named more than once, or not at all');
  }
});


// The instance is one setting with one place to change it (3 Nov 2026). Its own page was removed on 6 Nov 2026
// (Bala): Synthetic's select is the one control, and the choice is still kept for the browser tab.
test('the instance is chosen on Synthetic and kept for the tab; there is no Instance page', () => {
  const app = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(!app.includes("current === 'instance'") && !app.includes("['instance', 'Instance']"), 'the Instance page is back');
  assert.ok(app.includes("import { readInstance, writeInstance } from './Instances.jsx'"));
  assert.ok(app.includes('<Synthetic instance={instance} onPick={pickInstance} org={orgOfRun} />'),
    'Synthetic would pick its own instance again, and the two controls would disagree');
});

test('the instance is selectable on Synthetic, and it is the same setting', () => {
  // One setting, kept by LabApp: the select calls LabApp's pickInstance, which also remembers it for the tab.
  // A local useState here would be a second setting, and that is the thing to keep out.
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('onChange={(e) => onPick(e.target.value)}'),
    'the select does not write back to the shared instance');
  assert.ok(src.includes('const space = instance;'), 'the page keeps its own instance again');
  assert.ok(!/useState\(['"](ops|comp|assets|full)['"]\)/.test(src),
    'a second source of truth for the instance');
});

test('a batch is cleared when the instance changes', () => {
  // Ops Brain questions must not sit under a Compliance Engine heading.
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(/setBatch\(null\); setRun\(null\); setError\(''\); \}, \[instance\]\)/.test(src));
});

test('the instances come from the product, not a copy in the lab', () => {
  const src = readFileSync(new URL('../Instances.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes("from '../logic/instances.js'"),
    'a copy here could accept an instance the product no longer has');
  assert.ok(!/Ops Brain'[,:]/.test(src.replace(/DEFAULT_INSTANCE[\s\S]*?;/, '')));
  assert.ok(!/export default/.test(src), 'the Instance page is back');
});

test('an unknown stored instance falls back rather than generating for nothing', () => {
  const src = readFileSync(new URL('../Instances.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('INSTANCES.some((i) => i.key === v)'));
  assert.ok(src.includes('catch'), 'sessionStorage throws in a private window');
});


// Asking reads one company's records, so it cannot be pressed without one (3 Nov 2026). Before
// this the button posted, the service refused, and "Choose a company to view as first" appeared
// at the top of the page, away from the button and worded like a display setting.
test('Ask them for real is refused in the page, not by the service', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  // Matched loosely: the button grew a second reason to be disabled (the batch running out),
  // and the point of this test is the company, not the exact expression.
  assert.ok(/disabled=\{!!busy \|\| !asking/.test(src), 'the button can still be pressed with no company');
  assert.ok(src.includes('Choose a company above first'), 'the reason is not said beside the button');
});

test('Generate is not blocked by the same thing', () => {
  // Generation reads no company data; the service lets the platform view do it, and so must this.
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  const gen = src.slice(src.indexOf('onClick={generate}') - 200, src.indexOf('onClick={generate}'));
  assert.ok(!gen.includes('!org'), 'Generate was blocked on a company it does not need');
});

test('the company travels with the ask', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(/labApi\.runScenarios\(\{[^;]*\}, asking\)/s.test(src),
    'the request is sent without the company');
  const api = readFileSync(new URL('../labApi.js', import.meta.url), 'utf-8');
  assert.ok(api.includes('runScenarios: (body, org)'), 'labApi drops the company');
});

test('the company comes from the run that is open', () => {
  const app = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(app.includes('orgOfRun'), 'nothing works out which company the lab is in');
  assert.ok(app.includes('org={orgOfRun}'), 'Synthetic is never told the company');
});


// The company is chosen on the page, because asking is not about a run (3 Nov 2026).
test('the company is picked on the page, not inherited from whichever run is open', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('labApi.companies()'), 'nothing reads the companies');
  assert.ok(src.includes("id=\"synthetic-company\""), 'there is no company control');
  assert.ok(src.includes('const asking = company || org'),
    'the picked company must win, with the open run as the fallback');
});

test('one company is chosen for you rather than offered as a choice', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('if (rows.length === 1) setCompany(rows[0].id)'));
  assert.ok(src.includes('companies.length > 1 ?'), 'a company admin should not see a one-item picker');
});


// Working a batch through a few at a time (3 Nov 2026). A batch is 10 or 50 questions and one
// ask is capped at 5, so the only way through one is in slices -- and the slices have to behave
// as one batch, both on screen and in the database, or a reviewer annotates what looks like
// several small unrelated runs.
test('the next slice is asked from where the last one stopped', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(/offset:\s*from/.test(src), 'every ask starts at the top of the batch again');
  assert.ok(/batch:\s*Number\(count\)/.test(src),
    'without the batch size the service generates a SHORTER batch, which is a different batch: '
    + 'the refusal share is a fraction of the total');
  assert.ok(src.includes('setAsked(0)'), 'Generate must put the position back to the start');
});

test('the answers already on screen are not thrown away by the next ask', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('if (!asked) setRun(null);'),
    'clearing on every ask hides the questions the next few are meant to be added under');
  assert.ok(/results:\s*\(prev\.results \|\| \[\]\)\.concat/.test(src),
    'the new answers replace the old ones instead of being added below them');
});

test('the button says where in the batch it has got to, and stops at the end', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes("'Ask the next '"), 'it still offers to ask the FIRST few after some were asked');
  assert.ok(/left === 0/.test(src), 'asking past the end returns nothing and reads as a failure');
  assert.ok(/const take = asked \? Math\.min\(Number\(askCount\), left\)/.test(src),
    'the last slice must ask only what is left of the batch');
});

test('a saved batch says where to go and annotate it', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('run.run_code'), 'the page never names the run the answers were saved as');
  assert.ok(!src.includes('Answers are shown here and not stored'),
    'the page still says the answers are not kept, which has not been true since they are saved as a run');
  assert.ok(/run\.persisted === false/.test(src),
    'a failed write must be reported as a failure, not inferred from a missing flag');
});


// Learning (3 Nov 2026): what the annotations add up to, and where it goes. The loop the feedback
// asked for is annotate -> learn -> the skill document the agents read. This page is the middle of
// it, and the part that matters most is that it never claims to be the end.
test('Learning is a page of its own, and the lab app renders it', () => {
  const app = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(app.includes("import Learning from './Learning.jsx'"));
  assert.ok(app.includes("current === 'learning' ? <Learning"),
    'the menu names a page nothing draws, so opening it shows nothing');
  assert.deepEqual(L.navFor(null).map((x) => x[1]),
    ['Sessions', 'Runs', 'Synthetic', 'Labels', 'Progress', 'Loop', 'Notes', 'Learning']);
});

test('the draft comes from the service, not from the browser', () => {
  const src = readFileSync(new URL('../Learning.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('labApi.learningDraft('), 'the page does not ask for the draft');
  const api = readFileSync(new URL('../labApi.js', import.meta.url), 'utf-8');
  assert.equal(api.split('/learning/draft').length - 1, 1, 'the route is named more than once, or not at all');
});

test('a draft can be narrowed to one run', () => {
  const api = readFileSync(new URL('../labApi.js', import.meta.url), 'utf-8');
  assert.ok(/learningDraft: \(run, org\) =>.*run \? \{ run \}/.test(api),
    'every draft covers every run, so one run cannot be read on its own');
});

test('the platform view drafts for the company of the run in view (10 Nov 2026)', () => {
  // The platform team works in "All companies", and the draft is one company's: the request went without a company and
  // the service answered 400 "Choose a company to view as first", so the page never drew.
  const api = readFileSync(new URL('../labApi.js', import.meta.url), 'utf-8');
  assert.ok(/learningDraft: \(run, org\) =>[^\n]*\borg\b[^\n]*\)/.test(api.split('\n').find((l) => l.includes('learningDraft:')) || ''),
    'the company is not sent with the draft');
  const app = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(/<Learning orgFor=\{/.test(app), 'the lab app does not tell the page whose company a run is');
  const src = readFileSync(new URL('../Learning.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('labApi.learningDraft(code || undefined, orgFor'), 'the page does not send the run\'s company');
  assert.ok(src.includes('Open a run first'), 'with no company the page shows the raw refusal instead of what to do');
});

test('the page says it applies nothing, and where the draft has to go', () => {
  // The whole risk of this page is somebody annotating a run, reading a tidy draft and believing
  // the agents have been taught something. They have not: Trace Lab may never write the documents
  // the agents read.
  const src = readFileSync(new URL('../Learning.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('This page applies nothing'), 'the page never says it applies nothing');
  assert.ok(src.includes('Skill lab'), 'it does not say where the draft is applied');
  assert.ok(!/applies_anything.*true/.test(src));
});

test('the grouping by skill document is labelled as a suggestion', () => {
  const src = readFileSync(new URL('../Learning.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('suggested'), 'a guessed document shown as a decision files lessons against the wrong agent');
  assert.ok(src.includes('by_skill'), 'the page never shows the per-document grouping');
});

test('the .md is saved from what is already on the page', () => {
  // An <a href> cannot carry the lab's bearer, so a link to the file route would download a 401.
  const src = readFileSync(new URL('../Learning.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('URL.createObjectURL'), 'the download does not come from the loaded markdown');
  assert.ok(!src.includes('learningDraftFileUrl'), 'a plain link to the file route downloads a 401');
});


// Cross-domain questions (3 Nov 2026). One question, three lookups — the shape an answer fails
// quietly, by covering the first part well and dropping the rest. A reviewer cannot catch that
// without knowing what the parts were, so the page has to carry them.
test('a question that needs more than one area shows which, and what to cover', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes("'Needs ' + r.needs.join(' + ')"), 'the batch table never says a question has parts');
  assert.ok(src.includes("Must cover: "), 'a reviewer is shown a three-part question with nothing to judge it against');
});

test('the batch summary counts them', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('batch.cross_domain'),
    'a batch with none of these measures only the easy half of what people ask, and says nothing');
});

test('the answers carry the parts too, not just the batch table', () => {
  // The answer is where the dropped part actually shows, so this is where it matters most.
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  const answers = src.slice(src.indexOf("{'Answers'}"));
  assert.ok(answers.includes("' · needs ' + r.needs.join(' + ')"));
  assert.ok(answers.includes("'Must cover: ' + r.covers"));
});


// A batch does not refresh on its own — deliberately, so one being read does not change under the
// reader. That means a batch generated before a deploy sits on screen looking current, which is
// exactly what happened on 3 Nov: the same batch was read as the new one three times over.
test('the batch line says which batch it is and when it was made', () => {
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes("' · seed ' + batch.seed"), 'the seed is what reproduces a batch and names it');
  assert.ok(src.includes('batch.made_at'), 'nothing on screen says when the batch was made');
  assert.ok(src.includes('batch.fingerprint'));
});

test('the time is stamped when the page receives the batch, not read from the reply', () => {
  // It is the moment THIS page got it, which is what somebody looking at a stale table needs.
  const src = readFileSync(new URL('../Synthetic.jsx', import.meta.url), 'utf-8');
  assert.ok(/Object\.assign\(\{\}, made, \{ made_at: Date\.now\(\) \}\)/.test(src),
    'the stamp must be the page\'s own, and must not be overwritten by the reply');
});
