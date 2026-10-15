"""A certificate's building is decided once, at ingest, and never revisited.

If the answer was no — the building had not been created yet, the name did not match, the
document named no building — that certificate is invisible to every building-scoped view and
every coverage figure for good, while sitting in the register looking fine.
``backfill_site_links`` exists for the separate ``site_id`` link; this is the same contract
for ``building_id``.

These pin what the report must distinguish, because the four outcomes need four different
actions and collapsing them would make the report useless: a vendor accreditation that covers
a contractor is not a gap, and a certificate that names nothing cannot be placed by any rule
without inventing the answer.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.engines.compliance import building_links as BL


def test_a_contractor_certificate_is_not_a_missing_building():
    """ISO 9001 and public liability cover a company, not a building. Counting their null
    building_id as a gap would make the report cry wolf on most of its own rows."""
    for scope in ("Vendor", "vendor", "Contractor", "organisation"):
        assert scope.strip().lower() in BL.VENDOR_SCOPES


def test_fixtures_and_superseded_rows_are_left_out():
    assert BL._excluded({"a1_test_fixture": True})
    assert BL._excluded({"superseded_duplicate": True})
    assert BL._excluded({"archived": True})
    assert not BL._excluded({})
    assert not BL._excluded(None)


class _Cert:
    def __init__(self, **kw):
        self.id = kw.pop("id", uuid4())
        self.building_id = None
        self.certificate_type_code = kw.pop("code", "EICR")
        self.cert_type = None
        self.cert_scope = kw.pop("scope", "Building")
        self.building_name = kw.pop("building_name", None)
        self.building_reference = kw.pop("building_reference", None)
        self.site_id = kw.pop("site_id", None)
        self.site_ref = kw.pop("site_ref", None)
        self.document_id = kw.pop("document_id", None)
        self.source_document_id = None
        self.raw_metadata = kw.pop("raw_metadata", {})


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, certs):
        self.certs = certs
        self.committed = False

    async def execute(self, *a, **k):
        return _Scalars(self.certs)

    async def commit(self):
        self.committed = True


@pytest.fixture
def resolver(monkeypatch):
    """Stand in for the ingest-time resolver so these run without a database."""
    answers: dict[str, dict] = {}

    async def fake(session, *, building_name=None, building_reference=None,
                   site_name=None, site_id=None):
        key = building_name or building_reference or site_id
        return answers.get(key, {"building_id": None, "outcome": "unmatched",
                                 "reason": "no_match"})

    import src.engines.energy.graph_ingest as gi

    monkeypatch.setattr(gi, "resolve_building_for", fake)
    return answers


@pytest.mark.asyncio
async def test_a_certificate_that_names_its_building_is_linked(resolver, monkeypatch):
    building = uuid4()
    resolver["Riverside Campus"] = {"building_id": str(building), "outcome": "resolved",
                                    "reason": "exact_name", "building_label": "Riverside Campus"}
    cert = _Cert(building_name="Riverside Campus")
    session = _Session([cert])
    report = await BL.backfill_building_links(session, dry_run=True)
    assert report["counts"] == {"linked": 1, "ambiguous": 0, "no_evidence": 0,
                                "not_applicable": 0}
    assert report["linked"][0]["basis"] == "certificate"
    # A dry run writes nothing, which is the whole point of defaulting to one.
    assert cert.building_id is None and session.committed is False


@pytest.mark.asyncio
async def test_applying_writes_the_link_and_records_how_it_was_decided(resolver):
    building = uuid4()
    resolver["Riverside Campus"] = {"building_id": str(building), "outcome": "resolved",
                                    "reason": "exact_name", "building_label": "Riverside Campus"}
    cert = _Cert(building_name="Riverside Campus")
    session = _Session([cert])
    await BL.backfill_building_links(session, dry_run=False)
    assert str(cert.building_id) == str(building)
    assert cert.raw_metadata["building_link"]["via"] == "backfill"
    assert cert.raw_metadata["building_link"]["basis"] == "certificate"
    assert session.committed is True


@pytest.mark.asyncio
async def test_two_candidate_buildings_are_never_resolved_to_one_of_them(resolver):
    """A certificate filed against the wrong building understates one building's
    obligations and overstates another's."""
    resolver["The Old Mill"] = {"building_id": None, "outcome": "ambiguous",
                                "reason": "ambiguous_name"}
    session = _Session([_Cert(building_name="The Old Mill")])
    report = await BL.backfill_building_links(session, dry_run=True)
    assert report["counts"]["ambiguous"] == 1 and report["counts"]["linked"] == 0


@pytest.mark.asyncio
async def test_a_certificate_with_nothing_to_go_on_says_exactly_what_is_missing(resolver):
    """The useful half of the report: these cannot be placed by any rule, and the list of
    empty fields is what somebody who knows the answer needs to see."""
    session = _Session([_Cert(code="EICR")])
    report = await BL.backfill_building_links(session, dry_run=True)
    assert report["counts"]["no_evidence"] == 1
    assert set(report["no_evidence"][0]["missing"]) == {
        "building_name", "building_reference", "site", "document_on_a_building"}


@pytest.mark.asyncio
async def test_a_vendor_certificate_is_reported_as_not_applicable(resolver):
    session = _Session([_Cert(scope="Vendor", code="ISO_9001")])
    report = await BL.backfill_building_links(session, dry_run=True)
    assert report["counts"] == {"linked": 0, "ambiguous": 0, "no_evidence": 0,
                                "not_applicable": 1}
    assert "contractor" in report["not_applicable"][0]["reason"]


@pytest.mark.asyncio
async def test_the_route_is_a_dry_run_unless_asked_otherwise():
    import inspect

    from src.api.routes import compliance

    sig = inspect.signature(compliance.backfill_building_links)
    assert sig.parameters["dry_run"].default.default is True
