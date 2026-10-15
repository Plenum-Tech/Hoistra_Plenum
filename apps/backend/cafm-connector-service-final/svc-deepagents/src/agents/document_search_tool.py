"""Search inside the documents a question is about (svc-operations-intelligence /api/documents/search).

One general tool every engine holds. The agent plans: a fact in the records (a count, a date, a
status) is read from the records; a fact in a document (a contract clause, a report's
recommendation, a warranty's cover, a procedure) is looked up here, in the documents LINKED to the
record the question is about - never across the whole corpus. Scoped to the caller's company and
buildings by the service, cited by file and page. The plan itself is in skills/query-builder.
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_SERVICE = "operations_intelligence"
_TIMEOUT = 60.0


@tool
async def search_documents(question: str, vendor: str | None = None, contract_ref: str | None = None,
                           asset: str | None = None, building_name: str | None = None,
                           doc_type: str | None = None) -> dict:
    """Find what a DOCUMENT says - inside the documents linked to the record the question is about.

    Use it when an answer needs a fact that lives in a document, not a table: a contract's SLA,
    penalty or service-credit clause, an inspection or service report's findings and
    recommendations, a warranty's cover and exclusions, a certificate's conditions, an O&M
    procedure. Name what the documents are linked to - at least one of `vendor` (its contracts and
    certificates), `contract_ref`, `asset` (code or name: its certificates, warranties, reports,
    job sheets), `building_name`; `doc_type` narrows (contract, certificate, invoice, report,
    warranty, manual). Pass the question as asked.

    Returns `passages` (text, document, page) to quote, `searched` documents, and `not_indexed`
    documents - linked and on file but with no searchable text. When nothing is indexed, say so,
    name the documents, and answer from what the records extracted from them (e.g. a contract's
    clause and page in its parameters). Never quote a document this did not return.
    """
    from .energy_intelligence_agent import _resolve_building
    from .thread_scope import scope_building

    if not (vendor or contract_ref or asset or building_name):
        building_name, _ = scope_building()   # a follow-up: the thread's building
    params: dict = {"q": question[:500]}
    for k, v in (("vendor", vendor), ("contract_ref", contract_ref), ("asset", asset), ("doc_type", doc_type)):
        if v:
            params[k] = str(v)[:120]
    if building_name:
        bid, problem = await _resolve_building(building_name, None)
        if problem:
            return problem
        params["building_id"] = bid
    if len(params) == 1:
        return {"ok": False, "error": "Name what the documents are linked to - vendor, contract_ref, asset or "
                                      "building_name - so the search stays inside them."}
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/documents/search",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise into the agent loop
        log.error("document_search.failed", error=str(exc)[:300])
        return {"ok": False, "error": "The document search failed: " + str(exc).splitlines()[0][:300]}
