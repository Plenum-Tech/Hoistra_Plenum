"""A deleted vendor is history, not a vendor anybody can be offered or counted.

Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row stays so past
work orders, invoices and certificates still read "Acme Ltd (deleted)", and vendors.status
becomes 'deleted'. The agent had been told the opposite — that status "reads 'active' on every
row" and should be ignored — so every vendor list, count and recommendation it wrote would
carry the deleted ones, and the record engine had no idea 'deleted' was a finished state.

These hold each place the agent reads vendors to the same rule: a deleted vendor is left out
unless the question is about deleted vendors or history.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from src.agents import ontology_qa as oq
from src.agents import udr_agent
from src.agents.contract_performance_single_door import vendor_note_for
from src.services import principal as P

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "src" / "agents" / "system_prompt.py").read_text(encoding="utf-8")

# ops-intelligence's live_vendor_sql, on the table's own name: query_table reads
# `FROM plenum_cafm.vendors` with no alias, and a table name is its own whole-row value.
LIVE_VENDORS = "COALESCE(lower(to_jsonb(vendors)->>'status'), 'active') <> 'deleted'"


# ── the system prompt ─────────────────────────────────────────────────────────────────────

def _vendor_columns_section() -> str:
    start = PROMPT.index("**A blocked vendor is `block_state`, never `status`.**")
    return PROMPT[start:PROMPT.index("**Schema-first rule:**", start)]


class TestThePromptSaysWhatStatusMeans:
    def test_the_false_claim_that_status_is_always_active_is_gone(self):
        """It was true until vendors could be deleted; now it tells the model to ignore the
        one column that says a vendor is gone."""
        assert "on every row" not in _vendor_columns_section()

    def test_it_names_the_deleted_status_and_the_name_mark(self):
        section = _vendor_columns_section()
        assert "`status = 'deleted'`" in section
        assert '"(deleted)"' in section

    def test_it_says_to_leave_deleted_vendors_out_of_lists_counts_and_recommendations(self):
        section = _vendor_columns_section().lower()
        for word in ("lists", "counts", "recommend"):
            assert word in section, word
        assert "unless" in section and "history" in section

    def test_blocking_is_still_block_state(self):
        """The rule the section was written for still stands: blocked is block_state."""
        assert "WHERE block_state = 'Blocked'" in _vendor_columns_section()


# ── the record engine ─────────────────────────────────────────────────────────────────────

class TestDeletedIsAFinishedState:
    @pytest.mark.parametrize("value", ["deleted", "Deleted", "DELETED"])
    def test_deleted_reads_as_closed(self, value):
        assert oq.re.search(oq.CLOSED_VALUES_RE, value, oq.re.I)

    @pytest.mark.parametrize("value", ["active", "Active", "pending"])
    def test_a_live_vendor_does_not(self, value):
        assert not oq.re.search(oq.CLOSED_VALUES_RE, value, oq.re.I)

    def test_a_vendors_table_holding_deleted_vendors_gets_open_and_closed_states(self, monkeypatch):
        """Built from a register where three of 2,072 vendors are deleted: 'open' must leave
        them out and 'closed' must be them — before, neither state existed for vendors."""
        onto = asyncio.run(_build_vendor_ontology(monkeypatch))
        states = onto["concepts"]["Vendor"]["states"]
        assert states["closed"] == {"description": "finished (status in deleted)",
                                    "attribute": "status", "in": ["deleted"]}
        assert states["open"]["attribute"] == "status"
        assert states["open"]["not_in"] == ["Canceled", "Cancelled", "Closed", "Complete",
                                            "Completed", "Done", "Resolved", "deleted"]

    def test_an_ontology_cached_by_the_previous_builder_is_rebuilt(self, monkeypatch):
        """Ontologies are cached on disk by fingerprint. One built before 'deleted' was a closed
        state would be served on and on unless the fingerprint moved."""
        now = oq.schema_fingerprint(VENDOR_CATALOG)
        monkeypatch.setattr(oq, "BUILDER_VERSION", "5-svc1")
        assert oq.schema_fingerprint(VENDOR_CATALOG) != now


VENDOR_CATALOG = {"vendors": {
    "columns": ["id", "organization_id", "vendor_name", "vendor_code", "status", "block_state"],
    "types": {"id": "character varying", "organization_id": "uuid",
              "vendor_name": "character varying", "vendor_code": "character varying",
              "status": "character varying",
              "block_state": "character varying"},
    "pk": ["id"], "fks": []}}


class _Register:
    """What the profiler would read off a vendors table where three vendors were deleted."""

    SAMPLES = {"id": ["V-01", "V-02", "V-03"],
               "vendor_name": ["Apex Mechanical Ltd", "Brook Lifts (deleted)"],
               "vendor_code": ["APX-01", "CLE-02"], "status": ["active", "deleted"],
               "block_state": ["Clear", "Blocked"]}
    COUNTS = {"status": [("active", 2069), ("deleted", 3)],
              "block_state": [("Clear", 2066), ("Blocked", 6)]}

    def __init__(self, session, schema):
        pass

    async def row_counts(self):
        return {"vendors": 2072}

    async def distinct_sample(self, t, c, limit=0):
        return list(self.SAMPLES.get(c, []))

    async def value_counts(self, t, c):
        return list(self.COUNTS.get(c, []))

    async def is_unique(self, t, c):
        return c in ("id", "vendor_code")

    async def fill_ratio(self, t, c):
        return 1.0

    async def containment(self, values, t, c):
        return 0.0


async def _build_vendor_ontology(monkeypatch):
    async def catalog(session, schema):
        return {t: {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in m.items()}
                for t, m in VENDOR_CATALOG.items()}

    monkeypatch.setattr(oq, "load_catalog", catalog)
    monkeypatch.setattr(oq, "Profiler", _Register)
    onto, _lines = await oq.build_ontology(None)
    return onto


# ── query_table ───────────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _VendorsSession:
    """plenum_cafm.vendors as Postgres would answer query_table: the status filter applies
    only when the SQL carries it, and each bound equality filter narrows the rows."""

    ROWS = [{"id": "V-01", "vendor_name": "Apex Mechanical Ltd", "status": "active"},
            {"id": "V-02", "vendor_name": "Brook Lifts (deleted)", "status": "deleted"},
            {"id": "V-03", "vendor_name": "Cole Electrical", "status": None}]

    def __init__(self, sink: list):
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sink.append(sql)
        rows = list(self.ROWS)
        if LIVE_VENDORS in sql:
            rows = [r for r in rows if (r["status"] or "active").lower() != "deleted"]
        for k, v in (params or {}).items():
            if k.startswith("p_"):
                rows = [r for r in rows if r.get(k[2:]) == v]
        return _Result(rows)


@pytest.fixture
def vendors_table(monkeypatch):
    sink: list[str] = []
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: _VendorsSession(sink))
    token = P.caller_principal.set(None)          # an admin: no building clause
    yield sink
    P.caller_principal.reset(token)


def _query(table, filters=None):
    return asyncio.run(udr_agent.query_table.ainvoke({"table_name": table, "filters": filters}))


class TestQueryTableLeavesDeletedVendorsOut:
    def test_a_plain_read_of_vendors_leaves_the_deleted_one_out(self, vendors_table):
        assert [r["id"] for r in _query("vendors")] == ["V-01", "V-03"]
        assert vendors_table == [
            f"SELECT * FROM plenum_cafm.vendors WHERE {LIVE_VENDORS} LIMIT 100"]

    def test_a_filter_on_another_column_still_leaves_it_out(self, vendors_table):
        # A name filter goes through the resolution ladder (29 Oct 2026): several statements, one per
        # rung tried. Every one of them keeps the live-vendors clause, and the deleted one never returns.
        rows = _query("vendors", {"vendor_name": "Brook Lifts (deleted)"})
        assert "V-02" not in [r["id"] for r in rows]
        assert vendors_table and all(LIVE_VENDORS in sql for sql in vendors_table), vendors_table

    def test_asking_for_deleted_vendors_by_status_finds_them(self, vendors_table):
        rows = _query("vendors", {"status": "deleted"})
        assert [r["id"] for r in rows] == ["V-02"]
        assert LIVE_VENDORS not in vendors_table[0]

    def test_another_table_is_read_as_it_always_was(self, vendors_table):
        _query("work_orders")
        assert vendors_table == ["SELECT * FROM plenum_cafm.work_orders  LIMIT 100"]


# ── the contract upload reply ─────────────────────────────────────────────────────────────

class TestTheContractReplySaysTheVendorWasDeleted:
    SENTENCE = " The vendor on this contract was deleted, so the contract was not linked to it."

    def test_a_contract_naming_a_deleted_vendor_says_so_plainly(self):
        vendor = {"status": "vendor_deleted", "vendor_name": "Apex Mechanical Ltd (deleted)"}
        assert vendor_note_for(vendor) == self.SENTENCE

    def test_it_says_so_even_when_no_name_came_back(self):
        """Without this the reply would claim the document named no provider at all."""
        assert vendor_note_for({"status": "vendor_deleted", "vendor_name": None}) == self.SENTENCE


# ── the skill docs ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", ["skills/udr/SKILL.md", "skills/compliance/tables.md",
                                 "skills/compliance/SKILL.md", "skills/compliance/core.md"])
def test_every_skill_doc_describing_vendors_says_what_deleted_means(doc):
    """A sub-agent writes its own SQL from these. Each one that lists the vendors columns
    must say a deleted vendor is left out, or its blocked-vendor counts carry them."""
    text = (ROOT / doc).read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "`vendors`" in ln and "block_state" in ln)
    assert "`status`" in line and "'deleted'" in line and "(deleted)" in line, line
    assert "history" in line, line


# ── review fixes, 12 Oct 2026 ─────────────────────────────────────────────────────────────

class TestAnExactLookupIsAHistoryRead:
    """'Who did WO-1182 last March?' reads the closed work order, then the vendor it names by
    id. The model cannot know in advance that the vendor is retired, so an exact lookup keeps
    retired rows; 'Acme Lifts (deleted)' is the right answer, not 'unknown vendor'."""

    @pytest.mark.parametrize("filters", [{"id": "V-02"}, {"vendor_code": "V-02"}])
    def test_a_lookup_by_id_or_code_finds_a_deleted_vendor(self, vendors_table, filters):
        _VendorsSession.ROWS[1]["vendor_code"] = "V-02"
        try:
            rows = _query("vendors", filters)
        finally:
            _VendorsSession.ROWS[1].pop("vendor_code", None)
        assert [r["id"] for r in rows] == ["V-02"]
        assert LIVE_VENDORS not in vendors_table[0]


class TestTheRecordEngineLeavesDeletedVendorsOut:
    def _onto(self, monkeypatch, counts=None):
        if counts is not None:
            monkeypatch.setattr(_Register, "COUNTS", {**_Register.COUNTS, "status": counts})
            monkeypatch.setattr(_Register, "SAMPLES",
                                {**_Register.SAMPLES, "status": [v for v, _ in counts]})
        d = asyncio.run(_build_vendor_ontology(monkeypatch))
        return d, oq.Ontology(d)

    def test_deleted_is_a_closed_state_even_before_any_vendor_is_deleted(self, monkeypatch):
        """The ontology is cached on the schema fingerprint, not the values: one built the day
        before the first delete would otherwise never learn that 'deleted' is finished."""
        d, _ = self._onto(monkeypatch, counts=[("active", 2072)])
        states = d["concepts"]["Vendor"]["states"]
        assert states["closed"]["in"] == ["deleted"]
        assert "deleted" in states["open"]["not_in"]

    @pytest.mark.parametrize("question", ["how many vendors do we have?", "list our vendors"])
    def test_a_vendor_question_is_pinned_to_live_vendors(self, monkeypatch, question):
        _, onto = self._onto(monkeypatch)
        plan = {"focus": {"concept": "Vendor", "filters": [], "states": []}}
        notes = oq.pin_live_vendors(onto, plan, question)
        assert {"attribute": "status", "op": "not_in", "value": ["deleted"], "pinned": True} \
            in plan["focus"]["filters"]
        assert notes

    @pytest.mark.parametrize("question", ["which vendors did we delete?",
                                          "show deleted vendors", "vendor history for Brook Lifts"])
    def test_asking_about_deleted_vendors_or_history_is_not_pinned(self, monkeypatch, question):
        _, onto = self._onto(monkeypatch)
        plan = {"focus": {"concept": "Vendor", "filters": [], "states": []}}
        assert oq.pin_live_vendors(onto, plan, question) == []
        assert plan["focus"]["filters"] == []

    def test_a_plan_that_asks_for_closed_vendors_is_not_pinned(self, monkeypatch):
        _, onto = self._onto(monkeypatch)
        plan = {"focus": {"concept": "Vendor", "filters": [], "states": ["closed"]}}
        assert oq.pin_live_vendors(onto, plan, "list vendors") == []

    def test_other_concepts_are_not_pinned(self, monkeypatch):
        _, onto = self._onto(monkeypatch)
        plan = {"focus": {"concept": "WorkOrder", "filters": [], "states": []}}
        assert oq.pin_live_vendors(onto, plan, "list work orders") == []

    def test_the_answer_pipeline_applies_the_pin(self):
        src = (ROOT / "src" / "agents" / "ontology_qa.py").read_text(encoding="utf-8")
        body = src[src.index("async def answer_question"):]
        assert "pin_live_vendors(onto, plan, question)" in body


def test_the_direct_compliance_read_does_not_report_a_retired_vendor_as_blocked():
    """block_state stays 'Blocked' when a blocked vendor is retired; the certificate read must
    not keep flagging its building certificates 'blocked vendor', nor list it as blocked."""
    from src.agents.orchestrator import DeepAgentOrchestrator

    case = ("CASE WHEN lower(to_jsonb(v)->>'status') = 'deleted' THEN NULL "
            "ELSE v.block_state END")
    src = (ROOT / "src" / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    start = src.index("async def _fetch_compliance_table_direct")
    template = " ".join(src[start:src.index("ORDER BY {sort}", start)].split())
    assert case + " AS vendor_block_state" in template, template
    assert "v.block_state AS vendor_block_state" not in template
    assert DeepAgentOrchestrator._SPEC_JOIN_FIELDS["vendor_block_state"] == case
