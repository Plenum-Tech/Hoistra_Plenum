"""One-click compliance evidence pack (PDF) for insurers / auditors / BSA."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.approvals import list_queue, write_audit
from . import certificates as cert_svc
from .coverage import building_coverage, vendor_coverage


def _render_evidence_pdf(payload: dict[str, Any], out_path: Path) -> Path:
    title = payload.get("title") or "Compliance Evidence Pack"
    generated = payload.get("generated_at") or datetime.now(timezone.utc).isoformat()
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
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle(
            "EvH1",
            parent=styles["Heading1"],
            fontSize=15,
            textColor=colors.HexColor("#0c4a6e"),
            spaceAfter=6,
        )
        h2 = ParagraphStyle(
            "EvH2",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#0369a1"),
            spaceBefore=10,
            spaceAfter=4,
        )
        body = ParagraphStyle(
            "EvBody",
            parent=styles["Normal"],
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#1e293b"),
        )
        story: list[Any] = [
            Paragraph(str(title), h1),
            Paragraph(f"Generated: {generated}", body),
            Paragraph(
                "Source: Plenum Compliance Engine · plenum_cafm · live certificates + Approvals.",
                body,
            ),
            Spacer(1, 6),
        ]

        summary = payload.get("summary") or {}
        story.append(Paragraph("Portfolio summary", h2))
        story.append(
            Paragraph(
                f"Building certs: {summary.get('building_total', 0)} · "
                f"Vendor accreditations: {summary.get('vendor_total', 0)} · "
                f"Vendors blocked: {summary.get('vendors_blocked', 0)} · "
                f"Pending approvals: {summary.get('approvals_pending', 0)}",
                body,
            )
        )

        for section in payload.get("sections") or []:
            story.append(Paragraph(str(section.get("heading") or "Section"), h2))
            for line in section.get("lines") or []:
                story.append(Paragraph(str(line), body))
            rows = section.get("rows") or []
            if rows:
                data = [section.get("headers") or list(rows[0].keys())]
                keys = data[0]
                for r in rows[:80]:
                    data.append([str(r.get(k, "—") or "—")[:48] for k in keys])
                table = Table(data, repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")),
                            ("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#94a3b8")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 3),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                        ]
                    )
                )
                story.append(table)

        story.append(Spacer(1, 10))
        story.append(
            Paragraph(
                "This pack is an operational evidence snapshot for insurers, auditors, "
                "and Building Safety Act / Golden Thread reviews. It does not replace "
                "statutory registers — use Verify-now links for live register checks.",
                body,
            )
        )
        doc.build(story)
        return out_path
    except Exception:  # noqa: BLE001
        txt = out_path.with_suffix(".txt")
        lines = [title, f"Generated: {generated}", ""]
        for section in payload.get("sections") or []:
            lines.append(f"## {section.get('heading')}")
            lines.extend(str(x) for x in (section.get("lines") or []))
            for r in section.get("rows") or []:
                lines.append(str(r))
            lines.append("")
        txt.write_text("\n".join(lines), encoding="utf-8")
        return txt


async def build_evidence_pack(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    vendor_id: UUID | None = None,
    include_approvals: bool = True,
) -> dict[str, Any]:
    building = await cert_svc.list_certificates(
        session,
        cert_scope="Building",
        organization_id=organization_id,
        site_id=site_id,
        limit=500,
    )
    vendor = await cert_svc.list_certificates(
        session,
        cert_scope="Vendor",
        organization_id=organization_id,
        vendor_id=vendor_id,
        limit=500,
    )
    summary = await cert_svc.saved_space_summary(session, organization_id=organization_id)
    b_cov = await building_coverage(session, organization_id=organization_id)
    v_cov = await vendor_coverage(session, organization_id=organization_id)

    approvals: list[dict[str, Any]] = []
    if include_approvals:
        try:
            from ...shared.approvals import queue_item_to_dict

            raw_items = await list_queue(
                session,
                source_feature="A",
                status="pending",
                organization_id=organization_id,
                limit=100,
            )
            approvals = [queue_item_to_dict(a) for a in raw_items]
        except Exception:  # noqa: BLE001
            approvals = []

    sections: list[dict[str, Any]] = [
        {
            "heading": "Building coverage gaps",
            "lines": [
                f"Average building coverage: {b_cov.get('average_coverage_pct')}%",
                f"Not on record (portfolio): {(summary.get('building_certificates') or {}).get('not_on_record', 0)}",
            ],
            "headers": ["Building", "Coverage %", "On record", "Required", "Gaps"],
            "rows": [
                {
                    "Building": b.get("site_name"),
                    "Coverage %": b.get("coverage_pct"),
                    "On record": b.get("on_record"),
                    "Required": b.get("required"),
                    "Gaps": ", ".join((b.get("gaps") or [])[:6]) or "—",
                }
                for b in (b_cov.get("buildings") or [])[:40]
            ],
        },
        {
            "heading": "Building certificates",
            "headers": ["Type", "Number", "Status", "Expiry", "Building"],
            "rows": [
                {
                    "Type": c.get("certificate_type_name") or c.get("certificate_type_code"),
                    "Number": c.get("certificate_number"),
                    "Status": c.get("status"),
                    "Expiry": c.get("expiry_date"),
                    "Building": c.get("building_name") or c.get("building_reference") or "—",
                }
                for c in building
            ],
        },
        {
            "heading": "Vendor accreditations",
            "lines": [
                f"Average vendor coverage: {v_cov.get('average_coverage_pct')}%",
                f"Vendors blocked: {(summary.get('risk_dashboard') or {}).get('vendors_blocked', 0)}",
            ],
            "headers": ["Vendor", "Type", "Number", "Status", "Risk", "Expiry"],
            "rows": [
                {
                    "Vendor": c.get("vendor_name") or "—",
                    "Type": c.get("certificate_type_name") or c.get("certificate_type_code"),
                    "Number": c.get("certificate_number"),
                    "Status": c.get("status"),
                    "Risk": c.get("risk_badge") or c.get("vendor_block_state") or "—",
                    "Expiry": c.get("expiry_date"),
                }
                for c in vendor
            ],
        },
    ]
    if approvals:
        sections.append(
            {
                "heading": "Open Approvals (Feature A)",
                "headers": ["Type", "Summary", "Severity"],
                "rows": [
                    {
                        "Type": a.get("item_type"),
                        "Summary": a.get("summary"),
                        "Severity": a.get("severity"),
                    }
                    for a in approvals
                    if isinstance(a, dict)
                ],
            }
        )

    scope_bits = []
    if site_id:
        scope_bits.append(f"site {site_id}")
    if vendor_id:
        scope_bits.append(f"vendor {vendor_id}")
    title = "Compliance Evidence Pack" + (f" — {', '.join(scope_bits)}" if scope_bits else "")

    return {
        "title": title,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": {
            "building_total": len(building),
            "vendor_total": len(vendor),
            "vendors_blocked": (summary.get("risk_dashboard") or {}).get("vendors_blocked", 0),
            "approvals_pending": len(approvals),
        },
        "sections": sections,
    }


async def generate_evidence_pack_file(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    vendor_id: UUID | None = None,
    include_approvals: bool = True,
) -> FileResponse:
    payload = await build_evidence_pack(
        session,
        organization_id=organization_id,
        site_id=site_id,
        vendor_id=vendor_id,
        include_approvals=include_approvals,
    )
    tmp = Path(tempfile.gettempdir()) / f"compliance_evidence_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
    out = _render_evidence_pdf(payload, tmp)
    await write_audit(
        session,
        actor="system",
        action_type="compliance.evidence_pack.generate",
        source_feature="A",
        organization_id=organization_id,
        input_payload={
            "site_id": str(site_id) if site_id else None,
            "vendor_id": str(vendor_id) if vendor_id else None,
        },
        output_payload={"path": str(out), "summary": payload.get("summary")},
        detail={"title": payload.get("title")},
    )
    await session.commit()
    media = "application/pdf" if out.suffix.lower() == ".pdf" else "text/plain"
    return FileResponse(
        path=str(out),
        media_type=media,
        filename=out.name,
    )
