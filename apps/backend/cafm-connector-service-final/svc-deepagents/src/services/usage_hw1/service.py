"""Measured usage snapshots and manual weekly reports in the isolated schema."""
from datetime import date, datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo
from uuid import uuid5, NAMESPACE_URL
import hashlib,json
from sqlalchemy import text
from fastapi import HTTPException
from ..compliance_hw1.store import runtime_store
from ..compliance_hw1.outbox import record_message
from ...api.homework_identity import TestPrincipal

LOCAL=ZoneInfo("Asia/Dubai")
ROLES={"general_user","property_manager","portfolio_manager","compliance_officer"}

async def record_measurement(principal,request_id,domain,conversation_id,usage,step=0):
    """Persist actual provider usage metadata; absent token usage stays explicitly unavailable."""
    # Keep the verified read-only facade free of additional writes until its isolated
    # usage profile is explicitly enabled.
    if os.environ.get("HW1_USAGE_ENABLED", "false").lower() != "true":
        return
    if not isinstance(principal,TestPrincipal) or principal.role not in ROLES:
        raise HTTPException(401,"Trusted principal required")
    values=usage if isinstance(usage,dict) else {}
    input_tokens=values.get("input_tokens",values.get("prompt_tokens"))
    output_tokens=values.get("output_tokens",values.get("completion_tokens"))
    valid=lambda value:type(value) is int and value>=0
    measured=valid(input_tokens) and valid(output_tokens)
    if not measured: input_tokens=output_tokens=None
    event_id=uuid5(NAMESPACE_URL,f"usage:{request_id}:{domain}:{conversation_id or ''}:{step}").hex
    async with runtime_store().transaction(principal) as repo:
        if hasattr(repo,'session'):
            await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':event_id})
            if any(r['id']==event_id for r in await repo.list('history','usage_measurement')):return
        await repo.insert("history","usage_measurement",list(principal.building_ids),{
            "event_id":event_id,"actor":principal.user_id,"role":principal.role,
            "category":"compliance" if domain=="compliance" else domain,
            "domain":domain,"conversation_id":conversation_id,"request_id":request_id,
            "measurement_state":"measured" if measured else "token_usage_unavailable",
            "input_tokens":input_tokens,"output_tokens":output_tokens,
            "generated_at":datetime.now(timezone.utc).isoformat()},event_id)

def stamp(data):
    raw=data.get('generated_at',data.get('time',data.get('created_at')))
    try:
        value=datetime.fromisoformat(raw.replace('Z','+00:00'))
        return value if value.tzinfo else None
    except (TypeError,ValueError,AttributeError):return None


def period(start,cadence):
    if cadence=='weekly':
        if start.weekday()!=0:raise HTTPException(422,'Weekly report begins on Monday in Asia/Dubai')
        stop=start+timedelta(days=7)
    elif cadence=='monthly':
        if start.day!=1:raise HTTPException(422,'Monthly report begins on the first calendar day')
        stop=date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)
    else:raise HTTPException(422,'Unknown report cadence')
    begin=datetime.combine(start,time.min,LOCAL).astimezone(timezone.utc)
    end=datetime.combine(stop,time.min,LOCAL).astimezone(timezone.utc)
    return {'start':str(start),'end':str(stop-timedelta(days=1)),'timezone':'Asia/Dubai',
        'start_utc':begin.isoformat(),'end_exclusive_utc':end.isoformat(),
        'interval':'[Monday 00:00, next Monday 00:00)' if cadence=='weekly' else '[month start 00:00, next month start 00:00)'},begin,end


def visible(row,p,scope):
    if row['org']!=p.organization_id or not row['buildings'] or not set(row['buildings'])<=set(scope):return False
    d=row['data']
    if p.role=='general_user':return d.get('actor',d.get('owner',d.get('proposer')))==p.user_id
    if p.role=='compliance_officer':
        return d.get('category')=='compliance' or d.get('selected_route')=='compliance' or (
            row['kind']=='communication' and d.get('request',{}).get('purpose') in {'certificate_renewal','inspection_booking'}) or (
            row['kind']=='compliance' and d.get('request',{}).get('action') in
            {'accept_certificate','correct_certificate','select_version','request_renewal','approve_booking','block_vendor','unblock_vendor'})
    return True


def scoped_rows(rows,p,scope):return [r for r in rows if visible(r,p,scope)]


def hash_value(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


class UsageService:
    def __init__(self,store=None):self.store=store or runtime_store()

    async def weekly_report(self,p,period_start,request_id=None,building_ids=None):
        return await self.report(p,period_start,'weekly',request_id,building_ids)

    async def monthly_report(self,p,period_start,request_id=None,building_ids=None):
        return await self.report(p,period_start,'monthly',request_id,building_ids)

    async def report(self,p,period_start,cadence,request_id=None,building_ids=None):
        if not isinstance(p,TestPrincipal) or p.role not in ROLES:raise HTTPException(401,'Trusted principal required')
        from ..compliance_hw1.authorization import buildings
        if hasattr(self.store,'audit'):await self.store.audit(p,'usage_report','requested',request_id=request_id)
        try:
            scope=buildings(p,building_ids)
            window,begin,end=period(period_start,cadence)
        except HTTPException:
            if hasattr(self.store,'audit'):await self.store.audit(p,'usage_report','scope_or_period_denied',request_id=request_id)
            raise
        if hasattr(self.store,'audit'):await self.store.audit(p,'usage_report','capability_authorized',request_id=request_id)
        async with self.store.transaction(p) as repo:
            # Lock before reading mutable backlog so concurrent identical triggers share one snapshot.
            family=hash_value([p.organization_id,p.role,p.user_id if p.role=='general_user' else None,scope,cadence,str(period_start)])
            if hasattr(repo,'session'):
                await repo.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'usage-'+family})
            snapshot=await repo.usage_snapshot(scope) if hasattr(repo,'usage_snapshot') else {
                'history':await repo.list('history'),'proposals':await repo.list('proposals'),'records':await repo.list('records')}
            history=snapshot['history']
            allowed=scoped_rows(history,p,scope)
            kinds={'usage_measurement','trace','unified_trace','usage_failure','ingestion','ingestion_correction',
                'ingestion_conflict_resolution','building_onboarding','decision'}
            selected=[r for r in allowed if r['kind'] in kinds and stamp(r['data']) is not None and begin<=stamp(r['data'])<end]
            proposals=scoped_rows(snapshot['proposals'],p,scope)
            pending=[r for r in proposals if r['data'].get('status') in {'previewed','awaiting_confirmation'}]
            backlog=[{'id':r['id'],'kind':r['kind'],'status':r['data']['status'],
                'version':r['data'].get('version',1),'preview_hash':r['data'].get('preview_hash')} for r in pending]
            backlog.sort(key=lambda r:r['id'])
            source_ids=sorted(r['id'] for r in selected)
            recipients=await self.recipients(repo,p,scope,[r for r in snapshot['records'] if r['kind']=='contact'])
            recipient_config=[{'id':c['id'],'buildings':c['buildings'],'data_sha256':hash_value(c['data'])} for c in recipients]
            fingerprint=hash_value({'sources':source_ids,'backlog':backlog,'recipient_configuration':recipient_config})
            report_id=uuid5(NAMESPACE_URL,'usage:'+family+':'+fingerprint).hex
            prior=[r for r in history if r['kind']=='usage_report' and r['data'].get('family_id')==family]
            previous=next((r for r in prior if r['id']==report_id),None)
            if previous:
                await repo.audit('usage_report','already_generated',{'report_id':report_id},request_id)
                return previous['data']
            measurements=[r for r in selected if r['kind']=='usage_measurement']
            central=[r for r in selected if r['kind']=='unified_trace']
            request_ids={r['data'].get('request_id') for r in central}
            traces=central+[r for r in selected if r['kind']=='trace' and r['data'].get('request_id') not in request_ids]
            failures=[r for r in selected if r['kind']=='usage_failure']
            measured=[r for r in measurements if r['data'].get('measurement_state')=='measured']
            token_usage={'input_tokens':sum(r['data']['input_tokens'] for r in measured) if measured else None,
                'output_tokens':sum(r['data']['output_tokens'] for r in measured) if measured else None,
                'measurement':'provider-reported token usage' if measured else 'unavailable; provider did not expose token usage',
                'population':'source observations; explicit fictional boundary fixtures are separately counted',
                'measured_calls':len(measured),'complete_for_provider_calls':bool(measurements) and len(measured)==len(measurements)}
            domains={}
            for r in measurements:
                domain=r['data'].get('domain','unknown');domains[domain]=domains.get(domain,0)+1
            sources_by_kind={kind:sorted(r['id'] for r in selected if r['kind']==kind) for kind in kinds}
            managers=p.role in {'property_manager','portfolio_manager'}
            intake=[r for r in selected if r['kind']=='ingestion' and r['data'].get('outcome')=='accepted']
            corrections=[r for r in selected if (r['kind']=='ingestion_correction' and r['data'].get('outcome')=='applied') or
                (r['kind']=='ingestion_conflict_resolution' and r['data'].get('decision')=='accept_incoming')]
            # No claim that observed provider users/sessions measure the entire product population.
            metrics={'active_users':None,'sessions':None,'subscription_charges':None,'model_api_cost':None,
                'configured_usage_limit':None,'building_coverage_trend':None,
                'provider_active_users':len({r['data']['actor'] for r in measurements}),
                'provider_sessions':len({r['data'].get('conversation_id') for r in measurements if r['data'].get('conversation_id')}),
                'provider_calls':len(measurements),
                'measurement_origins':{'live_provider_receipts':sum(not r['data'].get('measurement_origin') for r in measurements),
                    'explicit_fictional_boundary_observations':sum(bool(r['data'].get('measurement_origin')) for r in measurements)},
                'provider_requests_by_domain':domains,'provider_token_usage':token_usage,
                'observed_agent_requests':len(traces),'ingestion_successes':len(intake),
                'ingestion_corrections':len(corrections),'ingestion_failures':sum(r['data'].get('domain')=='ingestion' for r in failures),
                'ingestion_backlog':sum(r['kind']=='ingestion' for r in pending),
                'buildings_onboarded':sum(r['kind']=='building_onboarding' for r in selected),
                'approval_backlog':len(pending),'processing_failures':len({r['data'].get('request_id',r['id']) for r in failures+[t for t in traces if t['data'].get('outcome')=='failed']})}
            if not managers:
                metrics['assigned_buildings']=None
            else:
                metrics['assigned_buildings']=sum(r['kind']=='building' and r['id'] in scope for r in snapshot['records'])
            generated=datetime.now(timezone.utc).isoformat()
            scope_label='own' if p.role=='general_user' else 'organization-compliance' if p.role=='compliance_officer' else scope
            data={'report_id':report_id,'family_id':family,'version':max((r['data'].get('version',0) for r in prior),default=0)+1,
                'cadence':cadence,'reporting_period':window,'trigger':'manual','generated_at':generated,
                'intended_cadence':'Monday 09:00 Asia/Dubai' if cadence=='weekly' else 'calendar month; manual trigger',
                'scope':scope_label,'building_ids':scope,'actor':p.user_id,'role':p.role,'metrics':metrics,
                'unavailable_metrics':['active-user/session counts beyond observed agent traces','subscription charges',
                    'model/API cost without verified pricing receipt','usage-limit thresholds not configured',
                    'building coverage trends without historical coverage telemetry','processing failures outside recorded adapters']+
                    (['provider token usage'] if not measured else []),
                'source_ids':source_ids,'source_events_by_kind':sources_by_kind,
                'source_freshness':max((stamp(r['data']) for r in selected),default=None),
                'recipient_contact_ids':[c['id'] for c in recipients],'recipient_config_sha256':hash_value(recipient_config),
                'backlog_as_of':generated,'backlog_source_ids':sorted(r['id'] for r in pending),'snapshot_sha256':fingerprint,
                'measurement_coverage':'observed source events only; missing/out-of-scope telemetry is not zero product activity',
                'recipient_scope':'configured same-role contacts covering the entire report scope; General User own contact only'}
            # JSON-normalize timestamps before retaining/returning to guarantee exact replay equality.
            data=json.loads(json.dumps(data,default=str))
            data['delivery']=await self.deliver(repo,p,data,recipients)
            await repo.insert('history','usage_report',scope,data,report_id)
            await repo.audit('usage_report','generated',{'report_id':report_id,'cadence':cadence},request_id)
            return data

    async def recipients(self,repo,p,scope,contacts=None):
        scope=set(scope);recipients=[]
        for c in (contacts if contacts is not None else await repo.list('records','contact')):
            d=c['data']
            if c['org']!=p.organization_id or not d.get('scope_verified') or not scope<=set(c['buildings']):continue
            if 'usage_digest' not in d.get('purposes',[]):continue
            match=(d.get('user_id')==p.user_id if p.role=='general_user' else
                   d.get('officer') if p.role=='compliance_officer' else d.get('manager_role')==p.role)
            if match:recipients.append(c)
        recipients.sort(key=lambda c:c['id'])
        return recipients

    async def deliver(self,repo,p,data,recipients):
        if not recipients:
            await repo.insert('history','notification_issue',data['building_ids'],{'event_id':'usage-'+data['report_id'],
                'status':'pending','reason':'no configured in-scope usage digest contact'},'usage-pending-'+data['report_id'])
            return [{'recipient_role':p.role,'status':'pending','reason':'no configured recipient'}]
        results=[]
        for c in recipients:
            message=await record_message(repo,'usage-'+data['report_id'],data['building_ids'],c['id'],
                {'report_id':data['report_id'],'reporting_period':data['reporting_period'],'metrics':data['metrics'],
                 'role_scope':data['scope'],'generated_at':data['generated_at']},
                'compliance' if p.role=='compliance_officer' else 'usage_digest',p.user_id,True)
            results.append({'recipient_id':c['id'],'outbox_id':message['outbox_id'],'status':message['status'],'attempts':message['attempts']})
        return results

    async def retrieve(self,p,report_id):
        if not isinstance(p,TestPrincipal):raise HTTPException(401,'Trusted principal required')
        if hasattr(self.store,'audit'):await self.store.audit(p,'usage_report_read','capability_authorized',{'report_id':report_id})
        async with self.store.transaction(p) as repo:
            row=await repo.get('history',report_id);d=row['data']
            valid=(row['kind']=='usage_report' and row['org']==p.organization_id and row['buildings'] and
                   set(row['buildings'])<=set(p.building_ids) and
                   (d.get('actor')==p.user_id if p.role=='general_user' else
                    d.get('scope')=='organization-compliance' and d.get('role')=='compliance_officer' if p.role=='compliance_officer' else
                    isinstance(d.get('scope'),list)))
            if not valid:raise HTTPException(404,'Usage report unavailable')
            await repo.audit('usage_report_read','succeeded',{'report_id':report_id})
            return d
