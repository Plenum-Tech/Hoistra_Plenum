"""Unit tests for CCC §3.1 batch ingest bounds + type classification heuristic."""
from __future__ import annotations

from dataclasses import dataclass

from src.engines.compliance.batch_ingest import MAX_BATCH_FILES, ingest_batch
from src.engines.compliance.classify import _heuristic_pick


@dataclass
class _FakePack:
    certificate_type_code: str
    certificate_type_name: str
    certificate_scope: str = "Building"


_PACKS = [
    _FakePack("EPC", "Energy Performance Certificate"),
    _FakePack("GAS_SAFE", "Gas Safety Certificate — Commercial (CP17)"),
    _FakePack("SIA_ACS", "SIA Approved Contractor Scheme", "Vendor"),
    _FakePack("FRA", "Fire Risk Assessment"),
    _FakePack("BPCA", "BPCA Corporate Membership", "Vendor"),
]


def test_heuristic_picks_epc_from_name():
    pack, conf = _heuristic_pick(_PACKS, "Energy Performance Certificate for 10 High St")
    assert pack is not None and pack.certificate_type_code == "EPC"
    assert conf in {"medium", "low"}


def test_heuristic_picks_from_keyword_hint():
    # "cp17" keyword hint maps to the commercial gas type even without name overlap.
    pack, _ = _heuristic_pick(_PACKS, "annual CP17 record for boiler room")
    assert pack is not None and pack.certificate_type_code == "GAS_SAFE"


def test_heuristic_picks_sia_acs_by_brand():
    pack, _ = _heuristic_pick(_PACKS, "SIA ACS approved contractor certificate")
    assert pack is not None and pack.certificate_type_code == "SIA_ACS"


def test_heuristic_returns_none_when_no_signal():
    pack, conf = _heuristic_pick(_PACKS, "quarterly newsletter about the weather")
    assert pack is None and conf == "low"


async def test_ingest_batch_rejects_more_than_five():
    files = [{"file_name": f"{i}.pdf"} for i in range(MAX_BATCH_FILES + 1)]
    out = await ingest_batch(files=files)
    assert out["ok"] is False
    assert out["max_files"] == MAX_BATCH_FILES


async def test_ingest_batch_rejects_empty():
    out = await ingest_batch(files=[])
    assert out["ok"] is False
