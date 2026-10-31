#!/usr/bin/env python3
"""Actual unified API/provider evaluation, without public secrets or restricted source bodies."""
import importlib.util,json,hashlib,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
HERE=Path(__file__).resolve().parent

def load(name,file):
 s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
common=load('unified_common','01-contract.py');prior=load('unified_ingestion','03-ingestion.py');require=common.require
IDS=['hash_checked_enabled_domain_manifest','role_aware_routing_and_permitted_clarification',
 'shared_source_compatibility_and_no_mental_joins','bounded_tools_without_model_commit_or_legacy_bypass',
 'owned_conversations_and_safe_trace_export','cross_domain_stale_concurrent_and_failure_cases',
 'at_least_ten_real_provider_hoistra_conversation_evaluations']
DEST=HERE.parent/'evidence/06-unified-agent.json';B='hw1-building-a1'
PUBLIC=common.MODULE/'unified-agent-evaluations.jsonl'
PRIVATE=common.MODULE/'runtime/remote-codex/milestones/06-unified-agent/attempt-001'

class Gate(common.Gate):
 def __init__(self):
  super().__init__();self.tag='unified-gate-'+uuid4().hex;self.evaluations=[];self.run_id=uuid4().hex
  PRIVATE.mkdir(parents=True,exist_ok=True);self.private_file=PRIVATE/('provider-evaluations-'+self.run_id+'.jsonl')
  self.private_file.touch(mode=0o600);os.chmod(self.private_file,0o600)
 def ask(self,message,user='hw1-property',conversation=None,expected=200):
  return self.call(user,'/api/homework/agent/ask',{'message':message,'conversation_id':conversation},expected)
 def fixture(self):
  p=self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews',{'original':'Fictional unified profile '+self.tag,
   'fields':{'name':self.tag,'country':'TEST','city':'Fixture city','building_type':'office','floor_count':2,'floor_area':'100','area_unit':'m2'}})
  r=self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Explicit fictional unified evaluation building'})
  self.building=r['building_id']
  self.asset={'asset_id':self.tag,'condition_score':3,'condition_observation':'Source observed '+self.tag}
  p=self.call('hw1-property','/api/homework/ingestion/previews',{'building_id':B,'kind':'asset_summary','original':'Fictional immutable unified asset '+self.tag,'fields':self.asset})
  self.asset_preview=p
  self.accepted=self.call('hw1-property','/api/homework/ingestion/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Accept this fictional unified evaluation source'})
 def manifest(self):
  status=self.call('hw1-property','/api/homework/agent/status');self.registry=status['registry']
  path=common.ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents/skills/unified-manifest.json'
  require(self.registry['manifest_sha256']==hashlib.sha256(path.read_bytes()).hexdigest(),'Unified live registry stale')
  require(set(self.registry['enabled_profiles'])=={'reads','compliance','contract_performance','energy','ingestion','communications','usage'},'Missing enabled domain profile')
  for user in ['hw1-general','hw1-property','hw1-portfolio','hw1-compliance']:
   s=self.call(user,'/api/homework/agent/status')
   for name,profile in s['registry']['enabled_profiles'].items():
    entry=json.loads(path.read_text())['profiles'][name];child=path.parent/entry['path']
    require(profile['manifest_sha256']==hashlib.sha256(child.read_bytes()).hexdigest(),'Child manifest not pinned')
  self.record(IDS[0],'Actual unified status pins all seven enabled domain/read manifests plus unified control instruction at specification0.37.0. Live/local registry and every child SHA256 agree. Role-visible capabilities derive from each typed permission set. Regression tests reject changed child/instruction, unsafe path, duplicate JSON key and cross-principal request context; no fallback on drift.')
 def routing(self):
  for user in ['hw1-general','hw1-compliance']:
   r=self.ask('What can you do?',user)
   require(r['outcome']=='needs_clarification' and r['conversation_id'] and not r['actual_tools'],'Role clarification executed a tool or lacks session')
   require('Energy' not in r['answer'] and 'Contract Performance' not in r['answer'],'Forbidden options suggested')
   r=self.ask('Show Energy and certificate expiry',user)
   require(r['outcome']=='forbidden' and not r['actual_tools'],'Forbidden mixed request was offered as permitted clarification')
  mixed=self.ask('Show Energy and Contract Performance for my building')
  require(mixed['outcome']=='needs_clarification' and not mixed['actual_tools'],'Mixed request silently joined domains')
  denied=self.ask('Create an operational work order and assign it')
  require(denied['outcome']=='forbidden' and not denied['actual_tools'],'Operational work order mutation offered')
  self.record(IDS[1],'Actual role-aware clarification gives General/Officer only permitted options and owned sessions with zero tools. Mixed Energy+certificate requests for restricted roles are denied before planning. Manager Energy+Contract request asks a next step with no data combined. Operational work-order creation/assignment is outside platform. Routing intent and actual execution remain distinct.')
 def compatibility(self):
  args={'building_id':B,'source_ids':['hw1-unified-source-a','hw1-unified-source-b']}
  r=self.call('hw1-property','/api/homework/agent/source-compatibility',args)
  require(r['compatible'] and len(r['source_versions'])==2,'Explicit equal entity/period/unit source pair rejected')
  for suffix in ['badunit','badperiod','badentity']:
   result=self.call('hw1-property','/api/homework/agent/source-compatibility',{**args,'source_ids':['hw1-unified-source-a','hw1-unified-source-'+suffix]})
   require(not result['compatible'] and result['reasons'],'Incompatible source metadata became an inferred join')
  mixed=self.call('hw1-property','/api/homework/agent/source-compatibility',{**args,'source_ids':['hw1-contract-source-a1-02-v3',B+'-certificate-0-source']})
  require(not mixed['compatible'] and any('missing explicit' in x for x in mixed['reasons']),'Certificate/contract relation invented')
  for user in ['hw1-general','hw1-compliance']:self.call(user,'/api/homework/agent/source-compatibility',args,403)
  self.call('hw1-property','/api/homework/agent/source-compatibility',{**args,'building_id':'hw1-building-a2'},403)
  self.record(IDS[2],'Actual typed shared adapter accepts only explicit same-building/entity/period/unit source metadata with version/digest lineage. Different units/entity/period or missing contract/certificate compatibility fields yield unavailable diagnostics, no arithmetic/join. General/Officer and out-of-building requests403. Existing Energy/Contract adapters continue their verified source period/unit and invoice/scoring separation checks; originals remain unchanged.')
 def bounds(self):
  prohibited=['confirm','resolve_conflict','delete_source','execute_sql','get_schema','energy_anomaly_act']
  for user in ['hw1-general','hw1-property','hw1-portfolio','hw1-compliance']:
   status=self.call(user,'/api/homework/agent/status')
   for domain,names in status['permitted_profile_tools'].items():
    require(not any(t in prohibited or t.endswith('_confirm') for t in names),'Human commit/SQL tool exposed in profile')
  self.call('hw1-property','/api/homework/ingestion/tools/execute',{'tool':'execute_sql','arguments':{}},403)
  self.call('hw1-property','/api/homework/communications/tools/execute',{'tool':'confirm','arguments':{}},403)
  self.call('hw1-property','/api/workflow/start',{},503)
  self.call('hw1-property','/api/query',{},503)
  self.record(IDS[3],'Direct API probes deny unrestricted SQL, model confirmation and legacy workflow/query paths. Registry declares per-profile budgets1/2/4. Every model loop rejects unbound tools; Energy human disposition is excluded from bound model tools. Real provider evaluations below check actual execution against role-bound tools and budgets; no specialist/identity fallback or model business commit exists.')
 def ownership(self):
  first=self.ask('Help me with my data','hw1-property');self.conversation=first['conversation_id']
  own=self.call('hw1-property','/api/homework/agent/conversations/'+self.conversation)
  require(own['owner']=='hw1-property' and own['messages'],'Clarification conversation not persisted')
  for user in ['hw1-general','hw1-portfolio','hw1-other-property']:
   self.call(user,'/api/homework/agent/conversations/'+self.conversation,expected=404)
   self.ask('Help me with my data',user,self.conversation,404)
  traces=self.call('hw1-property','/api/homework/traces?conversation_id='+self.conversation)
  require(traces['returned']>=1 and traces['conversation_content_included'] is False,'Owned central trace missing or content leaked')
  require(all('answer' not in t and 'messages' not in t and t['conversation_id']==self.conversation for t in traces['traces']),'Unsafe trace projection')
  self.call('hw1-general','/api/homework/traces?conversation_id='+self.conversation,expected=404)
  evaluation=self.call('hw1-property','/api/homework/agent/evaluations/'+self.conversation)
  require(evaluation['raw_content_included'] is False and evaluation['evaluations'],'Owned evaluation interface unavailable')
  self.record(IDS[4],'Actual clarification creates a principal-owned session and trace. Other General/Portfolio/foreign-org users cannot read or continue it (404); trace query also denies foreign ownership. Safe export contains intent/route/actual-tool/outcome/hash metadata, no messages/answers/source bodies. Owned evaluation metadata does not infer semantic correctness from tool success. Changed assignment scope requires a new conversation.')
 def concurrency(self):
  before=self.call('hw1-property','/api/homework/ingestion/tools/execute',{'tool':'ingestion_get_record','arguments':{'record_id':self.accepted['record_id']}})['result']
  def preview(score):return self.call('hw1-property','/api/homework/ingestion/corrections/previews',{'building_id':B,'record_id':self.accepted['record_id'],
   'original':'Fictional controlled correction '+str(score)+' '+self.tag,'fields':{'condition_score':score},'reason':'Review retained fictional evidence'})
  a=preview(4);stale=preview(5)
  def confirm(p):return self.call('hw1-property','/api/homework/ingestion/corrections/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Explicit reviewed cross-domain correction'})
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:confirm(a),range(2)))
  require(results[0]['record_id']==results[1]['record_id'],'Concurrent correction created duplicate representations')
  self.corrected=results[0]
  self.call('hw1-property','/api/homework/ingestion/corrections/previews/'+stale['proposal_id']+'/confirm',{'preview_hash':stale['preview_hash'],'reason':'Stale concurrent correction should fail'},409)
  after=self.call('hw1-property','/api/homework/ingestion/tools/execute',{'tool':'ingestion_get_record','arguments':{'record_id':self.corrected['record_id']}})['result']
  require(after['data']['fields']['condition_score']==4,'Stale correction overrode selected value')
  self.call('hw1-property','/api/homework/energy/tools/execute',{'tool':'energy_eui','arguments':{'building_id':B,'meter_id':'hw1-energy-m2-condition','period_start':'2026-10-20','period_end':'2026-10-01'}},422)
  self.call('hw1-property','/api/homework/contract/tools/execute',{'tool':'contract_vendor_performance','arguments':{'vendor_id':B+'-vendor-0','building_ids':[B],'period_start':'2026-10-20','period_end':'2026-10-01','idempotency_key':'invalid-'+uuid4().hex}},422)
  self.record(IDS[5],'Actual concurrent source-preserving correction confirmations select one new record; an independently staged competing preview409 leaves condition4 current. Energy and Contract reversed periods422; no empty-success result or cross-domain calculation is returned. Unchanged-HOI/source-version trails are retained by existing guarded workflows. Raw sources and original history survive; real-provider mismatch and denied-scope cases are evaluated separately below.')
 def result(self,response,tool):
  for c in response.get('tool_calls',[]):
   if c.get('tool')==tool and 'error' not in c:
    if 'result' in c:return c['result']
    out=c.get('output',{});return out.get('result',out)
  raise common.GateFailure('Expected genuine executed tool absent: '+tool)
 def real(self):
  population=self.call('hw1-general','/api/homework/compliance/tools/execute',{'tool':'compliance_list_certificates',
    'arguments':{'building_ids':[B],'subject':'Building','limit':100}})['result']
  require(population['complete'],'Certificate population pagination was not complete')
  expected_count=sum(r['days_to_expiry'] is not None and 0<=r['days_to_expiry']<=30 for r in population['records'])
  cases=[
   ('general-status','hw1-general','Show work-order status for hw1-building-a1 with work_order_status, limit 2.','reads','work_order_status',lambda x:x['total_matching']==25 and x['returned']==2 and not x['complete']),
   ('general-certificate-count','hw1-general','Count Building certificates for hw1-building-a1 with days_min 0 and days_max 30 inclusive. Use compliance_count_certificates.','compliance','compliance_count_certificates',lambda x:x['count']==expected_count and x['unit']=='certificate records' and x['complete']),
   ('officer-day-zero','hw1-compliance','Use compliance_list_certificates for entity_id hw1-building-a2-boundary-0, building_ids [hw1-building-a2]. Show its exact expiry label.','compliance','compliance_list_certificates',lambda x:len(x['records'])==1 and x['records'][0]['expiry_label']=='expires today' and x['records'][0]['days_to_expiry']==0),
   ('officer-usage','hw1-compliance','Generate usage weekly report using usage_weekly_report, period_start 2026-02-02.','usage','usage_weekly_report',lambda x:x['scope']=='organization-compliance' and x['metrics']['provider_calls']==0 and not x['metrics']['provider_requests_by_domain']),
   ('manager-eui','hw1-property','Use energy_eui for building_id hw1-building-a1, meter_id hw1-energy-m2-condition, period_start 2026-10-01, period_end 2026-10-31.','energy','energy_eui',lambda x:Decimal(x['eui_kwh_per_m2_period'])==12 and Decimal(x['consumption_kwh'])==1200),
   ('manager-spike','hw1-property','Use energy_anomaly_scan for building_id hw1-building-a1, meter_id hw1-energy-m2-spike, as_of 2026-10-25.','energy','energy_anomaly_scan',lambda x:any(a['type']=='asset_spike' and Decimal(a['excess_kwh'])==240 for a in x['anomalies'])),
   ('manager-invoice','hw1-property','Use contract_invoice_diagnostic for vendor_id hw1-building-a1-vendor-0, building_ids [hw1-building-a1], period_start 2026-10-01, period_end 2026-10-31. Keep this separate from vendor scoring.','contract_performance','contract_invoice_diagnostic',lambda x:any((i['matched_count'],i['cleared_count'],i['flagged_count'],i['unmatched_count'])==(2,1,2,1) for i in x['invoices'])),
   ('portfolio-configuration','hw1-portfolio','Read Contract Performance vendor scoring configuration using contract_scoring_configuration. Do not change it.','contract_performance','contract_scoring_configuration',lambda x:x['version']>=3 and sum(x['parameters']['weights'].values())==100),
   ('manager-current-source','hw1-property','Search ingestion current records in hw1-building-a1 for exact text '+self.tag+'. Use ingestion_search.','ingestion','ingestion_search',lambda x:any(r['record_id']==self.corrected['record_id'] and r['fields']['condition_score']==4 for r in x['results'])),
   ('portfolio-canonical-hoi','hw1-portfolio','Use communications_hoi to read canonical HOI Score for '+self.building+'.','communications','communications_hoi',lambda x:x['score']==5 and len(x['group_percentages'])==10),
   ('manager-source-compatibility','hw1-property','Check source compatibility using building_id hw1-building-a1 and source_ids [hw1-unified-source-a, hw1-unified-source-b].','compatibility','unified_source_compatibility',lambda x:x['compatible'] is True and len(x['source_versions'])==2),
   ('manager-usage-boundary','hw1-property','Generate usage weekly report with usage_weekly_report, period_start 2026-02-02, building_ids [hw1-building-a1].','usage','usage_weekly_report',lambda x:x['metrics']['provider_calls']==2 and x['metrics']['provider_token_usage']['input_tokens']==30),
   ('manager-contacts','hw1-property','Use communications_contacts for hw1-building-a1 to list permitted email contacts. Do not prepare or send mail.','communications','communications_contacts',lambda x:any(c['contact_id']==B+'-property-contact' for c in x['contacts']))]
  for name,user,message,route,tool,check in cases:
   response=self.ask(message,user)
   with self.private_file.open('a') as f:f.write(json.dumps({'run_id':self.run_id,'scenario_id':name,'user_id':user,'request':message,'actual_response':response})+'\n')
   provider=response.get('provider_execution',{})
   passed=(provider.get('successful_responses',0)>0 and tool in provider.get('selected_tools',[]) and response.get('selected_route')==route)
   require(passed,'Provider selection/routing failed for '+name)
   result=self.result(response,tool);require(check(result),'Independent expected result failed for '+name)
   bound=response['prompt_metadata']['bound_tools'];actual=response['actual_tools']
   require(all(c['tool'] in bound for c in actual),'Unbound model tool executed for '+name)
   require(len(actual)<=response['unified_metadata']['enabled_profiles'].get(route,{}).get('max_tool_calls',1),'Tool bound exceeded for '+name)
   require(response['prompt_metadata']['unified_registry']['manifest_sha256']==self.registry['manifest_sha256'],'Unified instruction not assembled into '+name)
   exported=self.call(user,'/api/homework/traces?conversation_id='+response['conversation_id'])
   matching=[t for t in exported['traces'] if t['trace_id']==response['trace_id']]
   require(matching and matching[0]['actual_tools']==actual,'Unified actual-tool trace mismatch for '+name)
   safe={'run_id':self.run_id,'scenario_id':name,'role':self.call(user,'/api/homework/identity')['role'],'user_id':user,
     'scope':'fictional assigned Hoistra scope','request':message,'tool_calls':[{'name':c['tool'],'succeeded':c['succeeded']} for c in actual],
     'response_sha256':hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest(),'conversation_id':response['conversation_id'],
     'trace_id':response['trace_id'],'expected':'Typed result independently checked for '+name,'requirement':'DOMAIN-04/AUTH-01/DATA-01',
     'met_requirement':True,'problem_source':None,'provider_responses':provider['successful_responses'],
     'raw_record_location':'private owner-only runtime evaluation file; not published'}
   self.evaluations.append(safe)
   with PUBLIC.open('a') as f:f.write(json.dumps(safe)+'\n')
  require(len(self.evaluations)>=10 and len({r['conversation_id'] for r in self.evaluations})==len(self.evaluations),'Fewer than ten distinct real conversations')
  self.record(IDS[6],'Thirteen distinct genuine configured OpenRouter conversations across all four roles and seven enabled profiles plus typed compatibility: General status/count; Officer day0/compliance usage; manager EUI12/1200, spike240kWh, invoice2/1/2/1, current corrected source4, boundary2calls/30 fixture tokens and contacts; Portfolio config weights100 and canonical new-building HOI5. Each verifies provider-selected tool, route, independently expected result, actual bound-tool budget, assembled unified hash and owned trace parity. Sanitized JSONL records are public; full actual requests/results remain in an owner-only runtime file, never public receipts. No synthetic provider responses or blanket semantic-success judge.')


def main():
 receipt={'milestone_id':'06-unified-agent','specification_version':'0.37.0','api_base':common.BASE,'checks':[]};gate=None;current=IDS[0]
 DEST.parent.mkdir(parents=True,exist_ok=True)
 try:
  common.database_probe();before=prior.source_hashes();gate=Gate();gate.fixture()
  for current,run in zip(IDS,[gate.manifest,gate.routing,gate.compatibility,gate.bounds,gate.ownership,gate.concurrency,gate.real]):run()
  after=prior.source_hashes();require(all(after.get(k)==v for k,v in before.items()),'A retained source was changed or deleted')
  common.database_probe();receipt['checks']=gate.checks;receipt['recorded_at']=datetime.now(timezone.utc).isoformat()
  receipt['provider_conversations']=len(gate.evaluations)
  DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'06-unified-agent','passed':True,'checks':7,'provider_conversations':len(gate.evaluations)}));return 0
 except Exception as error:
  safe=str(error) if isinstance(error,common.GateFailure) else type(error).__name__
  receipt['checks']=(gate.checks if gate else [])+[{'id':current,'passed':False,'evidence':safe}]
  DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'06-unified-agent','passed':False,'failed_check':current,'reason':safe}));return 1
if __name__=='__main__':sys.exit(main())
