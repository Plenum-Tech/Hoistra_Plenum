"""Fix round 1 for the company proposals (G0 2.4: every read is scoped to the acting company).

1. An accepted or rejected proposal is an ordinary platform-wide mode once the platform edits or merges it, so the
   proposer's own queue (`list_proposals`) must scrub its answer ids and note ids to the company, exactly as
   `list_modes` and `get_mode` do.
2. A mode a company proposed keeps who proposed it. Another company reading that mode through `list_modes` or
   `get_mode` must not see the proposing company's id, the proposer's identity or the platform decider's.

The first block is pure; the rest needs the scratch database."""
from __future__ import annotations

import pytest

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import taxonomy

DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}
HIDDEN = ("proposer_organization_id", "created_by", "created_by_email", "decided_by", "decided_by_email")
MODE = {"id": "m1", "proposer_organization_id": fx.ORG_A, "created_by": "u-1", "created_by_email": "a@a.test",
        "decided_by": "u-2", "decided_by_email": "staff@plenum.test", "name": "counts_cancelled_jobs_as_open"}


# ── pure: who may read a proposal's identity fields ──────────────────────────────────

def test_the_platform_owner_and_the_proposing_company_read_the_identity_fields():
    assert taxonomy._proposer_visible(MODE, True, None) is True
    assert taxonomy._proposer_visible(MODE, False, fx.ORG_A) is True
    assert taxonomy._hide_proposer(MODE, True) == MODE


@pytest.mark.parametrize("org", [fx.ORG_B, None, ""])
def test_anyone_else_is_refused_the_identity_fields(org):
    assert taxonomy._proposer_visible(MODE, False, org) is False


def test_hiding_blanks_exactly_the_identity_fields_and_leaves_the_input_alone():
    out = taxonomy._hide_proposer(MODE, False)
    assert {k: out[k] for k in HIDDEN} == dict.fromkeys(HIDDEN)
    assert out["name"] == MODE["name"] and out["id"] == MODE["id"]
    assert MODE["proposer_organization_id"] == fx.ORG_A and MODE["created_by_email"] == "a@a.test"


def test_a_mode_the_platform_made_has_nothing_to_hide():
    own = dict(MODE, proposer_organization_id=None)
    assert taxonomy._proposer_visible(own, False, fx.ORG_B) is True
    assert taxonomy._hide_proposer(own, True) == own


# ── scratch: the proposer's queue ────────────────────────────────────────────────────

async def _turn(main, org, n, prefix="x"):
    t = f"turn-{prefix}{org[:1]}{n:014d}"
    async with main.begin() as conn:
        await fx.add_turn(conn, t, org)
    return t


async def _note(lab, main, org=fx.ORG_A, n=0, prefix="p"):
    t = await _turn(main, org, n, prefix)
    async with lab.begin() as conn:
        return t, await fx.add_note(conn, t, org)


async def _propose(lab, main, org=fx.ORG_A, name="counts_cancelled_jobs_as_open", n=0):
    _t, note = await _note(lab, main, org, n)
    return (await taxonomy.create(fx.admin(org), dict(DRAFT, name=name), created_from=[note], org=org))["mode"]


@requires_scratch
@pytest.mark.parametrize("status", ["accepted", "all"])
async def test_the_proposers_queue_lists_only_its_own_answers_once_the_platform_edits_the_mode(
        lab_scratch, main_scratch, status):
    mode = await _propose(lab_scratch, main_scratch)
    accepted = (await taxonomy.accept_proposal(fx.owner(), mode["id"]))["mode"]
    a1, a2 = await _turn(main_scratch, fx.ORG_A, 1), await _turn(main_scratch, fx.ORG_A, 2)
    b1, b2 = await _turn(main_scratch, fx.ORG_B, 1), await _turn(main_scratch, fx.ORG_B, 2)
    await taxonomy.edit(fx.owner(), mode["id"], {"positive_turn_ids": [a1, b1], "close_negative_turn_ids": [b2, a2]},
                        base_version=accepted["version"])
    for who, org in ((fx.admin(), fx.ORG_A), (fx.admin(), None), (fx.acting_superadmin(), fx.ORG_A)):
        row = next(p for p in await taxonomy.list_proposals(who, org, status=status) if p["id"] == mode["id"])
        assert row["positive_turn_ids"] == [a1] and row["close_negative_turn_ids"] == [a2]
        assert row["positive_count"] == 2 and row["close_negative_count"] == 2
    full = next(p for p in await taxonomy.list_proposals(fx.owner(), None, status=status) if p["id"] == mode["id"])
    assert full["positive_turn_ids"] == [a1, b1] and full["close_negative_turn_ids"] == [b2, a2]


@requires_scratch
async def test_the_proposers_queue_lists_only_its_own_notes_once_the_platform_merges_the_mode(lab_scratch, main_scratch):
    a = await _propose(lab_scratch, main_scratch, fx.ORG_A, "counts_cancelled_jobs_as_open")
    b = await _propose(lab_scratch, main_scratch, fx.ORG_B, "includes_cancelled_jobs", n=1)
    a_live = (await taxonomy.accept_proposal(fx.owner(), a["id"]))["mode"]
    b_live = (await taxonomy.accept_proposal(fx.owner(), b["id"]))["mode"]
    await taxonomy.merge(fx.owner(), [b["id"]], into=a["id"], new=None,
                         base_versions={a["id"]: a_live["version"], b["id"]: b_live["version"]})
    row = next(p for p in await taxonomy.list_proposals(fx.admin(), fx.ORG_A, status="accepted") if p["id"] == a["id"])
    assert row["created_from"] == a["created_from"] and row["created_from_count"] == 2
    assert [n["annotation_id"] for n in row["notes"]] == a["created_from"]
    full = next(p for p in await taxonomy.list_proposals(fx.owner(), None, status="accepted") if p["id"] == a["id"])
    assert set(full["created_from"]) == set(a["created_from"]) | set(b["created_from"])


@requires_scratch
async def test_a_pending_proposal_still_lists_its_own_notes_and_counts(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    row = (await taxonomy.list_proposals(fx.admin(), fx.ORG_A))[0]
    assert row["created_from"] == mode["created_from"] and row["created_from_count"] == 1
    assert row["positive_turn_ids"] == [] and row["proposer_organization_id"] == fx.ORG_A


# ── scratch: another company reads the accepted mode ─────────────────────────────────

async def _accepted_proposal_of_company_a(lab, main):
    mode = await _propose(lab, main)
    await taxonomy.accept_proposal(fx.owner(), mode["id"])
    return mode["id"]


@requires_scratch
async def test_another_company_does_not_see_who_proposed_or_decided_a_mode_in_the_list(lab_scratch, main_scratch):
    mid = await _accepted_proposal_of_company_a(lab_scratch, main_scratch)
    seen = next(m for m in await taxonomy.list_modes(fx.admin(fx.ORG_B), fx.ORG_B) if m["id"] == mid)
    assert {k: seen[k] for k in HIDDEN} == dict.fromkeys(HIDDEN)
    assert seen["name"] == DRAFT["name"] and seen["status"] == "candidate"
    acting_b = next(m for m in await taxonomy.list_modes(fx.acting_superadmin(), fx.ORG_B) if m["id"] == mid)
    assert acting_b["proposer_organization_id"] is None and acting_b["created_by_email"] is None


@requires_scratch
async def test_the_proposing_company_and_the_platform_still_see_them_in_the_list(lab_scratch, main_scratch):
    mid = await _accepted_proposal_of_company_a(lab_scratch, main_scratch)
    for who, org in ((fx.admin(), fx.ORG_A), (fx.owner(), None)):
        seen = next(m for m in await taxonomy.list_modes(who, org) if m["id"] == mid)
        assert seen["proposer_organization_id"] == fx.ORG_A and seen["created_by_email"] == fx.admin().email
        assert seen["decided_by_email"] == fx.owner().email


@requires_scratch
async def test_another_company_does_not_see_who_proposed_or_decided_a_mode_in_the_drawer(lab_scratch, main_scratch):
    mid = await _accepted_proposal_of_company_a(lab_scratch, main_scratch)
    seen = await taxonomy.get_mode(fx.admin(fx.ORG_B), fx.ORG_B, mid)
    assert {k: seen["mode"][k] for k in HIDDEN} == dict.fromkeys(HIDDEN)
    actions = {e["action"]: e["actor_email"] for e in seen["events"]}
    assert actions == {"propose": None, "accept": None}
    mine = await taxonomy.get_mode(fx.admin(), fx.ORG_A, mid)
    assert mine["mode"]["proposer_organization_id"] == fx.ORG_A and mine["mode"]["created_by_email"] == fx.admin().email
    assert {e["action"]: e["actor_email"] for e in mine["events"]} == {"propose": fx.admin().email,
                                                                       "accept": fx.owner().email}
    full = await taxonomy.get_mode(fx.owner(), None, mid)
    assert full["mode"]["proposer_organization_id"] == fx.ORG_A and full["mode"]["decided_by_email"] == fx.owner().email


@requires_scratch
async def test_a_mode_the_platform_made_keeps_showing_company_callers_what_it_always_showed(lab_scratch, main_scratch):
    _t, note = await _note(lab_scratch, main_scratch)
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=[note]))["mode"]
    seen = await taxonomy.get_mode(fx.admin(fx.ORG_B), fx.ORG_B, mode["id"])
    assert seen["mode"]["proposer_organization_id"] is None
    assert seen["mode"]["created_by_email"] == fx.owner().email
    assert [e["actor_email"] for e in seen["events"]] == [fx.owner().email]
