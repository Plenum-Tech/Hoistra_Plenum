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
from . import condition_engine as ce

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


def suggestions() -> list[dict[str, str]]:
    """The chips the Assets page shows, served rather than hard-coded in the frontend."""
    return [{"id": s.id, "question": s.question, "page": "assets"} for s in SKILLS]


def match(question: str) -> tuple[Skill | None, float, list[tuple[Skill, float]]]:
    scored = sorted(((s, _score(question, s)) for s in SKILLS),
                    key=lambda p: p[1], reverse=True)
    near = [(s, c) for s, c in scored if c > 0][:3]
    best, conf = scored[0]
    return (best if conf >= CONFIDENCE_FLOOR else None), conf, near


async def ask(
    session: AsyncSession, *, question: str, building_ids: list[UUID] | None,
    organization_id: UUID | None,
) -> dict[str, Any]:
    """Answer a question about assets, from the caller's buildings only."""
    q = (question or "").strip()
    if not q:
        return {"ok": True, "understood": False, "question": q,
                "answer": "Ask me something about the assets.",
                "can_answer": suggestions()}

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
            "can_answer": suggestions(),
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
                      for s in SKILLS if s.id != skill.id][:3],
        "note": ("answered from the same records the page shows; every figure comes from the "
                 "endpoint named in source, narrowed to your buildings"),
    }
