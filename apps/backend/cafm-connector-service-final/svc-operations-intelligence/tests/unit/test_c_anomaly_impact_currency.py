"""What an anomaly says it costs, and in whose money.

Two faults these cover. Simultaneous heating and cooling fired on a 225% metric and
reported £0.00, because an unpriceable figure was written as zero — so a genuine plant
fault sat on the PM's queue looking like something that costs nothing. And every figure was
labelled sterling regardless of where the building is, so a Dubai meter's dirhams were
summed with Glasgow's pounds and escalated against a sterling threshold.
"""
from datetime import datetime, timedelta, timezone

from src.engines.energy import anomalies as A
from src.engines.energy import detectors as D

TARIFF = 0.28


def trend(zone: str, minutes: int, both_for: int):
    """BMS samples at 5-minute cadence; heating and cooling both calling for `both_for`."""
    start = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    out, t, elapsed = [], start, 0
    while elapsed < minutes:
        fighting = elapsed < both_for
        out.append((t, zone, 60.0 if fighting else 60.0, 55.0 if fighting else 0.0))
        t += timedelta(minutes=5)
        elapsed += 5
    return out


class TestTheFightRuleLeadsWithWhatItMeasures:
    def test_unpriced_reports_hours_not_zero_pounds(self):
        hit = D.detect_simultaneous_heating_cooling(trend("L3-E", 240, 60), tariff=TARIFF)
        assert hit is not None
        assert hit["priced"] is False
        assert hit["financial_gbp"] is None, "an unknown cost must not be written as zero"
        assert hit["impact"]["measure"] == "fighting_hours"
        assert hit["impact"]["value"] > 0
        assert hit["impact"]["unit"] == "h"
        assert "zone" in hit["impact"]["note"]

    def test_priced_when_the_zone_plant_draw_is_known(self):
        hit = D.detect_simultaneous_heating_cooling(trend("L3-E", 240, 60), tariff=TARIFF, zone_kw=12.0)
        assert hit["priced"] is True
        assert hit["financial_gbp"] > 0
        assert hit["impact"]["measure"] == "annualised_cost"

    def test_the_hours_are_carried_either_way(self):
        for zone_kw in (None, 12.0):
            hit = D.detect_simultaneous_heating_cooling(
                trend("L3-E", 240, 60), tariff=TARIFF, zone_kw=zone_kw)
            assert hit["detail"]["fighting_hours"] > 0
            assert hit["detail"]["longest_run_minutes"] >= 30


class TestZeroIsAClaimAndNoneIsNot:
    def test_a_data_fault_still_costs_nothing_on_purpose(self):
        fin = D._finance(0.0, 52.0, TARIFF, "GBP")
        assert fin["priced"] is True and fin["financial_gbp"] == 0.0

    def test_an_unpriceable_firing_has_no_figure_at_all(self):
        fin = D._finance(None, 52.0, TARIFF, "AED")
        assert fin["priced"] is False
        assert fin["financial_gbp"] is None
        assert fin["excess_kwh"] is None and fin["annualised_excess_kwh"] is None
        assert fin["currency"] == "AED", "the currency is known even when the amount is not"

    def test_the_shared_translation_is_the_same_one(self):
        """The three rules in anomalies.py and the ten in detectors.py must not drift."""
        a = A.financial_translation(excess_kwh=None, annualised_frequency=52.0, tariff=TARIFF)
        b = D._finance(None, 52.0, TARIFF)
        assert a == b


class TestEveryCountryGetsItsOwnMoney:
    def test_the_currency_follows_the_building(self):
        assert D.currency_for("AE") == "AED"
        assert D.currency_for("US") == "USD"
        assert D.currency_for("SG") == "SGD"
        assert D.currency_for("UK") == "GBP" and D.currency_for("GB") == "GBP"

    def test_an_unknown_or_missing_country_falls_back_rather_than_failing(self):
        for value in (None, "", "  ", "ZZ"):
            assert D.currency_for(value) == D.DEFAULT_CURRENCY

    def test_the_country_code_is_read_case_and_space_insensitively(self):
        assert D.currency_for(" ae ") == "AED"
        assert D.currency_for("Sg") == "SGD"

    def test_escalation_means_the_same_thing_in_every_currency(self):
        """AED 600 is not the money £600 is; comparing it to 500 made every AE anomaly
        read as high severity where the identical UK one read as medium."""
        assert A.severity_for(600.0, "GBP") == "high"
        assert A.severity_for(600.0, "AED") == "medium"
        assert A.severity_for(3000.0, "AED") == "high"
        assert A.severity_for(400.0, "GBP") == "medium"

    def test_every_currency_in_the_map_has_a_threshold(self):
        for currency in set(D.CURRENCY_BY_COUNTRY.values()):
            assert currency in A.HIGH_SEVERITY_AT, currency

    def test_an_unpriced_anomaly_is_not_filed_as_minor(self):
        """Unknown is not small — it must not be sorted below a £1 finding."""
        assert A.severity_for(None, "GBP") == "medium"

    def test_the_summary_never_prints_a_currency_it_does_not_mean(self):
        assert "AED 240.0" in A.money_phrase(240.0, "AED", None)
        assert "GBP" in A.money_phrase(240.0, None, None), "no currency → the default, said"

    def test_an_unpriceable_summary_reads_as_its_own_measure(self):
        phrase = A.money_phrase(None, "AED", {"measure": "fighting_hours", "value": 3.8, "unit": "h"})
        assert "3.8 h" in phrase and "not priceable" in phrase
        assert "0" not in phrase.split("not priceable")[0].replace("3.8", "")

    def test_a_rule_with_neither_money_nor_a_measure_says_so(self):
        assert A.money_phrase(None, "GBP", None) == "impact not quantified"


class TestTheMonthlyReportSpeaksTheSiteCurrency:
    def test_a_priced_row_prints_its_own_currency(self):
        from src.engines.energy.reports import _amount

        assert _amount({"financial_gbp": 1288.39}, "AED") == "AED 1288.39"
        assert _amount({"financial_gbp": 0.0}, "GBP") == "GBP 0.0"

    def test_an_unpriced_row_prints_its_measure_not_a_zero(self):
        from src.engines.energy.reports import _amount

        row = {"financial_gbp": None,
               "impact": {"measure": "fighting_hours", "value": 1.12, "unit": "h"}}
        assert _amount(row, "GBP") == "1.12 h (not priced)"

    def test_a_row_with_nothing_at_all_says_not_priced(self):
        from src.engines.energy.reports import _amount

        assert _amount({"financial_gbp": None}, "GBP") == "not priced"
        assert _amount({"financial_gbp": None, "impact": {}}, "GBP") == "not priced"

    def test_no_currency_is_hardcoded_into_the_report_text(self):
        """A Dubai site's report printed sterling on every line of it.

        Checked against the strings the module actually emits, not its source text \u2014
        comments and docstrings are free to name the pound when explaining why it went.
        """
        import ast
        import inspect

        from src.engines.energy import reports

        tree = ast.parse(inspect.getsource(reports))
        docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                        ast.AsyncFunctionDef))}
        emitted = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node.value) not in docstrings
        ]
        offenders = [v for v in emitted if "\u00a3" in v]
        assert not offenders, f"a hardcoded pound sign is back in the report output: {offenders}"
