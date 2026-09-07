"""Route uploaded smart-meter data to Feature C, the way contracts route to Feature B.

Without this, a half-hourly meter export dropped into the chat classified as "structured"
and went down the CMMS migration track — which maps it against work-order and asset
schemas and never writes a single MeterReading. The engine was reachable only by calling
its API directly, so the one feature built entirely around a recurring data feed had no
way to receive that feed from the place users actually upload things.

Classification is by CONTENT, not filename. A meter export is recognisable from its
header: an MPAN or MPRN, a timestamp column and a consumption column. That is a far
stronger signal than a name — exports arrive called things like "HH_Data_Q3.csv" or
"export(1).csv" — and, more importantly, it cannot mistake a work-order CSV for meter
data, which a keyword like "meter" or "energy" in a filename easily could.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import httpx
import structlog

from ..config import settings

log = structlog.get_logger(__name__)

# Column aliases the ingest endpoint accepts. Kept in step with parse_readings_csv in
# svc-operations-intelligence: a file this module accepts must be one that parser can read,
# or the upload is routed to Feature C only to be dropped there instead.
_TS_COLS = {"reading_at", "timestamp", "datetime", "time", "ts"}
_KWH_COLS = {"consumption_kwh", "kwh", "value", "consumption"}
_ID_COLS = {"mpan", "mprn", "meter_id"}

# Columns that mean this is operational data, not a meter feed. A work-order export can
# carry a timestamp and a cost column; it never carries these alongside an MPAN, and if it
# somehow does, the migration track is the safer place for it.
_NOT_METER = {"wo_code", "work_order_code", "ppm_code", "asset_code", "invoice_no",
              "line_no", "certificate_number", "vendor_name"}


def classify_energy_document(file_path: str, user_query: str | None = None) -> str | None:
    """Return 'meter_readings' for a smart-meter export, else None.

    Reads only the header row and one data row — enough to identify the file, cheap enough
    to run on every upload.
    """
    if Path(file_path).suffix.lower() not in {".csv", ".tsv"}:
        return None
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            head = fh.readline()
            if not head:
                return None
            sample = head + fh.readline()
    except OSError as exc:
        log.warning("single_door.energy.read_failed", error=str(exc)[:200])
        return None

    try:
        reader = csv.DictReader(io.StringIO(sample))
        cols = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
    except csv.Error:
        return None
    if not cols:
        return None

    if cols & _NOT_METER:
        return None
    has_ts = bool(cols & _TS_COLS)
    has_kwh = bool(cols & _KWH_COLS)
    has_id = bool(cols & _ID_COLS)

    # A timestamp and a consumption column alone could be almost anything; the meter
    # identifier is what makes it a meter feed, and is also what the endpoint needs to
    # resolve or create the meter. Without one there is nothing to attach readings to.
    if has_ts and has_kwh and has_id:
        return "meter_readings"
    return None


async def route_energy_upload(
    *,
    file_path: str,
    organization_id: str | None = None,
    user_query: str | None = None,
) -> dict[str, Any] | None:
    """Send a recognised meter export to Feature C ingestion. None when not meter data."""
    kind = classify_energy_document(file_path, user_query)
    if kind is None:
        return None

    name = Path(file_path).name
    base = settings.operations_intelligence_base_url.rstrip("/")
    try:
        raw = Path(file_path).read_bytes()
        data = {"source": "csv", "detect_gaps": "true"}
        if organization_id:
            data["organization_id"] = str(organization_id)
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base}/api/energy/readings/ingest/csv",
                files={"file": (name, raw, "text/csv")},
                data=data,
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.error("single_door.energy.route_failed", file=name, error=str(exc)[:300])
        return {
            "kind": "meter_readings",
            "ok": False,
            "summary": f"Energy ingest failed for {name}: {str(exc)[:200]}",
        }

    inserted = int(body.get("inserted") or 0)
    gaps = body.get("gaps_flagged") or []
    created = body.get("created_meters") or []
    meters = body.get("meters") or []

    bits = [f"Energy Intelligence: ingested {inserted} half-hourly readings from {name}"]
    if created:
        bits.append(f"{len(created)} meter(s) created from the MPAN/MPRN on the file")
    elif meters:
        bits.append(f"matched {len(meters)} existing meter(s)")
    if gaps:
        worst = max(int(g.get("missing_periods") or 0) for g in gaps)
        bits.append(
            f"{len(gaps)} reading gap(s) flagged, largest {worst} consecutive half-hours "
            f"— retrying up to 3 times before escalation"
        )
    if inserted == 0 and meters:
        bits.append("no new readings — this period is already on file")

    log.info(
        "single_door.energy.ingested",
        file=name, inserted=inserted, gaps=len(gaps), created=len(created),
    )
    return {
        "kind": "meter_readings",
        "ok": bool(body.get("ok", True)),
        "summary": ". ".join(bits) + ".",
        "result": body,
        # What the chat card renders.
        "ingest": {
            "document_name": name,
            "inserted": inserted,
            "meters": [str(m) for m in meters],
            "created_meters": [str(m) for m in created],
            "gaps": [
                {
                    "gap_start": g.get("gap_start"),
                    "gap_end": g.get("gap_end"),
                    "missing_periods": g.get("missing_periods"),
                }
                for g in gaps
            ],
        },
    }
