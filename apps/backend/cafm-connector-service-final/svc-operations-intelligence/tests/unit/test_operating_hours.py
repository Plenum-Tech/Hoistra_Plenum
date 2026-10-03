"""Deriving the hours a building keeps, so benchmark fit can be told from waste.

A building at 198 kWh/m2/yr against a reference of 172 is 15% over. Part is waste and part is
that the reference assumes a shorter week than the building works — a work order versus a
re-benchmark. Nothing could separate them: the assumption was not in the schema and the actual
was never derived, so any claim about operating hours was invented.
"""
from src.engines.energy.operating_hours import IN_USE_FRACTION, MIN_DAYS, _span, in_use_hours


def office_day(peak: float = 100.0, base: float = 10.0) -> dict[int, float]:
    """A bimodal day: overnight base, 08:00-18:00 plateau."""
    return {h: (peak if 8 <= h < 18 else base) for h in range(24)}


class TestReadingTheLoadProfile:

    def test_an_office_day_is_found(self):
        hours = in_use_hours(office_day())
        assert hours == list(range(8, 18))

    def test_the_threshold_is_relative_not_absolute(self):
        """The same building in January and July has the same shape at different magnitudes.
        An absolute kWh cutoff would find a summer office closed."""
        winter = in_use_hours(office_day(peak=400.0, base=40.0))
        summer = in_use_hours(office_day(peak=100.0, base=10.0))
        assert winter == summer

    def test_a_flat_load_yields_no_hours(self):
        """A continuous process load, or a meter reporting a constant. There is no occupancy
        signal to read, and inventing one out of noise would be worse than saying so."""
        assert in_use_hours({h: 50.0 for h in range(24)}) == []

    def test_too_few_buckets_yields_nothing(self):
        assert in_use_hours({1: 5.0, 2: 90.0}) == []

    def test_nothing_in_yields_nothing_out(self):
        assert in_use_hours({}) == []


class TestTheSpan:

    def test_close_is_the_exclusive_end(self):
        """08:00-18:00 means the last in-use hour is 17, so it closes at 18."""
        assert _span(list(range(8, 18))) == (8, 18, True)

    def test_a_break_is_reported_not_smoothed(self):
        """Two shifts, or a cleaning window. Printing 06:00-22:00 over it would describe a
        continuity the readings do not show."""
        open_h, close_h, contiguous = _span([6, 7, 8, 20, 21])
        assert (open_h, close_h) == (6, 22)
        assert contiguous is False

    def test_an_empty_day_is_contiguous_and_empty(self):
        assert _span([]) == (None, None, True)


class TestTheGuards:

    def test_a_fortnight_is_the_floor(self):
        """Answering "the building opens at 07:00" off three days is a guess with a decimal
        point on it."""
        assert MIN_DAYS >= 14

    def test_the_in_use_fraction_leaves_the_base_load_out(self):
        assert 0.0 < IN_USE_FRACTION < 0.5


class TestSimulatedReadingsAreNotMeasurement:
    """98% of readings in this deployment carry source='simulator', and every building but one
    is 99-100% simulated over ninety days — which is why five different buildings derived
    exactly the same 07:00-19:00. A pattern read off invented consumption is a fact about the
    simulator, and this figure is used to argue a benchmark fits a building badly."""

    def test_the_threshold_is_strict(self):
        from src.engines.energy.operating_hours import SIMULATED_LIMIT_PCT
        assert SIMULATED_LIMIT_PCT <= 25.0

    def test_a_simulated_profile_is_refused_before_anything_else(self):
        """The warning replaces the note rather than appending to it. A caller who reads "the
        building opens at 07:00" acts on it long before reading a field further down."""
        from src.engines.energy.operating_hours import _note
        note = _note(True, True, list(range(7, 19)), 12, 89, simulated_pct=99.9)
        assert "simulated" in note.lower()
        assert "re-benchmark argument" in note or "cannot" in note
        assert "hours a WEEK longer" not in note

    def test_a_half_simulated_profile_is_still_refused(self):
        """Harbour View is 47% simulated — the least synthetic building in the estate, and
        still half invented."""
        from src.engines.energy.operating_hours import _note
        assert "simulated" in _note(True, True, list(range(7, 19)), 12, 89,
                                    simulated_pct=47.4).lower()

    def test_real_data_gets_the_real_note(self):
        from src.engines.energy.operating_hours import _note
        note = _note(True, True, list(range(7, 19)), 12, 89, simulated_pct=0.0)
        assert "simulated" not in note.lower()
        assert "hours a WEEK longer" in note
