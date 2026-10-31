"""Compile explicit usage profile sources and hashes without changing shared skills."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SERVICE=ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0,str(SERVICE))
from src.agents.usage_prompt import ROOT as SKILLS,SOURCES
from src.services.usage_hw1.contracts import ROLE_TOOLS

def main():
    entries=[]
    folder=SKILLS/"profiles/usage";folder.mkdir(parents=True,exist_ok=True)
    for n,source in enumerate(SOURCES):
        original=SKILLS/source;target=folder/f"{n:02d}-{Path(source).name}"
        target.write_bytes(original.read_bytes())
        sha=hashlib.sha256(original.read_bytes()).hexdigest()
        entries.append({"source_path":source,"source_sha256":sha,"path":target.relative_to(SKILLS).as_posix(),"sha256":sha})
    manifest={"profile":"hw1-usage","specification_version":"0.37.0","policy_version":"hw1-usage-v1",
        "instructions":entries,"role_tools":{r:sorted(t) for r,t in ROLE_TOOLS.items()}}
    (SKILLS/"usage-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"profile":"hw1-usage","instruction_count":len(entries)}))

if __name__=="__main__":main()
