"""Independent arithmetic, evidence/disclosure and delivery-state regression cases."""
from copy import deepcopy
import json
import pytest
from fastapi import HTTPException
from test_ingestion_corrections import MemoryStore
from src.api.homework_identity import TestPrincipal as Principal
from src.services.communications_hw1.service import CommunicationsService,exact_preview
from src.services.communications_hw1.contracts import Preview,Confirmation
from src.services.compliance_hw1.hoi import calculate,building_score
from src.services.compliance_hw1.fixtures import dataset
from src.services.compliance_hw1.outbox import record_message,recover_message
from src.agents.communications_prompt import assemble

P=Principal('pm','property_manager','org-1',('b-1',))
G=Principal('g','general_user','org-1',('b-1',))
C=Principal('c','compliance_officer','org-1',('b-1',))

def fixture():
    store=MemoryStore();repo=store.repo;repo.principal=P;store.audit=repo.audit
    repo.tables.update(outbox={},events={},sessions={})
    def row(identifier,kind,data,buildings=None):
        r={'id':identifier,'kind':kind,'org':'org-1','buildings':buildings or ['b-1'],'data':{'scope_verified':True,**data}}
        repo.tables['records'][identifier]=r
        return r
    row('b-1','building',{'version':1})
    row('contact','contact',{'email':'team@fictional.invalid','contact_type':'team','purposes':['team_notification']})
    row('pm-contact','contact',{'email':'pm@fictional.invalid','contact_type':'team','manager_role':'property_manager','purposes':['team_notification']})
    return CommunicationsService(store),repo

def body(**changes):
    return Preview(building_id='b-1',entity_id='b-1',purpose='team_notification',contact_ids=['contact'],subject='Exact subject',body='Exact body',**changes)

async def test_exact_preview_revalidation_rejects_contact_change():
    svc,repo=fixture();p=(await svc.execute(P,'communications_preview',body().model_dump()))['result']
    assert not repo.tables['outbox']
    repo.tables['records']['contact']['data']['email']='changed@fictional.invalid'
    with pytest.raises(HTTPException) as e:
        await svc.confirm(P,p['proposal_id'],Confirmation(preview_hash=p['preview_hash'],idempotency_key='test-replay-key'))
    assert e.value.status_code==409 and not repo.tables['outbox']

async def test_target_change_requires_fresh_preview():
    svc,repo=fixture();p=(await svc.execute(P,'communications_preview',body().model_dump()))['result']
    repo.tables['records']['b-1']['data']['version']=2
    with pytest.raises(HTTPException) as e:
        await svc.confirm(P,p['proposal_id'],Confirmation(preview_hash=p['preview_hash'],idempotency_key='test-replay-key'))
    assert e.value.status_code==409

async def test_confirm_exact_and_reject_changed_replay():
    svc,repo=fixture();p=(await svc.execute(P,'communications_preview',body().model_dump()))['result']
    confirm=Confirmation(preview_hash=p['preview_hash'],idempotency_key='test-replay-key')
    first=await svc.confirm(P,p['proposal_id'],confirm)
    assert first['communication_outcomes'][0]['status']=='simulated sent'
    assert (await svc.confirm(P,p['proposal_id'],confirm))['duplicate']
    assert len(repo.tables['outbox'])==1
    assert next(iter(repo.tables['outbox'].values()))['data']['message']==p['preview']
    with pytest.raises(HTTPException):await svc.confirm(P,p['proposal_id'],confirm.model_copy(update={'decision':'reject'}))

@pytest.mark.parametrize('mode,status,attempts',[('fail','failed',2),('fail_once','simulated sent',2),('unknown','unknown',1),('missing','pending',0)])
async def test_failure_states_fallback_and_no_recursive_loop(mode,status,attempts):
    _,repo=fixture()
    repo.tables['records']['contact']['data'].update(simulation=mode,email=None if mode=='missing' else 'fixture@homework.invalid')
    result=await record_message(repo,'event',['b-1'],'contact',{'body':'test'},'communications','pm')
    assert (result['status'],result['attempts'])==(status,attempts)
    assert len(result['attempt_log'])==attempts
    if status!='simulated sent':
        assert any(r['data']['category']=='notification_failure' for r in repo.tables['outbox'].values())
        reviewed=await recover_message(repo,P,result['outbox_id'],'Review fictional delivery status')
        assert reviewed['attempts']==attempts
    count=len(repo.tables['outbox'])
    await record_message(repo,'event',['b-1'],'contact',{'body':'test'},'communications','pm')
    assert len(repo.tables['outbox'])==count

async def test_pending_automatic_recovery_once_requested_needs_new_preview():
    _,repo=fixture();repo.tables['records']['contact']['data']['email']=None
    auto=await record_message(repo,'auto',['b-1'],'contact',{},'communications','pm',True)
    requested=await record_message(repo,'requested',['b-1'],'contact',{},'communications','pm',False)
    repo.tables['records']['contact']['data']['email']='now@homework.invalid'
    assert (await recover_message(repo,P,requested['outbox_id'],'Review requested recipient change'))['attempts']==0
    assert (await recover_message(repo,P,auto['outbox_id'],'Recover configured unsent event'))['attempts']==1
    again=await recover_message(repo,P,auto['outbox_id'],'Repeat recovery safely')
    assert again['attempts']==1 and again['recovery']=='already resolved'

async def test_hoi_recipient_not_manager_denied():
    _,repo=fixture()
    with pytest.raises(HTTPException) as e:
        await record_message(repo,'hoi-event',['b-1'],'contact',{'score':100},'hoi','pm',True)
    assert e.value.status_code==403

async def test_general_and_officer_cannot_draft_hoi_target_or_attach_source():
    svc,repo=fixture()
    repo.tables['records']['hidden']={'id':'hidden','kind':'hoi_item','org':'org-1','buildings':['b-1'],'data':{'scope_verified':True}}
    for p in [G,C]:
        with pytest.raises(HTTPException):await exact_preview(repo,p,body().model_copy(update={'entity_id':'hidden'}))
        with pytest.raises(HTTPException):await svc.execute(p,'communications_hoi',{'building_id':'b-1'})
        _,metadata=assemble(p)
        assert 'communications_hoi' not in metadata['bound_tools']
        assert all('confirm' not in t and 'recover' not in t for t in metadata['bound_tools'])

async def test_original_attachment_change_invalidates_preview():
    svc,repo=fixture();repo.tables['sources']['s']={'id':'s','kind':'asset_summary','org':'org-1','buildings':['b-1'],
        'data':{'original':'retained original','version':1,'scope_verified':True}}
    p=(await svc.execute(P,'communications_preview',body(source_ids=['s']).model_dump()))['result']
    # This mimics a corrupt alternate store; the real DB disallows the source write.
    repo.tables['sources']['s']['data']['original']='tampered'
    with pytest.raises(HTTPException) as e:
        await svc.confirm(P,p['proposal_id'],Confirmation(preview_hash=p['preview_hash'],idempotency_key='test-replay-key'))
    assert e.value.status_code==409


def items():return [r for r in dataset()['records'] if r['kind']=='hoi_item' and r['buildings']==['hw1-building-a1']]

def test_independent_half_up_commercial_hierarchy():
    rows=items()
    # One invoice (with its two lines) absent; full PO => commercial75%, overall97.5->98.
    rows=[r for r in rows if not (r['data']['item_type']=='invoice' and r['data']['slot']==1)]
    result=calculate(rows)
    assert result['group_percentages'][6]=='75.00'
    assert result['score']==98


def test_independent_five_groups_and_partial_assets():
    rows=items();firstfive={'profile','location','asset','wo','ppm','inspection','building_certificate','vendor_accreditation'}
    assert calculate([r for r in rows if r['data']['item_type'] in firstfive])['score']==50
    assets=[r for r in rows if r['data']['item_type']=='asset' and r['data']['slot']<3]
    assert calculate(assets)['score']==6

async def test_invoice_line_credit_requires_retained_original_evidence():
    data=dataset();b='hw1-building-a1';_,repo=fixture()
    repo.principal=Principal('p','property_manager','hw1-org-a',(b,))
    for table in ['records','sources']:
        repo.tables[table]={r['id']:deepcopy(r) for r in data[table]}
    assert (await building_score(repo,b))['score']==100
    for row in repo.tables['sources'].values():
        if row['id'].startswith(b+'-hoi-invoice-'):
            row['data'].pop('lines',None)
            original=json.loads(row['data']['text']);original.pop('lines',None)
            row['data']['text']=json.dumps(original)
    assert (await building_score(repo,b))['score']==98

async def test_identical_original_new_id_does_not_double_credit():
    data=dataset();b='hw1-building-a1';_,repo=fixture();repo.principal=Principal('p','property_manager','hw1-org-a',(b,))
    for table in ['records','sources']:repo.tables[table]={r['id']:deepcopy(r) for r in data[table]}
    a=repo.tables['records'][b+'-hoi-asset-0'];z=repo.tables['records'][b+'-hoi-asset-1']
    # Copying a file into another ID/slot cannot turn one record into two assets.
    source=deepcopy(repo.tables['sources'][a['data']['source_id']]);source['id']='duplicate'
    repo.tables['sources']['duplicate']=source
    z['data'].update(fields=deepcopy(a['data']['fields']),source_id='duplicate')
    result=await building_score(repo,b)
    assert result['score']==98 and len(result['duplicate_source_references'])==1

async def test_certificate_cannot_fill_supporting_document_even_with_different_source_id():
    data=dataset();b='hw1-building-a1';_,repo=fixture();repo.principal=Principal('p','property_manager','hw1-org-a',(b,))
    for table in ['records','sources']:repo.tables[table]={r['id']:deepcopy(r) for r in data[table]}
    source=repo.tables['sources'][b+'-hoi-manual-0-source'];source['data']['information_type']='certificate'
    with pytest.raises(HTTPException) as e:await building_score(repo,b)
    assert e.value.status_code==409

async def test_manager_cannot_send_hoi_or_its_fixture_evidence_to_non_manager_contact():
    svc,repo=fixture()
    with pytest.raises(HTTPException) as e:
        await svc.execute(P,'communications_preview',body().model_copy(update={'subject':'HOI Score'}).model_dump())
    assert e.value.status_code==403
    repo.tables['sources']['s']={'id':'s','kind':'fixture','org':'org-1','buildings':['b-1'],
        'data':{'original':'fixture evidence','version':1,'scope_verified':True}}
    with pytest.raises(HTTPException):await svc.execute(P,'communications_preview',body(source_ids=['s']).model_dump())

async def test_failed_pending_recovery_generates_immediate_nonrecursive_issue():
    _,repo=fixture();contact=repo.tables['records']['contact']['data'];contact['email']=None
    original=await record_message(repo,'automatic',['b-1'],'contact',{},'communications','pm',True)
    contact.update(email='now@homework.invalid',simulation='unknown')
    recovered=await recover_message(repo,P,original['outbox_id'],'Resolve the configured fictional missing contact')
    assert recovered['status']=='unknown' and recovered['attempts']==1
    reviews=await recover_message(repo,P,original['outbox_id'],'Review unknown without blind retry')
    assert reviews['attempts']==1
    assert any(r['data'].get('status')=='unknown' and r['kind']=='notification_issue' for r in repo.tables['history'].values())

async def test_corrupt_invoice_line_projection_fails_closed():
    data=dataset();b='hw1-building-a1';_,repo=fixture();repo.principal=Principal('p','property_manager','hw1-org-a',(b,))
    for table in ['records','sources']:repo.tables[table]={r['id']:deepcopy(r) for r in data[table]}
    repo.tables['sources'][b+'-hoi-invoice-0-source']['data']['lines'][0]['fields']['quantity']=99
    with pytest.raises(HTTPException) as e:await building_score(repo,b)
    assert e.value.status_code==409

async def test_blocked_vendor_work_request_and_stale_eligibility_denied():
    svc,repo=fixture()
    repo.tables['records']['v']={'id':'v','kind':'vendor','org':'org-1','buildings':['b-1'],
        'data':{'scope_verified':True,'blocked':True,'version':1}}
    repo.tables['records']['contact']['data']['purposes'].append('work_order_request')
    request=body().model_copy(update={'purpose':'work_order_request','vendor_id':'v'})
    with pytest.raises(HTTPException) as e:await svc.execute(P,'communications_preview',request.model_dump())
    assert e.value.status_code==409
    repo.tables['records']['v']['data']['blocked']=False
    preview=(await svc.execute(P,'communications_preview',request.model_dump()))['result']
    repo.tables['records']['v']['data'].update(blocked=True,version=2)
    with pytest.raises(HTTPException):
        await svc.confirm(P,preview['proposal_id'],Confirmation(preview_hash=preview['preview_hash'],idempotency_key='blocked-stale-key'))
    assert not repo.tables['outbox']

async def test_communication_audit_outage_prevents_content_and_preview_persistence():
    from sqlalchemy.exc import SQLAlchemyError
    svc,repo=fixture()
    async def unavailable(*args,**kwargs):raise SQLAlchemyError('test audit unavailable')
    svc.store.audit=unavailable
    with pytest.raises(HTTPException) as e:await svc.execute(P,'communications_preview',body().model_dump())
    assert e.value.status_code==503 and not repo.tables['proposals'] and not repo.tables['outbox']

async def test_capability_and_denial_audit_precede_any_content_lookup():
    svc,repo=fixture();events=[]
    async def audit(*args,**kwargs):events.append(args[2])
    svc.store.audit=audit
    original_get=repo.get
    async def get(*args,**kwargs):
        assert events and events[0]=='capability_authorized'
        return await original_get(*args,**kwargs)
    repo.get=get
    await svc.execute(P,'communications_preview',body().model_dump())
    events.clear()
    with pytest.raises(HTTPException):await svc.execute(C,'communications_hoi',{'building_id':'b-1'})
    assert events[0]=='capability_denied'

async def test_server_retained_preview_is_owned_exact_and_cannot_be_read_by_another_role():
 svc,repo=fixture();p=(await svc.execute(P,'communications_preview',body().model_dump()))['result']
 stored=await svc.get_preview(P,p['proposal_id'])
 assert stored['preview']==p['preview'] and stored['preview_hash']==p['preview_hash']
 with pytest.raises(HTTPException) as e:await svc.get_preview(G,p['proposal_id'])
 assert e.value.status_code==404 and not repo.tables['outbox']

async def test_manager_failure_fallback_reuses_contact_snapshot_without_recursive_reload():
    from src.services.compliance_hw1.outbox import notify_managers
    _,repo=fixture()
    repo.tables['records']['pm-contact']['data']['simulation']='fail'
    contacts=await repo.list('records','contact')
    original=repo.list
    async def bounded_list(table,kind=None,*args,**kwargs):
        assert (table,kind)!=('records','contact'), 'Fallback reloaded the org contact list'
        return await original(table,kind,*args,**kwargs)
    repo.list=bounded_list
    result=await notify_managers(repo,'b-1','snapshot-event',{'status':'action needed'},'processing_failure',contacts=contacts)
    assert any(r['status']=='failed' for r in result)
    messages=list(repo.tables['outbox'].values())
    assert all(r['data']['attempts']==2 and r['data']['status']=='failed' for r in messages)
    assert len(messages)==2
    assert {r['data']['category'] for r in messages}=={'processing_failure','notification_failure'}

async def test_multibuilding_failure_loads_contacts_once_and_retains_each_missing_manager():
    _,repo=fixture()
    buildings=['b-1']+['fictional-'+str(i) for i in range(99)]
    repo.principal=Principal('portfolio','portfolio_manager','org-1',tuple(buildings))
    officer=deepcopy(repo.tables['records']['contact'])
    officer.update(id='officer',buildings=buildings)
    officer['data'].update(officer=True,simulation='fail')
    repo.tables['records']['officer']=officer
    reads=[];original=repo.list
    async def counted(table,kind=None,*args,**kwargs):
        if (table,kind)==('records','contact'):reads.append(1)
        return await original(table,kind,*args,**kwargs)
    repo.list=counted
    result=await record_message(repo,'multi-failure',buildings,'officer',{},'compliance','portfolio',True)
    assert result['status']=='failed' and result['attempts']==2 and len(reads)==1
    pending=[r for r in repo.tables['history'].values() if r['kind']=='notification_issue' and r['data'].get('status')=='pending']
    assert len(pending)==199
    assert set(b for r in pending for b in r['buildings'])==set(buildings)
    assert len(repo.tables['outbox'])==2  # Original failure plus the one configured PM fallback.
