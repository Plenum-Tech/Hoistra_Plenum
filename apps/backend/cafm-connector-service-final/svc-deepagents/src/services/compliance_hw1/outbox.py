"""Persist simulated messages only. This module imports no email transport."""
import hashlib
import re
import json
from datetime import datetime, timezone
from fastapi import HTTPException
from .authorization import authorize_row
from sqlalchemy import text


def now():
    return datetime.now(timezone.utc).isoformat()


def key(*values):
    return hashlib.sha256('|'.join(values).encode()).hexdigest()


async def validate_mail(repo, principal, target, mail):
    if principal.role not in {'property_manager','portfolio_manager'} and re.search(
            r'\b(?:hoi|hoist score)\b',mail.subject+' '+mail.body,re.I):
        raise HTTPException(403,'HOI communication outside role scope')
    recipients=[]
    for identifier in mail.contact_ids:
        contact=await repo.get('records',identifier,lock=True)
        if contact['kind']!='contact':
            raise HTTPException(422,'Authorized fictional contact required')
        authorize_row(principal,contact)
        data=contact['data']
        if not set(contact['buildings']) & set(target['buildings']):
            raise HTTPException(403,'Recipient scope denied')
        if data.get('email') and not data['email'].endswith('.invalid'):
            raise HTTPException(422,'Fictional .invalid contact required')
        if mail.purpose not in data['purposes']:
            raise HTTPException(403,'Recipient purpose denied')
        if principal.role=='general_user' and data['contact_type']!='team':
            raise HTTPException(403,'General User may email permitted team contacts only')
        if principal.role=='compliance_officer' and mail.purpose=='team_notification':
            raise HTTPException(403,'Compliance communication purpose required')
        if re.search(r'\b(?:hoi|hoist score)\b',mail.subject+' '+mail.body,re.I) and data.get('manager_role') not in {'property_manager','portfolio_manager'}:
            raise HTTPException(403,'HOI recipients must be assigned managers')
        recipients.append({'id':identifier,'email':data.get('email')})
    for identifier in mail.source_ids:
        if principal.role=='general_user':
            raise HTTPException(403,'Full-document attachment not granted')
        source=await repo.get('sources',identifier)
        authorize_row(principal,source,full=True)
        if source['data'].get('information_type')!='certificate':
            raise HTTPException(403,'Attachment type denied')
        if principal.role=='compliance_officer':
            from .contracts import CertificateValues
            try:
                raw=json.loads(source['data'].get('text',''))
                fields=CertificateValues.model_validate(raw['fields']).model_dump(mode='json')
                if set(raw)!={'fields'} or fields!=source['data'].get('fields'):raise ValueError
            except (ValueError,KeyError,TypeError):
                raise HTTPException(403,'Separable certificate attachment required') from None
    return {**mail.model_dump(),'recipients':recipients}


def simulated_delivery(contact):
    """Execute a bounded fictional adapter; no SMTP/client/network transport exists."""
    address=contact.get('email')
    if not address:return {'status':'pending','attempts':0,'reason':'missing contact','attempt_log':[]}
    if not address.endswith('.invalid'):raise HTTPException(422,'Real email address prohibited')
    mode=contact.get('simulation','success');log=[]
    for number in (1,2):
        outcome=('known failed' if mode=='fail' or (mode=='fail_once' and number==1)
                 else 'simulated sent' if mode in {'success','fail_once'} else 'unknown')
        log.append({'attempt':number,'outcome':outcome})
        # Only a positively known failure permits the single second invocation.
        if outcome!='known failed':break
    status='failed' if log[-1]['outcome']=='known failed' else log[-1]['outcome']
    reason=('one known-failure retry exhausted' if status=='failed' else
            'simulation outcome unknown; review, no blind retry' if status=='unknown' else
            'known first failure; one retry succeeded' if len(log)==2 else None)
    return {'status':status,'attempts':len(log),'reason':reason,'attempt_log':log}


async def record_message(repo, event, buildings, recipient, message, category,
                         actor, automatic=False, contacts=None):
    identifier='mail-'+key(event,recipient)
    # All domain send paths share this lock, including independent event drains.
    if hasattr(repo,'session'):
        await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':identifier})
    try:existing=await repo.get('outbox',identifier)
    except HTTPException as error:
        if error.status_code!=404:raise
        existing=None
    if existing:return existing['data']
    contact=await repo.get('records',recipient)
    if (contact['kind']!='contact' or contact['org']!=repo.principal.organization_id
            or not (set(contact['buildings']) & set(buildings) or (category=='compliance' and contact['data'].get('officer')))
            or not contact['data'].get('scope_verified')):
        raise HTTPException(403,'Notification contact association denied')
    if category=='hoi' and contact['data'].get('manager_role') not in {'property_manager','portfolio_manager'}:
        raise HTTPException(403,'HOI recipient must be an assigned manager')
    address=contact['data'].get('email')
    delivery=simulated_delivery(contact['data'])
    status=delivery['status'];attempts=delivery['attempts'];reason=delivery['reason']
    data={'scope_verified':True,'outbox_id':identifier,'event_id':event,'recipient_id':recipient,'recipient':address,
          'message':message,'category':category,'actor':actor,'automatic':automatic,
          'status':status,'attempts':attempts,'reason':reason,'created_at':now(),
          'attempt_log':delivery['attempt_log'],
          'transport':'simulated outbox; no real delivery'}
    await repo.insert('outbox','message',buildings,data,identifier)
    if status in {'failed','unknown','pending'}:
        # A visible record, not another send, avoids recursive failure-alert storms.
        await repo.insert('history','notification_issue',buildings,
            {'event_id':event,'outbox_id':identifier,'recipient_id':recipient,'status':status,'reason':reason},
            'issue-'+key(event,recipient))
    if status in {'failed','unknown','pending'} and category!='notification_failure':
        # Fallback contains identifiers/outcomes, never the restricted source/message.
        if contacts is None:
            contacts=await repo.list('records','contact')
        for building in set(buildings):
            await notify_managers(repo,building,'failure-'+identifier,{'outbox_id':identifier,
                'status':status,'reason':reason,'action':'review pending/failed simulated notification'},'notification_failure',contacts=contacts)
    return data


async def notify_managers(repo, building, event, message, category='hoi', contacts=None):
    contacts=[r for r in (contacts if contacts is not None else await repo.list('records','contact'))
              if building in r['buildings'] and not r['data'].get('usage_only') and r['data'].get('manager_role') in {'property_manager','portfolio_manager'}]
    results=[]
    for role in ['property_manager','portfolio_manager']:
        selected=[c for c in contacts if c['data']['manager_role']==role]
        if not selected:
            identifier='pending-'+key(event,building,role)
            try:pending=await repo.get('history',identifier)
            except HTTPException as error:
                if error.status_code!=404:raise
                pending=None
            if pending is None:
                await repo.insert('history','notification_issue',[building],
                    {'event_id':event,'status':'pending','reason':'missing '+role+' assignment/contact'},identifier)
            results.append({'role':role,'status':'pending'})
        for contact in selected:
            result=await record_message(repo,event,[building],contact['id'],message,category,repo.principal.user_id,True,contacts=contacts)
            results.append({'role':role,'status':result['status']})
    return results


async def recover_message(repo,principal,identifier,reason):
    """Review unknowns; resume only unsent configured messages with a real fixture contact."""
    row=await repo.get('outbox',identifier,lock=True)
    authorize_row(principal,row,full=True)
    d=row['data'];status=d['status']
    if status in {'unknown','failed'}:
        # Unknown outcomes never retry. Exhausted known failures never gain a third attempt.
        review_id='review-'+key(identifier,str(d.get('attempts',0)),status)
        if not any(r['id']==review_id for r in await repo.list('history','notification_review')):
            await repo.insert('history','notification_review',row['buildings'],{'outbox_id':identifier,
                'actor':principal.user_id,'reason':reason,'status':status,'attempts':d['attempts'],
                'time':now(),'action':'review recorded; no additional delivery attempt'},review_id)
        return {'outbox_id':identifier,'status':status,'attempts':d['attempts'],'recovery':'review recorded; no retry'}
    if status!='pending':return {'outbox_id':identifier,'status':status,'attempts':d['attempts'],'recovery':'already resolved'}
    contact=await repo.get('records',d['recipient_id'])
    address=contact['data'].get('email')
    if not address:return {'outbox_id':identifier,'status':'pending','attempts':0,'recovery':'missing contact remains pending'}
    if not d.get('automatic'):
        return {'outbox_id':identifier,'status':'pending','attempts':0,'recovery':'recipient changed; new exact user preview required'}
    if not address.endswith('.invalid'):raise HTTPException(422,'Fictional contact required')
    if contact['org']!=principal.organization_id or not set(contact['buildings']) & set(row['buildings']):
        raise HTTPException(403,'Recovery recipient association denied')
    delivery=simulated_delivery(contact['data'])
    new_status=delivery['status'];attempts=delivery['attempts']
    updated={**d,**delivery,'recipient':address,'recovered_at':now(),
        'recovery_actor':principal.user_id,'recovery_reason':reason}
    await repo.update('outbox',row,updated)
    await repo.insert('history','notification_recovery',row['buildings'],{
        'outbox_id':identifier,'before':status,'after':new_status,'actor':principal.user_id,'reason':reason,'time':now()},
        'recovery-'+key(identifier))
    if new_status!='simulated sent':
        await repo.insert('history','notification_issue',row['buildings'],{'outbox_id':identifier,
            'event_id':d['event_id'],'status':new_status,'reason':updated['reason'],'time':now()},'recovery-issue-'+key(identifier))
        if d['category']!='notification_failure':
            for building in row['buildings']:
                await notify_managers(repo,building,'recovery-failure-'+identifier,{'outbox_id':identifier,
                    'status':new_status,'action':'review failed configured recovery'},'notification_failure')
    return {'outbox_id':identifier,'status':new_status,'attempts':attempts,'recovery':'configured pending message resumed'}
