"""Install fictional permission fixtures in the isolated homework database only."""
import json
import secrets
import subprocess
from pathlib import Path

# Resolve the checkout dynamically so setup works on any approved host or branch.
ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'homework/module-1'
git_root = Path(subprocess.check_output(
    ['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, text=True).strip()).resolve()
if git_root != ROOT:
    raise RuntimeError('Homework setup must run from its containing Git checkout')


def sql(statement):
    result = subprocess.run(['docker', 'exec', '-i', 'hoistra-hw1-postgres-1',
        'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'hoistra_hw1', '-d', 'hoistra_hw1'],
        input=statement, text=True, capture_output=True)
    if result.returncode:
        # SQL input can include the generated password; never echo it on failure.
        raise RuntimeError('Isolated Point 2 database setup failed; inspect privately')


env = MODULE / 'runtime/.env'
lines = env.read_text().splitlines()
existing = next((line for line in lines if line.startswith('HW1_READ_DB_URL=')), None)
if existing:
    password = existing.split(':', 2)[2].split('@', 1)[0]
else:
    password = secrets.token_hex(32)
    with env.open('a') as output:
        output.write('\nHW1_READ_DB_URL=postgresql+asyncpg://hw1_reader:' + password + '@postgres:5432/hoistra_hw1\n')
    env.chmod(0o600)

sql("""
CREATE SCHEMA IF NOT EXISTS hw1_access AUTHORIZATION hoistra_hw1;
CREATE TABLE IF NOT EXISTS hw1_access.read_records (
    record_id text PRIMARY KEY,
    organization_id text NOT NULL,
    building_id text NOT NULL,
    content_building_ids text[],
    scope_verified boolean NOT NULL DEFAULT false,
    contains_sensitive_data boolean NOT NULL DEFAULT true,
    kind text NOT NULL,
    owner_user_id text,
    category text,
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS hw1_access.authorization_audit (
    event_id text PRIMARY KEY,
    request_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    user_id text,
    organization_id text,
    role text,
    tool text NOT NULL,
    target jsonb NOT NULL,
    outcome text NOT NULL,
    entry_path text NOT NULL,
    event_type text NOT NULL,
    policy_version text NOT NULL
);
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hw1_reader') THEN
    CREATE ROLE hw1_reader LOGIN NOINHERIT;
  END IF;
END $$;
""")
sql("ALTER ROLE hw1_reader PASSWORD '" + password + "';\n" + """
GRANT CONNECT ON DATABASE hoistra_hw1 TO hw1_reader;
GRANT USAGE ON SCHEMA hw1_access TO hw1_reader;
GRANT SELECT ON hw1_access.read_records TO hw1_reader;
GRANT INSERT ON hw1_access.authorization_audit TO hw1_reader;
""")

rows = []


def add(record_id, kind, building='hw1-building-a1', scope=None, verified=True,
        sensitive=False, owner=None, category=None, **payload):
    org = 'hw1-org-b' if building == 'hw1-building-b1' else 'hw1-org-a'
    payload['source_reference'] = 'fixture:' + record_id
    if category is not None:
        payload['category'] = category
    rows.append({'record_id': record_id, 'organization_id': org, 'building_id': building,
                 'content_building_ids': scope if scope is not None else [building],
                 'scope_verified': verified, 'contains_sensitive_data': sensitive,
                 'kind': kind, 'owner_user_id': owner, 'category': category, 'payload': payload})


for index in range(25):
    add(f'hw1-wo-a1-{index:02d}', 'work_order_status', work_order_id=f'fixture-wo-{index}',
        status='open', asset_id='fixture-asset-a1', due_at='2026-11-01',
        internal_private_note='PROJECTION_CANARY_NOT_FOR_SUMMARY')
add('hw1-wo-a2', 'work_order_status', 'hw1-building-a2', status='open')
add('hw1-wo-b1', 'work_order_status', 'hw1-building-b1', status='open')
for building in ('hw1-building-a1', 'hw1-building-a2', 'hw1-building-b1'):
    suffix = building.rsplit('-', 1)[1]
    add('hw1-cert-' + suffix, 'certificate_status', building, certificate_id='fixture-cert-' + suffix,
        subject_type='building', subject_id=building, status='valid', expiry_date='2026-12-01')
    add('hw1-certificate-document-' + suffix, 'certificate_documents', building,
        title='Fictional building certificate', content='Fictional permission-test certificate.')
add('hw1-asset-condition', 'asset_conditions', asset_id='fixture-asset-a1', condition='serviceable')
add('hw1-asset-model', 'asset_information', asset_id='fixture-asset-a1', model='Fictional pump model', details={'manufacturer': 'Test manufacturer'})
add('hw1-energy-record', 'building_records', record_type='energy_meter_readings', title='Fictional meter record', details={'kwh': 100})
add('hw1-warranty', 'building_documents', document_type='warranty', title='Fictional warranty', content='Building equipment warranty fixture.')
add('hw1-new-document-category', 'building_documents', document_type='future_building_document_type', title='Additional building document', content='Not limited to the original four categories.')
add('hw1-mixed-document', 'building_documents', scope=['hw1-building-a1', 'hw1-building-a2'], title='Mixed building document', content='A1 and A2 fixture evidence.')
add('hw1-unknown-scope', 'building_documents', scope=[], verified=False, content='UNKNOWN_SCOPE_CANARY')
add('hw1-sensitive-document', 'building_documents', sensitive=True, content='SENSITIVE_CONTENT_CANARY')
add('hw1-inspection', 'inspection_reports', title='Fictional inspection', content='Inspection fixture.')
add('hw1-invoice', 'invoice_documents', title='Fictional invoice', content='Invoice fixture.')
add('hw1-contract', 'contract_documents', title='Fictional contract', content='Contract fixture.')
add('hw1-compliance-evidence', 'compliance_evidence', title='Certificate evidence', content='Compliance-only fixture.')
for kind in ('energy_findings', 'invoice_findings', 'vendor_performance', 'work_order_sla'):
    add('hw1-' + kind, kind, finding='Fictional finding for permission testing.')
add('hw1-usage-general', 'usage_reports', owner='hw1-general', category='general', summary='Own fictional usage.')
add('hw1-usage-other', 'usage_reports', owner='hw1-property', category='general', summary='Another user fictional usage.')
add('hw1-usage-compliance', 'usage_reports', owner='hw1-compliance', category='compliance', summary='Fictional compliance usage.')

# json_populate_recordset keeps fixture values separate from SQL syntax.
encoded = json.dumps(rows)
assert '$fixtures$' not in encoded
sql("INSERT INTO hw1_access.read_records SELECT * FROM json_populate_recordset(NULL::hw1_access.read_records, $fixtures$" +
    encoded + "$fixtures$) ON CONFLICT (record_id) DO UPDATE SET payload = EXCLUDED.payload;")

compose = MODULE / 'docker-compose.hw1.yml'
content = compose.read_text()
anchor = '      HW1_TEST_IDENTITY_ENABLED: "true"'
assert anchor in content
if '      HW1_READ_DB_URL:' not in content:
    content = content.replace(anchor, anchor + '\n      HW1_READ_DB_URL: ${HW1_READ_DB_URL:?Run setup-point-2.py first}')
    compose.write_text(content)
print(f'Isolated permission schema and {len(rows)} fictional fixture rows prepared; no production/domain tables changed.')
