"""src/shared/name_match.py is svc-deepagents' agents/name_match.py, byte for byte (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compared a typed name
with a stored one did it its own way. The ladder now lives in one file, copied here so this service
needs no import from another; a copy that drifts is a second matcher again, and the same name would
find a building in the chat and not on the energy page. Change svc-deepagents' file and copy it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
OURS = HERE.parents[2] / "src" / "shared" / "name_match.py"
THEIRS = HERE.parents[3] / "svc-deepagents" / "src" / "agents" / "name_match.py"


def test_the_copy_is_byte_identical_to_svc_deepagents():
    if not THEIRS.is_file():
        pytest.skip(f"svc-deepagents is not beside this service here ({THEIRS} is absent), "
                    "so there is nothing to compare the copy with")
    assert OURS.read_bytes() == THEIRS.read_bytes(), (
        "src/shared/name_match.py differs from svc-deepagents/src/agents/name_match.py; "
        "copy the deepagents file over this one, unchanged")


def test_the_copy_is_importable_on_its_own():
    """No import from svc-deepagents: the copy must stand alone in this service."""
    from src.shared import name_match

    assert name_match.rung("JASMEET BUILDING", "JASMEET", {"building"}) == 0
