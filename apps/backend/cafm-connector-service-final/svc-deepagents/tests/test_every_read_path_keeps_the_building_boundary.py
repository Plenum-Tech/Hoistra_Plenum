"""Every module that reads building-keyed data narrows it to the caller.

services/principal.py proves the boundary WORKS -- another instance's rows are dropped, a caller
allocated to nothing gets " AND FALSE" rather than a clause matching everything. What that does
not prove is that each read reaches for it, and a boundary one query skips is not a boundary.

So this walks the source instead of trusting a reading of it. Seven modules in this service name
plenum_cafm or plenum_scoped, and each one narrows by one of three means:

  building_clause / restrict_rows   the SQL carries the caller's buildings
  principal.allows_building(...)    the row or the request is checked against them
  authorization=...                 the caller's own token is forwarded to the service that
                                    owns the data, which narrows it there

A module that reads building-keyed tables and does none of those is the failure this guards. It
is a structural test, not a semantic one: it cannot tell a correct check from a careless one, but
it can tell a check from none, and "none" is how a cross-instance row reaches a screen.
"""
from __future__ import annotations

import os
import re

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")

#: Reading any of these means reading somebody's records.
BUILDING_KEYED = re.compile(
    r"\bFROM\s+plenum_(?:cafm|scoped)\.(buildings|compliance_certificates|work_orders|invoices"
    r"|documents|document_chunks|ingestion_documents|assets|energy_meters|contract_sla_parameters)\b",
    re.I)

#: Any of these narrows the read to the caller.
NARROWS = (
    "building_clause", "restrict_rows", "restrict_records", "table_building_clause",
    "allows_building", "authorization=", "organization_id = CAST",
)

#: Its own definition: the helpers live here, so it cannot be asked to call them.
EXEMPT = {os.path.join("services", "principal.py")}


def _read(rel: str) -> str:
    with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
        return fh.read()


def _modules() -> list:
    """The relative paths of modules that read building-keyed tables. Paths only: a body as a
    test id prints the whole file."""
    out = []
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, name), SRC)
            if rel in EXEMPT:
                continue
            if BUILDING_KEYED.search(_read(rel)):
                out.append(rel)
    return sorted(out)


def test_at_least_one_module_is_actually_scanned():
    """A regex that matches nothing would make every test below pass for the wrong reason."""
    mods = _modules()
    assert len(mods) >= 3, "found %d modules reading building-keyed tables" % len(mods)


@pytest.mark.parametrize("rel", _modules())
def test_a_module_that_reads_records_narrows_them_to_the_caller(rel):
    body = _read(rel)
    assert any(n in body for n in NARROWS), (
        "%s reads building-keyed tables and never narrows them: no building_clause, no "
        "allows_building, no forwarded authorization. A read that skips the boundary is how a "
        "row from another instance reaches a screen." % rel)


def test_the_binding_service_is_the_one_that_trusts_its_callers():
    """building_binding writes a document to a building and checks nothing itself.

    Every caller does check -- workflow.py and ingestion_cases.py both call allows_building
    before binding -- so it is correct today and fragile tomorrow: a new caller that forgets
    inherits no protection. Named here so the next person reads it before adding one.
    """
    with open(os.path.join(SRC, "services", "building_binding.py"), encoding="utf-8") as fh:
        body = fh.read()
    if "allows_building" in body:
        return                              # it grew its own check; nothing to warn about
    callers = []
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                text = fh.read()
            if "building_binding" in text and "services/building_binding" not in name:
                callers.append((os.path.relpath(os.path.join(root, name), SRC), text))
    binding_callers = [(rel, t) for rel, t in callers
                       if "bind_documents_to_building" in t or "register_uploads" in t]
    for rel, text in binding_callers:
        assert "allows_building" in text, (
            "%s binds documents to a building and never checks the caller is allowed it; "
            "building_binding does not check either, so nothing does" % rel)


# -- the binding service now checks for itself ---------------------------------------------

class TestBindingRefusesABuildingTheCallerIsNotAllowed:
    """Found by the audit above: building_binding read building-keyed tables and narrowed
    nothing. Every caller checked, so it was correct and fragile -- a caller added later
    inherits no protection, and this function files somebody's document against a building."""

    def _principal(self, buildings):
        import uuid as _uuid
        from src.services.principal import Principal
        return Principal(user_id=_uuid.uuid4(), email="t@example.test",
                         organization_id=_uuid.uuid4(), role="user", can_ingest=True,
                         building_ids=None if buildings is None else tuple(buildings))

    @pytest.mark.asyncio
    async def test_a_building_the_caller_is_not_allocated_is_refused(self):
        import uuid as _uuid
        from src.services import building_binding
        from src.services.principal import caller_principal
        mine, theirs = _uuid.uuid4(), _uuid.uuid4()
        token = caller_principal.set(self._principal([mine]))
        try:
            with pytest.raises(PermissionError) as exc:
                await building_binding.bind_documents_to_building(str(theirs), ["d1"])
            assert str(theirs) in str(exc.value)
        finally:
            caller_principal.reset(token)

    @pytest.mark.asyncio
    async def test_nothing_is_refused_when_there_is_no_caller(self):
        """A background job has no principal, and must not be broken by this."""
        from src.services import building_binding
        # No documents, so it returns before any database work -- the refusal would come first.
        out = await building_binding.bind_documents_to_building("any", [])
        assert out == {"documents": 0, "certificates": 0, "created": 0}
