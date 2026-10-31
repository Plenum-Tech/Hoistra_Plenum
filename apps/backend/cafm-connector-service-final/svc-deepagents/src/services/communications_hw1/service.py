"""Explicit exact-message approval, scoped evidence, and simulated delivery only."""
import hashlib,json,re
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from ...api.homework_identity import TestPrincipal
from ..compliance_hw1.store import runtime_store
from ..compliance_hw1.authorization import authorize_row,buildings
from ..compliance_hw1.hoi import building_score
from ..compliance_hw1.outbox import record_message,notify_managers,now
from ..compliance_hw1.reads import read
from ..compliance_hw1.contracts import Query as ComplianceQuery
from .contracts import MODELS,ROLE_TOOLS,Preview,Confirmation

MANAGERS={'property_manager','portfolio_manager'}
CERT_PURPOSES={'certificate_renewal','inspection_booking'}
ATTACHMENT_KINDS={'certificate','hoi_item','fixture','asset_summary','inspection_report','work_order_report',
    'meter_reading','building_profile','energy_profile','energy_source','contract','contract_source'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

def identity(principal):
    if not isinstance(principal,TestPrincipal):raise HTTPException(401,'Trusted principal required')

async def exact_preview(repo,p,body,lock=False):
    buildings(p,[body.building_id])
    target=await repo.get('records',body.entity_id,lock=lock)
    authorize_row(p,target,selected=[body.building_id])
    if body.building_id not in target['buildings']:raise HTTPException(404,'Target unavailable')
    if p.role=='compliance_officer' and (body.purpose not in CERT_PURPOSES or target['kind']!='certificate'):
        raise HTTPException(403,'Officer communications are certificate-only')
    if p.role=='general_user' and (body.purpose not in {'team_notification','work_order_request'}|CERT_PURPOSES
            or target['kind'] not in {'building','certificate','asset_summary','work_order_report'}):
        raise HTTPException(403,'Permitted summary/team communication only')
    if p.role not in MANAGERS and re.search(r'\b(?:hoi|hoist score)\b',body.subject+' '+body.body,re.I):
        raise HTTPException(403,'HOI communication outside role scope')
    hoi_message=bool(re.search(r'\b(?:hoi|hoist score)\b',body.subject+' '+body.body,re.I))
    contacts=[]
    for identifier in sorted(set(body.contact_ids)):
        contact=await repo.get('records',identifier,lock=lock)
        authorize_row(p,contact,selected=[body.building_id])
        d=contact['data']
        if contact['kind']!='contact' or body.building_id not in contact['buildings']:
            raise HTTPException(404,'Contact unavailable')
        if body.purpose not in d.get('purposes',[]):raise HTTPException(403,'Contact purpose denied')
        if p.role=='general_user' and d.get('contact_type')!='team':raise HTTPException(403,'Team contacts only')
        address=d.get('email')
        if address and not address.endswith('.invalid'):raise HTTPException(422,'Fictional contact required')
        if hoi_message and d.get('manager_role') not in MANAGERS:
            raise HTTPException(403,'HOI recipients must be assigned managers')
        contacts.append({'contact_id':identifier,'address':address})
    vendor_snapshot=None
    if body.purpose=='work_order_request':
        vendor_ids={body.vendor_id} if body.vendor_id else set()
        if target['kind']=='vendor':vendor_ids.add(target['id'])
        if target['kind']=='work_order_report':
            linked=target['data'].get('vendor_id',target['data'].get('fields',{}).get('vendor_id'))
            if linked:vendor_ids.add(linked)
        for recipient in contacts:
            contact=await repo.get('records',recipient['contact_id'],lock=lock)
            if contact['data'].get('contact_type')=='vendor':
                linked=contact['data'].get('vendor_id')
                if not linked:raise HTTPException(409,'Vendor recipient association unavailable')
                vendor_ids.add(linked)
        if len(vendor_ids)>1:raise HTTPException(422,'Contradictory vendor associations')
        if vendor_ids:
            vendor=await repo.get('records',next(iter(vendor_ids)),lock=lock)
            authorize_row(p,vendor,selected=[body.building_id])
            if vendor['kind']!='vendor':raise HTTPException(422,'Vendor association required')
            if vendor['data'].get('blocked') is not False:
                raise HTTPException(409,'Vendor blocked or eligibility unknown; no new work request permitted')
            vendor_snapshot={'vendor_id':vendor['id'],'version':vendor['data'].get('version',1),'blocked':False}
    elif body.vendor_id:
        raise HTTPException(422,'Vendor request association applies only to work-order requests')
    attachments=[]
    for identifier in sorted(set(body.source_ids)):
        if p.role=='general_user':raise HTTPException(403,'Full-source attachments not granted')
        source=await repo.get('sources',identifier)
        authorize_row(p,source,full=True,selected=[body.building_id])
        if source['kind'] not in ATTACHMENT_KINDS:raise HTTPException(403,'Unsupported attachment source')
        if p.role=='compliance_officer':
            # Reuse the certificate adapter's separation check, never label mixed content safe.
            from ..compliance_hw1.contracts import CertificateValues
            d=source['data']
            try:
                raw=json.loads(d.get('text',''))
                fields=CertificateValues.model_validate(raw['fields']).model_dump(mode='json')
                if set(raw)!={'fields'} or fields!=d.get('fields') or d.get('information_type')!='certificate':raise ValueError
            except (ValueError,KeyError,TypeError):raise HTTPException(403,'Separable certificate attachment required') from None
        if source['kind'] in {'hoi_item','fixture'}:
            for recipient in contacts:
                contact=await repo.get('records',recipient['contact_id'])
                if contact['data'].get('manager_role') not in MANAGERS:
                    raise HTTPException(403,'HOI evidence recipients must be assigned managers')
        attachments.append({'source_id':identifier,'version':source['data'].get('version'),
            'sha256':digest(source['data'])})
    return {'building_id':body.building_id,'entity_id':body.entity_id,
        'entity_version':target['data'].get('version',1),'purpose':body.purpose,'vendor_eligibility':vendor_snapshot,
        'recipients':contacts,'subject':body.subject,'body':body.body,'attachments':attachments,
        'transport':'simulated outbox; no real delivery',
        'effect':'communication only; no operational work order or external action'}

class CommunicationsService:
    def __init__(self,store=None):self.store=store or runtime_store()

    async def _guard(self,p,operation,run,request_id=None,target=None,permitted=True):
        identity(p)
        try:
            await self.store.audit(p,operation,'capability_authorized' if permitted else 'capability_denied',target,request_id)
            if not permitted:raise HTTPException(403,'Communication operation denied')
            return await run()
        except HTTPException as error:
            await self.store.audit(p,operation,'denied_or_unavailable_'+str(error.status_code),target,request_id)
            raise
        except SQLAlchemyError:
            raise HTTPException(503,'Communications audit/store unavailable; no success claimed') from None

    async def get_preview(self,p,identifier):
        async def read():
            async with self.store.transaction(p) as repo:
                row=await repo.get('proposals',identifier);d=row['data']
                if row['kind']!='communication' or d.get('owner')!=p.user_id or d.get('role')!=p.role:
                    raise HTTPException(404,'Communication preview unavailable')
                buildings(p,row['buildings'])
                await repo.audit('communications_preview_read','succeeded',{'proposal_id':identifier})
                return {'proposal_id':identifier,**d}
        return await self._guard(p,'communications_preview_read',read,target={'proposal_id':identifier})

    async def execute(self,p,tool,args,request_id=None):
        return await self._guard(p,tool,lambda:self._execute(p,tool,args,request_id),request_id,
            permitted=tool in ROLE_TOOLS.get(getattr(p,"role",None),[]))

    async def confirm(self,p,identifier,body):
        return await self._guard(p,'communications_confirm',lambda:self._confirm(p,identifier,body),
            target={'proposal_id':identifier})

    async def recover(self,p,identifier,reason):
        return await self._guard(p,'communications_recovery',lambda:self._recover(p,identifier,reason),
            target={'outbox_id':identifier},permitted=getattr(p,"role",None) in MANAGERS)

    async def _execute(self,p,tool,args,request_id=None):

        identity(p)
        if tool not in ROLE_TOOLS.get(getattr(p,"role",None),[]):raise HTTPException(403,'Communication operation denied')
        try:
            q=MODELS[tool].model_validate(args)
            buildings(p,[q.building_id])
            async with self.store.transaction(p) as repo:
                if tool=='communications_preview':
                    preview=await exact_preview(repo,p,q);identifier=str(uuid4())
                    data={'owner':p.user_id,'role':p.role,'request':q.model_dump(),'preview':preview,
                        'preview_hash':digest(preview),'status':'awaiting_confirmation',
                        'expires_at':(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(),'created_at':now()}
                    await repo.insert('proposals','communication',[q.building_id],data,identifier)
                    result={'proposal_id':identifier,**data,
                        'confirmation_endpoint':'/api/homework/communications/proposals/'+identifier+'/confirm'}
                elif tool=='communications_contacts':
                    rows=await repo.list('records','contact')
                    contacts=[]
                    for r in rows:
                        if q.building_id not in r['buildings'] or not r['data'].get('scope_verified'):continue
                        d=r['data'];purposes=set(d.get('purposes',[]))
                        if p.role=='general_user' and d.get('contact_type')!='team':continue
                        if p.role=='compliance_officer':purposes &= CERT_PURPOSES
                        if not purposes:continue
                        contacts.append({'contact_id':r['id'],'address':d.get('email'),'purposes':sorted(purposes)})
                    result={'contacts':contacts[q.offset:q.offset+q.limit],'total_matching':len(contacts)}
                elif tool=='communications_hoi':
                    result=await building_score(repo,q.building_id)
                    histories=[r for r in await repo.list('history','hoi') if r['buildings']==[q.building_id]]
                    page=hoi_history_page(histories,q.limit,q.offset)
                    result.update(page)
                else:
                    result=await read(repo,p,'homework_get_outbox',ComplianceQuery(building_ids=[q.building_id],limit=q.limit,offset=q.offset,after_id=q.after_id,include_issues=q.include_issues))
                await repo.audit(tool,'succeeded',request_id=request_id)
            return {'tool':tool,'result':result}
        except ValidationError:raise HTTPException(422,'Invalid declared communications arguments') from None
        except SQLAlchemyError:raise HTTPException(503,'Communications store unavailable; no success claimed') from None

    async def _confirm(self,p,identifier,body):
        identity(p)
        async with self.store.transaction(p) as repo:
            row=await repo.get('proposals',identifier,lock=True);d=row['data']
            if row['kind']!='communication' or d.get('owner')!=p.user_id or d.get('role')!=p.role:
                raise HTTPException(404,'Communication preview unavailable')
            buildings(p,row['buildings'])
            if body.preview_hash!=d['preview_hash']:raise HTTPException(409,'Exact preview changed')
            if d['status']!='awaiting_confirmation':
                if d.get('idempotency_key')==body.idempotency_key and d.get('decision')==body.decision:
                    return {**d['result'],'duplicate':True}
                raise HTTPException(409,'Communication already decided')
            if datetime.fromisoformat(d['expires_at'])<datetime.now(timezone.utc):raise HTTPException(409,'Preview expired')
            current=await exact_preview(repo,p,Preview.model_validate(d['request']),lock=True)
            if current!=d['preview']:raise HTTPException(409,'Recipient, source or target changed; preview again')
            outcomes=[]
            if body.decision=='approve':
                for contact in current['recipients']:
                    outcomes.append(await record_message(repo,'communication-'+identifier,row['buildings'],contact['contact_id'],
                        current,'compliance' if current['purpose'] in CERT_PURPOSES else 'communications',p.user_id))
            result={'proposal_id':identifier,'status':'approved' if body.decision=='approve' else 'rejected',
                'communication_outcomes':[{'outbox_id':m['outbox_id'],'recipient_id':m['recipient_id'],
                    'status':m['status'],'attempts':m['attempts']} for m in outcomes],'duplicate':False}
            await repo.update('proposals',row,{**d,'status':result['status'],'decision':body.decision,
                'idempotency_key':body.idempotency_key,'result':result,'decided_at':now()})
            await repo.insert('history','communication_decision',row['buildings'],{
                'proposal_id':identifier,'actor':p.user_id,'preview_hash':d['preview_hash'],
                'decision':body.decision,'time':now(),'outbox_ids':[m['outbox_id'] for m in outcomes]},'comm-decision-'+identifier)
            await repo.audit('communications_confirm',result['status'],{'proposal_id':identifier})
            return result

    async def _recover(self,p,identifier,reason):
        identity(p)
        if p.role not in MANAGERS:raise HTTPException(403,'Assigned manager recovery required')
        from ..compliance_hw1.outbox import recover_message
        async with self.store.transaction(p) as repo:
            result=await recover_message(repo,p,identifier,reason)
            await repo.audit('communications_recovery',result['status'],{'outbox_id':identifier})
            return result


def hoi_history_page(rows, limit, offset):
    """Bound history before model handoff while retaining every underlying event."""
    if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
        raise HTTPException(422, 'Invalid history page')
    ordered=sorted(rows,key=lambda row:(str(row['data'].get('generated_at') or row['data'].get('time') or row['data'].get('calculated_at') or ''),row['id']),reverse=True)
    selected=ordered[offset:offset+limit]
    return {'history':[row['data'] for row in selected],
            'history_pagination':{'applies_to':'history_only','total_matching':len(ordered),'returned':len(selected),
                'offset':offset,'complete':offset==0 and len(selected)==len(ordered),
                'next_offset':offset+limit if offset+limit<len(ordered) else None}}
