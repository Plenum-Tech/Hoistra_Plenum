"""One button, both passes — because the page reads them as one answer.

The Energy page is built on two derivations of the same readings. The anomaly rules find what
deviates from a building's OWN baseline; the benchmark pass derives its EUI and compares that
against its market's reference. Only the first ran behind "Run energy scan".

So after an ingest on 23 Sep 2026 the page showed £119k of anomalies priced to the penny beside
"no EUI reading on record for any building in scope yet" and "no EUI reading to compare against a
benchmark". Nothing was missing: 35,040 readings were on file and the anomaly pass had read all
of them. Nobody had asked them the second question. The benchmark pass runs on a daily schedule,
so the page corrected itself by the next morning and looked broken until then — which is the
worst shape for a bug, because it disappears before anyone can report it.

The benchmark half is not allowed to fail the scan. The anomalies are found and committed by the
time it runs, and reporting the whole run as failed because the second half did would throw that
away and tell the user nothing true.
"""
from __future__ import annotations

import ast
import re

from src.api.routes import energy as route


def _scan_all_source() -> str:
    src = open(route.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "scan_all_anomalies")
    lines = src.splitlines()
    return "\n".join(lines[fn.lineno - 1:fn.end_lineno])


class TestTheScanDerivesTheEui:
    def test_it_runs_the_benchmark_pass(self):
        assert "bench_svc.validate(" in _scan_all_source(), \
            "without this the page shows anomalies and no EUI until the daily job"

    def test_the_benchmark_pass_persists(self):
        """A pass that does not write leaves the page exactly as it was."""
        assert "persist=True" in _scan_all_source()

    def test_it_is_scoped_to_the_caller(self):
        body = _scan_all_source()
        assert "position_svc.building_ids_for(" in body, "a scan must not reach other tenants"
        assert "organization_id=organization_id" in body

    def test_both_the_sweep_and_the_plain_scan_reach_it(self):
        """history_days takes a different branch; both must end at the same benchmark pass."""
        body = _scan_all_source()
        assert "backfill_all_active_meters" in body and "scan_all_active_meters" in body
        assert body.count("bench_svc.validate(") == 1, \
            "one call after the branch, not one per branch"
        assert not re.search(r"return await anom_svc\.\w+\(", body), \
            "an early return would skip the benchmark pass"


class TestTheAnomaliesSurviveABenchmarkFailure:
    def test_the_benchmark_pass_cannot_fail_the_scan(self):
        body = _scan_all_source()
        i = body.index("bench_svc.validate(")
        assert "try:" in body[:i], "the benchmark pass runs inside a try"
        assert "except Exception" in body[i:]

    def test_a_failure_is_reported_rather_than_swallowed(self):
        body = _scan_all_source()
        assert '"ok": False' in body and "log.warning(" in body, \
            "silence here is what made the original fault take a morning to notice"

    def test_the_result_says_what_the_benchmark_half_did(self):
        body = _scan_all_source()
        for key in ('"benchmarks"', "eui_derived", "snapshots_written"):
            assert key in body, f"the caller cannot see {key}"
