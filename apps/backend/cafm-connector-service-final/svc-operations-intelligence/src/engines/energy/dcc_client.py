"""UK smart meter client — DCC electricity (MPAN) + gas (MPRN).

Live mode: HTTP to configured DCC_API_BASE_URL.
Dev/demo: deterministic HH simulator used for gap retry fills when
DCC_SIMULATE_FILL=true and no live URL is configured.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx

from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

HH = timedelta(minutes=30)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _align_hh(dt: datetime) -> datetime:
    dt = _aware(dt).replace(second=0, microsecond=0)
    return dt.replace(minute=(dt.minute // 30) * 30)


async def fetch_half_hourly(
    *,
    meter_type: str,
    mpan: str | None = None,
    mprn: str | None = None,
    dcc_device_id: str | None = None,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """
    Return [{reading_at, consumption_kwh, quality_flag}] for HH periods in window.
    """
    start = _align_hh(window_start)
    end = _aware(window_end)
    if settings.dcc_api_base_url.strip():
        return await _fetch_live(
            meter_type=meter_type,
            mpan=mpan,
            mprn=mprn,
            dcc_device_id=dcc_device_id,
            window_start=start,
            window_end=end,
        )
    if settings.dcc_simulate_fill:
        return _simulate_fill(start, end, meter_type=meter_type, mpan=mpan, mprn=mprn)
    log.warning("dcc.fetch.skipped", reason="no_dcc_url_and_simulate_disabled")
    return []


async def _fetch_live(
    *,
    meter_type: str,
    mpan: str | None,
    mprn: str | None,
    dcc_device_id: str | None,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """
    Expected upstream contract (configurable adapter):
      GET {base}/v1/readings?mpan=|mprn=&from=&to=
      → { "readings": [ {"reading_at": ISO, "consumption_kwh": float, ...} ] }
    """
    base = settings.dcc_api_base_url.rstrip("/")
    params: dict[str, str] = {
        "from": window_start.isoformat(),
        "to": window_end.isoformat(),
        "meter_type": meter_type,
    }
    if mpan:
        params["mpan"] = mpan
    if mprn:
        params["mprn"] = mprn
    if dcc_device_id:
        params["device_id"] = dcc_device_id

    headers = {"Accept": "application/json"}
    if settings.dcc_api_key:
        headers["Authorization"] = f"Bearer {settings.dcc_api_key}"

    try:
        async with httpx.AsyncClient(timeout=settings.dcc_api_timeout_seconds) as client:
            resp = await client.get(f"{base}/v1/readings", params=params, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.error("dcc.fetch.failed", error=str(exc)[:300])
        raise

    raw = body.get("readings") if isinstance(body, dict) else body
    out: list[dict[str, Any]] = []
    for r in raw or []:
        at = r.get("reading_at") or r.get("timestamp")
        if not at:
            continue
        if isinstance(at, str):
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        out.append(
            {
                "reading_at": _align_hh(at).isoformat(),
                "consumption_kwh": float(r.get("consumption_kwh") or r.get("kwh") or 0),
                "quality_flag": r.get("quality_flag") or "ok",
            }
        )
    log.info("dcc.fetch.ok", count=len(out), mpan=mpan, mprn=mprn)
    return out


def _simulate_fill(
    window_start: datetime,
    window_end: datetime,
    *,
    meter_type: str,
    mpan: str | None,
    mprn: str | None,
) -> list[dict[str, Any]]:
    """Deterministic demo fill for gap retries when live DCC is unavailable."""
    seed = sum(ord(c) for c in (mpan or mprn or meter_type or "x"))
    base_kwh = 0.35 if meter_type == "electricity" else 0.55
    cur = _align_hh(window_start)
    end = _aware(window_end)
    out: list[dict[str, Any]] = []
    i = 0
    while cur < end:
        # Stable pseudo-random variation per slot
        jitter = ((seed + i * 17) % 100) / 1000.0
        out.append(
            {
                "reading_at": cur.isoformat(),
                "consumption_kwh": round(base_kwh + jitter, 4),
                "quality_flag": "estimated",
            }
        )
        cur += HH
        i += 1
    log.info("dcc.simulate_fill", count=len(out), mpan=mpan, mprn=mprn)
    return out


async def pull_meter_window(
    *,
    meter_id: UUID,
    meter_type: str,
    mpan: str | None,
    mprn: str | None,
    dcc_device_id: str | None,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    """Public helper used by ingest pull + gap retries."""
    _ = meter_id  # reserved for future device registry lookup
    return await fetch_half_hourly(
        meter_type=meter_type,
        mpan=mpan,
        mprn=mprn,
        dcc_device_id=dcc_device_id,
        window_start=window_start,
        window_end=window_end,
    )
