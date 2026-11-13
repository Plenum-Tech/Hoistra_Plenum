"""Which pages the caller holds, and what the chat may do with that.

An administrator may give a user an access role and tick the pages that user may open. From
then on data owned by any other page is withheld from them everywhere — and the chat is the
place it would otherwise leak fastest, because it reads across every register to answer one
question.

The pages come from the identity service with the caller (services/principal.py), not from
the browser. The space a browser declares (agents/spaces.py) can only narrow further: what a
question may use is what the space AND the pages both allow.

Three things are held to the pages here:

  * **Engines.** A question about a page the caller does not hold is answered with one plain
    sentence and nothing is read (the router's existing out-of-module path).
  * **Tools.** The model is never handed a tool it may not use: every tool list is filtered
    where it is bound. A tool is tagged with the page whose register it reads — by the module
    it is defined in, with the few exceptions named below.
  * **Documents.** A document belongs to the page that owns its type.

The backends are the second layer. Every tool call carries the caller's own token
(http_client.request), so a tool that slipped through would be refused by the service it
calls. The tools with no service behind them — the ones that read any table, or the document
index, which has no notion of who is asking — are withheld from a caller held to pages
outright, whatever pages those are.

``pages is None`` is a caller with every page: a normal user, an admin, a superadmin. Every
function here leaves such a caller exactly as they were.
"""
from __future__ import annotations

import functools
from contextvars import ContextVar
from typing import Any

#: "Read the pages from the signed-in caller" — the default, so no entry point has to
#: remember to set anything: wherever the caller is set, their pages follow.
FROM_CALLER: Any = object()

#: An explicit set of pages for the current context, overriding the caller's. Tests set it;
#: a run made on someone's behalf can. Left at FROM_CALLER, the caller decides.
active_pages: ContextVar[Any] = ContextVar("hoistra_active_pages", default=FROM_CALLER)

PAGES: tuple[str, ...] = ("buildings", "compliance", "energy", "vendors", "ops", "assets")

#: What a tool or a sub-agent answers when it is asked for something the caller's pages do
#: not own. One sentence, naming nothing internal.
OUTSIDE_PAGES = "That is outside the pages you have access to."


def set_for(principal: Any) -> None:
    """Pin the pages to this caller's for the current context."""
    active_pages.set(getattr(principal, "pages", None) if principal is not None else None)


def current() -> frozenset[str] | None:
    """The pages the current request's caller holds, or None for every page.

    Read from the signed-in caller (services/principal.caller_principal), which every route
    that runs a question already sets. A request with no caller is not held to pages here —
    the routes that take one refuse it before anything runs.
    """
    try:
        pinned = active_pages.get()
    except LookupError:          # pragma: no cover - a context without the var set
        pinned = FROM_CALLER
    if pinned is not FROM_CALLER:
        return pinned
    from ..services.principal import caller_principal  # local: keeps this module import-light

    try:
        caller = caller_principal.get()
    except LookupError:          # pragma: no cover
        caller = None
    return getattr(caller, "pages", None) if caller is not None else None


def narrowed_at() -> Any:
    """When the caller's pages last got narrower, for a caller held to some pages; else None."""
    from ..services.principal import caller_principal  # local: keeps this module import-light

    try:
        caller = caller_principal.get()
    except LookupError:          # pragma: no cover
        caller = None
    if caller is None or getattr(caller, "pages", None) is None:
        return None
    return getattr(caller, "access_narrowed_at", None)


def thread_key(session_id: Any) -> Any:
    """The key the loop's own state is kept under for this caller.

    The loop saves its messages - every earlier tool result - per conversation, and a session
    keeps its own notes and files. After a person's pages are narrowed those may hold another
    page's records, and the model would be handed them again on the next turn. So a caller
    with a narrowing carries on under a key of their own from that moment: same conversation
    on screen, nothing from before it in the model's hands. Everyone else keeps the session id
    itself, so their state is where it always was.
    """
    cut = narrowed_at()
    if not session_id or cut is None:
        return session_id
    try:
        stamp = int(cut.timestamp())
    except (AttributeError, OverflowError, OSError, ValueError):
        stamp = "cut"
    return f"{session_id}@{stamp}"


def pages_key(pages: frozenset[str] | None) -> tuple[str, ...] | None:
    """A stable key for one set of pages, for caching what is built per set."""
    return None if pages is None else tuple(sorted(pages))


# ── engines ───────────────────────────────────────────────────────────────────────────
#: The page each domain engine reads. The same table agents/spaces.py keeps for spaces; a
#: test holds the two equal.
ENGINE_PAGE: dict[str, str] = {
    "wo_engine": "ops",
    "contract_performance": "vendors",
    "compliance": "compliance",
    "energy_intelligence": "energy",
}

#: What each engine's subject is called in a sentence.
_SUBJECT: dict[str, str] = {
    "wo_engine": "work orders and maintenance",
    "contract_performance": "vendor performance",
    "compliance": "compliance",
    "energy_intelligence": "energy",
}


#: The sub-agents that are a domain engine, by the engine (the Maintenance page's read-only
#: agent is wo_engine's).
SUBAGENT_ENGINE: dict[str, str] = {**{e: e for e in ENGINE_PAGE}, "maintenance": "wo_engine"}


def allows_engine(engine: str | None) -> bool:
    """Whether the caller's pages allow answering from this engine. An engine that is not a
    domain engine is not decided here: its tools are."""
    pages = current()
    if pages is None or not engine or engine not in ENGINE_PAGE:
        return True
    return ENGINE_PAGE[engine] in pages


def refusal(engine: str | None) -> str | None:
    """What to say when a question belongs to a page the caller does not hold. It never
    offers to answer anyway, and names nothing but the subject."""
    if allows_engine(engine):
        return None
    return ("That question is about " + _SUBJECT.get(engine or "", "something")
            + ", which is outside the pages you have access to.")


# ── tools ─────────────────────────────────────────────────────────────────────────────
SHARED, INGEST, ANY = "shared", "ingest", "any"

#: The page a tool reads, by the module it is defined in. ``shared`` is open to everyone,
#: ``ingest`` is adding data (governed by the can-ingest switch), ``any`` can read any
#: register and is withheld from a caller held to pages.
_MODULE_TAGS: dict[str, tuple[str, ...]] = {
    "compliance_engine_agent": ("compliance",),
    "compliance_agent": ("compliance",),
    "contract_performance_agent": ("vendors",),
    "energy_intelligence_agent": ("energy",),
    "wo_engine_agent": ("ops",),
    # The table tools name their own table; the hybrid and ontology tools read across
    # registers; the document index answers anybody about any document.
    "udr_agent": (ANY,),
    "udr_hybrid_tools": (ANY,),
    "ontology_qa": (ANY,),
    "doc_rag_agent": (ANY,),
    # Searches the document register through the service that narrows it to the caller's pages.
    "document_search_tool": (SHARED,),
    "meta_tools": (SHARED,),
    "context_budget": (SHARED,),
    "migration_agent": (INGEST,),
    "fiix_agent": (INGEST,),
    "schema_mapper_agent": (INGEST,),
    "connector_tools": (INGEST,),
    "ingest_batch_agent": (INGEST,),
    "document_forensics": (INGEST,),
}

_ASSETS, _BUILDINGS_ASSETS, _VENDORS_ASSETS = ("assets",), ("buildings", "assets"), ("vendors", "assets")

#: Tools whose register is not their module's. Most engines' lists hold a few tools of
#: another page: the energy list carries the building and asset-condition tools, the
#: work-order list carries asset lookups.
_NAME_TAGS: dict[str, tuple[str, ...]] = {
    # in the energy module
    "list_energy_buildings": (SHARED,),            # names for everyone; the service narrows
    "get_hoist_score": (SHARED,),                  # the service narrows the bars
    "list_building_documents": (SHARED,),          # the service narrows by document type
    "upsert_building_energy_profile": ("buildings", "energy"),
    "get_building_cost_drivers": _BUILDINGS_ASSETS,
    "deduce_asset_condition": _ASSETS,
    "deduce_condition_from_inspection_vectors": _ASSETS,
    "investigate_asset": _ASSETS,
    "get_asset_intelligence": _ASSETS,
    "get_asset_condition_summary": _ASSETS,
    "list_asset_conditions": _ASSETS,
    "get_asset_condition_rules": _ASSETS,
    "list_unscored_assets": _ASSETS,
    "ingest_meter_readings": (INGEST,),
    "ingest_degree_days": (INGEST,),
    "ingest_bms_trends": (INGEST,),
    # in the vendors module
    "propose_asset_criticality": _VENDORS_ASSETS,
    "approve_asset_criticality": _VENDORS_ASSETS,
    "propose_asset_criticality_from_udr": _VENDORS_ASSETS,
    "ingest_contract_parameters": (INGEST,),
    "extract_contract_from_document": (INGEST,),
    "extract_and_verify_invoice": (INGEST,),
    # in the compliance module
    # lists the compliance certificates on file to choose from: the register is Compliance's
    "manage_vector_membership": ("compliance",),
    # in the work-order module
    "search_assets": _ASSETS,
    "get_asset_details": _ASSETS,
    "search_locations": ("assets", "ops"),
    # the typed lookups among the table tools
    "find_asset": _ASSETS,
    "get_asset_documents": _ASSETS,
    "find_location": ("assets", "ops"),
    # adding a document to the index is ingesting it
    "index_document": (INGEST,),
    "index_documents_batch": (INGEST,),
    # page tools kept beside the conversation helpers
    "replacement_candidates": _ASSETS,
    "work_order_blockers": ("ops",),
}

#: One read across several registers that cannot be given in part: every page is needed.
_NEEDS_ALL: dict[str, tuple[str, ...]] = {
    "get_cost_savings": ("ops", "energy", "assets", "vendors"),
}


def _module_of(tool: Any) -> str:
    fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None) or tool
    return str(getattr(fn, "__module__", "") or "").rsplit(".", 1)[-1]


def tool_tags(tool: Any) -> tuple[str, ...] | None:
    """The tags of one tool, or None when nothing here says who owns it."""
    name = str(getattr(tool, "name", "") or "")
    if name in _NEEDS_ALL:
        return _NEEDS_ALL[name]
    if name in _NAME_TAGS:
        return _NAME_TAGS[name]
    return _MODULE_TAGS.get(_module_of(tool))


def tool_allowed(tool: Any) -> bool:
    """Whether the caller may be handed this tool. A tool nothing here has tagged is withheld
    from a caller held to pages: a forgotten tag must fail closed."""
    pages = current()
    if pages is None:
        return True
    name = str(getattr(tool, "name", "") or "")
    if name in _NEEDS_ALL:
        return all(p in pages for p in _NEEDS_ALL[name])
    tags = tool_tags(tool)
    if not tags or ANY in tags:
        return False
    if SHARED in tags or INGEST in tags:
        return True
    return any(t in pages for t in tags)


def filter_tools(tools: list[Any]) -> list[Any]:
    """The tools the caller may be handed. The same list object for a caller with every page."""
    if current() is None:
        return tools
    return [t for t in tools if tool_allowed(t)]


def refusal_payload() -> dict[str, Any]:
    return {"ok": False, "reason": "page_not_allowed", "error": OUTSIDE_PAGES}


def is_guarded(tool: Any) -> bool:
    fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    return bool(getattr(fn, "_page_guard", False))


def guard_tools(tools: list[Any]) -> list[Any]:
    """Make each tool refuse to run for a caller whose pages do not own it. In place, once.

    Filtering the lists keeps a tool away from the model. This is the second layer, on the
    tool itself: a plan step, a shortcut or a sub-agent that reaches a tool some other way is
    refused before it reads anything. It matters most for the tools that read the database
    directly, which have no service behind them to refuse the caller's token. The wrapper
    keeps the function's module, name and signature, so the tool's owner and its arguments
    are what they were.
    """
    for tool in tools:
        if is_guarded(tool):
            continue
        inner_async = getattr(tool, "coroutine", None)
        inner_sync = getattr(tool, "func", None)
        if inner_async is not None:
            tool.coroutine = _guarded_async(tool, inner_async)
        if inner_sync is not None:
            tool.func = _guarded_sync(tool, inner_sync)
    return tools


def _guarded_async(tool: Any, inner: Any) -> Any:
    @functools.wraps(inner)
    async def run(*args: Any, **kwargs: Any) -> Any:
        if not tool_allowed(tool):
            return refusal_payload()
        return await inner(*args, **kwargs)
    run._page_guard = True  # type: ignore[attr-defined]
    return run


def _guarded_sync(tool: Any, inner: Any) -> Any:
    @functools.wraps(inner)
    def run(*args: Any, **kwargs: Any) -> Any:
        if not tool_allowed(tool):
            return refusal_payload()
        return inner(*args, **kwargs)
    run._page_guard = True  # type: ignore[attr-defined]
    return run


#: What each page is called when the model is told which ones it may answer from.
_PAGE_LABELS: dict[str, str] = {
    "buildings": "Buildings", "compliance": "Compliance", "energy": "Energy",
    "vendors": "Vendors", "ops": "Maintenance and work orders", "assets": "Assets",
}


def prompt_note() -> str | None:
    """What the model is told about a caller held to some pages, or None for every page.

    The tools already enforce it. This is so the model declines in one sentence instead of
    trying a tool it does not have, or answering from what it remembers of the conversation.
    """
    pages = current()
    if pages is None:
        return None
    mine = [label for key, label in _PAGE_LABELS.items() if key in pages]
    rest = [label for key, label in _PAGE_LABELS.items() if key not in pages]
    held = ", ".join(mine) if mine else "none"
    note = ("ACCESS: This person may be given information from these pages only: " + held + ".")
    if rest:
        note += (" Everything else — " + ", ".join(rest) + " — is outside the pages they have "
                 "access to. If they ask about it, say so in one sentence. Do not answer it from "
                 "memory, from earlier in the conversation or from a document, and do not "
                 "suggest another way to get it.")
    return note


# ── what a certificate reaches ───────────────────────────────────────────────────
#: The kinds of record the compliance answer reads beside each certificate (ontology_qa
#: CERT_BUNDLE), by the page whose register each one is.
REACH_KIND_PAGE: dict[str, str] = {
    "linked_assets": "assets",
    "asset_open_work_orders": "ops",
    "asset_ppm_not_done": "ops",
    "asset_maintenance_plans": "ops",
    "open_energy_anomalies": "energy",
}


def reach_kinds() -> frozenset[str] | None:
    """The kinds of linked record the caller may be given beside a certificate, or None for every
    kind (a caller with every page: read exactly as before)."""
    pages = current()
    if pages is None:
        return None
    return frozenset(k for k, page in REACH_KIND_PAGE.items() if page in pages)


# ── documents ─────────────────────────────────────────────────────────────────────────
#: A document belongs to the page that owns its type. The same table the document register's
#: service uses (svc-operations-intelligence shared/page_filter.DOC_TYPE_PAGE).
_DOC_TYPE_PAGE: dict[str, str] = {
    "compliance_certificate": "compliance", "certificate": "compliance",
    "risk_assessment": "compliance",
    "contract": "vendors", "service_contract": "vendors",
    "invoice": "vendors", "vendor_invoice": "vendors",
    "inspection_report": "ops", "report": "ops",
    "warranty": "assets", "om_manual": "assets", "manual": "assets",
    "utility_bill": "energy", "energy_bill": "energy", "meter_readings": "energy",
}


def document_page(doc_type: str | None) -> str:
    return _DOC_TYPE_PAGE.get(str(doc_type or "").strip().lower(), "buildings")


def document_allowed(doc_type: str | None) -> bool:
    pages = current()
    return pages is None or document_page(doc_type) in pages
