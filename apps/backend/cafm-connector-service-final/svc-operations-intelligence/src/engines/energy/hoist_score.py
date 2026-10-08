"""The portfolio Hoist Score: how many hoisted buildings each kind of record has reached.

buildings.hoist_score_for() answers for ONE building: does it have an asset register, a
certificate, a contract, a meter and a work order on record. The Home tile asks the same
question across the portfolio — of the buildings hoisted, how many has each kind of record
reached — and draws one bar per kind: "1 of 2 hoisted buildings with a contract on record".

That is coverage of the buildings, and it needs no ground truth beyond the buildings
themselves. It is deliberately NOT statutory-catalogue completeness (how many of the pack's
certificate types are filed), nor a count of vendors with terms, nor of meters linked: the
tile used to average those four unlike denominators into one number, and read 7 for a
portfolio whose two buildings had eight certificates between them.

A bar reads the covered / missing lists the Buildings table already derived from each
row's graph counts (buildings.apply_graph_rollup), so the Home bars and the Buildings
column cannot drift; a row that carries none — one with a recorded hoist_score, which is
someone's judgement about the number and says nothing about which records exist — is
derived here the same way. When the table is rooted on sites (plenum_cafm.buildings has no
rows) nothing was counted, and every bar says so instead of reporting a zero nobody measured.
"""
from __future__ import annotations

from typing import Any

from .buildings import HOIST_SCORE_DOMAINS, hoist_score_for

#: The five domains, in the order the per-building score names them.
DOMAINS: tuple[str, ...] = tuple(d for d, _ in HOIST_SCORE_DOMAINS)

NOT_COUNTED = "the building graph has no rows yet, so nothing is counted against a building"
NO_BUILDINGS = "no buildings hoisted yet"
NO_ALLOCATED = "no buildings allocated to you"
#: How many missing buildings a domain names. The tile prints three and "and N more"; a
#: 600-building portfolio does not need 600 stubs, five times over, in every Home load.
#: missing_count stays exact whatever the cap.
MISSING_CAP = 25


def _who(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "building_id": row.get("building_id"),
        "name": row.get("name"),
        # shape_building_row() spells the code `code`; the buildings table spells it
        # `building_code`. Both are read so the field is never null for a building that has one.
        "building_code": row.get("building_code") or row.get("code"),
    }


def portfolio_hoist_score(
    rows: list[dict[str, Any]], *, root: str, restricted: bool = False
) -> dict[str, Any]:
    """Aggregate the buildings table (list_buildings()'s rows) into per-domain coverage.

    ``root`` is the table's own ``root`` field: "buildings" when the graph counted, anything
    else when the rows came from sites and carry no counts. ``restricted`` says the rows were
    narrowed to the caller's allocation, so an empty list means "none allocated to you"
    rather than "nothing hoisted" — the portfolio may well have buildings they cannot see.
    """
    counted = root == "buildings"
    n = len(rows)

    per_row: list[dict[str, Any]] = []
    for r in rows:
        stored_c, stored_m = r.get("hoist_score_covered"), r.get("hoist_score_missing")
        if counted and isinstance(stored_c, list) and isinstance(stored_m, list):
            covered, missing = list(stored_c), list(stored_m)
            score: int | None = int(round(100.0 * len(covered) / len(DOMAINS)))
        elif counted:
            hs = hoist_score_for(r.get("graph_counts") or {},
                                 has_eui=r.get("eui_kwh_per_m2") is not None)
            score, covered, missing = hs["score"], hs["covered"], hs["missing"]
        else:
            score, covered, missing = None, [], list(DOMAINS)
        per_row.append({**_who(r), "score": score, "covered": covered, "missing": missing})

    domains: list[dict[str, Any]] = []
    for key in DOMAINS:
        if not counted or n == 0:
            domains.append({
                "key": key, "covered": None, "of": n, "pct": None,
                "missing_buildings": [], "missing_count": None,
                "note": NOT_COUNTED if not counted else (NO_ALLOCATED if restricted else NO_BUILDINGS),
            })
            continue
        reached = [p for p in per_row if key in p["covered"]]
        without = [p for p in per_row if key in p["missing"]]
        domains.append({
            "key": key,
            "covered": len(reached),
            "of": n,
            "pct": int(round(100.0 * len(reached) / n)),
            "missing_buildings": [_who(p) for p in without[:MISSING_CAP]],
            "missing_count": len(without),
            "note": None,
        })

    scores = [p["score"] for p in per_row if isinstance(p["score"], int)]
    return {
        "ok": True,
        "root": root,
        "buildings": n,
        "domains": domains,
        # The mean of the per-building scores over all five domains — what the Buildings
        # page's column averages to. The Home tile draws four of the five and takes its own
        # mean of those, so the two figures are related but not the same number.
        "score": int(round(sum(scores) / len(scores))) if scores else None,
        "rows": per_row,
    }
