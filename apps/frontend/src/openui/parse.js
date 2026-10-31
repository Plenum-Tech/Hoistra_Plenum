// parse — is a string an OpenUI answer, and does it draw anything? Loaded with the renderer.
import { createParser, parseMessage } from '@openuidev/react-lang';
import { library } from './library.jsx';
import { ROOT } from './spec.js';

// Some line is `name = Component(`. Rejects ordinary Markdown in well under a microsecond, so the
// parser only ever sees text that could be a program.
const PREFILTER = /^[ \t]*(?:root|[a-z_]\w*)[ \t]*=[ \t]*[A-Z]\w*\(/m;
let parser = null;

export function parseProgram(text) {
  if (typeof text !== 'string' || !text.trim() || !PREFILTER.test(text)) return null;
  try {
    if (!parser) parser = createParser(library.toJSONSchema(), ROOT);
    return parser.parse(parseMessage(text).content);
  } catch (e) {
    return null;
  }
}

export function isOpenUI(text) {
  const r = parseProgram(text);
  return !!(r && r.root && r.root.typeName === ROOT);
}

// A program whose every block was dropped (wrong types, unknown components) parses, but draws an
// empty box. That is not an answer.
export function rendersSomething(text) {
  const r = parseProgram(text);
  const kids = r && r.root && r.root.props && r.root.props.children;
  return Array.isArray(kids) && kids.some(Boolean);
}
