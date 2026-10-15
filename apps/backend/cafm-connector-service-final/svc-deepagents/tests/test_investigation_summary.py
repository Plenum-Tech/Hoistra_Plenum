"""The investigation summary: one model pass over three datasets, checked figure by figure.

The dock ends every Investigate with this. It may only say what the bms_trend, utility_bill
and weather datasets say, so each line's figures are grounded against them with the same
helpers contract_answer uses — a line that cites a figure the data does not hold is dropped and
named, and the overall sentence is reported rather than rewritten.
"""
from __future__ import annotations

import json

from src.agents import investigation_summary as isum

ASSET = {"name": "Boiler 1 — central plant", "building": "Bishopsgate Tower", "category": "Boilers"}
SOURCES = {
    "bms_trend": {"status": "found", "kind": "asset_readings", "points": 96, "graded_types": 2,
                  "out_of_band": [],
                  "types": [{"reading_type": "temperature", "unit": "°C",
                             "band": {"lo": 5, "hi": 95}, "latest": {"value": 69.43, "state": "in_band"},
                             "days": [{"day": "2026-09-26", "min": 68.1, "mean": 70.2, "max": 72.0,
                                       "n": 24, "out_of_band": 0}]},
                            {"reading_type": "pressure", "unit": "bar",
                             "band": {"lo": 3, "hi": 5.5}, "latest": {"value": 4.02, "state": "in_band"},
                             "days": []}]},
    "utility_bill": {"status": "found", "total": {"now_kwh": 1505169.0, "last_year_kwh": 8383.0,
                                                  "now_days": 56, "last_year_days": 1,
                                                  "comparable": False, "change_pct": None},
                     "first_reading_at": "2025-09-28T00:00:00+00:00",
                     "basis": "metered — the meter stands in for the bill"},
    "weather": {"status": "found", "source": "open-meteo",
                "total": {"hdd": 180.4, "last_year_hdd": 205.0, "hdd_change_pct": -12.0,
                          "cdd": 14.2, "last_year_cdd": 10.1, "cdd_change_pct": 40.6,
                          "comparable": True},
                "location": {"name": "London"}},
}


class _FakeAnthropic:
    def __init__(self, text):
        self._text, self.messages = text, self

    async def create(self, **kwargs):
        self.seen = kwargs
        return type("R", (), {"content": [type("B", (), {"text": self._text})()]})()


def reply(lines, overall="Readings are in band; the bill cannot be compared yet."):
    return json.dumps({"lines": [{"source": s, "text": t} for s, t in lines], "overall": overall})


GOOD = [
    ("bms_trend", "All 2 graded readings are within their bands across 96 points: temperature "
                  "69.43 °C, pressure 4.02 bar."),
    ("utility_bill", "Metered consumption was 1,505,169 kWh; last year has 1 of 56 days on "
                     "record, so there is no comparison. Metered history starts 2025-09-28."),
    ("weather", "Heating degree days are down 12% on last year (180.4 against 205), so a "
                "colder spell does not explain consumption."),
]


async def test_grounded_lines_are_kept_in_source_order():
    fake = _FakeAnthropic(reply(GOOD))
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=fake)
    assert out["ok"] is True
    assert [l["source"] for l in out["lines"]] == ["bms_trend", "utility_bill", "weather"]
    assert out["dropped"] == []
    assert out["meta"]["truncated"] is False
    # The data actually reached the model, and the rules went with it.
    sent = str(fake.seen["messages"])
    assert "1505169" in sent and "Boiler 1" in sent
    assert "not on record" in fake.seen["system"] and "could not be read" in fake.seen["system"]


async def test_a_line_citing_a_figure_the_data_does_not_hold_is_dropped_and_named():
    bad = GOOD[:2] + [("weather", "Heating degree days are down 31.77% on last year.")]
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic(reply(bad)))
    assert [l["source"] for l in out["lines"]] == ["bms_trend", "utility_bill"]
    assert out["dropped"] == [{"source": "weather", "figures": ["31.77%"], "kept": False}]


async def test_an_ungrounded_overall_is_reported_not_rewritten():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic(reply(GOOD, overall="Costs are up £4,430.")))
    assert out["overall"] == "Costs are up £4,430."
    assert out["dropped"] == [{"source": "overall", "figures": ["4430"], "kept": True}]


async def test_a_line_for_a_source_it_was_not_given_is_ignored():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic(reply(GOOD + [("gossip", "The vendor is late.")])))
    assert "gossip" not in [l["source"] for l in out["lines"]]


async def test_the_status_vocabulary_is_passed_through_to_the_model():
    sources = {**SOURCES, "weather": {"status": "unreadable", "detail": "archive answered HTTP 503"},
               "utility_bill": {"status": "not_found", "detail": "no meter recorded consumption"}}
    fake = _FakeAnthropic(reply([("weather", "The weather could not be read."),
                                 ("utility_bill", "Nothing is on record for the meters.")]))
    out = await isum.summarise(asset=ASSET, sources=sources, api_key="k", client=fake)
    assert out["ok"] is True
    sent = str(fake.seen["messages"])
    assert "unreadable" in sent and "archive answered HTTP 503" in sent


async def test_an_oversized_dataset_is_cut_and_the_model_is_told():
    big = {**SOURCES, "bms_trend": {**SOURCES["bms_trend"], "types": [
        {**SOURCES["bms_trend"]["types"][0], "days": [
            {"day": f"2026-08-{d:02d}", "min": 1.0, "mean": 2.0, "max": 3.0, "n": 24, "out_of_band": 0}
            for d in range(1, 29)] * 40}]}}
    fake = _FakeAnthropic(reply(GOOD))
    out = await isum.summarise(asset=ASSET, sources=big, api_key="k", client=fake, budget_chars=6000)
    assert out["meta"]["truncated"] is True
    assert "TRUNCATED" in str(fake.seen["messages"])
    assert len(str(fake.seen["messages"])) < 12_000


async def test_no_key_is_a_stated_failure():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="")
    assert out == {"ok": False, "reason": "no anthropic key", "meta": {"model": isum.DEFAULT_MODEL}}


async def test_json_wrapped_in_prose_is_still_read():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic("Here is the summary:\n```json\n" + reply(GOOD) + "\n```"))
    assert out["ok"] is True and len(out["lines"]) == 3


async def test_a_reply_with_no_json_is_a_stated_failure():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic("I cannot summarise this."))
    assert out["ok"] is False and "no JSON" in out["reason"]


async def test_every_line_dropped_is_a_stated_failure():
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k",
                               client=_FakeAnthropic(reply([("weather", "Down 31.77%."),
                                                            ("bms_trend", "Up 613.9%.")])))
    assert out["ok"] is False and "every line" in out["reason"]
    assert len(out["dropped"]) == 2


async def test_a_model_error_is_a_stated_failure():
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        async def create(self, **kw):
            raise RuntimeError("overloaded")

    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=Boom())
    assert out["ok"] is False and "overloaded" in out["reason"]


# Found on the first live run, 28 Sep 2026: with the bill marked not comparable the model still
# wrote "metered consumption has risen enormously" from the 8,383 kWh of last year's one day.
# A figure that is not a comparison is withheld from the model, and a line citing it is dropped.

async def test_last_years_figure_is_withheld_when_there_is_no_comparison():
    fake = _FakeAnthropic(reply(GOOD))
    await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=fake)
    sent = str(fake.seen["messages"])
    assert "8383" not in sent
    assert "last_year_withheld" in sent


async def test_a_line_quoting_the_withheld_figure_is_dropped():
    bad = [GOOD[0], ("utility_bill", "Metered consumption was 1,505,169 kWh against 8,383 kWh last year."), GOOD[2]]
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=_FakeAnthropic(reply(bad)))
    assert [l["source"] for l in out["lines"]] == ["bms_trend", "weather"]
    assert out["dropped"] == [{"source": "utility_bill", "figures": ["8383"], "kept": False}]


async def test_a_comparable_bill_keeps_last_year():
    comparable = {**SOURCES, "utility_bill": {**SOURCES["utility_bill"], "total": {
        "now_kwh": 1100.0, "last_year_kwh": 1000.0, "now_days": 56, "last_year_days": 56,
        "comparable": True, "change_pct": 10.0}}}
    fake = _FakeAnthropic(reply(GOOD))
    await isum.summarise(asset=ASSET, sources=comparable, api_key="k", client=fake)
    assert "1000.0" in str(fake.seen["messages"])


async def test_the_rules_forbid_describing_a_change_without_a_comparison():
    fake = _FakeAnthropic(reply(GOOD))
    await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=fake)
    assert "do not describe a rise or fall" in fake.seen["system"]
    assert "Do not compute new figures" in fake.seen["system"]


# ── Review fixes, 28 Sep 2026: the check was too forgiving ─────────────────────────────
# contract_answer's rules accept any integer up to 12 and any sum or difference of two
# figures, and the check ran against the full data rather than what the model was sent. On
# realistic data most invented figures passed — "up 8% on last year" for a bill with no
# comparison among them.

async def test_a_percentage_must_be_one_of_the_datas_own_changes():
    bad = [GOOD[0], ("utility_bill", "Metered consumption is up 8% on last year."), GOOD[2]]
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=_FakeAnthropic(reply(bad)))
    assert [l["source"] for l in out["lines"]] == ["bms_trend", "weather"]
    assert out["dropped"] == [{"source": "utility_bill", "figures": ["8%"], "kept": False}]


async def test_a_change_percentage_the_data_holds_is_kept_rounded_or_not():
    lines = [("weather", "Cooling degree days are up 41% (14.2 against 10.1), heating down 12%.")]
    out = await isum.summarise(asset=ASSET, sources=SOURCES, api_key="k", client=_FakeAnthropic(reply(lines)))
    assert out["ok"] is True and out["dropped"] == []


async def test_a_figure_only_in_the_part_that_was_cut_is_not_grounded():
    days = [{"day": f"2026-08-{d % 28 + 1:02d}", "min": 1.0, "mean": 2.0, "max": 3.0, "n": 24,
             "out_of_band": 0} for d in range(1200)]
    days[-1] = {**days[-1], "max": 987.65}
    big = {**SOURCES, "bms_trend": {**SOURCES["bms_trend"], "types": [
        {**SOURCES["bms_trend"]["types"][0], "days": days}]}}
    lines = [("bms_trend", "Temperature peaked at 987.65 °C.")]
    out = await isum.summarise(asset=ASSET, sources=big, api_key="k",
                               client=_FakeAnthropic(reply(lines)), budget_chars=6000)
    assert out["meta"]["truncated"] is True
    assert out["ok"] is False and out["dropped"][0]["figures"] == ["987.65"]


async def test_last_years_months_are_withheld_when_the_weather_is_not_comparable():
    wx = {"status": "found", "source": "weather_degree_days",
          "total": {"hdd": 44.1, "last_year_hdd": None, "comparable": False},
          "months": [{"month": "2026-09", "hdd": 44.1, "last_year_hdd": 97.6, "last_year_cdd": 3.0}]}
    fake = _FakeAnthropic(reply([("weather", "Heating degree days were 44.1 against 97.6 last September.")]))
    out = await isum.summarise(asset=ASSET, sources={**SOURCES, "weather": wx}, api_key="k", client=fake)
    assert "97.6" not in str(fake.seen["messages"])
    assert out["dropped"] and out["dropped"][0]["figures"] == ["97.6"]
