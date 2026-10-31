"""Evidence-linked previews and explicit human decisions, never model commits."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import text
from .authorization import authorize_action, authorize_row, buildings
from .contracts import CertificateValues, REVIEWERS
from .dates import classify
from .outbox import validate_mail, record_message, notify_managers, now
from .reads import clock
from .hoi import building_score


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def safe_target(row, principal):
    return {'id':row['id'],'kind':row['kind'],'building_ids':sorted(set(row['buildings']) & set(principal.building_ids)),
            'version':row['data'].get('version',1)}


def evidence_fields(text_value):
    try:
        data=json.loads(text_value)
        return CertificateValues.model_validate(data['fields']).model_dump(mode='json')
    except (ValueError,KeyError,TypeError):
        raise HTTPException(422,'Homework certificate evidence must be JSON with validated fields') from None


async def validate_proposal(repo,principal,body,target):
    authorize_action(principal,body.action,target)
    if body.action in {'block_vendor','unblock_vendor'} and target['kind']!='vendor':
        raise HTTPException(422,'Vendor target required')
    if body.action not in {'block_vendor','unblock_vendor','email','accept_certificate'} and target['kind']!='certificate':
        raise HTTPException(422,'Certificate target required')
    if body.action=='accept_certificate' and target['kind'] not in {'certificate','building'}:
        raise HTTPException(422,'Authorized building/certificate target required')
    if body.action in {'accept_certificate','correct_certificate','select_version'}:
        if not body.values or not (body.source_id or body.source_text):
            raise HTTPException(422,'Values and source evidence required')
        values=body.values.model_dump(mode='json')
        if target['kind']=='certificate' and (values['subject'],values['holder_id'])!=(target['data']['subject'],target['data']['holder_id']):
            raise HTTPException(422,'Correction cannot change source ownership/association')
        if values['subject']=='Building' and values['holder_id'] not in target['buildings']:
            raise HTTPException(403,'Certificate holder outside target')
        if values['subject']=='Vendor':
            vendor=await repo.get('records',values['holder_id'])
            authorize_row(principal,vendor)
            if vendor['kind']!='vendor' or not set(vendor['buildings'])&set(target['buildings']):
                raise HTTPException(403,'Vendor/building association unavailable')
        if body.source_id:
            source=await repo.get('sources',body.source_id)
            authorize_row(principal,source,full=True)
            if source['data'].get('information_type')!='certificate':
                raise HTTPException(422,'Certificate source representation required')
            fields=evidence_fields(json.dumps({'fields':source['data'].get('fields')}))
            if not set(source['buildings'])&set(target['buildings']):
                raise HTTPException(403,'Evidence association denied')
        else:
            fields=evidence_fields(body.source_text)
        if fields!=values:
            raise HTTPException(422,'Proposed values must match supplied evidence fields')
    if body.action=='email' and not body.mail:
        raise HTTPException(422,'Requested email requires exact preview')
    if body.mail:
        if body.action not in {'email','request_renewal','approve_booking'}:
            raise HTTPException(422,'Combined email must accompany renewal/booking only')
        if body.action=='request_renewal' and body.mail.purpose!='certificate_renewal':
            raise HTTPException(422,'Renewal email purpose required')
        if body.action=='approve_booking' and body.mail.purpose!='inspection_booking':
            raise HTTPException(422,'Booking email purpose required')
        return await validate_mail(repo,principal,target,body.mail)
    return None


async def prepare(repo,principal,body):
    target=await repo.get('records',body.entity_id)
    mail=await validate_proposal(repo,principal,body,target)
    if body.action in {'accept_certificate','correct_certificate','select_version'} and body.source_text:
        # Retain incoming evidence before review; proposal rows are mutable workflow state.
        source_id=await repo.insert('sources','certificate',target['buildings'],{
            'text':body.source_text,'original':body.source_text,'fields':body.values.model_dump(mode='json'),
            'version':1,'information_type':'certificate','scope_verified':True,'recorded_at':now()})
        body=body.model_copy(update={'source_id':source_id,'source_text':None})
    preview={'action':body.action,'target':safe_target(target,principal),
             'current':{k:target['data'].get(k) for k in ['reference','expiry_date','source_id','version','blocked']},
             'proposed':body.values.model_dump(mode='json') if body.values else None,
             'reason':body.reason,'source_id':body.source_id,
             'source_text':body.source_text,'mail':mail,
             'blocking_reason_ids':body.blocking_reason_ids,
             'effect':'organization-wide new-request eligibility; external WOs unchanged' if body.action in {'block_vendor','unblock_vendor'} else 'scoped certificate workflow',
             'simulation':'fictional records; no real mail/appointment/register verification'}
    identifier=str(uuid4())
    data={'request':body.model_dump(mode='json'),'preview':preview,'preview_hash':digest(preview),
          'version':1,'entity_version':target['data'].get('version',1),'status':'awaiting_confirmation',
          'proposer':principal.user_id,'expires_at':(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(),
          'created_at':now()}
    await repo.insert('proposals','compliance',target['buildings'],data,identifier)
    await repo.audit('proposal.prepare','awaiting_confirmation',{'proposal_id':identifier,'action':body.action})
    # Team mail does not give Compliance Officers access to unrelated communications.
    category='communications' if mail and mail['purpose']=='team_notification' else 'compliance'
    # All eligible people can review, but only one decision applies; notifications never approve.
    for building in set(target['buildings']) & set(principal.building_ids):
        await notify_managers(repo,building,'proposal-'+identifier,
            {'proposal_id':identifier,'action':body.action,'purpose':mail['purpose'] if mail else None,'review':'eligible explicit decision required'},category)
    if principal.role in REVIEWERS and category=='compliance':
        contacts=[c for c in await repo.list('records','contact') if c['data'].get('officer') and not c['data'].get('usage_only')]
        for c in contacts:
            await record_message(repo,'proposal-'+identifier,target['buildings'],c['id'],
                {'proposal_id':identifier,'action':body.action,'review':'certificate function only'},'compliance',principal.user_id,True)
    return {'proposal_id':identifier,**data,'outcome':'awaiting_confirmation',
            'confirmation_endpoint':f'/api/homework/actions/{identifier}/confirm'}


async def get_proposal(repo,principal,identifier):
    proposal=await repo.get('proposals',identifier)
    target=await repo.get('records',proposal['data']['request']['entity_id'])
    authorize_action(principal,proposal['data']['request']['action'],target)
    if principal.role=='general_user' and proposal['data']['proposer']!=principal.user_id:
        raise HTTPException(404,'Record unavailable')
    request=proposal['data']['request']
    if principal.role=='compliance_officer' and request.get('mail',{}):
        if request['mail']['purpose'] not in {'certificate_renewal','inspection_booking'}:
            raise HTTPException(404,'Proposal unavailable')
    return {'proposal_id':identifier,**proposal['data']}


async def apply_certificate(repo,principal,proposal,target,body):
    values=body.values.model_dump(mode='json')
    existing=await repo.list('records','certificate')
    if target['kind']=='building':
        duplicates=[r for r in existing if r['data']['reference']==values['reference']
                    and r['data']['holder_id']==values['holder_id'] and set(r['buildings'])&set(target['buildings'])]
        if duplicates:
            old={k:duplicates[0]['data'].get(k) for k in values}
            if old==values:
                return {'status':'unchanged','record_id':duplicates[0]['id'],'reason':'already ingested; no duplicate credit'}
            raise HTTPException(409,'Conflicting accepted record; prepare an explicit correction')
    old_source=target['data'].get('source_id')
    source_id=body.source_id
    if not source_id:
        source_id=await repo.insert('sources','certificate',target['buildings'],
            {'text':body.source_text,'fields':values,'version':target['data'].get('version',0)+1,
             'information_type':'certificate','scope_verified':True,'recorded_at':now()})
    before=dict(target['data']) if target['kind']=='certificate' else None
    data={**(target['data'] if target['kind']=='certificate' else {}),**values,
          'source_id':source_id,'accepted':True,'draft':False,'scope_verified':True,
          'version':target['data'].get('version',0)+1,'assessment':'unassessed',
          'assessment_reason':'new representation; no external verification claimed'}
    if target['kind']=='certificate':
        await repo.update('records',target,data)
        certificate_id=target['id']
    else:
        certificate_id=await repo.insert('records','certificate',target['buildings'],data)
    from ..ingestion_hw1.index import project
    await project(repo,{'id':certificate_id,'kind':'certificate','org':principal.organization_id,
        'buildings':target['buildings'],'data':data},
        previous={'id':certificate_id,'kind':'certificate','org':principal.organization_id,
                  'buildings':target['buildings'],'data':before} if before else None)
    # Only a fixed verified slot link can affect HOI; extra uploads never invent slots.
    if data.get('hoi_item_id'):
        item=await repo.get('records',data['hoi_item_id'])
        fields={k:values[k] for k in item['data']['fields'] if k in values}
        item_data={**item['data'],'fields':fields,'source_supported':[k for k,v in fields.items() if v is not None],
                   'source_id':source_id,'accepted':True}
        await repo.update('records',item,item_data)
    event_id='change-'+proposal['id']
    await repo.insert('events','certificate_changed',target['buildings'],
        {'proposal_id':proposal['id'],'certificate_id':certificate_id,'old_source_id':old_source,
         'new_source_id':source_id,'before':before,'after':data,'actor':principal.user_id,
         'reason':body.reason,'time':now()},event_id)
    return {'status':'applied','record_id':certificate_id,'source_id':source_id,
            'previous_source_retained':old_source,'event_id':event_id,'follow_through':'pending'}


async def unblock_reasons(repo,target,body):
    if not body.source_id:
        raise HTTPException(422,'Replacement certificate source required')
    source=await repo.get('sources',body.source_id)
    authorize_row(repo.principal,source,full=True)
    if source['data'].get('information_type')!='certificate' or source['data'].get('fields',{}).get('holder_id')!=target['id']:
        raise HTTPException(422,'Evidence must belong to this vendor')
    today=await clock(repo)
    state=classify(source['data']['fields'],today)
    if state['days_to_expiry'] is None or state['days_to_expiry']<0:
        raise HTTPException(409,'Replacement evidence is expired or unknown')
    reasons=target['data'].get('blocking_reasons',[])
    for reason in reasons:
        if reason['resolved']:
            continue
        if reason['category']!='certificate':
            raise HTTPException(409,'Unresolved non-certificate restriction; not cleared by renewal')
        certificate=await repo.get('records',reason['certificate_id'])
        accepted_source=await repo.get('sources',certificate['data']['source_id'])
        authorize_row(repo.principal,accepted_source,full=True)
        if not certificate['data'].get('accepted') or classify(certificate['data'],today)['days_to_expiry'] is None or classify(certificate['data'],today)['days_to_expiry']<0:
            raise HTTPException(409,'Every certificate blocking reason needs accepted current evidence')
    return [{**reason,'resolved':True} for reason in reasons]


async def confirm(repo,principal,identifier,confirmation):
    from .contracts import ProposalInput
    proposal=await repo.get('proposals',identifier,lock=True)
    data=proposal['data'];body=ProposalInput.model_validate(data['request'])
    target=await repo.get('records',body.entity_id,lock=True)
    authorize_action(principal,body.action,target)
    if principal.role=='general_user' and data['proposer']!=principal.user_id:
        raise HTTPException(404,'Record unavailable')
    if confirmation.version!=data['version'] or confirmation.preview_hash!=data['preview_hash']:
        raise HTTPException(409,'Preview changed; review again')
    if data['status']!='awaiting_confirmation':
        if (data.get('idempotency_key')==confirmation.idempotency_key and data.get('decided_by')==principal.user_id
                and data.get('confirmation_decision', 'approve' if data['status']=='approved' else 'reject')==confirmation.decision):
            return {'proposal_id':identifier,'status':data['status'],'result':data.get('result'),'duplicate':True}
        raise HTTPException(409,'Proposal already resolved')
    if datetime.fromisoformat(data['expires_at'])<datetime.now(timezone.utc):
        raise HTTPException(409,'Preview expired')
    if data['entity_version']!=target['data'].get('version',1):
        raise HTTPException(409,'Target changed; prepare a fresh preview')
    mail=await validate_proposal(repo,principal,body,target)
    if mail!=data['preview']['mail']:
        raise HTTPException(409,'Recipients or preview changed')
    result={'status':'rejected'}
    if confirmation.decision=='approve':
        if body.action in {'accept_certificate','correct_certificate','select_version'}:
            result=await apply_certificate(repo,principal,proposal,target,body)
        elif body.action in {'block_vendor','unblock_vendor'}:
            reasons=list(target['data'].get('blocking_reasons',[]))
            if body.action=='unblock_vendor':
                reasons=await unblock_reasons(repo,target,body)
            else:
                if not body.source_id:
                    raise HTTPException(422,'Certificate expiry evidence required')
                source=await repo.get('sources',body.source_id)
                authorize_row(principal,source,full=True)
                if source['data'].get('information_type')!='certificate' or source['data'].get('fields',{}).get('holder_id')!=target['id']:
                    raise HTTPException(422,'Vendor certificate evidence required')
                d=classify(source['data']['fields'],await clock(repo))['days_to_expiry']
                if d is None or d>=0:
                    raise HTTPException(409,'Certificate-expiry block requires lapsed evidence')
                certificates=[c for c in await repo.list('records','certificate') if c['data']['source_id']==body.source_id]
                if not certificates:
                    raise HTTPException(409,'Accepted certificate evidence required')
                reasons.append({'id':'block-'+identifier,'category':'certificate','certificate_id':certificates[0]['id'],'resolved':False})
            updated={**target['data'],'blocked':body.action=='block_vendor','blocking_reasons':reasons,
                     'version':target['data'].get('version',1)+1}
            await repo.update('records',target,updated)
            result={'status':'applied','blocked':updated['blocked'],'effect':'organization-wide new requests only'}
            # Eligibility is separate from completeness; retain unchanged-score trails.
            # Only assigned buildings may be recalculated by this principal.
            from ..ingestion_hw1.followthrough import changed
            for building in set(target['buildings']) & set(principal.building_ids):
                await changed(repo,principal,building,'vendor-eligibility-'+identifier+'-'+building,
                    body.source_id,target['id'],body.reason)
        elif body.action in {'request_renewal','approve_booking'}:
            result={'status':'recorded','workflow':'renewal request' if body.action=='request_renewal' else 'inspection booking approved',
                    'external_outcome':'not externally booked/renewed; fictional homework workflow'}
        elif body.action=='email':
            result={'status':'recorded','transport':'simulated only'}
        if mail:
            sends=[]
            for recipient in mail['recipients']:
                sends.append(await record_message(repo,'requested-'+identifier,target['buildings'],recipient['id'],
                    mail,'communications' if mail['purpose']=='team_notification' else 'compliance',principal.user_id))
            result['communication_outcomes']=[{'recipient_id':x['recipient_id'],'status':x['status']} for x in sends]
    updated={**data,'status':'approved' if confirmation.decision=='approve' else 'rejected',
             'confirmation_decision':confirmation.decision,'decided_by':principal.user_id,'decision_time':now(),'idempotency_key':confirmation.idempotency_key,'result':result}
    await repo.update('proposals',proposal,updated)
    await repo.insert('history','decision',target['buildings'],
        {'proposal_id':identifier,'actor':principal.user_id,'role':principal.role,'decision':confirmation.decision,
         'before':data['preview']['current'],'proposed':data['preview']['proposed'],
         'result':result,'reason':body.reason,'source_id':body.source_id,'time':now()})
    await repo.audit('proposal.confirm',updated['status'],{'proposal_id':identifier,'action':body.action})
    return {'proposal_id':identifier,'status':updated['status'],'result':result,'duplicate':False}


async def process_events(store,principal):
    processed=[]
    async with store.transaction(principal) as repo:
        processed_ids={h['data'].get('event_id') for h in await repo.list('history','event_processed')}
        pending=[e for e in await repo.list('events','certificate_changed') if e['id'] not in processed_ids and set(e['buildings'])<=set(principal.building_ids)]
        for event in pending[:20]:
            if not set(event['buildings'])&set(principal.building_ids):
                continue
            # Serialize event delivery so concurrent confirmations cannot duplicate follow-through.
            await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':event['id']})
            if any(h['data'].get('event_id')==event['id'] and h['kind']=='event_processed' for h in await repo.list('history','event_processed')):
                continue
            for building in event['buildings']:
                if building not in principal.building_ids:
                    continue
                history=sorted([h['data'] for h in await repo.list('history','hoi') if h['buildings']==[building]], key=lambda h:h.get('time',''))
                previous=[h['score'] for h in history]
                score=await building_score(repo,building)
                change={'event_id':event['id'],'building_id':building,'before':previous[-1] if previous else score['score'],
                        'after':score['score'],'score':score['score'],'formula_version':score['formula_version'],
                        'actor':event['data']['actor'],'proposal_id':event['data']['proposal_id'],
                        'source_id':event['data']['new_source_id'],'changes':event['data']['reason'],'before_values':event['data']['before'],
                        'after_values':event['data']['after'],'time':now()}
                await repo.insert('history','hoi',[building],change,'hoi-'+event['id']+'-'+building)
                await notify_managers(repo,building,event['id'],change)
            await repo.insert('history','event_processed',event['buildings'],{'event_id':event['id'],'time':now()})
            processed.append(event['id'])
    return {'processed_events':processed,'status':'completed' if len(pending)<=20 else 'partial','remaining_events':max(0,len(pending)-20)}


async def emit_expiry_notifications(store,principal):
    """Scan all authorized rows, committing one version/stage event per transaction.

    Holding every historical event/recipient advisory lock until the entire org scan
    finished exhausted PostgreSQL's shared lock table as immutable fixtures grew.
    Per-certificate transactions preserve event atomicity and release bounded locks.
    """
    from .reads import certificates
    from .contracts import Query
    from sqlalchemy.exc import SQLAlchemyError
    async with store.transaction(principal,repeatable=True) as repo:
        rows=await certificates(repo,principal,Query(limit=100))
    dispatched=[];changed=[]
    for certificate in rows:
        if certificate['expiry_label']=='current':continue
        try:
            async with store.transaction(principal) as repo:
                record=await repo.get('records',certificate['id'])
                if record['data'].get('source_id')!=certificate['source_id']:
                    changed.append(certificate['id']);continue
                event='expiry-'+digest({'certificate':certificate['id'],'source':certificate['source_id'],
                                      'label':certificate['expiry_label']})
                # Serialize just this certificate version/stage; message keys dedup recipients.
                await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':event})
                message={'certificate_id':certificate['id'],'source_id':certificate['source_id'],
                    'expiry_date':certificate.get('expiry_date'),'expiry_label':certificate['expiry_label'],
                    'assessment_date':certificate['assessment_date'],
                    'action_required':certificate['action_window'] or certificate['expiry_label'] in {'expired/lapsed','unknown'},
                    'approval':'notification only; no renewal/block/unblock approved'}
                for building in set(record['buildings'])&set(principal.building_ids):
                    await notify_managers(repo,building,event,message,'compliance')
                for contact in await repo.list('records','contact'):
                    if contact['data'].get('officer') and not contact['data'].get('usage_only'):
                        await record_message(repo,event,record['buildings'],contact['id'],message,'compliance',principal.user_id,True)
                dispatched.append({'certificate_id':certificate['id'],'expiry_label':certificate['expiry_label']})
        except SQLAlchemyError:
            # Earlier certificate events may already be committed; never label the whole scan sent.
            raise HTTPException(503,'Expiry notification scan partially processed; retry deduplicated drain; no full completion claimed') from None
    async with store.transaction(principal) as repo:
        await repo.audit('expiry_notifications.manual','partial' if changed else 'completed',{'certificate_count':len(dispatched)})
    return {'status':'partial' if changed else 'completed','trigger':'manual; no calendar scheduler',
        'certificates_evaluated':len(rows),'alert_stages':dispatched,'changed_during_scan':changed,
        'deduplication':'certificate/source-version/urgency-stage/recipient'}
