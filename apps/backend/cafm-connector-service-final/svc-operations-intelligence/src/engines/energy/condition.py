"""Feature C — asset condition 1–5 from inspection text / UDR vector layer + cross-ref."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...models.energy import (
    AssetConditionScore,
    EnergyMeter,
    EnergyRecommendation,
    MeterReading,
)
from ...shared.approvals import enqueue_approval, write_audit

log = get_logger(__name__)

LABELS = {
    1: "Critical — immediate remediation",
    2: "Poor — significant defects",
    3: "Fair — manageable wear",
    4: "Good — minor observations",
    5: "Excellent — no material defects",
}


def deduce_condition_from_text(report_text: str) -> tuple[int, str, dict[str, Any]]:
    """Heuristic keyword deduction of condition score 1–5 from inspection narrative."""
    t = (report_text or "").lower()
    signals: list[str] = []
    score = 4

    critical = ["failed", "critical", "unsafe", "immediate", "condemned", "life safety"]
    poor = ["severe", "major defect", "urgent", "replacement recommended", "beyond repair"]
    fair = ["moderate", "deteriorat", "attention required", "wear"]
    good = ["minor", "satisfactory", "within tolerance", "acceptable"]
    excellent = ["excellent", "as new", "no defects", "fully compliant"]

    if any(k in t for k in critical):
        score = 1
        signals = [k for k in critical if k in t][:5]
    elif any(k in t for k in poor):
        score = 2
        signals = [k for k in poor if k in t][:5]
    elif any(k in t for k in excellent):
        score = 5
        signals = [k for k in excellent if k in t][:5]
    elif any(k in t for k in good) and not any(k in t for k in fair):
        score = 4
        signals = [k for k in good if k in t][:5]
    elif any(k in t for k in fair):
        score = 3
        signals = [k for k in fair if k in t][:5]

    if re.search(r"(vibration|temp(erature)?|amp)\s*[:>]?\s*\d+", t) and score > 2:
        if "high" in t or "exceed" in t:
            score = min(score, 2)
            signals.append("elevated_measurement")

    return score, LABELS[score], {"signals": signals, "method": "heuristic"}


async def _llm_condition_score(report_text: str) -> tuple[int, str, dict[str, Any]] | None:
    """Optional Claude Haiku score when ANTHROPIC_API_KEY is set."""
    key = (settings.anthropic_api_key or "").strip()
    if not key or not (report_text or "").strip():
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=key)
        msg = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Score asset condition 1–5 from this inspection excerpt. "
                        "1=critical unsafe, 2=poor major defects, 3=fair, 4=good, 5=excellent. "
                        "Return JSON only: {\"score\": int, \"label\": str, \"signals\": [str]}.\n\n"
                        f"{report_text[:6000]}"
                    ),
                }
            ],
        )
        raw = msg.content[0].text if msg.content else ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        score = int(data.get("score", 0))
        if score < 1 or score > 5:
            return None
        label = str(data.get("label") or LABELS[score])
        return score, label, {
            "signals": data.get("signals") or [],
            "method": "llm_haiku",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.condition.llm_failed", error=str(exc)[:200])
        return None


async def fetch_inspection_text_from_vectors(
    session: AsyncSession,
    *,
    asset_id: UUID,
    asset_code: str | None = None,
) -> tuple[str, list[str]]:
    """
    Read inspection evidence from Doc RAG (vector layer) with SQL chunk fallback.
    Returns (combined_text, source_refs).
    """
    query = (
        f"Inspection findings defects measurements observations for asset "
        f"{asset_code or asset_id}"
    )
    sources: list[str] = []
    chunks: list[str] = []

    base = settings.doc_rag_base_url.rstrip("/")
    paths = ["/doc-rag/rag/query", "/rag/query", "/api/query"]
    for path in paths:
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{base}{path}",
                    json={"query": query, "top_k": 8},
                )
                if resp.status_code >= 400:
                    continue
                body = resp.json()
                answer = body.get("answer") or ""
                if answer:
                    chunks.append(str(answer))
                for c in body.get("citations") or body.get("retrieved_chunks") or body.get("sources") or []:
                    if isinstance(c, dict):
                        txt = c.get("text_content") or c.get("text") or c.get("excerpt") or ""
                        if txt:
                            chunks.append(str(txt))
                        ref = c.get("file_name") or c.get("document_id") or c.get("source") or ""
                        if ref:
                            sources.append(str(ref))
                    elif isinstance(c, str):
                        chunks.append(c)
                if chunks:
                    break
        except Exception as exc:  # noqa: BLE001
            log.info("energy.condition.doc_rag_miss", path=path, error=str(exc)[:120])

    if not chunks:
        # UDR vector / chunk table fallback on same Postgres
        like = f"%{asset_code or str(asset_id)[:8]}%"
        try:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT text_content, source_filename
                        FROM plenum_cafm.document_chunks
                        WHERE text_content ILIKE :like
                           OR normalized_text ILIKE :like
                        ORDER BY created_at DESC NULLS LAST
                        LIMIT 20
                        """
                    ),
                    {"like": like},
                )
            ).fetchall()
            for r in rows:
                if r[0]:
                    chunks.append(str(r[0]))
                if r[1]:
                    sources.append(str(r[1]))
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.condition.chunk_sql_failed", error=str(exc)[:200])

    combined = "\n\n".join(chunks)[:12000]
    return combined, list(dict.fromkeys(sources))[:10]


async def deduce_and_write_condition(
    session: AsyncSession,
    *,
    asset_id: UUID,
    report_text: str,
    source_report_ref: str | None = None,
    asset_code: str | None = None,
    organization_id: UUID | None = None,
    write_to_asset: bool = True,
    auto_cross_ref: bool = True,
) -> dict[str, Any]:
    llm = await _llm_condition_score(report_text)
    if llm:
        score, label, meta = llm
    else:
        score, label, meta = deduce_condition_from_text(report_text)

    provenance = {
        "source_report_ref": source_report_ref,
        "llm_deduced_label": label,
        **meta,
    }
    q = select(AssetConditionScore).where(AssetConditionScore.asset_id == asset_id)
    row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        row = AssetConditionScore(
            id=uuid4(),
            organization_id=organization_id,
            asset_id=asset_id,
            asset_code=asset_code,
            condition_score=score,
            llm_label=label,
            provenance_json=provenance,
            source_report_ref=source_report_ref,
        )
        session.add(row)
    else:
        row.condition_score = score
        row.llm_label = label
        row.provenance_json = provenance
        row.source_report_ref = source_report_ref
        row.deduced_at = datetime.now(timezone.utc)
        row.asset_code = asset_code or row.asset_code

    if write_to_asset:
        try:
            result = await session.execute(
                text(
                    """
                    UPDATE plenum_cafm.assets
                    SET condition_score = :score,
                        condition_provenance = CAST(:prov AS jsonb),
                        condition_updated_at = now()
                    WHERE id = :aid
                    """
                ),
                {
                    "score": score,
                    "prov": json.dumps(provenance),
                    "aid": str(asset_id),
                },
            )
            # An UPDATE that matches nothing is not a write. The asset may simply not
            # exist — condition can be deduced from a report naming an asset the register
            # has never seen — and recording written_to_asset=True in that case claims the
            # score reached the Asset entity when it went nowhere. The score is still kept
            # here with its provenance; only the claim about the asset is corrected.
            row.written_to_asset = bool(result.rowcount)
            if not result.rowcount:
                log.warning(
                    "energy.condition.asset_not_found",
                    asset_id=str(asset_id),
                    asset_code=asset_code,
                    score=score,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.condition.asset_write_failed", error=str(exc)[:200])
            row.written_to_asset = False

    await write_audit(
        session,
        actor="system",
        action_type="energy.condition.deduce",
        source_feature="C",
        organization_id=organization_id,
        output_payload={"asset_id": str(asset_id), "score": score, "label": label},
    )
    await session.commit()

    result: dict[str, Any] = {
        "ok": True,
        "asset_id": str(asset_id),
        "condition_score": score,
        "llm_label": label,
        "provenance": provenance,
        "written_to_asset": row.written_to_asset,
        "id": str(row.id),
    }

    if auto_cross_ref and score <= 2:
        try:
            xref = await cross_reference_condition_consumption(
                session,
                asset_id=asset_id,
                organization_id=organization_id,
            )
            result["cross_ref"] = xref
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.condition.auto_cross_ref_failed", error=str(exc)[:200])
            result["cross_ref"] = {"ok": False, "error": str(exc)[:200]}

    return result


async def deduce_condition_from_vector_layer(
    session: AsyncSession,
    *,
    asset_id: UUID,
    asset_code: str | None = None,
    organization_id: UUID | None = None,
    write_to_asset: bool = True,
    auto_cross_ref: bool = True,
) -> dict[str, Any]:
    """PRD path: read UDR/Doc RAG inspection vectors → condition score + provenance."""
    text_body, source_refs = await fetch_inspection_text_from_vectors(
        session, asset_id=asset_id, asset_code=asset_code
    )
    if not text_body.strip():
        return {
            "ok": False,
            "error": "no_inspection_vector_evidence",
            "asset_id": str(asset_id),
            "hint": "Index inspection reports in Doc RAG or pass report_text explicitly.",
        }
    source_ref = "; ".join(source_refs) if source_refs else f"vector:{asset_code or asset_id}"
    result = await deduce_and_write_condition(
        session,
        asset_id=asset_id,
        report_text=text_body,
        source_report_ref=source_ref,
        asset_code=asset_code,
        organization_id=organization_id,
        write_to_asset=write_to_asset,
        auto_cross_ref=auto_cross_ref,
    )
    result["vector_sources"] = source_refs
    result["evidence_chars"] = len(text_body)
    return result


# Above this, the benchmark is wrong rather than the asset. A chiller does not draw four
# orders of magnitude more than its type; a benchmark entered per-hour instead of per-year
# does exactly that.
_IMPLAUSIBLE_EXCESS_PCT = 2000.0


def repair_vs_replace_costs(
    *,
    condition_score: int,
    excess_pct: float,
) -> tuple[float, float, str]:
    """Indicative cost band — PM-facing comparison, not a quote.

    The replacement figure scales with how far consumption sits above benchmark, and that
    input is not bounded by anything upstream: a mis-scaled benchmark produced an excess of
    1,802,190% here, and this returned £144,186,019 as a replacement cost. It reached a PM
    queue only because a NUMERIC(14,2) column happened to reject it. A cost model that can
    return any number given bad input is not a cost model, so the scaling term is capped —
    beyond 200% over benchmark the asset is being replaced regardless, and the extra
    percentage tells you nothing more about the price.
    """
    base_repair = 2500 + (3 - min(condition_score, 3)) * 1500
    scaled_excess = min(max(0.0, excess_pct - 15), 200.0)
    base_replace = 12000 + scaled_excess * 80
    if condition_score <= 1 or excess_pct > 40:
        rec_type = "replacement"
    elif condition_score <= 2:
        rec_type = "remediation"
    else:
        rec_type = "preventive"
    return round(base_repair, 2), round(base_replace, 2), rec_type


async def cross_reference_condition_consumption(
    session: AsyncSession,
    *,
    asset_id: UUID,
    organization_id: UUID | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """
    When condition ≤2 AND sub-meter consumption >15% above asset-type benchmark
    → preventive/remediation/replacement recommendation (queue + insight, NO WO).
    """
    cond = (
        await session.execute(
            select(AssetConditionScore).where(AssetConditionScore.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    if not cond or cond.condition_score > 2:
        return {
            "ok": True,
            "triggered": False,
            "reason": "condition_above_threshold_or_missing",
            "condition_score": cond.condition_score if cond else None,
        }

    meter = (
        await session.execute(
            select(EnergyMeter).where(
                EnergyMeter.asset_id == asset_id,
                EnergyMeter.is_sub_meter.is_(True),
                EnergyMeter.active.is_(True),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if not meter or not meter.asset_type_benchmark_kwh:
        return {
            "ok": True,
            "triggered": False,
            "reason": "no_sub_meter_or_benchmark",
            "condition_score": cond.condition_score,
        }

    from datetime import timedelta

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(MeterReading.consumption_kwh), 0)).where(
                MeterReading.meter_id == meter.id,
                MeterReading.reading_at >= start,
                MeterReading.reading_at <= end,
            )
        )
    ).scalar_one()
    actual = float(total or 0)
    bench_annual = float(meter.asset_type_benchmark_kwh)
    bench_period = bench_annual * (days / 365.0)
    if bench_period <= 0:
        return {"ok": False, "error": "invalid_benchmark"}

    pct = round(100.0 * (actual - bench_period) / bench_period, 2)

    # asset_type_benchmark_kwh is an ANNUAL figure, prorated above. A benchmark entered in
    # per-hour or per-half-hour terms is off by three or four orders of magnitude and
    # produces a percentage in the millions — which then flows into the cost model and out
    # to a PM as a recommendation. An excess this large is not a finding about the asset,
    # it is a statement that the benchmark is wrong, so say that instead of costing it.
    if pct > _IMPLAUSIBLE_EXCESS_PCT:
        log.warning(
            "energy.cross_ref.implausible_benchmark",
            asset_id=str(asset_id),
            excess_pct=pct,
            benchmark_annual_kwh=bench_annual,
            actual_kwh=actual,
        )
        return {
            "ok": False,
            "error": "implausible_benchmark",
            "message": (
                f"Consumption computes to {pct:,.0f}% above benchmark, which means the "
                f"asset-type benchmark ({bench_annual} kWh/year) is wrong rather than the "
                f"asset. No recommendation raised."
            ),
            "excess_pct": pct,
            "benchmark_annual_kwh": bench_annual,
            "actual_kwh": actual,
        }
    if pct <= 15:
        return {
            "ok": True,
            "triggered": False,
            "reason": "consumption_within_15pct",
            "condition_score": cond.condition_score,
            "consumption_vs_benchmark_pct": pct,
        }

    repair, replace, rec_type = repair_vs_replace_costs(
        condition_score=cond.condition_score, excess_pct=pct
    )
    summary = (
        f"Asset {cond.asset_code or asset_id}: condition {cond.condition_score}/5 "
        f"({cond.llm_label}); consumption {pct}% above type benchmark. "
        f"Recommend {rec_type}. Repair ≈£{repair:,.0f} vs replace ≈£{replace:,.0f}. "
        f"No work order created — queue/dashboard insight only."
    )
    rec = EnergyRecommendation(
        id=uuid4(),
        organization_id=organization_id or cond.organization_id,
        asset_id=asset_id,
        site_id=meter.site_id,
        recommendation_type=rec_type,
        condition_score=cond.condition_score,
        consumption_vs_benchmark_pct=Decimal(str(pct)),
        repair_cost_gbp=Decimal(str(repair)),
        replace_cost_gbp=Decimal(str(replace)),
        summary=summary,
        detail_json={
            "actual_kwh": actual,
            "benchmark_period_kwh": bench_period,
            "days": days,
            "no_work_order": True,
        },
        status="open",
    )
    session.add(rec)
    await session.flush()
    item = await enqueue_approval(
        session,
        source_feature="C",
        item_type="energy_remediation_recommendation",
        summary=summary[:500],
        severity="high",
        payload={
            "recommendation_id": str(rec.id),
            "asset_id": str(asset_id),
            "recommendation_type": rec_type,
            "repair_cost_gbp": repair,
            "replace_cost_gbp": replace,
            "creates_work_order": False,
        },
        organization_id=organization_id or cond.organization_id,
        related_entity_type="energy_recommendation",
        related_entity_id=rec.id,
    )
    rec.queue_item_id = item.id
    await session.commit()
    return {
        "ok": True,
        "triggered": True,
        "recommendation_id": str(rec.id),
        "recommendation_type": rec_type,
        "condition_score": cond.condition_score,
        "consumption_vs_benchmark_pct": pct,
        "repair_cost_gbp": repair,
        "replace_cost_gbp": replace,
        "queue_item_id": str(item.id),
        "creates_work_order": False,
        "summary": summary,
    }
