"""A key removed at the PK gate must not reappear on the next page.

Reported 30 Oct: the primary key was taken off energy_monthly and edges at the gate, and the
pre-semantic page still listed both with their detected keys. The answer had been recorded
correctly; the page was not asking for it.

Two faults, one visible symptom.

1. ``build_table_resolution`` takes ``pk_override_by_table`` and overlays the person's answer
   onto the rows the page renders. Two of its three callers passed it. The pre-semantic node
   did not, so its report re-detected from the data every time.
2. That report is cached to save a full re-scan on pass two and on every resume. The cache had
   no idea the PK answers could change underneath it, so even once the override was passed the
   stale report would have been handed back.

Removing a key is recorded as an empty list — a real answer meaning "surrogate key, no source
column" — so these also pin that an empty list survives as itself and is not mistaken for
"unanswered" anywhere on the path.
"""
from __future__ import annotations

import inspect
import os
import re

NODE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes",
                    "pre_semantic_review_node.py")
STATE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "state.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestTheOverrideReachesThePage:
    def test_the_pre_semantic_report_is_built_with_the_confirmed_keys(self):
        src = _read(NODE)
        call = re.search(r"build_table_resolution\((.*?)\n            \)", src, re.S)
        assert call, "the build_table_resolution call moved; this test needs re-aiming"
        assert "pk_override_by_table=state.get(\"pk_confirmed_by_table\")" in call.group(1), (
            "the pre-semantic page re-detects primary keys unless the gate's answers are passed in"
        )

    def test_every_caller_of_the_builder_passes_the_override(self):
        """Three callers now. A fourth that forgets is the same bug again."""
        nodes = os.path.dirname(NODE)
        callers = []
        for name in os.listdir(nodes):
            if not name.endswith(".py"):
                continue
            body = _read(os.path.join(nodes, name))
            if "build_table_resolution(" in body or "pk_override_by_table" in body:
                callers.append((name, body))
        assert callers, "no caller found; the builder may have been renamed"
        for name, body in callers:
            if "build_table_resolution(" in body:
                assert "pk_override_by_table" in body, (
                    f"{name} builds the table resolution without the confirmed primary keys")


class TestTheCacheFollowsTheAnswers:
    def test_the_cached_report_is_keyed_to_the_pk_answers(self):
        src = _read(NODE)
        assert "table_resolution_pk_signature" in src
        # the signature must be consulted when deciding to reuse, not only written
        reuse = src[src.index("_tr_cached ="):src.index("_tr_cached =") + 400]
        assert "table_resolution_pk_signature" in reuse, (
            "the cache is reused without checking whether the PK answers moved")

    def test_the_signature_is_stored_whenever_the_report_is(self):
        src = _read(NODE)
        assert 'state["table_resolution_report"] = _table_resolution' in src
        at = src.index('state["table_resolution_report"] = _table_resolution')
        assert "table_resolution_pk_signature" in src[at:at + 200], (
            "a report cached without its signature can never be invalidated")

    def test_the_signature_is_declared_so_it_survives_a_checkpoint(self):
        assert re.search(r"\n\s{4}table_resolution_pk_signature\s*:", _read(STATE)), (
            "an undeclared key is dropped at the next step, which would disable the cache check")


class TestAnEmptyAnswerIsStillAnAnswer:
    def test_removing_a_key_normalises_to_an_empty_list(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.graph.nodes.pk_confirmation import normalize_pk_overrides

        for removal in ("", "_udr_id", "__surrogate__", "(surrogate)", [], None):
            out = normalize_pk_overrides({"energy_monthly": removal})
            assert out["energy_monthly"] == [], f"{removal!r} should read as a surrogate key"

    def test_the_overlay_turns_an_empty_list_into_a_surrogate_row(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.udr.table_resolution import _apply_pk_override

        cards = [{"table": "energy_monthly", "primary_key": ["meter_id", "month"]},
                 {"table": "assets", "primary_key": ["asset_id"]}]
        rows = [{"table": "energy_monthly", "primary_key": ["meter_id", "month"], "kind": "composite"},
                {"table": "assets", "primary_key": ["asset_id"], "kind": "natural"}]
        _apply_pk_override(cards, rows, {"energy_monthly": []})

        assert cards[0]["primary_key"] == ["_udr_id"]
        assert cards[0]["primary_key_kind"] == "surrogate"
        assert cards[0]["primary_key_confirmed"] is True
        assert rows[0]["primary_key"] == ["_udr_id"] and rows[0]["kind"] == "surrogate"
        # a table the person did not touch keeps what was detected
        assert cards[1]["primary_key"] == ["asset_id"] and "primary_key_confirmed" not in cards[1]

    def test_the_overlay_is_not_skipped_when_every_answer_is_a_removal(self):
        """`if not pk_override: return` guards the dict, not its values — both removals apply."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from src.udr.table_resolution import _apply_pk_override

        rows = [{"table": "energy_monthly", "primary_key": ["meter_id", "month"], "kind": "composite"},
                {"table": "edges", "primary_key": ["from_id", "to_id"], "kind": "composite"}]
        _apply_pk_override([], rows, {"energy_monthly": [], "edges": []})
        assert [r["kind"] for r in rows] == ["surrogate", "surrogate"]


def test_the_builder_still_takes_the_argument_this_all_depends_on():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from src.udr.table_resolution import build_table_resolution

    assert "pk_override_by_table" in inspect.signature(build_table_resolution).parameters
