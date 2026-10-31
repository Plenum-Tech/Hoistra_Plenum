#!/usr/bin/env python3
"""Index retained isolated evidence without changing rows or data privileges."""
import json,subprocess
NAME='hoistra-hw1-postgres-1'

def main():
 p=subprocess.run(['docker','inspect',NAME],capture_output=True,text=True,check=True)
 row=json.loads(p.stdout)[0]
 if row['Config']['Labels'].get('com.docker.compose.project')!='hoistra-hw1':
  raise RuntimeError('Unrelated database project refused')
 # scope_fingerprint is LEAKPROOF so RLS may use its text-equality index. It only
 # canonicalizes its fixed text-array argument: no SQL/data access, dynamic SQL,
 # identity/settings inspection, explicit exceptions or SECURITY DEFINER. JSON
 # encoding prevents delimiter ambiguity; the fixed-size digest is only an index
 # prefilter. Original building-array predicates remain authoritative, including
 # in the event of a digest collision. Source/manifest SHA256 integrity is separate.
 # CONCURRENTLY preserves normal reads/writes during additive index maintenance.
 statements=[
  "CREATE OR REPLACE FUNCTION hw1_compliance.scope_fingerprint(input text[]) RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT LEAKPROOF PARALLEL SAFE AS $$ BEGIN RETURN md5(array_to_json(ARRAY(SELECT DISTINCT value FROM unnest(input) value ORDER BY value))::text); END $$;",
  "REVOKE ALL ON FUNCTION hw1_compliance.scope_fingerprint(text[]) FROM PUBLIC;",
  "GRANT EXECUTE ON FUNCTION hw1_compliance.scope_fingerprint(text[]) TO hw1_compliance_app;"]
 for table in ['history','outbox','records','sources','proposals','events']:
  statements.append(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {table}_org_kind_id ON hw1_compliance.{table} (org,kind,id);')
 statements += [
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS history_notification_scope_fingerprint ON hw1_compliance.history (org,hw1_compliance.scope_fingerprint(buildings),id) WHERE kind='notification_issue' AND cardinality(buildings)>0;",
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS outbox_visible_scope_fingerprint ON hw1_compliance.outbox (org,hw1_compliance.scope_fingerprint(buildings),id) WHERE cardinality(buildings)>0 AND data->>'scope_verified'='true' AND (data->>'sensitive') IS DISTINCT FROM 'true';",
  "ANALYZE hw1_compliance.history;", "ANALYZE hw1_compliance.outbox;"]
 # Remove only superseded indexes introduced by this readiness attempt. The
 # original schema had primary keys only; no historical/user index is removed.
 # Keeping redundant copies amplified immutable-notification writes and cache IO.
 for name in ['history_buildings_gin','outbox_buildings_gin','history_notification_cover',
              'outbox_visible_cover','history_notification_scope','history_notification_exact_scope','outbox_visible_exact_scope','outbox_visible_scope_key','history_notification_scope_key']:
  statements.append(f'DROP INDEX CONCURRENTLY IF EXISTS hw1_compliance.{name};')
 result=subprocess.run(['docker','exec','-i',NAME,'psql','-X','-v','ON_ERROR_STOP=1','-U','hoistra_hw1','-d','hoistra_hw1'],
  input='\n'.join(statements),capture_output=True,text=True)
 if result.returncode:raise RuntimeError('Isolated evidence indexing failed; private DB diagnostics required')
 print(json.dumps({'isolated_evidence_indexes':'verified creation','source_rows_changed':False}))
if __name__=='__main__':main()
