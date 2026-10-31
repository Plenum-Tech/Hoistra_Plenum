"""Install only fictional Energy rows in the isolated Homework database."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0, str(SERVICE))
from src.services.energy_hw1.fixtures import dataset


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    batch = dataset()
    statements = ["BEGIN;"]
    for table, rows in batch.items():
        for row in rows:
            values = [literal(row["id"]), literal(row["kind"]), literal(row["org"]),
                      "ARRAY[" + ",".join(literal(x) for x in row["buildings"]) + "]::text[]",
                      literal(json.dumps(row["data"], sort_keys=True)) + "::jsonb"]
            statements.append(f"INSERT INTO hw1_compliance.{table} (id,kind,org,buildings,data) VALUES ({','.join(values)}) ON CONFLICT (id) DO NOTHING;")
    statements.extend([
        """CREATE OR REPLACE FUNCTION hw1_compliance.reject_immutable_contract_record_update()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF OLD.kind IN ('contract','work_order_report','invoice_verification','ppm_report',
        'contract_accreditation','contract_block_period','contract_scorecard','energy_meter',
        'energy_reading','energy_profile','occupancy_log','energy_asset_profile') THEN RAISE EXCEPTION 'immutable imported evidence'; END IF;
        RETURN NEW; END $$;""",
        "COMMIT;",
    ])
    run = subprocess.run(["docker", "exec", "-i", "hoistra-hw1-postgres-1", "psql", "-X",
        "-v", "ON_ERROR_STOP=1", "-U", "hoistra_hw1", "-d", "hoistra_hw1"],
        input="\n".join(statements), text=True, capture_output=True)
    if run.returncode:
        raise RuntimeError("Fictional Energy fixture installation failed")
    print(json.dumps({"profile": "fictional Energy", "records": len(batch["records"]),
                      "sources": len(batch["sources"]), "database": "hoistra_hw1"}))


if __name__ == "__main__":
    main()
