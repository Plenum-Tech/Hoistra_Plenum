"""Two writes the platform consoles make that the live schema refused.

POST /api/superadmin/companies answered 500 on the first deployed revision, for two reasons
neither the unit tests nor the engine's own error handling could see:

  * plenum_cafm.organizations.id has NO default. Every other table this feature writes
    (users, invitations, the two ledgers) defaults to gen_random_uuid(); the one it was
    modelled on does not, and INSERT ... RETURNING id violated NOT NULL.
  * plenum_cafm.ops_audit_log.source_feature is CHAR(1). The audit rows were written with
    "platform", which the database rejects as too long rather than truncating.

Both are static facts about the source, so both are pinned statically here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _write_audit_source_features():
    """Every literal passed as source_feature= to write_audit, with its location."""
    out = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "write_audit":
                continue
            for kw in node.keywords:
                if kw.arg == "source_feature" and isinstance(kw.value, ast.Constant):
                    out.append((path.name, node.lineno, kw.value.value))
    return out


def test_every_audit_source_feature_literal_fits_char_1():
    seen = _write_audit_source_features()
    assert seen, "expected write_audit(..., source_feature=<literal>) call sites"
    too_long = [(f, ln, v) for f, ln, v in seen if v is not None and len(str(v)) != 1]
    assert not too_long, too_long


def test_platform_feature_is_one_letter_and_distinct_from_the_product_features():
    from src.shared.approvals import PLATFORM_FEATURE
    assert len(PLATFORM_FEATURE) == 1
    assert PLATFORM_FEATURE not in {"A", "B", "C"}


def test_company_insert_generates_its_own_id():
    src = (SRC / "api" / "routes" / "superadmin.py").read_text(encoding="utf-8")
    # The VALUES list contains gen_random_uuid(), so it is read up to RETURNING rather than
    # to the first closing parenthesis.
    m = re.search(r"INSERT INTO plenum_cafm\.organizations\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)"
                  r"\s*RETURNING", src, re.S)
    assert m, "organizations INSERT not found"
    columns = [c.strip() for c in m.group(1).split(",")]
    values = [v.strip() for v in re.split(r",(?![^(]*\))", m.group(2))]
    assert columns[0] == "id" and values[0] == "gen_random_uuid()", (columns[:2], values[:2])
    assert len(columns) == len(values), (len(columns), len(values))
