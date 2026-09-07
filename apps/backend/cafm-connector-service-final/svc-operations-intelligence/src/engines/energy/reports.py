"""Feature C — monthly energy report + PDF export stub."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import (
    EnergyAnomaly,
    EnergyMeter,
    EnergyMonthlyReport,
    EnergyRecommendation,
    EuiSnapshot,
)
from ...shared.approvals import write_audit
from .eui import compute_site_eui

log = get_logger(__name__)


def _render_pdf_text(report: dict[str, Any], out_path: Path) -> str:
    """Structured multi-section PDF via reportlab; falls back to .txt."""
    title = report.get("title") or "Monthly Energy Report"
    sections: list[dict[str, Any]] = list(report.get("sections") or [])
    lines: list[str] = list(report.get("lines") or [])

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        doc = SimpleDocTemplate(
            str(out_path),
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle(
            "EnergyH1",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#064e3b"),
            spaceAfter=8,
        )
        h2 = ParagraphStyle(
            "EnergyH2",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#065f46"),
            spaceBefore=10,
            spaceAfter=4,
        )
        body = ParagraphStyle(
            "EnergyBody",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1e293b"),
        )
        story: list[Any] = [Paragraph(str(title), h1)]

        for sec in sections:
            story.append(Paragraph(str(sec.get("heading") or "Section"), h2))
            for line in sec.get("lines") or []:
                story.append(Paragraph(str(line).replace("\n", "<br/>"), body))
                story.append(Spacer(1, 2))
            table_rows = sec.get("table")
            if table_rows:
                t = Table(table_rows, hAlign="LEFT")
                t.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d1fae5")),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#a7f3d0")),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0fdf4")]),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ]
                    )
                )
                story.append(t)
                story.append(Spacer(1, 6))

        if not sections:
            for line in lines:
                story.append(Paragraph(str(line), body))
                story.append(Spacer(1, 2))

        story.append(Spacer(1, 12))
        story.append(
            Paragraph(
                "Notification and dashboard only — no work orders created (Phase 2 scope).",
                ParagraphStyle("Foot", parent=body, textColor=colors.HexColor("#64748b"), fontSize=8),
            )
        )
        doc.build(story)
        return str(out_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.report.pdf_fallback", error=str(exc)[:200])
        # Minimal canvas fallback
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            c = canvas.Canvas(str(out_path), pagesize=A4)
            width, height = A4
            y = height - 50
            c.setFont("Helvetica-Bold", 14)
            c.drawString(40, y, str(title)[:100])
            y -= 24
            c.setFont("Helvetica", 10)
            for line in lines:
                if y < 60:
                    c.showPage()
                    y = height - 50
                    c.setFont("Helvetica", 10)
                c.drawString(40, y, str(line)[:110])
                y -= 14
            c.save()
            return str(out_path)
        except Exception as exc2:  # noqa: BLE001
            log.warning("energy.report.txt_fallback", error=str(exc2)[:200])
            txt = out_path.with_suffix(".txt")
            txt.write_text("\n".join(lines), encoding="utf-8")
            return str(txt)


async def generate_monthly_energy_report(
    session: AsyncSession,
    *,
    site_id: UUID,
    report_month: date | None = None,
    organization_id: UUID | None = None,
    export_pdf: bool = True,
) -> dict[str, Any]:
    """1st-of-month job for preceding month → Energy Saved Space."""
    if report_month is None:
        today = date.today()
        first = date(today.year, today.month, 1)
        report_month = (first - timedelta(days=1)).replace(day=1)

    # month bounds
    if report_month.month == 12:
        period_end = date(report_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        period_end = date(report_month.year, report_month.month + 1, 1) - timedelta(days=1)

    eui = await compute_site_eui(
        session,
        site_id=site_id,
        period_start=report_month,
        period_end=period_end,
        meter_type="electricity",
        organization_id=organization_id,
    )

    # EUI trend — the last 6 MONTHS, one point each. Every report run writes a fresh
    # snapshot for the month it covers, so regenerating August three times put August on the
    # trend line three times and pushed real earlier months off the end of the chart. Take
    # the newest snapshot per period and keep six distinct periods.
    snap_rows = list(
        (
            await session.execute(
                select(EuiSnapshot)
                .where(EuiSnapshot.site_id == site_id)
                .order_by(EuiSnapshot.period_start.desc(), EuiSnapshot.id.desc())
                .limit(60)
            )
        ).scalars().all()
    )
    snaps: list[EuiSnapshot] = []
    seen_periods: set[Any] = set()
    for s in snap_rows:
        if s.period_start in seen_periods:
            continue
        seen_periods.add(s.period_start)
        snaps.append(s)
        if len(snaps) >= 6:
            break
    trend = [
        {
            "period_start": s.period_start.isoformat(),
            "eui": float(s.eui_kwh_per_m2),
            "benchmark": float(s.benchmark_kwh_per_m2) if s.benchmark_kwh_per_m2 else None,
            "deviation_pct": float(s.deviation_pct) if s.deviation_pct is not None else None,
            "financial_gbp": float(s.financial_gbp) if s.financial_gbp is not None else None,
        }
        for s in reversed(snaps)
    ]

    # Anomalies ranked by annualised cost
    month_start = datetime(report_month.year, report_month.month, 1, tzinfo=timezone.utc)
    month_end = datetime(period_end.year, period_end.month, period_end.day, 23, 59, tzinfo=timezone.utc)
    anomalies = list(
        (
            await session.execute(
                select(EnergyAnomaly)
                .where(
                    EnergyAnomaly.site_id == site_id,
                    EnergyAnomaly.detected_at >= month_start,
                    EnergyAnomaly.detected_at <= month_end,
                )
                .order_by(EnergyAnomaly.financial_gbp.desc().nullslast())
                .limit(50)
            )
        ).scalars().all()
    )
    # One row per EVENT, not per detection. Each scan re-detects a spike that is still in
    # the data, so a single weekend appeared five times and its cost was counted five times
    # — the August report read £47,828 against a true £17,110. Group by what identifies the
    # event; window bounds are not always recorded, so fall back to the meter, type and the
    # numbers the detector produced, which are identical across re-detections of one event.
    def _event_key(a: EnergyAnomaly) -> tuple[Any, ...]:
        # BOTH bounds, not either: these rows carry a NULL window_start and a window_end set
        # to the moment the scan ran, so keying on a half-known window makes every re-detection
        # look like a new event — which is what it did on the first attempt at this fix.
        if a.window_start is not None and a.window_end is not None:
            return (str(a.meter_id), a.anomaly_type, a.window_start, a.window_end)
        return (
            str(a.meter_id),
            a.anomaly_type,
            str(a.metric_pct),
            str(a.financial_gbp),
        )

    events: dict[tuple[Any, ...], EnergyAnomaly] = {}
    judged_expected: set[tuple[Any, ...]] = set()
    for a in anomalies:
        key = _event_key(a)
        # "expected" is a PM decision with a written reason attached. Once they have judged
        # one detection of an event, that judgement covers the event — a duplicate row still
        # marked "open" must not resurrect it as a cost to recover.
        if str(a.status or "").lower() == "expected":
            judged_expected.add(key)
        kept = events.get(key)
        if kept is None or (a.detected_at and kept.detected_at and a.detected_at > kept.detected_at):
            events[key] = a

    ranked = [
        {
            "id": str(a.id),
            "anomaly_type": a.anomaly_type,
            "financial_gbp": float(a.financial_gbp) if a.financial_gbp is not None else 0,
            "metric_pct": float(a.metric_pct) if a.metric_pct is not None else None,
            "status": "expected" if key in judged_expected else a.status,
            "detections": sum(1 for x in anomalies if _event_key(x) == key),
        }
        for key, a in sorted(
            events.items(),
            key=lambda kv: float(kv[1].financial_gbp or 0),
            reverse=True,
        )
    ]
    # Excess cost is what the site could recover. Consumption the PM has explained is not
    # recoverable, so it is reported in the list but excluded from the money.
    total_excess = round(
        sum(r["financial_gbp"] for r in ranked if r["status"] != "expected"), 2
    )
    if eui.get("financial_gbp"):
        total_excess = round(total_excess + float(eui["financial_gbp"]), 2)

    # Carbon
    meters = list(
        (
            await session.execute(
                select(EnergyMeter).where(
                    EnergyMeter.site_id == site_id,
                    EnergyMeter.active.is_(True),
                )
            )
        ).scalars().all()
    )
    carbon_factor = float(meters[0].carbon_kg_per_kwh) if meters else 0.207
    carbon_kg = round(float(eui.get("total_kwh") or 0) * carbon_factor, 4) if eui.get("ok") else None

    report_json = {
        "site_id": str(site_id),
        "report_month": report_month.isoformat(),
        "eui": eui if eui.get("ok") else {"ok": False, "error": eui.get("error")},
        "eui_trend": trend,
        "anomalies_ranked": ranked,
        "total_excess_cost_gbp": total_excess,
        "carbon_exposure_kg": carbon_kg,
        "note": "Notification and dashboard only — no work orders created.",
    }

    lines = [
        f"Monthly Energy Report — {report_month.isoformat()}",
        f"Site: {site_id}",
        f"EUI annualised: {eui.get('eui_kwh_per_m2_annualised')} kWh/m² (benchmark {eui.get('benchmark_kwh_per_m2')})",
        f"Deviation: {eui.get('deviation_pct')}% · Excess cost £{eui.get('financial_gbp')}",
        f"Anomalies: {len(ranked)} · Total excess estimate £{total_excess}",
        f"Carbon exposure: {carbon_kg} kg CO2e",
        "",
        "Anomalies by cost impact:",
    ]
    for r in ranked[:15]:
        lines.append(
            f"  - {r['anomaly_type']}: {r['metric_pct']}% · £{r['financial_gbp']} ({r['status']})"
        )

    sections = [
        {
            "heading": "Executive summary",
            "lines": [
                f"Report month: {report_month.isoformat()}",
                f"Site id: {site_id}",
                f"Total excess cost (estimate): £{total_excess}",
                f"Carbon exposure: {carbon_kg} kg CO₂e",
            ],
        },
        {
            "heading": "EUI vs TM46 / benchmark",
            "lines": [
                f"Annualised EUI: {eui.get('eui_kwh_per_m2_annualised')} kWh/m²",
                f"Benchmark: {eui.get('benchmark_kwh_per_m2')} kWh/m²",
                f"Deviation: {eui.get('deviation_pct')}%",
                f"Financial impact: £{eui.get('financial_gbp')}",
            ],
            "table": (
                [["Period", "EUI", "Benchmark", "Deviation %", "£"]]
                + [
                    [
                        t.get("period_start") or "—",
                        str(t.get("eui") if t.get("eui") is not None else "—"),
                        str(t.get("benchmark") if t.get("benchmark") is not None else "—"),
                        str(t.get("deviation_pct") if t.get("deviation_pct") is not None else "—"),
                        str(t.get("financial_gbp") if t.get("financial_gbp") is not None else "—"),
                    ]
                    for t in trend
                ]
                if trend
                else None
            ),
        },
        {
            "heading": "Anomalies ranked by cost",
            "lines": [f"{len(ranked)} anomalies in period."] if ranked else ["No anomalies recorded."],
            "table": (
                [["Type", "Metric %", "£ impact", "Status"]]
                + [
                    [
                        str(r["anomaly_type"]),
                        str(r["metric_pct"] if r["metric_pct"] is not None else "—"),
                        str(r["financial_gbp"]),
                        str(r["status"]),
                    ]
                    for r in ranked[:20]
                ]
                if ranked
                else None
            ),
        },
    ]

    pdf_url = None
    if export_pdf:
        out_dir = Path(__file__).resolve().parents[3] / "data" / "energy_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"energy_{site_id}_{report_month.isoformat()}.pdf"
        local_path = _render_pdf_text(
            {"title": lines[0], "lines": lines, "sections": sections},
            out_path,
        )
        from ...shared.blob import upload_energy_pdf

        pdf_url = await upload_energy_pdf(
            Path(local_path),
            site_id=str(site_id),
            report_month=report_month.isoformat(),
        )

    existing = (
        await session.execute(
            select(EnergyMonthlyReport).where(
                EnergyMonthlyReport.site_id == site_id,
                EnergyMonthlyReport.report_month == report_month,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.eui_trend_json = trend
        existing.anomalies_ranked_json = ranked
        existing.total_excess_cost_gbp = Decimal(str(total_excess))
        existing.carbon_exposure_kg = Decimal(str(carbon_kg)) if carbon_kg is not None else None
        existing.report_json = report_json
        existing.pdf_blob_url = pdf_url
        card = existing
    else:
        card = EnergyMonthlyReport(
            id=uuid4(),
            organization_id=organization_id,
            site_id=site_id,
            report_month=report_month,
            eui_trend_json=trend,
            anomalies_ranked_json=ranked,
            total_excess_cost_gbp=Decimal(str(total_excess)),
            carbon_exposure_kg=Decimal(str(carbon_kg)) if carbon_kg is not None else None,
            report_json=report_json,
            pdf_blob_url=pdf_url,
        )
        session.add(card)

    await write_audit(
        session,
        actor="system",
        action_type="energy.report.monthly",
        source_feature="C",
        organization_id=organization_id,
        output_payload={"site_id": str(site_id), "report_month": report_month.isoformat()},
    )
    await session.commit()
    return {
        "ok": True,
        "report_id": str(card.id),
        "report_month": report_month.isoformat(),
        "site_id": str(site_id),
        "total_excess_cost_gbp": total_excess,
        "carbon_exposure_kg": carbon_kg,
        "anomalies_count": len(ranked),
        "pdf_path": pdf_url,
        "report": report_json,
    }


async def saved_space_summary(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    rq = select(EnergyMonthlyReport).order_by(EnergyMonthlyReport.report_month.desc()).limit(12)
    if organization_id:
        rq = rq.where(EnergyMonthlyReport.organization_id == organization_id)
    reports = list((await session.execute(rq)).scalars().all())

    aq = select(EnergyAnomaly).where(EnergyAnomaly.status == "open").limit(50)
    if organization_id:
        aq = aq.where(EnergyAnomaly.organization_id == organization_id)
    open_anom = list((await session.execute(aq)).scalars().all())

    rq_rec = select(EnergyRecommendation).where(EnergyRecommendation.status == "open").limit(30)
    if organization_id:
        rq_rec = rq_rec.where(EnergyRecommendation.organization_id == organization_id)
    open_recs = list((await session.execute(rq_rec)).scalars().all())

    return {
        "ok": True,
        "themes": {"energy": "emerald"},
        "kpis": {
            "reports_count": len(reports),
            "open_anomalies": len(open_anom),
            "open_recommendations": len(open_recs),
            "total_open_anomaly_cost_gbp": round(
                sum(float(a.financial_gbp or 0) for a in open_anom), 2
            ),
        },
        "reports": [
            {
                "id": str(r.id),
                "site_id": str(r.site_id) if r.site_id else None,
                "report_month": r.report_month.isoformat(),
                "total_excess_cost_gbp": float(r.total_excess_cost_gbp) if r.total_excess_cost_gbp else None,
                "carbon_exposure_kg": float(r.carbon_exposure_kg) if r.carbon_exposure_kg else None,
                "pdf_path": r.pdf_blob_url,
                "anomalies_ranked": r.anomalies_ranked_json,
                "eui_trend": r.eui_trend_json,
            }
            for r in reports
        ],
        "open_anomalies": [
            {
                "id": str(a.id),
                "anomaly_type": a.anomaly_type,
                "financial_gbp": float(a.financial_gbp) if a.financial_gbp else None,
                "metric_pct": float(a.metric_pct) if a.metric_pct else None,
                "status": a.status,
            }
            for a in open_anom[:20]
        ],
        "open_recommendations": [
            {
                "id": str(r.id),
                "asset_id": str(r.asset_id) if r.asset_id else None,
                "recommendation_type": r.recommendation_type,
                "condition_score": r.condition_score,
                "consumption_vs_benchmark_pct": float(r.consumption_vs_benchmark_pct)
                if r.consumption_vs_benchmark_pct is not None
                else None,
                "repair_cost_gbp": float(r.repair_cost_gbp) if r.repair_cost_gbp is not None else None,
                "replace_cost_gbp": float(r.replace_cost_gbp) if r.replace_cost_gbp is not None else None,
                "summary": r.summary,
                "status": r.status,
            }
            for r in open_recs[:15]
        ],
    }
