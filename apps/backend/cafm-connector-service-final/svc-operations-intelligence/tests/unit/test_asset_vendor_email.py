"""The asset read names where its vendor can be written to.

28 Sep 2026: the Assets page's Raise work order and Request inspection drafts go to the asset's
vendor, and they are sent for real. The asset read named the vendor but not an address, so the
draft opened with an empty To line and the reader had to find one — or guess one. The address
is on the vendor's contact record; this reads it, in its own savepoint, so a database that
shapes vendor_contacts differently loses the address and never the asset.
"""

import pytest

from src.engines.energy import asset_intelligence as ai


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    def __init__(self, row=None, raises=None, rows=None):
        self.row, self.raises, self.calls = row, raises, []
        self.rows = rows if rows is not None else ([row] if row is not None else [])

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self.raises:
            raise self.raises
        return _Result(self.rows)


@pytest.fixture(autouse=True)
def _no_primary_column(monkeypatch):
    """Whether vendor_contacts has is_primary is read from information_schema and cached for the
    process; each test here says which database it is on instead."""
    async def no(_s):
        return False
    monkeypatch.setattr(ai, "_has_is_primary", no)


@pytest.mark.asyncio
async def test_the_first_contact_address_is_the_vendors_address():
    s = _Session(row={"email": "ops@apexmechanical.co.uk"})
    assert await ai.vendor_contact_email(s, "v-1") == "ops@apexmechanical.co.uk"
    sql, params = s.calls[0]
    assert "vendor_contacts" in sql and params == {"vid": "v-1"}


@pytest.mark.asyncio
async def test_no_vendor_means_no_lookup():
    s = _Session(row={"email": "x@y.z"})
    assert await ai.vendor_contact_email(s, None) is None
    assert s.calls == []


@pytest.mark.asyncio
async def test_a_contact_table_this_database_does_not_have_loses_the_address_not_the_asset():
    s = _Session(raises=RuntimeError('relation "plenum_cafm.vendor_contacts" does not exist'))
    assert await ai.vendor_contact_email(s, "v-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "   ", "not-an-address", "ops@", "@apex.co.uk"])
async def test_something_that_is_not_an_address_is_not_offered_as_one(value):
    s = _Session(row={"email": value})
    assert await ai.vendor_contact_email(s, "v-1") is None


@pytest.mark.asyncio
async def test_the_address_is_trimmed():
    s = _Session(row={"email": "  ops@apexmechanical.co.uk "})
    assert await ai.vendor_contact_email(s, "v-1") == "ops@apexmechanical.co.uk"


@pytest.mark.asyncio
async def test_the_primary_contact_is_preferred_where_the_register_marks_one(monkeypatch):
    """The send is real, so the address is the vendor's primary contact where vendor_contacts
    carries is_primary — the same rule the contractor recommender already uses."""
    async def yes(_s):
        return True
    monkeypatch.setattr(ai, "_has_is_primary", yes)
    s = _Session(row={"email": "ops@apexmechanical.co.uk"})
    await ai.vendor_contact_email(s, "v-1")
    assert "is_primary DESC" in s.calls[-1][0]


@pytest.mark.asyncio
async def test_without_the_column_a_single_contact_stands(monkeypatch):
    async def no(_s):
        return False
    monkeypatch.setattr(ai, "_has_is_primary", no)
    s = _Session(row={"email": "ops@apexmechanical.co.uk"})
    assert await ai.vendor_contact_email(s, "v-1") == "ops@apexmechanical.co.uk"
    assert "is_primary" not in s.calls[-1][0]


# Review, 28 Sep 2026: with no is_primary column "the first on record" was ORDER BY a random
# uuid, so a real email went to whichever contact's id sorted lowest — the accounts desk as
# often as the service desk. Several contacts and none marked is a choice for the reader.

@pytest.mark.asyncio
async def test_several_contacts_and_none_marked_is_left_to_the_reader():
    s = _Session(rows=[{"email": "accounts@apex.co.uk", "primary_flag": None},
                       {"email": "helpdesk@apex.co.uk", "primary_flag": None}])
    got = await ai.vendor_contacts(s, "v-1")
    assert got == {"email": None, "candidates": ["accounts@apex.co.uk", "helpdesk@apex.co.uk"]}
    assert await ai.vendor_contact_email(_Session(rows=s.rows), "v-1") is None


@pytest.mark.asyncio
async def test_the_marked_primary_wins_among_several(monkeypatch):
    async def yes(_s):
        return True
    monkeypatch.setattr(ai, "_has_is_primary", yes)
    s = _Session(rows=[{"email": "helpdesk@apex.co.uk", "primary_flag": True},
                       {"email": "accounts@apex.co.uk", "primary_flag": False}])
    assert await ai.vendor_contacts(s, "v-1") == {"email": "helpdesk@apex.co.uk", "candidates": []}


@pytest.mark.asyncio
async def test_one_valid_address_among_unusable_ones_stands():
    s = _Session(rows=[{"email": "ops@apex", "primary_flag": None}, {"email": "ops@apex.co.uk", "primary_flag": None},
                       {"email": " OPS@apex.co.uk ", "primary_flag": None}])
    assert await ai.vendor_contacts(s, "v-1") == {"email": "ops@apex.co.uk", "candidates": []}



@pytest.mark.asyncio
async def test_a_failed_probe_is_not_remembered_as_no_column(monkeypatch):
    """contractors._has_is_primary cached a transient failure as False for the life of the
    process, so one timeout at start-up sent every later email by the random order."""
    from src.engines.compliance import contractors as c
    monkeypatch.setattr(c, "_IS_PRIMARY", None)

    class Boom(_Session):
        async def execute(self, stmt, params=None):
            raise RuntimeError("statement timeout")

    class Scalar:
        def scalar(self):
            return 1

    class Ok(_Session):
        async def execute(self, stmt, params=None):
            return Scalar()

    assert await c._has_is_primary(Boom()) is False
    assert c._IS_PRIMARY is None, "a failure is not an answer"
    assert await c._has_is_primary(Ok()) is True
