"""Who confirmed a contract's terms, and when — visible, not just audited.

Confirming is the act that turns extracted readings into the numbers every later judgement
is made against: SLA breaches, service credits, whether an invoice line is overcharged.
`ops_audit_log` has recorded the actor since the route was written, and the screen could not
say so — ``params_to_dict`` returned ``confirmed_at`` and dropped ``confirmed_by``.

So a reader saw "CONFIRMED" and had no way to learn who made that call, on a set of numbers
now being enforced against a supplier. The name is the point of the record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.engines.contract_performance import parameters as params_mod

ORG = UUID("00000000-0000-0000-0000-000000000001")
USER = UUID("00000000-0000-0000-0001-000000000017")


class _Row:
    """A ContractSlaParameters row, only the columns params_to_dict reads."""

    def __init__(self, **over):
        self.id = over.get("id", uuid4())
        self.organization_id = ORG
        self.vendor_id = None
        self.contract_id = None
        self.document_id = None
        self.contract_ref = "UKRI-2938"
        self.signed_date = None
        self.status = over.get("status", "confirmed")
        self.confirmed_by = over.get("confirmed_by", USER)
        self.confirmed_at = over.get("confirmed_at", datetime(2026, 9, 18, 7, 1, 51, tzinfo=timezone.utc))
        self.defaults_used = []
        self.overrides_log = []
        self.field_sources = {}
        self.created_at = None
        self.updated_at = None
        for f in (
            "sla_response_p1_hours", "sla_response_p2_hours", "sla_response_p3_hours",
            "sla_response_p4_hours", "sla_completion_p1_hours", "sla_completion_p2_hours",
            "sla_completion_p3_hours", "sla_completion_p4_hours", "labour_day_rate",
            "labour_hour_rate", "overtime_rate", "call_out_rate", "payment_terms",
        ):
            setattr(self, f, None)
        for f in ("parts_pricing_json", "kpi_clauses_json", "ppm_obligations_json", "task_criticality_json"):
            setattr(self, f, {})


def test_the_confirmer_is_returned_alongside_the_time_they_confirmed():
    d = params_mod.params_to_dict(_Row())
    assert d["confirmed_at"] is not None, "the time was already returned"
    assert d["confirmed_by"] == str(USER), (
        "a screen that shows CONFIRMED and cannot say by whom is not a record of a decision"
    )


def test_an_unconfirmed_draft_names_nobody():
    d = params_mod.params_to_dict(_Row(status="draft", confirmed_by=None, confirmed_at=None))
    assert d["confirmed_by"] is None
    assert d["confirmed_at"] is None


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def mappings(self):
        return self


class _U:
    def __init__(self, id_, name, email):
        self.id = id_
        self.full_name = name
        self.email = email


@pytest.mark.asyncio
async def test_the_list_resolves_the_confirmer_to_a_name_in_one_query():
    """Same shape as the vendor-name lookup beside it: one query for the whole page.

    A uuid on screen is not an answer to "who confirmed this?". The list already resolves
    vendor ids to names for exactly this reason; the confirmer is resolved the same way, and
    a failure to resolve degrades to no name rather than failing the page.
    """
    row = _Row()
    seen = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            seen.append((sql, params or {}))
            if "FROM plenum_cafm.users" in sql:
                return _Res([_U(str(USER), "Aasim Shaik", "aasim.shaik@plenum-tech.com")])
            if "FROM plenum_cafm.vendors" in sql or "FROM plenum_cafm.ingestion_documents" in sql:
                return _Res([])
            return _Res([row])

    out = await params_mod.list_contract_parameters(FakeSession(), organization_id=ORG)
    assert out and out[0]["confirmed_by"] == str(USER)
    assert out[0]["confirmed_by_name"] == "Aasim Shaik", (
        f"the confirmer was not resolved; queries run: {[s[:60] for s, _ in seen]}"
    )
    user_queries = [s for s, _ in seen if "FROM plenum_cafm.users" in s]
    assert len(user_queries) == 1, "one query for the page, not one per row"


@pytest.mark.asyncio
async def test_a_confirmer_who_cannot_be_resolved_leaves_the_name_empty_not_the_page_broken():
    row = _Row()

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if "FROM plenum_cafm.users" in sql:
                raise RuntimeError("users table unavailable")
            if "FROM plenum_cafm.vendors" in sql or "FROM plenum_cafm.ingestion_documents" in sql:
                return _Res([])
            return _Res([row])

    out = await params_mod.list_contract_parameters(FakeSession(), organization_id=ORG)
    assert out, "a missing name must never cost the reader the contract list"
    assert out[0]["confirmed_by_name"] is None


@pytest.mark.asyncio
async def test_no_user_lookup_runs_when_nothing_is_confirmed():
    row = _Row(status="draft", confirmed_by=None, confirmed_at=None)
    seen = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            seen.append(sql)
            if "FROM plenum_cafm.vendors" in sql or "FROM plenum_cafm.ingestion_documents" in sql:
                return _Res([])
            return _Res([row])

    out = await params_mod.list_contract_parameters(FakeSession(), organization_id=ORG)
    assert out[0]["confirmed_by_name"] is None
    assert not [s for s in seen if "FROM plenum_cafm.users" in s], (
        "a page of drafts should not query the users table at all"
    )
