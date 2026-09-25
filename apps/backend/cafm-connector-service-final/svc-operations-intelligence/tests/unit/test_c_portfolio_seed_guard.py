"""The demo portfolio seed must not resurrect a building the customer already has.

Three of the nine demo buildings are also on record under a real customer's own site key,
with that customer's certificates filed against them. The duplicates were merged into the
owned rows and deleted; the seed put all three back on the next service start, because it
was guarded on site_id alone and a different key is not a conflict.
"""
from pathlib import Path

SEED = (Path(__file__).resolve().parents[2] / "seeds" / "portfolio_buildings.sql")


def _sql() -> str:
    return SEED.read_text(encoding="utf-8")


def _statement() -> str:
    """The SQL with its comments stripped — the header explains both guards by name, so a
    test that searches the whole file finds the prose rather than the clause."""
    return "\n".join(
        line for line in _sql().splitlines() if not line.lstrip().startswith("--")
    ).lower()


class TestThePortfolioSeedDoesNotResurrectMergedRows:
    def test_the_seed_exists_and_still_carries_the_nine(self):
        sql = _sql()
        for code in ("B-001", "B-002", "B-003", "B-004", "B-005",
                     "B-006", "B-007", "B-008", "B-009"):
            assert f"'{code}'" in sql

    def test_it_skips_a_name_an_organisation_already_owns(self):
        sql = _statement()
        assert "where not exists" in sql, "the seed has no owned-name guard"
        assert "organization_id is not null" in sql
        assert "site_name" in sql.split("where not exists", 1)[1][:400]

    def test_the_site_id_conflict_guard_is_still_there(self):
        """The two guards answer different questions and both are needed: one keeps operator
        edits to a seeded row, the other keeps a merged duplicate from returning."""
        assert "on conflict (site_id) do nothing" in _statement()

    def test_the_guard_is_part_of_the_insert_not_a_trailing_comment(self):
        sql = _statement()
        guard = sql.index("where not exists")
        conflict = sql.index("on conflict")
        assert guard < conflict, "the guard must filter the rows before the conflict clause"
        assert "select v.*" in sql, "the values list must be selected from, or it cannot be filtered"
