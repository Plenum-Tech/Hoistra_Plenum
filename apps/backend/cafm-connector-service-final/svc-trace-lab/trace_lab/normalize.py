"""Trace Lab normalizer: one plenum_cafm.agent_trace_turns row and its agent_trace_spans become a
Trace Lab record - a timeline of messages, model-call cards, groups, usage and flags.

Pure functions: no database, no clock, no mutation of the inputs. The loaders live in
conversations.py. Nothing here writes anywhere (Golden Rule G0).

Read-time only (addendum §2.6): provenance is inferred (provenance.classify), and there are no
gate spans. A post-loop rewrite of the answer is visible only by comparing the last orchestrator
loop model output with the stored answer; the record then carries the flag "inferred_rewrite".

One reading is deliberate: an `agent` span has no message of its own, so it is transparent for
ordering - its children sort among the agent's siblings by seq. The orchestrator loop's model
calls hang under an "orchestrator loop" agent span while its tools hang from the root
(svc-deepagents src/agents/trace.py on_model_start and on_activity); a strict depth-first walk
would list every model call before every tool. `group` still names the agent on every descendant.

evidence_text (the last section) is what an AI proposer is shown about one answer: the asker's question as the screen
shows it, the answer and the steps that are not model calls, each with a handle a quote can point back to.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from src.agents.fiix_credential_parse import _CRED_LINE_PATTERNS

from . import provenance

NORMALIZER_VERSION = "norm-1"
ROLES = ("user", "model_call", "tool_call", "tool_result", "db_query", "plan", "step", "assistant", "system_note")
RESULT_COLLAPSE_CHARS = 240       # longer results start collapsed
DB_ROWS_SHOWN = 10                # rows shown of the <=50 a db span keeps (trace.SQL_ROWS)
TEXT_FLATTEN_CHARS = 2000         # per message in `text`
LOOP_GROUP = "orchestrator loop"  # trace.on_model_start's group for the orchestrator's own model calls
ALWAYS_UNAVAILABLE = ("finish_reason", "response_id", "provider", "time_to_first_token",
                      "tool_call_ids", "cache_write_tokens", "reasoning_tokens")
CHECK_STEP = re.compile(r"^(verify claims|verify figures|check s\d+|step gates)$")
COT_NOTICE = ("Model reasoning is not shown. Each model card shows what the model was sent and what it returned, "
              "as recorded. Hoist does not keep a model's private reasoning and stores no provider summary of it, "
              "so a missing reasoning transcript is not a missing step. The plan, routing decisions, tool calls "
              "and their results are the evidence.")
COT_CARD_NOTE = "Private reasoning: not recorded (not a missing step)."
PUBLISHED_SUMMARY_LABEL = "Provider-published summary — not raw chain-of-thought"

#: Between the page context and the asker's words on the REST path (svc-deepagents orchestrator.py).
QUESTION_MARKER = "\n\n---\n\n**Current user message:**\n"
#: Characters of a question the trace keeps (trace.on_activity).
QUESTION_CHARS = 4000
#: Characters of an answer the trace keeps (trace._write).
ANSWER_CHARS = 20000
REDACTED = "[redacted]"
REASONING_HIDDEN = "[reasoning not shown]"

_MAX_DEPTH = 64
_JSON_PARSE_CHARS = 200_000
_NOT_JSON = object()

# ── redaction ────────────────────────────────────────────────────────────────────────────────

_SAFE_KEYS = frozenset({"password_changed_at", "fiix_credentials_configured", "max_tokens", "token_count"})
_KEY_RE = re.compile(
    r"(^|_)(authorization|proxy_authorization|cookie|set_cookie|password|passwd|pwd|secret|client_secret|"
    r"private_key|credentials?|connection_string|conn_str|dsn)($|_)"
    r"|(^|_)(api|app|access|secret|client|subscription|account|master|storage)_?key($|_)"
    r"|(^|_)(access|refresh|id|auth|bearer|session|sas|csrf|xsrf|api)_?token($|_)"
    r"|^(token|jwt|sig|signature)$")

_VALUE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}=*"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), "[redacted token]"),
    (re.compile(r"\bsk-(?:ant-|proj-|svcacct-|admin-)?[A-Za-z0-9_-]{16,}"), "[redacted key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "[redacted key]"),
    (re.compile(r"(?i)\b(AccountKey|SharedAccessKey|SharedAccessSignature)=[^;\s\"']+"), r"\1=[redacted]"),
    (re.compile(r"(?i)([?&](?:sig|signature|token|access_token|api_key|apikey)=)[^&\s\"']+"), r"\1[redacted]"),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^/\s@]+@"), r"\1:[redacted]@"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?key|app[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|password|passwd|pwd"
                r"|access[_-]?token|refresh[_-]?token)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1\2[redacted]"),
)
#: The Fiix credential lines (app, access and secret keys; the subdomain line is kept).
_FIIX_SECRET_LINES = tuple(p for p, field in _CRED_LINE_PATTERNS if field in ("app_key", "access_key", "secret_key"))
#: The start of a "key": pair inside text (JSON in prose, a cut payload, a Python repr with single or mixed
#: quotes, or JSON escaped inside a JSON string). A quote token is a run of backslashes then a quote
#: (`"`, `\"`, `\\\"` at deeper nesting); the key closes with the same token it opened with.
_KEY_COLON = re.compile(r"""(\\*)(["'])([^"'\\]{1,80})\1\2\s*:\s*""")


def _norm_key(key: Any) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[-.\s]+", "_", s).lower()


def sensitive_key(key: Any) -> bool:
    k = _norm_key(key)
    return k not in _SAFE_KEYS and _KEY_RE.search(k) is not None


def _redact_group1(m: re.Match[str]) -> str:
    whole, start = m.group(0), m.start(0)
    return whole[: m.start(1) - start] + REDACTED + whole[m.end(1) - start:]


def _value_rules(text: str) -> str:
    out = text
    for rx, repl in _VALUE_RULES:
        out = rx.sub(repl, out)
    for rx in _FIIX_SECRET_LINES:
        out = rx.sub(_redact_group1, out)
    return out


def _string_end(text: str, i: int, run: int, quote: str) -> tuple[int, int]:
    """For a string whose content starts at `i`, opened by `run` backslashes and `quote`: the index where
    its closing token starts and that token's length. A cut string closes at the end of the text.
    An unescaped string (run 0) closes at a quote after an even number of backslashes; an escaped one
    (JSON inside a JSON string) closes at exactly `run` backslashes and the quote."""
    j = i
    while True:
        p = text.find(quote, j)
        if p < 0:
            return len(text), 0
        r = 0
        while p - r - 1 >= i and text[p - r - 1] == "\\":
            r += 1
        if (run == 0 and r % 2 == 0) or (run > 0 and r == run):
            return (p - run if run else p), run + 1
        j = p + 1


def _container_end(text: str, i: int) -> int:
    """The index just past the bracket that closes the `{` or `[` at `i`, skipping brackets inside strings
    (escaped or not). A cut container runs to the end of the text."""
    depth, k, n = 0, i, len(text)
    while k < n:
        c = text[k]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return k + 1
        elif c == "\\" or c in "\"'":
            run = 0
            while k < n and text[k] == "\\":
                run += 1
                k += 1
            if k < n and text[k] in "\"'":
                start, length = _string_end(text, k + 1, run, text[k])
                k = start + length
            continue
        k += 1
    return n


def _keys_in_text(text: str) -> str:
    """Redact the value after every sensitive "key": pair, wherever it sits in the text. A string value
    is replaced by the marker (to the closing quote, or the end of a cut text); an object or array value
    is replaced whole, up to its matching bracket (or the end of a cut text). A pair with a harmless key
    is left alone and its value is scanned too, so a secret inside JSON that was escaped into a string
    value is still found."""
    out: list[str] = []
    pos = copied = 0
    while True:
        m = _KEY_COLON.search(text, pos)
        if m is None:
            break
        pos = m.end()
        if pos >= len(text) or not sensitive_key(m.group(3)):
            continue
        key_token = m.group(1) + m.group(2)
        head = f"{key_token}{m.group(3)}{key_token}: "
        if text[pos] in "{[":
            end = _container_end(text, pos)
            replacement = f"{head}{key_token}{REDACTED}{key_token}"
        else:
            vq = pos
            while vq < len(text) and text[vq] == "\\":
                vq += 1
            if vq >= len(text) or text[vq] not in "\"'":
                continue                      # a number, boolean or null: not a secret string
            start, length = _string_end(text, vq + 1, vq - pos, text[vq])
            end = start + length
            replacement = f"{head}{text[pos:vq + 1]}{REDACTED}{text[start:end]}"
        out.append(text[copied:m.start()])
        out.append(replacement)
        copied = pos = end
    out.append(text[copied:])
    return "".join(out)


def redact_text(text: str) -> str:
    """Secret values out of one string. A bracketed string that is JSON, or a Python literal such as
    str(dict), is parsed, redacted as a structure and written back as JSON. Any other string (JSON in
    prose, a cut payload, an unparseable repr) gets the value rules plus the key-in-text rule."""
    if not isinstance(text, str) or not text:
        return text
    if text.lstrip()[:1] in ("{", "[") and len(text) <= _JSON_PARSE_CHARS:
        parsed: Any = _NOT_JSON
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            try:
                parsed = ast.literal_eval(text.strip())
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                parsed = _NOT_JSON
        if isinstance(parsed, (dict, list, tuple)):
            return json.dumps(redact(parsed), ensure_ascii=False, default=str)
    return _keys_in_text(_value_rules(text))


def redact(value: Any, _depth: int = 0) -> Any:
    """A copy of `value` with sensitive keys' values (non-empty strings, objects, arrays) replaced and
    every string run through redact_text. Booleans, numbers and nulls under a sensitive key are kept."""
    if _depth > _MAX_DEPTH:
        return "[cut: nested too deep]"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if sensitive_key(k) and ((isinstance(v, str) and v != "") or isinstance(v, (Mapping, list, tuple, set, frozenset))):
                out[str(k)] = REDACTED
            else:
                out[str(k)] = redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


# ── small helpers ────────────────────────────────────────────────────────────────────────────

_FAR = datetime.max.replace(tzinfo=timezone.utc)
_NEAR = datetime.min.replace(tzinfo=timezone.utc)


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.isoformat()
    return None if v is None else str(v)


def _sid(v: Any) -> str | None:
    return None if v is None else str(v)


def _i(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(v: Any) -> list[Any]:
    return list(v) if isinstance(v, (list, tuple)) else []


def _seq(s: Mapping[str, Any]) -> int:
    return _i(s.get("seq")) or 0


def _sibling_key(s: Mapping[str, Any]) -> tuple[int, datetime, str]:
    """Siblings sort by (seq, started_at, id): seq is the causal order, and it can repeat after the
    trace absorbs ledger entries into existing spans."""
    return (_seq(s), _ts(s.get("started_at")) or _FAR, str(s.get("id")))


def _parse_structured(s: str) -> Any:
    """A JSON list/object, or a content list the trace stored as its Python repr; _NOT_JSON otherwise."""
    t = s.lstrip()
    if len(s) > _JSON_PARSE_CHARS or t[:1] not in ("[", "{"):
        return _NOT_JSON
    try:
        return json.loads(s)
    except (ValueError, RecursionError):
        if t.startswith(("[{'", "{'")):
            try:
                return ast.literal_eval(t)         # trace.on_model_end stores str(content) for a list
            except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
                return _NOT_JSON
    return _NOT_JSON


# ── reasoning ────────────────────────────────────────────────────────────────────────────────

_THINKING_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
_THINKING_KEYS = frozenset({"encrypted_content", "signature"})


def _strip_text(s: str) -> str:
    parsed = _parse_structured(s)
    if not isinstance(parsed, (list, dict)):
        return s
    stripped = strip_reasoning(parsed)
    return s if stripped == parsed else json.dumps(stripped, ensure_ascii=False, default=str)


def strip_reasoning(value: Any) -> Any:
    """Model reasoning out of a model payload: thinking / redacted_thinking / reasoning blocks and the
    encrypted_content and signature keys become "[reasoning not shown]". A provider-published reasoning
    summary is kept, under PUBLISHED_SUMMARY_LABEL, never as a timeline role."""
    if isinstance(value, str):
        return _strip_text(value)
    if isinstance(value, (list, tuple)):
        return [strip_reasoning(v) for v in value]
    if isinstance(value, Mapping):
        kind = value.get("type")
        if isinstance(kind, str) and kind in _THINKING_TYPES:
            if kind == "reasoning" and value.get("summary"):
                return {"label": PUBLISHED_SUMMARY_LABEL, "summary": strip_reasoning(value.get("summary"))}
            return REASONING_HIDDEN
        return {str(k): (REASONING_HIDDEN if k in _THINKING_KEYS else strip_reasoning(v)) for k, v in value.items()}
    return value


# ── the span tree ────────────────────────────────────────────────────────────────────────────

def _top(span: Mapping[str, Any], by_id: Mapping[str | None, Mapping[str, Any]]) -> str | None:
    """The id of the parentless span a span's chain ends at; None for a broken chain or a cycle."""
    seen: set[str | None] = set()
    cur: Mapping[str, Any] | None = span
    while cur is not None:
        sid = _sid(cur.get("id"))
        if sid in seen:
            return None
        seen.add(sid)
        parent = _sid(cur.get("parent_id"))
        if parent is None:
            return sid
        cur = by_id.get(parent)
    return None


def pick_tree(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[str]]:
    """A second Turn object for one turn_id writes a second tree (the turn insert only upserts the turn
    row). Keep the tree whose root ended_at equals the turn row's, else the latest root."""
    items = [s for s in spans if s.get("id") is not None]
    roots = [s for s in items if s.get("kind") == "turn" and not s.get("parent_id")]
    if len(roots) <= 1:
        return items, []
    want = _ts(turn.get("ended_at"))
    match = [r for r in roots if want is not None and _ts(r.get("ended_at")) == want]
    chosen = match[0] if match else max(roots, key=lambda r: (_ts(r.get("ended_at")) or _NEAR,
                                                               _ts(r.get("started_at")) or _NEAR, str(r.get("id"))))
    by_id = {_sid(s.get("id")): s for s in items}
    others = {_sid(r.get("id")) for r in roots} - {_sid(chosen.get("id"))}
    return [s for s in items if _top(s, by_id) not in others], ["duplicate_trees"]


def _children(spans: Sequence[Mapping[str, Any]], root_id: str | None):
    by_id = {_sid(s.get("id")): s for s in spans if s.get("id") is not None}
    kids: dict[str | None, list[Mapping[str, Any]]] = {}
    for sid, s in by_id.items():
        if sid == root_id:
            continue
        parent = _sid(s.get("parent_id"))
        if parent is None or parent == sid or parent not in by_id:
            parent = root_id                 # a span with no parent on record hangs from the root
        kids.setdefault(parent, []).append(s)
    return kids, by_id


def _expanded(node_id: str | None, group: str | None, kids: Mapping[str | None, list[Mapping[str, Any]]]):
    """node_id's children, with every agent child's own children merged in (recursively), sorted together:
    agent spans have no message, so their children sort among the agent's siblings."""
    out: list[tuple[Mapping[str, Any], str | None]] = []
    stack: list[tuple[str | None, str | None]] = [(node_id, group)]
    seen: set[str | None] = set()
    while stack:
        nid, g = stack.pop()
        for k in kids.get(nid, []):
            kid = _sid(k.get("id"))
            if kid in seen:
                continue
            seen.add(kid)
            out.append((k, g))
            if k.get("kind") == "agent":
                stack.append((kid, str(k.get("name") or g or "")))
    out.sort(key=lambda e: _sibling_key(e[0]))
    return out


def walk(spans: Sequence[Mapping[str, Any]], root_id: str | None) -> list[tuple[Mapping[str, Any], int, str | None]]:
    """Depth-first from the root, siblings by (seq, started_at, id), agent spans transparent. Each entry is
    (span, depth, group): depth counts non-agent ancestors, group names the nearest agent span above."""
    rid = _sid(root_id)
    kids, by_id = _children(spans, rid)
    out: list[tuple[Mapping[str, Any], int, str | None]] = []
    seen: set[str | None] = set()
    root = by_id.get(rid) if rid is not None else None
    stack: list[tuple[Mapping[str, Any], int, str | None]] = []
    if root is not None:
        stack.append((root, 0, None))
    else:
        stack.extend((k, 1, g) for k, g in reversed(_expanded(rid, None, kids)))
    while stack:
        s, depth, group = stack.pop()
        sid = _sid(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        out.append((s, depth, group))
        if s.get("kind") == "agent" and s is not root:
            continue                         # its children were merged into its parent's list
        stack.extend((k, depth + 1, g) for k, g in reversed(_expanded(sid, group, kids)))
    for sid, s in by_id.items():
        if sid not in seen:                  # unreachable from the root (a parent cycle): shown under it
            seen.add(sid)
            out.append((s, 1, None))
    return out


# ── model-call cards ─────────────────────────────────────────────────────────────────────────

_TRUNC_RE = re.compile(r"… \[\d+ more chars\]")


def _is_truncated(v: Any) -> bool:
    """A payload the trace's bound cut (trace._bound: "… [N more chars]" or {"_truncated": ...})."""
    if v is None:
        return False
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return bool(_TRUNC_RE.search(s)) or '"_truncated"' in s


def request_kind(inp: Any) -> str:
    """"exchange" (activity_log.fire_exchange: system prompt + user message), "loop_summary"
    (trace.on_model_start: a LangGraph model run's message summary), "none" or "other"."""
    if isinstance(inp, Mapping):
        if "system_prompt" in inp or "user_message" in inp:
            return "exchange"
        if "last_human_message" in inp:
            return "loop_summary"
        return "other"
    return "none" if inp is None else "other"


def call_label(kind: str | None, name: str | None, request_kind: str, loop_output: bool, requested_tools: int) -> str:
    if kind == "router":
        return "routing decision"
    if request_kind == "loop_summary" or loop_output:
        return "tool decision" if requested_tools else "answer synthesis"
    return str(name or "model")


def _model_payload(v: Any) -> Any:
    return None if v is None else redact(strip_reasoning(v))


def model_call_card(span: Mapping[str, Any], *, turn_id: str, group: str | None,
                    prompt_ref: str | None) -> dict[str, Any]:
    """One llm or router span as a card. Missing fields are null and named in `unavailable`; nothing is
    estimated, and the provider is never derived from the model name."""
    inp, out = span.get("input"), span.get("output")
    kind = "router" if span.get("kind") == "router" else "llm"
    rk = request_kind(inp)
    loop_output = isinstance(out, Mapping) and "tool_calls" in out and "content" in out
    requested = _list(out.get("tool_calls")) if loop_output else []
    if rk == "exchange":
        request: Any = {"system_prompt": inp.get("system_prompt"), "user_message": inp.get("user_message"),
                        "params": inp.get("params")}
    elif rk == "loop_summary":
        request = {"messages": inp.get("messages"), "last_human_message": inp.get("last_human_message"),
                   "tool_results_since_last_human": inp.get("tool_results_since_last_human")}
    elif rk == "other":
        request = inp
    else:
        request = None
    params = inp.get("params") if rk == "exchange" and isinstance(inp.get("params"), Mapping) else None
    model = span.get("model") or None
    tin, tout, tcache = _i(span.get("input_tokens")), _i(span.get("output_tokens")), _i(span.get("cache_read_tokens"))
    present = [x for x in (tin, tout, tcache) if x is not None]
    latency = _i(span.get("latency_ms"))
    cost = _f(span.get("cost_usd"))
    cost_status = "priced" if cost is not None else ("unpriced" if model and present else "unavailable")
    missing = {"model": model is None, "request": request is None, "response": out is None,
               "model_parameters": params is None, "input_tokens": tin is None, "output_tokens": tout is None,
               "cache_read_tokens": tcache is None, "latency": latency is None, "total_cost": cost is None}
    return {
        "span_id": _sid(span.get("id")), "turn_id": turn_id, "call_kind": kind, "group": group,
        "model": model, "provider": None,
        "operation": f"{span.get('agent') or 'orchestrator'} › {span.get('name') or 'model'}",
        "label": call_label(kind, span.get("name"), rk, loop_output, len(requested)),
        "request_kind": rk, "request": _model_payload(request),
        "request_complete": rk in ("exchange", "other"),
        "prompt_ref": prompt_ref if rk == "loop_summary" else None,
        "response": _model_payload(out),
        "model_parameters": None if params is None else redact(params),
        "usage": {"input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                  "total_tokens": sum(present) if present else None},
        "latency_s": None if latency is None else latency / 1000,
        "total_cost": cost, "cost_status": cost_status,
        "status": "error" if span.get("ok") is False else "ok",
        "status_message": redact_text(str(span.get("error"))) if span.get("error") else None,
        "raw": {"input": _model_payload(inp), "output": _model_payload(out)},
        "unavailable": list(ALWAYS_UNAVAILABLE) + [k for k, miss in missing.items() if miss],
        "cot_note": COT_CARD_NOTE,
        "started_at": _iso(span.get("started_at")), "ended_at": _iso(span.get("ended_at")),
        "truncated": _is_truncated(inp) or _is_truncated(out),
    }


# ── conversations, provenance and the asker's words ──────────────────────────────────────────

_SYNTHETIC_PREFIXES = ("golden-", "report-", "docs-", "ing-", "rerun", "skilllab-")
_RERUN_LEAD = re.compile(r"^re-run with corrections \(.*?\):\s*", re.S)


def _blank(session_id: Any) -> bool:
    return session_id is None or str(session_id).strip(" ") == ""      # as Postgres btrim(): spaces only


def session_kind(session_id: str | None, *, thread_exists: bool) -> str:
    """"observed" (a chat thread with this id exists), "synthetic" (an app-made session) or "inferred".
    Mirror of conversations.SESSION_KIND_SQL (a scratch-database test pins parity)."""
    if _blank(session_id):
        return "inferred"
    if str(session_id).startswith(_SYNTHETIC_PREFIXES):
        return "synthetic"
    return "observed" if thread_exists else "inferred"


def group_key(session_id: str | None, turn_id: str) -> str:
    """Mirror of conversations.GROUP_KEY_SQL. The shared 'rerun' session is never one conversation."""
    if _blank(session_id):
        return "t:" + str(turn_id)
    sid = str(session_id)
    if sid.startswith("rerun"):
        return "r:" + str(turn_id)
    return "s:" + sid


def session_label(kind: str, session_id: str | None, *, title: str | None, golden: Mapping[str, Any] | None) -> str:
    """The conversation's name. For an observed session the caller passes the thread title, else the first
    question; synthetic and inferred groups are always labelled as such."""
    sid = "" if session_id is None else str(session_id)
    if _blank(session_id):
        return "Single turn, no session recorded"
    if sid.startswith("rerun"):
        return "Correction re-run"
    if sid.startswith("golden-"):
        g = golden or {}
        return f"Golden run · {g.get('case_key') or '—'} · rep {g.get('rep') or '—'}"
    if sid.startswith("report-"):
        return "Report card run"
    if sid.startswith(("docs-", "ing-")):
        return "Document upload"
    if sid.startswith("skilllab-"):
        return "Skill lab replay"
    if kind == "observed":
        return str(title or "").strip()[:80] or "Untitled conversation"
    return "Session not in chat history"


def _payload(activity_input: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    p = activity_input.get("payload") if isinstance(activity_input, Mapping) else None
    return p if isinstance(p, Mapping) else None


def _provenance(turn: Mapping[str, Any], meta: Mapping[str, Any] | None,
                activity_input: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recorded provenance when the caller holds one (meta; Phase 3's golden runner for its own runs),
    else the read-time inference over the stored row and the activity payload, labelled "inferred"."""
    if meta:
        return {"channel": meta.get("channel") or "chat", "channel_source": meta.get("channel_source") or "recorded",
                "classifier_version": meta.get("classifier_version"), "origin": meta.get("origin"),
                "origin_params": redact(meta.get("origin_params")) if meta.get("origin_params") is not None else None,
                "page": meta.get("page"),
                "context_text": redact_text(meta.get("context_text")) if meta.get("context_text") else None,
                "asker_role": meta.get("asker_role"),
                "asker_scope": redact(meta.get("asker_scope")) if meta.get("asker_scope") is not None else None}
    guess = provenance.classify(turn.get("question"), turn.get("session_id"), turn.get("route"), turn.get("answer"),
                                _payload(activity_input))
    return {"channel": guess.channel, "channel_source": guess.source,
            "classifier_version": provenance.CLASSIFIER_VERSION, "origin": guess.origin, "origin_params": None,
            "page": None, "context_text": None, "asker_role": None, "asker_scope": None}


def repair_question(turn: Mapping[str, Any], meta: Mapping[str, Any] | None,
                    activity_input: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """The asker's own words. A recorded meta.asker_question wins; then the earliest activity row's message;
    then the text after the last wrapped-prompt marker. A stored question cut at 4,000 characters before
    its marker keeps its text and says so."""
    stored = str(turn.get("question") or "")
    if meta and isinstance(meta.get("asker_question"), str) and meta.get("asker_question"):
        return meta["asker_question"], []
    payload = _payload(activity_input)
    typed = payload.get("message") if payload is not None else None
    if isinstance(typed, str) and typed.strip() and typed != stored:
        return typed[:QUESTION_CHARS], ["question_repaired"]
    if QUESTION_MARKER in stored:
        return stored.rsplit(QUESTION_MARKER, 1)[1], ["question_repaired"]
    if meta is None and len(stored) >= QUESTION_CHARS:
        return stored, ["question_wrapped"]
    return stored, []


def turn_header(turn: Mapping[str, Any], meta: Mapping[str, Any] | None, *, turn_index: int,
                attempt: Mapping[str, Any], activity_input: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One line of a conversation's turn list: short, redacted, with its (inferred) provenance."""
    question, _ = repair_question(turn, meta, activity_input)
    prov = _provenance(turn, meta, activity_input)
    flags = []
    if turn.get("ok") is False:
        flags.append("failed")
    if str(turn.get("answer") or "") == "gate_interrupt":
        flags.append("workflow_stop")
    return {"turn_id": str(turn.get("turn_id")), "turn_index": turn_index, "started_at": _iso(turn.get("started_at")),
            "question": redact_text(question)[:300], "answer": redact_text(str(turn.get("answer") or ""))[:300],
            "ok": turn.get("ok") is not False, "channel": prov["channel"], "channel_source": prov["channel_source"],
            "origin": prov["origin"], "attempt": attempt.get("attempt", 1),
            "attempt_source": attempt.get("attempt_source"), "retry_of": attempt.get("retry_of"),
            "models": [str(m) for m in _list(turn.get("models"))], "tool_count": _i(turn.get("tool_calls")) or 0,
            "cost_usd": _f(turn.get("cost_usd")), "latency_ms": _i(turn.get("latency_ms")), "flags": flags}


def _norm_question(q: str) -> str:
    return _RERUN_LEAD.sub("", re.sub(r"\s+", " ", (q or "").strip().lower()))


def attempts(turns: Sequence[Mapping[str, Any]], metas: Mapping[str, Mapping[str, Any] | None] | None = None,
             activity_inputs: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """attempt / attempt_source / retry_of for every turn of one conversation, all inferred: a turn is a
    retry of the turn its activity row's `rerun_of` names, else of the latest earlier turn that asked the
    same question (case, spacing and a "Re-run with corrections (...):" lead ignored). The attempt is the
    target's attempt plus 1 when the target is in this conversation, else 2. A first attempt has
    attempt_source None."""
    metas = metas or {}
    acts = activity_inputs or {}
    order = sorted(turns, key=lambda t: (_ts(t.get("started_at")) or _FAR, str(t.get("turn_id"))))
    in_group = {str(t.get("turn_id")) for t in order}
    out: dict[str, dict[str, Any]] = {}
    latest_by_question: dict[str, str] = {}
    for t in order:
        tid = str(t.get("turn_id"))
        act = acts.get(tid)
        payload = _payload(act)
        target = str(payload["rerun_of"]) if payload is not None and payload.get("rerun_of") else None
        question, _ = repair_question(t, metas.get(tid), act)
        normalized = _norm_question(question)
        if target is None and normalized and normalized in latest_by_question:
            target = latest_by_question[normalized]
        if target is None:
            out[tid] = {"attempt": 1, "attempt_source": None, "retry_of": None}
        else:
            prev = out.get(target) if target in in_group else None
            out[tid] = {"attempt": prev["attempt"] + 1 if prev else 2, "attempt_source": "inferred", "retry_of": target}
        if normalized:
            latest_by_question[normalized] = tid
    return out


# ── the record ───────────────────────────────────────────────────────────────────────────────

def _chars(v: Any) -> int:
    if v is None:
        return 0
    return len(v) if isinstance(v, str) else len(json.dumps(v, ensure_ascii=False, default=str))


def block_text(span: Mapping[str, Any], field: str) -> str:
    """One span field's annotatable text exactly as the timeline shows it: the value normalize_turn gives
    that span's message, redacted; strings as they are, anything else as indented JSON. Phase 4's
    annotation blocks and evidence snapshots read this."""
    if field not in ("input", "output"):
        raise ValueError(f"not a span field: {field!r}")
    kind = str(span.get("kind") or "")
    raw = span.get(field)
    o_ = raw if isinstance(raw, Mapping) else {}
    if field == "output" and kind in ("llm", "router"):
        value = _model_payload(raw)
    elif field == "output" and kind == "db":
        value = redact(_list(o_.get("rows"))[:50])
    elif field == "output" and kind == "plan":
        value = redact(o_.get("plan"))
    else:
        value = redact(raw)
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _msg(turn_id: str, span: Mapping[str, Any] | None, role: str, part: str, depth: int, group: str | None,
         field: str | None) -> dict[str, Any]:
    sid = _sid(span.get("id")) if span is not None else None
    return {"id": f"{turn_id}:{sid or 'turn'}:{part}", "role": role, "turn_id": turn_id, "span_id": sid,
            "parent_span_id": _sid(span.get("parent_id")) if span is not None else None, "depth": depth,
            "seq": _i(span.get("seq")) if span is not None else None, "group": group, "label": None,
            "started_at": _iso(span.get("started_at")) if span is not None else None,
            "ended_at": _iso(span.get("ended_at")) if span is not None else None,
            "latency_ms": _i(span.get("latency_ms")) if span is not None else None,
            "ok": span.get("ok") is not False if span is not None else True,
            "error": redact_text(str(span.get("error"))) if span is not None and span.get("error") else None,
            "anchor": {"turn_id": turn_id, "span_id": sid, "field": field} if field else None,
            "block": block_text(span, field) if span is not None and field in ("input", "output") else None,
            "collapse": False,
            "truncated": span is not None and (_is_truncated(span.get("input")) or _is_truncated(span.get("output")))}


def _scope(span: Mapping[str, Any], by_id: Mapping[str | None, Mapping[str, Any]], root_id: str | None) -> str | None:
    """The nearest non-agent ancestor: where a model's tool request and the tool span it ran as both live."""
    seen: set[str] = set()
    parent = _sid(span.get("parent_id"))
    while parent is not None and parent not in seen:
        seen.add(parent)
        p = by_id.get(parent)
        if p is None:
            return root_id
        if p.get("kind") != "agent":
            return parent
        parent = _sid(p.get("parent_id"))
    return root_id


def _content_text(content: Any) -> str:
    """The text a loop model call returned: a plain string, or the text blocks of a content list the
    trace stored as JSON or as its Python repr (thinking blocks are never text)."""
    if not isinstance(content, str):
        return ""
    parsed = _parse_structured(content)
    if isinstance(parsed, list):
        parts = []
        for block in parsed:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return content


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _loop_answer(walked: Sequence[tuple[Mapping[str, Any], int, str | None]]) -> str | None:
    """The text of the orchestrator loop's LAST model call when that call requested no tool (its answer),
    else None. None too when the recorded content was cut by the trace's bound: no fair comparison."""
    last: Mapping[str, Any] | None = None
    for s, _, group in walked:
        out = s.get("output")
        if s.get("kind") == "llm" and group == LOOP_GROUP and isinstance(out, Mapping) \
                and "content" in out and "tool_calls" in out:
            last = out
    if last is None or _list(last.get("tool_calls")) or _is_truncated(last.get("content")):
        return None
    text = _content_text(last.get("content"))
    return text if text.strip() else None


def features(turn: Mapping[str, Any], messages: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Sampling features, from the turn row's totals, never from summing spans."""
    tools = [str(t) for t in _list(turn.get("tools"))]
    has_db = any(m.get("role") == "db_query" for m in (messages or []))
    retrieval = has_db or any(t.startswith("retrieve_") or "search" in t or "doc_rag" in t for t in tools)
    tin, tout, tcache = (_i(turn.get(k)) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    return {"turn_count": 2, "tool_call_count": _i(turn.get("tool_calls")) or 0, "distinct_tools": len(set(tools)),
            "has_retrieval": 1 if retrieval else 0, "tokens": tin + tout + tcache,
            "llm_calls": _i(turn.get("llm_calls")) or 0, "cost_usd": _f(turn.get("cost_usd")) or 0.0,
            "latency_ms": _i(turn.get("latency_ms")) or 0}


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def _flat(m: Mapping[str, Any]) -> str:
    role = m.get("role")
    if role in ("user", "assistant"):
        return str(m.get("text") or "")
    if role == "model_call":
        return f"{m.get('label') or ''} {_as_text((m.get('model_call') or {}).get('response'))}".strip()
    if role == "tool_call":
        return f"{m.get('name')} {_as_text(m.get('arguments'))}"
    if role == "tool_result":
        return _as_text(m.get("content"))
    if role == "db_query":
        return f"{m.get('label')} · {m.get('row_count')} rows"
    if role == "plan":
        return _as_text(m.get("plan"))
    if role == "step":
        return f"{m.get('name')} {_as_text(m.get('output'))}"
    return str(m.get("label") or "")


def flatten_text(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"{m.get('role')}: {_flat(m)[:TEXT_FLATTEN_CHARS]}" for m in messages)


def normalize_turn(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]], *,
                   meta: Mapping[str, Any] | None = None,
                   activity_input: Mapping[str, Any] | None = None,
                   conversation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """turn = one agent_trace_turns row; spans = its agent_trace_spans rows in any order; meta = recorded
    provenance the caller holds (None for live turns: provenance is inferred); activity_input = {"payload",
    "created_at"} of the turn's earliest orchestrator/turn/input activity row, or None; conversation =
    {key, session_kind, session_label, turn_index, turn_count, attempt, attempt_source, retry_of, run_id,
    thread_deleted}. Returns the Trace Lab record."""
    turn_id = str(turn.get("turn_id") or "")
    flags: list[str] = []

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    tree, tree_flags = pick_tree(turn, spans) if spans else ([], [])
    for f in tree_flags:
        flag(f)
    if not spans:
        flag("spans_purged")
    root = next((s for s in tree if s.get("kind") == "turn" and not s.get("parent_id")), None)
    root_id = _sid(root.get("id")) if root is not None else None
    by_id = {_sid(s.get("id")): s for s in tree}
    if any(s is not root and s.get("parent_id") is not None and _sid(s.get("parent_id")) not in by_id for s in tree):
        flag("orphan_spans")
    walked = walk(tree, root_id) if tree else []

    question, q_flags = repair_question(turn, meta, activity_input)
    for f in q_flags:
        flag(f)
    prov = _provenance(turn, meta, activity_input)
    answer = str(turn.get("answer") or "")

    prompt_ref = next((_sid(s.get("id")) for s in tree if s.get("kind") == "stage" and s.get("name") == "prompt assembled"), None)
    kids_count: dict[str | None, int] = {}
    for s in tree:
        p = _sid(s.get("parent_id"))
        kids_count[p] = kids_count.get(p, 0) + 1

    messages: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    tool_calls_by_span: dict[str | None, dict[str, Any]] = {}

    user = _msg(turn_id, root, "user", "user", 0, None, "question")
    user.update(text=redact_text(question), origin=prov["origin"], channel=prov["channel"], latency_ms=None,
                ok=True, error=None, truncated=False)
    messages.append(user)
    for s, depth, group in walked:
        if s is root:
            continue
        kind = str(s.get("kind") or "")
        name = str(s.get("name") or "")
        sid = _sid(s.get("id"))
        inp, out = s.get("input"), s.get("output")
        i_ = inp if isinstance(inp, Mapping) else {}
        o_ = out if isinstance(out, Mapping) else {}
        if kind == "agent":
            groups.append({"span_id": sid, "name": name, "parent_id": _sid(s.get("parent_id")),
                           "cost_usd": _f(s.get("cost_usd")), "cost_is_whole_run": True,
                           "latency_ms": _i(s.get("latency_ms")), "input_tokens": _i(s.get("input_tokens")),
                           "output_tokens": _i(s.get("output_tokens")), "child_count": kids_count.get(sid, 0)})
            continue
        if kind in ("llm", "router"):
            card = model_call_card(s, turn_id=turn_id, group=group,
                                   prompt_ref=prompt_ref if group == LOOP_GROUP else None)
            model_calls.append(card)
            m = _msg(turn_id, s, "model_call", "call", depth, group, "output")
            m.update(call_kind=card["call_kind"], label=card["label"], model_call=card, cot_note=COT_CARD_NOTE)
            messages.append(m)
        elif kind == "tool":
            call = _msg(turn_id, s, "tool_call", "call", depth, group, None)
            call.update(name=name, label=name, arguments=redact(inp), requested_by=None)
            content = redact(out)
            chars = _chars(content)
            result = _msg(turn_id, s, "tool_result", "result", depth, group, "output")
            result.update(name=name, label=name, content=content, chars=chars, collapse=chars > RESULT_COLLAPSE_CHARS)
            tool_calls_by_span[sid] = call
            messages.extend([call, result])
        elif kind == "db":
            rows = redact(_list(o_.get("rows"))[:50])
            m = _msg(turn_id, s, "db_query", "query", depth, group, "output")
            m.update(sql=redact_text(str(i_.get("sql") or "")), params=redact(i_.get("params") or {}),
                     label=str(i_.get("label") or name), row_count=_i(o_.get("row_count")), rows=rows,
                     rows_shown=DB_ROWS_SHOWN, rows_truncated=_i(o_.get("truncated")) or 0,
                     collapse=_chars(rows) > RESULT_COLLAPSE_CHARS)
            messages.append(m)
        elif kind == "plan":
            m = _msg(turn_id, s, "plan", "plan", depth, group, "output")
            m.update(label=name, source=i_.get("source"), plan=redact(o_.get("plan")), rejected=o_.get("rejected"))
            messages.append(m)
        elif kind == "step":
            output = redact(out)
            m = _msg(turn_id, s, "step", "step", depth, group, "output")
            m.update(name=name, label=name, subtype="check" if CHECK_STEP.match(name) else "step",
                     input=redact(inp), output=output, collapse=_chars(output) > RESULT_COLLAPSE_CHARS)
            messages.append(m)
        elif kind == "stage" and name == "prompt assembled":
            m = _msg(turn_id, s, "system_note", "note", depth, group, "output")
            m.update(subtype="prompt", name=name, label="What the model was given", input=redact(inp),
                     output=redact(out), collapse=True)
            messages.append(m)
        else:
            body = {"input": redact(inp), "output": redact(out)}
            m = _msg(turn_id, s, "system_note", "note", depth, group, "output")
            m.update(subtype="stage" if kind == "stage" else "unknown_kind", name=name,
                     label=name if kind == "stage" else f"{kind}: {name}", input=body["input"],
                     output=body["output"], collapse=_chars(body) > RESULT_COLLAPSE_CHARS)
            messages.append(m)

    # A model's requested tools (names only, no ids) take the next unclaimed same-name tool span with a later
    # seq in the same scope (agent spans are transparent there too).
    tools = [s for s, _, _ in walked if s.get("kind") == "tool"]
    claimed: set[str | None] = set()
    for s, _, _ in walked:
        if s.get("kind") != "llm":
            continue
        o_ = s.get("output")
        for req in (_list(o_.get("tool_calls")) if isinstance(o_, Mapping) else []):
            want = req.get("name") if isinstance(req, Mapping) else None
            if not want:
                continue
            here = _scope(s, by_id, root_id)
            cands = [t for t in tools if t.get("name") == want and _sid(t.get("id")) not in claimed
                     and _seq(t) > _seq(s) and _scope(t, by_id, root_id) == here]
            if not cands:
                flag("unexecuted_tool_requests")
                continue
            pick = min(cands, key=_sibling_key)
            claimed.add(_sid(pick.get("id")))
            tool_calls_by_span[_sid(pick.get("id"))]["requested_by"] = _sid(s.get("id"))
    # Activity-row tools pair by name (trace.on_activity): two same-name tools overlapping under one parent
    # may carry each other's outputs.
    same: dict[tuple[str | None, str], list[Mapping[str, Any]]] = {}
    for t in tools:
        same.setdefault((_sid(t.get("parent_id")), str(t.get("name"))), []).append(t)
    for group_tools in same.values():
        for i, a in enumerate(group_tools):
            for b in group_tools[i + 1:]:
                a0, a1, b0, b1 = (_ts(a.get("started_at")), _ts(a.get("ended_at")),
                                  _ts(b.get("started_at")), _ts(b.get("ended_at")))
                if a0 and a1 and b0 and b1 and a0 < b1 and b0 < a1:
                    flag("concurrent_same_name_tools")

    stop = answer == "gate_interrupt"
    # A post-loop rewrite is seen only by comparison, read time (addendum §2.6): labelled inferred.
    loop_answer = _loop_answer(walked)
    rewritten = (loop_answer is not None and not stop and turn.get("ok") is not False
                 and len(answer) < ANSWER_CHARS and _squash(loop_answer) != _squash(answer))
    assistant = _msg(turn_id, root, "assistant", "answer", 0, None, "answer")
    assistant.update(text="Stopped at an approval gate" if stop else redact_text(answer),
                     subtype="workflow_stop" if stop else ("error" if turn.get("ok") is False else "answer"),
                     rewrite={"source": "inferred", "loop_answer": redact_text(loop_answer)} if rewritten else None,
                     ok=turn.get("ok") is not False,
                     error=redact_text(str(turn.get("error"))) if turn.get("error") else None,
                     latency_ms=_i(turn.get("latency_ms")), truncated=False)
    messages.append(assistant)

    if rewritten:
        flag("inferred_rewrite")
    if any(m.get("truncated") for m in messages):
        flag("truncated")
    if turn.get("cost_complete") is False:
        flag("cost_incomplete")
    if turn.get("ok") is False:
        flag("failed")
    if stop:
        flag("workflow_stop")
    asker_scope = prov.get("asker_scope") if isinstance(prov.get("asker_scope"), Mapping) else {}
    if asker_scope.get("view_as") is True:
        flag("view_as")
    conv = dict(conversation or {})
    if conv.get("thread_deleted"):
        flag("thread_deleted")

    session_id = turn.get("session_id")
    golden = None
    if meta and meta.get("golden_run_id"):
        golden = {"run_id": str(meta.get("golden_run_id")), "case_key": meta.get("golden_case_key"),
                  "rep": _i(meta.get("rep"))}
    kind_ = conv.get("session_kind") or session_kind(session_id, thread_exists=False)
    key = conv.get("key") or group_key(session_id, turn_id)
    run_id = golden["run_id"] if golden else str(conv.get("run_id") or "live")
    label = conv.get("session_label") or session_label(kind_, session_id, title=None, golden=golden)
    tin, tout, tcache = (_i(turn.get(k)) or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    feedback = None
    if turn.get("feedback_rating") or turn.get("feedback_comment"):
        by = turn.get("feedback_by")
        feedback = {"rating": turn.get("feedback_rating"),
                    "comment": redact_text(turn.get("feedback_comment")) if turn.get("feedback_comment") else None,
                    "by_asker": by is not None and _sid(by) == _sid(turn.get("user_id")),
                    "at": _iso(turn.get("feedback_at"))}
    return {
        "id": turn_id, "trace_id": turn_id, "normalizer_version": NORMALIZER_VERSION,
        "timestamp": _iso(turn.get("started_at")), "ended_at": _iso(turn.get("ended_at")),
        "created_at": _iso(turn.get("created_at")),
        "models": [str(x) for x in _list(turn.get("models"))], "tools": [str(x) for x in _list(turn.get("tools"))],
        "input": redact_text(question), "output": redact_text(answer),
        "ok": turn.get("ok") is not False,
        "error": redact_text(str(turn.get("error"))) if turn.get("error") else None,
        "messages": messages, "model_calls": model_calls, "groups": groups,
        "usage": {"input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                  "total_tokens": tin + tout + tcache, "cost_usd": _f(turn.get("cost_usd")) or 0.0,
                  "cost_complete": turn.get("cost_complete") is not False,
                  "llm_calls": _i(turn.get("llm_calls")) or 0, "tool_calls": _i(turn.get("tool_calls")) or 0,
                  "latency_ms": _i(turn.get("latency_ms")) or 0},
        "features": features(turn, messages),
        "meta": {"session_id": session_id, "session_kind": kind_, "session_label": label, "conversation_key": key,
                 "run_id": run_id, "case_key": golden["case_key"] if golden else None,
                 "rep": golden["rep"] if golden else None, "golden": golden,
                 "channel": prov["channel"], "channel_source": prov["channel_source"],
                 "classifier_version": prov["classifier_version"], "origin": prov["origin"],
                 "origin_params": prov["origin_params"], "page": prov["page"], "context_text": prov["context_text"],
                 "turn_index": conv.get("turn_index", 1), "turn_count": conv.get("turn_count", 1),
                 "attempt": conv.get("attempt", 1), "attempt_source": conv.get("attempt_source"),
                 "retry_of": conv.get("retry_of"), "route": turn.get("route"),
                 "asker": {"user_id": _sid(turn.get("user_id")), "email": turn.get("email"),
                           "role": prov["asker_role"], "scope": prov["asker_scope"]},
                 "organization_id": _sid(turn.get("organization_id"))},
        "feedback": feedback,
        "text": flatten_text(messages),
        "flags": flags,
        "cot_boundary": COT_NOTICE,
        "permalink": "?tl=conversations&run=" + quote(run_id, safe="") + "&conv=" + quote(key, safe="")
                     + "&turn=" + quote(turn_id, safe=""),
    }


# ── what a proposer reads about one answer (Phase 5, spec Review §7.5) ───────────────────────

#: The steps a proposer is shown: the routing decision, the plan, its steps, tool calls and records read. Never an
#: `llm` call, the assembled prompt (`stage`) or an `agent` group. The chat records no gate span (Golden Rule G0), so
#: none is listed.
EVIDENCE_KINDS = ("router", "plan", "step", "tool", "db")
EVIDENCE_CAP = 24000
EVIDENCE_QUESTION_MAX = 4000
EVIDENCE_ANSWER_MAX = 8000
EVIDENCE_EARLIER_ANSWER_MAX = 1500
EVIDENCE_INPUT_MAX = 1500
EVIDENCE_OUTPUT_MAX = 3000
EVIDENCE_ERROR_MAX = 300
EVIDENCE_EARLIER_TURNS = 2


def evidence_text(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]], earlier: Sequence[Mapping[str, Any]], *,
                  cap: int = EVIDENCE_CAP, meta: Mapping[str, Any] | None = None,
                  activity_input: Mapping[str, Any] | None = None) -> tuple[str, dict[str, dict]]:
    """The text a proposer reads about one answer, plus a map from location handle ("question", "answer", "S1",
    "S1.in", ...) to {"span_id", "field", "text"} holding exactly the text shown, so a quote can be checked verbatim.

    The question is the asker's own words as the Conversations screen shows them (repair_question: a recorded
    `meta.asker_question`, else the earliest activity row's message, else the text after the wrapped-prompt marker,
    then redacted), and so are the questions of the earlier turns. A step's text is block_text, the same text the
    screen offers to annotate. Excludes llm spans, the assembled prompt and agent groups; never includes labels,
    notes, verdicts, sampling data or the asker's email (the turn's other columns are never read). Pure."""
    question = redact_text(repair_question(turn, meta, activity_input)[0])[:EVIDENCE_QUESTION_MAX]
    answer = redact_text(str(turn.get("answer") or ""))[:EVIDENCE_ANSWER_MAX]
    locations: dict[str, dict] = {"question": {"span_id": None, "field": "question", "text": question},
                                  "answer": {"span_id": None, "field": "answer", "text": answer}}
    head: list[str] = []
    if earlier:
        head.append("EARLIER IN THIS CONVERSATION")
        for e in list(earlier)[-EVIDENCE_EARLIER_TURNS:]:
            head.append("Q: " + redact_text(repair_question(e, None, None)[0]))
            head.append("A: " + redact_text(str(e.get("answer") or ""))[:EVIDENCE_EARLIER_ANSWER_MAX])
        head.append("")
    head += ["QUESTION", question, "", "ANSWER", answer, ""]
    if turn.get("ok") is False:
        head += ["THE TURN FAILED: " + redact_text(str(turn.get("error") or ""))[:EVIDENCE_ERROR_MAX], ""]
    head.append("WHAT THE ASSISTANT DID")
    out = "\n".join(head)
    n = 0
    ordered = sorted((s for s in spans or [] if s.get("kind") in EVIDENCE_KINDS),
                     key=lambda s: (s.get("seq") or 0, str(s.get("started_at") or ""), str(s.get("id") or "")))
    for s in ordered:
        handle = f"S{n + 1}"
        agent = s.get("agent")
        lines = [f'[{handle}] {s.get("kind")} "{s.get("name") or ""}"' + (f" ({agent})" if agent else "")]
        tin = block_text(s, "input")[:EVIDENCE_INPUT_MAX]
        tout = block_text(s, "output")[:EVIDENCE_OUTPUT_MAX]
        if tin:
            lines.append(f"  input ({handle}.in): {tin}")
        if tout:
            lines.append(f"  output ({handle}): {tout}")
        if s.get("error"):
            lines.append("  error: " + redact_text(str(s["error"]))[:EVIDENCE_ERROR_MAX])
        chunk = "\n".join(lines)
        if len(out) + 1 + len(chunk) > cap:
            break
        out += "\n" + chunk
        n += 1
        if tin:
            locations[f"{handle}.in"] = {"span_id": str(s.get("id")), "field": "input", "text": tin}
        if tout:
            locations[handle] = {"span_id": str(s.get("id")), "field": "output", "text": tout}
    return out, locations
