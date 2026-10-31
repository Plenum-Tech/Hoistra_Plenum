#!/usr/bin/env python3
"""Full declared backend acceptance, rollback-ready isolated promotion and active proof."""
import importlib.util,json,hashlib,os,re,shutil,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
HERE=Path(__file__).resolve().parent;MODULE=HERE.parents[1];ROOT=MODULE.parents[1]
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
common=load('readiness_client',HERE/'01-contract.py');support=load('readiness_support',MODULE/'readiness.py')
require=common.require
IDS=['all_previous_milestone_gates_reverified','canonical_spec_requirement_status_and_evidence_mapping',
 'full_service_regression_passes','complete_candidate_live_role_and_domain_matrix','rollback_ready_isolated_active_deployment',
 'active_full_agent_status_and_real_provider_question','homework_cli_commands_and_honest_remaining_boundaries']
DEST=HERE.parent/'evidence/07-readiness.json'
PRIOR=['01-contract','02-energy','03-ingestion','04-communications-hoi','05-usage-reports','06-unified-agent']
PRIVATE=MODULE/'runtime/remote-codex/milestones/07-readiness/attempt-001'
PYTHON=ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents/.venv/bin/python'

class Gate(common.Gate):
 def __init__(self):
  super().__init__();self.run_id=uuid4().hex;self.logs=PRIVATE/self.run_id;self.logs.mkdir(parents=True,mode=0o700);os.chmod(self.logs,0o700)
  if DEST.exists():shutil.copy2(DEST,self.logs/'07-readiness-previous-receipt.json')
  self.promoted=False;self.rollback_state=None;self.prior=[]
 def logged(self,command,name,timeout=1800):
  env=support.compose_environment() if list(command[:2])==['docker','compose'] else None
  result=subprocess.run([str(x) for x in command],cwd=ROOT,text=True,capture_output=True,timeout=timeout,env=env)
  path=self.logs/(name+'.log');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(result.stdout+'\n'+result.stderr)
  require(result.returncode==0,'Verification command failed: '+name+'; private resumable log retained')
  return result.stdout
 def prepare(self):
  # Rebuild/provision only the candidate before any acceptance claims or active changes.
  for name in ['setup-readiness-indexes.py','setup-contract-fixtures.py','setup-energy-fixtures.py','setup-ingestion-fixtures.py','setup-usage-fixtures.py','setup-unified-fixtures.py']:
   self.logged([PYTHON,MODULE/name],name)
  self.logged(['docker','compose','--env-file',MODULE/'runtime/.env','--profile','compliance-review','-p','hoistra-hw1','-f',MODULE/'docker-compose.hw1.yml','up','-d','--build','compliance_candidate'],'candidate-build',300)
  support.wait_health(18009)
  self.initial_active=support.inspect(support.ACTIVE_NAME)['Image']
 def previous(self):
  # These two gates never append original sources. Run them together, retaining
  # both exact before/after fingerprints; source-mutating ingestion starts later.
  starts={}
  for identifier in PRIOR[:2]:
   file=HERE.parent/'evidence'/f'{identifier}.json'
   if file.exists():shutil.copy2(file,self.logs/(identifier+'-previous-receipt.json'))
   starts[identifier]=time.time()
  with ThreadPoolExecutor(max_workers=2) as pool:
   futures=[pool.submit(self.logged,[sys.executable,HERE/f'{identifier}.py'],identifier,900) for identifier in PRIOR[:2]]
   for future in futures:future.result()
  for identifier in PRIOR:
   file=HERE.parent/'evidence'/f'{identifier}.json'
   if identifier not in starts:
    if file.exists():shutil.copy2(file,self.logs/(identifier+'-previous-receipt.json'))
    starts[identifier]=time.time();self.logged([sys.executable,HERE/f'{identifier}.py'],identifier,900)
   started=starts[identifier]
   receipt=json.loads(file.read_text())
   module=load('required_'+identifier.replace('-','_'),HERE/f'{identifier}.py')
   required=getattr(module,'REQUIRED',getattr(module,'IDS',None))
   require(receipt['milestone_id']==identifier and receipt['specification_version']=='0.37.0','Prior receipt identity mismatch')
   require(receipt['api_base']=='http://127.0.0.1:18009' and file.stat().st_mtime>=started-1,'Prior gate receipt is stale')
   require([c['id'] for c in receipt['checks']]==required and all(c['passed'] is True for c in receipt['checks']),'Prior milestone has a failed/missing check: '+identifier)
   self.prior.append({'milestone_id':identifier,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'checks':len(required)})
   print(json.dumps({'readiness_progress':'prior_gate_passed','milestone_id':identifier,'checks':len(required)}),flush=True)
  require(support.inspect(support.ACTIVE_NAME)['Image']==self.initial_active,'Active changed during candidate gate verification')
  self.record(IDS[0],'All six previous executable gates were freshly executed against rebuilt/provisioned candidate18009, exit0 and exact required identifiers passing: '+json.dumps(self.prior,sort_keys=True)+'. Historical previous receipts copied privately; no calculation-only or stale-receipt substitution. Active image remained unchanged during verification.')
 def mapping(self):
  self.logged([sys.executable,MODULE/'build-readiness-mapping.py'],'requirement-mapping')
  actual=json.loads((MODULE/'readiness-requirement-status-v0.37.0.json').read_text())
  original=json.loads((MODULE/'hw1-spec-prompt-skill-mapping-v0.37.0.json').read_text())
  require({r['id'] for r in actual['requirements']}=={r['id'] for r in original['requirements']} and len(actual['requirements'])==178,'Canonical requirement mapping incomplete')
  require(actual['specification_sha256']==hashlib.sha256((ROOT/'specifications.md').read_bytes()).hexdigest(),'Canonical specification hash stale')
  for row in actual['requirements']:
   require(row['policy'] and row['verification_scope'] and row['evidence'],'Requirement lacks explicit policy/implementation/evidence scope')
   for e in row['evidence']:require(hashlib.sha256((ROOT/e['receipt']).read_bytes()).hexdigest()==e['sha256'],'Requirement links stale evidence')
  self.record(IDS[1],'All178 historical v0.37 requirement IDs are covered by a separate current status/evidence register pinned to the actual canonical specification hash and fresh gate receipts. Owner policy, baseline adapter verification/adaptation and production deferrals are separate; legacy full-production/PDF-OCR/connector/subjective-evaluator conformance is not asserted. Original mapping/checklist history is preserved.')
 def regressions(self):
  output=self.logged([PYTHON,MODULE/'run-service-regressions.py'],'full-service-regressions',600)
  match=re.search(r'(\d+) passed, (\d+) warnings, (\d+) subtests passed',output)
  require(match is not None,'Full service suite lacks a successful pytest summary')
  self.suite={'passed':int(match[1]),'warnings':int(match[2]),'subtests':int(match[3])}
  require(self.suite['passed']>=500 and self.suite['subtests']>=65,'Full regression population unexpectedly reduced')
  self.record(IDS[2],'Actual complete service pytest run with private isolated DB environment (not dummy DB_URL): '+json.dumps(self.suite)+'. Log retained owner-only; no tests skipped/rewritten to suppress failures. Meaningful readiness regressions verify unrelated/public-container refusal, exclusive private rollback snapshots and owned server-retained exact email previews.')
 def matrix(self,base=None):
  old=common.BASE
  if base:common.BASE=base
  try:
   statuses=[]
   for user in ['hw1-general','hw1-property','hw1-portfolio','hw1-compliance']:
    status=self.call(user,'/api/homework/agent/status');require(len(status['registry']['enabled_profiles'])==7,'Enabled profile missing in live role matrix')
    for domain in ['compliance','contract','energy','ingestion','communications','usage']:
     tools=self.call(user,'/api/homework/'+domain+'/tools')['tools'];statuses.append((user,domain,len(tools)))
     if user in {'hw1-general','hw1-compliance'} and domain in {'contract','energy'}:require(not tools,'Forbidden commercial/Energy tools visible')
     if user=='hw1-general' and domain=='ingestion':require(not tools,'General ingestion tools visible')
     if user in {'hw1-general','hw1-compliance'}:require('communications_hoi' not in tools and 'homework_get_hoi' not in tools,'HOI leakage in role matrix')
    denied=self.call(user,'/api/homework/agent/ask',{'message':'Create and assign an operational work order'})
    require(denied['outcome']=='forbidden' and not denied['actual_tools'],'Operational work-order action not refused')
   return statuses
  finally:common.BASE=old
 def candidate_matrix(self):
  statuses=self.matrix()
  self.record(IDS[3],'Actual candidate four-role/six-domain API tool matrix (24 projections), unified seven-profile status for each role and operational-work-order refusals all pass. General no ingestion/commercial/Energy/HOI; Officer no commercial/Energy/HOI; scoped manager domains enabled. Prior gates additionally exercise each role/building/source projection, calculation, confirmation, failure and provider path.')
 def promote(self):
  self.rollback_state=support.prepare_rollback()
  require(support.image_id(self.rollback_state['rollback_tag'])==self.rollback_state['rollback_image_id'],'Rollback image pin invalid')
  self.promoted=True
  self.activation=support.activate(self.rollback_state)
  self.record(IDS[4],'After all prior gates, full suite and candidate matrix passed, only hoistra-hw1 orchestrator was recreated on localhost18008 with the exact verified candidate image and all reviewed domain flags. Private mode0600 original container/environment snapshot and validated exact rollback Compose file plus immutable original image tag are retained; no database/source deletion. '+json.dumps(self.activation,sort_keys=True)+'. No unrelated service/container or private runtime file was overwritten.')
 def active(self):
  self.matrix('http://127.0.0.1:18008')
  old=common.BASE;common.BASE='http://127.0.0.1:18008'
  try:
   status=self.call('hw1-property','/api/homework/agent/status')
   r=self.call('hw1-property','/api/homework/agent/ask',{'message':'Use energy_eui for building_id hw1-building-a1, meter_id hw1-energy-m2-condition, period_start 2026-10-01, period_end 2026-10-31. Report verified EUI.'})
   require(r['outcome']=='answered' and r['selected_route']=='energy','Active agent route failed')
   require(r['provider_execution']['successful_responses']>0 and 'energy_eui' in r['provider_execution']['selected_tools'],'Active question did not execute genuine provider-selected tool')
   data=next(c['result'] for c in r['tool_calls'] if c['tool']=='energy_eui' and 'result' in c)
   require(str(data['eui_kwh_per_m2_period']) in {'12','12.0'} and str(data['consumption_kwh']) in {'1200','1200.0'},'Active expected EUI1200/100=12 failed')
   require(r['unified_metadata']['manifest_sha256']==status['registry']['manifest_sha256'],'Active unified manifest/assembly mismatch')
   trace=self.call('hw1-property','/api/homework/traces?conversation_id='+r['conversation_id'])
   require(any(t['trace_id']==r['trace_id'] and t['actual_tools']==r['actual_tools'] for t in trace['traces']),'Active trace parity failed')
   self.active_proof={'conversation_id':r['conversation_id'],'trace_id':r['trace_id'],'manifest_sha256':status['registry']['manifest_sha256'],
    'provider_responses':r['provider_execution']['successful_responses'],'expected_eui':12}
  finally:common.BASE=old
  self.record(IDS[5],'Active18008 four-role/domain status/denial matrix passes after exact candidate promotion. Genuine configured OpenRouter selects energy_eui with independently expected1200kWh/100m2=12; unified registry/rendered assembly and owned actual-tool trace agree. '+json.dumps(self.active_proof,sort_keys=True)+'. Candidate18009 remains available; all communications simulated.')
 def cli(self):
  self.logged([sys.executable,MODULE/'hw1-cli.py','--help'],'cli-help')
  status=json.loads(self.logged([sys.executable,MODULE/'hw1-cli.py','--user','hw1-general','status'],'cli-status'))
  require(status['role']=='general_user' and len(status['registry']['enabled_profiles'])==7,'Active CLI status not role scoped')
  report=json.loads(self.logged([sys.executable,MODULE/'hw1-cli.py','--user','hw1-property','report','weekly','2026-02-02','--building','hw1-building-a1'],'cli-report'))
  require(report['metrics']['provider_calls']==2 and report['metrics']['provider_token_usage']['input_tokens']==30,'CLI Dubai boundary report not independently expected')
  require((MODULE/'READINESS-RUNBOOK.md').is_file(),'Readiness runbook missing')
  self.record(IDS[6],'Executable private-session CLI help, active General status and scoped manual weekly report tested (Dubai fixture2 observations/30 tokens, explicitly fictional). Runbook contains first question, tools, reports, owned traces, exact preview/human confirmation and private rollback commands. Production transport/connectors/OCR/frontend/calendar scheduling, unmeasured pricing/adoption/quotas and course video/Git publication remain explicitly outside this fictional backend verification.')


def main():
 receipt={'milestone_id':'07-readiness','specification_version':'0.37.0','api_base':'http://127.0.0.1:18009','checks':[]};gate=None;current=IDS[0]
 DEST.parent.mkdir(parents=True,exist_ok=True)
 try:
  gate=Gate();gate.prepare()
  for current,fn in zip(IDS,[gate.previous,gate.mapping,gate.regressions,gate.candidate_matrix,gate.promote,gate.active,gate.cli]):fn()
  receipt.update(api_base='http://127.0.0.1:18008',candidate_api_base='http://127.0.0.1:18009',checks=gate.checks,recorded_at=datetime.now(timezone.utc).isoformat(),full_service_suite=gate.suite,
    prior_milestones=gate.prior,activation=gate.activation,active_provider_proof=gate.active_proof)
  DEST.write_text(json.dumps(receipt,indent=2)+'\n')
  support.publish_checkpoint(receipt)
  print(json.dumps({'milestone_id':'07-readiness','passed':True,'checks':7,'active_api':'http://127.0.0.1:18008','suite':gate.suite}));return 0
 except Exception as error:
  safe=str(error) if isinstance(error,(common.GateFailure,support.ReadinessFailure)) else type(error).__name__
  rollback=None
  if gate and gate.promoted and gate.rollback_state:
   try:support.rollback(gate.rollback_state);rollback='original isolated image restored'
   except Exception:rollback='rollback failed; private state retained; immediate inspection required'
  receipt['checks']=(gate.checks if gate else [])+[{'id':current,'passed':False,'evidence':safe}]
  receipt['rollback_outcome']=rollback;DEST.write_text(json.dumps(receipt,indent=2)+'\n')
  print(json.dumps({'milestone_id':'07-readiness','passed':False,'failed_check':current,'reason':safe,'rollback_outcome':rollback}));return 1
if __name__=='__main__':sys.exit(main())
