"""Which of the vendors we already have is named in a document. No database.

An uploaded invoice was attributed to no vendor at all: the name is looked for with a
CSV-shaped regex, which finds nothing in a PDF, and an invoice with no vendor also finds no
contract — so the labour-rate and parts checks are skipped on an invoice that reads as fully
verified.

Reading a supplier's name out of free text means guessing where it starts and stops, and a
wrong guess attributes money to a company that does not exist. This asks the opposite
question, which can only ever answer with a vendor that is already in the register.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.shared import vendor_identity

VENDORS = [
    {"id": "v-halden", "vendor_name": "Halden Building Services"},
    {"id": "v-halden-fm", "vendor_name": "Halden"},
    {"id": "v-apex", "vendor_name": "Apex Lift Services"},
    {"id": "v-short", "vendor_name": "AB"},
]


class _Nested:
    async def __aenter__(self) -> "_Nested":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class Session:
    def __init__(self, vendors: list[dict[str, str]] | None = None) -> None:
        self.vendors = VENDORS if vendors is None else vendors

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        # The query orders by name length descending; the stub does the same so the test
        # exercises the tie-break rather than the database's collation.
        rows = sorted(self.vendors, key=lambda v: -len(v["vendor_name"]))

        class R:
            def mappings(self_inner) -> Any:
                class M:
                    def all(self_m) -> list[dict[str, str]]:
                        return rows
                return M()
        return R()


def run(text: str | None, vendors: list[dict[str, str]] | None = None) -> Any:
    return asyncio.run(vendor_identity.vendor_named_in(Session(vendors), text))


def test_the_vendor_named_on_the_invoice():
    assert run("VAT INVOICE\nHalden Building Services\nManchester") == "v-halden"


def test_the_longer_name_wins():
    # "Halden" also appears in "Halden Building Services". The more specific name is the one
    # meant, and picking the shorter would attribute the invoice to a different vendor.
    assert run("Invoice from Halden Building Services for Riverside Court") == "v-halden"


def test_a_vendor_not_named_is_not_matched():
    assert run("Invoice from Someone Else Entirely Ltd") is None


def test_case_and_whitespace_do_not_matter():
    assert run("invoice   from\n  APEX   LIFT   SERVICES") == "v-apex"


def test_a_two_letter_vendor_name_is_never_matched():
    # "AB" occurs inside ordinary words. Matching it would attribute documents to whichever
    # vendor happened to be initialised that way.
    assert run("Cabling works and fabrication, absolutely standard") is None


def test_nothing_to_read_is_none():
    for empty in (None, "", "  ", "x"):
        assert run(empty) is None


def test_an_empty_register_matches_nothing():
    assert run("Halden Building Services", vendors=[]) is None
