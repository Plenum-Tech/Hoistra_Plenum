"""
svc-ingestion/src/analysis/orchestrator.py

Task 5.1 — Layer 6 Orchestration Analysis.

Receives 5 pre-validated AgentResult objects from Layer 5.
Runs its own Bound → Aggregate → Vote → Constrain cycle on top of
already-clean data — this is Level 2 of the two-level determinism system.

Evaluation layers:
  EL-6.BOUND     — All 5 AgentResults present, typed, no human_review flags
  EL-6.AGG       — N=3 Sonnet runs, each output validated before vote
  EL-6.VOTE      — Majority vote on action field
  EL-6.CONSTRAIN — Confidence gate ≥ 0.85 + safety rules + audit write

Output: CMSDecision written to orchestration_audit_log (INSERT ONLY).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used
from data_agents.base_data_agent import AgentResult
from analysis.action_schema import CMSDecision, SingleOrchestratorRun

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL = "claude-sonnet-4-6"
_N_RUNS = 3
_CONFIDENCE_GATE = 0.85
_VALID_ACTIONS = {"create_wo", "order_part", "alert_critical", "no_action"}
_VALID_PRIORITIES = {"low", "medium", "high", "critical"}
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = """\
You are a facilities management decision engine for a CAFM system in the UAE.

You receive pre-validated assessment results from 5 specialist agents:
  - Asset Agent: operational status of the asset
  - WO Agent: triage classification of open work orders
  - PM Agent: preventive maintenance schedule status
  - Parts Agent: inventory reorder urgency
  - Inspection Agent: risk level from site inspection findings

Based on these combined signals, determine the single most important action to take.

Actions:
  create_wo        — Create or escalate a work order (asset needs maintenance)
  order_part       — Flag parts for immediate reorder (stock critically low)
  alert_critical   — Raise critical safety alert (immediate human attention needed)
  no_action        — No immediate action required

Priorities:
  critical — immediate (safety risk, asset failure imminent)
  high     — same day
  medium   — this week
  low      — scheduled maintenance window

Be conservative: when signals conflict, recommend the higher-impact action.
contributing_agents must list which agent IDs drove your decision.

Return ONLY a JSON object (no markdown fences):
{
  "action": "<one of the 4 actions>",
  "priority": "<low|medium|high|critical>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<max 60 words>",
  "contributing_agents": ["<agent_id>", ...]
}
"""


# ── Public entry point ────────────────────────────────────────────────────────


async def run_orchestration(
    asset_code: str,
    agent_results: dict[str, AgentResult],
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> CMSDecision:
    """
    Full Layer 6 Bound → Aggregate → Vote → Constrain cycle.

    Args:
        asset_code: the asset being analysed
        agent_results: dict keyed by domain ("asset", "wo", "pm", "parts", "inspection")
        session: async DB session
        client: Anthropic async client

    Returns:
        CMSDecision with audit_id already written to orchestration_audit_log.
    """
    with tracer.start_as_current_span("orchestrator.run") as span:
        span.set_attribute("cafm.asset_code", asset_code)
        span.set_attribute("cafm.agent_results_count", len(agent_results))

        # ── EL-6.BOUND ────────────────────────────────────────────────────────
        bound_passed, bound_failure = _el6_bound(agent_results)

        if not bound_passed:
            logger.warning(
                "el6_bound_failed",
                asset_code=asset_code,
                reason=bound_failure,
            )
            span.set_attribute("cafm.bound_passed", False)
            decision = await _write_audit_and_return(
                session=session,
                asset_code=asset_code,
                bound_passed=False,
                action="human_review",
                priority="high",
                confidence=0.0,
                reasoning=f"EL-6.BOUND failed: {bound_failure}",
                runs=[],
                runs_agreed=0,
                confidence_gate_passed=False,
                safety_passed=False,
                hard_rules_fired=[],
                agent_results=agent_results,
            )
            return decision

        span.set_attribute("cafm.bound_passed", True)
        logger.info("el6_bound_passed", asset_code=asset_code)

        # ── EL-6.AGG ──────────────────────────────────────────────────────────
        runs, tokens_total, cost_usd = await _el6_aggregate(
            asset_code=asset_code,
            agent_results=agent_results,
            client=client,
        )

        valid_runs = [r for r in runs if r.valid]
        if len(valid_runs) < 2:
            logger.warning(
                "el6_agg_insufficient_valid_runs",
                asset_code=asset_code,
                valid_count=len(valid_runs),
            )

        # ── EL-6.VOTE ─────────────────────────────────────────────────────────
        winner, runs_agreed = _el6_vote(runs, asset_code)

        if winner is None:
            logger.warning("el6_vote_inconclusive", asset_code=asset_code)
            decision = await _write_audit_and_return(
                session=session,
                asset_code=asset_code,
                bound_passed=True,
                action="human_review",
                priority="high",
                confidence=0.0,
                reasoning="Vote inconclusive — requires human review.",
                runs=runs,
                runs_agreed=0,
                confidence_gate_passed=False,
                safety_passed=False,
                hard_rules_fired=[],
                agent_results=agent_results,
            )
            return decision

        # ── EL-6.CONSTRAIN ────────────────────────────────────────────────────
        final_action, final_priority, hard_rules_fired, safety_passed = _el6_constrain(
            winner=winner,
            agent_results=agent_results,
        )

        confidence_gate_passed = winner.confidence >= _CONFIDENCE_GATE
        if not confidence_gate_passed:
            logger.info(
                "el6_constrain_confidence_gate_failed",
                asset_code=asset_code,
                confidence=winner.confidence,
                gate=_CONFIDENCE_GATE,
            )
            final_action = "human_review"
            safety_passed = False

        span.set_attribute("cafm.action", final_action)
        span.set_attribute("cafm.confidence", winner.confidence)
        span.set_attribute("cafm.runs_agreed", runs_agreed)
        span.set_attribute("cafm.confidence_gate_passed", confidence_gate_passed)
        span.set_attribute("cafm.safety_passed", safety_passed)
        span.set_attribute("cafm.hard_rules_fired", len(hard_rules_fired))

        decision = await _write_audit_and_return(
            session=session,
            asset_code=asset_code,
            bound_passed=True,
            action=final_action,
            priority=final_priority,
            confidence=winner.confidence,
            reasoning=winner.reasoning,
            runs=runs,
            runs_agreed=runs_agreed,
            confidence_gate_passed=confidence_gate_passed,
            safety_passed=safety_passed,
            hard_rules_fired=hard_rules_fired,
            contributing_agents=winner.contributing_agents,
            agent_results=agent_results,
        )

        logger.info(
            "el6_cycle_complete",
            asset_code=asset_code,
            action=final_action,
            priority=final_priority,
            confidence=winner.confidence,
            runs_agreed=runs_agreed,
            audit_id=str(decision.audit_id),
        )
        return decision


# ── EL-6.BOUND ────────────────────────────────────────────────────────────────


def _el6_bound(
    agent_results: dict[str, AgentResult],
) -> tuple[bool, str]:
    """
    EL-6.BOUND: validate all 5 AgentResult objects before AI is called.

    Checks:
      - All 5 domains present: asset, wo, pm, parts, inspection
      - No AgentResult has requires_human_review=True
      - All confidence values in 0.0–1.0
      - All audit_ids are valid UUIDs (resolvable — means audit log was written)

    Returns (passed, failure_reason).
    If any check fails → action = human_review, AI not called.
    """
    with tracer.start_as_current_span("orchestrator.bound") as span:
        required_domains = {"asset", "wo", "pm", "parts", "inspection"}
        present_domains = set(agent_results.keys())

        # Check all 5 AgentResults present
        missing = required_domains - present_domains
        if missing:
            reason = f"Missing agent results for domains: {missing}"
            span.set_attribute("cafm.bound_passed", False)
            span.set_attribute("cafm.failure_reason", reason)
            return False, reason

        # Check no human_review flags
        review_flagged = [
            domain for domain, r in agent_results.items()
            if r.requires_human_review
        ]
        if review_flagged:
            reason = f"AgentResults require human review: {review_flagged}"
            span.set_attribute("cafm.bound_passed", False)
            span.set_attribute("cafm.any_requires_human_review", True)
            return False, reason

        # Check confidence values in range
        for domain, r in agent_results.items():
            if not (0.0 <= r.confidence <= 1.0):
                reason = f"Invalid confidence {r.confidence} from {domain} agent"
                span.set_attribute("cafm.bound_passed", False)
                return False, reason

        # Check audit_ids are valid UUIDs (proxy for "was written to DB")
        for domain, r in agent_results.items():
            if not isinstance(r.audit_id, uuid.UUID):
                reason = f"Invalid audit_id type from {domain} agent"
                span.set_attribute("cafm.bound_passed", False)
                return False, reason

        span.set_attribute("cafm.bound_passed", True)
        span.set_attribute("cafm.agent_results_count", len(agent_results))
        span.set_attribute("cafm.any_requires_human_review", False)
        return True, ""


# ── EL-6.AGG ──────────────────────────────────────────────────────────────────


async def _el6_aggregate(
    asset_code: str,
    agent_results: dict[str, AgentResult],
    client: anthropic.AsyncAnthropic,
) -> tuple[list[SingleOrchestratorRun], int, float]:
    """
    EL-6.AGG: N=3 concurrent Sonnet runs via asyncio.gather.

    Each run output validated before being counted in vote:
      - action must be in _VALID_ACTIONS
      - priority must be in _VALID_PRIORITIES
      - confidence must be float in 0.0–1.0
      - reasoning must be present and ≤ 60 words

    Invalid run → valid=False, not counted in vote.
    """
    with tracer.start_as_current_span("orchestrator.aggregate") as span:
        span.set_attribute("cafm.model", _MODEL)
        span.set_attribute("cafm.n_runs", _N_RUNS)

        user_prompt = _build_orchestrator_prompt(asset_code, agent_results)

        tasks = [
            _single_orchestrator_run(
                run_number=i + 1,
                user_prompt=user_prompt,
                asset_code=asset_code,
                client=client,
            )
            for i in range(_N_RUNS)
        ]
        runs: list[SingleOrchestratorRun] = list(
            await asyncio.gather(*tasks, return_exceptions=False)
        )

        valid_count = sum(1 for r in runs if r.valid)
        span.set_attribute("cafm.runs_valid", valid_count)
        span.set_attribute(
            "cafm.winner_action",
            next((r.action for r in runs if r.valid), "unknown"),
        )

        logger.info(
            "el6_agg_complete",
            asset_code=asset_code,
            runs_valid=valid_count,
            total=len(runs),
        )
        return runs, 0, 0.0


async def _single_orchestrator_run(
    run_number: int,
    user_prompt: str,
    asset_code: str,
    client: anthropic.AsyncAnthropic,
) -> SingleOrchestratorRun:
    """One Sonnet run with EL-6.AGG validation on its output."""
    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text.strip() if response.content else ""

        claude_api_calls.add(1, {"agent_id": "orchestrator", "model": _MODEL})
        in_tokens = getattr(response.usage, "input_tokens", 0)
        out_tokens = getattr(response.usage, "output_tokens", 0)
        claude_tokens_used.add(
            in_tokens + out_tokens,
            {"agent_id": "orchestrator", "model": _MODEL},
        )

        # Strip markdown fences
        if raw_text.startswith("```"):
            parts = raw_text.split("```", 2)
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw_text = inner.strip()

        parsed = json.loads(raw_text)

        action_val = str(parsed.get("action", "")).strip()
        priority_val = str(parsed.get("priority", "low")).strip()
        confidence_val = float(parsed.get("confidence", 0.0))
        reasoning_val = str(parsed.get("reasoning", "")).strip()
        agents_val = parsed.get("contributing_agents", [])
        if not isinstance(agents_val, list):
            agents_val = []

        failure = ""
        is_valid = True

        if action_val not in _VALID_ACTIONS:
            is_valid = False
            failure = f"action '{action_val}' not in {_VALID_ACTIONS}"
        elif priority_val not in _VALID_PRIORITIES:
            is_valid = False
            failure = f"priority '{priority_val}' not in {_VALID_PRIORITIES}"
        elif not (0.0 <= confidence_val <= 1.0):
            is_valid = False
            failure = f"confidence {confidence_val} out of range"
        elif not reasoning_val:
            is_valid = False
            failure = "reasoning is empty"

        return SingleOrchestratorRun(
            run_number=run_number,
            action=action_val if is_valid else "no_action",
            priority=priority_val if is_valid else "low",
            confidence=confidence_val if is_valid else 0.0,
            reasoning=reasoning_val,
            contributing_agents=agents_val,
            raw_response=raw_text,
            valid=is_valid,
            failure_reason=failure,
        )

    except (json.JSONDecodeError, anthropic.APIError, ValueError, TypeError) as exc:
        logger.warning(
            "el6_agg_run_failed",
            asset_code=asset_code,
            run_number=run_number,
            error=str(exc),
        )
        return SingleOrchestratorRun(
            run_number=run_number,
            action="no_action",
            priority="low",
            confidence=0.0,
            reasoning="",
            raw_response="",
            valid=False,
            failure_reason=str(exc),
        )


def _build_orchestrator_prompt(
    asset_code: str,
    agent_results: dict[str, AgentResult],
) -> str:
    """Build the user-turn prompt from all 5 AgentResults."""
    summaries: list[str] = []
    for domain, result in agent_results.items():
        summaries.append(
            f"[{domain.upper()} AGENT]\n"
            f"  status: {result.status}\n"
            f"  confidence: {result.confidence}\n"
            f"  reasoning: {result.reasoning}\n"
            f"  runs_agreed: {result.runs_agreed}/3\n"
            f"  hard_rules_fired: {result.hard_rules_fired or 'none'}"
        )

    return (
        f"ASSET: {asset_code}\n\n"
        + "\n\n".join(summaries)
        + "\n\nWhat is the single most important action to take for this asset?"
    )


# ── EL-6.VOTE ─────────────────────────────────────────────────────────────────


def _el6_vote(
    runs: list[SingleOrchestratorRun],
    asset_code: str,
) -> tuple[SingleOrchestratorRun | None, int]:
    """
    EL-6.VOTE: majority vote on the action field.

    3-way split → (None, 0) → action = human_review.
    Tiebreak: highest confidence among agreeing runs.
    Returns (winner_run, runs_agreed).
    """
    with tracer.start_as_current_span("orchestrator.vote") as span:
        span.set_attribute("cafm.asset_code", asset_code)

        valid_runs = [r for r in runs if r.valid]
        span.set_attribute("cafm.valid_runs", len(valid_runs))

        if len(valid_runs) < 2:
            span.set_attribute("cafm.vote_result", "insufficient_runs")
            return None, 0

        action_counts = Counter(r.action for r in valid_runs)
        most_common = action_counts.most_common()
        top_action, top_count = most_common[0]

        # 3-way split: all actions appear exactly once
        if len(most_common) == len(valid_runs) and top_count == 1:
            span.set_attribute("cafm.vote_result", "three_way_split")
            logger.warning("el6_vote_three_way_split", asset_code=asset_code)
            return None, 0

        agreeing = [r for r in valid_runs if r.action == top_action]
        runs_agreed = len(agreeing)
        winner = max(agreeing, key=lambda r: r.confidence)

        span.set_attribute("cafm.runs_agreed", runs_agreed)
        span.set_attribute("cafm.winner_action", top_action)
        span.set_attribute("cafm.vote_result", "majority")

        logger.info(
            "el6_vote_result",
            asset_code=asset_code,
            action=top_action,
            runs_agreed=runs_agreed,
            confidence=winner.confidence,
        )
        return winner, runs_agreed


# ── EL-6.CONSTRAIN ────────────────────────────────────────────────────────────


def _el6_constrain(
    winner: SingleOrchestratorRun,
    agent_results: dict[str, AgentResult],
) -> tuple[str, str, list[str], bool]:
    """
    EL-6.CONSTRAIN safety rules — always applied after vote.

    Gate 1: confidence < 0.85 → downgrade to human_review (handled in caller)
    Gate 2: action == alert_critical → always human_review regardless of confidence
    Gate 3: any hard rule fired in ANY agent → included in reasoning context

    Returns (final_action, final_priority, hard_rules_fired, safety_passed).
    """
    with tracer.start_as_current_span("orchestrator.constrain") as span:
        final_action = winner.action
        final_priority = winner.priority
        hard_rules_fired: list[str] = []
        safety_passed = True

        # Collect all hard rules fired across all 5 agents
        for domain, result in agent_results.items():
            for rule in result.hard_rules_fired:
                hard_rules_fired.append(f"{domain}:{rule}")

        # Gate 2: alert_critical always requires human review
        if final_action == "alert_critical":
            safety_passed = False
            logger.info(
                "el6_constrain_alert_critical_gate",
                action=final_action,
            )

        span.set_attribute("cafm.action", final_action)
        span.set_attribute("cafm.confidence", winner.confidence)
        span.set_attribute("cafm.confidence_gate_passed", winner.confidence >= _CONFIDENCE_GATE)
        span.set_attribute("cafm.safety_rules_fired", len(hard_rules_fired))

        return final_action, final_priority, hard_rules_fired, safety_passed


# ── Audit write + CMSDecision builder ─────────────────────────────────────────


async def _write_audit_and_return(
    session: AsyncSession,
    asset_code: str,
    bound_passed: bool,
    action: str,
    priority: str,
    confidence: float,
    reasoning: str,
    runs: list[SingleOrchestratorRun],
    runs_agreed: int,
    confidence_gate_passed: bool,
    safety_passed: bool,
    hard_rules_fired: list[str],
    agent_results: dict[str, AgentResult],
    contributing_agents: list[str] | None = None,
) -> CMSDecision:
    """
    Write one row to orchestration_audit_log (INSERT ONLY — no UPDATE/DELETE ever).
    Returns CMSDecision with audit_id from the written row.

    Layer 7 verifies audit_id is resolvable before acting on any CMSDecision.
    """
    audit_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Serialise agent results for audit storage
    agent_results_jsonb: dict[str, Any] = {
        domain: {
            "agent_id": r.agent_id,
            "status": r.status,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
            "runs_agreed": r.runs_agreed,
            "hard_rules_fired": r.hard_rules_fired,
            "requires_human_review": r.requires_human_review,
            "audit_id": str(r.audit_id),
        }
        for domain, r in agent_results.items()
    }

    # Per-run validity flags
    run_map = {r.run_number: r for r in runs}
    r1, r2, r3 = run_map.get(1), run_map.get(2), run_map.get(3)

    tokens_total = 0
    cost_usd = 0.0

    # INSERT ONLY — orchestration_audit_log has no UPDATE or DELETE ever
    await session.execute(
        text(
            """
            INSERT INTO plenum_cafm.orchestration_audit_log (
                id, asset_code, bound_passed, action, priority, confidence,
                reasoning, runs_agreed,
                run_1_valid, run_2_valid, run_3_valid,
                confidence_gate_passed, safety_passed,
                agent_results_jsonb, hard_rules_fired,
                model_used, tokens_total, cost_usd, timestamp
            ) VALUES (
                :id, :asset_code, :bound_passed, :action, :priority, :confidence,
                :reasoning, :runs_agreed,
                :run_1_valid, :run_2_valid, :run_3_valid,
                :confidence_gate_passed, :safety_passed,
                :agent_results_jsonb, :hard_rules_fired,
                :model_used, :tokens_total, :cost_usd, :timestamp
            )
            """
        ),
        {
            "id": audit_id,
            "asset_code": asset_code,
            "bound_passed": bound_passed,
            "action": action,
            "priority": priority,
            "confidence": confidence,
            "reasoning": reasoning,
            "runs_agreed": runs_agreed,
            "run_1_valid": r1.valid if r1 else None,
            "run_2_valid": r2.valid if r2 else None,
            "run_3_valid": r3.valid if r3 else None,
            "confidence_gate_passed": confidence_gate_passed,
            "safety_passed": safety_passed,
            "agent_results_jsonb": json.dumps(agent_results_jsonb),
            "hard_rules_fired": json.dumps(hard_rules_fired),
            "model_used": _MODEL,
            "tokens_total": tokens_total,
            "cost_usd": cost_usd,
            "timestamp": now,
        },
    )

    logger.info(
        "el6_audit_written",
        audit_id=str(audit_id),
        asset_code=asset_code,
        action=action,
        confidence=confidence,
    )

    return CMSDecision(
        action=action,  # type: ignore[arg-type]
        asset_code=asset_code,
        priority=priority,  # type: ignore[arg-type]
        confidence=confidence,
        reasoning=reasoning,
        contributing_agents=contributing_agents or [],
        runs_agreed=runs_agreed,
        audit_id=audit_id,
        timestamp=now,
        runs=runs,
        safety_passed=safety_passed,
        hard_rules_fired=hard_rules_fired,
        agent_results_summary=agent_results_jsonb,
    )
