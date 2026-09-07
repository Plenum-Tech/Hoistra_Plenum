"""
cafm_shared/metrics.py

All custom Prometheus / OTel metrics for the CAFM AI Platform.

IMPORTANT:
- configure_telemetry() MUST be called before importing from this module.
  Instruments registered before set_meter_provider() bind to the no-op provider.
- Import individual instruments from here — never define metrics locally in services.
- Labels (attributes) are passed at record time, not at creation time.

Usage:
    from cafm_shared.metrics import claude_api_calls, claude_cost_usd
    claude_api_calls.add(1, {"model": "claude-sonnet-4-6", "agent_id": "pdf-agent", "purpose": "extraction"})
    claude_cost_usd.add(0.021, {"model": "claude-sonnet-4-6", "agent_id": "pdf-agent", "service": "svc-ingestion"})
"""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry import metrics
from opentelemetry.metrics import (
    Counter,
    Histogram,
    ObservableGauge,
    Observation,
)

# Lazy meter — bound after configure_telemetry() sets the global MeterProvider
meter = metrics.get_meter("cafm.platform", version="0.1.0")


# ── Counters ──────────────────────────────────────────────────────────────────
# Increment with: counter.add(n, {"label_key": "label_value"})

documents_ingested: Counter = meter.create_counter(
    name="cafm.documents.ingested.total",
    unit="1",
    description=(
        "Total documents successfully ingested through the pipeline. "
        "Labels: agent_id, source_type, status (accepted|review|rejected)"
    ),
)

claude_api_calls: Counter = meter.create_counter(
    name="cafm.claude.api_calls.total",
    unit="1",
    description=(
        "Total calls made to the Anthropic Claude API. "
        "Labels: model, agent_id, purpose (extraction|eval|entity_resolution|schema_mapping|query_synthesis)"
    ),
)

claude_tokens_used: Counter = meter.create_counter(
    name="cafm.claude.tokens.total",
    unit="1",
    description=(
        "Total tokens consumed from Claude API. "
        "Labels: model, token_type (input|output|cache_read), agent_id"
    ),
)

claude_cost_usd: Counter = meter.create_counter(
    name="cafm.claude.cost.usd.total",
    unit="USD",
    description=(
        "Total cost of Claude API calls in US dollars. "
        "Labels: model, agent_id, service"
    ),
)

review_queue_enqueued: Counter = meter.create_counter(
    name="cafm.review_queue.enqueued.total",
    unit="1",
    description=(
        "Total documents sent to the human-in-the-loop review queue. "
        "Labels: routing_reason (medium_confidence|low_confidence|rule_violation), agent_id"
    ),
)

entity_resolutions: Counter = meter.create_counter(
    name="cafm.entity_resolution.total",
    unit="1",
    description=(
        "Total entity resolution attempts. "
        "Labels: entity_type (asset|user|vendor), tier (1|2|3|4), resolved (true|false)"
    ),
)


# ── Histograms ────────────────────────────────────────────────────────────────
# Record with: histogram.record(value_ms, {"label_key": "label_value"})

document_processing_duration: Histogram = meter.create_histogram(
    name="cafm.document.processing.duration.ms",
    unit="ms",
    description=(
        "End-to-end processing time per document (Stages 1–4), in milliseconds. "
        "Labels: agent_id, source_type"
    ),
)

claude_api_latency: Histogram = meter.create_histogram(
    name="cafm.claude.latency.ms",
    unit="ms",
    description=(
        "Latency of a single Claude API call, in milliseconds. "
        "Labels: model, purpose"
    ),
)

eval_score_histogram: Histogram = meter.create_histogram(
    name="cafm.eval.score",
    unit="1",
    description=(
        "Distribution of LLM-as-judge eval scores (0.0–1.0). "
        "Labels: agent_id, source_type"
    ),
)

query_latency: Histogram = meter.create_histogram(
    name="cafm.query.latency.ms",
    unit="ms",
    description=(
        "End-to-end latency of a user query in svc-query, in milliseconds. "
        "Labels: retrieval_tier (tier1|tier2|tier3), output_format (text|json|word|pdf)"
    ),
)


# ── Observable Gauges (callback-based) ───────────────────────────────────────
# Callbacks return the current value each time Prometheus scrapes /metrics.
# Phase 1: noop stubs. Real implementations replace these by closing over
# shared state objects (see CLAUDE.md section 21.6 for the pattern).


def _noop_review_queue_depth(
    options: metrics.CallbackOptions,
) -> Sequence[Observation]:
    """Placeholder — replaced when review_queue/queue.py is implemented."""
    return [Observation(0, {})]


def _noop_budget_used_pct(
    options: metrics.CallbackOptions,
) -> Sequence[Observation]:
    """Placeholder — replaced when cost tracking (Task 5.1) is implemented."""
    return [Observation(0.0, {})]


def _noop_cache_hit_rate(
    options: metrics.CallbackOptions,
) -> Sequence[Observation]:
    """Placeholder — replaced when entity_resolver (Task 2.8) is implemented."""
    return [Observation(0.0, {})]


review_queue_depth: ObservableGauge = meter.create_observable_gauge(
    name="cafm.review_queue.depth",
    callbacks=[_noop_review_queue_depth],
    unit="1",
    description="Current number of documents waiting in the HITL review queue.",
)

claude_budget_used_pct: ObservableGauge = meter.create_observable_gauge(
    name="cafm.claude.budget.used.pct",
    callbacks=[_noop_budget_used_pct],
    unit="1",
    description="Percentage of the configured Claude spend budget consumed (0–100).",
)

redis_cache_hit_rate: ObservableGauge = meter.create_observable_gauge(
    name="cafm.entity_resolver.cache_hit_rate",
    callbacks=[_noop_cache_hit_rate],
    unit="1",
    description="Cache hit rate of the entity resolver Redis cache (0.0–1.0).",
)
