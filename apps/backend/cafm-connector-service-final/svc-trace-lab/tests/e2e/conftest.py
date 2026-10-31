"""Makes the flat helper modules of svc-trace-lab/tests (tl_scratch, golden_fakes, golden_xlsx, ...) importable from
tests/e2e/, where pytest puts only this folder on sys.path. Nothing else happens here."""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parent.parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
