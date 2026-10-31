"""Independent Case 1 answer and execution-evidence regressions."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.messages import AIMessage
from fastapi import HTTPException
from src.api.homework_identity import TestPrincipal as Principal
from src.agents.energy_response import render_eui
from src.agents.homework_energy_agent import ask as energy_ask, explicit_energy_request
from src.agents.homework_router import actual_tools, ask as unified_ask
from src.services.trace_hw1.energy import observation, export_tools
from src.services.trace_hw1.service import TraceService
from test_ingestion_corrections import MemoryStore

P=Principal('case1-user','property_manager','hw1-org-a',('hw1-building-a1',))
ARGS={'building_id':'hw1-building-a1','meter_id':'hw1-energy-m2-condition','period_start':'2026-10-01','period_end':'2026-10-31'}
QUESTION='Calculate Energy EUI for building_id hw1-building-a1, meter_id hw1-energy-m2-condition, period_start 2026-10-01, period_end 2026-10-31.'
DATA={'building_id':ARGS['building_id'],'meter_id':ARGS['meter_id'],'period_start':ARGS['period_start'],'period_end':ARGS['period_end'],
      'period_days_inclusive':31,'reading_count':1,'consumption_kwh':'1200','floor_area_m2':'100','eui_available':True,
      'eui_kwh_per_m2_period':'12','annualized_eui_kwh_per_m2_year':'141.29032258064516129','annualization_basis':'inclusive_period_days',
      'benchmark_available':True,'benchmark_label':'General office','benchmark_kwh_per_m2_year':'95','deviation_pct':'48.73',
      'benchmark_excess_kwh':'393.150684931506849','tariff_gbp_per_kwh':'0.20','financial_excess_gbp':'78.63','financial_excess_available':True,
      'coverage':{'basis':'source-defined period totals','complete':None},'freshness':'2026-10-01T00:00:00+00:00',
      'source_ids':['fixture-reading','fixture-profile'],'source_versions':{'fixture-reading':1,'fixture-profile':2}}


def memory():
    store=MemoryStore();store.repo.tables.update(sessions={},events={},outbox={});store.audit=store.repo.audit
    return store


def test_answer_explains_independently_expected_arithmetic_and_evidence_without_mutating_result():
    original=deepcopy(DATA);answer=render_eui(DATA,fictional=True)
    for text in ['Fictional Homework 1','31 inclusive days','1200 kWh ÷ 100 m² = 12','12 × 365 ÷ 31 = 141.29',
                 'not a measured annual total','95 × 100 × 31 ÷ 365','393.15','£0.2/kWh','£78.63',
                 'Hourly completeness is unknown','source-defined period totals','fixture-reading (version 1)','fixture-profile (version 2)']:
        assert text in answer
    assert DATA==original and '141.29032258064516129' not in answer


@pytest.mark.parametrize('removed',['floor_area_m2','consumption_kwh'])
def test_missing_required_eui_input_never_looks_like_zero_or_success(removed):
    data=deepcopy(DATA);data.pop(removed);data['eui_available']=False
    answer=render_eui(data)
    assert 'EUI unavailable' in answer and 'Period EUI:' not in answer and 'Fictional' not in answer


def test_missing_tariff_annualization_and_sources_are_explicit():
    data=deepcopy(DATA)
    for key in ['tariff_gbp_per_kwh','annualized_eui_kwh_per_m2_year','source_ids']:data.pop(key)
    answer=render_eui(data)
    assert 'Annualized EUI unavailable' in answer and 'Tariff-derived excess cost unavailable' in answer and 'Sources: unavailable' in answer
    assert '£78.63' not in answer


def test_verified_zero_consumption_is_not_treated_as_missing():
    data=deepcopy(DATA);data.update(consumption_kwh='0',eui_kwh_per_m2_period='0',annualized_eui_kwh_per_m2_year='0')
    assert 'Period EUI: 0 kWh ÷ 100 m² = 0' in render_eui(data)


@pytest.mark.parametrize('args,succeeded,role',[(ARGS,False,'property_manager'),({**ARGS,'building_id':'foreign'},True,'property_manager'),
    ({**ARGS,'secret':'never-export'},True,'property_manager'),(ARGS,True,'general_user'),(ARGS,True,'compliance_officer')])
def test_trace_arguments_are_withheld_on_failed_unscoped_or_ungranted_reads(args,succeeded,role):
    principal=Principal('u',role,'hw1-org-a',P.building_ids)
    item=observation(principal,'energy_eui',args,'model',succeeded)
    assert 'arguments' not in item and 'never-export' not in str(item)


def test_exact_arguments_are_copied_without_inferred_defaults_and_export_rechecks_scope():
    item=observation(P,'energy_eui',ARGS,'model',True)
    assert item['arguments']==ARGS and 'section_id' not in item['arguments']
    assert item['arguments'] is not ARGS
    assert actual_tools({'tool_calls':[{**item,'result':DATA}]},P)[0]==item
    narrower=Principal(P.user_id,P.role,P.organization_id,('other',))
    assert 'arguments' not in export_tools(narrower,[{**item,'result':DATA,'body':'private'}])[0]
    assert 'result' not in export_tools(P,[{**item,'result':DATA}])[0]


def test_fallback_retains_the_explicit_meter():
    assert explicit_energy_request(QUESTION)==('energy_eui',ARGS)


@pytest.mark.parametrize('origin',['model','fallback'])
async def test_real_agent_boundary_records_exact_execution_and_origin(origin):
    store=memory();service=SimpleNamespace(store=store,execute=AsyncMock(return_value={'result':deepcopy(DATA)}))
    class Model:
        count=0
        def bind_tools(self,tools):return self
        async def ainvoke(self,messages):
            self.count+=1
            if origin=='model' and self.count==1:
                return AIMessage(content='',tool_calls=[{'name':'energy_eui','args':deepcopy(ARGS),'id':'read1'}])
            return AIMessage(content='This prose is intentionally not the final rendered answer.')
    with patch.dict('os.environ',{'HW1_USAGE_ENABLED':'false'}):
        result=await energy_ask(P,SimpleNamespace(message=QUESTION,conversation_id=None),service,Model(),'r-case1')
    assert service.execute.await_args.args[2]==ARGS
    assert result['tool_calls'][0]['arguments']==ARGS
    assert result['tool_calls'][0]['execution_origin']==origin
    assert '1200 kWh ÷ 100 m² = 12' in result['answer']


@pytest.mark.parametrize('failed',[False,True])
async def test_unified_success_and_failure_traces_record_actual_unified_policy(failed):
    store=memory();request=SimpleNamespace(state=SimpleNamespace(test_principal=P,request_id='r'),app=SimpleNamespace(state=SimpleNamespace()))
    body=SimpleNamespace(message=QUESTION,conversation_id=None)
    dispatch=AsyncMock(side_effect=HTTPException(502,'failed')) if failed else AsyncMock(return_value={
        'selected_route':'energy','outcome':'answered','answer':'value','tool_calls':[{**observation(P,'energy_eui',ARGS,'model',True),'result':DATA}]})
    with patch('src.services.compliance_hw1.store.runtime_store',return_value=store),patch('src.agents.homework_router._dispatch',dispatch),patch('src.agents.answer_synthesis.create_answer_model',return_value=object()),patch('src.agents.answer_synthesis.generate_answer',AsyncMock(return_value=('value',{'status':'generated','successful_responses':0}))):
        if failed:
            with pytest.raises(HTTPException):await unified_ask(request,body)
        else:await unified_ask(request,body)
    rows=[row for row in store.repo.tables['history'].values() if row['kind']=='unified_trace']
    assert rows[-1]['data']['policy_version']=='hw1-unified-v1'
    if not failed:
        exported=await TraceService(store).export(P,rows[-1]['data']['conversation_id'])
        assert exported['traces'][0]['actual_tools'][0]['arguments']==ARGS
        assert exported['traces'][0]['policy_version']=='hw1-unified-v1'
        assert exported['conversation_content_included'] is False
