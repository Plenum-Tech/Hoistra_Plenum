"""Record activation-only wording edits; keep original read profile bytes intact."""
from pathlib import Path
import difflib,hashlib,json,re,sys
ROOT=Path('/root/Hoistra_Plenum')
SERVICE=ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents'
sys.path.insert(0,str(SERVICE))
# Source list is read without importing FastAPI from the setup host.
SOURCES=['orchestrator/SKILL.md','policies/permissions.md','policies/actions-and-evidence.md',
         'policies/responses-and-audit.md','query-builder/SKILL.md']
TOPICS=['SKILL.md','vocabulary.md','tool-routing.md','tool-selection.md','renewals.md','recipes.md',
        'cross-domain.md','tables.md','taxonomy.md','domain_compliance_knowledge.md','answering.md',
        'review.md','scope-eval.md','never.md']
SOURCES += ['compliance/'+n for n in TOPICS]
ROLES={'general_user','property_manager','portfolio_manager','compliance_officer'}
TOOLS={'compliance_resolve_entity','compliance_list_certificates','compliance_count_certificates','compliance_group_vendors',
       'compliance_get_taxonomy','compliance_get_coverage','compliance_get_evidence','compliance_get_vendor_eligibility',
       'compliance_get_findings','compliance_prepare_proposal','compliance_get_proposal','homework_get_hoi','homework_get_outbox'}


def active(text):
    # Only deployment descriptions change; source files remain frozen for the read profile.
    paragraphs=text.split('\n\n');changed=[]
    for p in paragraphs:
        lower=p.lower()
        is_status=(('current' in lower or 'present read profile' in lower) and
            any(x in lower for x in ['deployment','homework profile','homework deployment','read profile','read-only profile']) and
            any(x in lower for x in ['read-only','one-tool','guarded reads only','one guarded','one selected','one read','one-call','disabled','formats','deterministic']))
        if is_status:
            p=('Deployment: hw1-compliance enables only the verified offered Compliance/shared tools. '
               'At most four read/preview calls per turn; human confirmation is a separate authenticated endpoint. '
               'Legacy specialists, operational WOs, real mail, other domains and calendar scheduling remain unavailable. '
               'Read-only profile instructions remain scoped to hw1-read-only. '
               'Answers use actual scoped results; no automatic business approval or ungrounded claims.')
        p=p.replace('The current profile permits at most one read-tool call per request.',
            'This profile permits at most four read/preview calls; confirmation is not model-bound.')
        p=p.replace('Select at most one read tool per request.',
            'Select only offered read/preview tools within this profile limit.')
        changed.append(p)
    return '\n\n'.join(changed)


def main():
    skills=SERVICE/'skills';dest=skills/'profiles/compliance';dest.mkdir(parents=True,exist_ok=True)
    entries=[];patches=[]
    for i,source in enumerate(SOURCES):
        raw=(skills/source).read_bytes();before=raw.decode();after=active(before)
        path='profiles/compliance/'+str(i).zfill(2)+'-'+Path(source).name
        (skills/path).write_text(after)
        entries.append({'source_path':source,'source_sha256':hashlib.sha256(raw).hexdigest(),
                        'path':path,'sha256':hashlib.sha256(after.encode()).hexdigest(),'kind':'shared' if i<5 else 'domain'})
        patches.append(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=source,tofile=path)))
    role_tools={r:sorted(t for t in TOOLS if not (t=='homework_get_hoi' and r not in {'property_manager','portfolio_manager'})
                and not (t=='compliance_get_evidence' and r=='general_user')
                and not (t=='compliance_get_findings' and r=='general_user')) for r in sorted(ROLES)}
    manifest={'manifest_version':'2.0.0','specification_version':'0.37.0','policy_version':'hw1-compliance-v1',
              'profile':'hw1-compliance','max_tool_calls':4,'instructions':entries,'role_tools':role_tools}
    (skills/'compliance-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    evidence=ROOT/'homework/module-1/compliance-implementation';evidence.mkdir(exist_ok=True)
    (evidence/'activation-instructions.patch').write_text(''.join(patches))
    print('Separate Compliance profile prepared; default read-only instructions unchanged.')

if __name__=='__main__':main()
