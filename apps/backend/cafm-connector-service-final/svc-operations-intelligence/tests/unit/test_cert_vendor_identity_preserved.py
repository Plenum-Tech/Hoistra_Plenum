"""A second upsert of the same certificate must not wipe its vendor.

The regression these pin is not hypothetical. On 22 Sep 2026 three vendor accreditations
were ingested for Northbridge Estates. The single-door pass posted each one WITH its
company name, `resolve_or_create_vendor` created the vendor and set `vendor_id` — and then
the compliance sub-agent re-upserted all three through the `upsert_compliance_certificate`
tool carrying only the fields the model chose to repeat. Vendor identity was not among
them, so `vendor_id` was reset to NULL and `vendor_name` dropped out of `raw_metadata`.

Both certificates and vendors existed afterwards; nothing linked them, and nothing warned.
The unresolved-vendor warning only fires when a company name WAS supplied, so a payload
carrying none failed silently.
"""
from __future__ import annotations

from uuid import uuid4

from src.engines.compliance.certificates import _keep_vendor_id, _keep_vendor_meta


class TestVendorIdSurvivesAPartialReUpsert:
    def test_an_absent_vendor_id_keeps_the_one_already_on_the_record(self):
        prior = uuid4()
        assert _keep_vendor_id(None, prior) == prior

    def test_a_supplied_vendor_id_still_wins(self):
        prior, incoming = uuid4(), uuid4()
        assert _keep_vendor_id(incoming, prior) == incoming

    def test_a_first_ingest_with_no_prior_is_unchanged(self):
        assert _keep_vendor_id(None, None) is None

    def test_a_first_ingest_that_resolved_a_vendor_is_unchanged(self):
        incoming = uuid4()
        assert _keep_vendor_id(incoming, None) == incoming


class TestVendorNameSurvivesAPartialReUpsert:
    def test_metadata_without_a_vendor_name_keeps_the_stored_one(self):
        out = _keep_vendor_meta({"forensics_verdict": "pass"}, {"vendor_name": "Meridian Mechanical Ltd"})
        assert out["vendor_name"] == "Meridian Mechanical Ltd"
        assert out["forensics_verdict"] == "pass"

    def test_a_supplied_vendor_name_still_wins(self):
        out = _keep_vendor_meta({"vendor_name": "Brightline Electrical"}, {"vendor_name": "Stale Ltd"})
        assert out["vendor_name"] == "Brightline Electrical"

    def test_company_name_is_preserved_too(self):
        out = _keep_vendor_meta({}, {"company_name": "Guardian Fire Systems"})
        assert out["company_name"] == "Guardian Fire Systems"

    def test_nothing_else_is_resurrected_from_the_old_metadata(self):
        # Only vendor identity is carried forward. A stale forensics verdict from an earlier
        # run must NOT come back — the new write's own verdict is the current one.
        out = _keep_vendor_meta({}, {"vendor_name": "Meridian", "forensics_verdict": "pass"})
        assert out["vendor_name"] == "Meridian"
        assert "forensics_verdict" not in out

    def test_empty_prior_metadata_is_harmless(self):
        assert _keep_vendor_meta({"a": 1}, {}) == {"a": 1}
