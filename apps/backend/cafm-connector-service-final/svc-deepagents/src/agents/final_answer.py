"""The one way an evidence answer leaves the orchestrator (Workstream A1, 11 Nov 2026).

Until now only a multi-part question was answered by the orchestrator: its planner synthesised the
steps' results and checked the figures. Every other answer was whatever a sub-agent wrote - an
engine's prose, the general loop's last message, a sub-agent's text relayed through `task` -
sent to the user as it stood. In the 30 days to 11 Nov 2026 that was 94% of the turns on
hoistra_test (the planner wrote 21 of 367).

Here a sub-agent's text is a DRAFT. The answer the user reads is written by one final step from
what the tools returned, with the draft beside it as something to check, not to copy; then every
figure is traced to a tool result and the claims are checked against the rows (planner.verify,
planner.verify_claims), with up to two rewrites. A draft is never shipped because the final step
failed unless it passes the same checks.

Which answers do not come through here, and why (each is marked with its `source` on the result,
so a turn that skipped this step without a reason is logged - see `audit`):
  * planner      - the planner's synthesis, which is this step for a planned turn;
  * compliance_analyst / contract_analyst - the orchestrator's own analysts, which write from the
    tool rows and are grounded against them in code (compliance validate + review,
    contract_answer.ground). Since 12 Nov 2026 their text is a draft too and comes back through
    this step (orchestrator._answer_over_analyst); these names now mark only the turn where this
    step could not write and the analyst's own answer stood;
  * deterministic - text built in code from records (templates, status tracks, shortcuts);
  * clarify / choice - a question back to the user or option cards: no records were read;
  * orchestrator_direct - the orchestrator answered without calling a tool, so there is no
    sub-agent output to pass on and no evidence to write from.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable

import structlog

from . import answer_spec
from . import evidence as ev
from . import planner, trace

log = structlog.get_logger(__name__)

Llm = Callable[[str, str, str], Awaitable[str]]

#: The turn's presentation and plumbing: cards, panels, the plan, the loop's scratchpad. Not records.
NOT_EVIDENCE = frozenset({
    "compliance_response", "compliance_pipeline", "openui_view", "planner",
    "write_todos", "ls", "write_file", "edit_file",
    # The loop choosing where to send the question, and trimming its own context (12 Nov 2026).
    # select_skill's reply - which agent, which trigger words matched - was counted as the one piece
    # of evidence on turn-0742ad34d0b74817, so a turn that read no record was written as if it had.
    "select_skill", "compact_context",
})
#: A sub-agent handed a question by the loop: what comes back is its prose, a draft.
DRAFT_TOOLS = frozenset({"task"})

#: Sources that are allowed to carry an answer out without this step (see the module docstring).
EXEMPT = frozenset({"planner", "compliance_analyst", "contract_analyst", "deterministic", "clarify",
                    "choice", "orchestrator_direct"})

#: Characters of one tool result shown to the writer, and of all of them together.
PER_RESULT = 8000
TOTAL = 48000
#: Characters of drafts shown to the writer.
DRAFT_CHARS = 6000

#: What the user reads when the final step fails and the draft does not pass the checks.
UNWRITTEN = ("The records were read, but the answer could not be written just now. "
             "Please ask again in a moment.")

SYSTEM = "You are the final answer-writing step of the Plenum CAFM orchestrator."

FINAL_PROMPT = """You write the answer the user reads. Tools have read the records; their results are below. A
specialist also wrote a DRAFT from them. The draft is not evidence: it can be wrong, padded or invented.

Rules:
- The tool results (and the previous answer, when one is given) are your only facts. Every figure, name,
  date and code you state must appear in a tool result. Keep a statement from the draft only when a tool
  result supports it; drop the rest without comment.
- Be concise. Lead with the direct answer in one or two sentences. A lookup is a short answer: a line,
  a short list or one table. Add detail only where the question asks for it. No preamble, no restating
  the question, no filler, no offers to help further.
- A part of the question that no tool result answers: say so plainly for that part - the records hold
  nothing for it, or the lookup for it failed - and never fill it from general knowledge.
- Say a lookup failed or could not be read ONLY for a result listed under FAILED, in that result's own
  words. Never tell the user to "ask again" for any other reason. When no tool read anything for a part
  and the previous answer already settles it (it found none of the things the question is about, or it
  already gave the figure), answer from the previous answer and say that is where it comes from;
  when nothing settles it, say that part was not looked up.
- Keep the scope the tools used (company, building, period, vendor, records); do not generalise beyond it.
- Where a tool result carries `answer_rules`, follow them for that part.
- What the user can do next in Hoistra (approve, upload, choose, open a page) may be kept from the draft.
- Weigh findings as the LENS says, carrying money on a finding only where a tool result gives it.
- Markdown; a table where it helps. Never show tool names, table names, SQL or internal field names.

{states}

{forms}

LENS
{lens}

QUESTION
{question}
{scope}{shown}
DRAFT (not evidence)
{draft}

TOOL RESULTS
{results}"""

#: When the answer is drawn above cards built from the same records (12 Nov 2026). The first answer
#: written over the compliance analyst listed the four lapsed certificates as a table - and the chat
#: drew that table, a bar chart from its "Overdue" column, and then the cards' own bar chart and
#: certificate table under it: the same four rows four times (turn-548da79bee2642eb).
SHOWN_NOTE = """
SHOWN UNDER YOUR ANSWER (cards the chat draws from the same records, so the reader already sees them):
{shown}
Do not repeat what the cards show: no table and no list of those rows, and no column of their dates,
references or day counts. Write the direct answer in one or two sentences - the count, and the items by
name when there are five or fewer - then only what the cards do not show (a cause, a consequence, what
to do next). If the cards show everything, the one or two sentences are the whole answer.
"""

STRICT_NOTE = """

FIGURES STILL NOT FOUND IN ANY TOOL RESULT (remove each one, and the claim it carries; do not replace it
with another figure; say the records do not give it if the question asked for it): """


def _key(tool: str, output: Any) -> str:
    try:
        s = json.dumps(output, default=str, sort_keys=True)
    except (TypeError, ValueError):
        s = str(output)
    return tool + ":" + hashlib.sha1(s[:200000].encode("utf-8", "replace")).hexdigest()


def gather(*sources: list[dict[str, Any]] | None, question: str = "") -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The turn's evidence in the planner's result shape, and the sub-agents' drafts.

    `sources` are lists of {tool, output, error?} - an engine's inner calls, the loop's streamed
    calls, the trace's record of every tool (sub-agents included). The same call seen twice is
    kept once. Each result carries its state (agents/evidence.py): a tool that answers with an error
    does not raise, so {ok: false} or a refused call's {error, status_code} is a failure with its
    record, never evidence."""
    results: dict[str, dict[str, Any]] = {}
    drafts: list[str] = []
    seen: set[str] = set()
    for calls in sources:
        for tc in calls or []:
            if not isinstance(tc, dict):
                continue
            tool = str(tc.get("tool") or "")
            if not tool or tool in NOT_EVIDENCE or tool.startswith("phase2_engine:"):
                continue
            out = tc.get("output")
            out = getattr(out, "content", out)
            k = _key(tool, out)
            if k in seen:
                continue
            seen.add(k)
            if tool in DRAFT_TOOLS:
                if out:
                    drafts.append(out if isinstance(out, str) else json.dumps(out, default=str))
                continue
            env = ev.classify(tool, out, tc.get("error"), question=question)
            answered = env["status"] in ev.ANSWERED
            results[f"t{len(results) + 1}"] = {"tool": tool, "output": out if answered else None, "tool_calls": [],
                                                "ok": answered, "status": env["status"],
                                                "error": None if answered else env["message"],
                                                **({} if env["status"] == ev.OK else {"failure": env})}
    return results, drafts


def has_evidence(tool_calls: list[dict[str, Any]] | None) -> bool:
    """Whether a turn's calls include one that read something (not only cards, panels, plumbing)."""
    return any(isinstance(t, dict) and str(t.get("tool") or "") not in NOT_EVIDENCE
               and not str(t.get("tool") or "").startswith("phase2_engine:")
               for t in tool_calls or [])


def _results_text(results: dict[str, dict[str, Any]]) -> str:
    """SUCCEEDED / FAILED / CONFLICTS (agents/evidence.render), or that no tool returned anything."""
    if not any(k != "previous_answer" for k in results):
        prev = results.get("previous_answer")
        return ("(no lookup was run for this question - nothing failed; nothing was read)"
                + ("\n\n" + ev.render({"previous_answer": prev})[0] if prev else ""))
    return ev.render(results, per=PER_RESULT, total=TOTAL)[0]


def _prompt(question: str, draft: str, drafts: list[str], results: dict[str, dict[str, Any]], scope: str = "",
            shown: str = "") -> str:
    texts = [d for d in [draft, *drafts] if d and d.strip()]
    joined = "\n\n---\n\n".join(texts)
    if len(joined) > DRAFT_CHARS:
        joined = joined[:DRAFT_CHARS] + " …"
    return FINAL_PROMPT.format(lens=planner._lens_text(), question=question, draft=joined or "(none)",
                               results=_results_text(results), states=ev.STATE_RULES,
                               forms=answer_spec.prompt_block(answer_spec.form_for(results), answer_spec.parts_of(question)),
                               scope=(f"\nSCOPE CARRIED FROM THE CONVERSATION (a hard filter): {scope}\n" if scope else ""),
                               shown=SHOWN_NOTE.format(shown=shown) if shown else "")


def _unwritten(results: dict[str, dict[str, Any]]) -> str:
    """What the user reads when the writer failed and the draft did not pass: when every lookup also
    failed, the first failure's own record (what happened, what to do) - not "the records were read"."""
    lookups = {k: v for k, v in results.items() if k != "previous_answer"}
    if ev.outcome(lookups) == "failure":
        f = next((r.get("failure") for r in lookups.values() if r.get("failure")), None)
        if f:
            return f"{f['message']} {f['next_step']}"
    return UNWRITTEN


def _score(answer: str, question: str, results: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    """The claims the answer gets wrong against the results - the planner's two checks, plus
    (Workstream A3) a failed lookup worded as "none found", a refusal not said as one, technical
    error text, and a conflict given one-sided - and the figure check."""
    lookups = {k: v for k, v in results.items() if k != "previous_answer"}
    claims = [*planner.verify_claims(answer, question, results), *ev.state_mismatches(answer, lookups),
              *ev.conflict_mismatches(answer, ev.conflicts(lookups)),
              # Workstream A4: the form the lookups call for, and every part of the question answered.
              *answer_spec.checks(answer, answer_spec.form_for(lookups), answer_spec.parts_of(question))]
    return claims, planner.verify(answer, results)


async def write(question: str, draft: str, *, results: dict[str, dict[str, Any]], drafts: list[str] | None = None,
                llm: Llm, first: Llm | None = None, previous: str | None = None,
                scope: str = "", shown: str = "") -> tuple[str, dict[str, Any]]:
    """The answer the user reads, and a report of how it was written and checked.

    `first` writes the first version when given (the caller's streaming model call, so the chat
    paints the answer as it is written); rewrites go through `llm`. A rewrite replaces what was
    painted when the answer completes. `shown` says what cards the chat draws under the answer from
    the same records, so the text does not repeat them (SHOWN_NOTE).

    report: {source, evidence, failed, drafts, figures_checked, unmatched, contradictions, rewrites,
    draft_chars, answer_chars}. source is "synthesis", or "draft_checked" / "unwritten" when the
    writing model failed (the draft ships only when it passes the checks)."""
    drafts = list(drafts or [])
    if previous:
        results = {"previous_answer": {"tool": "", "output": previous, "tool_calls": [], "ok": True, "error": None},
                   **results}
    evidence = sum(1 for k, r in results.items() if k != "previous_answer" and r["ok"])
    failed = sum(1 for r in results.values() if not r["ok"])
    report: dict[str, Any] = {"source": "synthesis", "evidence": evidence, "failed": failed, "drafts": len(drafts),
                              "rewrites": 0, "draft_chars": len(draft or ""),
                              "outcome": ev.outcome({k: v for k, v in results.items() if k != "previous_answer"}),
                              "form": answer_spec.form_for(results), "parts": answer_spec.parts_of(question)}
    rid = trace.on_step_open("final", "final answer", {"evidence": evidence, "failed": failed, "drafts": len(drafts),
                                                       "outcome": report["outcome"]})
    prompt = _prompt(question, draft, drafts, results, scope, shown)
    try:
        answer = (await (first or llm)(SYSTEM, prompt, "final_answer")).strip()
        if not answer:
            raise ValueError("the writer returned nothing")
    except Exception as exc:  # noqa: BLE001 - decided below, never shown
        log.warning("final_answer.write_failed", error=str(exc)[:200])
        claims, check = _score(draft or "", question, results)
        ok = bool((draft or "").strip()) and not claims and check["ok"]
        answer = draft.strip() if ok else _unwritten(results)
        report.update(source="draft_checked" if ok else "unwritten", contradictions=claims,
                      figures_checked=check["figures_checked"], unmatched=check["unmatched"], answer_chars=len(answer))
        trace.on_step_close(rid, report, ok=False, error="the final writer failed: " + str(exc)[:200])
        return answer, report

    claims, check = _score(answer, question, results)
    for attempt in range(2):
        if not claims and check["ok"]:
            break
        note = ""
        if check["unmatched"]:
            note += (planner.UNTRACED_NOTE if attempt == 0 else STRICT_NOTE) + ", ".join(check["unmatched"][:8])
        if claims:
            note += planner.REWRITE_NOTE + "\n".join("- " + c for c in claims)
        try:
            second = (await llm(SYSTEM, prompt + note, "final_answer_rewrite")).strip()
        except Exception as exc:  # noqa: BLE001 - the earlier draft stands
            log.warning("final_answer.rewrite_failed", error=str(exc)[:200])
            break
        report["rewrites"] += 1
        if not second:
            continue
        c2, k2 = _score(second, question, results)
        if len(c2) <= len(claims) and len(k2["unmatched"]) <= len(check["unmatched"]):
            answer, claims, check = second, c2, k2
    report.update(contradictions=claims, figures_checked=check["figures_checked"], unmatched=check["unmatched"],
                  answer_chars=len(answer))
    if claims or not check["ok"]:
        log.warning("final_answer.not_clean", unmatched=check["unmatched"][:8], contradictions=claims[:3])
    trace.on_step_close(rid, report, ok=not claims and check["ok"],
                        error=None if (not claims and check["ok"]) else
                        "; ".join([*claims, *(["figures not traceable: " + ", ".join(check["unmatched"][:8])]
                                              if check["unmatched"] else [])])[:300])
    return answer, report


def audit(answer: str, tool_calls: list[dict[str, Any]] | None, source: str | None, *, where: str) -> bool:
    """True, and logged, when an answer that read records is leaving without the final step or a
    named reason. The guard that keeps A1 true as new paths are added."""
    if not (answer or "").strip() or not has_evidence(tool_calls):
        return False
    if source == "synthesis" or source in EXEMPT or source in ("draft_checked", "unwritten"):
        return False
    log.warning("final_answer.bypassed", where=where, source=source,
                tools=sorted({str(t.get("tool")) for t in tool_calls or [] if isinstance(t, dict)})[:12])
    rid = trace.on_step_open("final", "final answer skipped", {"where": where, "source": source})
    trace.on_step_close(rid, None, ok=False, error=f"answer left without the final step ({where})")
    return True
