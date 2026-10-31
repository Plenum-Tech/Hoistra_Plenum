"""Unified registry, permission, trace and independent source compatibility regressions."""
import json,shutil
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from fastapi import HTTPException
from src.api.homework_identity import TestPrincipal as Principal
from src.agents.unified_prompt import ROOT,validate,augment,CONTEXT
from src.agents.homework_router import permitted,help_text,actual_tools,ask
from src.services.unified_hw1.compatibility import Compatibility,compare
from test_ingestion_corrections import MemoryStore

P=Principal('p','property_manager','org-1',('b-1',))

def test_registry_pins_all_seven_and_nonempty_instructions():
 m,text=validate();assert len(m['enabled_profiles'])==7 and 'mentally join' in text
 assert m['enabled_profiles']['reads']['max_tool_calls']==1

@pytest.mark.parametrize('change',['instruction','child_manifest','path','duplicate_key'])
def test_registry_detects_drift_and_unsafe_paths(tmp_path,change):
 root=tmp_path/'skills';shutil.copytree(ROOT,root)
 mpath=root/'unified-manifest.json';m=json.loads(mpath.read_text())
 if change=='instruction':(root/'orchestrator/unified.md').write_text('changed')
 elif change=='child_manifest':(root/'energy-manifest.json').write_text('{}')
 elif change=='path':m['profiles']['energy']['path']='../private';mpath.write_text(json.dumps(m))
 else:mpath.write_text(mpath.read_text().replace('"profile":','"profile":"duplicate", "profile":',1))
 with pytest.raises(HTTPException) as e:validate(root=root)
 assert e.value.status_code==503

def test_context_is_request_local_identity_bound_and_does_not_change_old_read_path():
 assert augment('unchanged',{'x':1},P)==('unchanged',{'x':1})
 token=CONTEXT.set({'actor':'other','role':P.role,'instructions':'test','registry':{},'utc_date':'2026-10-26'})
 try:
  with pytest.raises(HTTPException):augment('body',{},P)
 finally:CONTEXT.reset(token)


def test_role_clarification_never_offers_forbidden_domains():
 app=SimpleNamespace(state=SimpleNamespace(**{k:True for k in ['hw1_compliance_enabled','hw1_energy_enabled','hw1_contract_enabled','hw1_ingestion_enabled','hw1_communications_enabled','hw1_usage_enabled']}))
 co=Principal('c','compliance_officer','org-1',('b-1',));g=Principal('g','general_user','org-1',('b-1',))
 for p in [co,g]:
  assert not permitted(p,'energy') and not permitted(p,'contract_performance')
  assert 'Energy' not in help_text(p,app) and 'Contract Performance' not in help_text(p,app)
 assert not permitted(co,'communications','Show HOI Score')
 assert permitted(co,'compliance') and permitted(g,'reads')


def test_actual_trace_never_fabricates_tools_or_copies_untrusted_names():
 assert actual_tools({'answer':'I could call a tool'})==[]
 result=actual_tools({'tool_calls':[{'tool':'untrusted secret/name','error':'denied'},{'tool':'energy_eui','result':{}}]})
 assert result==[{'tool':'unregistered_tool','succeeded':False},{'tool':'energy_eui','succeeded':True}]


def source(id,**changes):
 return {'id':id,'kind':'unified_fixture','org':'org-1','buildings':['b-1'],'data':{'scope_verified':True,'version':1,
  'fields':{'meter_id':'meter','period_start':'2026-10-01','period_end':'2026-10-31','unit':'kWh',**changes}}}

@pytest.mark.parametrize('changes,expected',[({},True),({'unit':'m3'},False),({'period_end':'2026-09-30'},False),({'meter_id':'other'},False),({'unit':None},False)])
async def test_source_compatibility_uses_explicit_metadata_only(changes,expected):
 s=MemoryStore();s.audit=s.repo.audit;s.repo.tables['sources']={'a':source('a'),'b':source('b',**changes)}
 before=deepcopy(s.repo.tables['sources'])
 result=await compare(P,Compatibility(building_id='b-1',source_ids=['a','b']),s)
 assert result['compatible'] is expected and s.repo.tables['sources']==before
 if not expected:assert result['reasons']

async def test_foreign_or_mixed_source_and_nonmanager_comparison_denied():
 s=MemoryStore();s.audit=s.repo.audit;s.repo.tables['sources']={'a':source('a'),'b':source('b')}
 s.repo.tables['sources']['b']['org']='foreign'
 q=Compatibility(building_id='b-1',source_ids=['a','b'])
 with pytest.raises(HTTPException) as e:await compare(P,q,s)
 assert e.value.status_code==404
 for role in ['general_user','compliance_officer']:
  with pytest.raises(HTTPException) as e:await compare(Principal('x',role,'org-1',('b-1',)),q,s)
  assert e.value.status_code==403

async def test_clarification_creates_owned_session_and_foreign_continuation_denied():
 store=MemoryStore();store.repo.tables.update(sessions={},events={},outbox={});store.audit=store.repo.audit
 request=SimpleNamespace(state=SimpleNamespace(test_principal=P,request_id='r1'),app=SimpleNamespace(state=SimpleNamespace()))
 body=SimpleNamespace(message='Help',conversation_id=None)
 dispatched=AsyncMock(return_value={'answer':'Which permitted task?','outcome':'needs_clarification','selected_route':None,'actual_tools':[]})
 with patch('src.services.compliance_hw1.store.runtime_store',return_value=store),patch('src.agents.homework_router._dispatch',dispatched):
  result=await ask(request,body)
  assert result['conversation_id'] and result['actual_tools']==[]
  assert store.repo.tables['sessions'][result['conversation_id']]['data']['messages']
  request.state.test_principal=Principal('foreign','property_manager','org-1',('b-1',))
  with pytest.raises(HTTPException) as e:await ask(request,SimpleNamespace(message='Continue',conversation_id=result['conversation_id']))
  assert e.value.status_code==404 and dispatched.await_count==1

async def test_energy_planner_cannot_invoke_human_disposition_even_when_role_api_allows_it():
 from langchain_core.messages import AIMessage
 from src.agents.homework_energy_agent import ask as energy_ask
 store=MemoryStore();store.repo.tables.update(sessions={},events={},outbox={});store.audit=store.repo.audit
 class Model:
  def bind_tools(self,tools):return self
  async def ainvoke(self,messages):return AIMessage(content='',tool_calls=[{'name':'energy_anomaly_act','args':{},'id':'unsafe-call'}])
 service=SimpleNamespace(store=store,execute=AsyncMock())
 with patch.dict('os.environ',{'HW1_USAGE_ENABLED':'false'}):
  result=await energy_ask(P,SimpleNamespace(message='Try a human action',conversation_id=None),service,Model(),'malicious-tool-regression')
 assert service.execute.await_count==0
 assert result['outcome']=='failed' and 'energy_anomaly_act' not in result['prompt_metadata']['bound_tools']

async def test_assignment_scope_change_invalidates_old_conversation_before_more_content():
 from src.services.compliance_hw1.sessions import conversation
 store=MemoryStore();store.repo.tables['sessions']={}
 row=await conversation(store.repo,P)
 wider=Principal('p','property_manager','org-1',('b-1','b-2'))
 with pytest.raises(HTTPException) as error:await conversation(store.repo,wider,row['id'])
 assert error.value.status_code==409
