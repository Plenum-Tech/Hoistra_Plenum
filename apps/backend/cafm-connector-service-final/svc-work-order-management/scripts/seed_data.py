"""BE2-05 — Seed sample assets, locations, work orders, and journey logs.

Usage:
    cd svc-work-order-management
    python -m scripts.seed_data

Requires DATABASE_URL env var pointing at a live PostgreSQL instance (or SQLite for dev).
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

# Allow running from the service root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from src.models.base import Base
from src.models.asset import Asset
from src.models.location import Location
from src.models.work_order import WorkOrder
from src.services.journey_service import create_journey_for_work_order


_now = datetime.utcnow()

_ASSETS = [
    # Air Handling Units
    {"asset_id": "AHU-001", "asset_name": "Air Handling Unit 1",    "asset_type": "Air Handler",   "category": "HVAC",          "location": "Level 3 Plant Room",  "manufacturer": "Carrier",   "model": "AHU-3000",   "serial_number": "CAR-AHU-001", "criticality_level": "high",     "condition": "good",      "operating_hours": 8760,  "replacement_cost": 45000.00, "next_maintenance_date": _now + timedelta(days=30)},
    {"asset_id": "AHU-002", "asset_name": "Air Handling Unit 2",    "asset_type": "Air Handler",   "category": "HVAC",          "location": "Level 5 Plant Room",  "manufacturer": "Carrier",   "model": "AHU-3000",   "serial_number": "CAR-AHU-002", "criticality_level": "medium",   "condition": "good",      "operating_hours": 6500,  "replacement_cost": 45000.00, "next_maintenance_date": _now + timedelta(days=60)},
    {"asset_id": "AHU-003", "asset_name": "Air Handling Unit 3",    "asset_type": "Air Handler",   "category": "HVAC",          "location": "Basement Plant Room", "manufacturer": "Trane",     "model": "M-Series",   "serial_number": "TRN-AHU-003", "criticality_level": "medium",   "condition": "fair",      "operating_hours": 9200,  "replacement_cost": 38000.00, "next_maintenance_date": _now - timedelta(days=10)},   # overdue
    # Chillers
    {"asset_id": "CH-001",  "asset_name": "Chiller Unit 1",         "asset_type": "Chiller",       "category": "Cooling System","location": "Basement Plant Room", "manufacturer": "York",      "model": "YK-1200",    "serial_number": "YRK-CH-001",  "criticality_level": "critical", "condition": "excellent", "operating_hours": 12000, "replacement_cost": 120000.00,"next_maintenance_date": _now + timedelta(days=45)},
    {"asset_id": "CH-002",  "asset_name": "Chiller Unit 2",         "asset_type": "Chiller",       "category": "Cooling System","location": "Basement Plant Room", "manufacturer": "York",      "model": "YK-1200",    "serial_number": "YRK-CH-002",  "criticality_level": "critical", "condition": "good",      "operating_hours": 10500, "replacement_cost": 120000.00,"next_maintenance_date": _now + timedelta(days=90)},
    # Generators
    {"asset_id": "GEN-001", "asset_name": "Emergency Generator 1",  "asset_type": "Generator",     "category": "Power System",  "location": "Ground Floor Plant",  "manufacturer": "Cummins",   "model": "C1100D5",    "serial_number": "CMN-GEN-001", "criticality_level": "critical", "condition": "good",      "operating_hours": 500,   "replacement_cost": 180000.00,"next_maintenance_date": _now - timedelta(days=5)},    # overdue
    {"asset_id": "GEN-002", "asset_name": "Emergency Generator 2",  "asset_type": "Generator",     "category": "Power System",  "location": "Ground Floor Plant",  "manufacturer": "Cummins",   "model": "C1100D5",    "serial_number": "CMN-GEN-002", "criticality_level": "critical", "condition": "good",      "operating_hours": 480,   "replacement_cost": 180000.00,"next_maintenance_date": _now + timedelta(days=20)},
    # Lifts
    {"asset_id": "LFT-001", "asset_name": "Passenger Lift 1",       "asset_type": "Elevator",      "category": "Vertical Transport","location": "Main Core",         "manufacturer": "Schindler", "model": "5500",       "serial_number": "SCH-LFT-001", "criticality_level": "high",     "condition": "good",      "operating_hours": 18000, "replacement_cost": 250000.00,"next_maintenance_date": _now + timedelta(days=30)},
    {"asset_id": "LFT-002", "asset_name": "Passenger Lift 2",       "asset_type": "Elevator",      "category": "Vertical Transport","location": "Main Core",         "manufacturer": "Schindler", "model": "5500",       "serial_number": "SCH-LFT-002", "criticality_level": "high",     "condition": "fair",      "operating_hours": 19000, "replacement_cost": 250000.00,"next_maintenance_date": _now + timedelta(days=15)},
    {"asset_id": "LFT-003", "asset_name": "Service Lift",           "asset_type": "Elevator",      "category": "Vertical Transport","location": "Service Core",      "manufacturer": "Kone",      "model": "MonoSpace",  "serial_number": "KNE-LFT-003", "criticality_level": "medium",   "condition": "good",      "operating_hours": 8000,  "replacement_cost": 150000.00,"next_maintenance_date": _now + timedelta(days=60)},
    # Fire Systems
    {"asset_id": "FP-001",  "asset_name": "Fire Pump Main",         "asset_type": "Fire Pump",     "category": "Fire Safety",   "location": "Ground Floor Plant",  "manufacturer": "Grundfos",  "model": "CR90",       "serial_number": "GRN-FP-001",  "criticality_level": "critical", "condition": "good",      "operating_hours": 200,   "replacement_cost": 35000.00, "next_maintenance_date": _now + timedelta(days=10)},
    {"asset_id": "FP-002",  "asset_name": "Fire Pump Jockey",       "asset_type": "Fire Pump",     "category": "Fire Safety",   "location": "Ground Floor Plant",  "manufacturer": "Grundfos",  "model": "CR45",       "serial_number": "GRN-FP-002",  "criticality_level": "critical", "condition": "good",      "operating_hours": 150,   "replacement_cost": 18000.00, "next_maintenance_date": _now + timedelta(days=10)},
    # Pumps
    {"asset_id": "PMP-001", "asset_name": "Chilled Water Pump 1",   "asset_type": "Pump",          "category": "Water System",  "location": "Basement Plant Room", "manufacturer": "Armstrong", "model": "4030",       "serial_number": "ARM-PMP-001", "criticality_level": "medium",   "condition": "good",      "operating_hours": 4380,  "replacement_cost": 15000.00, "next_maintenance_date": _now + timedelta(days=45)},
    {"asset_id": "PMP-002", "asset_name": "Chilled Water Pump 2",   "asset_type": "Pump",          "category": "Water System",  "location": "Basement Plant Room", "manufacturer": "Armstrong", "model": "4030",       "serial_number": "ARM-PMP-002", "criticality_level": "medium",   "condition": "good",      "operating_hours": 4100,  "replacement_cost": 15000.00, "next_maintenance_date": _now + timedelta(days=45)},
    # UPS
    {"asset_id": "UPS-001", "asset_name": "UPS System Main",        "asset_type": "UPS",           "category": "Power System",  "location": "IT Room Level 2",     "manufacturer": "APC",       "model": "Symmetra",   "serial_number": "APC-UPS-001", "criticality_level": "critical", "condition": "good",      "operating_hours": 6000,  "replacement_cost": 80000.00, "next_maintenance_date": _now + timedelta(days=20)},
    # Cooling Towers
    {"asset_id": "CT-001",  "asset_name": "Cooling Tower 1",        "asset_type": "Cooling Tower", "category": "Cooling System","location": "Roof Level",          "manufacturer": "BAC",       "model": "VT3-1108",   "serial_number": "BAC-CT-001",  "criticality_level": "high",     "condition": "good",      "operating_hours": 7000,  "replacement_cost": 55000.00, "next_maintenance_date": _now + timedelta(days=30)},
    {"asset_id": "CT-002",  "asset_name": "Cooling Tower 2",        "asset_type": "Cooling Tower", "category": "Cooling System","location": "Roof Level",          "manufacturer": "BAC",       "model": "VT3-1108",   "serial_number": "BAC-CT-002",  "criticality_level": "high",     "condition": "fair",      "operating_hours": 7500,  "replacement_cost": 55000.00, "next_maintenance_date": _now + timedelta(days=30)},
]

# BE2-05: sample work orders to seed alongside assets
_WORK_ORDERS = [
    {"work_order_id": "WO-SEED-001", "asset_id": "AHU-001", "desc": "Quarterly PM — AHU-001 filter replacement",  "priority": "high"},
    {"work_order_id": "WO-SEED-002", "asset_id": "CH-001",  "desc": "Chiller vibration noise investigation",       "priority": "critical"},
    {"work_order_id": "WO-SEED-003", "asset_id": "GEN-001", "desc": "Annual generator service — overdue",          "priority": "medium"},
    {"work_order_id": "WO-SEED-004", "asset_id": "AHU-003", "desc": "AHU-003 belt replacement (overdue PM)",       "priority": "high"},
]

_LOCATIONS = [
    {"location_id": "LOC-001", "name": "Basement Plant Room",    "building": "Main Tower", "floor": "B1",  "zone": "Mechanical"},
    {"location_id": "LOC-002", "name": "Ground Floor Plant",     "building": "Main Tower", "floor": "GF",  "zone": "Mechanical"},
    {"location_id": "LOC-003", "name": "Level 1 Lobby",         "building": "Main Tower", "floor": "L1",  "zone": "Common"},
    {"location_id": "LOC-004", "name": "Level 2 Office Area",   "building": "Main Tower", "floor": "L2",  "zone": "Office"},
    {"location_id": "LOC-005", "name": "Level 3 Plant Room",    "building": "Main Tower", "floor": "L3",  "zone": "Mechanical"},
    {"location_id": "LOC-006", "name": "Level 3 Office Area",   "building": "Main Tower", "floor": "L3",  "zone": "Office"},
    {"location_id": "LOC-007", "name": "Level 4 Office Area",   "building": "Main Tower", "floor": "L4",  "zone": "Office"},
    {"location_id": "LOC-008", "name": "Level 5 Plant Room",    "building": "Main Tower", "floor": "L5",  "zone": "Mechanical"},
    {"location_id": "LOC-009", "name": "Level 5 Office Area",   "building": "Main Tower", "floor": "L5",  "zone": "Office"},
    {"location_id": "LOC-010", "name": "Roof Level",            "building": "Main Tower", "floor": "RF",  "zone": "Mechanical"},
    {"location_id": "LOC-011", "name": "Main Core",             "building": "Main Tower", "floor": "ALL", "zone": "Circulation"},
    {"location_id": "LOC-012", "name": "Service Core",          "building": "Main Tower", "floor": "ALL", "zone": "Circulation"},
    {"location_id": "LOC-013", "name": "IT Room Level 2",       "building": "Main Tower", "floor": "L2",  "zone": "IT"},
    {"location_id": "LOC-014", "name": "Security Room GF",      "building": "Main Tower", "floor": "GF",  "zone": "Security"},
    {"location_id": "LOC-015", "name": "Car Park Level B1",     "building": "Main Tower", "floor": "B1",  "zone": "Car Park"},
    {"location_id": "LOC-016", "name": "Car Park Level B2",     "building": "Main Tower", "floor": "B2",  "zone": "Car Park"},
    {"location_id": "LOC-017", "name": "Loading Bay",           "building": "Main Tower", "floor": "GF",  "zone": "Service"},
    {"location_id": "LOC-018", "name": "Podium Level 1",        "building": "Podium",     "floor": "P1",  "zone": "Retail"},
    {"location_id": "LOC-019", "name": "Podium Level 2",        "building": "Podium",     "floor": "P2",  "zone": "Retail"},
    {"location_id": "LOC-020", "name": "External Plant Area",   "building": "Site",       "floor": "GF",  "zone": "External"},
]


async def seed(db_url: str) -> None:
    engine = create_async_engine(db_url, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # ── Assets ────────────────────────────────────────────────────────────
        inserted_assets = 0
        for data in _ASSETS:
            existing = await session.get(Asset, data["asset_id"])
            if not existing:
                session.add(Asset(**data))
                inserted_assets += 1
            else:
                # BE2-03: update extended fields on existing record
                for key, val in data.items():
                    if key != "asset_id":
                        setattr(existing, key, val)

        # ── Locations ─────────────────────────────────────────────────────────
        inserted_locations = 0
        for data in _LOCATIONS:
            existing = await session.get(Location, data["location_id"])
            if not existing:
                session.add(Location(**data))
                inserted_locations += 1

        await session.flush()

        # ── Work Orders + Journey Logs (BE2-05) ───────────────────────────────
        inserted_wo = 0
        for spec in _WORK_ORDERS:
            existing = await session.get(WorkOrder, spec["work_order_id"])
            if not existing:
                wo = WorkOrder(
                    work_order_id=spec["work_order_id"],
                    source="seed_script",
                    asset=spec["asset_id"],
                    location="LOC-001",
                    issue_description=spec["desc"],
                    priority=spec["priority"],
                    request_type="preventive",
                    status="pending_approval",
                    approval_type="preparation",
                    created_by="seed",
                )
                session.add(wo)
                await session.flush()

                jlog = await create_journey_for_work_order(
                    work_order_id=spec["work_order_id"],
                    priority=spec["priority"],
                    session=session,
                    asset_id=spec["asset_id"],
                    source_system="seed_script",
                    estimated_cost=1500.00,
                )
                wo.journey_log_id = jlog.jlog_id
                inserted_wo += 1

        await session.commit()
        print(f"Seeded {inserted_assets} assets, {inserted_locations} locations, {inserted_wo} work orders.")

    await engine.dispose()


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable not set.")
        sys.exit(1)
    asyncio.run(seed(db_url))
