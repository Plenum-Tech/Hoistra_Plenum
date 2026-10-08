"""A supplier named in a contract is registered under the company's own id.

Found on 17 Sep 2026 from a contract that vanished. The upload said "Ingestion complete",
the row was written, and the Vendors page never showed it: `vendor_id` was NULL. The log
told the rest —

    contractors.resolve_vendor_failed
    invalid input for query argument $2: 1 ('int' object has no attribute 'bytes')

`resolve_or_create_vendor` squashed the company's id to an integer before the INSERT,
on the strength of a comment saying organization_id "is a legacy INTEGER column". It is
not, and never was on this database: every one of the 120 organization_id columns in
plenum_cafm is uuid, organizations.id is uuid, and all six companies carry long-code ids.
int(UUID('00000000-…-000000000001')) is 1 — plausible-looking, and rejected by a uuid
column every single time. Three seeded tenants have ids that squash to small numbers,
which is exactly why the failure never looked absurd in a log.

The same function links compliance certificates to suppliers. 23 of 40 certificates had
no vendor; no vendor had been created since 27 August. This is the test that would have
caught it the day the cast was written.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.engines.compliance import contractors

ORG = UUID("00000000-0000-0000-0000-000000000001")
HASHED_ORG = UUID("1ff9670d-8aa7-4948-bd81-89eea083394b")   # a tenant whose id is nothing like a number


class _Result:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def first(self): return self._rows[0] if self._rows else None
    # find_vendor_id's normalised-name step reads every candidate and compares in Python.
    def all(self): return list(self._rows)
    def scalar(self): return self._rows[0]["id"] if self._rows else None


class _Nested:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    """No vendor exists; records every statement and the parameters bound to it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def begin_nested(self): return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return _Result([])

    def inserts(self):
        return [p for s, p in self.calls if s.upper().startswith("INSERT INTO PLENUM_CAFM.VENDORS")]


@pytest.mark.parametrize("org", [ORG, HASHED_ORG], ids=["seeded-looks-numeric", "random-uuid"])
async def test_a_new_supplier_is_inserted_under_the_companys_own_id(org):
    s = FakeSession()
    new_id = await contractors.resolve_or_create_vendor(
        s, company_name="Moreland Estate Property Management Limited", organization_id=org,
        create_if_missing=True,
    )
    assert new_id is not None, "creation must not be swallowed into None"
    ins = s.inserts()
    assert len(ins) == 1, "exactly one vendor row is written"
    bound = ins[0]["org"]
    assert str(bound) == str(org), f"the company id must reach the database as itself, got {bound!r}"
    assert not isinstance(bound, int), "the id was squashed to an integer — that is the bug"


async def test_the_supplier_row_carries_the_name_as_written():
    s = FakeSession()
    await contractors.resolve_or_create_vendor(
        s, company_name="  Moreland   Estate  Property Management Limited ", organization_id=ORG,
    )
    assert s.inserts()[0]["name"] == "Moreland Estate Property Management Limited"


async def test_no_company_means_no_creation_and_says_so_by_returning_none():
    """A vendor that belongs to no company is unreachable by every scoped read. Refusing is
    right; the caller then reports it rather than writing an orphan."""
    s = FakeSession()
    out = await contractors.resolve_or_create_vendor(
        s, company_name="Moreland Estate", organization_id=None, create_if_missing=True,
    )
    assert out is None and s.inserts() == []
