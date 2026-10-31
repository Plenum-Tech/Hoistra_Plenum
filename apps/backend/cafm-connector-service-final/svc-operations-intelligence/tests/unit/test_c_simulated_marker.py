"""A generated reading must never be mistaken for a metered one.

Harbour View's meters carry real half-hourly reads to 31 August and a simulated feed from
1 September. Told only "simulated", a reader discounts five real months; told nothing, they
act on invented ones. So the marker carries the dates, and every figure derived from that
period says where it came from.
"""
import inspect
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[5] / "frontend" / "src"


class TestTheBuildingRowSaysWhatIsSimulated:
    def test_coverage_is_counted_from_the_readings_not_the_meter_flag(self):
        from src.engines.energy.buildings import simulated_coverage

        body = inspect.getsource(simulated_coverage)
        assert "source = 'simulator'" in body
        assert "meter_readings" in body, "the flag says what the feed is doing, not what it wrote"
        for field in ("sim_from", "sim_to", "real_to"):
            assert field in body, f"the marker cannot name a period without {field}"

    def test_it_never_takes_the_page_down(self):
        """A marker is worth less than the list it annotates."""
        body = inspect.getsource(__import__(
            "src.engines.energy.buildings", fromlist=["simulated_coverage"]).simulated_coverage)
        assert "except Exception" in body and "return {}" in body

    def test_the_note_avoids_a_posix_only_format(self):
        """%-d is a POSIX strftime extension; on Windows it raises, and the except above
        would swallow the marker entirely rather than show a wrong date."""
        from src.engines.energy import buildings

        assert "%-d" not in inspect.getsource(buildings)


class TestAnAnomalySaysWhatItWasDetectedOn:
    def test_the_marker_only_counts_readings_the_detector_could_see(self):
        from src.engines.energy.anomalies import list_anomalies

        body = inspect.getsource(list_anomalies)
        assert "r.created_at <= a.detected_at" in body, (
            "a reading written after the anomaly was raised was not available to it")
        assert '"simulated":' in body

    def test_the_lookup_cannot_fail_the_list(self):
        from src.engines.energy.anomalies import list_anomalies

        body = inspect.getsource(list_anomalies)
        assert "except Exception" in body


class TestTheFrontendShowsIt:
    def _read(self, *parts):
        return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_anomaly_impact_is_not_hardcoded_sterling(self):
        """The API returns the building's own currency; printing £ on a Dubai meter's
        dirhams is the same fault the backend just had."""
        live = self._read("logic", "energyLive.js")
        assert "export function money(" in live
        assert "impactLabel(a)" in live

    def test_an_unpriced_anomaly_shows_its_own_measure(self):
        live = self._read("logic", "energyLive.js")
        label = live[live.index("export function impactLabel"):]
        assert "im.value" in label and "im.unit" in label, (
            "a rule that could not be priced still has a number to show")

    def test_the_building_row_carries_the_period_not_just_a_flag(self):
        assert "simulated: r.simulated" in self._read("logic", "buildingsLive.js")

    def test_the_banner_and_the_row_chip_are_rendered(self):
        module = self._read("screens", "Module.jsx")
        assert "vals.enSimulatedShow" in module and "vals.enSimulatedText" in module
        assert "a.simulatedShow" in module

    def test_there_is_no_seed_portfolio_left_to_mark(self):
        """This used to assert the demo portfolio was never flagged as simulated. There is
        no demo portfolio now: the Energy page reads the database or it reads nothing, so
        the question cannot arise. Kept as the guard against it coming back — a bundled
        portfolio rendered against an empty database is indistinguishable from real data,
        which is exactly how one came to be read as real."""
        energy = self._read("logic", "energy.js")
        assert "enBuildingValsSeed" not in energy
        assert "BUILDINGS" not in energy, "the bundled nine-building constant is back"
        assert "this.D()" not in energy, "the bundled anomalies are back"

    def test_the_live_list_still_marks_a_simulated_feed(self):
        """The marker itself is not what went away. A real building fed by the simulator
        still says so, per row and once above the list."""
        energy = self._read("logic", "energy.js")
        live = energy[energy.index("enBuildingValsLive(s) {"):]
        assert "simulatedShow: a.simulated" in live
        assert "enSimulatedShow:" in live and "b.simulated && b.simulated.any" in live
