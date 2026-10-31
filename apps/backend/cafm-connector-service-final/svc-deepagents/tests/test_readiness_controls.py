"""Prevent activation helpers from accepting unrelated containers or public bindings."""
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[5]
# Resolve through repository marker without depending on the test runner's cwd.
while not (ROOT/'specifications.md').exists():ROOT=ROOT.parent
s=importlib.util.spec_from_file_location('readiness',ROOT/'homework/module-1/readiness.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def row(project='hoistra-hw1',ip='127.0.0.1',port='18008'):
 return {'Config':{'Labels':{'com.docker.compose.project':project}},'HostConfig':{'PortBindings':{'8008/tcp':[{'HostIp':ip,'HostPort':port}]}}}
def test_activation_requires_exact_isolated_project_and_loopback_binding():
 m.validate_container(row(),18008)
 for bad in [row(project='cartwheel'),row(ip='0.0.0.0'),row(port='8008')]:
  with pytest.raises(m.ReadinessFailure):m.validate_container(bad,18008)
def test_private_rollback_snapshot_is_exclusive_and_owner_only(tmp_path):
 path=tmp_path/'snapshot.json';m.private_write(path,{'safe':'fixture'})
 assert path.stat().st_mode & 0o077==0
 with pytest.raises(FileExistsError):m.private_write(path,{'overwrite':'denied'})

@pytest.mark.asyncio
async def test_expiry_drain_releases_locks_per_certificate_instead_of_per_org(monkeypatch):
 from contextlib import asynccontextmanager
 from types import SimpleNamespace
 from src.services.compliance_hw1.actions import emit_expiry_notifications
 from src.services.compliance_hw1 import reads,actions
 from sqlalchemy.exc import SQLAlchemyError
 from src.api.homework_identity import TestPrincipal as Principal
 rows=[{'id':'cert-'+str(n),'source_id':'source-'+str(n),'expiry_label':'critical','expiry_date':'2026-10-27',
        'assessment_date':'2026-10-26','action_window':True} for n in range(3)]
 async def certificates(*args):return rows
 monkeypatch.setattr(reads,'certificates',certificates)
 committed=[];notifications=[]
 class Repo:
  def __init__(self):self.locks=0;self.session=self
  async def execute(self,*args,**kwargs):
   self.locks+=1
   if self.locks>8:raise SQLAlchemyError('independent bounded lock-budget fixture')
  async def get(self,table,identifier):return {'id':identifier,'buildings':['b1'],'data':{'source_id':'source-'+identifier[-1]}}
  async def list(self,*args):return []
  async def audit(self,*args):pass
 class Store:
  @asynccontextmanager
  async def transaction(self,*args,**kwargs):
   repo=Repo();yield repo;committed.append(repo.locks)
 async def notify(repo,building,event,*args):
  for _ in range(4):await repo.execute('fixture recipient advisory lock')
  notifications.append(event)
 monkeypatch.setattr(actions,'notify_managers',notify)
 p=Principal('p','property_manager','org1',('b1',))
 result=await emit_expiry_notifications(Store(),p)
 assert result['status']=='completed' and len(result['alert_stages'])==3
 assert len(notifications)==3 and max(committed)==5

@pytest.mark.asyncio
async def test_expiry_drain_reports_partial_processing_when_later_event_fails(monkeypatch):
 from contextlib import asynccontextmanager
 from src.services.compliance_hw1.actions import emit_expiry_notifications
 from src.services.compliance_hw1 import reads,actions
 from sqlalchemy.exc import SQLAlchemyError
 from fastapi import HTTPException
 from src.api.homework_identity import TestPrincipal as Principal
 rows=[{'id':str(n),'source_id':str(n),'expiry_label':'critical','expiry_date':'2026-10-27',
        'assessment_date':'2026-10-26','action_window':True} for n in range(2)]
 async def certificates(*args):return rows
 monkeypatch.setattr(reads,'certificates',certificates);notified=[]
 class Repo:
  @property
  def session(self):return self
  async def execute(self,*args,**kwargs):pass
  async def get(self,table,identifier):
   if identifier=='1':raise SQLAlchemyError('fixture event outage')
   return {'buildings':['b1'],'data':{'source_id':identifier}}
  async def list(self,*args):return []
 class Store:
  @asynccontextmanager
  async def transaction(self,*args,**kwargs):yield Repo()
 async def notify(repo,building,event,*args):notified.append(event)
 monkeypatch.setattr(actions,'notify_managers',notify)
 with pytest.raises(HTTPException) as error:await emit_expiry_notifications(Store(),Principal('p','property_manager','org1',('b1',)))
 assert error.value.status_code==503 and 'partially processed' in str(error.value.detail) and len(notified)==1

@pytest.mark.asyncio
async def test_real_outbox_sql_count_page_and_role_projection_agree(isolated_store):
 from src.services.compliance_hw1.store import runtime_store
 from src.api.homework_identity import TestPrincipal as Principal
 from src.services.compliance_hw1.reads import visible
 store=isolated_store
 for role,user,scope in [('general_user','hw1-general',['hw1-building-a1']),
                         ('property_manager','hw1-property',['hw1-building-a1']),
                         ('compliance_officer','hw1-compliance',['hw1-building-a1'])]:
  p=Principal(user,role,'hw1-org-a',tuple(scope))
  async with store.transaction(p,repeatable=True) as repo:
   page,count,issues=await repo.outbox_page(scope,2,0)
   # Independent old projection over the small matching SQL-only ID population:
   # each returned body must satisfy the same role/content/scope policy.
   assert len(page)<=2 and count>=len(page)
   assert all(visible(p,r,scope,full=True) for r in page)
   if role=='general_user':assert all(r['data']['actor']==user for r in page) and issues==[]
   if role=='compliance_officer':assert all(r['data']['category']=='compliance' for r in page)
   nextpage,same_count,_=await repo.outbox_page(scope,2,2)
   assert same_count==count and not ({r['id'] for r in page}&{r['id'] for r in nextpage})
 await store.engine.dispose()


def test_compose_does_not_inherit_controller_host_database_overrides(monkeypatch):
 for key in ['HW1_READ_DB_URL','HW1_COMPLIANCE_DB_URL','HW1_POSTGRES_PASSWORD']:
  monkeypatch.setenv(key,'host-test-only-fixture')
 monkeypatch.setenv('PATH','/test-preserved-path')
 env=m.compose_environment()
 assert all(key not in env for key in ['HW1_READ_DB_URL','HW1_COMPLIANCE_DB_URL','HW1_POSTGRES_PASSWORD'])
 assert env['PATH']=='/test-preserved-path'
 # Sanitizing a child process must not mutate the test runner environment.
 assert m.os.environ['HW1_READ_DB_URL']=='host-test-only-fixture'


@pytest.mark.asyncio
async def test_usage_snapshot_retains_inputs_without_loading_notification_history(isolated_store):
 from src.services.compliance_hw1.store import runtime_store
 from src.api.homework_identity import TestPrincipal as Principal
 from uuid import uuid4
 store=isolated_store;p=Principal('hw1-property','property_manager','hw1-org-a',('hw1-building-a1',))
 ids={kind:'readiness-filter-'+uuid4().hex for kind in ['usage_failure','usage_report','notification_issue']}
 # Append fictional evidence; never remove or rewrite source/history fixtures.
 async with store.transaction(p) as repo:
  for kind,identifier in ids.items():
   await repo.insert('history',kind,list(p.building_ids),{'fixture':'readiness usage snapshot filter'},identifier)
 async with store.transaction(p,repeatable=True) as repo:
  snapshot=await repo.usage_snapshot(list(p.building_ids))
  retained={r['id'] for r in snapshot['history']}
  assert ids['usage_failure'] in retained and ids['usage_report'] in retained
  assert ids['notification_issue'] not in retained
  # Filtering the adapter must not delete or hide the original on its authorized path.
  assert (await repo.get('history',ids['notification_issue']))['data']['fixture']=='readiness usage snapshot filter'


@pytest.mark.asyncio
async def test_certificate_event_recheck_reads_only_processed_markers():
 from contextlib import asynccontextmanager
 from src.services.compliance_hw1.actions import process_events
 from src.api.homework_identity import TestPrincipal as Principal
 class Repo:
  def __init__(self):self.session=self;self.calls=0
  async def execute(self,*args,**kwargs):pass
  async def list(self,table,kind=None):
   if table=='events':return [{'id':'fixture-already-processed','buildings':['b1']}]
   assert table=='history' and kind=='event_processed'
   self.calls+=1
   # A competing worker commits its marker before this worker gets the event lock.
   return [] if self.calls==1 else [{'kind':'event_processed','data':{'event_id':'fixture-already-processed'}}]
 repo=Repo()
 class Store:
  @asynccontextmanager
  async def transaction(self,*args,**kwargs):yield repo
 result=await process_events(Store(),Principal('p','property_manager','org1',('b1',)))
 assert result['processed_events']==[] and repo.calls==2


def test_retained_outbox_history_navigation_keeps_bounded_pages():
 from src.services.communications_hw1.contracts import Query
 from src.services.compliance_hw1.contracts import Query as ReadQuery
 from pydantic import ValidationError
 for cls,scope in [(Query,{'building_id':'b1'}),(ReadQuery,{'building_ids':['b1']})]:
  assert cls(**scope,offset=10100,limit=100).offset==10100
  with pytest.raises(ValidationError):cls(**scope,offset=10100,limit=101)
  with pytest.raises(ValidationError):cls(**scope,offset=-1)

@pytest.mark.asyncio
async def test_indexed_single_building_key_preserves_containment_and_duplicates(isolated_store):
 from sqlalchemy import text
 from src.services.compliance_hw1.store import runtime_store
 from src.api.homework_identity import TestPrincipal as Principal
 store=isolated_store
 async with store.transaction(Principal('hw1-property','property_manager','hw1-org-a',('hw1-building-a1',))) as repo:
  for buildings,expected in [(['a'],True),(['a','a'],True),(['a','b'],False),([],False),(['b'],False),(['a',None],False)]:
   row=(await repo.session.execute(text("""SELECT
    cardinality(CAST(:b AS text[]))>0 AND CAST(:b AS text[]) <@ ARRAY['a']::text[] AS original,
    cardinality(CAST(:b AS text[]))>0 AND CAST(:b AS text[]) <@ ARRAY['a']::text[]
      AND hw1_compliance.scope_fingerprint(CAST(:b AS text[]))=hw1_compliance.scope_fingerprint(ARRAY['a']::text[]) AS indexed"""),{'b':buildings})).one()
   assert row.original is expected and row.indexed is expected
  valid=(await repo.session.execute(text("""SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
   JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hw1_compliance'
   AND c.relname IN ('history_notification_scope_fingerprint','outbox_visible_scope_fingerprint')
   AND i.indisvalid AND i.indisready"""))).scalar_one()
  assert valid==2


@pytest.mark.asyncio
async def test_rls_app_role_can_use_scope_fingerprint_index_without_bypassing_policy(isolated_store):
 import json
 from sqlalchemy import text
 from src.services.compliance_hw1.store import runtime_store
 from src.api.homework_identity import TestPrincipal as Principal
 store=isolated_store
 # Prove the foreign fixture exists under its own trusted organization context,
 # then prove the same app role cannot see it under organization A.
 async with store.transaction(Principal('hw1-other-property','property_manager','hw1-org-b',('hw1-building-b1',))) as repo:
  fixture=(await repo.session.execute(text("SELECT count(*) FROM hw1_compliance.records WHERE org='hw1-org-b' AND id='hw1-building-b1'"))).scalar_one()
  assert fixture==1
 async with store.transaction(Principal('hw1-property','property_manager','hw1-org-a',('hw1-building-a1',))) as repo:
  row=(await repo.session.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"))).one()
  assert not row.rolsuper and not row.rolbypassrls
  plan=(await repo.session.execute(text("""EXPLAIN (FORMAT JSON) SELECT data FROM hw1_compliance.history
   WHERE org='hw1-org-a' AND kind='notification_issue' AND cardinality(buildings)>0
   AND buildings <@ ARRAY['hw1-building-a1']::text[]
   AND hw1_compliance.scope_fingerprint(buildings)=hw1_compliance.scope_fingerprint(ARRAY['hw1-building-a1']::text[]) ORDER BY id"""))).scalar_one()
  assert 'history_notification_scope_fingerprint' in json.dumps(plan)
  # The helper must never provide foreign-org row access.
  count=(await repo.session.execute(text("SELECT count(*) FROM hw1_compliance.records WHERE org='hw1-org-b' AND id='hw1-building-b1'"))).scalar_one()
  assert count==0


import pytest_asyncio
@pytest_asyncio.fixture
async def isolated_store():
 # pytest gives each test a separate event loop; never reuse the app singleton's
 # pooled asyncpg connections across loops, and close every owned connection.
 from src.services.compliance_hw1.store import Store
 store=Store(m.os.environ['HW1_COMPLIANCE_DB_URL'])
 try:yield store
 finally:await store.engine.dispose()

@pytest.mark.asyncio
async def test_failure_notifications_share_one_contact_snapshot_preserving_every_building(monkeypatch):
 from contextlib import asynccontextmanager
 from src.services.usage_hw1 import events
 from src.api.homework_identity import TestPrincipal as Principal
 calls=[];notices=[];rows=[{'id':'fixture-contact'}]
 class Repo:
  def __init__(self):self.session=self
  async def execute(self,*args,**kwargs):pass
  async def list(self,table,kind):
   calls.append((table,kind));return rows if table=='records' else []
  async def get(self,*args):
   from fastapi import HTTPException
   raise HTTPException(404,'fixture not yet recorded')
  async def insert(self,*args):pass
 class Store:
  @asynccontextmanager
  async def transaction(self,*args):yield Repo()
 async def notify(repo,building,event,message,category,contacts=None):
  assert contacts is rows
  notices.append((building,category))
 monkeypatch.setenv('HW1_USAGE_ENABLED','true')
 monkeypatch.setattr(events,'runtime_store',lambda:Store())
 monkeypatch.setattr(events,'notify_managers',notify)
 buildings=tuple('fixture-building-'+str(i) for i in range(100))
 await events.record_failure(Principal('p','portfolio_manager','org',buildings),'fixture-request','energy',422)
 assert calls.count(('records','contact'))==1
 assert notices==[(b,'processing_failure') for b in buildings]

@pytest.mark.asyncio
async def test_failure_delivery_releases_locks_and_resumes_only_uncommitted_stages(monkeypatch):
 from contextlib import asynccontextmanager
 from copy import deepcopy
 from fastapi import HTTPException
 from sqlalchemy.exc import SQLAlchemyError
 from src.services.usage_hw1 import events
 from src.api.homework_identity import TestPrincipal as Principal
 rows={};committed=[];outage=[True]
 class Repo:
  def __init__(self):self.session=self;self.locks=0
  async def execute(self,*args,**kwargs):
   self.locks+=1
   if self.locks>5:raise SQLAlchemyError('independent five-lock transaction budget')
  async def get(self,table,identifier):
   if identifier not in rows:raise HTTPException(404,'fixture absent')
   return rows[identifier]
  async def list(self,*args):return []
  async def insert(self,table,kind,buildings,data,identifier=None):
   assert identifier not in rows
   rows[identifier]={'kind':kind,'buildings':buildings,'data':data}
 class Store:
  @asynccontextmanager
  async def transaction(self,*args):
   before=deepcopy(rows);repo=Repo()
   try:
    yield repo
    committed.append(repo.locks)
   except Exception:
    rows.clear();rows.update(before);raise
 async def notify(repo,building,event,message,category,contacts=None):
  for _ in range(4):await repo.execute('fixture recipient lock')
  if building=='b2' and outage[0]:
   outage[0]=False;raise SQLAlchemyError('fixture later-stage outage')
  await repo.insert('outbox','fixture_notice',[building],{},'notice-'+building)
 monkeypatch.setenv('HW1_USAGE_ENABLED','true')
 monkeypatch.setattr(events,'runtime_store',lambda:Store())
 monkeypatch.setattr(events,'notify_managers',notify)
 p=Principal('p','portfolio_manager','org',('b1','b2','b3'))
 with pytest.raises(SQLAlchemyError):await events.record_failure(p,'fixture-resume','energy',422)
 assert 'notice-b1' in rows and 'notice-b2' not in rows
 await events.record_failure(p,'fixture-resume','energy',422)
 await events.record_failure(p,'fixture-resume','energy',422)
 assert {k for k in rows if k.startswith('notice-')}=={'notice-b1','notice-b2','notice-b3'}
 assert sum(r['kind']=='usage_failure' for r in rows.values())==1
 assert sum(r['kind']=='usage_failure_notification_processed' for r in rows.values())==3
 assert max(committed)==5

def test_explicit_report_intent_requires_one_valid_named_period():
 from src.agents.homework_usage_agent import explicit_report
 assert explicit_report('Generate usage_weekly_report period_start 2026-02-02')=={'tool':'usage_weekly_report','period_start':'2026-02-02'}
 assert explicit_report('usage_monthly_report period_start 2026-02-01')['period_start']=='2026-02-01'
 for question in ['usage_weekly_report','usage_weekly_report 2026-02-03',
                  'usage_monthly_report 2026-02-02','usage_weekly_report 2026-02-02 or 2026-02-09',
                  'usage_weekly_report usage_monthly_report 2026-02-02','usage_weekly_report 2026-99-99',
                  'Explain usage_weekly_report period_start 2026-02-02',
                  'Do not generate usage_weekly_report period_start 2026-02-02',
                  'Generate usage_weekly_report period_start 2026-02-02 but do not send']:
  assert explicit_report(question) is None

@pytest.mark.asyncio
@pytest.mark.parametrize('model_period,expected_calls,message',[
 ('2026-02-02',1,'Generate usage_weekly_report period_start 2026-02-02'),
 ('2026-02-09',0,'Generate usage_weekly_report period_start 2026-02-02'),
 ('2026-02-02',0,'Do not generate usage_weekly_report period_start 2026-02-02'),
 ('2026-02-02',0,'Explain usage_weekly_report period_start 2026-02-02')])
async def test_named_manual_usage_report_preserves_exact_period_and_provider_call(monkeypatch,model_period,expected_calls,message):
 from types import SimpleNamespace
 from langchain_core.messages import AIMessage
 from src.agents import homework_usage_agent as agent
 from src.services.usage_hw1 import tools,service as usage_service
 from test_communications_hoi import fixture,P
 svc,repo=fixture();executed=[];choices=[]
 async def execute(principal,body,request_id,service):
  executed.append(body);return {'result':{'period_start':body['arguments']['period_start'],'scope':'fictional assigned scope'}}
 async def measurement(*args,**kwargs):pass
 monkeypatch.setattr(tools,'execute',execute);monkeypatch.setattr(usage_service,'record_measurement',measurement)
 class Model:
  responses=0
  def bind_tools(self,tools,**kwargs):choices.append(kwargs.get('tool_choice'));return self
  async def ainvoke(self,messages):
   self.responses+=1
   return (AIMessage(content='',tool_calls=[{'name':'usage_weekly_report','args':{'period_start':model_period},'id':'fictional-call'}])
    if self.responses==1 else AIMessage(content='Verified result received'))
 body=SimpleNamespace(message=message,conversation_id=None)
 result=await agent.ask(P,body,svc,Model(),'fictional-named-period')
 if agent.generation_declined(message):assert all(choice is None for choice in choices)
 else:assert {'type':'function','function':{'name':'usage_weekly_report'}} in choices
 assert len(executed)==expected_calls
 assert result['provider_execution']['successful_responses']==2
 assert result['outcome']==('answered' if expected_calls else 'failed')
 if not expected_calls:assert result['tool_calls'][0]['status']==422

@pytest.mark.asyncio
async def test_cursor_page_matches_offset_projection_without_recount_or_issue_reloads(isolated_store):
 from src.api.homework_identity import TestPrincipal as Principal
 from src.services.compliance_hw1.reads import visible
 from fastapi import HTTPException
 p=Principal('hw1-property','property_manager','hw1-org-a',('hw1-building-a1',))
 async with isolated_store.transaction(p,repeatable=True) as repo:
  first,total,_=await repo.outbox_page(list(p.building_ids),2,0,include_issues=False)
  assert total>4 and len(first)==2
  cursor,count,issues=await repo.outbox_page(list(p.building_ids),2,0,after_id=first[-1]['id'],include_issues=False)
  offset,same_total,_=await repo.outbox_page(list(p.building_ids),2,2,include_issues=False)
  assert count is None and issues==[] and same_total==total
  assert [r['id'] for r in cursor[:2]]==[r['id'] for r in offset]
  assert len(cursor)==3  # One bounded sentinel proves another page exists.
  assert all(visible(p,r,list(p.building_ids),full=True) for r in cursor)
  assert all(r['id']>first[-1]['id'] for r in cursor)
  tail,count,issues=await repo.outbox_page(list(p.building_ids),2,0,after_id='zzzzzz',include_issues=False)
  assert tail==[] and count is None and issues==[]
  with pytest.raises(HTTPException):await repo.outbox_page(list(p.building_ids),2,1,after_id=first[-1]['id'])

def test_cursor_offset_ambiguity_is_rejected_by_both_typed_interfaces():
 from src.services.communications_hw1.contracts import Query as CommunicationsQuery
 from src.services.compliance_hw1.contracts import Query as ComplianceQuery
 from pydantic import ValidationError
 for cls,scope in [(CommunicationsQuery,{'building_id':'b1'}),(ComplianceQuery,{'building_ids':['b1']})]:
  with pytest.raises(ValidationError):cls(**scope,after_id='fixture',offset=1)
  with pytest.raises(ValidationError):cls(**scope,after_id='fixture',limit=101)

@pytest.mark.asyncio
async def test_scope_index_fingerprint_has_fixed_width_for_large_arrays(isolated_store):
 from sqlalchemy import text
 from src.api.homework_identity import TestPrincipal as Principal
 p=Principal('hw1-property','property_manager','hw1-org-a',('hw1-building-a1',))
 async with isolated_store.transaction(p) as repo:
  row=(await repo.session.execute(text("""SELECT
   length(hw1_compliance.scope_fingerprint(ARRAY(SELECT 'fictional-'||n FROM generate_series(1,2000) n))) AS width,
   hw1_compliance.scope_fingerprint(ARRAY['b','a','a'])=hw1_compliance.scope_fingerprint(ARRAY['a','b']) AS canonical"""))).one()
  assert row.width==32 and row.canonical is True

@pytest.mark.asyncio
async def test_immediate_officer_failure_uses_alert_contacts_not_digest_only_contacts(monkeypatch):
 from contextlib import asynccontextmanager
 from fastapi import HTTPException
 from src.services.usage_hw1 import events
 from src.api.homework_identity import TestPrincipal as Principal
 contacts=[{'id':'alert-officer','data':{'officer':True}},
           {'id':'digest-officer','data':{'officer':True,'usage_only':True,'purposes':['usage_digest']}},
           {'id':'manager','data':{'manager_role':'property_manager'}}]
 sent=[];managers=[]
 class Repo:
  def __init__(self):self.session=self
  async def execute(self,*args,**kwargs):pass
  async def get(self,*args):raise HTTPException(404,'fictional new event')
  async def list(self,*args):return contacts
  async def insert(self,*args):pass
 class Store:
  @asynccontextmanager
  async def transaction(self,*args):yield Repo()
 async def notify(repo,building,*args,**kwargs):managers.append(building)
 async def mail(repo,event,buildings,recipient,*args,**kwargs):sent.append(recipient)
 monkeypatch.setenv('HW1_USAGE_ENABLED','true')
 monkeypatch.setattr(events,'runtime_store',lambda:Store())
 monkeypatch.setattr(events,'notify_managers',notify);monkeypatch.setattr(events,'record_message',mail)
 await events.record_failure(Principal('officer','compliance_officer','org',('b1','b2')),'purpose-test','compliance',403)
 assert sent==['alert-officer'] and managers==['b1','b2']
