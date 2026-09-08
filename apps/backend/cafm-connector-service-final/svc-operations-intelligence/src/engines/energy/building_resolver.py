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

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
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


def _contains(n: str, row: dict[str, Any]) -> bool:
    for nk in row.get("name_keys") or set():
        if len(nk) < MIN_CONTAINMENT_LEN:
            continue
        if abs(len(nk) - len(n)) > MAX_CONTAINMENT_DELTA:
            continue
        if n in nk or nk in n:
            return True
    return False


def choose_building(
    rows: list[dict[str, Any]],
    *,
    name: Any = None,
    code: Any = None,
    site_name: Any = None,
    site_id: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """The one building this document belongs to, or why it cannot be chosen.

    Returns ``(row, reason)``. ``row`` is None whenever the answer is not unambiguous, and
    the reason is what the report shows so an unlinked document is a stated outcome rather
    than a silence.
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

        if len(n) >= MIN_CONTAINMENT_LEN:
            hits = _dedupe([r for r in rows if _contains(n, r)])
            if len(hits) == 1:
                return hits[0], "name_contains"
            if len(hits) > 1:
                return None, "ambiguous_name_contains"

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
)


def resolution_outcome(reason: str) -> str:
    """resolved | review | unmatched — what the pipeline should do with this reason."""
    if reason in AMBIGUOUS_REASONS:
        return "review"
    if reason in {"no_buildings", "no_match", "asset_not_placed"}:
        return "unmatched"
    return "resolved"


# ── DB ────────────────────────────────────────────────────────────────────────────────


async def load_building_index(session: AsyncSession, *, limit: int = 5000) -> list[dict[str, Any]]:
    """Every building reduced to its matchable forms, with its site's name joined in."""
    from .building_rollup import graph_shape

    shape = await graph_shape(session)
    if not shape["buildings"]["exists"]:
        return []
    key = shape["buildings"]["key"]
    have = shape["buildings"]["columns"]
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
    sql = f"SELECT {', '.join(cols)} FROM plenum_cafm.buildings b{joins} LIMIT {int(limit)}"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_resolver.index_failed", error=str(exc)[:200])
        return []
    return [building_keys(dict(r)) for r in rows]


#: Column spellings that might hold an asset's human reference.
_ASSET_CODE_COLS = ("asset_code", "code", "asset_tag", "tag", "reference")


async def load_asset_building_map(
    session: AsyncSession, codes: list[Any]
) -> dict[str, str]:
    """``{normalised asset code: building_id}`` for the codes asked about.

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
    if not assets["exists"] or "building_id" not in assets["columns"]:
        return {}
    code_col = next((c for c in _ASSET_CODE_COLS if c in assets["columns"]), None)
    if not code_col:
        return {}

    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        f"""SELECT {code_col}::text AS code, building_id::text AS bid
                            FROM plenum_cafm.assets
                            WHERE building_id IS NOT NULL AND {code_col} IS NOT NULL"""
                    )
                )
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001 — a missing link must never break a caller
        log.warning("building_resolver.asset_map_failed", error=str(exc)[:200])
        return {}

    out: dict[str, str] = {}
    clashes: set[str] = set()
    for r in rows:
        key = normalize_ref(r["code"])
        if not key or key not in wanted:
            continue
        prior = out.get(key)
        if prior and prior != r["bid"]:
            # The same asset code recorded against two buildings. Which is right is not
            # knowable from here, so neither is used.
            clashes.add(key)
            continue
        out[key] = str(r["bid"])
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
) -> dict[str, Any]:
    """Resolve one record's building. Reads only."""
    index = await load_building_index(session)
    asset_map = await load_asset_building_map(session, [asset_code]) if asset_code else {}
    bid = asset_map.get(normalize_ref(asset_code))
    if bid:
        return {
            "ok": True,
            "input": {"name": name, "code": code, "site_name": site_name,
                      "site_id": site_id, "asset_code": asset_code},
            "outcome": "resolved",
            "reason": "asset",
            "building_id": bid,
            "building": next(
                (r["label"] for r in index if r["building_id"] == bid), None
            ),
            "buildings_considered": len(index),
        }
    row, reason = choose_building(
        index, name=name, code=code, site_name=site_name, site_id=site_id
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
    if outcome != "resolved":
        # What it saw, so a person can decide rather than guess at why it declined.
        n = normalize_name(name)
        out["candidates"] = [
            {"building_id": r["building_id"], "name": r["label"], "site_id": r.get("site_id")}
            for r in index
            if (n and (n in (r.get("name_keys") or set()) or _contains(n, r)))
            or (site_id and r.get("site_id") == str(site_id))
        ][:10]
    return out


async def resolve_batch(
    session: AsyncSession, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve many at once against a single index read — the shape an ingest run needs.

    Two reads for the whole batch: the building index, and the asset map for every asset
    code mentioned. A work order file of 5,000 rows costs the same two queries as one row.
    """
    index = await load_building_index(session)
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
        bid = asset_map.get(normalize_ref(item.get("asset_code")))
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
            "building_id": building_id,
            "building": building,
        })
    return {
        "ok": True,
        "buildings_considered": len(index),
        "assets_placed": len(asset_map),
        "count": len(items),
        "by_outcome": tally,
        "by_reason": reasons,
        "results": results,
    }
