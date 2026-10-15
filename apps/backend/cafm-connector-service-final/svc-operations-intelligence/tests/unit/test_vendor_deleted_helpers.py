"""The shared vocabulary for a deleted (retired) vendor — what every reader filters on.

A retired vendor keeps its row so history still resolves; status 'deleted' is what says so,
and the name carries a visible mark so every history display in every service shows it.
"""
from __future__ import annotations

import asyncio

import pytest

from src.shared import vendor_identity as vi


# ── the name mark ────────────────────────────────────────────────────────────

def test_a_retired_vendor_name_carries_the_mark():
    assert vi.mark_deleted_name("Acme Lifts Ltd", 255) == "Acme Lifts Ltd (deleted)"


def test_the_mark_is_never_added_twice():
    assert vi.mark_deleted_name("Acme Lifts Ltd (deleted)", 255) == "Acme Lifts Ltd (deleted)"


def test_a_long_name_is_cut_so_the_mark_still_fits_the_column():
    out = vi.mark_deleted_name("A" * 200, 200)
    assert len(out) == 200
    assert out == "A" * 190 + " (deleted)"


def test_trailing_space_left_by_the_cut_is_dropped():
    # 20 - len(" (deleted)") = 10 characters of name: "Acme Lift " -> "Acme Lift".
    assert vi.mark_deleted_name("Acme Lift Services", 20) == "Acme Lift (deleted)"


def test_a_nameless_vendor_still_reads_as_deleted():
    assert vi.mark_deleted_name("", 255) == "Deleted vendor"
    assert vi.mark_deleted_name(None, 255) == "Deleted vendor"
    assert vi.mark_deleted_name("   ", 255) == "Deleted vendor"


def test_no_column_limit_means_no_cut():
    assert vi.mark_deleted_name("X" * 300, None) == "X" * 300 + " (deleted)"


def test_the_original_name_is_recovered_from_a_marked_one():
    assert vi.unmarked_name("Acme Lifts Ltd (deleted)") == "Acme Lifts Ltd"
    assert vi.unmarked_name("Acme Lifts Ltd") == "Acme Lifts Ltd"
    assert vi.unmarked_name(None) == ""


# ── the live-vendor predicate ────────────────────────────────────────────────

def test_the_live_predicate_names_the_alias_it_is_given():
    # A query aliasing vendors as "ven" must not be handed a predicate about "v".
    sql = vi.live_vendor_sql("ven")
    assert "to_jsonb(ven)" in sql and "to_jsonb(v)" not in sql


def test_the_live_predicate_refuses_an_unsafe_alias():
    with pytest.raises(ValueError):
        vi.live_vendor_sql("v; DROP TABLE x")


def test_the_deleted_ids_subquery_lists_only_retired_rows():
    sql = vi.deleted_vendor_ids_sql()
    assert sql.lstrip().upper().startswith("SELECT")
    assert "NOT (" in sql   # the negation of the live predicate, not a second spelling of it


# ── is_deleted_vendor ────────────────────────────────────────────────────────

class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Session:
    def __init__(self, status=None, raise_exc=None):
        self.status = status
        self.raise_exc = raise_exc
        self.sql = []
        self.params = []

    async def execute(self, clause, params=None):
        self.sql.append(str(clause))
        self.params.append(params or {})
        if self.raise_exc:
            raise self.raise_exc
        return _Result(self.status)

    def begin_nested(self):
        session = self

        class _Nested:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Nested()


def test_a_vendor_whose_status_is_deleted_is_deleted():
    s = _Session(status="deleted")
    assert asyncio.run(vi.is_deleted_vendor(s, "V-01")) is True
    assert s.params[0]["vid"] == "V-01"


def test_the_status_check_ignores_case():
    assert asyncio.run(vi.is_deleted_vendor(_Session(status="Deleted"), "x")) is True


def test_an_active_or_missing_vendor_is_not_deleted():
    assert asyncio.run(vi.is_deleted_vendor(_Session(status="active"), "x")) is False
    assert asyncio.run(vi.is_deleted_vendor(_Session(status=None), "x")) is False


def test_no_id_asks_nothing():
    s = _Session(status="deleted")
    assert asyncio.run(vi.is_deleted_vendor(s, None)) is False
    assert asyncio.run(vi.is_deleted_vendor(s, "")) is False
    assert s.sql == []


def test_an_unreadable_status_reads_as_not_deleted_by_default():
    s = _Session(raise_exc=RuntimeError("boom"))
    assert asyncio.run(vi.is_deleted_vendor(s, "x")) is False


def test_a_write_path_can_ask_for_an_unreadable_status_to_count_as_deleted():
    s = _Session(raise_exc=RuntimeError("boom"))
    assert asyncio.run(vi.is_deleted_vendor(s, "x", unreadable_is_deleted=True)) is True


# ── 12 Oct 2026 review: spelling-proof, and a write waits for a delete in flight ─────────

def test_the_status_check_matches_the_id_whatever_its_case():
    # A uuid sent upper-case ("AAAAAAAA-…") is the same vendor; an exact text match missed it
    # and read a retired vendor as live.
    s = _Session(status="deleted")
    asyncio.run(vi.is_deleted_vendor(s, "AAAAAAAA-0000-0000-0000-000000000001"))
    assert "lower(v.id::text) = lower(:vid)" in s.sql[0]


def test_a_read_takes_no_lock_and_a_write_waits_for_a_delete_in_flight():
    read, write = _Session(status="active"), _Session(status="active")
    asyncio.run(vi.is_deleted_vendor(read, "x"))
    asyncio.run(vi.is_deleted_vendor(write, "x", for_share=True))
    assert "FOR SHARE" not in read.sql[0].upper()
    assert write.sql[0].rstrip().upper().endswith("FOR SHARE")


# ── vendor_link_refusal: a write that links new data to a vendor ─────────────────────────

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"


class _RowResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _RowSession(_Session):
    def __init__(self, row=None, raise_exc=None):
        super().__init__(raise_exc=raise_exc)
        self.row = row

    async def execute(self, clause, params=None):
        self.sql.append(str(clause))
        self.params.append(params or {})
        if self.raise_exc:
            raise self.raise_exc
        return _RowResult(self.row)


def _refusal(row=None, raise_exc=None, vid="v-1", org=ORG_A):
    s = _RowSession(row, raise_exc)
    return s, asyncio.run(vi.vendor_link_refusal(s, vid, org, what="nothing was linked"))


def test_a_live_vendor_of_the_company_may_be_linked():
    s, out = _refusal({"organization_id": ORG_A, "status": "active"})
    assert out is None
    assert "lower(v.id::text) = lower(:vid)" in s.sql[0]
    assert s.sql[0].rstrip().upper().endswith("FOR SHARE")


def test_a_vendor_that_does_not_exist_is_refused_as_not_found():
    # A tab opened before the vendor was REMOVED still holds its id.
    _, out = _refusal(None)
    assert out["ok"] is False and out["reason"] == "vendor_not_found"


def test_another_companys_vendor_answers_like_a_missing_one():
    _, out = _refusal({"organization_id": ORG_B, "status": "active"})
    assert out["reason"] == "vendor_not_found"


def test_a_retired_vendor_is_refused_as_deleted():
    _, out = _refusal({"organization_id": ORG_A, "status": "deleted"})
    assert out["reason"] == "vendor_deleted"
    assert out["error"] == "This vendor has been deleted, so nothing was linked."


def test_an_unreadable_status_refuses_with_its_own_reason():
    _, out = _refusal(raise_exc=RuntimeError("statement timeout"))
    assert out["reason"] == "vendor_unreadable"


def test_no_company_known_checks_existence_and_status_only():
    _, out = _refusal({"organization_id": ORG_B, "status": "active"}, org=None)
    assert out is None


def test_no_vendor_named_is_nothing_to_refuse():
    s, out = _refusal(None, vid="  ")
    assert out is None and s.sql == []
