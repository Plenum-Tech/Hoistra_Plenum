"""CAFM-004/006 — rich, inline-resolvable HITL cards for Activity-Log Section 3.

The pre-semantic gate pauses the run on a *set* of real decision points (columns grouped by
value/format similarity, FK-vs-shared-attribute calls). Instead of one generic "Review in
migration panel" row, this turns each real decision in the run's ``column_intelligence`` report
into its own Section-3 card WITH inline option buttons — matching the UDR Activity-Log sample
("Action 1 of N — Column merge confirmation …", "Foreign Key or Shared Attribute? …").

PURE (no DB / LLM): reads the SAME ``column_intelligence`` payload the gate already carries (so
every number traces to real pipeline output — no invented values) and returns action dicts shaped
like the persisted inline actions the FE renders. The chosen option is recorded durably on the
run's activity entry (``refs.gate_decisions``) by the resolve endpoint, so each decision is
auditable and survives polls — the same "record the human decision, never auto-pick" contract as
the persisted inline actions (AL.3/AL.4).
"""

from __future__ import annotations

from typing import Any

# Keep Section 3 readable on very wide workbooks — surface the most informative decisions and
# report how many were collapsed rather than silently dropping them (no "covered everything" lie).
_MAX_MERGE_CARDS = 6
_MAX_FK_CARDS = 6
_MAX_FIELD_CARDS = 8


def _pct(v: Any) -> str:
    return f"{round(v * 100)}%" if isinstance(v, (int, float)) else "—"


def _members_phrase(members: list[str]) -> str:
    """"a and b" / "a, b +N more" for a card title."""
    m = [str(x) for x in members if x]
    if len(m) <= 2:
        return " and ".join(m)
    return f"{', '.join(m[:2])} +{len(m) - 2} more"


def _card(
    *, card_id: str, entry_id: str, migration_id: str, label: str, body: str, options: list[dict]
) -> dict:
    """Assemble one inline decision card (FE ``ActivityActionItem`` shape). NOT route_only — the
    FE renders its ``options`` as buttons; resolving posts the chosen option to the resolve
    endpoint, which records it on the run and flips the card ``resolved``."""
    return {
        "id": card_id,
        "entry_id": entry_id,
        "kind": "approval",
        "label": label,
        "description": body,
        "options": [{"id": o["id"], "label": o["label"], "value": o["label"]} for o in options],
        "status": "pending",
        "migration_id": migration_id,
        "chosen_option_id": None,
        "resolution_note": None,
        "deadline_at": None,
        "resolved_at": None,
        "created_at": None,
    }


def _merge_card(group: dict, *, entry_id: str, migration_id: str) -> dict | None:
    members = [str(x) for x in (group.get("members") or []) if x]
    if len(members) < 2:  # a 1-member "group" is not a merge decision
        return None
    canonical = str(group.get("canonical_name") or "").strip()
    gid = str(group.get("group_id") or "")
    canon_txt = f" into canonical column '{canonical}'" if canonical else ""
    return _card(
        card_id=f"gate:{entry_id}:merge:{gid or canonical or members[0]}",
        entry_id=entry_id,
        migration_id=migration_id,
        label=f"Column merge confirmation — {_members_phrase(members)}",
        body=(
            f"{len(members)} columns were grouped by value + format similarity{canon_txt}: "
            f"{', '.join(members)}. Confirm they represent the same attribute and should map to "
            "one destination column, or keep them separate."
        ),
        options=[
            {"id": "merge", "label": f"Merge into {canonical}" if canonical else "Merge as one column"},
            {"id": "keep", "label": "Keep separate"},
        ],
    )


def _fk_card(fk: dict, *, entry_id: str, migration_id: str) -> dict | None:
    st, sc = str(fk.get("src_table") or ""), str(fk.get("src_column") or "")
    dt, dc = str(fk.get("dst_table") or ""), str(fk.get("dst_column") or "")
    if not (st and sc and dt and dc):
        return None
    reason = str(fk.get("reason") or "").strip()
    return _card(
        card_id=f"gate:{entry_id}:fk:{st}.{sc}->{dt}.{dc}",
        entry_id=entry_id,
        migration_id=migration_id,
        label=f"Foreign Key or Shared Attribute? — {st}.{sc} ↔ {dt}.{dc}",
        body=(
            f"{st}.{sc} and {dt}.{dc} share {_pct(fk.get('ri'))} referential integrity"
            + (f" ({reason})" if reason else "")
            + ". Classify as a Foreign Key to enforce referential integrity (orphan rows flagged "
            "for remediation), or as a Shared Attribute for no enforcement."
        ),
        options=[
            {"id": "fk", "label": "Classify as Foreign Key"},
            {"id": "shared", "label": "Classify as Shared Attribute"},
        ],
    )


def _field_card(dec: dict, *, entry_id: str, migration_id: str) -> dict | None:
    """A field-mapping-gate decision: a Tier-2 column the semantic pass could NOT confidently
    place — flagged for review ("Review Required") or with no destination match ("New Column
    Suggested"). Built from the run's persisted ``mapping_decisions``."""
    st = str(dec.get("source_table") or "")
    sc = str(dec.get("source_column") or "")
    dc = str(dec.get("dest_column") or "")
    if not sc or dc not in ("Review Required", "New Column Suggested"):
        return None
    conf = dec.get("confidence")
    conf_txt = f" (confidence {_pct(conf)})" if isinstance(conf, (int, float)) else ""
    src = f"{sc} ({st})" if st else sc
    if dc == "New Column Suggested":
        label = f"New destination column — {src}"
        body = (
            f"No existing destination column matched {st + '.' if st else ''}{sc}{conf_txt}. "
            "Create a new column in the destination table, or leave it unmapped."
        )
        options = [
            {"id": "create", "label": "Create new column"},
            {"id": "unmapped", "label": "Leave unmapped"},
        ]
    else:
        label = f"Field mapping review — {src}"
        body = (
            f"Tier-2 semantic mapping flagged {st + '.' if st else ''}{sc}{conf_txt} for review. "
            "Approve the suggested destination, send it to deeper semantic search, or leave it "
            "unmapped."
        )
        options = [
            {"id": "approve", "label": "Approve mapping"},
            {"id": "semantic", "label": "Send to semantic search"},
            {"id": "unmapped", "label": "Leave unmapped"},
        ]
    return _card(
        card_id=f"gate:{entry_id}:field:{st}.{sc}",
        entry_id=entry_id, migration_id=migration_id, label=label, body=body, options=options,
    )


def build_gate_decision_actions(
    column_intelligence: Any,
    *,
    entry_id: str,
    migration_id: str,
    mapping_decisions: Any = None,
) -> list[dict]:
    """One Section-3 card per real decision the run reached — merge groups + FK-vs-shared
    candidates (from ``column_intelligence``), plus flagged / unmapped field decisions (from
    ``mapping_decisions``, the field-mapping gate's per-column calls). Returns [] when there is
    no real decision to surface, so the caller can fall back to the single generic gate row.

    PURE — every figure comes from the run's own payloads; nothing is fabricated."""
    ci = column_intelligence if isinstance(column_intelligence, dict) else {}
    cards: list[dict] = []

    groups = ci.get("groups")
    if isinstance(groups, list):
        for g in groups:
            if len(cards) >= _MAX_MERGE_CARDS:
                break
            if isinstance(g, dict):
                card = _merge_card(g, entry_id=entry_id, migration_id=migration_id)
                if card:
                    cards.append(card)

    fks = ci.get("fk_candidates")
    if isinstance(fks, list):
        fk_cards: list[dict] = []
        for f in fks:
            if len(fk_cards) >= _MAX_FK_CARDS:
                break
            if isinstance(f, dict):
                card = _fk_card(f, entry_id=entry_id, migration_id=migration_id)
                if card:
                    fk_cards.append(card)
        cards.extend(fk_cards)

    if isinstance(mapping_decisions, list):
        field_cards: list[dict] = []
        for d in mapping_decisions:
            if len(field_cards) >= _MAX_FIELD_CARDS:
                break
            if isinstance(d, dict):
                card = _field_card(d, entry_id=entry_id, migration_id=migration_id)
                if card:
                    field_cards.append(card)
        cards.extend(field_cards)

    return cards


def apply_gate_decisions(cards: list[dict], gate_decisions: Any) -> list[dict]:
    """Overlay the persisted ``refs.gate_decisions`` map ({card_id: {option_id, option_label,
    at}}) onto freshly-built cards so a resolved decision reads ``resolved`` across polls. Pure."""
    gd = gate_decisions if isinstance(gate_decisions, dict) else {}
    for c in cards:
        rec = gd.get(c["id"])
        if isinstance(rec, dict) and rec.get("option_id"):
            c["status"] = "resolved"
            c["chosen_option_id"] = rec.get("option_id")
            c["resolution_note"] = rec.get("option_label") or "Resolved"
            c["resolved_at"] = rec.get("at")
    return cards


def valid_option_ids(card: dict) -> set[str]:
    """The option ids a card offered — guards resolution against an option never presented."""
    return {str(o.get("id")) for o in (card.get("options") or []) if isinstance(o, dict)}


def find_decision_card(
    column_intelligence: Any,
    *,
    entry_id: str,
    migration_id: str,
    card_id: str,
    mapping_decisions: Any = None,
) -> dict | None:
    """Rebuild the run's decision cards and return the one matching ``card_id`` (server-side
    validation that a resolve targets a real, offered decision). None if not found."""
    for c in build_gate_decision_actions(
        column_intelligence, entry_id=entry_id, migration_id=migration_id,
        mapping_decisions=mapping_decisions,
    ):
        if c["id"] == card_id:
            return c
    return None
