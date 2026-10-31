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
from . import name_match

log = structlog.get_logger(__name__)

_SERVICE = "operations_intelligence"
_TIMEOUT = 60.0

# ── The record a search is about, named however it is typed (29 Oct 2026) ──────────────────
#
# The route compares a vendor with ILIKE '%text%', a contract_ref with = and an asset with code =
# or name ILIKE, so "Acme contractor", "Facilities Acme", "CT1042" and "B090765" found no document
# of "Acme Facilities Ltd", "CT-1042" or "B-090765", "Building 7" read Building 70's papers too,
# and "the contractor" was searched for as a name. Each name is first resolved by the shared
# ladder (agents/name_match.py) against the caller's own registers - the assets, and the contracts
# with their vendors - and the record's stored name or code is what is searched. A name several
# records hold is reported as ambiguous, never one of them picked. Not on a register (a vendor
# with certificates but no contract), it is searched as typed, then without its kind words.

_VENDOR_KIND_WORDS = frozenset({"vendor", "vendors", "contractor", "contractors", "supplier", "suppliers"})
_CONTRACT_KIND_WORDS = frozenset({"contract", "contracts", "agreement", "agreements", "ref", "reference"})


def _ambiguous(what: str, typed, candidates: list) -> dict:
    return {"ok": False, "error": what + "_ambiguous", "asked_for": typed, "candidates": candidates,
            "guidance": "'" + str(typed) + "' names " + str(len(candidates)) + " " + what + "s. Ask which one "
                        "is meant, or search each by its name; never answer from one of them as if it were all."}


def _one_name(typed, names, kind) -> tuple[str | None, list[str]]:
    """(the stored name ``typed`` names, []) — or (None, every name it names when several do)."""
    by_clean: dict[str, str] = {}
    for n in names:
        if n and name_match.clean(n) not in by_clean:
            by_clean[name_match.clean(n)] = str(n)
    hits = name_match.best_matches(typed, [(n, n) for n in by_clean.values()], kind)
    found = list(dict.fromkeys(k for k, _ in hits))
    if len(found) == 1:
        return found[0], []
    return None, found


async def _contract_register() -> list[dict] | None:
    """The caller's contracts (contract_ref, vendor_name), or None when they cannot be read."""
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"),
                              "/api/contract-performance/contracts", service=_SERVICE, timeout=_TIMEOUT,
                              params={"limit": 500})     # the route's maximum; it reads 100 by default
        body = resp.json()
        rows = body.get("parameters") if isinstance(body, dict) else None
        return [r for r in rows or [] if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 - the search goes on with the name as typed
        log.warning("document_search.contracts_unread", error=str(exc)[:200])
        return None


async def _asset_value(asset: str, building_id: str | None) -> tuple[str, dict | None, bool]:
    """(what to search the asset by, a refusal, whether it was left as typed)."""
    from .wo_engine_agent import find_assets

    try:
        rows, _top = await find_assets(asset)
    except Exception as exc:  # noqa: BLE001 - the route's own match, as before
        log.warning("document_search.assets_unread", error=str(exc)[:200])
        return str(asset), None, True
    if building_id:
        rows = [r for r in rows if str(r.get("building_id") or "") == str(building_id)] or rows
    if not rows:
        return str(asset), None, True
    by_code: dict[str, dict] = {}
    for r in rows:
        by_code.setdefault(name_match.clean(r.get("asset_code") or r.get("asset_name") or ""), r)
    if len(by_code) > 1:
        return "", _ambiguous("asset", asset, [{k: r.get(k) for k in ("asset_code", "asset_name", "building_id")}
                                               for r in list(by_code.values())[:20]]), False
    r = next(iter(by_code.values()))
    return str(r.get("asset_code") or r.get("asset_name")), None, False


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
    warranty, manual). Pass the question as asked. Names are found however they are typed (word
    order, punctuation, a code without its hyphen); a name several records share comes back as
    `vendor_ambiguous` / `asset_ambiguous` / `contract_ambiguous` with the candidates - ask which
    one is meant. A `name_note` says a name was read as its closest spelling: say so.

    Returns `passages` (text, document, page) to quote, `searched` documents, and `not_indexed`
    documents - linked and on file but with no searchable text. When nothing is indexed, say so,
    name the documents, and answer from what the records extracted from them (e.g. a contract's
    clause and page in its parameters). Never quote a document this did not return.
    """
    from .energy_intelligence_agent import _resolve_building
    from .thread_scope import scope_building
    from .wo_engine_agent import _ASSET_KIND_WORDS

    kinds = {"vendor": _VENDOR_KIND_WORDS, "contract_ref": _CONTRACT_KIND_WORDS, "asset": _ASSET_KIND_WORDS}
    for k, v in (("vendor", vendor), ("contract_ref", contract_ref), ("asset", asset)):
        if v and not name_match.meaningful(str(v), kinds[k]):
            # "the contractor", "the asset": a word for the kind of record, not a record's name.
            return {"ok": False, "error": "names_nothing", "asked_for": {k: v},
                    "guidance": "'" + str(v) + "' names no " + k.replace("_", " ") + ". Name it, or the record "
                                "the documents are linked to; do not search every document for it."}
    if not (vendor or contract_ref or asset or building_name):
        building_name, _ = scope_building()   # a follow-up: the thread's building
    params: dict = {"q": question[:500]}
    if doc_type:
        params["doc_type"] = str(doc_type)[:120]
    bid = None
    if building_name:
        bid, problem = await _resolve_building(building_name, None)
        if problem:
            return problem
        params["building_id"] = bid
    as_typed: dict[str, str] = {}      # names on no register: searched as typed, then without kind words
    notes: list[str] = []
    if asset:
        value, problem, typed = await _asset_value(str(asset), bid)
        if problem:
            return problem
        params["asset"] = value[:120]
        if typed:
            as_typed["asset"] = str(asset)
        elif name_match.rung(str(asset), value, kinds["asset"]) == 4:
            notes.append("'" + str(asset) + "' is not on the register as written; searched for its closest spelling, '"
                         + value + "' - say so.")
    register = await _contract_register() if (vendor or contract_ref) else None
    for k, v, col in (("vendor", vendor, "vendor_name"), ("contract_ref", contract_ref, "contract_ref")):
        if not v:
            continue
        one, several = _one_name(str(v), [r.get(col) for r in register or []], kinds[k])
        if several:
            return _ambiguous(k.split("_")[0], v, sorted(several))
        params[k] = (one or str(v))[:120]
        if not one:
            as_typed[k] = str(v)
        elif name_match.rung(str(v), one, kinds[k]) == 4:
            notes.append("'" + str(v) + "' is not on the register as written; searched for its closest spelling, '"
                         + one + "' - say so.")
    if len(params) == 1:
        return {"ok": False, "error": "Name what the documents are linked to - vendor, contract_ref, asset or "
                                      "building_name - so the search stays inside them."}
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/documents/search",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        out = resp.json()
        retry = {k: " ".join(name_match.meaningful(v, kinds[k])) for k, v in as_typed.items()}
        retry = {k: c[:120] for k, c in retry.items() if c and c != params[k].strip().lower()}
        if retry and isinstance(out, dict) and out.get("ok") is not False and not out.get("documents_found"):
            resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/documents/search",
                                  service=_SERVICE, timeout=_TIMEOUT, params={**params, **retry})
            again = resp.json()
            if isinstance(again, dict) and again.get("documents_found"):
                out = again
        if notes and isinstance(out, dict):
            out["name_note"] = " ".join(notes)
        return out
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise into the agent loop
        log.error("document_search.failed", error=str(exc)[:300])
        return {"ok": False, "error": "The document search failed: " + str(exc).splitlines()[0][:300]}
