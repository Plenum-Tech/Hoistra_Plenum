"""Trace Lab provenance: where a chat turn came from, inferred at READ time only.

Golden Rule G0 (addendum §2.6): Trace Lab captures nothing in the chat path and writes nothing
anywhere. A turn's channel and origin are inferred from what svc-deepagents already stores: the
turn's session id, question, route and answer (plenum_cafm.agent_trace_turns) and, in detail
views, the payload of the turn's earliest orchestrator/turn/input activity row
(plenum_cafm.agent_activity_log). Every result is labelled source "inferred" and carries
CLASSIFIER_VERSION. The old write-time design (turn_meta, request capture, trace hooks) is
superseded and must not come back here.

INFERRED_CHANNEL_SQL (a CASE over the alias `tt`) and classify() apply the same rules, in the
same order; a scratch-database test pins their parity. Only classify() can read the activity
payload, which refines what the SQL cannot see (a re-run, a WebSocket support turn).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from src.agents.agent_router import SUPPORT_SESSION_MARK

CLASSIFIER_VERSION = "inferred-1"
SOURCE = "inferred"
CHANNELS = ("chat", "ui_action", "support", "upload", "correction", "report", "workflow", "golden")

RERUN_PREFIX = "Re-run with corrections ("                                   # svc-deepagents rerun.py
REANSWER_PREFIX = "Re-answer the previous question with this correction: "   # frontend corrections.js
UPLOAD_PROMPTS = ("Validate and ingest this document.", "File this document on the building.")
FILES_NOTE = "uploaded files were already processed by the single-door pipeline"   # api/routes/workflow.py
MIGRATE_PREFIX = "Migrate "                                                  # frontend migration.js
INGEST_PATTERN = r"^Ingest ([0-9]+ documents?|this certificate|these [0-9]+ certificates) for "

#: App-written questions that are UI actions: (origin, pattern). One pattern text serves both
#: sides: Python compiles it with re.S (so "." spans lines, as in Postgres) and the SQL uses it
#: with "\Z" written as "$" (Postgres "$" matches only at the very end, as Python "\Z" does).
UI_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("ui.matrix_cell:risk", r"^Why is the .+ at .+ lapsed or blocked, and what should I do next\?\Z"),
    ("ui.matrix_cell:warn", r"^The .+ at .+ is expiring soon — what is the situation, and what should I do next\?\Z"),
    ("ui.matrix_cell:gap", r"^Why is there no .+ on record for .+, and what should I do next\?\Z"),
    ("ui.matrix_cell:ok", r"^What is the status of the .+ at .+, and who services it\?\Z"),
    ("ui.hoist_graph", r"^Map each regulated area of my buildings to its compliance obligations:"),
    ("ui.energy_investigate:anomaly", r"^Why is .+ (showing a .+|where it is), and what should I do about it\?\Z"),
    ("ui.energy_investigate:eui",
     r"^Why is .+ at [0-9]+ kWh/m² against a reference of [0-9]+, and how much of that gap can I act on\?\Z"),
    ("ui.energy_investigate:unknown",
     r"^What do we know about .+'s energy performance, and what is missing to assess it properly\?\Z"),
)

_INGEST_RX = re.compile(INGEST_PATTERN, re.S)
_UI_RX = tuple((origin, re.compile(pattern, re.S)) for origin, pattern in UI_TEMPLATES)


class Inferred(NamedTuple):
    """(channel, origin, source). A plain 3-tuple to callers that unpack it."""

    channel: str
    origin: str | None
    source: str = SOURCE


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def classify(question: Any, session_id: Any, route: Any, answer: Any,
             activity_payload: Mapping[str, Any] | None = None) -> Inferred:
    """The read-time channel of one turn. `activity_payload` is the payload of the turn's earliest
    orchestrator/turn/input activity row, when the caller has it (detail views); list views pass
    nothing, exactly like INFERRED_CHANNEL_SQL."""
    q, sid, r, a = _text(question), _text(session_id), _text(route), _text(answer)
    pl = activity_payload if isinstance(activity_payload, Mapping) else {}
    if sid.startswith("golden-"):
        return Inferred("golden", None)
    if sid.startswith("report-"):
        return Inferred("report", None)
    if q.startswith(RERUN_PREFIX):
        return Inferred("correction", "correction.rerun")
    if q.startswith(REANSWER_PREFIX):
        return Inferred("correction", "correction.reanswer")
    if sid.startswith("rerun"):
        return Inferred("correction", "correction.rerun")
    if sid.startswith("docs-") or sid.startswith("ing-"):
        return Inferred("upload", None)
    if q in UPLOAD_PROMPTS:
        return Inferred("upload", "upload.files")
    if FILES_NOTE in q:
        return Inferred("upload", None)
    if _INGEST_RX.match(q):
        return Inferred("upload", "upload.ingest_panel")
    if q.startswith(MIGRATE_PREFIX):
        return Inferred("upload", "upload.migrate")
    if pl.get("mode") == "rerun":                                    # activity payload only
        return Inferred("correction", "correction.rerun")
    if q.lstrip(" ").startswith(SUPPORT_SESSION_MARK):
        return Inferred("support", None)
    context = pl.get("extra_context") or pl.get("context")           # activity payload only
    if isinstance(context, str) and context.lstrip(" ").startswith(SUPPORT_SESSION_MARK):
        return Inferred("support", None)
    for origin, rx in _UI_RX:
        if rx.match(q):
            return Inferred("ui_action", origin)
    if q.strip(" ") == "" or a == "gate_interrupt" or r == "single:clarify":
        return Inferred("workflow", None)
    return Inferred("chat", None)


# ── the same rules as SQL, over agent_trace_turns tt ──────────────────────────────────────────

def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _starts(column: str, prefix: str) -> str:
    return f"strpos({column}, {_lit(prefix)}) = 1"


def _sql_pattern(pattern: str) -> str:
    return _lit(pattern.replace("\\Z", "$"))


_Q = "coalesce(tt.question, '')"
_S = "coalesce(tt.session_id, '')"
_A = "coalesce(tt.answer, '')"
_R = "coalesce(tt.route, '')"

INFERRED_CHANNEL_SQL = (
    "CASE"
    f" WHEN {_starts(_S, 'golden-')} THEN 'golden'"
    f" WHEN {_starts(_S, 'report-')} THEN 'report'"
    f" WHEN {_starts(_Q, RERUN_PREFIX)} OR {_starts(_Q, REANSWER_PREFIX)} OR {_starts(_S, 'rerun')}"
    " THEN 'correction'"
    f" WHEN {_starts(_S, 'docs-')} OR {_starts(_S, 'ing-')}"
    f" OR {_Q} IN ({', '.join(_lit(p) for p in UPLOAD_PROMPTS)})"
    f" OR strpos({_Q}, {_lit(FILES_NOTE)}) > 0"
    f" OR {_Q} ~ {_sql_pattern(INGEST_PATTERN)}"
    f" OR {_starts(_Q, MIGRATE_PREFIX)} THEN 'upload'"
    f" WHEN {_starts('ltrim(' + _Q + ')', SUPPORT_SESSION_MARK)} THEN 'support'"
    " WHEN " + " OR ".join(f"{_Q} ~ {_sql_pattern(p)}" for _, p in UI_TEMPLATES) + " THEN 'ui_action'"
    f" WHEN btrim({_Q}) = '' OR {_A} = 'gate_interrupt' OR {_R} = 'single:clarify' THEN 'workflow'"
    " ELSE 'chat' END"
)
