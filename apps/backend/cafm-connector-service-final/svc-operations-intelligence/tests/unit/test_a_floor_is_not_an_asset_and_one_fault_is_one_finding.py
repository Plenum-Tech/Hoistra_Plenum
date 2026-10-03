"""A floor is not an asset, and one fault is one finding.

Ingesting floor sub-meters on 24 Sep 2026 took Harbour Point from 21 open findings to 199.
Most of that is the honest consequence of metering the same energy three times over — building,
floor, asset — and the rollup already refuses to add those together. Two of the causes were
defects.

`asset_spike` ("sub-meter >120% of its 30-day median, sustained") was gated on
`meter.is_sub_meter`, which meant "isolates a single asset" only while the only sub-meters were
the ones on plant. Twelve floors then each reported a single-asset spike worth £22k.

And the dedup folded a repeat sighting into ONE existing finding, so once two rows for a fault
existed nothing ever collapsed them: the main gas meter carried nine open `schedule_mismatch`
rows whose windows all overlap, shown as nine live faults "active" from 2 days to 332.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from src.engines.energy import anomalies as A


class TestOnlyAMeterThatNamesAnAssetIsolatesOne:
    def test_the_gate_is_the_asset_not_the_sub_meter_flag(self):
        src = inspect.getsource(A.scan_meter_anomalies)
        assert "_isolates_one_asset = meter.asset_id is not None" in src
        assert "if _isolates_one_asset:\n        detectors.append(detect_asset_spike)" in src
        # The old gate must be gone, or a floor meter runs the rule again.
        assert "if meter.is_sub_meter:\n        detectors.append(detect_asset_spike)" not in src

    def test_a_floor_sub_meter_is_told_why_the_rule_did_not_run(self):
        src = inspect.getsource(A.scan_meter_anomalies)
        assert "reads a floor or a zone, not one machine" in src
        assert "incoming supply, so no single asset is isolated" in src


class TestOneFaultIsOneFinding:
    def test_a_superseded_finding_is_settled(self):
        """Or the next sighting could pick the row it just closed as the survivor."""
        assert "superseded" in A._SETTLED_STATUSES

    def test_supersede_keeps_the_trail_to_the_finding_it_joined(self):
        kept = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
        dup = SimpleNamespace(status="open", detail_json={"impact": "£8k/yr"})
        A._supersede(dup, kept)
        assert dup.status == "superseded"
        assert dup.detail_json["superseded_by"] == str(kept.id)
        assert "one fault, one finding" in dup.detail_json["superseded_reason"]
        # Nothing the row already said is lost.
        assert dup.detail_json["impact"] == "£8k/yr"

    def test_supersede_is_not_a_resolution(self):
        """Nobody fixed anything, so it must not read as resolved or dismissed."""
        dup = SimpleNamespace(status="open", detail_json=None)
        A._supersede(dup, SimpleNamespace(id="x"))
        assert dup.status not in ("resolved", "closed", "dismissed")

    def test_every_overlapping_open_finding_is_collapsed_not_just_the_newest(self):
        src = inspect.getsource(A.scan_meter_anomalies)
        assert "overlapping = list((" in src
        assert ".order_by(EnergyAnomaly.detected_at.asc())" in src
        assert "for _dup in overlapping[1:]:\n                _supersede(_dup, existing)" in src
        # .limit(1) was what left the other eight standing.
        assert ".limit(1)\n            )\n        ).scalars().first()" not in src

    def test_the_oldest_is_the_one_kept(self):
        """It holds the first detection and any approval raised against it."""
        src = inspect.getsource(A.scan_meter_anomalies)
        assert "existing = overlapping[0]" in src
        assert "detected_at.asc()" in src
