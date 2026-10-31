"""Authorized energy readings and compatible, source-preserving calculations."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib, json
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from fastapi import HTTPException
from ...api.homework_identity import TestPrincipal
from ..compliance_hw1.store import runtime_store
from .contracts import EnergyQuery, AnomalyQuery, AnomalyAction, AnomalyList
from .anomaly_detection import detect as detect_anomalies

MANAGERS = {"property_manager", "portfolio_manager"}
TOOLS = {"energy_readings", "energy_eui", "energy_anomaly_scan", "energy_anomaly_list", "energy_anomaly_act"}
TOOLS.add("energy_benchmarks")
TOOLS.update({"energy_gap_scan", "energy_gap_retry", "energy_remediation_queue", "energy_monthly_report", "energy_get_monthly_report"})
TM46_PATH = Path(__file__).resolve().parent / "reference" / "cibse_tm46_uk.json"


def tm46_pack():
    try:
        pack = json.loads(TM46_PATH.read_text(encoding="utf-8"))
        if (pack.get("unit") != "kWh/m2/year" or not isinstance(pack.get("building_types"), dict)
                or not pack["building_types"]):
            raise ValueError
        return pack
    except Exception:
        raise HTTPException(503, "Energy benchmark reference unavailable") from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class EnergyService:
    def __init__(self, store=None):
        self.store = store or runtime_store()

    async def execute(self, principal: TestPrincipal, tool: str, raw: dict, request_id=None):
        if not isinstance(principal, TestPrincipal):
            raise HTTPException(401, "Trusted principal required")
        if principal.role not in MANAGERS or tool not in TOOLS:
            await self.store.audit(principal, str(tool)[:100], "capability_denied",
                                   {"building_id": raw.get("building_id") if isinstance(raw, dict) else None}, request_id)
            raise HTTPException(403, "Energy access is not available to this role")
        if tool in {"energy_gap_scan", "energy_gap_retry", "energy_remediation_queue", "energy_monthly_report", "energy_get_monthly_report"}:
            from .workflows import execute_workflow
            return await execute_workflow(self, principal, tool, raw, request_id)
        if tool == "energy_anomaly_act":
            try:
                action = AnomalyAction.model_validate(raw)
            except Exception as exc:
                raise HTTPException(422, "Invalid anomaly action") from exc
            return {"result": await self._act_on_anomaly(principal, action, request_id)}
        if tool == "energy_benchmarks":
            if raw:
                raise HTTPException(422, "Energy benchmark listing accepts no filters")
            pack = tm46_pack()
            return {"result": {"pack": pack["pack"], "unit": pack["unit"],
                "note": pack["note"], "building_types": pack["building_types"]},
                "data_source": "bundled legacy Hoistra reference data"}
        if tool == "energy_anomaly_list":
            try:
                query = AnomalyList.model_validate(raw)
            except Exception as exc:
                raise HTTPException(422, "Invalid anomaly queue filter") from exc
            if query.building_id not in principal.building_ids:
                raise HTTPException(403, "Building access denied")
            return {"result": await self._list_anomalies(principal, query.building_id, query.status)}
        try:
            query = (AnomalyQuery.model_validate(raw) if tool == "energy_anomaly_scan"
                     else EnergyQuery.model_validate(raw))
        except Exception as exc:
            raise HTTPException(422, "Invalid energy period or scope") from exc
        if query.building_id not in principal.building_ids:
            await self.store.audit(principal, tool, "scope_denied", {"building_id": query.building_id}, request_id)
            raise HTTPException(403, "Building access denied")
        if tool == "energy_anomaly_scan":
            return await self._anomaly_scan(principal, query, request_id)
        async with self.store.transaction(principal, repeatable=True) as repo:
            meters = [r for r in await repo.list("records", "energy_meter")
                      if r["id"] == (query.meter_id or query.building_id + "-energy-meter-1")
                      and r["buildings"] == [query.building_id]
                      and r["data"].get("fixture_version") == "energy-fixtures-v1"]
            if len(meters) != 1:
                raise HTTPException(404 if query.meter_id else 503, "Authorized meter source unavailable")
            meter = meters[0]
            meter_source = await self._source(repo, principal, meter, query.building_id)
            if query.section_id and query.section_id != meter["data"].get("section_id"):
                raise HTTPException(404, "Energy section unavailable")
            rows = [r for r in await repo.list("records", "energy_reading")
                    if r["buildings"] == [query.building_id]
                    and r["data"].get("meter_id") == meter["id"]
                    and r["data"].get("fixture_version") == "energy-fixtures-v1"]
            selected = []
            for row in rows:
                source = await self._source(repo, principal, row, query.building_id)
                stamp = datetime.fromisoformat(row["data"]["timestamp"].replace("Z", "+00:00"))
                if stamp.date() >= query.period_start and stamp.date() <= query.period_end:
                    if row["data"].get("period_start") and (row["data"]["period_start"] != str(query.period_start)
                            or row["data"].get("period_end") != str(query.period_end)):
                        raise HTTPException(422, "Period reading cannot be prorated into a different period")
                    if stamp.tzinfo is None or row["data"].get("unit") != "kWh":
                        raise HTTPException(422, "Reading timestamp or unit is incompatible")
                    amount = Decimal(str(row["data"]["consumption"]))
                    if not amount.is_finite() or amount < 0:
                        raise HTTPException(409, "Invalid consumption source value")
                    selected.append((row, source, stamp))
            if len({stamp for _, _, stamp in selected}) != len(selected):
                raise HTTPException(409, "Duplicate reading periods require source review")
            if len({row["data"].get("interval") for row, _, _ in selected}) > 1:
                raise HTTPException(409, "Overlapping reading interval types cannot be summed")
            selected.sort(key=lambda x: x[2])
            unit = meter["data"].get("unit")
            if not unit or unit != "kWh":
                raise HTTPException(422, "Meter unit is not compatible with kWh EUI")
            tariff = None
            tariff_raw = meter["data"].get("tariff_gbp_per_kwh")
            if tariff_raw is not None:
                try:
                    tariff = Decimal(str(tariff_raw))
                except (InvalidOperation, TypeError, ValueError):
                    raise HTTPException(409, "Meter tariff source is invalid") from None
                if not tariff.is_finite() or tariff < 0:
                    raise HTTPException(409, "Meter tariff source is invalid")
            total = sum((Decimal(str(r["data"]["consumption"])) for r, _, _ in selected), Decimal(0))
            period_days = (query.period_end - query.period_start).days + 1
            hourly_period = bool(selected) and all(row["data"].get("interval")=="hour" for row,_,_ in selected)
            missing_hours = period_days*24-len(selected) if hourly_period else None
            result = {"building_id": query.building_id, "meter_id": meter["id"],
                      "section_id": meter["data"].get("section_id"), "period_start": query.period_start.isoformat(),
                      "period_end": query.period_end.isoformat(), "period_days_inclusive": period_days,
                      "reading_count": len(selected), "consumption_kwh": str(total) if selected else None, "unit": unit,
                      "source_ids": [meter_source["id"], *[source["id"] for _, source, _ in selected]],
                      "freshness": selected[-1][2].isoformat() if selected else None}
            result["coverage"] = {"missing_hourly_periods":missing_hours,
                "status": "complete" if hourly_period and missing_hours==0 else "incomplete" if hourly_period else "unknown",
                "complete":missing_hours==0 if hourly_period else None,
                "basis":"source hourly intervals" if hourly_period else "source-defined period totals"}
            if result["coverage"]["status"] == "unknown":
                result["limitations"] = [{"field": "coverage", "status": "unknown",
                    "reason": "source_defined_period_total_has_no_hourly_coverage_measurement"}]
            elif result["coverage"]["status"] == "incomplete":
                result["limitations"] = [{"field": "coverage", "status": "incomplete",
                    "missing_hourly_periods": missing_hours}]
            if tool == "energy_eui":
                profiles = [r for r in await repo.list("records", "energy_profile")
                            if r["buildings"] == [query.building_id]
                            and r["id"] == meter["data"].get("profile_id", query.building_id + "-energy-profile-1")
                            and r["data"].get("fixture_version") == "energy-fixtures-v1"]
                if len(profiles) != 1:
                    raise HTTPException(503, "Authorized building area source unavailable")
                profile = profiles[0]
                profile_source = await self._source(repo, principal, profile, query.building_id)
                result["source_ids"].append(profile_source["id"])
                area = Decimal(str(profile["data"].get("floor_area_m2", "0")))
                if area <= 0 or profile["data"].get("area_unit") != "m2" or not selected or (missing_hours is not None and missing_hours>0):
                    result["eui_available"] = False
                    result["unavailable_reason"] = "incomplete_hourly_period" if missing_hours else "missing_readings_or_positive_compatible_area"
                else:
                    result.update({"eui_available": True, "floor_area_m2": str(area),
                        "eui_kwh_per_m2_period": str(total / area),
                        "annualized_eui_kwh_per_m2_year": str(total / area * Decimal(365) / period_days),
                        "annualization_basis": "inclusive_period_days",
                        "calculation_basis": {"period_eui": "consumption_kwh / floor_area_m2",
                            "annualized_eui": "eui_kwh_per_m2_period * 365 / period_days_inclusive",
                            "annualized_is_estimate": True},
                        "area_source_id": profile_source["id"]})
                    profile_type = str(profile["data"].get("building_type") or "").strip().casefold()
                    meter_type = meter["data"].get("meter_type")
                    pack = tm46_pack()
                    category = pack["building_types"].get(profile_type)
                    benchmark = category.get(meter_type) if category else None
                    result["benchmark_source"] = {"pack": pack["pack"], "unit": pack["unit"],
                                                   "reference_sha256":hashlib.sha256(TM46_PATH.read_bytes()).hexdigest(),
                                                   "profile_building_type": profile_type or None}
                    if isinstance(benchmark, (int, float)) and benchmark > 0:
                        annual_eui = total / area * Decimal(365) / period_days
                        benchmark_value = Decimal(str(benchmark))
                        expected_kwh = benchmark_value * area * Decimal(period_days) / Decimal(365)
                        excess_kwh = max(Decimal(0), total - expected_kwh)
                        result.update({"benchmark_available": True, "benchmark_label": category["label"],
                            "benchmark_kwh_per_m2_year": str(benchmark_value),
                            "deviation_pct": str(((annual_eui - benchmark_value) * 100 / benchmark_value).quantize(Decimal("0.01"))),
                            "benchmark_excess_kwh": str(excess_kwh),
                            "benchmark_period_consumption_kwh": str(expected_kwh),
                            "benchmark_period_formula": "benchmark_kwh_per_m2_year * floor_area_m2 * period_days_inclusive / 365",
                            "financial_excess_formula": "benchmark_excess_kwh * tariff_gbp_per_kwh",
                            "tariff_gbp_per_kwh": str(tariff) if tariff is not None else None,
                            "tariff_source_id": meter_source["id"] if tariff is not None else None,
                            "financial_excess_gbp": str((excess_kwh * tariff).quantize(Decimal("0.01"))) if tariff is not None else None,
                            "financial_excess_available": tariff is not None})
                    else:
                        result.update({"benchmark_available": False,
                            "benchmark_unavailable_reason": "building type has no matching TM46 electricity category",
                            "benchmark_kwh_per_m2_year": None, "deviation_pct": None,
                            "benchmark_excess_kwh": None, "financial_excess_gbp": None})
            await repo.audit(tool, "succeeded", {"building_id": query.building_id,
                "period_start": query.period_start.isoformat(), "period_end": query.period_end.isoformat()}, request_id)
            result["source_versions"] = {sid:(await repo.get("sources",sid))["data"].get("version") for sid in result["source_ids"]}
            return {"result": result, "data_source": "fictional isolated source records"}

    async def _anomaly_scan(self, principal, query, request_id):
        """Run comparable-hour checks over verified isolated readings only."""
        async with self.store.transaction(principal) as repo:
            from .workflows import lock
            await lock(repo,"energy-scan:"+(query.meter_id or query.building_id+"-energy-meter-1"))
            start = query.as_of.toordinal() - 34
            meters = [r for r in await repo.list("records", "energy_meter")
                      if r["id"] == (query.meter_id or query.building_id + "-energy-meter-1")
                      and r["buildings"] == [query.building_id]
                      and r["data"].get("fixture_version") == "energy-fixtures-v1"]
            if len(meters) != 1:
                raise HTTPException(404 if query.meter_id else 503, "Authorized meter source unavailable")
            meter = meters[0]
            meter_source = await self._source(repo, principal, meter, query.building_id)
            if query.section_id and query.section_id != meter["data"].get("section_id"):
                raise HTTPException(404, "Energy section unavailable")
            if meter["data"].get("unit") != "kWh":
                raise HTTPException(422, "Anomaly scan requires a kWh meter")
            tariff_raw = meter["data"].get("tariff_gbp_per_kwh")
            try:
                tariff = Decimal(str(tariff_raw)) if tariff_raw is not None else None
            except (InvalidOperation, TypeError, ValueError):
                raise HTTPException(409, "Meter tariff source is invalid") from None
            if tariff is not None and (not tariff.is_finite() or tariff < 0):
                raise HTTPException(409, "Meter tariff source is invalid")

            rows = [r for r in await repo.list("records", "energy_reading")
                    if r["buildings"] == [query.building_id]
                    and r["data"].get("fixture_version") == "energy-fixtures-v1"]
            hourly = []
            source_ids = [meter_source["id"]]
            end_ordinal = query.as_of.toordinal()
            for row in rows:
                if row["data"].get("meter_id") != meter["id"]:
                    continue
                source = await self._source(repo, principal, row, query.building_id)
                stamp = datetime.fromisoformat(row["data"]["timestamp"].replace("Z", "+00:00"))
                if (start <= stamp.date().toordinal() <= end_ordinal
                        and row["data"].get("interval") == "hour"):
                    if stamp.tzinfo is None:
                        raise HTTPException(409, "Reading timezone is missing")
                    if row["data"].get("unit") != "kWh":
                        raise HTTPException(422, "Hourly reading unit is not compatible with kWh")
                    try:
                        amount = Decimal(str(row["data"]["consumption"]))
                    except (InvalidOperation, TypeError, ValueError):
                        raise HTTPException(409, "Hourly reading source value is invalid") from None
                    if not amount.is_finite():
                        raise HTTPException(409, "Hourly reading source value is invalid")
                    hourly.append((stamp, amount))
                    source_ids.append(source["id"])
            occupancy = [r for r in await repo.list("records", "occupancy_log")
                         if r["buildings"] == [query.building_id]
                         and r["data"].get("meter_id") in {None, meter["id"]}
                         and r["data"].get("fixture_version") == "energy-fixtures-v1"]
            occupancy_events = []
            for row in occupancy:
                source = await self._source(repo, principal, row, query.building_id)
                changed = row["data"].get("changed_at")
                state = row["data"].get("occupancy_state")
                if not changed or not state:
                    continue
                stamp = datetime.fromisoformat(changed.replace("Z", "+00:00"))
                if stamp.date().toordinal() <= end_ordinal:
                    occupancy_events.append((stamp, state))
                    source_ids.append(source["id"])

            scan_start = datetime.fromordinal(start).date()
            occupancy_known = any(stamp.date().toordinal() < start for stamp, _ in occupancy_events)
            changes=[]
            prior_state=None
            for stamp,state in sorted(occupancy_events):
                if prior_state is not None and state!=prior_state and stamp.date().toordinal()>=start:
                    changes.append(stamp)
                prior_state=state
            if len({stamp for stamp,_ in occupancy_events}) != len(occupancy_events):
                occupancy_known=False
            evaluation = detect_anomalies(
                hourly, as_of=query.as_of, tariff=tariff,
                is_sub_meter=bool(meter["data"].get("is_sub_meter", False)))
            reason = evaluation.get("reason")
            findings = evaluation.get("findings", [])
            if not occupancy_known:
                reason = "source-verified occupancy state is unavailable"
                findings = []
            else:
                latest_occupancy = max(occupancy_events, key=lambda item: item[0])
                changed_in_window = bool(changes)
                if changed_in_window:
                    findings = [item for item in findings if item["type"] != "baseline_drift"]
                for item in findings:
                    item["occupancy_state_at_window_end"] = latest_occupancy[1]
                if findings and tariff is None:
                    reason = "tariff evidence is missing, so anomaly cost cannot be calculated"
                    findings = []
            available = bool(evaluation.get("available")) and occupancy_known and not (
                reason and "tariff evidence" in reason)
            status = "scanned" if available else "insufficient_comparable_evidence"
            anomaly_ids = []
            if findings:
                existing = await repo.list("history", "energy_anomaly")
                for finding in findings:
                    anomaly_id = uuid5(NAMESPACE_URL, "hw1-energy-anomaly:" + meter["id"] + ":" + finding["type"] + ":" + finding["window_start"] + ":" + digest({"sources":sorted(set(source_ids)),"finding":finding})).hex
                    anomaly_ids.append(anomaly_id)
                    if not any(row["id"] == anomaly_id for row in existing):
                        from ..compliance_hw1.outbox import notify_managers
                        notices = await notify_managers(repo,query.building_id,"energy-anomaly-"+anomaly_id,
                            {"meter_id":meter["id"],"anomaly_id":anomaly_id,"type":finding["type"],
                             "window_start":finding["window_start"],"window_cost_gbp":finding["financial_gbp"]},"energy_action_needed")
                        await repo.insert("history", "energy_anomaly", [query.building_id], {
                            "anomaly_id": anomaly_id, "building_id": query.building_id,
                            "meter_id": meter["id"],
                            "finding": finding, "source_ids": sorted(set(source_ids)), "status": "open",
                            "notifications":notices,
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "actor": principal.user_id}, anomaly_id)
            await repo.audit("energy_anomaly_scan", status,
                {"building_id": query.building_id, "window_start": query.as_of.fromordinal(start).isoformat(),
                 "window_end": query.as_of.isoformat(), "hourly_count": len(hourly),
                 "occupancy_change_count": len(occupancy_events), "finding_count": len(findings)}, request_id)
            return {"result": {"building_id": query.building_id,
                "window_start": query.as_of.fromordinal(start).isoformat(), "window_end": query.as_of.isoformat(),
                "hourly_reading_count": len(hourly), "source_ids": sorted(set(source_ids)),
                "meter_id": meter["id"],
                "anomalies": findings, "anomaly_ids": anomaly_ids, "evaluation_status": status,
                "unavailable_reason": reason, "tariff_gbp_per_kwh": str(tariff) if tariff is not None else None,
                "occupancy_state_at_window_end": max(occupancy_events, key=lambda item: item[0])[1] if occupancy_events else None,
                "baseline_drift_suppressed": bool(occupancy_known and changes),
                "rule": "35-day scan; 48-hour weekend/hour matched median; baseline drift suppressed by an occupancy change"},
                "data_source": "fictional isolated source records"}

    async def _list_anomalies(self, principal, building_id, status):
        async with self.store.transaction(principal, repeatable=True) as repo:
            anomalies = [row for row in await repo.list("history", "energy_anomaly")
                         if row["buildings"] == [building_id]]
            actions = [row for row in await repo.list("history", "energy_anomaly_action")
                       if row["buildings"] == [building_id]]
            latest = {}
            for row in actions:
                event = row["data"]
                prior = latest.get(event["anomaly_id"])
                if prior is None or event["generated_at"] > prior["generated_at"]:
                    latest[event["anomaly_id"]] = event
            results = []
            for row in anomalies:
                item = row["data"]
                action = latest.get(item["anomaly_id"], {}).get("action")
                current = {"acknowledge":"acknowledged", "monitor":"monitoring",
                           "mark_expected":"expected"}.get(action, "open")
                if (status == "open" and current != "open") or (status != "open" and current != status):
                    continue
                results.append({**item, "status": current, "last_action": latest.get(item["anomaly_id"])})
            return {"building_id": building_id, "status": status, "anomalies": results,
                    "data_source": "fictional isolated source records"}

    async def _act_on_anomaly(self, principal, action, request_id):
        async with self.store.transaction(principal) as repo:
            from .workflows import lock
            await lock(repo,"energy-disposition:"+action.anomaly_id)
            row = await repo.get("history", action.anomaly_id)
            if row["kind"] != "energy_anomaly" or row["buildings"] != [row["data"].get("building_id")]:
                raise HTTPException(404, "Energy anomaly unavailable")
            building_id = row["data"].get("building_id")
            if building_id not in principal.building_ids:
                raise HTTPException(404, "Energy anomaly unavailable")
            for source_id in action.evidence_source_ids:
                evidence = await repo.get("sources",source_id)
                if evidence["org"] != principal.organization_id or evidence["buildings"] != [building_id]:
                    raise HTTPException(404,"Anomaly action evidence unavailable")
            actions = [event for event in await repo.list("history", "energy_anomaly_action")
                       if event["data"].get("anomaly_id") == action.anomaly_id]
            latest = max((event["data"] for event in actions),
                         key=lambda item: item["generated_at"], default=None)
            if (latest and latest.get("action") == action.action and latest.get("reason") == action.reason
                    and latest.get("evidence_source_ids",[]) == action.evidence_source_ids):
                return {"anomaly_id": action.anomaly_id, "status": action.action,
                        "outcome": "already_applied", "event_id": latest["event_id"]}
            predecessor = latest.get("event_id", "initial") if latest else "initial"
            event_id = uuid5(NAMESPACE_URL, "hw1-energy-anomaly-action:" + action.anomaly_id + ":" + principal.user_id + ":" + predecessor + ":" + action.action + ":" + (action.reason or "")).hex
            await repo.insert("history", "energy_anomaly_action", [building_id], {
                "event_id": event_id, "anomaly_id": action.anomaly_id, "building_id": building_id,
                "action": action.action, "reason": action.reason, "evidence_source_ids":action.evidence_source_ids,
                "actor": principal.user_id,
                "role": principal.role, "generated_at": datetime.now(timezone.utc).isoformat()}, event_id)
            await repo.audit("energy_anomaly_act", "succeeded", {"anomaly_id": action.anomaly_id,
                "action": action.action}, request_id)
            return {"anomaly_id": action.anomaly_id, "status": action.action,
                    "outcome": "recorded", "event_id": event_id}

    async def _source(self, repo, principal, row, building):
        source_id = row["data"].get("source_id")
        if not source_id:
            raise HTTPException(503, "Source evidence unavailable")
        source = await repo.get("sources", source_id)
        fields = source["data"].get("fields")
        if (source["org"] != principal.organization_id or source["buildings"] != [building]
                or not isinstance(fields, dict) or digest(fields) != source["data"].get("content_sha256")
                or any(row["data"].get(key) != value for key, value in fields.items())):
            raise HTTPException(409, "Energy source integrity or scope check failed")
        if source["data"].get("text") is not None:
            if source["data"]["text"] != json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str):
                raise HTTPException(409, "Energy source differs from retained original")
        return source
