"""New modes per block of reviews, the saturation hint and the re-review prompt (spec §4, Phase 5 rows). Pure: no
database. The database read behind them (taxonomy.discovery) is pinned in test_trace_lab_discovery_sql.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import taxonomy

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_the_curve_counts_new_modes_per_block():
    points = taxonomy.discovery_curve(25, {"m1": 0, "m2": 4, "m3": 12}, block=10)
    assert points == [{"block": 1, "reviews_through": 10, "new_modes": 2, "cumulative_modes": 2},
                      {"block": 2, "reviews_through": 20, "new_modes": 1, "cumulative_modes": 3},
                      {"block": 3, "reviews_through": 25, "new_modes": 0, "cumulative_modes": 3}]


def test_no_reviews_make_no_points_and_a_block_boundary_belongs_to_the_next_block():
    assert taxonomy.discovery_curve(0, {}, block=10) == []
    points = taxonomy.discovery_curve(20, {"m1": 9, "m2": 10}, block=10)      # review index 9 is the 10th, 10 the 11th
    assert [(p["block"], p["new_modes"], p["cumulative_modes"]) for p in points] == [(1, 1, 1), (2, 1, 2)]


def test_saturation_after_two_quiet_full_blocks():
    points = taxonomy.discovery_curve(30, {"m1": 0, "m2": 5}, block=10)
    sat = taxonomy.saturation(points, block=10, n_reviews=30)
    assert sat["hint"] is True and "The last 20 reviews produced no new failure mode." in sat["text"]


def test_no_hint_below_three_blocks_or_with_a_recent_mode():
    assert taxonomy.saturation(taxonomy.discovery_curve(25, {"m1": 0}, block=10), block=10, n_reviews=25)["hint"] is False
    points = taxonomy.discovery_curve(30, {"m1": 0, "m2": 25}, block=10)
    assert taxonomy.saturation(points, block=10, n_reviews=30)["hint"] is False


def test_the_hint_never_says_the_last_reviews_were_quiet_when_a_new_mode_came_in_the_unfinished_block():
    # 35 reviews: blocks 1-3 are full, block 4 holds 5. The sentence says "the last 20 reviews", and those include the
    # unfinished block, so a mode founded there (review 34) rules the hint out; a quiet unfinished block does not.
    quiet = taxonomy.saturation(taxonomy.discovery_curve(35, {"m1": 0}, block=10), block=10, n_reviews=35)
    assert quiet["hint"] is True and "The last 20 reviews produced no new failure mode." in quiet["text"]
    late = taxonomy.discovery_curve(35, {"m1": 0, "m2": 33}, block=10)
    assert taxonomy.saturation(late, block=10, n_reviews=35) == {"hint": False, "text": ""}


def test_no_hint_means_no_text():
    recent = taxonomy.saturation(taxonomy.discovery_curve(30, {"m1": 0, "m2": 15}, block=10), block=10, n_reviews=30)
    assert recent == {"hint": False, "text": ""}


def test_the_hint_names_the_block_size_it_was_asked_about():
    points = taxonomy.discovery_curve(15, {"m1": 0}, block=5)
    sat = taxonomy.saturation(points, block=5, n_reviews=15)
    assert sat["hint"] is True and "The last 10 reviews produced no new failure mode." in sat["text"]


def test_a_block_size_below_one_is_refused_not_a_division_by_zero():
    with pytest.raises(ValueError):
        taxonomy.discovery_curve(10, {}, block=0)


def test_a_mode_is_founded_by_the_earliest_note_of_itself_and_its_merged_parts():
    g = taxonomy.ModeGraph({
        "M": {"id": "M", "status": "candidate", "created_at": T0, "created_from": ["v3"], "merged_into": None},
        "A": {"id": "A", "status": "retired", "created_at": T0, "created_from": ["v1"], "merged_into": "M"},
        "R": {"id": "R", "status": "retired", "created_at": T0, "created_from": ["v2"], "merged_into": None}})
    reviews = [{"lineage_id": "L1"}, {"lineage_id": "L2"}, {"lineage_id": "L3"}]
    founding = taxonomy.founding_reviews(reviews, g, {"v1": "L1", "v2": "L2", "v3": "L3"})
    assert founding == {"M": 0}


def test_a_mode_whose_notes_are_not_among_the_reviews_has_no_founding_review():
    g = taxonomy.ModeGraph({
        "M": {"id": "M", "status": "confirmed", "created_at": T0, "created_from": ["v9"], "merged_into": None},
        "N": {"id": "N", "status": "frozen", "created_at": T0, "created_from": [], "merged_into": None},
        "P": {"id": "P", "status": "proposed", "created_at": T0, "created_from": ["v1"], "merged_into": None}})
    founding = taxonomy.founding_reviews([{"lineage_id": "L1"}], g, {"v1": "L1", "v9": "L-elsewhere"})
    assert founding == {}                  # M's note is another company's review; N has none; P is not live


def test_the_final_fair_batch_text():
    assert taxonomy.final_uniform_text(1) == ""
    assert taxonomy.final_uniform_text(0) == ""
    assert taxonomy.final_uniform_text(2) == ("The final fair batch produced 2 new failure modes. Review another "
                                              "batch before treating the list as stable.")


def test_the_re_review_prompt_counts_modes_created_after_the_fifth_review():
    reviews = [{"reviewer_id": "me", "turn_id": f"t{i}", "first_at": T0 + timedelta(hours=i)} for i in range(6)]
    created = {"m1": T0 + timedelta(hours=10), "m2": T0 + timedelta(hours=11), "m0": T0}
    out = taxonomy.re_review(reviews, created, "me")
    assert out["count"] == 2 and out["turn_ids"] == ["t0", "t1", "t2", "t3", "t4"]
    assert out["text"] == ("You have found 2 failure modes since you reviewed your first answers. "
                           "Worth a second pass.")
    assert taxonomy.re_review(reviews[:4], created, "me")["text"] == ""


def test_the_re_review_prompt_is_about_the_caller_alone_and_needs_two_new_modes():
    mine = [{"reviewer_id": "me", "turn_id": f"t{i}", "first_at": T0 + timedelta(hours=i)} for i in range(5)]
    theirs = [{"reviewer_id": "you", "turn_id": f"u{i}", "first_at": T0 + timedelta(hours=i)} for i in range(9)]
    created = {"m1": T0 + timedelta(hours=10)}
    assert taxonomy.re_review(mine + theirs, created, "me") == {"count": 1, "text": "", "turn_ids": []}
    assert taxonomy.re_review(theirs, created, "me") == {"count": 0, "text": "", "turn_ids": []}
    both = {"m1": T0 + timedelta(hours=10), "m2": T0 + timedelta(hours=4)}      # created AT the fifth review: not after it
    assert taxonomy.re_review(mine, both, "me")["count"] == 1
