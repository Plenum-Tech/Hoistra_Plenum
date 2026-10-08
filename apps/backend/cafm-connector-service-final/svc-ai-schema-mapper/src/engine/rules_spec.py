"""The write rules' tables, read from the Python modules that own them.

The engine's writer ports the per-row write rules (write_node, building_link, meter_link,
reference_link); the TABLES those rules use are passed to it from here on every run, so an edit
to one of these constants reaches the engine without a Go change. The procedural rules are
ported, and tests/test_engine_rules_oracle.py fails if the two ever disagree.
"""
from __future__ import annotations


def write_rules() -> dict:
    from ..graph.nodes import building_link as bl
    from ..graph.nodes import meter_link as ml
    from ..graph.nodes import reference_link as rl
    from ..graph.nodes import write_node as wn

    return {
        "natural_keys": {t: [list(g) for g in groups] for t, groups in wn._NATURAL_KEYS.items()},
        "core_parents": {t: list(ps) for t, ps in wn._CORE_PARENTS.items()},
        # A list: REFERENCES' order is the order the writer resolves columns in.
        "references": [
            {"column": col, "hint_keys": list(spec[0]), "table": spec[1], "match_columns": list(spec[2])}
            for col, spec in rl.REFERENCES.items()
        ],
        "building_linked_tables": list(wn._BUILDING_LINKED_TABLES),
        "building_hint_tables": list(wn._BUILDING_HINT_TABLES),
        "building_via_asset_tables": list(wn._BUILDING_VIA_ASSET_TABLES),
        "known_core_tables": sorted(wn._KNOWN_CORE_TABLES),
        "system_supplied_columns": sorted(wn._SYSTEM_SUPPLIED_COLUMNS),
        "building_hint_keys": list(bl._HINT_KEYS),
        "site_tables": sorted(bl._SITE_TABLES),
        "merge_keep": sorted(bl._MERGE_KEEP),
        "meter_hint_keys": list(ml._METER_HINT_KEYS),
        "mpan_keys": list(ml._MPAN_KEYS),
        "mprn_keys": list(ml._MPRN_KEYS),
        "either_keys": list(ml._EITHER_KEYS),
        "section_keys": list(ml._SECTION_KEYS),
        "sub_meter_keys": list(ml._SUB_METER_KEYS),
        "fuel_keys": list(ml._FUEL_KEYS),
        "floor_keys": list(ml._FLOOR_KEYS),
        "gas_words": list(ml._GAS_WORDS),
        "elec_words": list(ml._ELEC_WORDS),
        "write_chunk": int(wn._WRITE_CHUNK),
        "max_consecutive_row_failures": int(wn._MAX_CONSECUTIVE_ROW_FAILURES),
        "widen_scan_cap": int(wn._WIDEN_SCAN_CAP),
    }
