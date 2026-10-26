"""Test helper for golden-run tests (tests only): replaces the runner's seams to other modules - the
golden set store, the grader, the identity lookup, the isolation preflight and the orchestrator - with
in-memory stand-ins, so runner tests need only the scratch Trace Lab schema; seed() writes the stand-in set
and its cases as real rows, because golden_runs and golden_results reference them. No model, network or
application database."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from golden_fakes import BEARER, ORG, USER, Msg, RecordedOrchestrator, principal  # noqa: F401

SET_ID = "5e7a0000-0000-4000-8000-000000000001"
EXPECTED = "SECRET EXPECTED 759"


def case(key: str, question: str, followups: list[str] | None = None, **over: Any) -> dict[str, Any]:
    base = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"case:{key}")), "set_id": SET_ID, "organization_id": ORG,
            "case_key": key, "case_version": hashlib.sha256(key.encode()).hexdigest()[:12],
            "idx": int(key) if key.isdigit() else 0,
            "question": question, "followups": list(followups or []), "expected_answer": EXPECTED,
            "evaluation": "objective", "expected_values": [], "source": None, "verified_on": None,
            "page_context": None, "ask_as": "company", "requirement_ids": [], "notes": None, "stale": False}
    base.update(over)
    return base


CASES = [
    case("21", "How many work orders were raised in August 2026?", ["And how many of those were reactive?"]),
    case("22", "Which vendors hold a lapsed gas safety certificate?"),
]
ACTIVE_SET = {"id": SET_ID, "name": "Golden set", "version": 3, "kind": "scenario", "status": "active",
              "organization_id": ORG}


@dataclass
class FakeGrade:
    outcome: str = "pass"
    critique: str = "Matches the expected answer."
    mismatches: list[str] = field(default_factory=list)
    value_check: dict[str, Any] = field(default_factory=lambda: {"status": "none", "items": []})
    grader_version: str = "grader-test-1"
    usd: float = 0.02


class World:
    def __init__(self) -> None:
        self.cases: list[dict[str, Any]] = [dict(c) for c in CASES]
        self.active: dict[str, Any] | None = dict(ACTIVE_SET)
        self.selected: str | None = None
        self.problems: list[str] = []
        self.script: list[Msg] = []
        self.orchestrator = RecordedOrchestrator(self.script)
        self.graded: list[tuple[dict, Any]] = []
        self.grade_outcome = "pass"
        self.grader_fails = False


def install(monkeypatch) -> World:
    from src.agents import trace
    from trace_lab import orchestrator_host, proxy, runner

    w = World()

    async def active_set(org):
        return w.active if org == ORG else None

    async def cases_for(org, set_id):
        return [dict(c) for c in w.cases] if org == ORG and set_id == SET_ID else []

    async def basis(org):
        return {"per_answer_usd": 0.09, "basis": "default"}

    async def selected(authorization):
        return w.selected

    async def overlays():
        return 0

    async def grade(c, captured, *, earlier=None):
        w.graded.append((c, captured))
        if w.grader_fails:
            raise RuntimeError("grader down")
        return FakeGrade(outcome=w.grade_outcome)

    monkeypatch.setattr(runner, "_active_set", active_set)
    monkeypatch.setattr(runner, "_cases", cases_for)
    monkeypatch.setattr(runner, "answer_basis", basis)
    monkeypatch.setattr(runner, "selected_building", selected)
    monkeypatch.setattr(runner, "_grade", grade, raising=False)     # Task 8 adds runner._grade
    monkeypatch.setattr(runner, "_ACTIVE", {})
    monkeypatch.setattr(orchestrator_host, "assert_isolated", lambda: list(w.problems))
    monkeypatch.setattr(orchestrator_host, "ensure_overlays_readonly", overlays)
    monkeypatch.setattr(orchestrator_host, "get_orchestrator", lambda: w.orchestrator)
    monkeypatch.setattr(trace, "_ready", False)
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    for name in ("TRACE_LAB_GOLDEN_MAX_K", "TRACE_LAB_GOLDEN_MONTHLY_USD", "TRACE_LAB_GOLDEN_RUN_MAX_USD",
                 "TRACE_LAB_GOLDEN_MIN_TOKEN_MIN"):
        monkeypatch.delenv(name, raising=False)
    proxy.reset_for_tests()
    return w


async def seed(engine, w: World) -> None:
    """The stand-in set and its cases as real rows in the scratch schema (Phase 3 Task 1's golden_sets and
    golden_cases: golden_runs.set_id and golden_results.case_id reference them)."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.golden_sets (id, organization_id, name, kind, version, status, source_filename,"
            " source_sha256, set_hash, case_count, created_by) VALUES (CAST(:id AS uuid), CAST(:org AS uuid),"
            " 'Golden set', 'scenario', 3, 'active', 'golden.xlsx', :sha, :hash, :n, :by)"),
            {"id": SET_ID, "org": ORG, "sha": hashlib.sha256(b"golden.xlsx").hexdigest(), "hash": "0123456789ab",
             "n": len(w.cases), "by": USER})
        for c in w.cases:
            await conn.execute(text(
                "INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, case_version, idx,"
                " question, followups, expected_answer, evaluation, page_context)"
                " VALUES (CAST(:id AS uuid), CAST(:set AS uuid), CAST(:org AS uuid), :key, :ver, :idx, :q,"
                " CAST(:fu AS jsonb), :exp, :ev, :pc)"),
                {"id": c["id"], "set": SET_ID, "org": ORG, "key": c["case_key"], "ver": c["case_version"],
                 "idx": c["idx"], "q": c["question"], "fu": json.dumps(c["followups"]), "exp": c["expected_answer"],
                 "ev": c["evaluation"], "pc": c["page_context"]})


async def install_seeded(engine, monkeypatch) -> World:
    """install() plus seed(): what every scratch-database runner test starts from."""
    w = install(monkeypatch)
    await seed(engine, w)
    return w


def body(**over: Any) -> dict[str, Any]:
    b = {"set_id": None, "k": 1, "case_keys": None, "purpose": "adhoc", "usd_cap": None,
         "context_default": "The user is in the Hoistra orchestrator conversation.", "note": None}
    b.update(over)
    return b


def jwt(minutes: float) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return "Bearer " + seg({"alg": "HS256"}) + "." + seg({"sub": USER, "exp": time.time() + minutes * 60}) + ".sig"


async def drain() -> None:
    """Wait for every background item and grading job of this process."""
    from trace_lab import runner

    while True:
        tasks = [*runner._ACTIVE.values(), *getattr(runner, "_GRADING", set())]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)                      # let the done-callbacks forget the finished tasks
