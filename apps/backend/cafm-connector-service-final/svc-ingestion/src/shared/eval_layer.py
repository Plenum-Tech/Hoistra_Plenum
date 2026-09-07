"""
svc-ingestion/src/shared/eval_layer.py

Tasks 3.1 + 3.2 — Evaluation Layer for Ingestion Pipeline.

Implements EL-2.1, EL-2.2, EL-2.3 (LLM-as-judge + YAML rule engine).
Called by every ingestion agent that uses Claude after extraction.

EL-2.1  Raw extraction output validation
         - Valid JSON, required top-level keys present, no null ingestion_id/source_type
         - PASS → EL-2.2   FAIL → retry context returned to caller (max 3x)

EL-2.2  Intermediate JSON schema conformance
         - Pydantic validates entities block, per_field confidence tags present,
           no entity with null primary identifier
         - PASS → EL-2.3   FAIL → schema_violation error dict returned

EL-2.3  LLM-as-judge eval (Haiku)
         - Receives source excerpt + extracted JSON
         - Returns eval_score (0.0–1.0) + contradiction list
         - YAML contradiction rule engine fires after LLM response
         - eval_score written to confidence.eval_score
         - Score routing: ≥0.85 → accept, 0.60–0.84 → review queue, <0.60 → re-extract

OTel spans:
  ingestion.eval.extraction_output  ← EL-2.1
  ingestion.eval.schema_conformance ← EL-2.2
  ingestion.eval.llm_judge          ← EL-2.3
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import ValidationError

from cafm_shared.logging import get_logger
from shared.intermediate_schema import (
    ConfidenceLevel,
    EntitiesBlock,
    IntermediateSchema,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_REQUIRED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"entities", "confidence", "audit"})

# Score thresholds
SCORE_AUTO_ACCEPT: float = 0.85
SCORE_REVIEW_QUEUE_MIN: float = 0.60

# YAML rules directory (relative to this file's parent)
_RULES_DIR: Path = Path(__file__).parent / "rules"

# Haiku model for LLM-as-judge
_JUDGE_MODEL: str = "claude-haiku-4-5"
_JUDGE_MAX_TOKENS: int = 512


# ── Route decision ──────────────────────────────────────────────────────────────


class RouteDecision(str, Enum):
    ACCEPT = "accept"
    REVIEW_QUEUE = "review_queue"
    RE_EXTRACT = "re_extract"


# ── Result dataclasses ─────────────────────────────────────────────────────────


@dataclass
class EL21Result:
    """Output of EL-2.1 raw extraction output validation."""

    passed: bool
    parsed: dict[str, Any] | None
    error: str = ""
    retry_context: str = ""  # Appended to prompt on retry


@dataclass
class EL22Result:
    """Output of EL-2.2 schema conformance check."""

    passed: bool
    schema: IntermediateSchema | None
    violations: list[str] = field(default_factory=list)


@dataclass
class EL23Result:
    """Output of EL-2.3 LLM-as-judge evaluation."""

    eval_score: float
    contradictions: list[str] = field(default_factory=list)
    rules_violations: list[str] = field(default_factory=list)
    rules_passed: bool = True
    route: RouteDecision = RouteDecision.ACCEPT
    judge_raw: str = ""  # raw Haiku response for audit


# ── EL-2.1 — Raw extraction output validation ──────────────────────────────────


def el_2_1_raw_output(response_text: str) -> EL21Result:
    """
    EL-2.1: Validate that Claude's raw response text is usable JSON.

    Checks:
    - Response is valid JSON (handles markdown fencing)
    - All required top-level keys present: entities, confidence, audit
    - ingestion_id and source_type are not null if present

    Returns EL21Result with parsed dict on success, retry_context on failure.
    """
    with tracer.start_as_current_span("ingestion.eval.extraction_output") as span:
        # Strip markdown code fences if present
        text = response_text.strip()
        fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Attempt JSON parse
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            error = f"Response is not valid JSON: {exc}"
            retry_context = (
                f"Previous response failed JSON parsing: {exc}. "
                "Return ONLY valid JSON without any markdown, code fences, or extra text."
            )
            span.set_attribute("cafm.json_valid", False)
            span.set_attribute("cafm.retry_context", retry_context)
            span.set_status(StatusCode.ERROR, error)
            logger.warning("el_2_1_failed", reason="invalid_json", error=str(exc))
            return EL21Result(passed=False, parsed=None, error=error, retry_context=retry_context)

        if not isinstance(parsed, dict):
            error = "Response JSON is not a dict (expected object at root)"
            retry_context = (
                "Previous response returned a JSON array or scalar at root level. "
                "The response must be a JSON object with keys: entities, confidence, audit."
            )
            span.set_attribute("cafm.json_valid", False)
            span.set_status(StatusCode.ERROR, error)
            return EL21Result(passed=False, parsed=None, error=error, retry_context=retry_context)

        # Check required top-level keys
        missing = _REQUIRED_TOP_LEVEL_KEYS - set(parsed.keys())
        if missing:
            error = f"Missing required top-level keys: {sorted(missing)}"
            retry_context = (
                f"Previous response was missing these required keys: {sorted(missing)}. "
                "The JSON response must contain: entities, confidence, audit."
            )
            span.set_attribute("cafm.json_valid", True)
            span.set_attribute("cafm.missing_keys", str(sorted(missing)))
            span.set_status(StatusCode.ERROR, error)
            logger.warning("el_2_1_failed", reason="missing_keys", missing=sorted(missing))
            return EL21Result(passed=False, parsed=None, error=error, retry_context=retry_context)

        # Check ingestion_id and source_type are not explicitly null
        if parsed.get("ingestion_id") is None and "ingestion_id" in parsed:
            error = "ingestion_id is explicitly null"
            retry_context = "ingestion_id must not be null. Generate a UUID if none is available."
            span.set_status(StatusCode.ERROR, error)
            return EL21Result(passed=False, parsed=None, error=error, retry_context=retry_context)

        if parsed.get("source_type") is None and "source_type" in parsed:
            error = "source_type is explicitly null"
            retry_context = (
                "source_type must not be null. "
                "Set it to one of: pdf, excel, word, csv, xml, json, database, api."
            )
            span.set_status(StatusCode.ERROR, error)
            return EL21Result(passed=False, parsed=None, error=error, retry_context=retry_context)

        span.set_attribute("cafm.json_valid", True)
        span.set_attribute("cafm.retry_count", 0)
        logger.debug("el_2_1_passed")
        return EL21Result(passed=True, parsed=parsed)


# ── EL-2.2 — Intermediate JSON schema conformance ─────────────────────────────


def el_2_2_schema_conformance(raw_dict: dict[str, Any]) -> EL22Result:
    """
    EL-2.2: Validate the parsed dict against IntermediateSchema.

    Checks:
    - Pydantic validates every entity in entities{}
    - per_field confidence tags present (at least empty dict)
    - No entity has a null primary identifier (asset_code, wo_code, etc.)

    Returns EL22Result with schema object on success, violations list on failure.
    """
    with tracer.start_as_current_span("ingestion.eval.schema_conformance") as span:
        violations: list[str] = []

        # Validate entities block independently first to get entity count
        entities_raw = raw_dict.get("entities", {})
        try:
            entities = EntitiesBlock.model_validate(entities_raw)
            entities_count = entities.total_count
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(x) for x in err["loc"])
                violations.append(f"entities.{loc}: {err['msg']}")
            entities_count = 0

        # Validate per_field confidence tags exist
        confidence_raw = raw_dict.get("confidence", {})
        if not isinstance(confidence_raw, dict):
            violations.append("confidence must be a dict")
        else:
            per_field = confidence_raw.get("per_field")
            if per_field is None:
                violations.append("confidence.per_field is missing")
            elif not isinstance(per_field, dict):
                violations.append("confidence.per_field must be a dict")

        # Attempt full Pydantic validation
        try:
            schema = IntermediateSchema.model_validate(raw_dict)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(x) for x in err["loc"])
                msg = f"{loc}: {err['msg']}"
                if msg not in violations:
                    violations.append(msg)
            schema = None

        span.set_attribute("cafm.entities_count", entities_count)
        span.set_attribute("cafm.schema_valid", len(violations) == 0)
        span.set_attribute("cafm.violations_count", len(violations))

        if violations:
            logger.warning(
                "el_2_2_failed",
                violations=violations,
                entities_count=entities_count,
            )
            span.set_status(StatusCode.ERROR, f"{len(violations)} violations")
            return EL22Result(passed=False, schema=None, violations=violations)

        logger.debug("el_2_2_passed", entities_count=entities_count)
        return EL22Result(passed=True, schema=schema, violations=[])


# ── YAML contradiction rules engine ───────────────────────────────────────────


def _load_contradiction_rules(rules_file: Path | None = None) -> list[dict[str, Any]]:
    """
    Load YAML contradiction rules from disk.

    Each rule has:
      name: str               - rule identifier
      condition_field: str    - entity field to check (dot-path, e.g. "findings.severity")
      condition_value: str    - value that triggers the rule
      contradicted_by: str    - another field that contradicts when condition is met
      contradicted_value: str - the contradicting value (or 'any' to always fire)
      message: str            - human-readable description
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning("yaml_not_installed", detail="PyYAML not installed — rule engine disabled")
        return []

    if rules_file is None:
        rules_file = _RULES_DIR / "contradiction_rules.yaml"

    if not rules_file.exists():
        logger.debug("contradiction_rules_not_found", path=str(rules_file))
        return []

    try:
        with open(rules_file, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        rules: list[dict[str, Any]] = data.get("rules", []) if isinstance(data, dict) else []
        logger.debug("contradiction_rules_loaded", count=len(rules))
        return rules
    except Exception as exc:
        logger.warning("contradiction_rules_load_error", error=str(exc))
        return []


def _apply_contradiction_rules(
    extracted: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[str]:
    """
    Apply YAML contradiction rules to extracted JSON.

    Returns a list of rule violation messages. Empty list = no violations.
    """
    violations: list[str] = []
    entities = extracted.get("entities", {})

    for rule in rules:
        name = rule.get("name", "unnamed")
        message = rule.get("message", name)

        # Simple field presence check: look for condition_field value in any entity
        cond_field: str = rule.get("condition_field", "")
        cond_value: str = str(rule.get("condition_value", "")).lower()
        contra_field: str = rule.get("contradicted_by", "")
        contra_value: str = str(rule.get("contradicted_value", "any")).lower()

        if not cond_field or not contra_field:
            continue

        # Flatten entities to check across all entity types
        entity_list: list[dict[str, Any]] = []
        for entity_group in entities.values():
            if isinstance(entity_group, list):
                entity_list.extend(entity_group)

        for entity in entity_list:
            if not isinstance(entity, dict):
                continue

            # Resolve dot-path field
            cond_val_actual = _resolve_field(entity, cond_field)
            contra_val_actual = _resolve_field(entity, contra_field)

            if cond_val_actual is None or contra_val_actual is None:
                continue

            cond_match = str(cond_val_actual).lower() == cond_value
            if contra_value == "any":
                contra_match = contra_val_actual is not None
            else:
                contra_match = str(contra_val_actual).lower() == contra_value

            if cond_match and contra_match:
                violations.append(f"Rule '{name}': {message}")
                break  # Only fire once per rule

    return violations


def _resolve_field(entity: dict[str, Any], dot_path: str) -> Any:
    """Resolve a dot-path like 'findings.severity' in an entity dict."""
    parts = dot_path.split(".")
    current: Any = entity
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# ── EL-2.3 — LLM-as-judge eval ─────────────────────────────────────────────────


async def el_2_3_llm_judge(
    source_excerpt: str,
    extracted_json: dict[str, Any],
    client: anthropic.AsyncAnthropic,
    rules_file: Path | None = None,
    agent_id: str = "unknown",
) -> EL23Result:
    """
    EL-2.3: LLM-as-judge evaluation using Claude Haiku.

    Haiku receives: source excerpt + extracted JSON
    Returns: eval_score (0.0–1.0) + contradiction list + YAML rule violations
    Routes:
      ≥ 0.85 → ACCEPT
      0.60–0.84 → REVIEW_QUEUE
      < 0.60 → RE_EXTRACT
    """
    with tracer.start_as_current_span("ingestion.eval.llm_judge") as span:
        span.set_attribute("cafm.agent_id", agent_id)

        # Build judge prompt
        extracted_str = json.dumps(extracted_json, default=str, indent=2)
        # Truncate source excerpt to avoid token waste
        excerpt_truncated = source_excerpt[:3000] if len(source_excerpt) > 3000 else source_excerpt

        judge_prompt = f"""You are an expert CAFM data quality evaluator.

Review the source document excerpt and the extracted JSON data.
Assess how accurately the JSON represents the source content.

SOURCE EXCERPT:
{excerpt_truncated}

EXTRACTED JSON:
{extracted_str}

Evaluate the extraction quality and return ONLY a JSON object with this exact structure:
{{
  "eval_score": <float between 0.0 and 1.0>,
  "contradictions": [<list of contradiction strings, empty if none>],
  "reasoning": "<brief explanation in 1-2 sentences>"
}}

Scoring guide:
- 0.90-1.00: Excellent — all key entities extracted correctly, no contradictions
- 0.75-0.89: Good — most entities correct, minor omissions only
- 0.60-0.74: Acceptable — some errors or omissions, but core data present
- 0.40-0.59: Poor — significant errors or missing critical fields
- 0.00-0.39: Failed — major errors, wrong data, or extraction completely wrong

Return ONLY the JSON object, no markdown, no extra text."""

        # Call Haiku
        judge_raw = ""
        eval_score = 0.5  # Fallback score on API error
        contradictions: list[str] = []

        try:
            response = await client.messages.create(
                model=_JUDGE_MODEL,
                max_tokens=_JUDGE_MAX_TOKENS,
                messages=[{"role": "user", "content": judge_prompt}],
            )
            judge_raw = response.content[0].text if response.content else ""

            # Parse judge response
            parsed_judge = _parse_judge_response(judge_raw)
            eval_score = parsed_judge.get("eval_score", 0.5)
            contradictions = parsed_judge.get("contradictions", [])

            # Clamp score to [0, 1]
            eval_score = max(0.0, min(1.0, float(eval_score)))

        except anthropic.APIError as exc:
            logger.warning(
                "el_2_3_api_error",
                agent_id=agent_id,
                error=str(exc),
                fallback_score=eval_score,
            )
            span.set_status(StatusCode.ERROR, f"API error: {exc}")
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("el_2_3_parse_error", agent_id=agent_id, error=str(exc))

        # Apply YAML contradiction rules
        rules = _load_contradiction_rules(rules_file)
        rules_violations = _apply_contradiction_rules(extracted_json, rules)
        rules_passed = len(rules_violations) == 0

        # Rules violations reduce the score
        if rules_violations:
            penalty = min(0.30, len(rules_violations) * 0.10)
            eval_score = max(0.0, eval_score - penalty)
            logger.warning(
                "el_2_3_rules_violations",
                agent_id=agent_id,
                violations=rules_violations,
                score_after_penalty=round(eval_score, 3),
            )

        # Determine routing
        route = _route_by_score(eval_score)

        span.set_attribute("cafm.eval_score", round(eval_score, 3))
        span.set_attribute("cafm.rules_violations_count", len(rules_violations))
        span.set_attribute("cafm.route", route.value)

        logger.info(
            "el_2_3_complete",
            agent_id=agent_id,
            eval_score=round(eval_score, 3),
            contradictions=len(contradictions),
            rules_violations=len(rules_violations),
            route=route.value,
        )

        return EL23Result(
            eval_score=round(eval_score, 3),
            contradictions=contradictions,
            rules_violations=rules_violations,
            rules_passed=rules_passed,
            route=route,
            judge_raw=judge_raw,
        )


def _parse_judge_response(raw: str) -> dict[str, Any]:
    """Extract JSON from judge response, handling markdown fences."""
    text = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object from the text
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(0))
            except json.JSONDecodeError:
                pass
    return {"eval_score": 0.5, "contradictions": [], "reasoning": "parse error"}


def _route_by_score(score: float) -> RouteDecision:
    """Map eval_score to RouteDecision."""
    if score >= SCORE_AUTO_ACCEPT:
        return RouteDecision.ACCEPT
    elif score >= SCORE_REVIEW_QUEUE_MIN:
        return RouteDecision.REVIEW_QUEUE
    else:
        return RouteDecision.RE_EXTRACT


# ── Convenience: apply EL-2.3 result back to schema ────────────────────────────


def apply_eval_score_to_schema(
    schema: IntermediateSchema,
    el23: EL23Result,
) -> IntermediateSchema:
    """
    Write EL-2.3 result back into the schema's confidence block.
    Returns a new IntermediateSchema (Pydantic is immutable by default).
    """
    updated_confidence = schema.confidence.model_copy(
        update={
            "eval_score": el23.eval_score,
            "rules_passed": el23.rules_passed,
            "rules_violations": el23.contradictions + el23.rules_violations,
        }
    )
    return schema.model_copy(update={"confidence": updated_confidence})
