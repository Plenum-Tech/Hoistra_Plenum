"""Evidence snapshots without a database (trace_lab/evidence.py): which SQL goes to which database, who is
refused, what is read once, how live and snapshot rows are chosen, and the JSON shape both sources share. Two
recording fake engines stand in for db.main_engine() and db.lab_engine()."""
from __future__ import annotations

import dataclasses
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trace_lab import db, evidence

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
T0 = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
SPAN_A1 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
SPAN_A2 = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
USER = uuid.UUID("33333333-3333-4333-8333-333333333333")


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.rows[0][0] if self.rows else None


class _Conn:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params))
        return _Result(self.handler(sql, params) or ())


class _Engine:
    def __init__(self, handler):
        self.conn, self.opened = _Conn(handler), 0

    @asynccontextmanager
    async def begin(self):
        self.opened += 1
        yield self.conn


def turn_row(tid, org=ORG, **kw):
    return {"turn_id": tid, "session_id": "sess-1", "organization_id": uuid.UUID(org), "user_id": USER,
            "email": "asker@example.com", "question": "How many work orders?", "answer": "758.", "route": "single:wo",
            "started_at": T0, "ended_at": T0, "latency_ms": 2000, "llm_calls": 2, "tool_calls": 1,
            "cost_usd": Decimal("0.012345"), "models": ["m"], "tools": ["answer_from_records"], "ok": True,
            "created_at": T0, **kw}


def span_row(tid, sid, seq, **kw):
    return {"id": sid, "turn_id": tid, "seq": seq, "kind": "tool", "name": "answer_from_records",
            "agent": "wo_engine", "started_at": T0, "input": None, "output": {"count": 758}, "cost_usd": None,
            "ok": True, **kw}


class World:
    """The main database's turns and spans, and the lab database's evidence rows, behind two fake engines."""

    def __init__(self, monkeypatch, *, turns=(), spans=(), lab_turns=(), lab_spans=()):
        self.turns, self.spans = list(turns), list(spans)
        self.lab_turns, self.lab_spans = {r["turn_id"]: r for r in lab_turns}, list(lab_spans)
        self.main = _Engine(self._main)
        self.lab = _Engine(self._lab)
        monkeypatch.setattr(db, "main_engine", lambda: self.main)
        monkeypatch.setattr(db, "lab_engine", lambda: self.lab)

    def _main(self, sql, params):
        if "FROM plenum_cafm.agent_trace_turns" in sql:
            return [t for t in self.turns if t["turn_id"] in params["ids"]
                    and str(t["organization_id"]) == params["org"]]
        if "FROM plenum_cafm.agent_trace_spans" in sql:
            return sorted((s for s in self.spans if s["turn_id"] in params["ids"]),
                          key=lambda s: (s["turn_id"], s["seq"]))
        return None

    def _lab(self, sql, params):
        if sql.startswith("SELECT turn_id FROM tracelab.evidence_turns"):
            return [{"turn_id": t} for t in params["ids"] if t in self.lab_turns]
        if sql.startswith("SELECT turn_id, turn FROM tracelab.evidence_turns"):
            return [{"turn_id": t, "turn": r["turn"]} for t, r in self.lab_turns.items()
                    if t in params["ids"] and r["organization_id"] == params["org"]]
        if sql.startswith("SELECT turn_id, span_id, seq, span FROM tracelab.evidence_spans"):
            return [s for s in self.lab_spans if s["turn_id"] in params["ids"]]
        if sql.startswith("INSERT INTO tracelab.evidence_turns"):
            if params["t"] in self.lab_turns:
                return []
            self.lab_turns[params["t"]] = {"turn_id": params["t"], "organization_id": params["o"],
                                           "turn": json.loads(params["j"])}
            return [(params["t"],)]
        return None

    def lab_inserts(self):
        return [(s, p) for s, p in self.lab.conn.calls if s.startswith("INSERT")]


def lab_row(tid, org=ORG, **kw):
    return {"turn_id": tid, "organization_id": org, "turn": evidence.plain(turn_row(tid, org, **kw))}


def lab_span(tid, sid, seq, **kw):
    return {"turn_id": tid, "span_id": str(sid), "seq": seq, "span": evidence.plain(span_row(tid, sid, seq, **kw))}


# ── snapshot ───────────────────────────────────────────────────────────────────────────────

async def test_snapshot_reads_the_main_database_only_and_writes_only_to_the_lab(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a"), turn_row("turn-b")],
              spans=[span_row("turn-a", SPAN_A1, 1), span_row("turn-a", SPAN_A2, 2)])
    assert await evidence.snapshot(["turn-a", "turn-b"], organization_id=ORG, reason="sampled") == 2
    main_sql = [s for s, _ in w.main.conn.calls]
    assert main_sql[0] == "SET TRANSACTION READ ONLY"
    for sql in main_sql:
        assert "tracelab" not in sql and not re.match(r"(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP)", sql), sql
    turn_query = next((s, p) for s, p in w.main.conn.calls if "FROM plenum_cafm.agent_trace_turns" in s)
    assert "organization_id = CAST(:org AS uuid)" in turn_query[0] and turn_query[1]["org"] == ORG
    for sql, _ in w.lab.conn.calls:
        assert "plenum_cafm" not in sql and not re.match(r"(UPDATE|DELETE|TRUNCATE)", sql), sql
    for sql, _ in w.lab_inserts():
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql, sql


async def test_snapshot_copies_the_row_and_spans_as_plain_json_and_notes_why(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a")],
              spans=[span_row("turn-a", SPAN_A2, 2), span_row("turn-a", SPAN_A1, 1)])
    await evidence.snapshot(["turn-a"], organization_id=ORG, reason="annotated")
    (turn_sql, turn_params), *span_inserts = w.lab_inserts()
    assert turn_params["t"] == "turn-a" and turn_params["o"] == ORG and turn_params["r"] == "annotated"
    stored = json.loads(turn_params["j"])
    assert stored["organization_id"] == ORG and stored["started_at"] == "2026-10-01T09:30:00+00:00"
    assert stored["cost_usd"] == 0.012345 and stored["models"] == ["m"] and stored["question"] == "How many work orders?"
    rows = [r for _, params in span_inserts for r in (params if isinstance(params, list) else [params])]
    assert [(r["s"], r["q"]) for r in rows] == [(str(SPAN_A1), 1), (str(SPAN_A2), 2)]       # in sequence order
    assert json.loads(rows[0]["j"])["id"] == str(SPAN_A1) and json.loads(rows[0]["j"])["output"] == {"count": 758}
    assert all(r["t"] == "turn-a" for r in rows)


async def test_a_turn_without_spans_is_still_captured(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a")])           # the 90-day purge deletes spans, never the turn
    assert await evidence.snapshot(["turn-a"], organization_id=ORG, reason="sampled") == 1
    assert [s for s, _ in w.lab_inserts() if "evidence_spans" in s] == []


async def test_a_turn_already_captured_is_not_read_again(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a"), turn_row("turn-b")], lab_turns=[lab_row("turn-a")])
    assert await evidence.snapshot(["turn-a", "turn-b"], organization_id=ORG, reason="sampled") == 1
    asked = [p["ids"] for s, p in w.main.conn.calls if "FROM plenum_cafm.agent_trace_turns" in s]
    assert asked == [["turn-b"]]
    opened = w.main.opened
    assert await evidence.snapshot(["turn-a", "turn-b"], organization_id=ORG, reason="sampled") == 0
    assert w.main.opened == opened                               # nothing left to read from the application database


async def test_nothing_is_copied_for_another_company_or_a_missing_turn(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-x", OTHER), turn_row("turn-a")],
              spans=[span_row("turn-x", SPAN_A1, 1)])
    assert await evidence.snapshot(["turn-x", "turn-gone", "turn-a"], organization_id=ORG, reason="sampled") == 1
    assert [p["t"] for sql, p in w.lab_inserts() if "evidence_turns" in sql] == ["turn-a"]
    w2 = World(monkeypatch, turns=[turn_row("turn-x", OTHER)], spans=[span_row("turn-x", SPAN_A1, 1)])
    assert await evidence.snapshot(["turn-x", "turn-gone"], organization_id=ORG, reason="sampled") == 0
    assert w2.lab_inserts() == []


async def test_spans_are_read_only_for_turns_the_company_owns(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-x", OTHER), turn_row("turn-a")],
              spans=[span_row("turn-x", SPAN_A1, 1), span_row("turn-a", SPAN_A2, 1)])
    await evidence.snapshot(["turn-x", "turn-a"], organization_id=ORG, reason="sampled")
    asked = [p["ids"] for s, p in w.main.conn.calls if "FROM plenum_cafm.agent_trace_spans" in s]
    assert asked == [["turn-a"]]


async def test_arguments_are_checked_before_any_database_is_touched(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a")])
    with pytest.raises(ValueError, match="company"):
        await evidence.snapshot(["turn-a"], organization_id="not-a-uuid", reason="sampled")
    for reason in ("", "x" * 41, None):
        with pytest.raises(ValueError, match="reason"):
            await evidence.snapshot(["turn-a"], organization_id=ORG, reason=reason)
    assert await evidence.snapshot([], organization_id=ORG, reason="sampled") == 0
    assert await evidence.snapshot(["", None, "t" * 201, 7], organization_id=ORG, reason="sampled") == 0
    assert w.main.opened == 0 and w.lab.opened == 0


async def test_duplicate_ids_are_collapsed_and_order_is_kept(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("turn-a"), turn_row("turn-b")])
    assert await evidence.snapshot(["turn-b", "turn-a", "turn-b"], organization_id=ORG, reason="sampled") == 2
    asked = [p["ids"] for s, p in w.main.conn.calls if "FROM plenum_cafm.agent_trace_turns" in s]
    assert asked == [["turn-b", "turn-a"]]


async def test_a_large_snapshot_is_read_in_chunks(monkeypatch):
    ids = [f"turn-{i:04d}" for i in range(evidence.CHUNK * 2 + 5)]
    w = World(monkeypatch, turns=[turn_row(t) for t in ids])
    assert await evidence.snapshot(ids, organization_id=ORG, reason="sampled") == len(ids)
    sizes = [len(p["ids"]) for s, p in w.main.conn.calls if "FROM plenum_cafm.agent_trace_turns" in s]
    assert sizes == [evidence.CHUNK, evidence.CHUNK, 5]


# ── load ───────────────────────────────────────────────────────────────────────────────────

async def test_load_prefers_live_rows_then_snapshots_then_nothing(monkeypatch):
    World(monkeypatch,
          turns=[turn_row("live"), turn_row("purged"), turn_row("bare")],
          spans=[span_row("live", SPAN_A1, 1)],
          lab_turns=[lab_row("live"), lab_row("purged"), lab_row("snap-only")],
          lab_spans=[lab_span("live", SPAN_A1, 1), lab_span("purged", SPAN_A2, 2), lab_span("purged", SPAN_A1, 1),
                     lab_span("snap-only", SPAN_A2, 1)])
    got = await evidence.load(["live", "purged", "snap-only", "bare", "nowhere"], organization_id=ORG)
    assert {k: (v.source, len(v.spans)) for k, v in got.items()} == {
        "live": ("live", 1), "purged": ("snapshot", 2), "snap-only": ("snapshot", 1), "bare": ("live", 0)}
    assert "nowhere" not in got
    assert [s["seq"] for s in got["purged"].spans] == [1, 2]


async def test_load_gives_both_sources_the_same_json_shape(monkeypatch):
    World(monkeypatch, turns=[turn_row("live")], spans=[span_row("live", SPAN_A1, 1)],
          lab_turns=[lab_row("live")], lab_spans=[lab_span("live", SPAN_A1, 1)])
    live = (await evidence.load(["live"], organization_id=ORG))["live"]
    World(monkeypatch, lab_turns=[lab_row("live")], lab_spans=[lab_span("live", SPAN_A1, 1)])
    snap = (await evidence.load(["live"], organization_id=ORG))["live"]
    assert (live.source, snap.source) == ("live", "snapshot")
    assert live.turn == snap.turn and live.spans == snap.spans
    assert json.loads(json.dumps(live.turn)) == live.turn                      # plain JSON all the way down


async def test_load_never_returns_another_companys_rows(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("theirs", OTHER)], spans=[span_row("theirs", SPAN_A1, 1)],
              lab_turns=[lab_row("theirs", OTHER)], lab_spans=[lab_span("theirs", SPAN_A1, 1)])
    assert await evidence.load(["theirs"], organization_id=ORG) == {}
    assert w.main.conn.calls and all(p.get("org") == ORG for s, p in w.main.conn.calls if p and "org" in p)
    snap_q = [p for s, p in w.lab.conn.calls if s.startswith("SELECT turn_id, turn FROM")]
    assert snap_q and snap_q[0]["org"] == ORG
    assert [s for s, _ in w.lab.conn.calls if "evidence_spans" in s] == []     # no spans for a turn not ours


async def test_load_reads_the_lab_only_for_turns_without_live_spans(monkeypatch):
    w = World(monkeypatch, turns=[turn_row("live")], spans=[span_row("live", SPAN_A1, 1)])
    got = await evidence.load(["live"], organization_id=ORG)
    assert got["live"].source == "live" and w.lab.opened == 0
    assert await evidence.load([], organization_id=ORG) == {}


async def test_load_checks_its_arguments(monkeypatch):
    World(monkeypatch)
    with pytest.raises(ValueError, match="company"):
        await evidence.load(["t"], organization_id="nope")


# ── shapes ─────────────────────────────────────────────────────────────────────────────────

def test_evidence_turn_is_a_frozen_value():
    e = evidence.EvidenceTurn(turn={"turn_id": "t"}, spans=[], source="live")
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.source = "snapshot"                                                    # type: ignore[misc]
    assert dataclasses.is_dataclass(e) and [f.name for f in dataclasses.fields(e)] == ["turn", "spans", "source"]


def test_plain_turns_database_types_into_json_and_never_shortens_text():
    long = "x" * 50_000
    naive = datetime(2026, 10, 1, 9, 30)
    out = evidence.plain({"id": SPAN_A1, "cost": Decimal("1.5"), "at": naive, "aware": T0, "none": None, "long": long,
                          "nested": {"d": Decimal("2"), "list": [SPAN_A2]}, "n": 3, "ok": True})
    assert out == {"id": str(SPAN_A1), "cost": 1.5, "at": "2026-10-01T09:30:00+00:00",
                   "aware": "2026-10-01T09:30:00+00:00", "none": None, "long": long,
                   "nested": {"d": 2.0, "list": [str(SPAN_A2)]}, "n": 3, "ok": True}
    assert json.loads(json.dumps(out)) == out
