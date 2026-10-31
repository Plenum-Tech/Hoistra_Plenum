"""Compare only explicit source identity/period/unit metadata; missing data never joins."""
import hashlib,json
from pydantic import BaseModel,ConfigDict,Field
from fastapi import HTTPException
from ..compliance_hw1.store import runtime_store
from ..compliance_hw1.authorization import buildings
KINDS={'unified_fixture','energy_source','contract','certificate','asset_summary','inspection_report','work_order_report','meter_reading'}
class Compatibility(BaseModel):
    model_config=ConfigDict(extra='forbid')
    building_id:str=Field(min_length=1,max_length=160)
    source_ids:list[str]=Field(min_length=2,max_length=10)

async def compare(p,q,store=None):
    from ...api.homework_identity import TestPrincipal
    if not isinstance(p,TestPrincipal):raise HTTPException(401,'Trusted principal required')
    store=store or runtime_store()
    if p.role not in {'property_manager','portfolio_manager'}:
        await store.audit(p,'unified_source_compatibility','capability_denied')
        raise HTTPException(403,'Cross-domain source compatibility is manager-scoped')
    try:buildings(p,[q.building_id])
    except HTTPException:
        await store.audit(p,'unified_source_compatibility','scope_denied')
        raise
    await store.audit(p,'unified_source_compatibility','capability_authorized')
    async with store.transaction(p,repeatable=True) as repo:
        projections=[];reasons=[]
        if len(set(q.source_ids))!=len(q.source_ids):reasons.append('duplicate source IDs cannot prove independent evidence')
        for identifier in q.source_ids:
            row=(await repo.scoped('sources',[q.building_id],KINDS,identifier))[0]
            if row['org']!=p.organization_id or row['buildings']!=[q.building_id]:raise HTTPException(404,'Source unavailable')
            d=row['data'];f=d.get('fields',{})
            if not isinstance(f,dict):raise HTTPException(409,'Source fields unavailable')
            compact=json.dumps(f,sort_keys=True,separators=(',',':'),default=str).encode()
            spaced=json.dumps(f,sort_keys=True,default=str).encode()
            expected=d.get('content_sha256')
            if expected and expected not in {hashlib.sha256(compact).hexdigest(),hashlib.sha256(spaced).hexdigest()}:
                raise HTTPException(409,'Source digest does not match retained fields')
            projection={'source_id':identifier,'source_version':d.get('version'),
                'entity_id':f.get('meter_id',f.get('entity_id')),'period_start':f.get('period_start'),
                'period_end':f.get('period_end'),'unit':f.get('unit')}
            from datetime import date
            try:
                start=date.fromisoformat(projection['period_start']);end=date.fromisoformat(projection['period_end'])
                if start>end:raise ValueError
            except (TypeError,ValueError):reasons.append('invalid or unavailable source period')
            projections.append(projection)
        for field in ['entity_id','period_start','period_end','unit']:
            values=[x[field] for x in projections]
            if any(not isinstance(x,str) or not x.strip() for x in values):reasons.append('missing explicit '+field)
            elif len(set(values))!=1:reasons.append('incompatible '+field)
        await repo.audit('unified_source_compatibility','compatible' if not reasons else 'unavailable')
        return {'compatible':not reasons,'reasons':reasons,'building_id':q.building_id,
            'sources':projections,'source_versions':{x['source_id']:x['source_version'] for x in projections},
            'effect':'diagnostic only; no calculation, inferred join, source rewrite or business approval'}
