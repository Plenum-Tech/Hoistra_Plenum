"""The fallback model must be the one the deployments actually run.

`settings.compliance_summary_model or "claude-opus-5"` was written at seven call sites. Both
the Container App and the local .env set COMPLIANCE_SUMMARY_MODEL=claude-sonnet-5, so the
fallback was never reached and nobody had cause to notice it named a different, dearer model —
$5/$25 per MTok against $2/$10. A default that is never exercised still decides what happens
the day the variable is unset or mistyped.
"""
import re
from pathlib import Path

from src.config import DEFAULT_COMPLIANCE_SUMMARY_MODEL
from src.agents.llm_cost import PRICES

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


class TestTheDefault:

    def test_it_is_the_model_the_deployments_set(self):
        assert DEFAULT_COMPLIANCE_SUMMARY_MODEL == "claude-sonnet-5"

    def test_it_has_a_price(self):
        """An unmapped model returns None from cost(), so the spend silently stops being
        recorded rather than being recorded wrongly — which is harder to notice."""
        assert DEFAULT_COMPLIANCE_SUMMARY_MODEL in PRICES


class TestItIsWrittenOnlyOnce:

    def test_no_module_hardcodes_a_fallback_model(self):
        """Seven copies of a literal is how this drifted. One constant, imported."""
        offenders = []
        for py in SRC_DIR.rglob("*.py"):
            if py.name in ("config.py", "llm_cost.py"):
                continue  # the declaration, and the price table
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"""or\s+['"]claude-""", line):
                    offenders.append(f"{py.relative_to(SRC_DIR)}:{i}")
        assert not offenders, "hardcoded model fallback: " + ", ".join(offenders)

    def test_the_price_table_still_knows_the_old_model(self):
        """Kept deliberately. cost() returns None for an unmapped model, so dropping the row
        would make any opus-5 usage already logged price at nothing."""
        assert "claude-opus-5" in PRICES
