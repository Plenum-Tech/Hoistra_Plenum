"""Pre-flight refusals (spec §7.6): an unpriced model, an estimate above the cap, a cap above the run limit and the
monthly cap, all before a job exists; only the platform team starts, resumes, pauses or reads a run; every refusal has
a plain sentence. The SQL side (dispatch stops at the cap, a resume re-reads the stored items) is in
test_trace_lab_proposal_runs_resume_and_cap_sql.py. No database, no network."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import trace_lab_m4_fixtures as fx
from trace_lab import jobs, proposals, spend
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound

NOW = datetime.now(timezone.utc)
PKG = Path(__file__).resolve().parents[1] / "trace_lab"


def _caps(**kw):
    base = dict(price_known=True, expected_usd=1.0, usd_cap=5.0, run_max=25.0, month_spent=0.0, month_cap=100.0)
    return dict(base, **kw)


def test_an_unpriced_model_is_refused():
    with pytest.raises(Invalid) as e:
        proposals.check_caps(**_caps(price_known=False, expected_usd=0.0))
    assert e.value.reason == "price_unknown" and e.value.status == 422


def test_an_estimate_above_the_cap_is_refused():
    with pytest.raises(Invalid) as e:
        proposals.check_caps(**_caps(expected_usd=2.0, usd_cap=1.0))
    assert e.value.reason == "estimate_exceeds_cap" and e.value.extra == {"expected_usd": 2.0, "usd_cap": 1.0}


def test_a_cap_above_the_run_limit_is_refused():
    with pytest.raises(Invalid) as e:
        proposals.check_caps(**_caps(usd_cap=30.0))
    assert e.value.reason == "cap_above_limit" and e.value.extra == {"run_cap_max": 25.0}


def test_the_monthly_cap_is_a_conflict():
    with pytest.raises(Conflict) as e:
        proposals.check_caps(**_caps(usd_cap=5.0, month_spent=96.0))
    assert e.value.reason == "monthly_cap" and e.value.status == 409
    assert e.value.extra == {"month_spent": 96.0, "month_cap": 100.0}


def test_the_refusals_come_in_the_specs_order_and_a_cap_exactly_at_a_limit_passes():
    with pytest.raises(Invalid) as e:           # unpriced beats everything else
        proposals.check_caps(**_caps(price_known=False, expected_usd=99.0, usd_cap=1.0))
    assert e.value.reason == "price_unknown"
    with pytest.raises(Invalid) as e:           # estimate above the cap beats a cap above the limit
        proposals.check_caps(**_caps(expected_usd=40.0, usd_cap=30.0))
    assert e.value.reason == "estimate_exceeds_cap"
    proposals.check_caps(**_caps(expected_usd=25.0, usd_cap=25.0, run_max=25.0, month_spent=75.0, month_cap=100.0))


# ── start refuses before a job exists ────────────────────────────────────────────────────

def _plan_returning(**over):
    plan = {"items": [{"turn_id": "t1", "mode_ids": ["m1"]}], "params": {}, "model": "no-such-model",
            "prompt_version": "c1", "prompt_hash": "abc", "expected_usd": 0.0, "worst_usd": 0.0,
            "price_known": False, "canaries": 0, "mode_id": None, "organization_id": None}
    plan.update(over)

    async def plan_fn(**kw):
        return plan
    return plan_fn


@pytest.fixture
def recorded(monkeypatch):
    created: list[dict] = []

    async def spent(conn=None):
        return 0.0

    async def create(**kw):
        created.append(kw)
        raise AssertionError("no job may be created by a refused start")

    monkeypatch.setattr(spend, "proposals_month_spent", spent)
    monkeypatch.setattr(jobs, "create", create)
    return created


async def _start(**kw):
    args = dict(kind="cells", mode_id=None, since=NOW - timedelta(days=1), until=NOW, usd_cap=1.0)
    return await proposals.start_run(fx.owner(), **dict(args, **kw))


async def test_start_refuses_before_creating_a_job(monkeypatch, recorded):
    monkeypatch.setattr(proposals, "_plan", _plan_returning())
    with pytest.raises(Invalid) as e:
        await _start()
    assert e.value.reason == "price_unknown" and recorded == []


async def test_start_refuses_an_estimate_above_the_cap_and_a_cap_above_the_limit(monkeypatch, recorded):
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, expected_usd=2.0))
    with pytest.raises(Invalid) as e:
        await _start(usd_cap=1.0)
    assert e.value.reason == "estimate_exceeds_cap"
    with pytest.raises(Invalid) as e:
        await _start(usd_cap=26.0)
    assert e.value.reason == "cap_above_limit" and recorded == []
    monkeypatch.setenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", "3")           # the limit is the environment's
    with pytest.raises(Invalid) as e:
        await _start(usd_cap=4.0)
    assert e.value.reason == "cap_above_limit" and e.value.extra == {"run_cap_max": 3.0}


async def test_start_refuses_when_the_month_would_pass_its_cap(monkeypatch, recorded):
    async def spent(conn=None):
        return 98.5

    monkeypatch.setattr(spend, "proposals_month_spent", spent)
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, expected_usd=0.5))
    with pytest.raises(Conflict) as e:
        await _start(usd_cap=2.0)
    assert e.value.reason == "monthly_cap" and recorded == []


@pytest.mark.parametrize("cap", [0, -1, 0.0, float("nan"), float("inf"), "abc", None, True])
async def test_a_cap_must_be_a_positive_finite_number(monkeypatch, recorded, cap):
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, expected_usd=0.0))
    with pytest.raises(Invalid) as e:
        await _start(usd_cap=cap)
    assert e.value.reason == "bad_cap" and recorded == []


async def test_a_run_with_nothing_to_propose_creates_no_job(monkeypatch, recorded):
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, items=[]))
    with pytest.raises(Invalid) as e:
        await _start()
    assert e.value.reason == "nothing_to_propose"
    only_canaries = [{"turn_id": "t1", "mode_ids": ["m1"], "canary": True}]
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, items=only_canaries))
    with pytest.raises(Invalid) as e:
        await _start()
    assert e.value.reason == "nothing_to_propose" and recorded == []


async def test_a_second_open_run_of_a_kind_is_a_conflict_not_a_second_job(monkeypatch):
    seen = []

    async def spent(conn=None):
        return 0.0

    async def create(**kw):
        seen.append(kw)
        raise jobs.JobConflict(kw["scope_key"])

    monkeypatch.setattr(spend, "proposals_month_spent", spent)
    monkeypatch.setattr(jobs, "create", create)
    monkeypatch.setattr(proposals, "_plan", _plan_returning(price_known=True, expected_usd=0.4, model="claude-sonnet-5"))
    with pytest.raises(Conflict) as e:
        await _start(usd_cap=1.0)
    assert e.value.reason == "run_in_progress"
    (call,) = seen
    assert call["kind"] == "proposal_cells" and call["scope_key"] == "proposal_run:cells"
    assert str(call["usd_cap"]) == "1.0" and str(call["usd_estimate"]) == "0.4" and call["total"] == 1
    assert call["params"]["run_kind"] == "cells" and "items" not in call["params"]       # the items live on the run row
    assert call["actor_id"] == fx.OWNER_ID and call["actor_email"] == "owner@plenum.test"


# ── who may run the proposer ─────────────────────────────────────────────────────────────

CALLS = [
    ("estimate", lambda p: proposals.estimate(p, kind="cells", mode_id=None, since=NOW - timedelta(days=1), until=NOW)),
    ("start_run", lambda p: proposals.start_run(p, kind="cells", mode_id=None, since=NOW - timedelta(days=1),
                                                until=NOW, usd_cap=1.0)),
    ("resume_run", lambda p: proposals.resume_run(p, "11111111-1111-4111-8111-111111111111")),
    ("pause_run", lambda p: proposals.pause_run(p, "11111111-1111-4111-8111-111111111111")),
    ("list_runs", lambda p: proposals.list_runs(p)),
    ("get_run", lambda p: proposals.get_run(p, "11111111-1111-4111-8111-111111111111")),
]


@pytest.mark.parametrize("name,call", CALLS, ids=[c[0] for c in CALLS])
@pytest.mark.parametrize("who", [fx.admin(), object(), None], ids=["company_admin", "no_role", "nobody"])
async def test_only_the_platform_team_runs_the_proposer_and_it_is_checked_before_any_read(name, call, who):
    with pytest.raises(Forbidden) as e:
        await call(who)                       # no engine is installed: reaching one would raise something else
    assert e.value.reason == "platform_only" and e.value.status == 403


async def test_an_unknown_kind_or_company_is_refused_before_any_read():
    with pytest.raises(Invalid) as e:
        await proposals.estimate(fx.owner(), kind="everything", mode_id=None, since=NOW - timedelta(days=1), until=NOW)
    assert e.value.reason == "unknown_kind"
    with pytest.raises(Invalid) as e:
        await proposals.start_run(fx.owner(), kind="cells", mode_id=None, since=NOW - timedelta(days=1), until=NOW,
                                  usd_cap=1.0, filter_org="acme")
    assert e.value.reason == "bad_company"


async def test_a_run_id_that_is_not_a_uuid_is_not_found_before_any_read():
    for fn in (proposals.get_run, proposals.pause_run, proposals.resume_run):
        with pytest.raises(NotFound) as e:
            await fn(fx.owner(), "not-a-uuid")
        assert e.value.reason == "run_not_found"


# ── the estimate a person reads ──────────────────────────────────────────────────────────

async def test_the_estimate_counts_real_items_apart_from_canaries_and_shows_the_limits(monkeypatch):
    async def spent(conn=None):
        return 12.34567

    seen = {}

    async def plan(**kw):
        seen.update(kw)
        return {"items": [{"turn_id": "t1"}, {"turn_id": "t2"}, {"turn_id": "t3", "canary": True}], "model": "claude-sonnet-5",
                "price_known": True, "canaries": 1, "expected_usd": 0.4321, "worst_usd": 2.5}

    monkeypatch.setattr(spend, "proposals_month_spent", spent)
    monkeypatch.setattr(proposals, "_plan", plan)
    monkeypatch.delenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", raising=False)
    monkeypatch.delenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", raising=False)
    out = await proposals.estimate(fx.owner(), kind="depth_scan", mode_id="m-1", since=NOW - timedelta(days=1), until=NOW,
                                   filter_org="11111111-1111-4111-8111-111111111111", limit=50, include_candidates=True)
    assert out == {"model": "claude-sonnet-5", "price_known": True, "items": 2, "canaries": 1, "expected_usd": 0.4321,
                   "worst_usd": 2.5, "run_cap_max": 25.0, "month_spent": 12.3457, "month_cap": 100.0}
    assert seen["seed"] == "estimate" and seen["scope_org"] == "11111111-1111-4111-8111-111111111111"
    assert seen["limit"] == 50 and seen["include_candidates"] is True and seen["kind"] == "depth_scan"


# ── payloads ─────────────────────────────────────────────────────────────────────────────

def test_a_run_payload_hides_the_stored_items_and_stringifies_ids():
    import uuid
    rid, jid = uuid.uuid4(), uuid.uuid4()
    row = {"id": rid, "job_id": jid, "kind": "cells", "organization_id": None, "mode_id": None, "model": "claude-sonnet-5",
           "prompt_version": "c1", "prompt_hash": "abc", "params": {"limit": 5, "items": [{"turn_id": "t1"}]},
           "estimate_usd": 0.5, "worst_usd": 1, "items_planned": 1, "canary_total": 2, "canary_agree": 1,
           "created_by_email": "owner@plenum.test", "created_at": NOW}
    out = proposals.run_payload(row)
    assert out["id"] == str(rid) and out["job_id"] == str(jid) and out["params"] == {"limit": 5}
    assert out["estimate_usd"] == 0.5 and out["worst_usd"] == 1.0 and out["created_at"] == NOW.isoformat()
    assert "created_by" not in out                                       # the person's id stays on the row


def test_a_job_payload_reads_numbers_as_numbers():
    from decimal import Decimal
    out = proposals.job_payload({"id": "j", "status": "paused", "progress": '{"written": 3}', "usd_cap": Decimal("1.5"),
                                 "usd_spent": Decimal("0.25"), "total": 7, "done": 3, "error": None, "finished_at": None})
    assert out == {"id": "j", "status": "paused", "progress": {"written": 3}, "usd_cap": 1.5, "usd_spent": 0.25,
                   "total": 7, "done": 3, "error": None, "finished_at": None}


# ── the sentences ────────────────────────────────────────────────────────────────────────

def _reasons(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    return set(re.findall(r"\b_(?:invalid|not_found|conflict|forbidden)\(\s*\"([a-z_]+)\"", src))


def test_every_refusal_this_module_can_raise_has_a_sentence():
    used = _reasons(PKG / "proposals.py")
    assert {"price_unknown", "estimate_exceeds_cap", "cap_above_limit", "monthly_cap", "platform_only"} <= used
    assert used <= set(proposals.SENTENCES), sorted(used - set(proposals.SENTENCES))


def test_no_sentence_names_a_table_a_service_or_an_id_and_each_reads_as_a_sentence():
    for reason, sentence in proposals.SENTENCES.items():
        assert re.match(r"^[a-z][a-z0-9_]*$", reason)
        assert sentence and sentence[0].isupper() and sentence.rstrip()[-1] in ".?", (reason, sentence)
        low = sentence.lower()
        for bad in ("tracelab", "plenum", "svc-", "_id", "uuid", "sql", "postgres", "constraint", "job "):
            assert bad not in low, (reason, sentence)


def test_the_helpers_build_the_review_errors_with_their_status_and_extras():
    for helper, cls, status in ((proposals._invalid, Invalid, 422), (proposals._not_found, NotFound, 404),
                                (proposals._conflict, Conflict, 409), (proposals._forbidden, Forbidden, 403)):
        reason = next(iter(proposals.SENTENCES))
        exc = helper(reason, mode_id="m-1")
        assert isinstance(exc, cls) and exc.status == status and exc.reason == reason
        assert exc.error == proposals.SENTENCES[reason] and exc.extra == {"mode_id": "m-1"}
    with pytest.raises(KeyError):
        proposals._invalid("a_reason_with_no_sentence")
