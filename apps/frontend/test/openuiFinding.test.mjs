// openuiFinding — one issue of a list, drawn as a Finding (src/openui/components/Finding.jsx).
//
// 28 Oct 2026: the model may lay out an answer that lists several issues (each about one asset,
// certificate, vendor or work order) as Findings. The words are the answer's own, drawn in the
// order it wrote them: title, code, text. The tick's tone is the model's, held to those words
// (findingTone): a tone the words do not back is drawn neutral, and none is ever made up.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import * as core from '@openuidev/lang-core';
import { z } from 'zod';
import { makeLibrary, ROOT } from '../src/openui/spec.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-finding.bundle.mjs');
const res = await build({
  stdin: {
    contents: "export { library } from '../src/openui/library.jsx';\nexport { Finding, findingTone } from '../src/openui/components/Finding.jsx';",
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const { library, Finding, findingTone } = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const render = (program, streaming = false) => renderToStaticMarkup(
  React.createElement(Renderer, { library, response: program, isStreaming: streaming, publishObservability: false })
);
const prog = (...lines) => lines.join('\n');
// What a reader sees, in order: the markup's text with the tags taken out.
const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&#x27;/g, "'").replace(/&quot;/g, '"')
  .replace(/\s+/g, ' ').trim();
const findings = (html) => html.match(/<article class="oui-finding"[^>]*>[\s\S]*?<\/article>/g) || [];

const FRA = prog('root = Answer([f])',
  'f = Finding("Fire Risk Assessment", "Lapsed, expired 2026-06-29 (about 4 months ago). The building has no current FRA on record.", "risk", "FRA-B-301-2025")');

test('the language takes Finding(title, text, tone?, code?) in an Answer and in a Section', () => {
  const lib = makeLibrary(core, z);
  const parser = core.createParser(lib.toJSONSchema(), ROOT);
  const r = parser.parse(prog('root = Answer([f, s])', 'f = Finding("A", "a", "warn", "B-101")',
    's = Section("Risk to life", [g])', 'g = Finding("B", "b")'));
  assert.deepEqual(r.meta.errors, []);
  const [f, s] = r.root.props.children;
  assert.equal(f.typeName, 'Finding');
  assert.deepEqual({ ...f.props }, { title: 'A', text: 'a', tone: 'warn', code: 'B-101' });
  assert.equal(s.props.children[0].typeName, 'Finding');
  assert.match(lib.prompt(), /Finding\(title: string, text: string, tone\?: "risk" \| "warn" \| "ok" \| "neutral", code\?: string\)/);
});

test('a Finding draws its title, code and text in the order the answer wrote them', () => {
  const html = render(FRA);
  assert.equal(findings(html).length, 1);
  assert.match(html, /<article class="oui-finding" data-tone="risk"><span class="oui-finding-head"><span class="oui-finding-title">Fire Risk Assessment<\/span> <button type="button" class="oui-ref oui-code-ask"[^>]*>FRA-B-301-2025<\/button><\/span> <div class="oui-finding-text"><div class="md-p"[^>]*>Lapsed, expired/);
  assert.equal(textOf(findings(html)[0]),
    'Fire Risk Assessment FRA-B-301-2025 Lapsed, expired 2026-06-29 (about 4 months ago). The building has no current FRA on record.');
});

// 28 Oct 2026: the title sat on a line of its own, so the text's first line opened on the
// answer's punctuation (", expired 2026-06-29…"). The title runs in to the text's first paragraph.
test('the title runs in to the text: one sentence, a space only where the answer had one', () => {
  const words = (html) => findings(html)[0].replace(/<[^>]+>/g, '').replace(/&#x27;/g, "'");
  const comma = render(prog('root = Answer([f])',
    'f = Finding("Fire Risk Assessment (FRA-B-301-2025) — Lapsed", ", expired 2026-06-29 (about 4 months ago).", "risk")'));
  assert.equal(words(comma), 'Fire Risk Assessment (FRA-B-301-2025) — Lapsed, expired 2026-06-29 (about 4 months ago).');
  // The head is an inline span; the text's first paragraph is a direct child of the text.
  assert.match(comma, /<\/span><\/span><div class="oui-finding-text"><div class="md-p"[^>]*>, expired/);
  for (const p of [', a', '. a', '; a', ': a', ') a']) {
    assert.doesNotMatch(render(prog('root = Answer([f])', `f = Finding("Lift 4", "${p}")`)), /<\/span> <div class="oui-finding-text">/, p);
  }
  const prose = render(prog('root = Answer([f])',
    'f = Finding("Standby generator GEN-1.", "Covered by **Ostley Power Services**, also a blocked vendor.\\n\\nBackup power rests on it.")'));
  assert.equal(words(prose), 'Standby generator GEN-1. Covered by Ostley Power Services, also a blocked vendor.Backup power rests on it.');
  // Later paragraphs follow below, drawn by the same Markdown renderer as before.
  assert.match(prose, /also a blocked vendor\.<\/div><div style="margin-top:7px"><div style="display:flex;flex-direction:column;gap:7px"><div class="md-p"[^>]*>Backup power rests on it\.<\/div>/);
  // A text that opens on a list keeps the list whole, below the head.
  const list = render(prog('root = Answer([f])', 'f = Finding("Lift 4", "- booked for 4 Nov\\n- report due")'));
  assert.match(list, /<\/span> <div class="oui-finding-text"><div style="display:flex[^"]*"><ul/);
});

test('codes in the title are tags too, the characters unchanged', () => {
  const html = render(prog('root = Answer([f])',
    'f = Finding("Lift Asset-4471 (B-301-LIFT-4471) — **GEN-1** cover", "Past date.", "risk")'));
  assert.match(html, /<span class="oui-finding-title">Lift Asset-4471 <span class="oui-ref-group">\(<button type="button" class="oui-ref oui-code-ask"[^>]*>B-301-LIFT-4471<\/button>\)<\/span> — <strong[^>]*><button type="button" class="oui-ref oui-code-ask"[^>]*>GEN-1<\/button><\/strong> cover<\/span>/);
});

test('the text is Markdown, drawn as Text draws it: bold, lists and links', () => {
  const html = render(prog('root = Answer([f])',
    'f = Finding("Lift 4", "**LOLER** examination overdue:\\n- booked for 4 Nov\\n- see [the report](https://example.com/r)")'));
  assert.match(html, /<strong[^>]*>LOLER<\/strong>/);
  assert.match(html, /<li[^>]*>[\s\S]*booked for 4 Nov/);
  assert.match(html, /<a [^>]*href="https:\/\/example.com\/r"/);
  // A record code in the text is set as a tag, exactly as Text sets one; the words are unchanged.
  const tagged = render(prog('root = Answer([f])', 'f = Finding("Lift 4", "Booked under WO-1184 for 4 Nov.")'));
  assert.match(tagged, /Booked under <button type="button" class="oui-ref oui-code-ask"[^>]*>WO-1184<\/button> for 4 Nov\./);
});

test('without a code there is no tag, and without a tone the tick is neutral', () => {
  const html = render(prog('root = Answer([f])', 'f = Finding("Boiler 2", "Pressure dropped twice this week.")'));
  assert.match(html, /<article class="oui-finding" data-tone="neutral">/);
  assert.doesNotMatch(html, /oui-ref/);
  assert.equal(textOf(findings(html)[0]), 'Boiler 2 Pressure dropped twice this week.');
});

test('consecutive Findings in a Section are drawn one after another, in order', () => {
  const html = render(prog('root = Answer([s])', 's = Section("Risk to life", [a, b, c])',
    'a = Finding("Fire Risk Assessment", "Lapsed.", "risk", "FRA-B-2025")',
    'b = Finding("Gas safety", "Expires on Friday.", "warn")',
    'c = Finding("Emergency lighting", "In date until March.", "ok")'));
  const list = findings(html);
  assert.equal(list.length, 3);
  assert.deepEqual(list.map((f) => /data-tone="(\w+)"/.exec(f)[1]), ['risk', 'warn', 'ok']);
  assert.deepEqual(list.map(textOf), ['Fire Risk Assessment FRA-B-2025 Lapsed.', 'Gas safety Expires on Friday.', 'Emergency lighting In date until March.']);
  assert.match(html, /oui-section-body"><article class="oui-finding"/);
});

test('internal names are scrubbed from every part, as in the other components', () => {
  const html = render(prog('root = Answer([f])',
    'f = Finding("svc-udr check", "Read from svc-deepagents.", "neutral", "svc-udr")'));
  assert.doesNotMatch(html, /svc-/);
  assert.match(html, /the platform check/);
});

test('a Finding still arriving does not break: title only, then title and part of the text', () => {
  for (const partial of [
    'root = Answer([f])\nf = Finding(',
    'root = Answer([f])\nf = Finding("Fire Risk',
    'root = Answer([f])\nf = Finding("Fire Risk Assessment"',
    'root = Answer([f])\nf = Finding("Fire Risk Assessment", "Lapsed, exp',
    'root = Answer([f])\nf = Finding("Fire Risk Assessment", "Lapsed, expired.", "ri',
    'root = Answer([f])\nf = Finding("Fire Risk Assessment", "Lapsed, expired.", "risk", "FRA-B'
  ]) {
    const html = render(partial, true);
    assert.equal(typeof html, 'string', partial);
    assert.doesNotMatch(html, /undefined|null|\[object/, partial);
  }
  // Once the text starts arriving the finding is drawn, its words so far in place.
  const partText = findings(render('root = Answer([f])\nf = Finding("Fire Risk Assessment", "Lapsed, exp', true));
  assert.equal(partText.length, 1);
  assert.equal(textOf(partText[0]), 'Fire Risk Assessment Lapsed, exp');
  const partCode = findings(render('root = Answer([f])\nf = Finding("Fire Risk Assessment", "Lapsed, expired.", "risk", "FRA-B', true));
  assert.equal(textOf(partCode[0]), 'Fire Risk Assessment FRA-B Lapsed, expired.');
});

test('a Finding drawn directly with missing props draws nothing for the missing parts', () => {
  const draw = (props) => renderToStaticMarkup(React.createElement(Finding, { props }));
  assert.equal(draw({}), '<article class="oui-finding" data-tone="neutral"></article>');
  assert.equal(draw({ title: null, text: undefined, tone: 'risk' }), '<article class="oui-finding" data-tone="neutral"></article>');
  assert.match(draw({ title: 'Only the title' }), /^<article class="oui-finding" data-tone="neutral"><span class="oui-finding-head"><span class="oui-finding-title">Only the title<\/span><\/span><\/article>$/);
  assert.match(draw({ text: 'Only the text', code: 'B-101' }), /<span class="oui-ref">B-101<\/span>[\s\S]*Only the text/);
});

test('findingTone: the model\'s tone, shown only when the words back it', () => {
  const T = (tone, title, text, code) => findingTone(tone, title, text, code);
  // risk: risk words keep it; warn words alone step it down; nothing backing it is neutral.
  assert.equal(T('risk', 'Fire Risk Assessment', 'Lapsed, expired 2026-06-29.'), 'risk');
  assert.equal(T('risk', 'Lift 4', 'The examination is overdue.'), 'risk');
  assert.equal(T('risk', 'Vendor', 'Blocked from new work.'), 'risk');
  assert.equal(T('risk', 'Chiller', 'Failed its inspection.'), 'risk');
  assert.equal(T('risk', 'SLA', 'Breached twice.'), 'risk');
  assert.equal(T('risk', 'Lift', 'Past its inspection date.'), 'risk');
  // 28 Oct 2026: the live lift finding read amber off "due" and "poor"; "past date" is a risk word.
  assert.equal(T('risk', 'Lift Asset-4471 (B-301-LIFT-4471) — LOLER statutory inspection past date.',
    'The certificate on file shows an expiry of 2026-10-18, already passed. Condition score 2 (poor), next plan due 2026-12-03.'), 'risk');
  assert.equal(T('risk', 'Lift', 'Past its date.'), 'risk');
  assert.equal(T('risk', 'Lift', 'Past the due date.'), 'risk');
  assert.equal(T('risk', 'Building B', 'There is no current FRA.'), 'risk');
  assert.equal(T('risk', 'Contractor', 'Unaccredited for gas work.'), 'risk');
  assert.equal(T('risk', 'Gas safety', 'Expires on Friday.'), 'warn');
  assert.equal(T('risk', 'Boiler 2', 'Pressure dropped twice this week.'), 'neutral');
  assert.equal(T('risk', 'Emergency lighting', 'In date until March.'), 'neutral');
  // warn: a warn or a risk word keeps it (never raised to risk); nothing backing it is neutral.
  assert.equal(T('warn', 'Gas safety', 'Expiring within 7 days.'), 'warn');
  assert.equal(T('warn', 'PPM', 'Due tomorrow.'), 'warn');
  assert.equal(T('warn', 'Lift', 'Lapsed last week.'), 'warn');
  assert.equal(T('warn', 'Apex', 'SLA slipping since August.'), 'warn');
  assert.equal(T('warn', 'Boiler 2', 'Pressure dropped twice this week.'), 'neutral');
  // ok: an ok word with no risk word; a negated ok word does not count.
  assert.equal(T('ok', 'Emergency lighting', 'In date until March.'), 'ok');
  assert.equal(T('ok', 'Chiller 1', 'Passed its F-Gas check.'), 'ok');
  assert.equal(T('ok', 'Vendor', 'Compliant and on track.'), 'ok');
  assert.equal(T('ok', 'Lift', 'Valid, though the previous one expired.'), 'neutral');
  assert.equal(T('ok', 'SLA', 'Not met this month.'), 'neutral');
  assert.equal(T('ok', 'Vendor', 'Non-compliant on insurance.'), 'neutral');
  assert.equal(T('ok', 'Vendor', 'Invalid insurance certificate.'), 'neutral');
  assert.equal(T('ok', 'Boiler 2', 'Serviced in May.'), 'neutral');
  // neutral, no tone, or a tone outside the four: neutral, whatever the words say.
  assert.equal(T('neutral', 'Fire Risk Assessment', 'Expired.'), 'neutral');
  assert.equal(T(undefined, 'Fire Risk Assessment', 'Expired.'), 'neutral');
  assert.equal(T('info', 'Fire Risk Assessment', 'Expired.'), 'neutral');
  assert.equal(T(null, null, null, null), 'neutral');
});

test('findingTone reads whole words, any case, across title, text and code', () => {
  assert.equal(findingTone('risk', 'EXPIRED certificate', ''), 'risk');
  assert.equal(findingTone('risk', 'Gas safety', '**Overdue** by 9 days.'), 'risk');
  // "overdue" is not "due"; "invalid" is not "valid"; "metres" is not "met".
  assert.equal(findingTone('warn', 'Pump', 'Overdue.'), 'warn');
  assert.equal(findingTone('ok', 'Lift', 'Invalid.'), 'neutral');
  assert.equal(findingTone('ok', 'Riser', '12 metres of cable replaced.'), 'neutral');
  assert.equal(findingTone('warn', 'Chiller', 'Running 3 duet cycles.'), 'neutral');
  assert.equal(findingTone('risk', 'Lift', 'See record.', 'EXPIRED-LIFT-4'), 'risk');
});
