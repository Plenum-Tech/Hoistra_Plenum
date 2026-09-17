"""The vendor engine's answer: written from the rows, checked against them, drawn as cards.

Compliance gathers rows, hands them to an analyst that returns ZONES rather than prose, has code
strip whatever the rows do not support, and emits the result as a `compliance_response` tool
call. That last step is the whole reason its answers render as KPI tiles, per-owner groups and
priority actions: the interface (extractComplianceAnswer, complianceLive.js) finds that call by
name and draws from it. Anything else, however well written, renders as a paragraph.

The vendor engine had none of it — a small model wrote prose and the prose was the answer, with
nothing between it and the reader. This module is the missing middle: same three steps, a vendor
schema, and one deliberate difference in how grounding works.

**Why grounding here is numeric.** Compliance checks ids: every certificate named must be a row
that was fetched. A vendor answer's claims are not ids, they are FIGURES — a score, a month's
movement, how many sit below a threshold, how much is in dispute. So the check is that every
number in the answer exists in some fetched row, within rounding. That is the failure this
exists to stop: on 16 Sep 2026 a user was told "a total of £6,353.70 in disputed invoices" where
the figure was an invoice total and the disputed amount was never established at all.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Where each read tool keeps its rows, and what to call one when it is shown as evidence.
_ROW_SOURCES: dict[str, tuple[str, str]] = {
    "list_vendor_scorecards": ("scorecards", "scorecard"),
    "list_contract_parameters": ("parameters", "contract"),
    "list_invoices": ("invoices", "invoice"),
    "list_contract_approvals": ("items", "approval"),
    "get_score_weights": ("weights", "weights"),
}

#: Figures that are part of how scoring works rather than claims about this portfolio, plus the
#: small integers an answer derives by counting what it was given ("two vendors are below 80").
#: Without this every sentence that counts its own rows reads as invented.
_SCORING_CONSTANTS = {0.0, 1.0, 3.0, 7.0, 8.0, 25.0, 60.0, 80.0, 90.0, 100.0, 500.0}
_SMALL_COUNT_MAX = 12.0

#: Numbers inside these are the answer's own structure, not claims about data.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

#: The zones the interface draws. Anything else the analyst invents is dropped: an unknown key
#: renders nowhere, and carrying it only makes the payload look richer than the screen is.
ZONES = ("narrative", "sections", "groups", "kpis", "actions", "insights", "pending")


def evidence_rows(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every row the agent actually fetched, tagged with which read produced it.

    A tool that failed contributes nothing — an error payload is not evidence, and letting one
    through is how "no scorecards exist" gets written from a 401.
    """
    rows: list[dict[str, Any]] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        key, kind = _ROW_SOURCES.get(str(call.get("tool") or ""), (None, None))
        if not key:
            continue
        out = call.get("output")
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except Exception as exc:  # noqa: BLE001
                # Worth a line: a tool result that will not parse is evidence the analyst
                # silently never sees, and the answer that follows looks the same either way.
                log.warning(
                    "contract.evidence.unparsable",
                    tool=str(call.get("tool") or ""), error=str(exc)[:120],
                )
                continue
        if not isinstance(out, dict) or out.get("error"):
            continue
        found = out.get(key)
        if isinstance(found, dict):
            found = [found]
        for r in found or []:
            if isinstance(r, dict):
                rows.append({**r, "_kind": kind})
    return rows


def _row_numbers(rows: list[dict[str, Any]]) -> set[float]:
    """Every number anywhere in the fetched rows, including inside nested structures."""
    seen: set[float] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            seen.add(round(float(v), 2))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for m in _NUMBER.findall(v):
                try:
                    seen.add(round(float(m.replace(",", "")), 2))
                except ValueError:
                    continue

    walk(rows)
    return seen


def _is_grounded(value: float, have: set[float]) -> bool:
    """Whether a figure in the answer traces to one in the rows.

    Compared as numbers with a rounding tolerance, never as strings: an analyst writing 72.4
    from a row holding 72.43 is rounding, not inventing, and treating it as a hallucination
    costs a revision pass every time.
    """
    if abs(value) in _SCORING_CONSTANTS or (
        float(value).is_integer() and abs(value) <= _SMALL_COUNT_MAX
    ):
        return True
    # Magnitudes, not signed values: a row holding trend_delta -3.2 is written as "down 3.2",
    # which is correct English and was being reported as an invented figure. The sign lives in
    # the sentence; a check that insists on it drops entries for being well written.
    want = abs(value)
    for h in have:
        h = abs(h)
        if abs(h - want) <= max(0.05, want * 0.005):
            return True
        # The answer may round to whole pounds or to one decimal.
        if round(h) == round(want) or round(h, 1) == round(want, 1):
            return True
    # Arithmetic the rows support. £1,387.13 is £3,031.59 claimed minus £1,644.46 agreed —
    # both on the row, and the gap between them is the figure the property manager has to
    # decide about. It was being stripped as "in no row", which forbids the analyst from
    # subtracting: most of what reasoning about money IS. Only pairs, and only + and -, so a
    # figure that is no combination of the evidence is still caught.
    values = [abs(h) for h in have]
    for i, a in enumerate(values):
        for b in values[i + 1:]:
            for combined in (a + b, abs(a - b)):
                if combined and abs(combined - want) <= max(0.05, want * 0.005):
                    return True
    return False


def _numbers_in(text: Any) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.findall(str(text or "")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def ungrounded_numbers(payload: dict[str, Any] | None, rows: list[dict[str, Any]]) -> list[str]:
    """Figures in the answer that appear in no fetched row."""
    if not payload:
        return []
    have = _row_numbers(rows)
    bad: list[str] = []
    for zone in ZONES:
        for chunk in _texts(payload.get(zone)):
            for n in _numbers_in(chunk):
                if not _is_grounded(n, have):
                    bad.append(f"{n:g} (in {zone})")
    return bad


def _texts(value: Any) -> list[str]:
    """Every readable string in a zone, whatever shape the analyst used."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if isinstance(v, (str, int, float))]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out.extend(_texts(v))
        return out
    return [str(value)]


def ground(
    payload: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Strip what the rows do not support, and say what was stripped.

    Only whole entries are dropped — a KPI whose figure is unsupported goes, rather than the
    answer being rewritten around it. The narrative is left alone and reported instead: an
    analyst that invented a number in its summary has made a mistake worth seeing in the log,
    and silently editing prose hides it.
    """
    have = _row_numbers(rows)
    out = dict(payload)
    issues: list[str] = []
    for zone in ("kpis", "actions", "insights", "pending", "groups"):
        entries = out.get(zone)
        if not isinstance(entries, list):
            continue
        kept = []
        for e in entries:
            bad = [n for n in _numbers_in(" ".join(_texts(e))) if not _is_grounded(n, have)]
            if bad:
                label = (e.get("label") or e.get("title") or e.get("headline") or "")[:60] \
                    if isinstance(e, dict) else str(e)[:60]
                issues.append(f"dropped {zone} entry {label!r}: {', '.join(f'{n:g}' for n in bad)} in no row")
                continue
            kept.append(e)
        out[zone] = kept
    for n in ungrounded_numbers({"narrative": out.get("narrative")}, rows):
        issues.append(f"narrative cites {n} which is in no row")
    return out, issues


def as_tool_calls(
    payload: dict[str, Any] | None,
    *,
    question: str,
    steps: list[dict[str, Any]],
    cost: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The two synthetic calls the interface draws cards and the run panel from.

    The names are load-bearing: extractComplianceAnswer matches `compliance_response` and
    `compliance_pipeline` literally. They are not compliance-specific payloads — they are the
    interface's contract for "a structured answer" — so the vendor engine emits the same pair
    rather than a third name nothing renders.

    No zones means no calls: the prose answer then stands on its own, which is a worse answer
    but an honest one. Half a card set is worse than none.
    """
    if not payload:
        return []
    body = {z: payload.get(z) for z in ZONES if payload.get(z) is not None}
    if not body.get("narrative"):
        return []
    return [
        {"tool": "compliance_response", "input": {"question": (question or "")[:300]},
         "output": body},
        {"tool": "compliance_pipeline", "input": {"question": (question or "")[:300]},
         "output": {"steps": steps, "cost": cost}},
    ]


def _first_json_object(text: str) -> dict[str, Any] | None:
    """The first complete JSON object in a reply, however it was wrapped.

    Measured on 16 Sep 2026: one analyst pass in three came back as
    "Here is the analysis: {...}" or inside a fence, failed json.loads with "Expecting value:
    line 1 column 1", and the turn fell back to prose with nothing on screen to say why. The
    object was there every time. Braces are counted rather than regex-matched so a nested one
    does not truncate the outer, and strings are tracked so a brace inside a narrative does not
    close it early.
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    found = json.loads(text[start : i + 1])
                except Exception:  # noqa: BLE001 — keep scanning for a later object
                    start = -1
                    continue
                return found if isinstance(found, dict) else None
    return None


_ANALYST_SYSTEM = """You are a contract-performance analyst writing for a property manager who \
pays these vendors. You are given the rows the platform fetched, and your job is to REASON about \
them, not to reformat them.

Return ONLY a JSON object with these keys. THE FIELD NAMES ARE EXACT — the interface reads
these and nothing else, so a card whose keys are renamed draws empty with no error anywhere:

  narrative  - the closing summary: three or four sentences, the overall picture and what to do
               first. No bullet lists, no field names, no restating the question.
  sections   - one per part of the question: {"id", "title", "narrative"}.
  kpis       - the few figures that matter:
               {"label", "count", "unit", "sublabel", "severity"}
               `count` is the FIGURE (a number, or a string like "£6,353.70"); `unit` is what
               it counts ("invoices", "vendors", "hours"); `sublabel` is the one line of
               context; `severity` is "critical" | "warning" | "info" | "ok".
  groups     - one per VENDOR or per thing asked about, worst first:
               {"owner", "headline", "points": [], "severity"}
               `owner` is the company or invoice reference — this is the card's title.
               `headline` is where it stands in one line. `points` is the body: an ARRAY OF
               STRINGS, one fact each, naming the measure and the figure that proves it.
               This is where the detail goes.
  actions    - what to do: {"title", "scope", "severity", "tags": []}. `title` is the whole
               instruction. The evidence is in the groups; do not restate it.
  insights   - interactions the data supports: {"type", "text"} where type is "correlation" |
               "anomaly" | "risk". Never invent a correlation.
  pending    - decisions the property manager owes:
               {"name", "what_is_pending", "text"}.

RULES THAT DECIDE WHETHER THE ANSWER IS TRUE
- Use ONLY figures present in the rows. Every number is checked against them afterwards and
  anything ungrounded is stripped, so inventing one loses the entry that carried it.
- Carry the period, the provenance and the staleness with every figure: which month, whether a
  score was CAPPED or earned, whether a contract value is real or came from defaults
  (defaults_used / PRESENTATION_RULE on the row), whether a flagged delta has been agreed by
  the Adversary.
- A capped score is a ceiling, not a measurement. Never average capped and earned scores
  together without saying how many were capped.
- Say which measure you ranked on when you rank.
- A SUPERLATIVE IS A CLAIM ABOUT EVERY ROW. Before you write best, worst, highest, lowest,
  most or least, compare every row you were given and name the winner by its value — not the
  most recent row, not the one that caught the eye. "Best month: December 2023, 85.09" was
  written with July 2023 at 85.11 in the same evidence, and the table printed underneath the
  answer showed both. Losing a superlative by 0.02 to a row two lines down is the one error
  that reading all of them would always have caught.
- ANSWER IN CARDS, NOT IN PARAGRAPHS. The narrative is the summary of the answer, never the
  whole of it. Every figure you would put in a sentence belongs in a kpi as well: a total, a
  claim, an agreed amount, a gap, a count, a score, a month's delta. Four money figures in one
  paragraph is four cards a reader takes in at a glance, and it is the difference between an
  answer that is read and one that is skimmed.
- Each company, invoice or contract you say anything about earns a group, with `points`
  carrying the facts one per line — the reference, the figures, the counts, the status, what
  is unresolved. Anything you would have written as a run of clauses is points.
- A finding a manager must act on is an action; a decision they owe is a pending entry.
- The test for a zone is whether the rows support something real to put in it, NOT whether the
  question was portfolio-wide. Leave a zone out when the rows give it nothing — never because
  the question looked small. A question about one vendor is still answered in cards; it simply
  has one group instead of five.
- WHEN THE QUESTION ASKS FOR DETAIL — it says "with details", "break it down", "explain",
  "why", or names a thing and asks about it — the detail IS the answer. Give the group for
  that vendor or invoice with a bullet per fact (the reference, the amount, the counts, the
  status, what is unresolved), the KPIs that quantify it, and the action it implies. A single
  paragraph in reply to "with details" has not answered the question. The rule above about
  not padding a one-fact answer does not apply when detail was what was asked for.
- SAY WHAT IS NOT KNOWABLE. Per-line invoice detail and per-work-order scores have no read
  route: if the question needs them, say so plainly and give what the headers do support,
  rather than implying the figure you have is the figure that was asked for.
- A field on a row named answer_hint, or ending in _RULE or _NOTE, is an INSTRUCTION from the
  platform, not a value to render. It is there because the rows alone would mislead. Obey it,
  never print it, and never let it appear as a bullet or a KPI. The ones written today:
    answer_hint         nothing matched the name that was typed — do not attribute to one
    STATUS_FILTER_NOTE  the status asked for is not one this system records
    PRESENTATION_RULE   these contract figures are system defaults, not contract terms
    MULTI_VENDOR_NOTE   nobody was named and these contracts belong to different companies
    AMOUNT_RULE         the invoice total is NOT the amount in dispute — quote the deltas
    FRESHNESS_RULE      this is the newest scorecard and it is months or years out of date"""


async def write_answer(
    *,
    question: str,
    rows: list[dict[str, Any]],
    prose: str,
    api_key: str,
    model: str = "claude-sonnet-5",
    budget_chars: int = 90_000,
    client: Any = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The analyst pass. Returns (zones, meta) — zones is None when it could not answer.

    ``prose`` is the sub-agent's own reply, given as context rather than as truth: it knows what
    it was asked to look at, and the rows are what it is allowed to say.

    Anthropic is called directly, the way compliance calls its own analyst. llm_factory's
    create_chat_model builds an OpenAI/Azure client from settings.openai_api_key whatever model
    name it is handed — pointing it at claude-sonnet-5 sends that name to OpenAI, fails, and
    falls back to prose with nothing on screen to say why. ``client`` is the seam for tests.
    """
    meta: dict[str, Any] = {"model": model}
    if not (api_key or "").strip() and client is None:
        return None, {**meta, "skipped": "no anthropic key"}
    if not rows:
        return None, {**meta, "skipped": "no rows fetched"}
    digest = json.dumps(rows, default=str)
    truncated = len(digest) > budget_chars
    if truncated:
        # Named, never silent. An answer that says a vendor "is not present in the evidence"
        # when the row existed and was merely cut is the worst possible failure here.
        digest = json.dumps(rows[: max(1, len(rows) // 2)], default=str)[:budget_chars]
        meta["evidence_truncated"] = True
    human = (
        f"QUESTION:\n{(question or '').strip()[:1000]}\n\n"
        + ("NOTE: the evidence below was TRUNCATED to fit. Say so if the answer depends on rows "
           "you cannot see; never report absence from a truncated set.\n\n" if truncated else "")
        + f"WHAT THE AGENT REPORTED (context, not evidence):\n{(prose or '')[:2000]}\n\n"
        f"ROWS ({len(rows)}):\n{digest}"
    )
    t0 = time.perf_counter()
    try:
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=model,
            max_tokens=8000,
            system=_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": human}],
        )
        blocks = getattr(resp, "content", None) or []
        text = "".join(
            getattr(b, "text", "") if not isinstance(b, dict) else b.get("text", "")
            for b in blocks
        ).strip()
        payload = _first_json_object(text)
        if payload is None:
            raise ValueError("no JSON object in the reply")
    except Exception as exc:  # noqa: BLE001 — a failed analyst keeps the prose answer
        log.warning("contract.analyst.failed", error=str(exc)[:200])
        return None, {**meta, "error": str(exc)[:200]}
    meta["ms"] = round((time.perf_counter() - t0) * 1000)
    if not isinstance(payload, dict) or not str(payload.get("narrative") or "").strip():
        return None, {**meta, "skipped": "analyst returned no narrative"}
    return payload, meta


async def compose(
    *,
    question: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    api_key: str,
    model: str = "claude-sonnet-5",
    session_id: str = "",
    cost: dict[str, Any] | None = None,
    client: Any = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Turn a vendor turn's rows into cards, whichever route reached the tools.

    Two doors lead to the contract tools: the phase-2 engine, and the general agent handing the
    question to the sub-agent through `task`. Only the first composed an answer, so a question
    the keyword table missed came back as a paragraph while the same question phrased with the
    word "scorecard" in it came back as cards. Which door a question came through is not a
    property of the answer.

    Every failure here is soft on purpose: no rows, a refused model, unparsable JSON — the prose
    answer stands. Returns the answer, the tool calls to emit, and what happened.
    """
    # Compliance builds its own compliance_response through its own pipeline, and the renderer
    # draws the FIRST one it finds. A second would either be ignored or win over the real one.
    if any(str(tc.get("tool")) == "compliance_response" for tc in tool_calls if isinstance(tc, dict)):
        return answer, tool_calls, {"skipped": "compliance owns this turn"}
    try:
        rows = evidence_rows(tool_calls)
        if not rows:
            return answer, tool_calls, {"skipped": "no rows fetched"}
        zones, meta = await write_answer(
            question=question, rows=rows, prose=str(answer or ""),
            api_key=api_key, model=model, client=client,
        )
        issues: list[str] = []
        if zones:
            zones, issues = ground(zones, rows)
            if str(zones.get("narrative") or "").strip():
                answer = zones["narrative"]
        # Which zones were filled, not merely whether any were. "zones: true" with an empty card
        # on screen is indistinguishable from a good answer in this log, and that is the one
        # question worth asking when a reader says the card is bare.
        filled = {z: (len(v) if isinstance(v, (list, str)) else 1)
                  for z, v in (zones or {}).items() if v}
        log.info("contract.answer.composed", session_id=session_id, rows=len(rows),
                 zones=bool(zones), filled=filled, stripped=len(issues), issues=issues[:4], **meta)
        steps = [
            {"stage": "gather", "label": "Read the vendor register",
             "detail": f"{len(rows)} row(s) from {len(tool_calls)} tool call(s)"},
            {"stage": "analyse", "label": "Contract analyst reasoning",
             "detail": "rewritten from the rows", "ms": meta.get("ms")},
            {"stage": "ground", "label": "Checked the answer against the rows",
             "detail": (f"{len(issues)} unsupported entry(ies) removed" if issues
                        else "nothing ungrounded")},
        ]
        emitted = as_tool_calls(zones, question=question, steps=steps, cost=cost)
        # The engine may already have emitted a run panel for this turn — routing, agent, the
        # tools it called and what they returned. The renderer draws the FIRST panel it finds,
        # so a second one would hide either that or this. Extend it instead: the vendor
        # analyst's three steps are the second half of the same run, not a different run.
        existing = next(
            (tc for tc in tool_calls
             if isinstance(tc, dict) and str(tc.get("tool")) == "compliance_pipeline"
             and isinstance(tc.get("output"), dict)),
            None,
        )
        if existing is not None:
            out = existing["output"]
            out["steps"] = [*(out.get("steps") or []), *steps]
            if cost is not None:
                out["cost"] = cost
            emitted = [tc for tc in emitted if tc.get("tool") != "compliance_pipeline"]
        return answer, [*tool_calls, *emitted], meta
    except Exception as exc:  # noqa: BLE001 — never lose the turn over presentation
        log.warning("contract.answer.failed", session_id=session_id, error=str(exc)[:200])
        return answer, tool_calls, {"error": str(exc)[:200]}
