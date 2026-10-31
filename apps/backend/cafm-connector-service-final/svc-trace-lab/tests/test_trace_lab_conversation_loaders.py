"""Trace Lab conversation loaders without a database: each loader's SQL is bound and scoped, and its rows are
shaped, redacted and normalized as the routes return them. A fake connection answers each execute in turn."""
from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal

import pytest

from src.services.principal import Principal
from trace_lab import conversations as cv

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=tz.utc)
ORG = "11111111-1111-4111-8111-111111111111"
ADMIN = Principal(user_id=uuid.uuid4(), email="ada@example.com", organization_id=uuid.UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)
BIND = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]


class FakeConn:
    """Answers each execute with the next canned row list and records the SQL and parameters it was sent.
    An answer that is an Exception is raised instead (a missing table, say)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        answer = self.answers.pop(0) if self.answers else []
        if isinstance(answer, Exception):
            raise answer
        return _Result(answer)

    @asynccontextmanager
    async def begin_nested(self):
        yield self


def use(monkeypatch, conn):
    @asynccontextmanager
    async def fake_read():
        yield conn
    monkeypatch.setattr(cv, "_read", fake_read)


def assert_bound_and_scoped(conn):
    for sql, params in conn.calls:
        missing = set(BIND.findall(sql)) - set(params)
        assert not missing, (missing, sql[:300])
        if "agent_trace_turns tt" in sql:
            assert "tt.organization_id = CAST(:org AS uuid)" in sql and params["org"] == ORG, sql[:300]


def conv_row(key, sid, last, **kw):
    r = {"group_key": key, "session_kind": "observed", "session_id": sid, "title": None, "first_turn_at": last,
         "last_turn_at": last, "turns_in_window": 2, "matching_turns": 1, "failed_turns": 0, "cost_usd": Decimal("0.031"),
         "channels": ["chat"], "channel_sources": ["inferred"], "first_question": "How many boilers?",
         "asker_email": "fm@example.com", "thread_deleted_at": None, "matched_turn_id": None, "total": 3}
    r.update(kw)
    return r


def turn_row(tid, minutes, **kw):
    r = {"turn_id": tid, "session_id": "a", "organization_id": uuid.UUID(ORG), "user_id": uuid.uuid4(),
         "email": "fm@example.com", "question": "How many boilers?", "answer": "Four.", "route": None,
         "started_at": NOW + timedelta(minutes=minutes), "ended_at": NOW + timedelta(minutes=minutes, seconds=5),
         "latency_ms": 5000, "llm_calls": 1, "tool_calls": 0, "cost_usd": Decimal("0.01"), "cost_complete": True,
         "input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 0, "models": ["claude-sonnet-5"], "tools": [],
         "ok": True, "error": None, "feedback_rating": None, "feedback_comment": None, "feedback_by": None,
         "feedback_at": None, "created_at": NOW, "thread_title": "Boiler certificates", "thread_deleted_at": None,
         "thread_exists": True}
    r.update(kw)
    return r


def sp(id, parent, seq, kind, name, **kw):
    r = {"id": id, "turn_id": "turn-1", "parent_id": parent, "seq": seq, "kind": kind, "name": name, "agent": "orchestrator",
         "model": None, "started_at": NOW, "ended_at": NOW, "latency_ms": 1, "input": None, "output": None,
         "input_tokens": None, "output_tokens": None, "cache_read_tokens": None, "cost_usd": None, "ok": True, "error": None}
    r.update(kw)
    return r


async def test_the_conversation_list_pages_and_shapes_its_rows(monkeypatch):
    clock = {"as_of": NOW, "watermark": NOW - timedelta(seconds=2)}
    rows = [conv_row("s:a", "a", NOW, title="Boiler certificates"),
            conv_row("s:report-1", "report-1", NOW - timedelta(minutes=5), session_kind="synthetic"),
            conv_row("t:turn-9", None, NOW - timedelta(minutes=9), session_kind="inferred")]
    conn = FakeConn([clock], rows, [{"group_key": "s:a", "models": ["claude-sonnet-5"], "tool_calls": 2},
                                    {"group_key": "s:a", "models": ["gpt-5.6-terra"], "tool_calls": 1}])
    use(monkeypatch, conn)
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(now=NOW, q="boilers", channel="chat"), cursor=None, limit=2)
    assert_bound_and_scoped(conn)
    assert out["total"] == 3 and out["watermark"] == (NOW - timedelta(seconds=2)).isoformat()
    assert cv.decode_cursor(out["next_cursor"]) == (NOW - timedelta(minutes=5), "s:report-1")
    a, b = out["conversations"]
    assert a["session_label"] == "Boiler certificates" and a["models"] == ["claude-sonnet-5", "gpt-5.6-terra"]
    assert a["tool_count"] == 3 and a["cost_usd"] == 0.031 and a["flags"] == [] and a["run_id"] == "live"
    assert a["channel_sources"] == ["inferred"] and a["asker"] == {"email": "fm@example.com"}
    assert b["session_label"] == "Report card run" and b["flags"] == ["synthetic_session"]
    page_sql, page_params = conn.calls[1]
    assert page_params["limit_plus_one"] == 3 and "turn_meta" not in page_sql
    assert "channel = ANY(CAST(:f_channels AS text[]))" in page_sql and page_params["f_q"] == "%boilers%"
    assert conn.calls[2][1]["keys"] == ["s:a", "s:report-1"]


async def test_an_empty_list_and_a_late_page(monkeypatch):
    use(monkeypatch, FakeConn([{"as_of": NOW, "watermark": NOW}], []))
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(now=NOW), cursor=None, limit=500)
    assert out["total"] == 0 and out["conversations"] == [] and out["next_cursor"] is None
    conn = FakeConn([{"as_of": NOW, "watermark": NOW}], [])
    use(monkeypatch, conn)
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(now=NOW), cursor=cv.encode_cursor(NOW, "s:a"), limit=5)
    assert out["total"] is None and conn.calls[1][1]["ck"] == "s:a"
    assert conn.calls[1][1]["limit_plus_one"] == 6


async def test_a_conversation_reads_every_header_and_normalizes_the_active_turn(monkeypatch):
    turns = [turn_row("turn-1", 0), turn_row("turn-2", 1, question="And at B-302?")]
    spans = [sp(uuid.uuid4(), None, 0, "turn", "turn", turn_id="turn-2")]
    conn = FakeConn(turns, spans, [{"payload": {"message": "And at B-302?", "mode": "stream"}, "created_at": NOW}])
    use(monkeypatch, conn)
    out = await cv.get_conversation(ADMIN, ORG, "s:a", turn_id="turn-2")
    assert_bound_and_scoped(conn)
    assert "agent_activity_log" in conn.calls[2][0] and conn.calls[2][1] == {"sid": "a", "tid": "turn-2"}
    assert out["key"] == "s:a" and out["session_kind"] == "observed" and out["session_label"] == "Boiler certificates"
    assert [h["turn_index"] for h in out["turns"]] == [1, 2] and out["turn_count"] == 2 and not out["turns_truncated"]
    assert out["turns"][0]["channel_source"] == "inferred" and out["turns"][1]["channel"] == "chat"
    act = out["active"]
    assert act["id"] == "turn-2" and act["meta"]["turn_index"] == 2 and act["meta"]["conversation_key"] == "s:a"
    assert act["input"] == "And at B-302?" and out["run"] == {"run_id": "live", "kind": "live"}
    assert act["meta"]["channel_source"] == "inferred" and act["meta"]["classifier_version"] == "inferred-1"
    assert "tt.session_id = :sid" in conn.calls[0][0] and conn.calls[0][1]["key"] == "s:a"
    assert "th.organization_id IS NOT DISTINCT FROM tt.organization_id" in conn.calls[0][0]
    assert await cv.get_conversation(ADMIN, ORG, "x:bad") is None


async def test_without_a_turn_the_first_turn_is_active(monkeypatch):
    use(monkeypatch, FakeConn([turn_row("turn-1", 0), turn_row("turn-2", 1)], [], []))
    out = await cv.get_conversation(ADMIN, ORG, "s:a")
    assert out["active"]["id"] == "turn-1"
    use(monkeypatch, FakeConn([turn_row("turn-1", 0)]))
    assert await cv.get_conversation(ADMIN, ORG, "s:a", turn_id="turn-nope") is None


async def test_the_activity_row_repairs_the_question_and_refines_the_channel(monkeypatch):
    wrapped = "ctx\n\n---\n\n**Current user message:**\nWrapped?"
    support = {"message": "Typed words", "extra_context": "This is a Hoistra support session. Guide."}
    use(monkeypatch, FakeConn([turn_row("turn-1", 0, question=wrapped)], [], [{"payload": support, "created_at": NOW}]))
    out = await cv.get_conversation(ADMIN, ORG, "s:a")
    assert out["active"]["input"] == "Typed words" and "question_repaired" in out["active"]["flags"]
    assert "spans_purged" in out["active"]["flags"] and out["active"]["meta"]["channel"] == "support"
    assert out["turns"][0]["channel"] == "support"


async def test_a_missing_activity_table_is_only_missing_evidence(monkeypatch):
    use(monkeypatch, FakeConn([turn_row("turn-1", 0)], [], RuntimeError('relation "agent_activity_log" does not exist')))
    out = await cv.get_conversation(ADMIN, ORG, "s:a")
    assert out["active"]["id"] == "turn-1" and out["active"]["meta"]["channel"] == "chat"


async def test_a_deleted_thread_is_hidden_unless_asked(monkeypatch):
    turns = [turn_row("turn-1", 0, thread_deleted_at=NOW)]
    use(monkeypatch, FakeConn(list(turns)))
    assert await cv.get_conversation(ADMIN, ORG, "s:a") is None
    use(monkeypatch, FakeConn(list(turns), [], []))
    out = await cv.get_conversation(ADMIN, ORG, "s:a", include_deleted=True)
    assert out["thread_deleted"] is True and "thread_deleted" in out["flags"]
    assert "thread_deleted" in out["active"]["flags"]


async def test_a_turn_is_read_through_its_conversation(monkeypatch):
    conn = FakeConn([{"group_key": "r:turn-7"}], [turn_row("turn-7", 0, session_id="rerun")], [], [])
    use(monkeypatch, conn)
    out = await cv.get_turn(ADMIN, ORG, "turn-7")
    assert_bound_and_scoped(conn)
    assert out["conversation_key"] == "r:turn-7" and out["trace"]["id"] == "turn-7"
    assert "tt.turn_id = :tid" in conn.calls[1][0]
    assert await cv.get_turn(ADMIN, ORG, "x" * 81) is None
    use(monkeypatch, FakeConn([]))
    assert await cv.get_turn(ADMIN, ORG, "turn-nope") is None


async def test_updates_and_facets_build_bound_scoped_queries(monkeypatch):
    conn = FakeConn([{"as_of": NOW, "watermark": NOW - timedelta(seconds=2)}],
                    [{"new_turns": 3, "new_conversations": 2, "latest": NOW}])
    use(monkeypatch, conn)
    out = await cv.conversation_updates(ADMIN, ORG, cv.make_filter(now=NOW), watermark=NOW - timedelta(minutes=1))
    assert_bound_and_scoped(conn)
    assert (out["new_turns"], out["new_conversations"]) == (3, 2)
    assert conn.calls[1][1]["since"] == NOW - timedelta(minutes=1 + cv.UPDATE_LOOKBACK_MINUTES)
    assert "created_at > CAST(:wm AS timestamptz)" in conn.calls[1][0]
    conn = FakeConn([{"as_of": NOW, "watermark": NOW}],
                    [{"facet": "channel", "value": "chat", "n": 5}, {"facet": "channel", "value": "upload", "n": 9},
                     {"facet": "session_kind", "value": "observed", "n": 2},
                     {"facet": "model", "value": "claude-sonnet-5", "n": 4},
                     {"facet": "channel_source", "value": "inferred", "n": 14}])
    use(monkeypatch, conn)
    out = await cv.facets(ADMIN, ORG, cv.make_filter(now=NOW))
    assert_bound_and_scoped(conn)
    assert out["channels"] == [{"value": "upload", "turns": 9}, {"value": "chat", "turns": 5}]
    assert out["session_kinds"] == [{"value": "observed", "conversations": 2}]
    assert out["models"] == [{"value": "claude-sonnet-5", "turns": 4}]
    assert out["channel_sources"] == [{"value": "inferred", "turns": 14}]


async def test_model_call_rows_are_labelled_redacted_and_reasoning_free(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    base = {"turn_id": "turn-1", "kind": "llm", "name": "model", "agent": "orchestrator loop", "model": "claude-sonnet-5",
            "started_at": NOW, "latency_ms": 1800, "input_tokens": 900, "output_tokens": 40, "cache_read_tokens": 0,
            "cost_usd": Decimal("0.004"), "ok": True, "error": None, "request_kind": "loop_summary",
            "response_preview": "Use Bearer abcdefghijklmnop to call", "loop_output": True, "tool_call_requests": 1,
            "question_excerpt": "How many boilers?", "conversation_key": "s:a"}
    rows = [dict(base, span_id=uuid.uuid4()),
            dict(base, span_id=uuid.uuid4(), name="analyst", agent="compliance", request_kind="exchange",
                 loop_output=False, tool_call_requests=0,
                 response_preview="[{'type': 'thinking', 'thinking': 'secret chain', 'signature': 'abc'}, "
                                  "{'type': 'text', 'text': 'Two expire.'}]"),
            dict(base, span_id=uuid.uuid4(), name="analyst", agent="compliance", request_kind="exchange",
                 loop_output=False, tool_call_requests=0,
                 response_preview="[{'type': 'thinking', 'thinking': 'cut secret ch")]
    agg = {"calls": 3, "errors": 0, "latency_p50_ms": 1800.0, "latency_p95_ms": 1800.0, "input_tokens": 2700,
           "output_tokens": 120, "cache_read_tokens": 0, "cost_usd": Decimal("0.012"), "unpriced_calls": 0}
    by_model = [{"model": "claude-sonnet-5", "calls": 3, "errors": 0, "latency_p50_ms": 1800.0, "cost_usd": Decimal("0.012")}]
    conn = FakeConn([{"as_of": NOW, "watermark": NOW}], rows, [agg], by_model)
    use(monkeypatch, conn)
    out = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(now=NOW, calls=True), cursor=None, limit=100)
    assert_bound_and_scoped(conn)
    first, analyst, cut = out["calls"]
    assert first["label"] == "tool decision" and first["response_preview"] == "Use Bearer [redacted] to call"
    assert analyst["label"] == "analyst" and "secret chain" not in analyst["response_preview"]
    assert "Two expire." in analyst["response_preview"] and cut["response_preview"] == "[reasoning not shown]"
    assert out["aggregate"]["total_tokens"] == 2820 and out["aggregate"]["cost_usd"] == 0.012
    assert out["aggregate"]["by_model"][0]["model"] == "claude-sonnet-5" and out["next_cursor"] is None
    assert "s.kind IN ('llm', 'router')" in conn.calls[1][0] and out["retention_days"] == 90
    assert "turn_meta" not in conn.calls[1][0]
    with pytest.raises(cv.BadRequest):
        await cv.list_model_calls(ADMIN, ORG, cv.make_filter(now=NOW, calls=True), cursor=cv.encode_cursor(NOW, "s:a"), limit=10)


async def test_one_model_call_is_read_from_its_normalized_turn(monkeypatch):
    root, loop, m1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    head = turn_row("turn-1", 0, conversation_key="s:a")
    spans = [sp(root, None, 0, "turn", "turn"), sp(loop, root, 1, "agent", "orchestrator loop", agent="orchestrator loop"),
             sp(m1, loop, 2, "llm", "model", agent="orchestrator loop", model="claude-sonnet-5",
                input={"messages": 1, "last_human_message": "x", "tool_results_since_last_human": 0, "api_key": "k-1"},
                output={"content": "Four.", "tool_calls": []})]
    conn = FakeConn([head], spans)
    use(monkeypatch, conn)
    out = await cv.get_model_call(ADMIN, ORG, str(m1).upper())
    assert_bound_and_scoped(conn)
    assert out["turn_id"] == "turn-1" and out["conversation_key"] == "s:a"
    card = out["model_call"]
    assert card["span_id"] == str(m1) and card["group"] == "orchestrator loop" and card["label"] == "answer synthesis"
    assert "k-1" not in str(card)
    assert await cv.get_model_call(ADMIN, ORG, "not-a-uuid") is None
    use(monkeypatch, FakeConn([]))
    assert await cv.get_model_call(ADMIN, ORG, str(uuid.uuid4())) is None


async def test_runs_list_only_the_live_pseudo_run_in_phase_2(monkeypatch):
    live = {"started_at": NOW - timedelta(days=1), "ended_at": NOW, "conversation_count": 4, "turn_count": 9,
            "cost_usd": Decimal("0.5"), "models": ["claude-sonnet-5", "claude-haiku-4-5"]}
    conn = FakeConn([live])
    use(monkeypatch, conn)

    async def no_golden(org, *, since, until):
        return []

    monkeypatch.setattr(cv, "golden_runs_for", no_golden)
    out = await cv.list_runs(ADMIN, ORG, since=NOW - timedelta(days=30), until=NOW)
    assert_bound_and_scoped(conn)
    assert out["golden_available"] is True and [r["run_id"] for r in out["runs"]] == ["live"]
    run = out["runs"][0]
    assert run["name"] == "All live traffic" and run["turn_count"] == 9 and run["recorded"] is False
    assert run["cost_usd"] == 0.5 and run["models"] == ["claude-haiku-4-5", "claude-sonnet-5"]
