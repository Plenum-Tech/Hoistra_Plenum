"""Every service a call names must be one http_client knows (12 Oct 2026).

http_client adds the company being viewed (a superadmin acting for a client) only for the
services in _ORG_SCOPED_SERVICES, matched by name. Two calls in single_door_flow named
"operations-intelligence" with a hyphen, so after a superadmin migrated data for a client the
reconcile and the workbook extras ran in the superadmin's own company (pre-push review).
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_call_names_operations_intelligence_with_a_hyphen():
    bad = []
    for path in SRC.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'service\s*=\s*"operations-intelligence"', line):
                bad.append(f"{path.relative_to(SRC)}:{n}")
    assert bad == [], bad
