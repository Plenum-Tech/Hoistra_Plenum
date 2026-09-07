"""Feature 4 — Activity Log AL.5: refinements & follow-ups (PURE).

At the end of a completed outcome, the system may attach up to 3 **refinements** (useful
related actions the query didn't ask for) and up to 2 **follow-ups** (approval-required /
escalation queries, distinct from the Section-2 *inline* actions). Both are dismissible in
one click and don't reappear in the same session (enforced client-side, AL.5 AC3).

These functions are pure (no DB/LLM) so the <=3 / <=2 caps (AL.5 AC2) and the shape are
unit-testable. ``udr_suggestions`` derives meaningful suggestions from a UDR run result.
"""

from __future__ import annotations

MAX_REFINEMENTS = 3   # AL.5 AC2
MAX_FOLLOW_UPS = 2     # AL.5 AC2


def _norm(s, kind: str, i: int) -> dict:
    if isinstance(s, dict):
        label = str(s.get("label", ""))
        return {
            "id": str(s.get("id", f"{kind}-{i}")),
            "kind": kind,
            "label": label,
            "prompt": str(s.get("prompt") or label),
            "requires_approval": bool(s.get("requires_approval", kind == "follow_up")),
        }
    return {
        "id": f"{kind}-{i}",
        "kind": kind,
        "label": str(s),
        "prompt": str(s),
        "requires_approval": kind == "follow_up",
    }


def build_suggestions(refinements=None, follow_ups=None) -> dict:
    """Normalize + cap suggestions to the AL.5 limits (<=3 refinements, <=2 follow-ups)."""
    refs = [_norm(s, "refinement", i) for i, s in enumerate((refinements or [])[:MAX_REFINEMENTS])]
    fus = [_norm(s, "follow_up", i) for i, s in enumerate((follow_ups or [])[:MAX_FOLLOW_UPS])]
    return {"refinements": refs, "follow_ups": fus}


def udr_suggestions(
    *,
    table_count: int = 0,
    relationship_count: int = 0,
    test1_flags: int = 0,
    test2_flags: int = 0,
    blocked: bool = False,
    sample_table: str | None = None,
) -> dict:
    """Derive AL.5 refinements + follow-ups from a UDR run (AL.6 outcome). Not every run
    yields suggestions (AL.5 AC4 — not generated for every query)."""
    refinements: list[dict] = []
    if relationship_count and sample_table:
        refinements.append(
            {
                "label": f"Explore the work cloud for {sample_table}",
                "prompt": f"Show the related entities and relationships for {sample_table}",
            }
        )
    if test2_flags:
        plural = "s" if test2_flags != 1 else ""
        refinements.append(
            {
                "label": f"Inspect the {test2_flags} unexplained column overlap{plural}",
                "prompt": "List the Test 2 flagged column similarities and propose foreign keys",
            }
        )
    if table_count:
        refinements.append(
            {
                "label": "Summarise the UDR table & column metadata",
                "prompt": "Summarise the UDR run's table and column metadata",
            }
        )

    follow_ups: list[dict] = []
    if blocked:
        follow_ups.append(
            {
                "label": "Escalate the blocked UDR to a data steward",
                "prompt": "Escalate this UDR run for human review",
                "requires_approval": True,
            }
        )
    if test1_flags:
        plural = "s" if test1_flags != 1 else ""
        follow_ups.append(
            {
                "label": f"Approve remediation for {test1_flags} Test 1 item{plural}",
                "prompt": "Approve creating reference tables to fix the Test 1 chunk-PK failures",
                "requires_approval": True,
            }
        )
    return build_suggestions(refinements, follow_ups)
