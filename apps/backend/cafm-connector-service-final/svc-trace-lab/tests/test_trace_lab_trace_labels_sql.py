"""Failure-mode labels on a session's traces, on a throwaway Postgres (11 Nov 2026).

Runs are gone from Trace Lab: a person labels the turn they are reading on the session page. Each label says whether a
failure mode is there (positive) or not (negative) in one trace, from the same list of failure modes the Labels page
uses. Like every other table here it is append-only: a person's newest value is their value, clearing is a new row, and
everyone's values stay side by side. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import runs, trace_labels
from trace_lab.review_errors import Invalid, NotFound

pytestmark = requires_scratch

SME = member(org=ORG, all_companies=False, role="domain_expert", manages=False, user_id=str(uuid.uuid4()),
             email="sme@example.com")


async def _mode(name: str = "Wrong building") -> str:
    return (await runs.save_mode(member(), name=name, definition="The answer is about another building."))["lineage_id"]


async def _rows(lab) -> int:
    async with lab.connect() as c:
        return (await c.execute(text("SELECT count(*) FROM tracelab.trace_label_cells"))).scalar_one()


async def test_a_person_labels_a_trace_present_or_absent_per_failure_mode(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    out = await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    assert {k: out[k] for k in ("trace_id", "mode", "value")} == {"trace_id": "turn-1", "mode": lin, "value": "positive"}
    got = await trace_labels.labels_of(member(), "turn-1")
    assert [x["lineage_id"] for x in got["modes"]] == [lin] and got["me"] == member().user_id
    assert [{k: c[k] for k in ("value", "author_email", "mine")} for c in got["cells"][lin]] == [
        {"value": "positive", "author_email": "reviewer@example.com", "mine": True}]
    assert (await trace_labels.labels_of(member(), "turn-2"))["cells"] == {}, "a label belongs to one trace"


async def test_a_persons_newest_value_counts_and_everyone_elses_stays_beside_it(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(SME, "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="negative")
    cells = (await trace_labels.labels_of(member(), "turn-1"))["cells"][lin]
    assert [(c["author_email"], c["value"], c["mine"]) for c in cells] == [
        ("reviewer@example.com", "negative", True), ("sme@example.com", "positive", False)], "mine first"


async def test_clearing_takes_a_persons_value_away_and_keeps_every_row(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(member(), "turn-1", mode=lin, value=None)
    assert (await trace_labels.labels_of(member(), "turn-1"))["cells"] == {}
    assert await _rows(lab_scratch) == 2


async def test_a_label_needs_a_trace_the_person_may_see_a_live_mode_and_a_known_value(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    outsider = member(org=OTHER_ORG, all_companies=False, role="admin")
    with pytest.raises(NotFound):
        await trace_labels.set_label(outsider, "turn-1", mode=lin, value="positive")
    with pytest.raises(NotFound):
        await trace_labels.labels_of(outsider, "turn-1")
    with pytest.raises(NotFound):
        await trace_labels.set_label(member(), "turn-1", mode=str(uuid.uuid4()), value="positive")
    with pytest.raises(Invalid):
        await trace_labels.set_label(member(), "turn-1", mode=lin, value="maybe")
    await runs.retire_mode(member(), lin)
    with pytest.raises(NotFound):
        await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    assert await _rows(lab_scratch) == 0


async def test_a_company_member_labels_their_own_companys_traces(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(SME, "turn-1", mode=lin, value="negative")
    assert (await trace_labels.labels_of(SME, "turn-1"))["cells"][lin][0]["value"] == "negative"
