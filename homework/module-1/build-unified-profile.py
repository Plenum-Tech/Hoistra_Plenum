"""Pin existing reviewed child manifests; never rewrite their policies or instructions."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents'))
from src.agents.unified_prompt import ROOT as SKILLS,PROFILES
m={'profile':'hw1-unified','specification_version':'0.37.0','policy_version':'hw1-unified-v1',
 'instruction':{'path':'orchestrator/unified.md','sha256':hashlib.sha256((SKILLS/'orchestrator/unified.md').read_bytes()).hexdigest()},
 'answer_instruction':{'path':'orchestrator/answer-synthesis.md','sha256':hashlib.sha256((SKILLS/'orchestrator/answer-synthesis.md').read_bytes()).hexdigest()},
 'profiles':{name:{'path':path,'sha256':hashlib.sha256((SKILLS/path).read_bytes()).hexdigest(),'max_tool_calls':limit} for name,(path,_,limit) in PROFILES.items()}}
(SKILLS/'unified-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps({'profile':m['profile'],'pinned_profiles':len(PROFILES)}))
