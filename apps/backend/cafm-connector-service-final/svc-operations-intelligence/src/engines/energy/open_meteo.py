"""Open-Meteo: where a building is, and what the weather did there, for degree days.

Two reads and nothing written. The geocoder turns a site's postcode or city into coordinates,
and the ERA5 archive gives the daily mean temperature at 2 m for any past date range. Both
answers are cached in process for a few hours, because a building does not move and last
year's weather does not change.

Without ``OPEN_METEO_API_KEY`` the free endpoints are used, which Open-Meteo licenses for
non-commercial use only. With it, the customer endpoints carry the key. That is a business
decision for Plenum before production, not something this module can settle.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx

from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

#: A test seam: an ``httpx.MockTransport`` answers instead of the network when set.
TRANSPORT: httpx.AsyncBaseTransport | None = None

#: Per call. Three calls can run (postcode, city, archive); asset_sources bounds them together.
TIMEOUT_S = 6.0
CACHE_TTL_S = 6 * 3600
#: A failed answer is remembered this long, so a reader clicking again does not wait out the
#: timeout again for a provider that is down.
FAIL_TTL_S = 300
#: The archive key carries yesterday's date, so every building makes a new entry each day.
#: Bounded, and expired entries are dropped on every write, or a long-running process grows.
CACHE_MAX = 512
_CACHE: dict[tuple, tuple[float, Any]] = {}

#: The platform codes the United Kingdom "UK"; ISO 3166 — what the geocoder filters on — says GB.
_ISO = {"UK": "GB"}


class WeatherUnavailable(Exception):
    """The provider could not be read: a timeout, an HTTP error, or an unusable answer."""


def clear_cache() -> None:
    _CACHE.clear()


def iso_country(code: str | None) -> str | None:
    """The ISO 3166 alpha-2 code the geocoder filters on, or None to search unfiltered.

    Sites spell countries every way the platform accepts — UAE, United Kingdom, England,
    Dubai — and buildings.country_code_for already resolves them. A value that resolves to
    no two-letter code is left out rather than sent: the geocoder matches nothing on it.
    """
    from .buildings import country_code_for  # local: buildings is a large module

    c = country_code_for(code)
    c = _ISO.get(c or "", c)
    return c if c and len(c) == 2 else None


def _endpoint(kind: str) -> tuple[str, dict[str, str]]:
    key = (getattr(settings, "open_meteo_api_key", "") or "").strip()
    if key:
        return f"https://customer-{kind}-api.open-meteo.com", {"apikey": key}
    return f"https://{kind}-api.open-meteo.com", {}


def _cached(key: tuple) -> Any:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def _remember(key: tuple, value: Any, ttl: float = CACHE_TTL_S) -> Any:
    now = time.monotonic()
    for k in [k for k, (exp, _) in _CACHE.items() if exp <= now]:
        del _CACHE[k]
    while len(_CACHE) >= CACHE_MAX:
        del _CACHE[min(_CACHE, key=lambda k: _CACHE[k][0])]
    _CACHE[key] = (now + ttl, value)
    return value


def _failed(key: tuple) -> None:
    """Raise the remembered failure for this request, if there is one."""
    hit = _cached(("fail",) + key)
    if hit is not None:
        raise WeatherUnavailable(hit)


def _remember_failure(key: tuple, exc: "WeatherUnavailable") -> None:
    log.warning("open_meteo.unavailable", request=key[0], error=str(exc)[:160])
    _remember(("fail",) + key, str(exc), ttl=FAIL_TTL_S)


async def _get(kind: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    base, extra = _endpoint(kind)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=TRANSPORT) as client:
            resp = await client.get(base + path, params={**params, **extra})
    except httpx.HTTPError as exc:
        raise WeatherUnavailable(f"{kind} request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise WeatherUnavailable(f"{kind} answered HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise WeatherUnavailable(f"{kind} answered with something that is not JSON") from exc


async def geocode(name: str, country: str | None) -> dict[str, Any] | None:
    """The first match for a postcode or place name, or None when there is no match."""
    q = (name or "").strip()
    if not q:
        return None
    key = ("geo", q.lower(), country)
    hit = _cached(key)
    if hit is not None:
        return hit or None
    _failed(key)
    params: dict[str, Any] = {"name": q, "count": 1, "language": "en", "format": "json"}
    if country:
        params["countryCode"] = country
    try:
        body = await _get("geocoding", "/v1/search", params)
    except WeatherUnavailable as exc:
        _remember_failure(key, exc)
        raise
    results = body.get("results") or []
    first = results[0] if results else None
    out = ({"name": first.get("name") or q, "latitude": float(first["latitude"]),
            "longitude": float(first["longitude"])}
           if first and first.get("latitude") is not None else {})
    _remember(key, out)
    return out or None


async def daily_mean_temperature(lat: float, lon: float, start: date,
                                 end: date) -> list[tuple[date, float | None]]:
    """(day, mean 2 m temperature °C) for every day asked; a day the archive lacks is None."""
    key = ("archive", round(lat, 4), round(lon, 4), start.isoformat(), end.isoformat())
    hit = _cached(key)
    if hit is not None:
        return hit
    _failed(key)
    try:
        body = await _get("archive", "/v1/archive", {
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "daily": "temperature_2m_mean", "timezone": "auto",
        })
        daily = body.get("daily") or {}
        times, temps = daily.get("time") or [], daily.get("temperature_2m_mean") or []
        if not times:
            raise WeatherUnavailable("archive returned no days")
    except WeatherUnavailable as exc:
        _remember_failure(key, exc)
        raise
    out = []
    for t, v in zip(times, temps):
        try:
            out.append((date.fromisoformat(str(t)[:10]), None if v is None else float(v)))
        except (TypeError, ValueError):
            continue
    return _remember(key, out)
