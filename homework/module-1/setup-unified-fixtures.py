"""Append immutable explicit compatibility fixtures; no historic source/user changes."""
import hashlib,json,subprocess
B='hw1-building-a1'
def quote(v):return "'"+str(v).replace("'","''")+"'"
statements=['BEGIN;']
for suffix,changes in [('a',{}),('b',{}),('badunit',{'unit':'m3'}),('badperiod',{'period_end':'2026-09-30'}),('badentity',{'meter_id':'other-meter'})]:
 fields={'meter_id':'hw1-unified-meter-1','period_start':'2026-10-01','period_end':'2026-10-31','unit':'kWh',**changes}
 raw=json.dumps(fields,sort_keys=True,separators=(',',':'))
 data={'original':raw,'fields':fields,'content_sha256':hashlib.sha256(raw.encode()).hexdigest(),
       'scope_verified':True,'version':1,'information_type':'explicit_fictional_source_compatibility_fixture'}
 statements.append('INSERT INTO hw1_compliance.sources (id,kind,org,buildings,data) VALUES ('+
  ','.join([quote('hw1-unified-source-'+suffix),quote('unified_fixture'),quote('hw1-org-a'),"ARRAY['"+B+"']::text[]",quote(json.dumps(data))+'::jsonb'])+') ON CONFLICT (id) DO NOTHING;')
statements.append('COMMIT;')
r=subprocess.run(['docker','exec','-i','hoistra-hw1-postgres-1','psql','-X','-v','ON_ERROR_STOP=1','-U','hoistra_hw1','-d','hoistra_hw1'],input='\n'.join(statements),capture_output=True,text=True)
if r.returncode:raise SystemExit('Immutable unified source fixture provisioning failed')
print(json.dumps({'fixture_profile':'unified-v1','sources_considered':5,'originals_overwritten':False}))
