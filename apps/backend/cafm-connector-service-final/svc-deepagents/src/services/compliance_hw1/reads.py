"""Scoped Compliance reads and exact same-population filters/aggregations."""
from collections import Counter
from fastapi import HTTPException
from .authorization import authorize_row, buildings
from .dates import classify
from .contracts import MANAGERS
from .hoi import building_score


def visible(principal,row,selected=None,full=False):
    try:
        authorize_row(principal,row,full=full,selected=selected)
        return True
    except HTTPException:
        return False


async def clock(repo):
    row=await repo.get('records','clock-'+repo.principal.organization_id)
    from datetime import date
    return date.fromisoformat(row['data']['date'])


async def certificates(repo,principal,query):
    selected=buildings(principal,query.building_ids)
    if principal.role=='general_user' and any(v is not None for v in [query.assessment,query.blocked,query.draft]):
        raise HTTPException(403,'Detailed assessment/state filters not granted')
    today=await clock(repo)
    vendors={r['id']:r for r in await repo.list('records','vendor')}
    rows=[]
    for record in await repo.list('records','certificate'):
        if not visible(principal,record,selected):
            continue
        data=record['data']; state=classify(data,today)
        if query.entity_id and query.entity_id not in {record['id'],data['holder_id']}:
            continue
        if query.identifier and query.identifier.casefold() not in (data['reference']+' '+data.get('holder_name','')).casefold():
            continue
        pairs=[(query.subject,'subject'),(query.certificate_type,'certificate_type'),(query.country,'country'),(query.trade,'trade')]
        if any(v is not None and str(data.get(k,'')).casefold()!=v.casefold() for v,k in pairs):
            continue
        if query.expiry_label and state['expiry_label']!=query.expiry_label:
            continue
        days=state['days_to_expiry']
        if query.days_min is not None and (days is None or days<query.days_min):
            continue
        if query.days_max is not None and (days is None or days>query.days_max):
            continue
        if query.assessment and data.get('assessment','unassessed')!=query.assessment:
            continue
        if query.draft is not None and bool(data.get('draft'))!=query.draft:
            continue
        vendor=vendors.get(data['holder_id']) if data['subject']=='Vendor' else None
        blocked=vendor['data']['blocked'] if vendor else None
        if query.blocked is not None and blocked is not query.blocked:
            continue
        result={k:data.get(k) for k in ['reference','certificate_type','subject','holder_id','holder_name','expiry_date','review_date']}
        result.update(id=record['id'],**state,source_id=data['source_id'])
        if principal.role!='general_user':
            result.update({k:data.get(k) for k in ['issuer','issue_date','country','trade','assessment','assessment_reason','draft','version']})
            result['approved_vendor_block_state']=blocked
        rows.append(result)
    return rows


def page(rows,query):
    shown=rows[query.offset:query.offset+query.limit]
    return {'records':shown,'total_matching':len(rows),'returned':len(shown),'offset':query.offset,
            'next_offset':query.offset+query.limit if query.offset+query.limit<len(rows) else None,
            'complete':query.offset==0 and len(shown)==len(rows)}


async def read(repo,principal,tool,query):
    selected=buildings(principal,query.building_ids)
    base={'scope':selected,'filters':query.model_dump(exclude_none=True),
          'data_source':'fictional Homework fixtures','assessment_date':str(await clock(repo)),
          'fixture_version':'compliance-fixtures-v1'}
    if tool in {'compliance_list_certificates','compliance_count_certificates','compliance_group_vendors','compliance_get_findings'}:
        rows=await certificates(repo,principal,query)
        if tool=='compliance_count_certificates':
            return {**base,'count':len(rows),'unit':'certificate records','complete':True}
        if tool=='compliance_group_vendors':
            counts=Counter(r['holder_id'] for r in rows if r['subject']=='Vendor')
            compare={'gt':lambda n:n>query.threshold,'gte':lambda n:n>=query.threshold,'eq':lambda n:n==query.threshold}[query.comparison]
            rows=[{'vendor_id':k,'held_certificate_count':v} for k,v in sorted(counts.items()) if compare(v)]
        return {**base,**page(rows,query)}
    if tool=='compliance_resolve_entity':
        if not query.identifier:
            raise HTTPException(422,'identifier required')
        candidates=[]
        for r in await repo.list('records'):
            if r['kind'] not in {'building','vendor','certificate'} or not visible(principal,r,selected):
                continue
            name=r['data'].get('name',r['data'].get('reference',''))
            if query.identifier.casefold() in (r['id']+' '+name).casefold():
                candidates.append({'id':r['id'],'kind':r['kind'],'name':name})
        return {**base,**page(candidates,query),'resolution':'unique' if len(candidates)==1 else 'clarify' if candidates else 'no authorized match'}
    if tool=='compliance_get_taxonomy':
        packs=[r['data'] for r in await repo.list('records','pack')]
        types=[t for p in packs for t in p['types'] if (not query.subject or t['subject']==query.subject)
               and (not query.country or p['country']==query.country)
               and (not query.trade or t['trade']==query.trade)
               and (not query.certificate_type or t['code']==query.certificate_type)]
        return {**base,'pack_version':'fictional-pack-v1','authority':'fictional application test pack, not legal advice/verification',
                'types':types,'count':len(types),'unit':'pack types','complete':True}
    if tool=='compliance_get_coverage':
        taxonomy=await read(repo,principal,'compliance_get_taxonomy',query)
        rows=await certificates(repo,principal,query)
        expected={(t['subject'],t['code']) for t in taxonomy['types']}
        held={(r['subject'],r['certificate_type']) for r in rows}&expected
        return {**base,'pack_version':taxonomy['pack_version'],'denominator_basis':'country pack types; not all necessarily applicable',
                'types_total':len(expected),'types_on_file':len(held),'missing_types':sorted(expected-held),
                'meaning':'pack completeness, not compliance, safety or HOI','complete':True}
    if tool=='compliance_get_evidence':
        if not query.entity_id:
            raise HTTPException(422,'entity_id required')
        source=await repo.get('sources',query.entity_id)
        authorize_row(principal,source,full=True,selected=selected)
        if source['data'].get('information_type')!='certificate':
            raise HTTPException(404,'Record unavailable')
        if principal.role=='compliance_officer':
            import json
            from .contracts import CertificateValues
            try:
                raw=json.loads(source['data']['text'])
                fields=CertificateValues.model_validate(raw['fields']).model_dump(mode='json')
                if set(raw)!={'fields'} or fields!=source['data'].get('fields'):raise ValueError
            except (ValueError,KeyError,TypeError):
                raise HTTPException(404,'Separable certificate evidence unavailable') from None
        return {**base,'source_id':source['id'],'version':source['data']['version'],
                'content':source['data']['text'],'page_reference':'page 1','original_preserved':True}
    if tool=='compliance_get_vendor_eligibility':
        vendor=await repo.get('records',query.entity_id)
        if vendor['kind']!='vendor':
            raise HTTPException(404,'Record unavailable')
        authorize_row(principal,vendor,selected=selected)
        rows=await certificates(repo,principal,query)
        result={**base,'vendor_id':vendor['id'],'certificate_status':rows}
        if principal.role!='general_user':
            result.update(blocked=vendor['data']['blocked'],blocking_reasons=[{'id':r['id'],'category':r['category'],'resolved':r['resolved']}
                          for r in vendor['data']['blocking_reasons']],
                          effect='organization-wide new-request eligibility; existing external WOs unchanged')
        return result
    if tool=='homework_get_hoi':
        if not query.entity_id:
            raise HTTPException(422,'Building entity_id required')
        buildings(principal,[query.entity_id])
        score=await building_score(repo,query.entity_id)
        history=[r['data'] for r in await repo.list('history','hoi') if r['buildings']==[query.entity_id]]
        return {**base,**score,'history':history}
    if tool=='homework_get_outbox':
        if hasattr(repo,'outbox_page'):
            rows,total,issues=await repo.outbox_page(selected,query.limit,query.offset,query.after_id,query.include_issues)
            more=(len(rows)>query.limit if query.after_id is not None else query.offset+len(rows)<total)
            shown=[{'id':r['id'],**r['data']} for r in rows[:query.limit]]
            return {**base,'records':shown,'total_matching':total,'returned':len(shown),'offset':query.offset,
                'next_offset':query.offset+query.limit if query.after_id is None and more else None,
                'next_cursor':shown[-1]['id'] if shown and more else None,
                'total_matching_state':'counted on first/offset page' if total is not None else 'not recounted on cursor continuation',
                'complete':not more and query.after_id is None and query.offset==0,'notification_issues':issues}
        rows=[]
        for r in await repo.list('outbox'):
            if not visible(principal,r,selected,full=True):
                continue
            d=r['data']
            if principal.role=='general_user' and d['actor']!=principal.user_id:
                continue
            if principal.role=='compliance_officer':
                message=d.get('message',{})
                if d['category']!='compliance' or message.get('purpose')=='team_notification' or (message.get('action')=='email' and message.get('purpose') not in {'certificate_renewal','inspection_booking'}):
                    continue
            rows.append({'id':r['id'],**d})
        issues=[]
        if principal.role!='general_user':
            for issue in await repo.list('history','notification_issue'):
                if set(issue['buildings'])<=set(selected) and set(issue['buildings']) & set(selected):
                    linked=next((r for r in rows if r['id']==issue['data'].get('outbox_id')),None)
                    if linked or principal.role in MANAGERS:
                        issues.append(issue['data'])
        return {**base,**page(rows,query),'notification_issues':issues}
    raise HTTPException(403,'Operation not permitted')
