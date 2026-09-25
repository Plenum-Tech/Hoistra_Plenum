"""A flagged invoice line must reach the vendor it belongs to.

The Vendors page builds its Invoices tab from the approvals queue: it filters items of type
invoice_flag and hands each to the vendor named on it (vendorsLive.js reads
`payload.vendor_id || related_entity_id`). The queue item carried neither — its payload was
{invoice_ref, line} and related_entity_id was None.

So on 23 Sep 2026 a line billed at GBP 62.00 against a confirmed GBP 47.50 contract was
flagged correctly, priced correctly at GBP 27.55, and reached the queue correctly — the
portfolio header counted it as "INVOICE LINES HELD" while Meridian Mechanical's own Invoices
tab read 0, because nothing on the item said whose invoice it was.

vendor_id is a parameter of verify_invoice. It was in scope the whole time.
"""
from __future__ import annotations

import inspect

from src.engines.contract_performance import invoice as inv


class TestBothQueuePathsNameTheVendor:
    def test_every_invoice_flag_payload_carries_vendor_id(self):
        src = inspect.getsource(inv.verify_invoice)
        # Two branches enqueue a flag: the adversary-reviewed one and the plain one.
        assert src.count('item_type="invoice_flag"') >= 1
        assert src.count('"vendor_id": str(vendor_id) if vendor_id else None') == 2, (
            "both the adversary and the plain flagged branch must put vendor_id on the "
            "queue item, or the Vendors page cannot attribute the line"
        )

    def test_the_plain_branch_also_sets_related_entity(self):
        src = inspect.getsource(inv.verify_invoice)
        assert "related_entity_id=vendor_id" in src, (
            "vendorsLive.js falls back to related_entity_id when payload.vendor_id is absent"
        )


class TestTheVendorIsAvailableToUse:
    def test_verify_invoice_takes_a_vendor_id(self):
        assert "vendor_id" in inspect.signature(inv.verify_invoice).parameters
