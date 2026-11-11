"""Shared rows for the eval-store tests: one recorded turn and its spans, shaped exactly like agent_trace_turns and
agent_trace_spans (which is also what capture.run_message returns). A plain module, not conftest.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())
UID = str(uuid.uuid4())
T0 = datetime(2026, 11, 11, 9, 0, 0, tzinfo=timezone.utc)
ANSWER = "Two certificates expire within 30 days: the Gas Safety record and the EICR."


def sample_turn(turn_id: str = "turn-abc", session_id: str = "scn__baseline_v0__MT-001__1", *, at: datetime = T0,
                question: str = "Which certificates expire in the next 30 days at B-301?", answer: str = ANSWER,
                ok: bool = True, org: str = ORG, with_spans: bool = True) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A turn with a root span, a sub-agent, a tool call that failed and a model call with tokens and a cost."""
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{turn_id}:{i}")) for i in range(4)]
    turn = {"turn_id": turn_id, "session_id": session_id, "organization_id": org, "user_id": UID,
            "email": "asker@example.com", "question": question, "answer": answer, "route": "single:compliance",
            "started_at": at.isoformat(), "ended_at": (at + timedelta(seconds=5)).isoformat(), "latency_ms": 5000,
            "llm_calls": 1, "tool_calls": 1, "cost_usd": 0.0042, "cost_complete": True, "input_tokens": 120,
            "output_tokens": 30, "cache_read_tokens": 0, "models": ["gpt-5.6-terra"], "tools": ["list_certificates"],
            "ok": ok, "error": None if ok else "the turn failed"}
    if not with_spans:
        return turn, []

    def span(i: int, parent: int | None, kind: str, name: str, **extra: Any) -> dict[str, Any]:
        row = {"id": ids[i], "turn_id": turn_id, "session_id": session_id, "organization_id": org,
               "parent_id": ids[parent] if parent is not None else None, "seq": i, "kind": kind, "name": name,
               "agent": "compliance" if i else "orchestrator", "model": None,
               "started_at": (at + timedelta(seconds=i)).isoformat(),
               "ended_at": (at + timedelta(seconds=i + 1)).isoformat(), "latency_ms": 1000, "input": None,
               "output": None, "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
               "cost_usd": None, "ok": True, "error": None}
        row.update(extra)
        return row

    spans = [
        span(0, None, "turn", "turn"),
        span(1, 0, "agent", "compliance", input={"question": question}),
        span(2, 1, "tool", "list_certificates", input={"building": "B-301", "within_days": 30}, ok=False,
             error="upstream timeout"),
        span(3, 1, "llm", "analyst", model="gpt-5.6-terra", input={"messages": [{"role": "user", "content": question}]},
             output={"text": answer}, input_tokens=120, output_tokens=30, cache_read_tokens=0, cost_usd=0.0042),
    ]
    return turn, spans


def member(*, org: str | None = ORG, role: str = "platform", all_companies: bool = True, manages: bool = True,
           status: str = "active", user_id: str = UID, email: str = "reviewer@example.com") -> Any:
    """A Trace Lab member as labelling.access returns one. The default is the platform team, who see every company."""
    from src.services.principal import Principal

    from trace_lab import labelling

    # The service's own Principal, so code that copies it for a company (labelling.company_view) or asks is_admin works.
    principal = Principal(user_id=uuid.UUID(user_id), email=email, organization_id=uuid.UUID(org) if org else None,
                          role="superadmin" if all_companies else "admin", can_ingest=False, building_ids=None)
    return labelling.Member(principal=principal, org=org, user_id=user_id, email=email, role=role, status=status,
                            manages=manages, all_companies=all_companies)


async def seed_session(lab: Any, session_id: str = "scn__baseline_v0__MT-001__1", *, tag: str = "baseline_v0",
                       turn_ids: tuple[str, ...] = ("turn-1", "turn-2"), org: str = ORG, attempt: int = 1,
                       scenario: tuple[str, int] | None = None, at: datetime = T0) -> dict[str, int]:
    """One stored session with a trace per turn id (4 observations each), its run tag made if missing."""
    from sqlalchemy import text

    from trace_lab import eval_store

    meta = eval_store.SessionMeta(
        id=session_id, organization_id=org, run_tag=tag, scenario_id=scenario[0] if scenario else None,
        scenario_version=scenario[1] if scenario else None, attempt=attempt, origin="test", source="runner",
        user_id=UID, email="asker@example.com", role="property_manager", space="ops", started_at=at)
    async with lab.begin() as c:
        await c.execute(text("INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES (:t, 'baseline', 'test')"
                             " ON CONFLICT DO NOTHING"), {"t": tag})
        return await eval_store.write_session(c, meta, [
            eval_store.rows_for(*sample_turn(tid, session_id, at=at + timedelta(minutes=i), org=org))
            for i, tid in enumerate(turn_ids)])


async def seed_score(lab: Any, trace_id: str = "turn-1", session_id: str = "scn__baseline_v0__MT-001__1", *,
                     name: str = "logic", source: str = "EVAL", verdict: str = "pass", value: float | None = 1.0,
                     comment: str | None = None, author: str | None = None, details: dict | None = None,
                     evaluator: str | None = None) -> None:
    """One score row. A person's score (source ANNOTATION) names its author."""
    import json

    from sqlalchemy import text

    async with lab.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value, comment, details,"
            " evaluator, author_id, author_email, author_role) VALUES (:t, :s, :n, :src, :v, :val, :c,"
            " CAST(:d AS jsonb), :e, CAST(:a AS uuid), :ae, :ar)"),
            {"t": trace_id, "s": session_id, "n": name, "src": source, "v": verdict, "val": value, "c": comment,
             "d": json.dumps(details or {}), "e": evaluator or (f"{name}-1" if source == "EVAL" else None),
             "a": (author or UID) if source == "ANNOTATION" else None,
             "ae": "reviewer@example.com" if source == "ANNOTATION" else None,
             "ar": "platform" if source == "ANNOTATION" else None})
