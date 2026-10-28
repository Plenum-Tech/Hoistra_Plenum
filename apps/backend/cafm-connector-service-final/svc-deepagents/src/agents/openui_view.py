"""The orchestrator's OpenUI answers, read on the server.

The orchestrator writes its final answer as an OpenUI Lang program (the "Answer format" section
of its system prompt, generated from the chat's component spec into openui_prompt.md). The chat
draws that program as UI. Everything else that reads `answer` reads Markdown: reopened chats, the
report cards, the PDF export, cron and support emails, Trace Lab, memory extraction, the
evaluator and the composer. So `answer` stays Markdown. The program is read here, a Markdown
rendition of exactly what the chat draws takes its place, and the program travels beside it as
an `openui_view` card payload, the way the dashboard cards do.

The reader follows the OpenUI language spec (1.0) far enough to agree with the chat on what an
answer contains. It parses the whole grammar. It evaluates only what an answer is made of:
literals, lists, objects, references, component calls and the @ToAssistant / @OpenUrl / Action
steps. A mistake drops the smallest unit, as the chat drops it: a bad item leaves its list, a bad
optional prop is removed, and a missing or wrong required prop drops its component.
tests/test_openui_view.py holds it to @openuidev/lang-core's own output on the same programs.

Two things a model writes that OpenUI reads badly are put right before either reader sees the
program, and the chat is sent the program as read (27 Oct 2026): a `+0.45` is sent as the text
"+0.45" (OpenUI has no unary plus, so every cell after it moved one column), and a program written
with its root statement last is sent root first (the chat reads a program from its root on).
"""
from __future__ import annotations

import json
import math
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Context, Decimal
from pathlib import Path
from typing import Any

# The component set, prop names in their positional order, each prop's type and whether it is
# required. It mirrors apps/frontend/src/openui/spec.js, and tests/fixtures/openui/schema.json
# fails the build if the two drift. Types:
#   string, number, boolean, strnum, any, enum:a|b, comp (one component), action,
#   list:<type> (list:comp takes any component; list:list:any takes rows of anything).
SCHEMA: dict[str, list[tuple[str, str, bool]]] = {
    "Answer": [("children", "list:comp", True)],
    "Headline": [("text", "string", True)],
    "Text": [("markdown", "string", True)],
    "Metrics": [("items", "list:comp", True)],
    "Metric": [("label", "string", True), ("value", "strnum", True), ("note", "string", False),
               ("tone", "enum:risk|warn|ok|neutral", False)],
    "Table": [("columns", "list:strnum", True), ("rows", "list:list:any", True),
              ("caption", "string", False)],
    "BarChart": [("title", "string", True), ("labels", "list:strnum", True),
                 ("values", "list:strnum", True), ("unit", "string", False)],
    "LineChart": [("title", "string", True), ("labels", "list:strnum", True),
                  ("series", "list:comp", True), ("unit", "string", False)],
    "Series": [("name", "string", True), ("values", "list:strnum", True)],
    "Callout": [("tone", "enum:risk|warn|ok|info", True), ("text", "string", True),
                ("title", "string", False)],
    "Finding": [("title", "string", True), ("text", "string", True),
                ("tone", "enum:risk|warn|ok|neutral", False), ("code", "string", False)],
    "Details": [("title", "string", True), ("fields", "list:comp", True),
                ("subtitle", "string", False)],
    "Field": [("label", "string", True), ("value", "strnum", True)],
    "Section": [("title", "string", True), ("children", "list:comp", True),
                ("summary", "string", False), ("collapsed", "boolean", False)],
    "Steps": [("items", "list:comp", True)],
    "Step": [("title", "string", True), ("detail", "string", False), ("action", "comp", False)],
    "Buttons": [("items", "list:comp", True)],
    "Button": [("label", "string", True), ("action", "action", False),
               ("variant", "enum:primary|secondary", False)],
    "FollowUps": [("questions", "list:string", True)],
    "EmailDraft": [("subject", "string", True), ("to", "string", True), ("body", "string", True),
                   ("cc", "string", False)],
}
ROOT = "Answer"
ACTION_STEPS = {"ToAssistant", "OpenUrl"}
MAX_PROGRAM_CHARS = 60_000
PROMPT_PATH = Path(__file__).with_name("openui_prompt.md")


# Set by the chat's own turns (orchestrator.stream, and rerun_turn for a corrected answer): only
# they are taught to answer in OpenUI. REST callers (report cards, /run, the Skill lab, emails)
# read `answer` as text and get the Markdown the prompt always asked for.
CHAT_TURN: ContextVar[bool] = ContextVar("openui_chat_turn", default=False)


def enabled() -> bool:
    """OPENUI_ANSWERS=0 (or false/off/no) stops the prompt teaching the format and stops views
    being sent. A program that still arrives (a thread whose history is full of them) is read
    into Markdown either way, so no reader ever gets code."""
    return os.getenv("OPENUI_ANSWERS", "1").strip().lower() not in {"0", "false", "off", "no"}


def prompt_section() -> str:
    """The generated "Answer format" text, or "" when it cannot be read."""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


MARKER = "# Answer Format — OpenUI"
_SEPARATOR = "\n\n---\n\n"
_HEADER = (
    MARKER + "\n\n"
    "Write your final answer to the user as an OpenUI Lang program using the components below. "
    "This takes the place of the Markdown layout asked for elsewhere in this prompt: a Markdown "
    "table becomes a Table, a heading becomes a Section, an email draft becomes an EmailDraft, "
    "and a link to a page of the app becomes a Button with @OpenUrl. Inside Text, Markdown is "
    "still right: bold, lists and document links. Everything else this prompt says about what to "
    "answer, which tools to call, citing sources and what never to state still applies."
)


def answer_format_block() -> str:
    """The section appended to the orchestrator's system prompt, or "" without the prompt file."""
    section = prompt_section()
    return _SEPARATOR + _HEADER + "\n\n" + section if section else ""


def with_answer_format(content: str) -> str:
    """A system prompt with the section on the chat's own turns (CHAT_TURN) while switched on,
    and without it anywhere else.

    Used for new threads (agents/system_prompt.py) and, on every model call, for threads whose
    stored first-turn prompt predates the section or was taught it on another kind of turn
    (agents/context_budget.py)."""
    cut = content.find(_SEPARATOR + MARKER)
    if not (enabled() and CHAT_TURN.get()):
        return content[:cut] if cut >= 0 else content
    if MARKER in content:
        return content
    return content + answer_format_block()


@dataclass(frozen=True)
class Node:
    type: str
    props: dict[str, Any]


@dataclass
class ParseResult:
    root: Node | None
    unresolved: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    # The words and figures of what the chat drops (a component with named arguments or a wrong
    # type, data where a component belongs), outermost only, so they are not lost with it.
    dropped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class View:
    """A program and the Markdown rendition written in its place. `tail` is the Markdown of
    anything the chat cannot draw, which the chat shows under the view."""
    program: str
    markdown: str
    tail: str = ""


# ── source text ──────────────────────────────────────────────────────────────────────────────

def _prepare(text: str) -> str:
    """Spec 1.2: when fences are present only their contents count; prose around them is not."""
    lines = text.split("\n")
    if any(line.startswith("```") for line in lines):
        kept: list[str] = []
        inside = False
        for line in lines:
            if line.startswith("```"):
                inside = not inside
                continue
            if inside:
                kept.append(line)
        text = "\n".join(kept)
    return text.strip()


# ── lexing (spec 1.3) ────────────────────────────────────────────────────────────────────────

@dataclass
class _Tok:
    kind: str  # ident state func str num punct nl
    value: Any


_TWO = ("==", "!=", ">=", "<=", "&&", "||")
_ONE = set("=><+-*/%!?:.,()[]{}")
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SINGLE_ESCAPES = {"'": "'", "\\": "\\", "n": "\n", "t": "\t"}


def _decode_double(raw: str) -> str:
    # A raw newline inside a double-quoted string turns its escapes off in OpenUI's parser
    # (upstream issue #1445); the chat draws it that way, so the rendition does too.
    if "\n" in raw:
        return raw
    try:
        return json.loads('"' + raw + '"', strict=False)
    except ValueError:
        return raw


def _word(src: str, i: int) -> str:
    m = _IDENT.match(src, i)
    return m.group(0) if m else src[i]


def _lex(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(src)

    def prev_is_value() -> bool:
        if not toks:
            return False
        t = toks[-1]
        if t.kind in ("ident", "state", "str", "num"):
            return True
        return t.kind == "punct" and t.value in (")", "]")

    while i < n:
        c = src[i]
        if c == "\n":
            toks.append(_Tok("nl", "\n"))
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "#" or src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            toks.append(_Tok("str", _decode_double("".join(buf))))
            i = j + 1
            continue
        if c == "'":
            j, buf = i + 1, []
            while j < n and src[j] != "'":
                if src[j] == "\\" and j + 1 < n:
                    nxt = src[j + 1]
                    buf.append(_SINGLE_ESCAPES.get(nxt, "\\" + nxt))
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            toks.append(_Tok("str", "".join(buf)))
            i = j + 1
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit() and not prev_is_value()):
            m = _NUMBER.match(src, i)
            if m:
                s = m.group(0)
                toks.append(_Tok("num", float(s) if any(ch in s for ch in ".eE") else int(s)))
                i = m.end()
                continue
        if c in "$@" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] == "_"):
            word = _word(src, i + 1)
            toks.append(_Tok("state", "$" + word) if c == "$" else _Tok("func", word))
            i += 1 + len(word)
            continue
        if c.isalpha() or c == "_":
            word = _word(src, i)
            toks.append(_Tok("ident", word))
            i += len(word)
            continue
        two = src[i:i + 2]
        if two in _TWO:
            toks.append(_Tok("punct", two))
            i += 2
            continue
        if c in "&|":
            toks.append(_Tok("punct", c + c))
            i += 1
            continue
        if c in _ONE:
            toks.append(_Tok("punct", c))
            i += 1
            continue
        i += 1  # a character no rule matches is skipped; lexing never fails
    return toks


# ── statements (spec 1.4) and expressions (spec 1.5, 1.6) ────────────────────────────────────

def _statements(toks: list[_Tok]) -> list[list[_Tok]]:
    """Split at newlines outside brackets; a next line starting with `?` continues a ternary."""
    out: list[list[_Tok]] = []
    cur: list[_Tok] = []
    depth = 0
    for k, t in enumerate(toks):
        if t.kind == "punct" and t.value in "([{":
            depth += 1
        elif t.kind == "punct" and t.value in ")]}":
            depth = max(0, depth - 1)
        if t.kind == "nl":
            if depth > 0:
                continue
            nxt = next((u for u in toks[k + 1:] if u.kind != "nl"), None)
            if nxt is not None and nxt.kind == "punct" and nxt.value == "?":
                continue
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        out.append(cur)
    return out


class _Parser:
    """Recursive descent over one statement's tokens. AST nodes are tuples."""

    def __init__(self, toks: list[_Tok]):
        self.t = toks
        self.i = 0

    def peek(self) -> _Tok | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def at(self, value: str) -> bool:
        p = self.peek()
        return p is not None and p.kind == "punct" and p.value == value

    def eat(self, value: str) -> bool:
        if self.at(value):
            self.i += 1
            return True
        return False

    def expr(self) -> Any:
        return self.ternary()

    def ternary(self) -> Any:
        cond = self.binary(0)
        if self.eat("?"):
            yes = self.ternary()
            self.eat(":")
            no = self.ternary()
            return ("op", "?:", cond, yes, no)
        return cond

    _LEVELS = (("||",), ("&&",), ("==", "!="), (">", "<", ">=", "<="), ("+", "-"), ("*", "/", "%"))

    def binary(self, level: int) -> Any:
        if level == len(self._LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while True:
            p = self.peek()
            if p is not None and p.kind == "punct" and p.value in self._LEVELS[level]:
                self.i += 1
                left = ("op", p.value, left, self.binary(level + 1))
            else:
                return left

    def unary(self) -> Any:
        p = self.peek()
        if p is not None and p.kind == "punct" and p.value in ("!", "-"):
            self.i += 1
            return ("op", "unary" + p.value, self.unary())
        return self.postfix()

    def postfix(self) -> Any:
        node = self.primary()
        while True:
            if self.eat("."):
                p = self.peek()
                if p is not None and p.kind != "punct":
                    self.i += 1
                node = ("op", "member", node)
            elif self.at("["):
                self.i += 1
                self.expr()
                self.eat("]")
                node = ("op", "index", node)
            else:
                return node

    def args(self, close: str) -> list[Any]:
        items: list[Any] = []
        while self.peek() is not None and not self.at(close):
            start = self.i
            items.append(self.expr())
            self.eat(",")
            if self.i == start:  # no progress: skip the stray token
                self.i += 1
        self.eat(close)  # an unclosed bracket at the end is closed implicitly
        return items

    def primary(self) -> Any:
        p = self.peek()
        if p is None:
            return ("lit", None)
        self.i += 1
        if p.kind in ("str", "num"):
            return ("lit", p.value)
        if p.kind == "ident":
            if p.value in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[p.value])
            if p.value[0].isupper() and self.at("("):
                self.i += 1
                return ("comp", p.value, self.args(")"))
            return ("ref", p.value)
        if p.kind == "state":
            return ("state", p.value)
        if p.kind == "func":
            if self.eat("("):
                return ("func", p.value, self.args(")"))
            return ("lit", None)
        if p.kind == "punct":
            if p.value == "(":
                inner = self.expr()
                self.eat(")")
                return inner
            if p.value == "[":
                return ("arr", self.args("]"))
            if p.value == "{":
                return ("obj", self.entries())
        return ("lit", None)

    def entries(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        while True:
            k = self.peek()
            if k is None or (k.kind == "punct" and k.value == "}"):
                break
            self.i += 1
            key = "?"
            if k.kind in ("ident", "str", "num"):
                key = str(k.value)
            elif k.kind == "state":
                key = str(k.value)[1:]
            if not self.eat(":"):
                continue
            out.append((key, self.expr()))
            self.eat(",")
        self.eat("}")
        return out


# ── evaluation (spec 2, 3.1, 6.3, 8) ─────────────────────────────────────────────────────────

_MISSING = object()   # an unresolved reference: left out of lists, absent as a prop
_DROPPED = object()   # an invalid or unknown component: left out of lists, absent as a prop


class _Program:
    def __init__(self, statements: list[tuple[str, Any]]):
        self.order = [name for name, _ in statements]
        self.defs: dict[str, Any] = {}
        for name, ast in statements:  # a later definition of a name wins
            self.defs[name] = ast
        self.cache: dict[str, Any] = {}
        self.visiting: set[str] = set()
        self.reached: set[str] = set()
        self.unresolved: set[str] = set()
        self.dropped: list[tuple[int, str]] = []  # (id of the dropped call, its words)
        self.swallowed: set[int] = set()  # calls inside a dropped call: reported with it

    def _words(self, ast: Any, seen: set[str]) -> list[Any]:
        """The literal words and numbers under an expression, references followed once."""
        kind = ast[0]
        if kind == "lit":
            v = ast[1]
            ok = isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()
            return [v] if ok else []
        if kind == "arr":
            return [w for a in ast[1] for w in self._words(a, seen)]
        if kind == "obj":
            return [w for _, a in ast[1] for w in self._words(a, seen)]
        if kind == "comp":
            self.swallowed.add(id(ast))
            return [w for a in ast[2] for w in self._words(a, seen)]
        if kind == "ref" and ast[1] not in seen and ast[1] in self.defs:
            seen.add(ast[1])
            return self._words(self.defs[ast[1]], seen)
        return []  # @ToAssistant questions and the like are not the answer's words

    def _record(self, key: int, parts: list[Any]) -> None:
        words = [w for p in parts for w in self._words(p, set())]
        text = " · ".join(_num(w).strip() for w in words if _num(w).strip())
        if text:
            self.dropped.append((key, text))

    def dropped_text(self) -> list[str]:
        return [t for key, t in self.dropped if key not in self.swallowed]

    def ref(self, name: str) -> Any:
        if name in self.cache:
            self.reached.add(name)
            return self.cache[name]
        if name not in self.defs or name in self.visiting:
            if name not in self.defs:
                self.unresolved.add(name)
            return _MISSING
        self.reached.add(name)
        self.visiting.add(name)
        value = self.eval(self.defs[name])
        self.visiting.discard(name)
        self.cache[name] = value
        return value

    def eval(self, ast: Any) -> Any:
        kind = ast[0]
        if kind == "lit":
            return ast[1]
        if kind == "ref":
            return self.ref(ast[1])
        if kind == "arr":
            out = []
            for item in ast[1]:
                v = self.eval(item)
                if v is _MISSING or v is _DROPPED:
                    continue
                out.append(v)
            return out
        if kind == "obj":
            obj = {}
            for k, v in ast[1]:
                value = self.eval(v)
                obj[k] = None if value is _MISSING or value is _DROPPED else value
            return obj
        if kind == "func":
            name = ast[1].lstrip("@")
            if name in ACTION_STEPS:
                return {"action": name, "args": [self._plain(self.eval(a)) for a in ast[2]]}
            return None  # built-ins and custom functions are not part of an answer
        if kind == "comp":
            if ast[1] == "Action":
                return {"action": "Action", "args": [self._plain(self.eval(a)) for a in ast[2]]}
            out = self.component(ast[1], ast[2])
            if out is _DROPPED:
                self._record(id(ast), ast[2])
            return out
        return None  # state, operators, member access: not part of an answer

    @staticmethod
    def _plain(v: Any) -> Any:
        return None if v is _MISSING or v is _DROPPED else v

    def component(self, name: str, args: list[Any]) -> Any:
        spec = SCHEMA.get(name)
        if spec is None:
            for a in args:  # still walk the arguments: what they reference was reached
                self.eval(a)
            return _DROPPED
        props: dict[str, Any] = {}
        for idx, (prop, typ, required) in enumerate(spec):
            if idx >= len(args):
                if required:
                    return _DROPPED
                continue
            value = self.eval(args[idx])
            if value is _MISSING or value is _DROPPED:
                if required:
                    return _DROPPED
                continue
            if value is None:
                if required:
                    return _DROPPED
                props[prop] = None
                continue
            ok, clean = _check(typ, value)
            if not ok:
                if required:
                    return _DROPPED
                continue
            if typ == "list:comp" and isinstance(value, list) and len(clean) < len(value):
                # Data written where a component belongs ("str", 5) is left out of the list;
                # its words are kept with the rest of what the chat drops.
                lost = [("lit", v) for v in value if v is not None and not isinstance(v, Node)]
                self._record(id(args[idx]), lost)
            props[prop] = clean
        for a in args[len(spec):]:  # excess arguments are dropped; what they reference was reached
            self.eval(a)
        return Node(name, props)


def _check(typ: str, v: Any) -> tuple[bool, Any]:
    """Spec 8.2 / 8.4: does a value fit the prop's type, and what of it survives."""
    if typ == "any":
        return True, v
    if typ == "string":
        return isinstance(v, str), v
    if typ == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool), v
    if typ == "boolean":
        return isinstance(v, bool), v
    if typ == "strnum":
        # OpenUI does not check a string-or-number prop's type at all; a boolean passes too.
        return isinstance(v, (str, int, float)), v
    if typ.startswith("enum:"):
        return isinstance(v, str) and v in typ[5:].split("|"), v
    if typ == "comp":
        return isinstance(v, Node), v
    if typ == "action":
        return isinstance(v, dict) and "action" in v, v
    if typ.startswith("list:"):
        if not isinstance(v, list):
            return False, v
        inner = typ[5:]
        kept: list[Any] = []
        for item in v:
            if item is None:
                kept.append(None)  # an explicit null keeps its place in every kind of list
            elif inner == "comp":
                if isinstance(item, Node):  # data where a component belongs is left out
                    kept.append(item)
            elif inner.startswith("list:"):
                if isinstance(item, list):
                    kept.append(item)
            elif _check(inner, item)[0]:
                kept.append(item)
        return True, kept
    return False, v


def parse_full(program: str) -> ParseResult:
    """The program's tree as the chat would draw it, plus what could not be resolved or reached."""
    text = _prepare(program or "")
    if not text:
        return ParseResult(None)
    statements: list[tuple[str, Any]] = []
    for toks in _statements(_lex(text)):
        named = len(toks) >= 2 and toks[0].kind in ("ident", "state")
        if not named or not (toks[1].kind == "punct" and toks[1].value == "="):
            continue  # a line that is not `name = expression` is skipped
        statements.append((str(toks[0].value), _Parser(toks[2:]).expr()))
    prog = _Program(statements)
    value_names = [n for n in prog.order if not n.startswith("$")]

    def calls_root(name: str) -> bool:
        ast = prog.defs[name]
        return bool(ast[0] == "comp" and ast[1] == ROOT)

    entry = "root" if "root" in prog.defs else next((n for n in value_names if calls_root(n)), None)
    if entry is None:
        return ParseResult(None)
    value = prog.ref(entry)
    root = value if isinstance(value, Node) else None
    orphans = sorted({n for n in value_names if n not in prog.reached and n != entry})
    return ParseResult(root, sorted(prog.unresolved), orphans, prog.dropped_text() + _cut_off(root))


def _cut_off(root: Node | None) -> list[str]:
    """Values a chart holds past its last label. OpenUI keeps them in the tree, but the chat draws
    one bar or point per label (Charts.jsx), so they would vanish without a trace: they are reported
    with the rest of what the chat drops (27 Oct 2026)."""
    out: list[str] = []

    def lost(values: Any, labels: Any) -> None:
        if isinstance(values, list) and isinstance(labels, list):
            out.extend(_num(v) for v in values[len(labels):] if v is not None and _num(v).strip())

    def walk(v: Any) -> None:
        if isinstance(v, list):
            for x in v:
                walk(x)
            return
        if not isinstance(v, Node):
            return
        if v.type == "BarChart":
            lost(v.props.get("values"), v.props.get("labels"))
        if v.type == "LineChart":
            for s in _nodes(v.props.get("series")):
                lost(s.props.get("values"), v.props.get("labels"))
        for p in v.props.values():
            walk(p)

    walk(root)
    return out


def parse(program: str) -> Node | None:
    return parse_full(program).root


def summary(v: Any) -> Any:
    """The tree as plain data, in the fixtures' shape: null props left out, lists kept as is."""
    if isinstance(v, Node):
        props = {k: summary(p) for k, p in v.props.items() if p is not None}
        return {"type": v.type, "props": props}
    if isinstance(v, list):
        return [summary(x) for x in v]
    if isinstance(v, dict):
        return {k: summary(x) for k, x in v.items()}
    return v


# ── the Markdown rendition ───────────────────────────────────────────────────────────────────

_FIFTEEN = Context(prec=15, rounding=ROUND_HALF_UP)


def _js_number(x: float) -> str:
    """A number as the chat shows it: shared.jsx fmt, String(Number(n.toPrecision(15))) with -0 as 0.
    Never rounded to two places and never grouped (27 Oct 2026: a year 2004 read "2,004" and 0.004
    read "0"), so the rendition and the answer check read exactly what the reader sees."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    # toPrecision(15) rounds the exact binary value half up; Number() then reads it back.
    x = float(_FIFTEEN.plus(Decimal(x)))
    if math.isinf(x):  # rounded past the largest double, as JS rounds it
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    # String(n): the shortest digits that read back as n (Python's repr finds the same ones), laid out
    # by ECMAScript's Number::toString rules.
    d = Decimal(repr(abs(x))).normalize().as_tuple()
    digits = "".join(map(str, d.digits))
    k, n = len(digits), len(digits) + int(d.exponent)
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * -n + digits
    else:
        e = n - 1
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return ("-" if x < 0 else "") + s


def _num(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return _js_number(float(v))
    return str(v)


def cell_text(v: Any) -> str:
    """A cell, label or value as the chat shows it, "" for nothing (agents/answer_presenter.py)."""
    if v is None or v == "":
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return _num(v)


def _cell(v: Any) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, (dict, list)):
        text = json.dumps(v, ensure_ascii=False)
    else:
        text = _num(v)
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _table(head: list[str], rows: list[list[Any]]) -> str:
    # A row longer than the headings keeps its extra cells under blank headings, as the chat's Table
    # draws it (27 Oct 2026): a cell past the last heading is never cut.
    width = max([len(head), *(len(r) for r in rows)])
    lines = [
        "| " + " | ".join([_cell(h) for h in head] + [""] * (width - len(head))) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for row in rows:
        cells = [row[j] if j < len(row) else None for j in range(width)]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _nodes(v: Any) -> list[Node]:
    return [x for x in (v or []) if isinstance(x, Node)]


def _url_of(action: Any) -> str | None:
    """The page an @OpenUrl step (alone or inside Action([...])) opens, if any."""
    if not isinstance(action, dict):
        return None
    args = action.get("args") or []
    if action.get("action") == "OpenUrl":
        return args[0] if args and isinstance(args[0], str) else None
    if action.get("action") == "Action" and args and isinstance(args[0], list):
        return next((u for u in (_url_of(step) for step in args[0]) if u), None)
    return None


def _button_md(node: Node) -> str:
    """A button as text: a link when it opens a page (as the prompt's Markdown rules wrote
    in-app links), otherwise its label."""
    label = str(node.props.get("label", "")).strip()
    url = _url_of(node.props.get("action"))
    if not url or not label:
        return label
    page = url.startswith("/") and not url.startswith("//")
    web = url.lower().startswith(("http://", "https://"))
    return f"[{label}]({url})" if page or web else label


def _finding_md(p: dict[str, Any]) -> str:
    """One issue of several, as the answer wrote it in prose: its lead in bold, its record code in
    brackets when it has one, then the rest of it. "**Fire Risk Assessment** (FRA-B-301-2025) — Lapsed,
    expired 2026-06-29 …". The chat draws the same parts in the same order (Finding.jsx), and a part
    that is blank is left out, as the chat leaves it out (28 Oct 2026)."""
    title = str(p.get("title") or "").strip()
    code = str(p.get("code") or "").strip()
    text = str(p.get("text") or "").strip()
    bold = title.startswith("**") and title.endswith("**") and len(title) > 4
    head = " ".join(x for x in ((title if bold else f"**{title}**") if title else "",
                                f"({code})" if code else "") if x)
    return " — ".join(x for x in (head, text) if x)


def _block(node: Node, shown: bool = False) -> str:
    p, t = node.props, node.type

    def kids(v: Any) -> list[str]:
        return [b for b in (_block(c, shown) for c in _nodes(v)) if b]

    if t == "Headline":
        return p.get("text", "")
    if t == "Text":
        return p.get("markdown", "")
    if t == "Metric" or t == "Field":
        line = f"- {p.get('label', '')}: {_num(p.get('value', ''))}"
        return line + (f" ({p['note']})" if p.get("note") else "")
    if t == "Metrics":
        return "\n".join(kids(p.get("items")))
    if t == "Table":
        rows = [r for r in (p.get("rows") or []) if isinstance(r, list)]
        out = _table(p.get("columns") or [], rows)
        return out + (f"\n\n_{p['caption']}_" if p.get("caption") else "")
    if t == "BarChart":
        # Every label with its own value, a dash where there is none: never pair past a gap.
        labels, values = p.get("labels") or [], p.get("values") or []
        rows = [[label, values[i] if i < len(values) else None] for i, label in enumerate(labels)]
        return f"**{p.get('title', '')}**\n\n" + _table(["Item", p.get("unit") or "Value"], rows)
    if t == "LineChart":
        series = [s.props for s in _nodes(p.get("series")) if s.type == "Series"]
        labels = p.get("labels") or []
        values = [s.get("values") or [] for s in series]
        rows = [[label] + [v[i] if i < len(v) else None for v in values]
                for i, label in enumerate(labels)]
        title = f"**{p.get('title', '')}**" + (f" ({p['unit']})" if p.get("unit") else "")
        return title + "\n\n" + _table(["Period"] + [s.get("name", "") for s in series], rows)
    if t == "Callout":
        return (f"**{p['title']}:** " if p.get("title") else "") + p.get("text", "")
    if t == "Finding":
        return _finding_md(p)
    if t == "Details":
        head = f"**{p.get('title', '')}**" + (f" — {p['subtitle']}" if p.get("subtitle") else "")
        fields = [_block(f, shown) for f in _nodes(p.get("fields")) if f.type == "Field"]
        return "\n".join([head, *fields])
    if t == "Section":
        parts = [f"## {p.get('title', '')}"]
        if p.get("summary"):
            parts.append(p["summary"])
        # Collapsed, the reader sees its title and summary until they open it.
        if not (shown and p.get("collapsed") is True):
            parts.extend(kids(p.get("children")))
        return "\n\n".join(parts)
    if t == "Steps":
        lines = []
        for k, s in enumerate((x for x in _nodes(p.get("items")) if x.type == "Step"), start=1):
            line = f"{k}. **{s.props.get('title', '')}**"
            line += f" — {s.props['detail']}" if s.props.get("detail") else ""
            action = s.props.get("action")
            if isinstance(action, Node) and not shown:
                line += f" ({_button_md(action)})"
            lines.append(line)
        return "\n".join(lines)
    # What the reader can click is part of what they read, and of what the answer check reads:
    # a button or a follow-up can carry a figure or a code too. It is not where the answer's facts
    # are shown, so the shown rendition leaves it out (27 Oct 2026: a dropped data gap passed the
    # check because a follow-up chip still named it).
    if t in ("Buttons", "Button", "FollowUps") and shown:
        return ""
    if t == "Buttons":
        return " · ".join(_button_md(b) for b in _nodes(p.get("items")) if b.type == "Button")
    if t == "Button":
        return _button_md(node)
    if t == "FollowUps":
        qs = [str(q).strip() for q in (p.get("questions") or []) if isinstance(q, str) and q.strip()]
        return "Ask next: " + " · ".join(qs) if qs else ""
    if t == "Step":
        return f"1. **{p.get('title', '')}**" + (f" — {p['detail']}" if p.get("detail") else "")
    if t == "EmailDraft":
        lines = [f"Subject: {p.get('subject', '')}", f"To: {p.get('to', '')}"]
        if p.get("cc"):
            lines.append(f"Cc: {p['cc']}")
        lines += ["Email body:", p.get("body", "")]
        return "```\n" + "\n".join(lines) + "\n```"
    if t == "Answer":
        return "\n\n".join(kids(p.get("children")))
    return ""  # Series and Field: parts of a chart or a record, written by their parent


def render_markdown(node: Node | None, *, shown: bool = False) -> str:
    """The Markdown of everything the program draws. With shown=True, only what the reader sees
    without a click: no follow-up chips or buttons, and a collapsed Section as its title and summary
    (what agents/answer_presenter.py holds the answer's facts to)."""
    return _block(node, shown).strip() if isinstance(node, Node) else ""


# ── what the orchestrator calls ──────────────────────────────────────────────────────────────

# Some line is `name = Component(`: a cheap test that keeps ordinary Markdown away from the parser.
_PREFILTER = re.compile(r"^[ \t]*(?:root|[a-z_]\w*)[ \t]*=[ \t]*[A-Z]\w*\(", re.MULTILINE)
# Where the program starts: its `root = Answer(` statement, wherever it is. A sentence written
# before it (in the same message, or run on with no line break) is the answer's lead.
_ROOT_AT = re.compile(r"\broot\s*=\s*Answer\s*\(")
_FENCE_LINE = re.compile(r"^[ \t]*```[^\n]*$", re.MULTILINE)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', re.DOTALL)
_KEYWORD = re.compile(r"^(?:risk|warn|ok|neutral|info|primary|secondary)$")
# What a reader gets when a program draws nothing and has no words to fall back on.
FALLBACK = "This answer could not be shown as written. Ask again, or ask for it as text."


def _clean(text: str) -> str:
    return _FENCE_LINE.sub("", text).strip()


def _split(text: str) -> tuple[str, str, bool, bool]:
    """split_program, and whether statements written before the root were moved after it."""
    roots = list(_ROOT_AT.finditer(text))
    if not roots:
        return "", _clean(text), False, False
    start = roots[-1].start()
    # 27 Oct 2026: a program written with its root statement last was cut at `root = Answer(`, so
    # its other statements were kept as prose and the reader got code. The program starts at its
    # first statement; the chat reads a program from its root on (frontend detect.js), so those
    # statements go after the root, where OpenUI resolves them as it resolves any later statement.
    # With two roots the last one is the answer, as before.
    first = _PREFILTER.search(text, 0, start) if len(roots) == 1 else None
    if first is None:
        return _clean(text[:start]), _clean(text[start:]), True, False
    program = _clean(text[start:]) + "\n" + _clean(text[first.start():start])
    return _clean(text[:first.start()]), program, True, True


def split_program(text: str) -> tuple[str, str, bool]:
    """(lead, program, found): the prose before the program and the program, root statement first,
    fence lines removed. Without a root statement the whole text is the program and found is False."""
    lead, program, found, _moved = _split(text)
    return lead, program, found


# A unary plus on a number: `+0.45` where a value goes (after `[`, `,`, `(`, `=` or `:`).
_PLUS_NUMBER = re.compile(r"\+[ \t]*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?![\w.])")


def quote_signed_numbers(program: str) -> str:
    """The program with every `+number` in a value's place written as the string "+number".

    OpenUI has no unary plus: `+0.45` reads as a stray `+` and then 0.45, so a Table row gains a null
    cell and every figure after it moves one column right (@openuidev/lang-core does exactly this, as
    the Python reader does). The plus is part of a figure the model wrote, a change, so it is kept as
    text, which every component shows as written (27 Oct 2026). Strings, comments and a plus between
    two values (`2+3`) are left as they are."""
    out: list[str] = []
    i, n, last = 0, len(program), ""
    while i < n:
        c = program[i]
        if c in "\"'":
            j = i + 1
            while j < n and program[j] != c:
                j += 2 if program[j] == "\\" else 1
            out.append(program[i:j + 1])
            i, last = j + 1, c
            continue
        if c == "#" or program.startswith("//", i):
            j = program.find("\n", i)
            j = n if j < 0 else j
            out.append(program[i:j])
            i = j
            continue
        m = _PLUS_NUMBER.match(program, i) if c == "+" and last in ("", "[", ",", "(", "=", ":") else None
        if m:
            out.append('"+' + m.group(1) + '"')
            i, last = m.end(), '"'
            continue
        out.append(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out)


def plain_text(program: str) -> str:
    """The words of a program without its code: its string literals in order, as paragraphs."""
    out = []
    for m in _STRING.finditer(program or ""):
        s = (_decode_double(m.group(1)) if m.group(1) is not None else m.group(2)).strip()
        if not s or _KEYWORD.match(s) or s.startswith(("/", "http:", "https:")):
            continue
        out.append(s)
    return "\n\n".join(out)


def split_answer(text: str) -> tuple[str, View | None]:
    """(Markdown answer, view) for a final answer. Markdown comes back untouched with no view."""
    if not isinstance(text, str) or not _PREFILTER.search(text):
        return text, None
    lead, program, found, moved = _split(text)
    sent = quote_signed_numbers(program)
    try:
        result = parse_full(sent)
        body = render_markdown(result.root)
    except Exception:  # noqa: BLE001 - the reader must never cost the answer
        result, body = ParseResult(None), ""
    if result.root is None and not found:
        return text, None  # Markdown that happens to contain `x = Foo(`
    if not body:
        words = plain_text(program)
        return "\n\n".join(p for p in (lead, words or FALLBACK) if p), None
    tail = "\n\n".join(result.dropped)
    md = "\n\n".join(p for p in (lead, body, tail) if p)
    # The chat draws the program as it was read here: as written when nothing was moved or quoted,
    # otherwise the lead and the program as read (its root first, its signed figures as text).
    shown = text.strip() if not moved and sent == program else "\n\n".join(p for p in (lead, sent) if p)
    return md, View(program=shown, markdown=md, tail=tail)


def reconcile(view: View | None, final_answer: Any) -> dict[str, Any] | None:
    """The payload to send, if the view still shows the final answer.

    A later step may rewrite the answer (the EL-UDR evaluator, the vendor composer). When it only
    appended a note, the view stays and the note travels as `tail`; when it changed what the
    view shows, the view is dropped and the chat shows the Markdown.
    """
    if view is None or not enabled() or not isinstance(final_answer, str):
        return None
    if len(view.program) > MAX_PROGRAM_CHARS:
        return None
    answer, md = final_answer.strip(), view.markdown.strip()
    if not answer.startswith(md):
        return None
    tail = "\n\n".join(p for p in (view.tail, answer[len(md):].strip()) if p)
    out: dict[str, Any] = {"v": 1, "lang": view.program}
    if tail:
        out["tail"] = tail
    return out


def payload_entry(out: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "openui_view", "input": {}, "output": out}


def attach(view: View | None, final_answer: Any, tool_calls: list[Any]) -> list[Any]:
    """The turn's tool calls with the view's payload last, when the view still shows the answer."""
    out = reconcile(view, final_answer)
    return [*tool_calls, payload_entry(out)] if out else list(tool_calls)
