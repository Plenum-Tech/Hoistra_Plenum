"""Bishopsgate's vendor records that a workbook cannot carry, put in through the product's own
engines: the six contracts' terms, the assets' criticality, and a half-year of invoices.

The Vendors page is built from scorecards, and a scorecard needs three things the Migration
page does not write: a contract parameter set to measure against (contract_sla_parameters),
each asset's criticality (asset_criticality) and invoice verifications (invoice_verifications,
whose matched/flagged ratio is blended into the score). build_bishopsgate_workbook.py puts the
prototype's terms on each Vendor_Contracts row as JSON and the work orders behind every figure
in Work_Orders; this reads them back after the ingest and hands them to

  parameters.ingest_contract_parameters    the terms -> a DRAFT parameter set per contract
  parameters.upsert_asset_criticality      L1/L2/L3 per asset, PROPOSED
  invoice.verify_invoice                   each invoice's lines matched against the work orders

Nothing is confirmed or approved here. A confirmed contract makes a vendor's numbers binding
and the engine refuses a confirmation nobody is named for; an approved criticality triples a
vendor's penalty on that asset. Both are a person's decision, made on the Vendors page:
confirm each contract, approve the criticalities, then Rebuild scorecards.

    python db/tools/seed_bishopsgate_vendor_terms.py --prove <...-complete.xlsx>   # no database
    python db/tools/seed_bishopsgate_vendor_terms.py                                # dry run
    python db/tools/seed_bishopsgate_vendor_terms.py --apply

--prove scores the workbook with the engine's own pure functions (score_work_order_pure,
match_invoice_line, merge_extraction_with_defaults) and prints what each vendor's latest card
will say once the contracts are confirmed. Refuses any database but hoistra_test.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import random
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SVC = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-operations-intelligence")

BUILDING, CODE = "Bishopsgate Tower", "B-301"
#: Invoice lines per vendor over the half-year, about half the prototype's yearly sample.
INVOICE_LINES = {"APXM": 21, "APXL": 13, "NGEL": 17, "CLWC": 10, "SFLT": 12, "PNFS": 12, "FNMC": 12,
                 "CRVM": 12, "TLVL": 12, "BRKE": 12, "OSTP": 12, "KSTF": 12, "LUMF": 12, "AQLW": 12, "FNKH": 12}
#: The prototype's invoice lines the engine can judge (HOISTRA_VP.V.*.invoices), each on the
#: work order build_bishopsgate_workbook.py made for it. INV-8766, a recall credited by the
#: vendor, is not among them: the matcher has no notion of recall chargeability.
NAMED_LINES = {
    "APXM": [("INV-8841", "2026-08", "WO-B-301-4262", "Labour — 32 hrs out of hours", {"labour_rate": 85.0}, 3808.0),
             ("INV-8802", "2026-07", "WO-B-301-4239", "Parts — compressor seal kit", {"parts_cost": 1420.0}, 1420.0),
             ("INV-8744", "2026-06", None, "PPM — quarterly HVAC", {}, None)],
    "APXL": [("INV-8177", "2026-07", "WO-B-301-4318", "Call-out — Lift-2", {"labour_rate": 108.0}, 420.0),
             ("INV-8210", "2026-08", None, "PPM — monthly lift service", {}, None)],
    "CLWC": [("INV-8301", "2026-07", "WO-B-301-4325", "Labour — 18 hrs", {"labour_rate_over": 5.30}, 1140.0),
             ("INV-8330", "2026-08", None, "L8 monitoring — monthly", {}, None)],
    "NGEL": [("INV-9120", "2026-08", None, "PPM — fixed wire testing", {}, None),
             ("INV-9088", "2026-08", None, "Parts — RCBO replacement ×12", {}, None),
             ("INV-9041", "2026-07", None, "Labour — 6 hrs out of hours", {}, None)],
}
KPI_TERMS = ("Completion target", "First-time fix target", "Recall window", "Service credit formula",
             "Recall chargeability", "Uplift review")


def js_round(x: float) -> int:
    return math.floor(x + 0.5)


def term_hours(value: str | None) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*(hours?|business days?)", value or "")
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2).startswith("hour") else n * 24


def extraction_from(sla_json: str) -> tuple[dict, str, str | None]:
    """What the contract states, as the extractor would return it. Only terms the prototype
    marks as read from the contract go in; the engine fills the rest with its defaults and
    says so on every one."""
    doc = json.loads(sla_json)
    ext: dict = {"contract_ref": doc["contract_ref"]}
    kpi: dict = {}
    for t in doc["terms"]:
        if t.get("src") != "contract":
            continue
        lbl, val = t["label"], t["value"]
        cite = {k: t[k] for k in ("clause", "page") if t.get(k)}
        m = re.match(r"P([1-3]) response", lbl)
        if m:
            ext[f"sla_response_p{m.group(1)}_hours"] = term_hours(val)
        elif lbl == "Labour rate — standard":
            ext["labour_hour_rate"] = float(re.sub(r"[^\d.]", "", val))
        elif lbl == "Labour rate — out of hours":
            ext["overtime_rate"] = float(re.sub(r"[^\d.]", "", val))
        elif lbl == "Parts mark-up cap":
            ext["parts_pricing_json"] = {"markup_cap_pct": float(re.sub(r"[^\d.]", "", val)), **cite}
        elif lbl in KPI_TERMS:
            kpi[lbl] = {"value": val, **cite}
    if kpi:
        ext["kpi_clauses_json"] = kpi
    return ext, doc["contract_ref"], doc.get("signed")


def criticality_of(asset_class: str | None, crit_text: str | None) -> str:
    if (asset_class or "").lower().startswith("fan coil") or "fcu" in (asset_class or "").lower():
        return "L3"
    return "L1" if str(crit_text or "").lower() in ("high", "l1", "critical") else "L2"


def plan_invoices(vcode: str, wos: list[dict], hour_rate: float, rng: random.Random) -> list[dict]:
    """A half-year of invoices: one a month, lines on that month's completed work, the named
    prototype lines where they fall, and enough plain lines that matched/(matched+flagged)
    rounds to the prototype's invoice-accuracy figure."""
    from build_bishopsgate_workbook import GEN_PERF, VP_ID  # noqa: F401 - constants only
    target = TARGET_INVOICE[vcode]
    named = NAMED_LINES.get(vcode, [])
    named_flagged = sum(1 for n in named if n[4])
    total = INVOICE_LINES[vcode]
    for L in range(total, total + 20):
        m = next((c for c in range(L + 1) if js_round(100 * c / L) == target and L - c >= named_flagged), None)
        if m is not None:
            break
    flagged = L - m
    by_month: dict[str, list[dict]] = defaultdict(list)
    for w in wos:
        by_month[str(w["completed_at"])[:7]].append(w)
    months = sorted(by_month)[-6:]
    by_code = {w["wo_code"]: w for w in wos}
    lines: list[tuple[str, dict]] = []
    used: set[str] = set()
    for ref, month, wo_code, desc, override, amount in named:
        w = by_code.get(wo_code) if wo_code else next((x for x in by_month.get(month, []) if x["wo_code"] not in used), None)
        if not w:
            continue
        used.add(w["wo_code"])
        ln = {"wo_code": w["wo_code"], "description": desc, "labour_hours": w["labour_hours"],
              "labour_rate": hour_rate, "parts_cost": w.get("parts_cost") or None}
        ln.update({k: v for k, v in override.items() if k != "labour_rate_over"})
        if "labour_rate_over" in override:                 # "£5.30/hr above schedule", whatever the schedule is
            ln["labour_rate"] = round(hour_rate + override["labour_rate_over"], 2)
        ln["amount"] = amount or round((ln["labour_hours"] or 0) * ln["labour_rate"] + (ln["parts_cost"] or 0), 2)
        lines.append((ref, ln))
    extra_flag = flagged - named_flagged
    plain = [w for w in wos if w["wo_code"] not in used and (w.get("labour_hours") or 0) > 0]
    rng.shuffle(plain)
    for i, w in enumerate(plain[: L - len(lines)]):
        month = str(w["completed_at"])[:7]
        hours = w["labour_hours"]
        if i < extra_flag:                       # billed a quarter more time than was on site
            hours = round(hours * 1.25 + 0.25, 1)
        ln = {"wo_code": w["wo_code"], "description": "Labour and materials", "labour_hours": hours,
              "labour_rate": hour_rate, "parts_cost": w.get("parts_cost") or None}
        ln["amount"] = round(hours * hour_rate + (ln["parts_cost"] or 0), 2)
        lines.append((f"INV-{vcode}-{month.replace('-', '')}", ln))
    invoices: dict[str, dict] = {}
    for ref, ln in lines:
        inv = invoices.setdefault(ref, {"invoice_ref": ref, "lines": []})
        ln["line_id"] = f"{ref}-{len(inv['lines']) + 1}"
        inv["lines"].append(ln)
    return list(invoices.values())


TARGET_INVOICE: dict[str, int] = {}


def load_targets() -> None:
    from build_bishopsgate_workbook import GEN_PERF, VP_ID, read_prototype
    proto = read_prototype(os.environ.get("HOISTRA_PROTOTYPE", r"C:\Users\balap\Downloads\Hoistra_1 (1).html"))
    for vcode, vid in VP_ID.items():
        TARGET_INVOICE[vcode] = proto["HOISTRA_VP"]["V"][vid]["measured"]["invoice"]
    for vcode, p in GEN_PERF.items():
        TARGET_INVOICE[vcode] = p["invoice"]


def engine():
    """The svc-operations-intelligence engines, imported the way the service imports them."""
    from _env import hoistra_test_dsn
    dsn = hoistra_test_dsn()
    os.environ.setdefault("DB_URL", re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+asyncpg://", dsn))
    sys.path.insert(0, os.path.abspath(SVC))
    from src.engines.contract_performance import invoice, parameters, scoring
    return scoring, parameters, invoice


# ── --prove: the workbook, scored with the engine's pure functions ─────────────────────────

def prove(path: str) -> None:
    import openpyxl
    scoring, parameters, invoice = engine()
    wb = openpyxl.load_workbook(path, read_only=True)

    def rows(name):
        it = wb[name].iter_rows(values_only=True)
        hdr = next(it)
        return [dict(zip(hdr, r)) for r in it]

    vendors = {r["vendor_name"]: r for r in rows("Vendors")}
    code_of = {r["vendor_name"]: r["vendor_code"] for r in vendors.values()}
    assets = {r["asset_code"]: r for r in rows("Assets")}
    contracts = {r["vendor_code"] or code_of.get(r["vendor_name"]): r for r in rows("Vendor_Contracts")}
    today = dt.date.today()
    lapsed: dict[str, dt.date] = {}
    for c in rows("Compliance_Certificates"):
        if str(c.get("cert_scope")).lower() == "vendor" and c.get("expiry_date"):
            exp = c["expiry_date"].date() if isinstance(c["expiry_date"], dt.datetime) else c["expiry_date"]
            if exp < today:
                v = c.get("vendor_code")
                lapsed[v] = min(lapsed.get(v, exp), exp)
    weights = dict(scoring.DEFAULT_WEIGHTS)
    wos = defaultdict(list)
    for w in rows("Work_Orders"):
        vname = w.get("vendor") or w.get("vendor_name")
        if w.get("completed_at") and code_of.get(vname):
            wos[code_of[vname]].append(w)
    rng = random.Random(301)
    print(f"\n  {os.path.basename(path)} — each vendor's newest card, once its contract is confirmed\n")
    for vcode, c in contracts.items():
        ext, ref, _ = extraction_from(c["sla_terms"])
        params, defaults, _src = parameters.merge_extraction_with_defaults(ext)
        blocked = str(vendors[c["vendor_name"]].get("block_state") or "") == "Blocked"
        lapse = lapsed.get(vcode)
        cards = {}
        for approved in (True, False):
            by_month = defaultdict(list)
            prior = []
            for w in sorted(wos[vcode], key=lambda x: str(x["completed_at"])):
                crit = criticality_of(None, assets.get(w.get("asset_code"), {}).get("criticality")) if approved else "L2"
                if approved and "FCU" in str(w.get("asset_code")):
                    crit = "L3"
                wo = {"reported_at": w["reported_at"], "attended_at": w["attended_at"], "completed_at": w["completed_at"],
                      "priority": w["priority"], "first_fix": str(w["first_fix"]).lower() == "yes",
                      "recall": str(w["recall"]).lower() == "yes", "asset_id": w.get("asset_code"), "wo_code": w["wo_code"]}
                for k in ("reported_at", "attended_at", "completed_at"):
                    wo[k] = str(wo[k]).replace(" ", "T") + ("" if "+" in str(wo[k]) else "+00:00")
                s = scoring.score_work_order_pure(wo, params=params, weights=weights, prior_completions=prior,
                                                  accreditation_current=lapse is None, vendor_blocked=blocked,
                                                  criticality=crit, lapse_date=lapse)
                by_month[str(w["completed_at"])[:7]].append(s)
            cards[approved] = by_month
        # invoices, judged by the matcher itself
        wo_dicts = [{"wo_code": w["wo_code"], "completed_at": str(w["completed_at"]), "status": "Completed",
                     "labour_hours": float(w["labour_hours"] or 0), "parts_cost": float(w["parts_cost"] or 0)}
                    for w in wos[vcode]]
        rate = parameters.contracted_hourly_rate(params.get("labour_day_rate"), params.get("labour_hour_rate"))
        invs = plan_invoices(vcode, wo_dicts, rate, rng)
        idx = {w["wo_code"]: w for w in wo_dicts}
        matched = flagged = 0
        held = []
        for inv in invs:
            for ln in inv["lines"]:
                r = invoice.match_invoice_line(ln, work_orders=idx, labour_day_rate=params.get("labour_day_rate"),
                                               labour_hour_rate=rate, parts_framework=params.get("parts_pricing_json") or {})
                if r["status"] == "matched":
                    matched += 1
                else:
                    flagged += 1
                    if inv["invoice_ref"].startswith("INV-8") or inv["invoice_ref"].startswith("INV-9"):
                        held.append(f"{inv['invoice_ref']} £{r['delta_gbp']:,.0f}")
        ratio = matched / (matched + flagged) if matched + flagged else None
        months = sorted(cards[True])
        latest, prev = months[-1], months[-2]
        out = []
        for approved in (True, False):
            def overall(m):
                sc = cards[approved][m]
                avg = sum(x["overall_score"] for x in sc) / len(sc)
                o = scoring.blend_invoice_ratio_into_score(avg, ratio)
                if any(x["capped_by_block"] for x in sc):
                    o = min(o, float(weights["blocked_score_cap"]))
                return o
            out.append((overall(latest), overall(latest) - overall(prev)))
        sc = cards[True][latest]
        comp = lambda k, w: js_round(100 * (sum(x["component_scores"][k] for x in sc) / len(sc)) / w)  # noqa: E731
        print(f"  {vcode} {c['vendor_name']:22} {ref:16} {latest}: {len(sc)} jobs · "
              f"response {comp('sla_response', 25)}% · completion {comp('sla_completion', 25)}% · "
              f"first fix {comp('first_fix', 20)}% · no recall {comp('recall', 15)}% · "
              f"accreditation {comp('accreditation', 15)}%")
        print(f"       invoices {matched}/{matched + flagged} matched = {js_round(100 * ratio)}% · held {', '.join(held) or '—'}")
        print(f"       score {out[0][0]:.1f} (trend {out[0][1]:+.1f}) with criticality approved · "
              f"{out[1][0]:.1f} ({out[1][1]:+.1f}) before · {len(defaults)} terms on platform defaults"
              + (f" · capped: {'blocked' if blocked else ''}" if lapse else ""))


# ── --apply: through the engines, against hoistra_test ───────────────────────────────────

async def seed(org_name: str, apply: bool) -> None:
    scoring, parameters, invoice = engine()
    from sqlalchemy import text
    from src.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        db = (await s.execute(text("SELECT current_database()"))).scalar()
        if db != "hoistra_test":
            raise SystemExit(f"refusing to run against {db!r}: hoistra_test only")
        org = (await s.execute(text("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower(:n)"),
                               {"n": org_name})).scalar()
        if not org:
            raise SystemExit(f"no organization named {org_name!r}")
        contracts = (await s.execute(text(
            """SELECT c.id, c.vendor_id, v.vendor_code, v.vendor_name, c.contract_name, c.sla_terms
                 FROM plenum_cafm.vendor_contracts c JOIN plenum_cafm.vendors v ON v.id = c.vendor_id
                WHERE c.organization_id = :o ORDER BY v.vendor_code"""), {"o": org})).mappings().all()
        if not contracts:
            raise SystemExit("no vendor contracts for this organization - ingest the workbook first")
        assets = (await s.execute(text(
            """SELECT id, asset_code, criticality, asset_name FROM plenum_cafm.assets
                WHERE organization_id = :o AND building_code = :b"""), {"o": org, "b": CODE})).mappings().all()
        have_params = (await s.execute(text(
            "SELECT count(*) FROM plenum_cafm.contract_sla_parameters WHERE organization_id = :o"), {"o": org})).scalar()
        have_inv = {r[0] for r in (await s.execute(text(
            "SELECT invoice_ref FROM plenum_cafm.invoice_verifications WHERE organization_id = :o"), {"o": org})).all()}
        print(f"\n  {org_name} {org}: {len(contracts)} contracts, {len(assets)} assets, "
              f"{have_params} parameter sets and {len(have_inv)} invoices already on record")
        rng = random.Random(301)
        plan = []
        for c in contracts:
            try:
                ext, ref, signed = extraction_from(c["sla_terms"])
            except (TypeError, ValueError, KeyError):
                raise SystemExit(f"{c['contract_name']}: sla_terms is not the terms JSON build_bishopsgate_workbook.py "
                                 "writes - this is an older ingest; clear the page data and ingest the new workbook")
            wos = await scoring.fetch_completed_work_orders_from_udr(s, vendor_id=c["vendor_id"], organization_id=org,
                                                                   limit=5000)
            merged, _, _ = parameters.merge_extraction_with_defaults(ext)
            ext["_rate"] = parameters.contracted_hourly_rate(merged.get("labour_day_rate"), merged.get("labour_hour_rate"))
            ext["_day"] = merged.get("labour_day_rate")
            invs = [i for i in plan_invoices(c["vendor_code"], wos, ext["_rate"], rng)
                    if i["invoice_ref"] not in have_inv]
            plan.append((c, ext, ref, signed, invs))
            print(f"    {c['vendor_code']} {c['vendor_name']:22} {ref:16} {len(ext) - 1} terms from the contract · "
                  f"{len(wos)} completed work orders · {sum(len(i['lines']) for i in invs)} invoice lines in {len(invs)} invoices")
        if not apply:
            print("\n  dry run - pass --apply to write")
            return
        for c, ext, ref, signed, invs in plan:
            rate, day = ext.pop("_rate"), ext.pop("_day")
            r = await parameters.ingest_contract_parameters(
                s, extracted=ext, organization_id=org, vendor_id=c["vendor_id"], vendor_name=c["vendor_name"],
                contract_id=c["id"], contract_ref=ref, signed_date=signed, building_name=BUILDING,
                building_reference=CODE, file_name=f"{ref}.pdf")
            print(f"    {ref}: {r.get('status') or r.get('reason') or r.get('ok')}")
            for inv in invs:
                wos = await scoring.fetch_completed_work_orders_from_udr(s, vendor_id=c["vendor_id"], organization_id=org,
                                                                       limit=5000)
                out = await invoice.verify_invoice(
                    s, invoice_ref=inv["invoice_ref"], lines=inv["lines"], work_orders=wos, invoice_ref_identifies=True,
                    vendor_id=c["vendor_id"], organization_id=org, building_name=BUILDING, building_reference=CODE,
                    file_name=f"{inv['invoice_ref']}.pdf", labour_day_rate=day,
                    labour_hour_rate=ext.get("labour_hour_rate"), parts_pricing_json=ext.get("parts_pricing_json"))
                print(f"      {inv['invoice_ref']}: {out['matched_count']} matched, {out['flagged_count']} flagged")
        for a in assets:
            crit = criticality_of(a["asset_name"] if "FCU" in (a["asset_code"] or "") else None, a["criticality"])
            if "FCU" in (a["asset_code"] or ""):
                crit = "L3"
            await parameters.upsert_asset_criticality(s, asset_id=a["id"], asset_code=a["asset_code"],
                                                      organization_id=org, proposed=crit, source="workbook")
        await s.commit()
        print(f"\n  written: {len(plan)} draft parameter sets, invoices, {len(assets)} criticality proposals."
              "\n  Next, on the Vendors page: confirm each contract, approve the criticalities, Rebuild scorecards.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prove", metavar="WORKBOOK", help="score this workbook with the engine's pure functions; no database")
    args = ap.parse_args()
    load_targets()
    if args.prove:
        prove(args.prove)
    else:
        asyncio.run(seed(args.org_name, args.apply))


if __name__ == "__main__":
    main()
