"""The Assets page's Ask bar, answered from the condition engine and the asset register.

Same contract as the Maintenance one: every answer is composed from rows a named engine
function returned, `source` says which, and nothing here writes SQL from the question or
invents a figure. The skills call functions that are already building-scoped, so the Ask bar
cannot reach a building the caller is not allocated to — not because it checks, but because
the function underneath it cannot read one.

Matching is by pattern. Every chip the page ships matches exactly, reasonable paraphrases
match too, and anything else is answered with what this can and cannot be asked — a confident
answer to a question nobody asked is worse than no answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import asset_intelligence as ai
from . import buildings as bld_svc
from . import condition_engine as ce
from . import detection_coverage as dc_svc
from . import market_profiles as mp_svc

log = get_logger(__name__)

CONFIDENCE_FLOOR = 0.45


def _n_of(n: int, singular: str, plural: str | None = None) -> str:
    """"1 asset" / "5 assets". These strings are read by people, so they have to read."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _is_are(n: int) -> str:
    return "is" if n == 1 else "are"


@dataclass
class Skill:
    id: str
    question: str
    must: list[str] = field(default_factory=list)
    any_of: list[str] = field(default_factory=list)
    never: list[str] = field(default_factory=list)
    handler: Callable[..., Awaitable[dict[str, Any]]] | None = None
    endpoint: str = ""
    page: str = "assets"


def _n(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower())


def _score(q: str, s: Skill) -> float:
    text_ = _n(q)
    if text_.strip() == _n(s.question).strip():
        return 1.0
    if any(re.search(rf"\b{re.escape(w)}", text_) for w in s.never):
        return 0.0
    if not all(re.search(rf"\b{re.escape(w)}", text_) for w in s.must):
        return 0.0
    if not s.any_of:
        return 0.75 if s.must else 0.0
    hits = sum(1 for w in s.any_of if re.search(rf"\b{re.escape(w)}", text_))
    if not hits:
        return 0.0
    return min(0.55 + 0.15 * hits, 0.95)


# ── the skills ───────────────────────────────────────────────────────────────────────

async def _inspect_next(session, building_ids, org):
    """What to look at first: the assets both signals agree on, then the shared-load ones."""
    a = await ce.assess(session, building_ids=building_ids, organization_id=org)
    threat = [x for x in a["assets"] if x["band"] == ce.BAND_THREAT]
    watch = [x for x in a["assets"] if x["band"] == ce.BAND_WATCH]
    if not threat and not watch:
        return {"answer": ("Nothing is flagged. No asset has both an over-reference section "
                           "and an anomaly, and none has a persistent anomaly of its own."),
                "count": 0, "data": []}
    lead = ", ".join(f"{x['asset_name']} ({x['building']})" for x in (threat + watch)[:4])
    return {"answer": (
        f"{_n_of(len(threat), 'asset')} {_is_are(len(threat))} a threat and {len(watch)} "
        f"{_is_are(len(watch))} a watch. Start with the "
        + ("threat: both signals agree on it, so a work order is justified without waiting "
           "for a fault. " if len(threat) == 1 else
           "threats: both signals agree on them, so a work order is justified without "
           "waiting for a fault. ") + f"{lead}."),
        "count": len(threat) + len(watch), "data": threat + watch}


async def _threat_backlog(session, building_ids, org):
    """Open work orders on the assets the engine calls a threat."""
    a = await ce.assess(session, building_ids=building_ids, organization_id=org)
    threat = [x for x in a["assets"] if x["band"] == ce.BAND_THREAT]
    if not threat:
        return {"answer": "No asset is banded a threat, so there is no backlog on one.",
                "count": 0, "data": []}
    ids = [x["asset_id"] for x in threat]
    try:
        async with session.begin_nested():
            rows = (await session.execute(text("""
                SELECT w.asset_id::text AS asset_id, count(*) AS open_orders
                  FROM plenum_cafm.work_orders w
                 WHERE w.asset_id::text = ANY(CAST(:ids AS text[]))
                   AND lower(coalesce(w.status, '')) NOT IN
                       ('completed','closed','complete','done','cancelled','canceled')
                 GROUP BY 1"""), {"ids": ids})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("ask.threat_backlog_failed", error=str(exc)[:200])
        rows = []
    by_asset = {r["asset_id"]: int(r["open_orders"]) for r in rows}
    total = sum(by_asset.values())
    covered = sum(1 for x in threat if by_asset.get(x["asset_id"]))
    data = [{**x, "open_work_orders": by_asset.get(x["asset_id"], 0)} for x in threat]
    data.sort(key=lambda x: -x["open_work_orders"])
    bare = len(threat) - covered
    return {"answer": (
        f"{_n_of(total, 'work order')} {_is_are(total)} open across the "
        f"{_n_of(len(threat), 'threat asset')}, and {bare} of "
        f"{'it' if len(threat) == 1 else 'them'} {_is_are(bare)} carrying no open order at "
        f"all — those are the ones the engine is flagging and nobody has raised anything for."),
        "count": total, "data": data}


async def _sections_over(session, building_ids, org):
    s = await ai.sections(session, building_ids=building_ids)
    over = [x for x in (s.get("sections") or [])
            if x.get("deviation_pct") is not None and x["deviation_pct"] > 0]
    over.sort(key=lambda x: -x["deviation_pct"])
    measured = sum(1 for x in (s.get("sections") or []) if x.get("measured"))
    if not over:
        return {"answer": (
            f"No section is over its own reference. {measured} of "
            f"{len(s.get('sections') or [])} sections are metered; the rest have no sub-meter, "
            f"so they were not read."), "count": 0, "data": []}
    lead = ", ".join(
        f"{x['name']} at {x['eui_kwh_per_m2']} against {x['reference_eui_kwh_m2']} "
        f"({x['deviation_pct']:+.0f}%)" for x in over[:3])
    return {"answer": (
        f"{len(over)} of {_n_of(measured, 'metered section')} {_is_are(len(over))} over "
        f"their own reference: {lead}. Each is read against its own reference, not the "
        f"building's."),
        "count": len(over), "data": over}


async def _value_at_risk(session, building_ids, org):
    v = await ai.portfolio_value_at_risk(session, building_ids=building_ids)
    if not v.get("ok"):
        return {"answer": "I could not read the asset values.", "count": None, "data": []}
    if not v["assets_counted"]:
        return {"answer": (
            f"No asset can be valued: {v['assets_not_computable']} are missing a replacement "
            f"value, a design life or an install date, and none is counted as worth nothing."),
            "count": 0, "data": []}
    return {"answer": (
        f"{v.get('currency') or ''}{v['value_at_risk']:,.0f} a year is at risk across "
        f"{_n_of(v['assets_contributing'], 'asset')}. {v['assets_counted']} could be valued and "
        f"{v['assets_not_computable']} could not — those are reported as not computable rather "
        f"than counted as worthless."),
        "count": v["assets_contributing"], "data": v.get("assets", [])}


async def _condition_summary(session, building_ids, org):
    s = await ce.summary(session, building_ids=building_ids, organization_id=org)
    t = s["summary"]
    if not t["assets"]:
        return {"answer": "There are no assets in your buildings.", "count": 0, "data": []}
    unmetered = (f" {t['section_not_measured']} of them sit in sections with no sub-meter, so "
                 f"they were banded on the anomaly signal alone."
                 if t["section_not_measured"] else "")
    return {"answer": (
        f"Of {_n_of(t['assets'], 'asset')}, {t['threat']} {_is_are(t['threat'])} a threat, "
        f"{t['watch']} a watch and {t['in_control']} in control — "
        f"{t['in_control_anomaly_under_threshold']} of those carrying an anomaly too small to "
        f"count.{unmetered}"),
        "count": t["assets"], "data": s["buildings"]}


SKILLS: list[Skill] = [
    Skill("inspect_next", "Which assets should I inspect before winter?",
          any_of=["inspect", "inspection", "first", "winter", "priorit", "attention", "next"],
          must=["asset"], handler=_inspect_next,
          endpoint="/api/energy/condition/assets"),
    Skill("threat_backlog", "What is the work order backlog on threat assets?",
          must=["threat"], any_of=["backlog", "work order", "order", "orders", "open"],
          handler=_threat_backlog, endpoint="/api/energy/condition/assets?band=threat"),
    Skill("sections_over", "Which sections have gone over reference since last month?",
          must=["section"], any_of=["over", "reference", "gone", "above", "deviat", "worst"],
          handler=_sections_over, endpoint="/api/energy/sections"),
    Skill("value_at_risk", "What asset value is at risk?",
          must=["risk"], any_of=["value", "worth", "cost", "asset", "money", "much"],
          handler=_value_at_risk, endpoint="/api/energy/assets/value-at-risk"),
    Skill("condition_summary", "How many assets are a threat?",
          any_of=["how many", "many", "summary", "count", "overall", "condition"],
          must=[], handler=_condition_summary, endpoint="/api/energy/condition/summary"),
]


# ── the Energy page ──────────────────────────────────────────────────────────────────

async def _markets_excess(session, building_ids, org):
    """Which markets carry the excess, ranked on kWh above each building's own reference.

    Ranked in energy, not money. Every market prices differently and the profiles hold those
    terms as contract wording — "28.4p/kWh contracted", "$1.40/therm billed in therms" — not
    as a number this can multiply. Parsing a rate out of that prose to produce a confident
    figure is exactly the kind of number nobody could check, so the excess is given in kWh,
    which is exact, with each market's stated terms beside it.
    """
    rows = (await bld_svc.list_buildings(session, organization_id=org, limit=500)
            ).get("buildings") or []
    rows = _only_scoped(rows, building_ids)
    if not rows:
        return {"answer": "There are no buildings in your scope.", "count": 0, "data": []}
    try:
        profiles = mp_svc.load_profiles().get("markets") or {}
    except Exception:  # noqa: BLE001
        profiles = {}

    by_market: dict[str, dict[str, Any]] = {}
    for b in rows:
        cc = b.get("country_code") or "unknown"
        eui, ref, area = (_f(b.get("eui_kwh_per_m2")), _f(b.get("benchmark_kwh_per_m2")),
                          _f(b.get("gfa_counted_sqm")) or _f(b.get("gfa_sqm")))
        m = by_market.setdefault(cc, {
            "market": cc, "name": (profiles.get(cc) or {}).get("name", cc),
            "buildings": 0, "over_reference": 0, "excess_kwh": 0.0,
            "measurable": 0, "standard": b.get("benchmark_standard"),
            "electricity": (profiles.get(cc) or {}).get("elec"),
            "currency": (profiles.get(cc) or {}).get("cur")})
        m["buildings"] += 1
        if eui is None or ref is None or not area:
            continue
        m["measurable"] += 1
        if eui > ref:
            m["over_reference"] += 1
            m["excess_kwh"] += (eui - ref) * area

    markets = sorted(by_market.values(), key=lambda x: -x["excess_kwh"])
    for m in markets:
        m["excess_kwh"] = round(m["excess_kwh"])
    carrying = [m for m in markets if m["excess_kwh"] > 0]
    if not carrying:
        return {"answer": (
            f"No market is over reference. {sum(m['measurable'] for m in markets)} of "
            f"{len(rows)} buildings could be measured against their own pack."),
            "count": 0, "data": markets}
    lead = "; ".join(
        f"{m['name']} {m['excess_kwh']:,.0f} kWh across {m['over_reference']} of "
        f"{m['buildings']} buildings" for m in carrying[:3])
    return {"answer": (
        f"{lead}. Ranked on kWh above each building's own reference rather than on money: "
        f"every market prices differently and the profiles hold those terms as contract "
        f"wording, not as a rate this can multiply."),
        "count": len(carrying), "data": markets}


async def _worst_against_pack(session, building_ids, org):
    """Buildings furthest above their own country's reference, not a single league table."""
    rows = (await bld_svc.list_buildings(session, organization_id=org, limit=500)
            ).get("buildings") or []
    rows = _only_scoped(rows, building_ids)
    # No buildings at all and no buildings that can be measured are different answers: one is
    # "you are allocated to nothing", the other is "the readings are not there yet".
    if not rows:
        return {"answer": "There are no buildings in your scope.", "count": 0, "data": []}
    rated = [b for b in rows if _f(b.get("deviation_pct")) is not None]
    over = sorted((b for b in rated if _f(b["deviation_pct"]) > 0),
                  key=lambda b: -_f(b["deviation_pct"]))
    if not rated:
        return {"answer": ("No building can be placed against a reference yet — none has both "
                           "a measured intensity and a pack to be read against."),
                "count": 0, "data": []}
    if not over:
        return {"answer": (
            f"No building is above its own reference. All {len(rated)} that can be measured "
            f"are at or under the pack they are read against."), "count": 0, "data": rated}
    lead = "; ".join(
        f"{b.get('name')} at {b.get('eui_kwh_per_m2')} against {b.get('benchmark_kwh_per_m2')} "
        f"({_f(b['deviation_pct']):+.0f}%, {b.get('benchmark_standard')})" for b in over[:3])
    return {"answer": (
        f"{len(over)} of {len(rated)} measurable buildings are above their own reference: "
        f"{lead}. Each is read against its own country's pack, so these are not comparable "
        f"with one another — only with the standard each is held to."),
        "count": len(over), "data": over}


async def _route_limits(session, building_ids, org):
    """What the data route stops the detectors seeing — the rules that cannot arm, and why."""
    cov = await dc_svc.coverage(session, building_ids=building_ids, organization_id=org)
    rules = cov.get("rules") or []
    total = (cov.get("summary") or {}).get("buildings", 0)
    if not total:
        return {"answer": "There are no buildings in scope, so no rule could arm on one.",
                "count": 0, "data": rules}
    limited = [r for r in rules if r["armed"] < total]
    if not limited:
        return {"answer": (
            f"Nothing is limited: all {len(rules)} rules arm on every one of the {total} "
            f"buildings in scope."), "count": 0, "data": rules}
    limited.sort(key=lambda r: r["armed"])
    lead = "; ".join(
        f"{r['label']} on {r['armed']} of {total} ({r['needs']})" for r in limited[:3])
    return {"answer": (
        f"{len(limited)} of {len(rules)} rules cannot arm everywhere: {lead}. A rule that "
        f"cannot arm is shown as such rather than skipped quietly — the fix is a data route, "
        f"not a threshold."),
        "count": len(limited), "data": limited}


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _only_scoped(rows: list[dict], building_ids: list[UUID] | None) -> list[dict]:
    """list_buildings is organisation-scoped, not building-scoped, so narrow it here.

    None is unrestricted; an empty list is a caller allocated to nothing and must match no
    building rather than every one.
    """
    if building_ids is None:
        return rows
    allowed = {str(b) for b in building_ids}
    return [r for r in rows
            if str(r.get("building_id") or r.get("id") or r.get("key") or "") in allowed]


ENERGY_SKILLS = [
    Skill("markets_excess_cost", "Which markets drive the excess cost?",
          must=["market"], any_of=["excess", "cost", "drive", "worst", "spend", "most"],
          handler=_markets_excess, endpoint="/api/energy/buildings", page="energy"),
    Skill("worst_against_pack", "Which buildings are worst against their own pack?",
          must=["building"], any_of=["worst", "pack", "reference", "benchmark", "over",
                                     "above"],
          handler=_worst_against_pack, endpoint="/api/energy/buildings", page="energy"),
    Skill("data_route_limits", "Where does the data route limit what I can see?",
          any_of=["data route", "route", "limit", "coverage", "arm", "cannot see", "blind"],
          must=[], handler=_route_limits, endpoint="/api/energy/detection/coverage", page="energy"),
]
SKILLS.extend(ENERGY_SKILLS)


def suggestions(page: str | None = None) -> list[dict[str, str]]:
    """The chips a page shows, served rather than hard-coded in the frontend."""
    return [{"id": s.id, "question": s.question, "page": s.page}
            for s in SKILLS if page is None or s.page == page]


def match(question: str) -> tuple[Skill | None, float, list[tuple[Skill, float]]]:
    scored = sorted(((s, _score(question, s)) for s in SKILLS),
                    key=lambda p: p[1], reverse=True)
    near = [(s, c) for s, c in scored if c > 0][:3]
    best, conf = scored[0]
    return (best if conf >= CONFIDENCE_FLOOR else None), conf, near


async def ask(
    session: AsyncSession, *, question: str, building_ids: list[UUID] | None,
    organization_id: UUID | None, page: str | None = None,
) -> dict[str, Any]:
    """Answer a question about assets, from the caller's buildings only."""
    q = (question or "").strip()
    if not q:
        return {"ok": True, "understood": False, "question": q,
                "answer": "Ask me something about the assets or the energy records.",
                "can_answer": suggestions(page)}

    skill, confidence, near = match(q)
    if skill is None:
        return {
            "ok": True, "understood": False, "question": q,
            "confidence": round(confidence, 2),
            "answer": ("I cannot answer that from the asset records. I answer from what is on "
                       "record rather than guessing at what you meant, so here is what I can "
                       "be asked."),
            "closest": [{"id": s.id, "question": s.question, "confidence": round(c, 2)}
                        for s, c in near],
            "can_answer": suggestions(page),
        }
    try:
        out = await skill.handler(session, building_ids, organization_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ask.handler_failed", skill=skill.id, error=str(exc)[:250])
        return {"ok": False, "understood": True, "question": q, "intent": skill.id,
                "answer": "I understood the question but could not read the records for it.",
                "error": str(exc)[:200]}

    return {
        "ok": True, "understood": True, "question": q, "intent": skill.id,
        "matched": skill.question, "confidence": round(confidence, 2),
        "answer": out["answer"], "count": out.get("count"), "data": out.get("data", []),
        "source": {"endpoint": skill.endpoint, "scope": "your buildings only"},
        "followups": [{"id": s.id, "question": s.question}
                      for s in SKILLS if s.id != skill.id and s.page == skill.page][:3],
        "note": ("answered from the same records the page shows; every figure comes from the "
                 "endpoint named in source, narrowed to your buildings"),
    }
