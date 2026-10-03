"""Per-table row counts and the history behind them.

The export panel used to show a row count computed from a seed building's floors and area,
and four "builds" that were fixed percentages of it — so every table in the portfolio lost
exactly 2.4% overnight, including the empty ones. These are counted, and the historical
figures are read from each row's created_at rather than a snapshot nobody stores.
"""
from __future__ import annotations

import asyncio

import pytest

from src.engines.energy import building_tree as BT


class _Row(dict):
    """A mappings() row."""


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """Answers the two statements graph_tables issues, and can fail one table."""

    def __init__(self, columns, counts, *, fail: set[str] | None = None):
        self.columns = columns          # table -> column count
        self.counts = counts            # table -> [v0, v1, ...]
        self.fail = fail or set()
        self.statements: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if "information_schema.columns" in sql:
            return _Result([_Row(table_name=t, n=n) for t, n in self.columns.items()])
        table = sql.rsplit("plenum_cafm.", 1)[1].strip()
        if table in self.fail:
            raise RuntimeError("permission denied for table " + table)
        vals = self.counts[table]
        return _Result([_Row({f"v{i}": v for i, v in enumerate(vals)})])


def _shape(tables: dict[str, set[str]]):
    async def fake(session, **kw):
        return {t: {"exists": True, "key": "id", "columns": cols} for t, cols in tables.items()}
    return fake


def _run(session, tables, monkeypatch):
    monkeypatch.setattr(BT, "graph_shape", _shape(tables))
    return asyncio.run(BT.graph_tables(session))


def _by(out, table):
    return next(t for t in out["tables"] if t["table"] == table)


# ── the counts are counts ─────────────────────────────────────────────────────


def test_each_table_reports_its_own_history_not_a_shared_percentage(monkeypatch):
    """The tell of the old panel: every table moved by the same proportion. A table nobody
    wrote to last week must show the same figure at every cutoff."""
    s = _Session(
        {"buildings": 13, "documents": 8},
        {"buildings": [6, 6, 6, 6], "documents": [40, 12, 9, 0]},
    )
    out = _run(s, {"buildings": {"created_at"}, "documents": {"created_at"}}, monkeypatch)
    assert out["ok"] is True

    quiet = _by(out, "buildings")
    assert [v["rows"] for v in quiet["versions"]] == [6, 6, 6, 6]
    assert [v["delta"] for v in quiet["versions"]] == [None, 0, 0, 0], "unchanged is zero"

    busy = _by(out, "documents")
    assert [v["rows"] for v in busy["versions"]] == [40, 12, 9, 0]
    # Each delta is against the row above it, computed once here rather than re-derived by
    # the caller from percentages and coming out different.
    assert [v["delta"] for v in busy["versions"]] == [None, -28, -3, -9]


def test_the_live_count_is_the_first_figure_and_the_row_count(monkeypatch):
    s = _Session({"assets": 48}, {"assets": [9, 4, 4, 0]})
    out = _run(s, {"assets": {"created_at"}}, monkeypatch)
    t = _by(out, "assets")
    assert t["rows"] == 9 and t["versions"][0]["rows"] == 9
    assert t["versions"][0]["current"] is True
    assert t["columns"] == 48


def test_an_empty_table_is_zero_at_every_cutoff_rather_than_a_shrinking_number(monkeypatch):
    s = _Session({"contracts": 14}, {"contracts": [0, 0, 0, 0]})
    out = _run(s, {"contracts": {"created_at"}}, monkeypatch)
    assert [v["rows"] for v in _by(out, "contracts")["versions"]] == [0, 0, 0, 0]


# ── a table with no created_at has no history, and says so ────────────────────


def test_a_table_without_created_at_offers_its_live_count_alone(monkeypatch):
    """Not a zero history. There is nothing to reconstruct from, and inventing one would
    put a made-up figure beside the counted ones with no way to tell them apart."""
    s = _Session({"legacy": 5}, {"legacy": [11]})
    out = _run(s, {"legacy": {"name"}}, monkeypatch)
    t = _by(out, "legacy")
    assert t["rows"] == 11
    assert t["history_available"] is False
    assert len(t["versions"]) == 1 and t["versions"][0]["current"] is True
    assert "created_at" in t["why"]
    assert out["no_history"] == ["legacy"]
    # And the statement it issued asked for one figure, not four filtered ones.
    counted = [q for q in s.statements if "plenum_cafm.legacy" in q]
    assert len(counted) == 1 and "FILTER" not in counted[0]


# ── a table that cannot be read loses its own row, not the panel ──────────────


def test_one_unreadable_table_does_not_lose_the_others(monkeypatch):
    s = _Session(
        {"buildings": 13, "meters": 7},
        {"buildings": [6, 6, 0, 0], "meters": [0, 0, 0, 0]},
        fail={"meters"},
    )
    out = _run(s, {"buildings": {"created_at"}, "meters": {"created_at"}}, monkeypatch)
    assert out["ok"] is True
    assert _by(out, "buildings")["rows"] == 6
    broken = _by(out, "meters")
    assert broken["rows"] is None, "unknown, not zero — the panel must not print 0 here"
    assert "Could not count" in broken["error"]


def test_a_database_with_no_graph_tables_says_so(monkeypatch):
    out = _run(_Session({}, {}), {}, monkeypatch)
    assert out["ok"] is False and "No graph tables" in out["error"]


# ── the caveat travels with the payload ───────────────────────────────────────


def test_the_payload_states_that_a_past_count_is_a_floor(monkeypatch):
    """A row created last month and deleted yesterday is missing from every figure here.
    Someone reading "9 rows last Tuesday" as the table's size that day would be wrong, and
    nothing on the screen would tell them."""
    s = _Session({"buildings": 13}, {"buildings": [6, 6, 6, 6]})
    out = _run(s, {"buildings": {"created_at"}}, monkeypatch)
    basis = out["history_basis"]
    assert "created_at" in basis and "not from a stored snapshot" in basis
    assert "deleted" in basis and "floor" in basis


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
