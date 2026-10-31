#!/usr/bin/env python3
"""Attach current acceptance evidence without relabelling the historical requirement register."""
import json,hashlib
from pathlib import Path
MODULE=Path(__file__).resolve().parent;ROOT=MODULE.parents[1]
MAP={'contract':['01-contract'],'scoring_admin':['01-contract'],'energy':['02-energy'],
 'ingestion':['03-ingestion'],'corrections':['03-ingestion','04-communications-hoi'],
 'communications':['04-communications-hoi'],'hoi':['04-communications-hoi'],'notifications':['04-communications-hoi','05-usage-reports'],
 'usage':['05-usage-reports'],'routing':['06-unified-agent'],'state':['06-unified-agent'],'trace':['06-unified-agent'],
 'data':['01-contract','02-energy','06-unified-agent']}
DEFERRED={'FIIX-01','CONNECT-01','CONTRACT-FR-040'}
ADAPTED={'UI-01','HW1-SCOPE-01','INGEST-01','INGEST-02','MIG-01','DATA-04','DATA-05','DATA-06','COMP-05','STATE-01','HITL-01','ROUTE-03','ROUTE-05'}
def main():
 source=json.loads((MODULE/'hw1-spec-prompt-skill-mapping-v0.37.0.json').read_text());rows=[]
 for item in source['requirements']:
  identifier=item['id'];group=item['mapping_group'];milestones=MAP.get(group,['06-unified-agent'])
  if group in {'compliance','vendor','actions','identity','access','visibility','workorders'}:milestones=['03-ingestion','04-communications-hoi','06-unified-agent']
  status='baseline_adapter_verified'
  limit='Evidence covers linked fictional backend acceptance checks; not blanket conformance of legacy production tools.'
  if identifier in DEFERRED:status='production_extension_deferred';limit='Real connectors/transport and production working-calendar support remain outside this Homework baseline.'
  elif identifier in ADAPTED:status='backend_baseline_adaptation_verified';limit='Guarded typed fixtures/search/owned sessions/API evidence replaces legacy frontend, connector, workflow or subjective-evaluator assumptions; no production/LLM-judge conformance claim.'
  elif identifier.startswith('CONTRACT-FR-'):limit='Structured fictional contract inputs and verified scoring/configuration/diagnostic slice; no PDF/OCR, real-mail, frontend or production calendar claim. FR027 numerical invoice influence is superseded by separate diagnostic; date-specific caps apply per contribution.'
  evidence=[]
  for milestone in milestones:
   file=MODULE/'milestones/evidence'/f'{milestone}.json'
   if not file.exists():raise SystemExit('Missing milestone evidence')
   receipt=json.loads(file.read_text())
   if not receipt['checks'] or not all(x['passed'] for x in receipt['checks']):raise SystemExit('Milestone evidence is not passing')
   evidence.append({'receipt':file.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
    'checks':[x['id'] for x in receipt['checks']]})
  rows.append({'id':identifier,'canonical_group':group,'policy':'canonical v0.37.0 baseline applicability; inherited inventory grants no new permission',
   'implementation_status':status,'verification_scope':limit,'evidence':evidence})
 result={'specification_version':'0.37.0','specification_sha256':hashlib.sha256((ROOT/'specifications.md').read_bytes()).hexdigest(),
  'checkpoint':'complete declared Homework backend acceptance; activation recorded separately by07 gate',
  'historical_mapping_preserved':True,'requirements':rows,'requirement_count':len(rows),
  'activation_receipt':'homework/module-1/milestones/evidence/07-readiness.json',
  'limits':['fictional structured records/contacts only','no real email or external business action','no automatic scheduling',
   'no frontend/production identity/connectors/PDF-OCR certification','no unrestricted schema/SQL or operational WO lifecycle',
   'unmeasured adoption/pricing/quotas/coverage trends explicitly unavailable','ten-group fixed HOI only; no guessed slots or manual score changes',
   'backend acceptance is not legal/production verification or a course video/Git publishing claim']}
 (MODULE/'readiness-requirement-status-v0.37.0.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'canonical_requirements_mapped':len(rows),'specification_version':'0.37.0'}))
if __name__=='__main__':main()
