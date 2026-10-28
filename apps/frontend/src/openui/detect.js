// detect — cheap checks on the orchestrator's answer text that need no OpenUI code loaded.
//
// The chat page imports these on every render; the parser itself lives in the lazily loaded
// chunk (parse.js), so the app's first load does not carry it.

// Where a program starts: its `root = Answer(` statement. Not only at the start of the text: the
// live draft is every delta of the turn, so a sentence the model wrote before calling a tool
// runs straight into the program ("…work orders.root = Answer([…"), with no line break between.
// The last one wins, which is the answer if a draft ever holds two.
const ROOT = /\broot\s*=\s*Answer\s*\(/g;
export function programStart(text) {
  if (typeof text !== 'string') return -1;
  let at = -1;
  for (const m of text.matchAll(ROOT)) at = m.index;
  return at;
}
export const looksLikeOpenUIStart = (text) => programStart(text) >= 0;

// A fence line (```openui … ```) the prompt forbids but a model may still write: not prose, and
// not part of the program either (a lone closing fence would read to OpenUI as an empty one).
const FENCE_LINE = /^[ \t]*```[^\n]*$/gm;

// The text before the program (said to the reader, drawn above it) and the program itself.
export function splitProgram(text) {
  const s = String(text == null ? '' : text);
  const at = programStart(s);
  if (at < 0) return { lead: s.replace(FENCE_LINE, '').trim(), program: '' };
  return { lead: s.slice(0, at).replace(FENCE_LINE, '').trim(), program: s.slice(at).replace(FENCE_LINE, '').trim() };
}

// The words of a program, without its code: every string literal in order, as paragraphs.
// What a bubble shows when a program renders nothing, so the reader still gets the answer.
const STRING = /"((?:[^"\\]|\\.)*)"/g;
const KEYWORD = /^(?:risk|warn|ok|neutral|info|primary|secondary)$/;
export function plainTextOf(program) {
  const out = [];
  for (const m of String(program == null ? '' : program).matchAll(STRING)) {
    let s;
    try { s = JSON.parse('"' + m[1] + '"'); } catch (e) { s = m[1]; }
    s = s.trim();
    if (!s || KEYWORD.test(s) || /^(?:\/|https?:)/.test(s)) continue;
    out.push(s);
  }
  return out.join('\n\n');
}
