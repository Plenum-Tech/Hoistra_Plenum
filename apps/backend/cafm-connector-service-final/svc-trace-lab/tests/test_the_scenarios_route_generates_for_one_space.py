"""The page that generates a batch: what the route gives it, and what it refuses.

A UI needed somewhere to ask for a batch, so /scenarios/generate exists. Three things about it
are worth holding still:

* it is behind the same reviewer gate as every other golden route -- a batch of questions aimed
  at a company's own data is not something a plain user asks for;
* it refuses a count above 100. The feedback asked for 50-100, and a batch nobody reads is not
  an evaluation;
* the CSV's expected-answer column is EMPTY. A golden case needs a known right answer, and that
  has to be grounded in a real row and read by a person. Filling it with something a model
  invented would turn an evaluation set into a way of agreeing with itself.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi import APIRouter
from pydantic import ValidationError

from trace_lab import routes_scenarios as rs


# -- the request the page sends ----------------------------------------------------------------

@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
def test_each_real_space_is_accepted(space):
    assert rs.GenerateRequest(space=space).space == space


@pytest.mark.parametrize("bad", ["maintenance", "", "OPS", "everything"])
def test_a_space_that_is_not_real_is_refused(bad):
    with pytest.raises(ValidationError):
        rs.GenerateRequest(space=bad)


def test_a_space_must_be_named():
    """No default. A batch generated for "whatever" is the bug this whole piece is about."""
    with pytest.raises(ValidationError):
        rs.GenerateRequest()


@pytest.mark.parametrize("count", [50, 100])
def test_the_sizes_the_feedback_asked_for_are_allowed(count):
    assert rs.GenerateRequest(space="ops", count=count).count == count


@pytest.mark.parametrize("count", [0, -1, 101, 5000])
def test_a_count_outside_the_range_is_refused(count):
    with pytest.raises(ValidationError):
        rs.GenerateRequest(space="ops", count=count)


@pytest.mark.parametrize("ratio", [-0.1, 0.6, 1.0])
def test_a_refusal_share_over_half_is_refused(ratio):
    """Past half the batch the run stops being an evaluation and becomes a gate test."""
    with pytest.raises(ValidationError):
        rs.GenerateRequest(space="ops", challenge_ratio=ratio)


# -- who may ask ----------------------------------------------------------------------------------

def test_generating_does_not_demand_a_company():
    """Generation reads no company data: it is deterministic from (space, seed) and touches no
    database. Requiring a company refused the platform view with "Choose a company to view as
    first" for a batch that does not belong to a company (3 Nov 2026)."""
    from trace_lab.routes_golden import reader

    for path in ("/scenarios/spaces", "/scenarios/generate", "/scenarios/template.csv"):
        route = next(r for r in rs.router.routes if getattr(r, "path", "") == path)
        calls = [sub.call for sub in route.dependant.dependencies]
        assert any(c is reader or getattr(c, "__name__", "") == "reader" for c in calls), (
            "%s still demands a company" % path)


def test_asking_the_questions_does_demand_one():
    """A run reads one company's data through the chat, so it keeps the reviewer gate and the
    company that comes with it. Loosening this one would ask questions of whoever answered last."""
    from trace_lab.routes_conversations import reviewer

    route = next(r for r in rs.router.routes if getattr(r, "path", "") == "/scenarios/run")
    calls = [sub.call for sub in route.dependant.dependencies]
    assert any(c is reviewer or getattr(c, "__name__", "") == "reviewer" for c in calls)


def test_no_route_is_open():
    """Whichever gate each one takes, none of them is ungated."""
    for route in rs.router.routes:
        calls = [getattr(sub.call, "__name__", "") for sub in route.dependant.dependencies]
        assert "reader" in calls or "reviewer" in calls, getattr(route, "path", route)


def test_the_router_is_mounted():
    from trace_lab import app as app_mod

    assert "routes_scenarios" in __import__("inspect").getsource(app_mod)


# -- what comes back ---------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
async def test_generate_returns_a_batch_for_that_space(space):
    out = await rs.generate(rs.GenerateRequest(space=space, count=20, seed=1), who=None)
    assert out["ok"] is True
    assert out["space"] == space
    assert out["generated"] == 20 and len(out["scenarios"]) == 20
    assert out["fingerprint"]


@pytest.mark.asyncio
async def test_it_says_when_it_could_not_fill_the_batch():
    """Returning fewer without saying so reads as "that is all there is"."""
    out = await rs.generate(rs.GenerateRequest(space="ops", count=20, seed=1), who=None)
    assert out["short"] == (out["generated"] < out["requested"])


@pytest.mark.asyncio
async def test_every_question_names_the_engine_that_will_answer_it():
    out = await rs.generate(rs.GenerateRequest(space="ops", count=20, seed=2), who=None)
    for row in out["scenarios"]:
        assert row["engine"] and row["question"] and row["scenario_id"]


@pytest.mark.asyncio
async def test_the_spaces_route_lists_each_space_and_its_engines():
    out = await rs.list_spaces(who=None)
    keys = [s["key"] for s in out["spaces"]]
    assert keys == list(rs.scenarios.SPACES)
    ops = next(s for s in out["spaces"] if s["key"] == "ops")
    assert ops["name"] == "Ops Brain"
    assert "compliance" not in ops["engines"]


# -- the hand-off to a golden set ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_csv_is_the_golden_upload_shape():
    res = await rs.template_csv(rs.GenerateRequest(space="ops", count=10, seed=3), who=None)
    rows = list(csv.reader(io.StringIO(res.body.decode("utf-8"))))
    assert tuple(rows[0]) == rs.CSV_HEADER
    assert len(rows) == 11


@pytest.mark.asyncio
async def test_the_expected_answer_column_is_left_for_a_person():
    res = await rs.template_csv(rs.GenerateRequest(space="ops", count=10, seed=3), who=None)
    rows = list(csv.DictReader(io.StringIO(res.body.decode("utf-8"))))
    assert rows and all(r["expected_answer"] == "" for r in rows), (
        "a generated expected answer would make the set agree with itself")
    assert all(r["question"].strip() for r in rows)


@pytest.mark.asyncio
async def test_each_row_says_which_instance_and_engine_it_was_made_for():
    res = await rs.template_csv(rs.GenerateRequest(space="comp", count=10, seed=3), who=None)
    rows = list(csv.DictReader(io.StringIO(res.body.decode("utf-8"))))
    assert all("space=comp" in r["notes"] and "engine=" in r["notes"] for r in rows)


@pytest.mark.asyncio
async def test_a_question_the_instance_must_refuse_is_marked_in_the_file():
    res = await rs.template_csv(
        rs.GenerateRequest(space="ops", count=20, seed=5, challenge_ratio=0.25), who=None)
    notes = [r["notes"] for r in csv.DictReader(io.StringIO(res.body.decode("utf-8")))]
    assert any("OUT-OF-MODULE" in n for n in notes), (
        "a refusal case that is not marked would be read as a failed answer")


@pytest.mark.asyncio
async def test_the_file_is_offered_as_a_download_named_for_the_batch():
    res = await rs.template_csv(rs.GenerateRequest(space="assets", count=5, seed=9), who=None)
    assert res.media_type == "text/csv"
    assert "scenarios-assets-9.csv" in res.headers["content-disposition"]


# -- the companies route ---------------------------------------------------------------------

def test_listing_companies_only_ever_reads():
    """It answers with every company's name to the platform team, from the application database,
    which this service reads and never writes (G0)."""
    import inspect

    src = inspect.getsource(rs.list_companies)
    assert "conversations._read()" in src, "it must go through the read-only engine"
    upper = src.upper()
    for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER "):
        assert word not in upper, "the companies route writes: " + word.strip()


def test_a_company_admin_is_given_only_their_own():
    """Listing every company to somebody who may work in one would be a leak of the customer
    list, whatever they could do with it afterwards."""
    import inspect

    src = inspect.getsource(rs.list_companies)
    assert 'role", "")) != "superadmin"' in src
    assert "WHERE id = CAST(:o AS uuid)" in src, "a non-superadmin must be asked for by id"


def test_the_companies_route_is_behind_a_gate():
    from trace_lab.routes_golden import reader

    route = next(r for r in rs.router.routes if getattr(r, "path", "") == "/lab/companies")
    calls = [sub.call for sub in route.dependant.dependencies]
    assert any(c is reader or getattr(c, "__name__", "") == "reader" for c in calls)
