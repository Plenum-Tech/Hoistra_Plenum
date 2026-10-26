"""The pure parts of the Labels grid, note filing and discovery reads: how completeness is counted, the fair-rate
interval, where a note points, who may read or set aside what (refused before any database is touched), and that every
refusal the note code raises has a plain sentence for the screen. No database, no network."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace_lab import annotations, labels, taxonomy
from trace_lab.review_errors import Forbidden

PKG = Path(labels.__file__).parent
ORG = str(uuid.uuid4())
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _who(role, org=ORG, *, admin=None):
    return SimpleNamespace(role=role, organization_id=org, user_id=uuid.uuid4(), email="a@b.test",
                           is_admin=role in ("admin", "superadmin") if admin is None else admin)


def _cell(state):
    return labels.Cell(state, None if state == "unchecked" else "l1", None, None, None, False)


# ── completeness ──────────────────────────────────────────────────────────────────────────

def test_completeness_counts_decided_cells_and_names_the_answers_that_still_miss_some():
    modes = [{"id": "m1"}, {"id": "m2"}]
    rows = [{"turn_id": "t1", "question": "q1"}, {"turn_id": "t2", "question": "q2"}, {"turn_id": "t3", "question": "q3"}]
    cells = {("t1", "m1"): _cell("present"), ("t1", "m2"): _cell("absent"),
             ("t2", "m1"): _cell("present"), ("t2", "m2"): _cell("unchecked"),
             ("t3", "m1"): _cell("unchecked"), ("t3", "m2"): _cell("unchecked")}
    out = labels.completeness_counts(rows, modes, cells)
    assert (out["reviewed_turns"], out["modes"], out["required"], out["done"], out["missing"]) == (3, 2, 6, 3, 3)
    assert out["missing_preview"] == [{"turn_id": "t2", "question": "q2", "missing": 1},
                                      {"turn_id": "t3", "question": "q3", "missing": 2}] and out["more"] == 0


def test_the_preview_is_capped_and_says_how_many_more_answers_miss_cells():
    modes = [{"id": "m1"}]
    rows = [{"turn_id": f"t{i}", "question": f"q{i}"} for i in range(40)]
    cells = {(r["turn_id"], "m1"): _cell("unchecked") for r in rows}
    out = labels.completeness_counts(rows, modes, cells, preview=25)
    assert len(out["missing_preview"]) == 25 and out["more"] == 15 and out["missing"] == 40


def test_nothing_reviewed_or_no_modes_means_nothing_required():
    assert labels.completeness_counts([], [{"id": "m"}], {})["required"] == 0
    out = labels.completeness_counts([{"turn_id": "t", "question": "q"}], [], {})
    assert (out["required"], out["done"], out["missing"], out["missing_preview"]) == (0, 0, 0, [])


# ── the fair-rate interval ────────────────────────────────────────────────────────────────

def test_the_fair_rate_is_a_rounded_share_with_a_wilson_interval_and_nothing_when_there_is_no_sample():
    assert labels._wilson(0, 0) == (None, None, None)
    rate, low, high = labels._wilson(1, 2)
    assert rate == 0.5 and 0 < low < 0.5 < high < 1
    rate, low, high = labels._wilson(3, 3)
    assert rate == 1.0 and 0 < low < 1.0 and high <= 1.0


# ── where a note points ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    ({"span_agent": "wo_engine", "span_id": "s"}, "Work orders"),
    ({"span_agent": "tool:wo_engine", "span_id": "s"}, "Work orders"),
    ({"span_agent": "udr", "span_id": "s"}, "Records lookup"),
    ({"span_agent": "orchestrator", "span_id": "s"}, "Orchestrator (routing, planning, write-up)"),
    ({"span_agent": "something_new", "span_id": "s"}, "A step"),
    ({"span_agent": None, "span_id": "s"}, "A step"),
    ({"span_agent": None, "span_id": None, "field": "answer"}, "Answer"),
    ({"span_agent": None, "span_id": None, "field": "question"}, "Question"),
    ({}, "Answer")])
def test_a_note_says_where_it_points_in_words_a_reviewer_knows(row, expected):
    assert annotations.step_label(row) == expected


# ── who may do what, refused before any database work ─────────────────────────────────────

def test_a_read_acts_for_the_callers_own_company_or_for_the_one_a_superadmin_names():
    other = str(uuid.uuid4())
    assert taxonomy.acting_company(_who("superadmin"), None) is None
    assert taxonomy.acting_company(_who("superadmin"), other) == other
    assert taxonomy.acting_company(_who("admin"), ORG) == ORG
    assert taxonomy.acting_company(_who("admin"), None) == ORG              # naming none means their own
    for bad in (_who("admin"), _who("admin", None), _who("user", admin=False)):
        target = other if bad.organization_id else None
        with pytest.raises(Forbidden) as e:
            taxonomy.acting_company(bad, target)
        assert e.value.reason == "reviewers_only"


@pytest.mark.parametrize("call", [
    lambda p, org: labels.matrix(p, org), lambda p, org: labels.completeness(p, org),
    lambda p, org: labels.per_mode(p, org), lambda p, org: taxonomy.discovery(p, org),
    lambda p, org: annotations.unfiled_notes(p, org)])
async def test_a_company_admin_cannot_read_another_company_whatever_route_reached_the_function(call):
    with pytest.raises(Forbidden) as e:                                      # no database exists here: refused first
        await call(_who("admin"), str(uuid.uuid4()))
    assert e.value.reason == "reviewers_only"
    with pytest.raises(Forbidden):
        await call(_who("user", admin=False), ORG)


async def test_the_platform_owner_picking_a_company_must_name_one_and_nobody_else_can_pick():
    from trace_lab.review_errors import Invalid

    with pytest.raises(Invalid) as e:                                        # refused before any database is touched
        await labels.matrix(_who("superadmin", None), None, filter_org="not-a-company")
    assert e.value.reason == "bad_company" and e.value.error == "That is not a company id."


@pytest.mark.parametrize("role", ["admin", "user"])
async def test_only_the_platform_team_sets_a_note_aside_or_brings_it_back(role):
    who = _who(role, admin=role == "admin")
    with pytest.raises(Forbidden) as e:
        await annotations.dispose_note(who, str(uuid.uuid4()), outcome="no_mode_fits", note="x")
    assert e.value.reason == "platform_only"
    with pytest.raises(Forbidden):
        await annotations.undispose_note(who, str(uuid.uuid4()))


async def test_a_plain_user_cannot_file_a_note():
    with pytest.raises(Forbidden):
        await annotations.file_note(_who("user", admin=False), ORG, str(uuid.uuid4()), str(uuid.uuid4()), base=None)


async def test_a_note_or_mode_that_is_not_an_id_is_not_found_before_any_database_work():
    from trace_lab.review_errors import NotFound

    with pytest.raises(NotFound) as e:
        await annotations.file_note(_who("admin"), ORG, "not-an-id", str(uuid.uuid4()), base=None)
    assert e.value.reason == "annotation_not_found"
    with pytest.raises(NotFound) as e:
        await annotations.file_note(_who("admin"), ORG, str(uuid.uuid4()), "nope", base=None)
    assert e.value.reason == "mode_not_found"
    with pytest.raises(NotFound):
        await annotations.undispose_note(_who("superadmin", None), "not-an-id")


# ── sentences ─────────────────────────────────────────────────────────────────────────────

def test_every_refusal_the_note_code_raises_has_a_sentence_and_the_new_ones_read_plainly():
    src = (PKG / "annotations.py").read_text(encoding="utf-8")
    used = set(re.findall(r"\btaxonomy\.(?:invalid|not_found|conflict|forbidden)\(\s*\"([a-z_]+)\"", src))
    assert {"conflicts_with_label", "not_a_failure_while_present", "unknown_outcome", "not_a_wrong_note",
            "mode_not_found", "platform_only"} <= used, used
    assert used <= set(taxonomy.SENTENCES), sorted(used - set(taxonomy.SENTENCES))
    for reason in ("conflicts_with_label", "not_a_failure_while_present", "unknown_outcome"):
        sentence = taxonomy.SENTENCES[reason]
        assert sentence[0].isupper() and sentence.rstrip()[-1] == "."


def test_the_notes_outcomes_are_the_two_the_unfiled_list_offers():
    assert annotations.DISPOSE_OUTCOMES == ("no_mode_fits", "not_a_failure")
