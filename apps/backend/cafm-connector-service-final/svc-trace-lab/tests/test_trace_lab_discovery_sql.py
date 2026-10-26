"""taxonomy.discovery against a scratch database: reviews in the order reviewers first wrote them, the curve of the live
modes those reviews founded (a merged part counts for its target), the batch each came from, the hint, the final fair
batch's note, the caller's own re-review prompt, and company scope. Lab tables only (no chat row is read). Needs the
scratch database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import taxonomy
from trace_lab.review_errors import Forbidden

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc)
OLD, NEW = NOW - timedelta(days=1), NOW + timedelta(hours=1)


def _tid(i: int, group: str = "d") -> str:
    return f"turn-{group}{i:015d}"


async def _review(lab, i, *, org=fx.ORG_A, reviewer=None, group="d", **kw):
    """One review per transaction, so each has its own created_at and the order is the order of the calls."""
    async with lab.begin() as conn:
        return _tid(i, group), await fx.add_note(conn, _tid(i, group), org, reviewer=reviewer or fx.admin(org), **kw)


async def _reviews(lab, n, *, mine=6, group="d"):
    """n reviews: the first `mine` by admin 1, the rest by admin 2 except the last by admin 3."""
    turns, notes = [], []
    for i in range(n):
        who = fx.admin(n=1) if i < mine else (fx.admin(n=3) if i == n - 1 else fx.admin(n=2))
        t, note = await _review(lab, i, reviewer=who, group=group)
        turns.append(t)
        notes.append(note)
    return turns, notes


async def _mode(lab, name, notes, status="confirmed", created_at=OLD, **kw):
    async with lab.begin() as conn:
        return await fx.add_mode(conn, name, status=status, created_from=list(notes), created_at=created_at, **kw)


async def test_the_curve_counts_live_modes_by_the_review_that_founded_them(lab_scratch):
    t, n = await _reviews(lab_scratch, 11)
    async with lab_scratch.begin() as conn:                                        # a golden answer and a cleared verdict
        await fx.add_note(conn, "turn-g100000000000001", fx.ORG_A)                  # are not reviews
        await fx.add_note(conn, _tid(50), fx.ORG_A, tombstone=True)
        batch = await fx.add_batch_item(conn, t[1], strategy="diversity", plan_step="cluster_representatives")
        await fx.add_batch_item(conn, t[3], batch_id=batch, position=2)
    await _mode(lab_scratch, "found_at_review_two", [n[1]])
    merged = await _mode(lab_scratch, "found_at_review_eight", [n[7]], status="candidate")
    await _mode(lab_scratch, "merged_part_found_earlier", [n[3]], status="retired", merged_into=merged)
    await _mode(lab_scratch, "retired_for_good", [n[0]], status="retired")
    await _mode(lab_scratch, "only_a_proposal", [n[2]], status="proposed")
    await _mode(lab_scratch, "found_in_the_last_block", [n[10]], status="frozen")
    out = await taxonomy.discovery(fx.admin(), fx.ORG_A)
    assert (out["reviews"], out["block"]) == (11, 10)
    assert out["points"] == [{"block": 1, "reviews_through": 10, "new_modes": 2, "cumulative_modes": 2},
                             {"block": 2, "reviews_through": 11, "new_modes": 1, "cumulative_modes": 3}]
    small = await taxonomy.discovery(fx.admin(), fx.ORG_A, block=1)                 # a block is 5 reviews at the least
    assert small["block"] == 5
    assert small["points"] == [{"block": 1, "reviews_through": 5, "new_modes": 2, "cumulative_modes": 2},
                               {"block": 2, "reviews_through": 10, "new_modes": 0, "cumulative_modes": 2},
                               {"block": 3, "reviews_through": 11, "new_modes": 1, "cumulative_modes": 3}]
    assert (await taxonomy.discovery(fx.admin(), fx.ORG_A, block=500))["block"] == 50
    assert out["by_batch"] == [{"batch_id": batch, "new_modes": 2},                  # the merged part's note founded one of them
                               {"batch_id": None, "new_modes": 1}]                  # answers outside any batch come last
    assert out["saturation"] == {"hint": False, "text": "", "final_uniform_text": ""}


async def test_the_hint_names_two_quiet_blocks(lab_scratch):
    _t, n = await _reviews(lab_scratch, 16)
    await _mode(lab_scratch, "found_at_the_start", [n[0]])
    out = await taxonomy.discovery(fx.admin(), fx.ORG_A, block=5)
    assert [p["new_modes"] for p in out["points"]] == [1, 0, 0, 0]
    assert out["saturation"]["hint"] is True and "The last 10 reviews produced no new failure mode." in out["saturation"]["text"]
    await _mode(lab_scratch, "found_in_the_last_review", [n[15]])
    again = await taxonomy.discovery(fx.admin(), fx.ORG_A, block=5)
    assert again["saturation"]["hint"] is False and again["points"][-1]["new_modes"] == 1


async def test_the_final_fair_batch_reports_its_new_modes_only_when_it_is_complete(lab_scratch):
    t, n = await _reviews(lab_scratch, 11)
    async with lab_scratch.begin() as conn:
        batch = await fx.add_batch_item(conn, t[8], strategy="uniform", plan_step="final_uniform")
        await fx.add_batch_item(conn, t[10], batch_id=batch, position=2)
    await _mode(lab_scratch, "found_in_the_final_batch", [n[8]])
    await _mode(lab_scratch, "also_found_in_the_final_batch", [n[10]])
    await _mode(lab_scratch, "found_earlier", [n[1]])
    done = await taxonomy.discovery(fx.admin(), fx.ORG_A)
    assert done["saturation"]["final_uniform_text"] == taxonomy.final_uniform_text(2)
    assert "The final fair batch produced 2 new failure modes." in done["saturation"]["final_uniform_text"]
    async with lab_scratch.begin() as conn:                                         # one more item nobody reviewed yet
        await fx.add_batch_item(conn, "turn-d900000000000001", batch_id=batch, position=3)
    pending = await taxonomy.discovery(fx.admin(), fx.ORG_A)
    assert pending["saturation"]["final_uniform_text"] == ""


async def test_the_re_review_prompt_is_the_callers_own(lab_scratch):
    t, n = await _reviews(lab_scratch, 11)                                          # admin 1 wrote the first six reviews
    await _mode(lab_scratch, "before_my_fifth_review", [n[0]], created_at=OLD)
    await _mode(lab_scratch, "after_it_one", [n[1]], created_at=NEW)
    await _mode(lab_scratch, "after_it_two", [n[2]], created_at=NEW)
    await _mode(lab_scratch, "after_it_but_not_live", [n[3]], status="retired", created_at=NEW)
    mine = (await taxonomy.discovery(fx.admin(n=1), fx.ORG_A))["re_review"]
    assert mine["count"] == 2 and mine["turn_ids"] == t[:5]
    assert mine["text"] == "You have found 2 failure modes since you reviewed your first answers. Worth a second pass."
    theirs = (await taxonomy.discovery(fx.admin(n=2), fx.ORG_A))["re_review"]          # admin 2 wrote only four reviews
    assert theirs == {"count": 0, "text": "", "turn_ids": []}


async def test_each_company_counts_its_own_reviews_and_the_platform_counts_them_all(lab_scratch):
    _ta, na = await _reviews(lab_scratch, 6)
    tb, nb = [], []
    for i in range(3):
        t, note = await _review(lab_scratch, i, org=fx.ORG_B, group="e")
        tb.append(t)
        nb.append(note)
    await _mode(lab_scratch, "found_by_company_a", [na[0]])
    await _mode(lab_scratch, "found_by_company_b", [nb[0]])
    a = await taxonomy.discovery(fx.admin(), fx.ORG_A)
    b = await taxonomy.discovery(fx.admin(fx.ORG_B), fx.ORG_B)
    everyone = await taxonomy.discovery(fx.owner(), None)
    assert (a["reviews"], b["reviews"], everyone["reviews"]) == (6, 3, 9)
    assert [p["new_modes"] for p in a["points"]] == [1] and [p["new_modes"] for p in b["points"]] == [1]
    assert everyone["points"][0]["new_modes"] == 2
    assert (await taxonomy.discovery(fx.owner(), fx.ORG_B))["reviews"] == 3
    with pytest.raises(Forbidden):
        await taxonomy.discovery(fx.admin(), fx.ORG_B)


async def test_no_reviews_and_no_modes_is_an_empty_but_complete_answer(lab_scratch):
    out = await taxonomy.discovery(fx.admin(), fx.ORG_A)
    assert out == {"points": [], "by_batch": [], "saturation": {"hint": False, "text": "", "final_uniform_text": ""},
                   "re_review": {"count": 0, "text": "", "turn_ids": []}, "reviews": 0, "block": 10}
