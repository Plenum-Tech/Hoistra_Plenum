"""Row builders for the Phase 5 (taxonomy) scratch-database tests: a plain helper module, like tl_scratch.

The Phase 5 task briefs import this module under the name trace_lab_m4_fixtures. Two kinds of helper:

* Trace Lab's own tables (add_note, add_mode, add_run, add_proposal, add_label, add_batch_item) take a connection
  to the LAB scratch database (tl_scratch.lab_scratch, schema tracelab).
* The chat's tables (add_turn, add_span) take a connection to the WRITABLE engine that tl_scratch.main_scratch
  yields (schema plenum_cafm). Trace Lab's own database holds no copy of a chat turn, so no row here refers to
  a turn by a foreign key; a label or proposal just names the turn id.

Everything writes only to the throwaway database behind TRACE_LAB_TEST_DB_URL, through the connection the test
passes in. Nothing here can reach a real database.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.services.principal import Principal

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "aaaaaaaa-0000-4000-8000-000000000001"


def owner() -> Principal:
    """The platform owner: a superadmin who is not acting for a company."""
    return Principal(user_id=uuid.UUID(OWNER_ID), email="owner@plenum.test", organization_id=uuid.UUID(ORG_A),
                     role="superadmin", can_ingest=True, building_ids=None)


def admin(org: str = ORG_A, n: int = 1) -> Principal:
    return Principal(user_id=uuid.UUID(f"bbbbbbbb-{org[:4]}-4000-8000-{n:012d}"), email=f"admin{n}@{org[:4]}.test",
                     organization_id=uuid.UUID(org), role="admin", can_ingest=True, building_ids=None)


def acting_superadmin(org: str = ORG_A) -> Principal:
    """A superadmin acting for a company: same person as owner(); the company is named per request."""
    return owner()


async def add_turn(conn, turn_id: str, org: str = ORG_A, *, session_id: str | None = None,
                   question: str = "How many work orders are open at Bishopsgate?",
                   answer: str = "There are 12 open work orders at Bishopsgate.", started_at: datetime | None = None,
                   ok: bool = True, error: str | None = None, route: str = "single:wo_engine") -> str:
    """A chat turn in plenum_cafm.agent_trace_turns (MAIN scratch connection)."""
    await conn.execute(text(
        "INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, organization_id, user_id, email, question, "
        "answer, route, started_at, ok, error) VALUES (:t, :s, CAST(:o AS uuid), CAST(:u AS uuid), 'asker@x.test', "
        ":q, :a, :r, coalesce(CAST(:at AS timestamptz), now()), :ok, :err)"),
        {"t": turn_id, "s": session_id, "o": org, "u": str(uuid.uuid4()), "q": question, "a": answer, "r": route,
         "at": started_at, "ok": ok, "err": error})
    return turn_id


async def add_span(conn, turn_id: str, org: str = ORG_A, *, kind: str = "tool", name: str = "query_table",
                   agent: str | None = "udr", seq: int = 1, input: Any = None, output: Any = None,
                   error: str | None = None) -> str:
    """A span in plenum_cafm.agent_trace_spans (MAIN scratch connection)."""
    sid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, organization_id, seq, kind, name, agent, started_at, "
        "input, output, ok, error) VALUES (CAST(:id AS uuid), :t, CAST(:o AS uuid), :seq, :k, :n, :a, now(), "
        "CAST(:i AS jsonb), CAST(:out AS jsonb), :ok, :err)"),
        {"id": sid, "t": turn_id, "o": org, "seq": seq, "k": kind, "n": name, "a": agent,
         "i": None if input is None else json.dumps(input), "out": None if output is None else json.dumps(output),
         "ok": error is None, "err": error})
    return sid


async def add_note(conn, turn_id: str, org: str = ORG_A, *, reviewer: Principal | None = None,
                   note: str = "The count includes cancelled jobs.", quote: str = "12 open work orders",
                   field: str = "answer", verdict: str = "wrong", tombstone: bool = False,
                   lineage_id: str | None = None, version: int = 1, supersedes: str | None = None,
                   span_id: str | None = None, span_agent: str | None = None) -> str:
    """An annotation (LAB scratch connection)."""
    who = reviewer or admin(org)
    aid = str(uuid.uuid4())
    right = verdict == "right"
    bare = right or tombstone
    await conn.execute(text(
        "INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id, "
        "conversation_key, turn_id, reviewer_id, reviewer_email, tombstone, verdict, note, span_id, field, quote, "
        "quote_occurrence, quote_start, span_name, span_kind, span_agent, blind) VALUES (CAST(:id AS uuid), "
        "CAST(:lin AS uuid), :v, CAST(:sup AS uuid), 'verdict', CAST(:o AS uuid), :ck, :t, CAST(:r AS uuid), :re, "
        ":tomb, :verdict, :note, CAST(:span AS uuid), :field, :quote, :occ, :qs, :sn, :sk, :sa, true)"),
        {"id": aid, "lin": lineage_id or aid, "v": version, "sup": supersedes, "o": org, "ck": "t:" + turn_id,
         "t": turn_id, "r": str(who.user_id), "re": who.email, "tomb": tombstone,
         "verdict": None if tombstone else verdict,
         "note": None if tombstone else ("no failure observed" if right else note), "span": span_id,
         "field": None if bare else ("output" if span_id else field),
         "quote": None if bare else quote, "occ": None if bare else 1,
         "qs": None if bare else 0, "sn": None if bare else ("query_table" if span_id else "turn"),
         "sk": None if bare else ("tool" if span_id else field), "sa": span_agent})
    return aid


async def add_mode(conn, name: str, *, status: str = "candidate", level: str = "technical", agent: str = "wo_engine",
                   created_from: list[str] | None = None,
                   definition: str = "Present when the answer counts cancelled jobs as open.",
                   boundary: str = "", requirement_ids: tuple[str, ...] = (), merged_into: str | None = None,
                   split_from: str | None = None, evaluator_type: str | None = None,
                   rescoped_at: datetime | None = None, absorbed_at: datetime | None = None,
                   created_at: datetime | None = None, proposer_org: str | None = None,
                   rejected_reason: str | None = None) -> str:
    """A failure mode (LAB scratch connection). A 'proposed' mode names its proposing company (default ORG_A); a
    'rejected' one carries a reason."""
    mid = str(uuid.uuid4())
    if status == "proposed" and proposer_org is None:
        proposer_org = ORG_A
    if status == "rejected" and rejected_reason is None:
        rejected_reason = "Out of scope for the platform taxonomy."
    await conn.execute(text(
        "INSERT INTO tracelab.modes (id, name, definition, boundary, status, evaluator_type, level, agent, "
        "requirement_ids, created_from, merged_into, split_from, rescoped_at, absorbed_at, created_at, retired_at, "
        "created_by, created_by_email, proposer_organization_id, rejected_reason) VALUES "
        "(CAST(:id AS uuid), :n, :d, :b, :s, :ev, :lv, :ag, CAST(:req AS text[]), "
        "CAST(:cf AS uuid[]), CAST(:mi AS uuid), CAST(:sf AS uuid), coalesce(CAST(:ra AS timestamptz), now()), "
        "CAST(:aa AS timestamptz), coalesce(CAST(:ca AS timestamptz), now()), CASE WHEN :s = 'retired' THEN now() END, "
        "CAST(:by AS uuid), 'owner@plenum.test', CAST(:po AS uuid), CAST(:rr AS text))"),
        {"id": mid, "n": name, "d": definition, "b": boundary, "s": status, "ev": evaluator_type, "lv": level,
         "ag": agent, "req": list(requirement_ids), "cf": created_from or [str(uuid.uuid4())], "mi": merged_into,
         "sf": split_from, "ra": rescoped_at, "aa": absorbed_at, "ca": created_at, "by": OWNER_ID,
         "po": proposer_org, "rr": rejected_reason})
    return mid


async def add_run(conn, kind: str = "cells", *, mode_id: str | None = None, org: str | None = None,
                  canary_total: int = 0, canary_agree: int = 0, job_status: str = "done") -> str:
    """A proposal run and the job it belongs to (LAB scratch connection)."""
    job_kind = {"grouping": "proposal_grouping", "depth_scan": "proposal_depth_scan", "cells": "proposal_cells"}[kind]
    job_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.jobs (id, kind, status, organization_id, scope_key, params, usd_cap, created_by) "
        "VALUES (CAST(:id AS uuid), :k, :st, CAST(:o AS uuid), :sk, '{}'::jsonb, 5, CAST(:by AS uuid))"),
        {"id": job_id, "k": job_kind, "st": job_status, "o": org, "sk": f"test:{run_id}", "by": OWNER_ID})
    await conn.execute(text(
        "INSERT INTO tracelab.proposal_runs (id, job_id, kind, organization_id, mode_id, model, prompt_version, "
        "prompt_hash, params, estimate_usd, worst_usd, items_planned, canary_total, canary_agree, created_by) VALUES "
        "(CAST(:id AS uuid), CAST(:job AS uuid), :k, CAST(:o AS uuid), CAST(:m AS uuid), 'claude-sonnet-5', 'c1', "
        "'abcdef012345', '{}'::jsonb, 0.5, 1.0, 1, :ct, :ca, CAST(:by AS uuid))"),
        {"id": run_id, "job": job_id, "k": kind, "o": org, "m": mode_id, "ct": canary_total, "ca": canary_agree,
         "by": OWNER_ID})
    return run_id


async def add_proposal(conn, run_id: str, *, kind: str = "cell", turn_id: str | None = None,
                       mode_id: str | None = None, suggestion: str = "present", org: str | None = ORG_A,
                       evidence: str = "The answer counts cancelled jobs as open.", quote: str | None = None,
                       field: str | None = None, status: str = "pending", closed_reason: str | None = None,
                       annotation_ids: list[str] | None = None, draft: dict | None = None, is_canary: bool = False,
                       flags: dict | None = None, cache_key: str | None = None) -> str:
    """An AI proposal (LAB scratch connection). A group proposal names no company and no turn."""
    pid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.proposals (id, run_id, kind, cache_key, organization_id, turn_id, mode_id, "
        "suggestion, annotation_ids, draft, quote, field, evidence, status, closed_reason, is_canary, flags) VALUES "
        "(CAST(:id AS uuid), CAST(:run AS uuid), :k, :ck, CAST(:o AS uuid), :t, CAST(:m AS uuid), :s, "
        "CAST(:ann AS uuid[]), CAST(:dr AS jsonb), :q, :f, :ev, :st, :cr, :can, CAST(:fl AS jsonb))"),
        {"id": pid, "run": run_id, "k": kind, "ck": cache_key or pid, "o": None if kind == "group" else org,
         "t": None if kind == "group" else turn_id, "m": mode_id, "s": suggestion, "ann": annotation_ids or [],
         "dr": None if draft is None else json.dumps(draft), "q": quote, "f": field, "ev": evidence, "st": status,
         "cr": closed_reason, "can": is_canary, "fl": json.dumps(flags or {})})
    return pid


async def add_label(conn, turn_id: str, mode_id: str, *, present: bool | None = True, org: str = ORG_A,
                    source: str = "human_structured_label", approval: str = "single", proposal_id: str | None = None,
                    annotation_id: str | None = None, quote: str | None = None, note: str | None = None,
                    supersedes: str | None = None, created_at: datetime | None = None) -> str:
    """A label (LAB scratch connection). present=None writes a tombstone."""
    lid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.labels (id, organization_id, turn_id, mode_id, present, tombstone, source, note, "
        "quote, proposal_id, annotation_id, approval, decided_by, decided_by_email, supersedes, created_at) VALUES "
        "(CAST(:id AS uuid), CAST(:o AS uuid), :t, CAST(:m AS uuid), :p, :tomb, :src, :note, :q, CAST(:pr AS uuid), "
        "CAST(:an AS uuid), :ap, CAST(:by AS uuid), 'reviewer@x.test', CAST(:sup AS uuid), "
        "coalesce(CAST(:ca AS timestamptz), now()))"),
        {"id": lid, "o": org, "t": turn_id, "m": mode_id, "p": present, "tomb": present is None, "src": source,
         "note": note, "q": quote, "pr": proposal_id, "an": annotation_id, "ap": approval, "by": OWNER_ID,
         "sup": supersedes, "ca": created_at})
    return lid


async def add_batch_item(conn, turn_id: str, org: str = ORG_A, *, strategy: str = "uniform",
                         plan_step: str = "initial_uniform", batch_id: str | None = None, position: int = 1) -> str:
    """A sampled turn in a batch (LAB scratch connection); a new batch is created unless batch_id is given."""
    bid = batch_id or str(uuid.uuid4())
    if batch_id is None:
        await conn.execute(text(
            "INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since, "
            "window_until, requested, selected, pool_size, counts_for_prevalence, created_by) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), :ps, :st, 7, now() - interval '30 days', now() + interval '1 day', 15, 15, 100, :cfp, "
            "CAST(:by AS uuid))"),
            {"id": bid, "o": org, "ps": plan_step, "st": strategy, "cfp": strategy == "uniform", "by": OWNER_ID})
    await conn.execute(text(
        "INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason) VALUES "
        "(CAST(:id AS uuid), CAST(:b AS uuid), CAST(:o AS uuid), :t, :pos, 'uniform pick')"),
        {"id": str(uuid.uuid4()), "b": bid, "o": org, "t": turn_id, "pos": position})
    return bid
