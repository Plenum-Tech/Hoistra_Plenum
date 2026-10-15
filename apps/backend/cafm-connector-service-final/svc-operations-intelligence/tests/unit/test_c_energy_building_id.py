"""The energy tables key on the building, and used to call that column site_id.

Seven of them carried ``site_id`` holding a ``buildings.building_id``. Checked on both
databases before the rename: every non-null value joined to plenum_cafm.buildings and not one
joined to plenum_cafm.sites. The name pointed at the wrong level of the hierarchy and invited
exactly the wrong join, while the newer energy tables already said ``building_id``.

These pin the rename where it would otherwise rot: the models, the migration's idempotence,
the columns that must NOT be renamed because there the value really is a site, and the
deprecated response alias the shell still reads.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.models.energy import (
    BuildingEnergyProfile,
    EnergyAnomaly,
    EnergyMeter,
    EnergyMonthlyReport,
    EnergyRecommendation,
    EuiSnapshot,
    SiteOccupancyLog,
)

RENAMED = (EnergyMeter, EnergyAnomaly, EuiSnapshot, BuildingEnergyProfile,
           EnergyRecommendation, EnergyMonthlyReport, SiteOccupancyLog)
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "energy_building_id_rename.sql"


def test_every_energy_model_names_the_building_column_building_id():
    for model in RENAMED:
        cols = set(model.__table__.columns.keys())
        assert "building_id" in cols, f"{model.__name__} lost its building key"
        assert "site_id" not in cols, (
            f"{model.__name__} still has site_id; the value there is a building id"
        )


def test_the_migration_covers_exactly_the_models_that_were_renamed():
    sql = MIGRATION.read_text(encoding="utf-8")
    listed = set(re.findall(r"^\s*'(\w+)',?$", sql, re.M))
    assert listed == {m.__tablename__ for m in RENAMED}


def test_the_migration_is_idempotent_and_cannot_half_apply():
    """It runs at startup on every boot, so a second run must be a no-op, and a table that
    already has building_id must be left alone rather than renamed over."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "column_name = 'site_id'" in sql
    assert "NOT EXISTS" in sql and "column_name = 'building_id'" in sql
    assert "RENAME COLUMN site_id TO building_id" in sql


def test_the_migration_does_not_touch_columns_that_really_hold_a_site():
    """buildings.site_id and sites.site_id are correct names for real site references.
    Renaming those would break the building-to-site link the whole hierarchy rests on."""
    sql = MIGRATION.read_text(encoding="utf-8")
    listed = set(re.findall(r"^\s*'(\w+)',?$", sql, re.M))
    for table in ("buildings", "sites", "assets", "work_orders", "compliance_certificates"):
        assert table not in listed, f"{table}.site_id is a real site reference"


def test_the_anomaly_row_still_answers_to_the_old_key_for_now():
    """The shell joins anomalies to buildings on site_id. The response carries building_id
    and keeps site_id as a duplicate so the rename does not break the page mid-deploy."""
    import inspect

    from src.engines.energy import anomalies

    src = inspect.getsource(anomalies)
    assert '"building_id": str(r.building_id)' in src
    assert '"site_id": str(r.building_id)' in src, "the deprecated alias is what keeps the shell working"


def test_a_meter_upsert_still_accepts_the_old_field_name():
    """A client that has not been updated sends site_id and means the same building."""
    import inspect

    from src.api.schemas.energy import MeterUpsertRequest
    from src.engines.energy import meters

    fields = MeterUpsertRequest.model_fields
    assert "building_id" in fields and "site_id" in fields
    src = inspect.getsource(meters.upsert_meter)
    assert 'data.get("site_id")' in src and "row.building_id" in src
