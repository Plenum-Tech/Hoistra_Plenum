"""Pricing energy in the market it was consumed in, and saying what the price is worth.

Three things make this less obvious than multiplying kWh by a rate.

**A commercial tariff is a band, not a number.** The market specification gives 21.0p to 26.0p
in the UK, 20 to 33 fils plus a fuel surcharge in the UAE. Pricing at a midpoint produces a
figure nobody can check and that is wrong by up to a quarter either way, so everything here
returns a low and a high, and the caller decides how to show a range.

**The markets do not share a currency.** A portfolio spanning four of them has four totals, and
adding them needs an exchange rate on a stated date. No rate is held here, so nothing is added
across currencies — the per-market figures come back separately and the response says why. A
single portfolio number that quietly used a made-up rate would be the worst of the options.

**Some fuel cannot be priced at all.** LPG in the UAE is sold by weight with no piped grid, so
there is no per-kWh rate for it. That is reported as unpriceable rather than as zero: a fuel
nobody can price and a fuel that costs nothing are different facts.
"""
from __future__ import annotations

from typing import Any

from ...core.logging import get_logger
from .market_profiles import load_profiles

log = get_logger(__name__)

#: The fuels a market can be asked to price.
FUELS = ("electricity", "gas")


def conversions() -> dict[str, Any]:
    """The unit factors, held once so nothing converts a unit its own way."""
    return (load_profiles() or {}).get("conversions") or {}


def to_kwh(value: float | None, unit: str) -> float | None:
    """Bring a reading to kWh from whatever the market bills it in.

    Returns None rather than a guess for a unit with no published factor — a converted figure
    that used an assumed factor is indistinguishable from a measured one once it is a number.
    """
    if value is None:
        return None
    c = conversions()
    u = (unit or "").strip().lower()
    if u in ("kwh", "kw h", "kilowatt hour", "kilowatt hours"):
        return float(value)
    if u in ("therm", "therms"):
        return float(value) * float(c.get("therm_to_kwh") or 29.3001)
    if u in ("rth", "rt h", "refrigeration ton hour", "refrigeration ton hours"):
        return float(value) * float(c.get("rth_to_kwh") or 3.51685)
    return None


def gas_m3_to_kwh(m3: float | None, calorific_value: float | None = None) -> dict[str, Any]:
    """UK gas: volume to energy, saying whether the calorific value was the real one.

    The correction factor is fixed; the calorific value is published per settlement period. A
    conversion that fell back to the default says so, because it is an estimate and the
    metered one is not.
    """
    c = (conversions().get("uk_gas_m3_to_kwh") or {})
    if m3 is None:
        return {"kwh": None, "basis": "no volume given"}
    vcf = float(c.get("volume_correction_factor") or 1.02264)
    cv = calorific_value if calorific_value is not None else c.get("calorific_value_default")
    if cv is None:
        return {"kwh": None, "basis": "no calorific value available"}
    kwh = float(m3) * vcf * float(cv) / 3.6
    return {
        "kwh": round(kwh, 2),
        "volume_correction_factor": vcf,
        "calorific_value": float(cv),
        "calorific_value_is_default": calorific_value is None,
        "basis": (f"m3 x {vcf} x {cv} / 3.6"
                  + (" — calorific value is the fallback default, not the metered one"
                     if calorific_value is None else "")),
    }


def tariff_for(market: str, fuel: str = "electricity") -> dict[str, Any] | None:
    """The band this market bills that fuel at, or None if it holds no tariff for it."""
    m = ((load_profiles() or {}).get("markets") or {}).get(market) or {}
    return ((m.get("tariff") or {}).get(fuel)) or None


def price_kwh(
    market: str, kwh: float | None, fuel: str = "electricity",
) -> dict[str, Any]:
    """What this much energy costs in this market, as a range in that market's currency."""
    t = tariff_for(market, fuel)
    out: dict[str, Any] = {
        "market": market, "fuel": fuel, "kwh": None if kwh is None else round(float(kwh), 2),
        "low": None, "high": None, "currency": None, "priceable": False,
        "tariff": None, "basis": None,
    }
    if kwh is None:
        out["basis"] = "no consumption given"
        return out
    if not t:
        out["basis"] = f"no tariff on file for {fuel} in {market}"
        return out

    currency = (t.get("unit") or "/").split("/")[0] or None
    out["currency"] = currency
    out["tariff"] = {"low": t.get("low"), "high": t.get("high"), "unit": t.get("unit"),
                     "label": t.get("label"), "basis": t.get("basis")}
    if t.get("unpriceable") or t.get("low") is None or t.get("high") is None:
        out["basis"] = t.get("basis") or f"{fuel} is not priced per kWh in {market}"
        return out

    out.update({
        "priceable": True,
        "low": round(float(kwh) * float(t["low"]), 2),
        "high": round(float(kwh) * float(t["high"]), 2),
        "basis": (f"{kwh:,.0f} kWh at {t.get('label')}"
                  + (f"; excludes {t['excludes']}" if t.get("excludes") else "")),
    })
    if t.get("excludes"):
        out["excludes"] = t["excludes"]
    return out


def price_by_market(
    kwh_by_market: dict[str, float], fuel: str = "electricity",
) -> dict[str, Any]:
    """Price each market's energy in its own currency, and refuse to add across currencies.

    Where every market in scope shares one currency the total is given, because then it is
    arithmetic rather than a conversion. Otherwise the per-market figures stand on their own
    and ``total`` is null with the reason — adding them would need an exchange rate on a stated
    date, and none is held here.
    """
    priced = [price_kwh(m, kwh, fuel) for m, kwh in kwh_by_market.items()]
    priced.sort(key=lambda p: -(p["high"] or 0))
    usable = [p for p in priced if p["priceable"]]
    currencies = {p["currency"] for p in usable if p["currency"]}

    out: dict[str, Any] = {
        "fuel": fuel,
        "markets": priced,
        "priced": len(usable),
        "not_priceable": [
            {"market": p["market"], "reason": p["basis"]} for p in priced if not p["priceable"]],
        "currencies": sorted(currencies),
        "total": None,
        "total_currency": None,
        "note": None,
    }
    if not usable:
        out["note"] = "nothing in scope could be priced"
    elif len(currencies) == 1:
        out["total"] = round(sum(p["low"] for p in usable), 2), round(
            sum(p["high"] for p in usable), 2)
        out["total"] = {"low": out["total"][0], "high": out["total"][1]}
        out["total_currency"] = next(iter(currencies))
        out["note"] = "one currency across the markets in scope, so the totals are arithmetic"
    else:
        out["note"] = (
            f"{len(currencies)} currencies in scope ({', '.join(sorted(currencies))}), and no "
            f"exchange rate is held here — the markets are priced separately rather than added "
            f"at a rate nobody stated")
    return out


def statutory_rule(market: str) -> dict[str, Any] | None:
    """The rule this country is actually judged by, and the penalty if it carries one."""
    m = ((load_profiles() or {}).get("markets") or {}).get(market) or {}
    return m.get("statutory") or None


def assess_statutory(
    market: str, *, eui_kwh_m2: float | None = None, tco2e: float | None = None,
    carbon_cap_tco2e: float | None = None,
) -> dict[str, Any]:
    """Is this building over the line its own country draws, and what does that cost?

    The line is not the same shape in every market. The UK and Singapore test intensity
    against a published reference and carry no penalty; the UAE tests against a rolling
    baseline because nothing operational is published there; New York tests carbon against a
    cap and fines the excess per tonne. Returning one number for all four would flatten
    exactly the difference the page exists to show.
    """
    rule = statutory_rule(market)
    out: dict[str, Any] = {
        "market": market, "assessable": False, "over": None, "rule": None,
        "basis": None, "penalty": None, "currency": None,
    }
    if not rule:
        out["basis"] = f"no statutory rule on file for {market}"
        return out
    out["rule"] = rule.get("rule")
    out["basis"] = rule.get("basis")

    if rule.get("rule") == "eui_over_reference":
        ref = rule.get("reference_eui_kwh_m2")
        if eui_kwh_m2 is None or ref is None:
            out["basis"] = (f"{rule.get('basis')} — needs a measured intensity to test against "
                            f"{ref} kWh/m2/yr")
            return out
        out.update({
            "assessable": True, "over": eui_kwh_m2 > ref,
            "reference_eui_kwh_m2": ref, "eui_kwh_m2": round(float(eui_kwh_m2), 2),
            "over_by_kwh_m2": round(float(eui_kwh_m2) - float(ref), 2),
            "basis": f"{rule.get('flags_when')} ({rule.get('basis')})",
        })
        return out

    if rule.get("rule") == "carbon_cap":
        pen = rule.get("penalty") or {}
        if tco2e is None or carbon_cap_tco2e is None:
            out["basis"] = (f"{rule.get('basis')} — needs both a computed tCO2e and the "
                            f"statutory cap for this occupancy group")
            return out
        over_by = float(tco2e) - float(carbon_cap_tco2e)
        out.update({
            "assessable": True, "over": over_by > 0,
            "tco2e": round(float(tco2e), 2),
            "cap_tco2e": round(float(carbon_cap_tco2e), 2),
            "over_by_tco2e": round(over_by, 2),
            "currency": pen.get("unit"),
            "penalty": round(max(over_by, 0.0) * float(pen.get("per_tonne_over") or 0), 2),
            "basis": f"{rule.get('flags_when')}; {pen.get('formula')}",
        })
        return out

    out["basis"] = f"rule {rule.get('rule')!r} is on file but nothing here assesses it"
    return out
