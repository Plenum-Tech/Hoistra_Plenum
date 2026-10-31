"""golden_store on a throwaway Postgres (tracelab_scratch): each upload is the company's next version and retires
the last in one transaction, the diff names what changed, cases keep their sheet order and flags, roll back and
retire, the one Regression cases set, and another company never sees a set. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from src.services.principal import Principal
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import golden, golden_store

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
USER = uuid.uuid4()
SHA = "b" * 64
TODAY = datetime.now(timezone.utc).date()      # the run date: staleness is judged against the real clock
HEADER = "ID,Question,Follow-ups,Expected answer,Evaluation,Expected values,Verified on,Notes\n"


def admin(org=ORG):
    return Principal(user_id=USER, email="admin@example.com", organization_id=uuid.UUID(org), role="admin",
                     can_ingest=True, building_ids=None)


def parsed(*rows: str) -> golden.ParseResult:
    r = golden.parse_upload("bishopsgate.csv", (HEADER + "".join(rows)).encode("utf-8"), today=TODAY)
    assert r.errors == [], r.errors
    return r


async def save(engine, result, org=ORG, *, name=None, sha=SHA, note=None):
    async with engine.begin() as conn:
        return await golden_store.save_version(conn, org, result, actor=admin(org), filename="bishopsgate.csv",
                                               source_sha256=sha, name=name, note=note)


ONE = '1,How many floors does Bishopsgate Tower have?,,22,objective,22,2026-01-01,For people only\n'
TWO = '2,What is the postcode?,,EC2M 9ZZ,objective,,,\n'
THREE = f'3,Raise a work order for the lift?,"And when?\nWho?",No,judgment,,{TODAY.isoformat()},\n'   # fresh on any run date


async def test_the_first_upload_is_version_1_with_every_case_added(lab_scratch):
    out = await save(lab_scratch, parsed(ONE, TWO, THREE), name="Bishopsgate questions", note="  Re-verified by Priya  ")
    s = out["set"]
    assert s["note"] == "Re-verified by Priya"
    assert (s["version"], s["status"], s["kind"], s["name"], s["case_count"]) == (1, "active", "scenario",
                                                                                   "Bishopsgate questions", 3)
    assert (s["source_filename"], s["source_sha256"], s["source_kind"]) == ("bishopsgate.csv", SHA, "csv")
    assert (s["created_by"], s["created_by_email"]) == (str(USER), "admin@example.com")
    assert out["diff"]["added"] == ["1", "2", "3"] and out["diff"]["previous_version"] is None
    assert (s["stale_count"], s["unverified_count"]) == (1, 1)
    want = golden.set_hash([(c.case_key, c.case_version) for c in parsed(ONE, TWO, THREE).cases])
    assert s["set_hash"] == want
    active = await golden_store.active_set(ORG)
    assert active["id"] == s["id"]


async def test_cases_keep_sheet_order_flags_and_never_carry_a_secret(lab_scratch):
    s = (await save(lab_scratch, parsed(ONE, TWO, THREE)))["set"]
    cases = await golden_store.cases_for_run(ORG, s["id"])
    assert [(c["case_key"], c["idx"]) for c in cases] == [("1", 0), ("2", 1), ("3", 2)]
    one, two, three = cases
    assert one["stale"] is True and one["verified_on"] == "2026-01-01" and one["notes"] == "For people only"
    assert one["expected_values"][0]["value"] == 22 and one["followups"] == []
    assert two["verified_on"] is None and "unverified" in two["flags"]
    assert three["followups"] == ["And when?", "Who?"] and three["evaluation"] == "judgment"
    assert "write_intent" in three["flags"]
    page = await golden_store.get_cases(ORG, s["id"], flag="write_intent")
    assert page["total"] == 1 and page["cases"][0]["case_key"] == "3"
    assert (await golden_store.get_cases(ORG, s["id"], flag="stale"))["total"] == 1
    assert (await golden_store.get_cases(ORG, s["id"], flag="unverified"))["total"] == 1
    assert (await golden_store.get_cases(ORG, s["id"], q="postcode"))["total"] == 1
    assert (await golden_store.get_cases(ORG, s["id"], q="100%"))["total"] == 0
    assert [c["case_key"] for c in (await golden_store.get_cases(ORG, s["id"], limit=1, offset=1))["cases"]] == ["2"]
    with pytest.raises(ValueError):
        await golden_store.get_cases(ORG, s["id"], flag="everything")


async def test_a_new_upload_retires_the_last_and_names_the_changes(lab_scratch):
    first = (await save(lab_scratch, parsed(ONE, TWO), name="Bishopsgate questions"))["set"]
    changed_one = ONE.replace(",22,objective,22,", ",23,objective,23,")
    second = await save(lab_scratch, parsed(changed_one, THREE), sha="c" * 64)
    assert second["set"]["version"] == 2 and second["set"]["name"] == "Bishopsgate questions"   # the name carries on
    d = second["diff"]
    assert (d["previous_version"], d["changed"], d["added"], d["removed"], d["unchanged"]) == (1, ["1"], ["3"], ["2"], 0)
    listed = await golden_store.list_sets(ORG)
    assert listed["active"]["id"] == second["set"]["id"] and listed["regression"] is None
    assert [(v["version"], v["status"]) for v in listed["versions"]] == [(2, "active"), (1, "retired")]
    assert listed["versions"][1]["retired_at"] is not None and listed["versions"][1]["id"] == first["id"]


async def test_roll_back_and_retire(lab_scratch):
    one = (await save(lab_scratch, parsed(ONE)))["set"]
    two = (await save(lab_scratch, parsed(TWO), sha="c" * 64))["set"]
    back = await golden_store.activate(ORG, one["id"])
    assert back["status"] == "active" and back["retired_at"] is None
    assert (await golden_store.get_set(ORG, two["id"]))["status"] == "retired"
    gone = await golden_store.retire(ORG, one["id"])
    assert gone["status"] == "retired" and await golden_store.active_set(ORG) is None
    again = await golden_store.retire(ORG, one["id"])                     # retiring twice is harmless
    assert again["status"] == "retired"


async def test_the_regression_set_is_created_once_and_never_activated_or_retired(lab_scratch):
    assert await golden_store.regression_set(ORG) is None
    r = await golden_store.regression_set(ORG, create=True, actor=admin())
    assert (r["kind"], r["version"], r["name"], r["case_count"]) == ("regression", 0, golden_store.REGRESSION_SET_NAME, 0)
    assert (await golden_store.regression_set(ORG, create=True))["id"] == r["id"]
    assert (await golden_store.regression_set(ORG))["id"] == r["id"]
    for change in (golden_store.activate, golden_store.retire):
        with pytest.raises(golden_store.Conflict):
            await change(ORG, r["id"])
    s = (await save(lab_scratch, parsed(ONE)))["set"]
    assert s["version"] == 1                                              # uploads number from 1 beside it
    listed = await golden_store.list_sets(ORG)
    assert listed["regression"]["id"] == r["id"] and [v["id"] for v in listed["versions"]] == [s["id"]]
    assert (await golden_store.active_set(ORG))["id"] == s["id"]


async def test_another_company_never_sees_a_set(lab_scratch):
    s = (await save(lab_scratch, parsed(ONE)))["set"]
    for call in (golden_store.cases_for_run(OTHER, s["id"]), golden_store.get_cases(OTHER, s["id"]),
                 golden_store.activate(OTHER, s["id"]), golden_store.retire(OTHER, s["id"]),
                 golden_store.cases_for_run(ORG, "not-a-uuid"), golden_store.cases_for_run(ORG, str(uuid.uuid4()))):
        with pytest.raises(golden_store.NotFound):
            await call
    assert await golden_store.get_set(OTHER, s["id"]) is None
    assert (await golden_store.list_sets(OTHER)) == {"active": None, "versions": [], "regression": None}
    assert await golden_store.active_set(OTHER) is None


async def test_a_file_with_row_errors_is_never_saved(lab_scratch):
    bad = golden.parse_upload("s.csv", (HEADER + "1,,,22,,,,\n").encode(), today=date(2026, 10, 15))
    assert bad.errors
    with pytest.raises(ValueError):
        await save(lab_scratch, bad)
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.golden_sets"))).scalar_one() == 0


async def test_a_cap_smaller_than_the_versions_never_hides_the_regression_or_active_set(lab_scratch, monkeypatch):
    first = (await save(lab_scratch, parsed(ONE)))["set"]
    await save(lab_scratch, parsed(TWO), sha="c" * 64)
    await save(lab_scratch, parsed(THREE), sha="d" * 64)
    r = await golden_store.regression_set(ORG, create=True, actor=admin())
    await golden_store.activate(ORG, first["id"])                         # active is now the OLDEST version
    monkeypatch.setattr(golden_store, "MAX_LIST", 1)
    listed = await golden_store.list_sets(ORG)
    assert len(listed["versions"]) == 1 and listed["versions"][0]["version"] == 3
    assert listed["regression"]["id"] == r["id"]
    assert listed["active"]["id"] == first["id"]
