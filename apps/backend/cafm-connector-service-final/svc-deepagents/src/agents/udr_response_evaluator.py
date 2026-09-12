"""EL-UDR: hard evaluation gate for user-facing UDR answers."""
from __future__ import annotations

import json
import re
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

log = structlog.get_logger(__name__)

UDR_TOOL_NAMES = frozenset(
    {
        "get_schema",
        "lookup_user",
        "query_table",
        "find_asset",
        "find_location",
        "get_asset_documents",
        "udr_agent_query",
        "udr_list_tables",
        "udr_describe_table",
        "udr_read_records",
        "udr_get_record",
        "udr_search_records",
        "udr_create_record",
        "udr_update_record",
        "udr_delete_record",
        "udr_execute_select",
        "retrieve_workspace_corpus_summary",
        "retrieve_vector_evidence",
        "resolve_cross_source_links",
        "answer_with_graph_context",
    }
)
EVAL_THRESHOLD = 0.85
_MAX_EVIDENCE_CHARS = 24_000


class UdrEvaluation(BaseModel):
    """Typed evaluator result; invalid judge output cannot pass the gate."""

    grounded: bool
    answers_question: bool
    count_consistent: bool
    score: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    corrected_answer: str | None = None
    #: False when the evaluator never produced a readable verdict — its own output failed to
    #: parse, or the call failed. The other fields are then placeholders and assert nothing:
    #: "the judge did not answer" is a different fact from "the judge said no", and only the
    #: second is a finding about the candidate.
    evaluated: bool = True

    @property
    def passed(self) -> bool:
        return (
            self.grounded
            and self.answers_question
            and self.count_consistent
            and self.score >= EVAL_THRESHOLD
        )


def has_udr_tool_calls(tool_calls: list[dict[str, Any]] | None) -> bool:
    for call in tool_calls or []:
        tool = str(call.get("tool") or "")
        if tool in UDR_TOOL_NAMES:
            return True
        if tool == "task":
            task_input = call.get("input")
            if isinstance(task_input, dict) and task_input.get("agent") == "udr":
                return True
    return False


def _json_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _parse_evaluation(content: Any) -> UdrEvaluation:
    text = _json_content(content).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return UdrEvaluation.model_validate_json(text)


def _evidence(tool_calls: list[dict[str, Any]]) -> str:
    """Everything the answer could have been built from — not only the UDR half.

    This used to keep UDR tools and task(agent="udr") and drop every other sub-agent. The
    orchestrator fires several agents in parallel and synthesises one reply from all of them,
    so the evaluator was judging a combined answer against one of its sources. When that
    source was the wrong one — UDR reporting zero readings from a join on the wrong key while
    energy_intelligence correctly reported fifty — the evaluator called the correct figure
    ungrounded and degraded the answer. A judge of a combination has to see the combination.

    The gate deciding WHEN the evaluator runs (has_udr_tool_calls) is unchanged.
    """
    relevant = [
        call
        for call in tool_calls
        if str(call.get("tool") or "") in UDR_TOOL_NAMES
        or call.get("tool") == "task"
    ]
    return json.dumps(relevant, ensure_ascii=False, default=str)[:_MAX_EVIDENCE_CHARS]


async def evaluate_udr_response(
    *,
    user_message: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    llm: Any,
) -> tuple[str, UdrEvaluation]:
    """Evaluate and, when necessary, replace a UDR answer before it reaches the UI."""
    system = SystemMessage(
        content=(
            "You are EL-UDR, a strict CAFM answer evaluator. Compare the candidate answer only "
            "against the user request and UDR tool evidence. Check that it answers the requested "
            "operation (including GROUP BY, counts, > versus >=, filters, and entity scope), that "
            "every factual value is grounded, and that counts equal the evidence. Return JSON only "
            "with keys grounded, answers_question, count_consistent, score, issues, "
            "corrected_answer. If the candidate is wrong, corrected_answer must be a concise answer "
            "built only from the evidence. Never add external knowledge."
        )
    )
    human = HumanMessage(
        content=(
            f"USER REQUEST:\n{user_message}\n\n"
            f"CANDIDATE ANSWER:\n{answer}\n\n"
            f"UDR TOOL EVIDENCE:\n{_evidence(tool_calls)}"
        )
    )
    try:
        response = await llm.ainvoke([system, human])
        evaluation = _parse_evaluation(getattr(response, "content", response))
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("udr.eval.invalid_output", error=str(exc)[:300])
        evaluation = UdrEvaluation(
            grounded=False,
            answers_question=False,
            count_consistent=False,
            score=0.0,
            issues=["Evaluator output failed schema validation"],
            evaluated=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("udr.eval.failed", error=str(exc)[:300], exc_info=True)
        evaluation = UdrEvaluation(
            grounded=False,
            answers_question=False,
            count_consistent=False,
            score=0.0,
            issues=["Evaluator service unavailable"],
            evaluated=False,
        )

    if evaluation.passed:
        log.info("udr.eval.passed", score=evaluation.score)
        return answer, evaluation

    corrected = (evaluation.corrected_answer or "").strip()
    if corrected:
        # A correction is another agent output, so it must pass the same gate before delivery.
        corrected_human = HumanMessage(
            content=(
                f"USER REQUEST:\n{user_message}\n\n"
                f"CANDIDATE ANSWER:\n{corrected}\n\n"
                f"UDR TOOL EVIDENCE:\n{_evidence(tool_calls)}"
            )
        )
        try:
            corrected_response = await llm.ainvoke([system, corrected_human])
            corrected_evaluation = _parse_evaluation(
                getattr(corrected_response, "content", corrected_response)
            )
        except Exception as exc:  # noqa: BLE001
            log.error("udr.eval.correction_validation_failed", error=str(exc)[:300])
            corrected_evaluation = UdrEvaluation(
                grounded=False,
                answers_question=False,
                count_consistent=False,
                score=0.0,
                issues=["Corrected answer could not be validated"],
            )
        if corrected_evaluation.passed:
            log.warning(
                "udr.eval.corrected",
                score=corrected_evaluation.score,
                original_issues=evaluation.issues,
            )
            return corrected, corrected_evaluation

    # Graceful degradation — never dead-end the user with a contentless "retry" message.
    # Prefer a corrected answer that is at least grounded; otherwise fall back to the original
    # answer as long as it is grounded (i.e. every value traces to the evidence). Only a truly
    # ungrounded answer is withheld, and even then we explain what was missing instead of a bare
    # retry, so the assistant reads as intelligent rather than a brittle gate.
    if corrected and corrected_evaluation.grounded:
        best, best_eval = corrected, corrected_evaluation
    elif evaluation.grounded and answer.strip():
        best, best_eval = answer, evaluation
    elif not evaluation.evaluated and answer.strip():
        # The judge never returned a verdict, so there is no finding to withhold the answer
        # on — only a broken judge. Discarding a correct answer because the check around it
        # failed is the more damaging of the two errors available here, and it is the one
        # that was happening: three runs in four of a question whose answer was right.
        best, best_eval = answer, evaluation
    else:
        best, best_eval = "", evaluation

    if best.strip():
        note = ""
        if not best_eval.evaluated:
            # Say which check did not happen. "Could not be verified" reads as doubt about
            # the data; this is doubt about the verifier, and the reader should know which.
            note = (
                "\n\n_The automated accuracy check could not run on this answer, so it has "
                "not been independently verified against the retrieved records._"
            )
        elif not best_eval.count_consistent or best_eval.score < EVAL_THRESHOLD:
            note = (
                "\n\n_Automated accuracy check: this is grounded in the retrieved records but "
                "flagged for a quick human verification — please double-check any counts against "
                "the register._"
            )
        log.warning(
            "udr.eval.degraded",
            score=best_eval.score,
            grounded=best_eval.grounded,
            issues=evaluation.issues,
        )
        return best.strip() + note, best_eval

    log.warning("udr.eval.blocked", score=evaluation.score, issues=evaluation.issues)
    issue_text = "; ".join(i for i in evaluation.issues if i) or (
        "the retrieved records did not clearly support an answer"
    )
    return (
        "I found related data but couldn't fully verify an answer "
        f"({issue_text}). Try naming the table or entity — e.g. "
        "“list vendors with expired certificates” or “how many open work "
        "orders on chillers”.",
        evaluation,
    )
