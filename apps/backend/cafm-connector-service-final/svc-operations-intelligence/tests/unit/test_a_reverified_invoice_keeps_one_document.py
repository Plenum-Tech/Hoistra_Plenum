"""Re-verifying an invoice must not give it a second document.

An invoice reaches a building through plenum_cafm.documents, so verify_invoice mints a
document row when the caller has none. It minted one on *every* call: the uuid4() ran
before the existing-verification lookup, and then overwrote the id the earlier run had
already recorded. Seven verifications of MERI-2026-09-0051 left seven document rows on
Harbour Point — six of them orphaned, nothing pointing at them — and the building's
Documents panel read 15 files where 9 had been uploaded.

The verification row deduped correctly the whole time (one row, two lines, not fourteen).
Only its document did not, because the id was decided before there was anything to
compare it against.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.engines.contract_performance.invoice import verify_invoice


def _run(*, existing_document_id, passed_document_id=None):
    """Verify an invoice that has already been verified once. Returns the document_id
    that reached the graph, which is the id the documents row is keyed on."""
    seen: dict = {}

    async def _inner():
        existing = MagicMock()
        existing.id = uuid4()
        existing.document_id = existing_document_id
        existing.vendor_id = None

        def _execute(stmt, *a, **k):
            res = MagicMock()
            # Only the verification lookup is a SELECT; the line delete is not.
            res.scalar_one_or_none.return_value = (
                existing if "SELECT" in str(stmt).upper()[:8] else None
            )
            return res

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_execute)
        session.add = lambda x: None
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        async def fake_attach(_session, **kw):
            seen.update(kw)
            return {"building_id": None, "building_link_outcome": "unresolved",
                    "building_link_reason": "test", "document_id": kw.get("document_id")}

        with (
            patch(
                "src.engines.contract_performance.invoice.get_or_create_weights",
                return_value={"invoice_flag_adversary_gbp": 500.0,
                              "cost_variance_alert_pct": 15.0,
                              "cost_variance_job_count": 3},
            ),
            patch("src.engines.contract_performance.invoice.write_audit",
                  new_callable=AsyncMock),
            patch("src.engines.contract_performance.invoice._resolve_wo_ids",
                  return_value={}),
            patch("src.engines.contract_performance.invoice._persist_invoice_lines",
                  new_callable=AsyncMock),
            patch("src.engines.energy.graph_ingest.attach_to_graph", fake_attach),
        ):
            await verify_invoice(
                session,
                invoice_ref="MERI-2026-09-0051",
                lines=[{"line_id": "L1", "wo_code": "WO-B-101-35", "amount": 100.0}],
                work_orders=[],
                invoice_ref_identifies=True,
                document_id=passed_document_id,
                labour_day_rate=380.0,
            )
        return existing

    existing_row = asyncio.run(_inner())
    return seen.get("document_id"), existing_row


class TestOneInvoiceHasOneDocument:
    def test_reverification_reuses_the_document_it_already_has(self):
        first = uuid4()
        reached_graph, existing = _run(existing_document_id=first)
        assert str(reached_graph) == str(first), (
            "the second verification minted a new document id, so record_document "
            f"inserted a second row for one invoice (was {reached_graph})"
        )

    def test_reverification_does_not_overwrite_the_stored_document_id(self):
        first = uuid4()
        _, existing = _run(existing_document_id=first)
        assert str(existing.document_id) == str(first), (
            "the verification row was repointed at the new document, orphaning the old one"
        )


class TestAnUploadedFileStillWins:
    def test_a_caller_supplied_document_id_is_honoured(self):
        """An upload carries a real file and a real id; that is better evidence than
        whatever a previous verification minted for itself."""
        supplied = uuid4()
        reached_graph, _ = _run(existing_document_id=uuid4(),
                                passed_document_id=supplied)
        assert str(reached_graph) == str(supplied)


class TestAFirstVerificationStillGetsADocument:
    def test_no_existing_row_still_mints_one(self):
        reached_graph, _ = _run(existing_document_id=None)
        assert reached_graph is not None, (
            "an invoice with no document can never join to a building"
        )
