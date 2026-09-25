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
#: Measured 17 Sep 2026 at 14:12: at 24,000 total and 6,000 per call, the 47-row parts result
#: and the part→asset join were clipped, the judge wrote "cannot be confirmed from the supplied
#: visible rows", and its correction replaced two tables with three examples. The judge must
#: see what the answer was built from; the model reads this much without difficulty.
_MAX_EVIDENCE_CHARS = 80_000
#: One call's output may take at most this much of the evidence. Measured 17 Sep 2026: a
#: get_schema dump (221 tables, 2,580 columns) filled the whole 24,000-character budget on
#: its own, the rows that followed it were cut off, and the judge wrote "the evidence
#: contains only schema metadata" — then either failed a correct answer or, when its own
#: output broke, let an invented one through. Clipping per call keeps every source in view.
_MAX_CALL_CHARS = 20_000
#: Tools whose output describes the database rather than reads it. They gate the check
#: (a turn that only looked at the schema is still a UDR turn) but are not evidence: no
#: factual claim about a part, an asset or a count can be grounded in a column list.
_METADATA_TOOLS = frozenset({"get_schema", "udr_list_tables", "udr_describe_table",
                             "find_tables", "table_card"})


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


#: A code the way this estate writes them: AHU-01, PRT-CONT-2P, AST-GEN-501, B-SITE-AUH-001.
#: Upper-case first segment, at least one hyphenated segment. Dates start with a digit and
#: UUIDs are lower-case hex, so neither matches.
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b")
_IDENTIFIER_ALLOW = frozenset({"EL-UDR"})


def ungrounded_identifiers(answer: str, tool_calls: list[dict[str, Any]] | None,
                           user_message: str = "") -> list[str]:
    """The codes an answer names that appear in no retrieved record — a check that needs no model.

    The model judge decides whether a relationship or a count is supported; this decides the
    narrower, mechanical question of whether a code the answer prints was ever *seen*. An
    answer that names an asset, part or building code that no tool returned has invented it,
    and no judge output (or judge failure) should let that through. Codes the user typed are
    exempt: "AST-AHU-601 was not found" names one legitimately.
    """
    if not answer:
        return []
    haystack = json.dumps(tool_calls or [], ensure_ascii=False, default=str)
    asked = set(_IDENTIFIER.findall(user_message or ""))
    seen: set[str] = set()
    missing: list[str] = []
    for tok in _IDENTIFIER.findall(answer):
        if tok in seen or tok in asked or tok in _IDENTIFIER_ALLOW:
            continue
        seen.add(tok)
        if tok not in haystack:
            missing.append(tok)
    return missing


def _with_invented(evaluation: "UdrEvaluation", missing: list[str]) -> "UdrEvaluation":
    """The judge's verdict, overruled on grounding by the codes it could not have known were invented."""
    if not missing:
        return evaluation
    shown = ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")
    return evaluation.model_copy(update={
        "grounded": False,
        "issues": [*evaluation.issues,
                   f"names {len(missing)} identifier(s) that appear in no retrieved record: {shown}"],
    })


def _json_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(b, dict) for b in value):
        # Content blocks from a chat model that returns them: the judge's text is the text
        # parts joined, not a JSON array of blocks (which can never validate as a verdict).
        texts = [str(b.get("text") or "") for b in value if b.get("type") in (None, "text")]
        if any(texts):
            return "\n".join(t for t in texts if t)
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalise_score(payload: dict[str, Any]) -> dict[str, Any]:
    """Read a score the judge wrote on a 0-10 or 0-100 scale as the 0-1 it was asked for.

    Measured 17 Sep 2026 at 13:19: the judge returned `"score": 2`, pydantic refused it, the
    verdict was thrown away and an answer with an invented column reached the user with a
    footnote saying the check "could not run". A judge that answered on the wrong scale did
    answer; the scale is recoverable, the verdict is not once it is discarded.
    """
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return payload
    if 1 < score <= 10:
        return {**payload, "score": score / 10}
    if 10 < score <= 100:
        return {**payload, "score": score / 100}
    return payload


def _parse_evaluation(content: Any) -> UdrEvaluation:
    text = _json_content(content).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("evaluator verdict is not a JSON object")
    return UdrEvaluation.model_validate(_normalise_score(payload))


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
        _clipped(call)
        for call in tool_calls
        if str(call.get("tool") or "") not in _NOT_EVIDENCE
    ]
    return json.dumps(relevant, ensure_ascii=False, default=str)[:_MAX_EVIDENCE_CHARS]


#: What the judge must not ground an answer in: the agent's own plan and routing, the schema
#: and catalogue (descriptions of the database, not reads of it), and the run panel. Everything
#: else a tool returned is evidence — including a page engine's tools called directly by the
#: general loop on a fanned-out turn. Measured 17 Sep 2026 at 13:39: the orchestrator called
#: get_asset_condition_summary and list_asset_conditions itself, the filter kept only UDR tools
#: and task(), the judge saw "schema definitions only" and replaced a correct two-part answer
#: with a refusal.
_NOT_EVIDENCE = frozenset({
    *_METADATA_TOOLS, "select_skill", "write_todos", "read_todos", "compliance_pipeline",
})


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _table_rows(text: str) -> int:
    """Markdown table rows in an answer, header rules excluded — the rows a reader counts."""
    return sum(1 for line in (text or "").splitlines()
               if _TABLE_ROW.match(line) and not _TABLE_RULE.match(line))


def _clipped(call: dict[str, Any]) -> dict[str, Any]:
    """One call's record with its output held to _MAX_CALL_CHARS, so it cannot crowd out the rest."""
    out = call.get("output")
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
    if len(text) <= _MAX_CALL_CHARS:
        return call
    return {**call, "output": text[:_MAX_CALL_CHARS] + f"… [clipped {len(text) - _MAX_CALL_CHARS} chars]"}


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
            "every factual value is grounded, and that counts equal the evidence. A relationship "
            "the answer states between two records — a part linked to an asset, a vendor on a "
            "contract, a user granted a building — is grounded only if a join result in the "
            "evidence shows that pair; a link inferred from names, types or descriptions is "
            "ungrounded. A headline count must equal the number of matching rows in the evidence "
            "and in the answer's own table. Return JSON only with keys grounded, answers_question, "
            "count_consistent, score, issues, corrected_answer. score is a decimal from 0.0 to "
            "1.0. If the candidate is wrong, corrected_answer is the candidate with only its "
            "unsupported values fixed or removed and any rows the evidence shows were omitted "
            "added: keep every supported markdown table and row verbatim, never replace a table "
            "with examples or prose, and keep the candidate's headings. Where the evidence is "
            "marked [clipped], values beyond the clip are unverifiable, not wrong: keep them and "
            "set count_consistent to false. Never add external knowledge."
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

    missing = ungrounded_identifiers(answer, tool_calls, user_message)
    if missing:
        log.warning("udr.eval.ungrounded_identifiers", missing=missing[:8], count=len(missing))
        evaluation = _with_invented(evaluation, missing)

    if evaluation.passed:
        log.info("udr.eval.passed", score=evaluation.score)
        return answer, evaluation

    corrected = (evaluation.corrected_answer or "").strip()
    shrank = False  # set when the judge's correction has fewer table rows than the answer
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
        corrected_evaluation = _with_invented(
            corrected_evaluation, ungrounded_identifiers(corrected, tool_calls, user_message))
        if corrected_evaluation.passed and _table_rows(corrected) < _table_rows(answer):
            # A correction may not shrink a table. Measured 17 Sep 2026 at 14:12: the judge
            # could not verify every row of two tables, so its "correction" kept three rows as
            # examples and turned the rest into prose. The user had asked for the list. A
            # correction that drops rows is a different, smaller answer, not a corrected one —
            # the original goes out with the human-verification note instead.
            log.warning("udr.eval.correction_shrank_table",
                        original_rows=_table_rows(answer), corrected_rows=_table_rows(corrected))
            shrank = True
            corrected_evaluation = corrected_evaluation.model_copy(update={
                "score": min(corrected_evaluation.score, EVAL_THRESHOLD - 0.01),
                "issues": [*corrected_evaluation.issues, "correction dropped table rows"],
            })
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
    if corrected and corrected_evaluation.grounded and not shrank:
        best, best_eval = corrected, corrected_evaluation
    elif answer.strip() and not missing and (evaluation.grounded or shrank or evaluation.answers_question):
        # Grounded but imperfect; or the judge's only remedy was a smaller table; or the judge
        # found the answer answers the question but could not reconcile two sources (measured
        # 17 Sep 2026 at 15:40: the Assets page engine said 17, a stray COUNT said 6, and a
        # correct answer was withheld). Every code in it was returned by a tool (no `missing`),
        # so it goes out as written and the note below names what to double-check. Withholding
        # is for answers that name invented codes or that do not answer the question at all.
        best, best_eval = answer, evaluation
    elif not evaluation.evaluated and answer.strip() and not missing:
        # The judge never returned a verdict, so there is no finding to withhold the answer
        # on — only a broken judge. Discarding a correct answer because the check around it
        # failed is the more damaging of the two errors available here, and it is the one
        # that was happening: three runs in four of a question whose answer was right.
        # Unless the answer names codes no tool returned: that finding needs no judge.
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
        elif not best_eval.passed:
            first = next((i for i in best_eval.issues if i), "").strip()
            first = (first[:220] + "…") if len(first) > 220 else first
            note = (
                "\n\n_Automated accuracy check: flagged for a quick human verification"
                + (f" — {first}" if first else " — please double-check any counts against the register.")
                + "_"
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
