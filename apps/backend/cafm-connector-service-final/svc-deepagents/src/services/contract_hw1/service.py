"""Scoped source-backed calculations and explicit configuration confirmation."""
from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid5, NAMESPACE_URL

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..homework_domains.calculations import ScoringConfig, elapsed_sla_hours, vendor_score
from ..compliance_hw1.store import runtime_store
from ..compliance_hw1.contracts import MANAGERS
from ...api.homework_identity import TestPrincipal
from .contracts import ConfigConfirmation, ConfigProposal, InvoiceQuery, PerformanceQuery, ScorecardQuery
from .fixtures import FIXTURE_VERSION

TOOL_ROLES = {
    "contract_vendor_performance": MANAGERS,
    "contract_invoice_diagnostic": MANAGERS,
    "contract_get_scorecard": MANAGERS,
    "contract_scoring_configuration": {"portfolio_manager"},
    "contract_prepare_scoring_configuration": {"portfolio_manager"},
}
WEIGHT_KEYS = {
    "sla_response_pct", "sla_completion_pct", "first_fix_pct", "recall_pct",
    "accreditation_pct",
}
CONFIG_FIELDS = {
    "weights", "recall_window_days", "score_cap", "ppm_tolerance_days",
    "cost_variance_alert_pct", "cost_variance_alert_jobs", "invoice_flag_adversary_gbp",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _as_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_datetime(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Source timestamp must include its timezone")
    return result


def _score_config(data):
    return ScoringConfig(
        weights=data["weights"], recall_window_days=data["recall_window_days"],
        score_cap=data["score_cap"], ppm_tolerance_days=data["ppm_tolerance_days"],
        cost_variance_alert_pct=Decimal(str(data["cost_variance_alert_pct"])),
        cost_variance_alert_jobs=data["cost_variance_alert_jobs"],
        invoice_flag_adversary_gbp=Decimal(str(data["invoice_flag_adversary_gbp"])),
    ).validated()


def _scope(principal: TestPrincipal, requested):
    selected = list(principal.building_ids) if requested is None else requested
    if not selected or len(set(selected)) != len(selected) or any(
            building not in principal.building_ids for building in selected):
        raise HTTPException(403, "Building access denied")
    return sorted(selected)


def _in_scope(row, scope):
    buildings = set(row["buildings"])
    return bool(buildings) and buildings <= set(scope)


async def _records(repo, kind, scope=None, lock=False):
    rows = await repo.list("records", kind, lock=lock)
    return [row for row in rows if scope is None or _in_scope(row, scope)]


async def _verify_source(repo, principal, record, scope):
    """Require an immutable, same-scope source and its persisted content digest."""
    source_id = record["data"].get("source_id")
    if not source_id:
        raise HTTPException(503, "Source evidence reference unavailable")
    source = await repo.get("sources", source_id)
    if (source["org"] != principal.organization_id or not _in_scope(source, scope)
            or set(source["buildings"]) != set(record["buildings"])):
        raise HTTPException(404, "Evidence unavailable")
    source_digest = source["data"].get("content_sha256")
    source_fields = source["data"].get("fields")
    if (not source_digest or not isinstance(source_fields, dict)
            or source_digest != _hash(source_fields)):
        raise HTTPException(409, "Source evidence integrity check failed")
    if source["data"].get("text") is not None and source["data"]["text"] != _json(source_fields):
        raise HTTPException(409, "Source projection differs from retained original")
    if any(record["data"].get(key) != value for key, value in source_fields.items()):
        raise HTTPException(409, "Derived adapter values differ from immutable source evidence")
    return source


async def _config(repo, principal, lock=False):
    rows = await repo.list("records", "contract_scoring_config", lock=lock)
    candidates = [item for item in rows if item["org"] == principal.organization_id
                  and not item["buildings"]
                  and item["data"].get("fixture_version") == FIXTURE_VERSION]
    row = max(candidates, key=lambda item: item["data"].get("version", 0), default=None)
    if row is None:
        raise HTTPException(503, "Scoring configuration fixture unavailable")
    _score_config(row["data"]["parameters"])
    return row


def _row_record(row):
    return {"id": row["id"], "buildings": list(row["buildings"]), "data": row["data"]}


async def _current_contract(repo, principal, vendor_id, building, raised_on):
    candidates = []
    for row in await repo.list("records", "contract"):
        data = row["data"]
        if (data.get("vendor_id") != vendor_id or building not in row["buildings"]
                or data.get("fixture_version") != FIXTURE_VERSION):
            continue
        starts = _as_date(data["effective_from"])
        ends = _as_date(data["effective_to"]) if data.get("effective_to") else None
        signed = _as_date(data["signed_on"])
        if starts <= raised_on and (ends is None or raised_on <= ends) and signed <= raised_on:
            source = await _verify_source(repo, principal, row, [building])
            candidates.append((signed, row, source))
    if not candidates:
        raise ValueError("no_source_supported_contract_for_work_order_date")
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ValueError("same_signing_date_contract_tie_requires_review")
    return candidates[0][1], candidates[0][2]


async def _source_certificates(repo, principal, vendor_id, scope):
    result = []
    for row in await _records(repo, "contract_accreditation", scope):
        if (row["data"].get("vendor_id") == vendor_id
                and row["data"].get("fixture_version") == FIXTURE_VERSION):
            source = await _verify_source(repo, principal, row, scope)
            result.append((row, source))
    return result


async def _source_lapse_periods(repo, principal, vendor_id, scope):
    result = []
    for row in await _records(repo, "contract_block_period", scope):
        if (row["data"].get("vendor_id") != vendor_id
                or row["data"].get("fixture_version") != FIXTURE_VERSION):
            continue
        if row["data"].get("category") != "certificate_expiry":
            continue
        source = await _verify_source(repo, principal, row, scope)
        result.append((row, source))
    return result


async def _verified_work_orders(repo, principal, vendor_id, scope, start, end):
    result, excluded = [], []
    for row in await _records(repo, "work_order_report", scope):
        data = row["data"]
        if (data.get("vendor_id") != vendor_id
                or data.get("fixture_version") != FIXTURE_VERSION):
            continue
        try:
            raised = _as_datetime(data["raised_at"])
        except (KeyError, TypeError, ValueError):
            excluded.append({"work_order_id": row["id"], "reason": "invalid_or_missing_raise_timestamp"})
            continue
        if start <= raised.date() <= end:
            source = await _verify_source(repo, principal, row, scope)
            result.append((row, source, raised))
    return result, excluded


def _lapse_for(raised_on, periods):
    for record, source in periods:
        data = record["data"]
        start = _as_date(data["lapse_start"])
        end = _as_date(data["lapse_end"]) if data.get("lapse_end") else None
        if end is not None and end < start:
            raise ValueError("contradictory_lapse_period")
        if start <= raised_on and (end is None or raised_on <= end):
            return start, data["source_evidence_ref"]
    return None, None


def _accreditation_current(raised_on, certificates):
    for record, source in certificates:
        data = record["data"]
        starts = _as_date(data["issue_date"])
        if not data.get("expiry_date"):
            raise ValueError("accreditation_expiry_unknown")
        expires = _as_date(data["expiry_date"])
        if expires < starts:
            raise ValueError("accreditation_dates_contradictory")
        if starts <= raised_on <= expires:
            return True, source["id"]
    if not certificates:
        raise ValueError("accreditation_evidence_missing")
    return False, certificates[0][1]["id"] if certificates else None


def _component_scores(rows, config):
    values = {name: [] for name in WEIGHT_KEYS}
    per_order = {}
    for row in rows:
        data = row["data"]
        values["sla_response_pct"].append(100 if data["sla"]["response"]["met"] else 0)
        values["sla_completion_pct"].append(100 if data["sla"]["completion"]["met"] else 0)
        values["first_fix_pct"].append(100 if data["first_fix"] else 0)
        values["recall_pct"].append(100 if not data["recall"] else 0)
        values["accreditation_pct"].append(100 if data["accreditation_current"] else 0)
        per_order[row["id"]] = {key: data[key] for key in (
            "sla", "first_fix", "recall", "accreditation_current", "source_id", "raised_at",
            "contract_id", "contract_source_id", "lapse_evidence_ref", "score", "raw_score",
            "accreditation_cap_applied", "cost_variance_pct", "followup_source_ids")}
    means = {key: sum(items, Decimal(0)) / len(items) if items else Decimal(0)
             for key, items in ((name, [Decimal(v) for v in vals]) for name, vals in values.items())}
    return means, per_order


async def _performance(repo, principal, query: PerformanceQuery):
    if principal.role not in MANAGERS:
        raise HTTPException(403, "Vendor performance is not available to this role")
    scope = _scope(principal, query.building_ids)
    vendor_rows = [row for row in await _records(repo, "vendor", scope)
                   if row["id"] == query.vendor_id]
    if not vendor_rows:
        raise HTTPException(404, "Vendor unavailable")
    config_row = await _config(repo, principal)
    parameters = config_row["data"]["parameters"]
    config = _score_config(parameters)
    certificates = await _source_certificates(repo, principal, query.vendor_id, scope)
    lapse_periods = await _source_lapse_periods(repo, principal, query.vendor_id, scope)
    orders, excluded = await _verified_work_orders(repo, principal, query.vendor_id, scope,
                                                   query.period_start, query.period_end)
    # Follow-up reports beyond month end still establish a recall for this month's job.
    followups, _ = await _verified_work_orders(repo, principal, query.vendor_id, scope,
        query.period_start, query.period_end + timedelta(days=config.recall_window_days))
    eligible = []
    for row, source, raised in orders:
        data = row["data"]
        try:
            if len(row["buildings"]) != 1:
                excluded.append({"work_order_id": row["id"], "reason": "ambiguous_building_link"})
                continue
            if data.get("status") != "completed":
                excluded.append({"work_order_id": row["id"], "reason": "source_status_not_completed"})
                continue
            if data.get("conflict_unresolved"):
                excluded.append({"work_order_id": row["id"], "reason": "unresolved_source_conflict"})
                continue
            required = ("priority", "asset_id", "fault_code", "response_at", "completed_at")
            if any(not data.get(key) for key in required):
                excluded.append({"work_order_id": row["id"], "reason": "required_source_value_missing"})
                continue
            contract, contract_source = await _current_contract(
                repo, principal, query.vendor_id, row["buildings"][0], raised.date())
            priority = data["priority"]
            response_target = contract["data"]["response_target_hours"].get(priority)
            completion_target = contract["data"]["completion_target_hours"].get(priority)
            if response_target is None or completion_target is None:
                excluded.append({"work_order_id": row["id"], "reason": "contract_target_missing"})
                continue
            sla = elapsed_sla_hours(
                raised_at=raised, responded_at=_as_datetime(data["response_at"]),
                completed_at=_as_datetime(data["completed_at"]),
                response_target_hours=response_target,
                completion_target_hours=completion_target,
            )
            if not sla["response"]["assessable"] or not sla["completion"]["assessable"]:
                excluded.append({"work_order_id": row["id"], "reason": "sla_inputs_unassessable"})
                continue
            current, cert_source = _accreditation_current(raised.date(), certificates)
            lapse_start, lapse_ref = _lapse_for(raised.date(), lapse_periods)
            if (not current and lapse_start is None) or (current and lapse_start is not None):
                raise ValueError("accreditation_and_block_history_incomplete_or_conflicting")
            if lapse_start is not None:
                expiries = [_as_date(cert["data"]["expiry_date"]) for cert, _ in certificates
                            if cert["data"].get("expiry_date")]
                if not any(expiry + timedelta(days=1) == lapse_start for expiry in expiries):
                    raise ValueError("lapse_boundary_disagrees_with_expiry")
            eligible.append({"id": row["id"], "buildings": row["buildings"], "data": {
                "raised_at": raised.isoformat(), "asset_id": data["asset_id"],
                "fault_code": data["fault_code"], "sla": sla,
                "accreditation_current": current,
                "actual_cost": data.get("actual_cost"),
                "estimated_cost": data.get("estimated_cost"),
                "source_id": source["id"], "certificate_source_id": cert_source,
                "contract_id": contract["id"], "contract_source_id": contract_source["id"],
                "lapse_start": lapse_start, "lapse_evidence_ref": lapse_ref,
            }})
        except (KeyError, TypeError, ValueError) as exc:
            reason = str(exc)
            if reason not in {"no_source_supported_contract_for_work_order_date",
                              "same_signing_date_contract_tie_requires_review",
                              "accreditation_expiry_unknown", "accreditation_evidence_missing",
                              "accreditation_dates_contradictory", "contradictory_lapse_period",
                              "accreditation_and_block_history_incomplete_or_conflicting",
                              "lapse_boundary_disagrees_with_expiry"}:
                reason = "source_or_contract_conflict"
            excluded.append({"work_order_id": row["id"], "reason": reason})

    # A repeat fault on the same asset within the configured window makes the earlier
    # completed report a recall and not a first fix; both metrics use the same window.
    for item in eligible:
        this_date = _as_datetime(item["data"]["raised_at"])
        later = [(other, stamp) for other, _, stamp in followups if other["id"] != item["id"]
                 and other["buildings"] == item["buildings"]
                 and other["data"].get("asset_id") == item["data"]["asset_id"]
                 and not other["data"].get("conflict_unresolved")
                 and this_date < stamp <= this_date + timedelta(days=config.recall_window_days)]
        item["data"]["recall"] = any(other["data"].get("fault_code") == item["data"]["fault_code"]
                                    for other, _ in later)
        item["data"]["first_fix"] = not later
        item["data"]["followup_source_ids"] = sorted({other["data"]["source_id"] for other, _ in later})
        lapse_start, lapse_ref = item["data"]["lapse_start"], item["data"]["lapse_evidence_ref"]
        component_values = {
            "sla_response_pct": 100 if item["data"]["sla"]["response"]["met"] else 0,
            "sla_completion_pct": 100 if item["data"]["sla"]["completion"]["met"] else 0,
            "first_fix_pct": 100 if item["data"]["first_fix"] else 0,
            "recall_pct": 100 if not item["data"]["recall"] else 0,
            "accreditation_pct": 100 if item["data"]["accreditation_current"] else 0,
        }
        scored = vendor_score(component_values, config=config, raised_date=this_date.date(),
                              lapse_start=lapse_start, lapse_evidence_ref=lapse_ref)
        item["data"].update(score=scored["score"], raw_score=scored["raw_score"],
                            accreditation_cap_applied=scored["accreditation_cap_applied"],
                            lapse_evidence_ref=lapse_ref)
        actual, estimated = item["data"]["actual_cost"], item["data"]["estimated_cost"]
        if actual is not None and estimated is not None and Decimal(str(estimated)) > 0:
            item["data"]["cost_variance_pct"] = str(
                (Decimal(str(actual))-Decimal(str(estimated))) / Decimal(str(estimated)) * 100)
        else:
            item["data"]["cost_variance_pct"] = None
    means, detail = _component_scores(eligible, config)
    ppm = []
    for row in await _records(repo, "ppm_report", scope):
        data = row["data"]
        if (data.get("vendor_id") != query.vendor_id
                or data.get("fixture_version") != FIXTURE_VERSION):
            continue
        scheduled = _as_date(data["scheduled_date"])
        if not query.period_start <= scheduled <= query.period_end:
            continue
        source = await _verify_source(repo, principal, row, scope)
        delta = abs((_as_date(data["completed_date"]) - scheduled).days) if data.get("completed_date") else None
        tolerance = config.ppm_tolerance_days
        ppm.append({"ppm_report_id": row["id"], "source_id": source["id"],
                    "scheduled_date": str(scheduled), "completed_date": data.get("completed_date"),
                    "within_tolerance": delta is not None and delta <= tolerance})
    ppm_rate = (Decimal(sum(1 for row in ppm if row["within_tolerance"])) * 100 / len(ppm)
                if ppm else None)
    scores = [Decimal(item["data"]["score"]) for item in eligible]
    overall = (sum(scores) / len(scores)).quantize(Decimal("1"), rounding=ROUND_HALF_UP) if scores else None
    variance_jobs = [Decimal(item["data"]["cost_variance_pct"]) for item in eligible
                     if item["data"]["cost_variance_pct"] is not None]
    variance_alerts = [value for value in variance_jobs
                       if value > Decimal(str(parameters["cost_variance_alert_pct"]))]
    prior_month = query.period_start.replace(day=1)
    prior_month = date(prior_month.year-1, 12, 1) if prior_month.month == 1 else date(
        prior_month.year, prior_month.month-1, 1)
    prior_end = date(prior_month.year, prior_month.month,
                     monthrange(prior_month.year, prior_month.month)[1])
    prior_rows = await repo.list("records", "contract_scorecard")
    prior_score = next((row["data"].get("overall_score") for row in prior_rows
        if row["data"].get("vendor_id") == query.vendor_id
        and row["data"].get("reporting_period") == {"start": str(prior_month), "end": str(prior_end)}
        and set(row["buildings"]) <= set(_scope(principal, query.building_ids))), None)
    source_ids = sorted({item["data"]["source_id"] for item in eligible}
        | {item["data"]["contract_source_id"] for item in eligible}
        | {item["data"]["certificate_source_id"] for item in eligible if item["data"]["certificate_source_id"]}
        | {item["data"]["lapse_evidence_ref"] for item in eligible if item["data"]["lapse_evidence_ref"]}
        | {identifier for item in eligible for identifier in item["data"]["followup_source_ids"]}
        | {item["source_id"] for item in ppm})
    source_versions = {identifier: (await repo.get("sources", identifier))["data"].get("version")
                       for identifier in source_ids}
    return {
        "vendor_id": query.vendor_id, "building_scope": scope,
        "reporting_period": {"start": str(query.period_start), "end": str(query.period_end)},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_score": int(overall) if overall is not None else None,
        "trend_delta": (int(overall)-prior_score if overall is not None and prior_score is not None else None),
        "component_percentages": {key: str(value) for key, value in means.items()},
        "eligible_work_orders": len(eligible), "excluded_work_orders": excluded,
        "work_order_details": detail,
        "source_ids": source_ids, "source_versions": source_versions,
        "trigger": "manual", "sla_time_basis": "elapsed_hours",
        "parameter_snapshot": {"configuration_id": config_row["id"],
                               "configuration_version": config_row["data"]["version"],
                               "parameters": parameters},
        "cost_variance": {
            "eligible_jobs": len(variance_jobs),
            "above_threshold_jobs": len(variance_alerts),
            "threshold_pct": str(parameters["cost_variance_alert_pct"]),
            "alert_required": len(variance_alerts) >= parameters["cost_variance_alert_jobs"],
            "work_orders": {item["id"]: item["data"]["cost_variance_pct"] for item in eligible},
        },
        "source_freshness": {
            "latest_report_raise_at": max((item["data"]["raised_at"] for item in eligible), default=None),
            "freshness_label": "source report timestamps shown; upstream data_as_of not supplied",
        },
        "ppm": {"visit_count": len(ppm), "within_tolerance_count": sum(1 for row in ppm if row["within_tolerance"]),
                "compliance_pct": str(ppm_rate) if ppm_rate is not None else None,
                "tolerance_days": config.ppm_tolerance_days, "visits": ppm},
        "invoice_diagnostic_is_separate": True,
    }


async def _invoice_diagnostic(repo, principal, query: InvoiceQuery):
    if principal.role not in MANAGERS:
        raise HTTPException(403, "Invoice diagnostics are not available to this role")
    scope = _scope(principal, query.building_ids)
    vendors = [row for row in await _records(repo, "vendor", scope) if row["id"] == query.vendor_id]
    if not vendors:
        raise HTTPException(404, "Vendor unavailable")
    config = _score_config((await _config(repo, principal))["data"]["parameters"])
    work_orders = {}
    for wo in await _records(repo, "work_order_report", scope):
        if (wo["data"].get("vendor_id") == query.vendor_id
                and wo["data"].get("fixture_version") == FIXTURE_VERSION):
            await _verify_source(repo, principal, wo, scope)
            work_orders[wo["id"]] = wo
    invoices = []
    evidence_ids = set()
    for row in await _records(repo, "invoice_verification", scope):
        data = row["data"]
        if (data.get("vendor_id") != query.vendor_id
                or data.get("fixture_version") != FIXTURE_VERSION):
            continue
        invoice_date = _as_date(data["invoice_date"])
        if query.period_start <= invoice_date <= query.period_end:
            source = await _verify_source(repo, principal, row, scope)
            evidence_ids.add(source["id"])
            lines = data.get("lines")
            if not isinstance(lines, list):
                raise HTTPException(503, "Invoice diagnostic line data unavailable")
            evaluated = []
            for line in lines:
                wo = work_orders.get(line.get("work_order_id"))
                checks = {}
                delta = Decimal(0)
                if wo is None or wo["buildings"] != row["buildings"] or wo["data"].get("status") != "completed":
                    checks["work_order"] = "fail"
                else:
                    checks["work_order"] = "pass"
                    raised = _as_datetime(wo["data"]["raised_at"])
                    evidence_ids.add(wo["data"]["source_id"])
                    contract, contract_source = await _current_contract(repo, principal, query.vendor_id,
                                                          wo["buildings"][0], raised.date())
                    evidence_ids.add(contract_source["id"])
                    for field in ("labour_hours", "billed_hour_rate", "parts_qty", "billed_parts_total"):
                        value = line.get(field)
                        if value is not None and (not Decimal(str(value)).is_finite() or Decimal(str(value)) < 0):
                            raise HTTPException(409, "Invalid numeric invoice source value")
                    actual_hours = line.get("labour_hours")
                    attendance_hours = wo["data"].get("attendance_duration_hours")
                    if actual_hours is None or attendance_hours is None or Decimal(str(attendance_hours)) <= 0:
                        checks["labour_hours"] = "unavailable"
                    else:
                        actual_hours, attendance_hours = Decimal(str(actual_hours)), Decimal(str(attendance_hours))
                        checks["labour_hours"] = ("pass" if attendance_hours*Decimal("0.9") <= actual_hours
                                                  <= attendance_hours*Decimal("1.1") else "fail")
                    contracted_rate = contract["data"].get("labour_hour_rate")
                    if contracted_rate is None and contract["data"].get("labour_day_rate") is not None:
                        contracted_rate = Decimal(str(contract["data"]["labour_day_rate"])) / Decimal(8)
                    billed_rate = line.get("billed_hour_rate")
                    if contracted_rate is None:
                        checks["labour_rate"] = "not_applicable"
                    elif billed_rate is None or actual_hours is None:
                        checks["labour_rate"] = "unavailable"
                    else:
                        billed_rate = Decimal(str(billed_rate))
                        contracted_rate = Decimal(str(contracted_rate))
                        checks["labour_rate"] = "pass" if billed_rate <= contracted_rate else "fail"
                        delta += max(Decimal(0), billed_rate-contracted_rate) * actual_hours
                    parts_qty = line.get("parts_qty")
                    parts_total = line.get("billed_parts_total")
                    part_code = line.get("part_code")
                    expected_unit = contract["data"].get("parts_pricing", {}).get(part_code) if part_code else None
                    if expected_unit is None:
                        checks["parts_cost"] = "not_applicable"
                    elif parts_qty is None or parts_total is None:
                        checks["parts_cost"] = "unavailable"
                    else:
                        expected_total = Decimal(str(expected_unit)) * Decimal(str(parts_qty))
                        actual_parts = Decimal(str(parts_total))
                        tolerance = expected_total * Decimal("0.05")
                        checks["parts_cost"] = ("pass" if abs(actual_parts-expected_total) <= tolerance
                                                 else "fail")
                        delta += max(Decimal(0), actual_parts-expected_total)
                failed = any(value == "fail" for value in checks.values())
                unavailable = any(value == "unavailable" for value in checks.values())
                matched = checks.get("work_order") == "pass"
                cleared = matched and not failed and not unavailable
                adversary_required = delta > config.invoice_flag_adversary_gbp
                # Independent decimal recomputation compares billed and contract-backed totals;
                # mismatches remain flagged and cannot be described as reviewed or cleared.
                independently_computed = Decimal(0)
                if matched:
                    # Re-read the contract values and invoice quantities for the independent
                    # arithmetic check; do not reuse the matcher’s precomputed delta.
                    independent_rate = contract["data"].get("labour_hour_rate")
                    if independent_rate is None and contract["data"].get("labour_day_rate") is not None:
                        independent_rate = Decimal(str(contract["data"]["labour_day_rate"])) / Decimal(8)
                    if independent_rate is not None:
                        independent_rate_delta = max(Decimal(0),
                            Decimal(str(line.get("billed_hour_rate") or 0))
                            - Decimal(str(independent_rate))
                        ) * Decimal(str(line.get("labour_hours") or 0))
                        independently_computed += independent_rate_delta
                    independent_unit = contract["data"].get("parts_pricing", {}).get(line.get("part_code"))
                    if independent_unit is not None:
                        independent_expected = Decimal(str(independent_unit)) * Decimal(
                            str(line.get("parts_qty") or 0))
                        independently_computed += max(Decimal(0),
                            Decimal(str(line.get("billed_parts_total") or 0)) - independent_expected)
                arithmetic_agrees = independently_computed == delta
                evaluated.append({"line_id": line.get("line_id"),
                    "work_order_match": matched, "checks": checks,
                    "status": "cleared" if cleared else "flagged" if failed or not matched else "incomplete",
                    "delta_gbp": str(delta),
                    "adversary_gate": {"required": adversary_required,
                        "independent_arithmetic_agrees": arithmetic_agrees,
                        "status": "passed arithmetic" if adversary_required and arithmetic_agrees
                                 else "arithmetic mismatch" if adversary_required else "not required"},
                    "discrepancy": line.get("discrepancy") if failed or not matched else None})
            matched = sum(1 for line in evaluated if line["work_order_match"])
            cleared = sum(1 for line in evaluated if line["status"] == "cleared")
            flagged = sum(1 for line in evaluated if line["status"] == "flagged")
            unmatched = sum(1 for line in evaluated if not line["work_order_match"])
            invoices.append({"invoice_id": row["id"], "source_id": source["id"],
                             "invoice_date": str(invoice_date), "line_count": len(lines),
                             "matched_count": matched, "cleared_count": cleared,
                             "flagged_count": flagged, "unmatched_count": unmatched,
                             "counts_overlap": True,
                             "line_diagnostics": evaluated})
    return {"vendor_id": query.vendor_id,
            "reporting_period": {"start": str(query.period_start), "end": str(query.period_end)},
            "invoices": invoices[:query.limit], "invoice_count": len(invoices),
            "returned": min(len(invoices), query.limit), "complete": len(invoices) <= query.limit,
            "matched_definition": "line references a source work-order report",
            "cleared_definition": "matched line with every configured verification check passing",
            "flagged_definition": "line with at least one failed check; may also be unmatched",
            "unmatched_definition": "line has no source work-order match",
            "score_component": False,
            "building_scope": scope,
            "source_ids": sorted(evidence_ids),
            "source_versions": {identifier: (await repo.get("sources", identifier))["data"].get("version")
                                for identifier in sorted(evidence_ids)},
            "parameter_snapshot": {"invoice_flag_adversary_gbp": str(config.invoice_flag_adversary_gbp)}}


class ContractService:
    def __init__(self, store=None):
        self.store = store or runtime_store()

    async def execute(self, principal, tool, arguments, request_id):
        if not isinstance(principal, TestPrincipal):
            raise HTTPException(401, "Trusted principal required")
        if tool not in TOOL_ROLES or principal.role not in TOOL_ROLES[tool]:
            raise HTTPException(403, "Contract Performance operation not permitted")
        expected = {
            "contract_vendor_performance": set(PerformanceQuery.model_fields),
            "contract_invoice_diagnostic": set(InvoiceQuery.model_fields),
            "contract_get_scorecard": set(ScorecardQuery.model_fields),
            "contract_scoring_configuration": set(),
            "contract_prepare_scoring_configuration": set(ConfigProposal.model_fields),
        }[tool]
        if set(arguments) - expected:
            raise HTTPException(422, "Unsupported Contract Performance arguments")
        try:
            async with self.store.transaction(principal, repeatable=tool in {
                    "contract_invoice_diagnostic",
                    "contract_scoring_configuration"}) as repo:
                await repo.audit(tool, "capability_authorized", request_id=request_id)
                if tool == "contract_vendor_performance":
                    query = PerformanceQuery.model_validate(arguments)
                    await repo.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": principal.organization_id+":"+principal.user_id+":"+query.idempotency_key})
                    prior = next((row for row in await repo.list("records", "contract_scorecard")
                                  if row["data"].get("idempotency_key") == query.idempotency_key
                                  and row["data"].get("actor") == principal.user_id), None)
                    if prior and (prior["data"].get("vendor_id") != query.vendor_id
                                  or prior["data"].get("reporting_period") != {
                                      "start": str(query.period_start), "end": str(query.period_end)}
                                  or prior["buildings"] != _scope(principal, query.building_ids)):
                        raise HTTPException(409, "Idempotency key was already used for a different scorecard request")
                    result = prior["data"] if prior else await _performance(repo, principal, query)
                    if not prior:
                        record_id = str(uuid5(NAMESPACE_URL,
                            principal.organization_id+":"+principal.user_id+":"+query.idempotency_key))
                        result.update(idempotency_key=query.idempotency_key, configuration_kind="scorecard",
                                      actor=principal.user_id, scorecard_id=record_id)
                        await repo.insert("records", "contract_scorecard", result["building_scope"], result, record_id)
                    output = result
                elif tool == "contract_get_scorecard":
                    query = ScorecardQuery.model_validate(arguments)
                    scope = _scope(principal, query.building_ids)
                    row = await repo.get("records", query.scorecard_id)
                    if row["kind"] != "contract_scorecard" or not _in_scope(row, scope):
                        raise HTTPException(404, "Scorecard unavailable")
                    output = row["data"]
                elif tool == "contract_invoice_diagnostic":
                    output = await _invoice_diagnostic(repo, principal, InvoiceQuery.model_validate(arguments))
                    output["generated_at"] = datetime.now(timezone.utc).isoformat()
                    output["diagnostic_id"] = await repo.insert("history", "contract_invoice_diagnostic",
                        output["building_scope"], {**output, "actor": principal.user_id})
                elif tool == "contract_scoring_configuration":
                    if principal.role != "portfolio_manager":
                        raise HTTPException(403, "Portfolio Manager configuration permission required")
                    row = await _config(repo, principal)
                    output = {"organization_id": principal.organization_id,
                              "configuration_id": row["id"], "version": row["data"]["version"],
                              "parameters": row["data"]["parameters"],
                              "scope": "organization-wide scoring settings only"}
                else:
                    proposal = ConfigProposal.model_validate(arguments)
                    if principal.role != "portfolio_manager":
                        raise HTTPException(403, "Portfolio Manager configuration permission required")
                    current = await _config(repo, principal)
                    proposed = proposal.model_dump(mode="json", exclude={"reason"})
                    config = ScoringConfig(
                        weights=proposed["weights"],
                        recall_window_days=proposed["recall_window_days"],
                        score_cap=proposed["score_cap"],
                        ppm_tolerance_days=proposed["ppm_tolerance_days"],
                        cost_variance_alert_pct=Decimal(proposed["cost_variance_alert_pct"]),
                        cost_variance_alert_jobs=proposed["cost_variance_alert_jobs"],
                        invoice_flag_adversary_gbp=Decimal(proposed["invoice_flag_adversary_gbp"]),
                    ).validated()
                    proposed["weights"] = dict(config.weights)
                    proposed["cost_variance_alert_pct"] = str(config.cost_variance_alert_pct)
                    preview = {"organization_id": principal.organization_id,
                               "actor": principal.user_id, "role": principal.role,
                               "current_version": current["data"]["version"],
                               "current_parameters": current["data"]["parameters"],
                               "proposed_parameters": proposed, "reason": proposal.reason,
                               "impact": "all future organization vendor scorecards; previous snapshots remain unchanged",
                               "status": "awaiting_confirmation"}
                    preview_hash = _hash(preview)
                    identifier = str(uuid5(NAMESPACE_URL,
                        principal.organization_id+":"+request_id+":"+preview_hash))
                    await repo.insert("proposals", "contract_scoring_configuration", [],
                        {**preview, "preview_hash": preview_hash, "proposal_id": identifier,
                         "actor_id": principal.user_id, "created_at": datetime.now(timezone.utc).isoformat()},
                        identifier)
                    output = {**preview, "proposal_id": identifier, "preview_hash": preview_hash}
                await repo.audit(tool, "succeeded", target={"operation": tool}, request_id=request_id)
            return {"tool": tool, "result": output, "request_id": request_id,
                    "policy_version": "hw1-contract-v1"}
        except ValidationError:
            await self.store.audit(principal, tool, "invalid_arguments", request_id=request_id)
            raise HTTPException(422, "Invalid declared Contract Performance arguments") from None
        except (ValueError, TypeError, KeyError, ArithmeticError):
            await self.store.audit(principal, tool, "source_unassessable", request_id=request_id)
            raise HTTPException(409, "Contract source values are missing or incompatible; no result claimed") from None
        except HTTPException as exc:
            await self.store.audit(principal, tool, "denied_or_unavailable_"+str(exc.status_code),
                                   request_id=request_id)
            raise
        except SQLAlchemyError:
            raise HTTPException(503, "Contract Performance store unavailable; no success claimed") from None

    async def confirm_configuration(self, principal, identifier, confirmation, request_id):
        if principal.role != "portfolio_manager":
            raise HTTPException(403, "Portfolio Manager configuration permission required")
        try:
            parsed = ConfigConfirmation.model_validate(confirmation)
        except ValidationError:
            raise HTTPException(422, "Invalid configuration confirmation") from None
        try:
            async with self.store.transaction(principal) as repo:
                proposal = await repo.get("proposals", identifier, lock=True)
                data = proposal["data"]
                if proposal["kind"] != "contract_scoring_configuration" or data.get("actor_id") != principal.user_id:
                    raise HTTPException(404, "Configuration proposal unavailable")
                if data.get("status") == "confirmed" and data.get("idempotency_key") == parsed.idempotency_key:
                    if (data.get("preview_hash") != parsed.preview_hash
                            or data.get("current_version") != parsed.expected_version):
                        raise HTTPException(409, "Changed configuration confirmation")
                    return {"outcome": "already_confirmed", "configuration_version": data["result_version"]}
                if data.get("status") != "awaiting_confirmation":
                    raise HTTPException(409, "Configuration proposal is no longer pending")
                if data.get("preview_hash") != parsed.preview_hash or data.get("current_version") != parsed.expected_version:
                    raise HTTPException(409, "Configuration preview is stale or changed")
                config_row = await _config(repo, principal, lock=True)
                if config_row["data"]["version"] != parsed.expected_version:
                    raise HTTPException(409, "Configuration changed; prepare a new preview")
                next_version = parsed.expected_version + 1
                next_data = {"version": next_version, "parameters": data["proposed_parameters"],
                             "updated_at": datetime.now(timezone.utc).isoformat(),
                             "updated_by": principal.user_id, "reason": data["reason"]}
                await repo.update("records", config_row,
                                  {**config_row["data"], **next_data})
                await repo.update("proposals", proposal,
                    {**data, "status": "confirmed", "idempotency_key": parsed.idempotency_key,
                     "result_version": next_version, "confirmed_at": datetime.now(timezone.utc).isoformat()})
                await repo.insert("history", "contract_scoring_configuration", [],
                    {"organization_id": principal.organization_id, "actor": principal.user_id,
                     "role": principal.role, "before_version": parsed.expected_version,
                     "after_version": next_version, "before": config_row["data"]["parameters"],
                     "after": data["proposed_parameters"], "reason": data["reason"],
                     "proposal_id": identifier, "idempotency_key": parsed.idempotency_key,
                     "confirmed_at": datetime.now(timezone.utc).isoformat()})
                await repo.audit("contract_scoring_configuration.confirm", "succeeded",
                    {"proposal_id": identifier, "configuration_version": next_version}, request_id)
            return {"outcome": "confirmed", "configuration_version": next_version,
                    "historical_scorecards_unchanged": True}
        except HTTPException as exc:
            await self.store.audit(principal, "contract_scoring_configuration.confirm",
                "denied_or_conflict_"+str(exc.status_code), {"proposal_id": identifier}, request_id)
            raise
        except SQLAlchemyError:
            raise HTTPException(503, "Configuration transaction unavailable; no success claimed") from None


def runtime_service():
    return ContractService()
