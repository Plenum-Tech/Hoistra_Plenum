"""Independent period, role, backlog/version and unsupported-metric regressions."""
from datetime import date
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_ingestion_corrections import MemoryStore
from src.api.homework_identity import TestPrincipal as Principal
from src.services.usage_hw1.service import UsageService,period,visible
from src.agents.usage_prompt import assemble

P=Principal('p','property_manager','org-1',('b-1',))
def service():
 s=MemoryStore();s.repo.principal=P;s.repo.tables.update(outbox={},sessions={},events={})
 s.repo.tables['records']['b-1']={'id':'b-1','kind':'building','org':'org-1','buildings':['b-1'],'data':{'scope_verified':True}}
 s.audit=s.repo.audit
 return UsageService(s),s.repo

def event(id,when,actor='p',scope=None,domain='energy',kind='usage_measurement'):
 return {'id':id,'kind':kind,'org':'org-1','buildings':scope or ['b-1'],'data':{
  'actor':actor,'category':domain,'domain':domain,'generated_at':when,'input_tokens':10,'output_tokens':2,'measurement_state':'measured'}}

async def test_independent_half_open_week_and_month_cross_utc_date():
 svc,r=service()
 for ident,t in [('before','2026-02-01T19:59:59Z'),('begin','2026-02-01T20:00:00Z'),('last','2026-02-08T19:59:59Z'),('after','2026-02-08T20:00:00Z')]:r.tables['history'][ident]=event(ident,t)
 weekly=await svc.weekly_report(P,date(2026,2,2))
 assert weekly['metrics']['provider_calls']==2 and set(weekly['source_ids'])=={'begin','last'}
 assert weekly['reporting_period']['start_utc']=='2026-02-01T20:00:00+00:00'
 assert (await svc.monthly_report(P,date(2026,2,1)))['metrics']['provider_calls']==4

async def test_backlog_alone_changes_snapshot_version_and_prior_is_immutable():
 svc,r=service();a=await svc.weekly_report(P,date(2026,2,2));original=deepcopy(a)
 r.tables['proposals']['pending']={'id':'pending','kind':'ingestion','org':'org-1','buildings':['b-1'],'data':{'status':'previewed','actor':'p','preview_hash':'a'}}
 b=await svc.weekly_report(P,date(2026,2,2))
 assert b['version']==a['version']+1 and b['report_id']!=a['report_id'] and b['metrics']['ingestion_backlog']==1
 assert await svc.retrieve(P,a['report_id'])==original
 assert await svc.weekly_report(P,date(2026,2,2))==b
 r.tables['proposals']['pending']['data']['status']='confirmed'
 assert (await svc.weekly_report(P,date(2026,2,2)))['metrics']['ingestion_backlog']==0

async def test_general_own_and_officer_compliance_only_sources_and_backlog():
 svc,r=service()
 r.tables['history']['own']=event('own','2026-02-02T00:00:00Z')
 r.tables['history']['other']=event('other','2026-02-02T00:00:00Z',actor='other')
 r.tables['history']['compliance']=event('compliance','2026-02-02T00:00:00Z',domain='compliance')
 g=Principal('p','general_user','org-1',('b-1',));co=Principal('c','compliance_officer','org-1',('b-1',))
 gr=await svc.weekly_report(g,date(2026,2,2));cr=await svc.weekly_report(co,date(2026,2,2))
 assert set(gr['source_ids'])=={'own','compliance'} and cr['source_ids']==['compliance']
 with pytest.raises(HTTPException):await svc.retrieve(co,gr['report_id'])
 with pytest.raises(HTTPException):await svc.retrieve(g,cr['report_id'])

async def test_no_scope_expansion_or_foreign_org_even_for_same_report_id():
 svc,r=service()
 with pytest.raises(HTTPException):await svc.weekly_report(P,date(2026,2,2),building_ids=['other'])
 report=await svc.weekly_report(P,date(2026,2,2))
 foreign=Principal('p','property_manager','other-org',('b-1',))
 with pytest.raises(HTTPException):await svc.retrieve(foreign,report['report_id'])

async def test_narrow_contact_cannot_receive_whole_portfolio():
 svc,r=service();p=Principal('p','portfolio_manager','org-1',('b-1','b-2'))
 r.tables['records']['contact']={'id':'contact','kind':'contact','org':'org-1','buildings':['b-1'],
  'data':{'scope_verified':True,'manager_role':'portfolio_manager','email':'x@fictional.invalid','purposes':['usage_digest']}}
 report=await svc.weekly_report(p,date(2026,2,2))
 assert report['delivery'][0]['status']=='pending' and not r.tables['outbox']

async def test_unsupported_metrics_never_turn_into_zero_and_source_freshness_is_observed():
 svc,r=service();r.tables['history']['receipt']=event('receipt','2026-02-02T01:00:00Z')
 report=await svc.weekly_report(P,date(2026,2,2))
 for k in ['active_users','sessions','subscription_charges','model_api_cost','configured_usage_limit','building_coverage_trend']:assert report['metrics'][k] is None
 assert report['source_freshness']=='2026-02-02 01:00:00+00:00'
 assert report['unavailable_metrics'] and report['backlog_as_of']!=report['source_freshness']

async def test_provider_partial_coverage_retains_nulls_and_observed_total():
 svc,r=service();r.tables['history']['good']=event('good','2026-02-02T01:00:00Z')
 bad=event('bad','2026-02-02T01:00:00Z');bad['data'].update(measurement_state='token_usage_unavailable',input_tokens=None,output_tokens=None)
 r.tables['history']['bad']=bad
 report=await svc.weekly_report(P,date(2026,2,2));t=report['metrics']['provider_token_usage']
 assert t['input_tokens']==10 and t['measured_calls']==1 and t['complete_for_provider_calls'] is False

@pytest.mark.parametrize('start,cadence,stop',[(date(2024,2,1),'monthly','2024-02-29'),(date(2026,12,1),'monthly','2026-12-31')])
def test_calendar_leap_and_year_boundaries(start,cadence,stop):assert period(start,cadence)[0]['end']==stop

def test_role_prompt_manifest_never_offers_raw_telemetry_or_billing_tools():
 for role in ['general_user','property_manager','portfolio_manager','compliance_officer']:
  _,m=assemble(Principal('p',role,'org-1',('b-1',)))
  assert m['bound_tools']==['usage_get_report','usage_monthly_report','usage_weekly_report']

async def test_new_recipient_configuration_versions_pending_snapshot_without_rewriting():
 svc,r=service();first=await svc.weekly_report(P,date(2026,2,2))
 r.tables['records']['contact']={'id':'contact','kind':'contact','org':'org-1','buildings':['b-1'],
  'data':{'scope_verified':True,'manager_role':'property_manager','email':'x@fictional.invalid','purposes':['usage_digest']}}
 second=await svc.weekly_report(P,date(2026,2,2))
 assert second['version']==first['version']+1 and second['delivery'][0]['status']=='simulated sent'
 assert (await svc.retrieve(P,first['report_id']))['delivery'][0]['status']=='pending'

async def test_duplicate_failure_trace_and_http_event_count_one_request():
 svc,r=service()
 for kind in ['trace','usage_failure']:
  e=event(kind,'2026-02-02T01:00:00Z',kind=kind);e['data'].update(request_id='same-request',outcome='failed')
  r.tables['history'][kind]=e
 report=await svc.weekly_report(P,date(2026,2,2))
 assert report['metrics']['processing_failures']==1
 assert set(report['source_ids'])=={'trace','usage_failure'}
