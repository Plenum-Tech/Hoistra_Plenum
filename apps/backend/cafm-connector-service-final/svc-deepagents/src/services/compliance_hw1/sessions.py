"""Conversation ownership is separate from shared eligible action review."""
from uuid import uuid4
from fastapi import HTTPException
from .outbox import now


async def conversation(repo,principal,identifier=None):
    if identifier:
        row=await repo.get('sessions',identifier,lock=True)
        if row['data']['owner']!=principal.user_id or row['data']['role']!=principal.role or not set(row['buildings'])<=set(principal.building_ids):
            raise HTTPException(404,'Conversation unavailable')
        if set(row['buildings'])!=set(principal.building_ids):
            raise HTTPException(409,'Conversation assignment scope changed; start a new conversation')
        return row
    identifier=str(uuid4())
    data={'scope_verified':True,'role':principal.role,'owner':principal.user_id,'messages':[],'pending_proposal_ids':[],'created_at':now()}
    await repo.insert('sessions','conversation',list(principal.building_ids),data,identifier)
    return {'id':identifier,'data':data,'buildings':list(principal.building_ids)}


async def append(repo,row,user_message,reply,proposal_ids):
    data=dict(row['data'])
    # Conversation content is private session storage, never copied into audit targets.
    from ...agents.unified_prompt import CONTEXT
    context=CONTEXT.get()
    # The central route owns the final conversational turn; delegates retain proposal IDs only.
    if not (context and context.get('defer_answer_persistence')):
        data['messages']=(data['messages']+[{'user':user_message,'assistant':reply}])[-10:]
    data['pending_proposal_ids']=list(dict.fromkeys(data['pending_proposal_ids']+proposal_ids))[-20:]
    await repo.update('sessions',row,data)
