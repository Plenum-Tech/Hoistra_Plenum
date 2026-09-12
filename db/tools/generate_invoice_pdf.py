"""Render a maintenance invoice PDF from what the portfolio actually holds.

Built to give the ingestion pipeline something real to read: a document whose every line
carries a work-order code and an asset code that exist in plenum_cafm, so after ingesting it
you can check each extracted field against the row it came from rather than against a
plausible-looking number.

    python db/tools/generate_invoice_pdf.py --to <dsn> --building B-006 --vendor HALD \
        --out Invoice-B006.pdf

What comes from the database: the building and its site, the vendor, the contract reference,
and every work order on that building with its asset. What does not: the money. No deployment
of this platform has cost data for these buildings — cost-drivers reports billed 0.00 across
every asset — so the rate card below is illustrative, varied by asset type so an extractor
has real numbers to read rather than twelve identical ones. The document says so on its face,
because an invoice with invented figures that does not admit it is a forgery of a document
rather than a specimen of one.

Two fields are deliberately left off: the supplier's VAT registration number and its bank
details. A real invoice needs both; the platform holds neither, and inventing them would
make the specimen indistinguishable from a real payable document.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, timedelta
from typing import Any

import asyncpg
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# ── palette ─────────────────────────────────────────────────────────────────────────
INK = colors.HexColor("#16191A")
MUTED = colors.HexColor("#6E6A63")
FAINT = colors.HexColor("#938F87")
RULE = colors.HexColor("#D9D5CD")
RULE_SOFT = colors.HexColor("#E8E5DE")
DEEP = colors.HexColor("#143B41")
SAND = colors.HexColor("#8A5E1E")
SAND_BG = colors.HexColor("#F6EEDF")
BAND = colors.HexColor("#FBF6EA")

#: Illustrative rates by asset keyword: (planned visit, reactive attendance).
#: Varied on purpose. Twelve identical figures would let a broken extractor look correct.
RATE_CARD: list[tuple[str, float, float]] = [
    ("CHILLER", 620.00, 240.00),
    ("LIFT", 510.00, 285.00),
    ("BOILER", 445.00, 195.00),
    ("AHU", 385.00, 165.00),
    ("FIRE", 295.00, 150.00),
    ("PUMP", 265.00, 140.00),
]
DEFAULT_RATES = (350.00, 175.00)

SYMBOL = {"GBP": "£", "AED": "AED ", "USD": "$", "SGD": "S$", "EUR": "€"}


def rates_for(asset_code: str) -> tuple[float, float]:
    code = (asset_code or "").upper()
    for key, planned, reactive in RATE_CARD:
        if key in code:
            return planned, reactive
    return DEFAULT_RATES


def money(v: float) -> str:
    return f"{v:,.2f}"


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def gather(dsn: str, building_code: str, vendor_code: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn, ssl=_ssl_for(dsn))
    try:
        site_country = await conn.fetchval(
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema='plenum_cafm' AND table_name='sites'
                  AND column_name IN ('country_code','country') ORDER BY 1 LIMIT 1""")
        b = await conn.fetchrow(
            f"""SELECT b.building_id::text AS id, b.name, b.building_code, b.primary_use,
                       b.floors, b.gross_area_sqft, s.city,
                       {('s.' + site_country) if site_country else 'NULL'}::text AS country
                  FROM plenum_cafm.buildings b
                  LEFT JOIN plenum_cafm.sites s ON s.site_id = b.site_id
                 WHERE b.building_code = $1""", building_code)
        if b is None:
            raise SystemExit(f"no building with code {building_code}")
        v = await conn.fetchrow(
            "SELECT id::text, vendor_name, vendor_code, country, city "
            "FROM plenum_cafm.vendors WHERE vendor_code = $1", vendor_code)
        if v is None:
            raise SystemExit(f"no vendor with code {vendor_code}")
        contract = await conn.fetchval(
            "SELECT contract_ref FROM plenum_cafm.contracts "
            "WHERE building_id = $1::uuid LIMIT 1", b["id"])
        wos = await conn.fetch(
            """SELECT w.wo_code, w.title, w.status, w.created_at::date AS raised,
                      a.asset_code, a.asset_name
                 FROM plenum_cafm.work_orders w
                 LEFT JOIN plenum_cafm.assets a ON a.id = w.asset_id
                WHERE w.building_id = $1::uuid
                ORDER BY a.asset_code NULLS LAST, w.wo_code""", b["id"])
        org = await conn.fetchval(
            "SELECT organization_id::text FROM plenum_cafm.compliance_certificates "
            "WHERE organization_id IS NOT NULL LIMIT 1")
        return {"building": dict(b), "vendor": dict(v), "contract": contract,
                "work_orders": [dict(r) for r in wos], "org": org}
    finally:
        await conn.close()


def build(data: dict[str, Any], out: str, currency: str, vat_rate: float,
          invoice_no: str) -> dict[str, Any]:
    b, v = data["building"], data["vendor"]
    sym = SYMBOL.get(currency, currency + " ")
    today = date.today()

    def st(name, **kw):
        base = dict(fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK)
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_band_tag = st("bandtag", fontName="Courier-Bold", fontSize=7.5, textColor=SAND)
    s_band = st("band", fontSize=8, textColor=MUTED)
    s_kind = st("kind", fontName="Courier", fontSize=7.5, textColor=DEEP)
    s_name = st("name", fontName="Times-Bold", fontSize=20, leading=23)
    s_sub = st("sub", fontSize=8.5, textColor=MUTED, leading=11)
    s_lbl = st("lbl", fontSize=7, textColor=FAINT)
    s_lblr = st("lblr", fontSize=7, textColor=FAINT, alignment=TA_RIGHT)
    s_val = st("val", fontName="Courier", fontSize=8.5, alignment=TA_RIGHT)
    s_h2 = st("h2", fontName="Helvetica-Bold", fontSize=6.8, textColor=FAINT)
    s_party = st("party", fontName="Helvetica-Bold", fontSize=10)
    s_code = st("code", fontName="Courier", fontSize=7.5, textColor=DEEP)
    s_th = st("th", fontName="Helvetica-Bold", fontSize=6.8, textColor=colors.white)
    s_thr = st("thr", fontName="Helvetica-Bold", fontSize=6.8, textColor=colors.white,
               alignment=TA_RIGHT)
    s_td = st("td", fontSize=8)
    s_tdm = st("tdm", fontName="Courier", fontSize=7.5, textColor=DEEP)
    s_num = st("num", fontName="Courier", fontSize=8, alignment=TA_RIGHT)
    s_work = st("work", fontName="Helvetica-Bold", fontSize=8)
    s_small = st("small", fontSize=7, textColor=MUTED)
    s_note = st("note", fontSize=7.5, textColor=MUTED, leading=10)
    s_noteh = st("noteh", fontName="Helvetica-Bold", fontSize=6.8, textColor=SAND)
    s_foot = st("foot", fontSize=7, textColor=FAINT, leading=9.5)
    s_sig = st("sig", fontName="Courier", fontSize=7, textColor=MUTED)

    flow: list[Any] = []
    W = A4[0] - 20 * mm

    # ── the band, first, because this document is shaped like a payable invoice ──────
    band = Table([[Paragraph("SPECIMEN", s_band_tag),
                   Paragraph("Generated from the Hoistra test portfolio for ingestion "
                             "testing. Not a payable invoice.", s_band)]],
                 colWidths=[22 * mm, W - 22 * mm])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("LINEBEFORE", (0, 0), (0, -1), 2, SAND),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    flow += [band, Spacer(1, 10)]

    # ── masthead ────────────────────────────────────────────────────────────────────
    meta_rows = [
        ("Invoice no.", invoice_no),
        ("Invoice date", today.strftime("%d.%m.%Y")),
        ("Due date", (today + timedelta(days=30)).strftime("%d.%m.%Y")),
        ("Contract", str(data["contract"] or "—")),
        ("Currency", currency),
        ("Terms", "30 days net"),
    ]
    meta = Table([[Paragraph(k, s_lblr), Paragraph(val, s_val)] for k, val in meta_rows],
                 colWidths=[26 * mm, 30 * mm])
    meta.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    issuer = [Paragraph("VAT INVOICE" if currency == "GBP" else "TAX INVOICE", s_kind),
              Paragraph(v["vendor_name"], s_name),
              Paragraph(f"Mechanical &amp; electrical maintenance · "
                        f"{v.get('city') or b.get('city') or ''}"
                        f"{', ' if (v.get('city') or b.get('city')) else ''}"
                        f"{v.get('country') or ''}<br/>"
                        f"Vendor record {v['vendor_code']} · plenum_cafm.vendors", s_sub)]
    head = Table([[issuer, meta]], colWidths=[W - 56 * mm, 56 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1.4, DEEP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    flow += [head, Spacer(1, 9)]

    # ── parties ─────────────────────────────────────────────────────────────────────
    def party(title, name, lines, code):
        cell = [Paragraph(title.upper(), s_h2), Spacer(1, 3), Paragraph(name, s_party)]
        cell += [Paragraph(l, s_sub) for l in lines]
        cell += [Spacer(1, 2), Paragraph(code, s_code)]
        return cell

    area = b.get("gross_area_sqft")
    area_txt = f"{float(area):,.0f} sq ft" if area else "area not recorded"
    parties = Table([[
        party("Invoice to", "Planum Technologies", ["Portfolio operator"],
              f"org {str(data['org'] or '—')[:8]}…"),
        party("Property", b["name"],
              [f"{b.get('city') or ''}, {b.get('country') or ''}",
               f"{b.get('primary_use') or 'Use not recorded'} · "
               f"{b.get('floors') or '?'} floors · {area_txt}"],
              b["building_code"]),
        party("Scope", "Planned maintenance &amp; call-outs",
              [f"{len({w['asset_code'] for w in data['work_orders']})} assets · "
               f"{len(data['work_orders'])} work orders"],
              "plenum_cafm.work_orders"),
    ]], colWidths=[W / 3] * 3)
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("LEFTPADDING", (1, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-2, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white),
    ]))
    flow += [parties, Spacer(1, 10)]

    # ── line items ──────────────────────────────────────────────────────────────────
    rows = [[Paragraph("Sl.", s_th), Paragraph("Date", s_th), Paragraph("Work order", s_th),
             Paragraph("Asset", s_th), Paragraph("Work details", s_th),
             Paragraph(f"Amount ({currency})", s_thr)]]
    planned_total = reactive_total = 0.0
    planned_n = reactive_n = 0
    for i, w in enumerate(data["work_orders"], 1):
        p_rate, r_rate = rates_for(w.get("asset_code") or "")
        title = str(w.get("title") or "")
        reactive = "call-out" in title.lower() or "fault" in title.lower()
        amount = r_rate if reactive else p_rate
        if reactive:
            reactive_total += amount
            reactive_n += 1
        else:
            planned_total += amount
            planned_n += 1
        status = str(w.get("status") or "")
        work = ("Fault call-out" if reactive else "Annual service")
        detail = ("Reactive attendance" if reactive else "Planned maintenance visit")
        rows.append([
            Paragraph(f"{i:02d}.", ParagraphStyle("sl", parent=s_tdm, textColor=FAINT)),
            Paragraph(w["raised"].strftime("%d.%m.%Y") if w.get("raised") else "—", s_tdm),
            Paragraph(str(w.get("wo_code") or "—"), s_tdm),
            # Name and code in one cell: they are read together, and as separate columns
            # neither was wide enough to hold its longest value without breaking it.
            [Paragraph(str(w.get("asset_name") or "—"), s_td),
             Paragraph(str(w.get("asset_code") or "—"), s_tdm)],
            [Paragraph(work, s_work), Paragraph(f"{detail} · {status}", s_small)],
            Paragraph(money(amount), s_num),
        ])
    # Sized to the longest value each column actually carries — 10.09.2026 in Courier 7.5
    # needs 26mm with padding, B-006-FIRE-PANEL-05 needs 40mm — rather than to a guess.
    widths = [9 * mm, 26 * mm, 28 * mm, 41 * mm, W - 129 * mm, 25 * mm]
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DEEP),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE_SOFT),
        ("LINEBELOW", (0, -1), (-1, -1), 0.8, RULE),
    ]))
    flow += [table]

    # ── totals ──────────────────────────────────────────────────────────────────────
    sub = planned_total + reactive_total
    vat = round(sub * vat_rate, 2)
    total = sub + vat
    trows = [
        (f"Planned maintenance — {planned_n} visits", money(planned_total)),
        (f"Reactive call-outs — {reactive_n} attendances", money(reactive_total)),
        ("Subtotal excluding VAT", money(sub)),
        (f"VAT @ {vat_rate * 100:.0f}%", money(vat)),
    ]
    tt = [[Paragraph(k, s_sub), Paragraph(val, s_num)] for k, val in trows]
    tt.append([Paragraph(f"Total due ({currency})",
                         st("gt", fontName="Helvetica-Bold", fontSize=12)),
               Paragraph(sym + money(total),
                         st("gv", fontName="Courier-Bold", fontSize=12, alignment=TA_RIGHT))])
    totals = Table(tt, colWidths=[60 * mm, 30 * mm])
    totals.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 5),
        ("LINEABOVE", (0, -1), (-1, -1), 1.4, DEEP),
        ("TOPPADDING", (0, -1), (-1, -1), 7),
    ]))
    wrap = Table([[totals]], colWidths=[W])
    wrap.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    flow += [Spacer(1, 8), wrap]

    # ── what this document is, in the document ──────────────────────────────────────
    notes = [
        Paragraph("BASIS OF THIS DOCUMENT", s_noteh), Spacer(1, 4),
        Paragraph(f"<b>Every line traces to a row.</b> The {len(data['work_orders'])} items "
                  f"are the work orders recorded against building {b['building_code']} in "
                  f"plenum_cafm.work_orders, with their own work-order codes, asset codes, "
                  f"dates and statuses. Property, vendor and contract reference are read "
                  f"from the portfolio unchanged.", s_note), Spacer(1, 3),
        Paragraph("<b>The amounts do not.</b> This platform holds no cost data for this "
                  "building — cost-drivers reports billed 0.00 across every asset and zero "
                  "invoice lines. The rates are illustrative, varied by asset type so that "
                  "an extractor has real numbers to read rather than twelve identical ones. "
                  "They are not a quotation and are held nowhere in the database.", s_note),
        Spacer(1, 3),
        Paragraph("<b>No VAT registration number and no bank details.</b> A real invoice "
                  "requires both. The platform holds neither and neither has been invented, "
                  "so they are absent rather than filled in.", s_note),
    ]
    nt = Table([[notes]], colWidths=[W])
    nt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SAND_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2, SAND),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    flow += [Spacer(1, 12), KeepTogether(nt)]

    # ── footer ──────────────────────────────────────────────────────────────────────
    sig = lambda label: Paragraph(label.upper(), s_sig)
    foot = Table([[
        Paragraph(f"Hoistra · generated {today.strftime('%d.%m.%Y')} from the test "
                  f"portfolio<br/>{b['name']} ({b['building_code']}) · {v['vendor_name']} "
                  f"({v['vendor_code']})", s_foot),
        sig("Prepared by"), sig("Checked by"), sig("Signature"),
    ]], colWidths=[W - 96 * mm, 32 * mm, 32 * mm, 32 * mm])
    foot.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, -1), 0.4, RULE_SOFT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (1, 0), (-1, -1), 0.4, RULE),
        ("BOTTOMPADDING", (1, 0), (-1, -1), 16),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    flow += [Spacer(1, 14), foot]

    doc = BaseDocTemplate(out, pagesize=A4,
                          leftMargin=10 * mm, rightMargin=10 * mm,
                          topMargin=12 * mm, bottomMargin=12 * mm,
                          title=f"Invoice {invoice_no} — {b['name']}",
                          author=v["vendor_name"], subject="Specimen maintenance invoice")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])
    doc.build(flow)
    return {"lines": len(data["work_orders"]), "subtotal": sub, "vat": vat, "total": total}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", dest="dsn", required=True, help="the database to read")
    ap.add_argument("--building", required=True, help="building_code, e.g. B-006")
    ap.add_argument("--vendor", required=True, help="vendor_code, e.g. HALD")
    ap.add_argument("--out", required=True, help="path of the PDF to write")
    ap.add_argument("--currency", default="GBP")
    ap.add_argument("--vat-rate", type=float, default=0.20)
    ap.add_argument("--invoice-no", default=None)
    args = ap.parse_args()

    data = await gather(args.dsn, args.building, args.vendor)
    inv = args.invoice_no or (f"{args.vendor[:3].upper()}-"
                              f"{args.building.replace('-', '')}-0042")
    got = build(data, args.out, args.currency, args.vat_rate, inv)
    print(f"{args.out}")
    print(f"  {data['building']['name']} ({args.building}) · "
          f"{data['vendor']['vendor_name']} ({args.vendor})")
    print(f"  {got['lines']} lines · subtotal {money(got['subtotal'])} · "
          f"VAT {money(got['vat'])} · total {money(got['total'])} {args.currency}")


if __name__ == "__main__":
    asyncio.run(main())
