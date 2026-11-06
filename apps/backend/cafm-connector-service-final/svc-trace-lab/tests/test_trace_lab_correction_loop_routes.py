"""The correction loop's routes are mounted, the automatic sync starts and stops cleanly, and the rules that need no
database hold: what counts as derived, and what counts as the model's own answer (3 Nov 2026)."""
from __future__ import annotations

import asyncio

from trace_lab import app as app_module
from trace_lab import autosync, loop

PATHS = {
    ("GET", "/lab/proposals"), ("POST", "/lab/proposals/{kind}/{subject_id}"), ("GET", "/lab/spec"),
    ("GET", "/lab/spec/draft"), ("POST", "/lab/spec"), ("GET", "/lab/spec.md"), ("GET", "/lab/corrections"),
    ("POST", "/lab/corrections"), ("POST", "/lab/modes/{lineage_id}/regression"), ("POST", "/lab/runs/{code}/rerun"),
    ("GET", "/lab/runs/{code}/compare"), ("GET", "/lab/loop"), ("GET", "/lab/sync-status"),
    ("POST", "/lab/frontend-events"),
}


def test_every_loop_route_is_in_the_service_routers():
    from trace_lab import routes_loop

    assert routes_loop.router in app_module.ROUTERS
    have = {(m, r.path) for r in routes_loop.router.routes for m in r.methods}
    assert PATHS <= have, PATHS - have


def test_derived_means_a_rule_in_force_and_the_records_read():
    codes = {"S-1", "S-2"}
    assert loop.derivation({"spec_rule": "s-1", "data": "SELECT floors FROM buildings"}, codes)["derived"]
    assert loop.derivation({"spec_rule": "S-1", "reference": "SELECT 1", "type": "sql"}, codes)["derived"]
    no_rule = loop.derivation({"data": "SELECT 1"}, codes)
    assert not no_rule["derived"] and "names no rule" in no_rule["problems"][0]
    assert not loop.derivation({"spec_rule": "S-9", "data": "x"}, codes)["derived"]
    assert not loop.derivation({"spec_rule": "S-1"}, codes)["derived"]
    assert not loop.derivation(None, codes)["derived"]


def test_a_copy_of_the_models_answer_is_recognised_and_a_real_expected_answer_is_not():
    model = "Bishopsgate Tower has **18 floors** and 13 open work orders; no vendor is blocked there at the moment."
    assert loop.copies("Bishopsgate Tower has 18 floors and 13 open work orders; no vendor is blocked there at the moment",
                       [model])
    assert loop.copies("bishopsgate tower has 18 floors and 13 open work orders, no vendor is blocked there", [model])
    assert not loop.copies("Bishopsgate Tower has 22 floors.", [model])
    assert not loop.copies("22", ["22 floors at Bishopsgate Tower, by the buildings register."])


def test_the_automatic_sync_starts_once_and_stops(monkeypatch):
    calls = []

    async def fake_tick():
        calls.append(1)
        return {}

    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_S", "0.05")
    monkeypatch.setattr(autosync, "FIRST_DELAY_S", 0.0)
    monkeypatch.setattr(autosync, "tick", fake_tick)
    monkeypatch.setattr(autosync.db, "STATE", type("S", (), {"status": "ready"})())

    async def go():
        assert autosync.start() is True
        assert autosync.start() is False, "never two loops"
        await asyncio.sleep(0.18)
        await autosync.stop()
        n = len(calls)
        await asyncio.sleep(0.1)
        return n, len(calls)

    n, later = asyncio.run(go())
    assert n >= 2 and later == n, "it ran on its own and stopped when told"
    assert autosync.status()["enabled"] is True


def test_the_automatic_sync_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_S", "0")

    async def go():
        return autosync.start()

    assert asyncio.run(go()) is False and autosync.status()["enabled"] is False


def test_a_failed_round_is_recorded_and_raises_nothing(monkeypatch):
    from trace_lab import runs

    async def boom(org):
        raise RuntimeError("database gone")

    monkeypatch.setattr(runs, "sync_live", boom)
    out = asyncio.run(autosync.tick())
    assert out["last_ok"] is False and "database gone" in out["last_error"]


# 4 Nov 2026, R35-1: the expected answer was a sentence with no `values`, so the whole sentence became one "name"
# item that no answer ever contains - the fixed answer read "Still failing". The checkable facts are its codes and dates.
EXPECTED_R35 = {"expected": "The LOLER thorough examination for lift B-301-LIFT-4471 (Bishopsgate Tower) was due on 18 Oct "
                            "2026 and is 17 days overdue. A work order is already raised for it: WO-B-301-4533 (Draft) - "
                            "progress that rather than raising a new one.", "evidence": {"spec_rule": "S-1", "data": "x"}}
FIXED = ("The LOLER thorough examination for Lift B-301-LIFT-4471 at Bishopsgate Tower was due on 18 October 2026 and is "
         "now 17 days overdue; a draft work order (WO-B-301-4533) has already been raised.")
BEFORE = ("Building - Bishopsgate Tower - LOLER lift inspection lapsed 17 days ago - LOLER Thorough Examination (lift "
          "B-301-LIFT-4471) expired 18 October 2026. There is no next LOLER inspection due.")


def test_a_sentence_expected_answer_is_checked_on_its_codes_and_dates():
    from trace_lab import grading

    items = loop._items(EXPECTED_R35)
    kinds = sorted((i["kind"], str(i["value"])) for i in items)
    assert ("code", "WO-B-301-4533") in kinds and ("code", "B-301-LIFT-4471") in kinds and ("date", "2026-10-18") in kinds
    assert grading.check_values(items, FIXED, [])["status"] == "all_found"
    assert grading.check_values(items, BEFORE, [])["status"] == "mismatch", "the old answer never named the work order"
    assert loop._verdict("mismatch", "all_found") == "fixed"
    # A rerun that ended with no answer (it timed out or failed) is not a wrong answer (6 Nov 2026: R42 read
    # "still failing" because its rerun ran past the time limit and saved nothing).
    assert loop._verdict("mismatch", "mismatch", answered=False) == "no_answer"
    assert loop._verdict("all_found", None, answered=False) == "not_rerun"


def test_listed_values_still_win():
    items = loop._items({"expected": "anything", "evidence": {"values": ["22"]}})
    assert [i["kind"] for i in items] == ["number"]
