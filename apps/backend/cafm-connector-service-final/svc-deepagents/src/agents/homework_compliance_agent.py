"""Bounded planning with scoped tools and deterministic evidence-backed rendering."""
import json
import asyncio
from datetime import datetime,timezone
from uuid import uuid4
from fastapi import HTTPException
from langchain_core.messages import SystemMessage,HumanMessage,ToolMessage
from ..services.compliance_hw1.contracts import Query,ProposalInput,QUERY_KEYS
from ..services.compliance_hw1.authorization import allowed_tools
from ..services.compliance_hw1.sessions import conversation,append
from ..observability.homework import (
    invoke_traced_model,
    rendered_prompt_sha256,
    traced_tool_call,
)
from .compliance_prompt import assemble

DESCRIPTIONS={
 'compliance_resolve_entity':'Resolve a named building/vendor/certificate using only authorized candidates; clarify multiple matches.',
 'compliance_list_certificates':'Read certificate status with exact supported filters; no writes.',
 'compliance_count_certificates':'Exact count of the same authorized filtered certificate population.',
 'compliance_group_vendors':'Group held accreditations per vendor using comparison/threshold.',
 'compliance_get_taxonomy':'Read fictional versioned test-pack types; not fresh legal verification.',
 'compliance_get_coverage':'Pack types on file and missing; not compliance/HOI score.',
 'compliance_get_evidence':'Read authorized original certificate source JSON and version, not general reports.',
 'compliance_get_vendor_eligibility':'Read permitted accreditation and approved vendor-state facts.',
 'compliance_get_findings':'Read recorded assessment results; never rerun forensics automatically.',
 'compliance_prepare_proposal':'Persist an exact allowed action preview only. Never approve/commit or send.',
 'compliance_get_proposal':'Read an eligible action preview/status via entity_id=proposal_id.',
 'homework_get_hoi':'Managers only: canonical fixed ten-group completeness and history for entity_id=building ID.',
 'homework_get_outbox':'Read permitted simulated outbox statuses; never real delivery.'}


def tools(principal):
    result=[]
    for name in allowed_tools(principal):
        schema=(ProposalInput if name=='compliance_prepare_proposal' else Query).model_json_schema()
        if name!='compliance_prepare_proposal':
            schema['properties']={k:v for k,v in schema['properties'].items() if k in QUERY_KEYS[name]}
        result.append({'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'parameters':schema}})
    return result


def no_tool(message):
    try:
        text=message.strip()
        if text.startswith('```json') and text.endswith('```'):
            text=text[7:-3].strip()
        elif text.startswith('```') and text.endswith('```'):
            text=text[3:-3].strip()
        value=json.loads(text)
        if not isinstance(value,dict):
            value={}
    except (ValueError,TypeError):
        value={}
    reason=value.get('reason_code')
    if reason=='role_boundary' or value.get('outcome')=='forbidden':
        return 'forbidden','That information or action is outside your role or assigned scope.'
    if reason=='missing_capability' or value.get('outcome')=='not_enabled':
        return 'not_enabled','That operation is not enabled in this Compliance profile. No action was performed.'
    if reason=='workflow_explanation':
        return 'answered','An eligible person reviews the exact proposal and confirms it through the authenticated confirmation endpoint. Renewal alone never unblocks a vendor; source evidence is preserved.'
    if reason=='capability_help':
        return 'answered','You can ask for permitted certificate status, exact filtered counts, evidence and eligible action previews. Actual capabilities depend on your authenticated role.'
    question=value.get('question','')
    # Clarifications cannot smuggle unverified completion claims into a no-tool answer.
    if not isinstance(question,str) or not question.endswith('?') or len(question)>400 or any(w in question.lower() for w in ['completed','sent','unblocked','approved','created']):
        question='Which building, vendor or certificate and reporting condition do you mean?'
    return 'needs_clarification',question


def render(calls):
    lines=[];proposal_ids=[]
    for call in calls:
        result=call.get('output',{}).get('result',{})
        if call.get('error'):
            lines.append('The requested operation could not be completed: '+call['error'])
            continue
        if 'proposal_id' in result:
            proposal_ids.append(result['proposal_id'])
            lines.append('Proposal '+result['proposal_id']+' is '+result.get('status',result.get('outcome','available'))+'. No approval or business change is implied.')
        elif 'score' in result:
            lines.append(f"HOI Score: {result['score']}% record completeness ({result['formula_version']}).")
        elif 'count' in result:
            lines.append(f"{result['count']} matching {result.get('unit','records')} in the authorized query.")
        elif 'records' in result:
            lines.append(f"Showing {result['returned']} of {result['total_matching']} matching records. "+('Complete result.' if result.get('complete') else 'Partial result.'))
            for record in result['records']:
                if 'expiry_label' in record:
                    lines.append(f"{record['reference']}: {record['expiry_label']} (expiry {record.get('expiry_date') or 'not recorded'}).")
        elif 'types_total' in result:
            lines.append(f"{result['types_on_file']} of {result['types_total']} pack types on file; pack completeness, not compliance or HOI.")
        elif 'blocked' in result:
            lines.append('Recorded vendor block state: '+('blocked' if result['blocked'] else 'not blocked')+'. This is separate from certificate dates.')
        elif 'content' in result:
            lines.append('Authorized certificate evidence '+result['source_id']+', version '+str(result['version'])+'.')
    return '\n'.join(lines) or 'No verified result was returned.',proposal_ids


async def ask(principal,body,service,model,request_id=None):
    request_id=request_id or str(uuid4())
    prompt,metadata=assemble(principal)
    async with service.store.transaction(principal) as repo:
        session=await conversation(repo,principal,body.conversation_id)
        session_id=session['id'];context=session['data']
    if body.message.strip().lower() in {'yes','approve','confirm','go ahead'} and context['pending_proposal_ids']:
        identifier=context['pending_proposal_ids'][-1]
        arguments={'entity_id':identifier}
        preview=await traced_tool_call(
            principal,
            'compliance_get_proposal',
            arguments,
            lambda: service.execute(
                principal,'compliance_get_proposal',arguments,request_id
            ),
            allowed_tools=metadata['bound_tools'],
        )
        return {'outcome':'awaiting_confirmation','answer':'Confirm the displayed version/hash through the authenticated confirmation endpoint; chat text alone has not applied it.',
                'conversation_id':session_id,'preview':preview,'tool_calls':[{'tool':'compliance_get_proposal','input':{'entity_id':identifier},'output':preview}],
                'prompt_metadata':metadata}
    messages=[SystemMessage(content=prompt),HumanMessage(content=json.dumps({'prior_turns':context['messages'],'request':body.message}))]
    calls=[];control=None;usage_events=[];provider_tools=[];provider_failed=False;bound=model.bind_tools(tools(principal))
    for turn in range(5):
        try:
            response=await asyncio.wait_for(
                invoke_traced_model(
                    bound,
                    messages,
                    rendered_prompt_sha256(prompt, metadata),
                ),
                timeout=60,
            )
        except Exception:
            provider_failed=True
            control=('partial' if calls else 'failed','The provider was unavailable; no additional action was performed.')
            break
        messages.append(response)
        provider_tools.extend(c['name'] for c in response.tool_calls)
        usage_events.append(getattr(response,'usage_metadata',None))
        if not response.tool_calls:
            # Tool results supply facts; free model prose cannot replace or contradict them.
            control=None if calls else no_tool(response.content)
            break
        if len(response.tool_calls)+len(calls)>4:
            control=('partial','The four-call limit was reached. Please narrow or continue the request; no extra operation was executed.')
            break
        for call in response.tool_calls:
            try:
                # A model may quote supplied evidence, but cannot invent a new source document.
                supplied=[body.message]+[turn.get('user','') for turn in context['messages']]
                source_text=call['args'].get('source_text')
                if source_text and not any(source_text in message for message in supplied):
                    raise HTTPException(422,'Supply the original structured evidence through the authenticated intake; model-generated source text is not accepted')
                output=await traced_tool_call(
                    principal,
                    call['name'],
                    call['args'],
                    lambda call=call: service.execute(
                        principal,call['name'],call['args'],request_id
                    ),
                    allowed_tools=metadata['bound_tools'],
                )
                item={'tool':call['name'],'input':call['args'],'output':output}
                payload=output
            except HTTPException as exc:
                item={'tool':call['name'],'input':{},'error':str(exc.detail),'status_code':exc.status_code}
                payload={'error':str(exc.detail),'status_code':exc.status_code,'operation_succeeded':False}
            calls.append(item)
            messages.append(ToolMessage(content=json.dumps(payload,default=str),tool_call_id=call['id']))
    if not calls:
        outcome,answer=control or ('needs_clarification','Which authorized building or certificate do you mean?')
        proposal_ids=[]
    else:
        answer,proposal_ids=render(calls)
        if proposal_ids:
            outcome='awaiting_confirmation'
        elif all('error' in c for c in calls):
            outcome='forbidden' if all(c.get('status_code')==403 for c in calls) else 'failed'
        else:
            outcome='partial' if any('error' in c for c in calls) or control and control[0]=='partial' else 'answered'
        if control and control[0] in {'needs_clarification','partial','not_enabled','forbidden'}:
            answer+='\n'+control[1]
    async with service.store.transaction(principal) as repo:
        current=await conversation(repo,principal,session_id)
        await append(repo,current,body.message,answer,proposal_ids)
        # Trace summaries omit message/evidence bodies and secret-bearing raw arguments.
        await repo.insert('history','trace',list(principal.building_ids),
            {'actor':principal.user_id,'role':principal.role,'category':'compliance','conversation_id':session_id,'request_id':request_id,
             'intent':'user-request recorded in private owned conversation','selected_route':'compliance',
             'actual_tools':[{'tool':c['tool'],'succeeded':'error' not in c,
                 'arguments':{k:v for k,v in c.get('input',{}).items() if k in {'entity_id','building_ids','subject','certificate_type','days_min','days_max','expiry_label','limit','offset','action'}},
                 'result_summary':{k:v for k,v in c.get('output',{}).get('result',{}).items() if k in {'count','total_matching','returned','complete','proposal_id','status','source_id','version','assessment_date','fixture_version'}}} for c in calls],
             'outcome':outcome,'prompt_metadata':metadata,'fixture_version':'compliance-fixtures-v1',
             'assessment_clock':'2026-10-25','model':str(getattr(model,'model_name',getattr(model,'model','configured provider'))),
             'generated_at':datetime.now(timezone.utc).isoformat()})
    for index,usage in enumerate(usage_events):
        from ..services.usage_hw1.service import record_measurement
        await record_measurement(principal,request_id,'compliance',session_id,usage,index)
    return {'outcome':outcome,'answer':answer,'conversation_id':session_id,'tool_calls':calls,
            'prompt_metadata':metadata,'provider_execution':{'successful_responses':len(usage_events),'selected_tools':provider_tools,'provider_failed':provider_failed},
            'data_source':'fictional isolated Homework fixtures'}
