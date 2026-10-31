"""Monitoring batches per company (Phase 8, decision A). No database, no model: the store functions are replaced.

A superadmin acting for a company opens that company's batch into review batches. The batch's company is checked
before anything is read or created, so one company's batch is never opened for another, and a platform-wide batch is
not opened from inside a company. A superadmin acting for no company (the platform view, org None) keeps what it had:
any batch. The SQL (real rows, evidence, jobs) is in test_trace_lab_monitor_per_company_sql.py."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trace_lab import monitor

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"
ACTOR = "cccccccc-0000-4000-8000-000000000003"
M1 = "11111111-0000-4000-8000-000000000001"
J1 = "22222222-0000-4000-8000-000000000001"
TURNS = ["t1", "t2", "t3"]


def _batch(org):
    snap = {"judge_id": J1, "judge_key": "wrong_period-v2", "mode_id": M1, "mode_name": "wrong_period", "kind": "llm",
            "evaluator_key": f"judge:{J1}", "threshold": 0.15}
    plan = {"random": TURNS, "risk_groups": {}, "to_judge": TURNS}
    return {"id": "b1", "organization_id": org, "period_label": "2026-W41", "seed": 7, "judges": [snap], "plan": plan,
            "window_start": "2026-10-05T00:00:00+00:00", "window_end": "2026-10-12T00:00:00+00:00"}


@pytest.fixture
def seen(monkeypatch):
    """Every store read and write a call makes, as ('name', args). Every turn is flagged; turn t3 belongs to ORG_B."""
    state = SimpleNamespace(batch=_batch(ORG_A), calls=[])
    calls = state.calls
    owner = {"t1": ORG_A, "t2": ORG_A, "t3": ORG_B}

    async def verdicts(ek, ids):
        calls.append(("verdicts", tuple(ids)))
        return {t: {"status": "decided", "failure_present": 1} for t in ids}

    async def turn_rows(ids):
        calls.append(("turn_rows", tuple(ids)))
        return {t: {"organization_id": owner[t], "session_id": "s-" + t} for t in ids}

    async def existing(batch_id, mode_id):
        calls.append(("existing", batch_id))
        return []

    async def load(batch_id):
        calls.append(("load", batch_id))
        return state.batch

    async def create_batch(principal, org, req):
        calls.append(("create", org, tuple(i["turn_id"] for i in req["items"])))
        return {"ok": True, "batch": {"id": "rb-" + org[:4]}, "items": []}

    monkeypatch.setattr(monitor, "_verdicts", verdicts)
    monkeypatch.setattr(monitor, "_turn_rows", turn_rows)
    monkeypatch.setattr(monitor, "_review_batches_for", existing)
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor.sampling, "create_batch", create_batch)
    return state


def _names(seen):
    return [c[0] for c in seen.calls]


async def test_a_company_opens_its_own_batch_and_only_its_own_answers_become_review_batches(seen):
    out = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, org=ORG_A)
    assert out == ["rb-aaaa"]
    assert [c for c in seen.calls if c[0] == "create"] == [("create", ORG_A, ("t1", "t2"))]     # t3 is another company's


@pytest.mark.parametrize("batch_org,asking", [(ORG_B, ORG_A), (None, ORG_A), (ORG_A, ORG_B)])
async def test_another_scopes_batch_is_not_found_and_nothing_is_read_or_made(seen, batch_org, asking):
    seen.batch = _batch(batch_org)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, org=asking)
    assert (e.value.status, e.value.reason) == (404, "not_found") and "monitoring batch" in e.value.message
    assert _names(seen) == ["load"]                                  # the company is checked before anything else


async def test_the_platform_view_opens_any_batch_as_before(seen):
    for org in (None, ORG_A):
        seen.batch = _batch(org)
        seen.calls.clear()
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None)
        assert "create" in _names(seen)
    seen.batch = _batch(None)
    seen.calls.clear()
    made = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, org=None)
    assert made == ["rb-aaaa", "rb-bbbb"]                            # a platform batch: one review batch per company


async def test_a_batch_that_is_handed_in_is_checked_too(seen):
    other = _batch(ORG_B)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=other, org=ORG_A)
    assert e.value.status == 404 and _names(seen) == []              # nothing was even loaded


async def test_the_worker_opens_a_company_batch_for_that_company_only(seen, monkeypatch):
    """A company batch run by the worker: a flagged answer that is not the company's (it should never be in the plan)
    is left out of the review batch, not filed under the wrong company."""
    seen.batch = _batch(ORG_A)

    async def propose(b, mode_id, ids):
        return []

    monkeypatch.setattr(monitor, "_propose_cases", propose)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    seen.batch["started_by"] = ACTOR
    await monitor._act_on_crossings(seen.batch, seen.batch["plan"], results)
    assert results["modes"][0]["review_batch_ids"] == ["rb-aaaa"]
    assert [c for c in seen.calls if c[0] == "create"] == [("create", ORG_A, ("t1", "t2"))]
