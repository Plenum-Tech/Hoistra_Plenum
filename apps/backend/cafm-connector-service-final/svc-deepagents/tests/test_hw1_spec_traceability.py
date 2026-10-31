"""Regression coverage for canonical Homework 1 expected-outcome traceability."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
VALIDATOR = REPOSITORY_ROOT / "homework/module-1/validate_hw1_spec_evidence.py"


def test_homework_one_expected_outcomes_map_to_canonical_specification() -> None:
    # Run the public entry point so documentation and CI exercise the same check.
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
