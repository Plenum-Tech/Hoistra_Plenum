"""Materialize and hash the reviewed Contract Performance prompt profile."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "apps/backend/cafm-connector-service-final/svc-deepagents"
sys.path.insert(0, str(SERVICE))
from src.agents.contract_prompt import ROOT as SKILLS, SOURCES
from src.services.contract_hw1.service import TOOL_ROLES


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    entries = []
    destination = SKILLS / "profiles/contract"
    destination.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(SOURCES):
        source_path = SKILLS / source
        target = destination / f"{index:02d}-{Path(source).name}"
        target.write_bytes(source_path.read_bytes())
        entries.append({"source_path": source, "source_sha256": digest(source_path),
                        "path": target.relative_to(SKILLS).as_posix(),
                        "sha256": digest(target), "kind": "shared" if index < 5 else "domain"})
    roles = ("general_user", "property_manager", "portfolio_manager", "compliance_officer")
    role_tools = {role: sorted(name for name, allowed in TOOL_ROLES.items() if role in allowed)
                  for role in roles}
    manifest = {"manifest_version": "1.0.0", "specification_version": "0.37.0",
                "policy_version": "hw1-contract-v1", "profile": "hw1-contract-performance",
                "max_tool_calls": 4, "instructions": entries, "role_tools": role_tools}
    (SKILLS / "contract-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"profile": manifest["profile"], "instruction_count": len(entries),
                      "role_tool_counts": {role: len(names) for role, names in role_tools.items()}}))


if __name__ == "__main__":
    main()
