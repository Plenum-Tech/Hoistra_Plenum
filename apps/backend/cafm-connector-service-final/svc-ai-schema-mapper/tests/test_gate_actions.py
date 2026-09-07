"""Tests for CAFM-004/006 per-decision Section-3 cards (udr.gate_actions). Pure, no DB.
Run: python tests/test_gate_actions.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from udr.gate_actions import (  # noqa: E402
    apply_gate_decisions,
    build_gate_decision_actions,
    find_decision_card,
    valid_option_ids,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


CI = {
    "groups": [
        {"group_id": "G1", "canonical_name": "site_id",
         "members": ["assets.site_id", "sites.site_id", "technicians.base_site_id"]},
        {"group_id": "G2", "canonical_name": "supplier",
         "members": ["vendors.vendor_id", "workorders.vendor_id"]},
        {"group_id": "G3", "canonical_name": "solo", "members": ["only.one"]},  # not a merge
    ],
    "fk_candidates": [
        {"ri": 1.0, "reason": "grouped with PK sites.site_id", "confirmed": True,
         "dst_table": "sites", "src_table": "assets", "dst_column": "site_id", "src_column": "site_id"},
        {"ri": 0.931, "reason": "value overlap below FK threshold",
         "dst_table": "vendors", "src_table": "resource", "dst_column": "vendor_id", "src_column": "vendor_id"},
    ],
}

cards = build_gate_decision_actions(CI, entry_id="E1", migration_id="M1")

check("emits a card per real decision (2 merges + 2 FKs)", len(cards) == 4)
check("1-member group is NOT a merge card",
      not any("solo" in (c["label"] + c["description"]) for c in cards))

merge = next(c for c in cards if c["label"].startswith("Column merge confirmation"))
check("merge card names its members in the title", "assets.site_id" in merge["label"])
check("merge card body cites the real member count", "3 columns were grouped" in merge["description"])
check("merge card carries the migration id", merge["migration_id"] == "M1")
check("merge card is inline-resolvable (not route_only)", not merge.get("route_only"))
check("merge card offers Merge + Keep-separate options",
      valid_option_ids(merge) == {"merge", "keep"})
check("merge option label names the canonical target", "Merge into site_id" in [o["label"] for o in merge["options"]])

fk = next(c for c in cards if c["label"].startswith("Foreign Key or Shared Attribute?"))
check("FK card renders the src↔dst pair", "assets.site_id ↔ sites.site_id" in fk["label"])
check("FK card body shows the REAL referential-integrity pct", "100%" in fk["description"])
check("FK card offers FK + Shared-attribute options", valid_option_ids(fk) == {"fk", "shared"})
fk2 = [c for c in cards if "93%" in c["description"]]
check("FK card shows a below-threshold RI verbatim (93%)", len(fk2) == 1)

check("all cards are pending", all(c["status"] == "pending" for c in cards))
check("empty CI → no cards (caller falls back to generic row)",
      build_gate_decision_actions({}, entry_id="E1", migration_id="M1") == [])
check("non-dict CI → no cards", build_gate_decision_actions(None, entry_id="E1", migration_id="M1") == [])

# ── resolution overlay + server-side validation ────────────────────────────────
gd = {merge["id"]: {"option_id": "merge", "option_label": "Merge into site_id", "at": "2026-07-09T10:00:00"}}
overlaid = apply_gate_decisions(build_gate_decision_actions(CI, entry_id="E1", migration_id="M1"), gd)
rm = next(c for c in overlaid if c["id"] == merge["id"])
check("a persisted decision flips its card to resolved", rm["status"] == "resolved")
check("resolved card carries the chosen option + note",
      rm["chosen_option_id"] == "merge" and rm["resolution_note"] == "Merge into site_id")
check("un-resolved cards stay pending after overlay",
      next(c for c in overlaid if c["id"] == fk["id"])["status"] == "pending")

check("find_decision_card locates a real card by id",
      find_decision_card(CI, entry_id="E1", migration_id="M1", card_id=merge["id"]) is not None)
check("find_decision_card rejects an unknown id",
      find_decision_card(CI, entry_id="E1", migration_id="M1", card_id="gate:E1:merge:bogus") is None)

# ── field-mapping gate: flagged / unmapped field cards from mapping_decisions ────
MD = [
    {"source_table": "technicians_1", "source_column": "tech_id", "confidence": 0.98,
     "dest_table": "technicians", "dest_column": "tech_id"},  # resolved → NOT a card
    {"source_table": "technicians_1", "source_column": "shift_pattern", "confidence": 0.64,
     "dest_table": None, "dest_column": "Review Required"},
    {"source_table": "technicians_1", "source_column": "badge_no", "confidence": 0.41,
     "dest_table": None, "dest_column": "New Column Suggested"},
]
field_cards = build_gate_decision_actions(
    None, entry_id="E1", migration_id="M1", mapping_decisions=MD)
check("only flagged/unmapped fields become cards (resolved ones excluded)", len(field_cards) == 2)
review = next(c for c in field_cards if c["label"].startswith("Field mapping review"))
check("review card names the flagged column", "shift_pattern" in review["label"])
check("review card body shows the REAL confidence (64%)", "64%" in review["description"])
check("review card offers approve / semantic / unmapped",
      valid_option_ids(review) == {"approve", "semantic", "unmapped"})
newcol = next(c for c in field_cards if c["label"].startswith("New destination column"))
check("new-column card offers create / unmapped", valid_option_ids(newcol) == {"create", "unmapped"})
check("field cards resolve via find_decision_card (mapping_decisions threaded)",
      find_decision_card(None, entry_id="E1", migration_id="M1",
                         card_id=review["id"], mapping_decisions=MD) is not None)

print("\nALL GATE-ACTION TESTS PASSED")
