"""Code checks: deterministic verdicts over one answer, with no model call (spec section 2 of
"Judges, code checks, monitoring, eval cases and release check").

A check reads one NormalizedTurn (plus a case check's parameters) and returns pass, fail,
undecidable or not_applicable. A crash is undecidable, never a pass. Every behaviour change bumps
the check's version: the version is part of the evaluator key and of a code judge's prompt_hash,
so a changed check is a new evaluator and must be validated again.

NormalizedTurn is the one view of a turn that checks and judges read. turn_from_rows builds it
from the stored rows (the trace turn row, its spans, its turn_meta row). llm spans are left out on
purpose: a judge never reads the model's own messages (the HW5 leakage rule).

Golden Rule G0: Trace Lab changes nothing on the chat path, so no gate spans are captured. There
is no gate_draft_failed check, NormalizedTurn.gates stays empty, and gate_withheld recognises the
answer check's own wording in the final answer only.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal, Mapping, Sequence

from src.agents.planner import verify as planner_verify, verify_claims as planner_verify_claims
from src.agents.udr_response_evaluator import ungrounded_identifiers

CheckStatus = Literal["pass", "fail", "undecidable", "not_applicable"]
STATUSES = ("pass", "fail", "undecidable", "not_applicable")
FINDINGS_MAX = 8
FINDING_CHARS = 300
ANSWER_CAP = 20_000             # trace._write stores answer[:20000] (svc-deepagents src/agents/trace.py)
GATE_INTERRUPT = "gate_interrupt"
_CUT_MARK = re.compile(r"… \[\d+ more chars\]$")        # trace._bound (src/agents/trace.py)
#: The answer check's own wording on the final answer, lower-cased
#: (svc-deepagents src/agents/udr_response_evaluator.py: could-not-run note, flagged note, withheld text).
LEGACY_GATE_TEXTS = (
    "automated accuracy check could not run",
    "flagged for a quick human verification",
    "couldn't fully verify an answer",
)
_INABILITY = re.compile(
    r"\b(?:i\s+can(?:'|’)?t|i\s+cannot|i(?:'|’)m\s+(?:unable|not\s+able)\s+to|i\s+am\s+(?:unable|not\s+able)\s+to"
    r"|i\s+don(?:'|’)?t\s+have\s+access|i\s+do\s+not\s+have\s+access|unable\s+to\s+help)\b", re.I)


@dataclass(frozen=True)
class NormalizedTurn:
    turn_id: str
    session_id: str | None = None
    organization_id: str | None = None
    user_id: str | None = None
    started_at: datetime | None = None
    question: str = ""                      # the asker's own words (D6b)
    answer: str = ""                        # what the user saw, at most 20,000 characters
    answer_truncated: bool = False
    ok: bool = True
    error: str | None = None
    route: str | None = None
    page_context: str | None = None
    channel: str | None = None
    golden_case_key: str | None = None
    golden_run_id: str | None = None
    rep: int | None = None
    session_kind: str = "inferred"
    prior: tuple[dict[str, Any], ...] = ()  # [{turn_id, question, answer}], same observed session, oldest first
    tools: tuple[dict[str, Any], ...] = ()  # [{span_id, seq, name, agent, input, output, error, truncated}]
    db: tuple[dict[str, Any], ...] = ()     # [{span_id, seq, label, sql, rows, row_count, error, truncated}]
    cards: tuple[dict[str, Any], ...] = ()  # [{seq, name, payload}]; the trace keeps no card payloads
    gates: tuple[dict[str, Any], ...] = ()  # always empty under G0 (no gate spans are captured); kept for the shape
    spans_purged: bool = False
    tool_calls: int = 0
    feedback_rating: str | None = None
    feedback_comment: str | None = None


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    findings: tuple[str, ...] = ()          # plain sentences, at most 8, each at most 300 characters
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def failure_present(self) -> int | None:    # 1 = failure present; None = no verdict
        return {"fail": 1, "pass": 0}.get(self.status)


@dataclass(frozen=True)
class CodeCheck:
    key: str                                # snake_case, stable forever
    version: int                            # bump on ANY behaviour change
    label: str                              # UI text; plain English; no internal names
    description: str
    scope: Literal["live", "case"]          # live: no params, every chat answer; case: needs params
    fn: Callable[[NormalizedTurn, dict[str, Any]], CheckResult]

    @property
    def evaluator_key(self) -> str:
        return f"code:{self.key}@v{self.version}"


def is_truncated(value: Any, _depth: int = 0) -> bool:
    """The three cut markers Hoist writes: a string ending in "… [N more chars]", a dict holding
    "_truncated" (both trace._bound), and a query span output with truncated > 0 (only the first
    50 rows kept). Nested values are searched."""
    if _depth > 6:
        return False
    if isinstance(value, str):
        return bool(_CUT_MARK.search(value))
    if isinstance(value, dict):
        if "_truncated" in value:
            return True
        t = value.get("truncated")
        if isinstance(t, int) and not isinstance(t, bool) and t > 0:
            return True
        return any(is_truncated(v, _depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(is_truncated(v, _depth + 1) for v in value[:200])
    return False


def _jsonish(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def turn_from_rows(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]],
                   meta: Mapping[str, Any] | None = None, *, session_kind: str = "inferred",
                   prior: Sequence[Mapping[str, Any]] = ()) -> NormalizedTurn:
    """The turn view from stored rows. Pure. `turn` is an agent_trace_turns row, `spans` its span rows
    in any order, `meta` its turn_meta row (or None), `prior` up to two earlier turns of its observed
    conversation."""
    m = meta or {}
    tools: list[dict[str, Any]] = []
    db: list[dict[str, Any]] = []
    for s in sorted(spans, key=lambda x: (int(x.get("seq") or 0), str(x.get("id") or ""))):
        kind = s.get("kind")
        seq = int(s.get("seq") or 0)
        sid = str(s.get("id") or "")
        inp, out = _jsonish(s.get("input")), _jsonish(s.get("output"))
        if kind == "tool":
            tools.append({"span_id": sid, "seq": seq, "name": str(s.get("name") or ""), "agent": s.get("agent"),
                          "input": inp, "output": out, "error": s.get("error"),
                          "truncated": is_truncated(inp) or is_truncated(out)})
        elif kind == "db":
            i = inp if isinstance(inp, dict) else {}
            o = out if isinstance(out, dict) else {}
            db.append({"span_id": sid, "seq": seq, "label": i.get("label") or s.get("name") or "",
                       "sql": str(i.get("sql") or ""), "rows": o.get("rows") or [], "row_count": o.get("row_count"),
                       "error": s.get("error"), "truncated": is_truncated(o) or is_truncated(i)})
    answer = str(turn.get("answer") or "")
    started = turn.get("started_at")
    return NormalizedTurn(
        turn_id=str(turn.get("turn_id") or ""), session_id=turn.get("session_id"),
        organization_id=str(turn["organization_id"]) if turn.get("organization_id") else None,
        user_id=str(turn["user_id"]) if turn.get("user_id") else None,
        started_at=started if isinstance(started, datetime) else None,
        question=str(m.get("asker_question") or turn.get("question") or ""),
        answer=answer, answer_truncated=len(answer) >= ANSWER_CAP or is_truncated(answer),
        ok=bool(turn.get("ok", True)), error=turn.get("error"), route=turn.get("route"),
        page_context=m.get("context_text"), channel=m.get("channel"), golden_case_key=m.get("golden_case_key"),
        golden_run_id=str(m["golden_run_id"]) if m.get("golden_run_id") else None, rep=m.get("rep"),
        session_kind=session_kind,
        prior=tuple({"turn_id": str(p.get("turn_id") or ""), "question": str(p.get("question") or ""),
                     "answer": str(p.get("answer") or "")} for p in prior),
        tools=tuple(tools), db=tuple(db), spans_purged=not spans,
        tool_calls=int(turn.get("tool_calls") or 0), feedback_rating=turn.get("feedback_rating"),
        feedback_comment=turn.get("feedback_comment"))


# ── helpers over the turn view ───────────────────────────────────────────────────────────────

def _outputs(turn: NormalizedTurn) -> list[Any]:
    """What the turn's steps returned: tool outputs and query rows, in order."""
    outs: list[Any] = [t["output"] for t in turn.tools if t.get("output") not in (None, "", [], {})]
    outs += [{"row_count": d.get("row_count"), "rows": d.get("rows")} for d in turn.db
             if d.get("rows") or d.get("row_count")]
    return outs


def _any_cut(turn: NormalizedTurn) -> bool:
    return turn.spans_purged or any(t.get("truncated") for t in turn.tools) or any(d.get("truncated") for d in turn.db)


def _results(outs: list[Any]) -> dict[str, dict[str, Any]]:
    return {f"S{i}": {"ok": True, "output": o} for i, o in enumerate(outs, 1)}


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


# ── the checks ───────────────────────────────────────────────────────────────────────────────

def _no_answer(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    a = (turn.answer or "").strip()
    if not turn.ok:
        return CheckResult("fail", ("The turn failed" + (f": {turn.error}" if turn.error else "") + ".",))
    if not a:
        return CheckResult("fail", ("The answer is empty.",))
    if a == GATE_INTERRUPT:
        return CheckResult("fail", ("The answer stopped at an approval step.",))
    return CheckResult("pass")


def _invented_identifiers(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    if not (turn.answer or "").strip():
        return CheckResult("not_applicable")
    missing = ungrounded_identifiers(turn.answer, _outputs(turn), turn.question)
    if not missing:
        return CheckResult("pass")
    if _any_cut(turn):
        return CheckResult("undecidable", (f"Not found in what was kept: {', '.join(missing[:8])}; "
                                           "some results were cut or have aged out.",), {"codes": missing})
    return CheckResult("fail", tuple(f"Names {c}, which no step returned and the asker did not type." for c in missing),
                       {"codes": missing})


def _untraced_figures(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    outs = _outputs(turn)
    if not outs and not turn.spans_purged:
        return CheckResult("not_applicable")
    res = planner_verify(turn.answer or "", _results(outs))
    if res["ok"]:
        return CheckResult("pass", (), {"figures_checked": res["figures_checked"]})
    if _any_cut(turn):
        return CheckResult("undecidable", (f"Not found in what was kept: {', '.join(res['unmatched'][:8])}; "
                                           "some results were cut or have aged out.",), dict(res))
    return CheckResult("fail", tuple(f"States {f}, which no step returned." for f in res["unmatched"]), dict(res))


def _contradicts_results(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    outs = [t["output"] for t in turn.tools if t.get("output") not in (None, "", [], {}) and not t.get("error")]
    outs += [{"row_count": d.get("row_count"), "rows": d.get("rows")} for d in turn.db
             if (d.get("rows") or d.get("row_count")) and not d.get("error")]
    if not outs:
        return CheckResult("not_applicable")
    found = planner_verify_claims(turn.answer or "", turn.question or "", _results(outs))
    return CheckResult("fail", tuple(found)) if found else CheckResult("pass")


def _refusal(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    head = (turn.answer or "")[:300]
    if not head.strip():
        return CheckResult("not_applicable")
    if "couldn't fully verify an answer" in head.lower():     # the answer check's withheld text, not a refusal
        return CheckResult("pass")
    m = _INABILITY.search(head)
    return CheckResult("fail", (f'Opens with "{m.group(0)}".',)) if m else CheckResult("pass")


def _gate_withheld(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    """G0: no gate spans exist, so this reads only the answer check's own wording in the answer."""
    low = (turn.answer or "").lower()
    hit = next((t for t in LEGACY_GATE_TEXTS if t in low), None)
    if hit:
        return CheckResult("fail", ("The answer carries the answer check's own warning.",), {"legacy_text": hit})
    return CheckResult("not_applicable")


def _expected_values(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    """One implementation of the value check (R36): M3's grammar and matcher."""
    raw = (params or {}).get("values")
    if not raw:
        return CheckResult("not_applicable")
    from . import golden, grading

    items = golden.parse_expected_values(raw) if isinstance(raw, str) else [dict(i) for i in raw]
    if not items:
        return CheckResult("not_applicable")
    det = grading.check_values(items, turn.answer or "", [c.get("payload") for c in turn.cards])
    status = det.get("status")
    if status == "all_found":
        return CheckResult("pass", (), {"det": det})
    if status == "mismatch":
        bad = [i for i in det.get("items") or [] if i.get("ok") is False]
        return CheckResult("fail", tuple(
            (f"Must not state {str(i.get('raw', '')).lstrip('!')}." if str(i.get("raw", "")).startswith("!")
             else f"Expected {i.get('raw')}" + (f"; the nearest figure stated is {i['nearest']}." if i.get("nearest")
                                                  else "; it is not stated."))
            for i in bad), {"det": det})
    if status == "unclear":
        return CheckResult("undecidable", ("A yes or no could not be read from the first sentence.",), {"det": det})
    return CheckResult("not_applicable", (), {"det": det})


def _must_contain(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    want = _norm((params or {}).get("text"))
    if not want:
        return CheckResult("not_applicable")
    if want in _norm(turn.answer):
        return CheckResult("pass")
    return CheckResult("fail", (f'Leaves out "{params["text"]}".',))


def _must_not_contain(turn: NormalizedTurn, params: dict[str, Any]) -> CheckResult:
    banned = _norm((params or {}).get("text"))
    if not banned:
        return CheckResult("not_applicable")
    if banned in _norm(turn.answer):
        return CheckResult("fail", (f'Includes "{params["text"]}".',))
    return CheckResult("pass")


CHECKS: dict[str, CodeCheck] = {c.key: c for c in (
    CodeCheck("no_answer", 1, "Gives no answer",
              "The turn failed, or the answer is empty or stopped at an approval step.", "live", _no_answer),
    CodeCheck("invented_identifiers", 1, "Names a record code that no tool returned",
              "The answer names a code such as AHU-01 that no step returned and the asker did not type.",
              "live", _invented_identifiers),
    CodeCheck("untraced_figures", 1, "States a figure that no tool returned",
              "A figure of two or more digits (not a year) appears in no step result. Derived sums and "
              "percentages also flag, so validate it before counting it.", "live", _untraced_figures),
    CodeCheck("contradicts_results", 1, "Contradicts what its own steps returned",
              "The answer calls a field unavailable while a step returned values for it, or quotes an "
              "unfiltered total as a narrowed count.", "live", _contradicts_results),
    CodeCheck("refusal", 1, "Says it cannot help",
              "The first 300 characters say the assistant cannot help or has no access.", "live", _refusal),
    CodeCheck("gate_withheld", 1, "Answer was withheld or flagged for checking",
              "The answer carries the answer check's own warning, or the text it gives when it withholds an answer.",
              "live", _gate_withheld),
    CodeCheck("expected_values", 1, "Misses an expected figure",
              "An expected value from the case is not stated, or a forbidden one is.", "case", _expected_values),
    CodeCheck("must_contain", 1, "Leaves out required text",
              "The given text is absent (case and spacing ignored).", "case", _must_contain),
    CodeCheck("must_not_contain", 1, "Includes text it must not",
              "The given text is present (case and spacing ignored).", "case", _must_not_contain),
)}


def run_check(check: CodeCheck, turn: NormalizedTurn, params: dict[str, Any] | None = None) -> CheckResult:
    """Run one check. Any exception is undecidable with "check failed: <ExceptionType>"; never a pass."""
    try:
        res = check.fn(turn, dict(params or {}))
    except Exception as exc:  # noqa: BLE001 - a crash is never a pass
        return CheckResult("undecidable", (f"check failed: {type(exc).__name__}",))
    if res.status not in STATUSES:
        return CheckResult("undecidable", (f"check failed: bad status {res.status!r}",))
    return CheckResult(res.status, tuple(str(f)[:FINDING_CHARS] for f in res.findings[:FINDINGS_MAX]),
                       dict(res.detail))


def run_live_checks(turn: NormalizedTurn) -> dict[str, CheckResult]:
    """Every scope='live' check on one turn. Costs nothing and stores nothing."""
    return {k: run_check(c, turn) for k, c in CHECKS.items() if c.scope == "live"}


def code_hash(check_key: str, version: int) -> str:
    """A code judge's prompt_hash: sha256("code:<key>@v<version>")[:12]."""
    return hashlib.sha256(f"code:{check_key}@v{version}".encode("utf-8")).hexdigest()[:12]
