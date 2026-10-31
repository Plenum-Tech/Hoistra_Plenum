"""Add fictional Contract Performance sources to the isolated Homework DB only."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0, str(SERVICE))
from src.services.contract_hw1.fixtures import dataset


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    batch = dataset()
    statements = ["BEGIN;"]
    for table, rows in batch.items():
        for row in rows:
            columns = [sql_literal(row["id"]), sql_literal(row["kind"]),
                       sql_literal(row["org"]),
                       "ARRAY[" + ",".join(sql_literal(item) for item in row["buildings"]) + "]::text[]",
                       sql_literal(json.dumps(row["data"], sort_keys=True)) + "::jsonb"]
            statements.append(
                f"INSERT INTO hw1_compliance.{table} (id,kind,org,buildings,data) "
                f"VALUES ({','.join(columns)}) ON CONFLICT (id) DO NOTHING;")
    # Generic Homework records remain convenient adapters, but their imported source mirrors
    # and produced scorecards must not be editable through the app role's record-table grant.
    statements.extend([
        """CREATE OR REPLACE FUNCTION hw1_compliance.reject_immutable_contract_record_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.kind IN ('contract','work_order_report','invoice_verification','ppm_report',
                          'contract_accreditation','contract_block_period','contract_scorecard',
                          'energy_meter','energy_reading','energy_profile','occupancy_log','energy_asset_profile') THEN
            RAISE EXCEPTION 'immutable Contract evidence or scorecard';
          END IF;
          RETURN NEW;
        END $$;""",
        """DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='contract_evidence_immutable') THEN
            CREATE TRIGGER contract_evidence_immutable
            BEFORE UPDATE ON hw1_compliance.records
            FOR EACH ROW EXECUTE FUNCTION hw1_compliance.reject_immutable_contract_record_update();
          END IF;
        END $$;""",
        "COMMIT;",
    ])
    result = subprocess.run(
        ["docker", "exec", "-i", "hoistra-hw1-postgres-1", "psql", "-X",
         "-v", "ON_ERROR_STOP=1", "-U", "hoistra_hw1", "-d", "hoistra_hw1"],
        input="\n".join(statements), text=True, capture_output=True, check=False)
    if result.returncode:
        # Never include SQL, connection configuration, or provider credentials in diagnostics.
        raise RuntimeError("Isolated Contract fixtures were not installed; no success claimed")
    print(json.dumps({"profile": "fictional Contract Performance", "source_count": len(batch["sources"]),
                      "record_count": len(batch["records"]), "database": "hoistra_hw1",
                      "production_data": False, "existing_rows_overwritten": False}))


if __name__ == "__main__":
    main()
