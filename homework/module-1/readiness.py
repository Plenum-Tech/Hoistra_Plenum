"""Isolated activation with immutable image pinning and a private exact rollback plan."""
import json,os,re,subprocess,time,urllib.request
from pathlib import Path
from uuid import uuid4
MODULE=Path(__file__).resolve().parent;ROOT=MODULE.parents[1]
COMPOSE=MODULE/'docker-compose.hw1.yml';ACTIVE=MODULE/'docker-compose.hw1-active.yml'
RUNTIME=MODULE/'runtime/readiness';STATE=RUNTIME/'activation-state.json'
ACTIVE_NAME='hoistra-hw1-orchestrator-1';CANDIDATE_NAME='hoistra-hw1-compliance_candidate-1'
FLAGS=['HW1_COMPLIANCE_ENABLED','HW1_CONTRACT_ENABLED','HW1_ENERGY_ENABLED','HW1_INGESTION_ENABLED','HW1_USAGE_ENABLED']
class ReadinessFailure(Exception):pass

def compose_environment():
 # Controller pytest uses host DB URLs. They must never override container runtime
 # interpolation: --env-file alone has lower precedence than inherited variables.
 env=os.environ.copy()
 for path in (COMPOSE,ACTIVE):
  if path.exists():
   for key in re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)',path.read_text()):env.pop(key,None)
 return env

def run(command,timeout=180,input=None):
 env=compose_environment() if command[:2]==['docker','compose'] else None
 p=subprocess.run(command,cwd=ROOT,input=input,text=True,capture_output=True,timeout=timeout,env=env)
 if p.returncode:raise ReadinessFailure('Isolated command failed: '+command[0]+' '+('compose' if 'compose' in command else 'verification'))
 return p.stdout

def inspect(name):return json.loads(run(['docker','inspect',name]))[0]
def image_id(image):return json.loads(run(['docker','image','inspect',image]))[0]['Id']
def compose(args,override=None,timeout=180):
 command=['docker','compose','--env-file',str(MODULE/'runtime/.env'),'-p','hoistra-hw1','-f',str(COMPOSE)]
 if override:command+=['-f',str(override)]
 return run(command+list(args),timeout)
def private_write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);os.chmod(path.parent,0o700)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2)

def validate_container(row,port):
 labels=row['Config'].get('Labels',{})
 if labels.get('com.docker.compose.project')!='hoistra-hw1':raise ReadinessFailure('Unrelated Compose project refused')
 binds=row['HostConfig']['PortBindings'].get('8008/tcp',[])
 if binds!=[{'HostIp':'127.0.0.1','HostPort':str(port)}]:raise ReadinessFailure('Non-isolated service binding refused')

def prepare_rollback():
 RUNTIME.mkdir(parents=True,exist_ok=True);os.chmod(RUNTIME,0o700)
 active=inspect(ACTIVE_NAME);candidate=inspect(CANDIDATE_NAME)
 validate_container(active,18008);validate_container(candidate,18009)
 if STATE.exists():
  if STATE.stat().st_mode & 0o077:raise ReadinessFailure('Rollback state permissions too broad')
  state=json.loads(STATE.read_text())
  if image_id(state['rollback_tag'])!=state['rollback_image_id']:raise ReadinessFailure('Saved rollback image drifted')
  return state
 folder=RUNTIME/('rollback-'+uuid4().hex);folder.mkdir(mode=0o700)
 private_write(folder/'container-inspect.json',active)
 tag='hoistra-hw1-orchestrator:rollback-'+active['Image'].split(':')[1][:16]
 run(['docker','image','tag',active['Image'],tag])
 env=dict(item.split('=',1) for item in active['Config']['Env'])
 override=folder/'compose-rollback.json'
 private_write(override,{'services':{'orchestrator':{'image':tag,'environment':{k:v.replace('$','$$') for k,v in env.items()}}}})
 resolved=json.loads(compose(['config','--format','json'],override))
 service=resolved['services']['orchestrator']
 if service['image']!=tag or any(service['environment'].get(k)!=v for k,v in env.items()):
  raise ReadinessFailure('Rollback Compose configuration differs from saved active environment')
 state={'project':'hoistra-hw1','active_name':ACTIVE_NAME,'rollback_image_id':active['Image'],'rollback_tag':tag,
  'rollback_override':str(override),'original_started_at':active['State']['StartedAt'],'snapshot_folder':str(folder)}
 private_write(STATE,state)
 return state

def wait_health(port,seconds=120):
 deadline=time.monotonic()+seconds
 while time.monotonic()<deadline:
  try:
   with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/health',timeout=3) as r:
    if r.status==200:return
  except (OSError,ValueError):pass
  time.sleep(1)
 raise ReadinessFailure('Isolated health deadline exceeded at '+str(port))

def activate(state):
 candidate=inspect(CANDIDATE_NAME);validate_container(candidate,18009)
 candidate_env=dict(x.split('=',1) for x in candidate['Config']['Env'])
 if any(candidate_env.get(k)!='true' for k in FLAGS):raise ReadinessFailure('Candidate domain flag disabled')
 run(['docker','image','tag',candidate['Image'],'hoistra-hw1-orchestrator:hw1-baseline-v37'])
 compose(['up','-d','--no-build','--no-deps','--pull','never','orchestrator'],ACTIVE)
 wait_health(18008)
 active=inspect(ACTIVE_NAME);validate_container(active,18008)
 env=dict(x.split('=',1) for x in active['Config']['Env'])
 if active['Image']!=candidate['Image'] or any(env.get(k)!='true' for k in FLAGS):raise ReadinessFailure('Active image/flags differ from verified candidate')
 return {'active_image_id':active['Image'],'candidate_image_id':candidate['Image'],'active_started_at':active['State']['StartedAt'],
  'rollback_image_id':state['rollback_image_id'],'rollback_tag':state['rollback_tag'],'ports':[18008,18009]}

def rollback(state):
 if image_id(state['rollback_tag'])!=state['rollback_image_id']:raise ReadinessFailure('Rollback image mismatch')
 compose(['up','-d','--no-build','--no-deps','--pull','never','orchestrator'],Path(state['rollback_override']))
 wait_health(18008)
 if inspect(ACTIVE_NAME)['Image']!=state['rollback_image_id']:raise ReadinessFailure('Rollback did not restore saved image')

def publish_checkpoint(receipt):
    """Publish current status only from a complete verified receipt and actual active image."""
    import hashlib
    expected={'all_previous_milestone_gates_reverified','canonical_spec_requirement_status_and_evidence_mapping',
        'full_service_regression_passes','complete_candidate_live_role_and_domain_matrix','rollback_ready_isolated_active_deployment',
        'active_full_agent_status_and_real_provider_question','homework_cli_commands_and_honest_remaining_boundaries'}
    if (receipt.get('milestone_id')!='07-readiness' or {c['id'] for c in receipt['checks']}!=expected
            or not all(c['passed'] is True for c in receipt['checks'])):
        raise ReadinessFailure('Cannot publish readiness from incomplete/failed evidence')
    activation=receipt['activation']
    if inspect(ACTIVE_NAME)['Image']!=activation['active_image_id']:raise ReadinessFailure('Active image drifted after verification')
    status_file=MODULE/'domain-implementation-status-v0.37.0.json'
    archive=None
    if status_file.exists():
        raw=status_file.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        archive=MODULE/'baselines/pre-readiness-status'/('domain-status-'+digest+'.json');archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():
            with archive.open('xb') as f:f.write(raw)
    rows={}
    for domain,milestones in {'contract_performance':['01-contract'],'energy':['02-energy'],'general_ingestion':['03-ingestion'],
        'communications_hoi':['04-communications-hoi'],'usage_reports':['05-usage-reports'],'unified_agent':['06-unified-agent'],
        'homework_acceptance':['07-readiness']}.items():
        rows[domain]={'implementation':'verified declared fictional backend/API baseline','policy':'canonical0.37.0; no new role/business-action grants',
            'tests':{'milestones':milestones,'receipts':['homework/module-1/milestones/evidence/'+m+'.json' for m in milestones]},
            'activation':'isolated active18008 and candidate18009; production conformance not claimed'}
    status={'specification_version':'0.37.0','checkpoint':'declared-homework-backend-baseline-verified-and-isolated-active',
        'updated_at':receipt['recorded_at'],'policy_approval':{'status':'owner-approved baseline','source':'specifications.md and remote-codex/TASK.md'},
        'domains':rows,'verification':{'all_seven_milestones':'passed','full_service_suite':receipt['full_service_suite'],
            'readiness_receipt':'homework/module-1/milestones/evidence/07-readiness.json'},
        'runtime':{'compose_project':'hoistra-hw1','active_api':'http://127.0.0.1:18008','candidate_api':'http://127.0.0.1:18009',
            'active_profile':'hw1-unified / hw1-unified-v1','activation':activation,'unrelated_projects_touched':False},
        'previous_status_archive':archive.relative_to(ROOT).as_posix() if archive else None,
        'boundaries':['fictional structured backend only','no real transport, production connectors/identity, frontend or calendar scheduler',
            'no source deletion, arbitrary schema/SQL, operational WO lifecycle or role/billing/quota administration',
            'unmeasured pricing/adoption/coverage/limits unavailable; legacy full-production requirements not certified']}
    status_file.write_text(json.dumps(status,indent=2)+'\n')
    mapping=MODULE/'readiness-requirement-status-v0.37.0.json'
    data=json.loads(mapping.read_text());data['activation']=activation;data['baseline_acceptance']='all seven declared milestone gates passed'
    data['verification_timestamp']=receipt['recorded_at'];mapping.write_text(json.dumps(data,indent=2)+'\n')
