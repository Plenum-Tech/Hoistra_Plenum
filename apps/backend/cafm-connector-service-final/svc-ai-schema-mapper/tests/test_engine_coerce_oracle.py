"""The engine coerces every value exactly as write_node._coerce_value_for_db_type does.

Python is the oracle: each (value, destination type) pair is run through the real function and
through `hoist-engine coerce-eval`, and the two answers must be identical. A later change to the
Python rule makes this test fail until the engine follows it."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.engine.client import engine_available, run_engine
from src.graph.nodes.write_node import _COERCE_TYPE_MISMATCH, _coerce_value_for_db_type

VALUES = [None, 0, True, False, "", "  ", "5", " 5 ", "-12", "1_000", "٣", "102.0", "1.9", "-1.9", "1e3", "inf",
          "nan", "Quarterly", "1,234", "12.50", "-0.0", "true", "T", "yes", "N", "2", "No ", "2025-12-31",
          "2025-12-31T10:30:00", "2025-12-31 10:30", "2025-12-31T10:30:00Z", "2025-12-31T10:30:00+05:30",
          "2025-12-31T10:30:00.123456789", "2025-12-31T10:30:00,5", "20251231", "20251231T103000",
          "2025-W01-1", "2025W011", "2025-W53", "2026-W53-1", "2025-02-30", "2025-13-01", "0000-01-01",
          "31/12/2025", "2026-07-10 00:00:00", "2025-12-31T24:00:00", "2025-12-31x10:30", "2025-12-31T10",
          "2025-12-31T1030", "2025-12-31T10:30:00-00:00", "2025-12-31T10:30:00+24:00", "2025-12-31T10:30:00+05:99",
          "2025-12-31T", "2025-12-31T10:30:00.", "2025-1-5", "+2025-12-31", "2025-12-31Z",
          '{"a": 1}', "[1, 2]", "hello", "0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e",
          "{0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E}", "0e9a1c1e6b6f4f0a9d0e8f3b2a1c4d5e",
          "urn:uuid:0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e", "0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5", "A-001",
          "99999999999999999999", "1E+400", "sNaN", "Infinity", "٣.٥", "١٢٣٤-٠١-٠١"]
TYPES = ["text", "character varying", "character", "citext", "name", "integer", "bigint", "smallint",
         "numeric", "double precision", "real", "boolean", "timestamp without time zone",
         "timestamp with time zone", "date", "json", "jsonb", "uuid", "interval", "time without time zone",
         "USER-DEFINED", "ARRAY", "bytea", "point"]


def _kind_of_branch(db_type: str) -> str:
    t = (db_type or "").lower()
    if "char" in t or "text" in t or t in {"name", "citext"}:
        return "text"
    if t.startswith("interval"):
        return "interval"
    if "int" in t or "serial" in t:
        return "int"
    if "numeric" in t or "decimal" in t or "double" in t or "real" in t:
        return "decimal"
    if t == "boolean":
        return "bool"
    if "timestamp" in t:
        return "timestamp"
    if t == "date":
        return "date"
    if "json" in t:
        return "json"
    if t == "uuid":
        return "uuid"
    return "raw"


def _encode(result, db_type):
    if result is None:
        return {"k": "null"}
    if result is _COERCE_TYPE_MISMATCH:
        return {"k": "mismatch"}
    if isinstance(result, bool):
        return {"k": "bool", "s": "true" if result else "false"}
    if isinstance(result, int):
        return {"k": "int", "s": str(result)}
    if isinstance(result, Decimal):
        return {"k": "decimal", "s": str(result)}
    if isinstance(result, datetime):
        return {"k": "timestamptz" if result.tzinfo else "timestamp", "s": result.isoformat()}
    if isinstance(result, date):
        return {"k": "date", "s": result.isoformat()}
    if isinstance(result, str):
        return {"k": _kind_of_branch(db_type), "s": result}
    return {"k": "other", "s": repr(result)}


def _cell(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return {"b": v}
    return {"i": v} if isinstance(v, int) else {"s": v}


@pytest.mark.skipif(not engine_available(), reason="hoist-engine binary not mounted")
async def test_the_engine_coerces_exactly_like_the_python_writer(tmp_path):
    cases = [(v, t) for v in VALUES for t in TYPES]
    want = [_encode(_coerce_value_for_db_type(v, t), t) for v, t in cases]
    out = await run_engine("coerce-eval", {"cases": [{"value": _cell(v), "db_type": t} for v, t in cases]},
                           workdir=tmp_path)
    got = out["results"]
    assert len(got) == len(cases) == len(VALUES) * len(TYPES)
    kinds = {w["k"] for w in want}
    assert {"null", "mismatch", "text", "int", "decimal", "bool", "timestamp", "timestamptz", "date",
            "json", "uuid", "interval", "raw"} <= kinds, kinds   # the corpus reaches every branch
    diffs = [f"{v!r} as {t}: python {w} engine {g}" for (v, t), w, g in zip(cases, want, got) if w != g]
    assert not diffs, f"{len(diffs)} of {len(cases)} differ:\n" + "\n".join(diffs[:25])
