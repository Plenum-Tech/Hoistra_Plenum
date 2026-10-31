"""Add fictional scoped report contacts and immutable boundary observations."""
import json,subprocess,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def quote(v):return "'"+str(v).replace("'","''")+"'"
def run(sql):
 r=subprocess.run(['docker','exec','-i','hoistra-hw1-postgres-1','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','hoistra_hw1','-d','hoistra_hw1'],input=sql,capture_output=True,text=True)
 if r.returncode:raise SystemExit('Private fictional usage fixture query failed')
 return r.stdout.strip()
def insert(table,row):
 b='ARRAY['+','.join(quote(v) for v in row['buildings'])+']::text[]'
 return 'INSERT INTO hw1_compliance.'+table+' (id,kind,org,buildings,data) VALUES ('+','.join([quote(row['id']),quote(row['kind']),quote(row['org']),b,quote(json.dumps(row['data']))+'::jsonb'])+') ON CONFLICT (id) DO NOTHING;'
def main():
 f=ROOT/'homework/module-1/runtime/test-identities.json'
 if f.stat().st_mode & 0o077:raise SystemExit('Private identity file permissions too broad')
 identity=json.loads(f.read_text());statements=['BEGIN;']
 for u in identity['users']:
  scope=u['building_ids']
  if u['role']=='portfolio_manager':
   portfolio=u.get('portfolio_id') or u['organization_id']+'-default-portfolio'
   raw=run("SELECT data->'building_ids' FROM hw1_compliance.records WHERE id="+quote(portfolio)+";")
   if raw and raw!='null':scope=json.loads(raw)
  if u['role']=='compliance_officer':
   raw=run("SELECT json_agg(id ORDER BY id) FROM hw1_compliance.records WHERE kind='building' AND org="+quote(u['organization_id'])+";")
   if raw:scope=json.loads(raw)
  for mode in ['success','fail','unknown','missing']:
   ident='usage-fixture-'+u['user_id']+'-'+mode+'-'+hashlib.sha256(json.dumps(sorted(scope)).encode()).hexdigest()[:10]
   d={'scope_verified':True,'user_id':u['user_id'],'email':None if mode=='missing' else mode+'@usage.homework.invalid',
      'simulation':mode,'purposes':['usage_digest'],'contact_type':'team','fixture_version':'usage-v2','usage_only':True}
   if u['role'] in {'property_manager','portfolio_manager'}:d['manager_role']=u['role']
   if u['role']=='compliance_officer':d['officer']=True
   statements.append(insert('records',{'id':ident,'kind':'contact','org':u['organization_id'],'buildings':scope,'data':d}))
 for n,(when,tokens) in enumerate([('2026-02-01T19:59:59+00:00',1),('2026-02-01T20:00:00+00:00',10),
  ('2026-02-08T19:59:59+00:00',20),('2026-02-08T20:00:00+00:00',100),
  ('2026-01-31T19:59:59+00:00',1000),('2026-01-31T20:00:00+00:00',2000),('2026-02-28T20:00:00+00:00',4000)]):
  d={'actor':'hw1-property','role':'property_manager','domain':'energy','category':'energy','conversation_id':'fixture-boundary-session',
     'generated_at':when,'measurement_state':'measured','input_tokens':tokens,'output_tokens':tokens,
     'measurement_origin':'fictional source-backed boundary fixture, not a live provider receipt','source_id':'usage-boundary-source-'+str(n)}
  row={'id':'usage-boundary-event-'+str(n),'kind':'usage_measurement','org':'hw1-org-a','buildings':['hw1-building-a1'],'data':d}
  source={'id':'usage-boundary-source-'+str(n),'kind':'usage_fixture','org':row['org'],'buildings':row['buildings'],
     'data':{'original':json.dumps(d,sort_keys=True),'fields':d,'scope_verified':True,'version':1,'information_type':'fictional_usage_observation'}}
  statements.extend([insert('sources',source),insert('history',row)])
 statements.append('COMMIT;');run('\n'.join(statements));print(json.dumps({'fixture_profile':'usage-v2','immutable_boundary_events':7,'originals_overwritten':False}))
if __name__=='__main__':main()
