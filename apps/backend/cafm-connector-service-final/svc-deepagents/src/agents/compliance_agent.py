"""
Compliance agent tools — queries the plenum_cafm schema directly to assess
regulatory compliance status and generate compliance reports.
"""
import re
from typing import Any

import structlog
from langchain_core.tools import tool
from sqlalchemy import text

from .. import database
from ..services.principal import building_clause
from . import name_match

log = structlog.get_logger(__name__)

# ── the asset a typed code names (29 Oct 2026) ────────────────────────────────────────────────
# check_requirements compared the typed code with `a.asset_code = :asset_code`: exact and
# case-sensitive. "pt-b-301-a-boiler-01", "PTB301ABOILER01" or the asset's name found no row,
# and no row meant no findings, so an asset with an overdue PM — and an asset that does not
# exist — was reported "compliant". The code is now resolved first through the same ladder
# ontology_qa runs (exact, same letters and digits, phrase, every word, close spelling), on
# asset_code and asset_name, inside the caller's building boundary. A read: nothing is written.

#: Words around an asset code that are not part of it.
_ASSET_KIND_WORDS = frozenset({"asset", "assets", "equipment"})
_ASSET_LADDER = ("exact", "compact", "search", "words", "fuzzy")
_ASSET_MATCH_LIMIT = 50
_ASSET_FUZZY_CANDIDATES = 500


def _asset_pattern(variant: str) -> str:
    """ontology_qa._search_pattern: the phrase from the start of a word, any separator between
    its words, to the end of a word when it ends in a code or number ("AHU-7" is not AHU-70)."""
    parts = re.findall(r"[^\W_]+", variant)
    if not parts:
        return ""
    ws = name_match.words(variant)
    pat = "[^[:alnum:]]*".join(re.escape(w) for w in parts)
    return "\\m" + pat + ("\\M" if ws and name_match.exact_token(ws[-1]) else "")


def _asset_attempts(mode: str, typed: str) -> list:
    """What each attempt of a rung compares, in order (ontology_qa._rung_attempts)."""
    core_words = name_match.meaningful(typed, _ASSET_KIND_WORDS)
    core = " ".join(core_words)
    if mode == "exact":
        return [t for t in dict.fromkeys([typed, core]) if t]
    if mode == "compact":
        keys = [name_match.compact(typed), name_match.compact(core)]
        return [k for k in dict.fromkeys(keys) if k]
    if mode == "search":
        pats = [_asset_pattern(v) for v in name_match.phrase_variants(typed, _ASSET_KIND_WORDS)]
        return [p for p in dict.fromkeys(pats) if p]
    if mode == "words":
        return [core_words] if len(core_words) >= 2 else []
    if mode == "fuzzy":
        frags = name_match.fragments(core)
        return ["(" + "|".join(frags) + ")"] if frags else []
    return []


def _asset_rung_sql(mode: str, attempt, bsql: str) -> tuple[str, dict]:
    """One attempt of one rung over asset_code and asset_name (ontology_qa._rung_cond)."""
    params: dict[str, Any] = {}
    both = "concat_ws(' ', a.asset_code::text, a.asset_name::text)"
    if mode == "exact":
        params["q"] = attempt
        cond = "lower(a.asset_code::text) = lower(:q) OR lower(a.asset_name::text) = lower(:q)"
    elif mode == "compact":
        params["q"] = attempt
        cond = ("regexp_replace(lower(a.asset_code::text), '[^a-z0-9]+', '', 'g') = :q"
                " OR regexp_replace(lower(a.asset_name::text), '[^a-z0-9]+', '', 'g') = :q")
    elif mode == "search":
        params["q"] = attempt
        cond = "a.asset_code::text ~* :q OR a.asset_name::text ~* :q"
    elif mode == "words":
        conds = []
        for i, w in enumerate(attempt):
            params[f"w{i}"] = "\\m" + w + ("\\M" if name_match.exact_token(w) else "")
            conds.append(f"{both} ~* :w{i}")
        cond = " AND ".join(conds)
    else:
        params["q"] = attempt
        cond = f"{both} ~* :q"
    limit = _ASSET_FUZZY_CANDIDATES if mode == "fuzzy" else _ASSET_MATCH_LIMIT
    sql = (f"/* resolve asset: {mode} */ SELECT DISTINCT a.asset_code, a.asset_name"
           f" FROM plenum_cafm.assets a WHERE ({cond}){bsql} LIMIT {limit}")
    return sql, params


async def _resolve_asset(session, typed: str, bsql: str,
                         bparams: dict) -> tuple[str | None, list[dict]]:
    """The assets ``typed`` names at the first rung that finds any, and that rung; (None, [])
    when it names none. A phrase of kind words alone ("the asset") names nothing."""
    typed = str(typed or "").strip()
    if not name_match.meaningful(typed, _ASSET_KIND_WORDS):
        return None, []
    core = " ".join(name_match.meaningful(typed, _ASSET_KIND_WORDS))
    for mode in _ASSET_LADDER:
        for attempt in _asset_attempts(mode, typed):
            sql, params = _asset_rung_sql(mode, attempt, bsql)
            result = await session.execute(text(sql), {**params, **bparams})
            rows = [dict(r) for r in result.mappings().all()]
            if mode == "fuzzy" and rows:
                # Candidates by fragment; only the clearly closest name or code counts.
                scored: dict[str, tuple[float, dict]] = {}
                for r in rows:
                    texts = [t for t in (r.get("asset_code"), r.get("asset_name")) if t]
                    best = max((name_match.similarity(core, t) for t in texts), default=0.0)
                    if best >= name_match.FUZZY_MIN:
                        scored[str(r.get("asset_code"))] = (best, r)
                top = max((sc for sc, _ in scored.values()), default=0.0)
                rows = [r for sc, r in scored.values() if sc >= top - name_match.FUZZY_TIE]
            if rows:
                return mode, rows
    return None, []


@tool
async def check_requirements(asset_code: str, regulation: str | None = None) -> dict:
    """Check compliance status for an asset against maintenance and inspection requirements.

    Evaluates: PM schedule adherence, open corrective-action inspections,
    overdue work orders, and certification expiry. Optionally filter by
    a specific regulation or standard (e.g. 'ISO_55001', 'OSHA', 'UAE_FIRE').

    Returns a compliance_status (compliant | at_risk | non_compliant) with
    a list of specific findings for each failing check.

    Args:
        asset_code: Asset to check compliance for: its code or name as the user typed it (case,
            hyphens, word order and one wrong letter do not matter). Several assets matching come
            back as `ambiguous` with `matches`; none as an `error`.
        regulation: Optional regulation to filter checks against (checks all if omitted).
    """
    # The caller's buildings, or nothing for an admin. Every query below joins the
    # asset, and the building lives on the asset.
    bsql, bparams = building_clause("a.building_id")
    asked_for = asset_code
    async with database.AsyncSessionLocal() as session:
        try:
            # The asset the typed code names, inside the same boundary (29 Oct 2026): none is
            # not found and several are ambiguous - never "compliant" for want of a row.
            mode, matches = await _resolve_asset(session, asset_code, bsql, bparams)
            codes = list(dict.fromkeys(
                str(m["asset_code"]) for m in matches if m.get("asset_code")))
            if not codes:
                return {"asset_code": asked_for, "error": (
                    f"'{asked_for}' is not on the register as an asset code or name the caller can "
                    "see - say so; do not report a compliance status for it.")}
            if len(codes) > 1:
                return {"asset_code": asked_for, "ambiguous": True,
                        "matches": [{"asset_code": m.get("asset_code"),
                                     "asset_name": m.get("asset_name")} for m in matches],
                        "error": (f"'{asked_for}' names {len(codes)} assets - list them and "
                                  "ask which; never pick one.")}
            asset_code = codes[0]
            # Fetch PM compliance — maintenance_plans joined to assets via asset_id UUID FK
            pm_rows = (await session.execute(
                text(f"""
                    SELECT mp.sm_code, mp.frequency_type, mp.frequency_value, mp.next_due_date,
                           mp.next_due_date < CURRENT_DATE AS is_overdue
                    FROM plenum_cafm.maintenance_plans mp
                    JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text
                    WHERE a.asset_code = :asset_code
                      AND mp.status = 'active'
                      {bsql}
                """),
                {"asset_code": asset_code, **bparams},
            )).mappings().all()

            # Fetch open high/critical priority WOs — work_orders joined to assets via asset_id
            wo_rows = (await session.execute(
                text(f"""
                    SELECT wo.wo_code, wo.priority, wo.status, wo.created_at,
                           now() - wo.created_at AS age
                    FROM plenum_cafm.work_orders wo
                    JOIN plenum_cafm.assets a ON a.id = wo.asset_id::text
                    WHERE a.asset_code = :asset_code
                      AND wo.status NOT IN ('completed', 'closed', 'cancelled')
                      AND wo.priority IN ('high', 'critical')
                      {bsql}
                """),
                {"asset_code": asset_code, **bparams},
            )).mappings().all()

            # Fetch open corrective-action inspections (inspections table uses asset_code directly)
            inspection_rows = (await session.execute(
                text(f"""
                    SELECT id, inspection_date, risk_level, finding_type, observations
                    FROM plenum_cafm.inspections i
                    JOIN plenum_cafm.assets a ON a.asset_code = i.asset_code
                    WHERE i.asset_code = :asset_code
                      AND i.corrective_action = true
                      {bsql}
                    ORDER BY inspection_date DESC
                    LIMIT 20
                """),
                {"asset_code": asset_code, **bparams},
            )).mappings().all()

        except Exception as exc:
            log.error("compliance.check.error", asset=asset_code, error=str(exc))
            return {"error": str(exc)}

    findings: list[dict] = []
    for pm in pm_rows:
        if pm["is_overdue"]:
            findings.append({
                "type": "pm_overdue",
                "sm_code": pm["sm_code"],
                "next_due_date": str(pm["next_due_date"]),
                "frequency_type": pm["frequency_type"],
            })

    for wo in wo_rows:
        findings.append({
            "type": "high_priority_open_wo",
            "wo_code": wo["wo_code"],
            "priority": wo["priority"],
            "age_days": str(wo["age"]),
        })

    for insp in inspection_rows:
        findings.append({
            "type": "open_corrective_action",
            "inspection_id": str(insp["id"]),
            "risk_level": insp["risk_level"],
            "finding_type": insp["finding_type"],
        })

    if not findings:
        status = "compliant"
    elif any(f["type"] == "pm_overdue" for f in findings) or any(
        f.get("risk_level") == "High" for f in findings
    ):
        status = "non_compliant"
    else:
        status = "at_risk"

    out = {
        "asset_code": asset_code,
        "regulation": regulation or "all",
        "compliance_status": status,
        "findings_count": len(findings),
        "findings": findings,
    }
    if asset_code != asked_for:
        out["asked_for"] = asked_for
        out["matched_by"] = mode
        if mode == "fuzzy":
            out["note"] = (f"'{asked_for}' is not on the register as written; the closest asset is "
                           f"'{asset_code}' - say so.")
    return out


@tool
async def generate_compliance_report(
    scope: str = "all_assets",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Generate a portfolio-wide compliance summary report.

    Aggregates PM adherence, inspection outcomes, and high-priority WO backlog
    across all assets (or a filtered scope). Returns per-asset compliance scores
    and a portfolio-level summary with top risk items.

    Args:
        scope: 'all_assets' for the full portfolio, or an asset category name
               (e.g. 'Air Handler', 'Boiler') to restrict the report.
        date_from: Report period start (ISO 8601, optional).
        date_to: Report period end (ISO 8601, optional).
    """
    # The caller's buildings, or nothing for an admin. Every query below joins the
    # asset, and the building lives on the asset.
    bsql, bparams = building_clause("a.building_id")
    params: dict[str, Any] = {**bparams}

    # Build asset query — join asset_categories since assets has category_id FK (no direct category col)
    if scope != "all_assets":
        asset_sql = f"""
            SELECT a.asset_code, a.asset_name, ac.category_name AS category
            FROM plenum_cafm.assets a
            LEFT JOIN plenum_cafm.asset_categories ac ON ac.id = a.category_id
            WHERE ac.category_name = :category
            {bsql}
        """
        params["category"] = scope
    else:
        asset_sql = f"""
            SELECT a.asset_code, a.asset_name, ac.category_name AS category
            FROM plenum_cafm.assets a
            LEFT JOIN plenum_cafm.asset_categories ac ON ac.id = a.category_id
            WHERE TRUE {bsql}
        """

    async with database.AsyncSessionLocal() as session:
        try:
            assets = (await session.execute(text(asset_sql), params)).mappings().all()

            # Overdue PMs — maintenance_plans joined to assets via asset_id UUID FK
            overdue_pms = (await session.execute(
                text(f"""
                    SELECT a.asset_code, count(*) AS overdue_count
                    FROM plenum_cafm.maintenance_plans mp
                    JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text
                    WHERE mp.next_due_date < CURRENT_DATE
                      AND mp.status = 'active'
                      {bsql}
                    GROUP BY a.asset_code
                """), bparams,
            )).mappings().all()
            overdue_map: dict[str, int] = {r["asset_code"]: int(r["overdue_count"]) for r in overdue_pms}

            # Open high/critical priority WOs — work_orders joined to assets via asset_id
            open_critical_wos = (await session.execute(
                text(f"""
                    SELECT a.asset_code, count(*) AS wo_count
                    FROM plenum_cafm.work_orders wo
                    JOIN plenum_cafm.assets a ON a.id = wo.asset_id::text
                    WHERE wo.status NOT IN ('completed', 'closed', 'cancelled')
                      AND wo.priority IN ('high', 'critical')
                      {bsql}
                    GROUP BY a.asset_code
                """), bparams,
            )).mappings().all()
            wo_map: dict[str, int] = {r["asset_code"]: int(r["wo_count"]) for r in open_critical_wos}

        except Exception as exc:
            log.error("compliance.report.error", scope=scope, error=str(exc))
            return {"error": str(exc)}

    asset_scores: list[dict] = []
    non_compliant = 0
    at_risk = 0
    compliant_count = 0

    for asset in assets:
        code = asset["asset_code"]
        if not code:
            continue
        overdue = overdue_map.get(code, 0)
        high_wos = wo_map.get(code, 0)

        if overdue > 0 or high_wos >= 3:
            asset_status = "non_compliant"
            non_compliant += 1
        elif high_wos > 0:
            asset_status = "at_risk"
            at_risk += 1
        else:
            asset_status = "compliant"
            compliant_count += 1

        asset_scores.append({
            "asset_code": code,
            "asset_name": asset["asset_name"],
            "category": asset["category"],
            "compliance_status": asset_status,
            "overdue_pms": overdue,
            "open_high_priority_wos": high_wos,
        })

    total = len(asset_scores)
    return {
        "scope": scope,
        "period": {"from": date_from, "to": date_to},
        "summary": {
            "total_assets": total,
            "compliant": compliant_count,
            "at_risk": at_risk,
            "non_compliant": non_compliant,
            "compliance_rate_pct": round(compliant_count / total * 100, 1) if total else 0,
        },
        "asset_details": sorted(asset_scores, key=lambda x: x["compliance_status"]),
    }
