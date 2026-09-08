"""Link ingested asset rows to the building they sit in.

A CSV of assets says where each asset is in whatever words the source system used —
"Bishopsgate Tower", "BT-01", a site code, sometimes nothing at all. The graph needs a
building uuid. This module is the step between them for bulk ingestion.

It calls svc-operations-intelligence's ``/api/energy/buildings/resolve-batch`` rather than
matching here. That service owns the building graph and the tiered matcher, and one copy of
"which building is this" is the point — a second matcher in this service would drift from it
and the two would file the same asset against different buildings.

Two properties matter for bulk:

* **One call per file, not per row.** Hints are deduped first — an assets.csv of 5,000 rows
  across six buildings resolves six hints. The endpoint reads the building index once.
* **Never raises.** A building link is an enrichment. If operations-intelligence is down,
  the assets still ingest with ``building_id`` NULL and the reason recorded, exactly as
  ``graph_ingest`` behaves for certificates. Losing the link is recoverable; losing the
  asset row is not.

An ambiguous hint resolves to nothing, by the resolver's own rule: a campus named on a row
names the estate, not the structure, and filing against a guess misstates two buildings'
records at once.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from cafm_shared.logging import get_logger

logger = get_logger(__name__)

#: Where the building graph lives. Same env var svc-deepagents already uses.
_DEFAULT_BASE_URL = "http://svc-operations-intelligence:8009"
_PATH = "/api/energy/buildings/resolve-batch"
_TIMEOUT_S = 20.0

# Column spellings a source system might use, in the order they are trusted. A building
# code beats a building name beats the site the building sits on — the same order the
# resolver applies internally, so the strongest hint a row carries is the one sent.
_CODE_KEYS = ("buildingcode", "buildingref", "buildingno", "bldgcode", "blockcode")
_NAME_KEYS = ("buildingname", "building", "bldg", "block", "premises", "property")
_SITE_ID_KEYS = ("siteid", "sitecode", "sitereference", "siteref")
_SITE_NAME_KEYS = ("sitename", "estate", "campus")
#: Deliberately last and only as a code: "location" in a CMMS export is as often a room
#: as a building, so it is the weakest thing here and never read as a name.
_FALLBACK_CODE_KEYS = ("locationcode", "location")


def _norm_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key or "").lower())


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null", "n/a", "-"}:
        return None
    return s


def _first(lookup: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        got = _clean(lookup.get(k))
        if got:
            return got
    return None


def building_hint(row: dict[str, Any]) -> dict[str, str] | None:
    """What this row says about its building, or None when it says nothing.

    Reads both canonical and untouched source column names — the schema mapper renames the
    columns it recognises and leaves the rest alone, and a building column is usually one of
    the ones it leaves alone.
    """
    lookup = {_norm_key(k): v for k, v in row.items()}

    hint: dict[str, str] = {}
    code = _first(lookup, _CODE_KEYS) or _first(lookup, _FALLBACK_CODE_KEYS)
    if code:
        hint["code"] = code
    name = _first(lookup, _NAME_KEYS)
    if name:
        hint["name"] = name

    site_id = _first(lookup, _SITE_ID_KEYS)
    site_name = _first(lookup, _SITE_NAME_KEYS)
    # A bare "site" column could be either spelling. Sending it as both costs nothing —
    # the resolver tries the id tier first and falls through to the name tier — and
    # guessing wrong would silently drop the only hint the row has.
    bare_site = _clean(lookup.get("site"))
    if site_id or bare_site:
        hint["site_id"] = site_id or bare_site or ""
    if site_name or bare_site:
        hint["site_name"] = site_name or bare_site or ""

    return hint or None


def _hint_key(hint: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(hint.items()))


#: What would actually close each gap. A reason code tells an agent what happened; this
#: tells it what to do about it, which is the difference between a report it can act on
#: and one somebody has to interpret.
_REMEDY = {
    "no_building_column": (
        "The file names no building, site or location. Add a building, building_code or "
        "site_id column, or ingest one file per building."
    ),
    "no_match": (
        "Nothing in the portfolio matches this. Either the building is not in the graph "
        "yet, or the spelling differs from the one on record."
    ),
    "no_buildings": (
        "The portfolio holds no buildings at all. Backfill buildings from sites before "
        "linking assets."
    ),
    "ambiguous_name": (
        "More than one building carries this name. Add a building_code column so the rows "
        "name one of them."
    ),
    "ambiguous_name_contains": (
        "This text is contained in several building names. Add a building_code column."
    ),
    "ambiguous_building_code": (
        "More than one building carries this code. The codes in the graph need "
        "de-duplicating before these rows can be placed."
    ),
    "site_has_several_buildings": (
        "The rows name a site holding several buildings, so they name the estate and not "
        "the structure. Add a building or building_code column."
    ),
    "resolver_unavailable": (
        "operations-intelligence could not be reached, so nothing was attempted. The "
        "assets are ingested; re-run the link to fill these in."
    ),
    "resolver_response_misaligned": (
        "The resolver returned a different number of answers than questions asked, so no "
        "link could be trusted. Re-run the link."
    ),
}


def _row_label(row: dict[str, Any]) -> str | None:
    """Something a person can find the row by."""
    lookup = {_norm_key(k): v for k, v in row.items()}
    return _first(lookup, ("assetcode", "assetname", "name", "serial", "serialnumber"))


def _build_gaps(
    rows: list[dict[str, Any]],
    row_to_hint: list[int | None],
    hints: list[dict[str, str]],
    per_hint_reason: dict[int, str],
    no_hint_rows: list[int],
) -> list[dict[str, Any]]:
    """One entry per distinct thing that could not be placed, with what would fix it.

    Grouped by the hint rather than listed per row: 400 assets in one unnamed building is
    one problem to solve, and 400 identical lines would bury it.
    """
    gaps: list[dict[str, Any]] = []

    def _entry(reason: str, said: dict[str, str] | None, idxs: list[int]) -> dict[str, Any]:
        return {
            "field": "building_id",
            "reason": reason,
            "rows_affected": len(idxs),
            "row_says": said,
            "sample_assets": [
                lbl for lbl in (_row_label(rows[i]) for i in idxs[:5]) if lbl
            ],
            "remedy": _REMEDY.get(reason, "Unresolved — inspect these rows by hand."),
        }

    if no_hint_rows:
        gaps.append(_entry("no_building_column", None, no_hint_rows))

    by_hint: dict[int, list[int]] = {}
    for row_idx, hint_idx in enumerate(row_to_hint):
        if hint_idx is not None and hint_idx in per_hint_reason:
            by_hint.setdefault(hint_idx, []).append(row_idx)

    for hint_idx, idxs in by_hint.items():
        gaps.append(_entry(per_hint_reason[hint_idx], hints[hint_idx], idxs))

    gaps.sort(key=lambda g: g["rows_affected"], reverse=True)
    return gaps


def base_url() -> str:
    return (
        os.getenv("OPERATIONS_INTELLIGENCE_BASE_URL", "").strip().rstrip("/")
        or _DEFAULT_BASE_URL
    )


async def resolve_buildings(
    rows: list[dict[str, Any]],
    *,
    url: str | None = None,
    timeout: float = _TIMEOUT_S,
    client: Any = None,
) -> dict[str, Any]:
    """Resolve every row's building in one call. Returns a report; never raises.

    ``by_row`` is aligned to ``rows`` — index i is row i's building_id, or None when the row
    gave no hint, the hint matched nothing, or the hint was ambiguous. ``by_reason`` says
    which of those it was for every row, so an unlinked asset is a stated outcome and not a
    silence somebody has to go digging for.
    """
    report: dict[str, Any] = {
        "by_row": [None] * len(rows),
        "by_reason": {},
        "by_outcome": {},
        "rows": len(rows),
        "unique_hints": 0,
        "linked": 0,
        "unlinked": 0,
        # What is missing and what would fill it — the part an agent acts on.
        "gaps": [],
    }
    if not rows:
        return report

    # Dedupe: identical hints resolve identically, and a 5,000-row file is usually a
    # handful of distinct buildings.
    hints: list[dict[str, str]] = []
    seen: dict[tuple[tuple[str, str], ...], int] = {}
    row_to_hint: list[int | None] = []
    no_hint_rows: list[int] = []
    for row_idx, row in enumerate(rows):
        hint = building_hint(row)
        if not hint:
            row_to_hint.append(None)
            no_hint_rows.append(row_idx)
            continue
        key = _hint_key(hint)
        if key not in seen:
            seen[key] = len(hints)
            hints.append(hint)
        row_to_hint.append(seen[key])

    no_hint = len(no_hint_rows)
    if no_hint:
        report["by_reason"]["no_building_column"] = no_hint
        report["by_outcome"]["unmatched"] = no_hint
    report["unique_hints"] = len(hints)

    def _finish(per_hint_reason: dict[int, str]) -> dict[str, Any]:
        report["gaps"] = _build_gaps(
            rows, row_to_hint, hints, per_hint_reason, no_hint_rows
        )
        report["unlinked"] = len(rows) - report["linked"]
        return report

    if not hints:
        return _finish({})

    endpoint = (url or base_url()).rstrip("/") + _PATH
    try:
        if client is not None:
            resp = await client.post(endpoint, json={"items": hints})
        else:
            async with httpx.AsyncClient(timeout=timeout) as http:
                resp = await http.post(endpoint, json={"items": hints})
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results") or []
    except Exception as exc:  # noqa: BLE001 — a missing link must never fail an ingest
        logger.warning(
            "building_link.unavailable",
            error=str(exc)[:200],
            endpoint=endpoint,
            unique_hints=len(hints),
        )
        report["by_reason"]["resolver_unavailable"] = len(rows) - no_hint
        report["by_outcome"]["unmatched"] = (
            report["by_outcome"].get("unmatched", 0) + len(rows) - no_hint
        )
        report["error"] = str(exc)[:200]
        return _finish({i: "resolver_unavailable" for i in range(len(hints))})

    if len(results) != len(hints):
        # Order is the only thing tying a result to its hint. A length mismatch means that
        # assumption no longer holds, and a mis-aligned link is worse than no link.
        logger.warning("building_link.length_mismatch", sent=len(hints), got=len(results))
        report["by_reason"]["resolver_response_misaligned"] = len(rows) - no_hint
        report["by_outcome"]["unmatched"] = (
            report["by_outcome"].get("unmatched", 0) + len(rows) - no_hint
        )
        return _finish({i: "resolver_response_misaligned" for i in range(len(hints))})

    unresolved: dict[int, str] = {}
    for i, hint_idx in enumerate(row_to_hint):
        if hint_idx is None:
            continue
        res = results[hint_idx] or {}
        reason = str(res.get("reason") or "unknown")
        outcome = str(res.get("outcome") or "unmatched")
        report["by_reason"][reason] = report["by_reason"].get(reason, 0) + 1
        report["by_outcome"][outcome] = report["by_outcome"].get(outcome, 0) + 1
        if outcome == "resolved" and res.get("building_id"):
            report["by_row"][i] = str(res["building_id"])
            report["linked"] += 1
        else:
            unresolved[hint_idx] = reason

    return _finish(unresolved)
