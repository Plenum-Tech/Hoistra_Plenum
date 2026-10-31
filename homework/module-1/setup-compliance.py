"""Provision only the isolated Homework database; preserve existing records/secrets."""
from pathlib import Path
import json,os,secrets,subprocess,sys

ROOT=Path('/root/Hoistra_Plenum')
MODULE=ROOT/'homework/module-1'
SERVICE=ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents'
sys.path.insert(0,str(SERVICE))
from src.services.compliance_hw1.fixtures import dataset


def sql(statement):
    result=subprocess.run(['docker','exec','-i','hoistra-hw1-postgres-1','psql','-X','-v','ON_ERROR_STOP=1',
                           '-U','hoistra_hw1','-d','hoistra_hw1'],input=statement,text=True,capture_output=True)
    if result.returncode:
        # Never emit SQL containing provisioned credentials into assistant/tool logs.
        raise RuntimeError('Isolated Compliance database setup failed; inspect private server diagnostics')
    return result.stdout


def literal(value):
    return "'"+str(value).replace("'","''")+"'"


def main():
    env=MODULE/'runtime/.env'
    if not env.is_file() or env.stat().st_mode & 0o077:
        raise ValueError('Private runtime/.env required')
    content=env.read_text();values=dict(line.split('=',1) for line in content.splitlines() if '=' in line and not line.startswith('#'))
    password=secrets.token_urlsafe(32)
    if 'HW1_COMPLIANCE_DB_URL' in values:
        from urllib.parse import urlparse
        password=urlparse(values['HW1_COMPLIANCE_DB_URL']).password
    sql("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='hw1_compliance_app') THEN CREATE ROLE hw1_compliance_app LOGIN; END IF; END $$;\n"
        +"ALTER ROLE hw1_compliance_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD "+literal(password)+";\n"
        +"CREATE SCHEMA IF NOT EXISTS hw1_compliance AUTHORIZATION hoistra_hw1; GRANT USAGE ON SCHEMA hw1_compliance TO hw1_compliance_app;")
    for table in ['records','sources','proposals','history','outbox','events','sessions']:
        sql(f'''CREATE TABLE IF NOT EXISTS hw1_compliance.{table} (
            id text PRIMARY KEY,kind text NOT NULL,org text NOT NULL,buildings text[] NOT NULL,
            data jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
            ALTER TABLE hw1_compliance.{table} ENABLE ROW LEVEL SECURITY;
            DROP POLICY IF EXISTS own_organization ON hw1_compliance.{table};
            CREATE POLICY own_organization ON hw1_compliance.{table} TO hw1_compliance_app
            USING (org=current_setting('hw1.org',true)) WITH CHECK (org=current_setting('hw1.org',true));
            REVOKE ALL ON hw1_compliance.{table} FROM hw1_compliance_app;
            GRANT SELECT,INSERT ON hw1_compliance.{table} TO hw1_compliance_app;''')
        if table in {'records','proposals','outbox','sessions'}:
            sql(f'GRANT UPDATE ON hw1_compliance.{table} TO hw1_compliance_app;')
    sql('''CREATE TABLE IF NOT EXISTS hw1_compliance.audit (
        id text PRIMARY KEY,org text NOT NULL,actor text NOT NULL,role text NOT NULL,
        operation text NOT NULL,outcome text NOT NULL,target jsonb NOT NULL,request_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now());
        ALTER TABLE hw1_compliance.audit ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS own_organization ON hw1_compliance.audit;
        CREATE POLICY own_organization ON hw1_compliance.audit TO hw1_compliance_app
        USING (org=current_setting('hw1.org',true)) WITH CHECK (org=current_setting('hw1.org',true));
        REVOKE ALL ON hw1_compliance.audit FROM hw1_compliance_app;
        GRANT INSERT ON hw1_compliance.audit TO hw1_compliance_app;''')
    data=dataset()
    statements=[]
    for table,rows in data.items():
        for row in rows:
            fields=[literal(row['id']),literal(row['kind']),literal(row['org']),
                    'ARRAY['+','.join(literal(b) for b in row['buildings'])+']::text[]',literal(json.dumps(row['data']))+'::jsonb']
            statements.append(f"INSERT INTO hw1_compliance.{table} (id,kind,org,buildings,data) VALUES ("+','.join(fields)+') ON CONFLICT (id) DO NOTHING;')
    sql('BEGIN;\n'+'\n'.join(statements)+'\nCOMMIT;')
    url='postgresql+asyncpg://hw1_compliance_app:'+password+'@postgres:5432/hoistra_hw1'
    if 'HW1_COMPLIANCE_DB_URL' not in values:
        env.write_text(content.rstrip()+'\nHW1_COMPLIANCE_DB_URL='+url+'\nHW1_COMPLIANCE_ENABLED=false\n')
        os.chmod(env,0o600)
    print(json.dumps({'schema':'hw1_compliance','fixtures':{k:len(v) for k,v in data.items()},
                      'role':'hw1_compliance_app','production_data_used':False,'enabled':False}))

if __name__=='__main__':
    main()
