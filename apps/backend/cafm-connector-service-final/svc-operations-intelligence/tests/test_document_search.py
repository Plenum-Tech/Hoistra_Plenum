"""Document search: a register row's company comes from the row, its building, or the record citing it.

Run read-only against hoistra_test on 2 Oct 2026: Plenum's Apex Mechanical search found its contract
AM-2024-HVAC-07 (the register row has no company; its SLA parameters do), every result honest that
the text is not indexed; Plenum asking about Meridian found nothing; unrestricted found Meridian's
4 documents and 8 passages."""
from __future__ import annotations

import uuid

from src.engines import document_search as ds

COLS = {("contract_sla_parameters", "document_id"), ("contract_sla_parameters", "organization_id"),
        ("compliance_certificates", "document_id"), ("compliance_certificates", "organization_id"),
        ("compliance_certificates", "org_id"), ("compliance_certificates", "building_id"),
        ("work_orders", "building_id")}


def test_a_documents_company_falls_back_to_its_building_then_the_records_that_cite_it():
    org, bld = ds._owner_exprs(COLS)
    assert org.startswith("COALESCE(d.organization_id::text")
    assert "plenum_cafm.buildings b" in org and "contract_sla_parameters" in org
    assert "coalesce(x.organization_id, x.org_id)" in org
    # A link column the database lacks is never named (work_orders has no document_id here).
    assert "work_orders" not in org and "work_orders" not in bld
    assert "compliance_certificates" in bld


def test_the_scope_binds_the_company_and_refuses_an_empty_allocation():
    org = uuid.uuid4()
    sql, p = ds._scope(org, None, COLS)
    assert "= :ds_org" in sql and p["ds_org"] == str(org)
    assert ds._scope(org, (), COLS) == (" AND FALSE", {})
    b = uuid.uuid4()
    sql, p = ds._scope(org, (b,), COLS)
    assert "ANY(:ds_b)" in sql and p["ds_b"] == [str(b)]


def test_search_terms_keep_the_words_that_carry_the_question():
    assert ds.search_terms("What does the contract say about the service credit for a P2 breach?") == \
        ["service", "credit", "p2", "breach"]


def test_a_question_naming_nothing_searches_only_the_companys_indexed_documents(monkeypatch):
    import asyncio

    seen: list[tuple[str, dict]] = []

    async def cols(_session):
        return COLS

    async def grab(_session, sql, params):
        seen.append((sql, params))
        return []

    monkeypatch.setattr(ds, "_columns", cols)
    monkeypatch.setattr(ds, "_grab_rows", grab)
    org = uuid.uuid4()
    out = asyncio.run(ds.search(None, question="What is the fire door inspection interval?", organization_id=org))
    sql, params = seen[0]
    assert params["ds_org"] == str(org)                       # the company scope still binds
    assert "EXISTS (SELECT 1 FROM plenum_cafm.ingestion_documents" in sql  # indexed ones only
    assert f"LIMIT {ds.MAX_COMPANY_DOCUMENTS}" in sql
    assert out["ok"] and out["passages"] == [] and "No document your company owns is indexed" in out["note"]

    seen.clear()
    asyncio.run(ds.search(None, question="SLA credit", organization_id=org, vendor="Apex"))
    assert "EXISTS (SELECT 1 FROM plenum_cafm.ingestion_documents" not in seen[0][0]
    assert f"LIMIT {ds.MAX_DOCUMENTS}" in seen[0][0]
