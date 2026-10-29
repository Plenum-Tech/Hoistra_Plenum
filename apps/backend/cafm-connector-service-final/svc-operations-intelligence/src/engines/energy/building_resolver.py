"""Resolve what a document says into a building_id.

Extraction gives you a string — "Bishopsgate Tower", "BT-01", "12 Bishopsgate, EC2M 4NR" —
and the graph needs a uuid. This is the step between them, and it is where an ingestion
pipeline either becomes trustworthy or quietly starts filing certificates against the wrong
property.

The rule throughout: **an ambiguous answer is not an answer.** Two buildings matching a name
returns nothing with a stated reason, never the first row. A certificate filed against the
wrong building misstates two buildings' obligations at once — the one that wrongly holds it
and the one that wrongly lacks it — so silence is the cheaper error.

Tiers, in order, mirroring ``compliance/site_links.choose_site_match`` which already proved
this shape for sites:

0. ``asset``           — the record names an asset, and that asset is already placed.
                         Not a match at all: it is the link the graph already holds, so
                         a work order lands with the plant it services by construction.
1. ``building_code``   — an exact code is definitive
2. ``name``            — exact match on the normalised name
3. ``name_contains``   — containment, length-guarded, only for names long enough to mean it
4. ``site_sole``       — the document names a site, and that site holds exactly one building
5. unresolved          — reported with the reason and the candidates it saw

Nothing here writes. ``resolve_batch`` reports; linking is a separate, explicit step.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...shared import name_lookup, name_match
from ..compliance.site_links import normalize_name, normalize_ref

log = get_logger(__name__)

#: Below this length a containment match ("Mill" inside "Mill House") says nothing.
MIN_CONTAINMENT_LEN = 5
#: A containment match whose lengths differ by more than this is a different building
#: ("Tower A" vs "Tower A Annexe Car Park And Grounds").
MAX_CONTAINMENT_DELTA = 25
MIN_CODE_LEN = 2


def building_keys(row: dict[str, Any]) -> dict[str, Any]:
    """A building row reduced to what matching needs: its key, and its comparable forms."""
    names = [row.get("name"), row.get("building_name")]
    codes = [row.get("building_code"), row.get("code")]
    return {
        "building_id": str(row.get("building_id") or ""),
        "label": next((str(n) for n in names if n), None) or str(row.get("building_id") or ""),
        "site_id": str(row.get("site_id") or "") or None,
        "name_keys": {normalize_name(n) for n in names if normalize_name(n)},
        "code_keys": {normalize_ref(c) for c in codes if normalize_ref(c)},
        "site_name_key": normalize_name(row.get("site_name")),
        # As stored, for the name ladder (29 Oct 2026): "RT5, Crossrail", "B-090765".
        "texts": [str(t).strip() for t in names + codes if str(t or "").strip()],
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        k = r.get("building_id") or ""
        if k and k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def phrase_within(short: str, long: str) -> bool:
    """``short`` inside ``long`` (both normalised) from the start of a word, and to the end of a
    word when it ends in a code, a number or a one-/two-letter word (29 Oct 2026). A bare
    substring test filed a certificate for "Building 7" under "Building 70", and "7 Kingsway
    Court" under "17 Kingsway Court", whenever the right building was not on the register."""
    ws = short.split()
    if not ws:
        return False
    end = r"(?![a-z0-9])" if name_match.exact_token(ws[-1]) else ""
    return re.search(r"(?<![a-z0-9])" + re.escape(short) + end, long) is not None


def _contains(n: str, row: dict[str, Any]) -> bool:
    for nk in row.get("name_keys") or set():
        if len(nk) < MIN_CONTAINMENT_LEN:
            continue
        if abs(len(nk) - len(n)) > MAX_CONTAINMENT_DELTA:
            continue
        if phrase_within(n, nk) or phrase_within(nk, n):
            return True
    return False


#: The reason each rung of the name ladder resolves with ("name" is the exact name).
_RUNG_REASON = {
    name_lookup.EXACT: "name",
    name_lookup.COMPACT: "name_compact",
    name_lookup.PHRASE: "name_phrase",
    name_lookup.WORDS: "name_words",
    name_lookup.CLOSE: "name_close_spelling",
}


def _row_texts(r: dict[str, Any]) -> list[str]:
    return (list(r.get("texts") or [])
            or sorted(r.get("name_keys") or ()) + sorted(r.get("code_keys") or ()))


def _ladder(rows: list[dict[str, Any]], name: Any, *, max_rung: int,
            min_core_len: int = 3) -> tuple[int | None, list[dict[str, Any]]]:
    """The buildings ``name`` names at the best rung up to ``max_rung`` (several when shared)."""
    by_id = {r.get("building_id") or "": r for r in rows}
    pairs = [(r.get("building_id") or "", t) for r in rows for t in _row_texts(r)]
    top, keys = name_lookup.best(name, pairs, name_lookup.BUILDING_KIND_WORDS,
                                 max_rung=max_rung, min_core_len=min_core_len)
    return top, [by_id[k] for k in keys if k in by_id]


def choose_building(
    rows: list[dict[str, Any]],
    *,
    name: Any = None,
    code: Any = None,
    site_name: Any = None,
    site_id: Any = None,
    loose: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """The one building this document belongs to, or why it cannot be chosen.

    Returns ``(row, reason)``. ``row`` is None whenever the answer is not unambiguous, and
    the reason is what the report shows so an unlinked document is a stated outcome rather
    than a silence.

    The name is tried as written, then in its other exact forms (29 Oct 2026): with the kind
    word out ("JASMEET BUILDING" is "JASMEET"), as letters and digits ("B090765" is the code
    "B-090765"). Those are the only forms a caller that LINKS what it resolves may use
    (resolve-batch, graph_ingest): the same name, never part of one. ``loose`` is for a read
    that answers a question (GET /buildings/resolve): it also takes a phrase from the start of a
    word, every word in any order ("tower hussain") and, after containment, a close spelling
    ("Jasmet") — each named in the reason, and ambiguous whenever two buildings answer.
    """
    if not rows:
        return None, "no_buildings"

    ref = normalize_ref(code)
    if len(ref) >= MIN_CODE_LEN:
        hits = _dedupe([r for r in rows if ref in (r.get("code_keys") or set())])
        if len(hits) == 1:
            return hits[0], "building_code"
        if len(hits) > 1:
            return None, "ambiguous_building_code"

    n = normalize_name(name)
    if n:
        hits = _dedupe([r for r in rows if n in (r.get("name_keys") or set())])
        if len(hits) == 1:
            return hits[0], "name"
        if len(hits) > 1:
            return None, "ambiguous_name"

        # The other exact forms. On a link path the core must be as long as containment asks
        # (MIN_CONTAINMENT_LEN): such a core is inside the name as written, so it links nothing
        # the containment tier below would not — it only prefers the exact one of its hits.
        top, hits = _ladder(rows, name, max_rung=name_lookup.COMPACT,
                            min_core_len=3 if loose else MIN_CONTAINMENT_LEN)
        hits = _dedupe(hits)
        if len(hits) == 1:
            return hits[0], _RUNG_REASON[top]
        if len(hits) > 1:
            return None, "ambiguous_" + _RUNG_REASON[top]

        if loose:
            top, hits = _ladder(rows, name, max_rung=name_lookup.WORDS)
            hits = _dedupe(hits)
            if len(hits) == 1:
                return hits[0], _RUNG_REASON[top]
            if len(hits) > 1:
                return None, "ambiguous_" + _RUNG_REASON[top]

        # The kind of record alone ("the building") names no building, even inside a longer one.
        if len(n) >= MIN_CONTAINMENT_LEN and name_lookup.names_something(
                name, name_lookup.BUILDING_KIND_WORDS):
            hits = _dedupe([r for r in rows if _contains(n, r)])
            if len(hits) == 1:
                return hits[0], "name_contains"
            if len(hits) > 1:
                return None, "ambiguous_name_contains"

        if loose:
            top, hits = _ladder(rows, name, max_rung=name_lookup.CLOSE)
            hits = _dedupe(hits)
            if len(hits) == 1:
                return hits[0], _RUNG_REASON[top]
            if len(hits) > 1:
                return None, "ambiguous_" + _RUNG_REASON[top]

    # The document names a site rather than a building. That resolves only when the site
    # holds exactly one building — on a campus it names the estate, not the structure.
    sid = str(site_id or "").strip()
    if sid:
        hits = _dedupe([r for r in rows if r.get("site_id") == sid])
        if len(hits) == 1:
            return hits[0], "site_sole_building"
        if len(hits) > 1:
            return None, "site_has_several_buildings"

    sn = normalize_name(site_name)
    if sn:
        hits = _dedupe([r for r in rows if r.get("site_name_key") == sn])
        if len(hits) == 1:
            return hits[0], "site_sole_building"
        if len(hits) > 1:
            return None, "site_has_several_buildings"

    return None, "no_match"


#: Reasons that mean "a human must decide", as opposed to "nothing looked like it".
AMBIGUOUS_REASONS = frozenset(
    {"ambiguous_building_code", "ambiguous_name", "ambiguous_name_contains",
     "site_has_several_buildings"}
    | {"ambiguous_" + r for r in _RUNG_REASON.values()}
)


def name_candidates(rows: list[dict[str, Any]], name: Any, *,
                    loose: bool = False) -> list[dict[str, Any]]:
    """The buildings a name could mean, for a person to choose from when it did not resolve:
    those at the best rung of the ladder, else those it is contained in or contains."""
    n = normalize_name(name)
    if not n:
        return []
    exact = _dedupe([r for r in rows if n in (r.get("name_keys") or set())])
    if exact:
        return exact
    top, hits = _ladder(rows, name, max_rung=name_lookup.WORDS if loose else name_lookup.COMPACT)
    if hits and top is not None and top <= name_lookup.COMPACT:
        return _dedupe(hits)
    contains = []
    if len(n) >= MIN_CONTAINMENT_LEN and name_lookup.names_something(
            name, name_lookup.BUILDING_KIND_WORDS):
        contains = _dedupe([r for r in rows if _contains(n, r)])
    if hits or contains:
        return _dedupe(hits + contains)
    if loose:
        return _dedupe(_ladder(rows, name, max_rung=name_lookup.CLOSE)[1])
    return []


def resolution_outcome(reason: str) -> str:
    """resolved | review | unmatched — what the pipeline should do with this reason."""
    if reason in AMBIGUOUS_REASONS:
        return "review"
    if reason in {"no_buildings", "no_match", "asset_not_placed"}:
        return "unmatched"
    return "resolved"


# ── DB ────────────────────────────────────────────────────────────────────────────────


async def load_building_index(session: AsyncSession, *, limit: int = 5000, scope: Any = None) -> list[dict[str, Any]]:
    """Every building the caller may see, reduced to its matchable forms, with its site's name
    joined in. ``scope`` (an access.Scope) bounds it to the caller's company and, for a user
    allocated to buildings, those buildings (29 Oct 2026: it read every company's). None keeps the
    unbounded read for server-side callers that bring no caller (graph ingest)."""
    from . import building_rollup

    if scope is not None and not getattr(scope, "is_superadmin", False) and scope.organization_id is None:
        return []            # no company to act for: nothing, never everyone's
    shape = await building_rollup.graph_shape(session)
    if not shape["buildings"]["exists"]:
        return []
    key = shape["buildings"]["key"]
    have = shape["buildings"]["columns"]
    where: list[str] = []
    params: dict[str, Any] = {}
    if scope is not None:
        if scope.organization_id is not None:
            if "organization_id" in have:
                where.append("b.organization_id::text = :org")
                params["org"] = str(scope.organization_id)
            elif not getattr(scope, "is_superadmin", False):
                log.warning("building_resolver.no_company_column")
                return []
        if scope.building_ids is not None:
            where.append(f"b.{key}::text = ANY(:bids)")
            params["bids"] = [str(b) for b in scope.building_ids]
    cols = [f"b.{key}::text AS building_id"]
    for c in ("name", "building_name", "building_code", "site_id"):
        if c in have:
            cols.append(f"b.{c}::text AS {c}")
    joins = ""
    if shape["sites"]["exists"] and "site_id" in have:
        s_cols = shape["sites"]["columns"]
        skey = shape["sites"]["key"]
        # Both spellings exist on the same table in this deployment and only one is
        # populated, so coalesce rather than prefer — picking `name` alone read NULL and a
        # site with one building failed to resolve.
        name_cols = [f"s.{c}" for c in ("name", "site_name") if c in s_cols]
        sname = f"COALESCE({', '.join(name_cols)})" if name_cols else "NULL"
        cols.append(f"{sname}::text AS site_name")
        joins = f" LEFT JOIN plenum_cafm.sites s ON s.{skey}::text = b.site_id::text"
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT {', '.join(cols)} FROM plenum_cafm.buildings b{joins}{where_sql} LIMIT {int(limit)}"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_resolver.index_failed", error=str(exc)[:200])
        return []
    return [building_keys(dict(r)) for r in rows]


#: Column spellings that might hold an asset's human reference.
_ASSET_CODE_COLS = ("asset_code", "code", "asset_tag", "tag", "reference")


async def load_asset_building_map(
    session: AsyncSession, codes: list[Any]
) -> dict[str, dict[str, str | None]]:
    """``{normalised asset code: {asset_id, building_id}}`` for the codes asked about.

    The asset's own key travels with its building because the caller needs both: a work
    order that records which asset it is against is a work order the asset's history can be
    read back from, and a building link alone would place the cost without saying what
    incurred it.

    A work order names the asset it is against, and that asset is already placed. Reading
    the building off the asset is not another guess — it is the link the graph already
    holds, so a work order lands in the same building as the plant it services by
    construction rather than by matching the same name twice and hoping both land alike.

    It is transitive, and the record says so: the reason is ``asset``, not ``name``, so a
    work order placed this way is never read as having named its own building.
    """
    wanted = {normalize_ref(c) for c in (codes or []) if normalize_ref(c)}
    if not wanted:
        return {}

    from .building_rollup import graph_shape

    shape = await graph_shape(session)
    assets = shape["assets"]
    if not assets["exists"]:
        return {}
    code_col = next((c for c in _ASSET_CODE_COLS if c in assets["columns"]), None)
    if not code_col:
        return {}
    key_col = assets["key"]
    # building_id arrives with the migration; an asset table without it can still hand
    # back the asset's own key, which is what a work order records.
    bid_expr = "building_id::text" if "building_id" in assets["columns"] else "NULL"

    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        f"""SELECT {code_col}::text AS code,
                                   {key_col}::text  AS aid,
                                   {bid_expr}       AS bid
                            FROM plenum_cafm.assets
                            WHERE {code_col} IS NOT NULL"""
                    )
                )
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001 — a missing link must never break a caller
        log.warning("building_resolver.asset_map_failed", error=str(exc)[:200])
        return {}

    out: dict[str, dict[str, str | None]] = {}
    clashes: set[str] = set()
    for r in rows:
        key = normalize_ref(r["code"])
        if not key or key not in wanted:
            continue
        entry = {"asset_id": str(r["aid"]) if r["aid"] else None,
                 "building_id": str(r["bid"]) if r["bid"] else None}
        prior = out.get(key)
        if prior and prior != entry:
            # The same asset code recorded twice and differently. Which is right is not
            # knowable from here, so neither is used — picking one would file the work
            # order against plant that may not be the plant.
            clashes.add(key)
            continue
        out[key] = entry
    for key in clashes:
        out.pop(key, None)
    return out


async def resolve_one(
    session: AsyncSession,
    *,
    name: Any = None,
    code: Any = None,
    site_name: Any = None,
    site_id: Any = None,
    asset_code: Any = None,
    scope: Any = None,
) -> dict[str, Any]:
    """Resolve one record's building, among the buildings ``scope`` may see. Reads only."""
    index = await load_building_index(session, scope=scope)
    asset_map = await load_asset_building_map(session, [asset_code]) if asset_code else {}
    hit = asset_map.get(normalize_ref(asset_code)) or {}
    bid = hit.get("building_id")
    if bid:
        return {
            "ok": True,
            "input": {"name": name, "code": code, "site_name": site_name,
                      "site_id": site_id, "asset_code": asset_code},
            "outcome": "resolved",
            "reason": "asset",
            "asset_id": hit.get("asset_id"),
            "building_id": bid,
            "building": next(
                (r["label"] for r in index if r["building_id"] == bid), None
            ),
            "buildings_considered": len(index),
        }
    # A read that answers a question: the whole name ladder, close spelling included (29 Oct
    # 2026). resolve_batch, which feeds links, stays on the exact forms.
    row, reason = choose_building(
        index, name=name, code=code, site_name=site_name, site_id=site_id, loose=True
    )
    outcome = resolution_outcome(reason)
    out: dict[str, Any] = {
        "ok": True,
        "input": {"name": name, "code": code, "site_name": site_name,
                  "site_id": site_id, "asset_code": asset_code},
        "outcome": outcome,
        "reason": reason,
        "building_id": row["building_id"] if row else None,
        "building": row["label"] if row else None,
        "buildings_considered": len(index),
    }
    if reason == "name_close_spelling" and row:
        out["note"] = (f"{str(name).strip()!r} is not on the register as written; the closest "
                       f"name is {row['label']!r}.")
    if outcome != "resolved":
        # What it saw, so a person can decide rather than guess at why it declined.
        named = name_candidates(index, name, loose=True)
        named_ids = {r["building_id"] for r in named}
        out["candidates"] = [
            {"building_id": r["building_id"], "name": r["label"], "site_id": r.get("site_id")}
            for r in named + [r for r in index
                              if site_id and r.get("site_id") == str(site_id)
                              and r["building_id"] not in named_ids]
        ][:10]
    return out


async def resolve_batch(
    session: AsyncSession, items: list[dict[str, Any]], *, scope: Any = None
) -> dict[str, Any]:
    """Resolve many at once against a single index read — the shape an ingest run needs.

    Two reads for the whole batch: the building index, and the asset map for every asset
    code mentioned. A work order file of 5,000 rows costs the same two queries as one row.
    """
    index = await load_building_index(session, scope=scope)
    labels = {r["building_id"]: r["label"] for r in index}
    asset_map = await load_asset_building_map(
        session, [i.get("asset_code") for i in items]
    )

    results: list[dict[str, Any]] = []
    tally: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for item in items:
        # The asset tier first. A work order is against a piece of plant, and that plant
        # is already placed — reading the building off it is the link the graph holds,
        # not a second string match that might land somewhere else.
        hit = asset_map.get(normalize_ref(item.get("asset_code"))) or {}
        asset_id = hit.get("asset_id")
        bid = hit.get("building_id")
        if bid:
            row, reason, outcome = None, "asset", "resolved"
            building_id, building = bid, labels.get(bid)
        else:
            row, reason = choose_building(
                index,
                name=item.get("name"),
                code=item.get("code"),
                site_name=item.get("site_name"),
                site_id=item.get("site_id"),
            )
            # The row named an asset and nothing placed it. "Nothing matched" is true but
            # useless; "that asset has no building either" names the actual problem and
            # its order of operations — assets before work orders.
            if reason == "no_match" and normalize_ref(item.get("asset_code")):
                # The asset is known but unplaced, or not known at all. Either way the
                # work order still records which asset it is against.
                reason = "asset_not_placed"
            outcome = resolution_outcome(reason)
            building_id = row["building_id"] if row else None
            building = row["label"] if row else None
        tally[outcome] = tally.get(outcome, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        results.append({
            "input": item,
            "outcome": outcome,
            "reason": reason,
            # Recorded even when the building could not be: a work order that knows its
            # asset is one the asset's history can be read back from, and the building
            # follows later once the asset is placed.
            "asset_id": asset_id,
            "building_id": building_id,
            "building": building,
        })
    return {
        "ok": True,
        "buildings_considered": len(index),
        "assets_known": len(asset_map),
        "assets_placed": sum(1 for v in asset_map.values() if v.get("building_id")),
        "count": len(items),
        "by_outcome": tally,
        "by_reason": reasons,
        "results": results,
    }
