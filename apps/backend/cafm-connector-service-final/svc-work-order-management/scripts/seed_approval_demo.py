"""Seed demo users, roles, and historical WOs for dynamic approval (suggest_approval_chain).

Idempotent — safe to run on prod; skips rows that already exist.

Usage (from svc-work-order-management):
    set DATABASE_URL=postgresql+asyncpg://...
    python -m scripts.seed_approval_demo

Optional:
    python -m scripts.seed_approval_demo --migrate   # alembic upgrade head first
    python -m scripts.seed_approval_demo --force     # re-upsert demo approval steps
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings

DEMO_WO_PREFIX = "WO-DEMO-APPR-HVAC"
_DEMO_PASSWORD_HASH = "$2b$12$PlenumDemoSeedOnlyNotForLoginUse0000000000000000000"

_APPROVERS = [
    ("khalid.alrashid@facility.ae", "Khalid Al Rashid", "Maintenance Supervisor"),
    ("ops.manager@facility.ae", "Sara Operations", "Operations Manager"),
    ("facilities.director@facility.ae", "Omar Facilities", "Facilities Director"),
]

# Matches orchestrator prompts: CHILLER-102, Building B Basement 2, critical HVAC
_CHILLER_DEMO = [
    {
        "work_order_id": "WO-DEMO-APPR-CHILLER-001",
        "days_ago": 95,
        "title": "Critical — Central Chiller Unit not cooling",
        "issue": "HVAC system not cooling for CHILLER-102; refrigerant leak suspected.",
        "priority": "critical",
        "request_type": "repair",
        "location": "Building B, Basement 2",
        "asset": "CHILLER-102",
        "asset_category": "hvac",
        "estimated_cost": 12500,
    },
    {
        "work_order_id": "WO-DEMO-APPR-CHILLER-002",
        "days_ago": 55,
        "title": "Chiller compressor fault — Building B",
        "issue": "CHILLER-102 high discharge pressure; inspect compressor bearings.",
        "priority": "critical",
        "request_type": "repair",
        "location": "Building B, Basement 2",
        "asset": "CHILLER-102",
        "asset_category": "hvac",
        "estimated_cost": 9800,
    },
    {
        "work_order_id": "WO-DEMO-APPR-CHILLER-003",
        "days_ago": 28,
        "title": "Emergency chiller repair — no cooling",
        "issue": "Central Chiller Unit #2 offline; restore cooling to Building B zones.",
        "priority": "urgent",
        "request_type": "repair",
        "location": "Building B, Basement 2",
        "asset": "CHILLER-102",
        "asset_category": "hvac",
        "estimated_cost": 7200,
    },
]

_DEMO_WORK_ORDERS = _CHILLER_DEMO + [
    {
        "work_order_id": f"{DEMO_WO_PREFIX}-001",
        "days_ago": 120,
        "title": "Urgent HVAC repair — roof AHU belt failure",
        "issue": "Grinding noise from roof AHU; urgent belt and bearing inspection.",
        "priority": "urgent",
        "request_type": "repair",
        "location": "Building A Roof",
        "asset": "MOB-AHU-001",
        "asset_category": "hvac",
        "estimated_cost": 4200,
    },
    {
        "work_order_id": f"{DEMO_WO_PREFIX}-002",
        "days_ago": 75,
        "title": "HVAC repair — rooftop unit vibration",
        "issue": "Elevated vibration on MOB-AHU-001; align fan assembly.",
        "priority": "urgent",
        "request_type": "repair",
        "location": "Building A Roof",
        "asset": "MOB-AHU-001",
        "asset_category": "hvac",
        "estimated_cost": 3800,
    },
    {
        "work_order_id": f"{DEMO_WO_PREFIX}-003",
        "days_ago": 40,
        "title": "Emergency HVAC — roof plant room",
        "issue": "No cooling from roof AHU; check refrigerant and controls.",
        "priority": "high",
        "request_type": "repair",
        "location": "Building A Roof",
        "asset": "MOB-AHU-ROOF-02",
        "asset_category": "hvac",
        "estimated_cost": 5500,
    },
]


async def _table_exists(session, table: str) -> bool:
    r = await session.execute(
        text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'plenum_cafm' AND table_name = :t LIMIT 1
        """),
        {"t": table},
    )
    return r.fetchone() is not None


async def _column_type(session, table: str, column: str) -> str | None:
    r = await session.execute(
        text("""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = 'plenum_cafm'
              AND table_name = :t AND column_name = :c
        """),
        {"t": table, "c": column},
    )
    row = r.fetchone()
    return row[0] if row else None


def _is_uuid_type(dtype: str | None) -> bool:
    return bool(dtype and "uuid" in dtype.lower())


def _utc_naive(*, days_ago: int = 0) -> datetime:
    """Naive UTC for TIMESTAMP WITHOUT TIME ZONE columns on Azure."""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)


async def _column_exists(session, table: str, column: str) -> bool:
    r = await session.execute(
        text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'plenum_cafm'
              AND table_name = :t AND column_name = :c LIMIT 1
        """),
        {"t": table, "c": column},
    )
    return r.fetchone() is not None


async def _user_roles_link_supported(session) -> bool:
    """user_roles.user_id must match users.id type (Azure may mix int users + uuid FK)."""
    uid = await _column_type(session, "users", "id")
    link = await _column_type(session, "user_roles", "user_id")
    if not uid or not link:
        return False
    return _is_uuid_type(uid) == _is_uuid_type(link)


async def _coerce_org_for_table(session, table: str, org_id: str | int | None):
    """Map resolved org id to the column type on target table."""
    if org_id is None:
        return None
    col = await _column_type(session, table, "organization_id")
    if col and "int" in col:
        try:
            return int(org_id)
        except (TypeError, ValueError):
            return int(settings.default_organization_id)
    return org_id


async def _resolve_organization_id(session) -> str | int | None:
    if not await _table_exists(session, "organizations"):
        return settings.default_organization_id

    row = await session.execute(
        text("SELECT id FROM plenum_cafm.organizations ORDER BY created_at NULLS LAST LIMIT 1")
    )
    found = row.fetchone()
    if found:
        return found[0]

    # Local docker init uses SERIAL orgs
    cnt = await session.execute(text("SELECT COUNT(*) FROM plenum_cafm.organizations"))
    if int(cnt.scalar() or 0) == 0:
        id_col = await session.execute(
            text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'plenum_cafm'
                  AND table_name = 'organizations' AND column_name = 'id'
            """)
        )
        dtype = (id_col.fetchone() or [None])[0]
        if dtype and "uuid" in str(dtype):
            new_id = uuid.uuid4()
            await session.execute(
                text("""
                    INSERT INTO plenum_cafm.organizations (id, name, status)
                    VALUES (:id, 'Plenum Demo Org', 'active')
                """),
                {"id": new_id},
            )
            return new_id
        await session.execute(
            text("""
                INSERT INTO plenum_cafm.organizations (name, status)
                VALUES ('Plenum Demo Org', 'active')
            """)
        )
        row2 = await session.execute(text("SELECT id FROM plenum_cafm.organizations LIMIT 1"))
        found2 = row2.fetchone()
        if found2:
            return found2[0]

    try:
        return int(settings.default_organization_id)
    except ValueError:
        return settings.default_organization_id


async def _ensure_approval_rules(session) -> None:
    if not await _table_exists(session, "wo_approval_rules"):
        print("  skip rules: wo_approval_rules table missing (run alembic upgrade head)")
        return
    cnt = await session.execute(text("SELECT COUNT(*) FROM plenum_cafm.wo_approval_rules"))
    if int(cnt.scalar() or 0) > 0:
        print("  approval rules already present")
        return
    await session.execute(
        text("""
            INSERT INTO plenum_cafm.wo_approval_rules
              (dimension, match_value, match_operator, weight) VALUES
              ('priority', 'critical', 'eq', 45),
              ('priority', 'urgent', 'eq', 40),
              ('priority', 'high',   'eq', 25),
              ('priority', 'medium', 'eq', 15),
              ('priority', 'low',    'eq', 10),
              ('work_type', 'hvac',       'eq', 15),
              ('work_type', 'repair',     'eq', 5),
              ('building', 'roof', 'eq', 10),
              ('building', 'building b', 'eq', 12)
        """)
    )
    await session.execute(
        text("""
            INSERT INTO plenum_cafm.wo_approval_rules
              (dimension, match_operator, match_threshold, match_threshold_upper, weight) VALUES
              ('cost', 'lte',     5000,    NULL,  5),
              ('cost', 'between', 5000,    25000, 15),
              ('cost', 'between', 25000,   100000, 30),
              ('cost', 'gte',     100000,  NULL,  45)
        """)
    )
    print("  inserted default wo_approval_rules")


async def _ensure_approval_thresholds(session) -> None:
    if not await _table_exists(session, "wo_approval_thresholds"):
        return
    cnt = await session.execute(text("SELECT COUNT(*) FROM plenum_cafm.wo_approval_thresholds"))
    if int(cnt.scalar() or 0) > 0:
        print("  approval thresholds already present")
        return
    await session.execute(
        text("""
            INSERT INTO plenum_cafm.wo_approval_thresholds (level, min_score, max_score, required_roles) VALUES
              (1, 0,  39,  ARRAY['Maintenance Supervisor']::text[]),
              (2, 40, 69,  ARRAY['Maintenance Supervisor', 'Operations Manager']::text[]),
              (3, 70, NULL, ARRAY['Maintenance Supervisor', 'Operations Manager', 'Facilities Director']::text[])
        """)
    )
    print("  inserted default wo_approval_thresholds")


async def _ensure_role(session, org_id, role_name: str) -> str | None:
    if not await _table_exists(session, "roles"):
        return None
    if org_id is not None:
        row = await session.execute(
            text("""
                SELECT id::text FROM plenum_cafm.roles
                WHERE LOWER(name) = LOWER(:name)
                  AND organization_id::text = :org
                LIMIT 1
            """),
            {"name": role_name, "org": str(org_id)},
        )
    else:
        row = await session.execute(
            text("""
                SELECT id::text FROM plenum_cafm.roles
                WHERE LOWER(name) = LOWER(:name)
                LIMIT 1
            """),
            {"name": role_name},
        )
    found = row.fetchone()
    if found:
        return found[0]

    id_type = await _column_type(session, "roles", "id")
    params = {
        "org": await _coerce_org_for_table(session, "roles", org_id),
        "name": role_name,
        "desc": f"Demo role — {role_name}",
    }
    if _is_uuid_type(id_type):
        role_id = uuid.uuid4()
        await session.execute(
            text("""
                INSERT INTO plenum_cafm.roles (id, organization_id, name, description)
                VALUES (:id, :org, :name, :desc)
            """),
            {**params, "id": role_id},
        )
        return str(role_id)
    result = await session.execute(
        text("""
            INSERT INTO plenum_cafm.roles (organization_id, name, description)
            VALUES (:org, :name, :desc)
            RETURNING id::text
        """),
        params,
    )
    return result.scalar_one()


async def _ensure_user(session, org_id, email: str, full_name: str, role_name: str) -> str:
    row = await session.execute(
        text("SELECT id::text FROM plenum_cafm.users WHERE LOWER(email) = LOWER(:email) LIMIT 1"),
        {"email": email},
    )
    found = row.fetchone()
    if found:
        user_id = found[0]
    else:
        id_type = await _column_type(session, "users", "id")
        org_val = await _coerce_org_for_table(session, "users", org_id)
        base = {
            "org": org_val,
            "name": full_name,
            "email": email,
            "ph": _DEMO_PASSWORD_HASH,
        }
        if _is_uuid_type(id_type):
            user_id = str(uuid.uuid4())
            await session.execute(
                text("""
                    INSERT INTO plenum_cafm.users
                      (id, organization_id, full_name, email, password_hash, status)
                    VALUES (:id, :org, :name, :email, :ph, 'active')
                """),
                {**base, "id": user_id},
            )
        else:
            result = await session.execute(
                text("""
                    INSERT INTO plenum_cafm.users
                      (organization_id, full_name, email, password_hash, status)
                    VALUES (:org, :name, :email, :ph, 'active')
                    RETURNING id::text
                """),
                base,
            )
            user_id = result.scalar_one()
        print(f"  created user {email}")

    if await _table_exists(session, "user_roles") and await _user_roles_link_supported(session):
        role_id = await _ensure_role(session, org_id, role_name)
        if role_id:
            exists = await session.execute(
                text("""
                    SELECT 1 FROM plenum_cafm.user_roles
                    WHERE user_id::text = :uid AND role_id::text = :rid LIMIT 1
                """),
                {"uid": user_id, "rid": role_id},
            )
            if not exists.fetchone():
                ur_id_type = await _column_type(session, "user_roles", "id")
                ur_params = {"uid": user_id, "rid": role_id}
                if _is_uuid_type(ur_id_type):
                    await session.execute(
                        text("""
                            INSERT INTO plenum_cafm.user_roles (id, user_id, role_id)
                            VALUES (:id, :uid, :rid)
                        """),
                        {**ur_params, "id": uuid.uuid4()},
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO plenum_cafm.user_roles (user_id, role_id)
                            VALUES (:uid, :rid)
                        """),
                        ur_params,
                    )
    elif await _table_exists(session, "user_roles"):
        print(
            "  skip user_roles: users.id and user_roles.user_id types differ "
            "(approver emails still work for approval chains)"
        )
    return email


async def _demo_wo_exists(session, wo_id: str) -> bool:
    r = await session.execute(
        text("SELECT 1 FROM plenum_cafm.work_orders WHERE work_order_id = :id LIMIT 1"),
        {"id": wo_id},
    )
    return r.fetchone() is not None


async def _insert_work_order(session, org_id, spec: dict) -> None:
    wo_id = spec["work_order_id"]
    if await _demo_wo_exists(session, wo_id):
        print(f"  work order {wo_id} already exists")
        return

    created_at = _utc_naive(days_ago=spec["days_ago"])
    cols = [
        "work_order_id", "source", "title", "asset", "location",
        "issue_description", "priority", "request_type", "status",
        "requester_name", "requester_email", "created_at",
    ]
    vals = [
        ":wo_id", "'seed_approval_demo'", ":title", ":asset", ":location",
        ":issue", ":priority", ":request_type", "'completed'",
        "'Demo Requester'", "'demo@plenum-tech.com'", ":created_at",
    ]
    params: dict = {
        "wo_id": wo_id,
        "title": spec["title"],
        "asset": spec["asset"],
        "location": spec["location"],
        "issue": spec["issue"],
        "priority": spec["priority"],
        "request_type": spec["request_type"],
        "created_at": created_at,
    }

    if await _column_exists(session, "work_orders", "organization_id"):
        cols.append("organization_id")
        vals.append(":org_id")
        org_col = await session.execute(
            text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'plenum_cafm'
                  AND table_name = 'work_orders' AND column_name = 'organization_id'
            """)
        )
        org_dtype = (org_col.fetchone() or [None])[0]
        if org_dtype and ("int" in str(org_dtype) or "serial" in str(org_dtype)):
            try:
                params["org_id"] = int(settings.default_organization_id)
            except ValueError:
                params["org_id"] = 1
        else:
            params["org_id"] = org_id

    if await _column_exists(session, "work_orders", "estimated_cost"):
        cols.append("estimated_cost")
        vals.append(":cost")
        params["cost"] = spec["estimated_cost"]

    if await _column_exists(session, "work_orders", "asset_category"):
        cols.append("asset_category")
        vals.append(":asset_cat")
        params["asset_cat"] = spec["asset_category"]

    sql = f"INSERT INTO plenum_cafm.work_orders ({', '.join(cols)}) VALUES ({', '.join(vals)})"
    await session.execute(text(sql), params)
    print(f"  inserted work order {wo_id}")


async def _insert_approval_chain(
    session,
    wo_id: str,
    approver_emails: list[str],
    *,
    force: bool,
) -> None:
    if not await _table_exists(session, "wo_approval_requests"):
        return

    existing = await session.execute(
        text("""
            SELECT COUNT(*) FROM plenum_cafm.wo_approval_requests
            WHERE work_order_id = :wo AND status IN ('approved', 'rejected')
        """),
        {"wo": wo_id},
    )
    if int(existing.scalar() or 0) >= len(approver_emails) and not force:
        print(f"  approval chain for {wo_id} already seeded")
        return

    if force:
        await session.execute(
            text("DELETE FROM plenum_cafm.wo_approval_requests WHERE work_order_id = :wo"),
            {"wo": wo_id},
        )

    has_step = await _column_exists(session, "wo_approval_requests", "step_order")
    has_level = await _column_exists(session, "wo_approval_requests", "level")
    has_risk = await _column_exists(session, "wo_approval_requests", "risk_score")

    base_created = _utc_naive(days_ago=30)
    for step, email in enumerate(approver_emails, start=1):
        req_id = f"APR-{wo_id}-L{step}"
        responded = base_created + timedelta(hours=6 * step)
        cols = [
            "request_id", "work_order_id", "approval_type", "approver",
            "status", "notes", "requested_at", "responded_at",
        ]
        vals = [
            ":rid", ":wo", "'preparation'", ":approver",
            "'approved'", "'Demo approval — historical precedent'", ":req_at", ":resp_at",
        ]
        params = {
            "rid": req_id,
            "wo": wo_id,
            "approver": email,
            "req_at": base_created + timedelta(hours=6 * (step - 1)),
            "resp_at": responded,
        }
        if has_step:
            cols.append("step_order")
            vals.append(":step")
            params["step"] = step
        if has_level:
            cols.append("level")
            vals.append(":step")
        if has_risk:
            cols.append("risk_score")
            vals.append("55")
            cols.append("match_score")
            vals.append("88")
            cols.append("suggestion_source")
            vals.append("'history'")

        sql = f"INSERT INTO plenum_cafm.wo_approval_requests ({', '.join(cols)}) VALUES ({', '.join(vals)})"
        await session.execute(text(sql), params)

    print(f"  seeded {len(approver_emails)}-step approval chain on {wo_id}")


async def seed(*, run_migrate: bool, force: bool) -> None:
    db_url = settings.db_url or os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("ERROR: set DATABASE_URL or DB_URL")
        sys.exit(1)

    if run_migrate:
        print("Running alembic upgrade head...")
        subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=os.path.dirname(os.path.dirname(__file__)))

    engine = create_async_engine(db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        print("Seeding dynamic approval demo data...")
        org_id = await _resolve_organization_id(session)
        print(f"  organization_id={org_id}")

        await _ensure_approval_rules(session)
        await _ensure_approval_thresholds(session)

        approver_emails: list[str] = []
        for email, name, role in _APPROVERS:
            approver_emails.append(await _ensure_user(session, org_id, email, name, role))

        for spec in _DEMO_WORK_ORDERS:
            await _insert_work_order(session, org_id, spec)
            await _insert_approval_chain(session, spec["work_order_id"], approver_emails, force=force)

        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            print(f"ERROR: seed commit failed: {exc}")
            raise

    await engine.dispose()
    print("Done.")
    print("  Demo WOs: WO-DEMO-APPR-CHILLER-001..003 (CHILLER-102, Building B, Basement 2)")
    print("  Approvers: khalid.alrashid@facility.ae → ops.manager@facility.ae → facilities.director@facility.ae")
    print("  Test: suggest_approval_chain(work_type=repair, priority=critical,")
    print("        location='Building B, Basement 2', asset_category=hvac)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dynamic approval demo data")
    parser.add_argument("--migrate", action="store_true", help="Run alembic upgrade head first")
    parser.add_argument("--force", action="store_true", help="Replace demo approval request rows")
    args = parser.parse_args()
    asyncio.run(seed(run_migrate=args.migrate, force=args.force))


if __name__ == "__main__":
    main()
