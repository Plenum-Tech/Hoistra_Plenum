"""A document that states no contract terms must not become a contract.

Contract extraction behaves correctly on a non-contract: it finds nothing, and
merge_extraction_with_defaults fills every field from SYSTEM_DEFAULTS. What was missing was
the question that follows — "did we read ANYTHING from this document?" — so the row was
written anyway, with the platform's own numbers standing in for terms the vendor never
agreed to.

On 22 Sep 2026 ingesting an INVOICE created a second contract for Meridian Mechanical:
contract_ref NULL, no hourly rate, £350/day and £525 overtime, every value a default. It
then outranked the real contract (£47.50/hour, nothing defaulted) on the Vendors page, and
the confirm step refused it — correctly, but far too late to stop it existing.

The guard belongs at creation, where the confirm step's own test already applies: a value
nobody read from the document cannot be made binding on a vendor.
"""
from __future__ import annotations

import inspect

from src.engines.contract_performance.parameters import _read_any_terms


class TestNothingWasRead:
    def test_all_defaults_is_not_a_contract(self):
        assert _read_any_terms({"labour_day_rate": "default", "overtime_rate": "default"}) is False

    def test_no_field_sources_at_all_is_not_a_contract(self):
        assert _read_any_terms({}) is False

    def test_none_is_not_a_contract(self):
        assert _read_any_terms(None) is False


class TestSomethingWasRead:
    def test_one_field_off_the_document_is_enough(self):
        assert _read_any_terms({"labour_day_rate": "default", "labour_hour_rate": "contract"}) is True

    def test_every_field_read_is_a_contract(self):
        assert _read_any_terms({"a": "contract", "b": "contract"}) is True


class TestEveryCallerIsCovered:
    """The guard must sit at the WRITE, not at one caller of it.

    It first went into extract.py's auto_ingest branch. The orchestrator calls
    ingest_contract_parameters directly as a tool, so that path wrote contracts anyway —
    four phantom rows for one vendor, one of them named after an invoice number.
    """

    def test_the_writer_itself_refuses_a_document_with_no_terms(self):
        from src.engines.contract_performance import parameters as P

        src = inspect.getsource(P.ingest_contract_parameters)
        assert "_read_any_terms(field_sources)" in src, (
            "ingest_contract_parameters must check for read terms itself — a guard in a "
            "caller is bypassed by the orchestrator's direct tool call"
        )

    def test_the_check_precedes_any_write(self):
        from src.engines.contract_performance import parameters as P

        src = inspect.getsource(P.ingest_contract_parameters)
        assert src.index("_read_any_terms") < src.index("resolve_or_create_vendor"), (
            "the refusal must come before the function starts resolving and writing"
        )
