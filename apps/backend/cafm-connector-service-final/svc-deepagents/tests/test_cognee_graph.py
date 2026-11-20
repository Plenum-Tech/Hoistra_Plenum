"""cognee graph memory beside Memorilla (services/cognee_graph.py): gating, the cognee 1.6 config,
ingest/forget of company facts, graph context, the combined recall block.

Design: docs/superpowers/specs/2026-11-20-memorilla-cognee-hybrid-design.md. Memorilla decides
which memories bear on a question; cognee adds how they connect.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

from src.config import settings
from src.services import cognee_graph as cg

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
COG_URL = "postgresql://cog:cog@localhost:5462/cognee"


def _on(monkeypatch, companies="*", mode="shadow"):
    monkeypatch.setattr(settings, "cognee_memory_companies", companies)
    monkeypatch.setattr(settings, "cognee_db_url", COG_URL)
    monkeypatch.setattr(settings, "cognee_memory_mode", mode)


# --- Task 1: gating and configuration ----------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_companies", "")
    monkeypatch.setattr(settings, "cognee_db_url", "")
    assert cg.enabled(ORG_A) is False


def test_company_list_scopes(monkeypatch):
    _on(monkeypatch, companies=ORG_A)
    assert cg.enabled(ORG_A) is True
    assert cg.enabled(ORG_B) is False


def test_flag_without_db_url_stays_off(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_companies", "*")
    monkeypatch.setattr(settings, "cognee_db_url", "")
    assert cg.enabled(ORG_A) is False


def test_empty_org_never_enabled(monkeypatch):
    _on(monkeypatch)
    assert cg.enabled("") is False
    assert cg.enabled(None) is False


def test_refuses_main_db_url_in_any_spelling(monkeypatch):
    """The house recipe is 'strip +asyncpg and ?params' — that form of the main URL must still be
    refused: cognee creates and writes its own tables, and the main database is production."""
    _on(monkeypatch)
    monkeypatch.setattr(
        settings, "db_url", "postgresql+asyncpg://app:pw@prod-host:5432/hoistra?sslmode=require"
    )
    for variant in [
        "postgresql://app:pw@prod-host:5432/hoistra",
        "postgres://app:pw@prod-host:5432/hoistra",
        "postgresql+psycopg://app:pw@prod-host:5432/hoistra?sslmode=disable",
        "postgresql://app:pw@prod-host/hoistra",
    ]:
        monkeypatch.setattr(settings, "cognee_db_url", variant)
        assert cg.enabled(ORG_A) is False, variant


def test_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_mode", "")
    assert cg.live() is False


def test_live_mode(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_mode", "live")
    assert cg.live() is True
    monkeypatch.setattr(settings, "cognee_memory_mode", "LIVE ")
    assert cg.live() is True


def test_cognee_config_matches_cognee_1_6_fields(monkeypatch):
    """cognee 1.6.10 has no connection-string field; relational wants str ports, pgvector and the
    Postgres graph int ports; per-dataset handlers give every company its own database."""
    monkeypatch.setattr(settings, "cognee_db_url", "postgresql://cog:s3cr%40t@cognee-pg:5462/cogdb")
    monkeypatch.setattr(settings, "openai_model", "gpt-5.6-terra")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    cfg = cg._cognee_settings()
    assert cfg["relational"] == {
        "db_provider": "postgres",
        "db_host": "cognee-pg",
        "db_port": "5462",
        "db_username": "cog",
        "db_password": "s3cr@t",
        "db_name": "cogdb",
    }
    assert cfg["vector"]["vector_db_port"] == 5462
    assert cfg["vector"]["vector_dataset_database_handler"] == "pgvector"
    assert cfg["graph"]["graph_database_provider"] == "postgres"
    assert cfg["graph"]["graph_dataset_database_handler"] == "postgres_graph"
    assert cfg["llm"]["llm_model"] == "openai/gpt-5.6-terra"
    assert cfg["embedding"] == {
        "embedding_provider": "openai",
        "embedding_model": "openai/text-embedding-3-small",
        "embedding_dimensions": 1536,
        "embedding_api_key": "sk-test",
    }


class _FakeConfig:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        return lambda arg: self.calls.append((name, arg))


def test_configure_turns_off_cognee_session_memory(monkeypatch):
    """cognee's session memory records every search into one shared session (one default cognee
    user for every company): off, unless an operator set CACHING explicitly."""
    monkeypatch.setitem(sys.modules, "cognee", types.SimpleNamespace(config=_FakeConfig()))
    monkeypatch.setattr(cg, "_configured", False)
    monkeypatch.setattr(settings, "cognee_db_url", COG_URL)
    monkeypatch.delenv("CACHING", raising=False)
    cg._configure_cognee()
    assert os.environ["CACHING"] == "false"


def test_dataset_is_per_company_and_lowercase():
    assert cg._dataset("AbC-1") == "org_abc-1"


# --- Task 2: company facts in, forgotten facts out ---------------------------------------------


class _Calls:
    def __init__(self, raise_exc: Exception | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.raise_exc = raise_exc

    async def __call__(self, org_id, texts):
        self.calls.append((org_id, list(texts)))
        if self.raise_exc:
            raise self.raise_exc


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)


def _item(
    kind="fact",
    text="Pennard Fire Services is blocked at Bishopsgate Tower.",
    subject="Bishopsgate Tower",
    personal=False,
    active=True,
):
    return {"kind": kind, "text": text, "subject": subject, "personal": personal, "active": active}


def test_fact_text_is_memorillas_wording():
    from src.services import memorilla

    assert cg.fact_text("fact", "X is blocked.", "Bishopsgate") == memorilla.memory_text(
        "fact", "X is blocked.", "Bishopsgate"
    )


async def test_only_active_company_facts_and_corrections_enter_the_graph(monkeypatch):
    _on(monkeypatch)
    add = _Calls()
    monkeypatch.setattr(cg, "_cognee_add", add)
    cg.add_memories_soon(
        ORG_A,
        [
            _item(),
            _item(kind="correction", text="The plant room is on level 3."),
            _item(kind="preference", text="Costs in AED.", personal=True),
            _item(text="Pending fact.", active=False),
            _item(text="Someone's own fact.", personal=True),
        ],
    )
    await _drain()
    assert add.calls == [
        (
            ORG_A,
            [
                cg.fact_text(
                    "fact",
                    "Pennard Fire Services is blocked at Bishopsgate Tower.",
                    "Bishopsgate Tower",
                ),
                cg.fact_text("correction", "The plant room is on level 3.", "Bishopsgate Tower"),
            ],
        )
    ]


async def test_nothing_enters_the_graph_when_off_or_nothing_qualifies(monkeypatch):
    add = _Calls()
    monkeypatch.setattr(cg, "_cognee_add", add)
    monkeypatch.setattr(settings, "cognee_memory_companies", "")
    cg.add_memories_soon(ORG_A, [_item()])
    _on(monkeypatch)
    cg.add_memories_soon(ORG_A, [_item(active=False)])
    await _drain()
    assert add.calls == []


async def test_a_graph_write_error_never_reaches_the_caller(monkeypatch):
    _on(monkeypatch)
    add = _Calls(raise_exc=RuntimeError("cognee down"))
    monkeypatch.setattr(cg, "_cognee_add", add)
    cg.add_memories_soon(ORG_A, [_item()])
    await _drain()
    assert len(add.calls) == 1 and not cg._pending


async def test_forget_removes_company_facts_by_their_text(monkeypatch):
    _on(monkeypatch)
    forget = _Calls()
    monkeypatch.setattr(cg, "_cognee_forget", forget)
    cg.forget_memories_soon(
        ORG_A, [_item(), _item(kind="preference", text="Costs in AED.", personal=True)]
    )
    await _drain()
    assert forget.calls == [
        (
            ORG_A,
            [
                cg.fact_text(
                    "fact",
                    "Pennard Fire Services is blocked at Bishopsgate Tower.",
                    "Bishopsgate Tower",
                )
            ],
        )
    ]


# --- Task 3: reading the graph ------------------------------------------------------------------

#: cognee 1.6.10's context-only output, 20 Nov 2026 (fire-door question, Plenum export), trimmed.
REAL_CONTEXT = """Nodes:
Node: fire risk assessment at bishopsgate tower
__node_content_start__
Fire Risk Assessment with significant findings, including noted compartmentation breaches.
__node_content_end__

Connections:
fire risk assessment at bishopsgate tower --[requires]--> open fire-risk remedials at bishopsgate tower  (Fire Risk Assessment at Bishopsgate Tower requires Open Fire-Risk Remedials at Bishopsgate Tower to address significant fire-risk findings.)
Question: Which buildings are out of compliance... [bishopsgate, tower, fire] --[contains]--> open fire-risk remedials at bishopsgate tower  (Document chunk mentions open fire-risk remedials at bishopsgate tower: Open remedial work arising from significant fire-risk findings at Bishopsgate Tower; requires urgent progress.)
bishopsgate tower --[requires_urgent_progress]--> open fire-risk remedials at bishopsgate tower  (Bishopsgate Tower requires urgent progress on Open Fire-Risk Remedials at Bishopsgate Tower.)
Question: Which buildings are out of compliance... [bishopsgate, tower, fire] --[contains]--> fire risk assessment at bishopsgate tower  (Document chunk mentions fire risk assessment at bishopsgate tower: Fire Risk Assessment for Bishopsgate Tower; lapsed, with significant fire-risk findings and remedial work still open.)
"""  # noqa: E501 — cognee's output verbatim


def test_parse_edges_reads_cognee_connections():
    edges = cg.parse_edges(REAL_CONTEXT)
    assert len(edges) == 4
    assert edges[0] == (
        "fire risk assessment at bishopsgate tower",
        "requires",
        "open fire-risk remedials at bishopsgate tower",
        "Fire Risk Assessment at Bishopsgate Tower requires Open Fire-Risk Remedials at "
        "Bishopsgate Tower to address significant fire-risk findings.",
    )
    assert edges[1][0].startswith("Question: Which buildings") and edges[1][1] == "contains"


def test_parse_edges_reads_only_the_connections_section():
    text_ = (
        "Nodes:\nNode: x\n__node_content_start__\nnot --[an]--> edge, this is node text\n"
        "__node_content_end__\n\nConnections:\nboiler --[serves]--> level 3\n"
    )
    assert cg.parse_edges(text_) == [("boiler", "serves", "level 3", "")]
    assert cg.parse_edges("") == []
    assert cg.parse_edges(None) == []


def test_parse_edges_tolerates_the_closing_backtick():
    """Live smoke 20 Nov: cognee wraps the whole context in backticks, so the last connection line
    ends with "`" — its description must still split off."""
    text_ = (
        "Connections:\nbishopsgate tower --[has_draft_work_order]--> "
        "bishopsgate tower draft work order  "
        "(Bishopsgate Tower has a draft work order that should be progressed urgently.)`"
    )
    assert cg.parse_edges(text_) == [
        (
            "bishopsgate tower",
            "has_draft_work_order",
            "bishopsgate tower draft work order",
            "Bishopsgate Tower has a draft work order that should be progressed urgently.",
        )
    ]


async def test_an_empty_company_graph_is_no_edges_not_a_failure(monkeypatch):
    """Live smoke 20 Nov: a company with nothing in its graph yet (or everything forgotten) makes
    cognee raise NoDataError — that is "no connections", not a failed read."""
    _on(monkeypatch)

    class NoDataError(Exception):
        pass

    monkeypatch.setattr(
        cg,
        "_cognee_context",
        _Ctx(raise_exc=NoDataError("No searchable memory in dataset 'org_x'")),
    )
    out = await cg.graph_context("q", ORG_A)
    assert out["edges"] == [] and out["error"] is None


class _Ctx:
    def __init__(self, text="", raise_exc=None, delay_s=0.0):
        self.text, self.raise_exc, self.delay_s = text, raise_exc, delay_s
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, org_id, question):
        self.calls.append((org_id, question))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raise_exc:
            raise self.raise_exc
        return self.text


async def test_graph_context_none_when_off(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_companies", "")
    ctx = _Ctx(REAL_CONTEXT)
    monkeypatch.setattr(cg, "_cognee_context", ctx)
    assert await cg.graph_context("q", ORG_A) is None and ctx.calls == []


async def test_graph_context_returns_edges_for_the_company(monkeypatch):
    _on(monkeypatch)
    ctx = _Ctx(REAL_CONTEXT)
    monkeypatch.setattr(cg, "_cognee_context", ctx)
    out = await cg.graph_context("fire doors?", ORG_A)
    assert ctx.calls == [(ORG_A, "fire doors?")]
    assert len(out["edges"]) == 4 and out["error"] is None and out["latency_ms"] >= 0


async def test_graph_context_slow_or_failing_gives_no_edges(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(cg, "GRAPH_TIMEOUT_S", 0.05)
    monkeypatch.setattr(cg, "_cognee_context", _Ctx(REAL_CONTEXT, delay_s=2))
    slow = await cg.graph_context("q", ORG_A)
    assert slow["edges"] == [] and slow["error"] == "timeout"
    monkeypatch.setattr(cg, "_cognee_context", _Ctx(raise_exc=ValueError("boom")))
    bad = await cg.graph_context("q", ORG_A)
    assert bad["edges"] == [] and "boom" in bad["error"]


# --- Task 4: the graph picks which remembered facts connect ---------------------------------------
# Offline comparison 20 Nov (compare_hybrid.py, 9 questions x 3 runs): entity-to-entity edges added
# little (1-5 per question, mostly "x located_in building"); the graph's value is WHICH stored facts
# it links to the question's entities — e.g. AHU-3 -> "the vendor for AHU-3 is Apex Mechanical" ->
# "Apex Mechanical must not be instructed ... CHAS". Linked facts join "What I remember" verbatim.

AHU_Q = "AHU-3 at Bishopsgate needs a service. Who do we send?"
VENDOR = "At Bishopsgate Tower, the vendor for AHU-3 is Apex Mechanical."
HOLD = (
    "At Bishopsgate Tower, Apex Mechanical must not be instructed for CHW pump P1 or AHU-7 "
    "until its CHAS Premium Plus / SSIP Certificate issue is cleared."
)
DEC = "At Manchester Town Hall, there is a lapsed Display Energy Certificate."

#: cognee 1.6.10's shape for a facts-only graph (20 Nov probe): fact nodes named by a truncated
#: preview, their full text as node content, and "contains" edges to the entities they mention.
FACT_CONTEXT = f"""Nodes:
Node: [company fact] At Bishopsgate Tower, the vendor for AHU-3... [bishopsgate, tower, company]
__node_content_start__
[company fact] {VENDOR} Subject: Bishopsgate Tower.
__node_content_end__

Node: [company fact] At Bishopsgate Tower, Apex Mechanical... [bishopsgate, tower, company]
__node_content_start__
[company fact] {HOLD} Subject: Bishopsgate Tower.
__node_content_end__

Node: [company fact] At Manchester Town Hall... [manchester, town, hall]
__node_content_start__
[company fact] {DEC} Subject: Manchester Town Hall.
__node_content_end__

Node: ahu-3
__node_content_start__
Air handling unit 3 at Bishopsgate Tower.
__node_content_end__

Connections:
[company fact] At Bishopsgate Tower, the vendor for AHU-3... [bishopsgate, tower, company] --[contains]--> ahu-3  (Document chunk mentions ahu-3: Equipment)
[company fact] At Bishopsgate Tower, Apex Mechanical... [bishopsgate, tower, company] --[contains]--> apex mechanical  (Document chunk mentions apex mechanical: Organization)
[company fact] At Manchester Town Hall... [manchester, town, hall] --[contains]--> display energy certificate  (Document chunk mentions display energy certificate)
ahu-3 --[located_in]--> bishopsgate tower
"""  # noqa: E501 — cognee's output shape verbatim


def _mem(n, text_, kind="fact", subject="Bishopsgate Tower"):
    return {
        "id": f"m{n}",
        "kind": kind,
        "text": text_,
        "subject": subject,
        "user_id": None,
        "created_by_email": None,
        "created_at": None,
    }


def _g(context=FACT_CONTEXT, error=None):
    return {
        "edges": cg.parse_edges(context),
        "nodes": cg.parse_nodes(context),
        "latency_ms": 40,
        "error": error,
    }


def test_parse_nodes_reads_names_and_contents():
    nodes = cg.parse_nodes(FACT_CONTEXT)
    assert nodes["ahu-3"] == "Air handling unit 3 at Bishopsgate Tower."
    assert (
        VENDOR
        in nodes[
            "[company fact] At Bishopsgate Tower, the vendor for AHU-3... "
            "[bishopsgate, tower, company]"
        ]
    )
    assert cg.parse_nodes("") == {} and cg.parse_nodes(None) == {}


async def test_graph_context_also_returns_nodes(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(cg, "_cognee_context", _Ctx(FACT_CONTEXT))
    out = await cg.graph_context(AHU_Q, ORG_A)
    assert len(out["nodes"]) == 4 and len(out["edges"]) == 4


def test_linked_texts_follow_the_question_and_the_recalled_memories():
    """No hits: the question's AHU-3 reaches the vendor fact only. With the vendor fact recalled,
    its Apex Mechanical reaches the hold — the second hop. Manchester's DEC is never linked."""
    alone = cg.linked_texts(AHU_Q, [], _g())
    assert len(alone) == 1 and VENDOR in alone[0]
    two_hop = cg.linked_texts(AHU_Q, [_mem(1, VENDOR)], _g())
    assert any(HOLD in t for t in two_hop) and not any(DEC in t for t in two_hop)


def test_linked_texts_empty_without_a_graph():
    assert cg.linked_texts(AHU_Q, [], None) == []
    assert cg.linked_texts(AHU_Q, [], _g("", error="timeout")) == []


# --- Task 5: the recall hook (graph_for + linked_memories) -------------------------------------


@pytest.fixture
def spans(monkeypatch):
    from src.agents import trace

    got: list[dict] = []
    monkeypatch.setattr(
        trace,
        "on_memory",
        lambda name, inputs, output, **kw: got.append(
            {"name": name, "inputs": inputs, "output": output, **kw}
        ),
    )
    return got


@pytest.fixture
def visible(monkeypatch):
    """The caller's visible memories (chat_memories.visible): what may be recalled at all."""
    from src.services import chat_memories as cm

    rows = [_mem(1, VENDOR), _mem(2, HOLD), _mem(3, DEC, subject="Manchester Town Hall")]
    calls = []

    async def fake(include_pending=False):
        calls.append(include_pending)
        return list(rows)

    monkeypatch.setattr(cm, "visible", fake)
    return rows, calls


async def test_graph_for_reads_the_callers_company(monkeypatch):
    from src.http_client import caller_organization_id

    _on(monkeypatch)
    ctx = _Ctx(REAL_CONTEXT)
    monkeypatch.setattr(cg, "_cognee_context", ctx)
    token = caller_organization_id.set(ORG_A)
    try:
        g = await cg.graph_for("fire doors?")
    finally:
        caller_organization_id.reset(token)
    assert ctx.calls == [(ORG_A, "fire doors?")] and len(g["edges"]) == 4


async def test_graph_for_none_when_off(monkeypatch):
    monkeypatch.setattr(settings, "cognee_memory_companies", "")
    assert await cg.graph_for("q") is None


async def test_live_adds_the_linked_memories_the_caller_may_see(monkeypatch, spans, visible):
    _on(monkeypatch, mode="live")
    rows, _ = visible
    out = await cg.linked_memories(AHU_Q, [rows[0] | {"score": 0.7}], _g())
    assert [m["id"] for m in out] == ["m2"]  # the hold; the vendor was already recalled
    (span,) = spans
    assert span["name"] == "graph recall" and span["inputs"]["mode"] == "live"
    assert span["output"]["linked"] == [{"id": "m2", "text": HOLD[:200]}] and span["ok"] is True


async def test_shadow_traces_the_linked_memories_but_adds_none(monkeypatch, spans, visible):
    _on(monkeypatch, mode="shadow")
    rows, _ = visible
    assert await cg.linked_memories(AHU_Q, [rows[0]], _g()) == []
    assert spans[0]["inputs"]["mode"] == "shadow" and [
        x["id"] for x in spans[0]["output"]["linked"]
    ] == ["m2"]


async def test_a_fact_the_caller_may_not_see_is_never_linked(monkeypatch, spans, visible):
    """Forgotten, pending or another company's: still in a stale graph, but not in visible()."""
    _on(monkeypatch, mode="live")
    rows, _ = visible
    rows[:] = [r for r in rows if r["id"] != "m2"]
    assert await cg.linked_memories(AHU_Q, [_mem(1, VENDOR)], _g()) == []


async def test_linked_memories_are_capped(monkeypatch, spans, visible):
    _on(monkeypatch, mode="live")
    rows, _ = visible
    many = [
        _mem(10 + i, f"At Bishopsgate Tower, riser R{i} needs fire stopping.") for i in range(10)
    ]
    rows[:] = many
    nodes = "".join(
        f"Node: f{i}\n__node_content_start__\n{cg.fact_text('fact', m['text'], m['subject'])}"
        "\n__node_content_end__\n\n"
        for i, m in enumerate(many)
    )
    edges = "".join(f"f{i} --[contains]--> bishopsgate tower\n" for i in range(10))
    out = await cg.linked_memories(
        "Fire stopping at Bishopsgate Tower?", [], _g("Nodes:\n" + nodes + "Connections:\n" + edges)
    )
    assert len(out) == cg.LINKED_CAP == 4


async def test_no_graph_no_lookup_no_span(monkeypatch, spans, visible):
    _on(monkeypatch, mode="live")
    _, calls = visible
    assert await cg.linked_memories(AHU_Q, [], None) == []
    assert spans == [] and calls == []


async def test_a_failed_graph_read_is_traced_and_adds_nothing(monkeypatch, spans, visible):
    _on(monkeypatch, mode="live")
    assert await cg.linked_memories(AHU_Q, [], _g("", error="timeout")) == []
    assert spans[0]["ok"] is False and spans[0]["error"] == "timeout"


async def test_linked_memories_never_raise(monkeypatch, spans):
    from src.services import chat_memories as cm

    _on(monkeypatch, mode="live")

    async def boom(include_pending=False):
        raise RuntimeError("db down")

    monkeypatch.setattr(cm, "visible", boom)
    assert await cg.linked_memories(AHU_Q, [_mem(1, VENDOR)], _g()) == []


# chat_memories hooks: store / review / forget, on a fake database (pattern of test_chat_memories)


class _Res:
    def __init__(self, rows=None, rowcount=1):
        self._rows, self.rowcount = rows or [], rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.store["sql"].append(sql)
        for needle, rows in self.store["answers"]:
            if needle in sql:
                return _Res(rows, rowcount=len(rows) or 1)
        return _Res([], rowcount=1)


class _Engine:
    def __init__(self, store):
        self.store = store

    def begin(self):
        return _Conn(self.store)

    def connect(self):
        return _Conn(self.store)


@pytest.fixture
def memdb(monkeypatch):
    import uuid

    import src.database as database
    from src.http_client import caller_organization_id
    from src.services import chat_memories as cm
    from src.services.principal import Principal, caller_principal

    store = {"sql": [], "answers": []}
    monkeypatch.setattr(database, "_get_engine", lambda: _Engine(store))
    monkeypatch.setattr(cm, "_ready", True)

    async def no_embed(texts):
        return None

    monkeypatch.setattr(cm, "embed", no_embed)
    calls = {"add": [], "forget": []}
    monkeypatch.setattr(
        cg, "add_memories_soon", lambda org, items: calls["add"].append((org, items))
    )
    monkeypatch.setattr(
        cg, "forget_memories_soon", lambda org, items: calls["forget"].append((org, items))
    )
    admin = Principal(
        user_id=uuid.uuid4(),
        email="a@example.com",
        organization_id=uuid.UUID(ORG_A),
        role="superadmin",
        can_ingest=True,
        building_ids=None,
    )
    t1, t2 = caller_principal.set(admin), caller_organization_id.set(ORG_A)
    yield store, calls, cm, admin
    caller_principal.reset(t1)
    caller_organization_id.reset(t2)


def test_store_hands_new_memories_to_the_graph_with_their_status(memdb, monkeypatch):
    store, calls, cm, admin = memdb
    _on(monkeypatch)
    asyncio.run(
        cm.store(
            [
                {
                    "kind": "fact",
                    "text": "The plant room is on level 3.",
                    "subject": "Bishopsgate Tower",
                },
                {"kind": "preference", "text": "Costs in AED.", "subject": None},
            ],
            principal=admin,
            org=ORG_A,
        )
    )
    ((org, items),) = calls["add"]
    assert org == ORG_A
    assert [(i["kind"], i["personal"], i["active"]) for i in items] == [
        ("fact", False, True),
        ("preference", True, True),
    ]


def test_approval_adds_and_forget_removes_the_fact(memdb, monkeypatch):
    store, calls, cm, admin = memdb
    _on(monkeypatch)
    row = {
        "kind": "fact",
        "text": "Pennard is blocked.",
        "subject": "Bishopsgate Tower",
        "user_id": None,
    }
    store["answers"] = [
        ("SELECT kind, text, subject, user_id FROM plenum_cafm.chat_memories", [row])
    ]
    assert asyncio.run(cm.review("4f9b2c1e-0000-4000-8000-000000000001", "approve")) is True
    assert asyncio.run(cm.forget("4f9b2c1e-0000-4000-8000-000000000001")) is True
    assert calls["add"] == [
        (
            ORG_A,
            [
                {
                    "kind": "fact",
                    "text": "Pennard is blocked.",
                    "subject": "Bishopsgate Tower",
                    "personal": False,
                    "active": True,
                }
            ],
        )
    ]
    assert calls["forget"] == [
        (
            ORG_A,
            [
                {
                    "kind": "fact",
                    "text": "Pennard is blocked.",
                    "subject": "Bishopsgate Tower",
                    "personal": False,
                    "active": True,
                }
            ],
        )
    ]


def test_with_the_graph_off_no_extra_query_runs(memdb, monkeypatch):
    store, calls, cm, admin = memdb
    monkeypatch.setattr(settings, "cognee_memory_companies", "")
    asyncio.run(cm.review("4f9b2c1e-0000-4000-8000-000000000001", "approve"))
    asyncio.run(cm.forget("4f9b2c1e-0000-4000-8000-000000000001"))
    assert not any("SELECT kind, text, subject, user_id" in s for s in store["sql"])
    assert calls == {"add": [], "forget": []}


# --- review fixes (20 Nov) ---------------------------------------------------------------------


def test_refuses_the_main_database_under_other_credentials_or_a_localhost_alias(monkeypatch):
    """Review: the guard compared usernames, so the production database under a second login
    passed. Same host (localhost == 127.0.0.1), port and database name = the same database."""
    _on(monkeypatch)
    monkeypatch.setattr(settings, "db_url", "postgresql+asyncpg://app:pw@prod-host:5432/hoistra")
    monkeypatch.setattr(settings, "cognee_db_url", "postgresql://cog:other@prod-host:5432/hoistra")
    assert cg.enabled(ORG_A) is False
    monkeypatch.setattr(settings, "db_url", "postgresql+asyncpg://app:pw@localhost:5432/hoistra")
    monkeypatch.setattr(settings, "cognee_db_url", "postgresql://cog:x@127.0.0.1/hoistra")
    assert cg.enabled(ORG_A) is False
    monkeypatch.setattr(settings, "cognee_db_url", "postgresql://cog:x@127.0.0.1:5462/cognee")
    assert cg.enabled(ORG_A) is True


def test_configure_forces_cognee_access_control_on(monkeypatch):
    """Per-company databases rest on cognee's backend access control; never let it be off."""
    monkeypatch.setitem(sys.modules, "cognee", types.SimpleNamespace(config=_FakeConfig()))
    monkeypatch.setattr(cg, "_configured", False)
    monkeypatch.setattr(settings, "cognee_db_url", COG_URL)
    monkeypatch.setenv("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    cg._configure_cognee()
    assert os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] == "true"


async def test_the_turn_stops_waiting_for_a_read_that_ignores_cancellation(monkeypatch):
    """Review: wait_for waits for the cancelled read to finish; a read that swallows the
    cancellation would hold the turn past the cap."""
    _on(monkeypatch)
    monkeypatch.setattr(cg, "GRAPH_TIMEOUT_S", 0.05)

    async def stubborn(org_id, question):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)  # ignores the cancel for a while
        return ""

    monkeypatch.setattr(cg, "_cognee_context", stubborn)
    t0 = asyncio.get_running_loop().time()
    out = await cg.graph_context("q", ORG_A)
    assert out["error"] == "timeout" and asyncio.get_running_loop().time() - t0 < 0.3


async def test_a_forget_waits_for_an_earlier_add_of_the_same_company(monkeypatch):
    """Review: a forget scheduled while the add was still building the graph found nothing and the
    fact stayed. Writes for one company now run one at a time, in order."""
    _on(monkeypatch)
    order: list[str] = []

    async def slow_add(org_id, texts):
        order.append("add start")
        await asyncio.sleep(0.05)
        order.append("add end")

    async def forget(org_id, texts):
        order.append("forget")

    monkeypatch.setattr(cg, "_cognee_add", slow_add)
    monkeypatch.setattr(cg, "_cognee_forget", forget)
    cg.add_memories_soon(ORG_A, [_item()])
    cg.forget_memories_soon(ORG_A, [_item()])
    for _ in range(20):
        await asyncio.sleep(0.01)
    assert order == ["add start", "add end", "forget"]


async def test_a_preference_is_never_linked_even_when_a_fact_contains_its_words(
    monkeypatch, spans, visible
):
    """Review: a plain substring match let the caller's own preference ride in on a company fact
    that happened to contain its sentence. Only company facts/corrections, matched whole."""
    _on(monkeypatch, mode="live")
    rows, _ = visible
    rows[:] = [
        _mem(9, "Costs in AED.", kind="preference", subject=None) | {"user_id": "me"},
        _mem(2, HOLD),
    ]
    ctx = FACT_CONTEXT.replace("CHAS Premium Plus", "Costs in AED. CHAS Premium Plus")
    out = await cg.linked_memories(AHU_Q, [_mem(1, VENDOR)], _g(ctx))
    assert [
        m["id"] for m in out
    ] == []  # the HOLD text changed, so it no longer matches whole either


async def test_shadow_mode_reads_the_graph_in_the_background(monkeypatch, spans, visible):
    """Review: in shadow mode the turn waited (up to the cap) for a read it then discarded. The
    shadow read now runs after the turn has moved on; with the graph off nothing is scheduled."""
    from src.http_client import caller_organization_id

    _on(monkeypatch, mode="shadow")
    monkeypatch.setattr(cg, "_cognee_context", _Ctx(FACT_CONTEXT, delay_s=0.05))
    token = caller_organization_id.set(ORG_A)
    try:
        t0 = asyncio.get_running_loop().time()
        cg.shadow_soon(AHU_Q, [_mem(1, VENDOR)])
        assert asyncio.get_running_loop().time() - t0 < 0.01 and spans == []
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert spans and spans[0]["inputs"]["mode"] == "shadow"
        assert [x["id"] for x in spans[0]["output"]["linked"]] == ["m2"]
        monkeypatch.setattr(settings, "cognee_memory_companies", "")
        before = len(cg._pending)
        cg.shadow_soon(AHU_Q, [])
        assert len(cg._pending) == before
    finally:
        caller_organization_id.reset(token)


# --- local models (Bala runs this on Qwen locally, 20 Nov) -------------------------------------


def _local(monkeypatch, **over):
    monkeypatch.setattr(settings, "cognee_db_url", COG_URL)
    monkeypatch.setattr(settings, "openai_api_base", over.get("base", ""))
    monkeypatch.setattr(settings, "openai_model", over.get("model", "gpt-5.6-terra"))
    monkeypatch.setattr(settings, "openai_api_key", over.get("key", "sk-test"))
    for name in (
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
        "embedding_endpoint",
        "embedding_tokenizer",
        "llm_reasoning_effort",
    ):
        if name in over:
            monkeypatch.setattr(settings, f"cognee_{name}", over[name])


def test_cognee_follows_the_orchestrators_local_model_endpoint(monkeypatch):
    """OPENAI_API_BASE pointing at a local server (Ollama, llama.cpp) is where the orchestrator's
    model lives; cognee's graph building must use the same one, not api.openai.com."""
    _local(monkeypatch, base="http://localhost:11434/v1", model="qwen3:8b", key="")
    llm = cg._cognee_settings()["llm"]
    assert llm["llm_endpoint"] == "http://localhost:11434/v1"
    assert llm["llm_model"] == "openai/qwen3:8b"
    assert llm["llm_api_key"] == "local"  # local servers take any key; cognee wants one


def test_without_a_local_endpoint_the_llm_config_is_openais(monkeypatch):
    _local(monkeypatch)
    llm = cg._cognee_settings()["llm"]
    assert "llm_endpoint" not in llm and "llm_args" not in llm and llm["llm_api_key"] == "sk-test"


def test_reasoning_effort_reaches_the_local_model_when_set(monkeypatch):
    """Qwen3 on Ollama thinks by default (~10x the tokens per graph-building call); "none"
    stops it."""
    _local(monkeypatch, base="http://localhost:11434/v1", llm_reasoning_effort="none")
    assert cg._cognee_settings()["llm"]["llm_args"] == {"extra_body": {"reasoning_effort": "none"}}


def test_embeddings_can_use_a_local_model(monkeypatch):
    _local(
        monkeypatch,
        embedding_provider="ollama",
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimensions=1024,
        embedding_endpoint="http://localhost:11434/api/embed",
        embedding_tokenizer="Qwen/Qwen3-Embedding-0.6B",
    )
    assert cg._cognee_settings()["embedding"] == {
        "embedding_provider": "ollama",
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_dimensions": 1024,
        "embedding_api_key": "sk-test",
        "embedding_endpoint": "http://localhost:11434/api/embed",
        "huggingface_tokenizer": "Qwen/Qwen3-Embedding-0.6B",
    }


def test_embedding_defaults_are_unchanged(monkeypatch):
    _local(monkeypatch)
    assert cg._cognee_settings()["embedding"] == {
        "embedding_provider": "openai",
        "embedding_model": "openai/text-embedding-3-small",
        "embedding_dimensions": 1536,
        "embedding_api_key": "sk-test",
    }


def test_blank_cognee_model_settings_fall_back_to_defaults(monkeypatch):
    """A copied .env.example leaves these blank: the service must still start, on the defaults."""
    from src.config import Settings

    for name in ("COGNEE_EMBEDDING_PROVIDER", "COGNEE_EMBEDDING_MODEL",
                 "COGNEE_EMBEDDING_DIMENSIONS", "COGNEE_EMBEDDING_ENDPOINT",
                 "COGNEE_EMBEDDING_TOKENIZER", "COGNEE_LLM_REASONING_EFFORT", "COGNEE_MEMORY_MODE"):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert s.cognee_embedding_provider == "openai"
    assert s.cognee_embedding_model == "openai/text-embedding-3-small"
    assert s.cognee_embedding_dimensions == 1536
    assert s.cognee_memory_mode == "shadow"
