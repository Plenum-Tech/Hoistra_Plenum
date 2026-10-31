"""Preview, confirm, and verify the approved synthetic Fiix portfolio import.

The batch importer stays outside the model-facing API because a 526k-row owner
upload needs stronger guarantees than an interactive single-record tool:

* every source file and header is validated before any operational row is written;
* the exact nine-file manifest is bound to a confirmation hash;
* deterministic identifiers make collisions visible instead of silently merging;
* PostgreSQL COPY keeps the large, all-or-nothing transaction practical; and
* the trusted identity files are staged and validated before the DB transaction.

No credential is accepted on the command line. Database connection details are
read privately from the already-running Homework PostgreSQL container.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Iterator

import asyncpg


ORGANIZATION_ID = "hw1-org-a"
PORTFOLIO_ID = "hw1-org-a-default-portfolio"
PORTFOLIO_USER_ID = "hw1-portfolio"
TIMEZONE = "Asia/Dubai"
PROVENANCE = (
    "Owner-provided synthetic Fiix-style CMMS portfolio dataset submitted on "
    "31 October 2026. All sites, people, vendors, assets, and operational "
    "records are fictional."
)
DB_CONTAINER_DEFAULT = "hoistra-hw1-postgres-1"
COMPLIANCE_INSPECTIONS = frozenset(
    {"HSE", "FireLifeSafety", "WaterQuality", "LiftInspection"}
)
COPY_BATCH_SIZE = 5_000


@dataclass(frozen=True)
class FileSpec:
    dataset: str
    key: str
    headers: tuple[str, ...]


FILE_SPECS: dict[str, FileSpec] = {
    "sites.csv": FileSpec(
        "sites",
        "site_id",
        (
            "site_id", "site_name", "site_type", "country", "city", "floors",
            "gfa_sqm", "occupancy_profile", "handover_date",
        ),
    ),
    "assets.csv": FileSpec(
        "assets",
        "asset_id",
        (
            "asset_id", "site_id", "asset_type", "asset_name", "manufacturer",
            "model", "serial", "install_date", "criticality", "location", "status",
        ),
    ),
    "technicians.csv": FileSpec(
        "technicians",
        "tech_id",
        (
            "tech_id", "tech_name", "primary_skill", "secondary_skill", "level",
            "shift", "base_site_id", "employment_type", "hourly_cost_aed",
        ),
    ),
    "vendors.csv": FileSpec(
        "vendors",
        "vendor_id",
        (
            "vendor_id", "vendor_name", "specialty", "rate_card_hourly_aed",
            "sla_response_mins",
        ),
    ),
    "pm_templates.csv": FileSpec(
        "pm_templates",
        "pm_template_id",
        (
            "pm_template_id", "asset_type", "frequency", "estimated_minutes",
            "checklist_items", "reading_type",
        ),
    ),
    "work_orders_sample_120k.csv": FileSpec(
        "work_orders",
        "wo_id",
        (
            "wo_id", "site_id", "asset_id", "wo_type", "priority", "status",
            "created_at", "responded_at", "completed_at", "assigned_tech_id",
            "vendor_id", "fault_code", "cause_code", "resolution_code",
            "labor_minutes", "travel_minutes", "sla_response_target_mins",
            "sla_response_actual_mins", "sla_breached", "cost_parts_aed",
            "cost_vendor_aed",
        ),
    ),
    "inspections_sample_40k.csv": FileSpec(
        "inspections",
        "inspection_id",
        (
            "inspection_id", "site_id", "asset_id", "inspection_type", "timestamp",
            "inspector_tech_id", "findings_count", "critical_flag", "notes",
        ),
    ),
    "meter_readings_sample_300k.csv": FileSpec(
        "meter_readings",
        "reading_id",
        (
            "reading_id", "site_id", "asset_id", "reading_type", "unit",
            "reading_value", "timestamp", "source", "anomaly_flag",
        ),
    ),
    "manpower_utilization_sample.csv": FileSpec(
        "manpower_utilization",
        "tech_id",
        (
            "tech_id", "month", "planned_hours", "actual_hours", "overtime_hours",
            "travel_hours", "training_hours",
        ),
    ),
}


@dataclass
class Analysis:
    manifest: dict[str, Any]
    sites: dict[str, dict[str, str]]
    asset_sites: dict[str, str]
    asset_types_by_site: dict[str, set[str]]
    technician_sites: dict[str, str]
    vendor_rows: dict[str, dict[str, str]]
    vendor_sites: dict[str, set[str]]
    vendor_stats: dict[tuple[str, str], dict[str, Decimal | int]]
    manpower_stats: dict[tuple[str, str], dict[str, Decimal | set[str]]]


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def json_object(value: Any) -> dict[str, Any]:
    """Normalize asyncpg's default JSON text without assuming a custom codec."""
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise ValueError("Expected a JSON object from the Homework database")
    return dict(decoded)


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def building_id(site_id: str) -> str:
    if not re.fullmatch(r"S\d{3}", site_id):
        raise ValueError(f"Invalid Fiix site identifier: {site_id!r}")
    return "hw1-building-fiix-" + site_id.lower()


def file_source_id(batch_sha: str, filename: str) -> str:
    return "fiix-file-source-" + hashlib.sha256(f"{batch_sha}|{filename}".encode()).hexdigest()


def row_record_id(batch_sha: str, dataset: str, key: str) -> str:
    return "fiix-record-" + hashlib.sha256(f"{batch_sha}|{dataset}|{key}".encode()).hexdigest()


def read_record_id(*parts: str) -> str:
    # Read IDs remain understandable in traces while a hash suffix prevents a
    # future long source key from violating the tool's 160-character limit.
    readable = "fiix:" + ":".join(parts)
    if len(readable) <= 150:
        return readable
    return readable[:100] + ":" + hashlib.sha256(readable.encode()).hexdigest()[:32]


def require_directory(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Fiix data directory not found: {path}")
    actual = {item.name for item in path.iterdir() if item.is_file() and item.suffix == ".csv"}
    expected = set(FILE_SPECS)
    missing = sorted(expected - actual)
    extras = sorted(actual - expected)
    if missing or extras:
        raise ValueError(f"CSV set changed; missing={missing}, unexpected={extras}")


def rows(path: Path, spec: FileSpec) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != spec.headers:
            raise ValueError(f"{path.name}: headers changed")
        for line_number, row in enumerate(reader, start=2):
            yield line_number, {name: (row.get(name) or "").strip() for name in spec.headers}


def parse_decimal(value: str, label: str, *, minimum: Decimal = Decimal("0")) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: expected a number") from None
    if not parsed.is_finite() or parsed < minimum:
        raise ValueError(f"{label}: value is outside the allowed range")
    return parsed


def parse_int(value: str, label: str, *, minimum: int = 0) -> int:
    if not re.fullmatch(r"-?\d+", value):
        raise ValueError(f"{label}: expected an integer")
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{label}: value is outside the allowed range")
    return parsed


def parse_timestamp(value: str, label: str, *, optional: bool = False) -> datetime | None:
    if optional and not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label}: invalid ISO timestamp") from None
    return parsed


def add_error(errors: list[str], message: str, *, limit: int = 250) -> None:
    # A bounded report is actionable without flooding logs with owner data.
    if len(errors) < limit:
        errors.append(message)


def collect_master_data(data_dir: Path, errors: list[str]) -> tuple[
    dict[str, dict[str, str]],
    dict[str, str],
    dict[str, set[str]],
    dict[str, str],
    dict[str, dict[str, str]],
    dict[str, int],
]:
    counts: dict[str, int] = {}
    sites: dict[str, dict[str, str]] = {}
    asset_sites: dict[str, str] = {}
    asset_types_by_site: dict[str, set[str]] = defaultdict(set)
    technician_sites: dict[str, str] = {}
    vendor_rows: dict[str, dict[str, str]] = {}

    for filename in ("sites.csv", "assets.csv", "technicians.csv", "vendors.csv", "pm_templates.csv"):
        spec = FILE_SPECS[filename]
        seen: set[str] = set()
        count = 0
        for line, row in rows(data_dir / filename, spec):
            count += 1
            key = row[spec.key]
            if not key:
                add_error(errors, f"{filename}:{line}: missing {spec.key}")
                continue
            if key in seen:
                add_error(errors, f"{filename}:{line}: duplicate {spec.key}={key}")
                continue
            seen.add(key)
            if filename == "sites.csv":
                try:
                    building_id(key)
                    parse_int(row["floors"], f"{filename}:{line}:floors", minimum=1)
                    parse_decimal(row["gfa_sqm"], f"{filename}:{line}:gfa_sqm", minimum=Decimal("0.01"))
                    datetime.fromisoformat(row["handover_date"])
                except ValueError as exc:
                    add_error(errors, str(exc))
                if row["country"] != "UAE":
                    add_error(errors, f"{filename}:{line}: country must be UAE")
                sites[key] = row
            elif filename == "assets.csv":
                if row["site_id"] not in sites:
                    add_error(errors, f"{filename}:{line}: unknown site_id={row['site_id']}")
                asset_sites[key] = row["site_id"]
                asset_types_by_site[row["site_id"]].add(row["asset_type"])
            elif filename == "technicians.csv":
                if row["base_site_id"] not in sites:
                    add_error(errors, f"{filename}:{line}: unknown base_site_id={row['base_site_id']}")
                technician_sites[key] = row["base_site_id"]
                try:
                    parse_decimal(row["hourly_cost_aed"], f"{filename}:{line}:hourly_cost_aed")
                except ValueError as exc:
                    add_error(errors, str(exc))
            elif filename == "vendors.csv":
                vendor_rows[key] = row
                try:
                    parse_decimal(row["rate_card_hourly_aed"], f"{filename}:{line}:rate")
                    parse_int(row["sla_response_mins"], f"{filename}:{line}:sla", minimum=1)
                except ValueError as exc:
                    add_error(errors, str(exc))
            elif filename == "pm_templates.csv":
                known_types = set().union(*asset_types_by_site.values()) if asset_types_by_site else set()
                if row["asset_type"] not in known_types:
                    add_error(errors, f"{filename}:{line}: unknown asset_type={row['asset_type']}")
        counts[filename] = count
    return sites, asset_sites, asset_types_by_site, technician_sites, vendor_rows, counts


def validate_operational_data(
    data_dir: Path,
    sites: dict[str, dict[str, str]],
    asset_sites: dict[str, str],
    technician_sites: dict[str, str],
    vendor_rows: dict[str, dict[str, str]],
    counts: dict[str, int],
    errors: list[str],
) -> tuple[
    dict[str, set[str]],
    dict[tuple[str, str], dict[str, Decimal | int]],
    dict[tuple[str, str], dict[str, Decimal | set[str]]],
    Counter[str],
]:
    vendor_sites: dict[str, set[str]] = defaultdict(set)
    vendor_stats: dict[tuple[str, str], dict[str, Decimal | int]] = {}
    manpower_stats: dict[tuple[str, str], dict[str, Decimal | set[str]]] = {}
    inspection_types: Counter[str] = Counter()

    for filename in (
        "work_orders_sample_120k.csv",
        "inspections_sample_40k.csv",
        "meter_readings_sample_300k.csv",
        "manpower_utilization_sample.csv",
    ):
        spec = FILE_SPECS[filename]
        seen: set[str] = set()
        count = 0
        for line, row in rows(data_dir / filename, spec):
            count += 1
            key = row[spec.key] if filename != "manpower_utilization_sample.csv" else row["tech_id"] + "|" + row["month"]
            if not key:
                add_error(errors, f"{filename}:{line}: missing identity")
                continue
            if key in seen:
                add_error(errors, f"{filename}:{line}: duplicate identity={key}")
                continue
            seen.add(key)

            if filename == "work_orders_sample_120k.csv":
                site = row["site_id"]
                if site not in sites or asset_sites.get(row["asset_id"]) != site:
                    add_error(errors, f"{filename}:{line}: site/asset scope mismatch")
                if row["assigned_tech_id"] and row["assigned_tech_id"] not in technician_sites:
                    add_error(errors, f"{filename}:{line}: unknown assigned technician")
                vendor = row["vendor_id"]
                if vendor and vendor not in vendor_rows:
                    add_error(errors, f"{filename}:{line}: unknown vendor")
                try:
                    created = parse_timestamp(row["created_at"], f"{filename}:{line}:created_at")
                    responded = parse_timestamp(row["responded_at"], f"{filename}:{line}:responded_at")
                    completed = parse_timestamp(row["completed_at"], f"{filename}:{line}:completed_at", optional=True)
                    if responded is not None and created is not None and responded < created:
                        raise ValueError(f"{filename}:{line}: response precedes creation")
                    if completed is not None and responded is not None and completed < responded:
                        raise ValueError(f"{filename}:{line}: completion precedes response")
                    if (row["status"] == "Completed") != bool(completed):
                        raise ValueError(f"{filename}:{line}: status/completion mismatch")
                    # Labor and travel are legitimately unknown on some cancelled
                    # or externally fulfilled orders. Preserve that distinction
                    # instead of turning a missing duration into a false zero.
                    for field in ("labor_minutes", "travel_minutes"):
                        if row[field]:
                            parse_decimal(row[field], f"{filename}:{line}:{field}")
                    for field in (
                        "sla_response_target_mins", "sla_response_actual_mins",
                        "cost_parts_aed", "cost_vendor_aed",
                    ):
                        parse_decimal(row[field], f"{filename}:{line}:{field}")
                    expected_breach = int(Decimal(row["sla_response_actual_mins"]) > Decimal(row["sla_response_target_mins"]))
                    if int(row["sla_breached"]) != expected_breach:
                        raise ValueError(f"{filename}:{line}: SLA breach flag mismatch")
                except ValueError as exc:
                    add_error(errors, str(exc))
                if vendor:
                    vendor_sites[vendor].add(site)
                    stats = vendor_stats.setdefault(
                        (site, vendor),
                        {
                            "orders": 0, "completed": 0, "in_progress": 0,
                            "cancelled": 0, "sla_breaches": 0,
                            "vendor_cost_aed": Decimal("0"),
                        },
                    )
                    stats["orders"] = int(stats["orders"]) + 1
                    status_key = {"Completed": "completed", "InProgress": "in_progress", "Cancelled": "cancelled"}.get(row["status"])
                    if status_key:
                        stats[status_key] = int(stats[status_key]) + 1
                    stats["sla_breaches"] = int(stats["sla_breaches"]) + int(row["sla_breached"] or "0")
                    stats["vendor_cost_aed"] = Decimal(stats["vendor_cost_aed"]) + Decimal(row["cost_vendor_aed"] or "0")

            elif filename == "inspections_sample_40k.csv":
                site = row["site_id"]
                if site not in sites or asset_sites.get(row["asset_id"]) != site:
                    add_error(errors, f"{filename}:{line}: site/asset scope mismatch")
                if row["inspector_tech_id"] not in technician_sites:
                    add_error(errors, f"{filename}:{line}: unknown inspector")
                try:
                    parse_timestamp(row["timestamp"], f"{filename}:{line}:timestamp")
                    findings = parse_int(row["findings_count"], f"{filename}:{line}:findings")
                    if row["critical_flag"] == "1" and findings == 0:
                        raise ValueError(f"{filename}:{line}: critical inspection has no finding")
                except ValueError as exc:
                    add_error(errors, str(exc))
                inspection_types[row["inspection_type"]] += 1

            elif filename == "meter_readings_sample_300k.csv":
                site = row["site_id"]
                if site not in sites or asset_sites.get(row["asset_id"]) != site:
                    add_error(errors, f"{filename}:{line}: site/asset scope mismatch")
                try:
                    parse_timestamp(row["timestamp"], f"{filename}:{line}:timestamp")
                    parse_decimal(row["reading_value"], f"{filename}:{line}:reading_value")
                except ValueError as exc:
                    add_error(errors, str(exc))

            else:
                site = technician_sites.get(row["tech_id"])
                if not site:
                    add_error(errors, f"{filename}:{line}: unknown technician")
                    continue
                if not re.fullmatch(r"\d{4}-\d{2}", row["month"]):
                    add_error(errors, f"{filename}:{line}: invalid month")
                stats = manpower_stats.setdefault(
                    (site, row["month"]),
                    {
                        "technicians": set(), "planned_hours": Decimal("0"),
                        "actual_hours": Decimal("0"), "overtime_hours": Decimal("0"),
                        "travel_hours": Decimal("0"), "training_hours": Decimal("0"),
                    },
                )
                cast_set = stats["technicians"]
                if isinstance(cast_set, set):
                    cast_set.add(row["tech_id"])
                for field in ("planned_hours", "actual_hours", "overtime_hours", "travel_hours", "training_hours"):
                    try:
                        stats[field] = Decimal(stats[field]) + parse_decimal(row[field], f"{filename}:{line}:{field}")
                    except ValueError as exc:
                        add_error(errors, str(exc))
        counts[filename] = count
    return vendor_sites, vendor_stats, manpower_stats, inspection_types


def count_pm_projections(data_dir: Path, asset_types_by_site: dict[str, set[str]]) -> int:
    total = 0
    spec = FILE_SPECS["pm_templates.csv"]
    for _, row in rows(data_dir / "pm_templates.csv", spec):
        total += sum(row["asset_type"] in types for types in asset_types_by_site.values())
    return total


def analyze(data_dir: Path) -> Analysis:
    require_directory(data_dir)
    errors: list[str] = []
    sites, asset_sites, asset_types_by_site, technician_sites, vendor_rows, counts = collect_master_data(data_dir, errors)
    vendor_sites, vendor_stats, manpower_stats, inspection_types = validate_operational_data(
        data_dir, sites, asset_sites, technician_sites, vendor_rows, counts, errors
    )
    file_info = {
        filename: {
            "sha256": file_digest(data_dir / filename),
            "rows": counts.get(filename, 0),
            "bytes": (data_dir / filename).stat().st_size,
        }
        for filename in FILE_SPECS
    }
    batch_sha = digest({name: file_info[name] for name in sorted(file_info)})
    compliance_count = sum(inspection_types[name] for name in COMPLIANCE_INSPECTIONS)
    read_projection_count = (
        len(sites)
        + counts.get("assets.csv", 0) * 2
        + counts.get("work_orders_sample_120k.csv", 0) * 2
        + counts.get("inspections_sample_40k.csv", 0)
        + compliance_count
        + counts.get("meter_readings_sample_300k.csv", 0)
        + len(vendor_stats)
        + count_pm_projections(data_dir, asset_types_by_site)
        + len(manpower_stats)
    )
    manifest: dict[str, Any] = {
        "action": "fiix_portfolio_ingestion",
        "organization_id": ORGANIZATION_ID,
        "portfolio_id": PORTFOLIO_ID,
        "timezone": TIMEZONE,
        "provenance": PROVENANCE,
        "dataset_sha256": batch_sha,
        "files": file_info,
        "total_source_rows": sum(counts.values()),
        "typed_record_rows": sum(counts.values()) - len(sites),
        "building_count": len(sites),
        "agent_read_projection_rows": read_projection_count,
        "compliance_projection_rows": compliance_count,
        "property_manager_users": len(sites),
        "general_users": len(sites),
        "validation_errors": errors,
        "status": "valid" if not errors and len(sites) == 40 else "invalid",
        "mapping_version": "fiix-cmms-portfolio-v1",
        "privacy": {
            "technician_rows_agent_visible": 0,
            "manpower_projection": "building-month aggregate only",
            "manager_only_cost_fields": True,
        },
        "known_source_notes": [
            "Manpower source omits 2025-09 and includes 2026-01; retained unchanged.",
            "Vendor assignments with zero vendor cost are retained unchanged.",
        ],
    }
    if len(sites) != 40:
        manifest["validation_errors"].append(f"Expected 40 sites, received {len(sites)}")
    manifest["preview_hash"] = digest(manifest)
    return Analysis(
        manifest, sites, asset_sites, asset_types_by_site, technician_sites,
        vendor_rows, vendor_sites, vendor_stats, manpower_stats,
    )


def inspect_container(container: str) -> tuple[str, dict[str, str]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", container):
        raise ValueError("Invalid database container name")
    result = subprocess.run(
        ["docker", "inspect", container], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Homework PostgreSQL container is unavailable")
    document = json.loads(result.stdout)[0]
    environment = {}
    for entry in document.get("Config", {}).get("Env", []):
        key, separator, value = entry.partition("=")
        if separator:
            environment[key] = value
    addresses = [
        network.get("IPAddress")
        for network in document.get("NetworkSettings", {}).get("Networks", {}).values()
        if network.get("IPAddress")
    ]
    if len(addresses) != 1:
        raise RuntimeError("Homework PostgreSQL network address is missing or ambiguous")
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    if any(not environment.get(key) for key in required):
        raise RuntimeError("Homework PostgreSQL environment is incomplete")
    return addresses[0], environment


async def connect(container: str) -> asyncpg.Connection:
    host, environment = inspect_container(container)
    return await asyncpg.connect(
        host=host,
        port=5432,
        user=environment["POSTGRES_USER"],
        password=environment["POSTGRES_PASSWORD"],
        database=environment["POSTGRES_DB"],
        command_timeout=300,
    )


async def portfolio_state(connection: asyncpg.Connection, *, lock: bool = False) -> asyncpg.Record:
    suffix = " FOR UPDATE" if lock else ""
    row = await connection.fetchrow(
        "SELECT id, org, buildings, data FROM hw1_compliance.records "
        "WHERE kind='portfolio' AND data->>'portfolio_id'=$1" + suffix,
        PORTFOLIO_ID,
    )
    if row is None:
        raise RuntimeError("Trusted Homework portfolio record is missing")
    return row


async def persist_preview(connection: asyncpg.Connection, analysis: Analysis) -> dict[str, Any]:
    manifest = dict(analysis.manifest)
    if manifest["status"] != "valid":
        return {"proposal_id": None, **manifest}
    portfolio = await portfolio_state(connection)
    portfolio_data = json_object(portfolio["data"])
    manifest["portfolio_version"] = int(portfolio_data.get("version", 1))
    manifest["preview_hash"] = digest({key: value for key, value in manifest.items() if key != "preview_hash"})
    proposal_id = "fiix-proposal-" + manifest["preview_hash"][:32]
    data = {
        "status": "previewed",
        "preview": manifest,
        "preview_hash": manifest["preview_hash"],
        "actor": PORTFOLIO_USER_ID,
        "role": "portfolio_manager",
    }
    building_ids = [building_id(site) for site in sorted(analysis.sites)]
    await connection.execute(
        """
        INSERT INTO hw1_compliance.proposals (id, kind, org, buildings, data)
        VALUES ($1, 'fiix_portfolio_ingestion', $2, $3, $4::jsonb)
        ON CONFLICT (id) DO NOTHING
        """,
        proposal_id,
        ORGANIZATION_ID,
        building_ids,
        canonical(data),
    )
    return {"proposal_id": proposal_id, **manifest}


def source_rows(analysis: Analysis) -> list[tuple[Any, ...]]:
    batch = analysis.manifest["dataset_sha256"]
    buildings = [building_id(site) for site in sorted(analysis.sites)]
    values: list[tuple[Any, ...]] = []
    for filename, metadata in analysis.manifest["files"].items():
        values.append(
            (
                file_source_id(batch, filename),
                "fiix_file",
                ORGANIZATION_ID,
                buildings,
                canonical(
                    {
                        "filename": filename,
                        "sha256": metadata["sha256"],
                        "row_count": metadata["rows"],
                        "bytes": metadata["bytes"],
                        "dataset_sha256": batch,
                        "mapping_version": analysis.manifest["mapping_version"],
                        "provenance": PROVENANCE,
                        "timezone": TIMEZONE,
                        "original_retained_path": "homework/module-1/data/fiix-cmms/" + filename,
                        "scope_verified": True,
                        "synthetic": True,
                    }
                ),
            )
        )
    for site_id, site in analysis.sites.items():
        target = building_id(site_id)
        profile_id = f"fiix-building-profile-source-{site_id.lower()}"
        profile = site_profile(site)
        values.append(
            (
                profile_id,
                "building_profile",
                ORGANIZATION_ID,
                [target],
                canonical(
                    {
                        "original": canonical(site),
                        "fields": profile,
                        "content_sha256": digest(profile),
                        "version": 1,
                        "information_type": "building_profile",
                        "scope_verified": True,
                        "source_site_id": site_id,
                        "dataset_sha256": batch,
                        "provenance": PROVENANCE,
                    }
                ),
            )
        )
        energy = {
            "floor_area_m2": site["gfa_sqm"],
            "area_unit": "m2",
            "building_type": site["site_type"],
            "source_site_id": site_id,
        }
        values.append(
            (
                f"fiix-energy-profile-source-{site_id.lower()}",
                "energy_profile",
                ORGANIZATION_ID,
                [target],
                canonical(
                    {
                        "original": canonical(site),
                        "fields": energy,
                        "content_sha256": digest(energy),
                        "version": 1,
                        "information_type": "energy_profile",
                        "scope_verified": True,
                        "dataset_sha256": batch,
                        "provenance": PROVENANCE,
                    }
                ),
            )
        )
    return values


def site_profile(site: dict[str, str]) -> dict[str, Any]:
    return {
        "name": site["site_name"],
        "country": site["country"],
        "city": site["city"],
        "building_type": site["site_type"],
        "floor_count": int(site["floors"]),
        "floor_area": site["gfa_sqm"],
        "area_unit": "m2",
        "timezone": TIMEZONE,
        "source_site_id": site["site_id"],
        "occupancy_profile": site["occupancy_profile"],
        "handover_date": site["handover_date"],
    }


def building_records(analysis: Analysis, hoi_manifest: dict[str, Any]) -> list[tuple[Any, ...]]:
    batch = analysis.manifest["dataset_sha256"]
    records: list[tuple[Any, ...]] = []
    for site_id, site in analysis.sites.items():
        target = building_id(site_id)
        profile = site_profile(site)
        profile_source = f"fiix-building-profile-source-{site_id.lower()}"
        building_data = {
            **profile,
            "source_id": profile_source,
            "version": 1,
            "accepted": True,
            "portfolio_id": PORTFOLIO_ID,
            "created_by": PORTFOLIO_USER_ID,
            "scope_verified": True,
            "dataset_sha256": batch,
            "status": "active",
        }
        records.append((target, "building", ORGANIZATION_ID, [target], canonical(building_data)))
        records.append(
            (
                target + "-hoi-manifest",
                "hoi_manifest",
                ORGANIZATION_ID,
                [target],
                canonical(hoi_manifest),
            )
        )
        hoi_fields = {
            key: profile[key]
            for key in ("name", "country", "city", "building_type", "floor_count", "floor_area", "area_unit")
        }
        records.append(
            (
                target + "-hoi-profile-0",
                "hoi_item",
                ORGANIZATION_ID,
                [target],
                canonical(
                    {
                        "item_type": "profile",
                        "slot": 0,
                        "fields": hoi_fields,
                        "source_supported": list(hoi_fields),
                        "accepted": True,
                        "source_id": profile_source,
                        "dataset_sha256": batch,
                    }
                ),
            )
        )
        records.append(
            (
                target + "-energy-profile-1",
                "energy_profile",
                ORGANIZATION_ID,
                [target],
                canonical(
                    {
                        "floor_area_m2": site["gfa_sqm"],
                        "area_unit": "m2",
                        "building_type": site["site_type"],
                        "source_site_id": site_id,
                        "source_id": f"fiix-energy-profile-source-{site_id.lower()}",
                        "fixture_version": "fiix-cmms-portfolio-v1",
                        "dataset_sha256": batch,
                    }
                ),
            )
        )
    return records


def row_buildings(
    filename: str,
    row: dict[str, str],
    analysis: Analysis,
) -> list[str]:
    if filename == "technicians.csv":
        return [building_id(row["base_site_id"])]
    if filename == "manpower_utilization_sample.csv":
        return [building_id(analysis.technician_sites[row["tech_id"]])]
    if filename == "vendors.csv":
        return [building_id(site) for site in sorted(analysis.vendor_sites.get(row["vendor_id"], set()))]
    if filename == "pm_templates.csv":
        return [
            building_id(site)
            for site, types in sorted(analysis.asset_types_by_site.items())
            if row["asset_type"] in types
        ]
    site = row.get("site_id")
    return [building_id(site)] if site else []


def typed_record_batches(data_dir: Path, analysis: Analysis) -> Iterator[list[tuple[Any, ...]]]:
    batch_sha = analysis.manifest["dataset_sha256"]
    batch: list[tuple[Any, ...]] = []
    for filename, spec in FILE_SPECS.items():
        if filename == "sites.csv":
            continue
        source_id = file_source_id(batch_sha, filename)
        for line, row in rows(data_dir / filename, spec):
            key = row[spec.key] if filename != "manpower_utilization_sample.csv" else row["tech_id"] + "|" + row["month"]
            data = {
                "fields": row,
                "source_id": source_id,
                "version": 1,
                "adapter_key": key,
                "accepted": True,
                "scope_verified": True,
                "dataset_sha256": batch_sha,
                "filename": filename,
                "row_number": line,
                "source_reference": f"fiix:{batch_sha}:{filename}:{line}",
                "provenance": PROVENANCE,
                "contains_person_level_fields": filename in {"technicians.csv", "manpower_utilization_sample.csv"},
            }
            batch.append(
                (
                    row_record_id(batch_sha, spec.dataset, key),
                    "fiix_" + spec.dataset,
                    ORGANIZATION_ID,
                    row_buildings(filename, row, analysis),
                    canonical(data),
                )
            )
            if len(batch) >= COPY_BATCH_SIZE:
                yield batch
                batch = []
    if batch:
        yield batch


def projection_base(site_id: str, record_id: str, kind: str, payload: dict[str, Any]) -> tuple[Any, ...]:
    target = building_id(site_id)
    return (
        record_id,
        ORGANIZATION_ID,
        target,
        [target],
        True,
        False,
        kind,
        None,
        None,
        canonical(payload),
    )


def response_due_at(created_at: str, minutes: str) -> str:
    created = datetime.fromisoformat(created_at)
    return (created + timedelta(minutes=int(Decimal(minutes)))).isoformat()


def streaming_projection_batches(data_dir: Path, analysis: Analysis) -> Iterator[list[tuple[Any, ...]]]:
    batch_sha = analysis.manifest["dataset_sha256"]
    batch: list[tuple[Any, ...]] = []

    def emit(item: tuple[Any, ...]) -> Iterator[list[tuple[Any, ...]]]:
        nonlocal batch
        batch.append(item)
        if len(batch) >= COPY_BATCH_SIZE:
            complete = batch
            batch = []
            yield complete

    for site_id, site in analysis.sites.items():
        yield from emit(
            projection_base(
                site_id,
                read_record_id(site_id, "site"),
                "building_records",
                {
                    "record_type": "site_profile",
                    "title": site["site_name"],
                    "details": site_profile(site),
                    "source_reference": f"fiix:{batch_sha}:sites.csv:{site_id}",
                },
            )
        )

    asset_spec = FILE_SPECS["assets.csv"]
    for line, row in rows(data_dir / "assets.csv", asset_spec):
        reference = f"fiix:{batch_sha}:assets.csv:{line}"
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "asset", row["asset_id"], "information"),
                "asset_information",
                {
                    "asset_id": row["asset_id"],
                    "model": row["model"],
                    "details": row,
                    "source_reference": reference,
                },
            )
        )
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "asset", row["asset_id"], "condition"),
                "asset_conditions",
                {
                    "asset_id": row["asset_id"],
                    "condition": row["status"],
                    "observed_at": row["install_date"],
                    "source_reference": reference,
                },
            )
        )

    wo_spec = FILE_SPECS["work_orders_sample_120k.csv"]
    for line, row in rows(data_dir / "work_orders_sample_120k.csv", wo_spec):
        reference = f"fiix:{batch_sha}:work_orders_sample_120k.csv:{line}"
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "work_order", row["wo_id"], "status"),
                "work_order_status",
                {
                    "work_order_id": row["wo_id"],
                    "status": row["status"],
                    "asset_id": row["asset_id"],
                    "due_at": response_due_at(row["created_at"], row["sla_response_target_mins"]),
                    "source_reference": reference,
                },
            )
        )
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "work_order", row["wo_id"], "sla"),
                "work_order_sla",
                {
                    "work_order_id": row["wo_id"],
                    "finding": {
                        "priority": row["priority"],
                        "target_response_minutes": row["sla_response_target_mins"],
                        "actual_response_minutes": row["sla_response_actual_mins"],
                        "sla_breached": row["sla_breached"] == "1",
                        "vendor_id": row["vendor_id"] or None,
                        "cost_parts_aed": row["cost_parts_aed"],
                        "cost_vendor_aed": row["cost_vendor_aed"],
                    },
                    "source_reference": reference,
                },
            )
        )

    inspection_spec = FILE_SPECS["inspections_sample_40k.csv"]
    for line, row in rows(data_dir / "inspections_sample_40k.csv", inspection_spec):
        reference = f"fiix:{batch_sha}:inspections_sample_40k.csv:{line}"
        content = {
            "inspection_id": row["inspection_id"],
            "asset_id": row["asset_id"],
            "inspection_type": row["inspection_type"],
            "findings_count": row["findings_count"],
            "critical_flag": row["critical_flag"] == "1",
            "notes": row["notes"],
        }
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "inspection", row["inspection_id"]),
                "inspection_reports",
                {
                    "title": row["inspection_type"] + " inspection " + row["inspection_id"],
                    "content": content,
                    "inspection_date": row["timestamp"],
                    "source_reference": reference,
                },
            )
        )
        if row["inspection_type"] in COMPLIANCE_INSPECTIONS:
            yield from emit(
                projection_base(
                    row["site_id"],
                    read_record_id(row["site_id"], "inspection", row["inspection_id"], "compliance"),
                    "compliance_evidence",
                    {
                        "title": row["inspection_type"] + " inspection " + row["inspection_id"],
                        "content": content,
                        "certificate_id": None,
                        "source_reference": reference,
                    },
                )
            )

    meter_spec = FILE_SPECS["meter_readings_sample_300k.csv"]
    for line, row in rows(data_dir / "meter_readings_sample_300k.csv", meter_spec):
        reference = f"fiix:{batch_sha}:meter_readings_sample_300k.csv:{line}"
        yield from emit(
            projection_base(
                row["site_id"],
                read_record_id(row["site_id"], "reading", row["reading_id"]),
                "energy_findings",
                {
                    "meter_id": row["asset_id"],
                    "finding": {
                        "reading_id": row["reading_id"],
                        "reading_type": row["reading_type"],
                        "value": row["reading_value"],
                        "unit": row["unit"],
                        "source": row["source"],
                        "anomaly": row["anomaly_flag"] == "1",
                    },
                    "period": row["timestamp"],
                    "source_reference": reference,
                },
            )
        )

    for (site_id, vendor_id), stats in sorted(analysis.vendor_stats.items()):
        vendor = analysis.vendor_rows[vendor_id]
        finding = {
            "vendor_name": vendor["vendor_name"],
            "specialty": vendor["specialty"],
            "rate_card_hourly_aed": vendor["rate_card_hourly_aed"],
            "sla_response_mins": vendor["sla_response_mins"],
            "orders": int(stats["orders"]),
            "completed": int(stats["completed"]),
            "in_progress": int(stats["in_progress"]),
            "cancelled": int(stats["cancelled"]),
            "sla_breaches": int(stats["sla_breaches"]),
            "vendor_cost_aed": str(stats["vendor_cost_aed"]),
        }
        yield from emit(
            projection_base(
                site_id,
                read_record_id(site_id, "vendor", vendor_id, "performance"),
                "vendor_performance",
                {
                    "vendor_id": vendor_id,
                    "finding": finding,
                    "source_reference": f"fiix:{batch_sha}:vendors.csv:{vendor_id}",
                },
            )
        )

    pm_spec = FILE_SPECS["pm_templates.csv"]
    for line, row in rows(data_dir / "pm_templates.csv", pm_spec):
        for site_id, asset_types in analysis.asset_types_by_site.items():
            if row["asset_type"] not in asset_types:
                continue
            yield from emit(
                projection_base(
                    site_id,
                    read_record_id(site_id, "pm_template", row["pm_template_id"]),
                    "building_records",
                    {
                        "record_type": "pm_template",
                        "title": row["pm_template_id"] + " " + row["asset_type"],
                        "details": row,
                        "source_reference": f"fiix:{batch_sha}:pm_templates.csv:{line}",
                    },
                )
            )

    for (site_id, month), stats in sorted(analysis.manpower_stats.items()):
        technicians = stats["technicians"]
        details = {
            "month": month,
            "technician_count": len(technicians) if isinstance(technicians, set) else 0,
            **{
                key: str(stats[key])
                for key in ("planned_hours", "actual_hours", "overtime_hours", "travel_hours", "training_hours")
            },
        }
        yield from emit(
            projection_base(
                site_id,
                read_record_id(site_id, "manpower", month),
                "building_records",
                {
                    "record_type": "manpower_utilization",
                    "title": "Manpower utilization " + month,
                    "details": details,
                    "source_reference": f"fiix:{batch_sha}:manpower_utilization_sample.csv:{site_id}:{month}",
                },
            )
        )

    if batch:
        yield batch


async def copy_batches(
    connection: asyncpg.Connection,
    table: str,
    schema: str,
    columns: tuple[str, ...],
    batches: Iterable[list[tuple[Any, ...]]],
) -> int:
    copied = 0
    for batch in batches:
        await connection.copy_records_to_table(
            table,
            schema_name=schema,
            records=batch,
            columns=columns,
            timeout=300,
        )
        copied += len(batch)
    return copied


def identity_plan(
    analysis: Analysis,
    identity_path: Path,
    token_path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    for path in (identity_path, token_path):
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError(f"Identity runtime file must be owner-only: {path}")
    identity = json.loads(identity_path.read_text())
    tokens = json.loads(token_path.read_text())
    users = {row["user_id"]: row for row in identity["users"]}
    sessions = {row["user_id"]: row for row in identity["sessions"]}
    expiry = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    for site_id in sorted(analysis.sites):
        target = building_id(site_id)
        identity["buildings"][target] = ORGANIZATION_ID
        for user_id, role in (
            (f"hw1-property-{site_id}", "property_manager"),
            (f"hw1-user-{site_id}", "general_user"),
        ):
            expected = {
                "user_id": user_id,
                "role": role,
                "organization_id": ORGANIZATION_ID,
                "building_ids": [target],
            }
            if user_id in users and users[user_id] != expected:
                raise ValueError(f"Existing identity conflicts with approved mapping: {user_id}")
            if user_id not in users:
                identity["users"].append(expected)
                users[user_id] = expected
            if user_id not in tokens:
                tokens[user_id] = secrets.token_urlsafe(32)
            expected_digest = hashlib.sha256(tokens[user_id].encode()).hexdigest()
            if user_id in sessions:
                if sessions[user_id].get("token_sha256") != expected_digest:
                    raise ValueError(f"Session token mismatch for {user_id}")
            else:
                session = {
                    "user_id": user_id,
                    "token_sha256": expected_digest,
                    "expires_at": expiry,
                }
                identity["sessions"].append(session)
                sessions[user_id] = session
    return identity, tokens


def write_private_temp(path: Path, value: Any) -> Path:
    handle, name = tempfile.mkstemp(prefix=path.name + ".fiix-", dir=path.parent)
    os.close(handle)
    staged = Path(name)
    staged.chmod(0o600)
    staged.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return staged


async def ensure_read_index(connection: asyncpg.Connection) -> None:
    # The large import would make scoped reads scan the entire table without
    # this server-owned index. CONCURRENTLY avoids blocking current readers.
    await connection.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS hw1_read_records_scope_lookup "
        "ON hw1_access.read_records (organization_id, kind, building_id, record_id)"
    )


async def confirm(
    connection: asyncpg.Connection,
    data_dir: Path,
    analysis: Analysis,
    expected_hash: str,
    reason: str,
    identity_path: Path,
    token_path: Path,
) -> dict[str, Any]:
    if analysis.manifest["status"] != "valid":
        raise ValueError("Cannot confirm a Fiix batch with validation errors")
    if len(reason.strip()) < 8:
        raise ValueError("A specific confirmation reason is required")

    # Preview adds the current portfolio version, so fetch the stored proposal
    # rather than comparing against the file-only manifest hash.
    proposal_id = "fiix-proposal-" + expected_hash[:32]
    existing = await connection.fetchrow(
        "SELECT data FROM hw1_compliance.proposals WHERE id=$1", proposal_id
    )
    if existing is None:
        raise ValueError("Approved Fiix preview is missing")
    existing_data = json_object(existing["data"])
    if existing_data.get("status") == "confirmed":
        result = dict(existing_data.get("result") or {})
        result["outcome"] = "already_ingested"
        return result
    if existing_data.get("status") != "previewed" or existing_data.get("preview_hash") != expected_hash:
        raise ValueError("Approved Fiix preview changed")
    preview = existing_data["preview"]
    if preview.get("dataset_sha256") != analysis.manifest["dataset_sha256"]:
        raise ValueError("Fiix files changed after preview")

    planned_identity, planned_tokens = identity_plan(analysis, identity_path, token_path)
    staged_identity = write_private_temp(identity_path, planned_identity)
    staged_tokens = write_private_temp(token_path, planned_tokens)
    backup_suffix = ".before-fiix-" + analysis.manifest["dataset_sha256"][:12]
    identity_backup = identity_path.with_name(identity_path.name + backup_suffix)
    token_backup = token_path.with_name(token_path.name + backup_suffix)

    await ensure_read_index(connection)
    building_ids = [building_id(site) for site in sorted(analysis.sites)]
    source_values = source_rows(analysis)
    sources_columns = ("id", "kind", "org", "buildings", "data")
    records_columns = ("id", "kind", "org", "buildings", "data")
    reads_columns = (
        "record_id", "organization_id", "building_id", "content_building_ids",
        "scope_verified", "contains_sensitive_data", "kind", "owner_user_id",
        "category", "payload",
    )
    try:
        async with connection.transaction():
            proposal = await connection.fetchrow(
                "SELECT data FROM hw1_compliance.proposals WHERE id=$1 FOR UPDATE",
                proposal_id,
            )
            proposal_data = json_object(proposal["data"])
            if proposal_data.get("status") != "previewed" or proposal_data.get("preview_hash") != expected_hash:
                raise ValueError("Fiix preview was already consumed or changed")

            portfolio = await portfolio_state(connection, lock=True)
            portfolio_data = json_object(portfolio["data"])
            if int(portfolio_data.get("version", 1)) != int(preview["portfolio_version"]):
                raise ValueError("Portfolio membership changed after preview; prepare a fresh preview")
            collisions = await connection.fetchval(
                "SELECT count(*) FROM hw1_compliance.records WHERE id = ANY($1::text[])",
                building_ids,
            )
            if collisions:
                raise ValueError("One or more deterministic Fiix building IDs already exist")
            existing_names = {
                str(json_object(row["data"]).get("name") or "").strip().casefold()
                for row in await connection.fetch(
                    "SELECT data FROM hw1_compliance.records WHERE kind='building' AND org=$1",
                    ORGANIZATION_ID,
                )
            }
            incoming_names = {site["site_name"].strip().casefold() for site in analysis.sites.values()}
            if existing_names & incoming_names:
                raise ValueError("One or more Fiix site names already exist in the organization")

            manifest_row = await connection.fetchrow(
                "SELECT data FROM hw1_compliance.records WHERE kind='hoi_manifest' LIMIT 1"
            )
            if manifest_row is None:
                raise RuntimeError("HOI manifest template is unavailable")
            hoi_manifest = json_object(manifest_row["data"])

            await connection.copy_records_to_table(
                "sources", schema_name="hw1_compliance", records=source_values,
                columns=sources_columns, timeout=300,
            )
            base_records = building_records(analysis, hoi_manifest)
            await connection.copy_records_to_table(
                "records", schema_name="hw1_compliance", records=base_records,
                columns=records_columns, timeout=300,
            )
            typed_count = await copy_batches(
                connection, "records", "hw1_compliance", records_columns,
                typed_record_batches(data_dir, analysis),
            )
            read_count = await copy_batches(
                connection, "read_records", "hw1_access", reads_columns,
                streaming_projection_batches(data_dir, analysis),
            )
            if typed_count != analysis.manifest["typed_record_rows"]:
                raise RuntimeError("Typed-record count changed during confirmation")
            if read_count != analysis.manifest["agent_read_projection_rows"]:
                raise RuntimeError("Read-projection count changed during confirmation")

            members = sorted(set(portfolio_data.get("building_ids", portfolio["buildings"])) | set(building_ids))
            portfolio_data["building_ids"] = members
            portfolio_data["version"] = int(portfolio_data.get("version", 1)) + 1
            await connection.execute(
                "UPDATE hw1_compliance.records SET buildings=$1, data=$2::jsonb WHERE id=$3",
                members,
                canonical(portfolio_data),
                portfolio["id"],
            )
            result = {
                "outcome": "accepted",
                "proposal_id": proposal_id,
                "dataset_sha256": analysis.manifest["dataset_sha256"],
                "buildings": len(building_ids),
                "typed_records": typed_count,
                "read_projections": read_count,
                "file_sources": len(FILE_SPECS),
                "building_profile_sources": len(building_ids) * 2,
                "identity_users_added": len(building_ids) * 2,
                "reason": reason.strip(),
            }
            history_id = "fiix-history-" + expected_hash[:32]
            await connection.execute(
                """
                INSERT INTO hw1_compliance.history (id, kind, org, buildings, data)
                VALUES ($1, 'fiix_portfolio_ingestion', $2, $3, $4::jsonb)
                """,
                history_id,
                ORGANIZATION_ID,
                building_ids,
                canonical(result),
            )
            final_data = {
                **proposal_data,
                "status": "confirmed",
                "decision_reason": reason.strip(),
                "result": result,
            }
            await connection.execute(
                "UPDATE hw1_compliance.proposals SET data=$1::jsonb WHERE id=$2",
                canonical(final_data),
                proposal_id,
            )
        shutil.copy2(identity_path, identity_backup)
        shutil.copy2(token_path, token_backup)
        os.replace(staged_identity, identity_path)
        os.replace(staged_tokens, token_path)
        identity_path.chmod(0o600)
        token_path.chmod(0o600)
        return result
    except Exception:
        staged_identity.unlink(missing_ok=True)
        staged_tokens.unlink(missing_ok=True)
        raise


async def verify(connection: asyncpg.Connection, analysis: Analysis) -> dict[str, Any]:
    batch = analysis.manifest["dataset_sha256"]
    targets = [building_id(site) for site in sorted(analysis.sites)]
    building_count = await connection.fetchval(
        "SELECT count(*) FROM hw1_compliance.records WHERE kind='building' AND id=ANY($1::text[])",
        targets,
    )
    typed_count = await connection.fetchval(
        "SELECT count(*) FROM hw1_compliance.records WHERE data->>'dataset_sha256'=$1 "
        "AND kind LIKE 'fiix_%'",
        batch,
    )
    read_count = await connection.fetchval(
        "SELECT count(*) FROM hw1_access.read_records WHERE record_id LIKE 'fiix:%' "
        "AND building_id=ANY($1::text[])",
        targets,
    )
    portfolio = await portfolio_state(connection)
    portfolio_data = json_object(portfolio["data"])
    missing_members = sorted(set(targets) - set(portfolio_data.get("building_ids", [])))
    result = {
        "dataset_sha256": batch,
        "building_count": building_count,
        "typed_record_count": typed_count,
        "read_projection_count": read_count,
        "missing_portfolio_members": missing_members,
        "expected": {
            "buildings": 40,
            "typed_records": analysis.manifest["typed_record_rows"],
            "read_projections": analysis.manifest["agent_read_projection_rows"],
        },
    }
    result["status"] = "verified" if (
        building_count == 40
        and typed_count == analysis.manifest["typed_record_rows"]
        and read_count == analysis.manifest["agent_read_projection_rows"]
        and not missing_members
    ) else "failed"
    return result


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "confirm", "verify"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--db-container", default=os.environ.get("HW1_DB_CONTAINER", DB_CONTAINER_DEFAULT))
    parser.add_argument("--preview-hash")
    parser.add_argument("--reason")
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    analysis = analyze(args.data_dir)
    connection = await connect(args.db_container)
    try:
        if args.mode == "preview":
            return await persist_preview(connection, analysis)
        if args.mode == "verify":
            return await verify(connection, analysis)
        if not args.preview_hash or not args.reason or not args.identity_file or not args.token_file:
            raise ValueError(
                "confirm requires --preview-hash, --reason, --identity-file, and --token-file"
            )
        return await confirm(
            connection,
            args.data_dir,
            analysis,
            args.preview_hash,
            args.reason,
            args.identity_file,
            args.token_file,
        )
    finally:
        await connection.close()


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = asyncio.run(run(args))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
