"""A vendor named, not id'd, must not become a 422.

`upsert_compliance_certificate` exposed `vendor_id` and no `vendor_name`. A model that knows
the contractor only as "Meridian Mechanical Ltd" — which is all a certificate prints — had
nowhere correct to put it, so it put the name in the id. CertificateUpsertRequest types
vendor_id as UUID, Pydantic rejected the call 422, and the agent then reported "the record
could not be upserted into the register" about a certificate that was already stored by the
single-door pass. Observed 22 Sep 2026 on CP17-B-101-2026-0812.

The API has accepted `vendor_name` and resolved it to a vendor for as long as the column has
existed. The tool simply never offered it.
"""
from __future__ import annotations

from src.agents.compliance_engine_agent import _split_vendor_ref

UUID_S = "a6edb6cf-ba72-46ed-ba80-fc5e3bb731e6"


class TestARealUuidIsLeftAlone:
    def test_a_uuid_stays_the_id(self):
        assert _split_vendor_ref(UUID_S, None) == (UUID_S, None)

    def test_a_uuid_and_a_name_both_survive(self):
        assert _split_vendor_ref(UUID_S, "Meridian Mechanical Ltd") == (
            UUID_S,
            "Meridian Mechanical Ltd",
        )


class TestANameInTheIdFieldIsRoutedNotRejected:
    def test_a_company_name_moves_to_vendor_name(self):
        assert _split_vendor_ref("Meridian Mechanical Ltd", None) == (
            None,
            "Meridian Mechanical Ltd",
        )

    def test_an_explicit_vendor_name_is_not_overwritten(self):
        assert _split_vendor_ref("Meridian Mechanical Ltd", "Brightline Electrical") == (
            None,
            "Brightline Electrical",
        )

    def test_a_near_miss_is_still_treated_as_a_name(self):
        assert _split_vendor_ref("not-a-uuid-at-all", None) == (None, "not-a-uuid-at-all")


class TestNothingSuppliedStaysNothing:
    def test_both_absent(self):
        assert _split_vendor_ref(None, None) == (None, None)

    def test_a_blank_id_is_not_a_name(self):
        assert _split_vendor_ref("   ", None) == (None, None)

    def test_a_name_alone_passes_through(self):
        assert _split_vendor_ref(None, "Guardian Fire Systems") == (
            None,
            "Guardian Fire Systems",
        )
