"""Add only missing fictional trusted portfolio mappings; never overwrite assignments/history."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents"))

def literal(value):return "'"+str(value).replace("'","''")+"'"

def main():
    path=ROOT/"homework/module-1/runtime/test-identities.json"
    if path.stat().st_mode & 0o077: raise SystemExit("Private identity file permissions are too broad")
    identities=json.loads(path.read_text())
    rows=[]
    for user in identities["users"]:
        if user["role"]!="portfolio_manager": continue
        org=user["organization_id"];portfolio=user.get("portfolio_id") or org+"-default-portfolio"
        if any(identities["buildings"].get(b)!=org for b in user["building_ids"]):
            raise SystemExit("Trusted portfolio fixture has inconsistent organization scope")
        rows.append({"id":portfolio,"kind":"portfolio","org":org,"buildings":user["building_ids"],
            "data":{"portfolio_id":portfolio,"version":1,"fixture_version":"ingestion-fixtures-v1",
                    "origin":"trusted fictional identity assignment"}})
    statements=["BEGIN;"]
    for row in rows:
        buildings="ARRAY["+",".join(literal(b) for b in row["buildings"])+"]::text[]"
        statements.append("INSERT INTO hw1_compliance.records (id,kind,org,buildings,data) VALUES ("+
            ",".join([literal(row["id"]),literal(row["kind"]),literal(row["org"]),buildings,
                literal(json.dumps(row["data"]))+"::jsonb"])+") ON CONFLICT (id) DO NOTHING;")
    statements.append("COMMIT;")
    result=subprocess.run(["docker","exec","-i","hoistra-hw1-postgres-1","psql","-X","-v","ON_ERROR_STOP=1",
        "-U","hoistra_hw1","-d","hoistra_hw1"],input="\n".join(statements),text=True,capture_output=True)
    if result.returncode:raise SystemExit("Fictional portfolio fixture provisioning failed")
    print(json.dumps({"profile":"fictional portfolio mapping","records_considered":len(rows),"existing_rows_overwritten":False}))

if __name__=="__main__":main()
