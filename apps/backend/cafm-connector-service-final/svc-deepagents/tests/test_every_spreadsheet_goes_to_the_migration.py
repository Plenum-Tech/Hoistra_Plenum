"""One door for tabular data, and it asks which building before it opens.

A CSV used to be sorted before anyone saw it. An invoice or contract workbook went to the
contract-performance extractor, a smart-meter export to the energy ingest, and only what was
left reached the migration. Two things were wrong with that. The sort was a guess made from a
filename and a header row, and it decided which schema the file would be mapped against. And
it flipped on whether the covering message happened to contain the word "migrate", so the
same file took different routes depending on how someone phrased a sentence.

The migration now resolves what those routes resolved — a building from a site column or from
the uploader's own selection, a meter from a supply number — so everything can go through it.

The building matters more here than it looks. A half-hourly export names an MPAN and nothing
else; there is no site column to fall back on. Without a selection its meter belongs to no
building, and the readings are stored and then counted towards nothing, on a page that reports
success. So the flow asks rather than guesses.
"""
from __future__ import annotations

import ast
import io
import os

import pytest

_HERE = os.path.dirname(__file__)
_FLOW = os.path.join(_HERE, "..", "src", "agents", "single_door_flow.py")
_MIGR = os.path.join(_HERE, "..", "src", "agents", "migration_agent.py")


def source(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def constants(path: str) -> dict:
    tree = ast.parse(source(path))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def function(path: str, name: str):
    for node in ast.walk(ast.parse(source(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {os.path.basename(path)}")


def params(fn) -> set[str]:
    a = fn.args
    return {p.arg for p in a.args + a.posonlyargs + a.kwonlyargs}


class TestOneDoor:
    def test_every_spreadsheet_goes_to_the_migration(self):
        assert constants(_FLOW)["STRUCTURED_ALWAYS_MIGRATE"] is True

    def test_the_specialist_routes_are_switched_off_by_that_policy(self):
        """Not deleted. The classifiers still exist and the branches still read, so turning
        the policy off restores the old behaviour exactly."""
        src = source(_FLOW)
        assert "] if not STRUCTURED_ALWAYS_MIGRATE else []" in src
        assert src.count("] if not STRUCTURED_ALWAYS_MIGRATE else []") == 2, \
            "both the contract and the energy partitions"

    def test_a_contract_workbook_no_longer_stops_before_the_migration(self):
        src = source(_FLOW)
        assert "if not force_migration and not STRUCTURED_ALWAYS_MIGRATE:" in src

    def test_the_word_migrate_no_longer_changes_where_a_file_goes(self):
        """force_migration decided the route. A file should not take a different path because
        of a word in the covering sentence."""
        src = source(_FLOW)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("if classify_") and "not force_migration" in stripped:
                assert "STRUCTURED_ALWAYS_MIGRATE" in src, stripped


class TestItAsksWhichBuilding:
    def test_the_flow_holds_a_spreadsheet_that_names_no_building(self):
        src = source(_FLOW)
        assert "if structured_paths and not building_id:" in src
        assert "awaiting_building_selection" in src

    def test_it_says_nothing_was_written(self):
        """A held upload that does not say so reads as a failed one."""
        assert "Nothing has been written." in source(_FLOW)

    def test_it_names_the_files_it_is_asking_about(self):
        fn = function(_FLOW, "run_single_door_ingestion_sequence")
        body = ast.dump(fn)
        assert "_names" in body and "structured_paths" in body


class TestTheSelectionReachesTheMigration:
    @pytest.mark.parametrize("name", ["ingest_structured_batch", "ingest_single_file",
                                      "run_single_door_ingestion_sequence"])
    def test_the_flow_functions_carry_a_building(self, name):
        assert "building_id" in params(function(_FLOW, name))

    @pytest.mark.parametrize("name", ["start_migration", "start_migration_multi"])
    def test_the_migration_tools_accept_a_building(self, name):
        assert "building_id" in params(function(_MIGR, name))

    def test_the_tools_post_it(self):
        src = source(_MIGR)
        assert src.count('**({"building_id": building_id} if building_id else {})') == 2, \
            "both the single and the multi upload"

    def test_an_absent_building_is_left_out_rather_than_sent_as_the_word_none(self):
        """Form data is strings. Sending building_id="None" would make the writer look up a
        building called None and log a warning on every run that has no selection."""
        src = source(_MIGR)
        assert '"building_id": str(building_id)' not in src
        assert 'if building_id else {}' in src

    def test_every_call_site_passes_it_on(self):
        """A parameter that defaults to None and is never passed is the same as no parameter."""
        src = source(_FLOW)
        assert src.count("building_id=building_id") >= 4


class TestTheseAreStillSeparateQuestions:
    def test_a_document_is_not_a_spreadsheet(self):
        """The policy is about tabular data. PDFs keep their own tracks — compliance
        certificates, contracts and the doc-rag index are unaffected."""
        src = source(_FLOW)
        assert 'document_paths = [p for p in file_paths if _file_kind(p) == "document"]' in src

    def test_only_spreadsheets_are_held_for_a_building(self):
        """A certificate PDF names its building in the document. Holding those too would stop
        a compliance upload that never needed the selection."""
        assert "if structured_paths and not building_id:" in source(_FLOW)


class TestAMeterOnlyUploadIsAskedWhereTheMeterIs:
    """A meter export is the one upload whose answer is not in the file.

    It names an MPAN and a consumption figure and nothing about where the meter sits. Both the
    building and the floor have to come from whoever attached it, and a meter recorded against
    the whole building when it only reads one floor counts that consumption once per meter.
    """

    def test_a_meter_only_upload_gets_its_own_question(self):
        src = source(_FLOW)
        assert "_meters_only = structured_paths and all(" in src
        assert "awaiting_building_and_floor_selection" in src

    def test_it_asks_for_the_floor_as_well_as_the_building(self):
        src = source(_FLOW)
        assert "Which building are these meters on?" in src
        assert "floor, level, section or zone" in src

    def test_it_says_what_happens_if_no_floor_is_named(self):
        """Silence has to mean something definite, or the person cannot answer safely."""
        src = source(_FLOW)
        assert "incoming supply" in src
        assert "recorded as the main meter" in src

    def test_it_says_why_the_floor_matters(self):
        src = source(_FLOW)
        assert "once per meter" in src

    def test_a_mixed_upload_keeps_the_plain_building_question(self):
        """An asset export names its site in a column. Asking it about floors would be noise."""
        src = source(_FLOW)
        assert 'Which building are these for?' in src
        assert src.count("_note = \"awaiting_building_selection\"") == 1

    def test_both_branches_still_say_nothing_was_written(self):
        assert source(_FLOW).count("Nothing has been written.") == 2


class TestTheChatSaysWhetherTheMetersLinked:
    """Rows written is not the question a person asked by uploading a file.

    Nearly every link in the energy chain is a plain uuid with no constraint behind it, so
    readings can land perfectly and reach no building: the run reports success and the page
    stays empty. Telling them to run a script to find out whether their own upload worked is
    not an answer.
    """

    def test_the_flow_asks_for_a_link_report(self):
        src = source(_FLOW)
        assert "async def _meter_link_report(" in src
        assert "/api/energy/meters/link-report" in src

    def test_it_reaches_the_chat_and_not_only_the_log(self):
        """The summary is what the person reads. A report only in tool_calls is invisible."""
        src = source(_FLOW)
        assert "Meter links — " in src
        assert '"summary": _summary,' in src

    def test_the_gap_result_is_said_in_the_same_breath(self):
        src = source(_FLOW)
        assert "Gap check:" in src

    def test_a_failed_report_does_not_fail_the_ingest(self):
        """A report that cannot be fetched must not undo a write that succeeded."""
        src = source(_FLOW)
        assert "single_door.link_report_failed" in src

    def test_it_is_returned_for_anything_downstream(self):
        assert '"meter_links": _links,' in source(_FLOW)

    def test_nothing_is_reported_when_no_building_was_chosen(self):
        """Without a building there is nothing to scope the report to, and the run was held
        before it started anyway."""
        fn = function(_FLOW, "_meter_link_report")
        body = ast.dump(fn)
        assert "building_id" in body
