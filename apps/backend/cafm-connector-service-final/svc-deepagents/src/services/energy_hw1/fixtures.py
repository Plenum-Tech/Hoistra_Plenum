"""Small fictional source set; inputs and expected arithmetic are explicit."""
import hashlib
import json


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dataset():
    building, org = "hw1-building-a1", "hw1-org-a"
    records, sources = [], []

    def add(identifier, kind, fields):
        source_id = identifier + "-source"
        row = {"id": identifier, "kind": kind, "org": org, "buildings": [building],
               "data": {**fields, "source_id": source_id, "fixture_version": "energy-fixtures-v1"}}
        source = {"id": source_id, "kind": "energy_source", "org": org, "buildings": [building],
                  "data": {"fields": row["data"], "content_sha256": _digest(row["data"]),
                           "version": 1, "information_type": "energy", "scope_verified": True}}
        records.append(row)
        sources.append(source)
        return row

    add(building + "-energy-meter-1", "energy_meter",
        {"unit": "kWh", "section_id": "hw1-section-east", "meter_type": "electricity"})
    for reference, stamp, amount in [
        ("r1", "2026-10-03T00:00:00+00:00", "1250"),
        ("r2", "2026-10-18T00:00:00+00:00", "1750"),
    ]:
        add(building + "-energy-reading-" + reference, "energy_reading",
            {"meter_id": building + "-energy-meter-1", "timestamp": stamp,
             "consumption": amount, "unit": "kWh", "interval": "period"})
    add(building + "-energy-profile-1", "energy_profile",
        {"floor_area_m2": "1000", "area_unit": "m2", "building_type": "fictional-office"})
    milestone_dataset(records, sources)
    return {"records": records, "sources": sources}


def milestone_dataset(records, sources):
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    building = "hw1-building-a1"

    def add(identifier, kind, fields):
        source_id = identifier + "-source"
        data = {**fields, "source_id": source_id, "fixture_version": "energy-fixtures-v1"}
        original = json.dumps(data, sort_keys=True, separators=(",", ":"))
        records.append({"id": identifier, "kind": kind, "org": "hw1-org-a", "buildings": [building], "data": data})
        sources.append({"id": source_id, "kind": "energy_source", "org": "hw1-org-a", "buildings": [building],
            "data": {"fields": data, "text": original, "content_sha256": _digest(data), "version": 1,
                     "information_type": "energy", "scope_verified": True}})

    profile = "hw1-energy-m2-office-profile"
    add(profile, "energy_profile", {"floor_area_m2": "100", "area_unit": "m2", "building_type": "office"})
    start = datetime(2026,9,21,tzinfo=timezone.utc)
    end = datetime(2026,10,26,tzinfo=timezone.utc)
    for scenario in ("spike", "drift", "changed", "gap", "gap-unknown", "condition", "badunit"):
        meter = "hw1-energy-m2-" + scenario
        add(meter, "energy_meter", {"unit": "kWh" if scenario != "badunit" else "m3",
            "section_id": "hw1-section-energy-m2", "meter_type": "electricity", "profile_id": profile,
            "tariff_gbp_per_kwh": "0.20", "is_sub_meter": scenario == "spike",
            "expected_interval_minutes": 60, "dcc_simulation": "unknown" if scenario=="gap-unknown" else "known_failure"})
        if scenario in {"spike", "drift", "changed"}:
            add(meter+"-occupancy-initial", "occupancy_log", {"meter_id": meter,
                "changed_at": "2026-09-01T00:00:00+00:00", "occupancy_state": "normal"})
            if scenario == "changed":
                add(meter+"-occupancy-change", "occupancy_log", {"meter_id": meter,
                    "changed_at": "2026-10-19T00:00:00+00:00", "occupancy_state": "extended_hours"})
            for index in range(840):
                stamp = start + timedelta(hours=index)
                amount = Decimal(5 if stamp.weekday()>=5 else 20 if 8<=stamp.hour<18 else 10)
                if scenario == "spike" and stamp >= end-timedelta(hours=48): amount *= 2
                if scenario in {"drift","changed"} and stamp >= end-timedelta(days=7): amount *= Decimal("1.5")
                add(meter+"-r-"+str(index), "energy_reading", {"meter_id": meter,
                    "timestamp": stamp.isoformat(), "consumption": str(amount), "unit": "kWh", "interval": "hour"})
        elif scenario in {"gap","gap-unknown"}:
            for hour in range(24):
                if hour in {2,3}: continue
                add(meter+"-r-"+str(hour), "energy_reading", {"meter_id": meter,
                    "timestamp": datetime(2026,10,20,hour,tzinfo=timezone.utc).isoformat(),
                    "consumption": "10", "unit": "kWh", "interval": "hour"})
        elif scenario == "condition":
            add(meter+"-r", "energy_reading", {"meter_id": meter,"timestamp":"2026-10-01T00:00:00+00:00",
                "consumption":"1200","unit":"kWh","interval":"period",
                "period_start":"2026-10-01","period_end":"2026-10-31"})
            for suffix, narrative in [("repair","Inspection confirms severe corrosion and failed fan."),
                                      ("negated","Inspection found no failed fan and no corrosion.")]:
                add(meter+"-asset-"+suffix,"energy_asset_profile", {"meter_id":meter,
                    "asset_id":"hw1-energy-asset-"+suffix,"condition_score":2,"inspection_text":narrative,
                    "repair_cost_gbp":"100","replace_cost_gbp":"1000"})
