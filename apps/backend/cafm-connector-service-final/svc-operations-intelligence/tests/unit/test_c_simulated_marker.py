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

    def test_the_seed_portfolio_is_never_marked_simulated(self):
        """The demo portfolio is not the simulator's output, and a banner there would be
        telling the truth about the wrong thing."""
        energy = self._read("logic", "energy.js")
        # Sliced between the two DEFINITIONS: both names also appear in the dispatcher
        # above them, and slicing on the bare name cuts the seed body out entirely — the
        # test then passes by reading nothing.
        seed = energy[energy.index("enBuildingValsSeed(s) {"):energy.index("enBuildingValsLive(s) {")]
        assert "rawGroups" in seed, "the slice missed the seed body"
        assert 'enSimulatedShow: "none"' in seed
        assert "b.simulated" not in seed
