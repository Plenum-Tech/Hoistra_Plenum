"""Blind review (spec 2.3, 6.1) holds for a cell's history and for the 409 a stale label write returns.

`cells_for_turn` and the matrix already hide a label from a company caller who has not judged the answer. The cell's
history and the `current` of a stale 409 are the same label, so they follow the same rule: a caller who is not the
platform owner and has not judged the answer (or revealed it, or seen it through a finished sample) gets an empty chain
with `hidden: true`, and a 409 whose `current` is only the head's id (the client needs it as the new base) with no note,
quote, value or email. After judging, or as the platform owner, they get the label itself. The first block is pure; the
rest needs the scratch database."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels
from trace_lab.review_errors import Stale

TURN = "turn-c200000000000001"
NOTE = "Counts the July jobs twice."
QUOTE = "12 open work orders"
EMAIL = "reviewer@x.test"
BLIND = fx.admin(fx.ORG_A, n=3)            # same company as the label, has not judged the answer


# ── pure: what a 409 may carry to a caller who may not read the cell yet ────────────────

def test_a_blind_409_keeps_only_the_heads_id():
    head = {"id": "l-1", "present": False, "note": NOTE, "quote": QUOTE, "decided_by_email": EMAIL, "decided_by": "u-1",
            "turn_id": TURN, "mode_id": "m-1", "supersedes": None, "basis": {"x": 1}}
    out = labels.blind_head(head)
    assert out == {"id": "l-1"}
    assert head["note"] == NOTE                                        # the input is left alone


@pytest.mark.parametrize("current", [None, {}, {"note": NOTE}])
def test_a_409_with_no_head_to_name_carries_nothing(current):
    assert labels.blind_head(current) is None


FULL_HEAD = {"id": "l-1", "present": False, "note": NOTE, "quote": QUOTE, "decided_by_email": EMAIL}


@pytest.fixture
def stale_write(monkeypatch):
    """set_label's inner work refuses with a stale base and the full head; the blind-rule lookup is a stub."""
    seen: list[str] = []

    async def refuse(*_a, **_kw):
        raise Stale(dict(FULL_HEAD))

    def lookup(answer):
        async def may_read(principal, org, turn_id):
            seen.append(turn_id)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(labels, "_may_read_cells", may_read)

    monkeypatch.setattr(labels, "_set_label", refuse)
    lookup.seen = seen
    return lookup


async def _stale(who=BLIND):
    with pytest.raises(Stale) as e:
        await labels.set_label(who, fx.ORG_A, TURN, str(uuid.uuid4()), present=True, base=None)
    return e.value.current


async def test_a_caller_who_may_not_read_the_cell_is_refused_the_head_but_for_its_id(stale_write):
    stale_write(False)
    assert await _stale() == {"id": "l-1"}
    assert stale_write.seen == [TURN]


async def test_a_caller_who_may_read_the_cell_gets_the_whole_head(stale_write):
    stale_write(True)
    assert await _stale() == FULL_HEAD


async def test_a_failed_lookup_is_answered_as_if_the_caller_were_blind(stale_write):
    stale_write(RuntimeError("lab database unreachable"))
    assert await _stale() == {"id": "l-1"}


async def test_a_stale_409_that_names_no_head_needs_no_lookup(stale_write, monkeypatch):
    async def refuse(*_a, **_kw):
        raise Stale(None)

    monkeypatch.setattr(labels, "_set_label", refuse)
    stale_write(RuntimeError("must not be asked"))
    assert await _stale() is None
    assert stale_write.seen == []


# ── scratch ───────────────────────────────────────────────────────────────────────────────

async def _cell_with_a_label(lab, main):
    """One answer, one confirmed mode, and a head label somebody else wrote with a note, a quote and a name."""
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        head = await fx.add_label(conn, TURN, mid, present=False, note=NOTE, quote=QUOTE)
    return mid, head


def _reveals_nothing(obj) -> bool:
    blob = json.dumps(obj, default=str)
    return NOTE not in blob and QUOTE not in blob and EMAIL not in blob and "present" not in blob


async def _judge(lab, who):
    async with lab.begin() as conn:
        await fx.add_note(conn, TURN, fx.ORG_A, reviewer=who, verdict="right")


@requires_scratch
async def test_an_admin_who_has_not_judged_the_answer_gets_an_empty_hidden_chain(lab_scratch, main_scratch):
    mid, _head = await _cell_with_a_label(lab_scratch, main_scratch)
    for who, org in ((BLIND, fx.ORG_A), (fx.acting_superadmin(), fx.ORG_A)):
        assert await labels.history(who, org, TURN, mid) == {"chain": [], "hidden": True}


@requires_scratch
async def test_the_history_follows_the_same_rule_as_the_turn_chips(lab_scratch, main_scratch):
    mid, _head = await _cell_with_a_label(lab_scratch, main_scratch)

    async def both(who, org):
        return ((await labels.history(who, org, TURN, mid))["hidden"],
                (await labels.cells_for_turn(who, org, TURN))["hidden"])

    assert await both(BLIND, fx.ORG_A) == (True, True)
    assert await both(fx.owner(), None) == (False, False)
    await _judge(lab_scratch, BLIND)
    assert await both(BLIND, fx.ORG_A) == (False, False)


@requires_scratch
async def test_a_stale_409_names_only_the_head_to_a_caller_who_has_not_judged(lab_scratch, main_scratch):
    mid, head = await _cell_with_a_label(lab_scratch, main_scratch)
    for base in (None, str(uuid.uuid4())):
        with pytest.raises(Stale) as e:
            await labels.set_label(BLIND, fx.ORG_A, TURN, mid, present=True, base=base)
        assert e.value.current == {"id": head}
        assert _reveals_nothing(e.value.current)
    chain = await labels.history(fx.owner(), None, TURN, mid)
    assert [c["id"] for c in chain["chain"]] == [head]                       # nothing was written


@requires_scratch
async def test_the_loser_of_a_first_write_race_gets_the_winners_id_and_nothing_else(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    one, two = fx.admin(fx.ORG_A, n=4), fx.admin(fx.ORG_A, n=5)
    results = await asyncio.gather(
        labels.set_label(one, fx.ORG_A, TURN, mid, present=True, base=None, note=NOTE),
        labels.set_label(two, fx.ORG_A, TURN, mid, present=False, base=None, note=NOTE),
        return_exceptions=True)
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, Stale)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].current == {"id": winners[0]["label"]["id"]}
    assert _reveals_nothing(losers[0].current)


@requires_scratch
async def test_after_judging_the_answer_the_caller_gets_the_label_and_its_history(lab_scratch, main_scratch):
    mid, head = await _cell_with_a_label(lab_scratch, main_scratch)
    await _judge(lab_scratch, BLIND)
    seen = await labels.history(BLIND, fx.ORG_A, TURN, mid)
    assert seen["hidden"] is False and [c["id"] for c in seen["chain"]] == [head]
    assert (seen["chain"][0]["note"], seen["chain"][0]["quote"]) == (NOTE, QUOTE)
    assert seen["chain"][0]["decided_by_email"] == EMAIL
    with pytest.raises(Stale) as e:
        await labels.set_label(BLIND, fx.ORG_A, TURN, mid, present=True, base=None)
    assert e.value.current["id"] == head and e.value.current["note"] == NOTE and e.value.current["quote"] == QUOTE
    assert e.value.current["decided_by_email"] == EMAIL and e.value.current["present"] is False


@requires_scratch
async def test_a_caller_who_revealed_the_answer_gets_the_label_too(lab_scratch, main_scratch):
    mid, head = await _cell_with_a_label(lab_scratch, main_scratch)
    assert (await labels.history(BLIND, fx.ORG_A, TURN, mid))["hidden"] is True
    async with lab_scratch.begin() as conn:                 # what the reveal route writes: a `reveal` event by the caller
        await conn.execute(text("INSERT INTO tracelab.annotation_events (organization_id, turn_id, action, actor_id) "
                                "VALUES (CAST(:o AS uuid), :t, 'reveal', CAST(:a AS uuid))"),
                           {"o": fx.ORG_A, "t": TURN, "a": str(BLIND.user_id)})
    seen = await labels.history(BLIND, fx.ORG_A, TURN, mid)
    assert seen["hidden"] is False and [c["id"] for c in seen["chain"]] == [head]


@requires_scratch
async def test_the_platform_owner_always_gets_the_label_and_its_history(lab_scratch, main_scratch):
    mid, head = await _cell_with_a_label(lab_scratch, main_scratch)
    seen = await labels.history(fx.owner(), None, TURN, mid)
    assert seen["hidden"] is False and seen["chain"][0]["note"] == NOTE and seen["chain"][0]["quote"] == QUOTE
    with pytest.raises(Stale) as e:
        await labels.set_label(fx.owner(), None, TURN, mid, present=True, base=None)
    assert e.value.current["id"] == head and e.value.current["note"] == NOTE
    assert e.value.current["decided_by_email"] == EMAIL


@requires_scratch
async def test_a_stale_409_with_no_head_still_carries_none(lab_scratch, main_scratch):
    """A base was named but the cell has no label at all: nothing to name, so nothing to hide."""
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    with pytest.raises(Stale) as e:
        await labels.set_label(BLIND, fx.ORG_A, TURN, mid, present=True, base=str(uuid.uuid4()))
    assert e.value.current is None
