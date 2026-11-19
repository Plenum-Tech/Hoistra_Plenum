"""A faithful re-run of a records query (18 Nov 2026, Hussain and Bala).

Trace Lab reads the values a question's records query would give by running the SAME steps Hoistra's records tool runs
(svc-deepagents ontology_qa.answer_question), in the same order, on the same engine, company-scoped and read-only, every
query kept as a receipt. These tests use fakes for the database and for Hoistra's step functions (the style of
svc-deepagents' test_a_new_building_is_answered_from_its_record.py): no database is needed. One test reads Hoistra's real
source, so a change to the steps of its records tool fails here and says what to update."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from src.agents import ontology_qa as oq
from trace_lab import answer_key as ak
from trace_lab import records_run as rr

ORG = "company-1"
STEPS_AGG = tuple(s for s in rr.STEPS if s not in ("ensure_status_includes", "ensure_compliance_includes"))


def _node(concept, match=None, **kw):
    return dict({"concept": concept, "match": match, "states": [], "filters": [], "via": []}, **kw)


PLAN_COUNT_FLOORS = {"question_type": "count", "focus": _node("Floor"), "include": [], "aggregate": None,
                     "constraints": [_node("Building", "ABC")]}
PLAN_COUNT_BY_BUILDING = {"question_type": "count", "focus": _node("WorkOrder", states=["open"]), "include": [],
                          "aggregate": {"count_by": [{"concept": "Building", "attribute": "name"}]},
                          "constraints": [_node("Building")]}


# ── the steps are Hoistra's ────────────────────────────────────────────────────────────────────────────────────────

def test_steps_match_hoistras_records_tool():
    names = {n for n, v in vars(oq).items() if inspect.isfunction(v)}
    assert rr.hoistra_steps(inspect.getsource(oq.answer_question), names) == list(rr.STEPS), \
        "Hoistra's records tool changed its steps: update records_run.STEPS and run_query to match"


def test_hoistra_reads_what_is_still_pending_after_the_values():
    src = inspect.getsource(oq.answer_question)
    assert src.index("await execute_plan(") < src.index("await pending_context(")


def test_the_steps_are_read_between_planning_and_the_evidence():
    src = "x = await plan_question(a)\n y = one(1)\n await two(y)\n await two(2)\n out = evidence(z)\n later(3)"
    assert rr.hoistra_steps(src, {"one", "two", "later", "plan_question", "evidence"}) == ["one", "two", "two"]


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────────────────────────

class FakeReader:
    """Stands in for ReceiptReader: no connection, receipts are added by the fake steps."""
    made: list = []

    def __init__(self, principal):
        self.principal, self.receipts = principal, []
        FakeReader.made.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_pipeline(monkeypatch, *, unresolved=None, errors=None):
    """Replace the map loader, the reader and every step of Hoistra's records tool with fakes that record their name."""
    calls: list[str] = []
    asked: list = []                    # (step, question) for the steps that take the question
    seen: dict = {}
    FakeReader.made = []
    onto = SimpleNamespace(schema="plenum_scoped", concepts={})

    async def load_map(*a):
        calls.append("load_map")
        return onto

    def sync(name, notes=(), takes_question=False):
        def f(o, plan, *rest):
            assert o is onto
            calls.append(name)
            if takes_question:
                asked.append((name, rest[0]))
            return list(notes)
        return f

    def synced(name, ret):
        async def f(r, o, plan, *rest, **kw):
            assert o is onto
            calls.append(name)
            seen[name] = {"rest": rest, "kw": kw, "plan": plan}
            if "question" in kw or rest:
                asked.append((name, kw.get("question", rest[0] if rest else None)))
            return ret(r, plan)
        return f

    async def resolve_plan(r, o, plan, question=None):
        calls.append("resolve_plan")
        asked.append(("resolve_plan", question))
        r.receipts.append({"id": "q1", "label": "resolve Building (exact)", "sql": "SELECT 1", "params": {},
                           "row_count": 1, "rows": []})
        plan["resolved"] = True
        return plan, ["resolved ABC"], list(unresolved or [])

    async def add_missing_codes(r, o, plan, question):
        calls.append("add_missing_codes")
        asked.append(("add_missing_codes", question))
        return plan

    async def execute_plan(r, o, plan):
        calls.append("execute_plan")
        seen["executed"] = plan
        return {"total": 7, "rows": [{"n": 7}]}

    async def pending_context(r, o, plan):
        calls.append("pending_context")
        r.receipts.append({"id": "q2", "label": "open items", "sql": "SELECT 2", "params": {}, "row_count": 0, "rows": []})
        return {"open_total": 3}

    monkeypatch.setattr(rr, "load_map", load_map)
    monkeypatch.setattr(rr, "ReceiptReader", FakeReader)
    monkeypatch.setattr(oq, "ensure_grouping", sync("ensure_grouping", ["grouped"], True))
    monkeypatch.setattr(oq, "swap_empty_links", sync("swap_empty_links"))
    monkeypatch.setattr(oq, "apply_pinned", lambda o, plan, pinned: calls.append("apply_pinned") or seen.setdefault("pinned", []).append(pinned) or [])
    monkeypatch.setattr(oq, "pin_live_vendors", sync("pin_live_vendors", [], True))
    monkeypatch.setattr(oq, "names_from_filters", sync("names_from_filters"))
    monkeypatch.setattr(oq, "add_missing_codes", add_missing_codes)
    monkeypatch.setattr(oq, "resolve_plan", resolve_plan)
    monkeypatch.setattr(oq, "stated_count_notes", synced("stated_count_notes", lambda r, p: ["stated"]))
    monkeypatch.setattr(oq, "fill_date_filters", synced("fill_date_filters", lambda r, p: ["dates"]))
    monkeypatch.setattr(oq, "finished_states_note", sync("finished_states_note", ["finished"]))
    monkeypatch.setattr(oq, "ensure_status_includes", sync("ensure_status_includes", ["status"]))
    monkeypatch.setattr(oq, "ensure_compliance_includes", sync("ensure_compliance_includes", ["compliance"]))
    monkeypatch.setattr(oq, "validate_plan", lambda o, plan: calls.append("validate_plan") or list(errors or []))
    monkeypatch.setattr(oq, "execute_plan", execute_plan)
    monkeypatch.setattr(oq, "pending_context", pending_context)
    return SimpleNamespace(calls=calls, asked=asked, seen=seen, onto=onto)


def _steps(calls):
    return [c for c in calls if c not in ("load_map", "pending_context")]


# ── the whole path, in Hoistra's order ─────────────────────────────────────────────────────────────────────────────

async def test_run_query_calls_every_step_in_hoistras_order(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="how many floors in ABC", org=ORG)
    assert _steps(fake.calls) == list(rr.STEPS)
    assert out["found"] is True and out["unresolved"] == []
    assert out["receipts"][0]["label"] == "resolve Building (exact)"
    assert out["result"] == {"total": 7, "rows": [{"n": 7}]}
    assert out["read_at"].endswith("+00:00")
    # ensure_grouping, pin_live_vendors, add_missing_codes and resolve_plan are all given the question as asked
    assert fake.asked and all(q == "how many floors in ABC" for _step, q in fake.asked)
    assert {s for s, _q in fake.asked} == {"ensure_grouping", "pin_live_vendors", "add_missing_codes", "resolve_plan"}


async def test_the_page_filters_are_not_recorded_so_none_are_applied(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    assert fake.seen["pinned"] == [None, None]


async def test_an_aggregate_skips_the_status_includes_like_hoistra(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_BY_BUILDING, question="q", org=ORG)
    assert _steps(fake.calls) == list(STEPS_AGG)


async def test_a_name_not_on_record_stops_before_reading(monkeypatch):
    fake = _fake_pipeline(monkeypatch, unresolved=["ABC"])
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    assert out["found"] is False and out["unresolved"] == ["ABC"]
    assert "execute_plan" not in fake.calls and "pending_context" not in fake.calls
    assert out["result"] is None and out["pending"] is None
    assert out["notes"] == ["resolved ABC"]            # Hoistra hands back what resolving said, nothing else
    assert out["receipts"][0]["id"] == "q1" and out["plan"]["focus"]["concept"] == "Floor"


async def test_the_notes_are_hoistras_in_hoistras_order(monkeypatch):
    _fake_pipeline(monkeypatch)
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    assert out["notes"] == ["resolved ABC", "grouped", "stated", "dates", "finished",
                            "Also read, as always for this kind of record: status, compliance"]


async def test_an_aggregate_has_no_also_read_note(monkeypatch):
    _fake_pipeline(monkeypatch)
    out = await rr.run_query(PLAN_COUNT_BY_BUILDING, question="q", org=ORG)
    assert not any(n.startswith("Also read") for n in out["notes"])


async def test_the_result_plan_is_the_plan_as_run_and_the_caller_keeps_theirs(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    given = {"question_type": "count", "focus": _node("Floor"), "constraints": [_node("Building", "ABC")], "include": []}
    out = await rr.run_query(given, question="q", org=ORG)
    assert out["plan"]["resolved"] is True and fake.seen["executed"]["resolved"] is True
    assert "resolved" not in given and given["constraints"][0]["match"] == "ABC"


async def test_hoistra_mode_reads_what_is_still_pending_after_the_values(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    assert fake.calls.index("pending_context") == fake.calls.index("execute_plan") + 1
    assert out["pending"] == {"open_total": 3}
    assert [r["id"] for r in out["receipts"]] == ["q1", "q2"]       # its queries are receipts too


# ── as written ─────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_as_written_runs_only_the_resolve_and_read_steps(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    out = await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG, mode="as_written")
    assert rr.AS_WRITTEN == ("resolve_plan", "stated_count_notes", "fill_date_filters", "validate_plan", "execute_plan")
    assert _steps(fake.calls) == list(rr.AS_WRITTEN)
    assert out["found"] is True and out["pending"] is None
    assert out["notes"] == ["resolved ABC", "stated", "dates"]


async def test_as_written_resolves_the_plan_without_the_question(monkeypatch):
    fake = _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_FLOORS, question="how many floors in ABC", org=ORG, mode="as_written")
    assert fake.asked == [("resolve_plan", None)]


async def test_a_mode_it_does_not_know_is_refused(monkeypatch):
    _fake_pipeline(monkeypatch)
    with pytest.raises(ValueError):
        await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG, mode="guess")


# ── the plan has to fit ────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["hoistra", "as_written"])
async def test_a_plan_that_does_not_fit_the_database_is_a_value_error(monkeypatch, mode):
    fake = _fake_pipeline(monkeypatch, errors=["Floor has no state 'open'", "bad filter"])
    with pytest.raises(ValueError) as err:
        await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG, mode=mode)
    assert str(err.value) == "the plan does not fit this database: Floor has no state 'open'; bad filter"
    assert "execute_plan" not in fake.calls


# ── whose records ──────────────────────────────────────────────────────────────────────────────────────────────────

async def test_the_read_is_scoped_to_the_company_and_the_whole_company_by_default(monkeypatch):
    _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG)
    p = FakeReader.made[0].principal
    assert (p.role, p.organization_id, p.building_ids, p.email) == ("admin", ORG, None, None)


async def test_a_scope_narrows_the_read_to_a_users_buildings(monkeypatch):
    _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG, scope={"building_ids": ["b1", "b2"], "email": "fm@x.test"})
    p = FakeReader.made[0].principal
    assert (p.role, p.organization_id, p.building_ids, p.email) == ("admin", ORG, ["b1", "b2"], "fm@x.test")


async def test_a_scope_with_no_email_still_reads_the_whole_company(monkeypatch):
    _fake_pipeline(monkeypatch)
    await rr.run_query(PLAN_COUNT_FLOORS, question="q", org=ORG, scope={"building_ids": None, "email": None})
    assert FakeReader.made[0].principal.building_ids is None


# ── the receipts ───────────────────────────────────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, rows, fail_on=None):
        self.rows, self.fail_on, self.ran = rows, fail_on, []

    async def execute(self, stmt, params=None):
        self.ran.append((str(stmt), params))
        if self.fail_on and self.fail_on in str(stmt):
            raise RuntimeError("boom\nsecond line")
        return _Result(self.rows)

    def begin_nested(self):
        return _Nested()


def _reader(rows, **kw):
    r = rr.ReceiptReader(SimpleNamespace(role="admin", organization_id=ORG, building_ids=None))
    r.s = _Session(rows, **kw)
    return r


async def test_every_query_is_a_receipt_scoped_to_the_company():
    from datetime import date
    from decimal import Decimal

    r = _reader([{"n": i, "d": date(2026, 11, 1), "x": Decimal("1.5")} for i in range(60)])
    assert isinstance(r, oq.ScopedReader)
    assert ("app.udr_org", ORG) in r.settings and ("app.udr_unrestricted", "0") in r.settings

    p = oq.Params()
    sql = "SELECT n FROM floors WHERE name = " + p.add("ABC")
    rows = await r.fetch(sql, p, "floors at ABC")
    assert len(rows) == 60                                   # the reader still hands the engine every row
    got = await r.try_fetch("SELECT 2", None, "second")      # try_fetch is a receipt too
    assert len(got) == 60

    assert [x["id"] for x in r.receipts] == ["q1", "q2"]
    first = r.receipts[0]
    assert (first["label"], first["sql"], first["params"], first["row_count"]) == ("floors at ABC", sql, {"p0": "ABC"}, 60)
    assert len(first["rows"]) == ak.ROWS_KEPT == 50
    assert first["rows"][0] == {"n": 0, "d": "2026-11-01", "x": 1.5}          # JSON-safe
    assert "BEGIN READ ONLY;" in first["psql"] and f"'{ORG}'" in first["psql"] and "'ABC'" in first["psql"]
    assert r.receipts[1]["label"] == "second" and r.receipts[1]["params"] == {}


async def test_an_unscoped_receipt_says_the_whole_company():
    r = _reader([{"n": 1}])
    await r.fetch("SELECT 1", {}, "x")
    assert "SELECT set_config('app.udr_buildings', '', false);" in r.receipts[0]["psql"]


async def test_a_scoped_receipts_psql_reads_the_same_buildings_the_receipt_did():
    r = rr.ReceiptReader(SimpleNamespace(role="admin", organization_id=ORG, building_ids=["b1", "b'2"], email="fm@x.test"))
    r.s = _Session([{"n": 1}])
    await r.fetch("SELECT 1", {}, "x")
    assert dict(r.settings)["app.udr_buildings"] == "b1,b'2"                 # what the read itself ran with
    psql = r.receipts[0]["psql"]
    assert "SELECT set_config('app.udr_buildings', 'b1,b''2', false);" in psql
    assert "SELECT set_config('app.udr_buildings', '', false);" not in psql
    assert f"SELECT set_config('app.udr_org', '{ORG}', false);" in psql


def test_psql_text_names_the_buildings_only_when_it_is_given_them():
    receipt = {"sql": "SELECT 1", "params": {}}
    assert "set_config('app.udr_buildings', '', false)" in ak.psql_text(receipt, org=ORG)
    assert "set_config('app.udr_buildings', 'b1,b2', false)" in ak.psql_text(receipt, org=ORG, buildings="b1,b2")


async def test_a_query_with_no_label_is_called_query():
    r = _reader([])
    await r.fetch("SELECT 1", {}, "")
    assert r.receipts[0]["label"] == "query" and r.receipts[0]["row_count"] == 0


async def test_a_failed_lookup_adds_no_receipt_and_the_read_goes_on():
    r = _reader([{"n": 1}], fail_on="FAILS")
    assert await r.try_fetch("SELECT FAILS", None, "bad") == []
    assert r.receipts == []
    assert len(await r.try_fetch("SELECT 1", None, "good")) == 1 and r.receipts[0]["id"] == "q1"


# ── the map ────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_no_read_only_connection_means_not_ready(monkeypatch):
    from trace_lab import db

    monkeypatch.setattr(db, "_main", None)
    with pytest.raises(rr.NotReady):
        await rr.load_map()


class _Factory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(begin=lambda: _Nested(), execute=_noop)

    async def __aexit__(self, *exc):
        return False


async def _noop(*a, **kw):
    return None


def _fake_map(monkeypatch, *, built: bool):
    from trace_lab import db

    monkeypatch.setattr(db, "_main", object())

    async def load_catalog(s, schema):
        return {"schema": schema}

    async def get_ontology():
        return "the-map"

    monkeypatch.setattr(oq, "_session_factory", lambda: _Factory())
    monkeypatch.setattr(oq, "load_catalog", load_catalog)
    monkeypatch.setattr(oq, "schema_fingerprint", lambda cat: "fp1")
    monkeypatch.setattr(oq, "_ONTOLOGY", {"fp1": "the-map"} if built else {})
    monkeypatch.setattr(oq, "_cache_path", lambda fp: "/nonexistent/cafm_ontology_" + fp + ".json")
    monkeypatch.setattr(oq, "get_ontology", get_ontology)


async def test_a_map_that_was_never_built_is_not_ready_and_is_never_built_here(monkeypatch):
    _fake_map(monkeypatch, built=False)
    with pytest.raises(rr.NotReady):
        await rr.load_map()


async def test_a_built_map_is_loaded(monkeypatch):
    _fake_map(monkeypatch, built=True)
    assert await rr.load_map() == "the-map"


# ── what answer_key still offers ───────────────────────────────────────────────────────────────────────────────────

def test_answer_key_keeps_the_names_it_always_had():
    assert ak._ontology is rr.load_map
    assert ak.NotReady is rr.NotReady


async def test_the_answer_keys_own_run_still_reads_through_the_moved_reader(monkeypatch):
    """_run_plan keeps its behaviour (Task 5 reroutes it); only the reader it builds is the moved one."""
    from trace_lab import db

    fake = _fake_pipeline(monkeypatch)
    monkeypatch.setattr(db, "_main", object())

    async def ontology(_oq):
        return fake.onto

    async def resolve(r, o, plan, question=None):
        r.receipts.append({"id": "q1", "label": "x", "sql": "SELECT 1", "params": {}, "row_count": 0, "rows": []})
        return plan, [], []

    monkeypatch.setattr(ak, "_ontology", ontology)
    monkeypatch.setattr(oq, "resolve_plan", resolve)
    monkeypatch.setattr(oq, "validate_plan", lambda o, plan: [])
    out = await ak._run_plan(PLAN_COUNT_FLOORS, ORG, lambda onto, plan, result, receipts: [{"total": result["total"]}])
    assert out["found"] is True and out["facts"] == [{"total": 7}] and out["receipts"][0]["id"] == "q1"
    assert FakeReader.made[0].principal.organization_id == ORG and FakeReader.made[0].principal.building_ids is None
