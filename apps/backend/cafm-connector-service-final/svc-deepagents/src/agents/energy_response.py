"""Render source-backed Homework Energy values without inventing missing evidence."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def number(value):
    """Round display only; tool results retain their full calculation precision."""
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            return "unavailable"
        return format(parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError, TypeError):
        return "unavailable"


def render_eui(data, *, fictional=False):
    """Use only authorized returned fields; unknown hourly coverage remains unknown."""
    lines = ["Fictional Homework 1 data." if fictional else "Source-backed Energy result."]
    lines.append(f"Building {data.get('building_id', 'unavailable')}; meter {data.get('meter_id', 'unavailable')}.")
    days = data.get("period_days_inclusive")
    lines.append(f"Period: {data.get('period_start', 'unavailable')} through {data.get('period_end', 'unavailable')}; "
                 f"{days if days is not None else 'unavailable'} inclusive days.")
    consumption, area = number(data.get("consumption_kwh")), number(data.get("floor_area_m2"))
    lines.append(f"Verified consumption: {consumption} kWh; eligible floor area: {area} m².")
    if (data.get("eui_available") and consumption != "unavailable" and area != "unavailable"
            and Decimal(area) > 0 and isinstance(days, int) and days > 0):
        lines.append(f"Period EUI: {consumption} kWh ÷ {area} m² = {number(data.get('eui_kwh_per_m2_period'))} kWh/m².")
        annual = number(data.get("annualized_eui_kwh_per_m2_year"))
        if annual != "unavailable" and data.get("annualization_basis") == "inclusive_period_days" and isinstance(days, int) and days > 0:
            lines.append(f"Annualized estimate: {number(data.get('eui_kwh_per_m2_period'))} × 365 ÷ {days} = "
                         f"{annual} kWh/m²/year. This extrapolates the period; it is not a measured annual total.")
        else:
            lines.append("Annualized EUI unavailable: verified estimate or annualization basis is missing.")
    else:
        lines.append("EUI unavailable: required verified consumption, area or period evidence is missing or invalid.")
    if data.get("benchmark_available"):
        benchmark = number(data.get("benchmark_kwh_per_m2_year"))
        lines.append(f"Indicative TM46 {data.get('benchmark_label', 'unavailable')} benchmark: {benchmark} kWh/m²/year; "
                     f"annualized deviation: {number(data.get('deviation_pct'))}%.")
        if benchmark != "unavailable" and area != "unavailable" and isinstance(days, int) and days > 0:
            lines.append(f"Benchmark period basis: {benchmark} × {area} × {days} ÷ 365 kWh; "
                         f"period excess: {number(data.get('benchmark_excess_kwh'))} kWh.")
        tariff, cost = number(data.get("tariff_gbp_per_kwh")), number(data.get("financial_excess_gbp"))
        if data.get("financial_excess_available") and tariff != "unavailable" and cost != "unavailable":
            lines.append(f"Excess cost: {number(data.get('benchmark_excess_kwh'))} kWh × £{tariff}/kWh = £{cost} "
                         "(calculated using unrounded excess).")
        else:
            lines.append("Tariff-derived excess cost unavailable: verified tariff or cost evidence is missing.")
    else:
        lines.append("Benchmark comparison unavailable: " + str(data.get("benchmark_unavailable_reason") or "verified compatible benchmark missing") + ".")
    coverage = data.get("coverage") or {}
    lines.append(f"Evidence: {data.get('reading_count', 'unavailable')} source record(s); "
                 f"latest source reading {data.get('freshness') or 'unavailable'}.")
    if coverage.get("basis") == "source-defined period totals":
        lines.append("Consumption uses source-defined period totals; a source timestamp alone does not establish hourly coverage.")
    if coverage.get("complete") is None:
        lines.append("Hourly completeness is unknown; missing hours are not assumed to be zero.")
    elif coverage.get("complete") is False:
        lines.append(f"Hourly coverage is incomplete; missing periods: {coverage.get('missing_hourly_periods', 'unavailable')}.")
    elif coverage.get("complete") is True:
        lines.append("The tool reports complete coverage for the stated period.")
    versions = data.get("source_versions") or {}
    refs = [f"{source} (version {versions.get(source, 'unavailable')})" for source in data.get("source_ids", [])]
    lines.append("Sources: " + ("; ".join(refs) if refs else "unavailable") + ".")
    return "\n".join(lines)
