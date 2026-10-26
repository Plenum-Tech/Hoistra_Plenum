"""Trace Lab conversations without a database: filters, cursors, company scope, the SQL fragments, and the
read-only transaction every read runs in."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from src.agents import trace
from src.services.principal import Principal
from trace_lab import conversations as cv
from trace_lab import db, provenance

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ORG = "11111111-1111-4111-8111-111111111111"


def who(role="admin", org=ORG):
    return Principal(user_id=uuid.UUID("33333333-3333-4333-8333-333333333333"), email=f"{role}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def test_the_default_window_is_thirty_days_back_and_one_day_ahead():
    f = cv.make_filter(now=NOW)
    assert f.since == NOW - timedelta(days=30) and f.until == NOW + timedelta(days=1)
    assert f.run == "live" and f.reviewed == "any" and not f.include_deleted


def test_filters_are_validated_and_parsed():
    f = cv.make_filter(now=NOW, channel="chat, upload,chat", channel_source="inferred", session_kind="observed",
                       status="error", model=" gpt-5.6-terra ", q="  boilers ", user="ada@", deleted="include")
    assert f.channels == ("chat", "upload") and f.channel_sources == ("inferred",)
    assert f.session_kinds == ("observed",) and f.status == "error" and f.model == "gpt-5.6-terra"
    assert f.q == "boilers" and f.user == "ada@" and f.include_deleted
    calls = cv.make_filter(now=NOW, calls=True, agent="orchestrator loop", kind="router")
    assert isinstance(calls, cv.ModelCallFilter) and calls.kind == "router" and calls.agent == "orchestrator loop"
    assert not isinstance(cv.make_filter(now=NOW), cv.ModelCallFilter)


@pytest.mark.parametrize("kw,reason", [
    ({"since": NOW - timedelta(days=400)}, "window_too_wide"),
    ({"since": NOW, "until": NOW - timedelta(days=1)}, "bad_window"),
    ({"run": "yesterday"}, "bad_run"),
    ({"run": "golden-1a2b"}, "bad_run"),
    ({"run": str(uuid.uuid4()), "calls": True}, "golden_calls_unavailable"),
    ({"channel": "chat,email"}, "bad_channel"),
    ({"channel_source": "guessed"}, "bad_channel_source"),
    ({"session_kind": "shared"}, "bad_session_kind"),
    ({"reviewed": "maybe"}, "bad_reviewed"),
    ({"status": "maybe"}, "bad_status"),
    ({"deleted": "only"}, "bad_deleted"),
    ({"calls": True, "kind": "tool"}, "bad_kind"),
])
def test_bad_filters_are_refused_with_a_reason(kw, reason):
    with pytest.raises(cv.BadRequest) as e:
        cv.make_filter(now=NOW, **kw)
    assert e.value.reason == reason and e.value.error


@pytest.mark.parametrize("mode", ["any", "reviewed", "unreviewed", "mine"])
def test_every_reviewed_mode_is_a_valid_filter_once_open_coding_exists(mode):
    assert cv.make_filter(now=NOW, reviewed=mode).reviewed == mode


async def test_the_reviewed_filter_is_refused_until_the_review_tables_exist(monkeypatch):
    """Phase 4 moved the refusal from the query's parsing to the list: below version 3 (the review tables) a
    reviewed filter is 400 reviewed_filter_unavailable, answered before any database is touched."""
    from trace_lab.migrate import SchemaState

    def boom():
        raise AssertionError("no database may be read before the refusal")

    monkeypatch.setattr(db, "main_engine", boom)
    monkeypatch.setattr(db, "lab_engine", boom)
    f = cv.make_filter(now=NOW, reviewed="mine")
    for state in (None, SchemaState("ready", 3, 2), SchemaState("ready", 3, None)):
        monkeypatch.setattr(db, "STATE", state)
        with pytest.raises(cv.BadRequest) as e:
            await cv.list_conversations(who(), ORG, f, cursor=None, limit=10)
        assert e.value.reason == "reviewed_filter_unavailable" and e.value.error
        golden = cv.make_filter(now=NOW, reviewed="reviewed", run=str(uuid.uuid4()))
        with pytest.raises(cv.BadRequest):
            await cv.list_conversations(who(), ORG, golden, cursor=None, limit=10)


def test_the_reviewed_predicate_goes_into_the_turn_filters_with_its_ids_as_a_parameter():
    p: dict = {}
    f = cv.make_filter(now=NOW, reviewed="unreviewed", channel="chat")
    sql = cv._hit_sql(f, p, reviewed=("b", {"turn-1", "turn-2"}))
    assert "NOT (b.turn_id = ANY(CAST(:tl_rev_ids AS text[])))" in sql and "channel = ANY" in sql
    assert p["tl_rev_ids"] == ["turn-1", "turn-2"]
    p2: dict = {}
    assert cv._hit_sql(cv.make_filter(now=NOW, reviewed="mine"), p2, reviewed=("r", {"t"})) == \
        "r.turn_id = ANY(CAST(:tl_rev_ids AS text[]))" and p2 == {"tl_rev_ids": ["t"]}
    assert cv._hit_sql(cv.make_filter(now=NOW), {}, reviewed=("b", set())) == "TRUE"       # "any" never filters


def test_cursors_round_trip_and_bad_ones_are_refused():
    c = cv.encode_cursor(NOW, "s:3b1f")
    assert "=" not in c and cv.decode_cursor(c) == (NOW, "s:3b1f")
    assert cv.decode_cursor(None) is None and cv.decode_cursor("") is None
    for bad in ("%%%", "bm90IGpzb24", cv.encode_cursor(NOW, "k")[:-3] + "!!!", "x" * 401):
        with pytest.raises(cv.BadRequest) as e:
            cv.decode_cursor(bad)
        assert e.value.reason == "bad_cursor"


def test_scope_reuses_svc_deepagents_trace_scope_qualified_by_alias(monkeypatch):
    where, params = cv.scope_sql(who("admin"), ORG)
    assert where == "tt.organization_id = CAST(:org AS uuid)" and params == {"org": ORG}
    where, params = cv.scope_sql(who("superadmin", org=None), ORG, alias="t2")
    assert where == "t2.organization_id = CAST(:org AS uuid)" and params == {"org": ORG}
    where, params = cv.scope_sql(who("user"), ORG)
    assert where == "tt.organization_id = CAST(:org AS uuid) AND tt.user_id = CAST(:uid AS uuid)"
    assert params == {"org": ORG, "uid": "33333333-3333-4333-8333-333333333333"}
    seen = []

    def spy(principal, org):
        seen.append((principal.role, org))
        return "organization_id = CAST(:org AS uuid)", {"org": org}

    monkeypatch.setattr(trace, "_scope_sql", spy)
    cv.scope_sql(who("admin"), ORG)
    assert seen == [("admin", ORG)]                      # reused by import, never re-implemented


def test_scope_refuses_no_company_and_a_bad_alias():
    for org in (None, ""):
        with pytest.raises(ValueError):
            cv.scope_sql(who("superadmin", org=None), org)
    with pytest.raises(ValueError):
        cv.scope_sql(who("admin"), ORG, alias="tt; drop")


def test_search_text_is_escaped_for_ilike():
    assert cv._like("50%_off\\") == "%50\\%\\_off\\\\%"


def test_turn_filters_bind_every_value():
    p: dict = {}
    f = cv.make_filter(now=NOW, channel="chat", channel_source="inferred", session_kind="observed", status="ok",
                       model="m", q="boilers", user="ada")
    sql = cv._hit_sql(f, p)
    assert sql.count(" AND ") == 6
    assert p == {"f_channels": ["chat"], "f_sources": ["inferred"], "f_kinds": ["observed"], "f_model": "m",
                 "f_q": "%boilers%", "f_q_exact": "boilers", "f_user": "%ada%"}
    assert cv._hit_sql(cv.make_filter(now=NOW), {}) == "TRUE"
    assert cv._run_sql(cv.make_filter(now=NOW)) == "channel <> 'golden'"
    assert cv._deleted_sql(cv.make_filter(now=NOW)) == "thread_deleted_at IS NULL"
    assert cv._deleted_sql(cv.make_filter(now=NOW, deleted="include")) == "TRUE"
    p2: dict = {}
    calls = cv.make_filter(now=NOW, calls=True, channel="chat", status="error", model="m", agent="a", kind="llm", q="x")
    sql2 = cv._call_filters_sql(calls, p2, channel="CH", source="SRC")
    assert "CH = ANY(CAST(:f_channels AS text[]))" in sql2 and "NOT s.ok" in sql2 and "s.kind = :f_kind" in sql2
    assert p2 == {"f_channels": ["chat"], "f_model": "m", "f_agent": "a", "f_kind": "llm", "f_q": "%x%", "f_q_exact": "x"}


def test_the_group_key_session_kind_and_thread_join_sql():
    for piece in ("'t:' || tt.turn_id", "LIKE 'rerun%' THEN 'r:' || tt.turn_id", "'s:' || tt.session_id"):
        assert piece in cv.GROUP_KEY_SQL
    for prefix in ("golden-%", "report-%", "docs-%", "ing-%", "rerun%", "skilllab-%"):
        assert f"LIKE '{prefix}'" in cv.SESSION_KIND_SQL
    assert "th.id IS NOT NULL THEN 'observed'" in cv.SESSION_KIND_SQL
    assert "th.organization_id IS NOT DISTINCT FROM tt.organization_id" in cv.THREAD_JOIN
    base = cv._base_sql("tt.organization_id = CAST(:org AS uuid)")
    assert provenance.INFERRED_CHANNEL_SQL in base and cv.THREAD_JOIN in base
    assert "turn_meta" not in base and "plenum_tracelab" not in base
    assert cv.TURNS == trace.TURNS and cv.SPANS == trace.SPANS


class _Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append(str(stmt))


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    @asynccontextmanager
    async def begin(self):
        yield self.conn


async def test_every_read_is_a_read_only_bounded_transaction_on_the_main_engine(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(db, "main_engine", lambda: engine)
    async with cv._read() as conn:
        assert conn is engine.conn
    assert engine.conn.calls == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '10s'"]
