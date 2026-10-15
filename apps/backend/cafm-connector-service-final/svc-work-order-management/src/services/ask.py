"""The Ask bar: a question in words, answered from records the caller is allowed to see.

Every answer here is composed from rows a named engine function returned. Nothing writes SQL
from the question, and nothing generates a figure — the sentence is assembled from the same
numbers the panels show, and `source` names the function and the endpoint that produced them,
so any answer can be checked against the screen it came from.

That is a deliberate choice rather than a shortcut. A question turned into SQL by a model can
quietly widen its own scope, and a figure written by a model is a figure nobody can trace. By
routing to functions that are already building-scoped and already tested, the Ask bar inherits
the boundary for free: it cannot answer about a building the caller is not allocated to,
because the function underneath it cannot read one.

Matching is by pattern, not by model. Every chip the page ships is matched exactly, and a
reasonable paraphrase of each is matched too. When nothing matches with confidence the honest
answer is given — what it cannot do, and the list of what it can — rather than a guess at the
nearest skill. An unasked-for answer to the wrong question is worse than no answer.

A model-based classifier could sit in front of this later and pick the skill; the skills
themselves would not change, because they are where the truthfulness lives.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from . import inspection_intelligence as ii
from . import maintenance as mx

log = get_logger(__name__)

#: Below this, the question is treated as not understood. Set where a single strong signal
#: ("statutory", "behind plan") is enough but a stray shared word is not.
CONFIDENCE_FLOOR = 0.45


def _n_of(n: int, singular: str, plural: str | None = None) -> str:
    """"1 asset" / "5 assets". These strings are read by people, so they have to read."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _is_are(n: int) -> str:
    return "is" if n == 1 else "are"


@dataclass
class Skill:
    """One question this service can answer, and the function that answers it."""
    id: str
    question: str                                   # the canonical phrasing, shown as a chip
    page: str                                       # which page offers it
    must: list[str] = field(default_factory=list)   # every one of these must appear
    any_of: list[str] = field(default_factory=list)  # at least one of these
    never: list[str] = field(default_factory=list)  # disqualifiers
    handler: Callable[..., Awaitable[dict[str, Any]]] | None = None
    endpoint: str = ""


def _n(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def _score(q: str, s: Skill) -> float:
    """How well this question matches this skill, between 0 and 1.

    Exact match on the canonical question is 1.0. Otherwise every required term must be
    present, and the score rises with how many of the optional terms are too.
    """
    text = _n(q)
    if text.strip() == _n(s.question).strip():
        return 1.0
    if any(re.search(rf"\b{re.escape(w)}", text) for w in s.never):
        return 0.0
    if not all(re.search(rf"\b{re.escape(w)}", text) for w in s.must):
        return 0.0
    if not s.any_of:
        return 0.75 if s.must else 0.0
    hits = sum(1 for w in s.any_of if re.search(rf"\b{re.escape(w)}", text))
    if not hits:
        return 0.0
    return min(0.55 + 0.15 * hits, 0.95)


# ── the skills ───────────────────────────────────────────────────────────────────────

async def _statutory(session, building_ids, **_):
    o = await mx.overview(session, building_ids=building_ids)
    card = o["cards"]["statutory"]
    rows = card.get("decisions") or []
    if not card["value"]:
        return {"answer": (
            f"None of the {card['of_decisions']} decisions owed is statutory: no certificate "
            f"behind them has lapsed or falls due inside {card['window_days']} days."),
            "count": 0, "data": []}
    lapsed = sum(1 for d in rows
                 if (d.get("statutory_certificate") or {}).get("lapsed"))
    lead = ", ".join(
        f"{d.get('work_order') or d.get('queue_item') or 'unraised'}"
        f" ({(d.get('statutory_certificate') or {}).get('certificate_type') or 'certificate'}"
        f" expires {(d.get('statutory_certificate') or {}).get('expires')})"
        for d in rows[:3])
    return {"answer": (
        f"{card['value']} of the {card['of_decisions']} decisions owed are statutory — the "
        f"certificate behind them has lapsed or falls due inside {card['window_days']} days"
        + (f", {lapsed} already lapsed" if lapsed else "") + f". {lead}."),
        "count": card["value"], "data": rows}


async def _unconverted(session, building_ids, **_):
    r = await ii.unconverted_recommendations(session, building_ids=building_ids, limit=50)
    if not r.get("answerable"):
        return {"answer": f"I cannot answer that here: {r['reason']}.", "count": None,
                "data": [], "answerable": False}
    if not r["count"]:
        return {"answer": "Every recommendation on record became a work order.",
                "count": 0, "data": []}
    flagged = r.get("now_flagged_by_energy") or 0
    names = ", ".join(x["asset_name"] for x in r["reports"][:4] if x.get("asset_name"))
    return {"answer": (
        f"{_n_of(r['count'], 'recommendation')} "
        f"{'was' if r['count'] == 1 else 'were'} never converted to orders"
        + (f", and {flagged} of them are on assets energy has since flagged — the inspector "
           f"saw it first" if flagged else "")
        + (f". {names}." if names else ".")),
        "count": r["count"], "data": r["reports"]}


async def _ppm_behind(session, building_ids, **_):
    h = await mx.ppm_health_by_contract(session, building_ids=building_ids, year_to_date=True)
    behind = [c for c in h.get("contracts", []) if c["state"] == "behind plan"]
    if not h.get("contracts"):
        return {"answer": "No PPM visits are on record for your buildings this year.",
                "count": 0, "data": []}
    if not behind:
        return {"answer": (
            f"No contract is behind plan. {h['summary']['contracts']} contracts are running at "
            f"{h['summary']['completion_pct']}% of plan year to date."),
            "count": 0, "data": h["contracts"]}
    lead = "; ".join(
        f"{c['contract']} ({c['visits_to_plan']['done']} of {c['visits_to_plan']['plan']} "
        f"visits, {c['missed']} missed)" for c in behind[:3])
    return {"answer": (
        f"{len(behind)} of {_n_of(h['summary']['contracts'], 'contract')} "
        f"{_is_are(len(behind))} behind plan year to date: {lead}."),
        "count": len(behind), "data": behind}


async def _warranty(session, building_ids, **_):
    r = await ii.warranted_findings(session, building_ids=building_ids, limit=50)
    if not r.get("answerable"):
        return {"answer": f"I cannot answer that here: {r['reason']}.", "count": None,
                "data": [], "answerable": False}
    if not r["count"]:
        return {"answer": ("No finding on record sits on a part that is still under warranty."),
                "count": 0, "data": []}
    money = (f" — {r.get('currency') or ''}{r['claimable_value']:,.0f} of invoiced work is "
             f"claimable" if r.get("claimable_value") else "")
    return {"answer": (f"{_n_of(r['count'], 'finding')} {_is_are(r['count'])} on parts "
                       f"still under warranty{money}."),
            "count": r["count"], "data": r["findings"]}


async def _corroborated(session, building_ids, **_):
    r = await ii.corroborated_anomalies(session, building_ids=building_ids, limit=50)
    if not r.get("answerable"):
        return {"answer": f"I cannot answer that here: {r['reason']}.", "count": None,
                "data": [], "answerable": False}
    if not r.get("open_anomalies"):
        return {"answer": "No open anomaly is attributed to an asset, so none can be "
                          "corroborated by a report.", "count": 0, "data": []}
    if not r["count"]:
        return {"answer": (
            f"None of the {r['open_anomalies']} open anomalies was named by a report written "
            f"before it was detected."), "count": 0, "data": r["anomalies"]}
    return {"answer": (
        f"{r['count']} of {r['open_anomalies']} open anomalies were already named by a report "
        f"written before detection. {r['method']}."),
        "count": r["count"], "data": [a for a in r["anomalies"] if a["corroborated"]]}


async def _worst_condition(session, building_ids, **_):
    r = await ii.poorly_graded(session, building_ids=building_ids, limit=50)
    if not r.get("answerable"):
        return {"answer": f"I cannot answer that here: {r['reason']}.", "count": None,
                "data": [], "answerable": False}
    if not r["count"]:
        return {"answer": "No asset has been graded poor by an inspector.",
                "count": 0, "data": []}
    names = ", ".join(a["asset_name"] for a in r["assets"][:4] if a.get("asset_name"))
    return {"answer": f"{r['headline']}. {names}.", "count": r["count"], "data": r["assets"]}


async def _vendor_reports(session, building_ids, **_):
    r = await ii.reports_by_vendor(session, building_ids=building_ids, limit=50)
    if not r.get("answerable"):
        return {"answer": f"I cannot answer that here: {r['reason']}.", "count": None,
                "data": [], "answerable": False}
    if not r.get("fewest"):
        return {"answer": ("No vendor has completed work to file reports against, so none can "
                           "be ranked on reporting."), "count": 0, "data": r["vendors"]}
    f, m = r["fewest"], r["most"]
    lead = (f"{f['vendor']} files {f['reports']} reports against {f['completed_orders']} "
            f"completed orders ({f['reports_per_completed_order']} each)")
    # With one rankable vendor there is no fewest and no most, only that one — saying both
    # about the same row reads as two findings where there is only one.
    if r["rankable"] < 2:
        return {"answer": (
            f"Only one vendor has completed work to be ranked on, so there is nothing to "
            f"compare: {lead}. Ranked on reports per completed order, not the raw count."),
            "count": r["rankable"], "data": r["vendors"]}
    return {"answer": (
        f"{f['vendor']} files the fewest: {f['reports']} reports against "
        f"{f['completed_orders']} completed orders ({f['reports_per_completed_order']} each). "
        f"{m['vendor']} files the most at {m['reports_per_completed_order']} each. Ranked on "
        f"reports per completed order, not the raw count."),
        "count": r["rankable"], "data": r["vendors"]}


async def _decisions(session, building_ids, **_):
    d = await mx.decisions(session, building_ids=building_ids, limit=200)
    bs = d.get("by_state", {})
    if not d.get("total"):
        return {"answer": "Nothing is waiting on a decision in your buildings.",
                "count": 0, "data": []}
    parts = ", ".join(f"{v} {k.lower()}" for k, v in bs.items() if v)
    return {"answer": (f"{_n_of(d['total'], 'decision')} {_is_are(d['total'])} owed: "
                       f"{parts}."),
            "count": d["total"], "data": d["decisions"][:50]}


async def _blocked(session, building_ids, **_):
    d = await mx.decisions(session, building_ids=building_ids, state="Blocked", limit=200)
    if not d["count"]:
        return {"answer": "No decision is blocked.", "count": 0, "data": []}
    lead = "; ".join(f"{x.get('work_order')} on {x.get('asset') or x.get('building')}"
                     f" ({x.get('trigger')})" for x in d["decisions"][:3])
    return {"answer": (f"{_n_of(d['count'], 'decision')} {_is_are(d['count'])} blocked: "
                       f"{lead}."),
            "count": d["count"], "data": d["decisions"]}


SKILLS: list[Skill] = [
    Skill("statutory_decisions", "Which decisions are statutory?", "maintenance",
          must=["statutory"], handler=_statutory,
          endpoint="/api/maintenance/overview"),
    Skill("unconverted_recommendations",
          "Which recommendations were never converted to orders?", "maintenance",
          must=["recommendation"], any_of=["convert", "converted", "never", "order", "orders",
                                           "turned", "unconverted", "followed"],
          handler=_unconverted,
          endpoint="/api/maintenance/inspection-intelligence/unconverted-recommendations"),
    Skill("ppm_behind_plan", "Which PPM contracts are behind plan?", "maintenance",
          must=["ppm"], any_of=["behind", "plan", "contract", "contracts", "late", "missed"],
          handler=_ppm_behind, endpoint="/api/maintenance/ppm/contracts"),
    Skill("warranty", "What is under warranty?", "inspection",
          must=["warranty"], any_of=["under", "claim", "claimable", "part", "parts", "what"],
          handler=_warranty,
          endpoint="/api/maintenance/inspection-intelligence/warranted-findings"),
    Skill("corroborated", "Which reports confirm the energy anomalies?", "inspection",
          any_of=["confirm", "corroborate", "corroborated", "support", "already"],
          must=["anomal"], handler=_corroborated,
          endpoint="/api/maintenance/inspection-intelligence/corroborated-anomalies"),
    Skill("worst_condition", "Which assets are in the worst condition?", "inspection",
          must=["condition"], any_of=["worst", "poor", "bad", "graded", "grade", "asset",
                                      "assets"],
          handler=_worst_condition,
          endpoint="/api/maintenance/inspection-intelligence/poorly-graded"),
    Skill("vendor_reports", "Which vendor files the fewest reports?", "inspection",
          must=["vendor"], any_of=["report", "reports", "fewest", "least", "files", "filing"],
          handler=_vendor_reports, endpoint="/api/maintenance/inspection-intelligence"),
    Skill("decisions_owed", "What decisions are owed?", "maintenance",
          must=["decision"], any_of=["owed", "owe", "outstanding", "waiting", "many", "what"],
          never=["statutory"], handler=_decisions, endpoint="/api/maintenance/decisions"),
    Skill("decisions_blocked", "Which decisions are blocked?", "maintenance",
          must=["blocked"], any_of=["decision", "decisions", "order", "orders", "which"],
          handler=_blocked, endpoint="/api/maintenance/decisions?state=Blocked"),
]


def suggestions(page: str | None = None) -> list[dict[str, str]]:
    """The chips a page shows, served rather than hard-coded in the frontend."""
    return [{"id": s.id, "question": s.question, "page": s.page}
            for s in SKILLS if page is None or s.page == page]


def match(question: str) -> tuple[Skill | None, float, list[tuple[Skill, float]]]:
    """The best skill for this question, its confidence, and what else came close."""
    scored = sorted(((s, _score(question, s)) for s in SKILLS),
                    key=lambda p: p[1], reverse=True)
    near = [(s, c) for s, c in scored if c > 0][:3]
    best, conf = scored[0]
    return (best if conf >= CONFIDENCE_FLOOR else None), conf, near


async def ask(
    session: AsyncSession, *, question: str, building_ids: list[UUID] | None,
    page: str | None = None,
) -> dict[str, Any]:
    """Answer a question about maintenance, from the caller's buildings only."""
    q = (question or "").strip()
    if not q:
        return {"ok": True, "understood": False, "question": q,
                "answer": "Ask me something about the maintenance records.",
                "can_answer": suggestions(page)}

    skill, confidence, near = match(q)
    if skill is None:
        return {
            "ok": True, "understood": False, "question": q,
            "confidence": round(confidence, 2),
            "answer": (
                "I cannot answer that from the maintenance records. I answer from what is on "
                "record rather than by guessing at what you meant, so here is what I can be "
                "asked."),
            "closest": [{"id": s.id, "question": s.question, "confidence": round(c, 2)}
                        for s, c in near],
            "can_answer": suggestions(page),
        }

    try:
        out = await skill.handler(session, building_ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("ask.handler_failed", skill=skill.id, error=str(exc)[:250])
        return {"ok": False, "understood": True, "question": q, "intent": skill.id,
                "answer": "I understood the question but could not read the records for it.",
                "error": str(exc)[:200]}

    log.debug("ask.answered", skill=skill.id, confidence=confidence,
              count=out.get("count"))
    return {
        "ok": True,
        "understood": True,
        "question": q,
        "intent": skill.id,
        "matched": skill.question,
        "confidence": round(confidence, 2),
        "answer": out["answer"],
        "count": out.get("count"),
        "data": out.get("data", []),
        "answerable": out.get("answerable", True),
        "source": {"endpoint": skill.endpoint, "scope": "your buildings only"},
        "followups": [{"id": s.id, "question": s.question}
                      for s in SKILLS if s.id != skill.id and s.page == skill.page][:3],
        "note": ("answered from the same records the panels show; every figure here comes "
                 "from the endpoint named in source, narrowed to your buildings"),
    }
