"""Completeness counts reviewed answers x confirmed/frozen modes only; matrix shares are sample fractions; fair
rates come from uniform batches only; rows stay hidden until the caller has judged (spec §6.3, §6.4, §2.3).
Needs the scratch database: the chat's turns are read through the read-only main engine, Trace Lab's rows are in the
lab database."""
from __future__ import annotations

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels

pytestmark = requires_scratch


async def test_labels_on_unreviewed_answers_add_no_rows(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-i000000000000001")
        await fx.add_turn(conn, "turn-i000000000000002")
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-i000000000000001", verdict="right")
        m1 = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        await fx.add_mode(conn, "invents_asset_codes", status="frozen")
        await fx.add_mode(conn, "candidate_only_mode")
        await fx.add_label(conn, "turn-i000000000000001", m1, present=True)
        await fx.add_label(conn, "turn-i000000000000002", m1, present=True)
    out = await labels.completeness(fx.admin(), fx.ORG_A)
    assert (out["reviewed_turns"], out["modes"], out["required"], out["done"], out["missing"]) == (1, 2, 2, 1, 1)
    assert out["missing_preview"] == [{"turn_id": "turn-i000000000000001",
                                       "question": "How many work orders are open at Bishopsgate?", "missing": 1}]


async def test_shares_are_sample_fractions_and_fair_rates_use_uniform_batches_only(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-i100000000000001")
        await fx.add_turn(conn, "turn-i100000000000002")
    async with lab_scratch.begin() as conn:
        for t, strategy, step in (("turn-i100000000000001", "uniform", "initial_uniform"),
                                  ("turn-i100000000000002", "dimension", "dimension")):
            await fx.add_note(conn, t)
            await fx.add_batch_item(conn, t, strategy=strategy, plan_step=step)
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        await fx.add_label(conn, "turn-i100000000000001", m, present=True)
        await fx.add_label(conn, "turn-i100000000000002", m, present=True)
    out = await labels.matrix(fx.admin(), fx.ORG_A)
    assert out["sample_fractions"][m] == {"present": 2, "decided": 2, "fraction": 1.0, "kind": "sample_fraction"}
    fair = out["fair_sample"][m]
    assert (fair["present"], fair["n"], fair["kind"]) == (1, 1, "uniform_batches_only")
    assert fair["ci_low"] is not None and fair["ci_high"] <= 1.0
    assert out["totals"]["reviewed_turns"] == 2 and out["totals"]["missing"] == 0
    assert (await labels.matrix(fx.admin(), fx.ORG_A, state="missing"))["rows"] == []
    row = next(r for r in out["rows"] if r["turn_id"] == "turn-i100000000000001")
    assert row["verdicts"] == {"right": 0, "wrong": 1, "mine": "wrong"} and row["cells"][m]["state"] == "present"
    assert row["batch"]["plan_step"] == "initial_uniform"


async def test_rows_stay_hidden_until_the_caller_has_judged(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-i200000000000001")
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-i200000000000001")
        await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    other = await labels.matrix(fx.admin(n=2), fx.ORG_A)
    assert other["rows"] == [] and other["hidden_rows"] == 1
    owner = await labels.matrix(fx.owner(), None)
    assert [r["turn_id"] for r in owner["rows"]] == ["turn-i200000000000001"]
