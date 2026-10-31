"""Pin every enabled profile and augment domain prompts only within unified requests."""
import hashlib,json,importlib
from contextvars import ContextVar
from pathlib import Path
from fastapi import HTTPException
ROOT=Path(__file__).resolve().parents[2]/'skills'
PROFILES={'reads':('prompt-manifest.json',None,1),'compliance':('compliance-manifest.json','hw1_compliance_enabled',4),
 'contract_performance':('contract-manifest.json','hw1_contract_enabled',4),'energy':('energy-manifest.json','hw1_energy_enabled',2),
 'ingestion':('ingestion-manifest.json','hw1_ingestion_enabled',2),'communications':('communications-manifest.json','hw1_communications_enabled',2),
 'usage':('usage-manifest.json','hw1_usage_enabled',2)}
MODULES={'compliance':'compliance_prompt','contract_performance':'contract_prompt','energy':'energy_prompt',
 'ingestion':'ingestion_prompt','communications':'communications_prompt','usage':'usage_prompt'}
CONTEXT=ContextVar('hw1_unified_prompt',default=None)

def sha(raw):return hashlib.sha256(raw).hexdigest()
def unique(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('Duplicate manifest key')
  result[key]=value
 return result

def read(path,root):
 file=root/path
 if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe instruction path')
 return file.read_bytes()

def validate(enabled=None,root=None):
 root=Path(root or ROOT);enabled=set(PROFILES) if enabled is None else set(enabled)
 try:
  raw=read('unified-manifest.json',root);m=json.loads(raw,object_pairs_hook=unique)
  if set(m)!={'profile','specification_version','policy_version','instruction','answer_instruction','profiles'}:raise ValueError
  if (m['profile'],m['specification_version'],m['policy_version'])!=('hw1-unified','0.37.0','hw1-unified-v1'):raise ValueError
  if m['instruction']['path']!='orchestrator/unified.md' or sha(read(m['instruction']['path'],root))!=m['instruction']['sha256']:raise ValueError
  answer=m['answer_instruction']
  if answer['path']!='orchestrator/answer-synthesis.md' or sha(read(answer['path'],root))!=answer['sha256']:raise ValueError
  if set(m['profiles'])!=set(PROFILES) or not enabled<=set(PROFILES):raise ValueError
  profiles={}
  for name in sorted(enabled):
   item=m['profiles'][name];path,flag,limit=PROFILES[name]
   if set(item)!={'path','sha256','max_tool_calls'} or item['path']!=path or item['max_tool_calls']!=limit:raise ValueError
   body=read(path,root)
   if sha(body)!=item['sha256']:raise ValueError
   child=json.loads(body,object_pairs_hook=unique)
   if child['specification_version']!='0.37.0':raise ValueError
   for instruction in child['instructions']:
    for pathkey,hashkey in [('path','sha256'),('source_path','source_sha256')]:
     if pathkey in instruction and sha(read(instruction[pathkey],root))!=instruction[hashkey]:raise ValueError
   profiles[name]={'manifest_sha256':sha(body),'profile':child['profile'],'max_tool_calls':limit}
  return {'profile':m['profile'],'specification_version':m['specification_version'],'policy_version':m['policy_version'],
    'manifest_sha256':sha(raw),'instruction_sha256':m['instruction']['sha256'],'answer_instruction_sha256':answer['sha256'],'enabled_profiles':profiles},read(m['instruction']['path'],root).decode()
 except (ValueError,OSError,KeyError,TypeError,UnicodeError):raise HTTPException(503,'Unified instruction registry unavailable; no fallback') from None

def enabled_for(app):return [name for name,(_,flag,_) in PROFILES.items() if flag is None or getattr(app.state,flag,False)]

def augment(prompt,metadata,principal):
 context=CONTEXT.get()
 if context is None:return prompt,metadata
 if context['actor']!=principal.user_id or context['role']!=principal.role:raise HTTPException(403,'Unified context identity mismatch')
 text=prompt+'\n\n'+context['instructions']+'\nTrusted UTC date: '+context['utc_date']
 return text,{**metadata,'unified_registry':context['registry'],'rendered_prompt_sha256':sha(text.encode())}
