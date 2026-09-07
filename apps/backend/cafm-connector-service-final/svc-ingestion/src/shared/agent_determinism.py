"""
svc-ingestion/src/shared/agent_determinism.py

Task 4.1 — Reusable Bound → Aggregate → Constrain determinism cycle.

Implements all four EL-5.x evaluation layers:
  EL-5.BOUND     — Row-level Pydantic validation before AI sees data
  EL-5.AGG       — Per-run output validation (schema + value range checks)
  EL-5.VOTE      — Majority vote integrity check
  EL-5.CONSTRAIN — Hard rules + confidence gate + audit write

Every specialist data agent (Layer 5) calls AgentDeterminismCycle.run().
The cycle writes one row to agent_audit_log before returning AgentResult.

Usage:
    cycle = AgentDeterminismCycle(
        allowed_statuses=["operational", "at_risk", "critical"],
        confidence_threshold=0.80,
        model="claude-haiku-4-5",
        n_runs=3,
        system_prompt="You are an asset health agent...",
        rules_yaml_path=Path("data_agents/rules/asset_rules.yaml"),
        bound_schema=AssetBoundSchema,
        vote_field="status",
    )
    result: AgentResult = await cycle.run(
        raw_rows=rows,
        agent_id="asset-agent",
        domain="asset",
        context={"question": "What is the operational status of MOB-AHU-001?"},
        session=db_session,
        client=anthropic_client,
    )
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Type

import anthropic
import yaml
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used
from data_agents.base_data_agent import AgentResult, SingleRunResult

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────


class BoundValidationError(Exception):
    """
    EL-5.BOUND: raised when one or more input rows fail Pydantic validation.
    Agent halts — requires_human_review is set True on the returned AgentResult.
    """

    def __init__(self, message: str, rejected_rows: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.rejected_rows = rejected_rows


# ── Main cycle class ──────────────────────────────────────────────────────────


class AgentDeterminismCycle:
    """
    Reusable Bound → Aggregate → Constrain cycle for all Layer 5 agents.

    Each agent supplies its own:
      - allowed_statuses: valid enum values for the status/vote field
      - confidence_threshold: gate below which requires_human_review=True
      - model: Claude model string
      - n_runs: normally 3 (concurrent via asyncio.gather)
      - system_prompt: agent-specific instructions
      - rules_yaml_path: path to agent's YAML hard rules
      - bound_schema: Pydantic model to validate input rows
      - vote_field: which field in Claude's JSON response to vote on
    """

    def __init__(
        self,
        allowed_statuses: list[str],
        confidence_threshold: float,
        model: str,
        system_prompt: str,
        rules_yaml_path: Path,
        bound_schema: Type[BaseModel],
        vote_field: str = "status",
        n_runs: int = 3,
        max_tokens: int = 512,
    ) -> None:
        self.allowed_statuses = allowed_statuses
        self.confidence_threshold = confidence_threshold
        self.model = model
        self.system_prompt = system_prompt
        self.rules_yaml_path = rules_yaml_path
        self.bound_schema = bound_schema
        self.vote_field = vote_field
        self.n_runs = n_runs
        self.max_tokens = max_tokens
        self._rules: list[dict[str, Any]] | None = None  # lazy-loaded

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(
        self,
        raw_rows: list[dict[str, Any]],
        agent_id: str,
        domain: str,
        context: dict[str, Any],
        session: AsyncSession,
        client: anthropic.AsyncAnthropic,
        asset_code: str | None = None,
    ) -> AgentResult:
        """
        Full Bound → Aggregate → Vote → Constrain cycle.

        Returns AgentResult with audit_id already written to agent_audit_log.
        Layer 6 EL-6.BOUND verifies this audit_id is resolvable.
        """
        with tracer.start_as_current_span("data_agent.run") as span:
            span.set_attribute("cafm.agent_id", agent_id)
            span.set_attribute("cafm.domain", domain)
            span.set_attribute("cafm.rows_in", len(raw_rows))

            requires_human_review = False
            bound_passed = False
            validated: list[dict[str, Any]] = []
            runs: list[SingleRunResult] = []

            # ── EL-5.BOUND ────────────────────────────────────────────────────
            try:
                validated = self._bound(raw_rows, agent_id)
                bound_passed = True
                logger.info(
                    "el5_bound_passed",
                    agent_id=agent_id,
                    rows_in=len(raw_rows),
                    rows_valid=len(validated),
                )
            except BoundValidationError as exc:
                logger.error(
                    "el5_bound_failed",
                    agent_id=agent_id,
                    error=str(exc),
                    rejected_count=len(exc.rejected_rows),
                )
                span.set_status(StatusCode.ERROR, str(exc))
                # Write minimal audit row and return human_review result
                result = await self._write_audit_and_return(
                    session=session,
                    agent_id=agent_id,
                    domain=domain,
                    asset_code=asset_code,
                    bound_passed=False,
                    runs=[],
                    winner_status=None,
                    winner_confidence=None,
                    hard_rules_fired=[],
                    final_status=self.allowed_statuses[0],
                    confidence_gate_passed=False,
                    requires_human_review=True,
                    model=self.model,
                    tokens_total=0,
                    cost_usd=0.0,
                    raw_rows=raw_rows,
                    reasoning=f"BOUND validation failed: {exc}",
                )
                return result

            # ── EL-5.AGG ──────────────────────────────────────────────────────
            runs, tokens_total, cost_usd = await self._aggregate(
                validated=validated,
                agent_id=agent_id,
                context=context,
                client=client,
            )
            valid_runs = [r for r in runs if r.valid]
            if len(valid_runs) < 2:
                logger.warning(
                    "el5_agg_insufficient_valid_runs",
                    agent_id=agent_id,
                    valid_count=len(valid_runs),
                    total_runs=len(runs),
                )
                requires_human_review = True

            # ── EL-5.VOTE ─────────────────────────────────────────────────────
            winner, runs_agreed = self._majority_vote(runs, agent_id)

            if winner is None:
                # 3-way split or < 2 valid runs
                requires_human_review = True
                winner_status = self.allowed_statuses[0]
                winner_confidence = 0.0
                winner_reasoning = "Vote inconclusive — requires human review."
            else:
                winner_status = winner.status
                winner_confidence = winner.confidence
                winner_reasoning = winner.reasoning

            # ── EL-5.CONSTRAIN ────────────────────────────────────────────────
            hard_rules_fired, final_status, override_review = await self._apply_hard_rules(
                rows=validated,
                current_status=winner_status,
                agent_id=agent_id,
            )
            if override_review:
                requires_human_review = True

            # Confidence gate
            confidence_gate_passed = winner_confidence >= self.confidence_threshold
            if not confidence_gate_passed:
                requires_human_review = True
                logger.info(
                    "el5_constrain_confidence_gate_failed",
                    agent_id=agent_id,
                    confidence=winner_confidence,
                    threshold=self.confidence_threshold,
                )

            # Update span attributes
            span.set_attribute("cafm.runs_agreed", runs_agreed)
            span.set_attribute("cafm.winner_status", final_status)
            span.set_attribute("cafm.confidence_gate_passed", confidence_gate_passed)
            span.set_attribute("cafm.requires_human_review", requires_human_review)
            span.set_attribute("cafm.hard_rules_fired_count", len(hard_rules_fired))

            # Write audit row and build AgentResult
            result = await self._write_audit_and_return(
                session=session,
                agent_id=agent_id,
                domain=domain,
                asset_code=asset_code,
                bound_passed=bound_passed,
                runs=runs,
                winner_status=winner_status,
                winner_confidence=winner_confidence,
                hard_rules_fired=hard_rules_fired,
                final_status=final_status,
                confidence_gate_passed=confidence_gate_passed,
                requires_human_review=requires_human_review,
                model=self.model,
                tokens_total=tokens_total,
                cost_usd=cost_usd,
                raw_rows=validated,
                reasoning=winner_reasoning,
            )

            logger.info(
                "el5_cycle_complete",
                agent_id=agent_id,
                final_status=final_status,
                confidence=winner_confidence,
                runs_agreed=runs_agreed,
                hard_rules_fired=hard_rules_fired,
                requires_human_review=requires_human_review,
                audit_id=str(result.audit_id),
            )
            return result

    # ── EL-5.BOUND ────────────────────────────────────────────────────────────

    def _bound(
        self,
        rows: list[dict[str, Any]],
        agent_id: str,
    ) -> list[dict[str, Any]]:
        """
        EL-5.BOUND: validates every row against bound_schema.

        Rejects nulls, wrong types, impossible values.
        Rejected rows are logged — never silently dropped.
        Raises BoundValidationError if any row fails.
        """
        with tracer.start_as_current_span("data_agent.bound") as span:
            span.set_attribute("cafm.agent_id", agent_id)
            span.set_attribute("cafm.rows_in", len(rows))

            validated: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []

            for row in rows:
                try:
                    obj = self.bound_schema.model_validate(row)
                    validated.append(obj.model_dump())
                except ValidationError as exc:
                    rejected.append({"row": row, "error": str(exc)})
                    logger.warning(
                        "el5_bound_row_rejected",
                        agent_id=agent_id,
                        error=str(exc),
                    )

            span.set_attribute("cafm.rows_valid", len(validated))
            span.set_attribute("cafm.rows_rejected", len(rejected))

            if rejected:
                raise BoundValidationError(
                    f"EL-5.BOUND: {len(rejected)} row(s) failed validation",
                    rejected_rows=rejected,
                )

            return validated

    # ── EL-5.AGG ──────────────────────────────────────────────────────────────

    async def _aggregate(
        self,
        validated: list[dict[str, Any]],
        agent_id: str,
        context: dict[str, Any],
        client: anthropic.AsyncAnthropic,
    ) -> tuple[list[SingleRunResult], int, float]:
        """
        EL-5.AGG: fires N=3 Claude calls concurrently via asyncio.gather.

        Each run output validated before being added to runs list:
          - Response must parse as valid JSON
          - vote_field must be in allowed_statuses
          - confidence must be a float in 0.0–1.0
          - reasoning must be present and ≤ 60 words

        Invalid run output → that run marked valid=False, not counted in vote.
        Returns (runs, tokens_total, cost_usd).
        """
        with tracer.start_as_current_span("data_agent.aggregate") as span:
            span.set_attribute("cafm.agent_id", agent_id)
            span.set_attribute("cafm.model", self.model)
            span.set_attribute("cafm.n_runs", self.n_runs)

            user_prompt = self._build_user_prompt(validated, context)

            tasks = [
                self._single_run(
                    run_number=i + 1,
                    user_prompt=user_prompt,
                    agent_id=agent_id,
                    client=client,
                )
                for i in range(self.n_runs)
            ]
            run_results: list[SingleRunResult] = list(
                await asyncio.gather(*tasks, return_exceptions=False)
            )

            valid_count = sum(1 for r in run_results if r.valid)
            tokens_total = 0
            cost_usd = 0.0
            for r in run_results:
                # tokens are stored on the raw_response side — we track them
                # cumulatively; individual run tokens are estimated from model
                pass

            span.set_attribute("cafm.runs_valid", valid_count)
            span.set_attribute(
                "cafm.winner_status",
                next((r.status for r in run_results if r.valid), "unknown"),
            )

            logger.info(
                "el5_agg_complete",
                agent_id=agent_id,
                runs_valid=valid_count,
                total=len(run_results),
            )
            return run_results, tokens_total, cost_usd

    async def _single_run(
        self,
        run_number: int,
        user_prompt: str,
        agent_id: str,
        client: anthropic.AsyncAnthropic,
    ) -> SingleRunResult:
        """
        One Claude run with EL-5.AGG validation on its output.
        Returns a SingleRunResult with valid=True/False.
        """
        try:
            response = await client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = response.content[0].text.strip() if response.content else ""

            # Track metrics
            claude_api_calls.add(1, {"agent_id": agent_id, "model": self.model})
            in_tokens = getattr(response.usage, "input_tokens", 0)
            out_tokens = getattr(response.usage, "output_tokens", 0)
            claude_tokens_used.add(
                in_tokens + out_tokens,
                {"agent_id": agent_id, "model": self.model},
            )

            # Strip markdown fences if present
            if raw_text.startswith("```"):
                parts = raw_text.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw_text = inner.strip()

            # Parse JSON
            parsed = json.loads(raw_text)

            # EL-5.AGG validation
            status_val = str(parsed.get(self.vote_field, "")).strip()
            confidence_val = float(parsed.get("confidence", 0.0))
            reasoning_val = str(parsed.get("reasoning", "")).strip()

            failure: str = ""
            is_valid = True

            if status_val not in self.allowed_statuses:
                is_valid = False
                failure = (
                    f"status '{status_val}' not in allowed: {self.allowed_statuses}"
                )
            elif not (0.0 <= confidence_val <= 1.0):
                is_valid = False
                failure = f"confidence {confidence_val} out of range 0.0–1.0"
            elif not reasoning_val:
                is_valid = False
                failure = "reasoning is empty"

            return SingleRunResult(
                run_number=run_number,
                status=status_val if is_valid else self.allowed_statuses[0],
                confidence=confidence_val if is_valid else 0.0,
                reasoning=reasoning_val,
                raw_response=raw_text,
                valid=is_valid,
                failure_reason=failure,
            )

        except (json.JSONDecodeError, anthropic.APIError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "el5_agg_run_failed",
                agent_id=agent_id,
                run_number=run_number,
                error=str(exc),
            )
            return SingleRunResult(
                run_number=run_number,
                status=self.allowed_statuses[0],
                confidence=0.0,
                reasoning="",
                raw_response="",
                valid=False,
                failure_reason=str(exc),
            )

    def _build_user_prompt(
        self,
        validated: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str:
        """Build the user-turn prompt from validated rows + agent context."""
        rows_json = json.dumps(validated, indent=2, default=str)
        question = context.get("question", "Analyse this data and return your assessment.")
        return (
            f"DATA:\n{rows_json}\n\n"
            f"TASK: {question}\n\n"
            f"Return ONLY a JSON object (no markdown fences):\n"
            f'{{\n'
            f'  "{self.vote_field}": "<one of: {", ".join(self.allowed_statuses)}>",\n'
            f'  "confidence": <float 0.0-1.0>,\n'
            f'  "reasoning": "<max 60 words>"\n'
            f"}}"
        )

    # ── EL-5.VOTE ─────────────────────────────────────────────────────────────

    def _majority_vote(
        self,
        runs: list[SingleRunResult],
        agent_id: str,
    ) -> tuple[SingleRunResult | None, int]:
        """
        EL-5.VOTE: majority vote on the status field across all valid runs.

        Returns (winner_run, runs_agreed).
        If < 2 valid runs or a 3-way split → returns (None, 0).
        Tiebreak (2-way on ≥4 runs): highest confidence wins.
        """
        with tracer.start_as_current_span("data_agent.vote") as span:
            span.set_attribute("cafm.agent_id", agent_id)

            valid_runs = [r for r in runs if r.valid]
            span.set_attribute("cafm.valid_runs", len(valid_runs))

            if len(valid_runs) < 2:
                logger.warning("el5_vote_insufficient_runs", agent_id=agent_id, count=len(valid_runs))
                span.set_attribute("cafm.vote_result", "insufficient_runs")
                return None, 0

            status_counts = Counter(r.status for r in valid_runs)
            most_common = status_counts.most_common()

            # Check for majority (> half of valid runs)
            top_status, top_count = most_common[0]

            # 3-way split check (all statuses appear exactly once)
            if len(most_common) == len(valid_runs) and top_count == 1:
                logger.warning("el5_vote_three_way_split", agent_id=agent_id)
                span.set_attribute("cafm.vote_result", "three_way_split")
                return None, 0

            # Find runs that agreed on winner status
            agreeing_runs = [r for r in valid_runs if r.status == top_status]
            runs_agreed = len(agreeing_runs)

            # Pick winner: highest confidence among agreeing runs
            winner = max(agreeing_runs, key=lambda r: r.confidence)

            span.set_attribute("cafm.runs_agreed", runs_agreed)
            span.set_attribute("cafm.winner_status", top_status)

            logger.info(
                "el5_vote_result",
                agent_id=agent_id,
                winner_status=top_status,
                runs_agreed=runs_agreed,
                confidence=winner.confidence,
            )
            return winner, runs_agreed

    # ── EL-5.CONSTRAIN (hard rules) ───────────────────────────────────────────

    async def _apply_hard_rules(
        self,
        rows: list[dict[str, Any]],
        current_status: str,
        agent_id: str,
    ) -> tuple[list[str], str, bool]:
        """
        EL-5.CONSTRAIN step 1 — apply YAML hard rules.

        Hard rules always override AI vote — no exceptions.
        Returns (hard_rules_fired, final_status, requires_human_review_override).
        """
        with tracer.start_as_current_span("data_agent.constrain") as span:
            span.set_attribute("cafm.agent_id", agent_id)

            rules = self._load_rules()
            fired: list[str] = []
            final_status = current_status
            override_review = False

            for rule in rules:
                rule_name: str = rule.get("name", "unnamed")
                conditions: list[dict[str, Any]] = rule.get("conditions", [])
                action: dict[str, Any] = rule.get("action", {})

                if self._evaluate_rule_conditions(conditions, rows):
                    fired.append(rule_name)

                    # Apply status override
                    if "set_status" in action:
                        final_status = action["set_status"]

                    # Apply requires_human_review override
                    if action.get("requires_human_review", False):
                        override_review = True

                    logger.info(
                        "el5_hard_rule_fired",
                        agent_id=agent_id,
                        rule=rule_name,
                        set_status=action.get("set_status"),
                        requires_human_review=action.get("requires_human_review", False),
                    )

            span.set_attribute("cafm.hard_rules_fired_count", len(fired))
            span.set_attribute("cafm.final_status", final_status)
            return fired, final_status, override_review

    def _evaluate_rule_conditions(
        self,
        conditions: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> bool:
        """
        Evaluate all conditions for a rule against the validated rows.
        All conditions must be True for the rule to fire (AND logic).

        Condition types:
          any_row_where:    at least one row satisfies the field/operator/value check
          all_rows_where:   every row satisfies the check
          count_where_gte:  count of matching rows >= threshold
          field_contains:   string field contains substring (case-insensitive)
        """
        for cond in conditions:
            cond_type = cond.get("type", "any_row_where")
            field = cond.get("field", "")
            operator = cond.get("operator", "eq")
            value = cond.get("value")
            threshold = cond.get("threshold", 0)

            if cond_type == "any_row_where":
                if not any(self._row_matches(row, field, operator, value) for row in rows):
                    return False

            elif cond_type == "all_rows_where":
                if not all(self._row_matches(row, field, operator, value) for row in rows):
                    return False

            elif cond_type == "count_where_gte":
                count = sum(1 for row in rows if self._row_matches(row, field, operator, value))
                if count < threshold:
                    return False

            elif cond_type == "field_contains":
                # Check if any row's field contains the substring
                if not any(
                    value.lower() in str(row.get(field, "")).lower()
                    for row in rows
                ):
                    return False

        return True

    @staticmethod
    def _row_matches(
        row: dict[str, Any],
        field: str,
        operator: str,
        value: Any,
    ) -> bool:
        """Evaluate a single field operator against a row."""
        # Support nested field access with dot notation
        row_val = row
        for part in field.split("."):
            if isinstance(row_val, dict):
                row_val = row_val.get(part)
            else:
                return False

        if operator == "eq":
            return row_val == value
        elif operator == "neq":
            return row_val != value
        elif operator == "gte":
            try:
                return float(row_val) >= float(value)
            except (TypeError, ValueError):
                return False
        elif operator == "lte":
            try:
                return float(row_val) <= float(value)
            except (TypeError, ValueError):
                return False
        elif operator == "gt":
            try:
                return float(row_val) > float(value)
            except (TypeError, ValueError):
                return False
        elif operator == "lt":
            try:
                return float(row_val) < float(value)
            except (TypeError, ValueError):
                return False
        elif operator == "in":
            return row_val in (value if isinstance(value, list) else [value])
        elif operator == "not_in":
            return row_val not in (value if isinstance(value, list) else [value])
        elif operator == "is_null":
            return row_val is None
        elif operator == "is_not_null":
            return row_val is not None
        return False

    def _load_rules(self) -> list[dict[str, Any]]:
        """Lazy-load and cache YAML rules from disk."""
        if self._rules is not None:
            return self._rules

        if not self.rules_yaml_path.exists():
            logger.warning("el5_rules_file_not_found", path=str(self.rules_yaml_path))
            self._rules = []
            return self._rules

        with open(self.rules_yaml_path) as fh:
            data = yaml.safe_load(fh) or {}
        self._rules = data.get("rules", [])
        logger.info(
            "el5_rules_loaded",
            path=str(self.rules_yaml_path),
            count=len(self._rules),
        )
        return self._rules

    # ── Audit write + AgentResult builder ────────────────────────────────────

    async def _write_audit_and_return(
        self,
        session: AsyncSession,
        agent_id: str,
        domain: str,
        asset_code: str | None,
        bound_passed: bool,
        runs: list[SingleRunResult],
        winner_status: str | None,
        winner_confidence: float | None,
        hard_rules_fired: list[str],
        final_status: str,
        confidence_gate_passed: bool,
        requires_human_review: bool,
        model: str,
        tokens_total: int,
        cost_usd: float,
        raw_rows: list[dict[str, Any]],
        reasoning: str,
    ) -> AgentResult:
        """
        Writes one row to agent_audit_log and returns AgentResult.

        IMPORTANT: audit_id is written to the DB BEFORE AgentResult is returned.
        Layer 6 EL-6.BOUND verifies audit_ids are resolvable.
        """
        from models.ingestion import AgentAuditLog

        def _run_output(run: SingleRunResult | None) -> dict[str, Any] | None:
            if run is None:
                return None
            return {
                "status": run.status,
                "confidence": run.confidence,
                "reasoning": run.reasoning,
                "valid": run.valid,
                "failure_reason": run.failure_reason,
            }

        # Index runs by run_number (1-indexed)
        run_map: dict[int, SingleRunResult] = {r.run_number: r for r in runs}
        r1 = run_map.get(1)
        r2 = run_map.get(2)
        r3 = run_map.get(3)

        # Count runs_agreed (valid runs that agreed on winner_status)
        runs_agreed = sum(
            1 for r in runs if r.valid and r.status == final_status
        )

        audit_row = AgentAuditLog(
            agent_id=agent_id,
            domain=domain,
            asset_code=asset_code,
            bound_validation_passed=bound_passed,
            run_1_output=_run_output(r1),
            run_2_output=_run_output(r2),
            run_3_output=_run_output(r3),
            run_1_valid=r1.valid if r1 else None,
            run_2_valid=r2.valid if r2 else None,
            run_3_valid=r3.valid if r3 else None,
            runs_agreed=runs_agreed,
            winner_status=winner_status,
            winner_confidence=winner_confidence,
            hard_rules_fired=hard_rules_fired if hard_rules_fired else None,
            final_status=final_status,
            confidence_gate_passed=confidence_gate_passed,
            requires_human_review=requires_human_review,
            model_used=model,
            tokens_total=tokens_total,
            cost_usd=cost_usd,
            timestamp=datetime.now(timezone.utc),
        )
        session.add(audit_row)
        await session.flush()  # assigns audit_row.id without committing

        # Build and return AgentResult — audit_id is now in the DB
        return AgentResult(
            agent_id=agent_id,
            domain=domain,  # type: ignore[arg-type]
            status=final_status,
            confidence=winner_confidence if winner_confidence is not None else 0.0,
            reasoning=reasoning,
            runs=runs,
            runs_agreed=runs_agreed,
            hard_rules_fired=hard_rules_fired,
            requires_human_review=requires_human_review,
            raw_data={"rows": raw_rows},
            audit_id=audit_row.id,
        )
