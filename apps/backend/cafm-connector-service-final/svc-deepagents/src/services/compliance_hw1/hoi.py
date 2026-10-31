"""Canonical fixed-target scorer: completeness, never compliance or safety."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
import hashlib,json
from fastapi import HTTPException

FORMULA = 'hoi-fixed-coverage-v1'
# Quantities and fields are fixed before questions run, not supplied by the model.
TYPES = {
 'profile': (1, ['name','country','city','building_type','floor_count','floor_area','area_unit']),
 'location': (2, ['name','type','parent']),
 'asset': (5, ['reference','name','category','location','operational_status']),
 'wo': (3, ['reference','asset','description','raised_date','status']),
 'ppm': (2, ['reference','asset','task','frequency','next_due_date']),
 'inspection': (2, ['reference','type','inspection_date','inspected_entity','result']),
 'building_certificate': (3, ['reference','certificate_type','holder_id','issuer','issue_date','expiry_date']),
 'vendor_accreditation': (2, ['reference','certificate_type','holder_id','issuer','issue_date','expiry_date']),
 'vendor': (2, ['reference','name','trade','contact']),
 'contract': (2, ['reference','vendor','effective_from','effective_to','service_scope','sla_targets','rates']),
 'invoice': (2, ['reference','vendor','date','total','currency']),
 'po': (1, ['reference','supplier','date','status','total','currency']),
 'meter': (2, ['reference','type','building']),
 'reading': (8, ['meter','timestamp','consumption','unit','interval']),
 'energy_profile': (1, ['building_type','floor_area','area_unit']),
 'part': (3, ['reference','description','stock_location','quantity','unit']),
 'resource': (2, ['reference','trade','assigned_building']),
 'manual': (1, ['reference','type','building','version','content']),
 'drawing': (1, ['reference','type','building','version','content']),
 'warranty': (1, ['reference','type','building','version','content']),
}
GROUPS = [('profile','location'),('asset',),('wo','ppm'),('inspection',),
          ('building_certificate','vendor_accreditation'),('vendor','contract'),
          ('invoice','po'),('meter','reading','energy_profile'),('part','resource'),
          ('manual','drawing','warranty')]
LINE_FIELDS = ['description','quantity','unit_price','line_amount']


def meaningful(value):
    # Known zero and false are evidence; empty/default/invented values are not.
    return value is not None and value != '' and value != [] and value != {} and not (
        isinstance(value,str) and value.strip().lower() in {'','unknown','not recorded','n/a','default'})


def fraction(values, required, supported):
    return sum(Decimal(1) for f in required if f in supported and meaningful(values.get(f))) / Decimal(len(required))


def calculate(items):
    indexed={};seen_sources=set();duplicates=[]
    for record in sorted(items,key=lambda row:row.get('id','')):
        item=record['data']
        if record['kind']!='hoi_item' or not item.get('accepted') or item.get('superseded'):
            continue
        kind=item.get('item_type');slot=item.get('slot')
        if kind not in TYPES or type(slot) is not int or not 0<=slot<TYPES[kind][0]:
            continue  # Unexpected uploads never expand fixed targets or earn credit.
        key=(kind,slot)
        source_id=item.get('_evidence_identity',item.get('source_id'))
        if source_id and source_id in seen_sources:
            duplicates.append({'source_id':source_id,'item_type':item['item_type'],'slot':item['slot'],
                               'reason':'duplicate source cannot earn a second HOI contribution'})
            continue
        if key in indexed:
            raise ValueError('Conflicting accepted HOI slot identities')
        indexed[key]=item
        if source_id: seen_sources.add(source_id)
    types, missing = {}, []
    for kind,(count,fields) in TYPES.items():
        scores=[]
        for slot in range(count):
            item=indexed.get((kind,slot),{})
            fields_data=item.get('fields',{})
            supported=item.get('source_supported',[])
            score=fraction(fields_data,fields,supported) if item else Decimal(0)
            if kind=='invoice':
                lines=item.get('lines',[])
                line_scores=[fraction(lines[n].get('fields',{}),LINE_FIELDS,lines[n].get('source_supported',[]))
                             if n<len(lines) else Decimal(0) for n in range(2)]
                score=(score+sum(line_scores)/2)/2
            scores.append(score)
            absent=[f for f in fields if f not in supported or not meaningful(fields_data.get(f))]
            if absent or score<1:
                missing.append({'item_type':kind,'slot':slot,'missing_fields':absent,'fraction':str(score)})
        types[kind]=sum(scores)/Decimal(count)
    groups=[sum(types[t] for t in group)/Decimal(len(group)) for group in GROUPS]
    raw=sum(groups)/Decimal(10)*100
    return {'formula_version':FORMULA,'checklist_version':'hw1-fixed-fields-v1',
            'score':int(raw.quantize(Decimal('1'),rounding=ROUND_HALF_UP)),
            'group_percentages':[str(x*100) for x in groups],
            'type_percentages':{k:str(v*100) for k,v in types.items()},
            'missing':missing,'duplicate_source_references':duplicates,
            'measurement':'record completeness; not safety/compliance/performance'}


async def building_score(repo, building):
    rows=[r for r in await repo.list('records','hoi_item') if r['buildings']==[building]]
    manifest=await repo.get('records',building+'-hoi-manifest')
    if manifest['data'].get('formula')!=FORMULA or manifest['data'].get('types')!=list(TYPES):
        raise ValueError('HOI fixture targets unavailable')
    versions={}
    for row in rows:
        item=row['data']
        if row['kind']!='hoi_item' or not item.get('accepted') or item.get('superseded'):
            continue
        source_id=item.get('source_id')
        if not source_id:
            raise HTTPException(409,'Accepted HOI item has no evidence source')
        source=await repo.get('sources',source_id)
        evidence=source['data'].get('fields')
        if source['org']!=repo.principal.organization_id or source['buildings']!=[building] or not isinstance(evidence,dict):
            raise HTTPException(409,'Accepted HOI evidence scope or fields are unavailable')
        raw=source['data'].get('text')
        try: raw_fields=json.loads(raw).get('fields') if isinstance(raw,str) else None
        except (ValueError,TypeError): raw_fields=None
        if raw_fields is not None and raw_fields!=evidence:
            raise HTTPException(409,'HOI evidence projection differs from retained original')
        content_hash=source['data'].get('content_sha256')
        if content_hash:
            compact=json.dumps(evidence,sort_keys=True,separators=(',',':'),default=str).encode()
            spaced=json.dumps(evidence,sort_keys=True,default=str).encode()
            if content_hash not in {hashlib.sha256(compact).hexdigest(),hashlib.sha256(spaced).hexdigest()}:
                raise HTTPException(409,'HOI source digest verification failed')
        for field in item.get('source_supported',[]):
            if field not in evidence or item.get('fields',{}).get(field)!=evidence[field]:
                raise HTTPException(409,'HOI accepted fields do not match source evidence')
        if item['item_type']=='invoice':
            source_lines=source['data'].get('lines',[])
            try:original_lines=json.loads(raw).get('lines',[]) if isinstance(raw,str) else []
            except (ValueError,TypeError):original_lines=[]
            if source_lines!=original_lines:
                raise HTTPException(409,'Invoice line projection differs from retained original')
            for index,line in enumerate(item.get('lines',[])[:2]):
                fields=source_lines[index].get('fields',{}) if index<len(source_lines) else {}
                for field in line.get('source_supported',[]):
                    if field not in fields or fields[field]!=line.get('fields',{}).get(field):
                        # A missing source line earns no credit; contradictory projection is an error.
                        if field in fields:raise HTTPException(409,'Invoice line differs from retained source')
                line['source_supported']=[f for f in line.get('source_supported',[]) if f in fields]
        if item['item_type'] in {'manual','drawing','warranty'} and source['data'].get('information_type') in {'certificate','inspection_report'}:
            raise HTTPException(409,'Certificate/inspection evidence cannot fill supporting-document slots')
        # Distinct database IDs for an identical original cannot earn extra credit.
        original=source['data'].get('original',source['data'].get('text'))
        if isinstance(original,str):item['_evidence_identity']=hashlib.sha256(original.encode()).hexdigest()
        versions[source_id]=source['data'].get('version')
    result=calculate(rows)
    result.update(building_id=building,source_record_ids=[r['id'] for r in rows if r['data'].get('accepted') and not r['data'].get('superseded')],
                  source_ids=sorted({r['data']['source_id'] for r in rows if r['data'].get('accepted') and not r['data'].get('superseded')}),
                  source_versions=versions,
                  calculated_at=datetime.now(timezone.utc).isoformat())
    return result
