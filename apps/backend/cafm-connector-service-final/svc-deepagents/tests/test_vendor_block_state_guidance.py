"""The prompt must say which column means "blocked".

plenum_cafm.vendors carries both `status` and `block_state`, and they do not mean the same
thing. Measured on the live database: `status` is 'active' on all 2,072 rows, and blocking lives
in `block_state` ('Clear' 2,066 / 'Blocked' 6).

That makes "which vendors are blocked?" a question with a silent wrong answer. An agent writing
its own SQL naturally reaches for `status = 'blocked'`, which matches nothing — for every
company — and reads back as a truthful "none are blocked". A company with four blocked vendors
is told it has none, and nothing in the response says the filter was wrong.
"""
import pathlib

PROMPT = (pathlib.Path(__file__).resolve().parents[1]
          / "src" / "agents" / "system_prompt.py").read_text(encoding="utf-8")


class TestTheBlockedVendorColumnIsNamed:

    def test_the_prompt_names_block_state(self):
        assert "block_state" in PROMPT

    def test_it_says_status_is_not_the_blocking_column(self):
        """Naming the right column is not enough on its own — the wrong one is the obvious
        guess, so the prompt has to rule it out by name."""
        lowered = PROMPT.lower()
        assert "never `status`" in lowered or "not `status`" in lowered or (
            "`status` is the account record" in lowered)

    def test_it_gives_the_value_to_filter_on(self):
        assert "'Blocked'" in PROMPT

    def test_it_names_the_supporting_columns(self):
        """A blocked vendor is only actionable with the reason and the lapsed accreditation."""
        for col in ("block_reason", "block_date", "blocked_accreditation_type"):
            assert col in PROMPT, col

    def test_it_points_at_the_tool_before_raw_sql(self):
        """The compliance tool carries the reason and the accreditation already; hand-written
        SQL is the fallback, not the first move."""
        assert 'risk_filter="blocked"' in PROMPT
