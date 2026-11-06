"""The record engine reads a lapsed certificate by its expiry date too (3 Nov 2026).

"Which buildings have lapsed certificates and overdue work orders?" asked in the Assets Operating
System went to answer_from_records, not the certificate tools fixed earlier the same day. Its
'lapsed' state was the stored value alone (status = 'Lapsed'), so LOLER-B-301-4471 (expired
18 Oct 2026) and FGAS-B-301-CH101 - both still stored as "valid" because the lifecycle scan had
not run - were left out again: 11 lapsed certificates listed, those two missing. The planner had
also written the step as "status is exactly 'Lapsed'", which pins the stored column.

One rule for every reader (agents/cert_expiry.py): a certificate is lapsed when its status says
so, or when it is stored as in date and its expiry date has passed. A superseded or renewed
certificate whose date has passed is history, not lapsed.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from src.agents import cert_expiry, planner
from src.agents import ontology_qa as oq

TODAY = date.today()
PAST, FUTURE = TODAY - timedelta(days=16), TODAY + timedelta(days=200)

CERT_CATALOG = {"compliance_certificates": {
    "columns": ["id", "certificate_number", "status", "expiry_date", "building_id"],
    "types": {"id": "uuid", "certificate_number": "character varying", "status": "character varying",
              "expiry_date": "date", "building_id": "uuid"},
    "pk": ["id"], "fks": []}}


class _Register:
    SAMPLES = {"id": ["c1", "c2"], "certificate_number": ["LOLER-B-301-4471", "FRA-B-301-2025"],
               "status": ["valid", "Lapsed", "Current", "Superseded"],
               "expiry_date": [PAST.isoformat(), FUTURE.isoformat()]}
    COUNTS = {"status": [("valid", 40), ("Lapsed", 11), ("Current", 30), ("Superseded", 4)]}

    def __init__(self, session, schema):
        pass

    async def row_counts(self):
        return {"compliance_certificates": 85}

    async def distinct_sample(self, t, c, limit=0):
        return list(self.SAMPLES.get(c, []))

    async def value_counts(self, t, c):
        return list(self.COUNTS.get(c, []))

    async def is_unique(self, t, c):
        return c in ("id", "certificate_number")

    async def fill_ratio(self, t, c):
        return 1.0

    async def containment(self, values, t, c):
        return 0.0


def _states(monkeypatch):
    async def catalog(session, schema):
        return {t: {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in m.items()}
                for t, m in CERT_CATALOG.items()}

    monkeypatch.setattr(oq, "load_catalog", catalog)
    monkeypatch.setattr(oq, "Profiler", _Register)
    onto, _ = asyncio.run(oq.build_ontology(None))
    (concept,) = [c for c in onto["concepts"].values() if c.get("table") == "compliance_certificates"]
    return concept["states"]


def _holds(cond, row):
    """The condition as Postgres would read it (compile_condition compares text lower-cased)."""
    if "all" in cond:
        return all(_holds(c, row) for c in cond["all"])
    if "any" in cond:
        return any(_holds(c, row) for c in cond["any"])
    v = row.get(cond["attribute"])
    if "in" in cond:
        return v is not None and str(v).lower() in [str(x).lower() for x in cond["in"]]
    if "lt" in cond:
        assert cond["lt"] == "today"
        return v is not None and v < TODAY
    raise AssertionError(cond)


ROWS = {
    "LOLER-B-301-4471": {"status": "valid", "expiry_date": PAST},       # stale: lapsed by its date
    "FGAS-B-301-CH101": {"status": "Current", "expiry_date": PAST},     # stale too
    "FRA-B-301-2025": {"status": "Lapsed", "expiry_date": PAST},        # stored lapsed
    "EICR-B-301": {"status": "valid", "expiry_date": FUTURE},           # in date
    "GAS-OLD": {"status": "Superseded", "expiry_date": PAST},           # history, not lapsed
}


def _lapsed(cond):
    return sorted(n for n, r in ROWS.items() if _holds(cond, r))


def test_lapsed_takes_in_a_certificate_stored_in_date_whose_date_has_passed(monkeypatch):
    states = _states(monkeypatch)
    assert _lapsed(states["lapsed"]) == ["FGAS-B-301-CH101", "FRA-B-301-2025", "LOLER-B-301-4471"]


def test_expired_is_the_same_rule(monkeypatch):
    states = _states(monkeypatch)
    assert _lapsed(states["expired"]) == _lapsed(states["lapsed"])


def test_the_in_date_statuses_are_the_shared_ones_not_the_sample(monkeypatch):
    """The ontology is cached on the schema, not the values: 'active' must count as in date
    even when the sample never saw it."""
    states = _states(monkeypatch)
    leaves = [l for l in oq_leaves(states["lapsed"]) if "in" in l and "valid" in [x.lower() for x in l["in"]]]
    assert leaves and set(x.lower() for x in leaves[0]["in"]) == set(cert_expiry.IN_DATE_STATUSES)


def test_the_state_says_to_use_it_for_lapsed_or_expired(monkeypatch):
    desc = _states(monkeypatch)["lapsed"]["description"].lower()
    assert "use this" in desc and "expiry" in desc


def test_the_lapsed_state_compiles_to_sql(monkeypatch):
    states = _states(monkeypatch)
    onto = oq.Ontology({"concepts": {}, "relations": {}, "col_types": {}} | _onto_dict(monkeypatch))
    (name,) = [n for n, c in onto.concepts.items() if c.table == "compliance_certificates"]
    p = oq.Params()
    sql = oq.compile_states(onto, onto.concepts[name], "c", ["lapsed"], p)
    assert " OR " in sql and " AND " in sql and "CURRENT_DATE" in sql


def test_a_cached_ontology_from_the_previous_builder_is_rebuilt(monkeypatch):
    now = oq.schema_fingerprint(CERT_CATALOG)
    monkeypatch.setattr(oq, "BUILDER_VERSION", "7-svc1")
    assert oq.schema_fingerprint(CERT_CATALOG) != now


def test_the_engine_is_told_to_use_the_state_not_a_status_filter():
    guide = oq.PLAN_GUIDE.lower()
    assert "lapsed" in guide and "never a filter on status" in guide


def test_the_planner_does_not_pin_lapsed_to_a_stored_status_value():
    prompt = planner.PLAN_PROMPT.lower()
    assert "lapsed" in prompt and "expiry date" in prompt
    assert "status is exactly" in prompt          # named as the thing never to write


def oq_leaves(cond):
    if "attribute" in cond:
        yield cond
    for sub in cond.get("all", []) + cond.get("any", []):
        yield from oq_leaves(sub)


def _onto_dict(monkeypatch):
    async def catalog(session, schema):
        return {t: {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in m.items()}
                for t, m in CERT_CATALOG.items()}

    monkeypatch.setattr(oq, "load_catalog", catalog)
    monkeypatch.setattr(oq, "Profiler", _Register)
    onto, _ = asyncio.run(oq.build_ontology(None))
    return onto


# Pre-push review (3 Nov 2026): the lapsed block was first inserted inside the `for c in dates`
# loop, swallowing the work-order "overdue" rule under `if <certificate table>` - work_orders lost
# its overdue state. The builder is bumped again so an ontology cached with that fault is rebuilt.

WO_CATALOG = {"work_orders": {
    "columns": ["id", "wo_number", "status", "due_date", "completed_at", "building_id"],
    "types": {"id": "uuid", "wo_number": "character varying", "status": "character varying",
              "due_date": "timestamp with time zone", "completed_at": "timestamp with time zone",
              "building_id": "uuid"},
    "pk": ["id"], "fks": []}}


class _WoRegister(_Register):
    SAMPLES = {"id": ["w1", "w2"], "wo_number": ["WO-B-301-4562", "WO-B-301-4568"],
               "status": ["Open", "Held", "Closed"]}
    COUNTS = {"status": [("Open", 9), ("Held", 2), ("Closed", 300)]}

    async def row_counts(self):
        return {"work_orders": 311}


def test_work_orders_keep_their_overdue_state(monkeypatch):
    async def catalog(session, schema):
        return {t: {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in m.items()}
                for t, m in WO_CATALOG.items()}

    monkeypatch.setattr(oq, "load_catalog", catalog)
    monkeypatch.setattr(oq, "Profiler", _WoRegister)
    onto, _ = asyncio.run(oq.build_ontology(None))
    (concept,) = [c for c in onto["concepts"].values() if c.get("table") == "work_orders"]
    overdue = concept["states"].get("overdue")
    assert overdue, "work orders lost their overdue state"
    assert {"attribute": "due_date", "lt": "today"} in overdue["all"]
    assert "lapsed" not in concept["states"] or "expiry" not in concept["states"]["lapsed"]["description"]


def test_the_builder_moved_past_the_faulty_build():
    assert oq.BUILDER_VERSION not in ("7-svc1", "8-svc1")
