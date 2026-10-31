"""The senior's decision 3: a company admin PROPOSES a failure mode, the platform team decides. A proposal is stored as
`proposed` with the proposer's company, is not live (no grid, no labels, no judge), and only the platform team can accept
it (it becomes a candidate and its origin notes are filed) or reject it with a reason (the row is kept, never deleted).
An admin sees only their own company's proposals; the platform sees all. Needs the scratch database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, taxonomy
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound, Stale

pytestmark = requires_scratch
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}


async def _note(lab, main, org=fx.ORG_A, n=0, prefix="p"):
    t = f"turn-{prefix}{org[:1]}{n:014d}"
    async with main.begin() as conn:
        await fx.add_turn(conn, t, org)
    async with lab.begin() as conn:
        return t, await fx.add_note(conn, t, org)


async def _propose(lab, main, org=fx.ORG_A, name="counts_cancelled_jobs_as_open", n=0, who=None, **extra):
    _t, note = await _note(lab, main, org, n)
    return (await taxonomy.create(who or fx.admin(org), dict(DRAFT, name=name, **extra), created_from=[note], org=org))["mode"]


async def test_a_company_admin_who_creates_a_mode_proposes_it(lab_scratch, main_scratch):
    turn, note = await _note(lab_scratch, main_scratch)
    out = await taxonomy.create(fx.admin(), DRAFT, created_from=[note], org=fx.ORG_A)
    mode = out["mode"]
    assert mode["status"] == "proposed" and mode["proposer_organization_id"] == fx.ORG_A
    assert mode["created_from"] == [note] and out["labels_written"] == 0 and mode["judge_eligible"] is True
    async with lab_scratch.connect() as conn:
        labelled = (await conn.execute(text("SELECT count(*) FROM tracelab.labels"))).scalar()
        events = (await conn.execute(text("SELECT action, detail, actor_email FROM tracelab.mode_events"))).mappings().all()
    assert labelled == 0 and [e["action"] for e in events] == ["propose"]
    assert taxonomy.json_value(events[0]["detail"])["role"] == "company_admin" and events[0]["actor_email"] == fx.admin().email


async def test_the_company_defaults_to_the_admins_own_and_the_platform_owner_creates_a_candidate(lab_scratch, main_scratch):
    _t, note = await _note(lab_scratch, main_scratch)
    own = await taxonomy.create(fx.admin(), DRAFT, created_from=[note])                 # no org named: their own
    assert own["mode"]["status"] == "proposed" and own["mode"]["proposer_organization_id"] == fx.ORG_A
    _t2, note2 = await _note(lab_scratch, main_scratch, n=1)
    platform = await taxonomy.create(fx.owner(), dict(DRAFT, name="includes_cancelled_jobs"), created_from=[note2])
    assert platform["mode"]["status"] == "candidate" and platform["mode"]["proposer_organization_id"] is None
    assert platform["labels_written"] == 1


async def test_a_superadmin_acting_for_a_company_proposes_for_that_company(lab_scratch, main_scratch):
    _t, note = await _note(lab_scratch, main_scratch, fx.ORG_B)
    out = await taxonomy.create(fx.acting_superadmin(), DRAFT, created_from=[note], org=fx.ORG_B)
    assert out["mode"]["status"] == "proposed" and out["mode"]["proposer_organization_id"] == fx.ORG_B


async def test_a_proposal_may_cite_only_the_proposers_own_wrong_notes(lab_scratch, main_scratch):
    _t, mine = await _note(lab_scratch, main_scratch, fx.ORG_A)
    _t2, theirs = await _note(lab_scratch, main_scratch, fx.ORG_B)
    for cited in ([theirs], [mine, theirs]):
        with pytest.raises(Invalid) as e:
            await taxonomy.create(fx.admin(), DRAFT, created_from=cited, org=fx.ORG_A)
        assert e.value.reason == "not_a_wrong_note"
    assert (await taxonomy.list_proposals(fx.owner(), None, status="all")) == []


async def test_a_proposal_takes_no_platform_only_fields(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch, evaluator_type="judge", requirement_ids=["GEN-1"])
    assert mode["evaluator_type"] is None and mode["requirement_ids"] == []


async def test_a_plain_user_or_an_admin_without_a_company_cannot_propose(lab_scratch, main_scratch):
    _t, note = await _note(lab_scratch, main_scratch)
    user = fx.admin()
    user = type(user)(user_id=user.user_id, email=user.email, organization_id=user.organization_id, role="user",
                      can_ingest=False, building_ids=None)
    with pytest.raises(Forbidden) as e:
        await taxonomy.create(user, DRAFT, created_from=[note], org=fx.ORG_A)
    assert e.value.reason == "reviewers_only"
    nocompany = type(user)(user_id=uuid.uuid4(), email="x@y.test", organization_id=None, role="admin", can_ingest=False,
                           building_ids=None)
    with pytest.raises(Forbidden):
        await taxonomy.create(nocompany, DRAFT, created_from=[note])


async def test_a_proposal_holds_its_name_and_a_live_mode_blocks_it(lab_scratch, main_scratch):
    await _propose(lab_scratch, main_scratch)
    _t, note = await _note(lab_scratch, main_scratch, fx.ORG_B)
    with pytest.raises(Conflict) as e:
        await taxonomy.create(fx.admin(fx.ORG_B), DRAFT, created_from=[note], org=fx.ORG_B)
    assert e.value.reason == "name_taken"


async def test_a_proposed_mode_is_not_live(lab_scratch, main_scratch):
    turn, note = await _note(lab_scratch, main_scratch)
    mode = (await taxonomy.create(fx.admin(), DRAFT, created_from=[note], org=fx.ORG_A))["mode"]
    assert await taxonomy.list_modes(fx.owner(), None) == []
    assert await taxonomy.list_modes(fx.admin(), fx.ORG_A, include_retired=True) == []
    assert (await labels.cells_for_turn(fx.owner(), None, turn))["cells"] == []
    with pytest.raises(Conflict) as e:
        await labels.set_label(fx.owner(), None, turn, mode["id"], present=True, base=None)
    assert e.value.reason == "mode_not_live"
    for call in (taxonomy.edit(fx.owner(), mode["id"], {"boundary": "x"}, base_version=1),
                 taxonomy.rename(fx.owner(), mode["id"], "another_name_here", base_version=1),
                 taxonomy.retire(fx.owner(), mode["id"], reason="no", base_version=1)):
        with pytest.raises(Conflict) as c:
            await call
        assert c.value.reason == "mode_proposed"
    assert (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["mode"]["status"] == "proposed"


async def test_only_the_proposers_company_and_the_platform_can_open_a_proposal(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    assert await taxonomy.get_mode(fx.admin(fx.ORG_B), fx.ORG_B, mode["id"]) is None
    mine = await taxonomy.get_mode(fx.admin(), fx.ORG_A, mode["id"])
    assert mine["mode"]["status"] == "proposed" and [n["organization_id"] for n in mine["notes"]] == [fx.ORG_A]
    assert mine["contract"] is None                                  # a proposal has no contract to fill yet
    assert (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["mode"]["id"] == mode["id"]


async def test_an_admin_sees_only_their_companys_proposals_and_the_platform_sees_all(lab_scratch, main_scratch):
    a = await _propose(lab_scratch, main_scratch, fx.ORG_A, "counts_cancelled_jobs_as_open")
    b = await _propose(lab_scratch, main_scratch, fx.ORG_B, "includes_cancelled_jobs")
    ours = await taxonomy.list_proposals(fx.admin(), fx.ORG_A)
    assert [p["id"] for p in ours] == [a["id"]]
    theirs = await taxonomy.list_proposals(fx.admin(fx.ORG_B), fx.ORG_B)
    assert [p["id"] for p in theirs] == [b["id"]]
    everyone = await taxonomy.list_proposals(fx.owner(), None)
    assert {p["id"] for p in everyone} == {a["id"], b["id"]}
    acting = await taxonomy.list_proposals(fx.acting_superadmin(), fx.ORG_B)             # acting: that company only
    assert [p["id"] for p in acting] == [b["id"]]
    one = ours[0]
    assert one["status"] == "proposed" and one["proposer_organization_id"] == fx.ORG_A
    assert [n["annotation_id"] for n in one["notes"]] == a["created_from"] and one["notes"][0]["quote"]
    assert [p["id"] for p in await taxonomy.list_proposals(fx.admin(), None)] == [a["id"]]    # no company named: their own
    with pytest.raises(Forbidden) as e:
        await taxonomy.list_proposals(fx.admin(), fx.ORG_B)                              # never another company's
    assert e.value.reason == "reviewers_only"


async def test_the_list_filters_by_status_and_keeps_rejected_ones_visible_to_their_proposer(lab_scratch, main_scratch):
    a = await _propose(lab_scratch, main_scratch, fx.ORG_A, "counts_cancelled_jobs_as_open")
    b = await _propose(lab_scratch, main_scratch, fx.ORG_A, "includes_cancelled_jobs", n=1)
    await taxonomy.reject_proposal(fx.owner(), a["id"], "Too close to an existing mode.")
    await taxonomy.accept_proposal(fx.owner(), b["id"])
    assert [p["id"] for p in await taxonomy.list_proposals(fx.admin(), fx.ORG_A)] == []
    rejected = await taxonomy.list_proposals(fx.admin(), fx.ORG_A, status="rejected")
    assert [p["id"] for p in rejected] == [a["id"]] and rejected[0]["rejected_reason"] == "Too close to an existing mode."
    accepted = await taxonomy.list_proposals(fx.admin(), fx.ORG_A, status="accepted")
    assert [p["id"] for p in accepted] == [b["id"]] and accepted[0]["status"] == "candidate"
    both = await taxonomy.list_proposals(fx.admin(), fx.ORG_A, status="all")
    assert {p["id"] for p in both} == {a["id"], b["id"]}
    with pytest.raises(Invalid) as e:
        await taxonomy.list_proposals(fx.owner(), None, status="bogus")
    assert e.value.reason == "unknown_status"


async def test_the_platform_accepts_a_proposal_and_its_notes_are_filed(lab_scratch, main_scratch):
    turn, note = await _note(lab_scratch, main_scratch)
    mode = (await taxonomy.create(fx.admin(), DRAFT, created_from=[note], org=fx.ORG_A))["mode"]
    out = await taxonomy.accept_proposal(fx.owner(), mode["id"], base_version=mode["version"])
    accepted = out["mode"]
    assert accepted["status"] == "candidate" and out["labels_written"] == 1
    assert accepted["proposer_organization_id"] == fx.ORG_A and accepted["version"] == mode["version"] + 1
    assert accepted["decided_by"] == str(fx.owner().user_id) and accepted["decided_at"]
    assert (await labels.cells_for_turn(fx.owner(), None, turn))["cells"][0]["state"] == "present"
    detail = (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["events"][0]
    assert detail["action"] == "accept" and detail["detail"]["status"] == ["proposed", "candidate"]
    assert [m["id"] for m in await taxonomy.list_modes(fx.owner(), None)] == [mode["id"]]
    with pytest.raises(Conflict) as e:
        await taxonomy.accept_proposal(fx.owner(), mode["id"])
    assert e.value.reason == "not_a_proposal"


async def test_the_platform_may_tidy_a_proposal_while_accepting_it(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    out = await taxonomy.accept_proposal(fx.owner(), mode["id"], edits={
        "name": "counts_cancelled_work_orders", "boundary": "Not on hold.", "level": "domain", "agent": "compliance"})
    m = out["mode"]
    assert (m["name"], m["boundary"], m["level"], m["agent"]) == ("counts_cancelled_work_orders", "Not on hold.",
                                                                  "domain", "compliance")
    events = (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["events"][0]["detail"]
    assert events["changes"]["name"] == [DRAFT["name"], "counts_cancelled_work_orders"]


async def test_an_edit_that_breaks_the_draft_or_takes_a_live_name_stops_the_accept(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        await fx.add_mode(conn, "includes_cancelled_jobs")
    with pytest.raises(Conflict) as e:
        await taxonomy.accept_proposal(fx.owner(), mode["id"], edits={"name": "includes_cancelled_jobs"})
    assert e.value.reason == "name_taken"
    for bad, reason in (({"name": "Bad Name"}, "bad_name"), ({"level": "x"}, "unknown_level"),
                        ({"agent": "x"}, "unknown_agent"), ({"definition": "short"}, "bad_definition")):
        with pytest.raises(Invalid) as i:
            await taxonomy.accept_proposal(fx.owner(), mode["id"], edits=bad)
        assert i.value.reason == reason
    assert (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["mode"]["status"] == "proposed"


async def test_accepting_with_a_stale_version_is_refused(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    with pytest.raises(Stale) as e:
        await taxonomy.accept_proposal(fx.owner(), mode["id"], base_version=mode["version"] + 4)
    assert e.value.current["id"] == mode["id"]


async def test_the_platform_rejects_a_proposal_with_a_reason_and_the_row_is_kept(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    out = await taxonomy.reject_proposal(fx.owner(), mode["id"], "  Out of scope for the platform taxonomy.  ")
    rejected = out["mode"]
    assert rejected["status"] == "rejected" and rejected["rejected_reason"] == "Out of scope for the platform taxonomy."
    assert rejected["decided_by"] == str(fx.owner().user_id) and rejected["version"] == mode["version"] + 1
    async with lab_scratch.connect() as conn:
        kept = (await conn.execute(text("SELECT status, rejected_reason FROM tracelab.modes WHERE id = CAST(:i AS uuid)"),
                                   {"i": mode["id"]})).mappings().first()
        labelled = (await conn.execute(text("SELECT count(*) FROM tracelab.labels"))).scalar()
    assert kept["status"] == "rejected" and labelled == 0
    event = (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["events"][0]
    assert event["action"] == "reject" and event["detail"]["reason"] == "Out of scope for the platform taxonomy."
    assert [m["id"] for m in await taxonomy.list_modes(fx.owner(), None, include_retired=True)] == []
    with pytest.raises(Conflict) as e:
        await taxonomy.reject_proposal(fx.owner(), mode["id"], "again")
    assert e.value.reason == "not_a_proposal"


@pytest.mark.parametrize("reason", ["", "   ", None, "x" * 501])
async def test_a_rejection_needs_a_reason(lab_scratch, main_scratch, reason):
    mode = await _propose(lab_scratch, main_scratch)
    with pytest.raises(Invalid) as e:
        await taxonomy.reject_proposal(fx.owner(), mode["id"], reason)
    assert e.value.reason == "reason_required"
    assert (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["mode"]["status"] == "proposed"


async def test_a_rejected_proposals_name_can_be_proposed_again(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    await taxonomy.reject_proposal(fx.owner(), mode["id"], "Too vague.")
    again = await _propose(lab_scratch, main_scratch, fx.ORG_B, n=1)
    assert again["status"] == "proposed" and again["name"] == mode["name"]


async def test_a_rejected_mode_cannot_be_restored_or_edited(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    rejected = (await taxonomy.reject_proposal(fx.owner(), mode["id"], "Too vague."))["mode"]
    with pytest.raises(Conflict) as e:
        await taxonomy.edit(fx.owner(), mode["id"], {"boundary": "x"}, base_version=rejected["version"])
    assert e.value.reason == "mode_rejected"
    same = await taxonomy.restore(fx.owner(), mode["id"], base_version=rejected["version"])
    assert same["mode"]["status"] == "rejected"


async def test_only_the_platform_decides_a_proposal(lab_scratch, main_scratch):
    mode = await _propose(lab_scratch, main_scratch)
    for who in (fx.admin(), fx.admin(fx.ORG_B)):
        with pytest.raises(Forbidden) as e:
            await taxonomy.accept_proposal(who, mode["id"])
        assert e.value.reason == "platform_only"
        with pytest.raises(Forbidden):
            await taxonomy.reject_proposal(who, mode["id"], "No.")
    with pytest.raises(NotFound):
        await taxonomy.accept_proposal(fx.owner(), "not-a-uuid")
    async with lab_scratch.begin() as conn:
        live = await fx.add_mode(conn, "includes_cancelled_jobs")
    with pytest.raises(Conflict) as c:
        await taxonomy.reject_proposal(fx.owner(), live, "Not a proposal.")
    assert c.value.reason == "not_a_proposal"
    assert (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["mode"]["status"] == "proposed"
