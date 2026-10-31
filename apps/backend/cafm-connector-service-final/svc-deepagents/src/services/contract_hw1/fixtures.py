"""Additive fictional source fixtures; setup never overwrites existing evidence."""
import hashlib
import json


ORGANIZATION = "hw1-org-a"
BUILDING = "hw1-building-a1"
VENDOR = BUILDING + "-vendor-0"
FIXTURE_VERSION = "contract-fixtures-v3"


def _source(identifier, kind, fields, building=BUILDING, organization=ORGANIZATION):
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return {"id": identifier, "kind": kind, "org": organization,
            "buildings": [building],
            "data": {"fields": fields, "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                     "text": canonical, "version": 1, "information_type": kind,
                     "scope_verified": True}}


def _record(identifier, kind, data, building=BUILDING, organization=ORGANIZATION):
    return {"id": identifier, "kind": kind, "org": organization,
            "buildings": [building] if building else [], "data": {"scope_verified": True, **data}}


def dataset():
    sources, records = [], []
    params = {
        "weights": {"sla_response_pct": 25, "sla_completion_pct": 25,
                    "first_fix_pct": 20, "recall_pct": 15, "accreditation_pct": 15},
        "recall_window_days": 30, "score_cap": 60, "ppm_tolerance_days": 7,
        "cost_variance_alert_pct": "15", "cost_variance_alert_jobs": 3,
        "invoice_flag_adversary_gbp": "500",
    }
    records.append(_record("hw1-contract-config-org-a-v3", "contract_scoring_config", {
        "version": 3, "parameters": params, "effective_at": "2026-10-25T00:00:00+00:00",
        "fixture_version": FIXTURE_VERSION}, building=None))
    records.append(_record("hw1-contract-config-org-b-v3", "contract_scoring_config", {
        "version": 3, "parameters": params, "effective_at": "2026-10-25T00:00:00+00:00",
        "fixture_version": FIXTURE_VERSION}, building=None, organization="hw1-org-b"))

    contract_fields = {
        "vendor_id": VENDOR, "contract_ref": "TEST-CONTRACT-A1-02",
        "signed_on": "2026-02-01", "effective_from": "2026-01-01",
        "effective_to": "2026-12-31", "response_target_hours": {"P1": 4, "P2": 24},
        "completion_target_hours": {"P1": 12, "P2": 72},
        "labour_hour_rate": "80.00", "labour_day_rate": "640.00",
        "parts_pricing": {"TEST-PART": "25.00"},
    }
    contract_source = "hw1-contract-source-a1-02-v3"
    sources.append(_source(contract_source, "contract", contract_fields))
    records.append(_record("hw1-contract-a1-02-v3", "contract",
        {**contract_fields, "source_id": contract_source, "fixture_version": FIXTURE_VERSION}))

    lapse_fields = {"vendor_id": VENDOR, "category": "certificate_expiry",
                    "lapse_start": "2026-10-26", "lapse_end": None,
                    "certificate_id": "hw1-building-a1-accreditation-0"}
    lapse_source = "hw1-contract-lapse-source-a1-v3"
    sources.append(_source(lapse_source, "vendor_block_history", lapse_fields))
    records.append(_record("hw1-contract-lapse-a1-v3", "contract_block_period",
        {**lapse_fields, "source_id": lapse_source, "source_evidence_ref": lapse_source,
         "fixture_version": FIXTURE_VERSION}))

    accreditation_fields = {"vendor_id": VENDOR, "issue_date": "2026-01-01",
                            "expiry_date": "2026-10-25", "certificate_id": "TEST-ACC-A1-01"}
    accreditation_source = "hw1-contract-accreditation-source-a1-v3"
    sources.append(_source(accreditation_source, "vendor_accreditation", accreditation_fields))
    records.append(_record("hw1-contract-accreditation-a1-v3", "contract_accreditation",
        {**accreditation_fields, "source_id": accreditation_source,
         "fixture_version": FIXTURE_VERSION}))

    work_orders = [
        ("hw1-wo-report-a1-101-v3", "2026-10-20T08:00:00+00:00", "2026-10-20T10:00:00+00:00",
         "2026-10-20T15:00:00+00:00", "P1", "AHU-1", "FAULT-COOLING"),
        ("hw1-wo-report-a1-102-v3", "2026-10-26T08:00:00+00:00", "2026-10-26T14:00:00+00:00",
         "2026-10-27T02:00:00+00:00", "P1", "AHU-2", "FAULT-HEATING"),
        ("hw1-wo-report-a1-103-v3", "2026-10-28T08:00:00+00:00", "2026-10-28T09:00:00+00:00",
         "2026-10-28T16:00:00+00:00", "P1", "AHU-1", "FAULT-COOLING"),
    ]
    for identifier, raised, responded, completed, priority, asset, fault in work_orders:
        work_order_ref = identifier.split("-v")[0].split("-")[-1]
        fields = {"vendor_id": VENDOR, "work_order_ref": work_order_ref,
                  "raised_at": raised, "response_at": responded, "completed_at": completed,
                  "priority": priority, "asset_id": asset, "fault_code": fault,
                  "attendance_duration_hours": 2,
                  "status": "completed", "actual_cost": "100.00", "estimated_cost": "100.00"}
        source_id = identifier + "-source-v3"
        sources.append(_source(source_id, "external_work_order_report", fields))
        records.append(_record(identifier, "work_order_report",
            {**fields, "source_id": source_id, "fixture_version": FIXTURE_VERSION}))

    ppm_fields = {"vendor_id": VENDOR, "scheduled_date": "2026-10-15",
                  "completed_date": "2026-10-21", "visit_ref": "TEST-PPM-A1-01"}
    ppm_source = "hw1-ppm-source-a1-v3"
    sources.append(_source(ppm_source, "external_ppm_report", ppm_fields))
    records.append(_record("hw1-ppm-report-a1-01-v3", "ppm_report",
        {**ppm_fields, "source_id": ppm_source, "fixture_version": FIXTURE_VERSION}))

    invoice_fields = {"vendor_id": VENDOR, "invoice_ref": "TEST-INV-A1-01",
        "invoice_date": "2026-10-29", "lines": [
            {"line_id": "line-1", "work_order_id": "hw1-wo-report-a1-101-v3",
             "labour_hours": "2", "billed_hour_rate": "80", "part_code": "TEST-PART",
             "parts_qty": "1", "billed_parts_total": "25", "discrepancy": None},
            {"line_id": "line-2", "work_order_id": "hw1-wo-report-a1-102-v3",
             "labour_hours": "2", "billed_hour_rate": "90", "part_code": "TEST-PART",
             "parts_qty": "1", "billed_parts_total": "25",
             "discrepancy": "Labour rate exceeds the evidenced contract rate by GBP 20.00"},
            {"line_id": "line-3", "work_order_id": "missing-work-order",
             "labour_hours": "2", "billed_hour_rate": "80", "part_code": "TEST-PART",
             "parts_qty": "1", "billed_parts_total": "25",
             "discrepancy": "No matching source work-order report"},
        ]}
    invoice_source = "hw1-invoice-source-a1-01-v3"
    sources.append(_source(invoice_source, "supplier_invoice", invoice_fields))
    records.append(_record("hw1-invoice-verification-a1-01-v3", "invoice_verification",
        {**invoice_fields, "source_id": invoice_source, "fixture_version": FIXTURE_VERSION}))

    _milestone_edges(records, sources, contract_fields, accreditation_fields)
    return {"sources": sources, "records": records}


def _milestone_edges(records, sources, contract_template, certificate_template):
    """Independent source scenarios, isolated from the historic three-job score fixture."""
    def add(identifier, kind, fields, building=BUILDING, mirror_changes=None):
        source_id = identifier + "-source"
        sources.append(_source(source_id, kind, fields, building=building))
        records.append(_record(identifier, kind, {**fields, "source_id": source_id,
            "fixture_version": FIXTURE_VERSION, **(mirror_changes or {})}, building=building))

    def report(vendor, suffix, raised, building=BUILDING, **changes):
        from datetime import datetime, timedelta
        stamp = datetime.fromisoformat(raised)
        fields = {"vendor_id": vendor, "raised_at": raised,
            "response_at": (stamp + timedelta(hours=1)).isoformat(),
            "completed_at": (stamp + timedelta(hours=2)).isoformat(),
            "priority": "P1", "asset_id": suffix, "fault_code": "test-fault", "status": "completed",
            "attendance_duration_hours": 2, "actual_cost": "100", "estimated_cost": "100", **changes}
        identifier = "hw1-contract-edge-" + suffix
        add(identifier, "work_order_report", fields, building)
        return identifier

    for number in range(901, 908):
        vendor = BUILDING + "-vendor-" + str(number)
        add(vendor, "vendor", {"name": "Fictional Contract edge vendor " + str(number)})
        contract = {**contract_template, "vendor_id": vendor}
        if number == 901:
            contract["labour_hour_rate"] = None  # Explicitly exercise day-rate / eight fallback.
        if number == 903:
            contract["response_target_hours"] = {}
            contract["response_target_working_days"] = {"P1": 1}
        add("hw1-contract-edge-terms-" + str(number), "contract", contract)
        certificate = {**certificate_template, "vendor_id": vendor,
            "expiry_date": None if number == 904 else "2026-10-25" if number == 901 else "2026-12-31"}
        add("hw1-contract-edge-cert-" + str(number), "contract_accreditation", certificate)
        if number == 906:
            add("hw1-contract-edge-terms-tie-906", "contract", contract)
        if number == 901:
            block = {"vendor_id": vendor, "category": "certificate_expiry",
                "lapse_start": "2026-10-26", "lapse_end": None, "certificate_id": certificate["certificate_id"]}
            identifier = "hw1-contract-edge-lapse-901"
            add(identifier, "contract_block_period", block,
                mirror_changes={"source_evidence_ref": identifier + "-source"})
            wo = report(vendor, "expiry-day-901", "2026-10-25T08:00:00+00:00")
            report(vendor, "lapse-day-901", "2026-10-26T08:00:00+00:00")
            add("hw1-contract-edge-invoice-901", "invoice_verification", {
                "vendor_id": vendor, "invoice_date": "2026-10-29", "lines": [
                    {"line_id": "large-rate-delta", "work_order_id": wo,
                     "labour_hours": "2", "billed_hour_rate": "380", "parts_qty": "1",
                     "part_code": "TEST-PART", "billed_parts_total": "25"}]})
        elif number == 905:
            report(vendor, "month-end-905", "2026-10-31T08:00:00+00:00", asset_id="same-asset-905")
            report(vendor, "next-month-905", "2026-11-01T08:00:00+00:00", asset_id="same-asset-905")
        elif number == 907:
            other = report(vendor, "other-building-907", "2026-10-20T08:00:00+00:00", building="hw1-building-a2")
            add("hw1-contract-edge-invoice-907", "invoice_verification", {"vendor_id": vendor,
                "invoice_date": "2026-10-29", "lines": [{"line_id": "wrong-building", "work_order_id": other}]})
        else:
            identifier = report(vendor, "ordinary-" + str(number), "2026-10-20T08:00:00+00:00")
            if number == 902:
                # Deliberately invalid *derived mirror*, with retained original still intact.
                next(row for row in records if row["id"] == identifier)["data"]["response_at"] = "2026-10-19T08:00:00+00:00"
