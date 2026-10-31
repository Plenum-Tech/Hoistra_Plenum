"""Narrow intent router for the isolated Homework agent profiles."""
import re
from types import SimpleNamespace

from fastapi import HTTPException


_DOMAIN_PATTERNS = {
    "compliance": re.compile(
        r"\b(compliance|certificates?|accreditations?|renewals?|expiry|expir(?:e|ed|ing)|"
        r"coverage gaps?|country pack|vendor block(?:ed|ing)?|unblock(?:ed|ing)?)\b", re.I),
    "contract_performance": re.compile(
        r"\b(contract(?:s|ual)?|invoice(?:s)?|sla|scorecards?|vendor scoring|"
        r"vendor performance|vendor score|payment diagnostic)\b", re.I),
    "energy": re.compile(
        r"\b(energy|eui|kwh|meter(?:s|ing)?|consumption|anomal(?:y|ies)|baseline drift|"
        r"occupancy|energy report|tariff)\b", re.I),
    "ingestion": re.compile(
        r"\b(ingest(?:ion)?|upload|import|correct(?:ion)?|add (?:a )?building|"
        r"onboard(?:ing)?)\b", re.I),
}
_BROAD_REQUEST = re.compile(
    r"\b(help me with (?:my )?(?:data|records|building)|what can you do|"
    r"show me everything|all my data)\b", re.I)


def classify(message: str) -> tuple[str | None, list[str]]:
    """Return one explicit domain or request clarification if domains are mixed."""
    if re.search(r'\bsource compatibility\b',message,re.I):return 'compatibility',['compatibility']
    if re.search(r'\b(?:usage|adoption|weekly digest|processing backlog|monthly usage)\b',message,re.I) and not re.search(r'\bemail\b',message,re.I):
        return 'usage',['usage']
    # Communications is a shared workflow: a domain word in an email request is
    # context, not a request to score or mutate that domain.
    if re.search(r'\b(?:email|outbox|send (?:a |an |the )?message|hoi|hoist score)\b',message,re.I):
        return "communications", ["communications"]
    selected = [name for name, pattern in _DOMAIN_PATTERNS.items() if pattern.search(message)]
    if not selected and _BROAD_REQUEST.search(message):
        return None, []
    if len(selected) == 1:
        return selected[0], selected
    if len(selected) > 1:
        return None, selected
    return "reads", []


async def _dispatch(request, body):
    """Delegate through existing bounded agents; never broaden their tools or scope."""
    from ..llm_factory import create_chat_model

    principal = request.state.test_principal
    request_id = request.state.request_id
    domain, detected = classify(body.message)
    if re.search(r'\b(?:change|update|grant|delete|set)\b.{0,45}\b(?:account email|password|roles?|assignments?|subscription|billing|quota|hoi score|hoi formula)\b',body.message,re.I):
        return {'outcome':'forbidden','reason_code':'outside_platform','selected_route':None,'actual_tools':[],
            'tool_calls':[],'answer':'Account, role, assignment, billing and score-formula administration are not granted by these application roles.'}
    if not permitted(principal,domain,body.message) or any(not permitted(principal,d,body.message) for d in detected):
        return {'outcome':'forbidden','reason_code':'role_boundary','selected_route':domain,
            'actual_tools':[],'tool_calls':[],'answer':'That request is outside your role permissions. '+help_text(principal,request.app)}
    if re.search(r'\b(?:create|assign|dispatch|transition|close|approve)\b.{0,35}\bwork[ -]?orders?\b',body.message,re.I) and not re.search(r'\bemail\b',body.message,re.I):
        return {'outcome':'forbidden','reason_code':'outside_platform','selected_route':None,'actual_tools':[],
            'tool_calls':[],'answer':'Operational work orders are managed externally. Hoistra can read permitted reports and prepare an authorized email request.'}
    from ..api.routes.homework_reads import get_dispatcher
    await get_dispatcher(request).repository.audit(
        principal, "", {"selected_domain": domain, "detected_domains": detected},
        "needs_clarification" if domain is None else "routed",
        request_id, "homework_agent_router", "routing")
    if domain is None:
        if detected:
            answer = ("This request spans " + " and ".join(detected) + ". Which permitted part should I handle first? No data has been combined.")
        else:
            answer=help_text(principal,request.app)
        return {"outcome": "needs_clarification", "answer": answer,
                "selected_route": None, "actual_tools": [], "conversation_id": body.conversation_id}

    if domain == 'compatibility':
        from .homework_compatibility_agent import ask as delegate
        result=await delegate(principal,body,create_chat_model(),request_id)
        return {**result,'selected_route':'compatibility'}

    if domain == 'usage':
        if not getattr(request.app.state,'hw1_usage_enabled',False):
            return {'outcome':'not_enabled','selected_route':'usage','actual_tools':[],'answer':'Usage reports are not enabled.'}
        from ..services.usage_hw1.service import UsageService
        from .homework_usage_agent import ask as delegate
        result=await delegate(principal,body,UsageService(),create_chat_model(),request_id)
        return {**result,'selected_route':'usage'}

    if domain == "communications":
        if not getattr(request.app.state,"hw1_communications_enabled",False):
            return {"outcome":"not_enabled","selected_route":domain,"actual_tools":[],
                    "answer":"The simulated communications profile is not enabled."}
        from ..services.communications_hw1.service import CommunicationsService
        from .homework_communications_agent import ask as delegate
        result=await delegate(principal,body,CommunicationsService(),create_chat_model(),request_id)
        return {**result,"selected_route":domain}

    if domain == "ingestion":
        if not getattr(request.app.state, "hw1_ingestion_enabled", False):
            return {"outcome": "not_enabled", "selected_route": domain, "actual_tools": [],
                    "answer": "The ingestion profile is not enabled; no source was accepted."}
        from ..services.ingestion_hw1.service import IngestionService
        from .homework_ingestion_agent import ask as ingestion_ask
        result=await ingestion_ask(principal,body,IngestionService(),create_chat_model(),request_id)
        return {**result,"selected_route":"ingestion"}

    if domain == "reads":
        from ..api.routes.homework_reads import get_dispatcher
        from ..services.homework_reads import plan_and_read
        try:
            response = await plan_and_read(body.message, principal, get_dispatcher(request),
                                           create_chat_model(), request_id)
        except HTTPException:
            raise
        if isinstance(response, dict):
            source_context = ({"record_origin": "external_system_report", "managed_by": "external_system", "data_origin": "fictional_fixture", "lifecycle_changes_performed": False}
                if any(c.get("tool") == "work_order_status" for c in response.get("tool_calls", [])) else {})
            return {**response, "selected_route": "reads", "source_context": source_context}
        return {"outcome": "answered", "answer": str(response), "selected_route": "reads",
                "actual_tools": []}

    if domain == "compliance":
        enabled = getattr(request.app.state, "hw1_compliance_enabled", False)
        if not enabled:
            return {"outcome": "not_enabled", "selected_route": domain, "actual_tools": [],
                    "answer": "The Compliance profile is not enabled; no operation was performed."}
        from ..api.routes.homework_compliance import service
        from .homework_compliance_agent import ask as delegate
        result = await delegate(principal, SimpleNamespace(message=body.message,
            conversation_id=body.conversation_id), service(request), create_chat_model(), request_id)
    elif domain == "contract_performance":
        if not getattr(request.app.state, "hw1_contract_enabled", False):
            return {"outcome": "not_enabled", "selected_route": domain, "actual_tools": [],
                    "answer": "Contract Performance is not enabled; no operation was performed."}
        from ..api.routes.homework_contract import service
        from .homework_contract_agent import ask as delegate
        result = await delegate(principal, SimpleNamespace(message=body.message,
            conversation_id=body.conversation_id), service(request), create_chat_model(), request_id)
    else:
        if not getattr(request.app.state, "hw1_energy_enabled", False):
            return {"outcome": "not_enabled", "selected_route": domain, "actual_tools": [],
                    "answer": "Energy is not enabled; no operation was performed."}
        from ..api.routes.homework_energy import service
        from .homework_energy_agent import ask as delegate
        result = await delegate(principal, SimpleNamespace(message=body.message,
            conversation_id=body.conversation_id), service(request), create_chat_model(), request_id)

    # Keep the specialist trace as the source of executed-tool facts. Add only the
    # central route label to the response; do not fabricate a second tool trace.
    return {**result, "selected_route": domain}


def permitted(p,domain,message=''):
    if domain is None:return True
    if p.role=='general_user':
        if domain in {'contract_performance','energy','ingestion','compatibility'}:return False
        if domain=='communications' and re.search(r'\b(?:hoi|hoist score)\b',message,re.I):return False
    if p.role=='compliance_officer':
        if domain in {'contract_performance','energy','compatibility'}:return False
        if domain=='ingestion' and not re.search(r'\bcertificate\b',message,re.I):return False
        if domain=='communications' and re.search(r'\b(?:hoi|hoist score|maintenance|invoice|energy)\b',message,re.I):return False
        if domain=='reads' and re.search(r'\b(?:work[ -]?order|asset|inspection|energy|invoice|performance)\b',message,re.I):return False
    return True


def help_text(p,app):
    from .unified_prompt import enabled_for
    enabled=set(enabled_for(app))
    choices=[]
    if 'compliance' in enabled:choices.append('certificate status/evidence and permitted certificate actions' if p.role!='general_user' else 'certificate summaries')
    if p.role!='compliance_officer':choices.append('assigned-building status and asset summaries')
    if p.role in {'property_manager','portfolio_manager'}:
        for domain,label in [('contract_performance','Contract Performance'),('energy','Energy'),('ingestion','source intake and derived corrections')]:
            if domain in enabled:choices.append(label)
    if 'communications' in enabled:choices.append('certificate-related email previews' if p.role=='compliance_officer' else 'permitted team email previews')
    if 'usage' in enabled:choices.append('compliance-only usage' if p.role=='compliance_officer' else 'own usage reports' if p.role=='general_user' else 'scoped usage reports')
    return 'Which permitted task do you need: '+', '.join(choices)+'?'


def actual_tools(result, principal=None):
    tools=[]
    for call in result.get('tool_calls',[]):
        name=call.get('tool','')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,99}',name):name='unregistered_tool'
        succeeded='error' not in call and ('result' in call or 'output' in call)
        if 'execution_origin' in call:
            from ..services.trace_hw1.energy import observation
            tools.append(observation(principal, name, call.get('arguments'), call.get('execution_origin'), succeeded))
        else:
            tools.append({'tool':name,'succeeded':succeeded})
    return tools


async def ask(request,body):
    """Session ownership and manifest enforcement cover every central route/clarification."""
    from datetime import datetime,timezone
    from ..services.compliance_hw1.store import runtime_store
    from ..services.compliance_hw1.sessions import conversation,append
    from .unified_prompt import validate,enabled_for,CONTEXT
    p=request.state.test_principal;store=runtime_store()
    registry,instructions=validate(enabled_for(request.app))
    await store.audit(p,'unified_agent','capability_authorized',request_id=request.state.request_id)
    async with store.transaction(p) as repo:
        session=await conversation(repo,p,body.conversation_id)
    request_body=SimpleNamespace(message=body.message,conversation_id=session['id'])
    context={'actor':p.user_id,'role':p.role,'registry':registry,'instructions':instructions,
             'utc_date':datetime.now(timezone.utc).date().isoformat(),'defer_answer_persistence':True}
    token=CONTEXT.set(context)
    try:
        result=await _dispatch(request,request_body)
    except Exception as error:
        # Preserve failed request evidence without treating a raised delegate as empty success.
        attempts=[context['read_attempt']] if context.get('read_attempt') else []
        async with store.transaction(p) as repo:
            await repo.insert('history','unified_trace',list(p.building_ids),{
                'actor':p.user_id,'role':p.role,'category':'compliance' if p.role=='compliance_officer' else 'agent',
                'conversation_id':session['id'],'request_id':request.state.request_id,'intent':'domain_request',
                'selected_route':classify(body.message)[0],'actual_tools':attempts,'outcome':'failed',
                'error_status':error.status_code if isinstance(error,HTTPException) else 502,
                'execution_observation':'recorded read attempt' if attempts else 'delegate raised before returning tool observations',
                'prompt_metadata':context.get('read_prompt_metadata'),'provider_execution':context.get('read_provider'),
                'unified_metadata':registry,'policy_version':registry['policy_version'],'generated_at':datetime.now(timezone.utc).isoformat()})
        raise
    finally:CONTEXT.reset(token)
    result.setdefault('prompt_metadata',{'profile':'hw1-unified-control','specification_version':'0.37.0',
        'bound_tools':[],'model_invoked':False,'decision_layer':'deterministic routing/role guard','unified_registry':registry})
    tools=actual_tools(result,p);route=result.get('selected_route')
    limit=registry['enabled_profiles'].get(route,{}).get('max_tool_calls',1)
    if len(tools)>limit:raise HTTPException(502,'Unified tool budget exceeded; no full completion claimed')
    outcome=result.get('outcome') or ('answered' if tools else 'needs_clarification')
    if tools and any(not t['succeeded'] for t in tools):outcome='partial' if any(t['succeeded'] for t in tools) else 'failed'
    if result.get('provider_execution',{}).get('provider_failed') and tools:outcome='partial'
    await store.audit(p,'unified_agent',outcome,request_id=request.state.request_id)
    if route=='reads' and not tools and outcome=='needs_clarification':result['answer']=help_text(p,request.app)
    result={**result,'outcome':outcome,'conversation_id':session['id'],'actual_tools':tools,'unified_metadata':registry}
    result['execution_outcome']=outcome
    result['answer_generation']={'status':'not_requested','tools_bound':[],'successful_responses':0}
    if tools and any(item['succeeded'] for item in tools) and outcome not in {'forbidden','not_enabled'}:
        from .answer_synthesis import build_answer_input, generate_answer, FAILURE_ANSWER, create_answer_model
        try:
            evidence=build_answer_input(body.message,session['data']['messages'][-4:],result)
            result['answer'],generation=await generate_answer(create_answer_model(),evidence)
        except Exception as error:
            result['answer']=FAILURE_ANSWER
            generation={'status':'answer_generation_failed','reason':type(error).__name__,
                        'tools_bound':[],'successful_responses':0}
        result['answer_generation']=generation
        if generation.get('status')!='generated':
            result['outcome']='answer_generation_failed'
        if generation.get('successful_responses'):
            from ..services.usage_hw1.service import record_measurement
            category='compliance' if p.role=='compliance_officer' else 'answer_synthesis'
            await record_measurement(p,request.state.request_id,category,session['id'],generation.get('token_usage'),1000)
        await store.audit(p,'answer_synthesis',generation['status'],request_id=request.state.request_id)
    async with store.transaction(p) as repo:
        # Delegates deferred intermediate prose; persist the actual final answer once.
        current=await conversation(repo,p,session['id'])
        await append(repo,current,body.message,result['answer'],[])
        trace_id=await repo.insert('history','unified_trace',list(p.building_ids),{
            'actor':p.user_id,'role':p.role,'category':'compliance' if p.role=='compliance_officer' else 'agent',
            'conversation_id':session['id'],'request_id':request.state.request_id,
            'intent':'multi_domain_request' if len(classify(body.message)[1])>1 else 'domain_request',
            'selected_route':route,'actual_tools':tools,'outcome':result['outcome'],'execution_outcome':outcome,
            'answer_generation':result.get('answer_generation'),
            'prompt_metadata':result.get('prompt_metadata'),'unified_metadata':registry,
            'provider_execution':result.get('provider_execution'),'policy_version':registry['policy_version'],'generated_at':datetime.now(timezone.utc).isoformat()})
    result['trace_id']=trace_id
    return result
