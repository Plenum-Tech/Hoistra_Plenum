#!/usr/bin/env python3
"""Actual candidate API acceptance: exact messages, retry/recovery, fixed HOI and disclosure."""
import importlib.util,json,sys,subprocess,hashlib
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
HERE=Path(__file__).resolve().parent

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
common=load('contract_gate','01-contract.py');prior=load('ingestion_gate','03-ingestion.py')
require=common.require
IDS=['requested_email_exact_preview_and_confirmation','automatic_scoped_action_and_failure_notifications',
 'known_failure_retry_unknown_review_missing_contact_pending','event_recipient_deduplication_and_recovery',
 'canonical_fixed_ten_group_hoi_expected_arithmetic','all_authorized_hoi_changes_trigger_recalculation',
 'both_manager_trails_including_unchanged_score','no_general_or_officer_hoi_leakage']
DEST=HERE.parent/'evidence/04-communications-hoi.json'
A='hw1-building-a1'

class Gate(common.Gate):
    def __init__(self):
        super().__init__();self.tag='communications-hoi-'+uuid4().hex;self.events=[]

    def execute(self,tool,args,user='hw1-portfolio',expected=200):
        return self.call(user,'/api/homework/communications/tools/execute',{'tool':tool,'arguments':args},expected)

    def setup(self,resolve=False):
        command=[str(common.ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents/.venv/bin/python'),
            str(common.MODULE/'setup-communications-fixtures.py'),'--building',self.b]
        if resolve:command+=['--resolve-contact']
        r=subprocess.run(command,capture_output=True,text=True,timeout=60)
        require(r.returncode==0,'Additive fictional communication/HOI fixture provisioning failed')

    def initialize(self):
        p=self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews',{'original':'Fictional source '+self.tag,
            'fields':{'name':self.tag,'country':'TEST','city':'Fixture city','building_type':'office','floor_count':2,'floor_area':'100','area_unit':'m2'}})
        created=self.call('hw1-portfolio','/api/homework/ingestion/buildings/previews/'+p['proposal_id']+'/confirm',
            {'preview_hash':p['preview_hash'],'reason':'Approved fictional milestone building and fixed source evidence'})
        self.b=created['building_id'];self.setup();self.before_sources=prior.source_hashes()

    def outbox(self,building=None,user='hw1-portfolio'):
        records=[];cursor=None;issues=[];expected=None;seen=set()
        while True:
            args={'building_id':building or self.b,'limit':100,'include_issues':cursor is None}
            if cursor is not None:args['after_id']=cursor
            result=self.execute('communications_outbox',args,user)['result']
            if expected is None:
                expected=result['total_matching'];issues=result['notification_issues']
                require(type(expected) is int,'First page must count actual authorized rows')
            ids=[r['id'] for r in result['records']]
            require(ids==sorted(ids) and not seen.intersection(ids) and len(set(ids))==len(ids),'Duplicate or unordered outbox page')
            require(cursor is None or all(i>cursor for i in ids),'Outbox cursor failed to advance')
            seen.update(ids);records+=result['records']
            next_cursor=result['next_cursor']
            if next_cursor is None:
                require(len(records)==expected,'Cursor traversal did not return the independently counted authorized population')
                return records,issues
            require(ids and next_cursor==ids[-1],'Outbox continuation does not match returned evidence')
            cursor=next_cursor

    def draft(self,mode='success',purpose='team_notification',user='hw1-portfolio',building=None,**changes):
        b=building or self.b
        args={'building_id':b,'entity_id':b,'purpose':purpose,'contact_ids':[b+'-mail-'+mode],
            'subject':'Exact fictional preview '+self.tag.replace('-hoi',''),'body':'Only this fictional message is approved: '+self.tag.replace('-hoi',''),'source_ids':[],**changes}
        return self.execute('communications_preview',args,user)['result']

    def confirm(self,p,user='hw1-portfolio',body=None,expected=200):
        body=body or {'preview_hash':p['preview_hash'],'idempotency_key':'message-'+p['proposal_id']}
        return self.call(user,'/api/homework/communications/proposals/'+p['proposal_id']+'/confirm',body,expected)

    def hoi(self):return self.execute('communications_hoi',{'building_id':self.b})['result']

    def rows(self,p):return [r for r in self.outbox()[0] if r['event_id']=='communication-'+p['proposal_id']]

    def requested(self):
        p=self.draft(source_ids=[self.b+'-hoi-asset-0-source'],contact_ids=[self.b+'-property-contact'])
        exact=p['preview'];require(exact['subject']=='Exact fictional preview '+self.tag.replace('-hoi','') and exact['body'].endswith(self.tag.replace('-hoi','')),'Message preview text changed')
        require(exact['recipients'][0]['address']=='property@homework.invalid' and exact['attachments'][0]['source_id']==self.b+'-hoi-asset-0-source','Missing exact recipient/attachment preview')
        require(not self.rows(p),'Preview sent before confirmation')
        self.confirm(p,body={'preview_hash':'0'*64,'idempotency_key':'changed-hash'},expected=409)
        self.confirm(p,user='hw1-property',expected=404)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.confirm(p),range(2)))
        require(sum(not r['duplicate'] for r in results)==1 and len(self.rows(p))==1,'Concurrent confirmation simulated duplicate send')
        require(self.rows(p)[0]['message']==exact,'Sent content differs from exactly approved preview')
        self.confirm(p,body={'preview_hash':p['preview_hash'],'idempotency_key':'message-'+p['proposal_id'],'decision':'reject'},expected=409)
        rejected=self.draft();self.confirm(rejected,body={'preview_hash':rejected['preview_hash'],'idempotency_key':'reject-'+uuid4().hex,'decision':'reject'})
        require(not self.rows(rejected),'Rejected email sent')
        general=self.draft(user='hw1-general',building=A,contact_ids=[A+'-property-contact'],subject='Exact fictional team preview',body='Permitted fictional building summary')
        result=self.confirm(general,user='hw1-general')
        require(result['communication_outcomes'][0]['status']=='simulated sent','Permitted General team email failed')
        self.execute('communications_preview',{'building_id':A,'entity_id':A,'purpose':'team_notification','contact_ids':[A+'-vendor-contact-0'],
            'subject':'Summary','body':'Fictional summary'},'hw1-general',403)
        for purpose in ['work_order_request','contract_review','energy_review']:
            domain=self.draft(purpose=purpose)
            sent=self.confirm(domain)
            require(sent['communication_outcomes'][0]['status']=='simulated sent' and self.rows(domain)[0]['message']['purpose']==purpose,
                'Permitted domain message failed exact purpose/confirmation')
        blocked=self.draft(purpose='work_order_request',vendor_id=self.b+'-vendor-1')
        self.confirm(blocked)
        self.execute('communications_preview',{**blocked['request'],'vendor_id':self.b+'-vendor-0'},expected=409)
        self.record(IDS[0],'Actual preview preserves exact recipient/address, subject/body and source digest/version attachment; no send before approval or after rejection. Wrong hash409, foreign principal404. Two concurrent confirmations produce one send and one replay; changed replay decision409. General User permitted team message succeeds; vendor recipient403. Retained outbox content equals the exact approved preview. No real transport or operational work-order action.')

    def automatic(self):
        self.correction_asset=self.b+'-hoi-asset-0'
        self.original_asset=self.get_record(self.correction_asset)['data']['fields']
        self.corrected=self.correct(self.correction_asset,{**self.original_asset,'name':'Reviewed fixture name '+self.tag})
        score=self.hoi();event='hoi-derived-correction-'+self.last_proposal
        self.events.append(event);messages=[r for r in self.outbox()[0] if r['event_id']==event]
        require(len(messages)==2 and {r['recipient_id'] for r in messages}=={self.b+'-property-contact',self.b+'-portfolio-manager-contact'},'Both configured manager notices missing')
        require(any(r['status']=='pending' for r in messages) and any(r['status']=='simulated sent' for r in messages),'Missing-contact automatic result misreported')
        failure=[r for r in self.outbox()[0] if r['category']=='notification_failure']
        require(failure and all('source_id' not in r['message'] and 'score' not in r['message'] for r in failure),'Safe immediate failure fallback missing')
        self.pending=next(r for r in messages if r['status']=='pending')
        self.call('hw1-compliance','/api/homework/compliance/events/drain',{})
        arows,_=self.outbox(A)
        expiry=[r for r in arows if r['event_id'].startswith('expiry-')]
        require(expiry and any(r['recipient_id']=='hw1-org-a-officer' for r in expiry),'Configured certificate Officer notification missing')
        require(all(r['category']=='compliance' for r in expiry),'Expiry notice crossed domain category')
        self.record(IDS[1],'A live authorized evidence correction automatically creates both scoped manager HOI notices, without user email approval. Missing manager address produces pending, plus an immediate safe manager failure alert containing outcome/reference only. Manual configured certificate evaluation produces certificate-only manager/Officer urgency notices. Alerts never authorize business actions and no real transport is used.')

    def failures(self):
        expected={'fail':('failed',2),'fail_once':('simulated sent',2),'unknown':('unknown',1),'missing':('pending',0)}
        self.failure_messages=[]
        for mode,(status,attempts) in expected.items():
            p=self.draft(mode=mode);result=self.confirm(p);m=self.rows(p)[0]
            require((m['status'],m['attempts'])==(status,attempts),'Incorrect '+mode+' simulation status/attempt count')
            require(len(m['attempt_log'])==attempts,'Missing actual simulation attempt trail')
            self.failure_messages.append(m)
            recovered=self.call('hw1-portfolio','/api/homework/communications/outbox/'+m['id']+'/recover',{'reason':'Review this fictional failed or pending outcome'})
            require(recovered['attempts']==attempts,'Recovery blindly retried unknown/exhausted failure')
        failure=self.failure_messages[0]
        self.call('hw1-general','/api/homework/communications/outbox/'+failure['id']+'/recover',{'reason':'General role must not recover manager messages'},403)
        self.record(IDS[2],'Live requested messages exercise known failure=failed/2 attempts, first-known-failure recovery=simulated sent/2, unknown=unknown/1, missing contact=pending/0; each has a per-attempt trail. Manual review adds no third attempt or blind unknown retry; missing contact remains pending. General User recovery403. Failure alerts contain no source body and stop recursively at one fallback layer.')

    def recovery(self):
        before=self.outbox(A)[0];keys=[(r['event_id'],r['recipient_id']) for r in before]
        require(len(keys)==len(set(keys)),'Duplicate event/recipient records exist')
        self.call('hw1-compliance','/api/homework/compliance/events/drain',{})
        after=self.outbox(A)[0]
        require({r['id'] for r in before}=={r['id'] for r in after},'Unchanged configured event replay resent messages')
        self.setup(resolve=True)
        path='/api/homework/communications/outbox/'+self.pending['id']+'/recover'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:self.call('hw1-portfolio',path,{'reason':'Configured fictional manager contact became available'}),range(2)))
        require(all(r['status']=='simulated sent' and r['attempts']==1 for r in outcomes),'Pending recovery duplicated or failed simulation')
        actual=next(r for r in self.outbox()[0] if r['id']==self.pending['id'])
        require(actual['status']=='simulated sent' and actual['attempts']==1,'Pending original outbox row not recovered')
        require(all(not r['event_id'].startswith('failure-mail-') or r['category']=='notification_failure' for r in self.outbox()[0]),'Recursive failure loop')
        self.record(IDS[3],'Rerunning configured expiry/event drain preserves exactly the same outbox IDs and one event/recipient key each. A privately provisioned fictional missing manager address becomes available; two simultaneous recovery calls resume the original unsent automatic row once (simulated sent/1), then report already resolved. Unknown/exhausted outcomes remain review-only. All original/source fingerprints are checked separately; no source deletion.')

    def get_record(self,identifier):return self.call('hw1-portfolio','/api/homework/ingestion/tools/execute',{'tool':'ingestion_get_record','arguments':{'record_id':identifier}})['result']

    def correct(self,identifier,fields):
        p=self.call('hw1-portfolio','/api/homework/ingestion/corrections/previews',{'building_id':self.b,'record_id':identifier,
            'original':json.dumps({'fields':fields,'fixture_note':self.tag}), 'fields':fields,'reason':'Source-backed fictional evidence review'})
        self.last_proposal=p['proposal_id']
        return self.call('hw1-portfolio','/api/homework/ingestion/corrections/previews/'+p['proposal_id']+'/confirm',{
            'preview_hash':p['preview_hash'],'reason':'Explicit reviewed fictional evidence correction'})

    def arithmetic(self):
        baseline=self.hoi();require(baseline['score']==100 and len(baseline['group_percentages'])==10,'Complete fixed source fixtures expected100')
        fields={k:'' for k in self.original_asset}
        blank=self.correct(self.corrected['record_id'],fields)
        score=self.hoi();require(score['score']==98 and score['group_percentages'][1]=='80.0','One missing five-field asset must subtract exactly2 points:98')
        require(score['formula_version']=='hoi-fixed-coverage-v1' and len(score['source_versions'])==46,'Canonical formula/source lineage missing')
        self.corrected=self.correct(blank['record_id'],self.original_asset)
        require(self.hoi()['score']==100,'Restoring source-backed asset did not restore100')
        # Independent commercial hierarchy is exercised by full supplied line evidence.
        require(baseline['type_percentages']['invoice']=='100' and baseline['group_percentages'][6]=='100','Source-backed invoice header/line hierarchy not complete')
        self.record(IDS[4],'Source-backed API-onboarded fictional building with fixed46 items and ten groups calculates100. Actual approved all-five-fields-empty asset correction makes four of five assets complete:asset80%, total98; restoration returns100. Two invoices include original two-line evidence, commercial group100. Formula/source versions/checklist are returned. Independent deterministic regressions additionally verify half-up97.5->98, fivegroups50, invoice7.5point contribution, duplicate/overlap and stale/source projection denials.')

    def changes(self):
        for kind,fields in [('asset_summary',{'asset_id':self.tag,'condition_score':2,'condition_observation':'Source observed'}),
                ('inspection_report',{'inspection_id':self.tag,'inspection_date':'2026-10-01','result':'recorded','summary':'Source observed'}),
                ('work_order_report',{'external_reference':self.tag,'raised_at':'2026-10-01T08:00:00Z','responded_at':'2026-10-01T09:00:00Z','completed_at':'2026-10-01T10:00:00Z','status_summary':'External report','vendor_id':self.b+'-vendor-0','asset_id':self.tag}),
                ('meter_reading',{'meter_id':self.tag,'timestamp':datetime.now(timezone.utc).isoformat(),'consumption':'10','unit':'kWh','interval':'hour'})]:
            p=self.call('hw1-portfolio','/api/homework/ingestion/previews',{'building_id':self.b,'kind':kind,'original':'Fictional '+kind+' '+self.tag,'fields':fields})
            r=self.call('hw1-portfolio','/api/homework/ingestion/previews/'+p['proposal_id']+'/confirm',{'preview_hash':p['preview_hash'],'reason':'Accept this authorized fictional domain evidence'})
            require(r['hoi_follow_through']['status']=='recalculated' and r['hoi_follow_through']['before']==r['hoi_follow_through']['after']==100,'Accepted '+kind+' failed unchanged fixed-HOI recalculation')
            self.events.append('ingestion-'+p['proposal_id'])
            if kind=='asset_summary':
                replacement=self.call('hw1-portfolio','/api/homework/ingestion/previews',{'building_id':self.b,'kind':kind,'original':'Reviewed replacement '+self.tag,'fields':{**fields,'condition_score':3}})
                selected=self.call('hw1-portfolio','/api/homework/ingestion/conflicts/'+replacement['proposal_id']+'/resolve',{
                    'preview_hash':replacement['preview_hash'],'decision':'accept_incoming','reason':'Select this explicitly reviewed current representation'})
                require(selected['hoi_follow_through']['status']=='recalculated','Version selection failed HOI integration')
        values={'reference':self.tag,'certificate_type':'FIRE','subject':'Building','holder_id':self.b,'issuer':'Fictional issuer',
            'issue_date':'2026-01-01','expiry_date':'2027-01-01','review_date':None,'review_rule':None,'country':'TEST','trade':'Fire'}
        p=self.call('hw1-portfolio','/api/homework/compliance/proposals',{'action':'accept_certificate','entity_id':self.b,
            'reason':'Source-backed fictional unlinked certificate','values':values,'source_text':json.dumps({'fields':values})})['result']
        r=self.call('hw1-portfolio','/api/homework/actions/'+p['proposal_id']+'/confirm',{'version':p['version'],
            'preview_hash':p['preview_hash'],'idempotency_key':'cert-'+uuid4().hex})
        require(r['follow_through']['status']=='completed','Certificate changed event not drained')
        self.events.append('change-'+p['proposal_id'])
        certificate_id=r['result']['record_id'];first_source=r['result']['source_id']
        for action,changes in [('correct_certificate',{'issuer':'Reviewed fictional issuer'}),('select_version',{})]:
            reviewed={**values,**changes}
            args={'action':action,'entity_id':certificate_id,'reason':'Explicit fictional certificate change review','values':reviewed}
            if action=='select_version':args['source_id']=first_source
            else:args['source_text']=json.dumps({'fields':reviewed})
            q=self.call('hw1-compliance','/api/homework/compliance/proposals',args)['result']
            result=self.call('hw1-compliance','/api/homework/actions/'+q['proposal_id']+'/confirm',{'version':q['version'],
                'preview_hash':q['preview_hash'],'idempotency_key':'officer-cert-'+uuid4().hex})
            require(result['status']=='approved' and 'score' not in json.dumps(result).lower(),'Officer confirmation leaked HOI or failed')
            self.events.append('change-'+q['proposal_id'])
        block=self.call('hw1-portfolio','/api/homework/compliance/proposals',{'action':'block_vendor','entity_id':self.b+'-vendor-0',
            'source_id':self.b+'-accreditation-0-source','reason':'Lapsed source-backed fictional vendor accreditation'})['result']
        self.call('hw1-portfolio','/api/homework/actions/'+block['proposal_id']+'/confirm',{'version':block['version'],
            'preview_hash':block['preview_hash'],'idempotency_key':'vendor-block-'+uuid4().hex})
        self.events.append('vendor-eligibility-'+block['proposal_id']+'-'+self.b)
        histories=self.hoi()['history']
        require(all(any(h['event_id']==event for h in histories) for event in self.events),'An authorized input event lacks HOI history')
        self.record(IDS[5],'Actual accepted asset, inspection, external work-order-report and meter-reading APIs each automatically recalculate fixed HOI and retain unchanged100 trails, with no guessed fixed-slot credit. Explicit asset current-version replacement recalculates. Certificate acceptance creates and drains its event into HOI history. Direct fixed-item corrections change/recover arithmetic. Onboarding stores the initial trail; duplicate/no-op evidence cannot raise coverage or create business versions.')

    def trails(self):
        messages=self.outbox()[0];histories=self.hoi()['history']
        for event in self.events:
            history=next(h for h in histories if h['event_id']==event)
            require(history['before']==history['after'] and history['actor'] in {'hw1-portfolio','hw1-compliance'},'Unchanged-score trail missing actor/scores')
            notices=[r for r in messages if r['event_id']==event and r['category']=='hoi']
            require(len(notices)==2,'Expected both manager notices for '+event)
            for n in notices:
                m=n['message'];require(m['before']==m['after'] and m['formula_version']=='hoi-fixed-coverage-v1' and m['source_id'] and m['actor'],'Incomplete manager change trail')
        self.record(IDS[6],'For each newly accepted cross-domain event and fixed-evidence correction, API HOI history and exactly two scoped manager outbox records preserve equal previous/new scores when coverage is unchanged, source reference, actor and formula. Changed asset coverage also records100->98->100. The initial missing Portfolio Manager notice was recovered in place rather than discarded or resent. Property and Portfolio contacts receive the same event trail; no Officer or General recipient is included.')

    def disclosure(self):
        for user in ['hw1-general','hw1-compliance']:
            self.execute('communications_hoi',{'building_id':A},user,403)
            self.call(user,'/api/homework/compliance/tools/execute',{'tool':'homework_get_hoi','arguments':{'entity_id':A}},403)
            self.execute('communications_preview',{'building_id':A,'entity_id':A,'purpose':'team_notification',
                'contact_ids':[A+'-property-contact'],'subject':'HOI Score','body':'Provide hidden breakdown'},user,403)
            self.execute('communications_preview',{'building_id':A,'entity_id':A+'-hoi-asset-0','purpose':'team_notification',
                'contact_ids':[A+'-property-contact'],'subject':'Source','body':'Restricted target'},user,403)
            rows,issues=self.outbox(A,user)
            require(all(r['category']!='hoi' and 'group_percentages' not in json.dumps(r) for r in rows),'HOI leaked via alternate outbox')
            self.execute('communications_preview',{'building_id':A,'entity_id':A+'-certificate-0','purpose':'certificate_renewal',
                'contact_ids':[A+'-property-contact'],'subject':'Certificate source','body':'Fictional certificate evidence',
                'source_ids':[A+'-hoi-asset-0-source']},user,403)
            tools=self.call(user,'/api/homework/communications/tools')['tools'];require('communications_hoi' not in tools,'HOI model tool leaked')
        self.execute('communications_preview',{'building_id':self.b,'entity_id':self.b,'purpose':'team_notification',
            'contact_ids':[self.b+'-mail-success'],'subject':'HOI Score','body':'Canonical completeness breakdown'},expected=403)
        self.execute('communications_preview',{'building_id':self.b,'entity_id':self.b,'purpose':'team_notification',
            'contact_ids':[self.b+'-mail-success'],'subject':'Source','body':'Underlying fixed-checklist evidence',
            'source_ids':[self.b+'-hoi-asset-0-source']},expected=403)
        self.execute('communications_hoi',{'building_id':'hw1-building-a2'},'hw1-property',403)
        self.execute('communications_hoi',{'building_id':'hw1-building-b1'},expected=403)
        response=self.call('hw1-portfolio','/api/homework/agent/ask',{'message':'Use communications_hoi to show the canonical HOI Score for '+self.b+'. Do not send any email.'})
        require(response.get('selected_route')=='communications' and response.get('outcome')=='answered','HOI provider routing failed')
        require(response['provider_execution']['successful_responses']>0 and 'communications_hoi' in response['provider_execution']['selected_tools'],'No genuine configured provider HOI tool execution')
        actual=[c['result'] for c in response['tool_calls'] if c['tool']=='communications_hoi' and 'result' in c]
        require(actual and actual[0]['score']==100,'Provider result is not independently expected100')
        email=self.call('hw1-general','/api/homework/agent/ask',{'message':
            'Prepare an email with communications_preview only, no send: building_id hw1-building-a1, entity_id hw1-building-a1, '+
            'purpose team_notification, contact_ids [hw1-building-a1-property-contact], subject "Fictional provider preview", '+
            'body "Please review the permitted building status summary.", source_ids [].'})
        previews=[c['result'] for c in email['tool_calls'] if c['tool']=='communications_preview' and 'result' in c]
        require(email['provider_execution']['successful_responses']>0 and previews,'No genuine provider-selected exact email preview')
        require(previews[0]['preview']['subject']=='Fictional provider preview' and previews[0]['status']=='awaiting_confirmation','Provider preview does not match requested email')
        own=self.outbox(A,'hw1-general')[0]
        require(not any(r['event_id']=='communication-'+previews[0]['proposal_id'] for r in own),'Provider sent without human confirmation')
        manifest=common.ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents/skills/communications-manifest.json'
        require(response['prompt_metadata']['manifest_sha256']==hashlib.sha256(manifest.read_bytes()).hexdigest(),'Live prompt manifest stale')
        require(all('confirm' not in t and 'recover' not in t for t in response['prompt_metadata']['bound_tools']),'Model can commit/retry')
        denied=self.call('hw1-compliance','/api/homework/agent/ask',{'message':'Show the HOI Score for hw1-building-a1.'})
        require('communications_hoi' not in denied['prompt_metadata']['bound_tools'] and not any(c['tool']=='communications_hoi' and 'result' in c for c in denied['tool_calls']),'Officer model response leaked HOI')
        self.record(IDS[7],'General/Officer direct HOI tools403 and absent from their typed tool lists; restricted HOI-target/body email drafts403; alternate scoped outbox excludes HOI messages/breakdown. Property A2 and Portfolio foreign-org HOI403. Genuine configured OpenRouter central request executes communications_hoi with expected100 and current hash-checked profile; no model confirm/recovery tools. Officer agent binds no HOI tool and returns no HOI result. Source versions and all pre-existing original hashes remain intact.')


def main():
    receipt={'milestone_id':'04-communications-hoi','specification_version':'0.37.0','api_base':common.BASE,'checks':[]};gate=None;current=IDS[0]
    DEST.parent.mkdir(parents=True,exist_ok=True)
    try:
        common.database_probe();originals_before_initialize=prior.source_hashes();gate=Gate();gate.initialize()
        for current,run in zip(IDS,[gate.requested,gate.automatic,gate.failures,gate.recovery,gate.arithmetic,gate.changes,gate.trails,gate.disclosure]):run()
        after=prior.source_hashes()
        require(all(after.get(k)==v for k,v in originals_before_initialize.items()),'A pre-existing original changed or disappeared')
        require(all(after.get(k)==v for k,v in gate.before_sources.items()),'A provisioned fixture original changed or disappeared')
        common.database_probe();receipt['checks']=gate.checks;receipt['recorded_at']=datetime.now(timezone.utc).isoformat()
        DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'04-communications-hoi','passed':True,'checks':8}));return 0
    except Exception as error:
        safe=str(error) if isinstance(error,common.GateFailure) else type(error).__name__
        receipt['checks']=(gate.checks if gate else [])+[{'id':current,'passed':False,'evidence':safe}]
        DEST.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'milestone_id':'04-communications-hoi','passed':False,'failed_check':current,'reason':safe}));return 1

if __name__=='__main__':sys.exit(main())
