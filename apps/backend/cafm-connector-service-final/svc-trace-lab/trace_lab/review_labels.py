"""Human labels for the grader, from open coding (Trace Lab Phase 4).

Grader validation (grader_validation.py) measures the golden-set grader against people, and asks registered label
sources for {turn_id: "pass" | "fail"}. This is the source that reads what reviewers say in the Conversations view: a
Right verdict on a GOLDEN answer (one that belongs to a golden_results row) is a human pass, a Wrong verdict a human
fail. One verdict counts per answer per reviewer, and it is the reviewer's latest (a verdict is a new version, so the
live head; a cleared verdict is a tombstone and is no label). Reviewers who disagree about an answer leave it out: a
label no two people agree on cannot measure anyone. A verdict on live traffic is never a label.

Both tables are in the lab database, so this is one read on db.lab_engine(); nothing is written.
"""
from __future__ import annotations

import structlog
from sqlalchemy import text

from . import db, grader_validation

log = structlog.get_logger(__name__)

CHUNK = 2000
LABEL = {"right": "pass", "wrong": "fail"}

_SQL = text("""
    SELECT a.turn_id, a.verdict
      FROM tracelab.annotations a
     WHERE a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone AND a.verdict IN ('right', 'wrong')
       AND a.turn_id = ANY(CAST(:tl_ids AS text[]))
       AND EXISTS (SELECT 1 FROM tracelab.golden_results r
                    WHERE r.turn_id = a.turn_id AND r.organization_id = a.organization_id)""")


async def golden_human_labels(turn_ids: list[str]) -> dict[str, str]:
    """{turn_id: 'pass' | 'fail'} for the golden answers among turn_ids that reviewers agree on."""
    ids = sorted({str(t) for t in turn_ids if t})
    verdicts: dict[str, set[str]] = {}
    if not ids:
        return {}
    async with db.lab_engine().connect() as conn:
        for i in range(0, len(ids), CHUNK):
            for tid, verdict in (await conn.execute(_SQL, {"tl_ids": ids[i:i + CHUNK]})).all():
                verdicts.setdefault(tid, set()).add(LABEL[verdict])
    return {tid: next(iter(labels)) for tid, labels in verdicts.items() if len(labels) == 1}


def register() -> None:
    """Hand grader validation this source. Idempotent, and it never raises: without it the grader reads
    "unvalidated", which is the honest state."""
    try:
        grader_validation.register_label_source(golden_human_labels)
    except Exception as exc:  # noqa: BLE001 - a missing label source means fewer labels, never a broken service
        log.warning("trace_lab.review_labels.register_failed", error=str(exc)[:200])
