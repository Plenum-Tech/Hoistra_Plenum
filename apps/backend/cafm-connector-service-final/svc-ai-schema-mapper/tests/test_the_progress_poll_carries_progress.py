"""The progress poll carries progress, not the pipeline's whole record.

A migration of the 17-sheet workbook on 24 Sep 2026 ended with the panel frozen on "Node 8 of 9"
and "Could not read the migration: timed out after 60s", while the worker log said
`✓ MIGRATION COMPLETE`. The run had finished; the page could not read that it had.

`GET /api/migration/{id}/status` was returning 1,443,751 bytes and taking up to 2.1 seconds,
every ~3 seconds. Almost none of it was progress — `nodes[].output` held 715 KB of
Pre-Semantic mapping and 57 KB of file parse, and `udr_column_intelligence` another 651 KB of
per-column report — and nothing read any of it: not the frontend, which uses the top-level
artefact URLs and `nodes[].logs`, and not svc-deepagents' migration agent. When the UDR stage
after Node 9 then blocked the event loop for 69 seconds, the queued polls passed the client's
60-second timeout.

Measured against the real endpoint after the fix: 24,775 bytes in 0.36s.

These tests read the source rather than standing a database and a finished migration up behind
an HTTP client, because what went wrong is a decision about what to send, and that decision is
what they hold.
"""
from __future__ import annotations

import os
import re

# Read rather than import: app.py pulls in cafm_shared, which is installed in the container and
# not on a bare test host, and every assertion here is about the source anyway.
_APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")


def _create_app_source() -> str:
    src = open(_APP, encoding="utf-8").read()
    return src[src.index("def create_app"):]


class TestTheThresholdIsStatedAndDefensible:
    def test_an_output_over_sixteen_kilobytes_is_summarised(self):
        src = _create_app_source()
        assert "_MAX_POLLED_OUTPUT_BYTES = 16_384" in src

    def test_the_threshold_clears_every_real_result_and_no_bulk_dump(self):
        """Gate 2's 5.9 KB is the largest output that is a result rather than a dump; the three
        that are dumps are 57 KB, 651 KB and 715 KB. Any threshold between them would do — this
        asserts the one chosen sits in that gap, so a later edit cannot quietly swallow a
        result or start sending a dump."""
        src = _create_app_source()
        threshold = int(re.search(r"_MAX_POLLED_OUTPUT_BYTES = ([\d_]+)", src).group(1).replace("_", ""))
        assert 5_889 < threshold < 56_692


class TestNothingIsDroppedSilently:
    def test_an_omitted_output_says_so_and_says_how_big(self):
        src = _create_app_source()
        assert '"_omitted": True' in src
        assert '"_bytes": size' in src

    def test_an_omitted_output_says_how_to_ask_for_it(self):
        src = _create_app_source()
        assert "include_output=true" in src

    def test_a_small_output_is_untouched(self):
        """The threshold exists to stop a megabyte, not to hide the shape of a node's result."""
        src = _create_app_source()
        assert "if size <= _MAX_POLLED_OUTPUT_BYTES:\n                out.append(n)\n                continue" in src

    def test_a_node_that_cannot_be_measured_is_summarised_rather_than_sent(self):
        """An output that will not serialise is the one most likely to be enormous, and the
        poll must not be the place that discovers it."""
        src = _create_app_source()
        block = src[src.index("def _lighten_node_outputs"):src.index("@app.get", src.index("def _lighten_node_outputs"))]
        assert "except (TypeError, ValueError):" in block
        assert "size = _MAX_POLLED_OUTPUT_BYTES + 1" in block


class TestProgressStillTravels:
    def test_logs_are_never_trimmed(self):
        """Every node's logs together came to 15,770 bytes — they are what the panel shows, and
        they were never the problem."""
        src = _create_app_source()
        lighten = src[src.index("def _lighten_node_outputs"):src.index("@app.get", src.index("def _lighten_node_outputs"))]
        assert '"logs"' not in lighten

    def test_only_the_output_key_is_replaced(self):
        src = _create_app_source()
        assert '{**n, "output": {' in src

    def test_the_poll_is_the_route_that_trims_and_the_detail_route_is_not(self):
        """GET /api/migration/{id} exists to return the record; it keeps returning all of it."""
        src = _create_app_source()
        assert "_lighten_node_outputs(migration_nodes, include_output)" in src
        # The detail route's own return must not be trimmed.
        detail = src.index("async def get_migration_detail")
        after_detail = src[detail:detail + 6000]
        assert "udr_column_intelligence=_udr_column_intelligence_from_job(migration_job)" in after_detail
        assert "_lighten_record(" not in after_detail


class TestTheUdrRecordIsAlsoRecord:
    def test_the_three_large_udr_fields_are_summarised_in_the_poll(self):
        src = _create_app_source()
        for field in ("udr_relationship_report", "udr_table_resolution", "udr_column_intelligence"):
            assert f"{field}=_lighten_record(" in src, field

    def test_udr_test_results_stays_whole(self):
        """It is small, and it is the one the run's verdict is read from."""
        src = _create_app_source()
        assert "udr_test_results=_udr_test_results_from_job(migration_job)" in src

    def test_the_summary_names_the_route_that_returns_it(self):
        src = _create_app_source()
        assert "GET /api/migration/{{id}} returns it" in src  # doubled: it is an f-string


class TestBothPollsAreFixed:
    def test_the_schema_mapping_poll_has_the_same_guard(self):
        """It builds its nodes the same way from the same append-only node_logs."""
        src = _create_app_source()
        assert "_lighten_node_outputs(schema_nodes, include_output)" in src

    def test_both_polls_take_the_opt_in(self):
        src = _create_app_source()
        assert src.count("include_output: bool = Query(") == 2
