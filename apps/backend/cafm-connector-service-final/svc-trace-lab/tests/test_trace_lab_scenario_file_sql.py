"""Scenarios stored with their versions (11 Nov 2026, task plan B2.2): "every scenario has an expected outcome stored
with it". A change to a scenario is the next version and the older one stays, because a session keeps pointing at the
version it was asked with. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import scenario_file
from trace_lab.review_errors import Forbidden, Invalid

pytestmark = requires_scratch

SAMPLE = Path(__file__).parent / "fixtures" / "eval_scenarios_sample.jsonl"


def _rows() -> list[dict]:
    rows, errors = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    assert errors == []
    return rows


async def _versions(lab, scenario_id: str) -> list[tuple[int, str]]:
    async with lab.connect() as c:
        return [tuple(r) for r in (await c.execute(text(
            "SELECT version, status FROM tracelab.scenarios WHERE scenario_id = :id ORDER BY version"),
            {"id": scenario_id})).all()]


async def test_a_file_is_stored_as_version_1_drafts(lab_scratch):
    out = await scenario_file.save_scenarios(member(), _rows(), source="import")
    assert out == {"added": 4, "new_versions": 0, "unchanged": 0}
    assert await _versions(lab_scratch, "MT-001") == [(1, "draft")]


async def test_importing_the_same_file_twice_changes_nothing(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    assert await scenario_file.save_scenarios(member(), _rows(), source="import") == {
        "added": 0, "new_versions": 0, "unchanged": 4}


async def test_a_changed_expected_outcome_is_version_2_and_version_1_stays(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    rows = copy.deepcopy(_rows())
    rows[0]["turns"][0]["expected"]["must_include"] = ["WO-B-301-4551"]
    out = await scenario_file.save_scenarios(member(), rows, source="import")
    assert out == {"added": 0, "new_versions": 1, "unchanged": 3}
    assert await _versions(lab_scratch, "SD-001") == [(1, "draft"), (2, "draft")]
    latest = {r["scenario_id"]: r for r in await scenario_file.list_scenarios(member())}
    assert latest["SD-001"]["version"] == 2
    assert latest["SD-001"]["turns"][0]["expected"]["must_include"] == ["WO-B-301-4551"]


async def test_approving_adds_an_approved_version_with_the_same_content(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    out = await scenario_file.approve(member(), ["MT-001", "NOT-THERE"])
    assert out == {"approved": 1, "already": 0, "missing": ["NOT-THERE"], "shared": []}
    assert await _versions(lab_scratch, "MT-001") == [(1, "draft"), (2, "approved")]
    assert (await scenario_file.approve(member(), ["MT-001"]))["already"] == 1
    assert await _versions(lab_scratch, "MT-001") == [(1, "draft"), (2, "approved")]


async def test_an_approved_scenario_reimported_unchanged_stays_approved(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await scenario_file.approve(member(), ["MT-001"])
    out = await scenario_file.save_scenarios(member(), _rows(), source="import")
    assert out["unchanged"] == 4 and await _versions(lab_scratch, "MT-001") == [(1, "draft"), (2, "approved")]


async def test_the_list_filters_by_type_section_and_status(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await scenario_file.approve(member(), ["FL-001"])
    ids = lambda rows: [r["scenario_id"] for r in rows]                                      # noqa: E731
    assert ids(await scenario_file.list_scenarios(member(), type="multi_turn")) == ["MT-001"]
    assert ids(await scenario_file.list_scenarios(member(), section="partial_failure")) == ["FL-001"]
    assert ids(await scenario_file.list_scenarios(member(), status="approved")) == ["FL-001"]
    assert all(r["stale"] == [] for r in await scenario_file.list_scenarios(member()))


async def test_a_company_sees_its_own_scenarios_and_the_company_less_ones(lab_scratch):
    platform = member(org=None)
    await scenario_file.save_scenarios(platform, _rows()[:1], source="import")                       # no company
    await scenario_file.save_scenarios(member(org=ORG, all_companies=False, role="admin"), _rows()[1:2], source="import")
    theirs = member(org=OTHER_ORG, all_companies=False, role="admin")
    assert [r["scenario_id"] for r in await scenario_file.list_scenarios(theirs)] == ["SD-001"]
    ours = member(org=ORG, all_companies=False, role="admin")
    assert [r["scenario_id"] for r in await scenario_file.list_scenarios(ours)] == ["MD-001", "SD-001"]


async def test_an_invalid_scenario_is_never_stored(lab_scratch):
    bad = copy.deepcopy(_rows()[:1])
    bad[0]["type"] = "other"
    with pytest.raises(Invalid):
        await scenario_file.save_scenarios(member(), bad, source="import")
    assert await _versions(lab_scratch, "SD-001") == []


async def test_only_curators_store_or_approve_scenarios(lab_scratch):
    tester = member(org=ORG, all_companies=False, role="tester", manages=False)
    with pytest.raises(Forbidden):
        await scenario_file.save_scenarios(tester, _rows(), source="import")
    with pytest.raises(Forbidden):
        await scenario_file.approve(tester, ["MT-001"])


# ── one scenario id, one owner (a scenario id is the same on every machine and for every company) ──────────────

ADMIN = {"all_companies": False, "role": "admin"}


def _reworded(rows: list[dict], message: str) -> list[dict]:
    out = copy.deepcopy(rows)
    out[0]["turns"][0]["message"] = message
    return out


async def _owners(lab, scenario_id: str) -> list[str | None]:
    async with lab.connect() as c:
        return [r[0] for r in (await c.execute(text(
            "SELECT organization_id::text FROM tracelab.scenarios WHERE scenario_id = :id ORDER BY version"),
            {"id": scenario_id})).all()]


async def test_a_company_cannot_add_a_version_to_another_companys_scenario(lab_scratch):
    await scenario_file.save_scenarios(member(org=ORG, **ADMIN), _rows()[:1], source="import")
    theirs = member(org=OTHER_ORG, **ADMIN)
    with pytest.raises(Invalid) as e:
        await scenario_file.save_scenarios(theirs, _reworded(_rows()[:1], "a question of the other company"), source="import")
    assert e.value.reason == "scenario_id_taken" and "SD-001" in e.value.error
    with pytest.raises(Invalid):                       # the same words are refused too: it says nothing about what is stored
        await scenario_file.save_scenarios(theirs, _rows()[:1], source="import")
    assert await _owners(lab_scratch, "SD-001") == [ORG]
    assert [r["scenario_id"] for r in await scenario_file.list_scenarios(member(org=ORG, **ADMIN))] == ["SD-001"]


async def test_a_company_cannot_change_a_scenario_that_belongs_to_every_company(lab_scratch):
    await scenario_file.save_scenarios(member(org=None), _rows()[:1], source="import")
    ours = member(org=ORG, **ADMIN)
    assert (await scenario_file.save_scenarios(ours, _rows()[:1], source="import"))["unchanged"] == 1
    with pytest.raises(Invalid) as e:
        await scenario_file.save_scenarios(ours, _reworded(_rows()[:1], "our own wording"), source="import")
    assert e.value.reason == "scenario_id_taken"
    assert await _owners(lab_scratch, "SD-001") == [None]


async def test_the_platform_team_adding_a_version_leaves_the_scenario_with_its_company(lab_scratch):
    await scenario_file.save_scenarios(member(org=ORG, **ADMIN), _rows()[:1], source="import")
    out = await scenario_file.save_scenarios(member(org=None), _reworded(_rows()[:1], "reworded by the platform team"),
                                             source="import")
    assert out["new_versions"] == 1 and await _owners(lab_scratch, "SD-001") == [ORG, ORG]


async def test_a_company_approves_only_its_own_scenarios(lab_scratch):
    await scenario_file.save_scenarios(member(org=None), _rows()[:1], source="import")
    ours = member(org=ORG, **ADMIN)
    await scenario_file.save_scenarios(ours, _rows()[1:2], source="import")
    out = await scenario_file.approve(ours, ["SD-001", "MD-001"])
    assert out == {"approved": 1, "already": 0, "missing": [], "shared": ["SD-001"]}
    assert await _versions(lab_scratch, "SD-001") == [(1, "draft")]
