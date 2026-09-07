"""
svc-query/src/intent_classifier.py

Task 5.2 — Intent Classifier.

Classifies user queries into one of 5 intent types using Claude Haiku.
Must complete in < 500ms.

Intent types:
  tier1_structured  — Structured SQL query (~60%)
  tier2_document    — Fetch-then-read from blob (~20%)
  tier3_manual      — Vector search (manuals/SOPs only, ~5%)
  document_generate — Generate a new document from scratch (~10%)
  template_fill     — Fill an existing template (~5%)

If classifier confidence < 0.80 → asks user a clarifying question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL = "claude-haiku-4-5"
_CONFIDENCE_THRESHOLD = 0.80
_MAX_TOKENS = 256

IntentType = Literal[
    "tier1_structured",
    "tier2_document",
    "tier3_manual",
    "document_generate",
    "template_fill",
]

_VALID_INTENTS: set[str] = {
    "tier1_structured",
    "tier2_document",
    "tier3_manual",
    "document_generate",
    "template_fill",
}

# Few-shot examples hard-coded per CLAUDE.md §14
_FEW_SHOT_EXAMPLES = """
Examples (query → intent):
"Which assets have open WOs?"                              → tier1_structured
"How many parts are below minimum stock?"                  → tier1_structured
"List all Highest priority work orders"                    → tier1_structured
"What assets are in the HVAC category?"                    → tier1_structured
"What did the November inspection say about AHU-004?"      → tier2_document
"Show me the last inspection report for Building A"        → tier2_document
"What torque spec for the AHU drive belt?"                 → tier3_manual
"What does the SOP say about chiller shutdown?"            → tier3_manual
"Build me a PM schedule for all AHUs"                      → document_generate
"Create a weekly work order status report"                 → document_generate
"Give me a parts reorder summary"                          → document_generate
"Generate an asset health summary for this month"          → document_generate
"Fill in the inspection template for AHU-004 with today's data" → template_fill
"Populate the PM checklist for MOB-CHW-001"                → template_fill
"""

_SYSTEM_PROMPT = f"""\
You are a query intent classifier for a CAFM (facilities management) system.

Classify the user's query into exactly ONE of these 5 intent types:
  tier1_structured  — Questions answerable by SQL queries on structured data
  tier2_document    — Questions requiring reading a specific stored document
  tier3_manual      — Questions about equipment specs, SOPs, or technical manuals
  document_generate — Requests to create/generate a new report or document
  template_fill     — Requests to fill in an existing template with real data

{_FEW_SHOT_EXAMPLES}

Return ONLY a JSON object (no markdown fences, no extra text):
{{
  "intent": "<intent_type>",
  "confidence": <float 0.0-1.0>,
  "document_type": "<only if document_generate: pm_schedule|wo_report|wo_package|parts_reorder|inspection_template|asset_health_summary|maintenance_calendar|inspection_report|custom>",
  "clarifying_question": "<only if confidence < 0.80: a short question to disambiguate>"
}}
"""


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Result of intent classification."""

    intent: IntentType
    confidence: float
    document_type: str | None = None          # populated for document_generate
    needs_clarification: bool = False
    clarifying_question: str | None = None    # populated when confidence < 0.80
    raw_response: str = ""


# ── Main classifier ────────────────────────────────────────────────────────────


async def classify_intent(
    query: str,
    client: anthropic.AsyncAnthropic,
) -> ClassificationResult:
    """
    Classify a user query into one of 5 intent types.

    Returns ClassificationResult.
    If confidence < 0.80: needs_clarification=True, clarifying_question populated.
    """
    with tracer.start_as_current_span("document.classify_intent") as span:
        span.set_attribute("cafm.query_length", len(query))

        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": query}],
            )
            raw_text = response.content[0].text.strip() if response.content else "{}"

            claude_api_calls.add(1, {"agent_id": "intent-classifier", "model": _MODEL})
            in_tokens = getattr(response.usage, "input_tokens", 0)
            out_tokens = getattr(response.usage, "output_tokens", 0)
            claude_tokens_used.add(
                in_tokens + out_tokens,
                {"agent_id": "intent-classifier", "model": _MODEL},
            )

            # Strip markdown fences if present
            if raw_text.startswith("```"):
                parts = raw_text.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw_text = inner.strip()

            parsed = json.loads(raw_text)

            intent_val = str(parsed.get("intent", "tier1_structured")).strip()
            confidence_val = float(parsed.get("confidence", 0.0))
            confidence_val = max(0.0, min(1.0, confidence_val))
            document_type = parsed.get("document_type") or None
            clarifying_question = parsed.get("clarifying_question") or None

            # Validate intent
            if intent_val not in _VALID_INTENTS:
                logger.warning(
                    "intent_classifier_invalid_intent",
                    intent=intent_val,
                    query=query[:100],
                )
                intent_val = "tier1_structured"
                confidence_val = 0.5

            needs_clarification = confidence_val < _CONFIDENCE_THRESHOLD

            span.set_attribute("cafm.intent_type", intent_val)
            span.set_attribute("cafm.confidence", confidence_val)
            span.set_attribute("cafm.needs_clarification", needs_clarification)
            if document_type:
                span.set_attribute("cafm.document_type_detected", document_type)

            logger.info(
                "intent_classified",
                intent=intent_val,
                confidence=confidence_val,
                document_type=document_type,
                needs_clarification=needs_clarification,
            )

            return ClassificationResult(
                intent=intent_val,  # type: ignore[arg-type]
                confidence=confidence_val,
                document_type=document_type,
                needs_clarification=needs_clarification,
                clarifying_question=clarifying_question,
                raw_response=raw_text,
            )

        except (json.JSONDecodeError, anthropic.APIError, ValueError, TypeError) as exc:
            logger.error("intent_classifier_error", error=str(exc), query=query[:100])
            span.set_status(StatusCode.ERROR, str(exc))
            # Fallback: assume structured query (safest default)
            return ClassificationResult(
                intent="tier1_structured",
                confidence=0.5,
                needs_clarification=True,
                clarifying_question="Could you clarify what information you're looking for?",
                raw_response="",
            )
