"""Ask a few generated questions for real, and give back what came out.

Item 7 asked for batches of synthetic questions to be RUN and for each question, response, tool
sequence, trace, model, instance context and timestamp to be kept. Generation and the instance
context are done; this is the asking.

It is deliberately small and synchronous. The golden runner is the machine for a full batch --
a job, a step loop driven by the page, a spend cap, pause and resume -- but it grades, and
grading needs an expected answer, which a generated question does not have and must not be given
by a model. Rather than bend that machine, this answers a handful of questions in one request so
the loop can be seen end to end.

What it does NOT do, said plainly rather than left to be found:

* it does not persist. tracelab.jobs.kind has a CHECK constraint and there is no scenario table,
  so keeping these results needs a migration, and a Trace Lab migration is applied by a person.
  The results come back to the caller and are gone. Item 7's "save in Trace Lab" is not met by
  this; the rest of it is.
* it does not grade. There is no expected answer, so there is nothing to grade against. An
  out-of-module question is the one exception that judges itself: the instance must refuse it.

Isolation is the golden runner's own (orchestrator_host.assert_isolated, checked before anything
is asked), and each question is captured by capture.run_message in the space the batch names.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from . import capture, orchestrator_host, scenario_store, scenarios

log = structlog.get_logger(__name__)

#: How many questions one request will ask. A full batch belongs to the golden runner's job and
#: step loop; this is a look, and a look that holds an HTTP request open for ten minutes is not
#: one. Each question is a real chat turn: a model is called and it costs money.
MAX_QUESTIONS = 5


class NotSafe(RuntimeError):
    """The process is not isolated enough to ask a real question in."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


async def run(space: str, *, count: int = 2, seed: int = 0, challenge_ratio: float = 0.0,
              offset: int = 0, batch: int = 0,
              principal: Any = None, organization_id: str | None = None,
              authorization: str | None = None) -> dict[str, Any]:
    """Ask `count` questions of the batch for `space`, starting at `offset`.

    `batch` is how many the batch holds -- the number on screen. The whole batch is generated
    from the seed and then sliced, so asking questions 6 to 10 asks THOSE questions rather than
    regenerating a shorter batch, which would be a different five: the challenge share is a
    fraction of the total, so generate(5) and generate(10) do not share a prefix.
    """
    if count > MAX_QUESTIONS:
        raise ValueError(f"at most {MAX_QUESTIONS} questions in one run")
    if offset < 0:
        raise ValueError("offset cannot be negative")

    problems = orchestrator_host.assert_isolated()
    if problems:
        # Before anything is asked, not after: the point of the check is that no question
        # reaches a model from a process that could write something.
        raise NotSafe(problems)
    await orchestrator_host.ensure_overlays_readonly()

    # `batch` is the batch's declared size, so it is a ceiling as well as a floor: asking past
    # the end of a batch of ten must come back with nothing rather than generating an
    # eleventh question nobody listed. Without one, the batch is just this slice.
    whole = int(batch) if batch else offset + count
    rows = scenarios.generate(space, count=whole, seed=seed, challenge_ratio=challenge_ratio)
    # The slice the caller asked for. A batch the generator could not fill is short, so this can
    # come back with fewer than `count` or empty; `asked` says what actually happened.
    rows = rows[offset:offset + count]
    orchestrator = orchestrator_host.get_orchestrator()
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    out: list[dict[str, Any]] = []
    captured: list[dict[str, Any]] = []

    for s in rows:
        sid = f"scenario-{space}-{uuid.uuid4().hex[:12]}"
        try:
            ct = await capture.run_message(
                orchestrator, question=s.question, session_id=sid, page_context=None,
                principal=principal, organization_id=organization_id,
                authorization=authorization, space=space)
            tid = scenario_store.turn_id_for(sid, str(getattr(ct, "turn_id", "") or sid))
            row = _result(s, ct, sid, space)
            row["turn_id"] = tid
            out.append(row)
            # The turn and its spans travel beside the row: they are the record of this answer,
            # and without them the review page has a question with no steps behind it.
            captured.append({"turn_id": tid, "result": row, "turn": getattr(ct, "turn", None) or {},
                             "spans": list(getattr(ct, "spans", None) or []),
                             "cards": list(getattr(ct, "cards", None) or []),
                             "refused": list(getattr(ct, "refused", None) or [])})
        except Exception as exc:                      # noqa: BLE001 - one question must not end the run
            log.warning("scenario_run.question_failed", scenario=s.scenario_id, error=str(exc)[:300])
            out.append({"scenario_id": s.scenario_id, "question": s.question, "space": space,
                        "in_module": s.in_module, "ok": False, "error": str(exc)[:300],
                        "answer": "", "tools": [], "models": [], "asked_at": _now()})
        finally:
            orchestrator_host.release_session(sid)

    return {
        "space": space, "seed": seed, "asked": len(out),
        "started_at": started.isoformat(), "ms": int((time.perf_counter() - t0) * 1000),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in out), 6),
        "requested": count,
        "offset": offset,
        "batch": whole,
        "challenge_ratio": challenge_ratio,
        "fingerprint": scenarios.fingerprint(rows),
        # Filled in by the caller once the batch is written; False until then, so a reply never
        # claims a run is somewhere to come back to when it is not.
        "persisted": False,
        "results": out,
        # Not part of the reply: the store takes it and the route drops it.
        "captured": captured,
    }


#: How many steps travel back with one answer, and how much of each payload.
MAX_STEPS = 60
PAYLOAD_CHARS = 1200


def _steps(spans: list) -> list[dict[str, Any]]:
    """The span tree as the page draws it: in sequence, with the payloads trimmed."""
    out = []
    for sp in spans[:MAX_STEPS]:
        out.append({
            "id": sp.get("id"), "parent_id": sp.get("parent_id"), "seq": sp.get("seq"),
            "kind": sp.get("kind"), "name": sp.get("name"), "agent": sp.get("agent"),
            "model": sp.get("model"), "ms": sp.get("latency_ms"), "cost_usd": sp.get("cost_usd"),
            "input": _trim(sp.get("input")), "output": _trim(sp.get("output")),
            "ok": sp.get("ok"), "error": sp.get("error"),
        })
    return out


def _trim(value: Any) -> str:
    """A payload as the page shows it: one string, cut, never the whole prompt."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    s = " ".join(s.split())
    return s if len(s) <= PAYLOAD_CHARS else s[:PAYLOAD_CHARS - 1] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(s: scenarios.Scenario, ct: Any, sid: str, space: str) -> dict[str, Any]:
    """One answered question: everything item 7 names, from the captured turn."""
    turn = getattr(ct, "turn", None) or {}
    answer = str(getattr(ct, "answer", "") or "")
    tools = list(getattr(ct, "tool_names", None) or [])
    spans = list(getattr(ct, "spans", None) or [])
    models = sorted({str(sp.get("model")) for sp in spans if sp.get("model")})
    refused = _refused(s, answer)
    return {
        "scenario_id": s.scenario_id,
        "question": s.question,
        "answer": answer,
        # The sequence, not a set: the order tools were called in is the thing worth reading.
        "tools": tools,
        "tool_count": len(tools),
        "models": models,
        "spans": len(spans),
        # The steps themselves, so the page can show the trace without the batch having been
        # saved first: USER / ORCHESTRATOR / MODEL / OUTPUT, what each was given and what it
        # returned. Capped and trimmed -- a model input can be tens of thousands of characters,
        # and this travels in an HTTP reply.
        "steps": _steps(spans),
        "turn_id": turn.get("turn_id") or turn.get("id"),
        "session_id": sid,
        # The instance context, which is the whole reason the batch belongs to one space.
        "space": space,
        "engine": s.engine,
        "role": s.role,
        "style": s.style,
        "in_module": s.in_module,
        # The one thing that CAN be judged without an expected answer: a question outside this
        # instance has to be refused. Not a grade -- a gate check, and it says which it is.
        "refusal_expected": not s.in_module,
        "looks_refused": refused,
        "gate_ok": None if s.in_module else refused,
        "cost_usd": float(getattr(ct, "cost_usd", 0) or 0),
        "ms": int(getattr(ct, "ms", 0) or 0),
        "error": getattr(ct, "error", None),
        "ok": not getattr(ct, "error", None),
        "asked_at": _now(),
    }


#: What a refusal reads like, from agents/spaces.py's own wording ("not part of Ops Brain").
_REFUSAL_MARKS = ("not part of", "is not available in", "cannot answer", "isn't part of")


def _refused(s: scenarios.Scenario, answer: str) -> bool:
    """Whether the answer reads as the space declining. Deliberately simple: it is reported
    beside the answer, never used to pass or fail anything on its own."""
    low = (answer or "").lower()
    return any(m in low for m in _REFUSAL_MARKS)
