// runSessions — the Sessions tab moved into Runs (14 Nov 2026, Hussain). The menu has no Sessions; a "Sessions · <tag>"
// run and a person's live day run open, inside Runs, as one row per conversation (the Sessions list's own table), and a
// row opens the same session page. A question whose conversation is not filed yet is a "Reading in…" row that cannot be
// opened. Typed and imported runs keep their questions view. Runs holds the Read buttons and a Scenarios view, and an
// old ?view=sessions link lands in Runs (on its session when it names one).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const L = await bundle('../lab/LabApp.jsx');
const R = await bundle('../lab/Runs.jsx');
const SES = await bundle('../lab/Sessions.jsx');
const { sessionRow } = await import('../logic/sessions.js');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));
const src = (f) => readFileSync(new URL('../' + f, import.meta.url), 'utf-8');

const SESSIONS_RUN = { code: 'R47', title: 'Sessions · frontend_nov13_202718', kind: 'synthetic', source: 'sync', n_questions: 40 };
const LIVE_RUN = { code: 'R48', title: 'aasim@x.test · 2026-11-13', kind: 'production', source: 'sync', owner_email: 'aasim@x.test',
  run_day: '2026-11-13', n_questions: 3 };
const TYPED_RUN = { code: 'R3', title: 'Certificates batch', kind: 'synthetic', source: 'excel', n_questions: 12 };
const CURATOR = { manages: true, all_companies: false, user_id: 'me' };
const MEMBER = { manages: false, all_companies: false, user_id: 'me' };
const ROW = { id: '2db72d10-e565-46b4-b0b6-5ff51304c586', run_tag: 'chat', scenario_id: null, attempt: 1, origin: 'hoistra-prod',
  source: 'chat', type: null, turns: 2, status: 'ok', started_at: '2026-11-13T09:00:00.000Z', stale: false, expected_flagged: 0,
  scores: { logic: null, ux: null, domain: null }, codes: ['R48-1', 'R48-2'], turn_ids: ['turn-a1', 'turn-a2'] };
const WAITING = { code: 'R48-3', question: 'Which vendors are blocked?', asked_at: '2026-11-13T10:00:00.000Z', turn_id: 'turn-b1' };

test('the menu has no Sessions: Runs, Labels, Progress, Loop, Notes and Learning, plus People; Runs is the home page', () => {
  const WORK = ['runs', 'labels', 'progress', 'loop', 'notes', 'learning'];
  assert.deepEqual(L.navFor({ manages: false }).map((x) => x[0]), WORK);
  assert.deepEqual(L.navFor({ manages: true }).map((x) => x[0]), WORK.concat(['people']));
  assert.equal(L.HOME_VIEW, 'runs');
  const app = src('LabApp.jsx');
  assert.ok(/\/\*\s*\['sessions', 'Sessions'\],?\s*\*\//.test(app), 'commented out like Synthetic, so it can come back');
  assert.ok(!/^import Sessions from/m.test(app), 'the Sessions page is not drawn');
  assert.ok(!/^\s*\{current === 'sessions'/m.test(app));
});

test('a sessions run and a live run open as conversations; a typed or imported run opens as questions', () => {
  assert.equal(R.runMode(SESSIONS_RUN), 'sessions');
  assert.equal(R.runMode(LIVE_RUN), 'live');
  assert.equal(R.runMode(TYPED_RUN), 'questions');
  assert.equal(R.runMode({ ...TYPED_RUN, source: 'manual' }), 'questions');
  assert.equal(R.runMode({ ...SESSIONS_RUN, source: 'excel' }), 'questions', 'a typed batch named "Sessions · …" is a run');
  assert.equal(R.runMode(null), null, 'not known yet');
  const conv = html(R.default, { me: CURATOR, run: 'R47', known: [SESSIONS_RUN, TYPED_RUN], onRun() {} });
  assert.ok(conv.includes('aria-label="Conversations in R47"') && conv.includes('Reading the conversations'), conv);
  const live = html(R.default, { me: CURATOR, run: 'R48', known: [LIVE_RUN], onRun() {} });
  assert.ok(live.includes('aria-label="Conversations in R48"'));
  const typed = html(R.default, { me: CURATOR, run: 'R3', known: [TYPED_RUN], onRun() {} });
  assert.ok(typed.includes('Questions in R3') && typed.includes('Pick a question') && !typed.includes('Conversations in'));
});

test('the conversation list: the Sessions table, one row per conversation, the way back, and a waiting row that cannot be opened', () => {
  const out = html(R.RunConversationsList, { run: LIVE_RUN, mode: 'live', sessions: [ROW], waiting: [WAITING], onOpen() {}, onBack() {} });
  ['← All runs', '>R48<', 'aasim@x.test · 2026-11-13', '<th scope="col">Session</th>', '<th scope="col">Run tag</th>',
    '<th scope="col">Type</th>', '<th scope="col">Turns</th>', '<th scope="col">Status</th>', '<th scope="col">Started</th>',
    ROW.id, 'asked on hoistra-prod', '2 turns', 'Which vendors are blocked?', 'R48-3', 'Reading in…']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.equal((out.match(/class="lab-link lab-ses-open"/g) || []).length, 1, 'only the filed conversation opens');
  assert.ok(!/<button[^>]*>Which vendors are blocked\?</.test(out), 'the waiting row is not a button');
  assert.ok(!out.includes('Show every attempt'), 'a live day has no attempts');
  const ses = html(R.RunConversationsList, { run: SESSIONS_RUN, mode: 'sessions', sessions: [], waiting: [], onOpen() {}, onBack() {} });
  assert.ok(ses.includes('Show every attempt'));
  assert.ok(ses.includes('No sessions'), 'says so when there is nothing');
  assert.ok(html(R.RunConversationsList, { run: LIVE_RUN, mode: 'live', sessions: null, waiting: [] }).includes('Reading the conversations'));
});

test('opening a row shows the session page inside Runs, with the way back to the run', () => {
  const out = html(R.default, { me: CURATOR, run: 'R48', known: [LIVE_RUN], session: ROW.id, onRun() {}, onSession() {} });
  assert.ok(out.includes('Reading the session…') && out.includes('← R48'), out);
  assert.ok(src('Runs.jsx').includes('<SessionPage '), 'the same session page as before');
  // A question named by a link (Notes, Labels, Loop) opens the conversation it is a turn of, on that turn.
  const data = { sessions: [ROW], waiting: [WAITING] };
  assert.deepEqual(R.sessionForQuestion(data, 'R48-2'), { id: ROW.id, turn: 'turn-a2' });
  assert.equal(R.sessionForQuestion(data, 'R48-3'), null, 'not filed yet');
  assert.equal(R.sessionForQuestion(null, 'R48-2'), null);
});

test('the session page opens on the turn it is given', () => {
  assert.ok(/export default function SessionPage\(\{[^}]*\bturn\b/.test(src('SessionPage.jsx')));
  assert.ok(src('SessionPage.jsx').includes('tabs.some((t) => t.traceId === turn) ? turn : tabs[0].traceId'));
});

// 14 Nov 2026 (Hussain): "I only need one button which syncs everything together, the codex sessions and the live".
test('Runs has one Sync now for everything (the Read buttons are commented out) and a Runs | Scenarios switch', () => {
  const cur = html(R.default, { me: CURATOR, onRun() {} });
  ['Import Excel/CSV', 'New run'].forEach((t) => assert.ok(cur.includes(t), t));
  assert.equal((cur.match(/>Sync now</g) || []).length, 0, 'the one Sync now is in the top bar, on every page');
  assert.ok(!cur.includes('Read new sessions') && !cur.includes('Read chat questions'), 'no Read buttons any more');
  assert.ok(/role="tab"[^>]*aria-selected="true"[^>]*>Runs</.test(cur) && /role="tab"[^>]*aria-selected="false"[^>]*>Scenarios</.test(cur));
  const app = src('LabApp.jsx');
  assert.equal((app.match(/'Sync now'/g) || []).length, 1, 'one Sync now button in the whole lab: the top bar');
  assert.ok(/key=\{'v' \+ stamp\}/.test(app), 'a sync reloads the page under it, so the Runs list shows what came in');
  const runs = src('Runs.jsx');
  assert.ok(/\{\/\*[^]*'Read new sessions'[^]*'Read chat questions'[^]*\*\/\}/.test(runs), 'commented out, kept to bring back');
  const scen = html(R.RunsHome, { me: CURATOR, onRun() {}, startTab: 'scenarios' });
  assert.ok(scen.includes('aria-label="Scenario actions"') && !scen.includes('Search runs'));
});

test('the Runs page and the top bar Sync now are the same call and say the same thing', () => {
  const runs = src('Runs.jsx');
  const app = src('LabApp.jsx');
  assert.ok(app.includes('labApi.syncRuns()'));
  assert.ok(/syncLines\(out\)/.test(app), 'one way of saying what it did');
  assert.ok(app.includes("syncing ? 'Syncing…' : 'Sync now'") && /disabled=\{syncing\}/.test(app));
  assert.ok(/\{\/\*[^]*onClick=\{runSync\}[^]*\*\/\}/.test(runs), 'the Runs page button is commented out, kept to bring back');
});

test('what Sync now did, in one line: zero parts left out, "Up to date" when nothing is new, a failed part said', () => {
  assert.deepEqual(R.syncLines({ new_questions: 3, chat_sessions: 2, codex_sessions: 5, new_runs: 1, failed: {}, skipped: {} }),
    { line: '3 new questions · 2 conversations · 5 Codex sessions · 1 new run', problems: [] });
  assert.equal(R.syncLines({ new_questions: 1, chat_sessions: 0, codex_sessions: 0, new_runs: 0 }).line, '1 new question');
  assert.equal(R.syncLines({ new_questions: 0, chat_sessions: 0, codex_sessions: 0, new_runs: 0, failed: {}, skipped: {} }).line, 'Up to date');
  const part = R.syncLines({ new_questions: 2, failed: { codex: 'the scenario store is busy', chat: 'timed out' },
    skipped: {} });
  assert.equal(part.line, '2 new questions');
  assert.deepEqual(part.problems, ['Chat conversations could not be read: timed out',
    'Codex sessions could not be read: the scenario store is busy']);
  assert.deepEqual(R.syncLines({ new_questions: 0, skipped: { codex: 'Codex sessions are read in by company admins.' } }),
    { line: 'Up to date', problems: ['Codex sessions are read in by company admins.'] });
  assert.ok(R.syncLines({ new_questions: 2000, more: true }).line.endsWith('· more to come: press Sync now again'));
  assert.equal(R.syncText({ new_questions: 2, failed: { runs: 'no lock' } }), '2 new questions. Runs could not be made: no lock');
});

test('an old ?view=sessions link lands in Runs, on the session it names', () => {
  assert.deepEqual(L.placeOf('?view=sessions&session=scn__a__SD-1__1'),
    { view: 'runs', run: null, question: null, session: 'scn__a__SD-1__1', tag: null });
  assert.deepEqual(L.placeOf('?view=sessions&tag=frontend_nov13'), { view: 'runs', run: null, question: null, session: null, tag: 'frontend_nov13' });
  assert.equal(L.placeOf('?view=sessions').view, 'runs');
  assert.equal(L.startView(L.placeOf('?view=sessions')), 'runs');
  assert.equal(L.startView(L.placeOf('')), 'runs');
  assert.equal(L.placeQuery('runs', null, null, 'scn__a__SD-1__1'), 'view=runs&session=scn__a__SD-1__1');
  assert.equal(L.placeQuery('runs', 'R48', null, 'x-1'), 'view=runs&run=R48&session=x-1');
  assert.equal(L.placeQuery('labels', 'R3', null, 'x-1'), 'view=labels&run=R3', 'a session belongs to Runs only');
  assert.equal(L.placeQuery('runs', null, null, null), '');
  // The session named with no run opens on its own, the way back going to every run.
  const out = html(R.default, { me: CURATOR, session: 'scn__a__SD-1__1', onRun() {}, onSession() {} });
  assert.ok(out.includes('Reading the session…') && out.includes('← All runs'));
  // A tag opens that run tag's entry (14 Nov 2026), kept in the address as Runs' own.
  assert.equal(L.placeQuery('runs', null, null, null, 'hussain_5q_v0'), 'view=runs&tag=hussain_5q_v0');
  assert.equal(L.placeQuery('runs', null, null, 'scn__x', 'hussain_5q_v0'), 'view=runs&session=scn__x&tag=hussain_5q_v0');
  assert.equal(L.placeQuery('labels', 'R3', null, null, 'hussain_5q_v0'), 'view=labels&run=R3');
  assert.equal(L.placeOf('?view=runs&tag=hussain_5q_v0').tag, 'hussain_5q_v0', 'the tag keeps its case');
});

test('a stored answer links to its session inside Runs', () => {
  assert.ok(src('QuestionPage.jsx').includes("'?view=runs&session='"));
  assert.ok(!src('QuestionPage.jsx').includes('?view=sessions'));
});

test('the lab api names the run conversations route once', () => {
  const api = src('labApi.js');
  assert.ok(/runSessions: \(code, query\) => call\('GET', '\/lab\/runs\/' \+ enc\(code\) \+ '\/sessions'/.test(api));
});

// ── 14 Nov 2026 (Hussain): "each codex session is also a run, so it should also be R something … show something better" ──
const TAG_RUN = { code: 'R54', title: 'Sessions · hussain_5q_v0', kind: 'synthetic', source: 'sync', n_questions: 0, n_traced: 0,
  n_sessions: 7, n_turns: 8, first_at: '2026-11-12T09:00:00.000Z', last_at: '2026-11-12T10:00:00.000Z' };

test('a sessions run is a real run: its code, and counted from its sessions, on the card and in the top bar', () => {
  const v = R.runView(TAG_RUN);
  assert.equal(v.code, 'R54');
  assert.equal(v.kindText, 'sessions');
  assert.equal(v.counts, '7 sessions · 8 turns');
  const card = html(R.RunsList, { runs: [TAG_RUN, LIVE_RUN], onOpen() {} });
  assert.ok(card.includes('>R54<') && card.includes('Sessions · hussain_5q_v0') && card.includes('7 sessions · 8 turns'));
  assert.ok(!card.includes('0 questions'), 'not the questions it does not hold');
  const bar = html(L.RunPicker, { runs: [TAG_RUN, LIVE_RUN], run: null, onRun() {}, onSync() {} });
  assert.ok(bar.includes('R54 · Sessions · hussain_5q_v0 · 7 sessions'), bar);
  assert.ok(bar.includes('R48 · aasim@x.test · 2026-11-13 · 3 questions'));
  assert.equal(R.runMode(TAG_RUN), 'sessions', 'it opens on its conversations');
});

test('the Runs page asks for its own list; the top bar and Labels do not', () => {
  assert.ok(/labApi\.runs\(\{[^}]*for_page: 'true'/.test(src('Runs.jsx')));
  assert.ok(!src('Runs.jsx').includes('with_tags'));
  assert.ok(!src('LabApp.jsx').includes('for_page') && !src('LabelsView.jsx').includes('for_page'));
});

test("an old ?tag= link opens that tag's conversations, and a conversation as the session page", () => {
  const list = html(R.default, { me: CURATOR, tag: 'hussain_5q_v0', onRun() {}, onTag() {} });
  assert.ok(list.includes('aria-label="Conversations in Sessions · hussain_5q_v0"') && list.includes('Reading the conversations'));
  assert.ok(list.includes('Show every attempt') && list.includes('← All runs'));
  const page = html(R.default, { me: CURATOR, tag: 'hussain_5q_v0', session: 'scn__hussain_5q_v0__SD-1__1', onRun() {}, onTag() {}, onSession() {} });
  assert.ok(page.includes('Reading the session…') && page.includes('← Sessions · hussain_5q_v0'));
});

test("a link to a live day's scenario question still opens its conversation", () => {
  const data = { sessions: [ROW], waiting: [], elsewhere: [{ code: 'R48-4', session_id: 'scn__hussain_5q_v0__SD-1__1', turn_id: 'turn-s1' }] };
  assert.deepEqual(R.sessionForQuestion(data, 'R48-4'), { id: 'scn__hussain_5q_v0__SD-1__1', turn: 'turn-s1' });
});

test('a session row is titled by its first question; the scenario a chip; the id a small line; "asked here" in words', () => {
  const S = SES;
  const long = 'Which certificates expire in the next 30 days at B-301, and which of them belong to vendors that are blocked right now for missing insurance?';
  const rows = [
    { ...ROW, first_question: 'Which vendors are blocked?', origin: 'e162f2e241fb', asked_here: true },
    { ...ROW, id: 'scn__hussain_5q_v0__SD-1__1', scenario_id: 'SD-1', run_tag: 'hussain_5q_v0', first_question: long,
      origin: 'ubuntu-4gb-hel1-2', asked_here: false }];
  const out = html(S.SessionsTable, { rows, onOpen() {} });
  assert.ok(/<button[^>]*class="lab-link lab-ses-open"[^>]*>Which vendors are blocked\?</.test(out), out);
  assert.ok(out.includes('title="' + long.replace(/,/g, ',') + '"'), 'the whole question on hover');
  assert.ok(!out.includes('>' + long + '<'), 'a long question is cut short on the row');
  assert.ok(out.includes('…'));
  assert.ok(/<span class="lab-chip[^"]*">SD-1<\/span>/.test(out), 'the scenario as a chip');
  assert.ok(out.includes('asked here') && !out.includes('e162f2e241fb'), 'never the origin hash of this place');
  assert.ok(out.includes('asked on ubuntu-4gb-hel1-2'));
  assert.ok(/class="lab-small lab-mono[^"]*"[^>]*>2db72d10-e565-46b4-b0b6-5ff51304c586</.test(out), 'the id on its own small line');
  assert.ok(out.includes('user-select:all'), 'one click selects the id to copy');
  // With no question yet, the row keeps its old title.
  assert.ok(html(S.SessionsTable, { rows: [{ ...ROW, first_question: null }], onOpen() {} }).includes('>' + ROW.id + '</button>'));
});

// ── 14 Nov 2026 (Hussain): a Codex run is one batch of a run tag; runs in the order questions were last asked ───────
const BATCH_1 = { code: 'R60', title: 'Sessions · aasim_v0 · 14 Nov 09:02', kind: 'synthetic', source: 'sync', run_tag: 'aasim_v0',
  batch_start: '2026-11-14T09:02:00+00:00', n_sessions: 50, n_turns: 50 };
const BATCH_2 = { ...BATCH_1, code: 'R61', title: 'Sessions · aasim_v0 · 14 Nov 10:15', batch_start: '2026-11-14T10:15:00+00:00', n_sessions: 100 };

test('a batch run is a sessions run of its tag, its start in its title', () => {
  assert.equal(R.sessionsTagOf(BATCH_1), 'aasim_v0');
  assert.equal(R.sessionsTagOf({ title: 'Sessions · aasim_v0 · 14 Nov 10:15', kind: 'synthetic', source: 'sync' }), 'aasim_v0',
    'the tag without the start, when the service does not say it');
  assert.equal(R.sessionsTagOf({ title: 'Sessions · chat', kind: 'synthetic', source: 'sync' }), null, 'the chat is no batch');
  assert.equal(R.sessionsTagOf({ ...BATCH_1, run_tag: null }), null, 'the service says it is not a batch');
  assert.equal(R.runMode(BATCH_2), 'sessions');
  assert.equal(R.runLabel(BATCH_2), 'R61 · Sessions · aasim_v0 · 14 Nov 10:15 · 100 sessions');
});

test('an old ?tag= link opens the tag\'s latest batch', () => {
  assert.equal(L.runForTag([LIVE_RUN, BATCH_1, BATCH_2], 'aasim_v0'), 'R61');
  assert.equal(L.runForTag([{ ...SESSIONS_RUN, run_tag: 'frontend_nov13_202718', batch_start: null }], 'frontend_nov13_202718'), 'R47');
  assert.equal(L.runForTag([LIVE_RUN], 'aasim_v0'), null);
  assert.ok(/runForTag\(runs, tag\)/.test(src('LabApp.jsx')), 'the address\'s tag opens its run once the runs are read');
});

test('the top-bar picker keeps the service\'s order (the latest question asked first)', () => {
  const bar = html(L.RunPicker, { runs: [BATCH_2, LIVE_RUN, BATCH_1], run: null, onRun() {}, onSync() {} });
  const at = (code) => bar.indexOf('value="' + code + '"');
  assert.ok(at('R61') < at('R48') && at('R48') < at('R60'), bar);
});

test('a session row never shows a container id as where it was asked', () => {
  assert.equal(sessionRow({ id: 's1', origin: 'b58519d742f0', asked_here: false }).askedText, '', 'a container id says nothing');
  assert.equal(sessionRow({ id: 's1', origin: 'ubuntu-4gb-hel1-2', asked_here: false }).askedText, 'asked on ubuntu-4gb-hel1-2');
  assert.equal(sessionRow({ id: 's1', origin: 'b58519d742f0', asked_here: true }).askedText, 'asked here');
  assert.equal(sessionRow({ id: 's1', origin: 'ubuntu-4gb-hel1-2', attempt: 2 }).askedText, 'asked on ubuntu-4gb-hel1-2 · attempt 2');
});

// ── Browser back and forward (14 Nov 2026): a move to another page, run or session is a history entry, so Back returns
// to where the person was instead of leaving Trace Lab; a redirect the page makes by itself only rewrites the address ──
test('back and forward: each move is a history entry, a redirect only rewrites the address', () => {
  const at = (q) => '/lab/' + (q ? '?' + q : '');
  assert.equal(L.historyStep(at('view=runs&run=R55'), at('view=runs&run=R55'), false), null, 'the address is already right');
  assert.equal(L.historyStep(at(''), at('view=runs&run=R55'), true), 'replace', 'the first address is only tidied');
  assert.equal(L.historyStep(at(''), at('view=runs&run=R55'), false), 'push', 'opening a run');
  assert.equal(L.historyStep(at('view=runs&run=R55'), at('view=runs&run=R55&session=scn__a__S-1__1'), false), 'push', 'opening a session');
  assert.equal(L.historyStep(at('view=runs&run=R55&session=scn__a__S-1__1'), at('view=runs&run=R55'), false), 'push', 'back to the run');
  assert.equal(L.historyStep(at('view=runs&run=R55'), at('view=labels&run=R55'), false), 'push', 'another page');
  assert.equal(L.historyStep(at('view=labels&run=R57'), at('view=runs&run=R57&session=scn__a__S-1__1'), false), 'push', 'a Labels row');
  assert.equal(L.historyStep(at('view=runs&run=R53&q=R53-3'), at('view=runs&run=R53&session=bd31'), false), 'replace',
    'a question link that opened its conversation');
  assert.equal(L.historyStep(at('view=runs&tag=spec_v0'), at('view=runs&run=R57'), false), 'replace', 'an older ?tag= link that opened its run');
  assert.equal(L.historyStep(at('view=runs&run=R3'), at('view=runs&run=R3&q=R3-2'), false), 'push', 'a typed run opening a question');
});

test('back and forward: going back restores the page, run, question and session from the address', () => {
  const app = src('LabApp.jsx');
  assert.match(app, /addEventListener\('popstate'/);
  assert.match(app, /removeEventListener\('popstate'/);
  const pop = app.slice(app.indexOf("addEventListener('popstate'") - 900, app.indexOf("addEventListener('popstate'"));
  for (const set of ['setRun(', 'setQuestion(', 'setSession(', 'setTag(', 'onView(']) assert.ok(pop.includes(set), set + ' on popstate');
  assert.match(app, /window\.history\.pushState\(/);
});
