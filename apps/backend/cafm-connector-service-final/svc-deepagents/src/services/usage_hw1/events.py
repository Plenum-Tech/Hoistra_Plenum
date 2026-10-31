"""Durable, resumable failure observations and bounded configured notifications."""
import os
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import text
from ..compliance_hw1.store import runtime_store
from ..compliance_hw1.outbox import notify_managers,record_message,key

async def _exists(repo,identifier):
    try:await repo.get('history',identifier);return True
    except HTTPException as error:
        if error.status_code!=404:raise
        return False

async def _stage(store,p,identifier,buildings,deliver):
    # Release recipient advisory locks after each building/contact. A portfolio
    # failure must not hold its entire recipient population in one transaction.
    marker='failure-stage-'+key(identifier)
    async with store.transaction(p) as repo:
        await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':marker})
        if await _exists(repo,marker):return
        await deliver(repo)
        await repo.insert('history','usage_failure_notification_processed',buildings,
            {'event_id':identifier,'actor':p.user_id,'time':datetime.now(timezone.utc).isoformat()},marker)

async def record_failure(p,request_id,domain,status):
    if os.environ.get('HW1_USAGE_ENABLED','false')!='true':return
    identifier='usage-failure-'+request_id;store=runtime_store()
    async with store.transaction(p) as repo:
        await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:id,0))'),{'id':identifier})
        if not await _exists(repo,identifier):
            category='compliance' if p.role=='compliance_officer' else domain
            data={'actor':p.user_id,'role':p.role,'request_id':request_id,'domain':category,'category':category,
                'http_status':status,'outcome':'failed','generated_at':datetime.now(timezone.utc).isoformat(),
                'source':'observed authenticated adapter HTTP failure; no source content retained',
                'allocation':'authenticated assignment; no guessed building allocation'}
            await repo.insert('history','usage_failure',list(p.building_ids),data,identifier)
        contacts=await repo.list('records','contact')
    # A replay resumes only uncommitted stages. record_message retains exact
    # recipient deduplication and never blindly retries an existing unknown/failure.
    message={'event_id':identifier,'status':'action needed','http_status':status,
        'review':'Review the failed request under the same authenticated role; no action approved.'}
    for b in p.building_ids:
        event=identifier+'-'+b
        async def deliver(repo,b=b,event=event):
            await notify_managers(repo,b,event,message,'processing_failure',contacts=contacts)
        await _stage(store,p,event,[b],deliver)
    if p.role=='compliance_officer':
        for contact in contacts:
            # Digest-only contacts are not configured for immediate processing
            # alerts (same boundary as certificate notifications).
            if not contact['data'].get('officer') or contact['data'].get('usage_only'):continue
            async def deliver_officer(repo,recipient=contact['id']):
                await record_message(repo,identifier,list(p.building_ids),recipient,message,'compliance',p.user_id,True,contacts=contacts)
            await _stage(store,p,identifier+'-officer-'+contact['id'],list(p.building_ids),deliver_officer)
