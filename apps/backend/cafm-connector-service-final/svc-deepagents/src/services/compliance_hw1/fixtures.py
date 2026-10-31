"""Reproducible fictional records; never copies production or Cartwheel data."""
from datetime import date,timedelta
import json
from .hoi import TYPES,LINE_FIELDS,calculate

CLOCK=date(2026,10,25)
BUILDINGS={'hw1-building-a1':'hw1-org-a','hw1-building-a2':'hw1-org-a','hw1-building-b1':'hw1-org-b'}


def dataset():
    records=[];sources=[];histories=[]
    def record(identifier,kind,org,buildings,data):
        row={'id':identifier,'kind':kind,'org':org,'buildings':buildings,'data':{'scope_verified':True,**data}}
        records.append(row)
        return row
    for org in sorted(set(BUILDINGS.values())):
        org_buildings=[b for b,o in BUILDINGS.items() if o==org]
        record('clock-'+org,'clock',org,[],{'date':str(CLOCK),'fixture_version':'compliance-fixtures-v1'})
        record('pack-'+org,'pack',org,[],{'country':'TEST','version':'fictional-pack-v1','types':[
            {'code':'FIRE','subject':'Building','trade':'Fire','applicability':'fixture declared fire systems'},
            {'code':'ELECTRICAL','subject':'Building','trade':'Electrical','applicability':'fixture declared installation'},
            {'code':'LIFT','subject':'Building','trade':'Lift','applicability':'only a fixture building with lift'},
            {'code':'VENDOR_FIRE','subject':'Vendor','trade':'Fire','applicability':'fixture fire vendor'},
            {'code':'INSURANCE','subject':'Vendor','trade':'Insurance','applicability':'fixture vendor policy'}]})
        record(org+'-default-portfolio','portfolio',org,org_buildings,
            {'portfolio_id':org+'-default-portfolio','version':1,'fixture_version':'compliance-fixtures-v1'})
        record(org+'-officer','contact',org,org_buildings,{'email':'officer@homework.invalid','officer':True,
            'contact_type':'compliance','purposes':['certificate_renewal','inspection_booking']})
        record(org+'-portfolio-contact','contact',org,org_buildings,{'email':'portfolio@homework.invalid',
            'manager_role':'portfolio_manager','contact_type':'team','purposes':['certificate_renewal','inspection_booking','team_notification']})
    for building,org in BUILDINGS.items():
        record(building,'building',org,[building],{'name':'Test '+building,'version':1})
        record(building+'-property-contact','contact',org,[building],{'email':'property@homework.invalid',
            'manager_role':'property_manager','contact_type':'team','purposes':['certificate_renewal','inspection_booking','team_notification']})
        for simulation in ['fail','unknown','missing']:
            record(building+'-'+simulation+'-contact','contact',org,[building],
                {'email':None if simulation=='missing' else simulation+'@homework.invalid',
                 'simulation':simulation,'contact_type':'team','purposes':['team_notification','certificate_renewal']})
        for n in range(2):
            record(building+'-vendor-'+str(n),'vendor',org,[building],
                {'name':'Test vendor '+str(n),'version':1,'blocked':n==0,
                 'blocking_reasons':[{'id':'expiry-original','category':'certificate','certificate_id':building+'-accreditation-0','resolved':False}] if n==0 else []})
            record(building+'-vendor-contact-'+str(n),'contact',org,[building],
                {'email':'vendor'+str(n)+'@homework.invalid','vendor_id':building+'-vendor-'+str(n),'contact_type':'vendor','purposes':['certificate_renewal','inspection_booking']})
        items=[]
        for kind,(count,fields) in TYPES.items():
            for slot in range(count):
                identifier=f'{building}-hoi-{kind}-{slot}'
                values={field:f'Test {kind} {slot} {field}' for field in fields}
                values.update({k:1 for k in fields if k in {'floor_count','floor_area','quantity','consumption','total'}})
                lines=[{'fields':{k:1 if k!='description' else 'Fictional invoice line' for k in LINE_FIELDS},
                        'source_supported':LINE_FIELDS} for _ in range(2)] if kind=='invoice' else []
                source_id=identifier+'-source'
                source={'id':source_id,'kind':'fixture','org':org,'buildings':[building],
                        'data':{'text':json.dumps({'fields':values,**({'lines':lines} if lines else {})}),'fields':values,'version':1,
                                'information_type':'fixture','scope_verified':True,'lines':lines}}
                sources.append(source)
                item=record(identifier,'hoi_item',org,[building],{'item_type':kind,'slot':slot,'fields':values,
                     'source_supported':fields,'accepted':True,'source_id':source_id,'lines':lines})
                items.append(item)
        record(building+'-hoi-manifest','hoi_manifest',org,[building],
            {'formula':'hoi-fixed-coverage-v1','types':list(TYPES),'checklist_version':'hw1-fixed-fields-v1'})
        def certificate(identifier,subject,holder,days,slot=None,assessment='pass',review=False):
            values={'reference':identifier,'certificate_type':'VENDOR_FIRE' if subject=='Vendor' else 'FIRE',
                    'subject':subject,'holder_id':holder,'issuer':'Fictional issuer','issue_date':str(CLOCK-timedelta(days=365)),
                    'expiry_date':str(CLOCK+timedelta(days=days)) if days is not None else None,
                    'review_date':str(CLOCK+timedelta(days=7)) if review else None,
                    'review_rule':'fictional documented review anchor' if review else None,
                    'country':'TEST','trade':'Fire'}
            source_id=identifier+'-source'
            sources.append({'id':source_id,'kind':'certificate','org':org,'buildings':[building],
                'data':{'text':json.dumps({'fields':values}),'fields':values,'version':1,
                        'information_type':'certificate','scope_verified':True}})
            data={**values,'holder_name':holder,'source_id':source_id,'assessment':assessment,
                  'assessment_reason':'recorded simulated fixture check; no external register attestation',
                  'version':1,'draft':False,'accepted':True}
            if slot is not None:
                item_kind='vendor_accreditation' if subject=='Vendor' else 'building_certificate'
                data['hoi_item_id']=f'{building}-hoi-{item_kind}-{slot}'
                item=next(i for i in items if i['id']==data['hoi_item_id'])
                f={k:values[k] for k in TYPES[item_kind][1]}
                item['data'].update(fields=f,source_supported=[k for k,v in f.items() if v is not None],source_id=source_id)
            return record(identifier,'certificate',org,[building],data)
        for n,days in enumerate([-1,0,30]):
            certificate(building+'-certificate-'+str(n),'Building',building,days,n)
        for n,days in enumerate([-1,180]):
            certificate(building+'-accreditation-'+str(n),'Vendor',building+'-vendor-'+str(n),days,n)
        for days in [31,30,8,7,1,0,-1]:
            certificate(building+'-boundary-'+str(days),'Building',building,days,assessment='review' if days==8 else 'pass')
        certificate(building+'-unknown','Building',building,None,assessment='unassessed')
        certificate(building+'-review-anchor','Building',building,None,review=True)
        certificate(building+'-failed-assessment','Building',building,100,assessment='fail')
        histories.append({'id':building+'-hoi-initial','kind':'hoi','org':org,'buildings':[building],
                          'data':{'score':calculate(items)['score'],'formula_version':'hoi-fixed-coverage-v1','event_id':'fixture-initial',
                                  'time':'2026-10-25T00:00:00+00:00','actor':'fixture setup'}})
    # Same-org shared vendor is eligible from either linked building, without disclosing other building records.
    record('hw1-shared-vendor','vendor','hw1-org-a',['hw1-building-a1','hw1-building-a2'],
        {'name':'Shared test vendor','version':1,'blocked':False,'blocking_reasons':[]})
    for identifier,buildings,flags in [('mixed-evidence',['hw1-building-a1','hw1-building-a2'],{}),
                                     ('unknown-evidence',['hw1-building-a1'],{'scope_verified':False}),
                                     ('sensitive-evidence',['hw1-building-a1'],{'sensitive':True})]:
        sources.append({'id':identifier,'kind':'certificate','org':'hw1-org-a','buildings':buildings,
            'data':{'text':'Restricted fixture evidence','version':1,'information_type':'certificate','scope_verified':True,**flags}})
    return {'records':records,'sources':sources,'history':histories}
