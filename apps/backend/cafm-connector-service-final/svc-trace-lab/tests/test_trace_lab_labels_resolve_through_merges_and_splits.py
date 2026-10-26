"""Labels are never rewritten when the taxonomy changes; cells resolve at read time (spec §5.4, R10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trace_lab import labels, taxonomy

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _t(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _mode(mid, *, status="candidate", created=0, rescoped=None, absorbed=None, merged_into=None, split_from=None,
          level="technical"):
    return {"id": mid, "name": f"mode_{mid}", "status": status, "created_at": _t(created),
            "rescoped_at": _t(created if rescoped is None else rescoped),
            "absorbed_at": None if absorbed is None else _t(absorbed), "merged_into": merged_into,
            "split_from": split_from, "level": level, "created_from": [f"note-{mid}"], "requirement_ids": [],
            "positive_turn_ids": [], "close_negative_turn_ids": []}


def _head(turn, mode, *, present, at, tombstone=False, source="human_structured_label", approval="single"):
    return labels.Head(label_id=f"{turn}:{mode}:{at}", turn_id=turn, mode_id=mode,
                       present=None if tombstone else present, tombstone=tombstone, source=source,
                       approval=approval, created_at=_t(at), decided_by_email="r@x.test")


def _heads(*hs):
    return {(h.turn_id, h.mode_id): h for h in hs}


def _graph(*ms):
    return taxonomy.ModeGraph({m["id"]: m for m in ms})


def test_an_own_head_after_the_absorb_wins():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    hs = _heads(_head("t1", "M", present=False, at=20), _head("t1", "A", present=True, at=5))
    cell = labels.resolve_cell(g, hs, "t1", g.modes["M"])
    assert (cell.state, cell.via_mode_id, cell.label_id) == ("absent", None, "t1:M:20")


def test_present_from_any_merged_part():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    hs = _heads(_head("t1", "M", present=False, at=5), _head("t1", "A", present=True, at=3))
    cell = labels.resolve_cell(g, hs, "t1", g.modes["M"])
    assert (cell.state, cell.via_mode_id) == ("present", "A")


def test_absent_only_when_every_part_is_absent():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    both = _heads(_head("t1", "M", present=False, at=5), _head("t1", "A", present=False, at=4))
    assert labels.resolve_cell(g, both, "t1", g.modes["M"]).state == "absent"
    one = _heads(_head("t1", "M", present=False, at=5))
    assert labels.resolve_cell(g, one, "t1", g.modes["M"]).state == "unchecked"


def test_a_clear_after_the_absorb_reads_unchecked():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    hs = _heads(_head("t1", "M", present=None, at=12, tombstone=True), _head("t1", "A", present=True, at=3))
    assert labels.resolve_cell(g, hs, "t1", g.modes["M"]).state == "unchecked"


def test_a_split_parents_absent_is_inherited_and_its_present_is_not():
    g = _graph(_mode("P", status="retired"), _mode("C", created=20, split_from="P"))
    absent = labels.resolve_cell(g, _heads(_head("t1", "P", present=False, at=10)), "t1", g.modes["C"])
    assert (absent.state, absent.via_mode_id) == ("absent", "P")
    present = labels.resolve_cell(g, _heads(_head("t1", "P", present=True, at=10)), "t1", g.modes["C"])
    assert present.state == "unchecked"
    late = labels.resolve_cell(g, _heads(_head("t1", "P", present=False, at=25)), "t1", g.modes["C"])
    assert late.state == "unchecked"


def test_a_rescope_makes_earlier_labels_stop_counting():
    g = _graph(_mode("M", rescoped=30))
    hs = _heads(_head("t1", "M", present=True, at=20))
    assert labels.resolve_cell(g, hs, "t1", g.modes["M"]).state == "unchecked"
    hs2 = _heads(_head("t1", "M", present=True, at=31))
    assert labels.resolve_cell(g, hs2, "t1", g.modes["M"]).state == "present"


def test_human_origin_follows_r10():
    g = _graph(_mode("M"))
    for source, approval, expected in (("human_structured_label", "single", True), ("human_open_code", "single", True),
                                       ("accepted_suggestion", "single", True), ("rejected_suggestion", "single", True),
                                       ("accepted_label_proposal", "single", True),
                                       ("accepted_label_proposal", "bulk", False), ("accepted_suggestion", "bulk", False)):
        cell = labels.resolve_cell(g, _heads(_head("t1", "M", present=True, at=1, source=source, approval=approval)),
                                   "t1", g.modes["M"])
        assert cell.human_origin is expected, (source, approval)


def test_parts_are_transitive_and_retired_ids_resolve_to_their_live_mode():
    g = _graph(_mode("M"), _mode("B", status="retired", merged_into="M"), _mode("A", status="retired", merged_into="B"))
    assert sorted(p["id"] for p in g.parts("M")) == ["A", "B", "M"]
    assert g.root_of("A") == "M"
    assert g.root_of("M") == "M"
    assert [m["id"] for m in g.live()] == ["M"]


def test_relevant_ids_cover_parts_and_only_the_nearest_split_ancestor():
    g = _graph(_mode("G", status="retired"), _mode("P", status="retired", split_from="G"),
               _mode("C", created=20, split_from="P"), _mode("A", status="retired", merged_into="C"))
    assert g.relevant_ids(["C"]) == ["A", "C", "P"]


def test_resolve_covers_every_turn_and_live_mode_pair():
    g = _graph(_mode("M1"), _mode("M2"))
    cells = labels.resolve(g, _heads(_head("t1", "M1", present=True, at=1)), ["t1", "t2"], ["M1", "M2"])
    assert set(cells) == {("t1", "M1"), ("t1", "M2"), ("t2", "M1"), ("t2", "M2")}
    assert cells[("t1", "M1")].state == "present" and cells[("t2", "M2")].state == "unchecked"


def test_a_data_fault_mode_is_never_judge_eligible():
    assert taxonomy.judge_eligible(_mode("M", level="data")) is False
    assert taxonomy.judge_eligible(_mode("M", level="domain")) is True
    assert "data" in taxonomy.LEVELS


def test_cell_payload_names_the_part_it_came_through():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    cell = labels.resolve_cell(g, _heads(_head("t1", "A", present=True, at=3)), "t1", g.modes["M"])
    out = labels.cell_payload(cell, g, base=None, proposal={"id": "p1", "suggestion": "absent"})
    assert out["via"] == "A" and out["via_name"] == "mode_A" and out["state"] == "present"
    assert "proposal" not in out



def test_a_proposed_or_rejected_mode_is_not_live_and_resolves_to_nothing():
    """A company admin's proposal and a rejected one are rows, not live modes: they never show in the grid."""
    g = _graph(_mode("M"), _mode("P", status="proposed"), _mode("R", status="rejected"),
               _mode("X", status="retired", merged_into="M"))
    assert [m["id"] for m in g.live()] == ["M"]
    assert g.root_of("P") is None and g.root_of("R") is None
    assert g.root_of("X") == "M"
    assert set(taxonomy.LIVE_STATUSES) == {"candidate", "confirmed", "frozen"}
    assert {"proposed", "rejected"} <= set(taxonomy.STATUSES)
    assert not ({"proposed", "rejected"} & set(taxonomy.LIVE_STATUSES))


def test_a_merge_cycle_does_not_loop():
    g = _graph(_mode("A", status="retired", merged_into="B"), _mode("B", status="retired", merged_into="A"))
    assert g.root_of("A") is None
    assert sorted(p["id"] for p in g.parts("A")) == ["A", "B"]


def test_a_tombstone_of_a_part_is_not_a_decision_and_a_late_tombstone_of_the_mode_clears():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    hs = _heads(_head("t1", "M", present=False, at=5), _head("t1", "A", present=None, at=4, tombstone=True))
    assert labels.resolve_cell(g, hs, "t1", g.modes["M"]).state == "unchecked"


def test_cell_payload_for_an_unchecked_cell_carries_the_proposal_and_the_withdrawn_note():
    g = _graph(_mode("M"))
    cell = labels.resolve_cell(g, {}, "t1", g.modes["M"])
    out = labels.cell_payload(cell, g, base="b1", proposal={"id": "p1", "suggestion": "present"}, note_withdrawn=True)
    assert out["state"] == "unchecked" and out["proposal"]["id"] == "p1" and out["note_withdrawn"] is True
    assert out["base"] == "b1" and out["via"] is None and out["via_name"] is None and out["decided_at"] is None


def test_a_label_saved_at_the_instant_of_the_absorb_is_on_the_full_meaning():
    """A merge and a label saved in one transaction share a timestamp: that label decides, a part's present does not."""
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"))
    hs = _heads(_head("t1", "M", present=False, at=10), _head("t1", "A", present=True, at=3))
    cell = labels.resolve_cell(g, hs, "t1", g.modes["M"])
    assert (cell.state, cell.via_mode_id, cell.label_id) == ("absent", None, "t1:M:10")


def test_present_reports_the_modes_own_decision_first_and_among_parts_the_latest():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M"),
               _mode("B", status="retired", merged_into="M"))
    own = labels.resolve_cell(g, _heads(_head("t1", "M", present=True, at=5), _head("t1", "A", present=True, at=8)),
                              "t1", g.modes["M"])
    assert (own.state, own.via_mode_id, own.label_id) == ("present", None, "t1:M:5")
    parts = labels.resolve_cell(g, _heads(_head("t1", "A", present=True, at=3), _head("t1", "B", present=True, at=8)),
                                "t1", g.modes["M"])
    assert (parts.state, parts.via_mode_id, parts.label_id) == ("present", "B", "t1:B:8")


def test_a_split_parents_absent_older_than_the_parents_last_rescope_is_not_inherited():
    g = _graph(_mode("P", status="retired", rescoped=15), _mode("C", created=20, split_from="P"))
    for at, expected in ((10, "unchecked"), (14, "unchecked"), (15, "absent"), (16, "absent")):
        cell = labels.resolve_cell(g, _heads(_head("t1", "P", present=False, at=at)), "t1", g.modes["C"])
        assert cell.state == expected, at
        if expected == "absent":
            assert cell.via_mode_id == "P"


def test_a_part_label_saved_at_the_instant_of_its_rescope_counts_and_an_earlier_one_does_not():
    g = _graph(_mode("M", absorbed=10), _mode("A", status="retired", merged_into="M", rescoped=5))
    at_rescope = labels.resolve_cell(g, _heads(_head("t1", "A", present=True, at=5)), "t1", g.modes["M"])
    assert (at_rescope.state, at_rescope.via_mode_id) == ("present", "A")
    before = labels.resolve_cell(g, _heads(_head("t1", "A", present=True, at=4)), "t1", g.modes["M"])
    assert before.state == "unchecked"
