"""An omitted limit must fall back, not crash the run.

score_work_orders_from_udr and friends all declare `limit: int = 500`, but the route passes
`body.limit` through verbatim. A request that simply omits it sends None, which overrides
the default instead of falling back to it, and `int(None)` raised TypeError inside
_build_udr_wo_query — before a single work order was read, taking the whole scoring run
with it. Observed 23 Sep 2026 scoring 34 vendor-month buckets.
"""
from src.engines.contract_performance.scoring import _row_limit


class TestFallsBack:
    def test_none_takes_the_default(self):
        assert _row_limit(None) == 500

    def test_a_nonsense_value_takes_the_default(self):
        assert _row_limit("lots") == 500

    def test_zero_and_negatives_take_the_default(self):
        assert _row_limit(0) == 500
        assert _row_limit(-5) == 500

    def test_the_default_is_overridable(self):
        assert _row_limit(None, default=50) == 50


class TestRealLimitsSurvive:
    def test_an_int_is_kept(self):
        assert _row_limit(120) == 120

    def test_a_numeric_string_is_accepted(self):
        assert _row_limit("250") == 250
