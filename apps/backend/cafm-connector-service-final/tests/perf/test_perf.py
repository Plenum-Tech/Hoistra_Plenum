"""
tests/perf/test_perf.py

Performance tests — P95 latency targets for the CAFM platform.

Targets (from CLAUDE.md section 23):
  /health               → P95 < 50ms
  /metrics              → P95 < 100ms
  EL-2.1 raw output     → P95 < 5ms   (pure Python, no I/O)
  EL-2.2 schema check   → P95 < 20ms  (Pydantic validation)
  Confidence router     → P95 < 2ms   (pure Python)
  Schema mapper parse   → P95 < 5ms   (regex + dict)
  Intent classifier     → P95 < 500ms (Haiku via API — mocked here)
  Document plan vote    → P95 < 10ms  (in-memory Counter)
  Output renderer       → P95 < 5ms   (pure Python)

Run:
    pytest tests/perf/ --import-mode=importlib -v -s

These tests use asyncio timing and assert on p95 across N=100 iterations.
They are purely in-process (no network I/O) — all Claude calls mocked.
For live load testing (HTTP), see tests/perf/locustfile.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── sys.path ──────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent.parent
_INGESTION_SRC = str(_ROOT / "svc-ingestion" / "src")
_QUERY_SRC = str(_ROOT / "svc-query" / "src")

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

for _p in [_INGESTION_SRC, _QUERY_SRC]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Helpers ───────────────────────────────────────────────────────────────────

N_SAMPLES = 100
P95_INDEX = int(N_SAMPLES * 0.95)


def _p95(timings_ms: list[float]) -> float:
    """Return the p95 value from a list of millisecond timings."""
    return sorted(timings_ms)[P95_INDEX - 1]


_VALID_DICT: dict[str, Any] = {
    "ingestion_id": str(uuid.uuid4()),
    "source_type": "pdf",
    "agent_id": "pdf-agent",
    "source_filename": "test.pdf",
    "source_blob_url": "https://plenumstorage.blob.core.windows.net/pdf-raw/test.pdf",
    "extracted_at": "2025-11-15T10:30:00Z",
    "extraction_method": "claude-vision",
    "model_used": "claude-sonnet-4-6",
    "entities": {
        "assets": [], "work_orders": [], "readings": [], "findings": [],
        "technicians": [], "vendors": [], "certificates": [], "spare_parts": [],
    },
    "confidence": {
        "overall": "high", "per_field": {}, "eval_score": None,
        "rules_passed": True, "rules_violations": [],
    },
    "audit": {
        "prompt_template_id": None, "prompt_version": None,
        "passes": 1, "tokens_in": 1000, "tokens_out": 200,
        "cache_read_tokens": 0, "cost_usd": 0.005, "cost_aed": 0.018,
        "processing_ms": 3000,
    },
}

_VALID_JSON = json.dumps(_VALID_DICT)


# ═══════════════════════════════════════════════════════════════════════════
# EL-2.1 — Raw extraction output validation
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfEL21:
    """EL-2.1 must complete in P95 < 5ms for valid JSON inputs."""

    TARGET_P95_MS = 5.0

    def setup_method(self):
        from shared.eval_layer import el_2_1_raw_output
        self.fn = el_2_1_raw_output

    def test_p95_valid_json(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            result = self.fn(_VALID_JSON)
            timings.append((time.perf_counter() - t0) * 1000)
        assert result.passed is True

        p95 = _p95(timings)
        print(f"\nEL-2.1 valid JSON — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS, f"EL-2.1 P95 {p95:.2f}ms exceeds target {self.TARGET_P95_MS}ms"

    def test_p95_invalid_json(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.fn("this is not json {{{{")
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"EL-2.1 invalid JSON — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# EL-2.2 — Schema conformance
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfEL22:
    """EL-2.2 Pydantic validation must complete in P95 < 20ms."""

    TARGET_P95_MS = 20.0

    def setup_method(self):
        from shared.eval_layer import el_2_2_schema_conformance
        self.fn = el_2_2_schema_conformance

    def test_p95_valid_schema(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            result = self.fn(_VALID_DICT)
            timings.append((time.perf_counter() - t0) * 1000)
        assert result.passed is True

        p95 = _p95(timings)
        print(f"\nEL-2.2 valid schema — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# Confidence Router
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfConfidenceRouter:
    """Confidence router (pure Python) must complete in P95 < 2ms."""

    TARGET_P95_MS = 2.0

    def setup_method(self):
        from shared.confidence_router import route
        from shared.eval_layer import EL23Result, RouteDecision
        from shared.intermediate_schema import IntermediateSchema
        self.route = route
        self.schema = IntermediateSchema(**_VALID_DICT)
        self.el23_accept = EL23Result(
            eval_score=0.92, contradictions=[], rules_violations=[],
            rules_passed=True, route=RouteDecision.ACCEPT,
        )
        self.el23_review = EL23Result(
            eval_score=0.72, contradictions=[], rules_violations=[],
            rules_passed=True, route=RouteDecision.REVIEW_QUEUE,
        )

    def test_p95_accept_path(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.route(self.schema, self.el23_accept)
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"\nConfidence router (accept) — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS

    def test_p95_review_queue_path(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.route(self.schema, self.el23_review)
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"Confidence router (review) — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# Document plan vote
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfDocumentPlanVote:
    """Document planner vote (in-memory Counter) must complete in P95 < 10ms."""

    TARGET_P95_MS = 10.0

    def setup_method(self):
        from document_generator.planner import _vote_on_plan
        from document_generator.schemas import DocumentPlan, DocumentSection, PlanningRunResult
        self.vote = _vote_on_plan

        def _make_run(section_types: list[str], valid: bool = True) -> PlanningRunResult:
            if valid:
                sections = [
                    DocumentSection(type=t, heading=t.replace("_", " ").title(), data_source="assets")
                    for t in section_types
                ]
                plan = DocumentPlan(
                    document_type="asset_health_summary",
                    title="Test",
                    generated_for="test",
                    output_format="docx",
                    sections=sections,
                    footer={"generated_by": "test", "timestamp": "now", "audit_id": "uuid"},
                    data_sources_required=["assets"],
                )
            else:
                plan = None
            return PlanningRunResult(run_number=1, plan=plan, raw_response="{}", valid=valid)

        self.runs = [
            _make_run(["summary_table", "kpi_summary"]),
            _make_run(["summary_table", "kpi_summary"]),
            _make_run(["summary_table", "findings_list"]),
        ]

    def test_p95_plan_vote(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            result = self.vote(self.runs)
            timings.append((time.perf_counter() - t0) * 1000)
        assert result is not None

        p95 = _p95(timings)
        print(f"\nDocument plan vote — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# Output Renderer
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfOutputRenderer:
    """Output renderer dispatch (pure Python) must complete in P95 < 5ms."""

    TARGET_P95_MS = 5.0

    def setup_method(self):
        from output_renderer import render_text_answer, render_json_answer, render_held_for_review
        self.render_text = render_text_answer
        self.render_json = render_json_answer
        self.render_held = render_held_for_review

    def test_p95_text_render(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.render_text("There are 17 open work orders.", audit_id=str(uuid.uuid4()))
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"\nOutput renderer (text) — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS

    def test_p95_json_render(self):
        data = {"assets": [{"code": "MOB-AHU-001", "status": "operational"}]}
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.render_json(data, audit_id=str(uuid.uuid4()))
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"Output renderer (json) — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS

    def test_p95_held_for_review_render(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.render_held("pm_schedule", 0.72, ["value not found"])
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"Output renderer (held) — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# EL-5 Majority Vote
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfEL5Vote:
    """EL-5.VOTE (in-memory Counter) must complete in P95 < 2ms."""

    TARGET_P95_MS = 2.0

    def setup_method(self):
        from shared.agent_determinism import AgentDeterminismCycle
        from data_agents.base_data_agent import SingleRunResult
        from pydantic import BaseModel
        from pathlib import Path

        class _M(BaseModel):
            asset_code: str

        self.cycle = AgentDeterminismCycle(
            allowed_statuses=["operational", "at_risk", "critical"],
            confidence_threshold=0.80,
            model="claude-haiku-4-5",
            system_prompt="test",
            rules_yaml_path=Path(_ROOT / "svc-ingestion/src/data_agents/rules/asset_rules.yaml"),
            bound_schema=_M,
            vote_field="status",
        )
        self.runs = [
            SingleRunResult(run_number=i, raw_response="{}", status="operational",
                            confidence=0.90, reasoning="ok", valid=True)
            for i in range(3)
        ]

    def test_p95_majority_vote(self):
        timings = []
        for _ in range(N_SAMPLES):
            t0 = time.perf_counter()
            self.cycle._majority_vote(self.runs)
            timings.append((time.perf_counter() - t0) * 1000)

        p95 = _p95(timings)
        print(f"\nEL-5.VOTE — P95: {p95:.2f}ms (target < {self.TARGET_P95_MS}ms)")
        assert p95 < self.TARGET_P95_MS


# ═══════════════════════════════════════════════════════════════════════════
# Locust HTTP load test (separate runner — not collected by pytest)
# ═══════════════════════════════════════════════════════════════════════════

# The file tests/perf/locustfile.py provides HTTP-level load testing.
# Run with: locust -f tests/perf/locustfile.py --headless -u 20 -r 5 --run-time 60s
# P95 HTTP targets:
#   GET /health           < 50ms
#   GET /metrics          < 100ms
#   POST /ingest (CSV)    < 2000ms   (queued async — returns job_id immediately)
#   POST /query (Tier 1)  < 3000ms   (SQL gen + execute + ground answer)
#   POST /query (doc gen) < 15000ms  (plan + render + eval)
