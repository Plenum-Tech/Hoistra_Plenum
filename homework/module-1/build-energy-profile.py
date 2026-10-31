"""Materialize Energy's reviewed shared instructions and hash manifest."""
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SERVICE=ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0,str(SERVICE))
from src.agents.energy_prompt import ROOT as SKILLS, SOURCES
from src.services.energy_hw1.service import TOOLS

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    instructions=[]; target=SKILLS/"profiles/energy"; target.mkdir(parents=True,exist_ok=True)
    for i,source in enumerate(SOURCES):
        original=SKILLS/source; compiled=target/f"{i:02d}-{Path(source).name}"
        compiled.write_bytes(original.read_bytes())
        instructions.append({"source_path":source,"source_sha256":sha(original),"path":compiled.relative_to(SKILLS).as_posix(),"sha256":sha(compiled)})
    roles={"general_user":[],"property_manager":sorted(TOOLS),"portfolio_manager":sorted(TOOLS),"compliance_officer":[]}
    manifest={"manifest_version":"1.0.0","specification_version":"0.37.0","policy_version":"hw1-energy-v1","profile":"hw1-energy","instructions":instructions,"role_tools":roles}
    (SKILLS/"energy-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"profile":manifest["profile"],"instructions":len(instructions),"tool_counts":{k:len(v) for k,v in roles.items()}}))

if __name__=="__main__": main()
