"""Compile explicit ingestion profile sources and hashes without changing shared skills."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SERVICE=ROOT/"apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0,str(SERVICE))
from src.agents.ingestion_prompt import ROOT as SKILLS,SOURCES
from src.services.ingestion_hw1.service import ROLE_TOOLS

def main():
    entries=[]
    folder=SKILLS/"profiles/ingestion";folder.mkdir(parents=True,exist_ok=True)
    for n,source in enumerate(SOURCES):
        original=SKILLS/source;target=folder/f"{n:02d}-{Path(source).name}"
        target.write_bytes(original.read_bytes())
        sha=hashlib.sha256(original.read_bytes()).hexdigest()
        entries.append({"source_path":source,"source_sha256":sha,"path":target.relative_to(SKILLS).as_posix(),"sha256":sha})
    manifest={"profile":"hw1-ingestion","specification_version":"0.37.0","policy_version":"hw1-ingestion-v1",
        "instructions":entries,"role_tools":{r:sorted(t) for r,t in ROLE_TOOLS.items()}}
    (SKILLS/"ingestion-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps({"profile":"hw1-ingestion","instruction_count":len(entries)}))

if __name__=="__main__":main()
