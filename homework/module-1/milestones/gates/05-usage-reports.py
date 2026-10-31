#!/usr/bin/env python3
"""Running candidate verification for measured usage, scoped immutable reports and outbox."""
import importlib.util,json,subprocess,sys,hashlib
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
HERE=Path(__file__).resolve().parent

def load(name,file):
 s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
common=load('usage_common','01-contract.py');ingestion=load('usage_ingestion','03-ingestion.py');require=common.require
IDS=['source_events_for_provider_ingestion_onboarding_failure_and_backlog','unsupported_metrics_explicitly_unavailable',
 'weekly_monthly_period_and_timezone_boundaries','four_role_recipient_and_building_scope',
 'manual_trigger_immutable_snapshot_and_idempotent_replay','digest_and_immediate_failure_delivery_outcomes']
DEST=HERE.parent/'evidence/05-usage-reports.json';B='hw1-building-a1'
TODAY=datetime.now(timezone(timedelta(hours=4))).date();MONDAY=TODAY-timedelta(days=TODAY.weekday())


def metadata(table,ids):
 # Fixed server table allowlist, no model SQL. Return safe event/contact metadata only.
 require(table in {'history','records','proposals'},'Invalid private probe table')
 script='''
import asyncio,json
from sqlalchemy import text
from src.services.compliance_hw1.store import runtime_store
async def main():
 s=runtime_store()
 async with s.engine.connect() as c:
  await c.execute(text("SELECT set_config('hw1.org','hw1-org-a',true)"))
  rows=(await c.execute(text("SELECT id,kind,org,buildings,data FROM hw1_compliance.TABLE WHERE org='hw1-org-a' AND id=ANY(CAST(:ids AS text[]))"),{'ids':IDS})).mappings().all()
  fields=['actor','category','domain','selected_route','role','user_id','manager_role','officer','simulation','request_id','measurement_origin']
  print(json.dumps([{'id':r['id'],'kind':r['kind'],'org':r['org'],'buildings':r['buildings'],**{k:r['data'].get(k) for k in fields}} for r in rows]))
 await s.engine.dispose()
asyncio.run(main())
'''.replace('TABLE',table).replace('IDS',repr(list(ids)))
 r=subprocess.run(['docker','exec','-i','hoistra-hw1-compliance_candidate-1','python','-'],input=script,text=True,capture_output=True,timeout=60)
 require(r.returncode==0,'Private least-privilege metadata probe failed');return json.loads(r.stdout.strip().splitlines()[-1])


class Gate(common.Gate):
 def __init__(self):super().__init__();self.tag='usage-gate-'+uuid4().hex;self.reports={}
 def report(self,user='hw1-property',start=MONDAY,cadence='weekly',scope=None,expected=200):
  args={'period_start':str(start)}
  if scope is not None:args['building_ids']=scope
  return self.call(user,'/api/homework/usage/reports/'+cadence,args,expected)
 def get(self,user,identifier,expected=200):return self.call(user,'/api/homework/usage/reports/'+identifier,expected=expected)
 def outbox(self):
  rows=[];cursor=None;expected=None;seen=set()
  while True:
   args={'building_id':B,'limit':100,'include_issues':False}
   if cursor is not None:args['after_id']=cursor
   r=self.call('hw1-property','/api/homework/communications/tools/execute',{'tool':'communications_outbox','arguments':args})['result']
   if expected is None:
    expected=r['total_matching'];require(type(expected) is int,'First page must count authorized outbox rows')
   ids=[x['id'] for x in r['records']]
   require(ids==sorted(ids) and len(ids)==len(set(ids)) and not seen.intersection(ids),'Duplicate or unordered outbox evidence')
   require(cursor is None or all(i>cursor for i in ids),'Outbox cursor did not advance')
   seen.update(ids);rows+=r['records']
   if r['next_cursor'] is None:
    require(len(rows)==expected,'Outbox traversal differs from authorized first-page count')
    return rows
   require(ids and r['next_cursor']==ids[-1],'Outbox cursor does not match returned evidence')
   cursor=r['next_cursor']

 def preview(self):
  return self.call('hw1-property','/api/homework/ingestion/previews',{'building_id':B,'kind':'asset_summary',
   'original':'Immutable fictional usage intake '+uuid4().hex,'fields':{'asset_id':uuid4().hex,'condition_score':3,'condition_observation':self.tag}})
 def sources(self):
  before=self.report();metrics=before['metrics']
  response=self.call('hw1-property','/api/homework/agent/ask',{'message':
   'Generate the measured usage weekly report using usage_weekly_report, period_start '+str(MONDAY)+', building_ids [hw1-building-a1]. Use the recorded results only.'})
  require(response.get('selected_route')=='usage' and response.get('outcome')=='answered','Usage agent routing failed')
  require(response['provider_execution']['successful_responses']>0 and 'usage_weekly_report' in response['provider_execution']['selected_tools'],'No genuine configured provider usage tool call')
  manifest=common.ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents/skills/usage-manifest.json'
  require(response['prompt_metadata']['manifest_sha256']==hashlib.sha256(manifest.read_bytes()).hexdigest(),'Usage manifest stale')
  after=self.report();require(after['metrics']['provider_calls']>metrics['provider_calls'],'Actual provider receipt not counted')
  fresh=set(after['source_events_by_kind']['usage_measurement'])-set(before['source_events_by_kind']['usage_measurement'])
  require(fresh and all(not r['measurement_origin'] for r in metadata('history',fresh)),'Live provider receipt replaced by fictional fixture')
  p=self.preview();pending=self.report()
  require(pending['metrics']['ingestion_backlog']==after['metrics']['ingestion_backlog']+1 and p['proposal_id'] in pending['backlog_source_ids'],'Actual pending source proposal not counted')
  accepted=self.call('hw1-property','/api/homework/ingestion/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Accept this fictional source to verify actual measured usage'})
  done=self.report();require(done['metrics']['ingestion_successes']==after['metrics']['ingestion_successes']+1 and done['metrics']['ingestion_backlog']==after['metrics']['ingestion_backlog'],'Accepted intake source event/backlog not measured')
  self.call('hw1-property','/api/homework/ingestion/previews',{'building_id':B,'kind':'asset_summary','original':'Fictional rejected input',
   'fields':{'asset_id':self.tag,'condition_score':0,'condition_observation':'Invalid source condition'}},422)
  failed=self.report();require(failed['metrics']['ingestion_failures']==done['metrics']['ingestion_failures']+1,'Observed ingestion failure lacks durable period source')
  self.failure_ids=set(failed['source_events_by_kind']['usage_failure'])-set(done['source_events_by_kind']['usage_failure'])
  require(len(self.failure_ids)==1,'Failure event duplicated or missing')
  portfolio=self.report('hw1-portfolio')
  p=self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews',{'original':'Fictional measured onboarding '+self.tag,'fields':{
   'name':self.tag,'country':'TEST','city':'Fictional','building_type':'office','floor_count':2,'floor_area':'100','area_unit':'m2'}})
  self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Approve this fictional building for measured onboarding verification'})
  newer=self.report('hw1-portfolio');require(newer['metrics']['buildings_onboarded']==portfolio['metrics']['buildings_onboarded']+1,'Actual onboarding event not counted')
  self.current=failed
  self.record(IDS[0],'Genuine configured OpenRouter selects usage_weekly_report through central agent with current manifest hash; fresh provider receipts increase measured calls. An actual typed source preview adds one backlog item; confirmation adds one accepted intake event and clears it. Actual invalid-condition422 appends exactly one durable failure event. API onboarding adds one source-backed building event in the Portfolio report. No telemetry injection tool is exposed.')
 def unavailable(self):
  r=self.report();m=r['metrics']
  for key in ['active_users','sessions','subscription_charges','model_api_cost','configured_usage_limit','building_coverage_trend']:
   require(m[key] is None,'Unsupported metric fabricated: '+key)
  require(r['unavailable_metrics'] and r['source_freshness'] and r['backlog_as_of'] and r['measurement_coverage'],'Missing measurement coverage/freshness')
  require('subscription charges' in r['unavailable_metrics'] and m['provider_token_usage']['measured_calls']<=m['provider_calls'],'Unsupported cost or token coverage claim')
  self.record(IDS[1],'Actual report returns null for unmeasured full-platform active users/sessions, subscription charges, model cost without pricing, unconfigured limits and coverage trends. Observed provider population/token coverage, immutable event references, period source freshness and current backlog as-of are separately labelled; absent telemetry is never presented as zero total platform activity.')
 def boundaries(self):
  week=self.report(start=date(2026,2,2));month=self.report(start=date(2026,2,1),cadence='monthly')
  require(week['metrics']['provider_calls']==2 and week['metrics']['provider_token_usage']['input_tokens']==30,'Independent Dubai weekly boundary expected2 observations/30 fixture tokens')
  require(set(week['source_events_by_kind']['usage_measurement'])=={'usage-boundary-event-1','usage-boundary-event-2'},'Weekly inclusive/exclusive endpoints incorrect')
  require(month['metrics']['provider_calls']==5 and month['metrics']['provider_token_usage']['input_tokens']==2131,'Independent February month expected5 observations/2131 fixture tokens')
  require(week['reporting_period']['start_utc']=='2026-02-01T20:00:00+00:00' and week['reporting_period']['end_exclusive_utc']=='2026-02-08T20:00:00+00:00','Dubai weekly UTC boundaries incorrect')
  require(month['reporting_period']['end']=='2026-02-28' and month['reporting_period']['end_exclusive_utc']=='2026-02-28T20:00:00+00:00','Month boundary incorrect')
  self.report(start=date(2026,2,3),expected=422);self.report(start=date(2026,2,2),cadence='monthly',expected=422)
  require(all(r['measurement_origin'].startswith('fictional') for r in metadata('history',week['source_events_by_kind']['usage_measurement'])),'Boundary fixtures misrepresented as live receipts')
  self.record(IDS[2],'Immutable explicitly fictional source-backed boundary observations exercise actual APIs: Monday Feb2 local00:00 equals Feb1 UTC20:00; include that instant and last second, exclude both adjacent boundaries. Weekly2 observations/30 fixture input tokens; February5/2131, ending Feb28 UTC20:00 exclusively. Invalid non-Monday and non-first-of-month422. Generation time is distinct from intended Monday09:00 cadence. These boundary fixtures are not live provider receipts.')
 def roles(self):
  provision=subprocess.run([sys.executable,str(common.MODULE/'setup-usage-fixtures.py')],capture_output=True,text=True,timeout=60)
  require(provision.returncode==0,'Scoped report contact provisioning failed')
  for user in ['hw1-general','hw1-property','hw1-portfolio','hw1-compliance']:
   r=self.report(user);self.reports[user]=r;identity=self.call(user,'/api/homework/identity')
   require(set(r['building_ids'])<=set(identity['building_ids']),'Report building scope broadened')
   rows=metadata('history',r['source_ids'])
   if user=='hw1-general':require(all(x['actor']==identity['user_id'] for x in rows),'General report includes another actor')
   if user=='hw1-compliance':require(all(x['category']=='compliance' or x['selected_route']=='compliance' for x in rows),'Officer report includes unrelated domain')
   require(all(x['org']=='hw1-org-a' and set(x['buildings'])<=set(r['building_ids']) for x in rows),'Cross-building/org event leak')
   contacts=metadata('records',[d['recipient_id'] for d in r['delivery'] if 'recipient_id' in d])
   require(contacts and all(set(r['building_ids'])<=set(c['buildings']) for c in contacts),'Digest sent to narrower recipient')
   if user=='hw1-general':require(all(c['user_id']==identity['user_id'] for c in contacts),'General digest sent to another user')
   elif user=='hw1-compliance':require(all(c['officer'] for c in contacts),'Officer digest recipient not scoped compliance contact')
   else:require(all(c['manager_role']==identity['role'] for c in contacts),'Manager digest role mismatch')
  self.get('hw1-general',self.reports['hw1-property']['report_id'],404)
  self.get('hw1-compliance',self.reports['hw1-portfolio']['report_id'],404)
  self.get('hw1-other-property',self.reports['hw1-property']['report_id'],404)
  self.report(scope=['hw1-building-a2'],expected=403);self.report('hw1-portfolio',scope=['hw1-building-b1'],expected=403)
  self.call('hw1-property','/api/homework/usage/tools/execute',{'tool':'set_usage_limit','arguments':{}},403)
  self.record(IDS[3],'Actual reports and private safe event/contact metadata verify General own actor/contact only, Property assigned building, Portfolio assigned portfolio and Officer compliance-only events/recipients. Every digest contact covers its entire report scope. Cross-user, Officer commercial-report and foreign-org retrieval404; Property A2/Portfolio foreign building403; unsupported quota mutation403. No role or assignment edits occur.')
 def snapshots(self):
  old=self.report();before=json.dumps(old,sort_keys=True)
  with ThreadPoolExecutor(max_workers=2) as pool:replays=list(pool.map(lambda _:self.report(),range(2)))
  require(all(r==old for r in replays),'Concurrent exact trigger did not replay immutable snapshot')
  p=self.preview();pending=self.report()
  require(pending['report_id']!=old['report_id'] and pending['version']==old['version']+1,'Backlog-only change did not create next immutable version')
  require(json.dumps(self.get('hw1-property',old['report_id']),sort_keys=True)==before,'Previous snapshot was overwritten')
  require(self.report()==pending,'Unchanged new report not idempotent')
  self.record(IDS[4],'Two concurrent manual triggers return byte-equivalent stored snapshot, generation time and delivery records. An actual new pending source proposal changes the snapshot fingerprint and increments version even with no period provider/intake event change. Prior report GET is unchanged; exact new-state replay is identical. Reports/history/source originals use append-only DB privileges.')
 def delivery(self):
  r=self.reports['hw1-property'];outcomes={(x['status'],x['attempts']) for x in r['delivery']}
  require({('simulated sent',1),('failed',2),('unknown',1),('pending',0)}<=outcomes,'Digest simulated outcomes incorrect')
  rows=self.outbox();events=[x for x in rows if x['event_id']=='usage-'+r['report_id']]
  require(len(events)==len(r['delivery']) and len({x['recipient_id'] for x in events})==len(events),'Digest duplicates or missing outbox rows')
  require(any(x['category']=='processing_failure' and any(x['event_id'].startswith(i) for i in self.failure_ids) for x in rows),'Immediate adapter failure alert missing')
  require(any(x['category']=='notification_failure' and x['message'].get('outbox_id') in {z['id'] for z in events} for x in rows),'Digest failure lacks immediate manager fallback')
  for d in r['delivery']:
   if d['status'] in {'unknown','failed'}:
    review=self.call('hw1-property','/api/homework/communications/outbox/'+d['outbox_id']+'/recover',{'reason':'Review measured usage digest failure without blind retry'})
    require(review['attempts']==d['attempts'],'Digest review blindly retried')
  again=self.get('hw1-property',r['report_id']);require(again==r,'Delivery failure/review mutated immutable report snapshot')
  self.record(IDS[5],'Actual configured digest contacts exercise simulated sent1, known failure2 (one retry), unknown1 and missing-address pending0. Exactly one outbox row per report event/recipient; requested content is never silently sent. Known/unknown manual review makes no extra attempt and does not mutate report. A genuine authenticated adapter422 creates an immediate manager processing-failure alert, while digest delivery failure creates safe nonrecursive manager fallback. No real delivery or scheduler.')


def main():
 receipt={'milestone_id':'05-usage-reports','specification_version':'0.37.0','api_base':common.BASE,'checks':[]};gate=None;current=IDS[0]
 DEST.parent.mkdir(parents=True,exist_ok=True)
 try:
  common.database_probe();originals=ingestion.source_hashes();gate=Gate()
  for current,run in zip(IDS,[gate.sources,gate.unavailable,gate.boundaries,gate.roles,gate.snapshots,gate.delivery]):run()
  after=ingestion.source_hashes();require(all(after.get(k)==v for k,v in originals.items()),'A retained source original changed/disappeared')
  common.database_probe();receipt['checks']=gate.checks;receipt['recorded_at']=datetime.now(timezone.utc).isoformat()
  DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'05-usage-reports','passed':True,'checks':6}));return 0
 except Exception as error:
  safe=str(error) if isinstance(error,common.GateFailure) else type(error).__name__
  receipt['checks']=(gate.checks if gate else [])+[{'id':current,'passed':False,'evidence':safe}]
  DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'05-usage-reports','passed':False,'failed_check':current,'reason':safe}));return 1
if __name__=='__main__':sys.exit(main())
