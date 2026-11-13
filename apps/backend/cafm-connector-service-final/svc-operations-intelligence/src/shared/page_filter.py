"""Narrowing a response that mixes pages, row by row.

A caller with an access role may open only the pages an administrator ticked
(engines/auth/pages.py). Most routes are owned by one page and are refused outright by the
gate (engines/auth/page_map.py). A few answer with rows that belong to several pages at once —
the Pending queue, the value card, the cron list, a building's documents, the building graph,
the Hoist Score. Those are tagged ``shared`` and narrowed here: the caller keeps what their
pages own and loses the rest, instead of losing the whole list.

Three rules, the same as everywhere else:

  * ``pages is None`` is a caller with every page. Every function here hands such a caller
    back the object it was given, untouched, so their responses cannot change.
  * A row whose owner is unknown is hidden from a restricted caller. An approval source, a
    job module or a graph branch added later must be given an owner before a role user sees
    it; the tests in tests/unit/test_page_filter.py fail until it is.
  * Totals are recomputed from what is kept. A figure summed over rows the caller cannot see
    is a figure about somebody else's work.

Pure, and kept in ``shared`` so both the route layer and the engines can import it without
pulling in the auth package. ``PAGES`` repeats engines/auth/pages.PAGES; a test holds them equal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

PAGES: tuple[str, ...] = ("buildings", "compliance", "energy", "vendors", "ops", "assets")

Pages = frozenset[str] | None


#: What a caller is told when they ask for data owned by a page they do not hold. One
#: sentence, the same everywhere, naming nothing internal.
PAGE_NOT_ALLOWED = "That is not part of the pages you have access to."


def refusal_detail(page: str | None) -> dict[str, Any]:
    """The body of the 403 every service answers with for an unheld page."""
    return {"ok": False, "reason": "page_not_allowed", "page": page, "error": PAGE_NOT_ALLOWED}


def holds(pages: Pages, *any_of: str) -> bool:
    """Whether the caller holds at least one of these pages."""
    return pages is None or any(p in pages for p in any_of)


def _get(row: Any, key: str) -> Any:
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


# ── the Pending queue ─────────────────────────────────────────────────────────────────
#: approvals_queue_items.source_feature: A compliance, B contract performance, C energy.
#: P is the platform's own notices (usage, credits), which belong to no page.
FEATURE_PAGES: dict[str, tuple[str, ...]] = {
    "A": ("compliance",), "B": ("vendors",), "C": ("energy",),
}
#: Where one source spans two registers, or the platform raised the item about a record.
_ENTITY_PAGES: dict[tuple[str, str], tuple[str, ...]] = {
    ("B", "asset_criticality"): ("vendors", "assets"),
    ("P", "site"): ("buildings",),
}


def approval_pages(source_feature: str | None, related_entity_type: str | None) -> tuple[str, ...]:
    """The pages that own one queue item; holding any of them shows it. Empty = no page owns
    it, so a restricted caller never sees it."""
    feature = str(source_feature or "").strip().upper()
    entity = str(related_entity_type or "").strip().lower()
    return _ENTITY_PAGES.get((feature, entity)) or FEATURE_PAGES.get(feature, ())


def approval_allowed(pages: Pages, item: Any) -> bool:
    if pages is None:
        return True
    owners = approval_pages(_get(item, "source_feature"), _get(item, "related_entity_type"))
    return any(p in pages for p in owners)


def approvals(items: list[Any], pages: Pages) -> list[Any]:
    if pages is None:
        return items
    return [i for i in items if approval_allowed(pages, i)]


# ── the value card ────────────────────────────────────────────────────────────────────
VALUE_MODULE_PAGE: dict[str, str] = {
    "energy": "energy", "vendors": "vendors", "maintenance": "ops",
    "compliance": "compliance", "assets": "assets",
}
PNL_HEAD_PAGE: dict[str, str] = {
    "maintenance": "ops", "unplanned": "ops", "energy": "energy", "compliance": "compliance",
}


def _total(modules: list[dict[str, Any]], field: str) -> float | None:
    counted = [m for m in modules if m.get("counted")]
    return round(sum(m.get(field) or 0 for m in counted), 2) if counted else None


def value_summary(payload: dict[str, Any], pages: Pages) -> dict[str, Any]:
    """Keep the held modules and heads; recompute the ledger totals from what is kept."""
    if pages is None or not isinstance(payload, dict) or not isinstance(payload.get("ledger"), dict):
        return payload
    ledger = dict(payload["ledger"])
    modules = [m for m in ledger.get("modules") or []
               if VALUE_MODULE_PAGE.get(str(m.get("key"))) in pages]
    ledger.update(
        modules=modules,
        counted_modules=sum(1 for m in modules if m.get("counted")),
        total_detected=_total(modules, "detected"),
        total_saved=_total(modules, "saved"),
    )
    out = dict(payload, ledger=ledger)
    if isinstance(payload.get("pnl"), dict):
        out["pnl"] = dict(payload["pnl"], heads=[
            h for h in payload["pnl"].get("heads") or []
            if PNL_HEAD_PAGE.get(str(h.get("key"))) in pages])
    return out


# ── scheduled jobs ────────────────────────────────────────────────────────────────────
#: engines/crons/jobs.CATALOGUE "module" -> page. The free-form question job (module
#: "Orchestrator") is deliberately absent: it is asked and answered with an administrator's
#: reach, so neither it nor its answers are shown to a caller held to some pages.
CRON_MODULE_PAGE: dict[str, str] = {
    "Energy": "energy", "Assets": "assets", "Compliance": "compliance",
    "Vendors": "vendors", "Maintenance": "ops",
}


def cron_allowed(pages: Pages, module: str | None) -> bool:
    return pages is None or CRON_MODULE_PAGE.get(str(module or "")) in pages


# ── documents ─────────────────────────────────────────────────────────────────────────
#: A document belongs to the page that owns its type (engines/document_register.DOC_TYPES,
#: with the names the ingest writers use for the same types). Anything else — a drawing, a
#: staff list, an unclassified file — lives in a building's documents tree.
DOC_TYPE_PAGE: dict[str, str] = {
    "compliance_certificate": "compliance", "certificate": "compliance",
    "risk_assessment": "compliance",
    "contract": "vendors", "service_contract": "vendors",
    "invoice": "vendors", "vendor_invoice": "vendors",
    "inspection_report": "ops", "report": "ops",
    "warranty": "assets", "om_manual": "assets", "manual": "assets",
    "utility_bill": "energy", "energy_bill": "energy", "meter_readings": "energy",
}


def document_page(doc_type: str | None) -> str:
    return DOC_TYPE_PAGE.get(str(doc_type or "").strip().lower(), "buildings")


def document_allowed(pages: Pages, doc_type: str | None) -> bool:
    return pages is None or document_page(doc_type) in pages


def documents(rows: list[dict[str, Any]], pages: Pages) -> list[dict[str, Any]]:
    """Rows carry the type as ``type`` (the register list) or ``doc_type`` (the search)."""
    if pages is None:
        return rows
    return [r for r in rows
            if document_allowed(pages, r.get("type") if "type" in r else r.get("doc_type"))]


# ── buildings ─────────────────────────────────────────────────────────────────────────
#: What a building is called. Every page labels its rows with these, so every caller gets them.
_BUILDING_LABELS = ("building_id", "site_id", "id", "name", "building_name", "code",
                    "building_code", "country", "country_code")

#: The building graph's relations (engines/energy/building_tree.BRANCH_META) and the count
#: keys the register row carries for them (engines/energy/buildings.HOIST_SCORE_DOMAINS).
BRANCH_PAGE: dict[str, str] = {
    "floors": "buildings", "spaces": "buildings", "documents": "buildings",
    "assets": "assets", "equipment": "assets",
    "meters": "energy", "energy_meters": "energy",
    "compliance_certificates": "compliance", "certificates": "compliance",
    "work_orders": "ops",
    "contracts": "vendors", "contract_parameters": "vendors", "invoices": "vendors",
}
HOIST_DOMAIN_PAGE: dict[str, str] = {
    "assets": "assets", "compliance": "compliance", "contracts": "vendors",
    "energy": "energy", "maintenance": "ops",
}


def _held_domains(values: Any, pages: frozenset[str]) -> Any:
    if not isinstance(values, list):
        return values
    return [d for d in values if HOIST_DOMAIN_PAGE.get(str(d)) in pages]


def building_rows(rows: list[dict[str, Any]], pages: Pages) -> list[dict[str, Any]]:
    """The buildings register for this caller.

    Without the Buildings page a caller still gets the names — they label every other page's
    rows and fill every picker — and nothing else. With it they get the register row, less
    the counts and score parts that belong to pages they do not hold.
    """
    if pages is None:
        return rows
    if "buildings" not in pages:
        return [{k: r[k] for k in _BUILDING_LABELS if k in r} for r in rows]
    out = []
    for r in rows:
        row = dict(r)
        if isinstance(row.get("graph_counts"), dict):
            row["graph_counts"] = {k: v for k, v in row["graph_counts"].items()
                                   if BRANCH_PAGE.get(str(k)) in pages}
        for key in ("hoist_score_covered", "hoist_score_missing"):
            if key in row:
                row[key] = _held_domains(row[key], pages)
        out.append(row)
    return out


def building_tree(tree: dict[str, Any], pages: Pages) -> dict[str, Any]:
    """The building graph with only the branches the caller's pages own.

    A held branch that hangs off an unheld one is kept — meters hang off assets in the graph —
    under an emptied parent marked ``withheld``, so the drawer still has somewhere to put it.
    """
    if pages is None or not isinstance(tree, dict) or not isinstance(tree.get("branches"), list):
        return tree

    def keep(node: dict[str, Any]) -> bool:
        return BRANCH_PAGE.get(str(node.get("table"))) in pages

    def held_rows(node: dict[str, Any]) -> dict[str, Any]:
        # The documents branch lists every file on the building; each belongs to the page
        # that owns its type (the row's detail). The count is of what is listed.
        if str(node.get("table")) != "documents" or not isinstance(node.get("rows"), list):
            return node
        rows = [r for r in node["rows"] if document_allowed(pages, (r or {}).get("detail"))]
        return node if len(rows) == len(node["rows"]) else dict(node, rows=rows, count=len(rows), truncated=False)

    branches = []
    for node in tree["branches"]:
        children = [held_rows(c) for c in node.get("children") or [] if keep(c)]
        if keep(node):
            branches.append(dict(held_rows(node), children=children))
        elif children:
            branches.append(dict(node, count=0, rows=[], truncated=False, withheld=True,
                                 children=children))
    flat = [b for br in branches for b in [br] + br["children"]]
    return dict(
        tree, branches=branches,
        total_records=sum(int(b.get("count") or 0) for b in flat),
        unavailable=[b.get("table") for b in flat
                     if not b.get("available") and not b.get("withheld")],
    )


def hoist_score(out: dict[str, Any], pages: Pages) -> dict[str, Any]:
    """The Hoist Score for this caller: only the bars their pages own.

    The headline score is the mean over all five kinds of record. It is shown only to a
    caller who holds every page it draws on; a mean over the part someone can see would be a
    different number wearing the same name.
    """
    if pages is None or not isinstance(out, dict) or not isinstance(out.get("domains"), list):
        return out
    if all(p in pages for p in HOIST_DOMAIN_PAGE.values()):
        return out
    narrowed = dict(out, score=None, domains=[
        d for d in out["domains"] if HOIST_DOMAIN_PAGE.get(str(d.get("key"))) in pages])
    if isinstance(out.get("rows"), list):
        narrowed["rows"] = [
            dict(r, score=None, covered=_held_domains(r.get("covered"), pages),
                 missing=_held_domains(r.get("missing"), pages))
            for r in out["rows"]]
    return narrowed


#: What a vendor coverage row keeps for a caller who holds Vendors but not Compliance: who
#: the vendor is and whether they are blocked. How many certificates, which are missing and
#: the coverage figure are the compliance register's.
_BLOCK_KEYS = ("vendor_id", "vendor_name", "block_state", "cleared")


def vendor_block_states(payload: dict[str, Any], pages: Pages) -> dict[str, Any]:
    if pages is None or "compliance" in pages or not isinstance(payload, dict) \
            or not isinstance(payload.get("vendors"), list):
        return payload
    return dict(payload, vendors=[{k: v[k] for k in _BLOCK_KEYS if k in v} for v in payload["vendors"]])


def without_anomalies(payload: Any) -> Any:
    """The same payload with every anomaly figure blanked - the keys that begin ``anomal``.

    An asset's condition is the asset register's, but the anomaly counts, weeks and cost
    beside it are the energy register's findings about the asset. A copy: the original is
    left as it was."""
    if isinstance(payload, dict):
        return {k: (None if str(k).lower().startswith("anomal") else without_anomalies(v))
                for k, v in payload.items()}
    if isinstance(payload, list):
        return [without_anomalies(v) for v in payload]
    return payload


#: What a document is checked against on a building (engines/ingestion/ontology.py), by the
#: page each list belongs to. The building's own facts - names, codes, place, floors - and
#: the count of its documents have no entry: they stay.
ONTOLOGY_PAGE: dict[str, str] = {
    "vendors": "vendors", "contract_refs": "vendors", "contracts": "vendors",
    "assets": "assets", "equipment": "assets",
    "meters": "energy",
    "certificate_types": "compliance", "certificate_numbers": "compliance", "certificates": "compliance",
}


def ontology(profile: dict[str, Any], pages: Pages) -> dict[str, Any]:
    """A building's checking profile with only the lists the caller's pages own. The names of
    the files on the building span every page, so they go only to a holder of all of them."""
    if pages is None or not isinstance(profile, dict):
        return profile
    out = {k: ([] if ONTOLOGY_PAGE.get(k) and ONTOLOGY_PAGE[k] not in pages and isinstance(v, list) else v)
           for k, v in profile.items()}
    if isinstance(profile.get("counts"), dict):
        out["counts"] = {k: v for k, v in profile["counts"].items()
                         if ONTOLOGY_PAGE.get(k) is None or ONTOLOGY_PAGE[k] in pages}
    if "document_names" in out and not all(p in pages for p in set(DOC_TYPE_PAGE.values())):
        out["document_names"] = []
    return out


# ── history from before access was narrowed ───────────────────────────────────────────

def since_narrowing(rows: list[Any], pages: Pages, narrowed_at: datetime | None, *,
                    when: Callable[[Any], datetime | None]) -> list[Any]:
    """Rows made after this caller's pages last got narrower.

    An answer stored before a page was taken away may hold that page's data. Nothing is
    deleted: a caller who is given every page again sees it all again. A row with no time on
    it cannot be shown to be recent, so it is hidden.
    """
    if pages is None or narrowed_at is None:
        return rows
    cut = narrowed_at if narrowed_at.tzinfo else narrowed_at.replace(tzinfo=timezone.utc)

    def recent(row: Any) -> bool:
        at = when(row)
        if at is None:
            return False
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at > cut

    return [r for r in rows if recent(r)]
