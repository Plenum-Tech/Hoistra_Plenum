"""Hash-checked shared/domain instructions for scoped usage previews and reads."""
import hashlib,json
from pathlib import Path
from fastapi import HTTPException
from ..services.usage_hw1.contracts import ROLE_TOOLS

ROOT=Path(__file__).resolve().parents[2]/"skills"
SOURCES=["orchestrator/SKILL.md","policies/permissions.md","policies/actions-and-evidence.md",
         "policies/responses-and-audit.md","query-builder/domain-enabled.md","usage/hw1.md"]


def validate():
    try:
        manifest=json.loads((ROOT/"usage-manifest.json").read_text())
        if manifest["specification_version"]!="0.37.0" or manifest["policy_version"]!="hw1-usage-v1": raise ValueError
        if manifest["role_tools"]!={r:sorted(t) for r,t in ROLE_TOOLS.items()}: raise ValueError
        if [i["source_path"] for i in manifest["instructions"]]!=SOURCES: raise ValueError
        chunks=[]
        for item in manifest["instructions"]:
            for key,hash_key in [("source_path","source_sha256"),("path","sha256")]:
                path=ROOT/item[key]
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()): raise ValueError
                if hashlib.sha256(path.read_bytes()).hexdigest()!=item[hash_key]: raise ValueError
            chunks.append((ROOT/item["path"]).read_text())
        return manifest,"\n\n".join(chunks)
    except (ValueError,OSError,KeyError,TypeError):
        raise HTTPException(503,"Usage instruction profile unavailable") from None


def _assemble(principal):
    manifest,prompt=validate()
    tools=sorted(ROLE_TOOLS.get(principal.role,[]))
    prompt+="\nTrusted identity: "+json.dumps({"user_id":principal.user_id,"role":principal.role,
        "organization_id":principal.organization_id,"buildings":principal.building_ids,"portfolio_id":principal.portfolio_id})
    prompt+="\nOnly bounded manual report/read tools: "+json.dumps(tools)+". Maximum two tool calls; no model may confirm a decision."
    return prompt,{"profile":"hw1-usage","specification_version":"0.37.0","policy_version":"hw1-usage-v1",
        "bound_tools":tools,"loaded_instructions":manifest["instructions"],
        "manifest_sha256":hashlib.sha256((ROOT/"usage-manifest.json").read_bytes()).hexdigest()}


def assemble(principal):
    from .unified_prompt import augment
    prompt,metadata=_assemble(principal)
    return augment(prompt,metadata,principal)
