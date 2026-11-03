"""Questions that need more than one area to answer.

Asked for on 3 Nov 2026: "it should combine of more than one cross domain or cross tool
questions", with the example

    Please check work order WO00000359 at MixedUse 038. What is its current status, did it meet
    its response SLA, and how has HVAC Vendor 0111 been performing at this building overall?

One question, three lookups. That shape is the one worth measuring, because it is the one an
answer fails quietly: it answers the first part well, drops the other two, and reads as a good
answer. A batch of single lookups never catches that.

Three things have to hold, and they are what these tests are for:

* a question is only offered to an instance that has EVERY area it needs. Asked anywhere else it
  is a refusal, or worse a half-answer that reads as a whole one;
* it must ROUTE into one of those areas. The router reads the opening, and a three-part question
  that opens on the wrong area lands with an engine owning no part of it -- which measures the
  router, not the engines;
* what the answer must cover is carried with it, and never as an expected answer. Only a person
  reading real rows can write one of those.
"""
from __future__ import annotations

import csv
import io

import pytest

from trace_lab import routes_scenarios as rs
from trace_lab import scenarios


# -- the set itself ------------------------------------------------------------------------------

def test_there_are_cross_domain_questions_at_all():
    assert len(scenarios.CROSS_DOMAIN) >= 5


@pytest.mark.parametrize("c", scenarios.CROSS_DOMAIN, ids=lambda c: "+".join(c["needs"]))
def test_each_one_spans_at_least_two_areas(c):
    assert len(set(c["needs"])) >= 2, "a question needing one area is not cross-domain"
    assert set(c["needs"]) <= set(scenarios.BY_ENGINE), "names an engine that does not exist"


@pytest.mark.parametrize("c", scenarios.CROSS_DOMAIN, ids=lambda c: c["question"][:40])
def test_each_one_says_what_the_answer_must_cover(c):
    """A reviewer judging a three-part answer needs to know what the three parts were."""
    assert c["covers"] and len(c["covers"]) > 30


@pytest.mark.parametrize("c", scenarios.CROSS_DOMAIN, ids=lambda c: c["question"][:40])
def test_each_one_routes_into_an_area_it_needs(c):
    """The router reads the opening. "where is consumption rising fastest, and what are we paying
    the contractor who maintains the plant there" routed to the WORK-ORDER engine, which owns no
    part of it -- so the questions are worded to open on an area they actually need. A question
    that stops routing is dropped silently by the generator, so this is where it is noticed."""
    routed = scenarios.routes_to(c["question"])
    assert routed in c["needs"], f"routes to {routed}, which owns no part of it"


# -- what reaches a batch --------------------------------------------------------------------------

@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
def test_every_instance_gets_some(space):
    rows = scenarios.generate(space, count=10, seed=359375)
    assert [r for r in rows if r.needs], f"{space} batches are all single lookups"


@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
def test_an_instance_is_never_given_one_it_cannot_answer(space):
    """Half of a cross-domain question answered, with the other half refused, reads as a whole
    answer. It must never be asked where it cannot be answered in full."""
    from src.agents.spaces import allows

    mine = scenarios.engines_of(space)
    for r in scenarios.generate(space, count=25, seed=11):
        for area in r.needs:
            assert area in mine and allows(space, area), (
                f"{space} was given a question needing {area}, which it does not have")


@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
def test_they_do_not_crowd_out_the_plain_questions(space):
    """A batch of only multi-part questions stops measuring the single lookups that make up most
    of what people actually ask."""
    rows = scenarios.generate(space, count=10, seed=359375)
    cross = [r for r in rows if r.needs]
    assert len(cross) < len(rows), "the batch is nothing but cross-domain questions"


def test_they_are_asked_plainly():
    """A cross-domain question already has three parts to keep hold of. Mangling the typing as
    well measures two things at once and leaves a failure ambiguous."""
    rows = scenarios.generate("full", count=25, seed=5)
    assert all(r.style == "neutral" for r in rows if r.needs)


def test_a_cross_domain_question_is_in_module():
    """It is a question the instance HAS every area for, so it is never a refusal case."""
    rows = scenarios.generate("full", count=25, seed=5)
    assert all(r.in_module for r in rows if r.needs)


def test_a_plain_question_carries_no_cross_domain_fields():
    rows = scenarios.generate("full", count=25, seed=5)
    plain = [r for r in rows if not r.needs]
    assert plain and all(r.covers == "" for r in plain)


# -- what the reviewer is handed ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_batch_says_how_many_need_more_than_one_area():
    out = await rs.generate(rs.GenerateRequest(space="full", count=10, seed=359375), who=None)
    assert out["cross_domain"] == sum(1 for r in out["scenarios"] if r["needs"])
    assert out["cross_domain"] >= 1


@pytest.mark.asyncio
async def test_the_csv_carries_the_areas_and_what_to_check():
    out = await rs.template_csv(rs.GenerateRequest(space="full", count=10, seed=359375), who=None)
    rows = list(csv.DictReader(io.StringIO(out.body.decode("utf-8"))))
    cross = [r for r in rows if "CROSS-DOMAIN" in r["notes"]]
    assert cross, "a reviewer opening the file cannot tell which questions have three parts"
    for r in cross:
        assert "needs=" in r["notes"] and "MUST COVER:" in r["notes"]


@pytest.mark.asyncio
async def test_what_it_must_cover_is_never_put_in_the_expected_answer_column():
    """"Must cover" is what to check an answer against. An expected answer is what the answer
    should have SAID, and only a person reading real rows can write one -- filling it here would
    turn the set into a way of agreeing with itself."""
    out = await rs.template_csv(rs.GenerateRequest(space="full", count=10, seed=359375), who=None)
    rows = list(csv.DictReader(io.StringIO(out.body.decode("utf-8"))))
    assert all(r["expected_answer"] == "" for r in rows)
