"""Tests for the generated plenum_cafm schema + schema-grounded aliases
(matchers/plenum_cafm_schema.py). Pure — no DB. Run: python tests/test_plenum_cafm_schema.py"""
import importlib
import os
import re
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

S = importlib.import_module("matchers.plenum_cafm_schema")


def _n(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── authoritative schema present ─────────────────────────────────────────────────
check("schema has the full table set", len(S.TABLES) >= 140)
check("schema has ~1400 columns", sum(len(c) for c in S.TABLES.values()) >= 1400)
for t in ("assets", "work_orders", "sites", "vendors", "technicians", "spare_parts"):
    check(f"core table present: {t}", t in S.TABLES)
check("assets has real columns id/asset_code/barcode",
      {"id", "asset_code", "barcode"} <= set(S.TABLES["assets"]))

# ── table aliases resolve to a real table, never collide ─────────────────────────
check("table aliases populated", len(S.TABLE_ALIASES) >= 100)
check("every table alias points to a real table",
      all(v in S.TABLES for v in S.TABLE_ALIASES.values()))


def resolve(table, src):
    """Mirror deterministic Strategy 0b: alias -> real column on the routed table."""
    alias = (S.COLUMN_ALIASES_BY_TABLE.get(table) or {}).get(_n(src))
    real = {_n(c): c for c in S.TABLES[table]}
    return real.get(_n(alias)) if alias else None


# ── schema-grounded column aliases resolve to real columns ───────────────────────
_CASES = [
    ("assets", "serial_no", "serial_number"),
    ("assets", "mfg", "manufacturer"),
    ("assets", "model_no", "model_number"),
    ("assets", "loc_code", "location_code"),
    ("work_orders", "task_desc", "task_description"),
    ("work_orders", "desc", "description"),
    ("vendors", "addr", "address"),
    ("vendors", "ph", "phone"),
    ("spare_parts", "stock_qty", "stock_quantity"),
    ("spare_parts", "max_qty", "max_quantity"),
]
for _t, _src, _exp in _CASES:
    check(f"{_t}.{_src} -> {_exp}", resolve(_t, _src) == _exp)

# ── safety invariants: aliases are unambiguous + real ────────────────────────────
check("every column alias resolves to a real column",
      all(_n(v) in {_n(c) for c in S.TABLES[t]}
          for t, m in S.COLUMN_ALIASES_BY_TABLE.items() for v in m.values()))
check("no column alias shadows a real column name",
      all(a not in {_n(c) for c in S.TABLES[t]}
          for t, m in S.COLUMN_ALIASES_BY_TABLE.items() for a in m))

print("\nALL TESTS PASSED")
